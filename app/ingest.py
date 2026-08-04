"""
DMARC aggregate report ingestion.

Accepts, in any combination:
  - a raw .mbox file (e.g. a Google Takeout mail export, already unzipped)
  - a Takeout .zip (containing Takeout/Mail/*.mbox)
  - a directory of any of the above, or of loose report files
  - a single report file: .zip (containing report .xml), .xml.gz, or bare .xml

Attachment type is detected by magic bytes, not by declared Content-Type or filename
extension -- real-world reporters lie about both (e.g. GoDaddy sends .xml.gz as
application/octet-stream).

This module only knows how to turn "a blob of bytes" into stored rows. A future
IMAP-polling step can reuse `store_report` / `parse_feedback_xml` directly without
any redesign here.
"""

import argparse
import datetime
import gzip
import io
import mailbox
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app.db import get_connection, get_or_create_domain, init_db

MAGIC_ZIP = b"PK\x03\x04"
MAGIC_GZIP = b"\x1f\x8b"


def _text(parent, path):
    if parent is None:
        return None
    el = parent.find(path)
    if el is None or el.text is None:
        return None
    return el.text.strip()


def parse_feedback_xml(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    metadata = root.find("report_metadata")
    policy = root.find("policy_published")

    pct = _text(policy, "pct")
    result = {
        "org_name": _text(metadata, "org_name"),
        "email": _text(metadata, "email"),
        "report_id": _text(metadata, "report_id"),
        "date_begin": int(_text(metadata, "date_range/begin")) if _text(metadata, "date_range/begin") else None,
        "date_end": int(_text(metadata, "date_range/end")) if _text(metadata, "date_range/end") else None,
        "policy_domain": _text(policy, "domain"),
        "policy_adkim": _text(policy, "adkim"),
        "policy_aspf": _text(policy, "aspf"),
        "policy_p": _text(policy, "p"),
        "policy_sp": _text(policy, "sp"),
        "policy_pct": int(pct) if pct is not None else None,
        "policy_fo": _text(policy, "fo"),
        "records": [],
    }

    for record in root.findall("record"):
        row = record.find("row")
        identifiers = record.find("identifiers")
        policy_evaluated = row.find("policy_evaluated") if row is not None else None
        count_text = _text(row, "count")

        rec = {
            "source_ip": _text(row, "source_ip"),
            "count": int(count_text) if count_text else 1,
            "disposition": _text(policy_evaluated, "disposition"),
            "dkim_result": _text(policy_evaluated, "dkim"),
            "spf_result": _text(policy_evaluated, "spf"),
            "header_from": _text(identifiers, "header_from"),
            "envelope_from": _text(identifiers, "envelope_from"),
            "auth_results": [],
        }

        auth = record.find("auth_results")
        if auth is not None:
            for dkim in auth.findall("dkim"):
                rec["auth_results"].append({
                    "mechanism": "dkim",
                    "domain": _text(dkim, "domain"),
                    "selector": _text(dkim, "selector"),
                    "scope": None,
                    "result": _text(dkim, "result"),
                })
            for spf in auth.findall("spf"):
                rec["auth_results"].append({
                    "mechanism": "spf",
                    "domain": _text(spf, "domain"),
                    "selector": None,
                    "scope": _text(spf, "scope"),
                    "result": _text(spf, "result"),
                })

        result["records"].append(rec)

    return result


def iter_xml_blobs(filename: str, raw_bytes: bytes):
    """Yield (label, xml_bytes) pairs found inside a raw attachment blob."""
    if raw_bytes[:4] == MAGIC_ZIP:
        with zipfile_open(raw_bytes) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".xml"):
                    yield name, zf.read(name)
    elif raw_bytes[:2] == MAGIC_GZIP:
        try:
            yield filename, gzip.decompress(raw_bytes)
        except OSError:
            return
    elif raw_bytes.lstrip()[:5] == b"<?xml":
        yield filename, raw_bytes


def zipfile_open(raw_bytes: bytes):
    import zipfile
    return zipfile.ZipFile(io.BytesIO(raw_bytes))


def iter_attachments_from_mbox(mbox_path: Path):
    mb = mailbox.mbox(str(mbox_path))
    for msg in mb:
        for part in msg.walk():
            fn = part.get_filename()
            if not fn:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            yield fn, payload


def store_report(conn: sqlite3.Connection, report: dict, source_file: str, stats: dict) -> None:
    domain_name = report.get("policy_domain")
    if not domain_name:
        stats["errors"].append(f"{source_file}: no policy_published/domain, skipped")
        return
    if report.get("date_begin") is None or report.get("date_end") is None:
        stats["errors"].append(f"{source_file}: missing date_range, skipped")
        return

    domain_id = get_or_create_domain(conn, domain_name)
    try:
        cur = conn.execute(
            """INSERT INTO reports
               (domain_id, org_name, email, report_id, date_begin, date_end,
                policy_domain, policy_adkim, policy_aspf, policy_p, policy_sp, policy_pct, policy_fo, source_file)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (domain_id, report.get("org_name"), report.get("email"), report.get("report_id"),
             report["date_begin"], report["date_end"], domain_name,
             report.get("policy_adkim"), report.get("policy_aspf"), report.get("policy_p"),
             report.get("policy_sp"), report.get("policy_pct"), report.get("policy_fo"), source_file),
        )
    except sqlite3.IntegrityError:
        stats["duplicates"] += 1
        return

    report_row_id = cur.lastrowid
    for rec in report["records"]:
        rcur = conn.execute(
            """INSERT INTO report_records
               (report_id, source_ip, count, disposition, dkim_result, spf_result, header_from, envelope_from)
               VALUES (?,?,?,?,?,?,?,?)""",
            (report_row_id, rec.get("source_ip"), rec.get("count"), rec.get("disposition"),
             rec.get("dkim_result"), rec.get("spf_result"), rec.get("header_from"), rec.get("envelope_from")),
        )
        record_id = rcur.lastrowid
        for auth in rec["auth_results"]:
            conn.execute(
                """INSERT INTO record_auth_results (record_id, mechanism, domain, selector, scope, result)
                   VALUES (?,?,?,?,?,?)""",
                (record_id, auth["mechanism"], auth.get("domain"), auth.get("selector"),
                 auth.get("scope"), auth.get("result")),
            )
    conn.commit()
    stats["reports_stored"] += 1
    stats["records_stored"] += len(report["records"])


def _process_blob(conn: sqlite3.Connection, filename: str, raw_bytes: bytes, source_label: str, stats: dict) -> None:
    found = False
    for label, xml_bytes in iter_xml_blobs(filename, raw_bytes):
        found = True
        stats["attachments_seen"] += 1
        try:
            report = parse_feedback_xml(xml_bytes)
            store_report(conn, report, f"{source_label}!{label}", stats)
        except ET.ParseError as e:
            stats["errors"].append(f"{source_label}!{label}: XML parse error: {e}")
    if not found and filename.lower().endswith((".xml", ".zip", ".gz")):
        stats["errors"].append(f"{source_label}!{filename}: unrecognized content, skipped")


def ingest_source(conn: sqlite3.Connection, source_path: Path, stats: dict) -> None:
    source_path = Path(source_path)

    if source_path.is_dir():
        for f in sorted(source_path.rglob("*")):
            if f.is_file():
                ingest_source(conn, f, stats)
        return

    if source_path.suffix.lower() == ".mbox":
        for fn, payload in iter_attachments_from_mbox(source_path):
            _process_blob(conn, fn, payload, str(source_path), stats)
        return

    raw = source_path.read_bytes()

    if raw[:4] == MAGIC_ZIP:
        with zipfile_open(raw) as zf:
            names = zf.namelist()
            mbox_entries = [n for n in names if n.lower().endswith(".mbox")]
            if mbox_entries:
                for n in mbox_entries:
                    with tempfile.NamedTemporaryFile(suffix=".mbox", delete=False) as tmp:
                        tmp.write(zf.read(n))
                        tmp_path = Path(tmp.name)
                    try:
                        for fn, payload in iter_attachments_from_mbox(tmp_path):
                            _process_blob(conn, fn, payload, f"{source_path}!{n}", stats)
                    finally:
                        tmp_path.unlink()
                return

    _process_blob(conn, source_path.name, raw, str(source_path), stats)


def print_summary(conn: sqlite3.Connection, stats: dict) -> None:
    print(f"Attachments scanned : {stats['attachments_seen']}")
    print(f"Reports stored      : {stats['reports_stored']}")
    print(f"Records stored      : {stats['records_stored']}")
    print(f"Duplicates skipped  : {stats['duplicates']}")
    print(f"Errors              : {len(stats['errors'])}")
    for e in stats["errors"][:20]:
        print(f"  - {e}")
    if len(stats["errors"]) > 20:
        print(f"  ... and {len(stats['errors']) - 20} more")

    print("\nPer-domain report counts:")
    rows = conn.execute(
        """SELECT d.name, COUNT(*) as n, MIN(r.date_begin) as earliest, MAX(r.date_end) as latest
           FROM reports r JOIN domains d ON d.id = r.domain_id
           GROUP BY d.name ORDER BY n DESC"""
    ).fetchall()
    for row in rows:
        earliest = datetime.datetime.utcfromtimestamp(row["earliest"]).date() if row["earliest"] else "?"
        latest = datetime.datetime.utcfromtimestamp(row["latest"]).date() if row["latest"] else "?"
        print(f"  {row['name']:<28} {row['n']:>4} reports   {earliest} .. {latest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest DMARC aggregate reports into the DMARCTool database")
    parser.add_argument("source", help="Path to a .mbox file, a Takeout zip, a directory, or a single report file")
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)
    stats = {"attachments_seen": 0, "reports_stored": 0, "records_stored": 0, "duplicates": 0, "errors": []}
    ingest_source(conn, Path(args.source), stats)
    print_summary(conn, stats)


if __name__ == "__main__":
    main()
