"""
Tells a genuine subscriber click apart from a corporate email-security
gateway (Mimecast, Proofpoint, Microsoft Defender Safe Links, etc.) that
auto-visits every link in a message to scan it -- often before, or without,
a human ever reading the newsletter. SES only reports "a click happened";
ses_campaign_clicks (raw per-click ip/user-agent/link log, populated by
app.ses_events) is what lets this module tell the two apart, using signals
that are hard for a scanner to avoid:

  - clicked without ever opening the message first -- opens fire on a pixel
    load, which most gateways skip since they only fetch link URLs, not
    images, so a real person can't click a link inside mail they never
    rendered.
  - an implausible number of clicks from one recipient on one campaign --
    gateways commonly re-verify/re-fetch the same link multiple times;
    genuine readers rarely click a newsletter link more than once or twice.
  - a user-agent that names a known scanner, or is an obviously non-browser
    HTTP client (curl/python-requests/etc).

This is a heuristic, not a certainty -- like content_scoring.py, treat it as
a prioritization signal for reading the numbers, not a verdict.
"""

# More clicks than this from one recipient on one campaign reads as
# automated re-checking rather than a person re-clicking the same newsletter.
EXCESSIVE_CLICKS_PER_RECIPIENT = 3

# Substrings seen in security-gateway/link-scanner user-agents, or in
# obviously non-browser HTTP clients -- deliberately short and specific
# rather than an exhaustive list, same rationale as content_scoring.py's
# phrase list (long generic lists false-positive too easily).
_SCANNER_USER_AGENT_SUBSTRINGS = (
    "mimecast", "proofpoint", "urldefense", "safelinks", "barracuda",
    "forcepoint", "trendmicro", "trend micro", "symantec", "messagelabs",
    "zscaler", "fireeye", "checkpoint", "sophos", "ironport",
    "python-requests", "curl/", "go-http-client", "wget/", "okhttp",
)


def _looks_like_scanner(user_agent):
    if not user_agent:
        return False
    lowered = user_agent.lower()
    return any(s in lowered for s in _SCANNER_USER_AGENT_SUBSTRINGS)


def classify_campaign_clicks(conn, configuration_set: str, campaign_id: str) -> dict:
    """Returns {"genuine": int, "automated": int, "total_clickers": int,
    "reasons": {email: [reason, ...]}} for one campaign, by combining
    ses_campaign_recipients (opened/clicked flags) with the raw
    ses_campaign_clicks log (per-recipient click count/user-agent)."""
    recipients = conn.execute(
        """SELECT email, opened FROM ses_campaign_recipients
           WHERE configuration_set=? AND campaign_id=? AND clicked=1""",
        (configuration_set, campaign_id),
    ).fetchall()
    if not recipients:
        return {"genuine": 0, "automated": 0, "total_clickers": 0, "reasons": {}}

    click_rows = conn.execute(
        """SELECT email, user_agent, COUNT(*) as n FROM ses_campaign_clicks
           WHERE configuration_set=? AND campaign_id=? GROUP BY email, user_agent""",
        (configuration_set, campaign_id),
    ).fetchall()
    agent_counts_by_email = {}
    for row in click_rows:
        agent_counts_by_email.setdefault(row["email"], []).append((row["user_agent"], row["n"]))

    reasons = {}
    for r in recipients:
        email = r["email"]
        email_reasons = []
        if not r["opened"]:
            email_reasons.append("clicked without ever opening the message")

        agent_counts = agent_counts_by_email.get(email, [])
        total_clicks = sum(n for _, n in agent_counts)
        if total_clicks >= EXCESSIVE_CLICKS_PER_RECIPIENT:
            email_reasons.append(f"clicked {total_clicks} times -- more than a genuine reader typically would")

        for user_agent, _ in agent_counts:
            if _looks_like_scanner(user_agent):
                email_reasons.append(f"user-agent looks like a security scanner or bot ({user_agent})")
                break

        if email_reasons:
            reasons[email] = email_reasons

    automated = len(reasons)
    return {
        "genuine": len(recipients) - automated,
        "automated": automated,
        "total_clickers": len(recipients),
        "reasons": reasons,
    }
