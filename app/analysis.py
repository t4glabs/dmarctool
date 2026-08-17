"""
Analysis / recommendation engine.

Run with: python3 -m app.analysis

For each tracked domain this:
  1. Rebuilds policy_history from ingested reports (majority-vote per day, run-length
     encoded, so a single lagging reporter doesn't fake a policy change).
  2. Refreshes known_senders (per-IP totals + a best-effort classification) and flags
     genuinely new source IPs seen in the last `new_sender_window_days`.
  3. Flags known senders with high volume + high failure rate as worth investigating.
  4. Flags domains with no newly-ingested reports in a while (data staleness --
     distinct from DNS drift, which is a later module).
  5. Computes a rolling, volume-aware pass rate and produces a plain-language
     next-suggested-action + reasoning for ramping p/pct, anchored to configurable
     thresholds in the `settings` table.

All "system_suggested" findings are upserted into action_items, keyed on
(domain_id, category, ref_key) among currently-open items, so re-running this
doesn't pile up duplicates -- it just keeps the same item current.

Rolling pass-rate windows are anchored to the latest *ingested* report per domain,
not wall-clock time -- so recommendations reflect data quality, not how recently you
ran the ingester. Staleness (point 7: "have I even ingested recently?") is checked
separately against wall-clock time.
"""

import argparse
import datetime
import socket
import subprocess
from collections import Counter
from pathlib import Path

from app.db import get_connection, init_db
from app.labels import classification_label

DEFAULT_SETTINGS = {
    "min_pass_rate": "0.99",           # rolling pass rate required to consider ramping
    "low_pass_rate": "0.95",           # below this, recommend investigating instead of ramping
    "min_days_stable": "14",           # days at current p/pct required before ramping
    "rolling_window_days": "21",       # analysis window, capped by days at current policy
    "min_volume_for_recommendation": "50",  # total msgs in window needed to trust the rate
    "ramp_steps": "10,25,50,100",      # pct ramp ladder
    "new_sender_window_days": "14",    # a sender first seen within this window is "new"
    "high_volume_fail_threshold": "20",  # msgs in window to flag a failing sender
    "high_fail_rate_threshold": "0.5",   # pass rate below this + high volume => flag
    "stale_days_threshold": "3",        # days since last ingested report => flag staleness
    "blocklist_min_volume": "50",       # total msgs (all-time) needed before an IP is worth a DNSBL check
    "blocklist_recent_days": "30",      # skip IPs not seen sending in this many days
    "blocklist_recheck_hours": "4",     # don't re-query an IP against DNSBL zones more often than this
    "compliance_recheck_hours": "24",   # PTR/SPF/DKIM change far less often than blocklist status
    "spf_lookup_warn_threshold": "8",   # warn before hitting SPF's hard 10-DNS-lookup limit (RFC 7208)
    "dkim_min_bits": "1024",            # Gmail's hard minimum RSA DKIM key length (2048 recommended)
    "mailgun_recheck_hours": "6",        # don't re-poll the Mailgun API more often than this
    "mailgun_stats_window_days": "30",   # lookback window for Mailgun delivered/bounced/complained stats
    "mailgun_bounce_rate_warn": "0.05",  # bounce rate (of accepted) that triggers a flag
    "mailgun_complaint_rate_warn": "0.001",  # complaint rate (of accepted) that triggers a flag
    "postmaster_recheck_hours": "24",     # Postmaster Tools data itself lags/aggregates daily
    "postmaster_stats_window_days": "30", # lookback window for the SPAM_RATE / delivery-error metrics
    "ses_stats_window_days": "30",        # lookback window for SES bounce/complaint rate (from our own accumulated counts)
    "ses_bounce_rate_watch": "0.02",       # bounce rate (of delivered) that triggers an early "watch" flag
    "ses_bounce_rate_warn": "0.05",       # bounce rate (of delivered) that triggers a flag
    "ses_complaint_rate_watch": "0.0008", # complaint rate (of delivered) that triggers an early "watch" flag
    "ses_complaint_rate_warn": "0.001",   # complaint rate (of delivered) that triggers a flag
    "ses_max_messages_per_run": "3000",   # cap SQS messages drained per check so a big backlog can't block a request; the rest drain on the next run
    "ses_account_recheck_hours": "24",    # don't re-poll SES account health/identity verification more often than this
    "newsletter_inactive_campaigns": "9",  # campaigns received with zero opens across all of them => flagged inactive
    "volume_spike_recent_days": "3",       # "recent" window averaged for the spike comparison
    "volume_spike_baseline_days": "7",     # "before" window averaged as the baseline
    "volume_spike_min_baseline_avg": "10", # baseline must average at least this many msgs/day to count
    "volume_spike_multiplier": "2.0",      # recent avg must be at least this many times the baseline to flag
    "safe_browsing_recheck_hours": "24",   # Safe Browsing status doesn't change fast; daily is plenty
}


def ensure_default_settings(conn) -> dict:
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    conn.commit()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def epoch_day(ts: int) -> int:
    return ts // 86400


def day_to_date(day: int) -> datetime.date:
    return datetime.date(1970, 1, 1) + datetime.timedelta(days=day)


def all_domains(conn):
    return conn.execute("SELECT id, name FROM domains ORDER BY name").fetchall()


# ---------------------------------------------------------------------------
# 1. Policy history
# ---------------------------------------------------------------------------

def derive_policy_history(conn, domain_id: int) -> None:
    rows = conn.execute(
        """SELECT date_begin, date_end, policy_p, policy_sp, policy_pct, policy_adkim, policy_aspf
           FROM reports WHERE domain_id = ? ORDER BY date_begin""",
        (domain_id,),
    ).fetchall()
    if not rows:
        return

    by_day = {}
    for r in rows:
        day = epoch_day(r["date_begin"])
        by_day.setdefault(day, []).append(r)

    daily_key = {}
    daily_row = {}
    for day, day_rows in by_day.items():
        counts = Counter((r["policy_p"], r["policy_pct"]) for r in day_rows)
        winning_key = counts.most_common(1)[0][0]
        daily_key[day] = winning_key
        # keep a representative full row (sp/adkim/aspf) matching the winning key
        daily_row[day] = next(r for r in day_rows if (r["policy_p"], r["policy_pct"]) == winning_key)

    days_sorted = sorted(daily_key)
    runs = []
    for day in days_sorted:
        key = daily_key[day]
        row = daily_row[day]
        if runs and runs[-1]["key"] == key:
            runs[-1]["last_day"] = day
            runs[-1]["observed_to"] = max(runs[-1]["observed_to"], row["date_end"])
        else:
            runs.append({
                "key": key, "first_day": day, "last_day": day,
                "p": row["policy_p"], "pct": row["policy_pct"],
                "sp": row["policy_sp"], "adkim": row["policy_adkim"], "aspf": row["policy_aspf"],
                "observed_from": row["date_begin"], "observed_to": row["date_end"],
            })

    conn.execute("DELETE FROM policy_history WHERE domain_id = ? AND source = 'report'", (domain_id,))
    for run in runs:
        conn.execute(
            """INSERT INTO policy_history (domain_id, p, sp, pct, adkim, aspf, observed_from, observed_to, source)
               VALUES (?,?,?,?,?,?,?,?, 'report')""",
            (domain_id, run["p"], run["sp"], run["pct"], run["adkim"], run["aspf"],
             run["observed_from"], run["observed_to"]),
        )
    conn.commit()


def current_policy_run(conn, domain_id: int):
    """Latest policy_history run for this domain (source='report'), or None."""
    return conn.execute(
        """SELECT * FROM policy_history WHERE domain_id = ? AND source = 'report'
           ORDER BY observed_to DESC LIMIT 1""",
        (domain_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# 2/3. Known senders + new/failing sender flags
# ---------------------------------------------------------------------------

def _reverse_dns(ip: str, timeout: float = 1.5):
    try:
        socket.setdefaulttimeout(timeout)
        host, _, _ = socket.gethostbyaddr(ip)
        return host
    except Exception:
        return None


def _suggest_classification(conn, domain_id: int, domain_name: str, source_ip: str) -> str:
    row = conn.execute(
        """SELECT DISTINCT ar.domain FROM record_auth_results ar
           JOIN report_records rr ON rr.id = ar.record_id
           JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND rr.source_ip = ? AND ar.domain IS NOT NULL""",
        (domain_id, source_ip),
    ).fetchall()
    auth_domains = {r["domain"] for r in row}
    if f"mails.{domain_name}" in auth_domains:
        return "ses_newsletter"
    if domain_name in auth_domains:
        return "primary_domain"
    return "unclassified"


def _passing_auth_domains(conn, domain_id: int, source_ip: str) -> dict:
    """{domain: {mechanisms}} for every SPF/DKIM domain that actually PASSED
    for this sender's messages, per the DMARC report's own recorded auth
    results -- the shared core behind both _evaluated_auth_domains (per-sender
    detail text) and detect_borrowed_sending_identity (cross-sender pattern)."""
    rows = conn.execute(
        """SELECT rar.mechanism, rar.domain, COUNT(*) as n
           FROM record_auth_results rar
           JOIN report_records rr ON rr.id = rar.record_id
           JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND rr.source_ip = ? AND rar.domain IS NOT NULL AND rar.result = 'pass'
           GROUP BY rar.mechanism, rar.domain
           ORDER BY n DESC""",
        (domain_id, source_ip),
    ).fetchall()
    by_domain = {}
    for r in rows:
        by_domain.setdefault(r["domain"], set()).add(r["mechanism"])
    return by_domain


def _cross_domain_labels(conn, source_ip: str, exclude_domain_id: int):
    """Other tracked domains where this exact IP is already manually
    classified -- the strongest possible identification signal (you already
    told DMARCTool what this is, just for a different domain), and pure SQL
    with no network calls, so it's safe to run on every page load, not just
    in the background analysis job."""
    return conn.execute(
        """SELECT DISTINCT d.name, ks.classification FROM known_senders ks
           JOIN domains d ON d.id = ks.domain_id
           WHERE ks.source_ip = ? AND ks.domain_id != ? AND ks.classification != 'unclassified'""",
        (source_ip, exclude_domain_id),
    ).fetchall()


def _evaluated_auth_domains(conn, domain_id: int, source_ip: str) -> str:
    """What a failing sender's messages actually authenticate as, per the DMARC
    report's own recorded SPF/DKIM results -- the single most useful fact when
    triaging a consistently-failing sender. A sender that fails DMARC for the
    tracked domain but *passes* SPF/DKIM as some other domain almost always
    means a misconfigured shared ESP/mailing setup pointed at the wrong From
    address (e.g. another one of your own domains' sending config), not
    spoofing -- worth saying explicitly rather than leaving it to guesswork."""
    by_domain = _passing_auth_domains(conn, domain_id, source_ip)
    cross_domain = _cross_domain_labels(conn, source_ip, domain_id)
    cross_note = ""
    if cross_domain:
        labels = "; ".join(f"{row['name']}: {classification_label(row['classification'])}" for row in cross_domain)
        cross_note = f" This exact IP is already labeled elsewhere in your portfolio -- {labels}."
    if not by_domain:
        return ("no SPF/DKIM authentication passed for this sender at all -- could be spoofed mail, "
                "not a misconfigured integration." + cross_note)
    parts = [f"{d} ({'/'.join(sorted(m))} pass)" for d, m in by_domain.items()]
    return (f"actually authenticates as: {', '.join(parts)} -- likely a misconfigured sender/From-address "
            f"on that domain's setup, not spoofing.{cross_note}")


# Recognizable reverse-DNS patterns for common sending providers -- short and
# curated (same rationale as content_scoring.py's phrase list) rather than an
# exhaustive registry, just enough to turn a cryptic PTR hostname like
# "v512.v5f06b487.use4.send.mailgun.net" into a plain "Mailgun".
_ESP_PTR_PATTERNS = (
    ("mailgun.", "Mailgun"),
    ("sendgrid.", "SendGrid"),
    ("amazonses.com", "Amazon SES"),
    ("google.com", "Google Workspace / Gmail"),
    ("googlemail.com", "Google Workspace / Gmail"),
    ("outlook.com", "Microsoft 365 / Outlook"),
    ("zoho.", "Zoho Mail"),
    ("mandrillapp.com", "Mandrill (Mailchimp Transactional)"),
    ("mailchimp.com", "Mailchimp"),
    ("sparkpostmail.com", "SparkPost"),
    ("mtasv.net", "SparkPost"),
    ("postmarkapp.com", "Postmark"),
    ("sendinblue.com", "Brevo (Sendinblue)"),
)


def _guess_provider(ptr: str):
    if not ptr:
        return None
    lowered = ptr.lower()
    for pattern, name in _ESP_PTR_PATTERNS:
        if pattern in lowered:
            return name
    return None


_WHOIS_ORG_FIELDS = ("orgname:", "organization:", "org-name:", "descr:", "netname:")


def _whois_org(ip: str, timeout: float = 4.0):
    """Best-effort network/organization owner for an IP via the `whois` CLI
    (already present on macOS, same "shell out to a standard tool" pattern as
    dig elsewhere in this codebase) -- a fallback identification signal for
    when reverse DNS doesn't match a known ESP pattern at all, e.g. a generic
    cloud-VM hostname like "bc.googleusercontent.com" that doesn't say
    anything about which specific app/service is actually running there.
    WHOIS output format varies a lot by registry (ARIN/RIPE/APNIC/etc all use
    different field names), so this only tries a handful of common ones
    rather than fully parsing it -- same "good enough, not exhaustive"
    tradeoff as the PTR pattern list above. Slow and sometimes rate-limited
    by upstream registries -- background-job use only, never call this from
    a live page render (see guess_sender_identity's skip_lookup)."""
    try:
        out = subprocess.run(["whois", ip], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    lines = [line.strip() for line in out.stdout.splitlines()]
    # Check by field *priority*, not document order -- a field like NetName
    # (a short internal registry code, e.g. "GOOGL-46") often appears earlier
    # in the raw text than the more human-readable OrgName/Organization, so
    # scanning line-by-line and stopping at the first match of any field
    # would pick the less useful one.
    for field in _WHOIS_ORG_FIELDS:
        for line in lines:
            if line.lower().startswith(field):
                value = line.split(":", 1)[1].strip()
                if value and value.upper() not in ("NA", "N/A", ""):
                    return value
    return None


def guess_sender_identity(conn, domain_id: int, domain_name: str, source_ip: str,
                           ptr: str = None, skip_lookup: bool = False) -> str:
    """Best-effort, plain-language guess at what an unrecognized sending IP
    actually is, combining every signal DMARCTool already has -- ranked by
    how trustworthy each one actually is, not just what's available:
      1. Which domain(s) it actually authenticates as, from the DMARC
         report's own SPF/DKIM results -- cryptographic proof for this exact
         message, the most trustworthy signal available.
      2. Whether this exact IP is already labeled for a different domain in
         your own portfolio. Weaker than it sounds: ESPs like Mailgun/
         SendGrid commonly run *shared* sending IP pools reused across many
         unrelated customer accounts (no dedicated IP purchased), so the same
         physical IP can legitimately authenticate as completely different,
         unrelated domains for different customers at different times. If
         this disagrees with #1, that disagreement itself is the useful
         finding -- it means "shared pool", not "these domains are related".
      3. The sending provider name, parsed from reverse DNS (a specific,
         named ESP -- Mailgun, SES, etc.) -- or failing that, the network's
         registered owner via WHOIS (a much vaguer signal: "Google LLC" or
         "Amazon Technologies Inc." just means *some* customer's app is
         running on that cloud, not which one -- worded accordingly below).
    When nothing resolves, returns concrete manual next steps instead of just
    "go figure it out" -- the process to follow, not just a shrug. Not a
    certainty -- a starting point for the "What is this?" dropdown, same
    spirit as content_scoring.py's heuristics elsewhere in this tool. Pass
    `ptr` if it's already been looked up (e.g. cached in ptr_checks, or from a
    _reverse_dns() call the caller already made) to avoid a redundant lookup.
    If `ptr` is None and this is being called from a live web request (not
    the background analysis job), pass skip_lookup=True -- _reverse_dns() and
    _whois_org() are both blocking network calls (the latter can take several
    seconds and is sometimes rate-limited), which have no business running
    synchronously inside a page render."""
    if ptr is None and not skip_lookup:
        ptr = _reverse_dns(source_ip)
    provider = _guess_provider(ptr)
    provider_note = f" ({provider})" if provider else ""
    auth_domains = {
        d for d in _passing_auth_domains(conn, domain_id, source_ip)
        if d != domain_name and d not in _ESP_DEFAULT_AUTH_DOMAINS
    }
    cross_domain = _cross_domain_labels(conn, source_ip, domain_id)
    cross_domain_names = {row["name"] for row in cross_domain}

    if auth_domains and cross_domain_names and not (auth_domains & cross_domain_names):
        cross_labels = "; ".join(f"{row['name']}: {classification_label(row['classification'])}" for row in cross_domain)
        return (f"likely a SHARED sending IP{provider_note}, not dedicated infrastructure you'd recognize -- "
                f"for this domain's own mail it actually authenticates as {', '.join(sorted(auth_domains))}, but "
                f"this exact IP has separately been labeled elsewhere in your portfolio ({cross_labels}). That "
                f"mismatch usually means the provider recycles this IP across many unrelated customers, not "
                f"that the two domains are actually related.")

    if auth_domains:
        return (f"authenticates as {', '.join(sorted(auth_domains))}{provider_note} -- if that's "
                f"a domain/service you use, it's very likely legitimate, just sending under a different identity.")

    if cross_domain_names:
        labels = "; ".join(f"{row['name']}: {classification_label(row['classification'])}" for row in cross_domain)
        return (f"this exact IP is already labeled for another domain you track -- {labels}. Worth a caveat: if "
                f"this is a shared ESP IP pool (common with Mailgun/SendGrid), that alone doesn't guarantee a real "
                f"relationship -- it's a weaker signal than an actual authenticating domain, which wasn't found here.")

    if provider:
        return (f"sent through {provider} (from reverse DNS), but doesn't authenticate as any domain you track -- "
                f"legitimate if you use {provider} for something, otherwise check {provider}'s own sending/activity "
                f"logs for a message matching this IP's volume and date range to find out which account sent it.")

    whois_org = None if skip_lookup else _whois_org(source_ip)
    if whois_org:
        return (f"reverse DNS didn't match a known email provider, but this IP's network is registered to "
                f"\"{whois_org}\" (via WHOIS). That's the hosting/cloud company, not necessarily the sender -- "
                f"if it's a major cloud provider (Google, AWS, Azure, DigitalOcean, Hetzner, OVH, etc.), it likely "
                f"means one specific app or account on that cloud is sending this, not the provider itself. To "
                f"narrow it down: check whether you (or a service you use) run anything on {whois_org}'s "
                f"infrastructure that could plausibly send mail as this domain.")

    return ("genuinely unfamiliar -- no provider (from reverse DNS or WHOIS), authenticating domain, or prior "
            "labeling found for this IP. To investigate by hand: (1) note the PTR/rDNS shown in the Known Senders "
            "table, if any; (2) check whether the volume here is a one-off blip or growing over time -- a single "
            "small burst that never recurs is usually low-risk noise, while steady/growing volume deserves more "
            "attention; (3) if this same IP starts showing up across multiple of your tracked domains without ever "
            "authenticating as any of them, that's a stronger sign of spoofing than a misconfigured integration -- "
            "consider tightening this domain's DMARC policy (raise pct, or move toward p=reject) rather than "
            "trying to identify the sender.")


# Minimum span (first_seen to last_seen) a failing sender needs before its
# borrowed-identity pattern counts as *structural* rather than a one-off
# blip/test -- deliberately short (this is a heuristic tuning constant, same
# spirit as click_quality.py's thresholds, not a per-user policy choice worth
# a Settings entry).
MIN_BORROWED_IDENTITY_DAYS = 5

# ESPs commonly dual-sign with their own generic default domain *in addition*
# to a customer's verified domain (e.g. every Mailgun message also carries a
# d=mailgun.org signature alongside the customer's own). That's not a
# meaningful "borrowed identity" to flag on its own -- it's just how the ESP
# always signs -- so it's excluded here to avoid reporting the same root
# cause as two separate findings (the real culprit domain still gets one).
_ESP_DEFAULT_AUTH_DOMAINS = {"mailgun.org", "amazonses.com", "sendgrid.net"}


def detect_borrowed_sending_identity(conn, domain_id: int, domain_name: str, settings: dict) -> list:
    """Flags when this domain's failing mail *persistently* authenticates as
    a different domain -- e.g. several sites/newsletters sharing one ESP's
    verified sending domain while using a different display "From" address
    per site (a deliberate, common cost-saving setup -- not a mistake). This
    is a materially different situation from a one-off misconfigured sender
    (failure_investigation's job): DMARC alignment can never pass for this
    traffic no matter what, so it needs its own explicit warning, especially
    since it also means enforcement (ramping pct up) will quarantine/reject a
    growing share of this mail over time -- exactly the kind of thing that
    should give a ramp recommendation pause."""
    high_vol = int(settings["high_volume_fail_threshold"])
    high_fail_rate = float(settings["high_fail_rate_threshold"])
    tracked = {row["name"] for row in conn.execute("SELECT name FROM domains")}

    senders = conn.execute("SELECT * FROM known_senders WHERE domain_id = ?", (domain_id,)).fetchall()
    by_auth_domain = {}
    for s in senders:
        total = s["total_msgs"]
        pass_rate = s["pass_msgs"] / total if total else 0
        if total < high_vol or pass_rate >= high_fail_rate:
            continue
        span_days = epoch_day(s["last_seen"]) - epoch_day(s["first_seen"])
        if span_days < MIN_BORROWED_IDENTITY_DAYS:
            continue
        for auth_domain in _passing_auth_domains(conn, domain_id, s["source_ip"]):
            if auth_domain == domain_name or auth_domain in _ESP_DEFAULT_AUTH_DOMAINS:
                continue
            bucket = by_auth_domain.setdefault(auth_domain, {"total": 0, "ips": [], "span_days": 0})
            bucket["total"] += total
            bucket["ips"].append(s["source_ip"])
            bucket["span_days"] = max(bucket["span_days"], span_days)

    findings = []
    for auth_domain, info in by_auth_domain.items():
        whose = "your own domain " if auth_domain in tracked else ""
        findings.append({
            "category": "borrowed_sending_identity", "ref_key": auth_domain,
            "title": f"{domain_name}: mail persistently authenticates as {auth_domain}, not itself",
            "detail": (f"{info['total']} msgs across {len(info['ips'])} sending IP(s) "
                       f"({', '.join(info['ips'])}) over at least {info['span_days']} days consistently pass "
                       f"SPF/DKIM as {whose}{auth_domain} instead of {domain_name}. This isn't a one-off "
                       f"misconfiguration -- it looks like {domain_name} sends through {auth_domain}'s verified "
                       f"ESP identity with a different display From address (a common shared-ESP-account setup). "
                       f"DMARC alignment can never pass for this traffic unless {domain_name} is verified as its "
                       f"own sending domain with that provider. As long as this continues, raising {domain_name}'s "
                       f"DMARC enforcement (pct) will quarantine/reject a growing share of this mail -- if you "
                       f"don't plan to verify {domain_name} separately, keep its policy at p=none (or a low pct) "
                       f"rather than following a ramp-up recommendation."),
        })
    return findings


def update_known_senders(conn, domain_id: int, domain_name: str, settings: dict) -> None:
    rows = conn.execute(
        """SELECT rr.source_ip,
                  MIN(r.date_begin) as first_seen, MAX(r.date_end) as last_seen,
                  SUM(rr.count) as total_msgs,
                  SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) as pass_msgs
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ?
           GROUP BY rr.source_ip""",
        (domain_id,),
    ).fetchall()

    existing = {
        row["source_ip"]
        for row in conn.execute("SELECT source_ip FROM known_senders WHERE domain_id = ?", (domain_id,))
    }

    for row in rows:
        ip = row["source_ip"]
        fail_msgs = row["total_msgs"] - row["pass_msgs"]
        if ip not in existing:
            classification = _suggest_classification(conn, domain_id, domain_name, ip)
            conn.execute(
                """INSERT INTO known_senders
                   (domain_id, source_ip, first_seen, last_seen, total_msgs, pass_msgs, fail_msgs, classification)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (domain_id, ip, row["first_seen"], row["last_seen"], row["total_msgs"], row["pass_msgs"],
                 fail_msgs, classification),
            )
        else:
            conn.execute(
                """UPDATE known_senders
                   SET first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?),
                       total_msgs = ?, pass_msgs = ?, fail_msgs = ?
                   WHERE domain_id = ? AND source_ip = ?""",
                (row["first_seen"], row["last_seen"], row["total_msgs"], row["pass_msgs"], fail_msgs,
                 domain_id, ip),
            )
    conn.commit()


def likely_causal_senders(conn, domain_id: int, settings: dict) -> list:
    """Senders currently failing badly enough to trigger their own
    failure_investigation item (same thresholds), reused to connect a
    Postmaster DMARC-alignment/deliverability flag back to a concrete likely
    cause instead of leaving 'fix whichever's flagged' as the only guidance."""
    high_vol = int(settings["high_volume_fail_threshold"])
    high_fail_rate = float(settings["high_fail_rate_threshold"])
    rows = conn.execute("SELECT * FROM known_senders WHERE domain_id = ?", (domain_id,)).fetchall()
    out = []
    for s in rows:
        total = s["total_msgs"]
        pass_rate = s["pass_msgs"] / total if total else 0
        if total >= high_vol and pass_rate < high_fail_rate:
            out.append({"source_ip": s["source_ip"], "pass_rate": pass_rate, "total": total})
    return out


def flag_new_and_failing_senders(conn, domain_id: int, domain_name: str, settings: dict, now_day: int) -> list:
    new_window = int(settings["new_sender_window_days"])
    high_vol = int(settings["high_volume_fail_threshold"])
    high_fail_rate = float(settings["high_fail_rate_threshold"])

    findings = []
    senders = conn.execute("SELECT * FROM known_senders WHERE domain_id = ?", (domain_id,)).fetchall()
    for s in senders:
        total = s["total_msgs"]
        pass_rate = s["pass_msgs"] / total if total else 0
        first_seen_day = epoch_day(s["first_seen"])
        is_new = (now_day - first_seen_day) <= new_window

        if is_new and s["classification"] == "unclassified" and total >= 3:
            ptr = _reverse_dns(s["source_ip"])
            guess = guess_sender_identity(conn, domain_id, domain_name, s["source_ip"], ptr=ptr)
            detail = (f"First seen {day_to_date(first_seen_day)}, {total} msgs, "
                      f"{pass_rate:.0%} pass" + (f", PTR: {ptr}" if ptr else ", no PTR record")
                      + f" Best guess: {guess}")
            findings.append({
                "category": "new_sender", "ref_key": s["source_ip"],
                "title": f"New unrecognized sender {s['source_ip']} on this domain",
                "detail": detail,
            })

        if total >= high_vol and pass_rate < high_fail_rate:
            ptr = _reverse_dns(s["source_ip"]) if s["classification"] == "unclassified" else None
            label = classification_label(s["classification"])
            auth_note = _evaluated_auth_domains(conn, domain_id, s["source_ip"])
            detail = (f"{total} msgs, {pass_rate:.0%} pass ({s['fail_msgs']} failing), "
                      f"labeled as: {label}" + (f", PTR: {ptr}" if ptr else "")
                      + f". {auth_note[0].upper()}{auth_note[1:]}")
            findings.append({
                "category": "failure_investigation", "ref_key": s["source_ip"],
                "title": f"Investigate failing sender {s['source_ip']}",
                "detail": detail,
            })
    return findings


# ---------------------------------------------------------------------------
# 4. Staleness
# ---------------------------------------------------------------------------

def check_staleness(conn, domain_id: int, domain_name: str, settings: dict, wall_now: datetime.datetime) -> list:
    row = conn.execute(
        "SELECT MAX(date_end) as latest FROM reports WHERE domain_id = ?", (domain_id,)
    ).fetchone()
    if not row or row["latest"] is None:
        return []
    latest_dt = datetime.datetime.utcfromtimestamp(row["latest"])
    stale_days = (wall_now - latest_dt).days
    threshold = int(settings["stale_days_threshold"])
    if stale_days >= threshold:
        return [{
            "category": "data_stale", "ref_key": None,
            "title": f"No new DMARC reports ingested for {domain_name} in {stale_days} days",
            "detail": f"Latest ingested report ends {latest_dt.date()}. Re-run ingestion or check reporting inbox routing.",
        }]
    return []


# ---------------------------------------------------------------------------
# 5. Rolling stats + recommendation
# ---------------------------------------------------------------------------

def daily_pass_series(conn, domain_id: int, days: int = 60):
    """List of (date, total, passed, rate) for the last `days` days of ingested data,
    anchored to the latest ingested report for this domain (not wall-clock)."""
    latest_row = conn.execute(
        "SELECT MAX(date_end) as latest FROM reports WHERE domain_id = ?", (domain_id,)
    ).fetchone()
    if not latest_row or latest_row["latest"] is None:
        return []
    latest_day = epoch_day(latest_row["latest"])
    start_day = latest_day - days + 1

    rows = conn.execute(
        """SELECT r.date_begin, rr.count, rr.dkim_result, rr.spf_result
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ?""",
        (domain_id, start_day * 86400),
    ).fetchall()

    per_day = {d: [0, 0] for d in range(start_day, latest_day + 1)}
    for r in rows:
        day = epoch_day(r["date_begin"])
        if day not in per_day:
            continue
        per_day[day][0] += r["count"]
        if r["dkim_result"] == "pass" or r["spf_result"] == "pass":
            per_day[day][1] += r["count"]

    series = []
    for day in sorted(per_day):
        total, passed = per_day[day]
        rate = passed / total if total else None
        series.append((day_to_date(day), total, passed, rate))
    return series


def check_volume_spike(conn, domain_id: int, domain_name: str, settings: dict) -> list:
    """Gmail's guidance is explicit: increase sending volume gradually, since a
    sudden jump can trigger rate limiting or hurt reputation even if the mail
    itself is fine. This is distinct from the DMARC enforcement pct ramp (that's
    about how much of your *failing* mail gets penalized) -- this compares your
    recent total daily volume against your own trailing baseline, purely from
    report_records counts already ingested, no new data source needed.
    """
    recent_days = int(settings["volume_spike_recent_days"])
    baseline_days = int(settings["volume_spike_baseline_days"])
    min_baseline_avg = float(settings["volume_spike_min_baseline_avg"])
    multiplier = float(settings["volume_spike_multiplier"])

    series = daily_pass_series(conn, domain_id, days=recent_days + baseline_days)
    if len(series) < recent_days + baseline_days:
        return []

    baseline_window = series[:baseline_days]
    recent_window = series[baseline_days:]
    baseline_avg = sum(d[1] for d in baseline_window) / len(baseline_window)
    recent_avg = sum(d[1] for d in recent_window) / len(recent_window)

    if baseline_avg < min_baseline_avg or recent_avg < baseline_avg * multiplier:
        return []

    return [{
        "category": "volume_spike", "ref_key": None,
        "title": f"{domain_name}: sending volume jumped -- ~{recent_avg:.0f}/day recently vs ~{baseline_avg:.0f}/day before",
        "detail": (f"Average volume over the last {recent_days}d is {recent_avg / baseline_avg:.1f}x the prior "
                   f"{baseline_days}d average. Gmail's guidance: ramp sending volume up gradually rather than "
                   f"suddenly -- a sharp jump can trigger rate limiting or hurt reputation even when the mail "
                   f"itself is fine."),
    }]


def domain_window_stats(conn, domain_id: int, window_start: int, window_end: int):
    row = conn.execute(
        """SELECT SUM(rr.count) as total, SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) as passed
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ? AND r.date_end <= ?""",
        (domain_id, window_start, window_end),
    ).fetchone()
    total = row["total"] or 0
    passed = row["passed"] or 0
    rate = passed / total if total else 0.0
    return total, passed, rate


def provider_breakdown(conn, domain_id: int, window_start: int, window_end: int):
    """Per-reporting-provider (org_name) volume, pass rate, and disposition mix.

    A per-domain proxy for "how does Gmail/Yahoo/Microsoft see this mail" --
    the closest thing to per-provider reputation that's derivable from DMARC
    aggregate reports alone (no Postmaster Tools / SNDS access needed).
    """
    rows = conn.execute(
        """SELECT r.org_name as org_name,
                  SUM(rr.count) as total,
                  SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) as passed,
                  SUM(CASE WHEN rr.disposition='none' THEN rr.count ELSE 0 END) as disp_none,
                  SUM(CASE WHEN rr.disposition='quarantine' THEN rr.count ELSE 0 END) as disp_quarantine,
                  SUM(CASE WHEN rr.disposition='reject' THEN rr.count ELSE 0 END) as disp_reject
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ? AND r.date_end <= ?
           GROUP BY r.org_name
           ORDER BY total DESC""",
        (domain_id, window_start, window_end),
    ).fetchall()
    out = []
    for row in rows:
        total = row["total"] or 0
        out.append({
            "org_name": row["org_name"] or "Unknown reporter",
            "total": total,
            "rate": (row["passed"] or 0) / total if total else None,
            "disp_none": row["disp_none"] or 0,
            "disp_quarantine": row["disp_quarantine"] or 0,
            "disp_reject": row["disp_reject"] or 0,
        })
    return out


def sending_stream_breakdown(conn, domain_id: int, window_start: int, window_end: int):
    """Volume and pass rate per sending stream (the classification you set on
    known senders -- Workspace, SES bulk sender, primary domain, etc.).

    Complements provider_breakdown: that one groups by *receiving* provider,
    this one groups by *your* sending infrastructure, so a domain that mixes
    e.g. Google Workspace mail with an SES/Mailgun bulk stream can see each
    stream's health separately instead of one blended pass rate that hides
    a problem in the smaller stream.
    """
    rows = conn.execute(
        """SELECT ks.classification as classification,
                  SUM(rr.count) as total,
                  SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) as passed
           FROM report_records rr
           JOIN reports r ON r.id = rr.report_id
           JOIN known_senders ks ON ks.domain_id = r.domain_id AND ks.source_ip = rr.source_ip
           WHERE r.domain_id = ? AND r.date_begin >= ? AND r.date_end <= ?
           GROUP BY ks.classification
           ORDER BY total DESC""",
        (domain_id, window_start, window_end),
    ).fetchall()
    out = []
    for row in rows:
        total = row["total"] or 0
        out.append({
            "classification": row["classification"],
            "total": total,
            "rate": (row["passed"] or 0) / total if total else None,
        })
    return out


def ses_daily_series(conn, domain_id: int, days: int = 60):
    """List of (date_str, delivered, bounce_rate, complaint_rate) from our own
    accumulated SES event counts -- there's no external API to backfill from,
    so this only shows history from whenever the SNS/SQS pipeline went live."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, SUM(delivered) as delivered, SUM(bounced) as bounced, SUM(complained) as complained
           FROM ses_event_counts WHERE domain_id=? AND day >= ?
           GROUP BY day ORDER BY day""",
        (domain_id, since),
    ).fetchall()
    series = []
    for r in rows:
        delivered = r["delivered"] or 0
        bounce_rate = (r["bounced"] or 0) / delivered if delivered else None
        complaint_rate = (r["complained"] or 0) / delivered if delivered else None
        series.append((r["day"], delivered, bounce_rate, complaint_rate))
    return series


def _campaign_report_card(c: dict, structure: dict) -> dict:
    """Turns the various per-campaign checks already computed (subject/body
    scoring, display name, unsubscribe/header compliance, this campaign's
    own bounce/complaint rate, and now image/link structure) into a single
    "here's what's good, here's what's not, here's the score" report --
    always says *something*, even when everything is clean, rather than
    only ever speaking up about problems."""
    from app.content_scoring import score_html_structure

    good, issues = [], []

    if c["subject_score"]["score"] == 0:
        good.append(("Subject line", "No spam-trigger words or formatting issues found"))
    else:
        issues.append(("Subject line", "; ".join(c["subject_score"]["flags"]), c["subject_score"]["score"]))

    if c["body_score"] is None:
        good.append(("Newsletter content", "Not yet fetched from Listmonk -- will be scored once content sync catches up"))
    elif c["body_score"]["score"] == 0:
        good.append(("Newsletter content", "No spam-trigger words or formatting issues found in the body text"))
    else:
        issues.append(("Newsletter content", "; ".join(c["body_score"]["flags"]), c["body_score"]["score"]))

    structure_result = score_html_structure(structure["image_count"], structure["word_count"], structure["shortener_links"]) if structure else {"score": 0, "flags": []}
    if structure and structure["word_count"]:
        if structure_result["score"] == 0:
            good.append(("Images & links", f"{structure['image_count']} image(s), {structure['link_count']} link(s) -- healthy balance, no shorteners used"))
        else:
            issues.append(("Images & links", "; ".join(structure_result["flags"]), structure_result["score"]))

    if not c["display_name_issues"]:
        good.append(("Sender display name", f'"{c["from_display_name"] or "-"}" follows Gmail\'s display-name guidelines'))
    else:
        issues.append(("Sender display name", "; ".join(c["display_name_issues"]), 2 * len(c["display_name_issues"])))

    if not c["unsubscribe_issues"]:
        good.append(("Unsubscribe compliance", "One-click unsubscribe headers present and correctly formatted"))
    else:
        issues.append(("Unsubscribe compliance", "; ".join(c["unsubscribe_issues"]), 3 * len(c["unsubscribe_issues"])))

    if not c["header_issues"]:
        good.append(("Message formatting", "Message-ID present, subject isn't a misleading \"Re:\"/\"Fwd:\""))
    else:
        issues.append(("Message formatting", "; ".join(c["header_issues"]), 2 * len(c["header_issues"])))

    if c["delivered"]:
        if c["complaint_rate"] and c["complaint_rate"] >= 0.001:
            issues.append(("Spam complaints", f"{c['complaint_rate']:.2%} of recipients marked this as spam", 5))
        else:
            good.append(("Spam complaints", f"{(c['complaint_rate'] or 0):.2%} -- negligible"))
        if c["bounce_rate"] and c["bounce_rate"] >= 0.05:
            issues.append(("Bounce rate", f"{c['bounce_rate']:.2%} bounced -- check list hygiene", 3))
        else:
            good.append(("Bounce rate", f"{(c['bounce_rate'] or 0):.2%} bounced -- healthy"))

    overall_score = sum(score for _, _, score in issues)
    return {"good": good, "issues": issues, "overall_score": overall_score}


def recent_campaigns(conn, domain_id: int, limit: int = 10):
    """List of dicts, most recent newsletter first -- built entirely from SES's
    own Open/Click/Bounce/Complaint/Delivery events for messages that carry a
    Listmonk X-Listmonk-Campaign header. This is SES's own numbers, not
    Listmonk's -- the two can disagree since Listmonk tracks opens/clicks
    itself via pixel/link rewriting, while this reads what SES actually saw."""
    from app.bounce_reasons import categorize_bounce
    from app.click_quality import classify_campaign_clicks
    from app.content_scoring import score_text
    from app.display_name_checks import check_display_name
    from app.header_compliance import check_header_hygiene, check_unsubscribe_compliance
    from app.listmonk import analyze_html
    from app.open_quality import classify_campaign_opens

    rows = conn.execute(
        """SELECT * FROM ses_campaigns WHERE domain_id=?
           ORDER BY send_day DESC, updated_at DESC LIMIT ?""",
        (domain_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        delivered = r["delivered"] or 0
        bounce_breakdown = Counter()
        if r["bounced"]:
            for br in conn.execute(
                """SELECT bounce_reason FROM ses_campaign_recipients
                   WHERE configuration_set=? AND campaign_id=? AND bounced=1""",
                (r["configuration_set"], r["campaign_id"]),
            ):
                bounce_breakdown[categorize_bounce(br["bounce_reason"])] += 1
        click_quality = classify_campaign_clicks(conn, r["configuration_set"], r["campaign_id"])
        open_quality = classify_campaign_opens(conn, r["configuration_set"], r["campaign_id"])
        out.append({
            "campaign_id": r["campaign_id"],
            "subject": r["subject"] or "(no subject captured)",
            "from_display_name": r["from_display_name"],
            "send_day": r["send_day"],
            "delivered": delivered,
            "opened": r["opened"] or 0,
            "clicked": r["clicked"] or 0,
            "bounced": r["bounced"] or 0,
            "complained": r["complained"] or 0,
            "open_rate": (r["opened"] or 0) / delivered if delivered else None,
            "click_rate": (r["clicked"] or 0) / delivered if delivered else None,
            "bounce_rate": (r["bounced"] or 0) / delivered if delivered else None,
            "complaint_rate": (r["complained"] or 0) / delivered if delivered else None,
            "click_quality": click_quality,
            "genuine_click_rate": (click_quality["genuine"] / delivered) if delivered else None,
            "open_quality": open_quality,
            "genuine_open_rate": (open_quality["genuine"] / delivered) if delivered else None,
            "bounce_breakdown": bounce_breakdown.most_common(),
            "display_name_issues": check_display_name(r["from_display_name"]),
            "rejected": r["rejected"] or 0,
            "unsubscribe_issues": check_unsubscribe_compliance(r["list_unsubscribe"], r["list_unsubscribe_post"]),
            "header_issues": check_header_hygiene(r["message_id"], r["subject"]),
            "subject_score": score_text(r["subject"]),
            "body_score": score_text(r["body_text"]) if r["body_text"] else None,
        })
        structure = analyze_html(r["body_html"]) if r["body_html"] else None
        out[-1]["structure"] = structure
        out[-1]["report_card"] = _campaign_report_card(out[-1], structure)
    return out


def sending_cadence(conn, domain_id: int):
    """Gaps between consecutive newsletter send days -- Gmail's guidance is
    explicit: "send email at a consistent rate. Avoid sending email in
    bursts" and "avoid introducing sudden volume spikes if you do not have a
    history". Needs at least 3 distinct send days to say anything meaningful
    about a pattern, not just report noise from a single gap."""
    rows = conn.execute(
        "SELECT DISTINCT send_day FROM ses_campaigns WHERE domain_id=? AND send_day IS NOT NULL ORDER BY send_day",
        (domain_id,),
    ).fetchall()
    days = [datetime.datetime.strptime(r["send_day"], "%Y-%m-%d").date() for r in rows]
    if len(days) < 3:
        return {"days": [d.isoformat() for d in days], "average_gap_days": None, "latest_gap_days": None, "irregular": False}

    gaps = [(days[i] - days[i - 1]).days for i in range(1, len(days))]
    avg_gap = sum(gaps[:-1]) / len(gaps[:-1]) if len(gaps) > 1 else gaps[0]
    latest_gap = gaps[-1]
    # Flag only a clear departure from the domain's own history -- e.g. a
    # newsletter that normally goes out every ~7 days suddenly going out
    # next-day, or after a 2+ month silence.
    irregular = avg_gap > 0 and (latest_gap < avg_gap * 0.3 or latest_gap > avg_gap * 3)
    return {
        "days": [d.isoformat() for d in days],
        "average_gap_days": round(avg_gap, 1),
        "latest_gap_days": latest_gap,
        "irregular": irregular,
    }


def display_name_summary(conn, domain_id: int):
    """Distinct sender display names seen across this domain's newsletters,
    plus a consistency flag -- Gmail's guidelines call for "a consistent,
    clear, and accurate statement of the sender's identity", which is only
    checkable in aggregate across campaigns, not from a single one."""
    from app.display_name_checks import display_name_consistency

    campaigns = recent_campaigns(conn, domain_id, limit=50)
    names = display_name_consistency(campaigns)
    return {"names": names, "consistent": len(names) <= 1}


def subscriber_engagement_summary(conn, domain_id: int, threshold: int = None):
    """Replaces the manual monthly SQL query the team runs directly against
    Listmonk's DB ('received 9+ campaigns, opened zero') -- built from the
    same per-recipient SES event data as recent_campaigns, so it's the same
    trusted source, just aggregated differently."""
    if threshold is None:
        settings = ensure_default_settings(conn)
        threshold = int(settings["newsletter_inactive_campaigns"])
    per_email = conn.execute(
        """SELECT email, COUNT(DISTINCT campaign_id) as received, MAX(opened) as ever_opened,
                  MIN(first_seen_at) as first_received, MAX(last_seen_at) as last_received
           FROM ses_campaign_recipients WHERE domain_id=? AND delivered=1
           GROUP BY email""",
        (domain_id,),
    ).fetchall()
    inactive = sorted(
        (
            {
                "email": r["email"], "received": r["received"],
                "first_received": r["first_received"], "last_received": r["last_received"],
                "reason": f"{r['received']} newsletters received, 0 opened",
            }
            for r in per_email if r["received"] >= threshold and not r["ever_opened"]
        ),
        key=lambda x: -x["received"],
    )
    return {"total_subscribers": len(per_email), "inactive": inactive, "threshold": threshold}


def bounce_category_breakdown(conn, domain_id: int):
    """Bounce counts by plain-language category (see app/bounce_reasons.py),
    combined across Mailgun and SES -- turns a bare bounce count into an
    actual reason, using real diagnostic text already captured for
    suppression purposes."""
    from app.bounce_reasons import categorize_bounce

    counts = Counter()
    for r in conn.execute(
        "SELECT reason, bounce_type FROM ses_suppressions WHERE domain_id=? AND kind='bounce'",
        (domain_id,),
    ):
        counts[categorize_bounce(r["reason"], r["bounce_type"])] += 1
    for r in conn.execute(
        "SELECT reason FROM mailgun_suppressions WHERE domain_id=? AND kind='bounce'",
        (domain_id,),
    ):
        counts[categorize_bounce(r["reason"], None)] += 1
    return counts.most_common()


def mailgun_daily_series(conn, domain_id: int, days: int = 60):
    """List of (date_str, delivered, bounce_rate, complaint_rate) -- same shape
    as ses_daily_series so both can share the same chart function. Built from
    our own accumulated history; Mailgun's own API only ever returns a rolling
    window per query."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, SUM(delivered) as delivered, SUM(failed_perm) as failed_perm, SUM(complained) as complained
           FROM mailgun_daily_stats WHERE domain_id=? AND day >= ?
           GROUP BY day ORDER BY day""",
        (domain_id, since),
    ).fetchall()
    series = []
    for r in rows:
        delivered = r["delivered"] or 0
        bounce_rate = (r["failed_perm"] or 0) / delivered if delivered else None
        complaint_rate = (r["complained"] or 0) / delivered if delivered else None
        series.append((r["day"], delivered, bounce_rate, complaint_rate))
    return series


def postmaster_daily_series(conn, domain_id: int, days: int = 60):
    """List of (date_str, spam_rate) from our own accumulated daily Postmaster
    pulls -- Postmaster Tools itself only exposes a rolling window per query,
    so history here only goes back to whenever this domain was first checked."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, spam_rate FROM postmaster_daily_stats
           WHERE domain_id=? AND day >= ? ORDER BY day""",
        (domain_id, since),
    ).fetchall()
    return [(r["day"], r["spam_rate"]) for r in rows]


def recommend(conn, domain_id: int, domain_name: str, settings: dict, latest_report_end: int):
    run = current_policy_run(conn, domain_id)
    if run is None:
        return None

    p, pct = run["p"], run["pct"]
    days_stable = (epoch_day(latest_report_end) - epoch_day(run["observed_from"]))
    min_days_stable = int(settings["min_days_stable"])
    min_pass_rate = float(settings["min_pass_rate"])
    low_pass_rate = float(settings["low_pass_rate"])
    min_volume = int(settings["min_volume_for_recommendation"])
    rolling_window_days = int(settings["rolling_window_days"])
    ramp_steps = [int(x) for x in settings["ramp_steps"].split(",")]

    window_days = min(rolling_window_days, max(days_stable, 1))
    window_start = latest_report_end - window_days * 86400
    total, passed, rate = domain_window_stats(conn, domain_id, window_start, latest_report_end)

    if total < min_volume:
        return {
            "title": f"{domain_name}: insufficient recent volume for a recommendation",
            "detail": f"Only {total} msgs in the last {window_days}d at p={p}/pct={pct}. "
                      f"Need >= {min_volume} to trust the rate; will reassess as more reports arrive.",
        }

    if p == "none":
        if days_stable >= min_days_stable and rate >= min_pass_rate:
            return {
                "title": f"{domain_name}: ready to start enforcement (move p=none -> p=quarantine, pct=10)",
                "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs) at p=none for {days_stable}d "
                          f"(>= {min_days_stable}d threshold). Safe to begin the ramp.",
            }
        return {
            "title": f"{domain_name}: hold at p=none, monitoring",
            "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs); {days_stable}/{min_days_stable}d "
                      f"stability elapsed. Not yet ready to begin enforcement.",
        }

    if pct < 100:
        if rate < low_pass_rate:
            return {
                "title": f"{domain_name}: pass rate dropped at p={p}/pct={pct} -- investigate before ramping",
                "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs), below the {low_pass_rate:.0%} "
                          f"floor. See failure_investigation action items for likely culprits.",
            }
        if rate >= min_pass_rate and days_stable >= min_days_stable:
            next_steps = [s for s in ramp_steps if s > pct]
            next_pct = next_steps[0] if next_steps else 100
            return {
                "title": f"{domain_name}: safe to raise pct={pct} -> pct={next_pct}",
                "detail": f"Sustained {rate:.1%} pass over last {window_days}d ({total} msgs) at p={p}/pct={pct} "
                          f"for {days_stable}d (>= {min_days_stable}d threshold).",
            }
        return {
            "title": f"{domain_name}: hold at p={p}/pct={pct}",
            "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs); {days_stable}/{min_days_stable}d "
                      f"stability elapsed at this pct. Keep monitoring before ramping further.",
        }

    # pct == 100
    if p == "quarantine":
        if rate >= min_pass_rate and days_stable >= min_days_stable:
            return {
                "title": f"{domain_name}: ready to move p=quarantine -> p=reject",
                "detail": f"Sustained {rate:.1%} pass over last {window_days}d ({total} msgs) at "
                          f"p=quarantine/pct=100 for {days_stable}d.",
            }
        return {
            "title": f"{domain_name}: hold at p=quarantine/pct=100",
            "detail": f"{rate:.1%} pass over last {window_days}d ({total} msgs); {days_stable}/{min_days_stable}d "
                      f"stability elapsed. Not yet ready to move to p=reject.",
        }

    # p == 'reject', pct == 100 -- fully enforced
    return None


def eligible_known_senders(conn, settings: dict):
    """known_senders rows worth spending outbound-lookup budget on: excludes
    one-off/stray IPs and senders that stopped sending long ago. Shared by
    blocklist.py and compliance.py so both check the same real infrastructure.
    """
    min_volume = int(settings["blocklist_min_volume"])
    recent_cutoff = int(datetime.datetime.utcnow().timestamp()) - int(settings["blocklist_recent_days"]) * 86400
    return conn.execute(
        """SELECT DISTINCT ks.source_ip, ks.domain_id, d.name as domain_name
           FROM known_senders ks JOIN domains d ON d.id = ks.domain_id
           WHERE ks.classification != 'ignored'
             AND ks.total_msgs >= ?
             AND ks.last_seen >= ?""",
        (min_volume, recent_cutoff),
    ).fetchall()


def upsert_system_action(conn, domain_id: int, category: str, ref_key, title: str, detail: str) -> None:
    row = conn.execute(
        """SELECT id FROM action_items
           WHERE domain_id = ? AND category = ? AND (ref_key IS ? OR ref_key = ?) AND status = 'open' AND kind = 'system_suggested'""",
        (domain_id, category, ref_key, ref_key),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE action_items SET title = ?, detail = ?, updated_at = datetime('now') WHERE id = ?",
            (title, detail, row["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO action_items (domain_id, kind, category, ref_key, title, detail)
               VALUES (?, 'system_suggested', ?, ?, ?, ?)""",
            (domain_id, category, ref_key, title, detail),
        )


def run_analysis(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    wall_now = datetime.datetime.utcnow()

    for domain in all_domains(conn):
        domain_id, domain_name = domain["id"], domain["name"]
        derive_policy_history(conn, domain_id)
        update_known_senders(conn, domain_id, domain_name, settings)

        latest_row = conn.execute(
            "SELECT MAX(date_end) as latest FROM reports WHERE domain_id = ?", (domain_id,)
        ).fetchone()
        latest_report_end = latest_row["latest"]

        findings = []
        if latest_report_end is not None:
            now_day = epoch_day(latest_report_end)
            findings += flag_new_and_failing_senders(conn, domain_id, domain_name, settings, now_day)
            findings += check_volume_spike(conn, domain_id, domain_name, settings)
            findings += detect_borrowed_sending_identity(conn, domain_id, domain_name, settings)
        findings += check_staleness(conn, domain_id, domain_name, settings, wall_now)

        for f in findings:
            upsert_system_action(conn, domain_id, f["category"], f["ref_key"], f["title"], f["detail"])

        # Clear previously-open items whose underlying condition no longer holds --
        # every other check module in this codebase (compliance.py, blocklist.py,
        # dns_check.py, mailgun.py, postmaster.py, ses_account.py, etc.) dismisses
        # its own stale items the same way once a re-check comes back clean; these
        # four categories were missing that step, so e.g. a failing sender that
        # recovers, or a stale-data warning after ingestion resumes, stayed open forever.
        still_open = {(f["category"], f["ref_key"]) for f in findings}
        for category in ("new_sender", "failure_investigation", "borrowed_sending_identity"):
            open_items = conn.execute(
                "SELECT id, ref_key FROM action_items WHERE domain_id=? AND category=? AND status='open'",
                (domain_id, category),
            ).fetchall()
            for item in open_items:
                if (category, item["ref_key"]) not in still_open:
                    conn.execute(
                        "UPDATE action_items SET status='dismissed', resolved_at=datetime('now') WHERE id=?",
                        (item["id"],),
                    )
        for category in ("data_stale", "volume_spike"):
            if not any(f["category"] == category for f in findings):
                conn.execute(
                    """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                       WHERE domain_id=? AND category=? AND status='open'""",
                    (domain_id, category),
                )

        rec = None
        if latest_report_end is not None:
            rec = recommend(conn, domain_id, domain_name, settings, latest_report_end)
        if rec:
            upsert_system_action(conn, domain_id, "ramp_recommendation", None, rec["title"], rec["detail"])
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='ramp_recommendation' AND status='open'""",
                (domain_id,),
            )

        conn.commit()

        if verbose:
            print(f"\n=== {domain_name} ===")
            if latest_report_end is None:
                print("  no reports ingested yet")
                continue
            run = current_policy_run(conn, domain_id)
            print(f"  current policy (from reports): p={run['p']} pct={run['pct']} "
                  f"since {day_to_date(epoch_day(run['observed_from']))}")
            if rec:
                print(f"  -> {rec['title']}")
                print(f"     {rec['detail']}")
            else:
                print("  -> fully enforced (p=reject, pct=100) and no active recommendation")
            open_items = conn.execute(
                "SELECT category, title FROM action_items WHERE domain_id=? AND status='open' AND category != 'ramp_recommendation'",
                (domain_id,),
            ).fetchall()
            for item in open_items:
                print(f"  [{item['category']}] {item['title']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the DMARC analysis/recommendation engine")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_analysis(conn)


if __name__ == "__main__":
    main()
