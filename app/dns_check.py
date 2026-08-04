"""
Live DNS drift check.

Runs `dig TXT _dmarc.<domain>` for every tracked domain, parses the DMARC tags,
and compares them against whichever is more authoritative:
  - a manual_log policy_history entry (ground truth you logged yourself), if one
    is open-ended for this domain, else
  - the report-derived current policy_history run.

A mismatch against manual_log data is a real signal (DNS doesn't match what you
say you set). A mismatch against report-derived data alone is *ambiguous* -- it's
exactly as likely to mean "reports haven't caught up to a recent change yet" as
"DNS is wrong" -- so that case is worded as such rather than as an alarm.

Also flags: no DMARC TXT record found, more than one found (a spec violation --
receivers may disregard all of them), and DNS lookup failures.
"""

import argparse
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from app.analysis import all_domains, current_policy_run, ensure_default_settings, upsert_system_action
from app.db import get_connection, init_db

MAX_WORKERS = 10


def dig_txt(domain: str, timeout: float = 5.0):
    """Raw TXT record strings for _dmarc.<domain>, or None on lookup failure."""
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=2", "TXT", f"_dmarc.{domain}"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    records = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
        records.append("".join(parts) if parts else line)
    return records


def parse_dmarc_tags(txt: str) -> dict:
    tags = {}
    for chunk in txt.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        tags[k.strip().lower()] = v.strip()
    return tags


def _record(conn, domain_id, status, txt_value, parsed_p, parsed_pct, matches_expected, note):
    conn.execute(
        """INSERT INTO dns_checks (domain_id, status, txt_value, parsed_p, parsed_pct, matches_expected, note)
           VALUES (?,?,?,?,?,?,?)""",
        (domain_id, status, txt_value, parsed_p, parsed_pct, matches_expected, note),
    )


def check_domain(conn, domain_id: int, domain_name: str, records=None) -> dict:
    if records is None:
        records = dig_txt(domain_name)

    if records is None:
        note = "DNS lookup failed (network/dig error)"
        _record(conn, domain_id, "lookup_failed", None, None, None, 0, note)
        upsert_system_action(conn, domain_id, "dns_drift", None, f"{domain_name}: DNS lookup failed", note)
        return {"status": "lookup_failed", "note": note}

    dmarc_records = [r for r in records if r.lower().startswith("v=dmarc1")]

    if not dmarc_records:
        note = f"No DMARC TXT record found at _dmarc.{domain_name}"
        _record(conn, domain_id, "missing", None, None, None, 0, note)
        upsert_system_action(conn, domain_id, "dns_drift", None, f"{domain_name}: no DMARC record found", note)
        return {"status": "missing", "note": note}

    if len(dmarc_records) > 1:
        txt_value = " | ".join(dmarc_records)
        note = f"{len(dmarc_records)} DMARC TXT records found (spec violation -- receivers may ignore all of them)"
        _record(conn, domain_id, "multiple", txt_value, None, None, 0, note)
        upsert_system_action(conn, domain_id, "dns_drift", None,
                              f"{domain_name}: multiple DMARC TXT records (ambiguous)", note)
        return {"status": "multiple", "note": note, "records": dmarc_records}

    txt_value = dmarc_records[0]
    tags = parse_dmarc_tags(txt_value)
    parsed_p = tags.get("p")
    parsed_pct = int(tags["pct"]) if tags.get("pct", "").isdigit() else 100  # RFC 7489 default

    manual_run = conn.execute(
        """SELECT * FROM policy_history WHERE domain_id=? AND source='manual_log' AND observed_to IS NULL
           ORDER BY observed_from DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()
    report_run = current_policy_run(conn, domain_id)

    expected = manual_run or report_run
    expected_source = "manual_log" if manual_run else ("report" if report_run else None)

    matches = None
    note = "matches expected policy"
    if expected is not None:
        expected_pct = 100 if expected["pct"] is None else expected["pct"]  # RFC 7489 default
        matches = (expected["p"] == parsed_p and expected_pct == parsed_pct)
        if not matches:
            note = (f"live DNS shows p={parsed_p}/pct={parsed_pct}, {expected_source} data shows "
                    f"p={expected['p']}/pct={expected_pct}")
            if expected_source == "report":
                note += " -- if you changed DNS recently this may just be report lag, not a real problem"
    else:
        note = "no prior report or manual-log data to compare against yet"

    _record(conn, domain_id, "ok", txt_value, parsed_p, parsed_pct,
            (1 if matches else 0) if matches is not None else None, note)

    if matches is False:
        upsert_system_action(conn, domain_id, "dns_drift", None,
                              f"{domain_name}: live DNS disagrees with {expected_source} data", note)
    else:
        conn.execute(
            """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
               WHERE domain_id=? AND category='dns_drift' AND status='open'""",
            (domain_id,),
        )

    return {"status": "ok", "txt_value": txt_value, "parsed_p": parsed_p, "parsed_pct": parsed_pct,
            "matches": matches, "expected_source": expected_source, "note": note}


def run_dns_checks(conn, verbose: bool = True) -> None:
    ensure_default_settings(conn)
    domains = all_domains(conn)
    names = [d["name"] for d in domains]

    # dig_txt is pure network I/O with no DB access, so it's safe to fan out --
    # the actual DB reads/writes in check_domain() below stay sequential on
    # this one connection.
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(names) or 1)) as ex:
        records_by_name = dict(zip(names, ex.map(dig_txt, names))) if names else {}

    for domain in domains:
        result = check_domain(conn, domain["id"], domain["name"], records=records_by_name[domain["name"]])
        conn.commit()
        if verbose:
            print(f"=== {domain['name']} ===")
            if result["status"] == "ok":
                flag = "OK" if result["matches"] else ("MISMATCH" if result["matches"] is False else "no baseline yet")
                print(f"  live: p={result['parsed_p']} pct={result['parsed_pct']}  [{flag}]")
                if result["matches"] is not True:
                    print(f"  {result['note']}")
            else:
                print(f"  {result['status']}: {result['note']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check live DMARC DNS TXT records against report/manual-log data")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_dns_checks(conn)


if __name__ == "__main__":
    main()
