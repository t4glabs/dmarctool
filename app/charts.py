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
