"""
Heuristic spam-trigger scoring for newsletter text -- the "you shouldn't
write like this, don't use these words" check requested alongside the other
Gmail sender-guideline work (https://support.google.com/mail/answer/81126).

score_text() works on any block of text, so the same function scores subject
lines (real text already available via the SES event pipeline) today, and
full message bodies once Listmonk API access is wired in to fetch campaign
content -- SES events never carry the message body, only headers.

This is a best-effort heuristic, not a spam-filter simulation. High-risk
phrases are the ones consistently called out across deliverability guidance
(financial/urgency/too-good-to-be-true bait); formatting issues (shouting,
excessive punctuation, excessive emoji) are checked separately since those
trip filters regardless of wording. Curated deliberately short rather than
an exhaustive several-hundred-word list, since long generic "spam word"
lists are notorious for false-positiving on ordinary, legitimate copy (e.g.
a jobs newsletter legitimately using "opportunity" or "apply now").
"""

import re

# (phrase, weight) -- weight is roughly "how confidently is this actually a
# spam signal on its own", not just "does it ever appear in spam".
_HIGH_RISK_PHRASES = {
    "act now": 3, "apply now and win": 4, "call now": 2, "cash bonus": 3,
    "click here": 2, "credit card offers": 3,
    "double your": 3, "earn extra cash": 4, "eliminate debt": 4, "extra cash": 3,
    "for only $": 2, "free gift": 3, "free money": 4, "get out of debt": 4,
    "guaranteed": 2, "limited time offer": 3, "lose weight fast": 4,
    "lowest price": 2, "make money fast": 5, "meet singles": 5,
    "no credit check": 4, "no obligation": 2, "no strings attached": 3,
    "not spam": 4, "once in a lifetime": 2, "order now": 2,
    "pre-approved": 3, "risk-free": 2, "satisfaction guaranteed": 2,
    "act immediately": 3, "urgent response required": 4, "winner": 2,
    "work from home": 2, "100% free": 3,
}
# Regexes for phrases with common wording variants a literal substring would
# miss (e.g. "you've won" vs "you have won").
_HIGH_RISK_PATTERNS = {
    re.compile(r"congratulations,?\s+you('ve| have)\s+(been\s+)?won", re.I): ("congratulations, you've won", 5),
    re.compile(r"you('ve| have)\s+been\s+selected", re.I): ("you've been selected", 4),
}
_URGENCY_WORDS = ("urgent", "last chance", "final notice", "act fast", "hurry", "don't miss out", "expires today")

_EXCLAMATION_RE = re.compile(r"!{2,}")
_DOLLAR_RE = re.compile(r"\${2,}|\$\d")
_ALL_CAPS_WORD_RE = re.compile(r"\b[A-Z]{4,}\b")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)


def score_text(text: str) -> dict:
    """Returns {"score": int, "flags": [str, ...]}. Higher score = more
    spam-trigger-like. A handful of points is normal noise; treat this as a
    prioritization signal, not a verdict -- always let a human read the
    actual copy before deciding."""
    if not text:
        return {"score": 0, "flags": []}

    lower = text.lower()
    score = 0
    flags = []

    matched_phrases = [p for p in _HIGH_RISK_PHRASES if p in lower]
    for phrase in matched_phrases:
        # Skip if this match is wholly contained in another matched phrase (e.g.
        # "extra cash" inside "earn extra cash", "guaranteed" inside "satisfaction
        # guaranteed") -- same signal, shouldn't score/flag twice.
        if any(phrase != other and phrase in other for other in matched_phrases):
            continue
        score += _HIGH_RISK_PHRASES[phrase]
        flags.append(f'Contains "{phrase}"')

    for pattern, (label, weight) in _HIGH_RISK_PATTERNS.items():
        if pattern.search(text):
            score += weight
            flags.append(f'Contains "{label}"-style phrasing')

    urgency_hits = [w for w in _URGENCY_WORDS if w in lower]
    if urgency_hits:
        score += 2 * len(urgency_hits)
        flags.append(f"Urgency wording: {', '.join(urgency_hits)}")

    # >=2 all-caps words is common for legitimate acronym-heavy subjects
    # (e.g. "FCRA Purposes, POSH Audits") -- only flag once it's clearly more
    # than the occasional acronym.
    caps_words = [w for w in _ALL_CAPS_WORD_RE.findall(text) if w not in ("I",)]
    if len(caps_words) >= 3:
        score += 2
        flags.append(f"Multiple ALL-CAPS words ({', '.join(caps_words[:5])})")

    if _EXCLAMATION_RE.search(text):
        score += 2
        flags.append("Multiple exclamation marks in a row")

    if _DOLLAR_RE.search(text):
        score += 2
        flags.append("Dollar signs/amounts -- common in financial spam")

    emoji_count = len(_EMOJI_RE.findall(text))
    if emoji_count >= 3:
        score += 2
        flags.append(f"{emoji_count} emoji -- can read as noisy/low-quality to filters")

    return {"score": score, "flags": flags}


def score_html_structure(image_count: int, word_count: int, shortener_links: list) -> dict:
    """Structural content signals score_text() alone can't see, since
    they're about HTML layout, not wording: an image-heavy email with
    little real text, and links routed through a shortener service (which
    hides the real destination from both spam filters and recipients --
    Gmail's own guidance: "web links in the message body should be visible
    and easy to understand")."""
    score = 0
    flags = []

    if image_count > 0:
        # Roughly: more than 1 image per 50 words of text reads as
        # image-heavy/little-substance, a common spam pattern -- a plain
        # text-only email (word_count > 0, image_count 0) is never flagged.
        ratio = image_count / max(word_count / 50, 1)
        if ratio > 3:
            score += 3
            flags.append(f"Image-heavy: {image_count} image(s) for only {word_count} words of text")

    if shortener_links:
        score += 4
        flags.append(f"{len(shortener_links)} link-shortener URL(s) used ({', '.join(shortener_links[:3])}) -- hides the real destination from filters and recipients")

    return {"score": score, "flags": flags}


def risk_level(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 3:
        return "medium"
    return "low"
