"""
Domains registered to look like yours.

DMARC stops someone forging *your* domain. It does nothing about someone
registering a domain that merely *reads* like yours -- aikyamfe11ows.org,
tinybridge.com -- and emailing your donors from it, perfectly authenticated
under their own SPF and DKIM. For the orgs this tool serves, that is the
actual exposure: the whole reason a custom domain earns a funder's trust is
also what makes a near-copy of it dangerous.

Every commercial tool sells this (PowerDMARC calls it "Lookalike domain").
None of the mechanics need an account: generate the realistic near-misses,
ask DNS whether each one exists, and for the ones that do, ask whether it is
set up to handle mail.

## Why this reports a watchlist and not a pile of alerts

The first real run found 47 registered look-alikes across 18 domains, 23 of
them with working MX. Almost all are innocent -- `constitution.org` is a
famous site that happens to be the correct spelling of a domain here;
`captains.in`, `makestories.com` and `arpo.co` are unrelated businesses that
own a generic word. Raising an action item per candidate would have produced
exactly the kind of filler this tool has been pruned of elsewhere.

So: everything registered is stored and browsable, and **at most one action
item per domain** summarises the subset that can actually send mail. A
candidate can be marked "known, not a threat" once and it stops counting
forever, because a false positive you cannot silence becomes noise on the
second sighting.

## Cost

Roughly 530 `dig` lookups for 18 domains, about 33 seconds, gated to run
weekly (`lookalike_recheck_hours`). Variants barely overlap between domains
(deduping saved 6 lookups of 536), so the count scales linearly with how many
domains you track and how long their names are.
"""

import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor

from app.analysis import all_domains, ensure_default_settings, upsert_system_action
from app.db import get_connection, init_db
from app.domain_expiry import registrable_domain

MAX_WORKERS = 12

# Substitutions that actually fool a reader in a sans-serif font, plus the two
# multi-character ones ("rn" for "m") that are the classic trick. Deliberately
# short: every entry multiplies the lookup count, and exotic homoglyphs
# (Cyrillic look-alikes) belong to a different attack that needs punycode
# handling to describe honestly.
HOMOGLYPHS = {
    "l": ("1", "i"), "i": ("1", "l"), "o": ("0"), "m": ("rn",),
    "n": ("m",), "a": ("e",), "e": ("a",), "u": ("v",), "w": ("vv",),
}

# The endings a small Indian nonprofit's domain actually gets copied under.
# Not the full TLD list -- that would be thousands of lookups for no signal.
ALT_TLDS = ("com", "org", "net", "in", "co", "info", "ngo")


def variants(domain: str) -> set:
    """Realistic near-misses of one registrable domain.

    Four families, each a real squatting technique: drop a character, swap two
    adjacent ones, substitute a look-alike character, or keep the name and
    change the ending. Deliberately NOT including insertions of arbitrary
    letters (26x the lookups for a technique nobody uses) or hyphen tricks
    (which read as obviously different)."""
    label, _, tld = domain.rpartition(".")
    if not label or not tld:
        return set()
    out = set()

    for alt in ALT_TLDS:
        if alt != tld:
            out.add(f"{label}.{alt}")

    for i in range(len(label)):                      # omission
        if label[i] not in ".-":
            out.add(label[:i] + label[i + 1:] + "." + tld)

    for i in range(len(label) - 1):                  # transposition
        out.add(label[:i] + label[i + 1] + label[i] + label[i + 2:] + "." + tld)

    for i, ch in enumerate(label):                   # homoglyph
        for rep in HOMOGLYPHS.get(ch, ()):
            out.add(label[:i] + rep + label[i + 1:] + "." + tld)

    out.discard(domain)
    # A one-or-two-character label isn't a plausible imitation of anything.
    return {v for v in out if len(v.split(".")[0]) > 2}


def _dig(record_type: str, domain: str, timeout: float = 6.0):
    """dig +short output lines, or None if the lookup itself failed. None must
    never be treated as "doesn't exist" -- that would invent a clean result
    out of a network problem."""
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=4", "+tries=2", record_type, domain],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    return [l.strip() for l in out.stdout.strip().splitlines() if l.strip()]


def probe(candidate: str) -> dict:
    """{"candidate", "registered", "has_mx", "mx_hosts", "status"}.

    NS first because it is the cheapest yes/no for "is this domain
    registered", then MX only for the ones that exist -- which is what keeps
    the second query count in the dozens rather than the hundreds."""
    ns = _dig("NS", candidate)
    if ns is None:
        return {"candidate": candidate, "registered": None, "has_mx": None,
                "mx_hosts": None, "status": "lookup_failed"}
    if not ns:
        return {"candidate": candidate, "registered": False, "has_mx": False,
                "mx_hosts": None, "status": "available"}

    mx = _dig("MX", candidate)
    hosts = []
    for line in (mx or []):
        parts = line.split()
        host = (parts[-1] if parts else "").rstrip(".")
        # "." and "localhost" are null/placeholder MX records -- the domain is
        # registered but explicitly not accepting mail, so it can't be used to
        # write to anybody's donors.
        if host and host.lower() not in ("", "localhost"):
            hosts.append(host)
    return {
        "candidate": candidate,
        "registered": True,
        "has_mx": bool(hosts),
        "mx_hosts": ", ".join(hosts[:3]) if hosts else None,
        "status": "registered",
    }


def _store(conn, domain_id, root, result, kind):
    """Upsert one candidate, preserving `ignored` and `first_seen_at` across
    runs -- a candidate the operator has already dismissed must not come back
    every week, which is the whole reason this is a table and not a live
    query."""
    existing = conn.execute(
        "SELECT id, ignored FROM lookalike_domains WHERE domain_id=? AND candidate=?",
        (domain_id, result["candidate"]),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE lookalike_domains
               SET registered=?, has_mx=?, mx_hosts=?, status=?, last_checked_at=datetime('now')
               WHERE id=?""",
            (int(bool(result["registered"])) if result["registered"] is not None else None,
             int(bool(result["has_mx"])) if result["has_mx"] is not None else None,
             result["mx_hosts"], result["status"], existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO lookalike_domains
                 (domain_id, root_domain, candidate, variant_kind, registered, has_mx, mx_hosts, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (domain_id, root, result["candidate"], kind,
             int(bool(result["registered"])) if result["registered"] is not None else None,
             int(bool(result["has_mx"])) if result["has_mx"] is not None else None,
             result["mx_hosts"], result["status"]),
        )


def _variant_kind(root: str, candidate: str) -> str:
    """Which technique produced this candidate -- shown in the UI because
    "same name, different ending" and "one letter changed" deserve very
    different levels of concern."""
    r_label, _, r_tld = root.rpartition(".")
    c_label, _, c_tld = candidate.rpartition(".")
    if c_label == r_label and c_tld != r_tld:
        return "different ending"
    if len(c_label) < len(r_label):
        return "letter dropped"
    if len(c_label) > len(r_label):
        return "look-alike letters"
    return "letters changed"


def findings_for_domain(conn, domain_id: int, include_ignored: bool = False):
    """Registered look-alikes for one domain, mail-capable ones first."""
    sql = """SELECT * FROM lookalike_domains
             WHERE domain_id=? AND registered=1"""
    if not include_ignored:
        sql += " AND ignored=0"
    sql += " ORDER BY has_mx DESC, candidate"
    return conn.execute(sql, (domain_id,)).fetchall()


def _apply_action_item(conn, domain_id: int, domain_name: str):
    """One item per domain, or none. Counts only registered, non-ignored
    candidates that can actually handle mail -- a parked look-alike with no MX
    cannot email anyone, so it belongs in the list but not in an alert."""
    mail_capable = conn.execute(
        """SELECT candidate, mx_hosts FROM lookalike_domains
           WHERE domain_id=? AND registered=1 AND has_mx=1 AND ignored=0
           ORDER BY candidate""",
        (domain_id,),
    ).fetchall()

    if not mail_capable:
        conn.execute(
            """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
               WHERE domain_id=? AND category='lookalike_domain' AND status='open'""",
            (domain_id,),
        )
        return 0

    names = [r["candidate"] for r in mail_capable]
    shown = ", ".join(names[:3])
    more = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
    upsert_system_action(
        conn, domain_id, "lookalike_domain", None,
        f"{domain_name}: {len(names)} look-alike domain(s) are registered and set up for email",
        (f"{shown}{more} closely resemble {domain_name} and have working mail servers, so each one "
         f"could be used to email your supporters while looking like you. Most look-alikes are "
         f"innocent -- unrelated businesses often own a similar name -- so check them and mark the "
         f"ones you recognise as known, which stops them being counted here."),
    )
    return len(names)


def run_lookalike_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    if settings.get("lookalike_enabled", "1") != "1":
        if verbose:
            print("[lookalike] disabled in Settings")
        return
    recheck_hours = int(settings["lookalike_recheck_hours"])

    # One entry per registrable domain: checking mail.aikyamhq.com separately
    # from aikyamhq.com would double the lookups to say the same thing twice.
    roots = {}
    for d in all_domains(conn):
        root = registrable_domain(d["name"])
        if root and (root not in roots or d["name"] == root):
            roots[root] = d["id"]

    stale = []
    for root, domain_id in roots.items():
        row = conn.execute(
            """SELECT MAX(last_checked_at) AS last FROM lookalike_domains
               WHERE domain_id=? AND root_domain=?""",
            (domain_id, root),
        ).fetchone()
        if not row or not row["last"]:
            stale.append((root, domain_id))
        else:
            fresh = conn.execute(
                "SELECT ? > datetime('now', ?) AS fresh", (row["last"], f"-{recheck_hours} hours")
            ).fetchone()["fresh"]
            if not fresh:
                stale.append((root, domain_id))

    if not stale:
        if verbose:
            print("[lookalike] all domains checked recently, skipping")
        return

    tracked = {d["name"] for d in all_domains(conn)}
    for root, domain_id in stale:
        candidates = sorted(variants(root) - tracked - set(roots))
        if not candidates:
            continue
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(candidates))) as ex:
            results = list(ex.map(probe, candidates))
        for result in results:
            _store(conn, domain_id, root, result, _variant_kind(root, result["candidate"]))
        conn.commit()

        flagged = _apply_action_item(conn, domain_id, root)
        conn.commit()
        if verbose:
            registered = [r for r in results if r["registered"]]
            print(f"  {root}: {len(candidates)} checked, {len(registered)} registered, "
                  f"{flagged} mail-capable")

    if verbose:
        print(f"[lookalike] {len(stale)} domain(s) scanned")


def main() -> None:
    argparse.ArgumentParser(description="Find domains registered to look like yours").parse_args()
    conn = get_connection()
    init_db(conn)
    run_lookalike_checks(conn)


if __name__ == "__main__":
    main()
