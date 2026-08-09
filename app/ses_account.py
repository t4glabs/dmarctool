"""
Amazon SES account-wide health and per-domain identity verification.

Separate from ses_events.py (which drains the SNS/SQS event queue) because
this hits the SES API directly (sesv2 GetAccount / ListEmailIdentities)
instead -- account-wide reputation/quota/suppression-settings aren't tied to
any one domain's event stream, and identity verification is a distinct
concept from DMARC/DNS compliance.

Needs the same AWS_SES_ACCESS_KEY_ID / AWS_SES_SECRET_ACCESS_KEY /
AWS_SES_REGION as ses_events.py. If any are missing, this is a no-op.
"""

import argparse
import datetime

import boto3
import botocore.exceptions

from app.analysis import ensure_default_settings, upsert_system_action
from app.config import get_secret
from app.db import get_connection, init_db
from app.ses_events import _config_set_domain_map


def _sesv2_client():
    access_key = get_secret("AWS_SES_ACCESS_KEY_ID")
    secret_key = get_secret("AWS_SES_SECRET_ACCESS_KEY")
    region = get_secret("AWS_SES_REGION")
    if not (access_key and secret_key and region):
        return None
    return boto3.client(
        "sesv2", aws_access_key_id=access_key, aws_secret_access_key=secret_key, region_name=region,
    )


def _stale(conn, table, recheck_hours):
    row = conn.execute(f"SELECT MAX(checked_at) as last_checked FROM {table}").fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def run_ses_account_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    client = _sesv2_client()
    if not client:
        if verbose:
            print("[ses-account] missing AWS_SES_* credentials -- skipping")
        return

    recheck_hours = int(settings["ses_account_recheck_hours"])
    domain_map = _config_set_domain_map(conn)  # {config_set: (domain_id, domain_name)} -- only SES-using domains
    ses_domains = {domain_name for _, domain_name in domain_map.values()}
    if not ses_domains:
        if verbose:
            print("[ses-account] no domains with an SES configuration set -- skipping")
        return

    if not _stale(conn, "ses_account_status", recheck_hours):
        if verbose:
            print("[ses-account] recently checked -- skipping")
        return

    try:
        account = client.get_account()
    except botocore.exceptions.ClientError as e:
        if verbose:
            print(f"[ses-account] get_account failed: {e}")
        return

    enforcement_status = account.get("EnforcementStatus")
    sending_enabled = bool(account.get("SendingEnabled"))
    quota = account.get("SendQuota", {})
    sent_last_24h = quota.get("SentLast24Hours")
    max_24h_send = quota.get("Max24HourSend")
    max_send_rate = quota.get("MaxSendRate")
    suppressed_reasons = set(account.get("SuppressionAttributes", {}).get("SuppressedReasons", []))
    suppress_bounce = "BOUNCE" in suppressed_reasons
    suppress_complaint = "COMPLAINT" in suppressed_reasons

    conn.execute(
        """INSERT INTO ses_account_status
           (enforcement_status, sending_enabled, sent_last_24h, max_24h_send, max_send_rate,
            suppress_bounce, suppress_complaint)
           VALUES (?,?,?,?,?,?,?)""",
        (enforcement_status, int(sending_enabled), sent_last_24h, max_24h_send, max_send_rate,
         int(suppress_bounce), int(suppress_complaint)),
    )

    # Account-wide issues aren't tied to one domain, but affect every domain
    # sending through this SES account -- surface the same action item under
    # each of them so it shows up wherever someone's actually looking.
    problems = []
    if enforcement_status and enforcement_status != "HEALTHY":
        problems.append(f"SES account enforcement status is '{enforcement_status}', not Healthy.")
    if not sending_enabled:
        problems.append("SES account-wide sending is currently disabled.")
    if not suppress_bounce or not suppress_complaint:
        missing = [r for r, on in (("bounces", suppress_bounce), ("complaints", suppress_complaint)) if not on]
        problems.append(f"Account-level auto-suppression is off for: {', '.join(missing)}.")

    for domain_id, domain_name in domain_map.values():
        if problems:
            upsert_system_action(
                conn, domain_id, "ses_account_health", "account",
                "SES account-wide issue (affects all domains sending via this SES account)",
                " ".join(problems),
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='ses_account_health' AND ref_key='account' AND domain_id=? AND status='open'""",
                (domain_id,),
            )

    try:
        idents = client.list_email_identities().get("EmailIdentities", [])
    except botocore.exceptions.ClientError as e:
        idents = []
        if verbose:
            print(f"[ses-account] list_email_identities failed: {e}")

    for ident in idents:
        name = ident.get("IdentityName")
        if ident.get("IdentityType") != "DOMAIN" or name not in ses_domains:
            continue
        domain_id = next(did for did, dname in domain_map.values() if dname == name)
        status = ident.get("VerificationStatus")
        sending_ok = bool(ident.get("SendingEnabled"))
        conn.execute(
            """INSERT INTO ses_identity_checks (domain_id, identity_name, verification_status, sending_enabled)
               VALUES (?,?,?,?)""",
            (domain_id, name, status, int(sending_ok)),
        )
        if status != "SUCCESS" or not sending_ok:
            upsert_system_action(
                conn, domain_id, "ses_identity_unverified", name,
                f"SES identity for {name} isn't fully verified",
                f"Verification status: {status or 'unknown'}, sending enabled: {sending_ok}. "
                f"Mail sent through this SES identity may be rejected or unauthenticated until this is fixed.",
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='ses_identity_unverified' AND ref_key=? AND domain_id=? AND status='open'""",
                (name, domain_id),
            )

    conn.commit()
    if verbose:
        print(f"[ses-account] enforcement={enforcement_status} sending_enabled={sending_enabled} "
              f"quota={sent_last_24h}/{max_24h_send} ({len(idents)} identities checked)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check SES account health and domain identity verification")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_ses_account_checks(conn)


if __name__ == "__main__":
    main()
