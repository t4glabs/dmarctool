"""
Blocklist (DNSBL) monitoring for known sending IPs.

Checks each domain's known sender IPs against a small set of public DNS
blocklists (queried via `dig`, no API keys or accounts needed) so a
blocklisting of one of your legitimate sending IPs (SES pool, Workspace
outbound, etc.) shows up here instead of being discovered only when mail
starts silently disappearing.

IPv4 only for now -- IPv6 DNSBL zones use a different (nibble-reversed) query
format and none of the currently tracked senders use IPv6.
"""

import argparse
import datetime
import ipaddress
from concurrent.futures import ThreadPoolExecutor

from app import dnsbl
from app.analysis import all_domains, ensure_default_settings, eligible_known_senders, sender_ip_context, upsert_system_action
from app.db import get_connection, init_db
from app.public_suffix import registrable_domain

MAX_WORKERS = 10

DNSBL_ZONES = dnsbl.IP_ZONES


def _reversed_ip(ip: str):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version != 4:
        return None
    return ".".join(reversed(ip.split(".")))


def check_ip(ip: str, timeout: float = 5.0):
    """Query each configured DNSBL for `ip`. Returns (status, listed_on, note).

    status: listed | minor (only an end-user-range PBL entry -- not a
    reputation listing, so no alert) | clean | lookup_failed. Answers are read
    by app/dnsbl.py: a refusal code or a zone failing its own self-test is
    "couldn't check", never "listed" or "clean"."""
    reversed_ip = _reversed_ip(ip)
    if reversed_ip is None:
        return "lookup_failed", None, "not an IPv4 address -- skipped (IPv6 DNSBL lookups not supported yet)"

    res = dnsbl.check(reversed_ip, DNSBL_ZONES)
    listed, failed = res["listed"], res["failed"]
    if listed:
        return "listed", listed, f"listed on: {', '.join(listed)}"
    if failed:
        # A zone that failed to respond (or failed its self-test) wasn't
        # actually checked -- treating this as "clean" would risk silently
        # clearing a real listing on that zone. Inconclusive either way.
        why = "; ".join(f"{label}: {reason}" for label, reason in failed)
        note = (f"couldn't check any blocklist ({why})" if len(failed) == len(DNSBL_ZONES)
                else f"lookup incomplete ({why}); no listing found on the rest")
        return "lookup_failed", None, note
    if res["minor"]:
        return "minor", None, "; ".join(f"{label}: {detail}" for label, detail in res["minor"])
    return "clean", None, "not listed"


def _record(conn, source_ip, status, listed_on, note):
    conn.execute(
        "INSERT INTO blocklist_checks (source_ip, status, listed_on, note) VALUES (?, ?, ?, ?)",
        (source_ip, status, ",".join(listed_on) if listed_on else None, note),
    )


def run_blocklist_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    recheck_hours = int(settings["blocklist_recheck_hours"])

    # Only check senders with enough volume to matter and still actively sending --
    # a personal domain can accumulate hundreds of one-off/stray IPs that aren't
    # worth spending free DNSBL lookup budget on (see blocklist_min_volume /
    # blocklist_recent_days in Settings).
    senders = eligible_known_senders(conn, settings)

    by_ip = {}
    for row in senders:
        by_ip.setdefault(row["source_ip"], []).append((row["domain_id"], row["domain_name"]))

    # An IP that already has an OPEN blocklist item stays in the recheck set
    # even if it later falls below the volume/recency threshold above (e.g. a
    # one-off spoofing attempt that will never send again) -- otherwise it can
    # never get re-verified, clear itself, or pick up the identity context
    # added below; it would just sit open forever with stale information.
    open_blocklist_ips = conn.execute(
        """SELECT DISTINCT ai.ref_key as source_ip, ai.domain_id, d.name as domain_name
           FROM action_items ai JOIN domains d ON d.id = ai.domain_id
           WHERE ai.category='blocklist' AND ai.status='open' AND ai.ref_key IS NOT NULL"""
    ).fetchall()
    for row in open_blocklist_ips:
        by_ip.setdefault(row["source_ip"], [])
        if (row["domain_id"], row["domain_name"]) not in by_ip[row["source_ip"]]:
            by_ip[row["source_ip"]].append((row["domain_id"], row["domain_name"]))

    # Skip IPs checked within the recheck window -- their last known status
    # (already reflected in blocklist_checks / action_items) just carries forward,
    # so repeated "Run checks now" clicks don't re-query the same IPs every time.
    last_checked = {
        row["source_ip"]: row["last_checked"]
        for row in conn.execute(
            "SELECT source_ip, MAX(checked_at) as last_checked FROM blocklist_checks GROUP BY source_ip"
        )
    }
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)
    to_check = [
        ip for ip in by_ip
        if ip not in last_checked
        or datetime.datetime.strptime(last_checked[ip], "%Y-%m-%d %H:%M:%S") < cutoff
    ]

    results = {}
    if to_check:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_check))) as ex:
            results = dict(zip(to_check, ex.map(check_ip, to_check)))

    for source_ip, (status, listed_on, note) in results.items():
        domains = by_ip[source_ip]
        _record(conn, source_ip, status, listed_on, note)
        if verbose:
            print(f"=== {source_ip} ({', '.join(name for _, name in domains)}) ===")
            print(f"  {status}: {note}")

        if status == "listed":
            listed_str = ", ".join(listed_on)
            category_fact = {
                "not_yours": f"It's also on a public spam blocklist ({listed_str}), so this exact attempt was already going to be rejected elsewhere too.",
                "otherwise": f"It's also on a public spam blocklist ({listed_str}), which can send mail straight to spam or get it silently rejected.",
            }
            for domain_id, domain_name in domains:
                ctx = sender_ip_context(conn, domain_id, domain_name, source_ip, category_fact=category_fact)
                upsert_system_action(
                    conn, domain_id, "blocklist", source_ip,
                    f"{domain_name}: sending IP {source_ip} is on a blocklist",
                    ctx["detail"],
                )
        elif status in ("clean", "minor"):
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='blocklist' AND ref_key=? AND status='open'""",
                (source_ip,),
            )
        # status == "lookup_failed": inconclusive, leave any existing open item as-is
    conn.commit()

    if verbose:
        skipped = len(by_ip) - len(to_check)
        if skipped:
            print(f"({skipped} IP(s) skipped -- checked within the last {recheck_hours}h)")


def _website_domains(conn):
    """{domain_id: registered domain} -- one entry per registered domain, on
    the domain itself when tracked, else on the tracked subdomain standing
    for it. Domain blocklists list registered domains, not subdomains."""
    domains = all_domains(conn)
    tracked = {d["name"] for d in domains}
    out = {}
    for d in domains:
        reg = registrable_domain(d["name"])
        if (d["name"] == reg or reg not in tracked) and reg not in out.values():
            out[d["id"]] = reg
    return out


def check_domain(domain: str):
    """(status, listed_on, note) for the domain itself on the domain
    blocklists -- the lists spam filters check every link and sender domain
    in a message against, which is how a listed domain gets mail to spam even
    when every sending IP is clean."""
    res = dnsbl.check(domain, dnsbl.DOMAIN_ZONES)
    if res["listed"]:
        return "listed", res["listed"], f"listed on: {', '.join(res['listed'])}"
    if res["failed"]:
        why = "; ".join(f"{label}: {reason}" for label, reason in res["failed"])
        return "lookup_failed", None, (f"couldn't check any domain blocklist ({why})"
                                       if len(res["failed"]) == len(dnsbl.DOMAIN_ZONES)
                                       else f"lookup incomplete ({why}); not listed on the rest")
    if res["minor"]:
        return "minor", None, "; ".join(f"{label}: {detail}" for label, detail in res["minor"])
    return "clean", None, "not listed"


def run_domain_blocklist_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    recheck_hours = int(settings["blocklist_recheck_hours"])
    targets = _website_domains(conn)
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = {r["domain_id"] for r in conn.execute(
        "SELECT domain_id FROM domain_blocklist_checks GROUP BY domain_id HAVING MAX(checked_at) >= ?", (cutoff,))}
    to_check = {did: name for did, name in targets.items() if did not in fresh}
    if not to_check:
        if verbose:
            print("[domain blocklist] all domains checked recently, skipping")
        return
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_check))) as ex:
        results = dict(zip(to_check, ex.map(check_domain, to_check.values())))
    for domain_id, (status, listed_on, note) in results.items():
        name = to_check[domain_id]
        conn.execute(
            "INSERT INTO domain_blocklist_checks (domain_id, domain, status, listed_on, note) VALUES (?,?,?,?,?)",
            (domain_id, name, status, ",".join(listed_on) if listed_on else None, note),
        )
        if verbose:
            print(f"[domain blocklist] {name}: {status} -- {note}")
        if status == "listed":
            upsert_system_action(
                conn, domain_id, "domain_blocklisted", name,
                f"{name}: the domain itself is on a domain blocklist ({', '.join(listed_on)})",
                (f"{name} is listed on {', '.join(listed_on)}. Spam filters check every link and sender "
                 f"domain in a message against these lists, so mail mentioning {name} can go to spam even "
                 f"when every sending server is clean. Each list has a lookup and removal page; find out why "
                 f"it was listed (a hacked page on the website, a link in a newsletter) before asking for "
                 f"removal."),
            )
        elif status != "lookup_failed":
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='domain_blocklisted' AND status='open'""",
                (domain_id,),
            )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check known sending IPs (and each domain itself) against public DNS blocklists")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_blocklist_checks(conn)
    run_domain_blocklist_checks(conn)


if __name__ == "__main__":
    main()
