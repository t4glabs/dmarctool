"""
Wraps app.jev_client.ask() with the standing audience/voice context and the
standard criteria checklist documented in jev/CONTEXT.md and jev/CRITERIA.md.
This is the code half of that workflow -- see jev/WORKFLOW.md for the process
these functions are meant to be called from.

Hard rule: nothing from secrets.env is ever part of a Jev call. This module
never touches app.config.get_secret() for anything except letting jev_client
authenticate the request; AUDIENCE_CONTEXT and CRITERIA below are static,
secret-free business context.
"""

from app.jev_client import ask

# Kept in sync by hand with jev/CONTEXT.md -- that file is the human-readable
# source of truth; this constant is what's actually transmitted. Update both
# together.
AUDIENCE_CONTEXT = """\
Aikyam is a nonprofit that helps other nonprofits and grassroots movements with accessible technology
for social good. DMARCTool ("vigil") manages email authentication and deliverability on behalf of a
portfolio of real client domains so those orgs never have to understand DMARC/SPF/DKIM themselves.

The readers of the content being judged are staff at small NGOs with no technical background. They fear
technology, don't know what tools exist or how to use them, and are trusting Aikyam to handle something
they can't verify themselves. The report must make them feel SAFE and that trusting Aikyam was the right
call -- not lectured, not alarmed without a clear reason, not bored by repetition.

Voice rules: zero DMARC/SPF/DKIM/policy/percent jargon -- analogies instead. No raw internal category
codes ever leak into reader-facing text. No filler sections when nothing specific applies. No vague or
alarming language that can't name what/where/how. Praise must be gated on ALL related signals, not just
one, or it risks contradicting a problem mentioned elsewhere in the same report.

The single highest-value thing this tool does is catching people trying to impersonate these orgs
(spoofing attempts, look-alike domains) -- this should read as a wow-factor trust signal, but ONLY ever
described as strongly as what actually happened: "blocked" only if enforcement actually stopped it,
"seen and logged" if it was only detected. A look-alike domain currently reviewed and marked harmless
must never be described as "no longer a risk" -- only "checked, currently looks harmless."

Reports go out on a roughly monthly cadence to the same reader repeatedly, so unchanged facts restated
verbatim read as boilerplate over time -- referencing real streak/duration, or explicitly noting "no
change since last time," reads better than silently repeating the same paragraph.

This tool deliberately stays scoped to what THIS audience needs (a monthly, plain-language,
trust-building relationship) rather than chasing generic B2B SaaS completeness -- judge usefulness
against that, not against what a technical competitor product would ship.
"""

# Reusable Jev question templates -- see jev/CRITERIA.md for the full rationale
# behind each one. Keys here are the `criteria_names` callers pass to
# ask_with_context().
CRITERIA = {
    "audience_fit": {
        "type": "choice",
        "instructions": (
            "Is this understandable to a non-technical NGO staffer with zero DMARC/email-infrastructure "
            "background, with no jargon requiring a definition?"
        ),
        "criteria": {
            "clear_as_is": "A non-technical reader would understand this without help.",
            "needs_plain_language_pass": "Mostly clear but contains at least one term or phrase that would confuse this reader.",
            "too_technical_reader_will_skip": "Dense enough with jargon or technical framing that this reader would likely skip or skim past it.",
        },
    },
    "usefulness": {
        "type": "score",
        "instructions": (
            "How much does this help the reader feel safer about their email, or take a concrete next "
            "action -- versus being noise that doesn't change what they understand or do?"
        ),
        "criteria": [
            "Pure noise or filler -- changes nothing about what the reader understands or does.",
            "Mostly filler, a small amount of real signal.",
            "Some real value but easy to skip without losing much.",
            "Clearly useful -- adds real understanding or a real next step.",
            "Directly changes what the reader understands or does next.",
        ],
    },
    "repetition_risk": {
        "type": "choice",
        "instructions": (
            "Given this exact wording has plausibly appeared in a prior monthly report to the same "
            "reader, will it read as fresh information or as boilerplate they'll skip?"
        ),
        "criteria": {
            "fresh_or_appropriately_reframed": "Reads as new information, or is explicitly reframed against real history (a streak, a change since last time).",
            "borderline_needs_variation": "Plausible but generic enough that repeating it verbatim next cycle would start to feel like boilerplate.",
            "reads_as_boilerplate": "Generic enough already that a reader who saw last month's report would recognize it as the same filler.",
        },
    },
    "emotional_resonance": {
        "type": "score",
        "instructions": (
            "Does this content help the reader feel that trusting Aikyam with their email was the right "
            "call -- safety, protection, a threat that was caught, improvement visible over their own past?"
        ),
        "criteria": [
            "Flat and procedural -- no emotional resonance either way.",
            "Slightly reassuring but easy to not notice.",
            "Moderately reassuring or protective in tone.",
            "Clearly makes the reader feel looked after.",
            "A genuine 'wow, glad someone's watching this' moment.",
        ],
    },
    "honesty_calibration": {
        "type": "choice",
        "instructions": (
            "Does the strength of the claim (e.g. 'blocked' vs 'seen and logged', 'safe' vs 'reviewed "
            "and currently harmless') match exactly what the underlying data actually shows, with "
            "nothing overstated?"
        ),
        "criteria": {
            "accurately_calibrated": "The claim's strength matches the underlying evidence exactly.",
            "understates_a_real_win": "The underlying evidence supports a stronger, more reassuring claim than what's written.",
            "overstates_beyond_the_evidence": "The claim is stronger than what the underlying evidence actually supports.",
        },
    },
    "contradiction_check": {
        "type": "noul",
        "instructions": (
            "Considered next to the rest of the same report, does this content ever imply the opposite "
            "of something else stated in the same document (e.g. praised as reliable in one section, "
            "flagged as a live problem in another)?"
        ),
    },
    "actionability": {
        "type": "choice",
        "instructions": (
            "If this is advice rather than a status update, is there a specific, concrete next step the "
            "reader (or Aikyam on their behalf) can take -- or is it vague enough that no one would know "
            "what to actually do?"
        ),
        "criteria": {
            "concrete_next_step": "Names a specific, concrete action.",
            "vague_needs_specifics": "Gestures at a fix without naming a concrete action.",
            "not_applicable_this_is_status_not_advice": "This is a status update, not advice -- actionability doesn't apply.",
        },
    },
}


def ask_with_context(state: str, criteria_names: list[str], timeout: float = 15.0):
    """Runs the named CRITERIA templates against `state`, with AUDIENCE_CONTEXT
    automatically prepended so Jev always judges content against the full
    audience/voice/history picture, not an isolated snippet. `criteria_names`
    are keys into CRITERIA, e.g. ["audience_fit", "repetition_risk"] -- see
    jev/WORKFLOW.md step 2 for how to pick which ones apply. Returns the same
    (answers_dict_or_None, error_str_or_None) shape as jev_client.ask()."""
    questions = {name: CRITERIA[name] for name in criteria_names}
    full_state = f"{AUDIENCE_CONTEXT}\n---\nContent being evaluated:\n{state}"
    return ask(full_state, questions, timeout=timeout)
