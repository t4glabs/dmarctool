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
    "mailgun_events_window_days": "7",   # lookback window for the per-sender-identity breakdown (event-log pull, kept shorter than the stats window since it's heavier per domain)
    "mailgun_bounce_rate_warn": "0.05",  # bounce rate (of accepted) that triggers a flag
    "mailgun_complaint_rate_warn": "0.001",  # complaint rate (of accepted) that triggers a flag
    "postmaster_recheck_hours": "24",     # Postmaster Tools data itself lags/aggregates daily
    "postmaster_stats_window_days": "30", # lookback window for the SPAM_RATE / delivery-error metrics
    "ses_stats_window_days": "30",        # lookback window for SES bounce/complaint rate (from our own accumulated counts)
    "ses_bounce_rate_watch": "0.02",       # bounce rate (of delivered) that triggers an early "watch" flag
    "ses_bounce_rate_warn": "0.05",       # bounce rate (of delivered) that triggers a flag
    "ses_complaint_rate_watch": "0.0008", # complaint rate (of delivered) that triggers an early "watch" flag
    "ses_complaint_rate_warn": "0.001",   # complaint rate (of delivered) that triggers a flag
    "ses_max_messages_per_run": "200000",  # absolute safety ceiling on one drain; the time budget below is what normally stops it
    "ses_drain_seconds": "300",            # how long a background SES event drain may run (a pure message cap couldn't keep up with real campaign volume)
    "ses_drain_seconds_interactive": "15", # much shorter budget for the "Refresh now" button, which a person is waiting on
    "ses_backlog_warn": "2000",            # queued events above this raise an action item, since it makes displayed numbers partial
    "ses_account_recheck_hours": "24",    # don't re-poll SES account health/identity verification more often than this
    "newsletter_inactive_campaigns": "9",  # campaigns received with zero opens across all of them => flagged inactive
    "mailgun_newsletter_window_days": "30",   # how far back to look for Mailgun/Ghost newsletter sends
    "mailgun_newsletter_min_recipients": "20",  # fallback newsletter test for untagged bulk sends (Ghost's own tags are the primary signal)
    "campaign_click_benchmark": "0.033",   # click rate a newsletter is graded against (nonprofit-sector median)
    "campaign_open_benchmark": "0.286",    # open rate a newsletter is graded against (nonprofit-sector median)
    "volume_spike_recent_days": "3",       # "recent" window averaged for the spike comparison
    "volume_spike_baseline_days": "7",     # "before" window averaged as the baseline
    "volume_spike_min_baseline_avg": "10", # baseline must average at least this many msgs/day to count
    "volume_spike_multiplier": "2.0",      # recent avg must be at least this many times the baseline to flag
    "safe_browsing_recheck_hours": "24",   # Safe Browsing status doesn't change fast; daily is plenty
    "domain_expiry_recheck_hours": "24",   # registration expiry dates change at most once a year; daily is plenty
    "domain_expiry_warn_days": "30",       # flag + include in the email report once expiry is this close
    "access_log_retention_days": "90",     # how long to keep who-accessed-what history before pruning it
    "report_sender_name": "Domain Health",           # display name for the domain-health email's From header
    "report_subject_template": "Your {domain} domain health update from aikyam",  # {domain} substituted at send time
    "report_signoff_name": "The aikyam Team",         # sign-off name at the bottom of the domain-health email
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
    # Pinned domains first (for the Overview grid), alphabetical within each
    # group -- harmless for every other caller here, which just iterates
    # every domain regardless of order (background checks, etc).
    return conn.execute("SELECT id, name, pinned FROM domains ORDER BY pinned DESC, name").fetchall()


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
    """The mails.<domain> bulk-sending convention is used by more ESPs than
    just Amazon SES -- Mailgun, Google Workspace, and others all show up
    under it in practice -- so this checks reverse DNS to tell them apart
    instead of assuming SES for every match (a real mislabeling bug found
    when a domain owner asked why there was no "Mailgun" option at all:
    Mailgun/Google/Outlook senders were all being auto-labeled "ses_newsletter").
    Falls back to unclassified rather than guessing wrong when PTR doesn't
    clearly confirm a known provider."""
    row = conn.execute(
        """SELECT DISTINCT ar.domain FROM record_auth_results ar
           JOIN report_records rr ON rr.id = ar.record_id
           JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND rr.source_ip = ? AND ar.domain IS NOT NULL""",
        (domain_id, source_ip),
    ).fetchall()
    auth_domains = {r["domain"] for r in row}
    if f"mails.{domain_name}" in auth_domains:
        provider = _guess_provider(_reverse_dns(source_ip))
        if provider == "Mailgun":
            return "mailgun"
        if provider == "Amazon SES":
            return "ses_newsletter"
        if provider == "Google Workspace / Gmail":
            return "workspace"
        return "unclassified"
    if domain_name in auth_domains:
        return "primary_domain"
    return "unclassified"


def _passing_auth_domains(conn, domain_id: int, source_ip: str) -> dict:
    """{domain: {mechanisms}} for every SPF/DKIM domain that actually PASSED
    for this sender's messages, per the DMARC report's own recorded auth
    results -- the shared core behind both guess_sender_identity (per-sender
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
    ("mailchannels.net", "MailChannels"),
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


WHOIS_CACHE_RECHECK_HOURS = 24 * 30  # IP network ownership rarely changes; avoid re-hitting rate-limited registries


def cached_whois_org(conn, ip: str, allow_live: bool):
    """Read-through cache in front of _whois_org() so the slow/rate-limited
    network call only ever happens once per IP per recheck window, and a live
    page render (allow_live=False) can still show whatever a background job
    already discovered instead of nothing. A cached row with org=NULL means
    "checked, found nothing" -- distinct from "never checked" -- so a dead-end
    IP isn't re-queried every single background cycle either."""
    row = conn.execute("SELECT org, checked_at FROM ip_whois_cache WHERE source_ip=?", (ip,)).fetchone()
    if row:
        checked_at = datetime.datetime.strptime(row["checked_at"], "%Y-%m-%d %H:%M:%S")
        fresh = datetime.datetime.utcnow() - checked_at < datetime.timedelta(hours=WHOIS_CACHE_RECHECK_HOURS)
        if fresh or not allow_live:
            return row["org"]
    if not allow_live:
        return None
    org = _whois_org(ip)
    conn.execute(
        """INSERT INTO ip_whois_cache (source_ip, org, checked_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(source_ip) DO UPDATE SET org=excluded.org, checked_at=excluded.checked_at""",
        (ip, org),
    )
    conn.commit()
    return org


# Consumer/residential/mobile ISPs never legitimately run bulk email
# infrastructure for a small nonprofit's newsletter -- an IP registered to one
# of these is a strong, plain signal that a low-volume sender using it is a
# spoofer, not "possibly your own setup". Deliberately not exhaustive (same
# "good enough, not exhaustive" tradeoff as _ESP_PTR_PATTERNS above) -- covers
# the major national telecoms most likely to show up in real spoofing
# attempts, plus generic residential-ISP naming patterns.
_CONSUMER_ISP_KEYWORDS = (
    "CHINANET", "CHINA MOBILE", "CHINA UNICOM", "CHINA TELECOM", "KOREA TELECOM", "PLDT",
    "COMCAST", "AT&T", "VERIZON", "CHARTER COMMUNICATIONS", "COX COMMUNICATIONS",
    "BSNL", "AIRTEL", "RELIANCE JIO", "VODAFONE", "DEUTSCHE TELEKOM", "TELEFONICA",
    "TELKOM", "TELSTRA", "BROADBAND", "RESIDENTIAL", "DSL", "CABLE", "BSKYB", "VIRGIN MEDIA",
)

# Major cloud/hosting providers CAN legitimately run a customer's real mail-
# sending app -- these get a softer "worth checking" verdict rather than the
# confident "not yours" one above, since a genuine service you use might run
# on one of these.
_CLOUD_HOSTING_KEYWORDS = (
    "AMAZON", "AWS", "GOOGLE", "MICROSOFT", "AZURE", "DIGITALOCEAN", "HETZNER", "OVH",
    "LINODE", "VULTR", "ALIBABA", "TENCENT", "ORACLE", "CLOUDFLARE", "FASTLY", "RACKSPACE",
)


def _classify_whois_org(org: str) -> str:
    """"consumer_isp" | "cloud_hosting" | "unknown" -- the distinction that
    decides whether an unrecognized IP gets a confident "not your
    infrastructure" verdict or a hedged "worth checking" one."""
    if not org:
        return "unknown"
    upper = org.upper()
    if any(kw in upper for kw in _CONSUMER_ISP_KEYWORDS):
        return "consumer_isp"
    if any(kw in upper for kw in _CLOUD_HOSTING_KEYWORDS):
        return "cloud_hosting"
    return "unknown"


def guess_sender_identity(conn, domain_id: int, domain_name: str, source_ip: str,
                           ptr: str = None, skip_lookup: bool = False) -> dict:
    """Best-effort, plain-language guess at what an unrecognized sending IP
    actually is, combining every signal DMARCTool already has -- ranked by
    how trustworthy each one actually is, not just what's available:
      0. Whether a human already manually flagged this exact IP as
         "suspicious" while reviewing a different domain -- checked before
         everything else below, since a deliberate human judgment call
         outranks even cryptographic authentication.
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
    Returns a dict {"verdict", "icon", "headline", "summary", "action"} --
    verdict-first and structured, rather than one dense hedging paragraph, so
    every caller (the Known Senders table's popover, and sender_ip_context()
    for action items) can render a clear "here's the bottom line" followed by
    the reasoning, instead of burying the verdict inside a wall of caveats.
    "verdict" is one of "flagged" | "legitimate" | "maybe" | "not_yours" |
    "unclear" -- callers use it to decide things like whether a generic
    "how to fix this" box still makes sense (it doesn't, for "not_yours"),
    or whether to show an urgent warning instead of a calm one ("flagged").
    Not a certainty --
    a starting point, same spirit as content_scoring.py's heuristics
    elsewhere in this tool. Pass `ptr` if it's already been looked up (e.g.
    cached in ptr_checks, or from a _reverse_dns() call the caller already
    made) to avoid a redundant lookup. If `ptr` is None and this is being
    called from a live web request (not the background analysis job), pass
    skip_lookup=True -- _reverse_dns() and a fresh WHOIS lookup are both
    blocking network calls (the latter can take several seconds and is
    sometimes rate-limited), which have no business running synchronously
    inside a page render. With skip_lookup=True, the WHOIS step still reads
    from ip_whois_cache if a background job already looked this IP up -- it
    just won't trigger a new live lookup."""
    if ptr is None and not skip_lookup:
        ptr = _reverse_dns(source_ip)
    provider = _guess_provider(ptr)
    auth_domains = {
        d for d in _passing_auth_domains(conn, domain_id, source_ip)
        if d != domain_name and d not in _ESP_DEFAULT_AUTH_DOMAINS
    }
    cross_domain = _cross_domain_labels(conn, source_ip, domain_id)
    cross_domain_names = {row["name"] for row in cross_domain}

    # A human already reviewed this exact IP and flagged it as suspicious
    # while looking at a different domain -- that's a stronger signal than
    # cryptographic authentication, which only proves *who* technically sent
    # a message, not whether they're trustworthy. Checked first, ahead of
    # every other signal below, so it can never get softened into "probably
    # just a shared ESP pool."
    flagged_domains = [row["name"] for row in cross_domain if row["classification"] == "suspicious"]
    if flagged_domains:
        return {
            "verdict": "flagged",
            "icon": "🚨",
            "headline": "Flagged as suspicious elsewhere in your portfolio",
            "summary": (f"You've already marked this exact IP as suspicious (spam or spoofing) while reviewing "
                        f"{', '.join(flagged_domains)}. That's a stronger signal than authentication alone -- "
                        f"worth treating this with real caution here too, even if it also authenticates as "
                        f"something else."),
            "action": "Consider marking this sender suspicious here as well, and keep an eye on where else this IP shows up.",
        }

    if auth_domains and cross_domain_names and not (auth_domains & cross_domain_names):
        cross_labels = "; ".join(f"{row['name']}: {classification_label(row['classification'])}" for row in cross_domain)
        provider_note = f" ({provider})" if provider else ""
        return {
            "verdict": "legitimate",
            "icon": "✅",
            "headline": f"Shared sending IP{provider_note}, not dedicated infrastructure",
            "summary": (f"It authenticates as {', '.join(sorted(auth_domains))} for this domain's own mail, but "
                        f"this exact IP has separately been labeled elsewhere in your portfolio ({cross_labels}). "
                        f"That mismatch usually just means the provider recycles this IP across many unrelated "
                        f"customers, not that the two domains are actually related."),
            "action": "No action needed -- this is normal behavior for a shared-IP email provider like Mailgun or SendGrid.",
        }

    if auth_domains:
        return {
            "verdict": "legitimate",
            "icon": "✅",
            "headline": "Likely legitimate",
            "summary": (f"It authenticates as {', '.join(sorted(auth_domains))}"
                        + (f" ({provider})" if provider else "") + "."),
            "action": (f"No action needed if {', '.join(sorted(auth_domains))} is a domain or service you use -- "
                       f"this is just mail sent under a different identity."),
        }

    if cross_domain_names:
        labels = "; ".join(f"{row['name']}: {classification_label(row['classification'])}" for row in cross_domain)
        return {
            "verdict": "maybe",
            "icon": "❓",
            "headline": "Possibly related to another domain you track",
            "summary": (f"This exact IP is already labeled for another domain you track ({labels}). If that's a "
                        f"shared ESP IP pool (common with Mailgun/SendGrid), that alone doesn't guarantee a real "
                        f"relationship -- it's a weaker signal than an actual authenticating domain, which wasn't "
                        f"found here."),
            "action": "Worth a look if you don't recognize the connection; otherwise no action needed.",
        }

    if provider:
        return {
            "verdict": "maybe",
            "icon": "✅",
            "headline": f"Likely legitimate ({provider})",
            "summary": f"It's sent through {provider}, based on its reverse DNS, but doesn't authenticate as any domain you track.",
            "action": (f"Legitimate if you use {provider} for something; otherwise check {provider}'s own "
                       f"sending/activity logs for a message matching this IP's volume and date range."),
        }

    whois_org = cached_whois_org(conn, source_ip, allow_live=not skip_lookup)
    org_class = _classify_whois_org(whois_org)

    if org_class == "consumer_isp":
        return {
            "verdict": "not_yours",
            "icon": "✅",
            "headline": "Not your infrastructure -- likely a blocked spoofing attempt",
            "summary": (f"It's a home/business internet connection (\"{whois_org}\"), not Mailgun, SES, Google, "
                        f"or any platform you use -- consumer internet providers never run bulk email "
                        f"infrastructure like this."),
            "action": "No action needed. This is DMARC protection working as intended.",
        }

    if org_class == "cloud_hosting":
        return {
            "verdict": "maybe",
            "icon": "❓",
            "headline": "Possibly your infrastructure -- worth checking",
            "summary": (f"It's hosted on {whois_org}, a cloud/hosting provider -- this could be a real app or "
                        f"service you use, or it could be a spoofer renting cloud infrastructure."),
            "action": f"Check whether you (or a service you use) run anything on {whois_org} that could plausibly send mail as this domain.",
        }

    if whois_org:
        return {
            "verdict": "unclear",
            "icon": "❓",
            "headline": "Unclear -- worth a quick look",
            "summary": f"Its network is registered to \"{whois_org}\", which doesn't clearly look like either a residential ISP or a major cloud provider.",
            "action": f"Check whether you (or a service you use) run anything on {whois_org}'s infrastructure that could plausibly send mail as this domain.",
        }

    return {
        "verdict": "unclear",
        "icon": "❓",
        "headline": "Genuinely unfamiliar",
        "summary": "No provider, authenticating domain, or prior labeling was found for this IP.",
        "action": ("If this same IP starts showing up across multiple of your tracked domains without ever "
                   "authenticating as any of them, that's a stronger sign of spoofing than a misconfigured "
                   "integration -- a single one-off is usually low-risk noise."),
    }


def sender_ip_context(conn, domain_id: int, domain_name: str, source_ip: str, category_fact: dict = None) -> dict:
    """Shared by every check module that flags a raw IP in an action item
    (blocklist.py, compliance.py's PTR check) -- a bare IP on its own tells a
    reader nothing about whether it's real sending infrastructure or an
    unrelated one-off spoofing attempt, forcing manual WHOIS/PTR research
    every time. Returns {"verdict", "detail"}: `detail` is a verdict-first
    string -- headline, then each supporting fact (volume/first-seen, then
    guess_sender_identity()'s own analysis, then an optional category-
    specific fact -- e.g. "It's also on a public spam blocklist (...)") as
    its own bulleted line, then an action line -- rather than one dense
    paragraph mixing all of that together with no visual separation.
    `verdict` lets the caller (e.g. the domain page's "how to fix this" box)
    decide whether generic remediation advice still makes sense for THIS
    specific IP -- it doesn't, for "not_yours". `category_fact`, if given, is
    a {"not_yours": str, "otherwise": str} pair -- the wording needs to
    differ ("this fake attempt was already going nowhere" vs "this needs
    fixing"), and the verdict is only known after guess_sender_identity()
    runs, so the caller can't safely pick one string up front. Computed here
    where a slow WHOIS call is safe (background-job callers only -- never a
    live page render)."""
    sender = conn.execute(
        "SELECT total_msgs, first_seen FROM known_senders WHERE domain_id=? AND source_ip=?",
        (domain_id, source_ip),
    ).fetchone()
    volume_fact = f"Sent mail pretending to be {domain_name}"
    if sender:
        first_seen = datetime.datetime.utcfromtimestamp(sender["first_seen"]).date().isoformat()
        volume_fact = (f"Sent {sender['total_msgs']} message{'s' if sender['total_msgs'] != 1 else ''} "
                       f"pretending to be {domain_name} (first seen {first_seen})")

    guess = guess_sender_identity(conn, domain_id, domain_name, source_ip, skip_lookup=False)
    bullets = [volume_fact, guess["summary"]]
    if category_fact:
        bullets.append(category_fact["not_yours"] if guess["verdict"] == "not_yours" else category_fact["otherwise"])
    bullet_text = "\n".join(f"• {b}" for b in bullets)

    detail = f"{guess['icon']} {guess['headline']}\n\n{bullet_text}\n\n→ {guess['action']}"
    return {"verdict": guess["verdict"], "detail": detail}


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


def likely_causal_senders(conn, domain_id: int, domain_name: str, settings: dict, skip_lookup: bool = True) -> list:
    """Senders currently failing badly enough to trigger their own
    failure_investigation item (same thresholds), reused to connect a
    Postmaster DMARC-alignment/deliverability flag back to a concrete likely
    cause instead of leaving 'fix whichever's flagged' as the only guidance.
    Includes the same guess_sender_identity() verdict/headline shown on that
    failure_investigation item itself, so this note is self-contained --
    no need to go cross-reference the Known Senders table separately.
    Defaults to skip_lookup=True (safe for web.py's live-page-render caller);
    postmaster.py's background-job caller passes skip_lookup=False since a
    fresh WHOIS lookup is safe there."""
    high_vol = int(settings["high_volume_fail_threshold"])
    high_fail_rate = float(settings["high_fail_rate_threshold"])
    rows = conn.execute("SELECT * FROM known_senders WHERE domain_id = ?", (domain_id,)).fetchall()
    out = []
    for s in rows:
        total = s["total_msgs"]
        pass_rate = s["pass_msgs"] / total if total else 0
        if total >= high_vol and pass_rate < high_fail_rate:
            guess = guess_sender_identity(conn, domain_id, domain_name, s["source_ip"], skip_lookup=skip_lookup)
            out.append({
                "source_ip": s["source_ip"], "pass_rate": pass_rate, "total": total,
                "icon": guess["icon"], "headline": guess["headline"],
            })
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
            bullets = [
                f"First seen {day_to_date(first_seen_day)}, {total} msgs, {pass_rate:.0%} pass",
                f"PTR: {ptr}" if ptr else "No PTR record",
                guess["summary"],
            ]
            bullet_text = "\n".join(f"• {b}" for b in bullets)
            detail = f"{guess['icon']} {guess['headline']}\n\n{bullet_text}\n\n→ {guess['action']}"
            findings.append({
                "category": "new_sender", "ref_key": s["source_ip"],
                "title": f"New unrecognized sender {s['source_ip']} on this domain",
                "detail": detail,
            })

        if total >= high_vol and pass_rate < high_fail_rate:
            ptr = _reverse_dns(s["source_ip"]) if s["classification"] == "unclassified" else None
            label = classification_label(s["classification"])
            guess = guess_sender_identity(conn, domain_id, domain_name, s["source_ip"], ptr=ptr)
            bullets = [
                f"{total} msgs, {pass_rate:.0%} pass ({s['fail_msgs']} failing)",
                f"Labeled as: {label}" + (f", PTR: {ptr}" if ptr else ""),
                guess["summary"],
            ]
            bullet_text = "\n".join(f"• {b}" for b in bullets)
            detail = f"{guess['icon']} {guess['headline']}\n\n{bullet_text}\n\n→ {guess['action']}"
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


def portfolio_daily_pass_series(conn, days: int = 60):
    """Same shape as daily_pass_series, but summed across every tracked
    domain -- for the overview page's single portfolio-wide trend chart.
    Anchored to the latest ingested report across ALL domains, same
    "data quality, not wall-clock" rationale as the per-domain version."""
    latest_row = conn.execute("SELECT MAX(date_end) as latest FROM reports").fetchone()
    if not latest_row or latest_row["latest"] is None:
        return []
    latest_day = epoch_day(latest_row["latest"])
    start_day = latest_day - days + 1

    rows = conn.execute(
        """SELECT r.date_begin, rr.count, rr.dkim_result, rr.spf_result
           FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.date_begin >= ?""",
        (start_day * 86400,),
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
    """Per-day SES counts for charting, as dicts:
    {"day", "volume", "bounce_num", "bounce_den", "complaint_num", "complaint_den"}.

    Returns raw numerators/denominators rather than pre-divided daily rates on
    purpose. SES timestamps a bounce when the bounce *happens*, which is
    routinely a day or more after the send -- one real campaign here put 143
    of its 388 bounces on the send day and the other 245 on the next day,
    which had zero deliveries. Pre-dividing gives that day an undefined rate,
    so those 245 bounces vanished from the trend entirely and the chart
    understated the real bounce rate by ~2.7x. Handing counts to the chart
    lets a rolling window sum numerators and denominators separately, which
    is both the correct way to average a rate and immune to that lag.

    Bounce denominator is delivered+bounced ("attempted"), not delivered:
    delivered and bounced are disjoint SES outcomes, so dividing by delivered
    alone can exceed 100%. Complaints keep delivered as the denominator,
    since a complaint requires the message to have arrived."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, SUM(delivered) as delivered, SUM(bounced) as bounced, SUM(complained) as complained
           FROM ses_event_counts WHERE domain_id=? AND day >= ?
           GROUP BY day ORDER BY day""",
        (domain_id, since),
    ).fetchall()
    series = []
    for r in rows:
        delivered, bounced = r["delivered"] or 0, r["bounced"] or 0
        series.append({
            "day": r["day"],
            "volume": delivered,
            "bounce_num": bounced,
            "bounce_den": delivered + bounced,
            "complaint_num": r["complained"] or 0,
            "complaint_den": delivered,
        })
    return series


def recent_campaigns(conn, domain_id: int, limit: int = 10, settings: dict = None):
    """List of dicts, most recent newsletter first -- built entirely from SES's
    own Open/Click/Bounce/Complaint/Delivery events for messages that carry a
    Listmonk X-Listmonk-Campaign header. This is SES's own numbers, not
    Listmonk's -- the two can disagree since Listmonk tracks opens/clicks
    itself via pixel/link rewriting, while this reads what SES actually saw.

    `settings` only feeds the per-campaign scorecard's benchmarks (see
    app.campaign_score); callers that just want the raw per-campaign numbers
    can leave it out and the scorecard falls back to its own defaults."""
    from app.bounce_reasons import categorize_bounce
    from app.campaign_score import score_campaign
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
            # NOTE the opened/clicked columns are raw EVENT counts, not people
            # -- one recipient clicking five links counts five times. Dividing
            # them by `delivered` (open_rate/click_rate below) therefore
            # overstates engagement badly (one real send here: 1473 click
            # events from 203 actual people) and can even exceed 100%. They're
            # kept because they're what SES literally reported, but anything
            # comparing against an industry benchmark must use the unique_*
            # rates instead -- unique people is how open/click rate is defined
            # everywhere else.
            "open_rate": (r["opened"] or 0) / delivered if delivered else None,
            "click_rate": (r["clicked"] or 0) / delivered if delivered else None,
            # delivered+bounced ("attempted"), not delivered -- a bounced
            # message was never delivered, so delivered alone can exceed 100%.
            "bounce_rate": ((r["bounced"] or 0) / (delivered + (r["bounced"] or 0))
                            if (delivered + (r["bounced"] or 0)) else None),
            "complaint_rate": (r["complained"] or 0) / delivered if delivered else None,
            "unique_openers": open_quality["total_openers"],
            "unique_clickers": click_quality["total_clickers"],
            "unique_open_rate": (open_quality["total_openers"] / delivered) if delivered else None,
            "unique_click_rate": (click_quality["total_clickers"] / delivered) if delivered else None,
            "click_quality": click_quality,
            "genuine_click_rate": (click_quality["genuine"] / delivered) if delivered else None,
            "open_quality": open_quality,
            "genuine_open_rate": (open_quality["genuine"] / delivered) if delivered else None,
            "bounce_breakdown": bounce_breakdown.most_common(),
            # from_address matters here: it's what makes the gmail.com-
            # impersonation check in check_display_name reachable at all.
            "display_name_issues": check_display_name(r["from_display_name"], r["from_address"]),
            "rejected": r["rejected"] or 0,
            "unsubscribe_issues": check_unsubscribe_compliance(r["list_unsubscribe"], r["list_unsubscribe_post"]),
            "header_issues": check_header_hygiene(r["message_id"], r["subject"]),
            "subject_score": score_text(r["subject"]),
            "body_score": score_text(r["body_text"]) if r["body_text"] else None,
            "has_plain_text": bool(r["body_text"]),
            # Delivery events genuinely never reached us for this send, but
            # opens/clicks did -- which happens for campaigns sent before this
            # domain's SES configuration set started publishing to the event
            # pipeline (opens keep arriving for days afterwards, so they got
            # captured while the send-time Delivery events did not). SES has no
            # backfill, so this is unrecoverable rather than a sync that will
            # catch up. Flagged so the UI can say that instead of showing a
            # bare "0 delivered" next to thousands of opens, which reads as a
            # bug. unique_openers is a sound floor for how many really got it.
            "delivery_data_missing": (
                not delivered and (open_quality["total_openers"] or click_quality["total_clickers"])
            ),
        })
        structure = analyze_html(r["body_html"]) if r["body_html"] else None
        out[-1]["structure"] = structure
        out[-1]["report_card"] = score_campaign(out[-1], structure, settings)
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
    """Same dict shape as ses_daily_series (see its docstring for why counts
    rather than pre-divided rates) so both share one chart function.

    Mailgun's bounce denominator is `accepted` -- the messages Mailgun took
    responsibility for -- which is what Mailgun's own dashboard and this
    project's mailgun.py/verdicts.py/watchlist.py already use. The daily
    series was the odd one out, dividing by `delivered` and so overstating
    the rate (a permanently-failed message is never delivered)."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, SUM(accepted) as accepted, SUM(delivered) as delivered,
                  SUM(failed_perm) as failed_perm, SUM(complained) as complained
           FROM mailgun_daily_stats WHERE domain_id=? AND day >= ?
           GROUP BY day ORDER BY day""",
        (domain_id, since),
    ).fetchall()
    series = []
    for r in rows:
        delivered, accepted = r["delivered"] or 0, r["accepted"] or 0
        series.append({
            "day": r["day"],
            "volume": delivered,
            "bounce_num": r["failed_perm"] or 0,
            "bounce_den": accepted,
            "complaint_num": r["complained"] or 0,
            "complaint_den": delivered,
        })
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


def recent_mailgun_campaigns(conn, domain_id: int, limit: int = 10, settings: dict = None):
    """Newsletters this domain sent through Mailgun (Ghost blogs, typically),
    scored with the same scorecard as the SES+Listmonk ones so the two read
    identically -- see app.mailgun_campaigns for how sends are detected and
    what Mailgun's API can and cannot tell us."""
    from app.campaign_score import score_campaign
    from app.content_scoring import score_text
    from app.display_name_checks import check_display_name
    from app.header_compliance import check_header_hygiene

    rows = conn.execute(
        """SELECT * FROM mailgun_campaigns WHERE domain_id=?
           ORDER BY send_day DESC, delivered DESC LIMIT ?""",
        (domain_id, limit),
    ).fetchall()

    out = []
    for r in rows:
        delivered, bounced = r["delivered"] or 0, r["bounced"] or 0
        attempted = delivered + bounced
        out.append({
            "campaign_id": r["message_id"] or f"{r['mailgun_domain']}:{r['subject']}",
            "mailgun_domain": r["mailgun_domain"],
            "subject": r["subject"] or "(no subject captured)",
            "from_display_name": r["from_display_name"],
            "send_day": r["send_day"],
            "delivered": delivered,
            "opened": r["open_events"] or 0,
            "clicked": r["click_events"] or 0,
            "bounced": bounced,
            "complained": r["complained"] or 0,
            "unique_openers": r["unique_openers"] or 0,
            "unique_clickers": r["unique_clickers"] or 0,
            "unique_open_rate": ((r["unique_openers"] or 0) / delivered) if delivered else None,
            "unique_click_rate": ((r["unique_clickers"] or 0) / delivered) if delivered else None,
            "bounce_rate": (bounced / attempted) if attempted else None,
            "complaint_rate": ((r["complained"] or 0) / delivered) if delivered else None,
            "bounce_breakdown": [],
            "display_name_issues": check_display_name(r["from_display_name"], r["from_address"]),
            "header_issues": check_header_hygiene(r["message_id"], r["subject"]),
            # Mailgun's Events API never exposes List-Unsubscribe/-Post, so this
            # is unknowable here rather than failing -- declared so the
            # scorecard shrinks that pillar instead of penalising the campaign.
            "unsubscribe_issues": [],
            "technical_checks_unavailable": [
                "one-click unsubscribe headers (Mailgun's event data doesn't include them)"
            ],
            "subject_score": score_text(r["subject"]),
            # No message body is available from Mailgun, so content/layout
            # scoring genuinely can't run for these.
            "body_score": None,
            "has_plain_text": None,
            "open_quality": None,
            "click_quality": None,
            "open_tracking": bool(r["open_tracking"]),
            "click_tracking": bool(r["click_tracking"]),
            "tags": r["tags"],
            "source": "mailgun",
        })
        out[-1]["report_card"] = score_campaign(out[-1], None, settings)
    return out


def rate_trend_summary(series, window_days: int = 7) -> dict:
    """Current-vs-prior-window comparison and a plain "steady"/"volatile"
    read for a (date, numerator, denominator) series -- the numbers a chart
    alone still leaves you doing mental math for: is this actually getting
    better or worse, and is the latest daily number even meaningful or just
    noise from a small day's volume.

    Averages by summing numerators and denominators across the window (not
    by averaging daily rates), so a big send day isn't given the same weight
    as a 3-message day, and so events whose numerator and denominator land
    on different days -- bounce attribution lag -- still pair up correctly."""
    def window_rate(pts):
        num = sum(n for _, n, _ in pts)
        den = sum(d for _, _, d in pts)
        return (num / den) if den else None

    if not series:
        return {"latest": None, "latest_date": None, "avg_recent": None, "avg_prior": None,
                "delta": None, "stability": None, "recent_volume": 0}

    recent = series[-window_days:]
    prior = series[-2 * window_days:-window_days]
    avg_recent = window_rate(recent)
    avg_prior = window_rate(prior) if prior else None

    with_rate = [(d, n / den) for d, n, den in series if den]
    latest_date, latest_rate = with_rate[-1] if with_rate else (None, None)
    delta = (avg_recent - avg_prior) if (avg_recent is not None and avg_prior is not None) else None
    recent_volume = sum(d for _, _, d in recent)

    stability = None
    if avg_recent is not None and len([1 for _, _, d in recent if d]) >= 3:
        rates = [n / d for _, n, d in recent if d]
        mean = sum(rates) / len(rates)
        stdev = (sum((r - mean) ** 2 for r in rates) / len(rates)) ** 0.5
        # "Volatile" if day-to-day swings are large relative to the average
        # itself, OR in absolute terms for a near-zero average -- a rate
        # that's basically 0 most days with an occasional 1% spike is still
        # volatile even though "60% of a tiny number" reads as small.
        stability = "volatile" if (avg_recent > 0 and stdev > avg_recent * 0.6) or stdev > 0.01 else "steady"

    return {
        "latest": latest_rate, "latest_date": latest_date,
        "avg_recent": avg_recent, "avg_prior": avg_prior,
        "delta": delta, "stability": stability, "recent_volume": recent_volume,
    }


def health_score_series(conn, domain_id: int, days: int = 90):
    """List of (date_str, health_score_or_None) from domain_health_snapshots
    -- the one-number-combining-everything trend (pass rate + Gmail spam
    rate + bounce/complaint rate, see snapshot_domain_health's weights),
    for charting instead of four separate lines. Only as long as the
    snapshot history itself (snapshot_domain_health runs once per domain
    per run_analysis() call, so this lengthens naturally over time)."""
    since = (datetime.datetime.utcnow().date() - datetime.timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT snapshot_date, health_score FROM domain_health_snapshots
           WHERE domain_id=? AND snapshot_date >= ? ORDER BY snapshot_date""",
        (domain_id, since),
    ).fetchall()
    return [(r["snapshot_date"], r["health_score"]) for r in rows]


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


def snapshot_domain_health(conn, domain_id: int, domain_name: str, settings: dict) -> None:
    """Once-a-day composite health snapshot for this domain, stored in
    domain_health_snapshots. Reuses the same day-keyed history tables the
    dashboard's own trend charts already read (mailgun_daily_stats,
    ses_event_counts, postmaster_daily_stats/postmaster_stats) rather than
    adding a new collection path -- this is pure derived history, safe to
    recompute, and idempotent per domain per day via INSERT OR IGNORE.
    """
    today = datetime.datetime.utcnow().date().isoformat()
    already = conn.execute(
        "SELECT 1 FROM domain_health_snapshots WHERE domain_id=? AND snapshot_date=?",
        (domain_id, today),
    ).fetchone()
    if already:
        return

    now_epoch = int(datetime.datetime.utcnow().timestamp())
    window_start_epoch = now_epoch - 30 * 86400
    _, _, pass_rate = domain_window_stats(conn, domain_id, window_start_epoch, now_epoch)

    pm_row = conn.execute(
        "SELECT spam_rate FROM postmaster_stats WHERE domain_id=? ORDER BY checked_at DESC LIMIT 1",
        (domain_id,),
    ).fetchone()
    postmaster_spam_rate = pm_row["spam_rate"] if pm_row and pm_row["spam_rate"] is not None else None

    since_day = (datetime.datetime.utcnow().date() - datetime.timedelta(days=30)).isoformat()
    mg_row = conn.execute(
        """SELECT SUM(accepted) as accepted, SUM(delivered) as delivered,
                  SUM(failed_perm) as failed_perm, SUM(complained) as complained
           FROM mailgun_daily_stats WHERE domain_id=? AND day >= ?""",
        (domain_id, since_day),
    ).fetchone()
    ses_row = conn.execute(
        """SELECT SUM(delivered) as delivered, SUM(bounced) as bounced, SUM(complained) as complained
           FROM ses_event_counts WHERE domain_id=? AND day >= ?""",
        (domain_id, since_day),
    ).fetchone()
    combined_delivered = (mg_row["delivered"] or 0) + (ses_row["delivered"] or 0)
    combined_bounced = (mg_row["failed_perm"] or 0) + (ses_row["bounced"] or 0)
    combined_complained = (mg_row["complained"] or 0) + (ses_row["complained"] or 0)
    # Bounce denominator must be what was ATTEMPTED, not what was delivered --
    # a bounced message was never delivered, so dividing by delivered alone
    # can exceed 100% (a real row here recorded 6 bounces against 3 delivered
    # and stored bounce_rate=2.0, i.e. 200%, which then fed the health score
    # and the domain's Good/Bad/Ugly grade). Mailgun's own denominator is
    # `accepted`; SES's is delivered+bounced. Complaints stay over delivered,
    # since a complaint requires the message to have arrived.
    combined_attempted = (mg_row["accepted"] or 0) + (ses_row["delivered"] or 0) + (ses_row["bounced"] or 0)
    bounce_rate = min(combined_bounced / combined_attempted, 1.0) if combined_attempted else None
    complaint_rate = combined_complained / combined_delivered if combined_delivered else None

    policy_p = policy_pct = None
    run = current_policy_run(conn, domain_id)
    if run:
        policy_p, policy_pct = run["p"], run["pct"]

    weighted_sum = 0.0
    weight_total = 0.0
    for value, weight, threshold in (
        (pass_rate, 40, None),
        (postmaster_spam_rate, 25, 0.003),
        (bounce_rate, 20, 0.05),
        (complaint_rate, 15, 0.001),
    ):
        if value is None:
            continue
        component = value if threshold is None else 1 - min(value / threshold, 1)
        weighted_sum += component * weight
        weight_total += weight
    health_score = (weighted_sum / weight_total) * 100 if weight_total else None

    conn.execute(
        """INSERT OR IGNORE INTO domain_health_snapshots
           (domain_id, snapshot_date, pass_rate, postmaster_spam_rate, bounce_rate,
            complaint_rate, policy_p, policy_pct, health_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (domain_id, today, pass_rate, postmaster_spam_rate, bounce_rate,
         complaint_rate, policy_p, policy_pct, health_score),
    )


def run_analysis(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    wall_now = datetime.datetime.utcnow()

    for domain in all_domains(conn):
        domain_id, domain_name = domain["id"], domain["name"]
        derive_policy_history(conn, domain_id)
        update_known_senders(conn, domain_id, domain_name, settings)
        snapshot_domain_health(conn, domain_id, domain_name, settings)

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
