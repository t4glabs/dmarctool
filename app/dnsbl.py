"""
Reading DNS blocklist answers correctly -- shared by the sending-IP check
(blocklist.py) and the domain check (blocklist.run_domain_blocklist_checks).

A blocklist answers with an address, and not every address means "listed":

  * Spamhaus answers 127.255.255.252 / .254 / .255 when it refuses or can't
    serve the query (a public or over-quota resolver, a malformed query).
    URIBL and SURBL answer 127.0.0.1 for the same thing. That is
    "couldn't check", not a listing.
  * Spamhaus ZEN 127.0.0.10 / .11 is the PBL: "this address is in an
    end-user range", a statement about the range's owner's policy, not about
    anything the address did. Not urgent, and normally irrelevant for an email
    provider's IPs.
  * URIBL's grey list (127.0.0.4) is bulk senders with some opt-in use --
    plenty of legitimate newsletters sit there, and filters don't block on it.
  * An answer outside 127.0.0.0/8 is a resolver rewriting NXDOMAIN into an ad
    page, which would otherwise make everything look listed.

And before trusting a zone at all, its published test entries are looked up
(RFC 5782 section 5): the "listed" test entry must be listed and the
"never listed" one must not be. If either is wrong the zone's answers can't be
trusted this run, so it's reported as "couldn't check" rather than clean.
"""

import ipaddress
import threading
import time

from app import dns_lookup

# zone -> label, test entry that must be listed, test entry that must not be
IP_ZONES = {
    "zen.spamhaus.org": ("Spamhaus ZEN", "2.0.0.127", "1.0.0.127"),
    "b.barracudacentral.org": ("Barracuda BRBL", "2.0.0.127", "1.0.0.127"),
}
DOMAIN_ZONES = {
    "dbl.spamhaus.org": ("Spamhaus DBL", "dbltest.com", "invalid"),
    "multi.surbl.org": ("SURBL", "test.surbl.org", "invalid"),
    "multi.uribl.com": ("URIBL", "test.uribl.com", "invalid"),
}

_SELF_TEST_TTL = 3600
_self_tests = {}
_lock = threading.Lock()


def _classify(zone: str, answers: list):
    """(kind, detail) for one zone's answers to a real query.
    kind: listed | minor | error | clean."""
    if not answers:
        return "clean", None
    try:
        addrs = [ipaddress.ip_address(a) for a in answers]
    except ValueError:
        return "error", f"unexpected answer {answers}"
    if any(not a.is_loopback for a in addrs):
        return "error", f"answer {answers} isn't a blocklist code (resolver rewriting answers?)"
    last = [int(str(a).rsplit(".", 1)[1]) for a in addrs]
    third = [int(str(a).split(".")[2]) for a in addrs]

    if zone.endswith("spamhaus.org"):
        if any(t == 255 for t in third):
            return "error", "Spamhaus refused the query (error code 127.255.255.x)"
        if zone == "zen.spamhaus.org" and all(n in (10, 11) for n in last):
            return "minor", "in an end-user address range (Spamhaus PBL), not a reputation listing"
        return "listed", None
    if zone == "multi.uribl.com":
        if 1 in last:
            return "error", "URIBL refused the query (127.0.0.1: resolver blocked or over quota)"
        bits = 0
        for n in last:
            bits |= n
        if bits & (2 | 8):           # black, red
            return "listed", None
        if bits & 4:                 # grey only
            return "minor", "on URIBL's grey list (bulk mail with some opt-in use), which filters don't block on"
        return "clean", None
    if zone == "multi.surbl.org":
        if 1 in last:
            return "error", "SURBL refused the query (127.0.0.1: resolver blocked)"
        return "listed", None
    return "listed", None            # Barracuda and anything else: any loopback answer is a listing


def zone_working(zone: str, spec: tuple):
    """(ok, note) from the zone's own test entries, cached for an hour."""
    with _lock:
        cached = _self_tests.get(zone)
        if cached and time.time() - cached[0] < _SELF_TEST_TTL:
            return cached[1], cached[2]
    _, must_list, must_not = spec
    yes = dns_lookup.lookup(["A", f"{must_list}.{zone}"])
    no = dns_lookup.lookup(["A", f"{must_not}.{zone}"])
    if yes is None or no is None:
        ok, note = False, "its self-test lookup failed"
    elif _classify(zone, yes)[0] != "listed":
        ok, note = False, f"its test entry {must_list} came back {yes or 'not listed'}"
    elif no:
        ok, note = False, f"its never-listed test entry came back listed ({no})"
    else:
        ok, note = True, None
    with _lock:
        _self_tests[zone] = (time.time(), ok, note)
    return ok, note


def check(name: str, zones: dict):
    """Look `name` (a reversed IPv4 or a domain) up on every zone.
    Returns {"listed": [label], "minor": [(label, detail)], "failed": [(label, why)]}."""
    out = {"listed": [], "minor": [], "failed": []}
    for zone, spec in zones.items():
        label = spec[0]
        ok, why = zone_working(zone, spec)
        if not ok:
            out["failed"].append((label, why))
            continue
        answers = dns_lookup.lookup(["A", f"{name}.{zone}"])
        if answers is None:
            out["failed"].append((label, "lookup failed"))
            continue
        kind, detail = _classify(zone, answers)
        if kind == "listed":
            out["listed"].append(label)
        elif kind == "minor":
            out["minor"].append((label, detail))
        elif kind == "error":
            out["failed"].append((label, detail))
    return out
