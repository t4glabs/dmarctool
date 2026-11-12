"""
Website certificate expiry -- one TLS handshake to each tracked domain's
website (the domain itself and www.) to read when its certificate runs out.
An expired certificate puts a full-page browser warning in front of every
visitor, which is the same "is this really them?" doubt the rest of Vigil
exists to prevent.

Why 21 days and not 60: every certificate on the tracked sites (measured
12 Nov 2026, 64 hosts) is a 90-day Let's Encrypt or Google Trust Services
certificate that renews itself about 30 days before it runs out. A 60-day
warning would be permanently on for a third of them while nothing was wrong.
At 21 days left, renewal has been failing for over a week -- that's the
moment someone needs to look.

Only expiry raises an item. A certificate that doesn't cover the name (seen
on two www. hosts) or no HTTPS at all is recorded for the dashboard but not
raised: the wider website checks are planned to come from the public checker.
"""

import argparse
import datetime
import os
import socket
import ssl
import tempfile
from concurrent.futures import ThreadPoolExecutor

from app.analysis import all_domains, upsert_system_action
from app.db import get_connection, init_db
from app.public_suffix import registrable_domain

MAX_WORKERS = 10
WARN_DAYS = 21
RECHECK_HOURS = 24
_X509_CERT_HAS_EXPIRED = 10


def _not_after_unverified(host: str, timeout: float):
    """notAfter of a certificate that failed verification (so getpeercert()
    gives nothing): fetch it unverified and decode the DER blob."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            der = ss.getpeercert(binary_form=True)
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(ssl.DER_cert_to_PEM_cert(der))
        decoded = ssl._ssl._test_decode_cert(path)  # stdlib's own decoder; no public API for this
    finally:
        os.unlink(path)
    return decoded.get("notAfter")


def _parse_not_after(value: str) -> datetime.date:
    return datetime.datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").date()


def check_host(host: str, timeout: float = 8.0) -> dict:
    """{"host", "status", "not_after", "issuer", "note"}.
    status: ok | expiring | expired | invalid | no_https | lookup_failed."""
    out = {"host": host, "status": "lookup_failed", "not_after": None, "issuer": None, "note": None}
    try:
        socket.getaddrinfo(host, 443)
    except socket.gaierror:
        out.update(status="no_https", note="no address in DNS -- no website at this name")
        return out
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
    except ssl.SSLCertVerificationError as e:
        if e.verify_code == _X509_CERT_HAS_EXPIRED:
            try:
                not_after = _not_after_unverified(host, timeout)
                out["not_after"] = _parse_not_after(not_after).isoformat() if not_after else None
            except (OSError, ssl.SSLError, ValueError):
                pass
            out.update(status="expired", note=f"certificate has expired{' on ' + out['not_after'] if out['not_after'] else ''}")
        else:
            out.update(status="invalid", note=f"certificate not accepted: {e.verify_message}")
        return out
    except (ConnectionRefusedError, socket.timeout, TimeoutError):
        out.update(status="no_https", note="nothing answering on port 443")
        return out
    except (OSError, ssl.SSLError) as e:
        out.update(note=f"couldn't connect: {e}")
        return out

    not_after = _parse_not_after(cert["notAfter"])
    days_left = (not_after - datetime.date.today()).days
    out["not_after"] = not_after.isoformat()
    out["issuer"] = dict(x[0] for x in cert.get("issuer", ())).get("organizationName")
    out["status"] = "expiring" if days_left <= WARN_DAYS else "ok"
    out["note"] = f"valid until {out['not_after']} ({days_left} days)"
    return out


def _hosts_for(domains):
    """{domain_id: (website_domain, [hosts])} -- one entry per registered
    domain: on the domain itself when it's tracked, else on the tracked
    subdomain that stands for it (mails.humansofwelive.org)."""
    tracked = {d["name"] for d in domains}
    out = {}
    for d in domains:
        site = registrable_domain(d["name"])
        if d["name"] == site or site not in tracked:
            if site not in {v[0] for v in out.values()}:
                out[d["id"]] = (site, [site, f"www.{site}"])
    return out


def _stale(conn, domain_id):
    row = conn.execute("SELECT MAX(checked_at) AS last FROM cert_checks WHERE domain_id=?", (domain_id,)).fetchone()
    if not row["last"]:
        return True
    last = datetime.datetime.strptime(row["last"], "%Y-%m-%d %H:%M:%S")
    return last < datetime.datetime.utcnow() - datetime.timedelta(hours=RECHECK_HOURS)


def run_cert_checks(conn, verbose: bool = True) -> None:
    targets = {did: v for did, v in _hosts_for(all_domains(conn)).items() if _stale(conn, did)}
    if not targets:
        if verbose:
            print("[cert] all websites checked recently, skipping")
        return
    hosts = [h for _, (_, hs) in targets.items() for h in hs]
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(hosts))) as ex:
        results = dict(zip(hosts, ex.map(check_host, hosts)))

    for domain_id, (site, hs) in targets.items():
        rows = [results[h] for h in hs]
        for r in rows:
            conn.execute(
                "INSERT INTO cert_checks (domain_id, host, status, not_after, issuer, note) VALUES (?,?,?,?,?,?)",
                (domain_id, r["host"], r["status"], r["not_after"], r["issuer"], r["note"]),
            )
            if verbose:
                print(f"[cert] {r['host']}: {r['status']} -- {r['note']}")
        due = [r for r in rows if r["status"] in ("expiring", "expired")]
        if due:
            soonest = min(due, key=lambda r: r["not_after"] or "")
            parts = [f"{r['host']} {'expired' if r['status'] == 'expired' else 'runs out'}"
                     f"{' on ' + r['not_after'] if r['not_after'] else ''}" for r in due]
            upsert_system_action(
                conn, domain_id, "cert_expiring_soon", None,
                f"{site}: website certificate {'has expired' if soonest['status'] == 'expired' else 'runs out soon'}",
                (f"{'; '.join(parts)}. These certificates normally renew themselves about 30 days before they run "
                 f"out, so this means automatic renewal has stopped working. Whoever hosts the website (or "
                 f"Cloudflare, if it sits in front) needs to renew it; once it expires, browsers show visitors a "
                 f"full-page security warning."),
            )
        elif all(r["status"] != "lookup_failed" for r in rows):
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='cert_expiring_soon' AND status='open'""",
                (domain_id,),
            )
    conn.commit()


def main() -> None:
    argparse.ArgumentParser(description="Check website TLS certificate expiry").parse_args()
    conn = get_connection()
    init_db(conn)
    run_cert_checks(conn)


if __name__ == "__main__":
    main()
