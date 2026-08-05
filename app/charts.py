"""Server-rendered SVG sparkline for the pass-rate trend -- no JS charting
library, no CDN, no build step; just a string of SVG markup dropped into the template."""


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
        dots.append(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for(rate):.1f}" r="2" fill="{color}" opacity="0.8">'
            f'<title>{series[i][0]}: {rate:.1%} ({series[i][2]}/{series[i][1]})</title></circle>'
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
        dots.append(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for(rate):.1f}" r="2.3" style="{style}" opacity="0.9">'
            f'<title>{series[i][0]}: {rate:.3%}</title></circle>'
        )

    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {''.join(gridlines)}
  <polyline points="{polyline}" fill="none" stroke="currentColor" stroke-width="1.75" opacity="0.9"/>
  {''.join(dots)}
  <text x="{pad_l}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6">{first_date}</text>
  <text x="{width - pad_r}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.6" text-anchor="end">{last_date}</text>
</svg>'''


def dual_rate_sparkline(series, width: int = 640, height: int = 140,
                         label_a: str = "bounce", label_b: str = "complaint") -> str:
    """series: list of (date_str, delivered, rate_a_or_none, rate_b_or_none) as
    from analysis.ses_daily_series. Two lines on a shared, auto-scaled axis."""
    pad_l, pad_r, pad_t, pad_b = 42, 8, 10, 34
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    a_points = [(i, r[2]) for i, r in enumerate(series) if r[2] is not None]
    b_points = [(i, r[3]) for i, r in enumerate(series) if r[3] is not None]
    if len(series) < 2 or (not a_points and not b_points):
        return '<svg width="{}" height="{}"><text x="10" y="20" fill="currentColor" opacity="0.6">not enough history yet</text></svg>'.format(width, height)

    n = len(series)

    def x_for(i):
        return pad_l + (i / (n - 1)) * plot_w if n > 1 else pad_l

    observed_max = max([r for _, r in a_points] + [r for _, r in b_points] + [0.0])
    y_max = max(observed_max * 1.3, 0.01)

    def y_for(rate):
        r = max(0.0, min(y_max, rate))
        return pad_t + (1 - r / y_max) * plot_h

    def line_and_dots(points, style_color):
        if not points:
            return "", ""
        pts = " ".join(f"{x_for(i):.1f},{y_for(r):.1f}" for i, r in points)
        line = f'<polyline points="{pts}" fill="none" style="stroke:{style_color}" stroke-width="1.75" opacity="0.9"/>'
        dots = "".join(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for(r):.1f}" r="2.2" style="fill:{style_color}" opacity="0.9">'
            f'<title>{series[i][0]}: {r:.2%}</title></circle>'
            for i, r in points
        )
        return line, dots

    gridline = (
        f'<line x1="{pad_l}" y1="{y_for(0):.1f}" x2="{width - pad_r}" y2="{y_for(0):.1f}" '
        f'stroke="currentColor" stroke-opacity="0.15" stroke-width="1"/>'
    )

    a_line, a_dots = line_and_dots(a_points, "var(--warn)")
    b_line, b_dots = line_and_dots(b_points, "var(--bad)")
    first_date, last_date = series[0][0], series[-1][0]

    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  {gridline}
  {a_line}{b_line}{a_dots}{b_dots}
  <text x="{pad_l}" y="{height - 20}" font-size="10" fill="currentColor" opacity="0.6">{first_date}</text>
  <text x="{width - pad_r}" y="{height - 20}" font-size="10" fill="currentColor" opacity="0.6" text-anchor="end">{last_date}</text>
  <circle cx="{pad_l + 4}" cy="{height - 8}" r="3" style="fill:var(--warn)"/>
  <text x="{pad_l + 12}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.8">{label_a} rate</text>
  <circle cx="{pad_l + 90}" cy="{height - 8}" r="3" style="fill:var(--bad)"/>
  <text x="{pad_l + 98}" y="{height - 4}" font-size="10" fill="currentColor" opacity="0.8">{label_b} rate</text>
</svg>'''
