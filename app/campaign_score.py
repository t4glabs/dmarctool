"""
Evidence-weighted newsletter deliverability scorecard.

Replaces the original additive-demerit report card, which had three
structural problems against real data:

  1. No denominator. It summed "demerit points" with no maximum, so a
     reader couldn't tell whether 2 points was good or catastrophic, and
     "0" was indistinguishable from "we didn't actually check anything".
  2. It never scored engagement at all -- open rate, click rate, and this
     project's own quality-filtered genuine-open/genuine-click numbers were
     computed and then ignored. That's the single most predictive signal
     mailbox providers actually use in 2025, and it was invisible.
  3. Its loudest dimension was spam-trigger wordlists, which is the LEAST
     predictive thing here. Every campaign in this account came out "All
     clear" while a 3x spread in click rate and a rising bounce rate went
     unmentioned.

The weighting below follows what mailbox providers actually enforce and
reward, in roughly descending order of impact:

  * Spam complaint rate -- the one hard, published limit. Gmail requires
    <0.30% and recommends staying under 0.10%; since Nov 2025 Gmail
    rejects at SMTP (5xx) rather than just spam-foldering when bulk
    sender requirements are missed.
    https://support.google.com/a/answer/81126
  * List hygiene (bounces) -- the clearest proxy for list quality, and
    unaffected by open-tracking distortion.
  * Real engagement -- what actually decides inbox vs. Promotions vs.
    Spam once you're authenticated. Clicks are weighted above opens
    because Apple Mail Privacy Protection pre-fetches tracking pixels by
    default, inflating opens (and therefore deflating click-to-open) with
    no reliable way to detect it; clicks and bounces are MPP-immune. See
    app.open_quality's docstring for the same caveat.
  * Technical compliance -- one-click unsubscribe, Message-ID, honest
    subject, sane display name. Mandatory for bulk senders, and cheap to
    get right (usually a one-time ESP config), so it's weighted lower
    than the behavioural signals even though it's non-negotiable.
  * Content structure -- image/text balance, link shorteners, plain-text
    alternative. A real but secondary signal.
  * Spam-trigger wording -- deliberately capped at 5 points, because the
    industry consensus is that keyword lists no longer predict placement.
    Kept because it occasionally catches a genuinely bait-y subject, not
    because it deserves to dominate the grade.

Benchmarks default to nonprofit-sector medians (open ~28.6%, click ~3.3%)
since that's what every domain here actually is, and are settings-tunable.

Every pillar returns a plain-language headline, the reason it matters, and
-- when it isn't full marks -- a concrete fix with an example, so the card
functions as something a non-technical teammate can act on rather than a
number to feel bad about.
"""

# Gmail's own published thresholds (support.google.com/a/answer/81126).
COMPLAINT_RATE_IDEAL = 0.0003   # comfortably inside Google's "stay under 0.10%" advice
COMPLAINT_RATE_WARN = 0.001     # Google's recommended ceiling
COMPLAINT_RATE_LIMIT = 0.003    # Google's hard limit -- above this, mitigation support is withdrawn

# List hygiene. No provider publishes a bounce limit the way they do for
# complaints, so these are conventional deliverability-practice bands.
BOUNCE_RATE_IDEAL = 0.005
BOUNCE_RATE_WARN = 0.02
BOUNCE_RATE_LIMIT = 0.05

# Below this many delivered messages a percentage is noise, not a signal --
# 1 delivered and 11 clicks is a real row in this dataset and would
# otherwise read as "1100% click rate". Rate-based pillars go to "no_data"
# instead of manufacturing a grade out of nothing.
MIN_VOLUME_FOR_RATES = 50

# Don't publish a letter grade unless at least this much of the 100-point
# rubric was actually measurable -- see score_campaign().
MIN_WEIGHT_TO_GRADE = 50

DEFAULT_CLICK_BENCHMARK = 0.033   # nonprofit-sector median click rate (M+R / MailerLite ranges)
DEFAULT_OPEN_BENCHMARK = 0.286    # nonprofit-sector median open rate, MPP-inflated by nature

WEIGHTS = {
    "complaints": 25,
    "hygiene": 20,
    "engagement": 25,
    "technical": 15,
    "structure": 10,
    "wording": 5,
}


def _band(value, ideal, warn, limit):
    """Fraction of credit (1.0 -> 0.0) for a lower-is-better rate, plus a
    status. Full credit at or below `ideal`, zero at or above `limit`,
    linear in between so a rate creeping upward loses points gradually
    rather than snapping from perfect to failed at one threshold."""
    if value <= ideal:
        return 1.0, "ok"
    if value >= limit:
        return 0.0, "bad"
    fraction = 1.0 - (value - ideal) / (limit - ideal)
    return fraction, ("warn" if value >= warn else "ok")


def _target_band(value, target):
    """Fraction of credit for a higher-is-better rate measured against a
    benchmark. Hitting the benchmark is full marks (not a ceiling to beat)
    -- exceeding it doesn't earn extra, since the goal is "healthy", not
    "maximize a vanity metric"."""
    if target <= 0:
        return 1.0, "ok"
    ratio = value / target
    if ratio >= 1.0:
        return 1.0, "ok"
    if ratio >= 0.6:
        return ratio, "ok"
    if ratio >= 0.3:
        return ratio, "warn"
    return ratio, "bad"


def _pct(value):
    return "-" if value is None else f"{value:.2%}"


def _missing_delivery_note(c):
    """Explanation text for a send whose Delivery events were never captured
    at all (see analysis.recent_campaigns' delivery_data_missing). Distinct
    from "too small to measure": nothing here will improve, because SES can't
    backfill events it already discarded."""
    if not c.get("delivery_data_missing"):
        return None
    return (f"Amazon SES never sent us delivery events for this campaign -- it went out before this domain's "
            f"configuration set started publishing to the event pipeline, and SES has no way to backfill them. "
            f"At least {c.get('unique_openers') or 0} people demonstrably received it (they opened it), so rates "
            f"can't be calculated but the send itself was not a failure.")


def _complaints_pillar(c):
    weight = WEIGHTS["complaints"]
    delivered, rate = c["delivered"], c["complaint_rate"]
    if not delivered or delivered < MIN_VOLUME_FOR_RATES:
        return {
            "key": "complaints", "label": "Spam complaints", "weight": weight,
            "earned": None, "status": "no_data",
            "headline": ("Delivery data was never captured for this send"
                          if c.get("delivery_data_missing") else "Not enough delivered mail to measure"),
            "detail": (_missing_delivery_note(c) or
                        f"Only {delivered} message(s) delivered. A complaint rate needs at least "
                        f"{MIN_VOLUME_FOR_RATES} to mean anything."),
            "fix": None,
        }

    fraction, status = _band(rate or 0.0, COMPLAINT_RATE_IDEAL, COMPLAINT_RATE_WARN, COMPLAINT_RATE_LIMIT)
    complained = c["complained"]
    headline = f"{_pct(rate)} marked this as spam ({complained} of {delivered})"
    detail = ("Google's limit is 0.30% and it recommends staying under 0.10%. This is the one number with a "
              "published hard limit -- since late 2025 Gmail can reject mail outright rather than just "
              "spam-foldering it.")
    fix = None
    if status != "ok":
        fix = ("Complaints usually mean people don't remember signing up, or the content drifted from what they "
               "signed up for. Two things that reliably help: make the unsubscribe link obvious in the body "
               "(people who can't find it hit \"spam\" instead), and remind subscribers who you are near the top "
               "-- e.g. \"You're getting this because you subscribed to the PATTIC Forum Digest.\"")
    return {
        "key": "complaints", "label": "Spam complaints", "weight": weight,
        "earned": weight * fraction, "status": status,
        "headline": headline, "detail": detail, "fix": fix,
    }


def _hygiene_pillar(c):
    weight = WEIGHTS["hygiene"]
    delivered, rate = c["delivered"], c["bounce_rate"]
    if not delivered or delivered < MIN_VOLUME_FOR_RATES:
        return {
            "key": "hygiene", "label": "List hygiene (bounces)", "weight": weight,
            "earned": None, "status": "no_data",
            "headline": ("Delivery data was never captured for this send"
                          if c.get("delivery_data_missing") else "Not enough delivered mail to measure"),
            "detail": (_missing_delivery_note(c) or
                        f"Only {delivered} message(s) delivered -- too few for a bounce rate to be meaningful."),
            "fix": None,
        }

    fraction, status = _band(rate or 0.0, BOUNCE_RATE_IDEAL, BOUNCE_RATE_WARN, BOUNCE_RATE_LIMIT)
    headline = f"{_pct(rate)} bounced ({c['bounced']} of {delivered})"
    detail = ("Bounces are the clearest signal of list quality, and unlike open rates they can't be distorted by "
              "privacy features. Under 0.5% is healthy; above 2% providers start reading it as a poorly "
              "maintained list.")
    fix = None
    if status != "ok":
        top = c["bounce_breakdown"][0] if c.get("bounce_breakdown") else None
        cause = f" The most common reason here was \"{top[0]}\" ({top[1]} address(es))." if top else ""
        fix = ("Remove the addresses that permanently bounced from your Listmonk list -- they'll keep bouncing "
               "and dragging this number up every send." + cause +
               " Use the \"Download suppressions\" button at the top of this tab to get the exact list.")
    return {
        "key": "hygiene", "label": "List hygiene (bounces)", "weight": weight,
        "earned": weight * fraction, "status": status,
        "headline": headline, "detail": detail, "fix": fix,
    }


def _engagement_pillar(c, click_benchmark, open_benchmark):
    """Weighted 60/40 toward clicks over opens, because Apple Mail Privacy
    Protection pre-fetches tracking pixels by default -- opens are inflated
    by an undetectable amount, clicks are not.

    Graded on UNIQUE people (unique_click_rate/unique_open_rate), not raw
    event counts and not the automation-filtered "genuine" counts. Both of
    those would be wrong to benchmark: raw events double-count one person
    clicking five links, while the genuine counts are deliberately
    conservative (they strip anything resembling a scanner). Published
    benchmarks are measured as unique openers/clickers over delivered, so
    that's what we compare against; the automation split is reported
    alongside as context instead of silently changing the grade."""
    weight = WEIGHTS["engagement"]
    delivered = c["delivered"]
    if not delivered or delivered < MIN_VOLUME_FOR_RATES:
        return {
            "key": "engagement", "label": "Real engagement", "weight": weight,
            "earned": None, "status": "no_data",
            "headline": ("Delivery data was never captured for this send"
                          if c.get("delivery_data_missing") else "Not enough delivered mail to measure"),
            "detail": (_missing_delivery_note(c) or
                        (f"Only {delivered} message(s) delivered. (This campaign recorded "
                         f"{c['clicked']} click event(s) and {c['opened']} open event(s), but against so few "
                         f"deliveries a percentage would be misleading rather than informative.)")),
            "fix": None,
        }

    # A sending platform with click (or open) tracking switched off looks
    # identical to one where nobody clicked. Scoring that zero against a
    # benchmark would invent a failing grade out of missing instrumentation,
    # so an untracked metric drops out and the other carries the pillar --
    # and if neither is tracked there's nothing to grade at all.
    open_tracked = c.get("open_tracking", True)
    click_tracked = c.get("click_tracking", True)
    # If tracking is on for the sender NOW but this send recorded nothing, the
    # send almost certainly predates it -- Mailgun/ESP tracking only applies to
    # newly sent mail, never retroactively. Worth saying so explicitly, since
    # otherwise "no data" right after enabling tracking looks like it didn't work.
    now_on = []
    if not open_tracked and c.get("tracking_open_setting"):
        now_on.append("open")
    if not click_tracked and c.get("tracking_click_setting"):
        now_on.append("click")
    since_note = ""
    if now_on:
        since_note = (f" {' and '.join(now_on).capitalize()} tracking is switched on for this sender now, so "
                       f"newsletters sent from here on will have it -- tracking never applies retroactively to "
                       f"mail already sent.")

    if not open_tracked and not click_tracked:
        return {
            "key": "engagement", "label": "Real engagement", "weight": weight,
            "earned": None, "status": "no_data",
            "headline": "No open or click data recorded for this send",
            "detail": ("Without it there's no way to tell a newsletter nobody read from one the platform simply "
                        "didn't measure, so this isn't scored rather than scored as zero." + (
                            since_note or " Turning on open and click tracking in your sending platform would make "
                                          "the most important half of this scorecard visible.")),
            "fix": None,
        }

    click_rate = min(c.get("unique_click_rate") or 0.0, 1.0)
    open_rate = min(c.get("unique_open_rate") or 0.0, 1.0)

    click_fraction, click_status = _target_band(click_rate, click_benchmark)
    open_fraction, open_status = _target_band(open_rate, open_benchmark)
    if not click_tracked:
        fraction, click_status = open_fraction, "ok"
    elif not open_tracked:
        fraction, open_status = click_fraction, "ok"
    else:
        fraction = 0.6 * click_fraction + 0.4 * open_fraction
    status = "bad" if fraction < 0.3 else ("warn" if fraction < 0.6 else "ok")

    parts = []
    parts.append(f"{click_rate:.1%} of people clicked" if click_tracked else "no click data")
    parts.append(f"{open_rate:.1%} opened" if open_tracked else "no open data")
    benchmark_bits = []
    if click_tracked:
        benchmark_bits.append(f"{click_benchmark:.1%} clicks")
    if open_tracked:
        benchmark_bits.append(f"{open_benchmark:.1%} opens")
    headline = f"{', '.join(parts)} (benchmark: {' / '.join(benchmark_bits)})"
    automated = (c.get("open_quality") or {}).get("automated") or 0
    automated_note = ""
    if automated:
        automated_note = (f" Of {c['unique_openers']} opener(s), {automated} looked automated (image proxy or "
                           f"security scanner) rather than a person -- so the real figure is likely a little lower.")
    detail = (since_note.strip() + " " if since_note else "") + ("Counted as unique people, not raw events -- one person clicking five links is one engaged reader, "
              "not five. Engagement is what decides inbox vs. Promotions vs. Spam once authentication passes. "
              "Clicks count for more here than opens, because Apple Mail privacy features auto-load tracking "
              "pixels and inflate open counts on every list." + automated_note)
    fix = None
    if status != "ok":
        parts = []
        if click_status != "ok":
            parts.append(
                "Clicks are the weaker half. Give each item one clear, obvious link rather than several competing "
                "ones, and make the link text say what happens (\"View the job\" beats \"Read more\" or \"Click here\")."
            )
        if open_status != "ok":
            parts.append(
                "Opens are the weaker half -- that's a subject-line and sender-recognition problem, not a content "
                "one. Front-load the specific, concrete thing in the subject so it survives being cut off on "
                "mobile: \"New FCRA portal: what changed\" reads better than \"PATTIC Forum Digest, August 2026\"."
            )
        parts.append(
            "If a chunk of the list never opens anything, consider removing them -- providers weigh engagement "
            "per-recipient, so mailing people who never read you actively lowers placement for everyone else. "
            "See the inactive-subscriber report at the bottom of this tab."
        )
        fix = " ".join(parts)
    return {
        "key": "engagement", "label": "Real engagement", "weight": weight,
        "earned": weight * fraction, "status": status,
        "headline": headline, "detail": detail, "fix": fix,
    }


def _technical_pillar(c):
    """One-click unsubscribe, Message-ID, honest subject, sane display
    name -- all mandatory-for-bulk items, and all normally fixed once in
    the ESP rather than per-campaign.

    `unavailable_checks` lets a data source declare what it simply cannot
    see: Mailgun's Events API never exposes List-Unsubscribe headers, so for
    Ghost/Mailgun newsletters that check is unknowable rather than failed.
    Those checks shrink the pillar's denominator instead of counting against
    it -- scoring a campaign down for something we couldn't look at would be
    the same false-signal problem this whole scorecard exists to remove."""
    weight = WEIGHTS["technical"]
    unavailable = list(c.get("technical_checks_unavailable") or [])
    problems = (list(c["unsubscribe_issues"]) + list(c["header_issues"]) + list(c["display_name_issues"]))
    # Four independent things are checked; each failure costs a quarter of
    # the pillar rather than an unbounded per-issue tally.
    checks = max(1, 4 - len(unavailable))
    fraction = max(0.0, (checks - len(problems)) / checks)
    status = "ok" if not problems else ("bad" if len(problems) >= 2 else "warn")
    unavailable_note = (f" Not checkable for this send: {'; '.join(unavailable)}." if unavailable else "")

    if not problems:
        headline = (f"{checks} of {checks} checkable header/sender requirements correct"
                    if unavailable else
                    "One-click unsubscribe, Message-ID, subject and sender name all correct")
        detail = ("These are Google's mandatory requirements for bulk senders (5,000+/day) -- getting all of them "
                  "right is exactly what you want, and it's usually your sending platform's configuration doing "
                  "this for you automatically on every send." + unavailable_note)
        fix = None
    else:
        headline = f"{len(problems)} of {checks} required header/sender checks failed"
        detail = ("Google requires one-click unsubscribe (both List-Unsubscribe and List-Unsubscribe-Post "
                  "headers), an RFC 5322 Message-ID, a subject that isn't a misleading \"Re:\", and a display "
                  "name that identifies you plainly. These are pass/fail, not judgement calls." + unavailable_note)
        fix = ("Specifically: " + "; ".join(problems) +
               ". These are almost always a one-time fix in Listmonk (Settings -> Mailserver) rather than "
               "something to redo per campaign -- fix it once and every future send inherits it.")
    return {
        "key": "technical", "label": "Required headers & sender name", "weight": weight,
        "earned": weight * fraction, "status": status,
        "headline": headline, "detail": detail, "fix": fix,
    }


def _structure_pillar(c, structure):
    """Image/text balance, link shorteners, and whether a plain-text
    alternative exists. Real but secondary signals -- filters look at
    message shape, not just words."""
    weight = WEIGHTS["structure"]
    if not structure or not structure.get("word_count"):
        return {
            "key": "structure", "label": "Layout & links", "weight": weight,
            "earned": None, "status": "no_data",
            "headline": "Newsletter content not available to check",
            "detail": ("The campaign body hasn't been fetched from Listmonk yet, so image/text balance and link "
                        "structure couldn't be analysed for this send."),
            "fix": None,
        }

    from app.content_scoring import score_html_structure

    result = score_html_structure(structure["image_count"], structure["word_count"], structure["shortener_links"])
    flags = list(result["flags"])
    if not c.get("has_plain_text"):
        flags.append("No plain-text alternative found -- HTML-only mail is treated with more suspicion")

    # score_html_structure tops out at 7 (3 for image-heavy + 4 for shorteners).
    fraction = max(0.0, 1.0 - (result["score"] / 7.0)) if result["score"] else 1.0
    if not c.get("has_plain_text"):
        fraction *= 0.75
    status = "ok" if not flags else ("bad" if result["score"] >= 4 else "warn")

    if not flags:
        headline = (f"{structure['image_count']} image(s), {structure['link_count']} link(s), "
                    f"{structure['word_count']} words -- healthy balance")
        detail = ("Enough real text relative to images, no link shorteners, and a plain-text version present. "
                  "Roughly 60% text to 40% images is the usual guidance.")
        fix = None
    else:
        headline = "; ".join(flags)
        detail = ("Filters read message shape as well as wording: an image-dominated email with little text looks "
                  "like the classic 'all the content is inside a picture' spam pattern, and link shorteners hide "
                  "the real destination.")
        fix = ("Add real body text around the images so there's something to read even with images blocked (many "
               "people have them off by default), and always link the actual destination rather than a bit.ly/"
               "tinyurl wrapper -- your own domain in the link also builds recognition.")
    return {
        "key": "structure", "label": "Layout & links", "weight": weight,
        "earned": weight * fraction, "status": status,
        "headline": headline, "detail": detail, "fix": fix,
    }


def _wording_pillar(c):
    """Deliberately the smallest pillar. Keyword lists were how filters
    worked fifteen years ago; today they're one weak content signal inside
    a much larger reputation system, so a bait-y phrase shouldn't be able
    to sink an otherwise healthy send."""
    weight = WEIGHTS["wording"]
    subject_flags = list(c["subject_score"]["flags"])
    body_flags = list(c["body_score"]["flags"]) if c["body_score"] else []
    total = c["subject_score"]["score"] + (c["body_score"]["score"] if c["body_score"] else 0)

    # 10 raw points is already a very bait-y message; treat that as the floor.
    fraction = max(0.0, 1.0 - (total / 10.0))
    status = "ok" if total == 0 else ("bad" if total >= 6 else "warn")

    if total == 0:
        headline = "Nothing that reads as spam-bait wording"
        detail = ("Worth knowing but not worth much: modern filters weigh sender reputation and engagement far "
                  "more heavily than specific words, so this is the least important thing on this card.")
        fix = None
    else:
        where = []
        if subject_flags:
            where.append("Subject: " + "; ".join(subject_flags))
        if body_flags:
            where.append("Body: " + "; ".join(body_flags))
        headline = " | ".join(where)
        detail = ("Only a minor signal -- word lists no longer decide placement on their own. Worth a second look "
                  "mainly because bait-y phrasing tends to correlate with the complaints that DO matter.")
        fix = ("Reword the flagged phrases to describe what's actually in the newsletter. \"Act now\" or \"Limited "
               "time offer\" can become the real thing being announced -- e.g. \"Applications close Friday\". "
               "Plain and specific outperforms urgent and vague anyway.")
    return {
        "key": "wording", "label": "Spam-bait wording", "weight": weight,
        "earned": weight * fraction, "status": status,
        "headline": headline, "detail": detail, "fix": fix,
    }


def _grade(score):
    if score >= 90:
        return "A", "ok"
    if score >= 80:
        return "B", "ok"
    if score >= 70:
        return "C", "warn"
    if score >= 60:
        return "D", "warn"
    return "F", "bad"


_STATUS_RANK = {"bad": 0, "warn": 1, "no_data": 2, "ok": 3}


def score_campaign(c: dict, structure, settings: dict = None) -> dict:
    """The full scorecard for one campaign.

    Returns a 0-100 score and letter grade, plus every pillar split into
    `strengths` (what's going well) and `improvements` (what to fix, worst
    first) so the card reads as guidance rather than a verdict.

    Pillars that genuinely can't be measured (a campaign with almost no
    delivery data -- common here for backfilled sends) report "no_data" and
    are excluded from the denominator rather than scored as zero, so a
    campaign isn't marked down for something we simply couldn't see. When
    that happens `confidence` says so explicitly.
    """
    settings = settings or {}
    click_benchmark = float(settings.get("campaign_click_benchmark", DEFAULT_CLICK_BENCHMARK))
    open_benchmark = float(settings.get("campaign_open_benchmark", DEFAULT_OPEN_BENCHMARK))

    pillars = [
        _complaints_pillar(c),
        _hygiene_pillar(c),
        _engagement_pillar(c, click_benchmark, open_benchmark),
        _technical_pillar(c),
        _structure_pillar(c, structure),
        _wording_pillar(c),
    ]

    scored = [p for p in pillars if p["earned"] is not None]
    available_weight = sum(p["weight"] for p in scored)

    # Refuse to publish a grade off a minority of the rubric. The three
    # behavioural pillars are 70 of the 100 points, so a campaign with no
    # usable delivery data would otherwise score a confident-looking "A/100"
    # purely for having correct headers -- more misleading than saying
    # nothing, and exactly the false reassurance this rework exists to kill.
    if available_weight >= MIN_WEIGHT_TO_GRADE:
        score = round(sum(p["earned"] for p in scored) / available_weight * 100)
        grade, grade_status = _grade(score)
    else:
        score, grade, grade_status = None, "?", "muted"

    measured_rates = c["delivered"] and c["delivered"] >= MIN_VOLUME_FOR_RATES
    if score is None:
        confidence = "none"
    elif not measured_rates:
        confidence = "low"
    else:
        confidence = "high"

    strengths = [p for p in pillars if p["status"] == "ok"]
    improvements = sorted(
        (p for p in pillars if p["status"] in ("warn", "bad")),
        key=lambda p: (_STATUS_RANK[p["status"]], -(p["weight"] - (p["earned"] or 0))),
    )
    unmeasured = [p for p in pillars if p["status"] == "no_data"]

    return {
        "score": score,
        "grade": grade,
        "grade_status": grade_status,
        "confidence": confidence,
        "pillars": pillars,
        "strengths": strengths,
        "improvements": improvements,
        "unmeasured": unmeasured,
    }
