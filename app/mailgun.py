"""
Mailgun reputation + list-hygiene monitoring.

Pulls, for whichever domains DMARCTool already tracks (matched dynamically each
run against whatever domains actually exist on the Mailgun account -- root
domain or any subdomain, e.g. `mails.pattic.org` under tracked `pattic.org`):

  - delivered/bounced/complained/unsubscribed stats over a rolling window
    (Mailgun's own reputation numbers -- not a substitute for Postmaster Tools'
    Gmail-specific spam rate, since Gmail generally doesn't feed spam complaints
    back to third-party ESPs the way Yahoo does; treat this as a partial signal).
  - suppression lists (bounces/complaints/unsubscribes) -- literal addresses
    Mailgun has stopped sending to, which is the actual "clean your list" data:
    these are worth pruning from Listmonk too.

Uses stdlib `urllib` (no new dependency) and the API key in secrets.env --
never hardcoded, never stored in the SQLite DB. If no key is configured, this
is a no-op (so the rest of the check pipeline isn't blocked on it).
"""

import argparse
import base64
import datetime
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app.analysis import ensure_default_settings, upsert_system_action
from app.config import get_secret
from app.db import get_connection, init_db

API_BASE = "https://api.mailgun.net/v3"
SUPPRESSION_KINDS = {"bounces": "bounce", "complaints": "complaint", "unsubscribes": "unsubscribe"}
MAX_WORKERS = 6


def _auth_header(api_key: str) -> str:
    return "Basic " + base64.b64encode(f"api:{api_key}".encode()).decode()


def _get(url: str, api_key: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"Authorization": _auth_header(api_key)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.reason}"
    except (urllib.error.URLError, TimeoutError) as e:
        return None, f"network error: {e}"


def list_mailgun_domains(api_key: str):
    """All domain names on the Mailgun account, or (None, error)."""
    names, url = [], f"{API_BASE}/domains?limit=300"
    while url:
        data, err = _get(url, api_key)
        if err:
            return None, err
        items = data.get("items", [])
        names.extend(d["name"] for d in items)
        nxt = data.get("paging", {}).get("next")
        url = nxt if nxt and items else None
    return names, None


def match_tracked_domains(conn, mailgun_domains):
    """{mailgun_domain_name: (domain_id, tracked_domain_name)} for whichever
    Mailgun domains correspond to a domain (or subdomain of one) already in
    DMARCTool -- recomputed fresh each run, so newly ingested domains are
    picked up automatically with no config change here.
    """
    tracked = conn.execute("SELECT id, name FROM domains").fetchall()
    matches = {}
    for mg_name in mailgun_domains:
        for row in tracked:
            if mg_name == row["name"] or mg_name.endswith("." + row["name"]):
                matches[mg_name] = (row["id"], row["name"])
                break
    return matches


def fetch_stats(mailgun_domain: str, api_key: str, window_days: int):
    """Summed accepted/delivered/failed/complained/unsubscribed over the window."""
    url = f"{API_BASE}/{mailgun_domain}/stats/total?duration={window_days}d&" + "&".join(
        f"event={e}" for e in ("accepted", "delivered", "failed", "complained", "unsubscribed")
    )
    data, err = _get(url, api_key)
    if err:
        return None, err
    totals = {"accepted": 0, "delivered": 0, "failed_perm": 0, "failed_temp": 0, "complained": 0, "unsubscribed": 0}
    for bucket in data.get("stats", []):
        totals["accepted"] += bucket.get("accepted", {}).get("total", 0)
        totals["delivered"] += bucket.get("delivered", {}).get("total", 0)
        totals["failed_perm"] += bucket.get("failed", {}).get("permanent", {}).get("total", 0)
        totals["failed_temp"] += bucket.get("failed", {}).get("temporary", {}).get("total", 0)
        totals["complained"] += bucket.get("complained", {}).get("total", 0)
        totals["unsubscribed"] += bucket.get("unsubscribed", {}).get("total", 0)
    return totals, None


def fetch_suppressions(mailgun_domain: str, kind: str, api_key: str):
    """All items for one suppression endpoint ('bounces'|'complaints'|'unsubscribes')."""
    items, url = [], f"{API_BASE}/{mailgun_domain}/{kind}?limit=300"
    while url:
        data, err = _get(url, api_key)
        if err:
            return None, err
        page = data.get("items", [])
        items.extend(page)
        nxt = data.get("paging", {}).get("next")
        url = nxt if nxt and page else None
    return items, None


def _stale(conn, mailgun_domain, recheck_hours):
    row = conn.execute(
        "SELECT MAX(checked_at) as last_checked FROM mailgun_stats WHERE mailgun_domain=?", (mailgun_domain,)
    ).fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def _fetch_all(mailgun_domain, api_key, window_days):
    """All network I/O for one domain -- no DB access, safe to run concurrently."""
    stats, stats_err = fetch_stats(mailgun_domain, api_key, window_days)
    suppressions = {}
    for endpoint, kind in SUPPRESSION_KINDS.items():
        items, err = fetch_suppressions(mailgun_domain, endpoint, api_key)
        suppressions[kind] = (items, err)
    return {"stats": (stats, stats_err), "suppressions": suppressions}


def run_mailgun_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    api_key = get_secret("MAILGUN_API_KEY")
    if not api_key:
        if verbose:
            print("[mailgun] no MAILGUN_API_KEY in secrets.env -- skipping")
        return

    recheck_hours = int(settings["mailgun_recheck_hours"])
    window_days = int(settings["mailgun_stats_window_days"])
    bounce_warn = float(settings["mailgun_bounce_rate_warn"])
    complaint_warn = float(settings["mailgun_complaint_rate_warn"])

    mg_domains, err = list_mailgun_domains(api_key)
    if err:
        if verbose:
            print(f"[mailgun] could not list domains: {err}")
        return

    matches = match_tracked_domains(conn, mg_domains)
    if verbose:
        print(f"[mailgun] {len(matches)} Mailgun domain(s) match a tracked domain "
              f"(of {len(mg_domains)} on the account)")

    to_check = [mg for mg in matches if _stale(conn, mg, recheck_hours)]
    if verbose:
        skipped = len(matches) - len(to_check)
        if skipped:
            print(f"[mailgun] {skipped} domain(s) checked recently, skipping")

    fetched = {}
    if to_check:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_check))) as ex:
            fetched = dict(zip(
                to_check, ex.map(lambda mg: _fetch_all(mg, api_key, window_days), to_check)
            ))

    for mailgun_domain in to_check:
        domain_id, domain_name = matches[mailgun_domain]
        stats, err = fetched[mailgun_domain]["stats"]
        if err:
            if verbose:
                print(f"[mailgun] {mailgun_domain}: stats fetch failed -- {err}")
        else:
            conn.execute(
                """INSERT INTO mailgun_stats
                   (domain_id, mailgun_domain, window_days, accepted, delivered, failed_perm, failed_temp,
                    complained, unsubscribed)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (domain_id, mailgun_domain, window_days, stats["accepted"], stats["delivered"],
                 stats["failed_perm"], stats["failed_temp"], stats["complained"], stats["unsubscribed"]),
            )
            accepted = stats["accepted"] or 0
            bounce_rate = stats["failed_perm"] / accepted if accepted else 0.0
            complaint_rate = stats["complained"] / accepted if accepted else 0.0
            if verbose:
                print(f"[mailgun] {mailgun_domain} ({domain_name}): {accepted} accepted, "
                      f"{bounce_rate:.2%} bounced, {complaint_rate:.2%} complained (last {window_days}d)")

            if accepted and (bounce_rate >= bounce_warn or complaint_rate >= complaint_warn):
                upsert_system_action(
                    conn, domain_id, "mailgun_reputation", mailgun_domain,
                    f"{domain_name}: Mailgun bounce/complaint rate is elevated ({mailgun_domain})",
                    f"{bounce_rate:.2%} bounced, {complaint_rate:.2%} complained over the last {window_days}d "
                    f"({accepted} accepted). Check list quality before sending more.",
                )
            else:
                conn.execute(
                    """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                       WHERE category='mailgun_reputation' AND ref_key=? AND status='open'""",
                    (mailgun_domain,),
                )

        new_counts = {}
        for endpoint, kind in SUPPRESSION_KINDS.items():
            items, err = fetched[mailgun_domain]["suppressions"][kind]
            if err:
                if verbose:
                    print(f"[mailgun] {mailgun_domain}/{endpoint}: fetch failed -- {err}")
                continue

            existing = {
                row["email"] for row in conn.execute(
                    "SELECT email FROM mailgun_suppressions WHERE mailgun_domain=? AND kind=?",
                    (mailgun_domain, kind),
                )
            }
            new_count = 0
            for item in items:
                address = item.get("address")
                if not address:
                    continue
                if address not in existing:
                    new_count += 1
                conn.execute(
                    """INSERT INTO mailgun_suppressions
                       (domain_id, mailgun_domain, email, kind, reason, suppressed_at)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(mailgun_domain, email, kind) DO UPDATE SET last_checked_at=datetime('now')""",
                    (domain_id, mailgun_domain, address, kind, item.get("error"), item.get("created_at")),
                )
            new_counts[kind] = new_count
            if verbose and items:
                print(f"[mailgun] {mailgun_domain}/{endpoint}: {len(items)} total, {new_count} new")

        new_complaints = new_counts.get("complaint", 0)
        new_bounces = new_counts.get("bounce", 0)
        if new_complaints or new_bounces:
            upsert_system_action(
                conn, domain_id, "mailgun_new_suppressions", mailgun_domain,
                f"{domain_name}: new Mailgun suppressions ({mailgun_domain})",
                f"{new_bounces} new bounce(s), {new_complaints} new complaint(s) since the last check -- "
                f"these addresses won't receive mail from Mailgun anymore; worth pruning from Listmonk too.",
            )

    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Mailgun stats and suppression lists for tracked domains")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_mailgun_checks(conn)


if __name__ == "__main__":
    main()
