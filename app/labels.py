"""
Plain-language labels, tooltips, and settings help text -- kept separate from the
route/analysis logic so the dashboard can stay understandable to someone with no
DMARC background without cluttering the code that actually does the work.
"""

CATEGORY_LABELS = {
    "ramp_recommendation": "Next suggested step",
    "new_sender": "New sender to review",
    "failure_investigation": "Sender failing checks",
    "dns_drift": "DNS mismatch",
    "data_stale": "Reports not up to date",
    "blocklist": "Sending IP blocklisted",
    "ptr_issue": "PTR record issue",
    "spf_lookup_limit": "SPF record problem",
    "dkim_weak_key": "Weak DKIM key",
    "mailgun_reputation": "Mailgun bounce/complaint rate",
    "mailgun_new_suppressions": "New Mailgun suppressions",
    "postmaster_compliance": "Gmail Postmaster compliance",
    "ses_reputation_watch": "SES bounce/complaint rate trending up",
    "ses_reputation": "SES bounce/complaint rate",
    "ses_new_suppressions": "New SES suppressions",
    "ses_account_health": "SES account-wide issue",
    "ses_identity_unverified": "SES domain identity not verified",
    "display_name_issue": "Sender display name issue",
    "display_name_inconsistent": "Inconsistent sender display name",
    "volume_spike": "Sudden volume increase",
    "safe_browsing_flagged": "Domain flagged unsafe",
}

CATEGORY_HELP = {
    "ramp_recommendation": "Whether it looks safe to tighten enforcement further, and why.",
    "new_sender": "A source we haven't seen before started sending mail for this domain -- worth a quick look to confirm it's yours.",
    "failure_investigation": "A sending source with a lot of messages is failing authentication -- could be misconfigured, or could be someone spoofing your domain.",
    "dns_drift": "What your DNS record actually says right now doesn't match what we expect, based on your reports or your own change log.",
    "data_stale": "No new DMARC reports have been ingested in a while, so recommendations may be based on old data.",
    "blocklist": "One of this domain's known sending IPs showed up on a public spam blocklist -- mail from it may be getting silently rejected or spam-foldered by receivers that check that list.",
    "ptr_issue": "Gmail requires a sending IP's reverse DNS (PTR) to resolve to a hostname that in turn resolves back to that same IP. Missing or mismatched PTR records are one of Gmail's explicit sender requirements.",
    "spf_lookup_limit": "SPF allows at most 10 DNS lookups per check (RFC 7208). Going over silently breaks SPF for that domain -- receivers treat it as a hard failure with no warning.",
    "dkim_weak_key": "Gmail requires DKIM keys of at least 1024 bits (2048 recommended) for mail sent to personal Gmail accounts.",
    "mailgun_reputation": "Mailgun's reported bounce or complaint rate for this domain crossed the warning threshold -- worth checking your list quality before sending more. Note: this only reflects providers that feed complaints back to Mailgun (e.g. Yahoo) -- Gmail generally doesn't, so a low number here doesn't mean Gmail recipients are happy too.",
    "mailgun_new_suppressions": "Mailgun automatically suppressed new addresses (bounced or complained) since the last check -- these won't receive mail from you again unless removed from Mailgun's suppression list, and are worth pruning from your Listmonk list too.",
    "postmaster_compliance": "Google's own verdict (from Postmaster Tools) on one of its published sender requirements for this domain -- this is Gmail telling you directly what's wrong, not an inference from DMARC reports.",
    "ses_reputation_watch": "Amazon SES's own bounce or complaint rate for this domain has crossed the earlier \"watch\" threshold -- not yet at the danger line, but trending the wrong way.",
    "ses_reputation": "Amazon SES's own bounce or complaint rate for this domain crossed the warning threshold, based on real bounce/complaint events from its dedicated configuration set -- worth checking list quality before sending more.",
    "ses_new_suppressions": "SES recorded new bounces or complaints since the last check for this domain's configuration set -- these addresses are effectively dead ends; worth pruning from Listmonk too.",
    "ses_account_health": "A problem with the SES account itself (not one specific domain) -- e.g. its enforcement status isn't Healthy, sending is disabled account-wide, or automatic bounce/complaint suppression is turned off. Affects every domain sending through this SES account.",
    "ses_identity_unverified": "This domain's SES sending identity isn't fully verified. Mail sent through an unverified identity can be rejected outright or sent without proper authentication.",
    "display_name_issue": "A recent newsletter's \"From\" display name looks like it may not follow Gmail's sender guidelines (https://support.google.com/mail/answer/81126) -- e.g. it reads like subject-line/urgency content, is in all caps, contains emoji, or resembles a threaded-reply count.",
    "display_name_inconsistent": "This domain's newsletters have used more than one \"From\" display name. Gmail's guidelines call for \"a consistent, clear, and accurate statement of the sender's identity\" -- inconsistency can look less trustworthy to recipients and spam filters alike.",
    "volume_spike": "Your recent daily sending volume is well above your own trailing average. Gmail's guidance is to ramp volume up gradually -- a sudden jump (even of genuinely good mail) can trigger rate limiting or hurt reputation.",
    "safe_browsing_flagged": "Google Safe Browsing has flagged this domain (or a URL on it) as unsafe -- e.g. malware or phishing. This can independently hurt email deliverability and trust, separate from your DMARC/authentication setup.",
}

# Concrete "what to actually do about it" guidance per action-item category --
# separate from CATEGORY_HELP (which explains *what the problem is*) so alerts
# don't just tell you something's wrong without telling you how to fix it.
CATEGORY_REMEDIATION = {
    "ramp_recommendation": "Update your domain's DMARC TXT record (_dmarc.<domain>) with the new p=/pct= values yourself, then log the change under Log a manual action in the Manual Log tab so DMARCTool can track it against live DNS.",
    "new_sender": "Check the Known senders table in the Senders tab for this IP's reverse DNS (PTR) and volume. If it's a service you actually use, label it with the \"What is this?\" dropdown there so it stops getting flagged. If you don't recognize it, someone may be spoofing your domain -- consider tightening your DMARC policy (raise pct, or move toward p=reject).",
    "failure_investigation": "In the Known senders table (Senders tab), check whether this IP's hostname (PTR) matches a service you use, and whether it's covered by your SPF record's includes or signs with a DKIM selector your DNS actually publishes (Gmail sender requirements table, Authentication tab). Consistent, high-volume failures from one source are usually a misconfigured ESP integration, not spoofing.",
    "dns_drift": "Update your DMARC TXT record to match what you intended. If you changed it on purpose, log it under Log a manual action in the Manual Log tab so this stops being flagged as unexpected drift.",
    "data_stale": "Go to the Overview page (the domain list) and use the \"Add new reports\" upload form to bring this domain's data current.",
    "blocklist": "Request delisting directly: Spamhaus -> https://check.spamhaus.org/ (look up the IP, follow its removal link) -- Barracuda -> https://www.barracudacentral.org/rbl/removal-request . Before requesting removal, check for a real cause (compromised sender, sudden spam complaints, or a shared IP with a bad neighbor) or it may relist quickly. Once delisted, the Blocklist column under Known senders (Senders tab) will show Clean again within a few hours.",
    "ptr_issue": "If this is your own dedicated IP (not a shared ESP pool), add or fix its reverse DNS (PTR) record via your hosting/cloud provider's control panel -- for AWS EC2/SES dedicated IPs this means opening an AWS Support case to set a custom PTR. Shared ESP pool IPs (SES, Mailgun) already have this managed for you; this only needs action if it's your own infrastructure. See the PTR/rDNS column under Known senders (Senders tab) for which IP this is.",
    "spf_lookup_limit": "Reduce nested `include:` mechanisms in your SPF record -- remove ESP includes you no longer use, or replace static IP ranges with direct ip4:/ip6: entries instead of an include. See the Gmail sender requirements table in the Authentication tab for the current lookup count.",
    "dkim_weak_key": "Regenerate this DKIM key at 2048-bit: in Google Workspace, Admin Console -> Apps -> Google Workspace -> Gmail -> Authenticate email. For Mailgun/SES-hosted keys, use that provider's domain/DKIM settings to rotate the key. See the Gmail sender requirements table in the Authentication tab for exactly which selector and signing domain this is.",
    "mailgun_reputation": "Use the \"Download suppressions\" button at the top of this Deliverability & Spam tab to get a CSV of exactly which addresses recently bounced or complained, prune them from your Listmonk subscriber list, and review your opt-in practices and sending frequency before your next campaign.",
    "mailgun_new_suppressions": "These addresses are now suppressed in Mailgun and won't receive mail -- use the \"Download suppressions\" button at the top of this Deliverability & Spam tab to get the exact list (with reasons and dates) and remove them from your Listmonk list too.",
    "ses_reputation_watch": "No urgent action yet -- but check the \"Download suppressions\" CSV for this configuration set to see if a pattern is forming, and consider slowing send frequency until the rate settles back down.",
    "ses_reputation": "Use the \"Download suppressions\" button at the top of this Deliverability & Spam tab to get a CSV of this configuration set's bounce/complaint addresses, prune them from Listmonk, and review list quality and send frequency before your next campaign.",
    "ses_new_suppressions": "These addresses are now suppressed in SES and won't receive further mail -- use the \"Download suppressions\" button at the top of this Deliverability & Spam tab to get the exact list and remove them from Listmonk too.",
    "ses_account_health": "Check the AWS SES Console -> Account dashboard / Reputation for the specifics. If enforcement status isn't Healthy, AWS Support can usually clarify why. If auto-suppression is off, turn it back on under Account dashboard -> Suppression list settings.",
    "ses_identity_unverified": "Check AWS SES Console -> Identities for this domain. If verification is pending, confirm the required DNS records (TXT/CNAME/MX depending on method) are actually published. If sending is disabled, check for a policy violation notice from AWS.",
    "display_name_issue": "In Listmonk, edit this campaign's (or the default) \"From\" name to be a plain, consistent statement of who you are -- e.g. your organization name -- rather than subject-line wording, all-caps, emoji, or a reply-count-style suffix.",
    "display_name_inconsistent": "In Listmonk, standardize the \"From\" name used across all campaigns for this domain (Settings -> Mailserver, or per-campaign if overridden) to one consistent name.",
    "volume_spike": "If this jump was intentional (a planned campaign), no action needed -- it should stop flagging once your baseline catches up. If it wasn't intentional, check for a misconfigured automation/loop in Listmonk or your ESP that's resending or duplicating messages. Either way, avoid stacking another big increase on top of this one until it settles.",
    "safe_browsing_flagged": "Check https://transparencyreport.google.com/safe-browsing/search for the specific flagged URL/reason. If it's your own website, scan for and remove malware/injected content, then request a review in Google Search Console. If it's a third-party link in a footer/widget you don't control, remove it.",
}

# postmaster_compliance items all share one category, but the fix depends on
# *which* Postmaster requirement failed -- that's embedded in the action
# item's ref_key ("<postmaster_domain>:<REQUIREMENT>"), not the category itself.
POSTMASTER_REQUIREMENT_REMEDIATION = {
    "SPF_AND_DKIM": "Check the Authentication tab -- make sure SPF and DKIM are both published and passing for your actual sending infrastructure, not just your primary domain.",
    "DMARC_ALIGNMENT": "Your DKIM or SPF signing domain doesn't align closely enough with your header From: domain. Check the Authentication tab's SPF/DKIM entries -- the signing/envelope domain should be your domain or a subdomain of it.",
    "ENCRYPTION": "Some of your mail isn't using TLS in transit. In Google Workspace: Admin Console -> Apps -> Google Workspace -> Gmail -> Compliance -> Require TLS. For SES/Mailgun, set the sending configuration set's TLS policy to Required.",
    "DNS_RECORDS": "Your sending IP's reverse DNS (PTR) isn't set up correctly -- see the PTR/rDNS column under Known Senders in the Senders tab for which IP and what's wrong.",
    "ONE_CLICK_UNSUBSCRIBE": "Make sure your bulk sender (Listmonk/Mailgun/SES) includes both List-Unsubscribe and List-Unsubscribe-Post: List-Unsubscribe=One-Click headers on every campaign message.",
    "HONOR_UNSUBSCRIBE": "Confirm unsubscribe requests from Listmonk/Mailgun/SES are actually being processed and suppressed promptly, not just recorded.",
    # DELIVERABILITY is handled entirely in postmaster_remediation() below -- its
    # advice depends on the specific reason code, not a single static message.
}


def category_remediation(category):
    return CATEGORY_REMEDIATION.get(category)


def postmaster_remediation(ref_key, current_p=None, current_pct=None, current_spam_rate=None,
                            reason=None, google_volume=None, window_days=None):
    """DMARC_POLICY, USER_REPORTED_SPAM_RATE, and DELIVERABILITY get a domain-specific
    answer built from what DMARCTool already knows (live DNS policy, current spam rate,
    actual Gmail-seen message volume, and -- for DELIVERABILITY -- the specific reason
    code) instead of generic boilerplate. Two real gaps this closes:
      - "publish a policy" is wrong advice for a domain that already has one (Google's
        bar is *enforcing at a meaningful percentage*, not merely "a record exists").
      - "fix whichever requirement is flagged" is circular/useless when DELIVERABILITY
        is the *only* thing flagged and every other requirement is already COMPLIANT --
        which is exactly what MESSAGE_VOLUME_LOW means: nothing is misconfigured, Gmail
        just hasn't seen enough mail from this domain to score it confidently yet.
    """
    if not ref_key or ":" not in ref_key:
        return None
    requirement = ref_key.rsplit(":", 1)[-1]

    if requirement == "DMARC_POLICY":
        if current_p:
            if current_p == "none" or (current_pct is not None and current_pct < 100):
                detail = f"p={current_p}" + (f", pct={current_pct}" if current_pct is not None else "")
                return (f"You already have a DMARC policy ({detail}) -- Google's bar for this requirement is "
                        f"actively enforcing at a meaningful percentage, not just having a record published. "
                        f"Check the Overview tab's recommendation for whether it's safe to raise enforcement "
                        f"(move off p=none, or ramp pct toward 100).")
            return (f"You already have an enforcing DMARC policy (p={current_p}, pct={current_pct}) -- if Google "
                    f"still flags this, it may be seeing a different record than expected (reporting lag, or a "
                    f"stale cached lookup). Check the Authentication tab's DNS vs. reports section for a mismatch.")
        return "No DMARC record found for this domain. Publish one in DNS -- p=none is a fine starting point (see the Authentication tab)."

    if requirement == "USER_REPORTED_SPAM_RATE":
        rate_note = f" (currently {current_spam_rate:.3%})" if current_spam_rate is not None else ""
        return (f"Gmail users are marking your mail as spam more than recommended{rate_note}. Use the "
                f"\"Download suppressions\" button in this Deliverability & Spam tab (if you send via Mailgun/SES) "
                f"to see which addresses are bouncing or complaining, review consent/opt-in practices and sending "
                f"frequency, and prune non-engaged or complaining recipients from Listmonk.")

    if requirement == "DELIVERABILITY":
        if reason == "MESSAGE_VOLUME_LOW":
            volume_note = ""
            if google_volume is not None and window_days is not None:
                volume_note = f" Gmail has seen about {google_volume} message(s) from this domain in the last {window_days} days -- "
            return (f"This isn't a misconfiguration -- it means Gmail doesn't have enough sustained volume from "
                    f"this domain yet to confidently score its deliverability.{volume_note}Google doesn't publish "
                    f"an exact threshold, but consistent, regular sending (not just occasional bursts) is what "
                    f"builds that confidence over time. If every other requirement above is Compliant, there's "
                    f"nothing to actively fix here -- it typically clears on its own as real volume accumulates, "
                    f"or you can safely ignore it if this domain isn't meant to send much.")
        if reason == "SENDER_NOT_COMPLIANT":
            return ("This is Google's overall verdict combining every requirement above -- check which specific "
                    "one(s) in the table are marked Needs work and fix those; this clears on its own once they do.")
        return "This is Google's overall verdict combining every requirement above -- fix whichever specific one(s) are flagged NEEDS_WORK and this should clear on its own."

    return POSTMASTER_REQUIREMENT_REMEDIATION.get(requirement)


POSTMASTER_REQUIREMENT_LABELS = {
    "SPF_AND_DKIM": "SPF and DKIM",
    "DMARC_ALIGNMENT": "DMARC alignment",
    "DMARC_POLICY": "DMARC policy",
    "ENCRYPTION": "TLS encryption",
    "USER_REPORTED_SPAM_RATE": "Spam rate",
    "DNS_RECORDS": "DNS records (PTR/FCrDNS)",
    "ONE_CLICK_UNSUBSCRIBE": "One-click unsubscribe",
    "HONOR_UNSUBSCRIBE": "Honors unsubscribe requests",
    "DELIVERABILITY": "Overall deliverability",
}


def postmaster_requirement_label(requirement):
    return POSTMASTER_REQUIREMENT_LABELS.get(requirement, requirement)

CLASSIFICATION_LABELS = {
    "unclassified": "Not yet identified",
    "ses_newsletter": "Bulk/newsletter sender (Amazon SES)",
    "ses_pool": "Amazon SES sending pool",
    "workspace": "Google Workspace mail",
    "primary_domain": "Regular company email",
    "ignored": "Ignored (marked as fine)",
}

CLASSIFICATION_HELP = {
    "unclassified": "We don't have a label for this sender yet -- if it's not something you recognize, it's worth checking.",
    "ses_newsletter": "This sender signs its mail as your mails.<domain> subdomain, matching your bulk/newsletter sending setup.",
    "ses_pool": "Manually labeled as part of your Amazon SES sending pool.",
    "workspace": "Manually labeled as Google Workspace mail.",
    "primary_domain": "This sender signs its mail using your main domain name directly (often regular company mailbox traffic).",
    "ignored": "You've marked this sender as fine -- it won't be called out again.",
}

DNS_STATUS_LABELS = {
    "ok": "Checked",
    "missing": "No DMARC record found",
    "multiple": "Conflicting records found",
    "lookup_failed": "Could not check (network issue)",
    "unknown": "Not checked yet",
}

DNS_STATUS_HELP = {
    "ok": "We successfully read your domain's DMARC DNS record.",
    "missing": "Your domain has no DMARC record at all right now -- it is not protected against spoofing (unless a parent domain covers it).",
    "multiple": "More than one DMARC record exists for this domain. Mail providers won't know which one to trust and may ignore both -- worth cleaning up in DNS.",
    "lookup_failed": "The DNS check couldn't complete, likely a temporary network issue. Try again later.",
    "unknown": "We haven't run a DNS check for this domain yet.",
}

SETTINGS_META = {
    "min_pass_rate": {
        "label": "Pass rate needed to move forward",
        "help": "How good does your recent pass rate need to be before it's considered safe to tighten enforcement further?",
        "example": "0.99 means 99% of your mail must be passing authentication. Enter as a fraction, not a percentage (99% = 0.99).",
    },
    "low_pass_rate": {
        "label": "Pass rate that triggers a warning",
        "help": "If your recent pass rate drops below this, we recommend pausing and investigating instead of tightening further.",
        "example": "0.95 means below 95% pass, something is likely wrong and worth a look before continuing.",
    },
    "min_days_stable": {
        "label": "Days to hold steady before the next step",
        "help": "How many days your current setting needs to stay unchanged, with a good pass rate, before we suggest moving to the next step.",
        "example": "14 means two full weeks of good results at the current setting before recommending a change.",
    },
    "rolling_window_days": {
        "label": "Days of recent data to look at",
        "help": "How many days of your most recent reports we average together to calculate your current pass rate.",
        "example": "21 means we look at the last three weeks of mail, not your entire history.",
    },
    "min_volume_for_recommendation": {
        "label": "Minimum mail volume to trust the numbers",
        "help": "Small domains sometimes send very few emails in a given window -- a 100% (or 0%) pass rate on 2 emails doesn't mean much. Below this many messages, we say 'not enough data' instead of guessing.",
        "example": "50 means we want to see at least 50 emails in the window before making a recommendation.",
    },
    "ramp_steps": {
        "label": "Enforcement ladder (percent steps)",
        "help": "The sequence of enforcement percentages you move through over time, from lightest to full enforcement.",
        "example": "10,25,50,100 means: start enforcing on 10% of mail, then 25%, then 50%, then all of it. Comma-separated, no spaces.",
    },
    "new_sender_window_days": {
        "label": "\"New sender\" window",
        "help": "A sending source counts as new (and gets flagged for review) if it was first seen within this many days.",
        "example": "14 means anything first seen in the last two weeks is treated as new.",
    },
    "high_volume_fail_threshold": {
        "label": "Message count that makes a failure worth flagging",
        "help": "A sending source needs at least this many messages before a high failure rate gets flagged as worth investigating (avoids flagging one-off blips).",
        "example": "20 means a sender needs 20+ messages before we'll flag it for failing too often.",
    },
    "high_fail_rate_threshold": {
        "label": "Failure rate that triggers a flag",
        "help": "Combined with the message count above: if a sender's pass rate falls below this, it gets flagged as worth investigating.",
        "example": "0.5 means fewer than half its messages are passing authentication.",
    },
    "stale_days_threshold": {
        "label": "Days without new reports before warning you",
        "help": "If no new DMARC reports have been ingested for a domain in this many days, we'll remind you to re-run ingestion.",
        "example": "3 means you'll get a reminder if it's been 3+ days since the last report came in.",
    },
    "blocklist_min_volume": {
        "label": "Minimum mail volume to bother checking a sender for blocklisting",
        "help": "One-off/stray IPs that sent you a handful of messages aren't worth checking against public spam blocklists. Only senders with at least this many total messages get checked.",
        "example": "50 means a sending IP needs 50+ total messages before we check it against blocklists.",
    },
    "blocklist_recent_days": {
        "label": "\"Still sending\" window for blocklist checks",
        "help": "Only senders seen sending within this many days get checked against blocklists -- no point repeatedly checking IPs that stopped sending long ago.",
        "example": "30 means a sender must have sent something in the last 30 days to be checked.",
    },
    "blocklist_recheck_hours": {
        "label": "Minimum gap between blocklist re-checks",
        "help": "An IP already checked within this many hours is skipped (and its last known status just carries forward) -- avoids re-querying the same IPs every time you click \"Run checks now\".",
        "example": "4 means clicking refresh twice within 4 hours only actually re-checks new/expired IPs, not everything.",
    },
    "compliance_recheck_hours": {
        "label": "Minimum gap between PTR/SPF/DKIM re-checks",
        "help": "PTR, SPF, and DKIM records change far less often than blocklist status, so these are cached longer between checks.",
        "example": "24 means these only get re-checked about once a day, even if you click refresh more often.",
    },
    "spf_lookup_warn_threshold": {
        "label": "SPF lookup count that triggers a warning",
        "help": "SPF hard-fails once a domain's record needs more than 10 DNS lookups to evaluate. We warn before you hit that wall so you can fix it before it silently breaks.",
        "example": "8 means you'll get a warning once your SPF setup needs 8+ of its 10 allowed lookups.",
    },
    "dkim_min_bits": {
        "label": "Minimum acceptable DKIM key size (bits)",
        "help": "Gmail's published minimum for RSA DKIM keys sent to personal Gmail accounts. Keys below this get flagged as weak.",
        "example": "1024 is Gmail's hard minimum; Google recommends 2048 if your provider supports it.",
    },
    "mailgun_recheck_hours": {
        "label": "Minimum gap between Mailgun API polls",
        "help": "How often we re-fetch stats and suppression lists from Mailgun's API. Kept longer than the local DNS checks since it's a real API call against your account.",
        "example": "6 means Mailgun data refreshes at most every 6 hours, even if you click refresh more often.",
    },
    "mailgun_stats_window_days": {
        "label": "Mailgun stats lookback window",
        "help": "How many days of delivered/bounced/complained/unsubscribed history to pull from Mailgun each time.",
        "example": "30 means the numbers shown reflect the last 30 days, not your all-time totals.",
    },
    "mailgun_bounce_rate_warn": {
        "label": "Mailgun bounce rate that triggers a flag",
        "help": "If the share of accepted mail that permanently bounced goes above this, you'll get an action item -- high bounce rates are the clearest sign of a stale list.",
        "example": "0.05 means a flag once 5% or more of your mail bounces.",
    },
    "mailgun_complaint_rate_warn": {
        "label": "Mailgun complaint rate that triggers a flag",
        "help": "If the share of accepted mail marked as spam (via providers that report it back to Mailgun) goes above this, you'll get an action item.",
        "example": "0.001 means a flag once 0.1% or more of your mail gets marked as spam.",
    },
    "postmaster_recheck_hours": {
        "label": "Minimum gap between Postmaster Tools polls",
        "help": "How often we re-fetch spam rate and compliance status from Google. Postmaster Tools' own data is aggregated/lagged by about a day, so polling more often than this wouldn't show anything new.",
        "example": "24 means Postmaster data refreshes at most once a day.",
    },
    "postmaster_stats_window_days": {
        "label": "Postmaster spam-rate lookback window",
        "help": "How many days of Gmail-reported spam-rate history to pull each time.",
        "example": "30 means the number shown reflects the last 30 days, not all-time.",
    },
    "ses_stats_window_days": {
        "label": "SES bounce/complaint rate lookback window",
        "help": "How many days of DMARCTool's own accumulated SES event counts to sum when computing the rate shown (SES itself has no on-demand stats API -- this is built entirely from events we've captured).",
        "example": "30 means the rate reflects the last 30 days of events DMARCTool has seen since you set up the SNS/SQS pipeline.",
    },
    "ses_bounce_rate_watch": {
        "label": "SES bounce rate that triggers an early watch flag",
        "help": "A softer, earlier warning than the main bounce flag below -- worth keeping an eye on, not yet urgent.",
        "example": "0.02 means a soft flag once 2% or more of your mail bounces.",
    },
    "ses_bounce_rate_warn": {
        "label": "SES bounce rate that triggers a flag",
        "help": "If the share of delivered mail that bounced (for a domain's dedicated configuration set) goes above this, you'll get an action item.",
        "example": "0.05 means a flag once 5% or more of your mail bounces.",
    },
    "ses_complaint_rate_watch": {
        "label": "SES complaint rate that triggers an early watch flag",
        "help": "A softer, earlier warning than the main complaint flag below -- worth keeping an eye on, not yet urgent.",
        "example": "0.0008 means a soft flag once 0.08% or more of your mail gets marked as spam.",
    },
    "ses_complaint_rate_warn": {
        "label": "SES complaint rate that triggers a flag",
        "help": "If the share of delivered mail marked as spam goes above this, you'll get an action item.",
        "example": "0.001 means a flag once 0.1% or more of your mail gets marked as spam.",
    },
    "ses_account_recheck_hours": {
        "label": "SES account health recheck frequency (hours)",
        "help": "How often to re-poll SES for account-wide health (enforcement status, sending quota, suppression settings) and per-domain identity verification. This is a small, cheap API call, but no need to hit it constantly.",
        "example": "24 means account health refreshes at most once a day.",
    },
    "ses_max_messages_per_run": {
        "label": "Max SES events drained per check",
        "help": "SES publishes an event for every single delivered/bounced/complained message, so a busy sender can build a large backlog. This caps how many get processed in one check so it can't block a request for a long time -- the rest just get picked up on the next run.",
        "example": "3000 means at most 3000 queued events get processed each time; a bigger backlog drains over several runs.",
    },
    "newsletter_inactive_campaigns": {
        "label": "Newsletters received before someone counts as inactive",
        "help": "A subscriber who has received at least this many newsletters, but never opened a single one of them, gets flagged as inactive -- worth re-engaging or removing from the list.",
        "example": "9 means someone needs 9 delivered newsletters with zero opens before being flagged.",
    },
    "volume_spike_recent_days": {
        "label": "\"Recent\" window for volume-spike detection (days)",
        "help": "How many of the most recent days get averaged together as your current sending volume, for comparison against your own trailing baseline.",
        "example": "3 means the last 3 days' average volume is what gets compared.",
    },
    "volume_spike_baseline_days": {
        "label": "Baseline window for volume-spike detection (days)",
        "help": "How many days before the \"recent\" window get averaged together as your normal baseline volume.",
        "example": "7 means the 7 days before the recent window set your normal/expected volume.",
    },
    "volume_spike_min_baseline_avg": {
        "label": "Minimum baseline volume to bother checking for a spike",
        "help": "A domain with very little baseline volume can look like it \"doubled\" from just a couple of stray messages -- this avoids flagging that as a spike.",
        "example": "10 means your baseline needs to average at least 10 msgs/day before spikes are checked at all.",
    },
    "volume_spike_multiplier": {
        "label": "How much of a jump counts as a spike",
        "help": "Your recent average needs to be at least this many times your baseline average to get flagged.",
        "example": "2.0 means a flag once recent volume is 2x (a 100% increase over) your normal baseline.",
    },
    "safe_browsing_recheck_hours": {
        "label": "Minimum gap between Safe Browsing re-checks",
        "help": "How often we re-check each domain against Google Safe Browsing. This status doesn't change quickly, so daily is plenty.",
        "example": "24 means each domain gets checked at most once a day.",
    },
}


def explain_policy(p, pct):
    """Plain-language sentence for a given p= / pct= combination."""
    if not p:
        return ""
    pct = 100 if pct is None else pct
    if p == "none":
        return "Monitoring only -- nothing is blocked or sent to spam yet."
    verb = "sent to spam" if p == "quarantine" else "blocked outright"
    if pct >= 100:
        return f"Suspicious mail is {verb}, for all checked mail."
    return f"Suspicious mail is {verb}, for {pct}% of checked mail (the rest is only monitored)."


def category_label(category):
    return CATEGORY_LABELS.get(category, category)


def category_help(category):
    return CATEGORY_HELP.get(category, "")


def classification_label(classification):
    return CLASSIFICATION_LABELS.get(classification, classification)


def classification_help(classification):
    return CLASSIFICATION_HELP.get(classification, "")


def dns_status_label(status):
    return DNS_STATUS_LABELS.get(status, status)


def dns_status_help(status):
    return DNS_STATUS_HELP.get(status, "")
