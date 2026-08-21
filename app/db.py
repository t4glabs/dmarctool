import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dmarc.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    """SQLite has no 'ALTER TABLE ADD COLUMN IF NOT EXISTS' -- this makes
    adding a column to an already-existing table (vs. a schema.sql change,
    which only affects fresh installs) idempotent and additive-only, never
    touching existing rows' other columns or data."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    _ensure_columns(conn, "ses_campaign_recipients", {
        "bounced": "INTEGER NOT NULL DEFAULT 0",
        "bounce_reason": "TEXT",
    })
    _ensure_columns(conn, "ses_campaigns", {
        "from_display_name": "TEXT",
        "from_address": "TEXT",
        "message_id": "TEXT",
        "list_unsubscribe": "TEXT",
        "list_unsubscribe_post": "TEXT",
        "rejected": "INTEGER NOT NULL DEFAULT 0",
        "body_text": "TEXT",
        "body_html": "TEXT",
    })
    _ensure_columns(conn, "ses_event_counts", {
        "rejected": "INTEGER NOT NULL DEFAULT 0",
    })
    _ensure_columns(conn, "domain_report_settings", {
        "cc_email": "TEXT DEFAULT 'jinso@aikyamfellows.org'",
    })
    _ensure_columns(conn, "mailgun_identity_stats", {
        "retried_ok": "INTEGER NOT NULL DEFAULT 0",
    })
    _ensure_columns(conn, "mailgun_identity_failures", {
        "subject": "TEXT",
    })
    _ensure_columns(conn, "domains", {
        "pinned": "INTEGER NOT NULL DEFAULT 0",
    })
    conn.commit()


def get_or_create_domain(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip().lower()
    row = conn.execute("SELECT id FROM domains WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO domains (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid
