"""
Amazon SES bounce/complaint/delivery event ingestion.

SES has no per-domain read API for stats or reputation -- account-wide
GetSendStatistics/GetAccount/suppression-list calls blend every identity
together. The only way to get numbers separated per domain is to give each
domain its own configuration set (done in the AWS console) and have each one
publish Bounce/Complaint/Delivery events to a shared SNS topic, which fans out
to one SQS queue that this module polls and drains.

Matching a configuration set back to a tracked domain relies on a naming
convention agreed with the user: the configuration set name is the domain with
every "." replaced by "-" (pattic.org -> pattic-org, aikyam.school ->
aikyam-school). Any future domain's configuration set must follow the same
convention to be picked up automatically -- there's no API to ask SES which
domain a configuration set is "for".

Needs AWS_SES_ACCESS_KEY_ID / AWS_SES_SECRET_ACCESS_KEY / AWS_SES_REGION /
SES_EVENTS_QUEUE_URL in secrets.env. If any are missing, this is a no-op.
"""

import argparse
import datetime
import email.utils
import json

import boto3
import botocore.exceptions

from app.analysis import ensure_default_settings, upsert_system_action
from app.config import get_secret
from app.db import get_connection, init_db

EVENT_TO_COUNTER = {"bounce": "bounced", "complaint": "complained", "delivery": "delivered", "reject": "rejected"}
CAMPAIGN_EVENT_TO_COUNTER = {
    "bounce": "bounced", "complaint": "complained", "delivery": "delivered",
    "open": "opened", "click": "clicked", "reject": "rejected",
}


def _client_and_queue():
    access_key = get_secret("AWS_SES_ACCESS_KEY_ID")
    secret_key = get_secret("AWS_SES_SECRET_ACCESS_KEY")
    region = get_secret("AWS_SES_REGION")
    queue_url = get_secret("SES_EVENTS_QUEUE_URL")
    if not (access_key and secret_key and region and queue_url):
        return None, None
    client = boto3.client(
        "sqs", aws_access_key_id=access_key, aws_secret_access_key=secret_key, region_name=region,
    )
    return client, queue_url


def _config_set_domain_map(conn):
    """{configuration_set_name: (domain_id, domain_name)} assuming the
    domain-with-dots-as-hyphens naming convention."""
    return {
        row["name"].replace(".", "-"): (row["id"], row["name"])
        for row in conn.execute("SELECT id, name FROM domains")
    }


def _event_day(timestamp: str):
    """SES timestamps are ISO8601 UTC, e.g. '2026-08-07T12:34:56.789Z'. Returns
    the date portion, or None if missing/unparseable."""
    if not timestamp:
        return None
    try:
        return datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _campaign_info(mail):
    """Listmonk stamps every campaign email with an X-Listmonk-Campaign header
    (plus the Subject and From, always present) -- SES echoes the original
    message headers back on every event notification, so this works for
    Open/Click/Bounce/Complaint/Delivery alike. Returns a dict; campaign_id is
    None for non-campaign mail (plain transactional SES sends). from_display_name
    is the human-readable sender name (e.g. "PATTIC" from "PATTIC
    <hello@pattic.org>") -- not something DMARC reports carry at all, only
    available now via these raw SES event headers."""
    headers = {h.get("name"): h.get("value") for h in mail.get("headers", [])}
    from_display_name, from_address = None, None
    from_header = (mail.get("commonHeaders", {}) or {}).get("from")
    if from_header:
        from_display_name, from_address = email.utils.parseaddr(from_header[0])
        from_display_name = from_display_name or None  # parseaddr gives "" when there's no name part
    return {
        "campaign_id": headers.get("X-Listmonk-Campaign"),
        "subject": (mail.get("commonHeaders", {}) or {}).get("subject"),
        "from_display_name": from_display_name,
        "from_address": from_address,
        "message_id": headers.get("Message-Id") or headers.get("Message-ID"),
        "list_unsubscribe": headers.get("List-Unsubscribe"),
        "list_unsubscribe_post": headers.get("List-Unsubscribe-Post"),
    }


def parse_event(raw_body: str):
    """Returns (kind, configuration_set, recipients, day, meta) for a real SES
    event, or (None, None, None, None, None) for anything else (e.g. SNS's
    "Successfully validated SNS topic..." confirmation, which isn't JSON and
    isn't a send event). `day` is the event's own timestamp (when it actually
    happened), not the date this function happens to run -- a backlog drained
    days or weeks late must still land on the day the mail was actually
    sent/bounced/complained about, not the day the queue got drained. `meta`
    is the dict from _campaign_info (campaign_id/subject/from_display_name/
    from_address), same for every event type since it's read from the same
    underlying mail headers."""
    envelope = json.loads(raw_body)
    inner_raw = envelope.get("Message", "")
    try:
        event = json.loads(inner_raw)
    except json.JSONDecodeError:
        return None, None, None, None, None

    event_type = event.get("eventType")
    mail = event.get("mail", {})
    config_set = (mail.get("tags", {}) or {}).get("ses:configuration-set", [None])[0]
    mail_day = _event_day(mail.get("timestamp"))
    meta = _campaign_info(mail)

    if event_type == "Bounce":
        bounce = event.get("bounce", {})
        recipients = [
            {"email": r["emailAddress"], "reason": r.get("diagnosticCode"), "bounce_type": bounce.get("bounceType")}
            for r in bounce.get("bouncedRecipients", [])
        ]
        day = _event_day(bounce.get("timestamp")) or mail_day
        return "bounce", config_set, recipients, day, meta
    if event_type == "Complaint":
        complaint = event.get("complaint", {})
        recipients = [
            {"email": r["emailAddress"], "reason": complaint.get("complaintFeedbackType")}
            for r in complaint.get("complainedRecipients", [])
        ]
        day = _event_day(complaint.get("timestamp")) or mail_day
        return "complaint", config_set, recipients, day, meta
    if event_type == "Delivery":
        delivery = event.get("delivery", {})
        day = _event_day(delivery.get("timestamp")) or mail_day
        return "delivery", config_set, [{"email": r} for r in delivery.get("recipients", [])], day, meta
    if event_type == "Open":
        open_ = event.get("open", {})
        day = _event_day(open_.get("timestamp")) or mail_day
        recipients = [
            {"email": r, "ip_address": open_.get("ipAddress"), "user_agent": open_.get("userAgent"),
             "opened_at": open_.get("timestamp")}
            for r in mail.get("destination", [])
        ]
        return "open", config_set, recipients, day, meta
    if event_type == "Click":
        click = event.get("click", {})
        day = _event_day(click.get("timestamp")) or mail_day
        recipients = [
            {"email": r, "ip_address": click.get("ipAddress"), "user_agent": click.get("userAgent"),
             "link": click.get("link"), "clicked_at": click.get("timestamp")}
            for r in mail.get("destination", [])
        ]
        return "click", config_set, recipients, day, meta
    if event_type == "Reject":
        # SES refused to even attempt sending -- a pre-send reputation/content
        # filter, not a real bounce from the recipient's server. No per-event
        # timestamp of its own; mail.destination is the intended recipient list.
        reason = event.get("reject", {}).get("reason")
        return "reject", config_set, [{"email": r, "reason": reason} for r in mail.get("destination", [])], mail_day, meta
    return None, config_set, None, None, None


def _upsert_campaign_event(conn, domain_id, config_set, campaign_id, meta, day, kind, n):
    """counter comes from CAMPAIGN_EVENT_TO_COUNTER, a fixed internal dict --
    not attacker-controlled, so building the column name into the SQL is safe."""
    counter = CAMPAIGN_EVENT_TO_COUNTER[kind]
    conn.execute(
        f"""INSERT INTO ses_campaigns
               (domain_id, configuration_set, campaign_id, subject, from_display_name, from_address,
                message_id, list_unsubscribe, list_unsubscribe_post, send_day, {counter})
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(configuration_set, campaign_id) DO UPDATE SET
              {counter}={counter}+excluded.{counter},
              -- Prefer the newest non-null value, not the first-ever one: a
              -- Listmonk test send and the real campaign send share the same
              -- campaign_id, and the test (usually processed first, to a
              -- single recipient) must not permanently pin these fields if the
              -- subject/from/headers changed before the real send went out.
              subject=COALESCE(excluded.subject, ses_campaigns.subject),
              from_display_name=COALESCE(excluded.from_display_name, ses_campaigns.from_display_name),
              from_address=COALESCE(excluded.from_address, ses_campaigns.from_address),
              message_id=COALESCE(excluded.message_id, ses_campaigns.message_id),
              list_unsubscribe=COALESCE(excluded.list_unsubscribe, ses_campaigns.list_unsubscribe),
              list_unsubscribe_post=COALESCE(excluded.list_unsubscribe_post, ses_campaigns.list_unsubscribe_post),
              send_day=MIN(ses_campaigns.send_day, excluded.send_day),
              updated_at=datetime('now')""",
        (domain_id, config_set, campaign_id, meta["subject"], meta["from_display_name"], meta["from_address"],
         meta["message_id"], meta["list_unsubscribe"], meta["list_unsubscribe_post"], day, n),
    )


def _log_campaign_clicks(conn, domain_id, config_set, campaign_id, recipients):
    """Raw per-click log (see ses_campaign_clicks in schema.sql) -- lets
    app.click_quality tell a genuine subscriber click apart from a security
    gateway auto-visiting every link, which ses_campaign_recipients' single
    clicked=0/1 flag per recipient can't do on its own."""
    for r in recipients:
        conn.execute(
            """INSERT INTO ses_campaign_clicks
               (domain_id, configuration_set, campaign_id, email, clicked_at, ip_address, user_agent, link)
               VALUES (?,?,?,?,?,?,?,?)""",
            (domain_id, config_set, campaign_id, r["email"],
             r.get("clicked_at") or datetime.datetime.utcnow().isoformat(),
             r.get("ip_address"), r.get("user_agent"), r.get("link")),
        )


def _log_campaign_opens(conn, domain_id, config_set, campaign_id, recipients):
    """Raw per-open log (see ses_campaign_opens in schema.sql) -- lets
    app.open_quality flag opens that are an automated image pre-fetch (e.g.
    Gmail's own image proxy) rather than a person actually reading the
    message, which ses_campaign_recipients' single opened=0/1 flag per
    recipient can't do on its own."""
    for r in recipients:
        conn.execute(
            """INSERT INTO ses_campaign_opens
               (domain_id, configuration_set, campaign_id, email, opened_at, ip_address, user_agent)
               VALUES (?,?,?,?,?,?,?)""",
            (domain_id, config_set, campaign_id, r["email"],
             r.get("opened_at") or datetime.datetime.utcnow().isoformat(),
             r.get("ip_address"), r.get("user_agent")),
        )


RECIPIENT_COLUMN = {"delivery": "delivered", "open": "opened", "click": "clicked"}


def _upsert_campaign_recipients(conn, domain_id, config_set, campaign_id, kind, recipients):
    """Per-recipient-per-campaign record, needed to tell 'the same 5 people
    opened every campaign' apart from '5 different people opened one each' --
    a per-campaign total alone can't do that. Only called for
    delivery/open/click; bounce has its own function below since it also
    needs to store the raw diagnostic text, not just flip a flag."""
    column = RECIPIENT_COLUMN[kind]
    for r in recipients:
        conn.execute(
            f"""INSERT INTO ses_campaign_recipients (domain_id, configuration_set, campaign_id, email, {column})
                VALUES (?,?,?,?,1)
                ON CONFLICT(configuration_set, campaign_id, email) DO UPDATE SET
                  {column}=1, last_seen_at=datetime('now')""",
            (domain_id, config_set, campaign_id, r["email"]),
        )


def _upsert_campaign_recipient_bounces(conn, domain_id, config_set, campaign_id, recipients):
    """Records which specific newsletter caused which specific bounce, with
    the raw diagnostic text kept for app.bounce_reasons to categorize on
    display -- turns 'this campaign had 40 bounces' into 'this campaign had
    30 no-such-user, 10 mailbox-full', not just a domain-wide aggregate."""
    for r in recipients:
        conn.execute(
            """INSERT INTO ses_campaign_recipients
               (domain_id, configuration_set, campaign_id, email, bounced, bounce_reason)
               VALUES (?,?,?,?,1,?)
               ON CONFLICT(configuration_set, campaign_id, email) DO UPDATE SET
                 bounced=1, bounce_reason=excluded.bounce_reason, last_seen_at=datetime('now')""",
            (domain_id, config_set, campaign_id, r["email"], r.get("reason")),
        )


def run_ses_event_ingest(conn, verbose: bool = True) -> None:
    settings_now = ensure_default_settings(conn)
    sqs, queue_url = _client_and_queue()
    if not sqs:
        if verbose:
            print("[ses] missing AWS_SES_* credentials or SES_EVENTS_QUEUE_URL -- skipping")
        return

    max_messages = int(settings_now["ses_max_messages_per_run"])

    domain_map = _config_set_domain_map(conn)
    today = datetime.date.today().isoformat()
    counts = {}  # (domain_id, config_set, day) -> {"delivered": n, "bounced": n, "complained": n, "rejected": n}
    new_suppressions = {}  # (domain_id, config_set) -> {"bounce": n, "complaint": n}
    touched_campaigns = set()  # (domain_id, config_set, campaign_id)
    processed = 0

    # SES publishes a Delivery event for every successfully sent message, not just
    # bounces/complaints -- at real bulk-sending volume this queue can carry tens of
    # thousands of backlogged messages. Deleting one at a time (the original version
    # of this loop) meant one SQS API round-trip per message, which is what caused a
    # ~24,000-message backlog to hang a single check for many minutes. Batch deletes
    # (up to 10 per call, matching the receive batch) plus a per-run cap keep this
    # bounded -- a large backlog drains over several runs instead of blocking one.
    while processed < max_messages:
        try:
            resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
        except botocore.exceptions.ClientError as e:
            if verbose:
                print(f"[ses] SQS receive failed: {e}")
            break
        messages = resp.get("Messages", [])
        if not messages:
            break

        for msg in messages:
            try:
                kind, config_set, recipients, day, meta = parse_event(msg["Body"])
            except (json.JSONDecodeError, KeyError):
                kind, config_set, recipients, day, meta = None, None, None, None, None

            match = domain_map.get(config_set) if config_set else None
            if kind and match:
                domain_id, domain_name = match
                event_day = day or today
                campaign_id = meta["campaign_id"]

                if kind in EVENT_TO_COUNTER:
                    count_key = (domain_id, config_set, event_day)
                    counts.setdefault(count_key, {"delivered": 0, "bounced": 0, "complained": 0, "rejected": 0})
                    counts[count_key][EVENT_TO_COUNTER[kind]] += len(recipients)

                if campaign_id:
                    touched_campaigns.add((domain_id, config_set, campaign_id))
                    _upsert_campaign_event(conn, domain_id, config_set, campaign_id, meta, event_day, kind, len(recipients))
                    if kind in RECIPIENT_COLUMN:
                        _upsert_campaign_recipients(conn, domain_id, config_set, campaign_id, kind, recipients)
                        if kind == "click":
                            _log_campaign_clicks(conn, domain_id, config_set, campaign_id, recipients)
                        elif kind == "open":
                            _log_campaign_opens(conn, domain_id, config_set, campaign_id, recipients)
                    elif kind == "bounce":
                        _upsert_campaign_recipient_bounces(conn, domain_id, config_set, campaign_id, recipients)

                if kind in ("bounce", "complaint"):
                    supp_key = (domain_id, config_set)
                    for r in recipients:
                        existing = conn.execute(
                            "SELECT 1 FROM ses_suppressions WHERE configuration_set=? AND email=? AND kind=?",
                            (config_set, r["email"], kind),
                        ).fetchone()
                        conn.execute(
                            """INSERT INTO ses_suppressions
                               (domain_id, configuration_set, email, kind, bounce_type, reason)
                               VALUES (?,?,?,?,?,?)
                               ON CONFLICT(configuration_set, email, kind) DO UPDATE SET
                                 reason=excluded.reason, bounce_type=excluded.bounce_type,
                                 last_seen_at=datetime('now')""",
                            (domain_id, config_set, r["email"], kind, r.get("bounce_type"), r.get("reason")),
                        )
                        if not existing:
                            new_suppressions.setdefault(supp_key, {"bounce": 0, "complaint": 0})
                            new_suppressions[supp_key][kind] += 1

        # Delete regardless of whether each message was recognized -- validation
        # confirmations and anything from an unmapped configuration set shouldn't
        # pile up forever either. One batch call per receive, not one call per message.
        try:
            sqs.delete_message_batch(
                QueueUrl=queue_url,
                Entries=[{"Id": str(i), "ReceiptHandle": m["ReceiptHandle"]} for i, m in enumerate(messages)],
            )
        except botocore.exceptions.ClientError as e:
            if verbose:
                print(f"[ses] batch delete failed: {e}")
        processed += len(messages)
        if verbose and processed % 500 < 10:
            print(f"[ses] ...{processed} processed so far this run")

    for (domain_id, config_set, day), c in counts.items():
        conn.execute(
            """INSERT INTO ses_event_counts (domain_id, configuration_set, day, delivered, bounced, complained, rejected)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(configuration_set, day) DO UPDATE SET
                 delivered=delivered+excluded.delivered, bounced=bounced+excluded.bounced,
                 complained=complained+excluded.complained, rejected=rejected+excluded.rejected""",
            (domain_id, config_set, day, c["delivered"], c["bounced"], c["complained"], c["rejected"]),
        )
        if verbose:
            domain_name = next(n for cs, (did, n) in domain_map.items() if did == domain_id and cs == config_set)
            print(f"[ses] {config_set} ({domain_name}) {day}: +{c['delivered']} delivered, "
                  f"+{c['bounced']} bounced, +{c['complained']} complained")

    window_days = int(settings_now["ses_stats_window_days"])
    bounce_watch = float(settings_now["ses_bounce_rate_watch"])
    bounce_warn = float(settings_now["ses_bounce_rate_warn"])
    complaint_watch = float(settings_now["ses_complaint_rate_watch"])
    complaint_warn = float(settings_now["ses_complaint_rate_warn"])
    cutoff = (datetime.date.today() - datetime.timedelta(days=window_days)).isoformat()

    touched = {(domain_id, config_set) for domain_id, config_set, _ in counts} | set(new_suppressions)
    for domain_id, config_set in touched:
        domain_name = domain_map.get(config_set, (None, None))[1]
        row = conn.execute(
            """SELECT SUM(delivered) as delivered, SUM(bounced) as bounced, SUM(complained) as complained
               FROM ses_event_counts WHERE configuration_set=? AND day >= ?""",
            (config_set, cutoff),
        ).fetchone()
        delivered = row["delivered"] or 0
        bounce_rate = (row["bounced"] or 0) / delivered if delivered else 0.0
        complaint_rate = (row["complained"] or 0) / delivered if delivered else 0.0

        if delivered and (bounce_rate >= bounce_warn or complaint_rate >= complaint_warn):
            upsert_system_action(
                conn, domain_id, "ses_reputation", config_set,
                f"{domain_name}: SES bounce/complaint rate is elevated ({config_set})",
                f"{bounce_rate:.2%} bounced, {complaint_rate:.2%} complained over the last {window_days}d "
                f"({delivered} delivered). Check list quality before sending more.",
            )
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='ses_reputation_watch' AND ref_key=? AND status='open'""",
                (config_set,),
            )
        elif delivered and (bounce_rate >= bounce_watch or complaint_rate >= complaint_watch):
            # Softer, earlier tier -- matches the team's own documented two-tier
            # thresholds (e.g. bounce: watch at 2%, danger at 5%), which a single
            # cutoff at the danger line was missing entirely.
            upsert_system_action(
                conn, domain_id, "ses_reputation_watch", config_set,
                f"{domain_name}: SES bounce/complaint rate is trending up ({config_set})",
                f"{bounce_rate:.2%} bounced, {complaint_rate:.2%} complained over the last {window_days}d "
                f"({delivered} delivered). Not urgent yet, but worth watching before it crosses the danger line.",
            )
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='ses_reputation' AND ref_key=? AND status='open'""",
                (config_set,),
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category IN ('ses_reputation', 'ses_reputation_watch') AND ref_key=? AND status='open'""",
                (config_set,),
            )

        new = new_suppressions.get((domain_id, config_set), {"bounce": 0, "complaint": 0})
        if new["bounce"] or new["complaint"]:
            upsert_system_action(
                conn, domain_id, "ses_new_suppressions", config_set,
                f"{domain_name}: new SES suppressions ({config_set})",
                f"{new['bounce']} new bounce(s), {new['complaint']} new complaint(s) -- "
                f"worth pruning these addresses from Listmonk too.",
            )

        rejected_row = conn.execute(
            "SELECT SUM(rejected) as n FROM ses_event_counts WHERE configuration_set=? AND day >= ?",
            (config_set, cutoff),
        ).fetchone()
        rejected_n = rejected_row["n"] or 0
        if rejected_n:
            # SES refusing to even attempt sending is rare and more severe than a
            # bounce (which at least reached the recipient's server) -- worth
            # flagging on any occurrence, not just past a rate threshold.
            upsert_system_action(
                conn, domain_id, "ses_rejected", config_set,
                f"{domain_name}: SES refused to send {rejected_n} message(s) ({config_set})",
                f"{rejected_n} message(s) over the last {window_days}d were rejected by SES itself before "
                f"attempting delivery -- usually a content or reputation filter. This is more serious than a "
                f"normal bounce, which at least reached the recipient's server.",
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='ses_rejected' AND ref_key=? AND status='open'""",
                (config_set,),
            )

    if touched_campaigns:
        from app.analysis import sending_cadence
        from app.content_scoring import risk_level, score_text
        from app.display_name_checks import check_display_name, display_name_consistency
        from app.header_compliance import check_header_hygiene, check_unsubscribe_compliance

        for domain_id, config_set, campaign_id in touched_campaigns:
            row = conn.execute(
                """SELECT subject, from_display_name, message_id, list_unsubscribe, list_unsubscribe_post
                   FROM ses_campaigns WHERE configuration_set=? AND campaign_id=?""",
                (config_set, campaign_id),
            ).fetchone()
            if not row:
                continue

            name_issues = check_display_name(row["from_display_name"])
            if name_issues:
                upsert_system_action(
                    conn, domain_id, "display_name_issue", campaign_id,
                    f"\"{row['from_display_name']}\" may not follow Gmail's display-name guidelines "
                    f"(newsletter: {row['subject'] or campaign_id})",
                    " ".join(name_issues),
                )
            else:
                conn.execute(
                    """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                       WHERE category='display_name_issue' AND ref_key=? AND domain_id=? AND status='open'""",
                    (campaign_id, domain_id),
                )

            compliance_issues = (
                check_unsubscribe_compliance(row["list_unsubscribe"], row["list_unsubscribe_post"])
                + check_header_hygiene(row["message_id"], row["subject"])
            )
            if compliance_issues:
                upsert_system_action(
                    conn, domain_id, "campaign_compliance_issue", campaign_id,
                    f"Newsletter formatting issue (newsletter: {row['subject'] or campaign_id})",
                    " ".join(compliance_issues),
                )
            else:
                conn.execute(
                    """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                       WHERE category='campaign_compliance_issue' AND ref_key=? AND domain_id=? AND status='open'""",
                    (campaign_id, domain_id),
                )

            subject_result = score_text(row["subject"])
            if risk_level(subject_result["score"]) == "high":
                upsert_system_action(
                    conn, domain_id, "subject_spam_risk", campaign_id,
                    f"Subject line looks spam-trigger-heavy (newsletter: {row['subject']})",
                    " ".join(subject_result["flags"]),
                )
            else:
                conn.execute(
                    """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                       WHERE category='subject_spam_risk' AND ref_key=? AND domain_id=? AND status='open'""",
                    (campaign_id, domain_id),
                )

        for domain_id in {d for d, _, _ in touched_campaigns}:
            campaigns = conn.execute(
                "SELECT from_display_name FROM ses_campaigns WHERE domain_id=? ORDER BY send_day DESC",
                (domain_id,),
            ).fetchall()
            names = display_name_consistency([dict(c) for c in campaigns])
            if len(names) > 1:
                upsert_system_action(
                    conn, domain_id, "display_name_inconsistent", "consistency",
                    "Sender display name isn't consistent across newsletters",
                    f"Names seen: {', '.join(names)}.",
                )
            else:
                conn.execute(
                    """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                       WHERE category='display_name_inconsistent' AND domain_id=? AND status='open'""",
                    (domain_id,),
                )

            cadence = sending_cadence(conn, domain_id)
            if cadence["irregular"]:
                upsert_system_action(
                    conn, domain_id, "sending_cadence_irregular", "cadence",
                    "Newsletter sending cadence looks irregular",
                    f"Usual gap between sends is about {cadence['average_gap_days']} day(s), but the most recent "
                    f"gap was {cadence['latest_gap_days']} day(s). Gmail's guidance is to send at a consistent "
                    f"rate and avoid bursts or long silences followed by a sudden resumption.",
                )
            else:
                conn.execute(
                    """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                       WHERE category='sending_cadence_irregular' AND domain_id=? AND status='open'""",
                    (domain_id,),
                )

    conn.commit()
    if verbose:
        print(f"[ses] processed {processed} message(s) from the queue")


def main() -> None:
    parser = argparse.ArgumentParser(description="Drain the SES bounce/complaint/delivery SQS queue")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_ses_event_ingest(conn)


if __name__ == "__main__":
    main()
