"""
One-time OAuth authorization for Postmaster Tools v2.

Run this once (python -m app.postmaster_auth): it opens a browser tab for you
to log in with the Google account that has your domains registered at
postmaster.google.com, then captures the resulting refresh token and writes
it to secrets.env. After that, app/postmaster.py can silently refresh its own
access tokens forever (or until you revoke access) -- no browser needed again.

Uses a loopback HTTP server on 127.0.0.1 (RFC 8252) to catch the redirect,
matching the "Desktop app" OAuth client type -- no redirect URI needs to be
pre-registered in Google Cloud Console.
"""

import json
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.config import SECRETS_PATH, get_secret

SCOPES = [
    "https://www.googleapis.com/auth/postmaster.traffic.readonly",
    "https://www.googleapis.com/auth/postmaster.domain",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class _CallbackHandler(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorized. You can close this tab and return to the terminal.</body></html>")

    def log_message(self, fmt, *args):
        pass  # keep stdout clean


def _append_secret(key: str, value: str):
    lines = SECRETS_PATH.read_text().splitlines() if SECRETS_PATH.exists() else []
    lines = [l for l in lines if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    SECRETS_PATH.write_text("\n".join(lines) + "\n")
    SECRETS_PATH.chmod(0o600)


def main():
    client_id = get_secret("GOOGLE_POSTMASTER_CLIENT_ID")
    client_secret = get_secret("GOOGLE_POSTMASTER_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Missing GOOGLE_POSTMASTER_CLIENT_ID / GOOGLE_POSTMASTER_CLIENT_SECRET in secrets.env")
        return

    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_port
    redirect_uri = f"http://127.0.0.1:{port}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # force a refresh token even on re-auth
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("Opening your browser for Google sign-in...")
    print(f"If it doesn't open automatically, visit:\n{url}\n")
    print("Log in with the Google account that has your domains registered at postmaster.google.com.")
    webbrowser.open(url)

    server.handle_request()
    code = _CallbackHandler.code
    server.server_close()

    if not code:
        print("No authorization code received -- aborting.")
        return

    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        tokens = json.loads(resp.read())

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("No refresh_token in the response -- Google only issues one on first consent for this "
              "client/account pair. If you've authorized this exact app before, revoke access at "
              "https://myaccount.google.com/permissions and re-run this script.")
        print(tokens)
        return

    _append_secret("GOOGLE_POSTMASTER_REFRESH_TOKEN", refresh_token)
    print("Done -- refresh token saved to secrets.env. You won't need to do this again.")


if __name__ == "__main__":
    main()
