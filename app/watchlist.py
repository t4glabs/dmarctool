"""
Domains visible in Mailgun/Postmaster but not tracked in DMARCTool.

DMARCTool only learns about a domain when a DMARC report for it gets
ingested -- a domain with no DMARC record published (or one you just haven't
exported a Takeout zip for yet) stays invisible forever otherwise, even if
it's actively sending mail with real problems right now. This is deliberately
a lightweight radar, not full monitoring: computed live on page load, nothing
stored or scheduled, since it's for occasional review of domains you manage
for others (friends/clients) -- not the daily/weekly DMARC flow the main
dashboard is for.
"""

import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from app.config import get_secret
from app.mailgun import fetch_stats as mailgun_fetch_stats, list_mailgun_domains
from app.postmaster import _refresh_access_token, fetch_compliance, fetch_stats as postmaster_fetch_stats, list_verified_domains

_TXT_QUOTE_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _has_dmarc(domain: str, timeout: float = 5.0):
    """True/False, or None if the lookup itself failed (not the same as "no record")."""
    try:
        out = subprocess.run(
            ["dig", "+time=3", "+tries=2", "TXT", f"_dmarc.{domain}", "+short"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = _TXT_QUOTE_RE.findall(line)
        txt = "".join(parts) if parts else line
        if txt.lower().startswith("v=dmarc1"):
            return True
    return False


def build_watchlist(conn):
    """List of dicts, one per untracked domain seen in Mailgun and/or Postmaster,
    sorted with the most concerning (Postmaster NEEDS_WORK items) first."""
    tracked = [r["name"] for r in conn.execute("SELECT name FROM domains")]

    def is_tracked(name):
        return any(name == t or name.endswith("." + t) for t in tracked)

    entries = {}  # domain -> {"sources": set(), ...}

    mg_key = get_secret("MAILGUN_API_KEY")
    mg_domains = []
    if mg_key:
        mg_domains, err = list_mailgun_domains(mg_key)
        mg_domains = mg_domains or []
    for d in mg_domains:
        if not is_tracked(d) and not d.startswith("sandbox"):
            entries.setdefault(d, {"sources": set()})["sources"].add("Mailgun")

    pm_token = None
    if get_secret("GOOGLE_POSTMASTER_REFRESH_TOKEN"):
        pm_token, _ = _refresh_access_token()
    pm_domains = []
    if pm_token:
        pm_domains, err = list_verified_domains(pm_token)
        pm_domains = pm_domains or []
    for d in pm_domains:
        if not is_tracked(d):
            entries.setdefault(d, {"sources": set()})["sources"].add("Postmaster")

    domains = list(entries.keys())
    if not domains:
        return []

    mg_stats_domains = [d for d in domains if "Mailgun" in entries[d]["sources"]] if mg_key else []
    pm_domains_to_check = [d for d in domains if "Postmaster" in entries[d]["sources"]] if pm_token else []

    # All four of these are independent I/O-bound calls (DNS lookups, two
    # different APIs) -- previously run as four separate sequential
    # ThreadPoolExecutor batches, which meant total wait time was the *sum*
    # of each batch's slowest lookup. Running them in one shared pool cuts
    # that down to roughly the slowest *single* batch instead, since they
    # all overlap.
    total_tasks = len(domains) + len(mg_stats_domains) + 2 * len(pm_domains_to_check)
    dmarc_results, mg_stats, comp_results, stat_results = {}, {}, {}, {}
    with ThreadPoolExecutor(max_workers=min(25, max(total_tasks, 1))) as ex:
        futures = {}
        for d in domains:
            futures[ex.submit(_has_dmarc, d)] = ("dmarc", d)
        for d in mg_stats_domains:
            futures[ex.submit(mailgun_fetch_stats, d, mg_key, 30)] = ("mg_stats", d)
        for d in pm_domains_to_check:
            futures[ex.submit(fetch_compliance, d, pm_token)] = ("pm_compliance", d)
            futures[ex.submit(postmaster_fetch_stats, d, pm_token, 30)] = ("pm_stats", d)

        for future in futures:
            kind, d = futures[future]
            result = future.result()
            if kind == "dmarc":
                dmarc_results[d] = result
            elif kind == "mg_stats":
                mg_stats[d] = result
            elif kind == "pm_compliance":
                comp_results[d] = result
            elif kind == "pm_stats":
                stat_results[d] = result

    pm_compliance = {}
    pm_stats = {}
    for d in pm_domains_to_check:
        rows, err = comp_results[d]
        pm_compliance[d] = [r[0] for r in rows if r[1] == "NEEDS_WORK"] if rows else None
        stats, serr = stat_results[d]
        pm_stats[d] = stats.get("spam_rate") if stats else None

    out = []
    for d in domains:
        info = entries[d]
        mg_bounce_rate = None
        stats, daily, err = mg_stats.get(d, (None, None, None))
        if stats and stats.get("accepted"):
            mg_bounce_rate = (stats.get("failed_perm") or 0) / stats["accepted"]

        out.append({
            "domain": d,
            "sources": sorted(info["sources"]),
            "has_dmarc": dmarc_results.get(d),
            "mailgun_bounce_rate": mg_bounce_rate,
            "postmaster_needs_work": pm_compliance.get(d),
            "postmaster_spam_rate": pm_stats.get(d),
        })

    def severity(e):
        # concerning first: postmaster issues, then no DMARC, then everything else
        return (
            0 if e["postmaster_needs_work"] else 1,
            0 if e["has_dmarc"] is False else 1,
            e["domain"],
        )

    return sorted(out, key=severity)
