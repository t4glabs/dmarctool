"""
Self-hosted email-address verifier -- syntax + MX + disposable-domain +
real SMTP RCPT-TO probing + catch-all detection, the same pipeline every
commercial verifier (MillionVerifier, ZeroBounce, etc.) runs, with the same
honest ceiling they have: this rules OUT "definitely doesn't exist" and rules
IN "the mail server is currently willing to accept this," it can't promise
zero future bounces (mailbox-full is a state, not a property; big providers
sometimes accept-all at RCPT stage and bounce later; greylisting causes
false negatives on a first attempt).

No third-party API, no subscription, stdlib only:
  - MX lookup via `dig` (subprocess) -- same pattern as every other DNS check
    in this project (dns_check.py, compliance.py, mta_sts.py), not dnspython.
  - The SMTP probe itself uses stdlib smtplib's low-level mail()/rcpt() calls
    directly -- NEVER .sendmail()/.data() -- so no message content is ever
    transmitted; the whole "probe" is EHLO, MAIL FROM, RCPT TO, then QUIT.

Verdicts (the same 4-tier vocabulary every commercial tool uses, so it reads
familiarly): valid / invalid / risky / unknown.
  - invalid: bad syntax, no MX record, or the mail server said outright "no
    such user" (550/551/553).
  - risky: the domain is catch-all (accepts anything, so an individual
    mailbox can't be confirmed), a known disposable-email provider, or the
    server gave a temporary/greylist response (4xx) -- genuinely ambiguous,
    not a hard no.
  - valid: MX exists, not catch-all, not disposable, and the server said
    "yes" (250/251) to this specific mailbox.
  - unknown: couldn't complete the check at all (connection refused, timed
    out, or the from-address/HELO name isn't configured yet in Settings).

On-disk cache (email_verifications table) is a read-through cache keyed on
the address -- re-verifying something already checked recently is instant
and doesn't hit the network again.
"""

import re
import smtplib
import socket
import subprocess
import time

_SYNTAX_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._%+\-]*[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$")

# Curated, not exhaustive -- same "good enough, not a maintained live feed"
# tradeoff as every other static list in this project (_ESP_PTR_PATTERNS,
# _CONSUMER_ISP_KEYWORDS in analysis.py). Covers the disposable providers
# actually common enough to show up on a real newsletter signup form.
DISPOSABLE_DOMAINS = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamail.info", "guerrillamail.biz",
    "guerrillamail.de", "guerrillamail.net", "guerrillamail.org", "guerrillamailblock.com",
    "sharklasers.com", "10minutemail.com", "10minutemail.net", "10minutemail.co.za",
    "temp-mail.org", "tempmail.com", "tempmail.net", "tempinbox.com", "throwawaymail.com",
    "yopmail.com", "yopmail.net", "yopmail.fr", "trashmail.com", "trash-mail.com",
    "getnada.com", "dispostable.com", "fakeinbox.com", "mailnesia.com", "mintemail.com",
    "mytemp.email", "emailondeck.com", "maildrop.cc", "moakt.cc", "mailcatch.com",
    "spamgourmet.com", "mailexpire.com", "mohmal.com", "burnermail.io", "temp-mail.io",
    "discard.email", "discardmail.com", "mailsac.com", "inboxbear.com", "tempr.email",
    "tmpmail.org", "tmpmail.net", "tmail.ws", "fakemailgenerator.com", "emailfake.com",
    "correotemporal.org", "instantemailaddress.com", "mailtemp.info", "spam4.me",
    "no-spam.ws", "trbvm.com", "deadaddress.com", "spambox.us", "example.com",
})

_TEMP_SMTP_CODES = {421, 450, 451, 452}
_HARD_REJECT_CODES = {550, 551, 553}
_MAILBOX_FULL_CODES = {552}


def check_syntax(email: str):
    """(ok, reason). Cheapest, instant, free -- checked first so nothing
    that's already obviously malformed burns a DNS lookup or an SMTP round
    trip."""
    email = (email or "").strip()
    if not email or "@" not in email:
        return False, "Missing @ or empty"
    if not _SYNTAX_RE.match(email):
        return False, "Doesn't look like a valid email address (bad syntax)"
    return True, None


def mx_lookup(domain: str, timeout: float = 5.0):
    """[(priority, host), ...] sorted by priority (lowest/most-preferred
    first), or [] if the domain has no MX record at all (can't receive mail),
    or None if the lookup itself failed (network issue, not a real answer --
    same "None means try again" distinction every dig-based check in this
    project already makes)."""
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=2", "MX", domain],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    records = []
    for line in out.stdout.strip().splitlines():
        parts = line.strip().rstrip(".").split()
        if len(parts) == 2:
            try:
                records.append((int(parts[0]), parts[1]))
            except ValueError:
                continue
    records.sort(key=lambda r: r[0])
    return records


def smtp_probe(mx_host: str, helo_name: str, mail_from: str, rcpt_to: str, timeout: float = 10.0):
    """The actual "does this mailbox exist" check. Opens a real SMTP
    connection, walks EHLO -> MAIL FROM -> RCPT TO, reads the response code,
    and disconnects -- .sendmail()/.data() are NEVER called, so no message
    content is ever transmitted; nothing is actually sent to anyone.
    Returns (code: int|None, message: str, error: str|None). code is the
    RCPT TO response code (the one that matters); None with a filled `error`
    means the probe itself couldn't complete."""
    try:
        smtp = smtplib.SMTP(timeout=timeout)
        smtp.connect(mx_host, 25)
        smtp.ehlo(helo_name)
        smtp.mail(mail_from)
        code, message = smtp.rcpt(rcpt_to)
        try:
            smtp.quit()
        except Exception:
            pass
        return code, message.decode(errors="replace") if isinstance(message, bytes) else str(message), None
    except (smtplib.SMTPException, socket.error, OSError) as e:
        return None, "", str(e)


def _random_local_part():
    import random
    import string
    return "verify-probe-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


def is_catchall(mx_host: str, domain: str, helo_name: str, mail_from: str, timeout: float = 10.0):
    """True if this domain's mail server accepts RCPT TO for ANY address
    (a near-certainly-fake, randomly generated local part) -- meaning
    individual mailbox verification on this domain is meaningless, since
    "yes" and "no" both come back as "yes". None if the probe itself
    couldn't complete (treat as unknown, not as a real answer)."""
    fake = f"{_random_local_part()}@{domain}"
    code, _, error = smtp_probe(mx_host, helo_name, mail_from, fake, timeout=timeout)
    if error:
        return None
    return code in (250, 251)


def _cached_is_catchall(conn, mx_host: str, domain: str, helo_name: str, mail_from: str,
                         timeout: float, use_cache: bool, cache_hours: int):
    """Same read-through cache pattern as verify_email itself, but keyed on
    the DOMAIN, not the address -- catch-all is a property of the mail
    server, so every address on that domain (within one batch, or across
    separate checks over time) reuses one probe instead of repeating it.
    Without this, a batch of addresses on the same catch-all domain hammers
    that one server with a redundant fake-address probe per row. Returns
    (is_catchall, was_fresh_probe) -- callers only need to pace themselves
    (time.sleep) after a probe that actually hit the network."""
    if use_cache:
        row = conn.execute(
            "SELECT is_catchall FROM email_domain_catchall WHERE domain=? AND checked_at >= datetime('now', ?)",
            (domain, f"-{cache_hours} hours"),
        ).fetchone()
        if row is not None:
            return bool(row["is_catchall"]), False

    result = is_catchall(mx_host, domain, helo_name, mail_from, timeout=timeout)
    if result is not None:
        conn.execute(
            """INSERT INTO email_domain_catchall (domain, is_catchall, checked_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(domain) DO UPDATE SET
                 is_catchall=excluded.is_catchall, checked_at=excluded.checked_at""",
            (domain, int(result)),
        )
        conn.commit()
    return result, True


def verify_email(conn, email: str, from_address: str, helo_name: str,
                  timeout: float = 10.0, use_cache: bool = True, cache_hours: int = 24 * 7):
    """The full pipeline for one address: syntax -> MX -> disposable ->
    catch-all -> SMTP probe. Returns a dict with verdict/reason/detail, and
    upserts it into the email_verifications cache table. `from_address` and
    `helo_name` MUST be configured in Settings first (a real domain you
    control) -- most receiving servers don't hard-require this to be
    deliverable, but a well-formed, plausible sender is what every real
    verifier tool uses; an empty/placeholder value gets rejected by some
    servers outright."""
    email = (email or "").strip().lower()

    if use_cache:
        cached = conn.execute(
            "SELECT * FROM email_verifications WHERE email=? AND checked_at >= datetime('now', ?)",
            (email, f"-{cache_hours} hours"),
        ).fetchone()
        if cached:
            return dict(cached)

    def _store(verdict, reason, mx_host=None, is_disposable=False, is_catchall_=False, smtp_code=None):
        conn.execute(
            """INSERT INTO email_verifications
               (email, verdict, reason, mx_host, is_disposable, is_catchall, smtp_code, checked_at)
               VALUES (?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(email) DO UPDATE SET
                 verdict=excluded.verdict, reason=excluded.reason, mx_host=excluded.mx_host,
                 is_disposable=excluded.is_disposable, is_catchall=excluded.is_catchall,
                 smtp_code=excluded.smtp_code, checked_at=excluded.checked_at""",
            (email, verdict, reason, mx_host, int(is_disposable), int(is_catchall_), smtp_code),
        )
        conn.commit()
        return conn.execute("SELECT * FROM email_verifications WHERE email=?", (email,)).fetchone()

    ok, reason = check_syntax(email)
    if not ok:
        return dict(_store("invalid", reason))

    domain = email.rsplit("@", 1)[1]
    is_disp = domain in DISPOSABLE_DOMAINS

    if not from_address or not helo_name:
        return dict(_store(
            "unknown", "Verifier isn't configured yet -- set a from-address and HELO name in Settings first.",
            is_disposable=is_disp,
        ))

    mx_records = mx_lookup(domain)
    if mx_records is None:
        return dict(_store("unknown", "MX lookup failed (network/DNS error) -- try again", is_disposable=is_disp))
    if not mx_records:
        return dict(_store("invalid", f"{domain} has no MX record -- it can't receive mail at all", is_disposable=is_disp))

    mx_host = mx_records[0][1]

    catchall, catchall_was_fresh_probe = _cached_is_catchall(
        conn, mx_host, domain, helo_name, from_address,
        timeout=timeout, use_cache=use_cache, cache_hours=cache_hours)
    if catchall_was_fresh_probe:
        time.sleep(0.3)  # a beat between probes to the same server, not back-to-back (skipped on a cache hit)

    code, message, error = smtp_probe(mx_host, helo_name, from_address, email, timeout=timeout)

    if error:
        return dict(_store("unknown", f"Couldn't complete the check: {error}",
                            mx_host=mx_host, is_disposable=is_disp, is_catchall_=bool(catchall)))

    if catchall:
        return dict(_store(
            "risky", f"{domain} accepts mail for ANY address (catch-all) -- can't confirm this specific mailbox",
            mx_host=mx_host, is_disposable=is_disp, is_catchall_=True, smtp_code=code,
        ))
    if is_disp:
        return dict(_store(
            "risky", f"{domain} is a known disposable/throwaway email provider",
            mx_host=mx_host, is_disposable=True, smtp_code=code,
        ))
    if code in (250, 251):
        return dict(_store("valid", f"Mail server accepted it (SMTP {code})", mx_host=mx_host, smtp_code=code))
    if code in _HARD_REJECT_CODES:
        return dict(_store("invalid", f"Mail server said no such user (SMTP {code}: {message.strip()})",
                            mx_host=mx_host, smtp_code=code))
    if code in _MAILBOX_FULL_CODES:
        return dict(_store("risky", f"Mailbox may be full/over quota (SMTP {code}: {message.strip()})",
                            mx_host=mx_host, smtp_code=code))
    if code in _TEMP_SMTP_CODES:
        return dict(_store("risky", f"Temporary rejection, possibly greylisting (SMTP {code}: {message.strip()}) -- try again later",
                            mx_host=mx_host, smtp_code=code))
    return dict(_store("unknown", f"Unexpected response (SMTP {code}: {message.strip()})", mx_host=mx_host, smtp_code=code))
