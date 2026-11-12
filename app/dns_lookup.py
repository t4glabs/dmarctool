"""
One place every DNS check asks DNS a question, so a failed lookup can never be
read as "there is no such record".

`dig +short` prints nothing and exits 0 both when a name genuinely has no
record AND when the resolver gave up on it (SERVFAIL: a broken DNSSEC chain,
an unreachable or lame authoritative server). Read on its own, a resolver
failure became "no DMARC record found" (a subscriber-facing item) or "this
look-alike isn't registered". `dig @1.1.1.1 +short TXT dnssec-failed.org`
shows it: empty output, exit 0, while the full answer says `status: SERVFAIL`.

So this reads dig's status line instead of its silence. NOERROR and NXDOMAIN
are real answers; SERVFAIL, REFUSED, a timeout or a dig error are "couldn't
check" -- returned as None, which every caller already treats as "try again
next run, change nothing".
"""

import re
import subprocess

_STATUS_RE = re.compile(r"->>HEADER<<-.*status: ([A-Z]+)")
_TXT_QUOTE_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
ANSWERED = ("NOERROR", "NXDOMAIN")


def query(args, timeout: float = 6.0, time_per_try: int = 3, tries: int = 2):
    """(status, rdata_lines) for one dig query. `args` is what follows `dig`,
    e.g. ["TXT", "_dmarc.example.org"] or ["-x", "1.2.3.4"].

    status is dig's own (NOERROR, NXDOMAIN, SERVFAIL, REFUSED...) or "FAILED"
    when dig itself couldn't get an answer (timeout, no network). rdata_lines
    are the answer section's values -- the same lines `+short` would print,
    CNAME targets included -- and are empty unless status is NOERROR."""
    cmd = ["dig", f"+time={time_per_try}", f"+tries={tries}", "+noall", "+comments", "+answer"] + list(args)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return "FAILED", []
    statuses = _STATUS_RE.findall(out.stdout)
    if out.returncode != 0 or not statuses:
        return "FAILED", []
    status = statuses[-1]
    if status != "NOERROR":
        return status, []
    rdata = []
    for line in out.stdout.splitlines():
        if not line.strip() or line.startswith(";"):
            continue
        parts = line.split(None, 4)  # name ttl class type rdata
        if len(parts) == 5:
            rdata.append(parts[4].strip())
    return status, rdata


def lookup(args, timeout: float = 6.0, time_per_try: int = 3, tries: int = 2):
    """Answer lines like `dig +short` prints, [] when DNS really says there's
    nothing there (NXDOMAIN, or the name exists without that record type), or
    None when the lookup itself failed. None must never be read as "none"."""
    status, rdata = query(args, timeout, time_per_try, tries)
    if status not in ANSWERED:
        return None
    return rdata


def txt(name: str, timeout: float = 6.0):
    """TXT values for `name`, each record's quoted chunks joined back into one
    string (DNS splits values over 255 characters), [] if none, None if the
    lookup failed."""
    lines = lookup(["TXT", name], timeout)
    if lines is None:
        return None
    records = []
    for line in lines:
        parts = _TXT_QUOTE_RE.findall(line)
        records.append("".join(parts) if parts else line)
    return records
