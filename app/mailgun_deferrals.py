"""
Temporary delivery refusals ("deferrals") on Mailgun -- a receiving server
saying "not now, try again later" -- and in particular the ones where a big
mailbox provider is deliberately slowing down YOUR mail.

Why this exists: on 2026-11-09 iLAB's monthly Ghost newsletter (524 people,
news.ilabindia.org) drew 35 red "failed" lines in Mailgun's log, all Gmail's

    421 4.7.28 Gmail has detected an unusual rate of mail originating from
    your SPF domain [news.ilabindia.org 35] ... temporarily rate limited

which read like a disaster. It wasn't: all 29 people affected got the
newsletter on Mailgun's automatic retry 10-35 minutes later. But nothing in
this tool could say so. The reason text only lives in Mailgun's event log,
which keeps ~5 days on this account, and mailgun_daily_stats stores just a
bare failed_temp count. The October send had the same 35 temporary failures
and nobody noticed; theigniteindia.com had 614 of 953 on 2026-09-11 and
those reasons are gone for good.

So each deferral is captured from the event pass app.mailgun_campaigns
already makes (no extra API calls), classified, and followed to its final
outcome (delivered on retry / failed / still pending).

What the classification separates, and why it matters:
  * Gmail's 4.7.28 names exactly WHAT it is throttling: your SPF domain (the
    Return-Path/bounce domain, e.g. news.ilabindia.org), your DKIM domain,
    or the sending IP. On a shared Mailgun IP pool the domain variants are
    the ones about you specifically. Observed live: a domain that sends
    almost nothing for a month and then hands Gmail ~490 messages within
    ~15 seconds gets the first ~93% accepted and the rest held for 10
    minutes -- even with perfect SPF/DKIM/DMARC and a 0% spam rate.
  * Gmail's authentication-related throttles (4.7.26/27/30/32) mean the mail
    isn't passing SPF/DKIM/DMARC -- a real configuration problem, not pace.
  * Other providers' throttling (Yahoo's TSS04 "unexpected volume",
    Microsoft's "rate limited").
  * Per-RECIPIENT deferrals (mailbox full, greylisting, one inbox receiving
    too fast). These say nothing about the sender's reputation and never
    raise anything on their own.

Only the first three ("sender-level") raise the mailgun_throttled action item.
"""

import datetime
import re
import statistics

from app.analysis import upsert_system_action

# kind -> (plain-language label, sender_level). sender_level means the
# receiving provider is slowing down the SENDER (pace/reputation/auth), as
# opposed to one recipient's own inbox being the reason.
DEFERRAL_KINDS = {
    "gmail_rate_spf": ("Gmail slowed down mail from your sending domain", True),
    "gmail_rate_dkim": ("Gmail slowed down mail signed by your domain", True),
    "gmail_rate_ip": ("Gmail slowed down mail from the sending server's IP address", True),
    "gmail_rate_other": ("Gmail slowed down your mail (unusual rate)", True),
    "gmail_unauthenticated": ("Gmail slowed down mail that failed its authentication checks", True),
    "rate_limited": ("Receiving provider slowed down your mail (rate limit)", True),
    "mailbox_full": ("Recipient's mailbox full (temporary)", False),
    "greylisted": ("Recipient's server asked for a retry (greylisting)", False),
    "recipient_busy": ("Recipient's inbox receiving mail too fast", False),
    "other": ("Other temporary refusal", False),
}
SENDER_LEVEL_KINDS = {k for k, (_label, sender) in DEFERRAL_KINDS.items() if sender}

# Gmail's auth-related deferrals: 4.7.26 unauthenticated, 4.7.27 SPF not
# passing, 4.7.30 DKIM not passing, 4.7.32 From not aligned.
_GMAIL_AUTH_CODES = {"4.7.26", "4.7.27", "4.7.30", "4.7.32"}
_RATE_LIMIT_PHRASES = ("rate limit", "rate-limit", "ratelimit", "unexpected volume", "tss04", "throttl",
                       "too many messages", "too many connections")
_MAILBOX_FULL_PHRASES = ("out of storage", "over quota", "mailbox is full", "mailbox full", "quota exceeded")
_ENHANCED_CODE_RE = re.compile(r"(?<![\d.])([245]\.\d{1,3}\.\d{1,3})(?![\d.])")
# Gmail repeats the enhanced code at the start of every continuation line
# ("...from your SPF\n4.7.28 domain [...]"), which splits phrases like
# "SPF domain" across the prefix. Strip those before matching or displaying.
_CONTINUATION_PREFIX_RE = re.compile(r"\n\s*[245]\.\d{1,3}\.\d{1,3}\s*")

# Distinct recipients sender-throttled within the lookback window before an
# action item is raised -- one stray 4.7.28 isn't a pattern worth an alert.
MIN_THROTTLED_RECIPIENTS = 3
# A deferral still "pending" this long after its last retry is treated as
# not delivered: Mailgun's own retry schedule gives up well before this.
PENDING_GIVE_UP_HOURS = 24
# Days with at least this many temporary failures (from mailgun_daily_stats)
# but no captured reasons are listed as "happened before, reason unknown".
HISTORY_MIN_FAILED_TEMP = 5


def normalize_reason(reason):
    """One readable line from a multi-line SMTP reply."""
    text = _CONTINUATION_PREFIX_RE.sub(" ", reason or "")
    return re.sub(r"\s+", " ", text).strip()


def quotable_reason(code, reason, limit=260):
    """The receiving server's words, ready to quote: code shown once (Gmail
    already starts its text with it), boilerplate help-link tail dropped."""
    text = reason or ""
    if code and not text.startswith(code):
        text = f"{code} {text}"
    text = text.split(" For more information")[0]
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def provider_name(mx_host):
    host = (mx_host or "").lower()
    if "google" in host:
        return "Gmail"
    if "outlook" in host or "hotmail" in host:
        return "Outlook"
    if "yahoo" in host:
        return "Yahoo"
    return None


def classify_deferral(reason, enhanced_code=None, mx_host=None):
    text = normalize_reason(reason).lower()
    code = enhanced_code
    if not code:
        found = _ENHANCED_CODE_RE.findall(text)
        code = found[0] if found else None
    is_google = provider_name(mx_host) == "Gmail" or "gsmtp" in text or "gmail" in text

    if code == "4.7.28" or "unusual rate" in text:
        if not is_google:
            return "rate_limited"
        if "spf domain" in text:
            return "gmail_rate_spf"
        if "dkim domain" in text:
            return "gmail_rate_dkim"
        if "ip address" in text or "ip netblock" in text:
            return "gmail_rate_ip"
        return "gmail_rate_other"
    if is_google and code in _GMAIL_AUTH_CODES:
        return "gmail_unauthenticated"
    if any(p in text for p in _RATE_LIMIT_PHRASES):
        return "rate_limited"
    if code == "4.2.2" or any(p in text for p in _MAILBOX_FULL_PHRASES):
        return "mailbox_full"
    if "greylist" in text or "graylist" in text:
        return "greylisted"
    if code == "4.2.1" or "receiving mail at a rate" in text:
        return "recipient_busy"
    return "other"


def _iso(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else None


def record_deferrals(conn, domain_id, mailgun_domain, records):
    """Upserts per-(message, recipient) deferral records built by
    mailgun_campaigns.fetch_campaigns. Re-scans run every few hours against a
    ~5-day event log, so a later scan may see FEWER of a message's events than
    an earlier one did -- the merge only ever widens the time span/attempt
    count and never downgrades a settled outcome back to 'pending'."""
    for r in records:
        reason = normalize_reason(r["reason"])
        conn.execute(
            """INSERT INTO mailgun_deferrals
               (domain_id, mailgun_domain, message_id, recipient, from_address, subject, is_newsletter, mx_host,
                kind, enhanced_code, reason, attempts, first_deferred_at, last_deferred_at, outcome, delivered_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(mailgun_domain, message_id, recipient) DO UPDATE SET
                 is_newsletter=MAX(is_newsletter, excluded.is_newsletter),
                 mx_host=COALESCE(excluded.mx_host, mx_host),
                 kind=excluded.kind, enhanced_code=excluded.enhanced_code, reason=excluded.reason,
                 attempts=MAX(attempts, excluded.attempts),
                 first_deferred_at=MIN(first_deferred_at, excluded.first_deferred_at),
                 last_deferred_at=MAX(last_deferred_at, excluded.last_deferred_at),
                 outcome=CASE WHEN excluded.outcome='pending' THEN outcome ELSE excluded.outcome END,
                 delivered_at=COALESCE(excluded.delivered_at, delivered_at),
                 updated_at=datetime('now')""",
            (domain_id, mailgun_domain, r["message_id"] or "", r["recipient"], r["from_address"], r["subject"],
             int(r["is_newsletter"]), r["mx_host"],
             classify_deferral(reason, r["enhanced_code"], r["mx_host"]), r["enhanced_code"], reason[:1000],
             r["attempts"], _iso(r["first_ts"]), _iso(r["last_ts"]), r["outcome"], _iso(r["delivered_ts"])),
        )


def _effective_outcome(row, now):
    if row["outcome"] != "pending":
        return row["outcome"]
    last = datetime.datetime.strptime(row["last_deferred_at"], "%Y-%m-%d %H:%M:%S")
    if now - last > datetime.timedelta(hours=PENDING_GIVE_UP_HOURS):
        return "failed"
    return "pending"


def _delay_minutes(row):
    if not row["delivered_at"]:
        return None
    first = datetime.datetime.strptime(row["first_deferred_at"], "%Y-%m-%d %H:%M:%S")
    done = datetime.datetime.strptime(row["delivered_at"], "%Y-%m-%d %H:%M:%S")
    return max(0.0, (done - first).total_seconds() / 60)


def deferral_summary(conn, domain_id, days=90):
    """Everything the domain page shows about delivery delays: one group per
    newsletter send (matched to its mailgun_campaigns row for the totals) plus
    one "everything else" group per Mailgun domain for transactional mail,
    newest first; and the pre-capture history from mailgun_daily_stats."""
    now = datetime.datetime.utcnow()
    since = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT * FROM mailgun_deferrals WHERE domain_id=? AND first_deferred_at >= ?
           ORDER BY first_deferred_at""",
        (domain_id, since),
    ).fetchall()

    groups = {}
    for r in rows:
        key = ((r["mailgun_domain"], r["from_address"], r["subject"]) if r["is_newsletter"]
               else (r["mailgun_domain"], None, None))
        g = groups.setdefault(key, {"rows": [], "mailgun_domain": r["mailgun_domain"],
                                    "subject": r["subject"] if r["is_newsletter"] else None,
                                    "from_address": r["from_address"], "is_newsletter": bool(r["is_newsletter"])})
        g["rows"].append(r)

    sends = []
    for (mg_domain, from_address, subject), g in groups.items():
        campaign = None
        if g["is_newsletter"]:
            campaign = conn.execute(
                """SELECT * FROM mailgun_campaigns WHERE mailgun_domain=? AND from_address=? AND subject=?
                   ORDER BY send_day DESC LIMIT 1""",
                (mg_domain, from_address, subject),
            ).fetchone()
        people = {}
        for r in g["rows"]:  # one row per (message, recipient); collapse to people
            prev = people.get(r["recipient"])
            if prev is None or (r["delivered_at"] and not prev["delivered_at"]):
                people[r["recipient"]] = r
        outcomes = {"delivered": 0, "failed": 0, "pending": 0}
        for r in people.values():
            outcomes[_effective_outcome(r, now)] += 1
        kinds = {}
        for r in people.values():
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        delays = [d for d in (_delay_minutes(r) for r in people.values()) if d is not None]
        sender_rows = [r for r in people.values() if r["kind"] in SENDER_LEVEL_KINDS]
        sample = (sender_rows or list(people.values()))[-1]
        sends.append({
            "mailgun_domain": mg_domain,
            "is_newsletter": g["is_newsletter"],
            "subject": subject,
            "first_at": g["rows"][0]["first_deferred_at"],
            "send_day": (campaign["send_day"] if campaign else None) or g["rows"][0]["first_deferred_at"][:10],
            "recipients": (campaign["delivered"] + campaign["bounced"]) if campaign else None,
            "google_recipients": campaign["google_recipients"] if campaign else None,
            "send_span_seconds": campaign["send_span_seconds"] if campaign else None,
            "people": len(people),
            "sender_level": len(sender_rows),
            "provider": provider_name(sample["mx_host"]),
            "kinds": sorted(((DEFERRAL_KINDS[k][0], n, k in SENDER_LEVEL_KINDS) for k, n in kinds.items()),
                            key=lambda x: (not x[2], -x[1])),
            "delivered": outcomes["delivered"],
            "failed": outcomes["failed"],
            "pending": outcomes["pending"],
            "delay_min": min(delays) if delays else None,
            "delay_median": statistics.median(delays) if delays else None,
            "delay_max": max(delays) if delays else None,
            "sample_reason": quotable_reason(sample["enhanced_code"], sample["reason"]),
        })
    sends.sort(key=lambda s: s["first_at"], reverse=True)

    captured_days = {(r["mailgun_domain"], r["first_deferred_at"][:10]) for r in rows}
    history = [
        dict(h) for h in conn.execute(
            """SELECT mailgun_domain, day, accepted, failed_temp FROM mailgun_daily_stats
               WHERE domain_id=? AND failed_temp >= ? AND day >= date('now', ?)
               ORDER BY day DESC""",
            (domain_id, HISTORY_MIN_FAILED_TEMP, f"-{days} days"),
        ).fetchall()
        if (h["mailgun_domain"], h["day"]) not in captured_days
    ][:8]
    capture_started = conn.execute("SELECT MIN(updated_at) AS t FROM mailgun_deferrals").fetchone()["t"]
    return {"sends": sends, "history": history,
            "capture_started": capture_started[:10] if capture_started else None}


def _fmt_day(day):
    d = datetime.date.fromisoformat(day[:10])
    return f"{d.day} {d.strftime('%b')}"


def _wait_phrase(lo, hi):
    lo, hi = round(lo or 0), round(hi or 0)
    if hi < 1:
        return "under a minute"
    if lo == hi:
        return f"about {hi} minute{'s' if hi != 1 else ''}"
    return f"{max(lo, 1)} to {hi} minutes"


def deferral_verdict(summary):
    """(status, text) for the domain page, about the most recent send that a
    provider slowed down. Wording Jev-tested (2026-11-10, 5 drafts): this
    "made N people wait X minutes ... Everyone still got it" shape was the
    first judged clear as-is, at 84% accurately calibrated / 96% natural."""
    sends = summary["sends"]
    throttled = [s for s in sends if s["sender_level"]]
    if throttled:
        s = throttled[0]
        who = s["provider"] or "A receiving mail provider"
        what = f"your {_fmt_day(s['send_day'])} newsletter" if s["is_newsletter"] else \
            f"some of your mail on {_fmt_day(s['send_day'])}"
        n = s["sender_level"]
        if s["failed"]:
            return "bad", (f"⚠️ {who} held back {n} people's copies of {what}, and {s['failed']} never got "
                           f"through.")
        if s["pending"]:
            return "warn", (f"⏳ {who} is holding back {n} people's copies of {what}. Mailgun is still "
                            f"retrying {s['pending']} of them.")
        return "warn", (f"🐢 {who} made {n} {'person' if n == 1 else 'people'} wait "
                        f"{_wait_phrase(s['delay_min'], s['delay_max'])} for {what}. Everyone still got it.")
    if sends:
        n = sum(s["people"] for s in sends)
        return "ok", (f"✅ No mail provider has slowed this domain down. {n} message(s) were briefly held by "
                      f"individual inboxes (a full mailbox, greylisting), which is normal.")
    if summary["capture_started"]:
        return "ok", f"✅ No delivery delays recorded since tracking began on {summary['capture_started']}."
    return "muted", "No delivery delays recorded yet."


def update_throttle_action_items(conn, domain_id, domain_name, mailgun_domain, lookback_days):
    """Raises mailgun_throttled while a provider has slowed this Mailgun
    domain down within `lookback_days`; auto-dismisses once it hasn't. The
    lookback is deliberately longer than Mailgun's own 5-day log retention so
    a monthly newsletter's item doesn't vanish before anyone looks."""
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
    kinds = tuple(SENDER_LEVEL_KINDS)
    rows = conn.execute(
        f"""SELECT * FROM mailgun_deferrals
            WHERE mailgun_domain=? AND first_deferred_at >= ? AND kind IN ({','.join('?' * len(kinds))})
            ORDER BY first_deferred_at""",
        (mailgun_domain, since, *kinds),
    ).fetchall()
    people = {}
    for r in rows:
        prev = people.get(r["recipient"])
        if prev is None or (r["delivered_at"] and not prev["delivered_at"]):
            people[r["recipient"]] = r
    if len(people) < MIN_THROTTLED_RECIPIENTS:
        conn.execute(
            """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
               WHERE category='mailgun_throttled' AND ref_key=? AND status='open'""",
            (mailgun_domain,),
        )
        return

    now = datetime.datetime.utcnow()
    outcomes = [_effective_outcome(r, now) for r in people.values()]
    failed, pending = outcomes.count("failed"), outcomes.count("pending")
    delays = [d for d in (_delay_minutes(r) for r in people.values()) if d is not None]
    latest = rows[-1]
    who = provider_name(latest["mx_host"]) or "A receiving mail provider"
    n = len(people)
    if failed:
        outcome_text = f"{failed} never got through"
    elif pending:
        outcome_text = f"Mailgun still retrying {pending}"
    else:
        outcome_text = "all delivered"
    days_seen = sorted({r["first_deferred_at"][:10] for r in rows})
    detail = (
        f"{who} temporarily refused mail from {mailgun_domain} for {n} recipient(s) on "
        f"{', '.join(_fmt_day(d) for d in days_seen)} and Mailgun retried automatically"
        + (f": {n - failed - pending} got through {_wait_phrase(min(delays), max(delays))} late" if delays else "")
        + (f", {failed} never did" if failed else "")
        + (f", {pending} still being retried" if pending else "")
        + f". What {who} said: \"{quotable_reason(latest['enhanced_code'], latest['reason'])}\" "
        f"This is a pace/reputation signal about the sending domain, not about individual addresses -- "
        f"don't remove these recipients from your list. See \"Delivery delays\" in the Mailgun section of the "
        f"Deliverability tab for per-send detail and history."
    )
    upsert_system_action(
        conn, domain_id, "mailgun_throttled", mailgun_domain,
        f"{domain_name}: {who} slowed down mail from {mailgun_domain} ({n} delayed, {outcome_text})",
        detail,
    )
