"""Cross-references an email address against every "this has already caused
a real problem" source DMARCTool already collects -- SES/Mailgun suppression
lists (real ESP-reported bounces/complaints) and the Listmonk blocklist
(subscribers Listmonk itself already stopped mailing). Read-only everywhere;
nothing here ever writes back to Listmonk/Mailgun/SES.

Deliberately kept separate from email_verifier.py's live SMTP verdict:
"is this mail server currently willing to accept it" (SMTP, a snapshot) and
"has this address already caused a real delivery problem in the past"
(this, history) are two different questions. Disagreement between them is
itself a useful signal -- an address Listmonk already blocklisted for repeat
bounces that STILL passes a fresh SMTP check is stronger evidence for
removal than either fact alone (confirmed empirically once already: see
dmarctool_email_verifier.md, two SES-dead addresses that verified "valid"
again after mailbox state changed weeks later).
"""

import email.utils

_CHUNK = 900  # stay comfortably under SQLite's default 999-variable-per-query limit


def _bare_email(raw: str) -> str:
    """Some ses_suppressions rows carry a full 'Display Name <email@x.com>'
    string (straight from the original send's To: header), not a bare
    address -- matched naively, that never lines up with a plain address
    typed into the checker or found in a CSV. Same normalization already
    used at suppression-CSV-export time elsewhere in this project
    (web.py's _clean_email), duplicated here rather than importing web.py
    (which imports this module)."""
    return (email.utils.parseaddr(raw or "")[1] or raw or "").strip().lower()


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def known_bad_map(conn, emails):
    """{email: [source, ...]} for a whole list of addresses at once. Each
    address not already known-bad in any tracked source maps to []. `emails`
    may contain mixed case / duplicates; keys in the result are lowercased.

    ses_suppressions.email needs address-parsing before it can be matched
    (see _bare_email) so it's loaded and normalized in full rather than
    pushed into a WHERE IN (...) -- cheap at this table's real size (low
    thousands), and correct instead of silently missing every row that
    carries a display name."""
    emails = list({(e or "").strip().lower() for e in emails if e and e.strip()})
    if not emails:
        return {}

    out = {e: [] for e in emails}
    wanted = set(emails)

    ses_best = {}  # bare email -> best (kind, bounce_type) row, Permanent preferred over Transient/Undetermined
    for row in conn.execute("SELECT email, kind, bounce_type FROM ses_suppressions"):
        e = _bare_email(row["email"])
        if e not in wanted:
            continue
        if e not in ses_best or (row["bounce_type"] == "Permanent" and ses_best[e]["bounce_type"] != "Permanent"):
            ses_best[e] = row
    for e, row in ses_best.items():
        detail = row["bounce_type"] or row["kind"]
        out[e].append(f"SES suppression ({row['kind']}, {detail})")

    for chunk in _chunks(emails, _CHUNK):
        placeholders = ",".join("?" * len(chunk))

        for row in conn.execute(
            f"""SELECT email, kind FROM mailgun_suppressions
                WHERE lower(email) IN ({placeholders})
                GROUP BY lower(email)""",
            chunk,
        ):
            e = row["email"].strip().lower()
            if e in out:
                out[e].append(f"Mailgun suppression ({row['kind']})")

        for row in conn.execute(
            f"SELECT email FROM listmonk_blocklist WHERE email IN ({placeholders})", chunk
        ):
            e = row["email"].strip().lower()
            if e in out:
                out[e].append("Listmonk blocklist")

    return out


def known_bad_sources(conn, email: str):
    """Single-address convenience wrapper around known_bad_map."""
    return known_bad_map(conn, [email]).get((email or "").strip().lower(), [])


def known_bad_counts(conn):
    """{"ses": n, "mailgun": n, "listmonk": n} -- distinct-address counts per
    source, for the summary panel on the email checker page."""
    ses = len({_bare_email(r["email"]) for r in conn.execute("SELECT email FROM ses_suppressions")})
    mailgun = conn.execute("SELECT COUNT(DISTINCT lower(email)) c FROM mailgun_suppressions").fetchone()["c"]
    listmonk = conn.execute("SELECT COUNT(*) c FROM listmonk_blocklist").fetchone()["c"]
    listmonk_synced_at = conn.execute("SELECT MAX(synced_at) m FROM listmonk_blocklist").fetchone()["m"]
    return {"ses": ses, "mailgun": mailgun, "listmonk": listmonk, "listmonk_synced_at": listmonk_synced_at}
