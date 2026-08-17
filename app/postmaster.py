"""
Google Postmaster Tools v2 integration.

Pulls, for whichever domains DMARCTool already tracks and that are VERIFIED in
your Postmaster Tools account (matched dynamically each run, same pattern as
mailgun.py -- no hardcoded domain list):

  - USER_REPORTED_SPAM_RATE (the SPAM_RATE metric) -- Gmail's own real
    spam-complaint rate. This is the number Gmail's sender guidelines say must
    stay under 0.30%, ideally under 0.10%. Nothing else in this tool can
    produce this number; DMARC reports and Mailgun's own complaint rate are
    both just proxies for it.
  - delivery error rate/count.
  - the full compliance verdict from `complianceStatus` -- Google's own
    COMPLIANT / NEEDS_WORK judgment on SPF+DKIM, DMARC alignment/policy, TLS
    encryption, DNS records (PTR), one-click/honor-unsubscribe, and an overall
    deliverability verdict.

Uses stdlib `urllib` only. Needs GOOGLE_POSTMASTER_CLIENT_ID/SECRET and a
GOOGLE_POSTMASTER_REFRESH_TOKEN in secrets.env (see app/postmaster_auth.py for
the one-time browser authorization that produces the refresh token). If any
of those are missing, this is a no-op.
"""

import argparse
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app.analysis import ensure_default_settings, likely_causal_senders, upsert_system_action
from app.config import get_secret
from app.db import get_connection, init_db

API_BASE = "https://gmailpostmastertools.googleapis.com/v2"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_WORKERS = 6

# Metrics confirmed to work with no `filter` needed against the live API.
# AUTH_SUCCESS_RATE / TLS_ENCRYPTION_RATE / FEEDBACK_LOOP_SPAM_RATE all require
# a `filter` string whose exact syntax isn't documented and wasn't worth
# guessing further -- complianceStatus already covers auth alignment and TLS
# with a plain COMPLIANT/NEEDS_WORK verdict, which is the more actionable form
# of that data anyway.
STATS_METRICS = {"spam_rate": "SPAM_RATE", "delivery_error_rate": "DELIVERY_ERROR_RATE",
                  "delivery_error_count": "DELIVERY_ERROR_COUNT"}


def _refresh_access_token():
    client_id = get_secret("GOOGLE_POSTMASTER_CLIENT_ID")
    client_secret = get_secret("GOOGLE_POSTMASTER_CLIENT_SECRET")
    refresh_token = get_secret("GOOGLE_POSTMASTER_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return None, "missing GOOGLE_POSTMASTER_CLIENT_ID/SECRET/REFRESH_TOKEN in secrets.env"
    data = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())["access_token"], None
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        return None, f"token refresh failed: {e}"


def _get(url, access_token, timeout=15.0):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except (urllib.error.URLError, TimeoutError) as e:
        return None, f"network error: {e}"


def _post(url, body, access_token, timeout=15.0):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except (urllib.error.URLError, TimeoutError) as e:
        return None, f"network error: {e}"


def list_verified_domains(access_token):
    """VERIFIED domain names on the Postmaster Tools account, or (None, error)."""
    names, url = [], f"{API_BASE}/domains?pageSize=100"
    while url:
        data, err = _get(url, access_token)
        if err:
            return None, err
        for d in data.get("domains", []):
            if d.get("verificationState") == "VERIFIED":
                names.append(d["name"].replace("domains/", "", 1))
        tok = data.get("nextPageToken")
        url = f"{API_BASE}/domains?pageSize=100&pageToken={tok}" if tok else None
    return names, None


def match_tracked_domains(conn, pm_domains):
    """Same dynamic root-or-subdomain matching used for Mailgun."""
    tracked = conn.execute("SELECT id, name FROM domains").fetchall()
    matches = {}
    for name in pm_domains:
        for row in tracked:
            if name == row["name"] or name.endswith("." + row["name"]):
                matches[name] = (row["id"], row["name"])
                break
    return matches


def fetch_stats(domain, access_token, window_days):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=window_days)
    date_range = {
        "start": {"year": start.year, "month": start.month, "day": start.day},
        "end": {"year": today.year, "month": today.month, "day": today.day},
    }
    body = {
        "metricDefinitions": [
            {"name": key, "baseMetric": {"standardMetric": metric}}
            for key, metric in STATS_METRICS.items()
        ],
        "timeQuery": {"dateRanges": {"dateRanges": [date_range]}},
        "aggregationGranularity": "OVERALL",
    }
    data, err = _post(f"{API_BASE}/domains/{domain}/domainStats:query", body, access_token)
    if err:
        return None, err
    values = {}
    for stat in data.get("domainStats", []):
        value = stat.get("value", {})
        values[stat["metric"]] = value.get("floatValue", value.get("intValue"))
    return values, None


def fetch_daily_spam_rate(domain, access_token, window_days):
    """Day-by-day SPAM_RATE (not the OVERALL aggregate from fetch_stats) so we
    can build our own trend history -- Postmaster Tools has no separate
    "history" endpoint, just this same query with DAILY granularity.

    Note: Google's daily breakdown lags behind the OVERALL rolling number by a
    day or two (and skips days without enough volume to report), so the most
    recent 1-3 days here may look better than the current headline spam rate
    actually is -- the headline (fetch_stats) number stays authoritative.
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=window_days)
    date_range = {
        "start": {"year": start.year, "month": start.month, "day": start.day},
        "end": {"year": today.year, "month": today.month, "day": today.day},
    }
    days, page_token = [], None
    while True:
        body = {
            "metricDefinitions": [{"name": "spam_rate", "baseMetric": {"standardMetric": "SPAM_RATE"}}],
            "timeQuery": {"dateRanges": {"dateRanges": [date_range]}},
            "aggregationGranularity": "DAILY",
            "pageSize": 200,
        }
        if page_token:
            body["pageToken"] = page_token
        data, err = _post(f"{API_BASE}/domains/{domain}/domainStats:query", body, access_token)
        if err:
            return None, err
        for stat in data.get("domainStats", []):
            date = stat.get("date")
            if date is None:
                continue
            value = stat.get("value", {})
            day_str = f"{date['year']:04d}-{date['month']:02d}-{date['day']:02d}"
            days.append((day_str, value.get("floatValue")))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return days, None


def fetch_compliance(domain, access_token):
    data, err = _get(f"{API_BASE}/domains/{domain}/complianceStatus", access_token)
    if err:
        return None, err
    compliance = data.get("complianceData", {})
    rows = [(row["requirement"], row["status"]["status"], None) for row in compliance.get("rowData", [])]
    for key, field in (
        ("ONE_CLICK_UNSUBSCRIBE", "oneClickUnsubscribeVerdict"),
        ("HONOR_UNSUBSCRIBE", "honorUnsubscribeVerdict"),
    ):
        verdict = compliance.get(field, {}).get("status", {}).get("status")
        if verdict:
            rows.append((key, verdict, None))
    deliverability = compliance.get("deliverabilityStatusVerdict", {})
    d_status = deliverability.get("state", {}).get("status")
    if d_status:
        rows.append(("DELIVERABILITY", d_status, deliverability.get("reason")))
    return rows, None


def _stale(conn, postmaster_domain, recheck_hours):
    row = conn.execute(
        "SELECT MAX(checked_at) as last_checked FROM postmaster_stats WHERE postmaster_domain=?",
        (postmaster_domain,),
    ).fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def _fetch_all(domain, access_token, window_days):
    stats, stats_err = fetch_stats(domain, access_token, window_days)
    compliance, compliance_err = fetch_compliance(domain, access_token)
    daily, daily_err = fetch_daily_spam_rate(domain, access_token, window_days)
    return {"stats": (stats, stats_err), "compliance": (compliance, compliance_err), "daily": (daily, daily_err)}


def run_postmaster_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    access_token, err = _refresh_access_token()
    if err:
        if verbose:
            print(f"[postmaster] {err} -- skipping")
        return

    recheck_hours = int(settings["postmaster_recheck_hours"])
    window_days = int(settings["postmaster_stats_window_days"])

    pm_domains, err = list_verified_domains(access_token)
    if err:
        if verbose:
            print(f"[postmaster] could not list domains: {err}")
        return

    matches = match_tracked_domains(conn, pm_domains)
    if verbose:
        print(f"[postmaster] {len(matches)} verified domain(s) match a tracked domain "
              f"(of {len(pm_domains)} verified on the account)")

    to_check = [d for d in matches if _stale(conn, d, recheck_hours)]
    if verbose:
        skipped = len(matches) - len(to_check)
        if skipped:
            print(f"[postmaster] {skipped} domain(s) checked recently, skipping")

    fetched = {}
    if to_check:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_check))) as ex:
            fetched = dict(zip(
                to_check, ex.map(lambda d: _fetch_all(d, access_token, window_days), to_check)
            ))

    for postmaster_domain in to_check:
        domain_id, domain_name = matches[postmaster_domain]

        stats, stats_err = fetched[postmaster_domain]["stats"]
        if stats_err:
            if verbose:
                print(f"[postmaster] {postmaster_domain}: stats fetch failed -- {stats_err}")
        else:
            spam_rate = stats.get("spam_rate")
            conn.execute(
                """INSERT INTO postmaster_stats
                   (domain_id, postmaster_domain, window_days, spam_rate, delivery_error_rate, delivery_error_count)
                   VALUES (?,?,?,?,?,?)""",
                (domain_id, postmaster_domain, window_days, spam_rate,
                 stats.get("delivery_error_rate"), stats.get("delivery_error_count")),
            )
            if verbose:
                pct = f"{spam_rate:.3%}" if spam_rate is not None else "?"
                print(f"[postmaster] {postmaster_domain} ({domain_name}): spam rate {pct} (last {window_days}d)")

        daily, daily_err = fetched[postmaster_domain]["daily"]
        if daily_err:
            if verbose:
                print(f"[postmaster] {postmaster_domain}: daily history fetch failed -- {daily_err}")
        else:
            for day_str, day_rate in daily:
                conn.execute(
                    """INSERT INTO postmaster_daily_stats (domain_id, postmaster_domain, day, spam_rate)
                       VALUES (?,?,?,?)
                       ON CONFLICT(postmaster_domain, day) DO UPDATE SET spam_rate=excluded.spam_rate""",
                    (domain_id, postmaster_domain, day_str, day_rate),
                )

        compliance, comp_err = fetched[postmaster_domain]["compliance"]
        if comp_err:
            if verbose:
                print(f"[postmaster] {postmaster_domain}: compliance fetch failed -- {comp_err}")
            continue

        for requirement, status, reason in compliance:
            conn.execute(
                """INSERT INTO postmaster_compliance (domain_id, postmaster_domain, requirement, status, reason)
                   VALUES (?,?,?,?,?)""",
                (domain_id, postmaster_domain, requirement, status, reason),
            )
            if verbose:
                print(f"[postmaster]   {requirement}: {status}" + (f" ({reason})" if reason else ""))

            ref_key = f"{postmaster_domain}:{requirement}"
            if status == "NEEDS_WORK":
                spam_note = ""
                if requirement == "USER_REPORTED_SPAM_RATE" and stats and stats.get("spam_rate") is not None:
                    spam_note = f" (measured spam rate: {stats['spam_rate']:.3%} over the last {window_days}d)"
                causal_note = ""
                # DMARC alignment/SPF+DKIM/overall-deliverability flags are the ones a
                # currently-failing sender can plausibly explain -- TLS/unsubscribe
                # requirements are unrelated to sender authentication, so skip those.
                if requirement in ("DMARC_ALIGNMENT", "SPF_AND_DKIM") or (
                    requirement == "DELIVERABILITY" and reason == "SENDER_NOT_COMPLIANT"
                ):
                    causal = likely_causal_senders(conn, domain_id, settings)
                    if causal:
                        parts = "; ".join(f"{c['source_ip']} ({c['pass_rate']:.0%} pass, {c['total']} msgs)" for c in causal)
                        causal_note = (f" Possible cause: currently-failing sending source(s) -- {parts} -- "
                                       f"see the matching 'Investigate failing sender' item and Known senders "
                                       f"in the Senders tab for what they actually authenticate as.")
                upsert_system_action(
                    conn, domain_id, "postmaster_compliance", ref_key,
                    f"{domain_name}: Gmail flags {requirement.replace('_', ' ').lower()} as needing work",
                    f"Google's Postmaster Tools reports this as NEEDS_WORK for {postmaster_domain}."
                    f"{spam_note}" + (f" Reason: {reason}." if reason else "") + causal_note,
                )
            else:
                conn.execute(
                    """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                       WHERE category='postmaster_compliance' AND ref_key=? AND status='open'""",
                    (ref_key,),
                )

    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Postmaster Tools stats and compliance status")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_postmaster_checks(conn)


if __name__ == "__main__":
    main()
