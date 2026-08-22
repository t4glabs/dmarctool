"""
One-line, deterministic verdicts for every data-heavy table/section on a
domain page -- "here's the bottom line" instead of making the reader scan
raw numbers themselves to figure out if something needs attention.

Every function here returns (status, text) where status is one of
'ok'/'warn'/'bad'/'muted', matching the existing badge/pill CSS classes.
All of this is computed from data the app already gathers, using the same
thresholds already used elsewhere (settings table, or the same fixed
thresholds baked into existing badges) -- nothing here needs an LLM call or
any kind of judgment beyond "count the things past a known threshold."
"""


def domain_vibe_verdict(health_score):
    """The one-glance "how's this domain doing overall" read, from its
    latest composite health_score (app.analysis.snapshot_domain_health --
    already a weighted combination of pass rate, Gmail's own spam rate, and
    bounce/complaint rate into one 0-100 number, so this isn't inventing a
    new formula, just naming its tiers). Deliberately blunt, plain-language
    tiers rather than a raw score -- meant to work on the main dashboard
    itself, not a separate simplified view, so it has to read fine to a
    non-technical reader too.

    The tiers are named "Healthy" / "Needs attention" / "At risk", not
    good/bad/ugly: that phrasing was shorthand for wanting three tiers, and
    the dashboard is something a domain owner may end up looking at. Telling
    someone their organization is "Ugly" is both judgemental and useless --
    these names say what state it's in and imply what to do about it. The
    internal status keys stay ok/warn/bad to match the existing CSS classes
    and every other verdict in this module."""
    if health_score is None:
        return "muted", "Not enough data yet to say."
    if health_score >= 80:
        return "ok", "Authentication, spam rate, and bounce/complaint signals all look healthy."
    if health_score >= 50:
        return "warn", "At least one signal (pass rate, spam rate, or bounce/complaint rate) needs attention."
    return "bad", "Multiple signals are struggling -- worth working through the open action items below."


def senders_verdict(senders, classifications=None):
    """`classifications` from app.source_classification.classify_sources.
    Passing it keeps forwarded mail out of the issue list: a relay that broke
    our own DKIM signature is not a sender problem, it is someone else's mail
    server doing something to our message in transit, and nothing at this end
    can fix it. Counting it as an issue is what made this verdict cry wolf --
    on pattic.org it accounted for 21 of the 27 sources it was flagging."""
    if not senders:
        return "muted", "No known senders recorded yet."

    classifications = classifications or {}
    forwarded = 0
    issues = []
    for s in senders:
        bl = s.get("blocklist_status")
        if bl and bl["status"] == "listed":
            issues.append(f"{s['source_ip']} is on a blocklist")
        pt = s.get("ptr_status")
        if pt and pt["status"] in ("ptr_missing", "mismatch"):
            issues.append(f"{s['source_ip']} has a PTR/reverse-DNS problem")
        if s["total_msgs"] and s["total_msgs"] >= 20 and (s["pass_msgs"] / s["total_msgs"]) < 0.95:
            kind = (classifications.get(s["source_ip"]) or {}).get("kind")
            if kind == "forwarded":
                forwarded += 1
            elif kind == "third_party":
                signed = (classifications[s["source_ip"]].get("signed_by") or ["another domain"])[0]
                issues.append(f"{s['source_ip']} sends as this domain but authenticates as {signed}")
            else:
                issues.append(f"{s['source_ip']} is failing authentication ({s['pass_msgs']}/{s['total_msgs']} passed)")

    fwd_note = (f" {forwarded} other source(s) fail only because their mail was forwarded, which is normal "
                f"and nothing you can fix." if forwarded else "")
    if not issues:
        return "ok", (f"✅ All {len(senders)} known sender(s) look healthy -- no blocklist, PTR, or "
                      f"authentication problems.{fwd_note}")
    shown = "; ".join(issues[:3])
    more = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
    return "bad", f"⚠️ {len(issues)} sender issue(s) need attention: {shown}{more}.{fwd_note}"


def provider_verdict(providers):
    if not providers:
        return "muted", "No provider data in this window yet."

    issues = []
    for p in providers:
        if p["rate"] is not None and p["rate"] < 0.95 and p["total"] >= 20:
            issues.append(f"{p['org_name']} is only passing {p['rate']:.0%} of your mail")
        acted = p["disp_reject"] + p["disp_quarantine"]
        # ignore a stray message or two out of a huge volume -- only worth a flag if
        # it's either a meaningful share of this provider's mail or a real headcount
        if acted and (acted >= 5 or (p["total"] and acted / p["total"] >= 0.005)):
            issues.append(f"{p['org_name']} quarantined/rejected {acted} message(s)")

    if not issues:
        return "ok", f"✅ All {len(providers)} provider(s) are treating your mail well -- high pass rates, nothing quarantined or rejected."
    shown = "; ".join(issues[:3])
    more = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
    return "warn", f"⚠️ {shown}{more}."


def stream_verdict(streams):
    if not streams or len(streams) < 2:
        return "muted", "Only one sending stream seen -- nothing to compare yet."

    rated = [s for s in streams if s["rate"] is not None]
    if not rated:
        return "muted", "Not enough data to compare streams yet."

    best = max(rated, key=lambda s: s["rate"])
    worst = min(rated, key=lambda s: s["rate"])
    if best["rate"] - worst["rate"] < 0.02:
        return "ok", "✅ All sending streams are performing about the same -- no single stream is dragging things down."
    from app.labels import classification_label
    return ("warn", f"⚠️ {classification_label(worst['classification'])} is passing at {worst['rate']:.0%}, "
                     f"noticeably worse than {classification_label(best['classification'])} at {best['rate']:.0%}.")


def spf_dkim_verdict(spf_checks, dkim_checks):
    if not spf_checks and not dkim_checks:
        return "muted", "Not checked yet -- run checks from the overview page."

    issues = []
    for c in spf_checks:
        if c["status"] in ("over_limit", "missing"):
            issues.append(f"SPF for {c['spf_domain']}: {c['note']}")
        elif c["status"] == "warn":
            issues.append(f"SPF for {c['spf_domain']} is close to the 10-lookup limit ({c['lookup_count']})")
    for c in dkim_checks:
        if c["status"] in ("weak", "missing"):
            issues.append(f"DKIM {c['selector']}._domainkey.{c['signing_domain']}: {c['note']}")

    if not issues:
        total = len(spf_checks) + len(dkim_checks)
        return "ok", f"✅ All {total} SPF/DKIM check(s) look healthy."
    shown = "; ".join(issues[:2])
    more = f" (+{len(issues) - 2} more)" if len(issues) > 2 else ""
    return "bad", f"⚠️ {shown}{more}."


def report_auth_verdict(report_auth):
    """Deliberately 'warn', never 'bad': as of the first run every domain
    reporting to aikyamfellows.org lacked its authorization record while
    Google, Outlook, GoDaddy and Zoho were all still delivering reports.
    Calling a latent standards gap a failure would be false, and would train
    the reader to discount the badges that do mean something."""
    if not report_auth:
        return "muted", "No external report addresses to check."
    missing = [c for c in report_auth if c["status"] == "missing"]
    failed = [c for c in report_auth if c["status"] == "lookup_failed"]
    if missing:
        names = ", ".join(c["destination_domain"] for c in missing[:3])
        return "warn", (f"⚠️ {names} hasn't published a record permitting it to collect this domain's "
                        f"reports. Reports are still arriving, so this is housekeeping -- but a strict "
                        f"provider would stop sending them silently.")
    if failed:
        return "muted", "Couldn't check the permission record this time (DNS lookup failed)."
    return "ok", f"✅ All {len(report_auth)} report destination(s) are properly authorized."


def dns_history_verdict(dns_history):
    if not dns_history:
        return "muted", "No DNS checks recorded yet."

    recent = dns_history[:10]
    mismatches = [c for c in recent if c["matches_expected"] == 0]
    if not mismatches:
        return "ok", f"✅ DNS has matched expectations for all of the last {len(recent)} checks."
    # "bad" if the problem is still happening right now (most recent check still
    # mismatches) vs "warn" if it's already resolved and this is just recent history --
    # a missing-record situation reads very differently from a since-fixed report lag.
    severity = "bad" if recent[0]["matches_expected"] == 0 else "warn"
    return severity, (f"⚠️ {len(mismatches)} of the last {len(recent)} checks didn't match expectations -- "
                       f"most recent: {mismatches[0]['note'] or 'see detail below'}.")


def mailgun_verdict(mailgun_stats, bounce_warn, complaint_warn):
    if not mailgun_stats:
        return "muted", "No Mailgun data for this window yet."

    issues = []
    for s in mailgun_stats:
        if not s["accepted"]:
            continue
        bounce_rate = s["failed_perm"] / s["accepted"]
        complaint_rate = s["complained"] / s["accepted"]
        if bounce_rate >= bounce_warn:
            issues.append(f"{s['mailgun_domain']} bounce rate is {bounce_rate:.1%}")
        if complaint_rate >= complaint_warn:
            issues.append(f"{s['mailgun_domain']} complaint rate is {complaint_rate:.2%}")

    if not issues:
        return "ok", f"✅ Bounce and complaint rates look healthy across {len(mailgun_stats)} Mailgun domain(s)."
    return "bad", "⚠️ " + "; ".join(issues) + "."


def ses_verdict(ses_stats, bounce_warn, complaint_warn):
    if not ses_stats:
        return "muted", "No SES event data for this window yet."

    issues = []
    for s in ses_stats:
        # delivered/bounced are disjoint SES outcome counters (a bounced message was
        # never delivered), so "attempted" -- the correct bounce-rate denominator --
        # is their sum, not delivered alone. Also means a config set with zero
        # delivered but nonzero bounced (everything bounced) must not be skipped.
        attempted = s["delivered"] + s["bounced"]
        if not attempted:
            continue
        bounce_rate = s["bounced"] / attempted
        complaint_rate = s["complained"] / s["delivered"] if s["delivered"] else 0
        if bounce_rate >= bounce_warn:
            issues.append(f"{s['configuration_set']} bounce rate is {bounce_rate:.1%}")
        if complaint_rate >= complaint_warn:
            issues.append(f"{s['configuration_set']} complaint rate is {complaint_rate:.2%}")

    if not issues:
        return "ok", f"✅ Bounce and complaint rates look healthy across {len(ses_stats)} configuration set(s)."
    return "bad", "⚠️ " + "; ".join(issues) + "."


def ses_account_verdict(status):
    """Mirrors the exact 'bad' conditions app.ses_account uses when it raises
    the ses_account_health action item, so this badge and that action item
    never disagree about whether the account is healthy."""
    if not status:
        return "muted", "No SES account data yet."
    problems = []
    if status["enforcement_status"] and status["enforcement_status"] != "HEALTHY":
        problems.append(f"enforcement status is {status['enforcement_status']}")
    if not status["sending_enabled"]:
        problems.append("sending is disabled")
    if not status["suppress_bounce"] or not status["suppress_complaint"]:
        problems.append("auto-suppression is off")
    if problems:
        return "bad", "⚠️ " + "; ".join(problems) + "."
    return "ok", "✅ SES account is healthy."


def ses_identity_verdict(identity):
    """Mirrors the exact 'bad' condition app.ses_account uses when it raises
    the ses_identity_unverified action item."""
    if not identity:
        return "muted", "No SES identity data yet."
    if identity["verification_status"] != "SUCCESS" or not identity["sending_enabled"]:
        return "bad", (f"⚠️ Identity verification: {identity['verification_status'] or 'unknown'}, "
                        f"sending enabled: {bool(identity['sending_enabled'])}.")
    return "ok", "✅ Identity verified and sending enabled."


def postmaster_verdict(postmaster_compliance):
    if not postmaster_compliance:
        return "muted", "No Postmaster Tools data for this domain yet."

    needs_work = [c for c in postmaster_compliance if c["status"] == "NEEDS_WORK"]
    if not needs_work:
        return "ok", f"✅ All {len(postmaster_compliance)} Postmaster requirement(s) are compliant."
    from app.labels import postmaster_requirement_label
    labels = ", ".join(postmaster_requirement_label(c["requirement"]) for c in needs_work[:4])
    more = f" (+{len(needs_work) - 4} more)" if len(needs_work) > 4 else ""
    return "warn", f"⚠️ {len(needs_work)} requirement(s) need work: {labels}{more}."
