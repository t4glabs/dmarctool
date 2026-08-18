"""
Gmail sender-guidelines checks that are derivable from DNS + already-ingested
report data alone, with zero external credentials:

  - PTR / forward-confirmed reverse DNS (FCrDNS) on known sending IPs -- Gmail's
    "sending domains or IPs have valid forward and reverse DNS records" requirement.
  - SPF DNS-lookup budget -- SPF allows at most 10 DNS lookups per evaluation
    (RFC 7208); going over silently breaks SPF for that domain. Checked for every
    domain your own reports show is actually used as an SPF/envelope-from domain
    (root domain, plus any mails./news. subdomain you send bulk mail from).
  - DKIM key length -- Gmail requires >=1024-bit RSA keys (2048 recommended).
    Checked for every (selector, signing domain) pair your reports show is
    actually yours to fix -- third-party infra DKIM (amazonses.com, mailgun.org,
    forwarders, etc.) is deliberately excluded since you don't control those keys.

All three reuse the dig-based, no-account-needed approach already used for DNS
drift and blocklist checks.
"""

import argparse
import base64
import ipaddress
import re
import subprocess
import datetime
from concurrent.futures import ThreadPoolExecutor

from app.analysis import (
    all_domains, ensure_default_settings, eligible_known_senders, sender_ip_context, upsert_system_action,
)
from app.db import get_connection, init_db

MAX_WORKERS = 10
SPF_LOOKUP_MECHANISMS = ("a", "mx", "ptr", "exists")
_TXT_QUOTE_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _dig(args, timeout=5.0):
    try:
        out = subprocess.run(
            ["dig", "+time=3", "+tries=2"] + list(args) + ["+short"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _dig_txt(name, timeout=5.0):
    """TXT records for `name`, each with multi-string chunks already joined."""
    out = _dig(["TXT", name], timeout)
    if out is None:
        return None
    records = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = _TXT_QUOTE_RE.findall(line)
        records.append("".join(parts) if parts else line)
    return records


# ---------------------------------------------------------------------------
# PTR / forward-confirmed reverse DNS
# ---------------------------------------------------------------------------

def check_ptr(ip: str, timeout: float = 5.0):
    """Returns (status, ptr_hostname, note)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "lookup_failed", None, "not a valid IP address"

    ptr_out = _dig(["-x", ip], timeout)
    if ptr_out is None:
        return "lookup_failed", None, "PTR lookup failed (network/dig error)"
    hostnames = [h.rstrip(".") for h in ptr_out.splitlines() if h.strip()]
    if not hostnames:
        return "ptr_missing", None, f"no PTR record for {ip} -- Gmail requires reverse DNS to resolve to a hostname"

    record_type = "AAAA" if addr.version == 6 else "A"
    for hostname in hostnames:
        fwd_out = _dig([record_type, hostname], timeout)
        if fwd_out and ip in [line.strip() for line in fwd_out.splitlines()]:
            return "confirmed", hostname, f"PTR -> {hostname} -> resolves back to {ip}"
    return "mismatch", hostnames[0], f"PTR -> {hostnames[0]}, but that hostname doesn't resolve back to {ip}"


# ---------------------------------------------------------------------------
# SPF DNS-lookup budget (RFC 7208: max 10 lookups)
# ---------------------------------------------------------------------------

def _spf_record_for(domain, timeout=5.0):
    """The domain's v=spf1 TXT record, "" if none, or None on lookup failure."""
    txts = _dig_txt(domain, timeout)
    if txts is None:
        return None
    for txt in txts:
        if txt.lower().startswith("v=spf1"):
            return txt
    return ""


def _count_spf_lookups(domain, timeout, depth, seen):
    if depth > 10 or domain in seen:
        return 0, "ok", None
    seen.add(domain)

    record = _spf_record_for(domain, timeout)
    if record is None:
        return 0, "lookup_failed", f"could not fetch SPF record for {domain}"
    if record == "":
        return 0, "ok", None if depth else "missing"

    count = 0
    for token in record.split()[1:]:
        if token.startswith(("+", "-", "~", "?")):
            token = token[1:]
        if "=" in token:
            key, _, target = token.partition("=")
            if key.lower() == "redirect" and target:
                count += 1
                sub_count, sub_status, sub_note = _count_spf_lookups(target, timeout, depth + 1, seen)
                if sub_status == "lookup_failed":
                    return count, "lookup_failed", sub_note
                count += sub_count
            continue
        mech = token.split(":", 1)[0].split("/", 1)[0].lower()
        target = token.split(":", 1)[1] if ":" in token else None
        if mech == "include" and target:
            count += 1
            sub_count, sub_status, sub_note = _count_spf_lookups(target, timeout, depth + 1, seen)
            if sub_status == "lookup_failed":
                return count, "lookup_failed", sub_note
            count += sub_count
        elif mech in SPF_LOOKUP_MECHANISMS:
            count += 1
        if count > 30:  # safety cap -- we already know it's well over the limit
            return count, "over_limit_probably", None
    return count, "ok", None


def count_spf_lookups(domain: str, timeout: float = 5.0):
    """Returns (lookup_count, status, note). status: 'ok'|'missing'|'lookup_failed'."""
    count, status, note = _count_spf_lookups(domain, timeout, 0, set())
    if status in ("lookup_failed", "missing"):
        return count, status, note or (f"no SPF (v=spf1) record found at {domain}" if status == "missing" else None)
    return count, "ok", None


# ---------------------------------------------------------------------------
# DKIM key length
# ---------------------------------------------------------------------------

def _der_read_tlv(data: bytes, offset: int):
    tag = data[offset]
    offset += 1
    length_byte = data[offset]
    offset += 1
    if length_byte & 0x80:
        num_bytes = length_byte & 0x7F
        length = int.from_bytes(data[offset:offset + num_bytes], "big")
        offset += num_bytes
    else:
        length = length_byte
    return tag, offset, offset + length


def _rsa_key_bits(der: bytes):
    """Parse an X.509 SubjectPublicKeyInfo DER blob, return the RSA modulus bit length or None."""
    try:
        tag, v_start, _ = _der_read_tlv(der, 0)                       # outer SEQUENCE
        if tag != 0x30:
            return None
        _, _, alg_end = _der_read_tlv(der, v_start)                   # AlgorithmIdentifier (skip)
        tag2, bs_start, bs_end = _der_read_tlv(der, alg_end)          # BIT STRING
        if tag2 != 0x03:
            return None
        rsa_der = der[bs_start + 1:bs_end]                            # skip "unused bits" byte
        tag3, rsa_v_start, _ = _der_read_tlv(rsa_der, 0)              # RSAPublicKey SEQUENCE
        if tag3 != 0x30:
            return None
        tag4, mod_start, mod_end = _der_read_tlv(rsa_der, rsa_v_start)  # modulus INTEGER
        if tag4 != 0x02:
            return None
        modulus = rsa_der[mod_start:mod_end].lstrip(b"\x00")
        return len(modulus) * 8 if modulus else 0
    except (IndexError, ValueError):
        return None


def check_dkim_key(selector: str, signing_domain: str, min_bits: int, timeout: float = 5.0):
    """Returns (status, key_bits, note). status: 'ok'|'weak'|'missing'|'lookup_failed'."""
    name = f"{selector}._domainkey.{signing_domain}"
    txts = _dig_txt(name, timeout)
    if txts is None:
        return "lookup_failed", None, f"could not fetch DKIM record at {name}"
    if not txts:
        return "missing", None, f"no DKIM record found at {name}"

    # dig follows CNAMEs (common for ESP-hosted DKIM, e.g. SES "Easy DKIM"), so the
    # first line(s) can be the CNAME target hostname itself rather than the actual
    # DKIM TXT content -- pick the record that actually looks like one.
    dkim_record = next((t for t in txts if "p=" in t), None)
    if dkim_record is None:
        return "missing", None, f"no DKIM key data found at {name} (found: {', '.join(txts)})"

    tags = {}
    for chunk in dkim_record.split(";"):
        chunk = chunk.strip()
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            tags[k.strip().lower()] = v.strip()

    p_value = tags.get("p", "")
    if not p_value:
        return "missing", None, f"DKIM record at {name} has no public key (revoked?)"

    if tags.get("k", "rsa").lower() == "ed25519":
        return "ok", None, f"Ed25519 key at {name} (Gmail's bit-length rule is RSA-specific; not applicable)"

    try:
        der = base64.b64decode(p_value + "=" * (-len(p_value) % 4))
    except Exception:
        return "lookup_failed", None, f"couldn't decode DKIM public key at {name}"

    bits = _rsa_key_bits(der)
    if bits is None:
        return "lookup_failed", None, f"couldn't parse DKIM public key structure at {name}"
    if bits < min_bits:
        return "weak", bits, f"{bits}-bit RSA key at {name} -- below Gmail's {min_bits}-bit minimum"
    if bits < 2048:
        return "ok", bits, f"{bits}-bit RSA key at {name} -- meets the minimum, but Google recommends 2048-bit"
    return "ok", bits, f"{bits}-bit RSA key at {name}"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _stale(conn, table, key_col, key_val, recheck_hours):
    row = conn.execute(
        f"SELECT MAX(checked_at) as last_checked FROM {table} WHERE {key_col}=?", (key_val,)
    ).fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def run_compliance_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    recheck_hours = int(settings["compliance_recheck_hours"])
    spf_warn = int(settings["spf_lookup_warn_threshold"])
    dkim_min_bits = int(settings["dkim_min_bits"])

    _run_ptr(conn, settings, recheck_hours, verbose)
    _run_spf(conn, recheck_hours, spf_warn, verbose)
    _run_dkim(conn, recheck_hours, dkim_min_bits, verbose, settings)
    conn.commit()


def _run_ptr(conn, settings, recheck_hours, verbose):
    senders = eligible_known_senders(conn, settings)
    by_ip = {}
    for row in senders:
        by_ip.setdefault(row["source_ip"], []).append((row["domain_id"], row["domain_name"]))

    # An IP with an already-OPEN ptr_issue stays in the recheck set even if it
    # later falls below the volume/recency threshold above -- same reasoning
    # as blocklist.py's identical fix: otherwise it can never re-verify,
    # clear itself, or pick up the identity context added below.
    open_ptr_issue_ips = conn.execute(
        """SELECT DISTINCT ai.ref_key as source_ip, ai.domain_id, d.name as domain_name
           FROM action_items ai JOIN domains d ON d.id = ai.domain_id
           WHERE ai.category='ptr_issue' AND ai.status='open' AND ai.ref_key IS NOT NULL"""
    ).fetchall()
    for row in open_ptr_issue_ips:
        by_ip.setdefault(row["source_ip"], [])
        if (row["domain_id"], row["domain_name"]) not in by_ip[row["source_ip"]]:
            by_ip[row["source_ip"]].append((row["domain_id"], row["domain_name"]))

    to_check = [ip for ip in by_ip if _stale(conn, "ptr_checks", "source_ip", ip, recheck_hours)]
    results = {}
    if to_check:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_check))) as ex:
            results = dict(zip(to_check, ex.map(check_ptr, to_check)))

    for source_ip, (status, hostname, note) in results.items():
        domains = by_ip[source_ip]
        conn.execute(
            "INSERT INTO ptr_checks (source_ip, status, ptr_hostname, note) VALUES (?,?,?,?)",
            (source_ip, status, hostname, note),
        )
        if verbose:
            print(f"[PTR] {source_ip} ({', '.join(n for _, n in domains)}): {status} -- {note}")
        if status in ("ptr_missing", "mismatch"):
            category_fact = {
                "not_yours": "It also has a reverse-DNS problem, unsurprising for a spoofing attempt rather than real infrastructure.",
                "otherwise": f"It also has a reverse-DNS problem ({note}), which can make legitimate mail from it look untrustworthy to receivers.",
            }
            for domain_id, domain_name in domains:
                ctx = sender_ip_context(conn, domain_id, domain_name, source_ip, category_fact=category_fact)
                upsert_system_action(
                    conn, domain_id, "ptr_issue", source_ip,
                    f"{domain_name}: sending IP {source_ip} has a PTR/reverse-DNS problem",
                    ctx["detail"],
                )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='ptr_issue' AND ref_key=? AND status='open'""",
                (source_ip,),
            )


def _run_spf(conn, recheck_hours, warn_threshold, verbose):
    targets = conn.execute(
        """SELECT DISTINCT d.id as domain_id, d.name as domain_name, ar.domain as spf_domain
           FROM record_auth_results ar
           JOIN report_records rr ON rr.id = ar.record_id
           JOIN reports r ON r.id = rr.report_id
           JOIN domains d ON d.id = r.domain_id
           WHERE ar.mechanism='spf' AND ar.domain IS NOT NULL
             AND (ar.domain = d.name OR ar.domain LIKE '%.' || d.name)
           UNION
           SELECT id as domain_id, name as domain_name, name as spf_domain FROM domains"""
    ).fetchall()

    by_target = {}
    for row in targets:
        by_target[(row["domain_id"], row["spf_domain"])] = row["domain_name"]

    to_check = [
        key for key in by_target
        if _stale(conn, "spf_checks", "spf_domain", key[1], recheck_hours)
    ]
    spf_domains = list({key[1] for key in to_check})
    results = {}
    if spf_domains:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(spf_domains))) as ex:
            results = dict(zip(spf_domains, ex.map(count_spf_lookups, spf_domains)))

    for (domain_id, spf_domain), domain_name in by_target.items():
        if spf_domain not in results:
            continue
        count, status, note = results[spf_domain]
        if status == "ok":
            if count > 10:
                status, note = "over_limit", f"{count} DNS lookups (SPF allows max 10) -- SPF is likely failing silently"
            elif count >= warn_threshold:
                status, note = "warn", f"{count} DNS lookups (limit 10) -- getting close to the limit"
            else:
                note = f"{count} DNS lookups (limit 10)"

        conn.execute(
            "INSERT INTO spf_checks (domain_id, spf_domain, status, lookup_count, note) VALUES (?,?,?,?,?)",
            (domain_id, spf_domain, status, count, note),
        )
        if verbose:
            print(f"[SPF] {spf_domain} ({domain_name}): {status} -- {note}")
        if status in ("over_limit", "missing"):
            upsert_system_action(
                conn, domain_id, "spf_lookup_limit", spf_domain,
                f"{domain_name}: SPF record at {spf_domain} has a problem", note,
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='spf_lookup_limit' AND ref_key=? AND status='open'""",
                (spf_domain,),
            )


def _run_dkim(conn, recheck_hours, min_bits, verbose, settings):
    # Same volume/recency bar as blocklist/PTR checks -- reports accumulate one-off
    # selectors from old/decommissioned senders and forwarders that aren't worth
    # checking (or flagging as "missing" when they're really just retired).
    min_volume = int(settings["blocklist_min_volume"])
    recent_cutoff = int(datetime.datetime.utcnow().timestamp()) - int(settings["blocklist_recent_days"]) * 86400
    targets = conn.execute(
        """SELECT d.id as domain_id, d.name as domain_name,
                  ar.domain as signing_domain, ar.selector as selector
           FROM record_auth_results ar
           JOIN report_records rr ON rr.id = ar.record_id
           JOIN reports r ON r.id = rr.report_id
           JOIN domains d ON d.id = r.domain_id
           WHERE ar.mechanism='dkim' AND ar.domain IS NOT NULL AND ar.selector IS NOT NULL
             AND (ar.domain = d.name OR ar.domain LIKE '%.' || d.name)
           GROUP BY d.id, ar.domain, ar.selector
           HAVING SUM(rr.count) >= ? AND MAX(r.date_end) >= ?""",
        (min_volume, recent_cutoff),
    ).fetchall()

    by_target = {
        (row["domain_id"], row["signing_domain"], row["selector"]): row["domain_name"]
        for row in targets
    }

    def _dkim_stale(signing_domain, selector):
        row = conn.execute(
            """SELECT MAX(checked_at) as last_checked FROM dkim_checks
               WHERE signing_domain=? AND selector=?""",
            (signing_domain, selector),
        ).fetchone()
        if row["last_checked"] is None:
            return True
        last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
        return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)

    to_check = [key for key in by_target if _dkim_stale(key[1], key[2])]
    results = {}
    if to_check:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_check))) as ex:
            futures = ex.map(lambda k: check_dkim_key(k[2], k[1], min_bits), to_check)
            results = dict(zip(to_check, futures))

    for (domain_id, signing_domain, selector), domain_name in by_target.items():
        if (domain_id, signing_domain, selector) not in results:
            continue
        status, bits, note = results[(domain_id, signing_domain, selector)]
        conn.execute(
            "INSERT INTO dkim_checks (domain_id, signing_domain, selector, status, key_bits, note) VALUES (?,?,?,?,?,?)",
            (domain_id, signing_domain, selector, status, bits, note),
        )
        if verbose:
            print(f"[DKIM] {selector}._domainkey.{signing_domain} ({domain_name}): {status} -- {note}")
        ref_key = f"{selector}._domainkey.{signing_domain}"
        if status == "weak":
            upsert_system_action(
                conn, domain_id, "dkim_weak_key", ref_key,
                f"{domain_name}: DKIM key for selector {selector} is too weak", note,
            )
        elif status == "missing":
            upsert_system_action(
                conn, domain_id, "dkim_weak_key", ref_key,
                f"{domain_name}: DKIM record for selector {selector} is missing", note,
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='dkim_weak_key' AND ref_key=? AND status='open'""",
                (ref_key,),
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check PTR/FCrDNS, SPF lookup budget, and DKIM key length")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_compliance_checks(conn)


if __name__ == "__main__":
    main()
