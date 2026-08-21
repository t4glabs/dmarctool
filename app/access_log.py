"""
Access/audit log -- who hit this app, when, and what they did.

DMARCTool is reachable through a Cloudflare Tunnel with Cloudflare Access in
front of it (email-OTP login restricted to @aikyamfellows.org/@aikyamhq.com).
Cloudflare Access injects the verified, logged-in email into every request
that passes its check via the Cf-Access-Authenticated-User-Email header --
this module just records that alongside every request this app serves, so
"who did X and when" is answerable from inside the app itself (indefinitely,
subject to our own retention setting) rather than only from Cloudflare's own
dashboard-only Access logs, which have their own (often short) retention.

Not a replacement for Cloudflare's own Access logs (which also capture
denied/failed login attempts that never reach this app at all) -- a
complement, for what actually happened once a request got through.
"""

DEFAULT_RETENTION_DAYS = 90


def actor_from_request(request) -> str:
    email = request.headers.get("cf-access-authenticated-user-email")
    if email:
        return email
    # No Access header at all means this request never went through the
    # Cloudflare Tunnel + Access check -- i.e. it hit 127.0.0.1:8787 directly
    # from this same Mac (the only other way to reach it, since the app
    # binds to loopback only). Worth distinguishing from a real login.
    return "local (direct, no Cloudflare Access)"


def client_ip(request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")


def record_access(conn, request, status_code: int) -> None:
    conn.execute(
        "INSERT INTO access_log (method, path, status_code, actor_email, ip) VALUES (?, ?, ?, ?, ?)",
        (request.method, request.url.path, status_code, actor_from_request(request), client_ip(request)),
    )
    conn.commit()


def recent_access_log(conn, limit: int = 300):
    return conn.execute("SELECT * FROM access_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def prune_old_access_log(conn, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
    conn.execute("DELETE FROM access_log WHERE ts < datetime('now', ?)", (f"-{retention_days} days",))
    conn.commit()
