"""
"Settings roadmap": for each email-authentication DNS setting on a domain,
what it is now, whether to keep it or change it (and when), and the evidence.

The pct ramp (app.analysis.recommend) already does this for ONE setting. The
operator asked for the same guidance across the rest, after meeting advice
elsewhere to "tighten SPF from ~all to -all after a few clean weeks" -- advice
that's wrong for a domain already enforcing DMARC: RFC 9989 s7.1 warns that
-all can get a message rejected "before any DMARC processing takes place", so
forwarded mail that would have passed on its DKIM signature is lost, and
those losses "will never appear in aggregate DMARC reports". So most rows here
deliberately say Keep: knowing what NOT to touch is half the value.

Every verdict is computed from data already collected (live DNS, SPF/DKIM
checks, report auth results, open action items, the pct watch) -- nothing
new is fetched at page load. Wording Jev-tested, see jev/DECISIONS_LOG.md.
"""

import datetime
import ipaddress
import json

from app.dns_check import parse_dmarc_tags
from app.pct_watch import latest_pct_watch, reporter_label

# Jev-tested 2026-11-10: this set read clear as-is (56%); "Keep / Change /
# Not yet / Optional / Can't tell yet" needed a plain-language pass.
VERDICT_LABELS = {"keep": "Leave as is", "change": "Change now", "wait": "Wait", "optional": "Nice to have",
                  "unknown": "Not enough data"}
ROADMAP_INTRO = ("For each email setting on this domain: leave it as is, change it now, or wait. Worked out from "
                 "this domain's own reports and rechecked every 6 hours. On a healthy domain most rows say Leave "
                 "as is.")
VERDICT_CLASS = {"keep": "ok", "change": "warn", "wait": "muted", "optional": "muted", "unknown": "muted"}
_SEVERITY = {"change": 4, "unknown": 3, "wait": 2, "optional": 1, "keep": 0}
_POLICY_RANK = {"none": 0, "quarantine": 1, "reject": 2}
LOOKBACK_DAYS = 90


def _row(setting, now, verdict, action, why):
    return {"setting": setting, "now": now, "verdict": verdict, "label": VERDICT_LABELS[verdict],
            "css": VERDICT_CLASS[verdict], "action": action, "why": why}


def _strip_domain(title, name):
    return title[len(name) + 2:] if title.startswith(name + ": ") else title


def _mail_volume(conn, domain_id):
    """(own messages -- ones that PASSED DMARC -- in the last LOOKBACK_DAYS,
    whether any report ever arrived). Passing only, so a parked domain that's
    merely being spoofed doesn't look like it sends mail."""
    since = int((datetime.datetime.utcnow() - datetime.timedelta(days=LOOKBACK_DAYS)).timestamp())
    recent = conn.execute(
        """SELECT COALESCE(SUM(rr.count), 0) n FROM report_records rr JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ?
             AND (rr.dkim_result = 'pass' OR rr.spf_result = 'pass')""",
        (domain_id, since),
    ).fetchone()["n"]
    ever = conn.execute("SELECT 1 FROM reports WHERE domain_id = ? LIMIT 1", (domain_id,)).fetchone()
    return recent, bool(ever)


def _subdomain_signers(conn, domain_id, name, mechanism):
    """[(auth_domain, messages)] for mail that passed `mechanism` as a SUBDOMAIN
    of this domain -- mail that only counts for DMARC under relaxed alignment."""
    since = int((datetime.datetime.utcnow() - datetime.timedelta(days=LOOKBACK_DAYS)).timestamp())
    return [(r["d"], r["n"]) for r in conn.execute(
        """SELECT lower(ra.domain) d, SUM(rr.count) n
           FROM record_auth_results ra JOIN report_records rr ON rr.id = ra.record_id
           JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ? AND ra.mechanism = ? AND ra.result = 'pass'
             AND lower(ra.domain) LIKE '%.' || lower(?)
           GROUP BY lower(ra.domain) ORDER BY n DESC""",
        (domain_id, since, mechanism, name),
    ).fetchall()]


def _pct_note(watch):
    """One sentence on whether receivers still honour pct, for a domain using a partial one."""
    judged = [w for w in watch if w["verdict"] in ("honours", "ignores")]
    if not judged:
        return ("The new DMARC standard (RFC 9989) drops pct, and there isn't enough recent data yet to tell "
                "whether Gmail and Microsoft still follow it.")
    ignoring = [reporter_label(w["reporter"]) for w in judged if w["verdict"] == "ignores"]
    honouring = [reporter_label(w["reporter"]) for w in judged if w["verdict"] == "honours"]
    checked = max(w["checked_at"] for w in judged)[:10]
    if ignoring:
        return (f"{' and '.join(ignoring)} already ignore{'s' if len(ignoring) == 1 else ''} pct and apply the "
                f"full policy (RFC 9989 dropped pct). Checked {checked}.")
    return (f"The new DMARC standard (RFC 9989) drops pct. {' and '.join(honouring)} still follow it as of "
            f"{checked}; you'll get an alert here if that changes.")


def _dmarc_row(name, tags, rec_row, watch):
    if tags is None:
        return _row("DMARC policy", "No record", "change", "Publish a DMARC record",
                    "Without one, anyone can send email pretending to be this domain, and receiving servers "
                    "have no instructions for what to do with it.")
    p = tags.get("p", "none")
    try:
        pct = int(tags.get("pct", "100"))
    except ValueError:
        pct = 100
    now = f"p={p}" + (f", pct={pct}" if pct != 100 else "") + ("; t=y" if tags.get("t") == "y" else "")
    note = f" {_pct_note(watch)}" if p != "none" and 1 <= pct <= 99 else ""
    if p == "reject" and pct == 100 and tags.get("t") != "y":
        return _row("DMARC policy", now, "keep", "",
                    "This is the strongest setting: mail that fails the checks is refused. Nothing further to do.")
    if not rec_row:
        return _row("DMARC policy", now, "unknown", "Wait for more reports",
                    "Not enough DMARC reports yet to say whether the next step is safe." + note)
    title = _strip_domain(rec_row["title"], name)
    ready = title.startswith(("safe to raise", "ready to"))
    return _row("DMARC policy", now, "change" if ready else "wait",
                title[0].upper() + title[1:], (rec_row["detail"] or "") + note)


def _spf_qualifier(record):
    for token in reversed((record or "").split()):
        t = token.lower()
        if t in ("all", "+all", "-all", "~all", "?all"):
            return "+all" if t == "all" else t
    return None


# Jev-tested 2026-11-10 (3 drafts): this framing moved from "too technical"
# to the best audience fit, 96% actionable. Its claim is kept to exactly what
# RFC 9989 s7.1 says: rejected early, never in aggregate reports.
_KEEP_SOFTFAIL_WHY = ("Switching to -all wouldn't stop any more fake emails, because your DMARC setting already does "
                      "that. It would only add a risk: some emails your readers forward could be refused, and it "
                      "wouldn't show up in your reports (RFC 9989 section 7.1).")
_DROP_HARDFAIL_WHY = ("-all doesn't stop any more fake emails than your DMARC setting already does. It only adds a "
                      "risk: some emails your readers forward can be refused, and it doesn't show up in your "
                      "reports (RFC 9989 section 7.1).")


def _envelope_own_mail(conn, domain_id, spf_domain):
    """Own mail (passed DMARC) in the last LOOKBACK_DAYS whose SPF was checked
    against `spf_domain` -- i.e. that used it as the bounce/envelope domain.
    SPF only ever judges that domain, so this is the mail -all could actually
    cost: folktaler.com's apex had -all with just 1 such message in 90 days,
    everything else going out via mails./send.mail. subdomains."""
    since = int((datetime.datetime.utcnow() - datetime.timedelta(days=LOOKBACK_DAYS)).timestamp())
    return conn.execute(
        """SELECT COALESCE(SUM(rr.count), 0) n
           FROM record_auth_results ra JOIN report_records rr ON rr.id = ra.record_id
           JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ? AND ra.mechanism = 'spf' AND lower(ra.domain) = lower(?)
             AND (rr.dkim_result = 'pass' OR rr.spf_result = 'pass')""",
        (domain_id, since, spf_domain),
    ).fetchone()["n"]


# Own emails per 90 days using a -all domain as their bounce domain before
# switching to ~all is "Change now" rather than "Nice to have".
HARDFAIL_CHANGE_MIN = 10


def _spf_verdict(qualifier, sends, has_reports, envelope_msgs):
    if qualifier is None:
        return "change", "End the record with ~all", "Without an ending, receivers get no guidance about servers you didn't list."
    if qualifier == "+all":
        return "change", "Replace +all with ~all", "+all tells receivers that any server in the world may send as you."
    if qualifier == "?all":
        return "change", "Replace ?all with ~all", "?all tells receivers to treat unlisted servers as neutral, which protects nothing."
    if qualifier == "-all":
        # Judged on mail that actually uses THIS domain as its bounce domain:
        # that's the only mail -all can cost.
        if envelope_msgs >= HARDFAIL_CHANGE_MIN:
            return "change", "Switch to ~all", _DROP_HARDFAIL_WHY
        if envelope_msgs:
            return "optional", "Switch to ~all", (f"Only {envelope_msgs} of your emails in 90 days used this as their "
                                                  f"bounce address, so the risk is small. " + _DROP_HARDFAIL_WHY)
        if has_reports:
            return "keep", "", ("None of your mail in 90 days used this as its bounce address, so -all costs nothing "
                                "here and locks it down.")
        return "unknown", "Keep -all only if this domain never sends email", "No DMARC reports yet, so there's no way to tell whether it sends mail. If it does, switch to ~all."
    # ~all
    if sends:
        return "keep", "", _KEEP_SOFTFAIL_WHY
    return "optional", "Lock down with v=spf1 -all, if this domain never sends email", (
        "No mail from this domain in 90 days. For a domain that never sends, -all is the right lock-down. "
        "If it still sends now and then, keep ~all.")


def _spf_row(conn, domain_id, name, spf_checks, sends, has_reports):
    own = [c for c in spf_checks if c["spf_domain"] == name or c["spf_domain"].endswith("." + name)]
    if not own:
        return _row("SPF ending", "Not checked yet", "unknown", "Wait for the next check", "")
    results = []
    for c in sorted(own, key=lambda c: (c["spf_domain"] != name, c["spf_domain"])):
        if c["status"] == "missing":
            if c["spf_domain"] == name:
                results.append((c["spf_domain"], "none", "change", "Publish an SPF record",
                                "Without one, receivers have nothing to check your sending servers against."))
            continue
        record = None
        try:
            record = json.loads(c["lookup_tree"] or "{}").get("record")
        except (ValueError, TypeError):
            pass
        if record is None:
            continue
        # A subdomain only gets an SPF check because mail was seen using it as
        # the envelope (bounce) domain, so it sends by definition.
        is_apex = c["spf_domain"] == name
        q = _spf_qualifier(record)
        verdict, action, why = _spf_verdict(q, sends if is_apex else True, has_reports,
                                            _envelope_own_mail(conn, domain_id, c["spf_domain"]) if q == "-all" else 0)
        results.append((c["spf_domain"], q or "no ending", verdict, action, why))
    if not results:
        return _row("SPF ending", "Couldn't read", "unknown", "Wait for the next check", "")
    now = " · ".join(f"{d}: {q}" for d, q, *_ in results)
    worst = max(results, key=lambda r: _SEVERITY[r[2]])
    domain, _q, verdict, action, why = worst
    if len(results) > 1:
        # Several SPF domains listed under "Now": say which one this is about.
        if action:
            action = f"{domain}: {action[0].lower()}{action[1:]}"
        why = f"{domain}: {why}"
    return _row("SPF ending", now, verdict, action, why)


def _alignment_row(conn, domain_id, name, tags):
    adkim = (tags or {}).get("adkim", "r")
    aspf = (tags or {}).get("aspf", "r")
    now = "relaxed" if adkim == "r" and aspf == "r" else f"adkim={adkim}, aspf={aspf}"
    dkim_subs = _subdomain_signers(conn, domain_id, name, "dkim")
    spf_subs = _subdomain_signers(conn, domain_id, name, "spf")
    broken = (dkim_subs if adkim == "s" else []) + (spf_subs if aspf == "s" else [])
    if broken:
        sub, n = max(broken, key=lambda s: s[1])
        return _row("Alignment", now, "change", "Switch back to relaxed (remove adkim=s / aspf=s)",
                    f"Mail authenticated as {sub} ({n} messages in 90 days) can't count for {name} under strict "
                    f"alignment, so it fails DMARC.")
    subs = dkim_subs or spf_subs
    if adkim == "r" and aspf == "r":
        if subs:
            sub, n = subs[0]
            # Jev-tested: leading with the concrete count read less technical than the mechanism.
            return _row("Alignment", now, "keep", "",
                        f"{n:,} of your emails in the last 90 days went out signed as {sub}. Relaxed lets those "
                        f"count as {name}; strict would make every one of them fail.")
        return _row("Alignment", now, "keep", "",
                    "Relaxed also accepts mail signed by your own subdomains. Strict adds little protection and "
                    "breaks the moment you add a newsletter or mail service on a subdomain.")
    return _row("Alignment", now, "keep", "Keep, but switch to relaxed before adding any subdomain sender",
                f"Strict works today because all your mail is signed as {name} itself.")


def _subdomain_policy_row(name, tags):
    if tags is None:
        return _row("Subdomain policy", "-", "unknown", "Publish a DMARC record first", "")
    p, sp = tags.get("p", "none"), tags.get("sp")
    if not sp:
        return _row("Subdomain policy", "not set (follows p=)", "keep", "",
                    f"Subdomains get the same policy as {name}, so a fake like billing.{name} is treated the same "
                    f"as a fake {name}.")
    if _POLICY_RANK.get(sp, 0) < _POLICY_RANK.get(p, 0):
        return _row("Subdomain policy", f"sp={sp}", "change", f"Remove sp= (or set sp={p})",
                    f"Subdomains are less protected than {name} itself, so a fake like billing.{name} gets weaker "
                    f"treatment. Only keep this if a subdomain deliberately sends mail that can't pass yet.")
    return _row("Subdomain policy", f"sp={sp}", "keep", "", "Subdomains are at least as protected as the main domain.")


def _dkim_row(conn, domain_id, name, dkim_checks, open_items):
    item = next((i for i in open_items if i["category"] in ("dkim_weak_key", "dkim_alignment_gap")), None)
    keys = [c for c in dkim_checks if c["status"] in ("ok", "weak")]
    now = (", ".join(f"{c['selector']} ({c['key_bits']}-bit)" if c["key_bits"] else c["selector"] for c in keys[:3])
           + (f" +{len(keys) - 3} more" if len(keys) > 3 else "")) if keys else "no keys checked"
    google = google_dkim_status(conn, domain_id, open_items)
    if google:
        lead = (f"{google['msgs']} emails your team sent from Google Workspace in the last 30 days were signed "
                f"with Google's generic key instead of {name}'s own, so they don't count toward your protection.")
        if google["key_published"]:
            return _row("DKIM", now, "change", "Switch on Google's DKIM signing (one click)",
                        f"{lead} The key is already set up; it just needs switching on. "
                        + GOOGLE_START_STEPS.format(name=name))
        return _row("DKIM", now, "change", "Set up Google's DKIM signing",
                    f"{lead} No key is set up yet. " + GOOGLE_SETUP_STEPS.format(name=name))
    if item:
        title = _strip_domain(item["title"], name)
        return _row("DKIM", now, "change", title[0].upper() + title[1:], "See the open action item for the fix.")
    if not keys:
        # compliance._run_dkim only checks selectors with real recent volume,
        # so on a quiet domain "no keys checked" doesn't mean "not signing".
        signing = _subdomain_signers(conn, domain_id, name, "dkim") or conn.execute(
            """SELECT 1 FROM record_auth_results ra JOIN report_records rr ON rr.id = ra.record_id
               JOIN reports r ON r.id = rr.report_id
               WHERE r.domain_id = ? AND ra.mechanism = 'dkim' AND ra.result = 'pass' AND lower(ra.domain) = lower(?)
                 AND r.date_begin >= ? LIMIT 1""",
            (domain_id, name, int((datetime.datetime.utcnow() - datetime.timedelta(days=LOOKBACK_DAYS)).timestamp())),
        ).fetchone()
        if signing:
            return _row("DKIM", "signing (key not checked)", "keep", "",
                        "Your mail is DKIM-signed with your own domain's key. Volume is too low for the tool to "
                        "check the key's strength yet.")
        return _row("DKIM", now, "unknown", "Wait for reports",
                    "No mail signed with this domain's own DKIM key in the last 90 days.")
    small = [c for c in keys if c["key_bits"] and c["key_bits"] < 2048]
    if small:
        return _row("DKIM", now, "optional", f"Upgrade {small[0]['selector']} to a 2048-bit key when convenient",
                    f"{small[0]['key_bits']}-bit meets Gmail's minimum today, but 2048-bit is the recommended size. "
                    f"Your email provider's DKIM settings can generate a new key.")
    return _row("DKIM", now, "keep", "",
                "Signing keys are 2048-bit and no DKIM problems were found in your reports.")


def _report_address_row(tags, report_auth):
    rua = (tags or {}).get("rua")
    if not rua:
        return _row("Report address", "none", "change", "Add a rua= address",
                    "Without it no DMARC reports are sent, so this tool can't see what happens to your mail.")
    missing = [r for r in report_auth if r["status"] == "missing"]
    if missing:
        dest = missing[0]["destination_domain"]
        return _row("Report address", rua, "change", f"Publish the permission record on {dest}",
                    "Reports to another domain need that domain's permission record (RFC 9990). Some receivers "
                    "already stop sending reports without it, with no error.")
    return _row("Report address", rua, "keep", "", "Reports are reaching this tool and the destination is authorised.")


def _mta_sts_row(mta_sts):
    if not mta_sts or mta_sts["mta_sts_status"] == "not_set_up":
        return _row("MTA-STS", "not set up", "optional", "Once everything above says Leave as is",
                    "Makes mail servers use encryption when delivering email TO you, so it can't be read in "
                    "transit. Newer and optional; most small domains don't have it yet.")
    if mta_sts["mta_sts_status"] == "broken":
        return _row("MTA-STS", "broken", "change", "Fix or remove the MTA-STS record", mta_sts["mta_sts_note"] or "")
    if mta_sts["mta_sts_mode"] == "enforce":
        return _row("MTA-STS", "enforce", "keep", "", "Incoming mail must be delivered encrypted.")
    return _row("MTA-STS", mta_sts["mta_sts_mode"] or "set up", "wait", "Move to mode: enforce after a few clean weeks",
                "In testing mode nothing is enforced yet. Switch once your TLS reports show no failures.")


# Jev-tested 2026-11-10: "generic key ... don't count toward your protection"
# read clearly less technical (48% vs 64% too-technical), 100% actionable.
GOOGLE_START_STEPS = ("In admin.google.com: Apps -> Google Workspace -> Gmail -> Authenticate email -> pick {name} -> "
                      "Start authentication.")
GOOGLE_SETUP_STEPS = ("In admin.google.com: Apps -> Google Workspace -> Gmail -> Authenticate email -> pick {name} -> "
                      "Generate new record (2048-bit) -> add the TXT record it shows to {name}'s DNS -> wait an hour "
                      "or so -> Start authentication.")


def google_dkim_status(conn, domain_id, open_items):
    """Latest compliance._run_google_dkim finding while its action item is
    still open (the item auto-dismisses once Google signs as the domain)."""
    if not any(i["category"] == "google_dkim_not_signing" for i in open_items):
        return None
    row = conn.execute(
        "SELECT * FROM google_dkim_checks WHERE domain_id=? ORDER BY checked_at DESC LIMIT 1", (domain_id,)
    ).fetchone()
    return {"msgs": row["default_signed_msgs"], "key_published": bool(row["key_published"])} if row else None


# DMARC tags with nothing left to do for these domains. pct is NOT here: the
# ramp still uses it, see app.pct_watch.
LEFTOVER_TAGS = {
    "ri": "ri= was dropped by the new DMARC standard (RFC 9989), so providers ignore it.",
    "rf": "rf= was dropped by the new DMARC standard (RFC 9989), so providers ignore it.",
    "ruf": ("ruf= asks providers to email you copies of individual failing messages. This tool never reads them, "
            "and most big providers stopped sending them for privacy reasons."),
    "fo": "fo= only controls those same failure copies.",
}


def leftover_tags(txt):
    return [k for k in LEFTOVER_TAGS if k in parse_dmarc_tags(txt or "")]


def cleaned_record(txt):
    """The same record with LEFTOVER_TAGS removed, original order kept."""
    keep = []
    for part in (txt or "").split(";"):
        part = part.strip()
        if part and part.split("=", 1)[0].strip().lower() not in LEFTOVER_TAGS:
            keep.append(part)
    return "; ".join(keep)


def _leftover_tags_row(txt):
    if txt is None:
        return None
    found = leftover_tags(txt)
    if not found:
        return _row("Leftover record tags", "none", "keep", "", "The record has no tags that nothing uses any more.")
    return _row("Leftover record tags", ", ".join(f"{t}=" for t in found), "optional",
                f"Remove {', '.join(f'{t}=' for t in found)} next time you edit the record",
                " ".join(LEFTOVER_TAGS[t] for t in found if t != "rf" or "ri" not in found)
                + f" Paste this instead: {cleaned_record(txt)}")


def _ip_ranges(node):
    out = []
    for token in (node.get("record") or "").split():
        t = token.lstrip("+~?-").lower()
        if t.startswith(("ip4:", "ip6:")):
            try:
                out.append(ipaddress.ip_network(t[4:], strict=False))
            except ValueError:
                pass
    for child in node.get("children") or []:
        out += _ip_ranges(child)
    return out


# Own mail using the main domain as its bounce address, per 90 days, before
# an SPF entry with no matching mail is judged unused rather than unknowable.
UNUSED_INCLUDE_MIN_MSGS = 20


def unused_spf_includes(conn, domain_id, name, spf_checks):
    """(own messages that used `name` as their bounce domain, [include
    domains none of that mail came from]). An include only matters for mail
    whose envelope is `name` itself, so that's the only mail looked at.
    Includes that resolve to no IP ranges (a:/mx:-only) can't be judged and
    are skipped. Found 2026-11-10: ilabindia.org still had
    include:spf.flockmail.com, 66 ranges, zero of 91 reports' mail from them."""
    apex = next((c for c in spf_checks if c["spf_domain"] == name and c.get("lookup_tree")), None)
    if not apex:
        return 0, []
    try:
        tree = json.loads(apex["lookup_tree"])
    except (ValueError, TypeError):
        return 0, []
    since = int((datetime.datetime.utcnow() - datetime.timedelta(days=LOOKBACK_DAYS)).timestamp())
    rows = conn.execute(
        """SELECT rr.source_ip ip, SUM(rr.count) n
           FROM record_auth_results ra JOIN report_records rr ON rr.id = ra.record_id
           JOIN reports r ON r.id = rr.report_id
           WHERE r.domain_id = ? AND r.date_begin >= ? AND ra.mechanism = 'spf' AND ra.result = 'pass'
             AND lower(ra.domain) = lower(?)
           GROUP BY rr.source_ip""",
        (domain_id, since, name),
    ).fetchall()
    total = sum(r["n"] for r in rows)
    addrs = []
    for r in rows:
        try:
            addrs.append(ipaddress.ip_address(r["ip"]))
        except ValueError:
            pass
    unused = []
    for child in tree.get("children") or []:
        nets = _ip_ranges(child)
        if not nets:
            continue
        if not any(a in net for a in addrs for net in nets if net.version == a.version):
            unused.append(child.get("domain"))
    return total, unused


def _spf_entries_row(conn, domain_id, name, spf_checks):
    total, unused = unused_spf_includes(conn, domain_id, name, spf_checks)
    if total < UNUSED_INCLUDE_MIN_MSGS:
        return None  # too little mail uses the main domain as its bounce address to judge anything
    if not unused:
        return _row("SPF entries", "all in use", "keep", "",
                    f"Every service listed in {name}'s SPF record sent real mail in the last 90 days.")
    listed = ", ".join(f"include:{u}" for u in unused)
    return _row("SPF entries", listed, "optional", f"Remove {listed} if you no longer send through it",
                f"None of the {total:,} emails that used {name} as their bounce address in 90 days came from "
                f"{'that service' if len(unused) == 1 else 'those services'}. Each entry lets anyone using that "
                f"service send as {name}, so drop ones you've stopped using. Check first: something that sends "
                f"only a few times a year might not show up in 90 days.")


def _link_tracking_row(conn, domain_id):
    rows = conn.execute(
        "SELECT * FROM mailgun_domain_settings WHERE domain_id=? ORDER BY mailgun_domain", (domain_id,)
    ).fetchall()
    if not rows:
        return None  # not sending through Mailgun
    http_click = [r["mailgun_domain"] for r in rows if r["web_scheme"] == "http" and r["click_tracking"]]
    http_only = [r["mailgun_domain"] for r in rows if r["web_scheme"] == "http" and not r["click_tracking"]]
    now = " · ".join(f"{r['mailgun_domain']}: {r['web_scheme'] or '?'}" for r in rows)
    if http_click:
        mg = http_click[0]
        return _row("Newsletter link tracking", now, "change", "Switch Mailgun link tracking to HTTPS",
                    f"Every tracked link in mail from {mg} goes through a plain-HTTP address (http://email.{mg}). "
                    f"Chrome now shows a warning before opening plain-HTTP links (from Chrome 154, October 2026), so "
                    f"readers may hit a security warning when they click. Steps: Mailgun -> Domain settings for {mg} "
                    f"-> tracking protocol -> HTTPS -> save. Mailgun sets up the certificate itself.")
    if http_only:
        return _row("Newsletter link tracking", now, "optional", "Switch to HTTPS before turning click tracking on",
                    "Click tracking is off, so no links are rewritten today. If you turn it on, switch the tracking "
                    "protocol to HTTPS in Mailgun's domain settings first.")
    return _row("Newsletter link tracking", now, "keep", "", "Tracked links use HTTPS.")


def incoming_mail_status(conn, domain_id, name):
    """(verdict, action, why, now, kind) for the domain's MX setup -- kind is
    none | split | backup | no_route | ok -- or None if not
    checked yet. Shared by the roadmap row and the overview panel so the two
    can't disagree."""
    from app.mx_check import (RELAY_PROVIDERS, latest_mx_check, mx_provider_label, sending_addresses,
                              split_summary)
    check = latest_mx_check(conn, domain_id)
    if not check:
        return None
    records = check["records"]
    keys = []
    for _p, _h, k in records:
        if k not in keys:
            keys.append(k)
    now = " · ".join(f"{p} {h}" for p, h, _k in records[:4]) + (f" +{len(records) - 4} more" if len(records) > 4 else "")
    if check["status"] == "none":
        return "keep", "", f"No MX records, so {name} doesn't receive email. Fine if nobody writes to it.", "none", "none"
    if check["status"] == "split":
        from app.mx_check import bounce_share
        main, others, rejecting, remove = split_summary(name, check)
        nowhere = ", which has nowhere to put it" if check.get("mailgun_route") == 0 else ""
        evidence = (f" We tested it: {main} accepts {check['probe_address']}, {' and '.join(rejecting)} bounces it. "
                    f"So you can expect {bounce_share(check, main)} emails sent to you to bounce back to the sender."
                    if rejecting else "")
        return ("change", f"Delete the {' and '.join(others)} MX records ({', '.join(remove)})",
                f"Email sent to {name} is split at random between {main}, where your mailboxes are, and "
                f"{' and '.join(others)}{nowhere}.{evidence} Deleting those records sends everything to {main}."
                + (" Your Mailgun sending keeps working." if "Mailgun" in others else ""), now, "split")
    if check["status"] == "backup":
        main, others, rejecting, remove = split_summary(name, check)
        bounce = (f", and it would bounce it (checked: it rejects {check['probe_address']})" if rejecting else "")
        return ("optional", f"Remove the backup MX records for {', '.join(remove)}",
                f"{main} handles your incoming mail. {' and '.join(others)} is listed only as a backup, so it gets "
                f"mail only if {main} is unreachable{bounce}.", now, "backup")
    # single provider
    key = keys[0]
    if key in RELAY_PROVIDERS and check.get("mailgun_route") == 0:
        senders = sending_addresses(conn, domain_id, name)
        if not senders:
            return ("keep", "", f"Only Mailgun receives mail here and nothing forwards it, which is normal for a "
                                f"domain used only for sending.", now, "ok")
        tested = check["probe"][0] if check.get("probe") else None
        if not (tested and tested.get("code") and 500 <= tested["code"] < 600):
            # Only suggest a route once it's PROVEN no inbox exists (operator's rule,
            # 2026-11-10): a probe that didn't run or didn't clearly reject proves nothing.
            return ("unknown", "Wait for the next check",
                    f"Only Mailgun receives mail for {name} and there's no Mailgun route for it, but the live "
                    f"check couldn't confirm whether mail to {senders[0]} is rejected, so no change is suggested yet.",
                    now, "unconfirmed")
        dest = _known_forward_destination(conn, domain_id, name)
        to = f"to {dest} (where this domain's reports already go)" if dest else "to an inbox you read"
        return ("change", f"Add a Mailgun route forwarding {name} mail {to}",
                f"No inbox exists for {name}: only Mailgun receives its mail, there's no Mailgun route for it, and "
                f"we checked live that Mailgun rejects {check['probe_address']}. So replies to your emails from "
                f"{', '.join(senders[:2])} bounce, unless those emails set a different Reply-To. Fix in Mailgun: "
                f"Receiving -> Create route -> match recipient \".*@{name}\" -> forward {to}.",
                now, "no_route")
    return "keep", "", f"All incoming mail goes to {mx_provider_label(key)}.", now, "ok"


def _known_forward_destination(conn, domain_id, name):
    """An inbox outside this domain that the tool KNOWS someone reads: the
    domain's report recipient. None if there isn't one -- never guessed."""
    row = conn.execute("SELECT recipient_email FROM domain_report_settings WHERE domain_id=?", (domain_id,)).fetchone()
    for addr in ((row["recipient_email"] if row else "") or "").split(","):
        addr = addr.strip().lower()
        if addr and "@" in addr and not addr.endswith("@" + name):
            return addr
    return None


def _incoming_mail_row(conn, domain_id, name):
    status = incoming_mail_status(conn, domain_id, name)
    if not status:
        return None
    verdict, action, why, now, _kind = status
    return _row("Incoming mail (MX)", now, verdict, action, why)


def shared_sending_domain(conn, domain_id, name):
    """(mailbox provider name) when Mailgun sends AS the root domain that
    also hosts real mailboxes -- the "cross" setup behind captains.ngo's MX
    mix-up -- else None."""
    from app.mx_check import RELAY_PROVIDERS, latest_mx_check, mx_provider_label
    if not conn.execute(
        "SELECT 1 FROM mailgun_domain_settings WHERE domain_id=? AND mailgun_domain=?", (domain_id, name)
    ).fetchone():
        return None
    check = latest_mx_check(conn, domain_id)
    if not check:
        return None
    mailbox = [k for _p, _h, k in sorted(check["records"]) if k not in RELAY_PROVIDERS]
    return mx_provider_label(mailbox[0]) if mailbox else None


def _sending_domain_row(conn, domain_id, name):
    provider = shared_sending_domain(conn, domain_id, name)
    if not provider:
        return None
    return _row("Newsletter sending domain", f"Mailgun sends as {name}", "optional",
                f"Move Mailgun sending to its own subdomain (e.g. mails.{name})",
                f"Mailgun sends as {name} itself, the same domain your {provider} mailboxes use, so the two share "
                f"one sending reputation: if Gmail slows down a bulk send (as it did for iLAB on 9 Nov), it is "
                f"slowing down {name}, staff email included. A subdomain keeps them apart and keeps Mailgun's "
                f"receiving records out of {name}'s MX. Set up the subdomain in Mailgun, switch whatever sends "
                f"through it, then remove include:mailgun.org from {name}'s SPF record.")


def settings_roadmap(conn, domain_id, name, latest_dns, rec_row, spf_checks, dkim_checks, report_auth, mta_sts,
                     open_items):
    """Rows for the domain page, most important setting first. Takes what
    domain_detail() has already loaded rather than re-querying it."""
    tags = parse_dmarc_tags(latest_dns["txt_value"]) if latest_dns and latest_dns["txt_value"] else None
    recent_msgs, has_reports = _mail_volume(conn, domain_id)
    watch = latest_pct_watch(conn)
    rows = [
        _dmarc_row(name, tags, rec_row, watch),
        _spf_row(conn, domain_id, name, spf_checks, recent_msgs > 0, has_reports),
        _spf_entries_row(conn, domain_id, name, spf_checks),
        _alignment_row(conn, domain_id, name, tags),
        _subdomain_policy_row(name, tags),
        _dkim_row(conn, domain_id, name, dkim_checks, open_items),
        _report_address_row(tags, report_auth),
        _leftover_tags_row(latest_dns["txt_value"] if latest_dns else None),
        _incoming_mail_row(conn, domain_id, name),
        _sending_domain_row(conn, domain_id, name),
        _link_tracking_row(conn, domain_id),
        _mta_sts_row(mta_sts),
    ]
    return [r for r in rows if r]
