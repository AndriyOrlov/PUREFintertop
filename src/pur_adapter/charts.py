"""Small SVG charts in the style of the HDRDisplayCalibrate reports.

The look follows HDRDisplayCalibrate's Charts.cs: dark card, faint grid,
grey labels, legend at the top right, dashed reference lines, bars tinted with
the colour of what they measure, and green / yellow / red grades. Output is
plain SVG text, so it needs no plotting library and renders on GitHub.
"""

import html
import itertools
import math

_CLIP_IDS = itertools.count(1)  # unique ids when several charts share a page

# Colours from HDRDisplayCalibrate (Ui.cs and Charts.cs).
ACCENT = "#60cdff"
GOOD = "#6ccb5f"
WARM = "#ffc83d"
BAD = "#ff6b6b"
MUTED = "#9a9a9a"
LABEL = "#aaaaaa"
GRID = "rgba(255,255,255,0.11)"
AXIS = "rgba(255,255,255,0.35)"
PAGE = "#202020"
CARD = "rgba(255,255,255,0.063)"
FONT = "Segoe UI, Inter, Helvetica, Arial, sans-serif"


def grade(value, good_below, warm_below):
    """Colour for a value where smaller is better."""
    if value < good_below:
        return GOOD
    return WARM if value < warm_below else BAD


def _text(x, y, content, size=10, color=LABEL, anchor="start", weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" '
            f'font-family="{FONT}" text-anchor="{anchor}" font-weight="{weight}">'
            f"{html.escape(str(content))}</text>")


def _line(x1, y1, x2, y2, color, width=1.0, dashed=False):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="{width}"{dash}/>')


def _frame(width, height, body, standalone):
    """Wrap chart content; standalone charts carry their own dark background."""
    background = ""
    if standalone:
        background = (f'<rect width="{width}" height="{height}" rx="10" fill="{PAGE}"/>'
                      f'<rect width="{width}" height="{height}" rx="10" fill="{CARD}"/>')
    size = f'width="{width}" height="{height}"' if standalone else 'width="100%"'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'{size} role="img">{background}{"".join(body)}</svg>')


def nice_ticks(low, high):
    """About five round tick values between low and high (as in Charts.cs)."""
    span = high - low
    step = 10 ** math.floor(math.log10(span / 5))
    if span / step > 10:
        step *= 2
    if span / step > 10:
        step *= 2.5
    value = math.ceil(low / step) * step
    ticks = []
    while value <= high + 1e-9:
        ticks.append(value)
        value += step
    return ticks


def format_number(value):
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 1:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


class Series:
    """One line or set of points. `point_colors` tints each marker separately."""

    def __init__(self, name, points, color=ACCENT, dashed=False, markers=False,
                 lines=True, point_colors=None):
        self.name = name
        self.points = list(points)
        self.color = color
        self.dashed = dashed
        self.markers = markers
        self.lines = lines
        self.point_colors = point_colors


def line_chart(series, *, x_label="", y_label="", log_x=False, log_y=False,
               x_range=None, y_range=None, width=640, height=300, standalone=True,
               tick_format=format_number, legend="right"):
    """Line / scatter chart with optional log axes and a legend (Charts.cs LineChart)."""
    left, bottom, top, right = 52, 34, 10, 12
    usable = [(x, y) for s in series for x, y in s.points
              if (not log_x or x > 0) and (not log_y or y > 0)]
    body = []
    if not usable:
        return _frame(width, height, [_text(width / 2, height / 2, "no data", 12, MUTED, "middle")],
                      standalone)

    def scale_x(v):
        return math.log10(v) if log_x else v

    def scale_y(v):
        return math.log10(v) if log_y else v

    x0 = scale_x(x_range[0] if x_range else min(x for x, _ in usable))
    x1 = scale_x(x_range[1] if x_range else max(x for x, _ in usable))
    y0 = scale_y(y_range[0] if y_range else min(y for _, y in usable))
    y1 = scale_y(y_range[1] if y_range else max(y for _, y in usable))
    if x1 - x0 < 1e-9:
        x1 = x0 + 1
    if y1 - y0 < 1e-9:
        y1 = y0 + 1

    def to_canvas(x, y):
        return (left + (scale_x(x) - x0) / (x1 - x0) * (width - left - right),
                height - bottom - (scale_y(y) - y0) / (y1 - y0) * (height - bottom - top))

    def ticks(low, high, log):
        if log:
            return [10 ** e for e in range(math.floor(low), math.ceil(high) + 1)]
        return nice_ticks(low, high)

    for value in ticks(x0, x1, log_x):
        x, y = to_canvas(value, 10 ** y0 if log_y else y0)
        if left - 1 <= x <= width - right + 1:
            body.append(_line(x, y, x, top, GRID))
            body.append(_text(x, y + 14, tick_format(value), anchor="middle"))
    for value in ticks(y0, y1, log_y):
        x, y = to_canvas(10 ** x0 if log_x else x0, value)
        if top - 1 <= y <= height - bottom + 1:
            body.append(_line(x, y, width - right, y, GRID))
            body.append(_text(left - 6, y + 3.5, tick_format(value), anchor="end"))
    body.append(_line(left, height - bottom, width - right, height - bottom, AXIS))
    body.append(_line(left, top, left, height - bottom, AXIS))
    body.append(_text(width - right, height - 6, x_label, 11, anchor="end"))
    body.append(_text(left + 4, top + 11, y_label, 11))

    clip_id = f"plot{next(_CLIP_IDS)}"
    body.append(f'<clipPath id="{clip_id}"><rect x="{left}" y="{top}" '
                f'width="{width - left - right}" height="{height - bottom - top}"/></clipPath>'
                f'<g clip-path="url(#{clip_id})">')
    for s in series:
        points = [(x, y) for x, y in s.points if (not log_x or x > 0) and (not log_y or y > 0)]
        canvas_points = [to_canvas(x, y) for x, y in points]
        if s.lines and len(canvas_points) > 1:
            dash = ' stroke-dasharray="6 4"' if s.dashed else ""
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in canvas_points)
            body.append(f'<polyline points="{path}" fill="none" stroke="{s.color}" '
                        f'stroke-width="2"{dash} stroke-linejoin="round"/>')
        if s.markers:
            for index, (x, y) in enumerate(canvas_points):
                color = s.point_colors[index] if s.point_colors else s.color
                body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}" '
                            f'stroke="rgba(0,0,0,0.5)" stroke-width="0.6"/>')
    body.append("</g>")

    # Legend: top right as in Charts.cs, or top left (below the axis name)
    # when the data climbs towards the top right.
    legend_width = 40 + max(len(s.name) for s in series) * 6.2
    legend_x = width - right - legend_width if legend == "right" else left + 10
    legend_y = top + 4 if legend == "right" else top + 20
    body.append(f'<rect x="{legend_x - 6:.0f}" y="{legend_y - 4}" width="{legend_width:.0f}" '
                f'height="{len(series) * 17 + 8}" rx="6" fill="{PAGE}" fill-opacity="0.85"/>')
    for s in series:
        if s.lines:
            body.append(_line(legend_x, legend_y + 8, legend_x + 22, legend_y + 8, s.color, 2, s.dashed))
        else:
            body.append(f'<circle cx="{legend_x + 11}" cy="{legend_y + 8}" r="3.2" fill="{s.color}"/>')
        body.append(_text(legend_x + 28, legend_y + 12, s.name, 11))
        legend_y += 17
    return _frame(width, height, body, standalone)


def bar_chart(bars, *, thresholds=(), caption="", max_value=None, width=640, height=200,
              standalone=True, value_format=format_number):
    """Vertical bars, each in its own colour, with dashed threshold lines
    (Charts.cs DeltaEChart). `bars` is [(value, colour)], `thresholds` is
    [(value, colour)]."""
    left, bottom = 40, 18
    body = []
    if not bars:
        return _frame(width, height, [_text(width / 2, height / 2, "no data", 12, MUTED, "middle")],
                      standalone)
    # The scale follows the data; thresholds above it are simply not drawn.
    highest = max_value or max(max(value for value, _ in bars) * 1.1, 1e-9)
    bar_width = (width - left - 4) / len(bars)
    plot_height = height - bottom - 6
    threshold_levels = {level for level, _ in thresholds}
    for level in nice_ticks(0, highest):
        y = height - bottom - level / highest * plot_height
        if level > 0 and level not in threshold_levels:
            body.append(_line(left, y, width - 4, y, GRID))
            body.append(_text(left - 6, y + 3.5, value_format(level), anchor="end"))
    for level, color in thresholds:
        if level < highest:
            y = height - bottom - level / highest * (height - bottom - 6)
            body.append(_line(left, y, width - 4, y, color, 0.9, dashed=True))
            body.append(_text(left - 6, y + 3.5, value_format(level), anchor="end"))
    for index, (value, color) in enumerate(bars):
        bar_height = value / highest * (height - bottom - 6)
        body.append(f'<rect x="{left + index * bar_width + 0.5:.2f}" '
                    f'y="{height - bottom - bar_height:.2f}" '
                    f'width="{max(bar_width - 1, 1):.2f}" height="{bar_height:.2f}" fill="{color}"/>')
    body.append(_line(left, height - bottom, width - 4, height - bottom, AXIS))
    body.append(_text(left, height - 4, caption))
    return _frame(width, height, body, standalone)


def share_bar(parts, *, width=640, height=46, standalone=True):
    """One horizontal bar split into labelled shares: [(label, value, colour)]."""
    total = sum(value for _, value, _ in parts) or 1
    body, x = [], 0.0
    for label, value, color in parts:
        share = value / total * width
        body.append(f'<rect x="{x:.2f}" y="4" width="{max(share, 0):.2f}" height="18" fill="{color}"/>')
        if share > 60:
            body.append(_text(x + 6, 17, f"{label} {value / total:.0%}", 11, "#111111"))
        x += share
    legend = " · ".join(f"{label} {value / total:.0%}" for label, value, _ in parts)
    body.append(_text(0, 40, legend, 11))
    return _frame(width, height, body, standalone)
