"""Server-rendered SVG sparkline for the pass-rate trend -- no JS charting
library, no CDN, no build step; just a string of SVG markup dropped into the template."""

import datetime
import math


def pass_rate_sparkline(series, threshold: float = 0.99, width: int = 640, height: int = 140) -> str:
    """series: list of (date, total, passed, rate) as returned by analysis.daily_pass_series."""
    pad_l, pad_r, pad_t, pad_b = 36, 8, 10, 20
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    points_with_data = [(i, p[3]) for i, p in enumerate(series) if p[3] is not None]
    if len(series) < 2 or not points_with_data:
        return '<svg width="{}" height="{}"><text x="10" y="20" fill="currentColor" opacity="0.6">not enough data yet</text></svg>'.format(width, height)

    n = len(series)

    def x_for(i):
        return pad_l + (i / (n - 1)) * plot_w if n > 1 else pad_l

    # y-axis: 0.90 to 1.00, clamped
    y_min, y_max = 0.90, 1.0
    def y_for(rate):
        r = max(y_min, min(y_max, rate))
        return pad_t + (1 - (r - y_min) / (y_max - y_min)) * plot_h

    path_pts = [f"{x_for(i):.1f},{y_for(rate):.1f}" for i, rate in points_with_data]
    polyline = " ".join(path_pts)

    threshold_y = y_for(threshold)

    gridlines = []
    for pct in (0.90, 0.95, 0.99, 1.00):
        gy = y_for(pct)
        gridlines.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
            f'stroke="currentColor" stroke-opacity="0.15" stroke-width="1"/>'
        )
        gridlines.append(
            f'<text x="2" y="{gy + 4:.1f}" font-size="10" fill="currentColor" opacity="0.6">{int(pct*100)}</text>'
        )

    first_date = series[0][0].strftime("%b %-d")
    last_date = series[-1][0].strftime("%b %-d")

    dots = []
    for i, rate in points_with_data:
        color = "currentColor"
        tooltip = f"{series[i][0]}: {rate:.1%} ({series[i][2]}/{series[i][1]})"
        dots.append(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for(rate):.1f}" r="3" fill="{color}" opacity="0.8" '
            f'data-tooltip="{tooltip}"/>'
        )

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {''.join(gridlines)}
  <line x1="{pad_l}" y1="{threshold_y:.1f}" x2="{width - pad_r}" y2="{threshold_y:.1f}"
        stroke="currentColor" stroke-opacity="0.35" stroke-width="1" stroke-dasharray="4 3"/>
  <polyline points="{polyline}" fill="none" stroke="currentColor" stroke-width="1.75" opacity="0.9"/>
  {''.join(dots)}
  <text x="{pad_l}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6">{first_date}</text>
  <text x="{width - pad_r}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6" text-anchor="end">{last_date}</text>
</svg>'''
    return svg


def spam_rate_sparkline(series, width: int = 640, height: int = 140) -> str:
    """series: list of (date_str, rate_or_none) as from analysis.postmaster_daily_series.
    Reference lines at Gmail's own published thresholds (0.10% ideal, 0.30% hard limit)."""
    pad_l, pad_r, pad_t, pad_b = 42, 8, 10, 20
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    points_with_data = [(i, r) for i, (_, r) in enumerate(series) if r is not None]
    if len(series) < 2 or not points_with_data:
        return '<svg width="{}" height="{}"><text x="10" y="20" fill="currentColor" opacity="0.6">not enough history yet</text></svg>'.format(width, height)

    n = len(series)

    def x_for(i):
        return pad_l + (i / (n - 1)) * plot_w if n > 1 else pad_l

    observed_max = max(r for _, r in points_with_data)
    y_max = max(observed_max * 1.3, 0.004)

    def y_for(rate):
        r = max(0.0, min(y_max, rate))
        return pad_t + (1 - r / y_max) * plot_h

    path_pts = [f"{x_for(i):.1f},{y_for(rate):.1f}" for i, rate in points_with_data]
    polyline = " ".join(path_pts)

    gridlines = []
    for pct, label in ((0.001, "0.10%"), (0.003, "0.30%")):
        if pct <= y_max:
            gy = y_for(pct)
            gridlines.append(
                f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
                f'stroke="currentColor" stroke-opacity="0.25" stroke-width="1" stroke-dasharray="4 3"/>'
            )
            gridlines.append(
                f'<text x="2" y="{gy + 4:.1f}" font-size="10" fill="currentColor" opacity="0.6">{label}</text>'
            )

    first_date, last_date = series[0][0], series[-1][0]

    dots = []
    for i, rate in points_with_data:
        style = "fill:var(--bad)" if rate >= 0.003 else ("fill:var(--warn)" if rate >= 0.001 else "fill:currentColor")
        tooltip = f"{series[i][0]}: {rate:.3%}"
        dots.append(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for(rate):.1f}" r="3" style="{style}" opacity="0.9" '
            f'data-tooltip="{tooltip}"/>'
        )

    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {''.join(gridlines)}
  <polyline points="{polyline}" fill="none" stroke="currentColor" stroke-width="1.75" opacity="0.9"/>
  {''.join(dots)}
  <text x="{pad_l}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6">{first_date}</text>
  <text x="{width - pad_r}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6" text-anchor="end">{last_date}</text>
</svg>'''


def metric_trend_chart(series, thresholds=(), width: int = 640, height: int = 170, rolling_days: int = 7) -> str:
    """series: list of (date_str, numerator, denominator) -- ONE metric
    (just bounce rate, or just complaint rate), never sharing an axis with
    another metric at a wildly different scale. A 5% bounce-rate warning
    threshold and a 0.1% complaint-rate warning threshold are 50x apart --
    squeezed onto one shared axis (the old dual_rate_sparkline), the smaller
    one flatlines into invisibility. This is the direct replacement, called
    once per metric.

    Draws faint raw daily dots (mostly noise on a low-volume day -- a 100%
    bounce rate off of 1 message means nothing) behind a bold
    `rolling_days`-day *volume-weighted* rolling average -- sum of events
    over sum of volume across the window, not a plain average of daily
    rates, so a single big campaign day isn't drowned out by a week of tiny
    ones or vice versa. `thresholds`: list of (value, label, color) in
    ascending order -- drawn as dashed reference lines, and used to color
    the latest point/line by the most severe one it's crossed. The latest
    rolling value is called out directly on the chart (not just on hover),
    and the x-axis gets real, spaced-out date labels instead of only the
    two ends."""
    pad_l, pad_r, pad_t, pad_b = 34, 50, 14, 22
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    # Daily rate is only defined where the day had a denominator; days with a
    # numerator but no denominator (a bounce landing the day after the send)
    # still contribute to the rolling window below, they just have no dot.
    points_with_data = [(i, num / den, num, den) for i, (_, num, den) in enumerate(series) if den]
    if len(series) < 2 or not points_with_data:
        return '<svg width="{}" height="{}"><text x="10" y="20" fill="currentColor" opacity="0.6">not enough history yet</text></svg>'.format(width, height)

    n = len(series)

    def x_for(i):
        return pad_l + (i / (n - 1)) * plot_w if n > 1 else pad_l

    # Rolling window sums numerators and denominators separately -- the correct
    # way to average a rate, and it keeps events whose numerator and
    # denominator land on different days (bounce attribution lag) in the same
    # window instead of dropping them.
    rolling = []
    for i, rate, _num, _den in points_with_data:
        win_num = sum(num for idx, (_, num, den) in enumerate(series)
                      if i - rolling_days + 1 <= idx <= i)
        win_den = sum(den for idx, (_, num, den) in enumerate(series)
                      if i - rolling_days + 1 <= idx <= i)
        rolling.append((i, (win_num / win_den) if win_den else rate))

    # Scale to the smoothed trend's RECENT window + thresholds, not its
    # full history -- an old, isolated low-volume spike can still show up
    # close to full height in the rolling average itself (there's nothing
    # nearby to dilute it against), which would otherwise stretch the whole
    # y-axis and squash the actual current trend into an unreadable sliver
    # near the bottom. Anything taller than this (old spike or a raw dot)
    # still renders -- clamped to the top edge by y_for below -- it just
    # doesn't dictate the scale the recent, relevant trend has to be read
    # against.
    recent_window = rolling[-(rolling_days * 3):]
    rolling_max = max(r for _, r in recent_window)
    threshold_max = max((t[0] for t in thresholds), default=0.0)
    y_max = max(rolling_max, threshold_max) * 1.25 or 0.01

    def y_for(rate):
        r = max(0.0, min(y_max, rate))
        return pad_t + (1 - r / y_max) * plot_h

    gridlines = [
        f'<text x="1" y="{pad_t + 4}" font-size="10" fill="currentColor" opacity="0.5">{y_max:.2%}</text>',
        f'<text x="1" y="{pad_t + plot_h:.1f}" font-size="10" fill="currentColor" opacity="0.5">0%</text>',
    ]
    for value, label, color in thresholds:
        if value <= y_max:
            gy = y_for(value)
            gridlines.append(
                f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
                f'stroke="{color}" stroke-opacity="0.55" stroke-width="1" stroke-dasharray="4 3"/>'
            )
            gridlines.append(
                f'<text x="{width - pad_r + 4:.1f}" y="{gy + 3:.1f}" font-size="9" fill="{color}" opacity="0.9">{label}</text>'
            )

    raw_dots = "".join(
        f'<circle cx="{x_for(i):.1f}" cy="{y_for(r):.1f}" r="2" fill="currentColor" opacity="0.25" '
        f'data-tooltip="{series[i][0]}: {r:.3%} ({num} of {den})"/>'
        for i, r, num, den in points_with_data
    )

    latest_color = "var(--ok)"
    for value, _label, color in thresholds:
        if rolling[-1][1] >= value:
            latest_color = color

    roll_pts = " ".join(f"{x_for(i):.1f},{y_for(r):.1f}" for i, r in rolling)
    roll_line = f'<polyline points="{roll_pts}" fill="none" stroke="{latest_color}" stroke-width="2.25" opacity="0.95"/>'

    latest_i, latest_roll = rolling[-1]
    latest_x, latest_y = x_for(latest_i), y_for(latest_roll)
    anchor = "end" if latest_x > width * 0.7 else "start"
    label_x = latest_x - 8 if anchor == "end" else latest_x + 8
    latest_dot = (f'<circle cx="{latest_x:.1f}" cy="{latest_y:.1f}" r="5" fill="{latest_color}" '
                  f'data-tooltip="{rolling_days}-day average: {latest_roll:.3%}"/>')
    latest_label = (f'<text x="{label_x:.1f}" y="{max(latest_y - 8, 12):.1f}" font-size="12" font-weight="700" '
                     f'fill="{latest_color}" text-anchor="{anchor}">{latest_roll:.2%}</text>')

    tick_every = max(1, n // 8)
    x_ticks = []
    for i in range(0, n, tick_every):
        try:
            label = datetime.date.fromisoformat(series[i][0]).strftime("%b %-d")
        except ValueError:
            label = series[i][0]
        x_ticks.append(
            f'<text x="{x_for(i):.1f}" y="{height - 4}" font-size="9" fill="currentColor" '
            f'opacity="0.55" text-anchor="middle">{label}</text>'
        )
    if (n - 1) % tick_every != 0:
        try:
            last_label = datetime.date.fromisoformat(series[-1][0]).strftime("%b %-d")
        except ValueError:
            last_label = series[-1][0]
        x_ticks.append(
            f'<text x="{x_for(n - 1):.1f}" y="{height - 4}" font-size="9" fill="currentColor" '
            f'opacity="0.55" text-anchor="end">{last_label}</text>'
        )

    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {''.join(gridlines)}
  {raw_dots}
  {roll_line}
  {latest_dot}
  {latest_label}
  {''.join(x_ticks)}
</svg>'''


def volume_bar_chart(series, width: int = 640, height: int = 140) -> str:
    """series: list of (date_str, count). A bar per day of raw volume (not a
    rate) -- for spotting *when* you sent and how big each campaign was, which
    a rate chart can't show (a tiny test send and a 10,000-message campaign can
    have the identical 0% bounce rate)."""
    pad_l, pad_r, pad_t, pad_b = 30, 8, 16, 20
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    points_with_data = [(i, c) for i, (_, c) in enumerate(series) if c is not None]
    if len(series) < 2 or not any(c for _, c in points_with_data):
        return '<svg width="{}" height="{}"><text x="10" y="20" fill="currentColor" opacity="0.6">not enough history yet</text></svg>'.format(width, height)

    n = len(series)
    slot_w = plot_w / n
    bar_w = max(slot_w * 0.65, 1)
    observed_max = max(c for _, c in points_with_data)
    y_max = max(observed_max * 1.15, 1)

    bars = []
    for i, c in points_with_data:
        h = (max(0, min(y_max, c)) / y_max) * plot_h
        x = pad_l + i * slot_w + (slot_w - bar_w) / 2
        y = pad_t + plot_h - h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
            f'fill="currentColor" opacity="0.75" data-tooltip="{series[i][0]}: {c}"/>'
        )

    first_date, last_date = series[0][0], series[-1][0]

    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  <text x="2" y="{pad_t}" font-size="10" fill="currentColor" opacity="0.6">{observed_max}</text>
  <line x1="{pad_l}" y1="{pad_t + plot_h:.1f}" x2="{width - pad_r}" y2="{pad_t + plot_h:.1f}" stroke="currentColor" stroke-opacity="0.15" stroke-width="1"/>
  {''.join(bars)}
  <text x="{pad_l}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6">{first_date}</text>
  <text x="{width - pad_r}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6" text-anchor="end">{last_date}</text>
</svg>'''


def disposition_donut_chart(pass_count: int, quarantine_count: int, reject_count: int,
                             width: int = 150, height: int = 150) -> str:
    """Donut chart of DMARC disposition mix -- what actually happened to mail
    that was evaluated, not just the pass-rate percentage already shown
    elsewhere. Same disp_none/disp_quarantine/disp_reject counts
    analysis.provider_breakdown() already sums (callers should pass the
    domain-window totals across all providers), just visualized."""
    total = pass_count + quarantine_count + reject_count
    cx, cy = width / 2, height / 2
    # stroke_w is a fraction of r, so the ring's OUTER edge is r + stroke_w/2,
    # not r itself -- sizing r off the half-viewport alone (ignoring that)
    # let the ring's outside edge overflow past the svg's own edges, clipping
    # it on all 4 sides. Solve for r so the outer edge fits within the
    # viewport with a small margin instead.
    margin = 6
    stroke_fraction = 0.62
    max_outer_r = min(width, height) / 2 - margin
    r = max_outer_r / (1 + stroke_fraction / 2)
    stroke_w = r * stroke_fraction
    circumference = 2 * math.pi * r

    if total <= 0:
        return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">'
                f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="currentColor" '
                f'stroke-opacity="0.15" stroke-width="{stroke_w:.1f}"/>'
                f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" class="donut-center-sublabel">no data</text></svg>')

    segments = [
        (pass_count, "var(--ok)", "delivered"),
        (quarantine_count, "var(--warn)", "quarantined"),
        (reject_count, "var(--bad)", "rejected"),
    ]
    arcs = []
    cumulative = 0.0
    for count, color, seg_label in segments:
        if count <= 0:
            continue
        length = (count / total) * circumference
        pct = count / total
        arcs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="{stroke_w:.1f}" '
            f'stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
            f'stroke-dashoffset="{-cumulative:.2f}" transform="rotate(-90 {cx} {cy})" '
            f'data-tooltip="{count} {seg_label} ({pct:.1%})"/>'
        )
        cumulative += length

    pass_rate = pass_count / total
    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {''.join(arcs)}
  <text x="{cx}" y="{cy - 2}" text-anchor="middle" class="donut-center-label">{pass_rate:.0%}</text>
  <text x="{cx}" y="{cy + 15}" text-anchor="middle" class="donut-center-sublabel">delivered</text>
</svg>'''


def provider_stacked_bar_chart(rows, width: int = 640, max_rows: int = 8) -> str:
    """rows: analysis.provider_breakdown()'s return list (dicts with org_name/
    total/disp_none/disp_quarantine/disp_reject) -- one stacked-by-disposition
    horizontal bar per reporting provider, so "who sees this mail and what
    happens to it" is visual, with the existing table underneath for exact
    numbers."""
    rows = [r for r in rows if r["total"]][:max_rows]
    label_w = 140
    pad_l, pad_r, pad_t, row_h, row_gap, legend_h = label_w, 55, 6, 18, 9, 22
    if not rows:
        return (f'<svg width="{width}" height="60"><text x="10" y="20" fill="currentColor" '
                f'opacity="0.6">not enough data yet</text></svg>')

    plot_h = len(rows) * row_h + (len(rows) - 1) * row_gap
    height = pad_t + plot_h + legend_h
    plot_w = width - pad_l - pad_r
    max_total = max(r["total"] for r in rows)
    scale = (plot_w / max_total) if max_total else 0

    parts = []
    for idx, r in enumerate(rows):
        y = pad_t + idx * (row_h + row_gap)
        total = r["total"]
        label = r["org_name"]
        label = label if len(label) <= 22 else label[:21] + "…"
        x = float(pad_l)
        parts.append(
            f'<text x="{pad_l - 8}" y="{y + row_h * 0.72:.1f}" font-size="11" fill="currentColor" '
            f'opacity="0.85" text-anchor="end">{label}</text>'
        )
        for count, color, seg_label in (
            (r["disp_none"], "var(--ok)", "delivered"),
            (r["disp_quarantine"], "var(--warn)", "quarantined"),
            (r["disp_reject"], "var(--bad)", "rejected"),
        ):
            if count <= 0:
                continue
            seg_w = max(count * scale, 1.0)
            pct = count / total if total else 0
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{seg_w:.1f}" height="{row_h}" fill="{color}" '
                f'data-tooltip="{r["org_name"]}: {count} {seg_label} ({pct:.1%})"/>'
            )
            x += seg_w
        parts.append(
            f'<text x="{x + 6:.1f}" y="{y + row_h * 0.72:.1f}" font-size="10" fill="currentColor" '
            f'opacity="0.6">{total}</text>'
        )

    legend_y = height - 6
    legend = (
        f'<circle cx="{pad_l}" cy="{legend_y - 3:.1f}" r="3" fill="var(--ok)"/>'
        f'<text x="{pad_l + 8}" y="{legend_y:.1f}" font-size="10" fill="currentColor" opacity="0.75">delivered</text>'
        f'<circle cx="{pad_l + 78}" cy="{legend_y - 3:.1f}" r="3" fill="var(--warn)"/>'
        f'<text x="{pad_l + 86}" y="{legend_y:.1f}" font-size="10" fill="currentColor" opacity="0.75">quarantined</text>'
        f'<circle cx="{pad_l + 172}" cy="{legend_y - 3:.1f}" r="3" fill="var(--bad)"/>'
        f'<text x="{pad_l + 180}" y="{legend_y:.1f}" font-size="10" fill="currentColor" opacity="0.75">rejected</text>'
    )

    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {''.join(parts)}
  {legend}
</svg>'''


def health_score_sparkline(series, width: int = 640, height: int = 140) -> str:
    """series: list of (date_str, score_or_none 0-100) as from
    analysis.health_score_series -- the single number that already combines
    pass rate, Gmail's own spam rate, and bounce/complaint rate (see
    snapshot_domain_health's weights), so one line stands in for four.
    Colored by the same health tiers as verdicts.domain_vibe_verdict
    (>=80 ok, >=50 warn, else bad) so the chart and the plain-language
    verdict next to it never disagree.

    Below ~90px tall this switches to a compact mode with no axis or date
    text. The dashboard cards render it at 48px, where the full-size padding
    left an 18px-tall plot: the 50 and 80 axis labels are only 30% of that
    apart, so they collided into an unreadable smudge that looked like a
    missing-glyph box. Dropping the text also hands those 24 vertical pixels
    back to the line itself, which is the only part that's legible at this
    size. Nothing is lost -- each dot keeps its own date+score tooltip, and
    the dashed 50/80 bands still show which tier the line is sitting in."""
    compact = height < 90
    pad_l, pad_r, pad_t, pad_b = (4, 4, 6, 6) if compact else (28, 8, 10, 20)
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    points_with_data = [(i, s) for i, (_, s) in enumerate(series) if s is not None]
    if len(series) < 2 or not points_with_data:
        return '<svg width="{}" height="{}"><text x="10" y="20" fill="currentColor" opacity="0.6">not enough history yet</text></svg>'.format(width, height)

    n = len(series)

    def x_for(i):
        return pad_l + (i / (n - 1)) * plot_w if n > 1 else pad_l

    def y_for(score):
        s = max(0.0, min(100.0, score))
        return pad_t + (1 - s / 100.0) * plot_h

    path_pts = [f"{x_for(i):.1f},{y_for(s):.1f}" for i, s in points_with_data]
    polyline = " ".join(path_pts)

    gridlines = []
    for val in (50, 80):
        gy = y_for(val)
        gridlines.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
            f'stroke="currentColor" stroke-opacity="0.15" stroke-width="1" stroke-dasharray="4 3"/>'
        )
        if not compact:
            gridlines.append(f'<text x="1" y="{gy + 4:.1f}" font-size="10" fill="currentColor" opacity="0.6">{val}</text>')

    first_date, last_date = series[0][0], series[-1][0]

    dots = []
    for i, s in points_with_data:
        style = "fill:var(--ok)" if s >= 80 else ("fill:var(--warn)" if s >= 50 else "fill:var(--bad)")
        dots.append(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for(s):.1f}" r="3" style="{style}" '
            f'data-tooltip="{series[i][0]}: {s:.0f}/100"/>'
        )

    axis_text = "" if compact else (
        f'<text x="{pad_l}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6">{first_date}</text>'
        f'<text x="{width - pad_r}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6" '
        f'text-anchor="end">{last_date}</text>'
    )
    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {''.join(gridlines)}
  <polyline points="{polyline}" fill="none" stroke="currentColor" stroke-width="1.75" opacity="0.9"/>
  {''.join(dots)}
  {axis_text}
</svg>'''


def vibe_distribution_donut(good_count: int, bad_count: int, ugly_count: int,
                             width: int = 150, height: int = 150) -> str:
    """Donut of how many tracked domains currently fall into each health
    tier (verdicts.domain_vibe_verdict) -- one portfolio-wide
    glance instead of reading every card's own badge one at a time."""
    total = good_count + bad_count + ugly_count
    cx, cy = width / 2, height / 2
    # A stroked circle straddles its radius, so half the stroke sits OUTSIDE r --
    # sizing r off the box first (the obvious way) clipped the ring on all four
    # sides. Solve r from the outer edge instead, so the widest painted pixel
    # lands exactly `margin` inside the viewBox.
    margin = 6
    stroke_fraction = 0.62
    max_outer_r = min(width, height) / 2 - margin
    r = max_outer_r / (1 + stroke_fraction / 2)
    stroke_w = r * stroke_fraction
    circumference = 2 * math.pi * r

    if total <= 0:
        return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">'
                f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="currentColor" '
                f'stroke-opacity="0.15" stroke-width="{stroke_w:.1f}"/>'
                f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" class="donut-center-sublabel">no data</text></svg>')

    segments = [
        (good_count, "var(--ok)", "Healthy"),
        (bad_count, "var(--warn)", "needing attention"),
        (ugly_count, "var(--bad)", "at risk"),
    ]
    arcs = []
    cumulative = 0.0
    for count, color, seg_label in segments:
        if count <= 0:
            continue
        length = (count / total) * circumference
        arcs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="{stroke_w:.1f}" '
            f'stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
            f'stroke-dashoffset="{-cumulative:.2f}" transform="rotate(-90 {cx} {cy})" '
            f'data-tooltip="{count} {seg_label} ({count / total:.0%})"/>'
        )
        cumulative += length

    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {''.join(arcs)}
  <text x="{cx}" y="{cy - 2}" text-anchor="middle" class="donut-center-label">{total}</text>
  <text x="{cx}" y="{cy + 15}" text-anchor="middle" class="donut-center-sublabel">domains</text>
</svg>'''
