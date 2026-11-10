"""
Where does each domain's INCOMING mail go -- and does every server it can
go to actually accept it?

Built 2026-11-10 after finding captains.ngo's MX listing Google Workspace and
Mailgun at the SAME priority (10) -- left over from an old setup where
Mailgun's optional "receiving" records were added to the root domain that
Google Workspace also uses. Sending servers pick at random among equal-
priority MX hosts (RFC 5321 s5.1) and don't try another after a permanent
rejection, and a live RCPT probe showed Google accepting info@captains.ngo
while mxa.mailgun.org answered "550 5.0.1 Recipient rejected" (the Mailgun
account has no routes, so it has nowhere to deliver). Roughly two in three
emails to captains.ngo were bouncing, and nothing in this tool looked at MX
at all.

Three shapes:
  * single provider             -- fine.
  * split  -- the most-preferred priority holds hosts from two providers:
              mail is divided at random between them. Raises `mx_split`.
  * backup -- another provider only at a less-preferred priority: reached
              only if the main one is unreachable (pattic.org's Mailgun
              records behind Google). Roadmap/overview reminder only.

When providers are mixed, the verdict is backed by evidence where possible:
a known-real mailbox (an address the tool has seen this domain send from or
deliver reports to) is checked against the main mailbox provider, then
against one host of every other provider. Same probe as the email verifier
(EHLO/MAIL/RCPT then QUIT -- no message is ever sent), only for mixed
domains, at most once per compliance_recheck_hours.
"""

import datetime
import json

from app.analysis import ensure_default_settings, upsert_system_action
from app.config import get_secret
from app.email_verifier import mx_lookup, smtp_probe

# (key, display name, host suffixes). Unlisted hosts group by their own
# last two labels, so two unknown providers still read as two.
MX_PROVIDERS = [
    ("google", "Google Workspace", ("google.com", "googlemail.com")),
    ("microsoft", "Microsoft 365", ("mail.protection.outlook.com", "outlook.com")),
    ("zoho", "Zoho Mail", ("zoho.com", "zoho.in", "zoho.eu", "zohomail.com")),
    ("mailgun", "Mailgun", ("mailgun.org",)),
    ("ses", "Amazon SES", ("amazonaws.com",)),
    ("cloudflare", "Cloudflare Email Routing", ("mx.cloudflare.net",)),
    ("proton", "Proton Mail", ("protonmail.ch",)),
    ("hostinger", "Hostinger", ("hostinger.com",)),
]
# Providers that only RELAY inbound mail onward via rules you configure
# (Mailgun routes, SES receipt rules) -- they hold no mailboxes themselves,
# so they're never picked as "the" mailbox provider to test against.
RELAY_PROVIDERS = {"mailgun", "ses"}


def provider_of(host):
    h = (host or "").lower().rstrip(".")
    for key, name, suffixes in MX_PROVIDERS:
        if any(h == s or h.endswith("." + s) for s in suffixes):
            return key, name
    tail = ".".join(h.split(".")[-2:])
    return tail, tail


def classify_mx(records):
    """'none' | 'single' | 'split' | 'backup' -- see the module docstring."""
    if not records:
        return "none"
    providers = {provider_of(h)[0] for _, h in records}
    if len(providers) == 1:
        return "single"
    top = min(p for p, _ in records)
    return "split" if len({provider_of(h)[0] for p, h in records if p == top}) > 1 else "backup"


def _candidate_addresses(conn, domain_id, name):
    """Addresses at this domain the tool already knows are used for real:
    report recipients and observed sending identities, then postmaster@."""
    found = []

    def add(addr):
        addr = (addr or "").strip().lower()
        if addr.endswith("@" + name) and addr not in found:
            found.append(addr)

    for (emails,) in conn.execute(
        "SELECT recipient_email FROM domain_report_settings WHERE domain_id=?", (domain_id,)
    ).fetchall():
        for e in (emails or "").split(","):
            add(e)
    for sql in ("SELECT DISTINCT from_address FROM mailgun_campaigns WHERE domain_id=?",
                "SELECT DISTINCT from_address FROM mailgun_identity_stats WHERE domain_id=?",
                "SELECT DISTINCT from_address FROM ses_campaigns WHERE domain_id=?"):
        try:
            for (a,) in conn.execute(sql, (domain_id,)).fetchall():
                add(a)
        except Exception:
            pass  # a table without that column on an old install mustn't stop the check
    add(f"postmaster@{name}")
    return found


def _probe(conn, domain_id, name, records, settings):
    """(known_real_address or None, [{provider, host, code, message}])."""
    helo, mail_from = settings.get("email_verifier_helo_name"), settings.get("email_verifier_from_address")
    if not helo or not mail_from:
        return None, []
    by_provider = {}
    for prio, host in records:
        by_provider.setdefault(provider_of(host)[0], (prio, host))
    mailbox = sorted(
        ((prio, key, host) for key, (prio, host) in by_provider.items() if key not in RELAY_PROVIDERS),
        key=lambda t: t[0],
    )
    if not mailbox:
        return None, []
    _, main_key, main_host = mailbox[0]
    known = None
    results = []
    for addr in _candidate_addresses(conn, domain_id, name)[:3]:
        code, message, err = smtp_probe(main_host, helo, mail_from, addr, timeout=15)
        if code and 200 <= code < 300:
            known = addr
            results.append({"provider": main_key, "host": main_host, "code": code, "message": message[:160]})
            break
    if not known:
        return None, []
    for key, (prio, host) in by_provider.items():
        if key == main_key:
            continue
        code, message, err = smtp_probe(host, helo, mail_from, known, timeout=15)
        results.append({"provider": key, "host": host, "code": code, "message": (message or err or "")[:160]})
    return known, results


def _mailgun_routes():
    """Expressions of every inbound route on the Mailgun account, or None if
    it can't be read. Without a matching route Mailgun's MX can't deliver an
    incoming message anywhere."""
    key = get_secret("MAILGUN_API_KEY")
    if not key:
        return None
    from app.mailgun import _get
    data, err = _get("https://api.mailgun.net/v3/routes?limit=1000", key)
    if err or data is None:
        return None
    return [(r.get("expression") or "").lower() for r in (data.get("items") or [])]


def sending_addresses(conn, domain_id, name):
    """From addresses at this domain seen in its own outgoing mail -- the
    addresses people reply to."""
    found = []
    for sql in ("SELECT DISTINCT from_address FROM mailgun_campaigns WHERE domain_id=?",
                "SELECT DISTINCT from_address FROM mailgun_identity_stats WHERE domain_id=?",
                "SELECT DISTINCT from_address FROM ses_campaigns WHERE domain_id=?"):
        try:
            for (a,) in conn.execute(sql, (domain_id,)).fetchall():
                a = (a or "").strip().lower()
                if a.endswith("@" + name) and a not in found:
                    found.append(a)
        except Exception:
            pass
    return found


def mx_provider_label(key):
    return next((n for k, n, _ in MX_PROVIDERS if k == key), key)


def latest_mx_check(conn, domain_id):
    row = conn.execute(
        "SELECT * FROM mx_checks WHERE domain_id=? ORDER BY checked_at DESC LIMIT 1", (domain_id,)
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["records"] = json.loads(row["records"] or "[]")
    out["probe"] = json.loads(row["probe"] or "[]")
    return out


def provider_names(records):
    names = []
    for _p, _h, key in records:
        name = next((n for k, n, _ in MX_PROVIDERS if k == key), key)
        if name not in names:
            names.append(name)
    return names


def _stale(conn, domain_id, hours):
    row = conn.execute("SELECT MAX(checked_at) t FROM mx_checks WHERE domain_id=?", (domain_id,)).fetchone()
    if not row["t"]:
        return True
    return datetime.datetime.strptime(row["t"], "%Y-%m-%d %H:%M:%S") < (
        datetime.datetime.utcnow() - datetime.timedelta(hours=hours))


def run_mx_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    hours = int(settings["compliance_recheck_hours"])
    routes = None
    routes_loaded = False
    for d in conn.execute("SELECT id, name FROM domains ORDER BY name").fetchall():
        domain_id, name = d["id"], d["name"]
        if _stale(conn, domain_id, hours):
            records = mx_lookup(name)
            if records is None:
                continue  # lookup failed -- try again next cycle, don't record a false "no MX"
            status = classify_mx(records)
            tagged = [[p, h, provider_of(h)[0]] for p, h in records]
            known, probe = (None, [])
            has_route = None
            if status in ("split", "backup"):
                known, probe = _probe(conn, domain_id, name, records, settings)
            if any(t[2] == "mailgun" for t in tagged):
                # Mailgun's MX accepts nothing without an inbound route --
                # confirmed live, it answers "550 5.0.1 Recipient rejected"
                # for every address (captains.ngo, thudippu.in).
                if not routes_loaded:
                    routes, routes_loaded = _mailgun_routes(), True
                if routes is not None:
                    has_route = int(any(name in r or "catch_all" in r for r in routes))
            conn.execute(
                """INSERT INTO mx_checks (domain_id, records, status, probe_address, probe, mailgun_route)
                   VALUES (?,?,?,?,?,?)""",
                (domain_id, json.dumps(tagged), status, known, json.dumps(probe), has_route),
            )
            conn.commit()
            if verbose:
                print(f"[mx] {name}: {status} {[t[2] for t in tagged]} probe={probe}")
        check = latest_mx_check(conn, domain_id)
        if check and check["status"] == "split":
            _raise_split_item(conn, domain_id, name, check)
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='mx_split' AND status='open'""",
                (domain_id,),
            )
    conn.commit()


def split_summary(name, check):
    """(main provider name, [other provider names], rejecting provider names,
    hosts to remove) for a mixed-provider check, from its probe evidence."""
    records = check["records"]
    names = {k: n for k, n, _ in MX_PROVIDERS}
    accepted = [p for p in check["probe"] if p["code"] and 200 <= p["code"] < 300]
    main_key = accepted[0]["provider"] if accepted else next(
        (k for _p, _h, k in sorted(records) if k not in RELAY_PROVIDERS), records[0][2])
    rejecting = [p["provider"] for p in check["probe"] if p["code"] and 500 <= p["code"] < 600]
    others = []
    for _p, _h, k in records:
        if k != main_key and k not in others:
            others.append(k)
    remove = [h for _p, h, k in records if k != main_key]
    return (names.get(main_key, main_key), [names.get(k, k) for k in others],
            [names.get(k, k) for k in rejecting], remove)


def bounce_share(check, main_name):
    """How much of the incoming mail an equal-priority split sends AWAY from
    the main provider, as plain words: senders pick uniformly among the
    most-preferred hosts (RFC 5321 s5.1), so it's the other providers' share
    of those hosts. An expectation, not a measurement -- worded as one."""
    names = {k: n for k, n, _ in MX_PROVIDERS}
    top = min(p for p, _h, _k in check["records"])
    tier = [k for p, _h, k in check["records"] if p == top]
    away = sum(1 for k in tier if names.get(k, k) != main_name)
    return "about half" if away * 2 == len(tier) else f"about {away} in {len(tier)}"


def _raise_split_item(conn, domain_id, name, check):
    main, others, rejecting, remove = split_summary(name, check)
    other = " and ".join(others)
    nowhere = ", which has nowhere to put it" if check.get("mailgun_route") == 0 else ""
    if rejecting:
        title = f"{name}: incoming mail is split between {main} and {other} -- some of it bounces"
        evidence = (f" We tested it: {main} accepts {check['probe_address']}, {' and '.join(rejecting)} bounces "
                    f"it. So you can expect {bounce_share(check, main)} emails sent to you to bounce back to "
                    f"the sender.")
    else:
        title = f"{name}: incoming mail is split between {main} and {other}"
        evidence = (" Couldn't test which server rejects mail, so check both." if not check["probe"] else
                    f" We tested it: both accepted {check['probe_address']}, so your mail may be landing in "
                    f"two different places.")
    # Jev-tested 2026-11-10: this plain framing cut "too technical" 77% -> 49%;
    # "you can expect" keeps the bounce share an expectation, not a measurement.
    upsert_system_action(
        conn, domain_id, "mx_split", None, title,
        f"Email sent to {name} is split at random between {main}, where your mailboxes are, and "
        f"{other}{nowhere}.{evidence} Fix: delete the MX records for {', '.join(remove)} and keep only "
        f"{main}'s." + (" Your Mailgun sending keeps working." if "Mailgun" in others else ""),
    )
