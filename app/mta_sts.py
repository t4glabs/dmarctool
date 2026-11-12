"""
Zero-credential MTA-STS (RFC 8461) and TLS-RPT (RFC 8460) checks -- whether
inbound mail to a domain is protected against SMTP TLS downgrade/
interception attacks, and whether the domain has visibility into TLS
delivery failures. Same dig-based, no-API-key pattern as compliance.py,
plus one plain HTTPS GET (stdlib urllib, no new dependency) to fetch the
MTA-STS policy file itself -- the DNS record alone only proves intent to
participate, not that the policy is actually reachable/valid.

Deliberately informational rather than alarming when a domain simply has
never set this up: MTA-STS/TLS-RPT are optional, newer protocols with
inconsistent support across mail providers (unlike SPF/DKIM/DMARC, which
are now baseline expectations) -- most small nonprofit domains won't have
this, and that's not a problem worth an action item on its own. An action
item only fires when a domain has *started* down this path (a DNS record
exists) but it's actually broken -- a dangling, misconfigured record is
worse than no record at all, since it invites participating receivers to
try and fail rather than just falling back to opportunistic TLS.
"""

import argparse
import datetime
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app import dns_lookup
from app.analysis import all_domains, ensure_default_settings, upsert_system_action
from app.db import get_connection, init_db
from app.email_verifier import mx_lookup

MAX_WORKERS = 10


def _dig_txt(name: str, timeout: float = 6.0):
    return dns_lookup.txt(name, timeout)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """RFC 8461 section 3.3: "HTTP 3xx redirects MUST NOT be followed".
    Sending servers don't follow them, so a policy only reachable through a
    redirect is a policy nobody applies -- urllib's default of following it
    let Vigil call such a domain "ok"."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _fetch_policy(domain: str, timeout: float = 6.0):
    """GET of the MTA-STS policy file, the way a sending server does it (no
    redirects). Returns (text, error)."""
    url = f"https://mta-sts.{domain}/.well-known/mta-sts.txt"
    req = urllib.request.Request(url, headers={"User-Agent": "DMARCTool/1.0"})
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.read().decode(errors="replace"), None
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            return None, (f"HTTP {e.code} redirect to {e.headers.get('Location', '?')} -- sending servers "
                          f"must not follow redirects (RFC 8461 section 3.3), so they never see the policy")
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return None, f"network error: {e}"


def _parse_policy(text: str) -> dict:
    tags = {"mx": []}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "mx":
            tags["mx"].append(value)
        else:
            tags[key] = value
    return tags


def mx_matches(host: str, patterns) -> bool:
    """RFC 8461 section 4.1: a pattern matches the MX host exactly, or with a
    leading "*." standing for exactly one leftmost label."""
    host = host.lower().rstrip(".")
    for pattern in patterns:
        pattern = pattern.lower().rstrip(".")
        if pattern.startswith("*."):
            if "." in host and host.split(".", 1)[1] == pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def check_mta_sts(domain: str, txt_records=None, mx_hosts=None) -> dict:
    """{"status": "not_set_up"|"broken"|"withdrawn"|"ok"|"lookup_failed", "mode", "mx", "max_age", "note"}.

    mx_hosts: the domain's live MX hostnames (None = not known); in enforce
    mode each must match an mx: line, or senders refuse to deliver to it."""
    if txt_records is None:
        txt_records = _dig_txt(f"_mta-sts.{domain}")
    if txt_records is None:
        return {"status": "lookup_failed", "note": "Couldn't look up the MTA-STS DNS record this time."}
    sts_records = [r for r in txt_records if r.lower().startswith("v=stsv1")]

    if not sts_records:
        return {"status": "not_set_up",
                "note": "No MTA-STS DNS record -- optional, most domains don't have this set up yet."}

    policy_text, err = _fetch_policy(domain)
    url = f"https://mta-sts.{domain}/.well-known/mta-sts.txt"
    if err:
        return {"status": "broken",
                "note": (f"DNS record exists (v=STSv1) but the policy file at {url} couldn't be fetched ({err}) -- "
                         f"any receiver that checks MTA-STS will find a dangling policy.")}

    tags = _parse_policy(policy_text)
    if tags.get("version") != "STSv1":
        return {"status": "broken",
                "note": f"Policy file at {url} doesn't start with version: STSv1 (found {tags.get('version')!r}), "
                        f"so senders discard it."}
    mode = tags.get("mode")
    if mode not in ("enforce", "testing", "none"):
        return {"status": "broken",
                "note": f"Policy file at {url} doesn't declare a valid mode -- found {mode!r}."}
    if not (tags.get("max_age") or "").isdigit():
        return {"status": "broken",
                "note": f"Policy file at {url} has no valid max_age (found {tags.get('max_age')!r}), so senders "
                        f"discard it."}
    if mode == "none":
        # Not a test: mode none is how a domain withdraws its policy (RFC 8461
        # section 8.3), telling senders to forget what they cached.
        return {"status": "withdrawn", "mode": mode, "mx": tags["mx"], "max_age": tags.get("max_age"),
                "note": "mode: none -- the policy is being withdrawn, so nothing is enforced."}
    if not tags.get("mx"):
        return {"status": "broken",
                "note": "Policy file has no mx: entries -- receivers won't know which servers to expect TLS from."}

    note = f"mode={mode}, {len(tags['mx'])} mx pattern(s), max_age={tags.get('max_age')}s"
    if mx_hosts:
        unlisted = [h for h in mx_hosts if not mx_matches(h, tags["mx"])]
        if unlisted and mode == "enforce":
            return {"status": "broken", "mode": mode, "mx": tags["mx"], "max_age": tags.get("max_age"),
                    "note": (f"mode is enforce but mail server(s) {', '.join(unlisted)} aren't covered by the "
                             f"policy's mx: lines ({', '.join(tags['mx'])}), so senders that apply MTA-STS will "
                             f"refuse to deliver to them.")}
        if unlisted:
            note += f"; {', '.join(unlisted)} not covered by an mx: line (fix before switching to enforce)"
    elif mx_hosts is None:
        note += "; couldn't check the policy against the live MX records this time"

    return {"status": "ok", "mode": mode, "mx": tags["mx"], "max_age": tags.get("max_age"), "note": note}


def check_tls_rpt(domain: str, txt_records=None) -> dict:
    """{"status": "not_set_up"|"broken"|"ok", "rua", "note"}."""
    if txt_records is None:
        txt_records = _dig_txt(f"_smtp._tls.{domain}")
    rpt_records = [r for r in (txt_records or []) if r.lower().startswith("v=tlsrptv1")]
    if not rpt_records:
        return {"status": "not_set_up",
                "note": "No TLS-RPT DNS record -- optional, gives visibility into TLS delivery failures if set up."}
    rua_match = re.search(r"rua=([^;]+)", rpt_records[0], re.IGNORECASE)
    if not rua_match:
        return {"status": "broken", "note": f"TLS-RPT record found but has no rua= reporting address: {rpt_records[0]}"}
    return {"status": "ok", "rua": rua_match.group(1).strip(), "note": f"reports to {rua_match.group(1).strip()}"}


def _record(conn, domain_id, mta_sts, tls_rpt):
    conn.execute(
        """INSERT INTO mta_sts_checks
           (domain_id, mta_sts_status, mta_sts_mode, mta_sts_mx, mta_sts_max_age, mta_sts_note,
            tlsrpt_status, tlsrpt_rua, tlsrpt_note)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (domain_id, mta_sts["status"], mta_sts.get("mode"),
         ", ".join(mta_sts["mx"]) if mta_sts.get("mx") else None,
         mta_sts.get("max_age"), mta_sts["note"],
         tls_rpt["status"], tls_rpt.get("rua"), tls_rpt["note"]),
    )


def _stale(conn, domain_id, recheck_hours):
    row = conn.execute(
        "SELECT MAX(checked_at) as last_checked FROM mta_sts_checks WHERE domain_id=?", (domain_id,)
    ).fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def run_mta_sts_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    # Reuses compliance_recheck_hours -- MTA-STS/TLS-RPT records change about
    # as rarely as SPF/DKIM/PTR, not worth a dedicated setting.
    recheck_hours = int(settings["compliance_recheck_hours"])
    domains = all_domains(conn)
    to_check = [d for d in domains if _stale(conn, d["id"], recheck_hours)]
    if not to_check:
        if verbose:
            print("[mta_sts] all domains checked recently, skipping")
        return

    def _check_one(d):
        mta_sts_txt = _dig_txt(f"_mta-sts.{d['name']}")
        tls_rpt_txt = _dig_txt(f"_smtp._tls.{d['name']}")
        mx = mx_lookup(d["name"]) if mta_sts_txt else []
        mx_hosts = None if mx is None else [host for _, host in mx]
        return (d, check_mta_sts(d["name"], txt_records=mta_sts_txt, mx_hosts=mx_hosts),
                check_tls_rpt(d["name"], txt_records=tls_rpt_txt))

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_check))) as ex:
        results = list(ex.map(_check_one, to_check))

    for d, mta_sts, tls_rpt in results:
        _record(conn, d["id"], mta_sts, tls_rpt)
        if mta_sts["status"] == "broken":
            upsert_system_action(
                conn, d["id"], "mta_sts_broken", None,
                f"{d['name']}: MTA-STS record exists but is broken",
                mta_sts["note"],
            )
        elif mta_sts["status"] != "lookup_failed":
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='mta_sts_broken' AND status='open'""",
                (d["id"],),
            )
        if verbose:
            print(f"=== {d['name']} ===")
            print(f"  MTA-STS: {mta_sts['status']} -- {mta_sts['note']}")
            print(f"  TLS-RPT: {tls_rpt['status']} -- {tls_rpt['note']}")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check MTA-STS and TLS-RPT DNS records + policy validity")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_mta_sts_checks(conn)


if __name__ == "__main__":
    main()
