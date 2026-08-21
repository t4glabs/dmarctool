"""
Zero-credential domain-registration-expiry check, via RDAP (Registration
Data Access Protocol, RFC 9082/9083) -- how close each tracked domain is to
lapsing, which would take its mail (and website) down entirely, a failure
mode none of DMARCTool's other checks can see.

Inspired by the user's own external ak545/dns-domain-expiration-checker fork
(ddec_rdap.py), simplified drastically for what this tool actually needs:
just an expiration date and a days-left count for a small, known set of
domains, not that script's full CLI/Telegram/Google-Chat reporting stack.
RDAP over stdlib urllib/json replaces its whois-binary + requests/dateutil/
python-whois/colorama dependency chain entirely -- every RDAP response is
plain JSON, and IANA publishes the bootstrap registry mapping each TLD to
its RDAP server, so no new dependency is needed (same stdlib-first pattern
as mta_sts.py and safe_browsing.py).
"""

import argparse
import datetime
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app.analysis import all_domains, ensure_default_settings, upsert_system_action
from app.db import get_connection, init_db

MAX_WORKERS = 6
IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
REQUEST_HEADERS = {"User-Agent": "DMARCTool/1.0", "Accept": "application/rdap+json"}
_BOOTSTRAP_TTL_SECONDS = 24 * 3600

# Module-level cache: the IANA bootstrap file changes rarely, and re-fetching
# it once per domain per run would be wasteful and slow.
_bootstrap_cache = {"data": None, "fetched_at": 0.0}

# A handful of common two-label public suffixes -- not exhaustive (a full
# Public Suffix List is more than this project needs), just enough that the
# "last two labels" heuristic below doesn't misfire on domains this tool is
# realistically likely to track.
_TWO_LABEL_SUFFIXES = {"co.in", "org.in", "net.in", "gov.in", "ac.in", "co.uk", "org.uk", "com.au"}


def registrable_domain(name: str) -> str:
    """Best-effort apex/registrable domain for a possibly-subdomained tracked
    name (e.g. mail.aikyamhq.com -> aikyamhq.com, prerna.aikyam.school ->
    aikyam.school) -- registration expiry is a property of the registered
    domain, not any subdomain sending under it."""
    labels = name.lower().rstrip(".").split(".")
    if len(labels) <= 2:
        return name.lower()
    last_two = ".".join(labels[-2:])
    if last_two in _TWO_LABEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last_two


def _bootstrap() -> dict:
    now = time.time()
    if _bootstrap_cache["data"] and now - _bootstrap_cache["fetched_at"] < _BOOTSTRAP_TTL_SECONDS:
        return _bootstrap_cache["data"]
    req = urllib.request.Request(IANA_BOOTSTRAP_URL, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return _bootstrap_cache["data"] or {}
    _bootstrap_cache["data"] = data
    _bootstrap_cache["fetched_at"] = now
    return data


def _rdap_base_urls(tld: str) -> list:
    for tlds, urls in (entry[:2] for entry in _bootstrap().get("services", [])):
        if tld in tlds:
            return urls
    return []


def _extract_registrar(data: dict):
    for entity in data.get("entities", []):
        if "registrar" not in entity.get("roles", []):
            continue
        vcard = entity.get("vcardArray")
        if not vcard or len(vcard) < 2:
            continue
        for field in vcard[1]:
            if isinstance(field, list) and len(field) > 3 and field[0] == "fn":
                return field[3]
    return None


def check_domain_expiry(registrable: str, timeout: float = 10.0) -> dict:
    """{"status": "ok"|"error", "expires_at": "YYYY-MM-DD"|None, "registrar": str|None, "note": str}."""
    tld = registrable.rsplit(".", 1)[-1]
    base_urls = _rdap_base_urls(tld)
    if not base_urls:
        return {"status": "error", "expires_at": None, "registrar": None,
                "note": f"No RDAP service listed for .{tld} in IANA's bootstrap registry."}

    last_err = None
    for base in base_urls:
        url = base.rstrip("/") + "/domain/" + registrable
        req = urllib.request.Request(url, headers=REQUEST_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"status": "error", "expires_at": None, "registrar": None,
                        "note": f"RDAP ({base}) says {registrable} isn't registered -- unexpected for a domain "
                                f"you own; worth checking manually."}
            last_err = f"HTTP {e.code} from {base}"
            continue
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            last_err = f"{base}: {e}"
            continue

        expires_at = None
        for event in data.get("events", []):
            if event.get("eventAction") == "expiration" and event.get("eventDate"):
                expires_at = event["eventDate"][:10]
                break
        registrar = _extract_registrar(data)
        if not expires_at:
            return {"status": "error", "expires_at": None, "registrar": registrar,
                    "note": f"RDAP response from {base} had no expiration date in its events."}
        return {"status": "ok", "expires_at": expires_at, "registrar": registrar, "note": f"via {base}"}

    return {"status": "error", "expires_at": None, "registrar": None,
            "note": f"RDAP lookup failed for every known .{tld} endpoint ({last_err})."}


def days_until(expires_at: str) -> int:
    expiry_date = datetime.date.fromisoformat(expires_at)
    return (expiry_date - datetime.date.today()).days


def _record(conn, domain_id, result: dict) -> None:
    conn.execute(
        """INSERT INTO domain_expiry_checks (domain_id, status, expires_at, registrar, note)
           VALUES (?, ?, ?, ?, ?)""",
        (domain_id, result["status"], result["expires_at"], result["registrar"], result["note"]),
    )


def _stale(conn, domain_id, recheck_hours) -> bool:
    row = conn.execute(
        "SELECT MAX(checked_at) as last_checked FROM domain_expiry_checks WHERE domain_id=?", (domain_id,)
    ).fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def run_domain_expiry_checks(conn, verbose: bool = True) -> None:
    settings = ensure_default_settings(conn)
    recheck_hours = int(settings["domain_expiry_recheck_hours"])
    warn_days = int(settings["domain_expiry_warn_days"])
    domains = all_domains(conn)
    to_check = [d for d in domains if _stale(conn, d["id"], recheck_hours)]
    if not to_check:
        if verbose:
            print("[domain_expiry] all domains checked recently, skipping")
        return

    def _check_one(d):
        return d, check_domain_expiry(registrable_domain(d["name"]))

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_check))) as ex:
        results = list(ex.map(_check_one, to_check))

    for d, result in results:
        _record(conn, d["id"], result)
        days_left = days_until(result["expires_at"]) if result["expires_at"] else None
        if days_left is not None and days_left <= warn_days:
            registrar_note = f" (registrar: {result['registrar']})" if result["registrar"] else ""
            upsert_system_action(
                conn, d["id"], "domain_expiring_soon", None,
                f"{d['name']} expires in {days_left} day{'s' if days_left != 1 else ''} ({result['expires_at']})",
                f"This domain's registration expires on {result['expires_at']}{registrar_note}. "
                f"Renew it before then -- once a domain lapses, its website and all mail (including DMARC "
                f"reports) stop working immediately.",
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE domain_id=? AND category='domain_expiring_soon' AND status='open'""",
                (d["id"],),
            )
        if verbose:
            print(f"=== {d['name']} ===")
            if result["status"] == "ok":
                print(f"  expires {result['expires_at']} ({days_left} days) -- {result['note']}")
            else:
                print(f"  couldn't check -- {result['note']}")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check domain registration expiry via RDAP")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_domain_expiry_checks(conn)


if __name__ == "__main__":
    main()
