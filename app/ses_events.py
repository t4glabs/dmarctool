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
import json

import boto3
import botocore.exceptions

from app.analysis import ensure_default_settings, upsert_system_action
from app.config import get_secret
from app.db import get_connection, init_db

EVENT_TO_COUNTER = {"bounce": "bounced", "complaint": "complained", "delivery": "delivered"}
CAMPAIGN_EVENT_TO_COUNTER = {
    "bounce": "bounced", "complaint": "complained", "delivery": "delivered",
    "open": "opened", "click": "clicked",
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
    (plus the Subject, always present) -- SES echoes the original message
    headers back on every event notification, so this works for
    Open/Click/Bounce/Complaint/Delivery alike. Returns (campaign_id, subject),
    either of which may be None for non-campaign mail (transactional sends)."""
    headers = {h.get("name"): h.get("value") for h in mail.get("headers", [])}
    campaign_id = headers.get("X-Listmonk-Campaign")
    subject = (mail.get("commonHeaders", {}) or {}).get("subject")
    return campaign_id, subject


def parse_event(raw_body: str):
    """Returns (kind, configuration_set, recipients, day, campaign_id, subject)
    for a real SES event, or (None, None, None, None, None, None) for anything
    else (e.g. SNS's "Successfully validated SNS topic..." confirmation, which
    isn't JSON and isn't a send event). `day` is the event's own timestamp
    (when it actually happened), not the date this function happens to run --
    a backlog drained days or weeks late must still land on the day the mail
    was actually sent/bounced/complained about, not the day the queue got
    drained. `campaign_id`/`subject` are only populated for mail sent through
    Listmonk; plain transactional SES sends have neither."""
    envelope = json.loads(raw_body)
    inner_raw = envelope.get("Message", "")
    try:
        event = json.loads(inner_raw)
    except json.JSONDecodeError:
        return None, None, None, None, None, None

    event_type = event.get("eventType")
    mail = event.get("mail", {})
    config_set = (mail.get("tags", {}) or {}).get("ses:configuration-set", [None])[0]
    mail_day = _event_day(mail.get("timestamp"))
    campaign_id, subject = _campaign_info(mail)

    if event_type == "Bounce":
        bounce = event.get("bounce", {})
        recipients = [
            {"email": r["emailAddress"], "reason": r.get("diagnosticCode"), "bounce_type": bounce.get("bounceType")}
            for r in bounce.get("bouncedRecipients", [])
        ]
        day = _event_day(bounce.get("timestamp")) or mail_day
        return "bounce", config_set, recipients, day, campaign_id, subject
    if event_type == "Complaint":
        complaint = event.get("complaint", {})
        recipients = [
            {"email": r["emailAddress"], "reason": complaint.get("complaintFeedbackType")}
            for r in complaint.get("complainedRecipients", [])
        ]
        day = _event_day(complaint.get("timestamp")) or mail_day
        return "complaint", config_set, recipients, day, campaign_id, subject
    if event_type == "Delivery":
        delivery = event.get("delivery", {})
        day = _event_day(delivery.get("timestamp")) or mail_day
        return "delivery", config_set, [{"email": r} for r in delivery.get("recipients", [])], day, campaign_id, subject
    if event_type == "Open":
        day = _event_day(event.get("open", {}).get("timestamp")) or mail_day
        return "open", config_set, [{"email": r} for r in mail.get("destination", [])], day, campaign_id, subject
    if event_type == "Click":
        day = _event_day(event.get("click", {}).get("timestamp")) or mail_day
        return "click", config_set, [{"email": r} for r in mail.get("destination", [])], day, campaign_id, subject
    return None, config_set, None, None, None, None


def _upsert_campaign_event(conn, domain_id, config_set, campaign_id, subject, day, kind, n):
    """counter comes from CAMPAIGN_EVENT_TO_COUNTER, a fixed internal dict --
    not attacker-controlled, so building the column name into the SQL is safe."""
    counter = CAMPAIGN_EVENT_TO_COUNTER[kind]
    conn.execute(
        f"""INSERT INTO ses_campaigns (domain_id, configuration_set, campaign_id, subject, send_day, {counter})
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(configuration_set, campaign_id) DO UPDATE SET
              {counter}={counter}+excluded.{counter},
              subject=COALESCE(ses_campaigns.subject, excluded.subject),
              send_day=MIN(ses_campaigns.send_day, excluded.send_day),
              updated_at=datetime('now')""",
        (domain_id, config_set, campaign_id, subject, day, n),
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
    counts = {}  # (domain_id, config_set, day) -> {"delivered": n, "bounced": n, "complained": n}
    new_suppressions = {}  # (domain_id, config_set) -> {"bounce": n, "complaint": n}
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
                kind, config_set, recipients, day, campaign_id, subject = parse_event(msg["Body"])
            except (json.JSONDecodeError, KeyError):
                kind, config_set, recipients, day, campaign_id, subject = None, None, None, None, None, None

            match = domain_map.get(config_set) if config_set else None
            if kind and match:
                domain_id, domain_name = match
                event_day = day or today

                if kind in EVENT_TO_COUNTER:
                    count_key = (domain_id, config_set, event_day)
                    counts.setdefault(count_key, {"delivered": 0, "bounced": 0, "complained": 0})
                    counts[count_key][EVENT_TO_COUNTER[kind]] += len(recipients)

                if campaign_id:
                    _upsert_campaign_event(conn, domain_id, config_set, campaign_id, subject, event_day, kind, len(recipients))

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
            """INSERT INTO ses_event_counts (domain_id, configuration_set, day, delivered, bounced, complained)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(configuration_set, day) DO UPDATE SET
                 delivered=delivered+excluded.delivered, bounced=bounced+excluded.bounced,
                 complained=complained+excluded.complained""",
            (domain_id, config_set, day, c["delivered"], c["bounced"], c["complained"]),
        )
        if verbose:
            domain_name = next(n for cs, (did, n) in domain_map.items() if did == domain_id and cs == config_set)
            print(f"[ses] {config_set} ({domain_name}) {day}: +{c['delivered']} delivered, "
                  f"+{c['bounced']} bounced, +{c['complained']} complained")

    window_days = int(settings_now["ses_stats_window_days"])
    bounce_warn = float(settings_now["ses_bounce_rate_warn"])
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
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='ses_reputation' AND ref_key=? AND status='open'""",
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
