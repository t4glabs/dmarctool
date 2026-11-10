"""
Newsletter detection and per-campaign stats for mail sent through Mailgun
rather than SES+Listmonk -- in practice Ghost blogs with Mailgun integrated.

Why this is a separate path from ses_events.py: SES campaigns are identified
by Listmonk's own X-Listmonk-Campaign header, which SES echoes back on every
event, so grouping is exact. Mailgun's Events API has no campaign identifier
and exposes only from/subject/to/message-id headers. So a "campaign" here is
reconstructed by grouping delivery events on (from_address, subject) -- Ghost
gives each newsletter issue its own subject, so within one fetch window that
reliably identifies one send.

Distinguishing newsletters from Ghost's transactional mail (member signup
notifications, "complete your sign up" confirmations, member-import
receipts, test sends) matters because none of those belong on a newsletter
scorecard. Two signals, checked in that order:

  1. Mailgun tags. Ghost tags every bulk newsletter with `bulk-email` and
     `ghost-email`, and tags nothing else. Measured across four live Ghost
     domains, the separation was total: the one real newsletter carried
     ('bulk-email','ghost-email') on all 524 deliveries, while every
     transactional subject carried no tags at all. This is Ghost telling us
     directly, so it's the primary signal.
  2. A broadcast shape, for bulk senders that don't tag (or if Ghost changes
     its tagging): at least `mailgun_newsletter_min_recipients` distinct
     people AND many recipients sharing ONE message-id. That second half
     matters -- recipient count alone is not safe. Measured live, Ghost's
     "Complete your sign up to X" confirmations reached 43 and 31 people
     (over the 20-recipient line) but as ~1 message-id per recipient spread
     across 3-4 days, because they trickle out one at a time as people join.
     A real broadcast is the opposite shape: 524 recipients, ONE message-id,
     one day. So the test is recipients-per-message-id >= BROADCAST_RATIO,
     which separated every real case here by two orders of magnitude
     (524.0 vs 0.3-1.0) and correctly excludes an individually-addressed
     per-user forum digest that recipient count alone would have swept in.

Open/click attribution: Mailgun's open and click events carry NO subject
header, so they can't be grouped the same way -- but they do carry
message-id, and the delivery events give us message-id -> campaign. Joining
on that is what makes engagement scoring possible here at all.

Known limits, all surfaced rather than hidden:
  * No List-Unsubscribe header is exposed by the Events API, so one-click
    unsubscribe compliance genuinely cannot be checked for these campaigns.
  * No message body, so content/layout scoring isn't possible either.
  * History only goes back as far as Mailgun's own event retention and this
    module's fetch window.
"""

import collections
import datetime
import email.utils
import urllib.parse

from app.analysis import ensure_default_settings
from app.config import get_secret
from app.mailgun import API_BASE, _get, list_mailgun_domains, match_tracked_domains
from app.mailgun_deferrals import record_deferrals, update_throttle_action_items

# Tags Ghost puts on bulk newsletter sends (and on nothing else).
NEWSLETTER_TAGS = {"bulk-email", "ghost-email"}

# Recipients per distinct message-id above which a send is a broadcast rather
# than individually-addressed mail. Live data clustered at 524.0 for a real
# newsletter and 0.3-1.0 for everything transactional, so anything in this
# range is safe; 5 keeps a wide margin on both sides.
BROADCAST_RATIO = 5

MAX_PAGES = 20  # safety bound on Events API pagination per event type


def fetch_tracking_settings(mailgun_domain, api_key):
    """(open_enabled, click_enabled, web_scheme) straight from Mailgun's own
    domain settings, or (None, None, None) if it couldn't be read. web_scheme
    'http' means tracked links are plain-HTTP addresses (see
    mailgun_domain_settings in schema.sql).

    This is authoritative, unlike inferring from whether events showed up --
    but it describes the domain RIGHT NOW, and Mailgun tracking only applies
    to newly sent mail. A campaign sent before tracking was switched on has
    no open/click events and never will, so this setting must not be used to
    decide whether a past send is scorable (that would grade a 0% click rate
    against a send that was simply never instrumented). It's used only to
    explain the gap and to tell the reader that future sends will be
    measurable -- per-campaign event presence still decides scoring.
    """
    data, err = _get(f"{API_BASE}/domains/{mailgun_domain}/tracking", api_key)
    if err or not data:
        return None, None, None
    tracking = data.get("tracking") or {}
    return ((tracking.get("open") or {}).get("active"),
            (tracking.get("click") or {}).get("active"),
            tracking.get("web_scheme"))


def _fetch_event_pages(mailgun_domain, api_key, event, begin, limit=300):
    """Yields raw event items for one event type, following Mailgun's paging."""
    url = (f"{API_BASE}/{mailgun_domain}/events?event={event}"
           f"&begin={urllib.parse.quote(begin)}&ascending=yes&limit={limit}")
    pages = 0
    while url and pages < MAX_PAGES:
        data, err = _get(url, api_key)
        if err:
            return
        items = data.get("items", [])
        if not items:
            return
        for item in items:
            yield item
        url = data.get("paging", {}).get("next")
        pages += 1


def fetch_campaigns(mailgun_domain, api_key, window_days, min_recipients,
                     tracking_open=None, tracking_click=None):
    """Returns (campaigns, deferrals, error). Each campaign is a dict of raw
    counts -- see the module docstring for how sends are grouped and how
    newsletters are told apart from Ghost's transactional mail. `deferrals`
    is one record per (message, recipient) that got a temporary refusal, for
    app.mailgun_deferrals -- built from this same event pass, since these
    temporary-failure events are the only place the receiving server's
    reason ever appears and Mailgun expires them within days."""
    begin = email.utils.format_datetime(
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=window_days)
    )

    # (from_address, subject) -> aggregate
    groups = {}
    msgid_to_key = {}  # message-id -> group key, so open/click events can be attributed
    # (group_key, recipient, message_id) -> final outcome. Mailgun can log a
    # `delivered` and then, seconds later, a PERMANENT `failed` for the very
    # same message and recipient: it handed the message to the receiving MX
    # (which looks like delivery) and that server then rejected it outright
    # ("550 account does not exist", "553 relaying disallowed"). Two real
    # recipients in this account did exactly that. Naively unioning the event
    # types counts those people as both delivered and bounced -- or, if
    # bounces are then de-duplicated against deliveries, as delivered and NOT
    # bounced, which is the worse of the two errors. Events are fetched
    # ascending, so keeping the last outcome per (recipient, message) gives
    # the true final result.
    outcomes = {}
    # Per (group_key, recipient, message_id): earliest attempt of any kind
    # (for how much of a burst the send was), the receiving server's
    # hostname (Google's MX hosts identify Gmail + Workspace recipients, which
    # an address's own domain can't), when it finally delivered, and its
    # temporary refusals if any.
    first_attempt = {}
    mx_hosts = {}
    delivered_ts = {}
    deferred = {}

    def group_for(from_raw, subject):
        display, addr = email.utils.parseaddr(from_raw or "")
        addr = (addr or from_raw or "").lower()
        key = (addr, subject or "")
        g = groups.setdefault(key, {
            "from_address": addr, "from_display_name": display or None, "subject": subject or "",
            "complained": set(), "tags": set(), "days": set(), "message_id": None, "message_ids": set(),
            "openers": set(), "clickers": set(), "open_events": 0, "click_events": 0,
        })
        if display and not g["from_display_name"]:
            g["from_display_name"] = display
        return key, g

    for event in ("delivered", "failed", "complained"):
        for item in _fetch_event_pages(mailgun_domain, api_key, event, begin):
            headers = (item.get("message") or {}).get("headers") or {}
            from_raw, subject = headers.get("from"), headers.get("subject")
            if not from_raw:
                continue
            key, g = group_for(from_raw, subject)
            recipient = item.get("recipient", "")
            g["tags"].update(item.get("tags") or [])
            msg_id = headers.get("message-id")
            if msg_id:
                msgid_to_key[msg_id] = key
                g["message_ids"].add(msg_id)
                if not g["message_id"]:
                    g["message_id"] = msg_id
            ts = item.get("timestamp")
            if ts:
                g["days"].add(datetime.datetime.utcfromtimestamp(ts).date().isoformat())

            if event == "complained":
                g["complained"].add(recipient)
                continue
            triple = (key, recipient, msg_id)
            if ts:
                first_attempt[triple] = min(first_attempt.get(triple, ts), ts)
            ds = item.get("delivery-status") or {}
            if ds.get("mx-host"):
                mx_hosts[triple] = ds["mx-host"]
            if event == "failed" and item.get("severity") != "permanent":
                # A temporary failure says nothing final -- Mailgun retries, and
                # a later delivered/failed event for this pair settles it. Its
                # reason text is kept, though: it's the only record of WHY.
                d = deferred.setdefault(triple, {"attempts": 0, "first_ts": ts, "last_ts": ts})
                d["attempts"] += 1
                d["first_ts"] = min(d["first_ts"] or ts, ts or d["first_ts"])
                d["last_ts"] = max(d["last_ts"] or ts, ts or d["last_ts"])
                d["reason"] = ds.get("message") or ds.get("description") or item.get("reason") or ""
                d["enhanced_code"] = ds.get("enhanced-code")
                d["mx_host"] = ds.get("mx-host")
                continue
            if event == "delivered" and ts and triple not in delivered_ts:
                delivered_ts[triple] = ts
            outcomes[triple] = "delivered" if event == "delivered" else "bounced"

    # Collapse per-message outcomes to per-recipient: someone who got any copy
    # of the send counts as delivered, otherwise a permanent failure is a bounce.
    per_recipient = {}
    for (key, recipient, _msg_id), outcome in outcomes.items():
        current = per_recipient.get((key, recipient))
        if current != "delivered":
            per_recipient[(key, recipient)] = outcome
    delivered_by_key = collections.defaultdict(set)
    bounced_by_key = collections.defaultdict(set)
    for (key, recipient), outcome in per_recipient.items():
        (delivered_by_key if outcome == "delivered" else bounced_by_key)[key].add(recipient)

    # Open/click events carry no subject -- attribute them via message-id.
    for event, uniq_field, count_field in (("opened", "openers", "open_events"),
                                            ("clicked", "clickers", "click_events")):
        for item in _fetch_event_pages(mailgun_domain, api_key, event, begin):
            msg_id = ((item.get("message") or {}).get("headers") or {}).get("message-id")
            key = msgid_to_key.get(msg_id)
            if not key:
                continue
            g = groups[key]
            g[count_field] += 1
            g[uniq_field].add(item.get("recipient", ""))

    domain_has_opens = any(g["open_events"] for g in groups.values())
    domain_has_clicks = any(g["click_events"] for g in groups.values())

    triples_by_key = collections.defaultdict(list)
    for triple in first_attempt:
        triples_by_key[triple[0]].append(triple)

    campaigns = []
    deferrals = []
    for key, g in groups.items():
        addr, subject = key
        delivered = len(delivered_by_key[key])
        msgids = max(len(g["message_ids"]), 1)
        recipients_per_message = delivered / msgids
        is_newsletter = bool(g["tags"] & NEWSLETTER_TAGS) or (
            delivered >= min_recipients and recipients_per_message >= BROADCAST_RATIO
        )
        for triple in triples_by_key[key]:
            d = deferred.get(triple)
            if not d:
                continue
            outcome = outcomes.get(triple)
            deferrals.append({
                "message_id": triple[2], "recipient": triple[1], "from_address": addr, "subject": subject,
                "is_newsletter": is_newsletter, "mx_host": d["mx_host"] or mx_hosts.get(triple),
                "reason": d["reason"], "enhanced_code": d["enhanced_code"], "attempts": d["attempts"],
                "first_ts": d["first_ts"], "last_ts": d["last_ts"],
                "outcome": {"delivered": "delivered", "bounced": "failed"}.get(outcome, "pending"),
                "delivered_ts": delivered_ts.get(triple) if outcome == "delivered" else None,
            })
        if not is_newsletter:
            continue
        starts = sorted(first_attempt[t] for t in triples_by_key[key])
        google = {t[1] for t in triples_by_key[key] if "google" in (mx_hosts.get(t) or "").lower()}
        campaigns.append({
            "mailgun_domain": mailgun_domain,
            "from_address": addr,
            "from_display_name": g["from_display_name"],
            "subject": subject,
            "message_id": g["message_id"],
            "tags": ",".join(sorted(g["tags"])) or None,
            "send_day": min(g["days"]) if g["days"] else None,
            "delivered": delivered,
            "bounced": len(bounced_by_key[key]),
            "complained": len(g["complained"]),
            "unique_openers": len(g["openers"]),
            "unique_clickers": len(g["clickers"]),
            "open_events": g["open_events"],
            "click_events": g["click_events"],
            # Whether this Mailgun domain has open/click tracking on at all,
            # judged from whether ANY campaign in the window produced such
            # events. Without this, a domain with click tracking switched off
            # is indistinguishable from one where nobody clicked -- and
            # scoring the latter's zero against a benchmark would invent a
            # failing grade out of missing instrumentation.
            "open_tracking": domain_has_opens,
            "click_tracking": domain_has_clicks,
            # Mailgun's current domain setting -- explanatory only, never used
            # to decide scorability. See fetch_tracking_settings().
            "tracking_open_setting": tracking_open,
            "tracking_click_setting": tracking_click,
            "google_recipients": len(google),
            # First attempt to the 95th-percentile first attempt: how fast the
            # list was released. Retries are excluded (only each message's
            # FIRST attempt counts) and the slowest 5% trimmed, so a few late
            # stragglers don't make a burst look spread out.
            "send_span_seconds": (round(starts[int(0.95 * (len(starts) - 1))] - starts[0])
                                  if starts else None),
        })
    campaigns.sort(key=lambda c: (c["send_day"] or "", c["delivered"]), reverse=True)
    return campaigns, deferrals, None


def _stale(conn, mailgun_domain, recheck_hours):
    """True if this Mailgun domain hasn't been scanned for newsletters within
    `recheck_hours`, per mailgun_campaign_scans."""
    row = conn.execute(
        "SELECT scanned_at AS last FROM mailgun_campaign_scans WHERE mailgun_domain=?",
        (mailgun_domain,),
    ).fetchone()
    if not row or not row["last"]:
        return True
    last = datetime.datetime.strptime(row["last"], "%Y-%m-%d %H:%M:%S")
    return last < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def run_mailgun_campaign_sync(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    api_key = get_secret("MAILGUN_API_KEY")
    if not api_key:
        if verbose:
            print("[mailgun-campaigns] no MAILGUN_API_KEY in secrets.env -- skipping")
        return

    window_days = int(settings["mailgun_newsletter_window_days"])
    min_recipients = int(settings["mailgun_newsletter_min_recipients"])
    recheck_hours = int(settings["mailgun_recheck_hours"])
    throttle_lookback_days = int(settings["mailgun_throttle_lookback_days"])

    mg_domains, err = list_mailgun_domains(api_key)
    if err:
        if verbose:
            print(f"[mailgun-campaigns] could not list domains: {err}")
        return
    matches = match_tracked_domains(conn, mg_domains)

    total = 0
    for mailgun_domain, (domain_id, domain_name) in matches.items():
        # Rate-limit per domain, matching how mailgun.py gates its own checks.
        # Without this, every call re-paginated five event types across every
        # matched domain -- ~110s of pure API waiting -- which is fine for a
        # 6-hourly background job but made the interactive "Refresh now"
        # button block for over two minutes on every press.
        if not _stale(conn, mailgun_domain, recheck_hours):
            continue
        t_open, t_click, web_scheme = fetch_tracking_settings(mailgun_domain, api_key)
        if web_scheme is not None:
            conn.execute(
                """INSERT INTO mailgun_domain_settings
                   (mailgun_domain, domain_id, web_scheme, open_tracking, click_tracking)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(mailgun_domain) DO UPDATE SET domain_id=excluded.domain_id,
                     web_scheme=excluded.web_scheme, open_tracking=excluded.open_tracking,
                     click_tracking=excluded.click_tracking, checked_at=datetime('now')""",
                (mailgun_domain, domain_id, web_scheme,
                 None if t_open is None else int(t_open), None if t_click is None else int(t_click)),
            )
        campaigns, deferrals, cerr = fetch_campaigns(mailgun_domain, api_key, window_days, min_recipients,
                                                      tracking_open=t_open, tracking_click=t_click)
        if cerr:
            if verbose:
                print(f"[mailgun-campaigns] {mailgun_domain}: {cerr}")
            continue
        for c in campaigns:
            conn.execute(
                """INSERT INTO mailgun_campaigns
                   (domain_id, mailgun_domain, from_address, from_display_name, subject, message_id, tags,
                    send_day, delivered, bounced, complained, unique_openers, unique_clickers,
                    open_events, click_events, open_tracking, click_tracking,
                    tracking_open_setting, tracking_click_setting, google_recipients, send_span_seconds)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(mailgun_domain, from_address, subject, send_day) DO UPDATE SET
                     from_display_name=excluded.from_display_name, message_id=excluded.message_id,
                     tags=excluded.tags, delivered=excluded.delivered, bounced=excluded.bounced,
                     complained=excluded.complained, unique_openers=excluded.unique_openers,
                     unique_clickers=excluded.unique_clickers, open_events=excluded.open_events,
                     click_events=excluded.click_events, open_tracking=excluded.open_tracking,
                     click_tracking=excluded.click_tracking,
                     tracking_open_setting=excluded.tracking_open_setting,
                     tracking_click_setting=excluded.tracking_click_setting,
                     google_recipients=excluded.google_recipients,
                     send_span_seconds=COALESCE(excluded.send_span_seconds, send_span_seconds),
                     updated_at=datetime('now')""",
                (domain_id, mailgun_domain, c["from_address"], c["from_display_name"], c["subject"],
                 c["message_id"], c["tags"], c["send_day"], c["delivered"], c["bounced"], c["complained"],
                 c["unique_openers"], c["unique_clickers"], c["open_events"], c["click_events"],
                 int(c["open_tracking"]), int(c["click_tracking"]),
                 None if c["tracking_open_setting"] is None else int(c["tracking_open_setting"]),
                 None if c["tracking_click_setting"] is None else int(c["tracking_click_setting"]),
                 c["google_recipients"], c["send_span_seconds"]),
            )
            total += 1
        record_deferrals(conn, domain_id, mailgun_domain, deferrals)
        update_throttle_action_items(conn, domain_id, domain_name, mailgun_domain, throttle_lookback_days)
        conn.execute(
            """INSERT INTO mailgun_campaign_scans (mailgun_domain, scanned_at) VALUES (?, datetime('now'))
               ON CONFLICT(mailgun_domain) DO UPDATE SET scanned_at=datetime('now')""",
            (mailgun_domain,),
        )
        # Commit per domain rather than once at the end: the loop makes several
        # paginated Mailgun API calls per domain, and holding one write
        # transaction open across all of that network latency is what blocked
        # other writers long enough to matter.
        conn.commit()
        if verbose and campaigns:
            print(f"[mailgun-campaigns] {mailgun_domain} ({domain_name}): {len(campaigns)} newsletter(s)")
    conn.commit()
    if verbose:
        print(f"[mailgun-campaigns] {total} newsletter campaign(s) recorded")


def main() -> None:
    from app.db import get_connection, init_db
    conn = get_connection()
    init_db(conn)
    run_mailgun_campaign_sync(conn)


if __name__ == "__main__":
    main()
