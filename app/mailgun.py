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
  - a delivered/failed breakdown by the real "From" identity in each message
    (via Mailgun's Events API), for domains where one Mailgun account is
    shared across several websites/campaigns (a common Aikyam setup) -- the
    stats/total endpoint above only gives one blended number for the whole
    Mailgun domain, which hides which specific sender identity is actually
    causing an elevated bounce/complaint rate.

Uses stdlib `urllib` (no new dependency) and the API key in secrets.env --
never hardcoded, never stored in the SQLite DB. If no key is configured, this
is a no-op (so the rest of the check pipeline isn't blocked on it).
"""

import argparse
import base64
import datetime
import email.utils
import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app.analysis import ensure_default_settings, upsert_system_action
from app.bounce_reasons import categorize_bounce
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


def send_message(mailgun_domain: str, api_key: str, from_addr: str, to_addr: str,
                  subject: str, text: str, html: str, cc_addr: str = None, timeout: float = 20.0):
    """Sends a single email via Mailgun's Messages API (POST {domain}/messages),
    same auth/base-URL conventions as every read-only call in this file --
    used by app.domain_report for the periodic plain-language owner reports.
    `to_addr`/`cc_addr` accept Mailgun's own comma-separated multi-address
    format directly. Returns (message_id, error)."""
    url = f"{API_BASE}/{mailgun_domain}/messages"
    fields = {"from": from_addr, "to": to_addr, "subject": subject, "text": text, "html": html}
    if cc_addr:
        fields["cc"] = cc_addr
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Authorization": _auth_header(api_key),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()).get("id"), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode(errors='replace')}"
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


def _bucket_totals(bucket):
    return {
        "accepted": bucket.get("accepted", {}).get("total", 0),
        "delivered": bucket.get("delivered", {}).get("total", 0),
        "failed_perm": bucket.get("failed", {}).get("permanent", {}).get("total", 0),
        "failed_temp": bucket.get("failed", {}).get("temporary", {}).get("total", 0),
        "complained": bucket.get("complained", {}).get("total", 0),
        "unsubscribed": bucket.get("unsubscribed", {}).get("total", 0),
    }


def fetch_stats(mailgun_domain: str, api_key: str, window_days: int):
    """Returns (totals, daily, error). Mailgun's stats/total endpoint already
    buckets by day within the window -- `daily` is [(day_str, day_totals), ...]
    pulled from the same response instead of throwing that breakdown away."""
    url = f"{API_BASE}/{mailgun_domain}/stats/total?duration={window_days}d&" + "&".join(
        f"event={e}" for e in ("accepted", "delivered", "failed", "complained", "unsubscribed")
    )
    data, err = _get(url, api_key)
    if err:
        return None, None, err
    totals = {"accepted": 0, "delivered": 0, "failed_perm": 0, "failed_temp": 0, "complained": 0, "unsubscribed": 0}
    daily = []
    for bucket in data.get("stats", []):
        day_totals = _bucket_totals(bucket)
        for k, v in day_totals.items():
            totals[k] += v
        try:
            day_str = email.utils.parsedate_to_datetime(bucket["time"]).date().isoformat()
            daily.append((day_str, day_totals))
        except (KeyError, TypeError, ValueError):
            continue
    return totals, daily, None


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


def fetch_identity_breakdown(mailgun_domain: str, api_key: str, window_days: int):
    """Delivered/retried/failed counts per real "From" identity, via
    Mailgun's Events API -- fetch_stats() above only gives one blended
    number for the whole Mailgun domain, which hides the real picture when
    several different websites/campaigns share one Mailgun account (a
    common Aikyam setup: several beneficiary orgs' mail going out under one
    shared domain).

    Deduplicates by (recipient, message-id) before counting anything as a
    failure. Mailgun automatically retries a message that gets a temporary
    (4xx) response on a backoff schedule -- found via a real investigation
    that one recipient's 18 raw "failed" events were actually just 3 real
    messages, each retried 6 times after a temporary rejection, all 3 of
    which eventually delivered fine. Counting every retry attempt as its
    own failure would have overstated that identity's real failure rate by
    a wide margin. A message only counts as a genuine failure here if it
    never shows up as delivered at all; one that needed retries but got
    through is tracked separately as "retried_ok" -- worth knowing about
    (a slow/strict recipient server), but not a real problem.

    Groups by the actual From email address parsed out of each event's own
    message headers, not by whichever Mailgun domain the event happens to be
    filed under. Mailgun's own event-log retention is limited (usually
    ~30 days depending on plan), and events are heavier per-page than
    suppressions, so this is deliberately given its own, shorter window
    (mailgun_events_window_days) rather than reusing the full stats window.
    Returns ({email_address: {"display": name_or_None, "delivered": n,
    "retried_ok": n, "failed": n}}, [per-failure detail dicts], error) --
    the failure list carries one entry per message that never ultimately
    delivered (recipient, subject, plain-language category via
    bounce_reasons.categorize_bounce() using its LAST retry attempt, raw
    reason, bounce type, timestamp of that last attempt), so a reader can
    download exactly the addresses behind one identity+category combination
    instead of one combined CSV they'd have to filter by hand. The subject
    matters in its own right for an identity that sends the same templated
    report for many different underlying sites (e.g. Plausible Analytics'
    "Weekly report for <site>.org") -- it's the only place that site's name
    actually appears, since From/To never carry it."""
    begin = email.utils.format_datetime(
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=window_days)
    )
    delivered_keys = set()       # {(recipient, message_id)} -- eventually succeeded, however many attempts it took
    failed_attempts = {}         # (recipient, message_id) -> latest failure detail (ascending order => last write is the final attempt)
    identity_by_key = {}         # (recipient, message_id) -> (addr, display)

    for event in ("delivered", "failed"):
        url = (f"{API_BASE}/{mailgun_domain}/events?event={event}"
               f"&begin={urllib.parse.quote(begin)}&ascending=yes&limit=300")
        while url:
            data, err = _get(url, api_key)
            if err:
                return None, None, err
            items = data.get("items", [])
            for item in items:
                headers = (item.get("message") or {}).get("headers") or {}
                raw_from = headers.get("from")
                if not raw_from:
                    continue
                display, addr = email.utils.parseaddr(raw_from)
                addr = (addr or raw_from).lower()
                recipient = item.get("recipient", "")
                message_id = headers.get("message-id", "")
                key = (recipient, message_id)
                identity_by_key[key] = (addr, display)

                if event == "delivered":
                    delivered_keys.add(key)
                else:
                    ds = item.get("delivery-status") or {}
                    reason_text = ds.get("message") or ds.get("description") or item.get("reason") or ""
                    severity = item.get("severity")
                    bounce_type = {"permanent": "Permanent", "temporary": "Transient"}.get(severity)
                    occurred_at = None
                    ts = item.get("timestamp")
                    if ts:
                        occurred_at = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    failed_attempts[key] = {
                        "recipient": recipient,
                        "subject": headers.get("subject", ""),
                        "reason": reason_text,
                        "bounce_type": bounce_type,
                        "occurred_at": occurred_at,
                    }
            nxt = data.get("paging", {}).get("next")
            url = nxt if nxt and items else None

    breakdown = {}
    failures = []
    for key in delivered_keys:
        addr, display = identity_by_key[key]
        entry = breakdown.setdefault(addr, {"display": None, "delivered": 0, "retried_ok": 0, "failed": 0})
        entry["delivered"] += 1
        if key in failed_attempts:
            entry["retried_ok"] += 1
        if display and not entry["display"]:
            entry["display"] = display

    for key, detail in failed_attempts.items():
        if key in delivered_keys:
            continue  # eventually succeeded -- already counted as retried_ok above, not a real failure
        addr, display = identity_by_key[key]
        entry = breakdown.setdefault(addr, {"display": None, "delivered": 0, "retried_ok": 0, "failed": 0})
        entry["failed"] += 1
        if display and not entry["display"]:
            entry["display"] = display
        failures.append({
            "from_address": addr,
            "recipient": detail["recipient"],
            "subject": detail["subject"],
            "category": categorize_bounce(detail["reason"], detail["bounce_type"]),
            "reason": detail["reason"],
            "bounce_type": detail["bounce_type"],
            "occurred_at": detail["occurred_at"],
        })

    return breakdown, failures, None


def _identity_breakdown_note(breakdown: dict, window_days: int, top_n: int = 5) -> str:
    """Top-N failing identities as bullet lines, appended to the
    mailgun_reputation action item -- turns a single blended bounce rate
    into "who specifically is causing it" when a Mailgun domain is shared
    across several websites or campaigns. Only identities with at least one
    failure are considered; returns "" (no section added) when there's
    nothing to show, e.g. no matching event-log data for the window."""
    failing = {addr: c for addr, c in breakdown.items() if c["failed"] > 0}
    if not failing:
        return ""
    ranked = sorted(failing.items(), key=lambda kv: kv[1]["failed"], reverse=True)[:top_n]
    bullets = "\n".join(
        f"• {(c['display'] + ' <' + addr + '>') if c['display'] else addr}: "
        f"{c['failed']} of {c['delivered'] + c['failed']} failed "
        f"({c['failed'] / (c['delivered'] + c['failed']):.0%})"
        for addr, c in ranked
    )
    return f"\n\nBy sender identity (last {window_days}d):\n{bullets}"


def _stale(conn, mailgun_domain, recheck_hours):
    row = conn.execute(
        "SELECT MAX(checked_at) as last_checked FROM mailgun_stats WHERE mailgun_domain=?", (mailgun_domain,)
    ).fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def _fetch_all(mailgun_domain, api_key, window_days, events_window_days):
    """All network I/O for one domain -- no DB access, safe to run concurrently."""
    stats, daily, stats_err = fetch_stats(mailgun_domain, api_key, window_days)
    suppressions = {}
    for endpoint, kind in SUPPRESSION_KINDS.items():
        items, err = fetch_suppressions(mailgun_domain, endpoint, api_key)
        suppressions[kind] = (items, err)
    identity, identity_failures, identity_err = fetch_identity_breakdown(mailgun_domain, api_key, events_window_days)
    return {"stats": (stats, stats_err), "daily": daily, "suppressions": suppressions,
            "identity": (identity, identity_failures, identity_err)}


def run_mailgun_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    api_key = get_secret("MAILGUN_API_KEY")
    if not api_key:
        if verbose:
            print("[mailgun] no MAILGUN_API_KEY in secrets.env -- skipping")
        return

    recheck_hours = int(settings["mailgun_recheck_hours"])
    window_days = int(settings["mailgun_stats_window_days"])
    events_window_days = int(settings["mailgun_events_window_days"])
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
                to_check, ex.map(lambda mg: _fetch_all(mg, api_key, window_days, events_window_days), to_check)
            ))

    for mailgun_domain in to_check:
        domain_id, domain_name = matches[mailgun_domain]

        identity_breakdown, identity_failures, identity_err = fetched[mailgun_domain]["identity"]
        if identity_err:
            if verbose:
                print(f"[mailgun] {mailgun_domain}: identity breakdown fetch failed -- {identity_err}")
            identity_breakdown = {}
        else:
            conn.execute("DELETE FROM mailgun_identity_stats WHERE mailgun_domain=?", (mailgun_domain,))
            for addr, c in identity_breakdown.items():
                conn.execute(
                    """INSERT INTO mailgun_identity_stats
                       (domain_id, mailgun_domain, from_address, from_display, window_days, delivered, retried_ok, failed)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (domain_id, mailgun_domain, addr, c["display"], events_window_days,
                     c["delivered"], c["retried_ok"], c["failed"]),
                )
            conn.execute("DELETE FROM mailgun_identity_failures WHERE mailgun_domain=?", (mailgun_domain,))
            for f in identity_failures:
                conn.execute(
                    """INSERT INTO mailgun_identity_failures
                       (domain_id, mailgun_domain, from_address, recipient, subject, category, reason, bounce_type, occurred_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (domain_id, mailgun_domain, f["from_address"], f["recipient"], f["subject"], f["category"],
                     f["reason"], f["bounce_type"], f["occurred_at"]),
                )

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

            for day_str, day_totals in fetched[mailgun_domain]["daily"] or []:
                conn.execute(
                    """INSERT INTO mailgun_daily_stats
                       (domain_id, mailgun_domain, day, accepted, delivered, failed_perm, failed_temp,
                        complained, unsubscribed)
                       VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(mailgun_domain, day) DO UPDATE SET
                         accepted=excluded.accepted, delivered=excluded.delivered,
                         failed_perm=excluded.failed_perm, failed_temp=excluded.failed_temp,
                         complained=excluded.complained, unsubscribed=excluded.unsubscribed""",
                    (domain_id, mailgun_domain, day_str, day_totals["accepted"], day_totals["delivered"],
                     day_totals["failed_perm"], day_totals["failed_temp"], day_totals["complained"],
                     day_totals["unsubscribed"]),
                )

            if accepted and (bounce_rate >= bounce_warn or complaint_rate >= complaint_warn):
                upsert_system_action(
                    conn, domain_id, "mailgun_reputation", mailgun_domain,
                    f"{domain_name}: Mailgun bounce/complaint rate is elevated ({mailgun_domain})",
                    f"{bounce_rate:.2%} bounced, {complaint_rate:.2%} complained over the last {window_days}d "
                    f"({accepted} accepted). Check list quality before sending more."
                    + _identity_breakdown_note(identity_breakdown, events_window_days),
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
