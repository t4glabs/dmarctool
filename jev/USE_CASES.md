# 100 concrete Jev use cases inside DMARCTool's existing features

Grounded in the real modules and action-item categories that exist in this codebase today (not
hypothetical future features). Each is a real decision point where Claude, going forward, should
consult Jev via `app/jev_context.py::ask_with_context()` using the relevant `CRITERIA.md` questions,
rather than deciding on judgment alone. Grouped by area; numbered for reference from `DECISIONS_LOG.md`.

## A. Report section framing (`app/domain_report.py`)
1. Does a new `_PROBLEM_STORY` entry for a freshly-added category pass `audience_fit` before shipping?
2. Does a new `_TIP_LIBRARY` entry pass `actionability` — a real next step, not vague reassurance?
3. Does the headline verdict sentence for an "all clear" period pass `repetition_risk` against the last
   3 real headlines sent to the same domain?
4. When `_whats_working` renders a streak line, does the exact phrasing pass `repetition_risk` if the
   same streak bucket (e.g. "3 months") repeated last cycle?
5. Does a rewritten `_explain_policy_for_owner` sentence pass `honesty_calibration` against the real
   `disposition` value it's describing?
6. Before adding any new recurring reassurance line (the kind the domain-paid-up line used to be), does
   it pass `usefulness` at a score that justifies appearing every single cycle?
7. Does the cumulative "care ledger" sentence's phrasing pass `emotional_resonance` — does it read as
   evidence of an ongoing relationship, or as a dry running total?
8. When two `_PROBLEM_STORY` entries could both apply to the same underlying event, does the chosen one
   pass `contradiction_check` against whichever one Chapter 1/2 already fixed nearby?
9. Does the sent-volume growth/drop sentence (`deliverability` string) pass `audience_fit` — will
   "sent 40% more than last month" register as good or alarming without more framing?
10. Before shipping wording for a brand-new category with no historical report to compare against, does
    a synthetic first-draft pass `audience_fit` and `actionability` before it ever reaches a real client?

## B. Wow-factor, impersonation & threat framing (`analysis.py` spoof/look-alike detection)
11. Does a caught-spoof summary sentence pass `honesty_calibration` — "blocked" only if disposition
    actually stopped it, "seen and logged" otherwise?
12. Does the count-plus-examples framing (fake identity, source country, date) pass `emotional_resonance`
    without also tipping into `audience_fit` failure (too much raw technical detail per example)?
13. When a look-alike domain is newly found and marked "reviewed, looks harmless," does the reassurance
    sentence pass `honesty_calibration` — not implying zero future risk, just current status?
14. If a previously-benign look-alike domain's registration/hosting changes in a way that looks more
    threatening, does the escalated wording pass `emotional_resonance` at real urgency without also
    tripping `audience_fit` into alarming-without-explanation?
15. Does the "your sending IP came off a blocklist" celebration sentence pass `repetition_risk` if the
    same IP has bounced on/off a blocklist multiple times (is this still a win, or now noise)?
16. Before adding a NEW threat-detection category (e.g. a new spoofing pattern), does its first-draft
    report sentence pass all of `audience_fit`, `honesty_calibration`, and `emotional_resonance`?
17. Does a cross-domain source-classification finding (source serving multiple tracked domains) pass
    `audience_fit` when explained to a reader who only understands their own single domain?
18. When a forwarding-classified source used to look like an attack and is now correctly excluded, does
    the "this is not a threat" framing pass `honesty_calibration` without underselling that it WAS
    checked?
19. Does a "we checked and this is a known/authorized report collector" sentence (report-address
    authorization) pass `usefulness` — does a non-technical reader need to know this at all, or is it
    purely an operator-facing fact?
20. For the DNSBL/blocklist check specifically, does the wording distinguishing "on a blocklist" from
    "borderline/informational-only list" pass `honesty_calibration`?

## C. Deliverability status & reputation signals (`mailgun.py`, `postmaster.py`, `ses_account.py`)
21. Does the Postmaster spam-rate sentence pass `honesty_calibration` when the real verdict is a
    low-volume `NEEDS_WORK` rather than an actual measured complaint problem?
22. Does a Postmaster `complianceStatus` failure (SPF/DKIM/DMARC/TLS/PTR/unsubscribe) translate into
    report wording that passes `audience_fit` without leaking the raw verdict name?
23. Does the SES account-level enforcement-status sentence (from `ses_account.py`) pass `usefulness` —
    is this operator-only, or does the reader need to know?
24. Does a Mailgun reputation-tip rewrite pass `actionability` — a specific fix Aikyam will do, not a
    generic "watch your sending reputation"?
25. Does a chronic-transient-bounce cleanup sentence pass `contradiction_check` against any nearby
    sentence describing suppressions/list hygiene, so the two don't read as duplicated claims?
26. Does the sent-volume comparison threshold (currently ±15%, gated on `prev_total/total >= 20`) produce
    a sentence that passes `usefulness` at the boundary volumes, or does it read as noise near the floor?
27. Does a newly-registered-with-Postmaster domain's first report mention pass `emotional_resonance` —
    framed as "we set this up for you" rather than a dry technical fact?
28. Does the DKIM-weak-key tip pass `actionability` for a reader who has no idea what a key length is?
29. Does the SPF-lookup-budget tip (which SPF include is spending the budget) pass `audience_fit` — can
    this be explained without the word "lookup" at all?
30. Does the MTA-STS/TLS-RPT "informational, not yet set up" framing pass `honesty_calibration` — clearly
    optional/newer, not implied as a current failure?

## D. Historical, streak & repetition management (`domain_report.py`, `analysis.py`)
31. Does a `_pass_rate_streak_days` sentence pass `repetition_risk` at each of its rounding buckets (a
    week, a month, three months) — does the reader notice it's basically saying the same thing each time?
32. Does the recurrence note on a repeated problem pass `emotional_resonance` — appropriately serious
    without reading as an accusation the reader did something wrong?
33. Does a multi-month health-score trajectory sentence, once enough history exists, pass `audience_fit`
    for a reader who's never seen a trend line described in words before?
34. When a category flips from "still open" to "resolved" then back to "still open" across cycles
    (a real recurring issue), does the framing pass `contradiction_check` against the immediately
    preceding cycle's resolved framing?
35. Does the `_ALL_CLEAR_PHRASES` rotation actually reduce `repetition_risk` in practice, or do the 3
    variants still read as interchangeable to Jev?
36. For a domain that has been quiet/no-incidents for a very long streak (6+ months), does the
    reassurance sentence pass `usefulness` — is there a point past which "still fine" stops being worth a
    full sentence?
37. Does the `_coverage_expansion_note` ("we found and started watching an address") pass
    `emotional_resonance` as a proactive-care signal rather than reading as "we found a problem with your
    setup"?
38. When a resolved item is mentioned for the first time next cycle as "no longer an issue," does that
    exact wording pass `repetition_risk` if it appears again the cycle after (should it disappear
    entirely by cycle 3)?
39. Does the ledger's running total ("N resolved since we started") pass `emotional_resonance` at very
    low N (1-2) versus higher N (10+) — does the sentence need different framing at each range?
40. Before adding ANY new recurring status line, does a projected 6-cycle simulation of its wording pass
    `repetition_risk` before it ships (not just the first cycle's version)?

## E. Tips & recommendations quality (`_TIP_LIBRARY`, `_WHY_IT_MATTERS`)
41. Does a rewritten generic-DNS tip (shared across `spf_missing`/`dns_missing`/`dkim_missing`/etc.) pass
    `audience_fit` for EACH of the categories it's reused across, not just the first one tested?
42. Does a `_WHY_IT_MATTERS` sentence pass `emotional_resonance` — does it connect the technical fact to
    a real consequence (funder trust, donor scam risk) rather than restating the technical fact?
43. Does a tip's specificity pass `actionability` when the underlying fix genuinely requires Aikyam's own
    action (not the reader's) — is that made clear so the reader isn't left thinking THEY need to do
    something?
44. Does a newly-written `dkim_alignment_gap` tip pass `audience_fit` — can "aligns" be explained without
    using the word "align"?
45. Before merging two tips that currently read almost identically (candidate for `_TIP_LIBRARY`
    de-duplication), does a diff of the two pass `repetition_risk` confirming they really are
    redundant, not subtly different?
46. Does an urgent-CTA tip (`_URGENT_STILL_OPEN_CATEGORIES`) pass `honesty_calibration` — is the urgency
    level justified by how serious the underlying issue actually is?
47. Does a housekeeping-only note (list pruning, chronic-bounce cleanup) pass `contradiction_check`
    against being mistakenly framed as either "a problem" or "something fixed"?
48. Does a tip that references a specific real number (e.g. "50 out of 100 suspicious emails") pass
    `honesty_calibration` against the real live rate at time of send, not a stale cached example?
49. When compliance.py surfaces a new Gmail sender-guideline failure type Aikyam hasn't reported on
    before, does its first tip draft pass the full checklist before appearing in ANY real report?
50. Does the domain-expiry urgent tip (now gated at 60 days) pass `emotional_resonance` — serious enough
    to prompt action, not so alarming it reads as an imminent-loss threat when 60 days is comfortable?

## F. New detector rollout (any brand-new `action_items` category)
51. Before a new category ships to ANY real report, does it have a `_PROBLEM_STORY` entry that passes
    `audience_fit`?
52. Does the new category's dashboard label (`labels.py CATEGORY_LABELS`) pass `audience_fit`
    independently of the report wording (dashboard reader vs. report reader are different audiences)?
53. Does the new category's `CATEGORY_HELP`/`CATEGORY_REMEDIATION` tooltip pass `actionability` for the
    operator (Aikyam), distinct from the client-facing tip?
54. Does the new category's placement in `CATEGORY_PRIORITY_ORDER` get sanity-checked against
    `usefulness`/`emotional_resonance` scores of neighboring categories (is it really more/less urgent)?
55. Does the new category avoid `contradiction_check` failures against every EXISTING category it could
    plausibly co-occur with (e.g. a new DKIM category next to `dkim_alignment_gap`)?
56. Before wiring a new category into `_URGENT_STILL_OPEN_CATEGORIES`, does its urgency framing pass
    `honesty_calibration` against how bad the underlying signal genuinely is?
57. Does the new category's minimum-volume/threshold settings (mirroring the `dkim_alignment_gap`
    pattern) get validated to avoid a `usefulness` failure from low-volume false alarms?
58. Does the first REAL instance of a new category (not a synthetic test) get run back through the full
    checklist before its first live report send?
59. If a new category is operator-only (like the `chronic_transient_bounce` dual-treatment pattern), is
    that decision itself justified against `audience_fit` (would a client reader actually want this)?
60. Does a new category's self-dismiss/cleanup logic get checked against `contradiction_check` — could a
    resolved instance and a still-open instance of the same category ever render contradictory report
    lines in the same cycle?

## G. Non-technical explainability of dashboard-only data (deciding what crosses into reports at all)
61. For each dashboard-only metric never yet surfaced in a report, does it pass `usefulness` at a score
    high enough to justify adding it (most raw dashboard numbers should fail this deliberately)?
62. Does the source-classification breakdown (own/forwarding/third-party/suspicious) pass `audience_fit`
    if summarized for a report, or is it operator-only?
63. Does the SPF-lookup-count detail (which include spends the budget) pass `usefulness` for a client
    reader, or does it stay dashboard-only (operator-facing) permanently?
64. Does the per-IP operator label (YAMM etc.) pass `usefulness` for a client report, or does it stay
    dashboard-only since it's about Aikyam's own sending infrastructure choices, not the client's?
65. Does a cross-domain shared-source finding pass `audience_fit` if ever surfaced to a client whose
    domain is only one of several sharing that source?
66. Does the DNSBL-check cache/recency detail (why a check wasn't rerun today) pass `usefulness` for a
    reader, or is "was checked, currently clear" the only client-relevant fact?
67. Does the email-verifier's catch-all/disposable/RCPT-TO probe detail pass `audience_fit` if ever
    referenced for a report about the health of the reader's OWN outbound list (vs. its current
    operator-only usage)?
68. Does the known-bad cross-reference (SES/Mailgun/Listmonk blocklist) pass `usefulness` framed as "we
    already know these addresses bounce, so we skip them" rather than exposing the raw source list?
69. Does the SES suppression-list scope/export detail pass `usefulness` for a reader, or does only the
    aggregate cleanup count belong in a report?
70. Does the IMAP-ingestion "reports auto-pulled" fact ever pass `usefulness` for a client (probably not
    — purely an Aikyam operational detail) versus staying entirely internal?

## H. Newsletter / campaign engagement reporting (`ses_events.py` + `listmonk.py` + `content_scoring.py`)
71. Does a per-campaign open/click summary sentence pass `audience_fit` for a reader who's never seen an
    "open rate" concept before?
72. Does the inactive-subscriber detection framing pass `emotional_resonance` — "keeping your list
    healthy" versus a dry deduplication statistic?
73. Does a content-scoring finding (spam-trigger phrase, ALL-CAPS, image-to-text ratio) pass
    `actionability` when surfaced to the newsletter author, not just logged internally?
74. Does the display-name-guideline check (subject-line content, emoji, gmail.com spoofing) pass
    `audience_fit` when the underlying Gmail rule is genuinely obscure even to a technical reader?
75. Does the one-click-unsubscribe header-compliance finding pass `usefulness` — does a non-technical
    newsletter author need to know this, or is it purely a technical prerequisite Aikyam handles?
76. Does cross-campaign display-name-consistency feedback pass `emotional_resonance` — framed as brand
    trust-building, not a compliance nag?
77. Does a bounce-reason categorization (no-such-user, mailbox-full, blocked) pass `audience_fit` when
    summarized per-campaign rather than per-address?
78. Does the link-shortener structural-scoring finding pass `actionability` — a specific alternative
    suggested, not just "avoid link shorteners"?
79. Does a newsletter-engagement trend (this campaign vs. the org's own past campaigns) pass
    `emotional_resonance` the same way domain-level historical framing does?
80. Before Listmonk sync is re-enabled (currently blocked on a token-permission grant), does the FIRST
    real campaign-content-sourced report run through the full checklist before shipping?

## I. Domain lifecycle & setup status (`check_no_report_history`, expiry, subdomain discovery)
81. Does each of the 6 `check_no_report_history` classification cases have report/dashboard wording that
    independently passes `audience_fit` (a domain with no custom email at all needs very different
    wording from one with email but no DMARC record yet)?
82. Does the "no custom email domain in use" case pass `honesty_calibration` — is DMARCTool confident
    enough in a live DNS check to say this plainly, or should it hedge?
83. Does the domain-expiring-soon urgent tip at the new 60-day threshold pass `emotional_resonance` at
    exactly 60 days versus 10 days remaining — should wording escalate as the deadline nears?
84. Does a newly-discovered untracked sending subdomain's first-mention wording pass `audience_fit` —
    can "subdomain" be explained plainly, or does it need an analogy?
85. Does the acknowledged-subdomain fix (no longer re-flagging after dismissal) get validated with
    `contradiction_check` against any report wording that assumes it's still open?
86. Does a domain that ramps from `p=none` to `p=quarantine` to `p=reject` over real months have
    escalation wording that passes `emotional_resonance` at each step (progress, not a new problem)?
87. Does the "same level as last time, still watching" policy-unchanged sentence pass `repetition_risk`
    across a genuinely long unchanged stretch (many months at the same policy level)?
88. Does a dormant multi-month health-trajectory feature's FIRST real appearance (once enough history
    exists) get validated against the full checklist before its first live send?
89. Does the ramp-recommendation logic's suggested next policy step have client-facing wording (if ever
    surfaced) that passes `actionability` — clear about whose action it requires (Aikyam's, not the
    reader's)?
90. Does a domain that's been dormant/inactive for a long period (no real mail sent) have report wording
    that passes `honesty_calibration` — "nothing to report because no mail was sent" rather than a
    fabricated all-clear.

## J. Cross-report / portfolio-wide consistency
91. When the SAME underlying category fires on two different real domains in the same cycle, do the two
    reports' wording pass `contradiction_check` against each other for consistency (not literally
    identical, but not contradictory in tone/severity either)?
92. Does a newly-rewritten shared tip (used across multiple categories) get validated against
    `audience_fit` separately for EACH domain type in the portfolio (a domain with heavy newsletter
    volume vs. one with almost none)?
93. Before a portfolio-wide audit like Chapter 1/2, does the SAMPLE of reports chosen for review pass a
    basic representativeness check (real data, not just-started periods) before trusting the audit's
    conclusions?
94. Does a systemic fix found via user pushback (the DKIM-alignment blind-spot pattern) get re-validated
    against EVERY domain in the portfolio, not just the one that prompted the question, before being
    marked resolved?
95. Does the report-send interval setting (`interval_days`, currently ~30) interact correctly with any
    time-sensitive wording (streaks, expiry, recurrence notes) across the actual range of intervals in
    use across the portfolio (are all domains on the same interval)?
96. Does a report re-derived historically (via direct `_build_context()` calls, as used to validate the
    streak-framing fix) get compared against what a live/current period would have produced, to catch
    drift between the two code paths?
97. When multiple detectors fire in the same cycle for the same domain, does the resulting report's
    section order still read coherently as a single narrative, or does it read as an unordered dump —
    checked via `usefulness` on the WHOLE report, not just each section in isolation?
98. Does a domain with a genuinely quiet cycle (nothing new, nothing resolved) get a report that still
    passes `usefulness` overall, or does the whole-report checklist reveal it should be a shorter,
    different-shaped report rather than the full template with empty sections?
99. Before any change ships to `_TIP_LIBRARY`/`_PROBLEM_STORY` that's shared across the WHOLE portfolio,
    does it get spot-checked against at least 2 real, different domains' actual current state (not just
    one)?
100. Does this very use-case list itself get periodically re-validated against real report content as
    the tool grows — are there real decision points made in the last few sessions that aren't yet
    represented here, and should be added?
