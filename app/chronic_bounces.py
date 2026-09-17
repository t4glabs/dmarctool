"""
Chronic transient bounces -- addresses stuck "temporarily" failing forever.

A permanent bounce ("no such user") is unambiguous and already gets pruned by
hand from Mailgun/SES's own suppression exports. A TRANSIENT bounce (mailbox
full, greylisted, temporary server error) is supposed to be self-healing --
the next send should just go through. In practice, some addresses never
recover: the same inbox has been "temporarily" full for months, or a spam
filter has been "temporarily" rejecting every send since spring. Nothing else
in this tool ever notices, because nothing about a single transient bounce
looks wrong -- it's only wrong in aggregate, over time, which is exactly what
neither Mailgun's nor SES's own suppression list tracks (each treats every
transient bounce as independent and keeps retrying forever).

Surfaced from data already collected, no new integration:
  - SES: ses_suppressions (SES's own Permanent/Transient/Undetermined
    classification, authoritative) joined against ses_campaign_recipients
    (a real per-campaign bounce log, kept indefinitely) for an occurrence
    count across distinct sends.
  - Mailgun: mailgun_suppressions only -- there is no persistent per-event
    Mailgun bounce log (mailgun_identity_failures is a rolling few-day
    window, wiped and rebuilt every check), so only the date span since
    first_seen_at is available, not an occurrence count.

Deliberately excludes anything already categorised as a definitive permanent
failure (app.bounce_reasons.PERMANENT_CATEGORIES) -- this check exists
specifically for the gap in the operator's own stated workflow: "we only
delete permanent / user-not-exist, transient bounces are not that often
checked."
"""

import datetime

from app.analysis import all_domains, upsert_system_action
from app.bounce_reasons import PERMANENT_CATEGORIES, categorize_bounce

MAX_EXAMPLES = 5


def _chronic_watermark(conn, domain_id: int):
    """The last time the operator marked this domain's chronic-transient
    reminder done -- i.e. "I've reviewed and pruned this batch." Only usable
    as real acknowledgment memory for SES (see the SES-vs-Mailgun note on
    _mailgun_chronic below): SES's last_seen_at only moves when a genuinely
    NEW bounce/complaint EVENT arrives for that address, so an address that
    was actually removed from the sending list simply stops generating new
    events and its last_seen_at freezes at its pre-acknowledgment value --
    correctly dropping it from the list on the next check. One still
    actively bouncing (never removed) gets a fresh event and reappears,
    which is exactly the intended "keep nagging until truly fixed" behaviour."""
    return conn.execute(
        """SELECT MAX(resolved_at) m FROM action_items
           WHERE domain_id=? AND category='chronic_transient_bounce' AND status IN ('done','dismissed')""",
        (domain_id,),
    ).fetchone()["m"]


def _ses_chronic(conn, domain_id: int, min_occurrences: int, min_days: int, watermark):
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=min_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT s.email, s.first_seen_at, s.last_seen_at, s.reason, s.bounce_type,
                  (SELECT COUNT(DISTINCT scr.campaign_id) FROM ses_campaign_recipients scr
                     WHERE scr.domain_id = s.domain_id AND scr.email = s.email AND scr.bounced = 1) AS occurrences
           FROM ses_suppressions s
           WHERE s.domain_id = ? AND s.kind = 'bounce' AND s.bounce_type IN ('Transient', 'Undetermined')""",
        (domain_id,),
    ).fetchall()
    out = []
    for r in rows:
        occurrences = r["occurrences"] or 0
        chronic = occurrences >= min_occurrences or r["first_seen_at"] <= cutoff
        if not chronic:
            continue
        if watermark and r["last_seen_at"] <= watermark:
            continue  # acknowledged, and nothing genuinely new since -- treat as handled
        category = categorize_bounce(r["reason"], r["bounce_type"])
        if category in PERMANENT_CATEGORIES:
            continue  # SES's own classification says transient, but the text reads permanent -- let that path handle it
        out.append({
            "email": r["email"], "source": "SES", "occurrences": occurrences,
            "first_seen": r["first_seen_at"], "last_seen": r["last_seen_at"], "category": category,
        })
    return out


def _mailgun_chronic(conn, domain_id: int, min_days: int):
    """No acknowledgment memory here, unlike SES above -- a real data
    limitation, not an oversight. mailgun_suppressions.last_checked_at is
    touched on EVERY periodic sync for every address still on Mailgun's own
    suppression list, whether or not a fresh send was actually attempted
    (Mailgun's suppression list is a static roster it keeps forever, not an
    event stream) -- so it can't distinguish "still actively bouncing" from
    "pruned from Listmonk months ago but Mailgun still lists it". Marking the
    reminder done here just clears it for the current check; it reappears
    next cycle as a standing nudge rather than a one-time check-off. In
    practice this rarely matters: Mailgun contributes ~0 of most domains'
    chronic-transient list, SES dominates (confirmed: 220/220 on pattic.org)."""
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=min_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT email, first_seen_at, last_checked_at, reason
           FROM mailgun_suppressions
           WHERE domain_id = ? AND kind = 'bounce' AND first_seen_at <= ?""",
        (domain_id, cutoff),
    ).fetchall()
    out = []
    for r in rows:
        category = categorize_bounce(r["reason"], None)
        if category in PERMANENT_CATEGORIES:
            continue
        out.append({
            "email": r["email"], "source": "Mailgun", "occurrences": None,
            "first_seen": r["first_seen_at"], "last_seen": r["last_checked_at"], "category": category,
        })
    return out


def chronic_transient_bounces(conn, domain_id: int, min_occurrences: int, min_days: int):
    """Every address for this domain that's been failing "temporarily" for
    longer than a fresh recovery would take, or across enough distinct sends
    that it plainly isn't a one-off -- i.e. actually dead in practice, just
    never escalated to a permanent bounce by the ESP itself. Sorted worst
    (most persistent) first. Once you've marked the reminder done, an SES
    address only reappears if it's genuinely bounced again since -- if you
    actually pruned it, it stays cleared (see _chronic_watermark)."""
    watermark = _chronic_watermark(conn, domain_id)
    found = (_ses_chronic(conn, domain_id, min_occurrences, min_days, watermark)
             + _mailgun_chronic(conn, domain_id, min_days))
    found.sort(key=lambda x: x["first_seen"])
    return found


def run_chronic_bounce_checks(conn, verbose: bool = True) -> None:
    from app.analysis import ensure_default_settings
    settings = ensure_default_settings(conn)
    min_occurrences = int(settings["chronic_transient_min_occurrences"])
    min_days = int(settings["chronic_transient_min_days"])

    flagged = 0
    for d in all_domains(conn):
        found = chronic_transient_bounces(conn, d["id"], min_occurrences, min_days)
        if found:
            flagged += 1
            examples = "; ".join(f"{f['email']} ({f['category']}, first seen {f['first_seen'][:10]})"
                                  for f in found[:MAX_EXAMPLES])
            more = f", and {len(found) - MAX_EXAMPLES} more" if len(found) > MAX_EXAMPLES else ""
            upsert_system_action(
                conn, d["id"], "chronic_transient_bounce", None,
                f"{d['name']}: {len(found)} address(es) stuck 'temporarily' bouncing long-term",
                f"These have been failing with a temporary (not permanent) reason for {min_days}+ days, or across "
                f"{min_occurrences}+ separate sends, without ever succeeding or being formally suppressed as "
                f"permanent -- in practice they're dead, just never escalated by Mailgun/SES itself. "
                f"Examples: {examples}{more}. Download the suppressions CSV on this domain's page (now includes "
                f"these) and prune them from Listmonk/Ghost like a permanent bounce, then mark this done -- SES "
                f"addresses you've genuinely removed won't reappear (only ones still actively bouncing will); "
                f"Mailgun ones don't have that same memory and reappear each cycle as a standing reminder.",
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='chronic_transient_bounce' AND status='open'""",
                (d["id"],),
            )
    conn.commit()
    if verbose:
        print(f"[chronic_bounces] {flagged} domain(s) with chronic transient bounces "
              f"(>= {min_occurrences} sends or {min_days}+ days)")
