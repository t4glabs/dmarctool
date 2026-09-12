"""
Pull DMARC aggregate reports straight from the mailbox over IMAP, retiring the
manual Google Takeout export/import.

Read-only by design. The label folder is opened with EXAMINE (readonly=True) and
messages are fetched with BODY.PEEK, so even though an IMAP app-password
technically grants full mailbox access, this code cannot modify, delete, move, or
even mark-as-read any mail. It only reads the one label folder
(DMARC_IMAP_FOLDER, default "dmarc-reports"), never the rest of the inbox.

Progress is a local high-water mark (last processed IMAP UID) in
imap_ingest_state, so each run fetches only messages newer than the last -- the
mailbox itself is left completely untouched. Idempotent regardless: it reuses
ingest._process_blob (the author left a note that an IMAP step could), so zip/gz/
xml handling and report_id de-duplication are shared with the Takeout path, and
even re-reading a message can never double-count.

stdlib only (imaplib + email). Config in secrets.env:
  DMARC_IMAP_HOST      e.g. imap.gmail.com
  DMARC_IMAP_PORT      993 (default)
  DMARC_IMAP_USER      the mailbox address
  DMARC_IMAP_PASSWORD  a Gmail App Password (not the account password)
  DMARC_IMAP_FOLDER    the label to read (default "dmarc-reports")
"""

import datetime
import email
import imaplib
import re

from app.config import get_secret
from app.ingest import _process_blob

DEFAULT_FOLDER = "dmarc-reports"
SEED_DAYS = 45  # first run / after a UIDVALIDITY reset: how far back to backfill


def _config():
    host = get_secret("DMARC_IMAP_HOST")
    user = get_secret("DMARC_IMAP_USER")
    password = get_secret("DMARC_IMAP_PASSWORD")
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(get_secret("DMARC_IMAP_PORT") or 993),
        "user": user,
        "password": password,
        "folder": get_secret("DMARC_IMAP_FOLDER") or DEFAULT_FOLDER,
    }


def _get_state(conn, folder):
    row = conn.execute(
        "SELECT uidvalidity, last_uid FROM imap_ingest_state WHERE folder=?", (folder,)
    ).fetchone()
    return (row["uidvalidity"], row["last_uid"]) if row else (None, 0)


def _set_state(conn, folder, uidvalidity, last_uid):
    conn.execute(
        """INSERT INTO imap_ingest_state (folder, uidvalidity, last_uid, last_run)
           VALUES (?,?,?,datetime('now'))
           ON CONFLICT(folder) DO UPDATE SET
             uidvalidity=excluded.uidvalidity, last_uid=excluded.last_uid, last_run=excluded.last_run""",
        (folder, uidvalidity, last_uid),
    )
    conn.commit()


def _resolve_folder(M, wanted: str) -> str:
    """Map a configured label name to the real IMAP folder name. Gmail exposes
    a label by its DISPLAY name ("DMARC Reports"), while people often set the
    `label:` search form ("dmarc-reports") -- so match case-, space- and
    hyphen-insensitively against the actual folder list. Falls back to the
    configured value (select will then surface a clear not-found)."""
    def norm(s):
        return s.lower().replace("-", " ").replace("_", " ").strip()

    typ, boxes = M.list()
    if typ != "OK" or not boxes:
        return wanted
    target = norm(wanted)
    for b in boxes:
        s = b.decode(errors="replace")
        m = re.search(r'"([^"]*)"\s*$', s)  # folder name is the last quoted token
        name = m.group(1) if m else s.split()[-1]
        if norm(name) == target:
            return name
    return wanted


def _attachments(raw_message: bytes):
    """(filename, decoded_bytes) for every attachment part -- same shape as
    ingest.iter_attachments_from_mbox, just sourced from an IMAP fetch."""
    msg = email.message_from_bytes(raw_message)
    for part in msg.walk():
        fn = part.get_filename()
        if not fn:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            yield fn, payload


def run_imap_ingest(conn, verbose: bool = True):
    """One pull of new DMARC report emails from the configured label folder.
    Returns a stats dict, or None if not configured / the connection failed
    (so it's a harmless no-op until secrets.env has the creds)."""
    cfg = _config()
    if not cfg:
        if verbose:
            print("[imap] no DMARC_IMAP_* creds in secrets.env -- skipping")
        return None

    stats = {"attachments_seen": 0, "reports_stored": 0, "records_stored": 0, "duplicates": 0, "errors": []}
    try:
        # timeout is load-bearing: without it, the underlying socket has NO
        # timeout at all, so a stalled connection (e.g. a half-open TCP socket
        # right after the laptop wakes from sleep) blocks this call FOREVER --
        # not an exception something could catch, an actual permanent hang.
        # This ran inside the shared background-sweep thread holding the one
        # sqlite connection open, so it froze every other request behind
        # SQLite's lock too -- the whole app looked dead. 30s bounds every
        # operation on this connection (login/select/search/fetch/logout all
        # reuse the same socket), turning a hang into a normal, logged failure.
        M = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], timeout=30)
        M.login(cfg["user"], cfg["password"])
    except Exception as e:
        if verbose:
            print(f"[imap] connect/login failed: {e}")
        return None

    try:
        folder = _resolve_folder(M, cfg["folder"])
        quoted = f'"{folder}"'
        typ, _ = M.select(quoted, readonly=True)  # EXAMINE: cannot modify the mailbox
        if typ != "OK":
            if verbose:
                print(f"[imap] folder {cfg['folder']!r} (resolved to {folder!r}) not found -- check the label name")
            return None

        uidvalidity = None
        typ, val = M.status(quoted, "(UIDVALIDITY)")
        if typ == "OK" and val and val[0]:
            m = re.search(rb"UIDVALIDITY (\d+)", val[0])
            uidvalidity = int(m.group(1)) if m else None

        stored_validity, last_uid = _get_state(conn, cfg["folder"])
        incremental = last_uid and stored_validity is not None and stored_validity == uidvalidity
        if incremental:
            typ, res = M.uid("search", None, f"UID {last_uid + 1}:*")
        else:  # first run or the folder was renumbered -> backfill a window
            since = (datetime.date.today() - datetime.timedelta(days=SEED_DAYS)).strftime("%d-%b-%Y")
            typ, res = M.uid("search", None, f"SINCE {since}")

        uids = sorted(int(x) for x in (res[0].split() if (typ == "OK" and res and res[0]) else []))
        if incremental:
            uids = [u for u in uids if u > last_uid]
        max_uid = last_uid if incremental else 0
        fetched = 0
        # Batch-fetch (one round trip per CHUNK instead of per message -- weeks
        # of reports is otherwise hundreds of slow IMAP round trips) and save
        # the high-water mark after every chunk, so a long first backfill that
        # gets interrupted resumes from where it stopped instead of restarting.
        CHUNK = 50
        for i in range(0, len(uids), CHUNK):
            chunk = uids[i:i + CHUNK]
            typ, msgdata = M.uid("fetch", ",".join(str(u) for u in chunk), "(BODY.PEEK[])")  # PEEK: never sets \Seen
            if typ != "OK":
                continue  # leave max_uid where it was -> this chunk retries next run
            for item in msgdata:
                if isinstance(item, tuple) and item[1]:
                    for fn, payload in _attachments(item[1]):
                        _process_blob(conn, fn, payload, f"imap:{folder}", stats)
            fetched += len(chunk)
            max_uid = max(max_uid, chunk[-1])
            _set_state(conn, cfg["folder"], uidvalidity, max_uid)

        _set_state(conn, cfg["folder"], uidvalidity, max_uid)
    finally:
        try:
            M.logout()
        except Exception:
            pass

    if verbose:
        print(f"[imap] {fetched} new message(s), {stats['reports_stored']} report(s) stored, "
              f"{stats['duplicates']} duplicate(s), {len(stats['errors'])} error(s)")
    return stats


def main():
    from app.db import get_connection, init_db
    conn = get_connection()
    init_db(conn)
    run_imap_ingest(conn)


if __name__ == "__main__":
    main()
