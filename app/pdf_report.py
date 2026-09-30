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
            ["typst", "compile", typ_path, pdf_path],
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


def render_domain_report_pdf(conn, domain_id: int, domain_name: str, recipient_label: str,
                              period_start, period_end) -> bytes:
    """Renders the full domain report as PDF bytes. Round A scope: masthead +
    KPI tiles + headline only -- the rest of build_domain_report()'s sections
    (care_ledger, whats_working, resolved, still_open, protection/
    deliverability + charts, newsletter, tips) are added in Rounds B-D,
    reusing this same function's context dict, not a new one."""
    from app.domain_report import _build_context

    context = _build_context(conn, domain_id, domain_name, recipient_label, period_start, period_end)
    body = _masthead_and_kpi(context)
    typ_source = _PREAMBLE + _page_setup(domain_name) + body
    return _compile_typst(typ_source)
