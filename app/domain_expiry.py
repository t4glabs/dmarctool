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
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app import public_suffix
from app.analysis import all_domains, ensure_default_settings, upsert_system_action
from app.db import get_connection, init_db

MAX_WORKERS = 6
IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
REQUEST_HEADERS = {"User-Agent": "DMARCTool/1.0", "Accept": "application/rdap+json"}
_BOOTSTRAP_TTL_SECONDS = 24 * 3600

# Module-level cache: the IANA bootstrap file changes rarely, and re-fetching
# it once per domain per run would be wasteful and slow.
_bootstrap_cache = {"data": None, "fetched_at": 0.0}

def registrable_domain(name: str) -> str:
    """The registered domain for a possibly-subdomained tracked name (e.g.
    mail.aikyamhq.com -> aikyamhq.com, prerna.aikyam.school -> aikyam.school,
    x.edu.in -> x.edu.in) -- registration expiry is a property of the
    registered domain, not any subdomain sending under it. From the Public
    Suffix List (app/public_suffix.py); kept here because callers import it
    from this module."""
    return public_suffix.registrable_domain(name)


_bootstrap_lock = threading.Lock()


def _bootstrap() -> dict:
    # One fetch at a time: the checks run in parallel, and without the lock
    # every worker fetched it at once -- one slow fetch then came back {} and
    # its domain read "no RDAP service for .school" (12 Nov 2026).
    with _bootstrap_lock:
        now = time.time()
        if _bootstrap_cache["data"] and now - _bootstrap_cache["fetched_at"] < _BOOTSTRAP_TTL_SECONDS:
            return _bootstrap_cache["data"]
        req = urllib.request.Request(IANA_BOOTSTRAP_URL, headers=REQUEST_HEADERS)
        for _ in range(2):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                break
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
                data = None
        if not data:
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


def _rdap_statuses(data: dict) -> list:
    """RDAP status values in EPP's spelling ("client transfer prohibited" ->
    "clientTransferProhibited"), so RDAP and WHOIS results compare equal."""
    out = []
    for st in data.get("status") or []:
        words = str(st).split()
        out.append(words[0].lower() + "".join(w.capitalize() for w in words[1:]) if words else str(st))
    return out


def _whois_lookup(registrable: str, timeout: float = 15.0) -> dict:
    """Fallback for when RDAP has no expiry date (or no RDAP server at all):
    the system `whois` client, parsed for the expiry and creation dates and
    the EPP status codes. WHOIS output isn't standardised, so this only reads
    the field names registries actually use and returns nothing it can't parse."""
    try:
        out = subprocess.run(["whois", registrable], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"error": f"whois failed: {e}"}
    text = out.stdout or ""

    def _parse(raw):
        m = re.match(r"(\d{4})[-/.](\d{2})[-/.](\d{2})", raw)
        if m:
            return "-".join(m.groups())
        for fmt in ("%d-%b-%Y", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(raw.split()[0], fmt).date().isoformat()
            except ValueError:
                continue
        return None

    def _date(patterns):
        # The LAST matching line: macOS whois prints IANA's record for the TLD
        # first (its own "created: 1985-01-01" for .org), then the registry's.
        found = None
        for line in text.splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() in patterns and value.strip():
                found = _parse(value.strip()) or found
        return found

    expires = _date({"registry expiry date", "registrar registration expiration date", "expiration date",
                     "expiry date", "expires on", "expires", "paid-till", "expire", "renewal date"})
    created = _date({"creation date", "created", "created on", "registered on", "registration time"})
    statuses = []
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() in ("domain status", "status") and value.strip():
            code = value.strip().split()[0]
            if code[:1].islower() and any(c.isupper() for c in code) and code not in statuses:  # EPP codes are camelCase
                statuses.append(code)
    # Without an expiry line the registry section is missing and "created" is IANA's date for the TLD.
    return {"expires_at": expires, "registered_at": created if expires else None, "statuses": statuses}


def check_domain_expiry(registrable: str, timeout: float = 10.0) -> dict:
    """{"status": "ok"|"error", "expires_at": "YYYY-MM-DD"|None, "registered_at", "statuses": [EPP codes],
    "registrar": str|None, "note": str}. RDAP first; WHOIS when RDAP gives no expiry date."""
    tld = registrable.rsplit(".", 1)[-1]
    base_urls = _rdap_base_urls(tld)
    rdap = None
    last_err = None if base_urls else (
        f"No RDAP service listed for .{tld} in IANA's bootstrap registry." if _bootstrap()
        else "couldn't load IANA's RDAP bootstrap registry")
    for base in base_urls:
        url = base.rstrip("/") + "/domain/" + registrable
        req = urllib.request.Request(url, headers=REQUEST_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"status": "error", "expires_at": None, "registered_at": None, "statuses": [],
                        "registrar": None,
                        "note": f"RDAP ({base}) says {registrable} isn't registered -- unexpected for a domain "
                                f"you own; worth checking manually."}
            last_err = f"HTTP {e.code} from {base}"
            continue
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            last_err = f"{base}: {e}"
            continue
        events = {ev.get("eventAction"): (ev.get("eventDate") or "")[:10] for ev in data.get("events", [])}
        rdap = {"expires_at": events.get("expiration") or None, "registered_at": events.get("registration") or None,
                "statuses": _rdap_statuses(data), "registrar": _extract_registrar(data), "base": base}
        break

    if rdap and rdap["expires_at"]:
        return {"status": "ok", "expires_at": rdap["expires_at"], "registered_at": rdap["registered_at"],
                "statuses": rdap["statuses"], "registrar": rdap["registrar"], "note": f"via {rdap['base']}"}

    # RDAP answered without an expiry date, or couldn't be reached at all.
    whois = _whois_lookup(registrable)
    if whois.get("expires_at"):
        return {"status": "ok", "expires_at": whois["expires_at"],
                "registered_at": (rdap or {}).get("registered_at") or whois.get("registered_at"),
                "statuses": (rdap or {}).get("statuses") or whois.get("statuses") or [],
                "registrar": (rdap or {}).get("registrar"),
                "note": "via WHOIS (RDAP gave no expiry date)" if rdap else f"via WHOIS (RDAP: {last_err})"}
    why = "RDAP response had no expiration date" if rdap else f"RDAP lookup failed ({last_err})"
    return {"status": "error", "expires_at": None, "registered_at": (rdap or {}).get("registered_at"),
            "statuses": (rdap or {}).get("statuses") or [], "registrar": (rdap or {}).get("registrar"),
            "note": f"{why}, and WHOIS didn't give one either."}


def days_until(expires_at: str) -> int:
    expiry_date = datetime.date.fromisoformat(expires_at)
    return (expiry_date - datetime.date.today()).days


def expiry_phrase(days_left: int) -> str:
    """"expires in 12 days" / "expires today" / "expired 3 days ago" -- an
    expired domain used to read "expires in -3 days"."""
    if days_left > 0:
        return f"expires in {days_left} day{'s' if days_left != 1 else ''}"
    if days_left == 0:
        return "expires today"
    ago = -days_left
    return f"expired {ago} day{'s' if ago != 1 else ''} ago"


# Registry statuses that mean the domain is already offline or on its way to
# deletion, whatever the expiry date says: clientHold/serverHold take it out of
# DNS (website and email stop), redemptionPeriod/pendingDelete mean it has
# lapsed and will be released. Urgent regardless of the date.
HOLD_STATUSES = {
    "clientHold": "its registrar has suspended it (clientHold), so the website and email are offline",
    "serverHold": "the registry has suspended it (serverHold), so the website and email are offline",
    "redemptionPeriod": "it has lapsed and is in the redemption period: it can still be recovered, at a fee, "
                        "before it is deleted",
    "pendingDelete": "it has lapsed and is about to be deleted and released for anyone to register",
}
URGENT_DAYS = 14
TRANSFER_LOCKS = {"clientTransferProhibited", "serverTransferProhibited"}


def _record(conn, domain_id, result: dict) -> None:
    conn.execute(
        """INSERT INTO domain_expiry_checks (domain_id, status, expires_at, registrar, note, statuses, registered_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (domain_id, result["status"], result["expires_at"], result["registrar"], result["note"],
         ",".join(result.get("statuses") or []) or None, result.get("registered_at")),
    )


def _stale(conn, domain_id, recheck_hours) -> bool:
    row = conn.execute(
        "SELECT MAX(checked_at) as last_checked FROM domain_expiry_checks WHERE domain_id=?", (domain_id,)
    ).fetchone()
    if row["last_checked"] is None:
        return True
    last_dt = datetime.datetime.strptime(row["last_checked"], "%Y-%m-%d %H:%M:%S")
    return last_dt < datetime.datetime.utcnow() - datetime.timedelta(hours=recheck_hours)


def _dismiss(conn, domain_id, category):
    conn.execute(
        """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
           WHERE domain_id=? AND category=? AND status='open'""",
        (domain_id, category),
    )


def _apply_items(conn, d, result, warn_days, tracked):
    """Raise/clear the registration items for one tracked domain. A failed
    lookup changes nothing -- it used to dismiss an open expiry warning."""
    if result["status"] != "ok":
        return
    name, registrable = d["name"], registrable_domain(d["name"])
    statuses = set(result.get("statuses") or [])
    registrar_note = f" (registrar: {result['registrar']})" if result["registrar"] else ""

    holds = [HOLD_STATUSES[s] for s in HOLD_STATUSES if s in statuses]
    if holds:
        upsert_system_action(
            conn, d["id"], "domain_status_alert", None,
            f"{registrable}: the registry lists it as {', '.join(s for s in HOLD_STATUSES if s in statuses)}",
            f"{registrable}'s registry status says {'; and '.join(holds)}{registrar_note}. Contact the registrar "
            f"today -- this overrides the expiry date ({result['expires_at']}).",
        )
    else:
        _dismiss(conn, d["id"], "domain_status_alert")

    days_left = days_until(result["expires_at"])
    phrase = expiry_phrase(days_left)
    if days_left <= URGENT_DAYS:
        upsert_system_action(
            conn, d["id"], "domain_expiry_urgent", None,
            f"{name} {phrase} ({result['expires_at']}) -- renew now",
            f"This domain's registration {'expired on' if days_left < 0 else 'expires on'} {result['expires_at']}"
            f"{registrar_note}. Renew it today: once a domain lapses, its website and all mail (including DMARC "
            f"reports) stop working, and after the grace period anyone can register it.",
        )
        _dismiss(conn, d["id"], "domain_expiring_soon")
    elif days_left <= warn_days:
        upsert_system_action(
            conn, d["id"], "domain_expiring_soon", None,
            f"{name} {phrase} ({result['expires_at']})",
            f"This domain's registration expires on {result['expires_at']}{registrar_note}. "
            f"Renew it before then -- once a domain lapses, its website and all mail (including DMARC "
            f"reports) stop working immediately.",
        )
        _dismiss(conn, d["id"], "domain_expiry_urgent")
    else:
        _dismiss(conn, d["id"], "domain_expiring_soon")
        _dismiss(conn, d["id"], "domain_expiry_urgent")

    # Transfer lock: raised once per registered domain (on the domain itself,
    # or on a tracked subdomain when the domain isn't tracked on its own).
    owns_lock_item = name == registrable or registrable not in tracked
    if owns_lock_item and statuses and not (statuses & TRANSFER_LOCKS):
        upsert_system_action(
            conn, d["id"], "transfer_lock_off", None,
            f"{registrable}: transfer lock is off -- worth switching on",
            f"{registrable}'s registry status ({', '.join(sorted(statuses))}) has no transfer lock "
            f"(clientTransferProhibited){registrar_note}. The lock is a free switch at the registrar that stops "
            f"the domain being moved to another registrar without you unlocking it first.",
        )
    else:
        _dismiss(conn, d["id"], "transfer_lock_off")


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

    tracked = {d["name"] for d in domains}
    for d, result in results:
        _record(conn, d["id"], result)
        _apply_items(conn, d, result, warn_days, tracked)
        if verbose:
            print(f"=== {d['name']} ===")
            if result["status"] == "ok":
                print(f"  {expiry_phrase(days_until(result['expires_at']))} ({result['expires_at']}) -- "
                      f"{result['note']}; status: {', '.join(result.get('statuses') or []) or 'none listed'}")
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
