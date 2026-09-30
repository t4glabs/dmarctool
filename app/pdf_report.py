"""Generates the client-facing domain report as a PDF via Typst (external CLI,
installed at /opt/homebrew/bin/typst -- a system binary dependency, same shape
as `dig` in compliance.py, not a Python package). Reuses the exact same
content/voice layer as the email report (build_domain_report()/_build_context()
in app.domain_report) -- this module is a presentation layer only, it makes no
new content decisions.

Freed from every email-client rendering constraint (see app/charts.py and the
Chapter 23-29 history in jev/DECISIONS_LOG.md): real system fonts, real
vector charts via Typst's native primitives, real layout. No external Typst
packages (no cetz) -- charts are hand-rolled from rect/line/circle, matching
this project's own "no charting library" precedent from app/charts.py.
"""

import datetime
import os
import subprocess
import tempfile


def _typst_str(value) -> str:
    """Escapes a Python value for safe embedding as a Typst string literal.
    Backslash and double-quote are the only characters that need escaping
    inside a Typst string -- markup-mode special characters (#, *, _, [, ])
    have no meaning inside a quoted string, only in markup mode. Always pass
    dynamic content (domain names, recipient labels, newsletter subject
    lines) through this into a Typst function's string parameter -- never
    interpolate raw text directly into markup, which could break compilation
    or (worse) be silently misinterpreted as Typst syntax."""
    if value is None:
        value = ""
    s = str(value)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Absolute path, not a bare "typst" -- launchd runs this service with a
# minimal PATH (no Homebrew prefix; that only exists in an interactive
# shell's own PATH via `brew shellenv`). `dig` (app/compliance.py) gets away
# with a bare name because it lives at /usr/bin/dig, a default system path
# always on launchd's PATH -- Typst, a Homebrew install, is not. Found this
# the hard way: worked from every one of this round's own shell-run test
# scripts, then 500'd with FileNotFoundError the first time it ran through
# the real launchd-managed service.
_TYPST_BIN = "/opt/homebrew/bin/typst"


def _compile_typst(typ_source: str) -> bytes:
    """Compiles a Typst source string to PDF bytes via the real `typst`
    CLI. Raises RuntimeError with the compiler's own stderr on failure --
    a silently-empty or missing PDF would be far worse than a loud error
    here, since this always runs synchronously inside a real report send."""
    with tempfile.TemporaryDirectory() as scratch_dir:
        typ_path = os.path.join(scratch_dir, "report.typ")
        pdf_path = os.path.join(scratch_dir, "report.pdf")
        with open(typ_path, "w") as f:
            f.write(typ_source)
        result = subprocess.run(
            [_TYPST_BIN, "compile", typ_path, pdf_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"typst compile failed:\n{result.stderr}")
        with open(pdf_path, "rb") as f:
            return f.read()


# Design tokens -- carries the hue of the established email/dashboard brand
# tokens (app/static/style.css, app/domain_report.py's _EMAIL_CHART_COLORS)
# forward for identity continuity, adapted for print: white page (not the
# email's warm cream -- print reads cleaner on near-white), one restrained
# accent used sparingly (rules, tile borders, chart lines, the wordmark),
# never as a decorative fill. New York (serif) for the wordmark/headings,
# Helvetica Neue (sans) for body/data -- both confirmed real system fonts,
# no email-client font-stripping risk to hedge against here.
_PREAMBLE = '''
#let ink = rgb("#1F2421")
#let muted = rgb("#7A746B")
#let accent = rgb("#7358B3")
#let ok-color = rgb("#1A7F37")
#let warn-color = rgb("#9A6400")
#let bad-color = rgb("#C0392B")
#let border-color = rgb("#E8E2D8")
#let tile-bg = rgb("#F7F4EF")

#let heading-font = "New York"
#let body-font = "Helvetica Neue"

#set text(font: body-font, size: 10.5pt, fill: ink, lang: "en")
#set par(leading: 0.65em, justify: false)

#show heading: set text(font: heading-font, weight: "regular")
#show heading.where(level: 1): set text(size: 26pt, fill: ink)
#show heading.where(level: 2): set text(size: 14pt, weight: "bold", fill: ink)

#let eyebrow(s) = text(font: body-font, size: 8.5pt, weight: "bold", fill: muted, tracking: 0.08em, upper(s))

#let section-title(s) = [
  #v(0.5cm)
  #heading(level: 2, s)
  #line(length: 100%, stroke: (paint: border-color, thickness: 0.6pt))
  #v(0.3cm)
]

#let stat-tile(label, value, unit: "") = rect(
  fill: tile-bg, stroke: none, radius: 6pt, inset: 14pt, width: 100%,
  [
    #eyebrow(label)
    #v(0.25cm)
    #text(font: body-font, size: 22pt, weight: "bold", fill: ink, value)
    #text(font: body-font, size: 11pt, fill: muted, unit)
  ]
)

#let segment-gauge(label, frac) = {
  let clamped = calc.min(calc.max(frac, 0.0), 1.0)
  [
    #text(font: body-font, size: 9.5pt, fill: muted, label)
    #v(0.15cm)
    #box(width: 100%, height: 10pt, fill: tile-bg, radius: 5pt, clip: true, stroke: none,
      rect(width: clamped * 100%, height: 100%, fill: accent, radius: 5pt, stroke: none)
    )
    #v(0.5cm)
  ]
}

// Native-primitive line chart (no cetz -- see jev/DECISIONS_LOG.md Chapter
// 30/32 for why: Typst has no arc/path support for true donuts, but
// connected-point line charts work cleanly with place()/line()/circle()).
// `data`: array of (label, value) pairs, value already on a real 0..N scale
// (not necessarily 0..1 -- ymax is derived from the data itself, with an
// optional `threshold` line folded into the scale like charts.py's own
// metric_trend_chart does for its dashed reference lines).
#let line-chart(data, height: 120pt, threshold: none) = {
  let n = data.len()
  let plot-h = height - 16pt
  let max-val = data.at(0).at(1)
  for d in data {
    if d.at(1) > max-val { max-val = d.at(1) }
  }
  if threshold != none and threshold > max-val { max-val = threshold }
  let ymax = calc.max(max-val * 1.2, 0.001)
  let last = data.at(n - 1)

  box(width: 100%, height: height + 14pt, fill: tile-bg, radius: 4pt, clip: false, inset: 0pt,
    {
      for i in range(0, 4) {
        let frac = i / 3
        place(top + left, dy: (1 - frac) * plot-h + 8pt, line(length: 100%, stroke: (paint: border-color, thickness: 0.5pt)))
      }
      if threshold != none {
        let ty = (1 - threshold / ymax) * plot-h + 8pt
        place(top + left, dy: ty, line(length: 100%, stroke: (paint: bad-color, thickness: 0.7pt, dash: "dashed")))
      }
      if n > 1 {
        for i in range(n - 1) {
          let (label1, val1) = data.at(i)
          let (label2, val2) = data.at(i + 1)
          let x1 = (i / (n - 1)) * 100%
          let y1 = (1 - val1 / ymax) * plot-h + 8pt
          let x2 = ((i + 1) / (n - 1)) * 100%
          let y2 = (1 - val2 / ymax) * plot-h + 8pt
          place(top + left, dx: x1, dy: y1,
            line(start: (0pt, 0pt), end: (x2 - x1, y2 - y1), stroke: (paint: accent, thickness: 1.6pt))
          )
        }
      }
      let ly = (1 - last.at(1) / ymax) * plot-h + 8pt
      place(top + left, dx: 100%, dy: ly, place(center + horizon, circle(radius: 2.6pt, fill: accent, stroke: none)))
      place(top + left, dx: 0pt, dy: plot-h + 12pt, text(font: body-font, size: 8pt, fill: muted, data.at(0).at(0)))
      place(top + right, dx: 0pt, dy: plot-h + 12pt, text(font: body-font, size: 8pt, fill: muted, last.at(0)))
    }
  )
}
'''


def _page_setup(domain_name: str) -> str:
    """Page-level setup (margins, footer) kept separate from _PREAMBLE since
    it needs the real domain name interpolated -- built fresh per report,
    unlike the fixed helper-function definitions above."""
    return f'''
#set page(
  paper: "a4",
  margin: (top: 2.5cm, bottom: 2.5cm, left: 2.5cm, right: 2.5cm),
  footer: [
    #set text(size: 8.5pt, fill: muted, font: body-font)
    #set align(center)
    #context [#{_typst_str(domain_name)} --- #counter(page).display() / #counter(page).final().first()]
  ]
)
'''


def _masthead_and_kpi(context: dict) -> str:
    """Wordmark, period, greeting, intro, and the KPI stat-tile row -- the
    same 3 numbers the email report's KPI strip shows (Chapter 23-27),
    re-derived from the identical _build_context() dict so the PDF and email
    can never disagree about the same fact. Tile 3 (health-timeline delta)
    stays dormant-but-wired exactly like the email version, for the same
    reason (domain_health_snapshots doesn't have 3+ months of history yet
    portfolio-wide)."""
    parts = [f'''
= aikyam
#eyebrow({_typst_str(f"{context['period_start']} to {context['period_end']}")})

#v(0.3cm)
#text(font: heading-font, size: 17pt, weight: "bold")[Hi #{_typst_str(context['recipient_label'])},]

#v(0.35cm)
#text(size: 10.5pt)[Here's how #text(weight: "bold")[#{_typst_str(context['domain_name'])}]'s emails have been doing lately, in plain terms. No technical jargon, just what happened and what we did about it.]
''']

    tiles = []
    if context.get("delivery_rate_pct") is not None:
        tiles.append(f'stat-tile("Delivered safely", "{context["delivery_rate_pct"]}", unit: "%")')
    if context.get("health_score_value") is not None:
        tiles.append(f'stat-tile("Health score", "{context["health_score_value"]}", unit: "/100")')
    if context.get("health_timeline_delta") is not None:
        delta = context["health_timeline_delta"]
        sign = "+" if delta > 0 else ""
        since = _typst_str(context.get("health_timeline_since") or "")
        tiles.append(f'stat-tile("Since " + {since}, "{sign}{delta}", unit: "pts")')

    if tiles:
        cols = "(1fr, " * (len(tiles) - 1) + "1fr)" if len(tiles) > 1 else "(1fr)"
        tiles_joined = ",\n  ".join(tiles)
        parts.append(f'''
#v(0.6cm)
#grid(columns: {cols}, gutter: 0.6cm,
  {tiles_joined}
)
''')

    if context.get("headline"):
        parts.append(f'''
#v(0.5cm)
#rect(fill: rgb("#EAF4EC"), stroke: none, radius: 6pt, inset: 14pt, width: 100%,
  text(size: 11pt, fill: rgb("#1A5C28"), {_typst_str(context["headline"])})
)
''')

    return "\n".join(parts)


def _short_date(day) -> str:
    """Accepts either a real date/datetime (analysis.daily_pass_series'
    own convention) or an ISO date string (postmaster_daily_series/
    mailgun_daily_series's convention) -- charts.py's pass_rate_sparkline
    hits the same inconsistency and handles it the same way, by just
    calling strftime directly on whichever it already has."""
    if isinstance(day, (datetime.date, datetime.datetime)):
        return day.strftime("%b %-d")
    try:
        return datetime.date.fromisoformat(day).strftime("%b %-d")
    except ValueError:
        return str(day)


def _typst_series(points: list) -> str:
    """points: [(label, value), ...] (value already numeric, no Nones).
    Returns a Typst array-of-pairs literal for line-chart()."""
    pairs = ", ".join(f"({_typst_str(label)}, {value})" for label, value in points)
    return f"({pairs})"


def _capitalize_first(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def _bullet_list(item_bodies: list) -> str:
    """item_bodies: list of already-Typst-safe content strings (built via
    #{_typst_str(...)} escaping, never raw interpolation). Renders as a
    native Typst bullet list."""
    if not item_bodies:
        return ""
    joined = ",\n  ".join(f"[{body}]" for body in item_bodies)
    return f"#list(spacing: 0.55cm, marker: [#text(fill: accent)[•]],\n  {joined}\n)"


def _resolved_item(item: dict) -> str:
    story = _capitalize_first(item["story"]) + "."
    parts = [f'#{_typst_str(story)} ', '#text(weight: "bold")[We’ve taken care of it.]']
    if item.get("why"):
        parts.append(f' #text(fill: muted)[#{_typst_str(item["why"])}]')
    if item.get("impact"):
        parts.append(f' The payoff: #{_typst_str(item["impact"])}.')
    if item.get("history"):
        parts.append(f' #text(fill: muted)[#{_typst_str(item["history"])}]')
    return "".join(parts)


def _still_open_item(item: dict) -> str:
    story = _capitalize_first(item["story"]) + "."
    parts = [f'#{_typst_str(story)}']
    if item.get("detail"):
        parts.append(f' #{_typst_str(item["detail"])}.')
    if item.get("why"):
        parts.append(f' #text(fill: muted)[#{_typst_str(item["why"])}]')
    if item.get("history"):
        parts.append(f' #text(fill: muted)[#{_typst_str(item["history"])}]')
    return "".join(parts)


def _standing_narrative(context: dict) -> str:
    """care_ledger, health_trend/timeline, whats_working -- reuses the exact
    same prose Chapters 1-22 already Jev-validated for the email, just given
    real print typography and room to breathe. No new content decisions."""
    parts = []
    if context.get("care_ledger"):
        parts.append(f'\n#{_typst_str(context["care_ledger"])}\n')
    if context.get("health_trend"):
        parts.append(f'\n#section-title("Where you stand")\n#{_typst_str(context["health_trend"])}\n')
        if context.get("health_timeline"):
            parts.append(f'\n#v(0.3cm)\n#text(fill: muted)[#{_typst_str(context["health_timeline"])}]\n')
    if context.get("whats_working"):
        bullets = [f'#{_typst_str(item)}' for item in context["whats_working"]]
        parts.append(f'''
#v(0.4cm)
#rect(fill: rgb("#EAF4EC"), stroke: none, radius: 8pt, inset: 16pt, width: 100%,
  [
    #text(font: heading-font, weight: "bold", size: 12pt, fill: ok-color)[What's already working for you]
    #v(0.3cm)
    {_bullet_list(bullets)}
  ]
)
''')
    return "\n".join(parts)


def _action_ledger(context: dict) -> str:
    """resolved + still_open (+ contact_cta) + risk_warning, and the
    "nothing needed fixing" fallback -- gated on `not headline` exactly like
    the email template's own Chapter 29 fix, so a true all-clear domain never
    shows the same "nothing to worry about" message twice (once in the
    masthead headline, once here)."""
    parts = []
    if context.get("resolved"):
        bullets = [_resolved_item(item) for item in context["resolved"]]
        parts.append(f'''
#section-title("What we sorted out for you")
{_bullet_list(bullets)}
''')
    if context.get("still_open"):
        bullets = [_still_open_item(item) for item in context["still_open"]]
        parts.append(f'''
#section-title("What we're still working on")
{_bullet_list(bullets)}
#v(0.3cm)
We're already working through all of this, and we'll let you know as each one clears.
''')
        if context.get("contact_cta"):
            parts.append(f'\n#v(0.2cm)\n#{_typst_str(context["contact_cta"])}\n')
    if not context.get("resolved") and not context.get("still_open") and not context.get("headline"):
        parts.append('''
#rect(fill: rgb("#EAF4EC"), stroke: none, radius: 8pt, inset: 16pt, width: 100%,
  text(fill: ok-color)[Nothing needed fixing this time, everything's running smoothly.]
)
''')
    if context.get("risk_warning"):
        parts.append(f'''
#v(0.4cm)
#rect(fill: rgb("#FBEBE8"), stroke: (paint: rgb("#EAC6C1"), thickness: 1pt), radius: 8pt, inset: 16pt, width: 100%,
  [
    #text(font: heading-font, weight: "bold", size: 12pt, fill: bad-color)[Heads up]
    #v(0.25cm)
    #text(fill: bad-color)[#{_typst_str(context["risk_warning"])}]
  ]
)
''')
    return "\n".join(parts)


def _protection_and_deliverability(context: dict, chart_data: dict) -> str:
    """deliverability + protection + spam_trend + list_hygiene, plus the 3
    real trend charts (delivered-safely gauge, spam-rate/bounce-rate/
    pass-rate line charts) -- re-derived from the SAME chart_data() the email
    report's _email_charts() uses (app/domain_report.py), never a second
    independent query, so a chart here and the email's version of it can
    never disagree. impersonation/blocklist good-news callouts close the
    zone, matching the email's own section order."""
    parts = ['#section-title("Getting through, and staying protected")']

    total = chart_data["disp_none"] + chart_data["disp_quarantine"] + chart_data["disp_reject"]
    parts.append(f'\n#eyebrow("How your emails are arriving")\n#v(0.15cm)\n#{_typst_str(context["deliverability"])}\n')
    if total > 0:
        frac = chart_data["disp_none"] / total
        parts.append(f'\n#v(0.3cm)\n#segment-gauge("Delivered safely", {frac})\n')

    parts.append(f'\n#eyebrow("Protection from fake emails")\n#v(0.15cm)\n')
    if context.get("protection_tightened"):
        parts.append(f'#text(weight: "bold")[#{_typst_str(context["protection_tightened"])}]\n\n')
    parts.append(f'#{_typst_str(context["protection"])}\n')

    spam_points = [(_short_date(d), r) for d, r in chart_data["spam_series"] if r is not None]
    if context.get("spam_trend"):
        parts.append(f'\n#v(0.4cm)\n#eyebrow("Google\'s own view of your mail")\n#v(0.15cm)\n#{_typst_str(context["spam_trend"])}\n')
        if len(spam_points) >= 2:
            parts.append(f'\n#v(0.3cm)\n#line-chart({_typst_series(spam_points)}, threshold: 0.001)\n')

    if context.get("list_hygiene"):
        parts.append(f'\n#v(0.4cm)\n#eyebrow("Keeping your list clean")\n#v(0.15cm)\n#{_typst_str(context["list_hygiene"])}.\n')

    bounce_points = [(_short_date(d), num / den) for d, num, den in chart_data["bounce_points"] if den]
    if len(bounce_points) >= 2:
        parts.append(f'''
#v(0.4cm)
#eyebrow("Your bounce rate over time")
#v(0.15cm)
#line-chart({_typst_series(bounce_points)}, threshold: {chart_data["bounce_warn_threshold"]})
''')

    pass_points = [(_short_date(d), r) for d, total_, passed_, r in chart_data["pass_rate_series"] if r is not None]
    if len(pass_points) >= 2:
        parts.append(f'''
#v(0.4cm)
#eyebrow("Your delivery rate over time")
#v(0.15cm)
#line-chart({_typst_series(pass_points)})
''')

    if context.get("impersonation_good_news"):
        parts.append(f'''
#v(0.4cm)
#rect(fill: rgb("#EAF4EC"), stroke: none, radius: 8pt, inset: 16pt, width: 100%,
  text(fill: ok-color)[#{_typst_str(context["impersonation_good_news"])}]
)
''')
    elif context.get("blocklist_good_news"):
        parts.append(f'''
#v(0.4cm)
#rect(fill: rgb("#EAF4EC"), stroke: none, radius: 8pt, inset: 16pt, width: 100%,
  text(fill: ok-color)[#{_typst_str(context["blocklist_good_news"])}]
)
''')
    return "\n".join(parts)


def _newsletter_and_closing(context: dict) -> str:
    """newsletter (+ its real open/click segment gauges, re-derived from the
    exact same newsletter_bars data Chapter 26's email table-bars use -- same
    number, different render), tips, and the closing paragraph/signoff.
    Closing wording is the exact established text, not a new decision."""
    parts = []
    if context.get("newsletter"):
        parts.append(f'#section-title("Your newsletter\'s reach")\n#{_typst_str(context["newsletter"])}\n')
        if context.get("newsletter_bars"):
            gauges = "\n".join(
                f'#segment-gauge({_typst_str(label)}, {frac})' for label, frac in context["newsletter_bars"]
            )
            parts.append(f'\n#v(0.3cm)\n{gauges}\n')

    if context.get("tips"):
        bullets = [f'#{_typst_str(tip)}' for tip in context["tips"]]
        parts.append(f'''
#v(0.4cm)
#rect(fill: rgb("#EAF4EC"), stroke: none, radius: 8pt, inset: 16pt, width: 100%,
  [
    #text(font: heading-font, weight: "bold", size: 12pt, fill: ok-color)[Tips for the next few weeks]
    #v(0.3cm)
    {_bullet_list(bullets)}
  ]
)
''')

    parts.append(f'''
#v(0.6cm)
This might look like a lot to take in, but it really matters. These are the things that decide whether
people actually see your emails, and whether someone could try to impersonate your organization using
your name. Please take a moment to read through it, and reply any time if anything's unclear. We're
always happy to walk through it with you.

#v(0.4cm)
With care, \\
#text(weight: "bold")[#{_typst_str(context["signoff_name"])}]
''')
    return "\n".join(parts)


def render_domain_report_pdf(conn, domain_id: int, domain_name: str, recipient_label: str,
                              period_start, period_end) -> bytes:
    """Renders the full domain report as PDF bytes -- Round D scope, the
    complete single-document assembly: masthead/KPI/headline (Round A),
    standing narrative + action ledger (Round B), protection/deliverability
    + charts (Round C), newsletter + tips + closing (Round D). One shared
    context dict throughout, zero new content decisions anywhere."""
    from app.domain_report import _build_context
    from app.domain_report import chart_data as _get_chart_data

    context = _build_context(conn, domain_id, domain_name, recipient_label, period_start, period_end)
    cdata = _get_chart_data(conn, domain_id, period_start, period_end)
    body = "\n".join([
        _masthead_and_kpi(context),
        _standing_narrative(context),
        _action_ledger(context),
        _protection_and_deliverability(context, cdata),
        _newsletter_and_closing(context),
    ])
    typ_source = _PREAMBLE + _page_setup(domain_name) + body
    return _compile_typst(typ_source)
