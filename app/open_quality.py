"""
Flags newsletter "opens" that look like an automated image pre-fetch rather
than a person actually reading the message -- the open-tracking counterpart
to app.click_quality. Uses the same raw per-event log approach (see
ses_campaign_opens in schema.sql, populated by app.ses_events), but the
available signals are weaker than for clicks:

  - a user-agent that names a known image-proxy/scanner (most reliably
    Gmail's own image proxy, "GoogleImageProxy", which openly identifies
    itself), or an obviously non-browser HTTP client.
  - an implausible number of opens from one recipient on one campaign.

There is deliberately no "opened without clicking" signal here -- unlike
clicking, not clicking is completely normal, so it carries no information.

IMPORTANT LIMITATION: Apple Mail Privacy Protection (on by default on
iOS/macOS Mail) pre-fetches every image, including tracking pixels, through
Apple's own relay the moment a message arrives -- by design, to look
indistinguishable from a real device opening the message later. There is no
reliable way to detect this from the event data SES provides. That means the
"genuine" bucket below should be read as "not caught by a known automated
signal", not as "confirmed a human read this" -- for audiences with a lot of
iPhone/Mac Mail users, a real chunk of "genuine" opens here are still Apple's
pre-fetch, not a person. Treat this as a partial, best-effort signal, same
spirit as content_scoring.py's heuristics.
"""

# More opens than this from one recipient on one campaign reads as automated
# repeat-fetching -- genuine re-opens (checking again, a second device) are
# normal and shouldn't get flagged, so this threshold is more lenient than
# click_quality's.
EXCESSIVE_OPENS_PER_RECIPIENT = 5

# Substrings seen in image-proxy/scanner user-agents, or in obviously
# non-browser HTTP clients. "googleimageproxy" is the one genuinely reliable
# signal here -- Gmail's proxy identifies itself openly. The rest overlap
# with click_quality's scanner list since the same security gateways that
# pre-visit links often pre-fetch images too.
_AUTOMATED_USER_AGENT_SUBSTRINGS = (
    "googleimageproxy", "mimecast", "proofpoint", "barracuda", "forcepoint",
    "trendmicro", "trend micro", "symantec", "messagelabs", "zscaler",
    "fireeye", "checkpoint", "sophos", "ironport",
    "python-requests", "curl/", "go-http-client", "wget/", "okhttp",
)


def _looks_automated(user_agent):
    if not user_agent:
        return False
    lowered = user_agent.lower()
    return any(s in lowered for s in _AUTOMATED_USER_AGENT_SUBSTRINGS)


def classify_campaign_opens(conn, configuration_set: str, campaign_id: str) -> dict:
    """Returns {"genuine": int, "automated": int, "total_openers": int,
    "reasons": {email: [reason, ...]}} for one campaign. "genuine" means "no
    known automated signal found", not "confirmed human" -- see module
    docstring re: Apple Mail Privacy Protection."""
    recipients = conn.execute(
        """SELECT email FROM ses_campaign_recipients
           WHERE configuration_set=? AND campaign_id=? AND opened=1""",
        (configuration_set, campaign_id),
    ).fetchall()
    if not recipients:
        return {"genuine": 0, "automated": 0, "total_openers": 0, "reasons": {}}

    open_rows = conn.execute(
        """SELECT email, user_agent, COUNT(*) as n FROM ses_campaign_opens
           WHERE configuration_set=? AND campaign_id=? GROUP BY email, user_agent""",
        (configuration_set, campaign_id),
    ).fetchall()
    agent_counts_by_email = {}
    for row in open_rows:
        agent_counts_by_email.setdefault(row["email"], []).append((row["user_agent"], row["n"]))

    reasons = {}
    for r in recipients:
        email = r["email"]
        email_reasons = []

        agent_counts = agent_counts_by_email.get(email, [])
        total_opens = sum(n for _, n in agent_counts)
        if total_opens >= EXCESSIVE_OPENS_PER_RECIPIENT:
            email_reasons.append(f"opened {total_opens} times -- more than a genuine reader typically would")

        for user_agent, _ in agent_counts:
            if _looks_automated(user_agent):
                email_reasons.append(f"user-agent looks like an image proxy or scanner ({user_agent})")
                break

        if email_reasons:
            reasons[email] = email_reasons

    automated = len(reasons)
    return {
        "genuine": len(recipients) - automated,
        "automated": automated,
        "total_openers": len(recipients),
        "reasons": reasons,
    }
