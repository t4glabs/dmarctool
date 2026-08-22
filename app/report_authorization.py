"""
External-destination authorization for DMARC report addresses (RFC 7489 s7.1).

When a domain's DMARC record sends reports to a mailbox on a *different*
domain -- `_dmarc.arpo.in` with `rua=mailto:dmarc-reports@aikyamfellows.org`
-- the receiving domain has to say it consents, by publishing a TXT record at

    <reporting-domain>._report._dmarc.<destination-domain>

containing at least `v=DMARC1`. A wildcard (`*._report._dmarc.<dest>`)
authorizes every domain at once, which is the right shape here: one record on
the domain that collects, rather than one per domain that reports.

Why this is worth checking even though reports are arriving: a receiver that
enforces the rule silently stops sending, and "no reports" looks exactly like
"no problems". There is no error, no bounce, and the dashboard just goes quiet
-- so this is the one DMARC misconfiguration that gets *less* visible the
worse it gets. Every commercial DMARC tool's record checker tests it; we
didn't.

Deliberately reported as a warning rather than a failure, and the wording says
so: as of the first run every tracked domain lacked its authorization record
while Google, Outlook, GoDaddy and Zoho were all still sending reports. Calling
that "broken" would be false. It is a latent gap, and one wildcard record
closes it for all of them.

`dig` only, no credentials -- same as every other DNS check here.
"""

import argparse
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from app.analysis import all_domains, ensure_default_settings, upsert_system_action
from app.db import get_connection, init_db

MAX_WORKERS = 10

# rua/ruf values are comma-separated URIs, each optionally carrying a size
# limit after an exclamation mark (rua=mailto:x@y.com!10m).
_MAILTO_RE = re.compile(r"^\s*mailto:([^@!\s]+)@([^!\s,]+)", re.IGNORECASE)


def _txt(name: str, timeout: float = 5.0):
    """TXT record strings for `name`, or None if the lookup itself failed.
    An empty list means "looked it up, nothing there" -- a real answer, and
    distinct from a network failure, which must never be reported as missing
    consent."""
    try:
        out = subprocess.run(
            ["dig", "+short", "TXT", name],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    values = []
    for line in out.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # dig prints TXT values quoted, and splits strings over 255 chars into
        # several adjacent quoted chunks that are meant to be concatenated.
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
        values.append("".join(parts) if parts else line)
    return values


def destination_domains(rua_value: str, own_domain: str) -> list:
    """Distinct external destination domains in an rua= value.

    A destination is "external" only if it is neither the domain itself nor a
    parent/child of it: aikyamhq.com reporting to dmarc@mail.aikyamhq.com needs
    no authorization, since it is the same organizational domain."""
    own = own_domain.strip().lower().rstrip(".")
    out = []
    for uri in (rua_value or "").split(","):
        m = _MAILTO_RE.match(uri)
        if not m:
            continue
        dest = m.group(2).strip().lower().rstrip(".")
        if not dest or dest == own:
            continue
        if dest.endswith("." + own) or own.endswith("." + dest):
            continue
        if dest not in out:
            out.append(dest)
    return out


def check_authorization(reporting_domain: str, destination_domain: str) -> dict:
    """{"status": "authorized"|"missing"|"lookup_failed", "via", "record", "note"}

    Checks the domain-specific record first, then the wildcard -- the order
    receivers use, and the order that makes the note useful ("authorized by a
    wildcard" is worth saying, because it silently covers future domains too).
    """
    specific = f"{reporting_domain}._report._dmarc.{destination_domain}"
    wildcard = f"*._report._dmarc.{destination_domain}"

    for name, via in ((specific, "exact"), (wildcard, "wildcard")):
        values = _txt(name)
        if values is None:
            return {"status": "lookup_failed", "via": None, "record": None,
                    "note": f"couldn't look up {name} (network/dig error)"}
        for v in values:
            if v.strip().lower().startswith("v=dmarc1"):
                return {"status": "authorized", "via": via, "record": v,
                        "note": (f"{destination_domain} authorizes reports for {reporting_domain}"
                                 + (" via a wildcard record" if via == "wildcard" else ""))}

    return {
        "status": "missing", "via": None, "record": None,
        "note": (f"{destination_domain} has no record authorizing it to receive DMARC reports for "
                 f"{reporting_domain}. Reports are still arriving today, so nothing is broken right "
                 f"now, but a receiver that enforces RFC 7489 section 7.1 would stop sending "
                 f"silently."),
    }


def _record_result(conn, domain_id, destination, result):
    conn.execute(
        """INSERT INTO report_auth_checks (domain_id, destination_domain, status, authorized_via, note)
           VALUES (?,?,?,?,?)""",
        (domain_id, destination, result["status"], result["via"], result["note"]),
    )


def _apply(conn, domain_id: int, domain_name: str, results: list) -> int:
    """Store each destination's result and raise or clear its action item.
    Returns how many destinations are unauthorized."""
    missing = 0
    destinations = [dest for dest, _ in results]

    for dest, result in results:
        _record_result(conn, domain_id, dest, result)
        if result["status"] == "missing":
            missing += 1
            upsert_system_action(
                conn, domain_id, "rua_unauthorized", dest,
                f"{domain_name}: {dest} hasn't authorized it to receive its DMARC reports",
                result["note"],
            )
        else:
            # Covers "authorized" AND "lookup_failed" -- a failed lookup is not
            # evidence of a problem, and holding an item open through a network
            # blip would be worse than missing one cycle.
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='rua_unauthorized' AND ref_key=? AND status='open'""",
                (domain_id, dest),
            )

    # Don't keep an item about a destination this domain no longer reports to.
    if destinations:
        placeholders = ",".join("?" * len(destinations))
        conn.execute(
            f"""UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                WHERE domain_id=? AND category='rua_unauthorized' AND status='open'
                  AND ref_key NOT IN ({placeholders})""",
            (domain_id, *destinations),
        )
    else:
        conn.execute(
            """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
               WHERE domain_id=? AND category='rua_unauthorized' AND status='open'""",
            (domain_id,),
        )
    return missing


def latest_report_auth(conn, domain_id: int):
    """Most recent check per destination domain, for the domain page."""
    return conn.execute(
        """SELECT * FROM report_auth_checks WHERE domain_id=?
             AND id IN (SELECT MAX(id) FROM report_auth_checks WHERE domain_id=? GROUP BY destination_domain)
           ORDER BY destination_domain""",
        (domain_id, domain_id),
    ).fetchall()


def run_report_auth_checks(conn, verbose: bool = True) -> None:
    """One pass over every tracked domain, reading each one's rua= from the
    latest stored DNS check rather than re-querying _dmarc (dns_check.py has
    already done that this cycle)."""
    ensure_default_settings(conn)
    from app.dns_check import parse_dmarc_tags

    targets = []
    for d in all_domains(conn):
        row = conn.execute(
            """SELECT txt_value FROM dns_checks WHERE domain_id=? AND txt_value IS NOT NULL
               ORDER BY checked_at DESC LIMIT 1""",
            (d["id"],),
        ).fetchone()
        if not row:
            continue
        rua = parse_dmarc_tags(row["txt_value"]).get("rua")
        if rua:
            targets.append((d["id"], d["name"], rua))

    if not targets:
        if verbose:
            print("[report_auth] no domains with an rua= address yet")
        return

    # One DNS round trip per (domain, destination) pair, at most two, so fan
    # out the same way the other DNS checks here do.
    def _one(t):
        domain_id, name, rua = t
        return t, [(dest, check_authorization(name, dest))
                   for dest in destination_domains(rua, name)]

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(targets))) as ex:
        all_results = list(ex.map(_one, targets))

    missing = 0
    for (domain_id, name, _rua), results in all_results:
        missing += _apply(conn, domain_id, name, results)
        if verbose:
            for dest, result in results:
                print(f"  {name} -> {dest}: {result['status']}"
                      + (f" ({result['via']})" if result.get("via") else ""))
    conn.commit()
    if verbose:
        print(f"[report_auth] {len(targets)} domain(s) checked, {missing} unauthorized destination(s)")


def main() -> None:
    argparse.ArgumentParser(description="Check DMARC report external-destination authorization").parse_args()
    conn = get_connection()
    init_db(conn)
    run_report_auth_checks(conn)


if __name__ == "__main__":
    main()
