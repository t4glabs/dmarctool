"""
Google Safe Browsing check -- confirms each tracked domain's own website
isn't flagged for malware/phishing/unwanted software. This is called out
explicitly in Gmail's sender guidelines ("check regularly that your domain
isn't listed as unsafe with Google Safe Browsing") and can independently hurt
email deliverability/trust separate from DMARC/authentication, which is why
none of the other checks in this tool would ever catch it.

Uses the Safe Browsing Lookup API v4 -- a plain API key, no OAuth, no
per-domain verification needed (unlike Search Console). Needs
SAFE_BROWSING_API_KEY in secrets.env. If missing, this is a no-op.
"""

import argparse
import datetime
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app.analysis import all_domains, ensure_default_settings, upsert_system_action
from app.config import get_secret
from app.db import get_connection, init_db

API_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
THREAT_TYPES = ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"]
MAX_WORKERS = 10


def check_url(url: str, api_key: str, timeout: float = 10.0):
    """Returns (threat_types, error). threat_types is [] if clean."""
    body = {
        "client": {"clientId": "dmarctool", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": THREAT_TYPES,
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}],
        },
    }
    req = urllib.request.Request(
        f"{API_URL}?key={api_key}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except (urllib.error.URLError, TimeoutError) as e:
        return None, f"network error: {e}"

    matches = data.get("matches", [])
    return sorted({m["threatType"] for m in matches}), None


def _stale(conn, domain_id, recheck_hours):
    row = conn.execute(
        "SELECT MAX(checked_at) as last_checked FROM safe_browsing_checks WHERE domain_id=?", (domain_id,)
    ).fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def run_safe_browsing_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    api_key = get_secret("SAFE_BROWSING_API_KEY")
    if not api_key:
        if verbose:
            print("[safe_browsing] no SAFE_BROWSING_API_KEY in secrets.env -- skipping")
        return

    recheck_hours = int(settings["safe_browsing_recheck_hours"])
    all_domains_list = all_domains(conn)
    domains = [d for d in all_domains_list if _stale(conn, d["id"], recheck_hours)]
    if verbose:
        skipped = len(all_domains_list) - len(domains)
        if skipped:
            print(f"[safe_browsing] {skipped} domain(s) checked recently, skipping")

    results = {}
    if domains:
        urls = [f"https://{d['name']}/" for d in domains]
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(urls))) as ex:
            results = dict(zip((d["id"] for d in domains), ex.map(lambda u: check_url(u, api_key), urls)))

    for d in domains:
        threat_types, err = results[d["id"]]
        if err:
            conn.execute(
                "INSERT INTO safe_browsing_checks (domain_id, status, note) VALUES (?,?,?)",
                (d["id"], "lookup_failed", err),
            )
            if verbose:
                print(f"[safe_browsing] {d['name']}: lookup failed -- {err}")
            continue

        if threat_types:
            note = f"Flagged for: {', '.join(threat_types)}"
            conn.execute(
                "INSERT INTO safe_browsing_checks (domain_id, status, threat_types, note) VALUES (?,?,?,?)",
                (d["id"], "flagged", ",".join(threat_types), note),
            )
            if verbose:
                print(f"[safe_browsing] {d['name']}: FLAGGED -- {note}")
            upsert_system_action(
                conn, d["id"], "safe_browsing_flagged", None,
                f"{d['name']}: flagged by Google Safe Browsing", note,
            )
        else:
            conn.execute(
                "INSERT INTO safe_browsing_checks (domain_id, status, note) VALUES (?,?,?)",
                (d["id"], "clean", "not flagged"),
            )
            if verbose:
                print(f"[safe_browsing] {d['name']}: clean")
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='safe_browsing_flagged' AND status='open'""",
                (d["id"],),
            )

    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check every tracked domain against Google Safe Browsing")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_safe_browsing_checks(conn)


if __name__ == "__main__":
    main()
