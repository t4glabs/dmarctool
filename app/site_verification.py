"""
Google Postmaster Tools domain verification, automated.

Postmaster Tools has no API of its own to "add" a domain -- a domain only
shows up in list_verified_domains() once it's verified as a property under
the SAME Google account, via Google's general-purpose Site Verification API
(the same underlying mechanism Search Console's "Domain property"
verification uses). This module calls that API directly: fetch the DNS TXT
token a domain needs, then attempt verification once the record is live --
so adding a domain to Postmaster Tools can happen without ever visiting
postmaster.google.com.

Needs the `siteverification.verify_only` scope on the SAME refresh token
app/postmaster.py already uses (GOOGLE_POSTMASTER_REFRESH_TOKEN) -- added to
postmaster_auth.py's SCOPES 2026-09-17. If an existing refresh token predates
that change, every call here fails with a scope-related error rather than a
confusing bare 403 -- the fix is re-running `python -m app.postmaster_auth`
once (mints a fresh refresh token carrying the new scope too); this is a
real, unavoidable one-time step, since Google only grants a scope that was
actually requested on that specific consent screen. Until then, the
"missing from Postmaster" reminder still fires -- it just can't hand you
the exact DNS record automatically, and says so.
"""

import json
import re
import urllib.error
import urllib.request

from app.postmaster import _refresh_access_token

API_BASE = "https://www.googleapis.com/siteVerification/v1"

_REAUTH_HINT = ("Postmaster auth doesn't have the domain-verification permission yet -- run "
                "`python -m app.postmaster_auth` once (mints a fresh token with the missing scope "
                "added) and this will start working automatically.")

# Google's error text embeds the exact per-project activation link when the
# API itself (as opposed to the OAuth scope) hasn't been turned on for this
# Cloud project yet -- a separate one-time gate from OAuth consent. Extracted
# rather than hardcoded, so it's always this project's real URL.
_ACTIVATION_URL_RE = re.compile(r"https://console\.developers\.google\.com/apis/api/\S+?(?=[\s\"]|$)")


def _post_full(url: str, body: dict, access_token: str):
    """Like postmaster._post, but returns the FULL error body (no 200-char
    truncation) -- needed here because Google's "API not enabled" error
    embeds a long activation URL that truncation would cut off mid-link."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        return None, f"network error: {e}"


def _explain_error(err: str) -> str:
    """Turn a raw Google API error into ONE clear, correctly-targeted
    instruction. This project has hit two genuinely different one-time setup
    gates -- an ungranted OAuth scope, and the Site Verification API not
    being enabled on the Cloud project at all -- and they need different
    fixes; telling someone to redo OAuth for an API-not-enabled error would
    send them down the wrong path and waste their time."""
    low = (err or "").lower()
    if "service_disabled" in low or "has not been used in project" in low or "accessnotconfigured" in low:
        m = _ACTIVATION_URL_RE.search(err or "")
        url = m.group(0) if m else "https://console.cloud.google.com/apis/library/siteverification.googleapis.com"
        return (f"The Site Verification API itself isn't enabled yet for this Google Cloud project -- a separate, "
                f"one-time, one-click step from the OAuth permission. Visit {url}, click Enable, wait a couple of "
                f"minutes for it to take effect, then this will start working on the next check.")
    if "insufficient" in low or "scope" in low or "forbidden" in low:
        return _REAUTH_HINT
    return err


def get_verification_token(domain_name: str):
    """(token, error). `token` is the exact DNS TXT record VALUE Google
    expects at this domain's root for INET_DOMAIN-level verification (this
    single record also covers every subdomain, matching how Postmaster
    reports work). Stable for a given domain -- safe to cache and reuse."""
    access_token, err = _refresh_access_token()
    if err:
        return None, err
    body = {"site": {"type": "INET_DOMAIN", "identifier": domain_name}, "verificationMethod": "DNS_TXT"}
    data, err = _post_full(f"{API_BASE}/token", body, access_token)
    if err:
        return None, _explain_error(err)
    return data.get("token"), None


def attempt_verify(domain_name: str):
    """Try to complete verification now that (hopefully) the TXT record is
    live in DNS. Returns (verified: bool, error). Google rejecting this
    because the record isn't there yet is the ordinary, expected case while
    DNS propagates -- reported as (False, None), not alarming; safe to call
    on every check cycle until it succeeds. A genuine setup gate (scope/API
    not enabled) is distinguished and surfaced instead."""
    access_token, err = _refresh_access_token()
    if err:
        return False, err
    body = {"site": {"type": "INET_DOMAIN", "identifier": domain_name}}
    data, err = _post_full(f"{API_BASE}/webResource?verificationMethod=DNS_TXT", body, access_token)
    if err:
        explained = _explain_error(err)
        return False, (explained if explained != err else None)  # unrecognised 4xx -> just "not yet", not alarming
    return True, None
