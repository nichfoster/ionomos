"""
Static SVG charts for the accuracy pages (compare.py, benchmark.py), built on charts.py's helpers and classes.

    scatter(points, ...)        x against y with the identity line and a fitted line (compare: fold change
                                against fold change, p against p)
    group_boxes(groups, ...)    a box per group with its points and the expected value marked (benchmark:
                                measured against expected log2 ratio per species)
    dot_rows(rows, ...)         one row per label, a dot per value, a reference line (benchmark: observed
                                false discovery proportion per setting against the nominal alpha)

No script, no plotting library: the pages work offline and print as they are. Colours are charts.py's
categorical slots, so they follow the report's light and dark themes.
"""
from __future__ import annotations

import math
from html import escape

from ionomos.downstream.charts import _f, _quantiles, _svg, _tick_label, nice_ticks

MAX_POINTS = 4000   # a scatter draws at most this many background points (every highlighted one is drawn)


def _thin(points: list[dict], limit: int = MAX_POINTS) -> tuple[list[dict], int]:
    """Every point with a class other than "ns", and an even pick of the rest (deterministic)."""
    key = [p for p in points if p.get("cls", "ns") != "ns"]
    rest = [p for p in points if p.get("cls", "ns") == "ns"]
    if len(rest) > limit:
        step = len(rest) / limit
        rest = [rest[int(k * step)] for k in range(limit)]
    return rest + key, len(points) - len(rest) - len(key)


def scatter(points: list[dict], xlabel: str, ylabel: str, title: str, legend: list[tuple[str, str]] | None = None,
            line: tuple[float, float] | None = None, square: bool = True, standalone: bool = False,
            width: int = 520, height: int = 480) -> str:
    """points: [{"x", "y", "cls": "ns" | "c0".., "label"}]; line: (slope, intercept) drawn besides y = x;
    legend: [(class, text)]. square: both axes share one range (fold change against fold change)."""
    pts = [p for p in points if p["x"] is not None and p["y"] is not None
           and math.isfinite(p["x"]) and math.isfinite(p["y"])]
    ml, mr, mt, mb = 60, 16, 40, 48
    pw, ph = width - ml - mr, height - mt - mb
    xs, ys = [p["x"] for p in pts] or [0.0, 1.0], [p["y"] for p in pts] or [0.0, 1.0]
    if square:
        lo, hi = min(xs + ys), max(xs + ys)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        xt = yt = nice_ticks(lo, hi)
    else:
        xt, yt = nice_ticks(min(xs), max(xs)), nice_ticks(min(ys), max(ys))
    x0, x1, y0, y1 = xt[0], xt[-1], yt[0], yt[-1]

    def X(v):
        return ml + (v - x0) / (x1 - x0) * pw

    def Y(v):
        return mt + ph - (v - y0) / (y1 - y0) * ph

    b = []
    for v in xt:
        b.append(f'<line class="grid" x1="{_f(X(v))}" x2="{_f(X(v))}" y1="{mt}" y2="{mt + ph}"/>')
        b.append(f'<text class="tick" x="{_f(X(v))}" y="{mt + ph + 16}" text-anchor="middle">{_tick_label(v)}</text>')
    for v in yt:
        b.append(f'<line class="grid" x1="{ml}" x2="{ml + pw}" y1="{_f(Y(v))}" y2="{_f(Y(v))}"/>')
        b.append(f'<text class="tick" x="{ml - 8}" y="{_f(Y(v) + 4)}" text-anchor="end">{_tick_label(v)}</text>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}"/>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml}" y1="{mt}" y2="{mt + ph}"/>')
    a, c = max(x0, y0), min(x1, y1)
    if a < c:  # y = x, where both axes cover it
        b.append(f'<line class="thr" x1="{_f(X(a))}" y1="{_f(Y(a))}" x2="{_f(X(c))}" y2="{_f(Y(c))}"/>')
    if line is not None and math.isfinite(line[0]) and math.isfinite(line[1]):
        ends = []
        for xv in (x0, x1):  # the fitted line, clipped to the plot
            yv = line[0] * xv + line[1]
            if yv < y0 or yv > y1:
                if not line[0]:
                    continue
                yv = min(max(yv, y0), y1)
                xv = (yv - line[1]) / line[0]
            ends.append((xv, yv))
        if len(ends) == 2:
            b.append(f'<line class="s1" stroke-width="1.5" stroke-dasharray="5 4" x1="{_f(X(ends[0][0]))}" '
                     f'y1="{_f(Y(ends[0][1]))}" x2="{_f(X(ends[1][0]))}" y2="{_f(Y(ends[1][1]))}"/>')
    drawn, skipped = _thin(pts)
    for p in drawn:
        cls = p.get("cls", "ns")
        tip = f"{p.get('label', '')}  {p['x']:.3g}, {p['y']:.3g}"
        b.append(f'<circle class="{cls}" cx="{_f(X(p["x"]))}" cy="{_f(Y(p["y"]))}" r="{2.5 if cls == "ns" else 3.2}">'
                 f'<title>{escape(tip)}</title></circle>')
    b.append(f'<text class="atitle" x="{ml + pw / 2}" y="{height - 10}" text-anchor="middle">{escape(xlabel)}</text>')
    b.append(f'<text class="atitle" transform="translate(15 {mt + ph / 2}) rotate(-90)" text-anchor="middle">'
             f'{escape(ylabel)}</text>')
    x = ml
    for cls, text in legend or []:
        b.append(f'<circle class="{cls}" cx="{x + 5}" cy="{mt - 18}" r="4.5"/>')
        b.append(f'<text class="lgd" x="{x + 14}" y="{mt - 14}">{escape(text)}</text>')
        x += 28 + 6.6 * len(text)
    if not pts:
        b.append(f'<text class="atitle" x="{ml + pw / 2}" y="{mt + ph / 2}" text-anchor="middle">Nothing to plot</text>')
    elif skipped:
        b.append(f'<text class="tick" x="{ml + pw}" y="{mt + ph - 6}" text-anchor="end">{len(drawn):,} of '
                 f'{len(pts):,} points drawn</text>')
    return _svg(width, height, "".join(b), title, standalone)


def group_boxes(groups: list[dict], ylabel: str, title: str, standalone: bool = False, width: int = 640,
                height: int = 420) -> str:
    """groups: [{"name", "values": [float], "expected": float | None}]. A box (quartiles, whiskers at 1.5 IQR)
    over the group's points (thinned, spread sideways by rank), and a line at the expected value."""
    groups = [g for g in groups if g["values"]]
    ml, mr, mt, mb = 60, 16, 30, 48
    pw, ph = width - ml - mr, height - mt - mb
    if not groups:
        return _svg(width, height, f'<text class="atitle" x="{width / 2}" y="{height / 2}" text-anchor="middle">'
                    "No features in any group</text>", title, standalone)
    stats_ = [_quantiles(g["values"]) for g in groups]
    expected = [g["expected"] for g in groups if g.get("expected") is not None]
    lo = min([q[0] for q in stats_] + expected)
    hi = max([q[4] for q in stats_] + expected)
    pad = 0.08 * ((hi - lo) or 1.0)
    ticks = nice_ticks(lo - pad, hi + pad)
    y0, y1 = ticks[0], ticks[-1]
    band = pw / len(groups)

    def Y(v):
        return mt + ph - (min(max(v, y0), y1) - y0) / (y1 - y0) * ph

    b = []
    for v in ticks:
        b.append(f'<line class="grid" x1="{ml}" x2="{ml + pw}" y1="{_f(Y(v))}" y2="{_f(Y(v))}"/>')
        b.append(f'<text class="tick" x="{ml - 8}" y="{_f(Y(v) + 4)}" text-anchor="end">{_tick_label(v)}</text>')
    for i, (g, (w0, q1, med, q3, w1)) in enumerate(zip(groups, stats_, strict=True)):
        cx = ml + band * i + band / 2
        bw = min(70, band * 0.5)
        k = i % 8
        vals = sorted(g["values"])
        if len(vals) > 600:
            step = len(vals) / 600
            vals = [vals[int(j * step)] for j in range(600)]
        for j, v in enumerate(vals):  # a deterministic sideways spread, so dense regions look dense
            off = ((j * 0.618034) % 1 - 0.5) * bw * 0.9
            b.append(f'<circle class="c{k}" opacity=".28" cx="{_f(cx + off)}" cy="{_f(Y(v))}" r="1.8"/>')
        b.append(f'<line class="whisk" x1="{_f(cx)}" x2="{_f(cx)}" y1="{_f(Y(w1))}" y2="{_f(Y(q3))}"/>')
        b.append(f'<line class="whisk" x1="{_f(cx)}" x2="{_f(cx)}" y1="{_f(Y(q1))}" y2="{_f(Y(w0))}"/>')
        tip = f"{g['name']}: median {med:.2f}, quartiles {q1:.2f} to {q3:.2f}, n {len(g['values']):,}"
        b.append(f'<rect class="c{k}" x="{_f(cx - bw / 2)}" y="{_f(Y(q3))}" width="{_f(bw)}" '
                 f'height="{_f(max(Y(q1) - Y(q3), 1))}" rx="3" opacity=".45"><title>{escape(tip)}</title></rect>')
        b.append(f'<line class="med" x1="{_f(cx - bw / 2)}" x2="{_f(cx + bw / 2)}" y1="{_f(Y(med))}" y2="{_f(Y(med))}"/>')
        if g.get("expected") is not None:
            b.append(f'<line class="thr" stroke-dasharray="6 4" x1="{_f(cx - band * 0.42)}" x2="{_f(cx + band * 0.42)}" '
                     f'y1="{_f(Y(g["expected"]))}" y2="{_f(Y(g["expected"]))}"/>')
            b.append(f'<text class="tick" x="{_f(cx + band * 0.42)}" y="{_f(Y(g["expected"]) - 4)}" text-anchor="end">'
                     f'expected {_tick_label(round(g["expected"], 2))}</text>')
        b.append(f'<text class="tick" x="{_f(cx)}" y="{mt + ph + 16}" text-anchor="middle">{escape(g["name"][:24])}</text>')
        b.append(f'<text class="tick" x="{_f(cx)}" y="{mt + ph + 31}" text-anchor="middle">n = {len(g["values"]):,}</text>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}"/>')
    b.append(f'<text class="atitle" transform="translate(15 {mt + ph / 2}) rotate(-90)" text-anchor="middle">'
             f'{escape(ylabel)}</text>')
    return _svg(width, height, "".join(b), title, standalone)


def dot_rows(rows: list[tuple[str, list[float]]], xlabel: str, title: str, ref: float | None = None,
             ref_label: str = "", standalone: bool = False, width: int = 640) -> str:
    """rows: [(label, values)]. A dot per value on a shared axis from 0, the row's mean as a tick, and a
    reference line (the nominal alpha)."""
    rows = [(name, [v for v in vals if v is not None and math.isfinite(v)]) for name, vals in rows]
    band = 24
    ml = max(120, int(6.6 * max((len(n) for n, _ in rows), default=10)) + 16)
    mr, mt, mb = 24, 26, 40
    height = mt + band * max(len(rows), 1) + mb
    pw = width - ml - mr
    top = max([v for _n, vals in rows for v in vals] + [ref or 0, 1e-9])
    ticks = nice_ticks(0, top * 1.05, 6)
    top = ticks[-1]

    def X(v):
        return ml + v / top * pw

    b = []
    for v in ticks:
        b.append(f'<line class="grid" x1="{_f(X(v))}" x2="{_f(X(v))}" y1="{mt}" y2="{mt + band * len(rows)}"/>')
        b.append(f'<text class="tick" x="{_f(X(v))}" y="{mt + band * len(rows) + 16}" text-anchor="middle">'
                 f'{_tick_label(v)}</text>')
    if ref is not None:
        b.append(f'<line class="thr" stroke-dasharray="6 4" x1="{_f(X(ref))}" x2="{_f(X(ref))}" y1="{mt - 6}" '
                 f'y2="{mt + band * len(rows)}"/>')
        b.append(f'<text class="tick" x="{_f(X(ref) + 4)}" y="{mt - 10}">{escape(ref_label)}</text>')
    for i, (name, vals) in enumerate(rows):
        y = mt + band * i + band / 2
        b.append(f'<text class="tick" x="{ml - 8}" y="{_f(y + 4)}" text-anchor="end">{escape(name)}</text>')
        for v in vals:
            b.append(f'<circle class="c{i % 8}" opacity=".55" cx="{_f(X(v))}" cy="{_f(y)}" r="3.2">'
                     f'<title>{escape(name)}: {v:.3f}</title></circle>')
        if vals:
            mu = sum(vals) / len(vals)
            b.append(f'<line class="med" x1="{_f(X(mu))}" x2="{_f(X(mu))}" y1="{_f(y - 8)}" y2="{_f(y + 8)}"/>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml}" y1="{mt}" y2="{mt + band * len(rows)}"/>')
    b.append(f'<text class="atitle" x="{ml + pw / 2}" y="{height - 8}" text-anchor="middle">{escape(xlabel)}</text>')
    return _svg(width, height, "".join(b), title, standalone)


def page(title: str, meta: str, body: str) -> str:
    """A self-contained HTML page with the report's CSS and the chart classes."""
    from ionomos.downstream.charts import STYLE
    from ionomos.downstream.report import _asset

    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
            f"<style>{_asset('report.css')}{STYLE}</style></head><body><main><h1>{escape(title)}</h1>"
            f"<div class='meta'>{escape(meta)}</div>{body}</main></body></html>")
