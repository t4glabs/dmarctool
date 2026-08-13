"""
Listmonk API integration -- fetches real newsletter body content so
app.content_scoring can actually "read" a campaign, not just its subject
line. SES events never carry the message body, only headers, so this is
the only way to close that gap.

Scoped deliberately narrow: only fetches content for campaigns DMARCTool
already knows about via the SES event pipeline (ses_campaigns, matched by
the Listmonk campaign UUID) -- not a general Listmonk sync. Listmonk stays
the tool the user actually sends from; this only ever reads.

Uses stdlib `urllib` (no new dependency, matching mailgun.py/postmaster.py)
and the API token in secrets.env. Needs LISTMONK_URL / LISTMONK_API_USERNAME
/ LISTMONK_API_TOKEN. If any are missing, this is a no-op.
"""

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from app.analysis import upsert_system_action
from app.config import get_secret
from app.content_scoring import risk_level, score_html_structure, score_text
from app.db import get_connection, init_db

# Common shortener services -- spam filters distrust these since they hide
# the real destination, one of the content signals in Gmail's own guidance
# ("web links in the message body should be visible and easy to understand").
_SHORTENER_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.co", "ow.ly", "is.gd", "buff.ly", "goo.gl",
    "rebrand.ly", "cutt.ly", "shorturl.at", "rb.gy", "tiny.cc",
}


class _ContentAnalyzer(HTMLParser):
    """Minimal HTML-to-plain-text extractor plus structural stats (image
    count, links, shorteners), stdlib only -- no BeautifulSoup dependency
    for what's just "strip tags, keep readable text, count a few things"."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.image_count = 0
        self.links = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")
        elif tag == "img":
            self.image_count += 1
        elif tag == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def analyze_html(html_text: str) -> dict:
    """Returns {"text", "image_count", "link_count", "shortener_links",
    "word_count"} -- the plain-text extraction plus the structural signals
    (image-to-text ratio, link shorteners) that score_text() alone can't
    see, since those are about HTML structure, not wording."""
    if not html_text:
        return {"text": "", "image_count": 0, "link_count": 0, "shortener_links": [], "word_count": 0}

    parser = _ContentAnalyzer()
    parser.feed(html_text)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    shortener_links = []
    for href in parser.links:
        domain = urllib.parse.urlparse(href).netloc.lower()
        if any(domain == d or domain.endswith("." + d) for d in _SHORTENER_DOMAINS):
            shortener_links.append(href)

    return {
        "text": text,
        "image_count": parser.image_count,
        "link_count": len(parser.links),
        "shortener_links": shortener_links,
        "word_count": len(text.split()),
    }


def _auth_header(user: str, token: str) -> str:
    return f"token {user}:{token}"


def _client():
    url = get_secret("LISTMONK_URL")
    user = get_secret("LISTMONK_API_USERNAME")
    token = get_secret("LISTMONK_API_TOKEN")
    if not (url and user and token):
        return None, None
    return url.rstrip("/"), _auth_header(user, token)


def fetch_all_campaigns(timeout: float = 20.0):
    """{uuid: {"subject":..., "body":..., "name":...}} for every campaign in
    Listmonk. 130 campaigns total as of writing -- cheap to paginate through
    fully rather than needing a per-UUID lookup endpoint (Listmonk's API
    doesn't offer one)."""
    url, auth = _client()
    if not url:
        return {}, "missing LISTMONK_URL/LISTMONK_API_USERNAME/LISTMONK_API_TOKEN"

    out = {}
    page = 1
    per_page = 100
    while True:
        req = urllib.request.Request(
            f"{url}/api/campaigns?page={page}&per_page={per_page}",
            headers={"Authorization": auth},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            return out, str(e)

        results = body.get("data", {}).get("results", [])
        for c in results:
            out[c["uuid"]] = c
        total = body.get("data", {}).get("total", len(out))
        if len(out) >= total or not results:
            break
        page += 1
    return out, None


def run_listmonk_content_sync(conn, verbose: bool = True) -> None:
    """Backfills body_text for any tracked campaign that doesn't have it yet,
    then scores it and raises an action item on a high spam-trigger score --
    same scoring function already used for subject lines, now applied to the
    actual newsletter content the user explicitly asked to have reviewed.

    Also corrects `subject` from Listmonk on every run, for every tracked
    campaign (not just ones missing a body) -- Listmonk is authoritative for
    the subject actually sent (a sent campaign is locked there), whereas
    ses_events.py can only go by whatever a given SES-echoed header happened
    to say, which is wrong if a one-recipient test send (sharing the same
    campaign_id) got processed before the real send with an edited subject."""
    url, _ = _client()
    if not url:
        if verbose:
            print("[listmonk] missing LISTMONK_URL/LISTMONK_API_USERNAME/LISTMONK_API_TOKEN -- skipping")
        return

    tracked = conn.execute(
        "SELECT id, domain_id, campaign_id, subject, body_html FROM ses_campaigns"
    ).fetchall()
    if not tracked:
        if verbose:
            print("[listmonk] no tracked campaigns yet")
        return

    campaigns, err = fetch_all_campaigns()
    if err:
        if verbose:
            print(f"[listmonk] could not fetch campaigns: {err}")
        return

    for row in tracked:
        listmonk_campaign = campaigns.get(row["campaign_id"])
        if not listmonk_campaign:
            continue  # not found in Listmonk (deleted there, or UUID mismatch) -- leave as-is, try again next run

        true_subject = listmonk_campaign.get("subject")
        if true_subject and true_subject != row["subject"]:
            conn.execute("UPDATE ses_campaigns SET subject=? WHERE id=?", (true_subject, row["id"]))
            if verbose:
                print(f"[listmonk] corrected subject for campaign {row['campaign_id']}: {true_subject!r}")

        if row["body_html"] is not None:
            continue  # body already fetched; not re-scoring unchanged content every run

        html = listmonk_campaign.get("body", "") or ""
        info = analyze_html(html)
        conn.execute(
            "UPDATE ses_campaigns SET body_html=?, body_text=? WHERE id=?",
            (html, info["text"], row["id"]),
        )

        text_result = score_text(info["text"])
        structure_result = score_html_structure(info["image_count"], info["word_count"], info["shortener_links"])
        combined_score = text_result["score"] + structure_result["score"]
        combined_flags = text_result["flags"] + structure_result["flags"]

        if risk_level(combined_score) == "high":
            upsert_system_action(
                conn, row["domain_id"], "content_spam_risk", row["campaign_id"],
                f"Newsletter content looks spam-trigger-heavy (newsletter: {true_subject or row['subject'] or row['campaign_id']})",
                " ".join(combined_flags),
            )
        else:
            conn.execute(
                """UPDATE action_items SET status='dismissed', resolved_at=datetime('now')
                   WHERE category='content_spam_risk' AND ref_key=? AND domain_id=? AND status='open'""",
                (row["campaign_id"], row["domain_id"]),
            )
        if verbose:
            print(f"[listmonk] scored content for campaign {row['campaign_id']}: "
                  f"score={combined_score} ({risk_level(combined_score)})")

    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill and score newsletter body content from Listmonk")
    parser.parse_args()
    conn = get_connection()
    init_db(conn)
    run_listmonk_content_sync(conn)


if __name__ == "__main__":
    main()
