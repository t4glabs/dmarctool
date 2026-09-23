"""
TypeSafe AI's "Jev" -- a structured-decision model, not a chat/text-generation
model. Given a piece of content (`state`) and one or more typed questions
(Noul = yes/no probability, Choice = pick one of N options, Score = rate
against an ordered rubric), it returns calibrated probabilities/confidence,
never freeform prose. That makes it a poor fit for writing report copy (see
app.domain_report's hand-tuned voice rules -- Jev can't generate text at all),
but a good fit for classification/scoring decisions: e.g. "should this fact
be included in a report for a non-technical reader?"

Uses stdlib urllib (no new dependency, matching mailgun.py/listmonk.py/
postmaster.py) rather than the `typesafe_sdk` PyPI package -- the raw HTTP
API is a single Bearer-auth JSON POST, not worth a dependency for.
"""

import json
import urllib.error
import urllib.request

from app.config import get_secret

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"


def ask(state, questions: dict, timeout: float = 15.0):
    """Runs one or more typed questions against Jev for a single `state`
    (the content being evaluated). `questions` is {name: question_dict},
    e.g. {"relevance": {"type": "choice", "instructions": "...",
    "criteria": {"include": "...", "skip": "..."}}}. Returns
    (answers_dict_or_None, error_str_or_None) -- answers_dict is exactly
    the API's own `answers` map (each entry has type/choice-or-score-or-
    noul/probabilities/confidence), so callers read it the same way the
    API documents it, no extra translation layer."""
    api_key = get_secret("JEV_API_KEY")
    if not api_key:
        return None, "missing JEV_API_KEY in secrets.env"

    body = json.dumps({"state": state, "model": MODEL, "questions": questions}).encode()
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        return None, f"HTTP {e.code}: {detail[:300]}"
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        return None, str(e)
