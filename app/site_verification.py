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

from app.postmaster import _post, _refresh_access_token

API_BASE = "https://www.googleapis.com/siteVerification/v1"

_REAUTH_HINT = ("Postmaster auth doesn't have the domain-verification permission yet -- run "
                "`python -m app.postmaster_auth` once (mints a fresh token with the missing scope "
                "added) and this will start working automatically.")


def _looks_like_missing_scope(err: str) -> bool:
    low = (err or "").lower()
    return "insufficient" in low or "scope" in low or "forbidden" in low


def get_verification_token(domain_name: str):
    """(token, error). `token` is the exact DNS TXT record VALUE Google
    expects at this domain's root for INET_DOMAIN-level verification (this
    single record also covers every subdomain, matching how Postmaster
    reports work). Stable for a given domain -- safe to cache and reuse."""
    access_token, err = _refresh_access_token()
    if err:
        return None, err
    body = {"site": {"type": "INET_DOMAIN", "identifier": domain_name}, "verificationMethod": "DNS_TXT"}
    data, err = _post(f"{API_BASE}/token", body, access_token)
    if err:
        return None, (_REAUTH_HINT if _looks_like_missing_scope(err) else err)
    return data.get("token"), None


def attempt_verify(domain_name: str):
    """Try to complete verification now that (hopefully) the TXT record is
    live in DNS. Returns (verified: bool, error). Google rejecting this
    because the record isn't there yet is the ordinary, expected case while
    DNS propagates -- NOT treated as an error, just "not yet"; safe to call
    on every check cycle until it succeeds."""
    access_token, err = _refresh_access_token()
    if err:
        return False, err
    body = {"site": {"type": "INET_DOMAIN", "identifier": domain_name}}
    data, err = _post(f"{API_BASE}/webResource?verificationMethod=DNS_TXT", body, access_token)
    if err:
        if _looks_like_missing_scope(err):
            return False, _REAUTH_HINT
        return False, None  # most likely "record not found yet" -- ordinary, not alarming
    return True, None
