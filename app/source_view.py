"""
One sending source, across every domain it touches.

Everything else in this tool is organised per-domain, which is the right
default and also a blind spot: a single misconfigured relay shows up as an
unrelated-looking problem on each domain separately, and nothing says "these
are the same thing".

The case that prompted this: Mailgun IPs 69.72.42.1 and 69.72.42.14
authenticate as aikyamsolve.org while sending as aikyamhq.com AND
tinybridge.in AND aikyamfellows.org. On aikyamhq.com there was enough volume
to raise a borrowed-identity item; on tinybridge.in the same
misconfiguration, from the same IP, with the same borrowed identity, sat at 4
messages and raised nothing. One fix addresses all three, and no per-domain
page could say so.

Two things live here:

  `source_overview()` -- the read-only view behind /source/<ip>, answering
  "what is this machine, whose mail does it send, and is it doing the same
  thing everywhere".

  `corroborated_identity_pairs()` -- the same cross-domain evidence, fed back
  into app.analysis's borrowed-identity detector so it can recognise a pattern
  it has already proven elsewhere instead of demanding independent proof on
  every domain. See detect_borrowed_sending_identity for how it is applied.
"""

from app.source_classification import HEALTHY_PASS_RATE, classify_sources


def _rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def source_overview(conn, source_ip: str) -> dict:
    """Everything known about one sending IP, across all tracked domains."""
    per_domain = _rows(
        conn,
        """SELECT d.id AS domain_id, d.name AS domain_name,
                  SUM(rr.count) AS total,
                  SUM(CASE WHEN rr.dkim_result='pass' OR rr.spf_result='pass' THEN rr.count ELSE 0 END) AS passed,
                  SUM(CASE WHEN rr.disposition IN ('quarantine','reject') THEN rr.count ELSE 0 END) AS acted,
                  MIN(r.date_begin) AS first_seen, MAX(r.date_end) AS last_seen
           FROM report_records rr
           JOIN reports r ON r.id = rr.report_id
           JOIN domains d ON d.id = r.domain_id
           WHERE rr.source_ip = ?
           GROUP BY d.id, d.name
           ORDER BY total DESC""",
        (source_ip,),
    )
    if not per_domain:
        return {}

    # Which identities this IP produces valid signatures for, and on which
    # domains -- the fact that makes a shared root cause visible.
    identities = {}
    for row in _rows(
        conn,
        """SELECT LOWER(TRIM(ar.domain)) AS auth_domain, d.name AS domain_name, SUM(rr.count) AS msgs
           FROM record_auth_results ar
           JOIN report_records rr ON rr.id = ar.record_id
           JOIN reports r ON r.id = rr.report_id
           JOIN domains d ON d.id = r.domain_id
           WHERE rr.source_ip = ? AND ar.result = 'pass' AND ar.domain IS NOT NULL
           GROUP BY auth_domain, d.name""",
        (source_ip,),
    ):
        entry = identities.setdefault(row["auth_domain"], {"domains": [], "msgs": 0})
        entry["domains"].append(row["domain_name"])
        entry["msgs"] += row["msgs"] or 0

    tracked = {r["name"].lower() for r in _rows(conn, "SELECT name FROM domains")}

    # Per-domain classification, reusing the same logic the Senders tab shows
    # so the two can never disagree about what this source is.
    domains = []
    for row in per_domain:
        cls = classify_sources(conn, row["domain_id"], row["domain_name"]).get(source_ip) or {}
        total, passed = row["total"] or 0, row["passed"] or 0
        domains.append({
            "domain_name": row["domain_name"],
            "total": total,
            "passed": passed,
            "acted": row["acted"] or 0,
            "pass_rate": (passed / total) if total else None,
            "kind": cls.get("kind"),
            "label": cls.get("label"),
            "help": cls.get("help"),
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        })

    total_msgs = sum(d["total"] for d in domains)
    total_passed = sum(d["passed"] for d in domains)
    failing = [d for d in domains
               if d["pass_rate"] is not None and d["pass_rate"] < HEALTHY_PASS_RATE]

    return {
        "source_ip": source_ip,
        "domains": domains,
        "identities": sorted(
            ({"auth_domain": k, "msgs": v["msgs"], "domains": sorted(set(v["domains"])),
              "is_tracked": k in tracked} for k, v in identities.items()),
            key=lambda i: -i["msgs"],
        ),
        "total_msgs": total_msgs,
        "total_passed": total_passed,
        "pass_rate": (total_passed / total_msgs) if total_msgs else None,
        "acted": sum(d["acted"] for d in domains),
        "domain_count": len(domains),
        "failing_domain_count": len(failing),
        "ptr": conn.execute(
            "SELECT status, ptr_hostname, note FROM ptr_checks WHERE source_ip=? ORDER BY checked_at DESC LIMIT 1",
            (source_ip,),
        ).fetchone(),
        "blocklist": conn.execute(
            "SELECT status, note FROM blocklist_checks WHERE source_ip=? ORDER BY checked_at DESC LIMIT 1",
            (source_ip,),
        ).fetchone(),
    }


_RELAY_PTR_HINTS = ("sec", "av", "filter", "forward", "relay", "gateway", "gw", "spam", "proxy", "barracuda", "mimecast", "proofpoint")


def _owner_line(conn, overview: dict):
    """Plain 'who owns this address' sentence from WHOIS + reverse DNS, with a
    guess at what the reverse-DNS name implies (a named ESP, a security/
    forwarding relay, or just a rented cloud server) -- the part a
    non-technical reader needs to answer 'is this mine?'."""
    from app.analysis import cached_whois_org, cached_whois_country, _guess_provider
    ip = overview["source_ip"]
    org = cached_whois_org(conn, ip, allow_live=False)
    country = cached_whois_country(conn, ip)
    ptr = overview["ptr"]["ptr_hostname"] if overview.get("ptr") and overview["ptr"]["ptr_hostname"] else None
    provider = _guess_provider(ptr) if ptr else None

    who = org or "an unknown network"
    if country:
        who += f" (registered in {country})"
    parts = [f"This address belongs to {who}."]
    if provider:
        parts.append(f"Its reverse-DNS name points to {provider}.")
    elif ptr:
        low = ptr.lower()
        if any(h in low for h in _RELAY_PTR_HINTS):
            parts.append(f"Its reverse-DNS name ({ptr}) looks like an email security / forwarding service.")
        elif "amazonaws" in low or "compute" in low or "ec2" in low or "cloud" in low:
            parts.append(f"Its reverse-DNS name ({ptr}) looks like a rented cloud server.")
        else:
            parts.append(f"Its reverse-DNS name is {ptr}.")
    return " ".join(parts)


_MINE_CHECKLIST = [
    "a newsletter / bulk-email tool (Listmonk, Mailchimp, a YAMM-style merge)",
    "your website's contact, order, or signup forms",
    "a CRM, helpdesk, or invoicing system",
    "an email security / filtering / forwarding service in front of your mail",
]


def source_action_guide(conn, overview: dict):
    """The plain-language 'what is this, and what should I do?' panel for the
    top of the source page -- the thing a non-technical operator actually needs
    when they land on a mystery IP and don't know if it's theirs or what to do.

    Drives the verdict off the evidence-based per-domain classification
    (forwarded / third_party / unverified / aligned) the Senders tab already
    shows, plus guess_sender_identity for the 'whose machine is this' owner
    read -- then turns it into a bottom line, a plain explanation, and a short
    list of concrete steps with the 'if you recognise it / if you don't' fork
    that the vague 'check whether you run anything on Amazon' advice was
    missing. Returns {status, title, owner, plain, steps, checklist}."""
    from app.analysis import guess_sender_identity, get_ip_label

    domains = overview.get("domains", [])
    if not domains:
        return None
    ip = overview["source_ip"]
    owner = _owner_line(conn, overview)
    acted = overview.get("acted", 0)
    label = get_ip_label(conn, ip)
    dom_names = [d["domain_name"] for d in domains]
    dom_phrase = dom_names[0] if len(dom_names) == 1 else f"{len(dom_names)} of your domains"

    # "healthy" is decided by the overall pass rate + absence of a genuinely
    # problematic kind -- NOT by every domain being classified. A mostly-passing
    # sender that also touches a couple of low-volume domains (kind "unknown")
    # is still healthy; requiring all-"aligned" wrongly dumped it into the
    # "unrecognised" bucket.
    kinds = {d.get("kind") for d in domains}
    pass_rate = overview.get("pass_rate") or 0
    problematic = ("unverified" in kinds) or ("third_party" in kinds)
    healthy = pass_rate >= 0.95 and not problematic

    # A human already flagged this IP as suspicious somewhere -> that outranks
    # everything below, even a friendly label.
    primary = max(domains, key=lambda d: d["total"])
    did = conn.execute("SELECT id FROM domains WHERE name=?", (primary["domain_name"],)).fetchone()
    guess = guess_sender_identity(conn, did["id"], primary["domain_name"], ip, skip_lookup=True) if did else {}
    if guess.get("verdict") == "flagged":
        return {"status": "bad", "title": "You've flagged this one before as suspicious", "owner": owner,
                "plain": guess["summary"],
                "steps": ["You already decided this looks like spam/spoofing -- there's nothing to fix on your side; "
                          "DMARC blocks it. Leave it flagged.",
                          f"If it keeps showing up, that's fine -- it just means someone keeps trying and failing to send as {dom_phrase}."],
                "checklist": None}

    # The operator has named this IP -> they've decided it's a known sender of
    # theirs, so skip the "is this even yours?" mystery and speak to fixing it.
    if label:
        if healthy:
            return {"status": "ok", "title": f"\"{label}\" -- your own mail, working correctly", "owner": owner,
                    "plain": f"You've labelled this \"{label}\", and it's passing authentication for {dom_phrase}. Nothing to do.",
                    "steps": ["Nothing needed -- this is a healthy sender."], "checklist": None}
        if "forwarded" in kinds:
            owner = f"You've labelled this \"{label}\". " + owner  # fall through to the forwarding explanation
        else:
            return {"status": "warn", "title": f"\"{label}\" is failing authentication -- worth fixing", "owner": owner,
                    "plain": (f"You've marked this as your \"{label}\" sender, and it's failing authentication for "
                              f"{dom_phrase}. That means it isn't signing your mail correctly, so providers can doubt it "
                              f"or drop it into spam -- and it's what drags down your Gmail spam-rate reputation."),
                    "steps": [f"Ask whoever runs {label} to turn on DKIM signing (and SPF) for {dom_phrase} -- this is "
                              f"the single highest-impact fix for {label}. aikyam can walk them through it.",
                              "Once it's signing correctly, this flips to green on its own as new reports arrive.",
                              "Nothing here puts your real mail at risk today -- it's a deliverability fix, not an emergency."],
                    "checklist": None}

    if healthy:
        return {"status": "ok", "title": "This is your own mail, working correctly", "owner": owner,
                "plain": f"It passes authentication for {dom_phrase}, so mailbox providers trust it as genuinely yours.",
                "steps": ["Nothing to do -- this is a healthy sender.",
                          "If you want it to be recognisable at a glance, give it a label with the 🏷️ box below."],
                "checklist": None}

    if "forwarded" in kinds:
        return {"status": "info", "title": "This looks like mail forwarding -- nothing to fix", "owner": owner,
                "plain": (f"Some of this mail carries your real signature but arrives changed -- that's what forwarding "
                          f"does (an auto-forward rule, a mailing list, or an email-security relay passing your mail "
                          f"on). Forwarding breaks the security checks, so it shows as failing, but it is NOT someone "
                          f"forging you, and DMARC handles it safely."),
                "steps": ["You don't need to do anything -- this is normal and not a risk to your reputation.",
                          "If you'd rather stop seeing it, open its row in the Senders tab and set it to \"Ignore\".",
                          f"Any forged copy of your mail would still be blocked -- your protection isn't affected."],
                "checklist": None}

    if "third_party" in kinds:
        signed = next((i["auth_domain"] for i in overview.get("identities", []) if i["auth_domain"] not in dom_names), None)
        signed_note = f" as {signed}" if signed else " as a different domain"
        return {"status": "warn", "title": "A service sending under a different identity", "owner": owner,
                "plain": (f"This consistently authenticates{signed_note}, not as {dom_phrase}. That's usually a shared "
                          f"email service (like Mailgun or SES) that was set up under one domain but is being used to "
                          f"send as another -- so it can never fully pass for {dom_phrase} as it stands."),
                "steps": [f"If this is a service you use: ask whoever set it up to verify/sign it for {dom_phrase} too "
                          f"(aikyam can help).",
                          "If you don't recognise it: treat it as not yours -- see the checklist."],
                "checklist": _MINE_CHECKLIST}

    # Failing, no valid signature for anyone (unverified) -- or an unclassified
    # cloud sender. This is the 'is it mine or a spoofer?' case the user hit.
    spoof_line = (f" Your DMARC has already caught {acted} of these." if acted else "")
    return {"status": "warn", "title": "Unrecognised sender -- check whether it's yours", "owner": owner,
            "plain": (f"It's failing authentication for {dom_phrase} and carries no valid signature of yours. That is "
                      f"either your own server set up wrong (not signing your mail), or someone trying to send as "
                      f"you.{spoof_line}"),
            "steps": [f"Go through the checklist -- do you (or a vendor) run anything on this network that emails as {dom_phrase}?",
                      "If NONE of them fit → it's very likely not yours. There's nothing to fix: DMARC is already "
                      "stopping it. Set its row to \"Ignore\" (or Suspicious) in the Senders tab so you know you've reviewed it.",
                      "If one DOES fit → it's your own sender, just misconfigured. Ask whoever runs it to turn on "
                      "DKIM/SPF for your domain (aikyam can walk them through it)."],
            "checklist": _MINE_CHECKLIST}


def shared_cause_verdict(overview: dict):
    """(status, text) for the top of the source page: is this one machine
    doing the same wrong thing on several domains, which is the whole reason
    this page exists."""
    if not overview:
        return "muted", "No mail recorded from this source."

    failing = overview["failing_domain_count"]
    borrowed = [i for i in overview["identities"]
                if i["is_tracked"] and len(i["domains"]) > 1]

    if failing > 1 and borrowed:
        i = borrowed[0]
        return "bad", (
            f"⚠️ This one source is failing authentication on {failing} of your domains, and on each of "
            f"them it signs as {i['auth_domain']}. That is a single misconfiguration showing up "
            f"{failing} times -- fixing it where this source is configured fixes all of them at once."
        )
    if failing > 1:
        return "warn", (f"⚠️ This source is failing authentication on {failing} of your domains, so whatever "
                        f"is wrong is likely in how the source itself is set up rather than in any one "
                        f"domain's records.")
    if overview["acted"]:
        return "warn", (f"⚠️ {overview['acted']} message(s) from this source have already been sent to spam or "
                        f"blocked outright by the receiving providers.")
    if failing == 1:
        return "warn", "⚠️ This source is failing authentication on one of your domains."
    return "ok", f"✅ This source's mail is passing on all {overview['domain_count']} domain(s) it sends for."


def corroborated_identity_pairs(conn, high_vol: int, high_fail_rate: float, min_days: int) -> dict:
    """{(source_ip, auth_domain): [domain names where this is independently proven]}

    A pair earns a place here by clearing the borrowed-identity detector's own
    full evidence bar -- enough volume, mostly failing, sustained over days --
    on at least one domain. The detector then treats the same pair on another
    domain as the same already-established fact rather than a fresh hypothesis
    needing its own weeks of evidence.

    Deliberately built from the same thresholds the caller uses, passed in
    rather than re-read here, so corroboration can never be looser than the
    primary rule it is derived from.
    """
    rows = _rows(
        conn,
        """SELECT d.name AS domain_name, ks.source_ip,
                  ks.total_msgs, ks.pass_msgs, ks.first_seen, ks.last_seen
           FROM known_senders ks
           JOIN domains d ON d.id = ks.domain_id""",
    )

    from app.analysis import _ESP_DEFAULT_AUTH_DOMAINS, _passing_auth_domains, epoch_day

    pairs = {}
    for row in rows:
        total = row["total_msgs"] or 0
        if total < high_vol:
            continue
        if total and (row["pass_msgs"] / total) >= high_fail_rate:
            continue
        if (epoch_day(row["last_seen"]) - epoch_day(row["first_seen"])) < min_days:
            continue

        domain_id = conn.execute(
            "SELECT id FROM domains WHERE name = ?", (row["domain_name"],)
        ).fetchone()["id"]
        for auth_domain in _passing_auth_domains(conn, domain_id, row["source_ip"]):
            if auth_domain == row["domain_name"] or auth_domain in _ESP_DEFAULT_AUTH_DOMAINS:
                continue
            pairs.setdefault((row["source_ip"], auth_domain), []).append(row["domain_name"])
    return pairs
