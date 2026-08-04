"""
Local secrets loader.

API keys live in secrets.env (KEY=VALUE per line, chmod 600, project root) --
never in source, never in the SQLite DB. Add new lines there as more
integrations (Mailgun, Postmaster Tools, etc.) come online.
"""

from pathlib import Path

SECRETS_PATH = Path(__file__).resolve().parent.parent / "secrets.env"


def load_secrets() -> dict:
    secrets = {}
    if not SECRETS_PATH.exists():
        return secrets
    for line in SECRETS_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        secrets[key.strip()] = value.strip()
    return secrets


def get_secret(name: str):
    return load_secrets().get(name)
