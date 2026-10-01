"""
Charts as hand-written SVG: no plotting library, works offline, light and dark.

    volcano(diff)            the differential result
    id_bars(m)               features quantified per sample (QC)
    box_plots(m)             log2 value distribution per sample (QC)
    correlation_heatmap(m)   sample-sample Pearson r (QC)
    figures(payload, style)  the figures for slides, drawn from the report's data with one export style
                             (volcano, PCA, heatmap, correlation; colours written out, no CSS): see the end

Colour roles follow the dataviz reference palette (validated with its
checker): up/down use the diverging red/blue poles, not-significant is a
neutral gray, conditions take categorical slots in fixed order, the heatmap
is the one-hue blue ramp. Every SVG uses CSS classes; `STYLE` defines them
(light + dark) and is embedded in standalone .svg files and once in the report.
Each mark carries data-* attributes for the report's tooltip, and a <title>
for standalone viewing.
"""
from __future__ import annotations

import math
import re
from html import escape

from ionomos.downstream import stats
from ionomos.downstream.analysis import DiffResult
from ionomos.downstream.quant import QuantMatrix

CATEGORICAL = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
               ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#4a3aa7", "#9085e9"), ("#e34948", "#e66767")]
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
       "#1c5cab", "#184f95", "#104281", "#0d366b"]

_TOKENS_LIGHT = """--vz-surface:#fcfcfb;--vz-text:#0b0b0b;--vz-text2:#52514e;--vz-muted:#898781;
--vz-grid:#e1e0d9;--vz-axis:#c3c2b7;--vz-up:#e34948;--vz-down:#2a78d6;--vz-ns:#c3c2b7;--vz-warn:#a15c07;"""
_TOKENS_DARK = """--vz-surface:#1a1a19;--vz-text:#ffffff;--vz-text2:#c3c2b7;--vz-muted:#898781;
--vz-grid:#2c2c2a;--vz-axis:#383835;--vz-up:#e66767;--vz-down:#3987e5;--vz-ns:#52514e;--vz-warn:#f2b64a;"""


def _cat_tokens(dark: bool) -> str:
    return "".join(f"--vz-c{i}:{pair[1 if dark else 0]};" for i, pair in enumerate(CATEGORICAL))


STYLE = f"""
.vz{{{_TOKENS_LIGHT}{_cat_tokens(False)}font-family:system-ui,-apple-system,"Segoe UI",sans-serif}}
@media (prefers-color-scheme: dark){{:root:where(:not([data-theme="light"])) .vz{{{_TOKENS_DARK}{_cat_tokens(True)}}}}}
:root[data-theme="dark"] .vz{{{_TOKENS_DARK}{_cat_tokens(True)}}}
.vz .bg{{fill:var(--vz-surface)}}
.vz .grid{{stroke:var(--vz-grid);stroke-width:1}}
.vz .axis{{stroke:var(--vz-axis);stroke-width:1}}
.vz .thr{{stroke:var(--vz-muted);stroke-width:1}}
.vz .tick{{fill:var(--vz-muted);font-size:12px;font-variant-numeric:tabular-nums}}
.vz .atitle{{fill:var(--vz-text2);font-size:13px}}
.vz .lbl{{fill:var(--vz-text2);font-size:12px}}
.vz .lgd{{fill:var(--vz-text2);font-size:13px}}
.vz .warn{{fill:var(--vz-warn);font-size:12.5px;font-weight:600}}
.vz .up{{fill:var(--vz-up);stroke:var(--vz-surface);stroke-width:1.5}}
.vz .down{{fill:var(--vz-down);stroke:var(--vz-surface);stroke-width:1.5}}
.vz .ns{{fill:var(--vz-ns);opacity:.7}}
.vz .med{{stroke:var(--vz-text);stroke-width:2}}
.vz .whisk{{stroke:var(--vz-muted);stroke-width:1}}
.vz .cellv{{font-size:10px;font-variant-numeric:tabular-nums}}
{"".join(f".vz .c{i}{{fill:var(--vz-c{i})}} .vz .s{i}{{stroke:var(--vz-c{i})}}" for i in range(len(CATEGORICAL)))}
"""


def _f(v: float) -> str:
    return f"{v:.1f}"


def nice_ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step + 1e-9) * step  # the axis always covers the data: first tick <= lo ...
    end = math.ceil(hi / step - 1e-9) * step     # ... and last tick >= hi
    out, v = [], start
    while v <= end + 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def _tick_label(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:g}".replace("-", "−")


def _svg(w: int, h: int, body: str, label: str, standalone: bool) -> str:
    style = f"<style>{STYLE}</style>" if standalone else ""
    ns = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ""
    return (f'<svg{ns} class="vz" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{escape(label)}" '
            f'preserveAspectRatio="xMidYMid meet" style="max-width:{w}px">{style}'
            f'<rect class="bg" x="0" y="0" width="{w}" height="{h}" rx="8"/>{body}</svg>')


def condition_slots(m: QuantMatrix) -> dict[str, int]:
    """Conditions -> categorical slot, fixed by first appearance (colour follows the entity)."""
    return {c: i % len(CATEGORICAL) for i, c in enumerate(m.conditions)}


# ------------------------------------------------------------------- volcano --


def _conf_banner(d: DiffResult, x: float, y: float) -> str:
    """The label every low-confidence / fold-change-only plot carries, so a copied SVG can't lose it."""
    few = "one sample" if not d.groups or min(d.groups) <= 1 else f"{min(d.groups)} samples"
    text = {"low": f"LOW CONFIDENCE — a group has {few}; p-values borrowed",
            "none": "FOLD CHANGE ONLY — no replicates, no p-values"}.get(d.confidence)
    return f'<text class="warn" x="{_f(x)}" y="{_f(y)}" text-anchor="end">{text}</text>' if text else ""


def fold_change_plot(d: DiffResult, standalone: bool = False, width: int = 760, height: int = 520) -> str:
    """For comparisons with no replicates: log2FC (y) against mean log2 abundance (x), or against rank for
    ratio data. Candidates are |log2FC| >= the cut-off; nothing here is a p-value."""
    s = d.settings
    lfc = s.log2fc or 1.0
    ml, mr, mt, mb = 64, 24, 40, 52
    pw, ph = width - ml - mr, height - mt - mb
    pts = [r for r in d.rows if r["log2fc"] is not None and math.isfinite(r["log2fc"])]
    ratio = d.control is None or not any(r["mean_treatment"] is not None and r["mean_control"] is not None
                                         for r in pts)  # no abundances: rank instead
    if ratio:
        ordered = sorted(pts, key=lambda r: r["log2fc"])
        xs = {id(r): k + 1 for k, r in enumerate(ordered)}
    else:
        xs = {id(r): ((r["mean_treatment"] + r["mean_control"]) / 2 if r["mean_treatment"] is not None
                      and r["mean_control"] is not None else None) for r in pts}
        pts = [r for r in pts if xs[id(r)] is not None and math.isfinite(xs[id(r)])]
    xv = [xs[id(r)] for r in pts] or [0.0, 1.0]
    x0, x1 = min(xv), max(xv)
    if x1 - x0 < 1e-9:
        x0, x1 = x0 - 1, x1 + 1
    ymax = max([abs(r["log2fc"]) for r in pts] + [lfc + 0.5]) * 1.08
    xt, yt = nice_ticks(x0, x1), nice_ticks(-ymax, ymax)
    x0, x1 = min(x0, xt[0]), max(x1, xt[-1])
    ymax = max(ymax, abs(yt[0]), abs(yt[-1]))

    def X(v):
        return ml + (v - x0) / (x1 - x0) * pw

    def Y(v):
        return mt + ph - (v + ymax) / (2 * ymax) * ph

    b = []
    for v in xt:
        b.append(f'<line class="grid" x1="{_f(X(v))}" x2="{_f(X(v))}" y1="{mt}" y2="{mt + ph}"/>')
        b.append(f'<text class="tick" x="{_f(X(v))}" y="{mt + ph + 16}" text-anchor="middle">{_tick_label(v)}</text>')
    for v in yt:
        b.append(f'<line class="grid" x1="{ml}" x2="{ml + pw}" y1="{_f(Y(v))}" y2="{_f(Y(v))}"/>')
        b.append(f'<text class="tick" x="{ml - 8}" y="{_f(Y(v) + 4)}" text-anchor="end">{_tick_label(v)}</text>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}"/>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml}" y1="{mt}" y2="{mt + ph}"/>')
    for v in (-lfc, lfc):
        b.append(f'<line class="thr" x1="{ml}" x2="{ml + pw}" y1="{_f(Y(v))}" y2="{_f(Y(v))}"/>')
    xlabel = ("rank (sorted by log2 H/L)" if d.control is None else "rank (sorted by log2 fold change)") if ratio \
        else "mean log2 abundance"
    ylabel = "log2 ratio heavy / light" if d.control is None else \
        f"log2 fold change ({escape(d.treatment)} / {escape(d.control)})"
    b.append(f'<text class="atitle" x="{ml + pw / 2}" y="{height - 12}" text-anchor="middle">{xlabel}</text>')
    b.append(f'<text class="atitle" transform="translate(16 {mt + ph / 2}) rotate(-90)" text-anchor="middle">'
             f'{ylabel}</text>')
    order = {"": 0, "down": 1, "up": 2}
    for r in sorted(pts, key=lambda r: order[r["significant"]]):
        cls = r["significant"] or "ns"
        tip = f"{r['label']}  log2FC {r['log2fc']:.2f}  (fold change only)"
        b.append(f'<circle class="{cls}" cx="{_f(X(xs[id(r)]))}" cy="{_f(Y(r["log2fc"]))}" r="{4 if cls != "ns" else 3}" '
                 f'data-l="{escape(r["label"])}" data-fc="{r["log2fc"]:.3f}"><title>{escape(tip)}</title></circle>')
    placed: list[tuple[float, float, float, float]] = []
    for r in [r for r in pts if r["significant"]][: s.top_labels]:  # rows are sorted by |log2FC|
        cx, cy = X(xs[id(r)]), Y(r["log2fc"])
        text = r["label"][:24]
        w = 7.0 * len(text)
        for x0_, dy in ((cx + 7, 4), (cx - 7 - w, 4), (cx + 7, -8), (cx - 7 - w, 16)):
            box = (x0_, cy + dy - 10, x0_ + w, cy + dy + 3)
            inside = ml <= box[0] and box[2] <= ml + pw and mt <= box[1] and box[3] <= mt + ph
            if inside and not any(not (box[2] < p[0] or box[0] > p[2] or box[3] < p[1] or box[1] > p[3]) for p in placed):
                placed.append(box)
                b.append(f'<text class="lbl" x="{_f(x0_)}" y="{_f(cy + dy)}">{escape(text)}</text>')
                break
    if not pts:
        b.append(f'<text class="atitle" x="{ml + pw / 2}" y="{mt + ph / 2}" text-anchor="middle">No fold changes '
                 f'could be computed — see the issues in report.html</text>')
    x = ml
    for cls, name, n in (("up", "Up", d.up), ("down", "Down", d.down)):
        b.append(f'<circle class="{cls}" cx="{x + 5}" cy="{mt - 16}" r="5"/>')
        label = f"{name} {n:,} (|log2FC| ≥ {lfc:g})"
        b.append(f'<text class="lgd" x="{x + 14}" y="{mt - 12}">{label}</text>')
        x += 30 + 7 * len(label)
    b.append(_conf_banner(d, ml + pw, mt + 14))
    return _svg(width, height, "".join(b), f"Fold-change plot (no statistics), {d.name}: {d.up} up, {d.down} down",
                standalone)


def volcano(d: DiffResult, standalone: bool = False, width: int = 760, height: int = 520) -> str:
    if getattr(d, "confidence", "") == "none":
        return fold_change_plot(d, standalone, width, height)
    s = d.settings
    ml, mr, mt, mb = 64, 24, 40, 52
    pw, ph = width - ml - mr, height - mt - mb
    pts = [r for r in d.rows if r["pvalue"] is not None and r["log2fc"] is not None and math.isfinite(r["log2fc"])]
    ys = [-math.log10(r["pvalue"]) for r in pts if r["pvalue"] > 0]
    ycap = (max(ys) if ys else 1) + 0.5
    xmax = max([abs(r["log2fc"]) for r in pts] + [s.log2fc + 0.5, 1.0]) * 1.05
    ymax = max(ycap, -math.log10(d.y_threshold_p) if d.y_threshold_p else 0, 2) * 1.05
    xt, yt = nice_ticks(-xmax, xmax), nice_ticks(0, ymax)
    xmax = max(abs(xt[0]), abs(xt[-1]), xmax)
    ymax = max(yt[-1], ymax)

    def X(v):
        return ml + (v + xmax) / (2 * xmax) * pw

    def Y(v):
        return mt + ph - v / ymax * ph

    b = []
    for v in xt:
        b.append(f'<line class="grid" x1="{_f(X(v))}" x2="{_f(X(v))}" y1="{mt}" y2="{mt + ph}"/>')
        b.append(f'<text class="tick" x="{_f(X(v))}" y="{mt + ph + 16}" text-anchor="middle">{_tick_label(v)}</text>')
    for v in yt:
        b.append(f'<line class="grid" x1="{ml}" x2="{ml + pw}" y1="{_f(Y(v))}" y2="{_f(Y(v))}"/>')
        b.append(f'<text class="tick" x="{ml - 8}" y="{_f(Y(v) + 4)}" text-anchor="end">{_tick_label(v)}</text>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}"/>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml}" y1="{mt}" y2="{mt + ph}"/>')
    if s.log2fc > 0:
        for v in (-s.log2fc, s.log2fc):
            if -xmax < v < xmax:
                b.append(f'<line class="thr" x1="{_f(X(v))}" x2="{_f(X(v))}" y1="{mt}" y2="{mt + ph}"/>')
    if d.y_threshold_p:
        yv = -math.log10(d.y_threshold_p)
        if 0 < yv < ymax:
            b.append(f'<line class="thr" x1="{ml}" x2="{ml + pw}" y1="{_f(Y(yv))}" y2="{_f(Y(yv))}"/>')
    xlabel = (f"log2 fold change ({escape(d.treatment)} / {escape(d.control)})" if d.control
              else "log2 ratio heavy / light")
    b.append(f'<text class="atitle" x="{ml + pw / 2}" y="{height - 12}" text-anchor="middle">{xlabel}</text>')
    b.append(f'<text class="atitle" transform="translate(16 {mt + ph / 2}) rotate(-90)" text-anchor="middle">'
             f'−log10 p-value</text>')

    order = {"": 0, "down": 1, "up": 2}
    placed: list[tuple[float, float, float, float]] = []
    for i, r in enumerate(sorted(pts, key=lambda r: order[r["significant"]])):
        y = -math.log10(r["pvalue"]) if r["pvalue"] > 0 else ycap
        cls = r["significant"] or "ns"
        rad = 4 if cls != "ns" else 3
        q = "" if r.get("qvalue") is None else f"{r['qvalue']:.3g}"
        tip = f"{r['label']}  log2FC {r['log2fc']:.2f}  p {r['pvalue']:.3g}" + (f"  q {q}" if q else "")
        b.append(f'<circle class="{cls}" cx="{_f(X(r["log2fc"]))}" cy="{_f(Y(y))}" r="{rad}" data-i="{i}" '
                 f'data-l="{escape(r["label"])}" data-fc="{r["log2fc"]:.3f}" data-p="{r["pvalue"]:.3g}" '
                 f'data-q="{q}"><title>{escape(tip)}</title></circle>')

    # selective direct labels: the most significant hits only, skipped when they would collide
    sig = [r for r in pts if r["significant"]][: s.top_labels]
    for r in sig:
        cx = X(r["log2fc"])
        cy = Y(-math.log10(r["pvalue"]) if r["pvalue"] > 0 else ycap)
        text = r["label"][:24]
        w = 7.0 * len(text)
        right = r["log2fc"] >= 0
        for dy in (4, -8, 16, -20, 28):
            x0 = cx + 7 if right else cx - 7 - w
            y0 = cy + dy - 10
            box = (x0, y0, x0 + w, y0 + 13)
            inside = ml <= box[0] and box[2] <= ml + pw and mt <= box[1] and box[3] <= mt + ph
            clash = any(not (box[2] < p[0] or box[0] > p[2] or box[3] < p[1] or box[1] > p[3]) for p in placed)
            if inside and not clash:
                placed.append(box)
                b.append(f'<text class="lbl" x="{_f(x0)}" y="{_f(y0 + 10)}">{escape(text)}</text>')
                break

    if not pts:
        b.append(f'<text class="atitle" x="{ml + pw / 2}" y="{mt + ph / 2}" text-anchor="middle">No features could be '
                 f'tested in this comparison — see the issues in report.html</text>')
    lg = [("up", "Up", d.up), ("down", "Down", d.down), ("ns", "Not significant", d.tested - d.up - d.down)]
    x = ml
    for cls, name, n in lg:
        b.append(f'<circle class="{cls}" cx="{x + 5}" cy="{mt - 16}" r="5"/>')
        label = f"{name} {n:,}"
        b.append(f'<text class="lgd" x="{x + 14}" y="{mt - 12}">{label}</text>')
        x += 30 + 7 * len(label)
    b.append(_conf_banner(d, ml + pw, mt + 14))
    return _svg(width, height, "".join(b), f"Volcano plot, {d.name}: {d.up} up, {d.down} down", standalone)


# ------------------------------------------------------------------------ QC --


def id_bars(m: QuantMatrix, standalone: bool = False) -> str:
    slots = condition_slots(m)
    counts = [(smp, sum(1 for v in m.column(smp) if v is not None)) for smp in m.samples]
    band = 26
    ml = max(90, 7 * max(len(s) for s in m.samples) + 16)
    width, mt, mb = 760, 34, 36
    height = mt + band * len(counts) + mb
    pw = width - ml - 70
    top = max(c for _, c in counts) or 1
    ticks = nice_ticks(0, top, 5)
    top = ticks[-1]
    b = []
    for v in ticks:
        x = ml + v / top * pw
        b.append(f'<line class="grid" x1="{_f(x)}" x2="{_f(x)}" y1="{mt}" y2="{mt + band * len(counts)}"/>')
        b.append(f'<text class="tick" x="{_f(x)}" y="{mt + band * len(counts) + 16}" text-anchor="middle">{_tick_label(v)}</text>')
    for i, (smp, c) in enumerate(counts):
        y = mt + i * band
        th = min(18, band - 6)
        w = max(c / top * pw, 1)
        cls = f"c{slots[m.condition[smp]]}"
        r = min(4, w / 2)
        # 4px rounded data-end, square at the baseline
        path = (f"M{ml},{_f(y + (band - th) / 2)} h{_f(w - r)} a{r},{r} 0 0 1 {r},{r} v{_f(th - 2 * r)} "
                f"a{r},{r} 0 0 1 -{r},{r} h-{_f(w - r)} z")
        b.append(f'<path class="{cls}" d="{path}" data-l="{escape(smp)}" data-v="{c:,}"><title>{escape(smp)}: {c:,}</title></path>')
        b.append(f'<text class="tick" x="{ml - 8}" y="{_f(y + band / 2 + 4)}" text-anchor="end">{escape(smp)}</text>')
        b.append(f'<text class="lbl" x="{_f(ml + w + 6)}" y="{_f(y + band / 2 + 4)}">{c:,}</text>')
    b.append(f'<line class="axis" x1="{ml}" x2="{ml}" y1="{mt}" y2="{mt + band * len(counts)}"/>')
    b.append(_legend(m, slots, ml, 18))
    return _svg(width, height, "".join(b), f"{m.level}s quantified per sample", standalone)


def _legend(m: QuantMatrix, slots: dict[str, int], x: float, y: float) -> str:
    if len(slots) < 2:
        return ""
    out = []
    for c, i in slots.items():
        out.append(f'<rect class="c{i}" x="{_f(x)}" y="{_f(y - 9)}" width="10" height="10" rx="2"/>')
        out.append(f'<text class="lgd" x="{_f(x + 15)}" y="{_f(y)}">{escape(c)}</text>')
        x += 30 + 7 * len(c)
    return "".join(out)


def _quantiles(xs: list[float]) -> tuple[float, float, float, float, float]:
    s = sorted(xs)

    def q(p):
        k = (len(s) - 1) * p
        f = math.floor(k)
        c = min(f + 1, len(s) - 1)
        return s[f] + (s[c] - s[f]) * (k - f)

    q1, med, q3 = q(0.25), q(0.5), q(0.75)
    iqr = q3 - q1
    lo = min(v for v in s if v >= q1 - 1.5 * iqr)
    hi = max(v for v in s if v <= q3 + 1.5 * iqr)
    return lo, q1, med, q3, hi


def box_plots(m: QuantMatrix, standalone: bool = False) -> str:
    slots = condition_slots(m)
    data = [(smp, [v for v in m.column(smp) if v is not None]) for smp in m.samples]
    data = [(s, v) for s, v in data if len(v) >= 3]
    if not data:
        return ""
    qs = [(s, _quantiles(v)) for s, v in data]
    lo = min(q[0] for _, q in qs)
    hi = max(q[4] for _, q in qs)
    ticks = nice_ticks(lo, hi, 6)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    ml, mr, mt = 56, 16, 34
    band = max(28, min(60, 680 // max(len(qs), 1)))
    width = ml + mr + band * len(qs)
    rot = max(len(s) for s, _ in qs) > band // 7
    mb = 30 + (min(max(len(s) for s, _ in qs), 30) * 5 if rot else 0)
    height = mt + 240 + mb
    ph = 240

    def Y(v):
        return mt + ph - (v - lo) / (hi - lo or 1) * ph

    b = []
    for v in ticks:
        b.append(f'<line class="grid" x1="{ml}" x2="{width - mr}" y1="{_f(Y(v))}" y2="{_f(Y(v))}"/>')
        b.append(f'<text class="tick" x="{ml - 8}" y="{_f(Y(v) + 4)}" text-anchor="end">{_tick_label(v)}</text>')
    for i, (smp, (w0, q1, med, q3, w1)) in enumerate(qs):
        cx = ml + band * i + band / 2
        bw = min(24, band * 0.6)
        k = slots[m.condition[smp]]
        tip = f"{smp}: median {med:.2f}, IQR {q1:.2f}–{q3:.2f}"
        b.append(f'<line class="whisk" x1="{_f(cx)}" x2="{_f(cx)}" y1="{_f(Y(w1))}" y2="{_f(Y(q3))}"/>')
        b.append(f'<line class="whisk" x1="{_f(cx)}" x2="{_f(cx)}" y1="{_f(Y(q1))}" y2="{_f(Y(w0))}"/>')
        b.append(f'<rect class="c{k}" x="{_f(cx - bw / 2)}" y="{_f(Y(q3))}" width="{_f(bw)}" '
                 f'height="{_f(max(Y(q1) - Y(q3), 1))}" rx="3" opacity=".55" data-l="{escape(smp)}" '
                 f'data-v="median {med:.2f}"><title>{escape(tip)}</title></rect>')
        b.append(f'<line class="med" x1="{_f(cx - bw / 2)}" x2="{_f(cx + bw / 2)}" y1="{_f(Y(med))}" y2="{_f(Y(med))}"/>')
        if rot:
            b.append(f'<text class="tick" transform="translate({_f(cx + 3)} {mt + ph + 12}) rotate(-45)" '
                     f'text-anchor="end">{escape(smp[:30])}</text>')
        else:
            b.append(f'<text class="tick" x="{_f(cx)}" y="{mt + ph + 16}" text-anchor="middle">{escape(smp)}</text>')
    b.append(f'<line class="axis" x1="{ml}" x2="{width - mr}" y1="{mt + ph}" y2="{mt + ph}"/>')
    b.append(_legend(m, slots, ml, 18))
    kind = "log2 ratio" if m.kind == "ratio" else "log2 value"
    b.append(f'<text class="atitle" transform="translate(14 {mt + ph / 2}) rotate(-90)" text-anchor="middle">{kind}</text>')
    return _svg(width, height, "".join(b), "value distribution per sample", standalone)


def correlations(m: QuantMatrix) -> list[list[float | None]]:
    cols = [m.column(s) for s in m.samples]
    n = len(cols)
    out = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            pairs = [(a, b) for a, b in zip(cols[i], cols[j], strict=True) if a is not None and b is not None]
            if len(pairs) < 3:
                continue
            xa, xb = [p[0] for p in pairs], [p[1] for p in pairs]
            ma, mb = stats.mean(xa), stats.mean(xb)
            sa = math.sqrt(sum((x - ma) ** 2 for x in xa))
            sb = math.sqrt(sum((x - mb) ** 2 for x in xb))
            if sa == 0 or sb == 0:
                continue
            r = sum((x - ma) * (y - mb) for x, y in pairs) / (sa * sb)
            out[i][j] = out[j][i] = r
    return out


def correlation_heatmap(m: QuantMatrix, standalone: bool = False) -> str:
    if len(m.samples) < 2:
        return ""
    r = correlations(m)
    vals = [v for row in r for v in row if v is not None]
    if not vals:
        return ""
    lo = min(min(vals), 0.9 if min(vals) > 0.9 else min(vals))
    n = len(m.samples)
    cell = max(14, min(44, 560 // n))
    lab = min(max(len(s) for s in m.samples), 30) * 6.5 + 12
    ml, mt = lab, lab * 0.72 + 12
    width = int(ml + cell * n + 120)
    height = int(mt + cell * n + 20)
    b = []
    for i in range(n):
        for j in range(n):
            v = r[i][j]
            x, y = ml + j * cell, mt + i * cell
            if v is None:
                continue
            t = (v - lo) / (1 - lo) if 1 - lo > 0 else 1
            k = min(len(SEQ) - 1, max(0, round(t * (len(SEQ) - 1))))
            b.append(f'<rect x="{_f(x + 1)}" y="{_f(y + 1)}" width="{cell - 2}" height="{cell - 2}" rx="2" '
                     f'fill="{SEQ[k]}" data-l="{escape(m.samples[i])} × {escape(m.samples[j])}" data-v="r = {v:.3f}">'
                     f'<title>{escape(m.samples[i])} × {escape(m.samples[j])}: r = {v:.3f}</title></rect>')
            if cell >= 34:
                ink = "#ffffff" if k >= 7 else "#0b0b0b"
                b.append(f'<text class="cellv" x="{_f(x + cell / 2)}" y="{_f(y + cell / 2 + 3)}" text-anchor="middle" '
                         f'fill="{ink}">{v:.2f}</text>')
    for i, smp in enumerate(m.samples):
        b.append(f'<text class="tick" x="{_f(ml - 6)}" y="{_f(mt + i * cell + cell / 2 + 4)}" text-anchor="end">{escape(smp[:30])}</text>')
        b.append(f'<text class="tick" transform="translate({_f(ml + i * cell + cell / 2 + 4)} {_f(mt - 6)}) rotate(-45)">{escape(smp[:30])}</text>')
    # colour key
    kx = ml + cell * n + 24
    for k, col in enumerate(reversed(SEQ)):
        b.append(f'<rect x="{kx}" y="{_f(mt + k * 12)}" width="14" height="12" fill="{col}"/>')
    b.append(f'<text class="tick" x="{kx + 20}" y="{_f(mt + 9)}">r = 1</text>')
    b.append(f'<text class="tick" x="{kx + 20}" y="{_f(mt + len(SEQ) * 12)}">r = {lo:.2f}</text>')
    return _svg(width, height, "".join(b), "sample correlation heatmap", standalone)


# ------------------------------------------------------- figures for slides --
#
# The same figures as the report's export (assets/report.js "figure export"), drawn here from the report's
# data (report.payload) so `ionomos export` and the watcher need no browser: volcano, PCA, heatmap of the hits,
# sample correlation. One style, with the keys the report saves as a house style (export_style.json) and
# config.yaml holds under analysis.export. Unlike the plots above, nothing here uses CSS: colours are written
# out and text is plain <text> with a named font and fallbacks, so PowerPoint, Illustrator and Inkscape open
# the files as they are. Keep STYLE_DEFAULTS, SIZES and PALETTES in step with report.js.

SIZES = {  # name, width, height, unit, text size (pt) that suits it; a 16:9 PowerPoint slide is 1280 x 720 px at 96 px / inch
    "slide169": ("16:9 slide", 1280, 720, "px", 14), "slide43": ("4:3 slide", 960, 720, "px", 14),
    "half": ("Half a slide", 640, 600, "px", 12), "col1": ("Journal figure, one column (85 mm)", 85, 70, "mm", 7),
    "col2": ("Journal figure, two columns (180 mm)", 180, 110, "mm", 7), "custom": ("Custom size", 0, 0, "", 0)}
STATIC_FIGURES = ("volcano", "pca", "heatmap", "correlation")
STYLE_DEFAULTS: dict = {
    "size": "slide169", "width": 1280, "height": 720, "unit": "px", "font_pt": 14, "font_family": "Arial",
    "line_scale": 1, "point_scale": 1, "palette": "default", "up": "#e34948", "down": "#2a78d6", "neutral": "#c3c2b7",
    "background": "light", "title": True, "subtitle": True, "legend": True, "note": True, "labels": "screen",
    "label_count": None, "png_scale": 2, "png_dpi": 0, "zip_format": "both", "figures": []}
_ENUMS = {"size": tuple(SIZES), "unit": ("px", "mm"), "palette": ("default", "colorblind", "grey", "custom"),
          "background": ("light", "dark", "transparent"), "labels": ("screen", "top", "marked", "none"),
          "zip_format": ("svg", "png", "both")}
_RANGES = {"width": (20, 8000), "height": (20, 8000), "font_pt": (4, 48), "line_scale": (0.25, 4),
           "point_scale": (0.25, 4), "png_scale": (1, 4)}
PALETTES = {  # (light, dark): up, down, not significant, the eight categories; colorblind is Okabe and Ito's set
    "default": ({"up": "#e34948", "down": "#2a78d6", "ns": "#c3c2b7", "c": [p[0] for p in CATEGORICAL]},
                {"up": "#e66767", "down": "#3987e5", "ns": "#52514e", "c": [p[1] for p in CATEGORICAL]}),
    "colorblind": ({"up": "#d55e00", "down": "#0072b2", "ns": "#b3b3b3",
                    "c": ["#0072b2", "#e69f00", "#009e73", "#cc79a7", "#56b4e9", "#d55e00", "#f0e442", "#000000"]},
                   {"up": "#e69f00", "down": "#56b4e9", "ns": "#5a5a5a",
                    "c": ["#56b4e9", "#e69f00", "#009e73", "#cc79a7", "#0072b2", "#d55e00", "#f0e442", "#ffffff"]}),
    "grey": ({"up": "#111111", "down": "#6b6b6b", "ns": "#cfcfcf",
              "c": ["#111111", "#5c5c5c", "#8c8c8c", "#b0b0b0", "#333333", "#747474", "#9e9e9e", "#c4c4c4"]},
             {"up": "#f2f2f2", "down": "#9a9a9a", "ns": "#4d4d4d",
              "c": ["#f2f2f2", "#b0b0b0", "#8c8c8c", "#6b6b6b", "#d9d9d9", "#9e9e9e", "#7a7a7a", "#5c5c5c"]}),
}
_INKS = {
    "light": {"surface": "#ffffff", "sunk": "#f1f0ec", "text": "#0b0b0b", "text2": "#3f3e3c", "muted": "#6f6d68",
              "grid": "#e1e0d9", "axis": "#a8a79c", "warn": "#8a5300"},
    "dark": {"surface": "#1a1a19", "sunk": "#141413", "text": "#ffffff", "text2": "#c3c2b7", "muted": "#9a9891",
             "grid": "#2c2c2a", "axis": "#4a4a46", "warn": "#f2b64a"},
}
_TESTS = {"limma": "limma moderated t-test", "welch": "Welch t-test", "student": "Student t-test"}
_PAD = 12


class StyleError(ValueError):
    pass


def _style_value(k: str, v):
    """One style value, checked. Raises StyleError saying what the key takes."""
    num = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if k in _ENUMS:
        if isinstance(v, str) and v.strip().lower() in _ENUMS[k]:
            return v.strip().lower()
        raise StyleError(f"export.{k} must be one of {', '.join(_ENUMS[k])}")
    if k in _RANGES:
        lo, hi = _RANGES[k]
        if num and lo <= v <= hi:
            return v
        raise StyleError(f"export.{k} must be a number from {lo:g} to {hi:g}")
    if isinstance(STYLE_DEFAULTS[k], bool):
        if isinstance(v, bool):
            return v
        raise StyleError(f"export.{k} must be true or false")
    if k in ("up", "down", "neutral"):
        if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v.strip()):
            return v.strip().lower()
        raise StyleError(f"export.{k} must be a colour like \"#e34948\" (in quotes)")
    if k == "font_family":
        if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}", v.strip()):
            return v.strip()
        raise StyleError("export.font_family must be a font's name: letters, digits, spaces, - and _ (at most 40)")
    if k == "label_count":
        if v is None or (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 200):
            return v
        raise StyleError("export.label_count must be a whole number from 0 to 200 (or empty: as the report)")
    if k == "png_dpi":
        if num and (v == 0 or 72 <= v <= 1200):
            return v
        raise StyleError("export.png_dpi must be 0 (use png_scale) or 72 to 1200")
    if k == "figures":
        items = [v] if isinstance(v, str) else v
        if isinstance(items, (list, tuple)):
            names = [str(x).strip().lower() for x in items if str(x).strip()]
            names = list(STATIC_FIGURES) if names == ["all"] else [n for n in names if n != "none"]
            if all(n in STATIC_FIGURES for n in names):
                return list(dict.fromkeys(names))
        raise StyleError(f"export.figures must list any of {', '.join(STATIC_FIGURES)} (or all, or [] for none)")
    raise StyleError(f"unknown export setting {k!r}")


def style_layer(raw, lenient: bool = False) -> dict:
    """The keys a config block, a style file or a command line sets, each checked. Unknown keys and bad
    values raise StyleError (a typo should not silently do nothing); lenient=True drops them instead."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        if lenient:
            return {}
        raise StyleError("export must be a mapping, e.g. {size: slide169, palette: colorblind}")
    out = {}
    for key, v in raw.items():
        k = str(key).strip().lower()
        if k == "ionomos_export_style":  # the marker of a style file saved from the report
            continue
        try:
            if k not in STYLE_DEFAULTS:
                raise StyleError(f"unknown export setting {key!r} (known: {', '.join(STYLE_DEFAULTS)})")
            out[k] = _style_value(k, v)
        except StyleError:
            if not lenient:
                raise
    # a size named without a text size takes the size that suits it (7 pt for a journal column, 14 pt for a slide)
    if "size" in out and "font_pt" not in out and out["size"] != "custom":
        out["font_pt"] = SIZES[out["size"]][4]
    return out


def style_from(*layers, lenient: bool = False) -> dict:
    """A complete style: the defaults, then each layer's keys (later layers win)."""
    out = {**STYLE_DEFAULTS, "figures": []}
    for layer in layers:
        out.update(style_layer(layer, lenient))
    return out


def safe_name(name: str) -> str:
    """A file name that is safe on Windows, macOS and in a zip (report.js safeName): ASCII letters, digits and
    . _ + - only, no dot or underscore at either end, no "..", not a device name, at most 120 characters."""
    s = re.sub(r"[^A-Za-z0-9_.+-]+", "_", str(name or ""))
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", s)
    ext = "." + m.group(1) if m and m.start() > 0 else ""
    stem = re.sub(r"[._]{2,}", "_", s[: m.start()] if ext else s)
    stem = re.sub(r"[._]+$", "", re.sub(r"^[._-]+|[._]+$", "", stem)[:110]) or "figure"
    if re.fullmatch(r"(?i)con|prn|aux|nul|com[0-9]|lpt[0-9]", stem.split(".")[0]):
        stem = "_" + stem
    return stem + ext


def clean_text(s) -> str:
    """Text for a file: no control characters (they break XML)."""
    return re.sub(r"[\x00-\x1f\x7f￾￿]+", " ", "" if s is None else str(s)).strip()


def _x(s) -> str:
    return escape(clean_text(s), quote=True)


def _n(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def size_px(style: dict) -> tuple[float, float, bool]:
    """(width, height) of the whole figure in px at 96 per inch, and whether the size is given in mm."""
    own = style["size"] == "custom"
    z = SIZES[style["size"]]
    mm = (style["unit"] if own else z[3]) == "mm"
    k = 96 / 25.4 if mm else 1
    w, h = (style["width"], style["height"]) if own else (z[1], z[2])
    return max(120, min(8000, w * k)), max(120, min(8000, h * k)), mm


def font_stack(name: str) -> str:
    """The family with fallbacks every program knows, e.g. 'Segoe UI', Arial, Helvetica, sans-serif."""
    low = name.lower()
    if re.search(r"times|georgia|garamond|cambria|palatino|serif|book", low) and "sans" not in low:
        tail = ["'Times New Roman'", "Times", "serif"]
    elif re.search(r"courier|mono|consolas|menlo", low):
        tail = ["'Courier New'", "Courier", "monospace"]
    else:
        tail = ["Arial", "Helvetica", "sans-serif"]
    first = f"'{name}'" if re.search(r"[ _-]", name) else name
    return ", ".join([first] + [t for t in tail if t.replace("'", "").lower() != low])


def _inks(style: dict) -> dict:
    dark = style["background"] == "dark"
    t = dict(_INKS["dark" if dark else "light"])
    m = PALETTES.get(style["palette"], PALETTES["default"])[1 if dark else 0]
    own = style["palette"] == "custom"
    t.update(up=style["up"] if own else m["up"], down=style["down"] if own else m["down"],
             ns=style["neutral"] if own else m["ns"], c=m["c"])
    if style["palette"] == "grey":  # the inks too: no tint left
        t = {k: (_grey(v) if isinstance(v, str) else v) for k, v in t.items()}
    return t


def _grey(c: str) -> str:
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    y = max(0, min(255, round(0.2126 * r + 0.7152 * g + 0.0722 * b)))
    return f"#{y:02x}{y:02x}{y:02x}"


def _box(style: dict) -> tuple[int, int]:
    """Width and height (in drawing units: text is 12 of them) left for the plot inside the figure."""
    pw, ph, _mm = size_px(style)
    s = style["font_pt"] / 9
    h = ph / s - 1.6 * _PAD - (24 if style["title"] else 0) - (18 if style["subtitle"] else 0) \
        - (22 if style["legend"] else 0) - (20 if style["note"] else 0)
    return max(200, int(pw / s - 2 * _PAD)), max(120, int(h))


def style_text(style: dict) -> str:
    w, h, mm = size_px(style)
    k = 25.4 / 96 if mm else 1
    return (f"{SIZES[style['size']][0]} ({w * k:.1f} × {h * k:.1f} {'mm' if mm else 'px'}), text "
            f"{style['font_pt']:g} pt {style['font_family']}, palette {style['palette']}, {style['background']} background")


def cut_text(d: dict, comp: dict | None = None) -> str:
    """The saved cut-offs in plain words (report.js cutText)."""
    s = d.get("settings") or {}
    if comp is not None and comp.get("conf") == "none":
        return f"|log2FC| ≥ {s.get('log2fc') or 1:g} (fold change only: no replicates, no p-values)"
    return (f"|log2FC| ≥ {s.get('log2fc', 1.0):g} and {'adjusted p' if s.get('use_adjusted', True) else 'p'} "
            f"≤ {s.get('alpha', 0.05):g}")


def analysis_text(d: dict) -> str:
    s = d.get("settings") or {}
    parts = [_TESTS.get(s.get("test"), str(s.get("test") or "")),
             "" if d.get("kind") == "ratio" else f"normalisation: {s.get('normalize', '')}",
             f"imputation: {d['imputationLabel']}" if d.get("imputationLabel") else ""]
    return "; ".join(p for p in parts if p)


def _text(x: float, y: float, s, size: float = 12, fill: str = "", **attrs) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{_x(v)}"' for k, v in attrs.items() if v is not None)
    return f'<text x="{_n(x)}" y="{_n(y)}" font-size="{_n(size)}" fill="{fill}"{extra}>{_x(s)}</text>'


def _compose(style: dict, d: dict, plot, what: str, title: str, subtitle: str,
             legend: list[tuple[str | None, str]], cuts: str = "", warn: str = "", comparison: str = "",
             generator: str = "") -> str:
    """The finished figure around a plot: title, subtitle, legend, the plot, the cut-offs line, and a <desc>
    that says where it came from (report.js composeFigure). `plot` is (body, w, h) for a plot with a size of
    its own (it is made smaller to fit, never stretched), or a function (w, h) -> body for one that fills
    the room that is left."""
    ink = _inks(style)
    pw, ph, mm = size_px(style)
    s = style["font_pt"] / 9
    lw_, lh_ = pw / s, ph / s

    def tw(t: str, fs: float) -> float:  # nothing to measure with: an estimate of a text's width
        return len(t) * fs * 0.56

    def cut(t: str, fs: float) -> str:
        n = int((lw_ - 2 * _PAD) / (fs * 0.56))
        return t[: max(1, n - 1)] + "…" if len(t) > n else t

    exp = clean_text(d.get("title") or "Experiment")
    ttl = cut(clean_text(title), 16) if style["title"] else ""
    sub = cut(clean_text(subtitle), 12) if style["subtitle"] else ""
    note = cut(clean_text(exp + (f" · hits: {cuts}" if cuts else "") + " · " + analysis_text(d)), 10) if style["note"] else ""
    rows: list[list[tuple[float, str | None, str]]] = [[]]
    lx = 0.0
    for col, label in (legend if style["legend"] else []):
        label = clean_text(label)[:70]
        iw = (15 if col else 0) + tw(label, 12) + 16
        if lx and lx + iw > lw_ - 2 * _PAD:
            rows.append([])
            lx = 0.0
        rows[-1].append((lx, col, label))
        lx += iw
    nrows = len(rows) if rows[0] else 0
    top = _PAD + (24 if ttl else 0) + (18 if sub else 0) + (18 if warn else 0) + nrows * 18 + (4 if nrows else 0)
    bottom = (20 if note else 0) + _PAD * 0.6
    if callable(plot):
        w, h = int(lw_ - 2 * _PAD), max(120, int(lh_ - top - bottom))
        body = plot(w, h)
    else:
        body, w, h = plot
    fit = max(0.05, min(1.0, (lw_ - 2 * _PAD) / w, (lh_ - top - bottom) / h))
    widest = max([tw(ttl, 16), tw(sub, 12), tw(warn, 11.5), tw(note, 10)]
                 + [r[-1][0] + (15 if r[-1][1] else 0) + tw(r[-1][2], 12) for r in rows if r])
    ow, oh = min(lw_, max(w * fit, widest) + 2 * _PAD), top + h * fit + bottom
    if lw_ - ow < 3:  # a plot that fills the size gives exactly the size
        ow = lw_
    if abs(lh_ - oh) < 3:
        oh = lh_
    per = (25.4 / 96) * s if mm else s
    unit = "mm" if mm else ""
    desc = [f"Figure: {what}", f"Experiment: {exp}", f"Comparison: {clean_text(comparison)}" if comparison else "",
            f"Cut-offs: {cuts}" if cuts else "", warn, f"Analysis: {analysis_text(d)}",
            f"Source table: {clean_text(d.get('sourceName'))}" if d.get("sourceName") else "",
            f"Export style: {style_text(style)}", generator]
    b = [f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{_n(ow * per)}{unit}" '
         f'height="{_n(oh * per)}{unit}" viewBox="0 0 {_n(ow)} {_n(oh)}" font-family="{_x(font_stack(style["font_family"]))}">',
         f"<title>{_x(what + (': ' + comparison if comparison else ''))}</title>",
         "<desc>" + escape("\n".join(clean_text(x) for x in desc if x), quote=False) + "</desc>"]
    if style["background"] != "transparent":
        b.append(f'<rect x="0" y="0" width="{_n(ow)}" height="{_n(oh)}" fill="{ink["surface"]}"/>')
    y = _PAD
    if ttl:
        b.append(_text(_PAD, y + 16, ttl, 16, ink["text"], font_weight=600))
        y += 24
    if sub:
        b.append(_text(_PAD, y + 12, sub, 12, ink["text2"]))
        y += 18
    if warn:
        b.append(_text(_PAD, y + 12, warn, 11.5, ink["warn"], font_weight=600))
        y += 18
    if nrows:
        for row in rows:
            for x, col, label in row:
                if col:
                    b.append(f'<circle cx="{_n(_PAD + x + 5)}" cy="{_n(y + 8)}" r="5" fill="{col}"/>')
                b.append(_text(_PAD + x + (15 if col else 0), y + 12, label, 12, ink["text2"]))
            y += 18
    scale = f" scale({fit:.4f})" if fit < 0.999 else ""
    b.append(f'<g transform="translate({_n((ow - w * fit) / 2)} {_n(top)}){scale}">{body}</g>')
    if note:
        b.append(_text(_PAD, top + h * fit + 14, note, 10, ink["muted"]))
    b.append("</svg>\n")
    return "".join(b)


def _axes(ink: dict, X, Y, xt, yt, L, R, T, B, W, H, xl: str, yl: str, k: float) -> list[str]:
    """Grid, tick labels, the two axes and their titles (report.js axes); k is the line-width scale."""
    b = []
    for v in yt:
        b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(Y(v))}" y2="{_n(Y(v))}" stroke="{ink["grid"]}" stroke-width="{_n(k)}"/>')
        b.append(_text(L - 6, Y(v) + 4, _tick_label(v), 12, ink["muted"], text_anchor="end"))
    for v in xt:
        b.append(f'<line x1="{_n(X(v))}" x2="{_n(X(v))}" y1="{T}" y2="{_n(H - B)}" stroke="{ink["grid"]}" stroke-width="{_n(k)}"/>')
        b.append(_text(X(v), H - B + 16, _tick_label(v), 12, ink["muted"], text_anchor="middle"))
    b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(H - B)}" y2="{_n(H - B)}" stroke="{ink["axis"]}" stroke-width="{_n(k)}"/>')
    b.append(f'<line x1="{L}" x2="{L}" y1="{T}" y2="{_n(H - B)}" stroke="{ink["axis"]}" stroke-width="{_n(k)}"/>')
    if xl:
        b.append(_text((L + W - R) / 2, H - 6, xl, 13, ink["text2"], text_anchor="middle"))
    if yl:
        b.append(_text(14, (T + H - B) / 2, yl, 13, ink["text2"], text_anchor="middle",
                       transform=f"rotate(-90 14 {_n((T + H - B) / 2)})"))
    return b


def _inner_ticks(lo: float, hi: float, n: int) -> list[float]:
    return [v for v in nice_ticks(lo, hi, n) if lo - 1e-9 <= v <= hi + 1e-9]


def _labels(items: list[tuple[str, float, float]], bounds: tuple[float, float, float, float], fs: float,
            ink: dict) -> list[str]:
    """Names beside their points without overlap: eight spots are tried, the far ones with a leader line; a
    name that fits nowhere is left out (report.js placeLabels)."""
    out, boxes = [], []
    for name, px, py in items:
        s = clean_text(name)[:22]
        w = len(s) * fs * 0.575 + 4
        spots = [(px + 6, py - 6), (px - 6 - w, py - 6), (px + 6, py + fs + 2), (px - 6 - w, py + fs + 2),
                 (px + 12, py - 16), (px - 12 - w, py - 16), (px + 12, py + fs + 12), (px - 12 - w, py + fs + 12)]
        for ci, (bx, by) in enumerate(spots):
            box = (bx, by - fs + 0.5, bx + w, by + 2)
            if box[0] < bounds[0] or box[2] > bounds[1] or box[1] < bounds[2] or box[3] > bounds[3]:
                continue
            if any(not (box[2] < o[0] or box[0] > o[2] or box[3] < o[1] or box[1] > o[3]) for o in boxes):
                continue
            boxes.append(box)
            if ci >= 4:
                out.append(f'<line x1="{_n(px)}" y1="{_n(py)}" x2="{_n(bx + w if bx < px else bx)}" y2="{_n(by - fs / 2)}" '
                           f'stroke="{ink["muted"]}" stroke-width="0.8"/>')
            out.append(_text(bx, by, s, fs, ink["text"]))
            break
    return out


def _significant(d: dict, c: dict, i: int) -> str:
    """up / down / "" at the report's saved cut-offs (report.js rawSig)."""
    s = d.get("settings") or {}
    fc = c["fc"][i]
    if fc is None:
        return ""
    if c.get("conf") == "none":
        return "" if abs(fc) < (s.get("log2fc") or 1) else ("up" if fc > 0 else "down")
    score = (c["q"] if s.get("use_adjusted", True) else c["p"])[i]
    if score is None:
        return ""
    return ("up" if fc > 0 else "down") if score <= s.get("alpha", 0.05) and abs(fc) >= s.get("log2fc", 1.0) else ""


def _label_count(d: dict, style: dict) -> int:
    if style["labels"] in ("none", "marked"):  # nothing is pinned or searched outside the report
        return 0
    n = style["label_count"]
    return int((d.get("settings") or {}).get("top_labels", 15) if n is None else n)


def figure_volcano(d: dict, k: int, style: dict, generator: str = "") -> str:
    """Comparison k of the report as a volcano plot (fold change against abundance or rank when it has no
    replicates), at the saved cut-offs."""
    c = d["comps"][k]
    s = d.get("settings") or {}
    ink = _inks(style)
    L, R, T, B = 56, 18, 26, 44
    fco = c.get("conf") == "none"
    lfc = s.get("log2fc", 1.0)
    pts = []
    for i, fc in enumerate(c["fc"]):
        p = c["p"][i]
        if fc is None or (p is None and not fco):
            continue
        x = c["a"][i] if fco else fc
        if x is None:
            continue
        pts.append((i, x, fc if fco else -math.log10(max(p, 1e-300))))
    if fco:
        xs = [p[1] for p in pts] or [0.0, 1.0]
        ym = max([abs(p[2]) for p in pts] + [1.0]) * 1.08
        x0, x1, y0, y1 = min(xs) - 0.5, max(xs) + 0.5, -ym, ym
    else:
        xm = max([abs(p[1]) for p in pts] + [1.0]) * 1.08
        x0, x1, y0, y1 = -xm, xm, 0.0, max([p[2] for p in pts] + [1.0]) * 1.06

    sig = {i: _significant(d, c, i) for i, _x0, _y0 in pts}

    def draw(W: int, H: int) -> str:
        def X(v):
            return L + (v - x0) / (x1 - x0) * (W - L - R)

        def Y(v):
            return H - B - (v - y0) / (y1 - y0) * (H - T - B)

        ratio = d.get("kind") == "ratio"
        if fco:
            xl = ("rank (sorted by log2 fold change)" if c.get("t2") else "rank (sorted by log2 H/L)") if c.get("aRank") \
                else "mean log2 abundance"
            yl = "log2 fold change"
        else:
            xl = "log2 H/L" if ratio else "log2 fold change"
            yl = "−log10 p (line: adjusted p cut-off)" if s.get("use_adjusted", True) else "−log10 p"
        ls, ps = style["line_scale"], style["point_scale"]
        b = _axes(ink, X, Y, _inner_ticks(x0, x1, 8), _inner_ticks(y0, y1, 6), L, R, T, B, W, H, xl, yl, ls)
        dash = f'stroke="{ink["muted"]}" stroke-width="{_n(ls)}" stroke-dasharray="4 4"'
        if fco:
            for v in (-(lfc or 1), 0, lfc or 1):
                if y0 < v < y1:
                    b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(Y(v))}" y2="{_n(Y(v))}" stroke="{ink["muted"]}" '
                             f'stroke-width="{_n(ls)}"' + (' stroke-dasharray="4 4"' if v else "") + "/>")
        else:
            if lfc > 0:
                for v in (-lfc, lfc):
                    if x0 < v < x1:
                        b.append(f'<line x1="{_n(X(v))}" x2="{_n(X(v))}" y1="{T}" y2="{_n(H - B)}" {dash}/>')
            if s.get("use_adjusted", True):  # the p at which the adjusted p reaches alpha
                ok = [c["p"][i] for i, q in enumerate(c["q"]) if q is not None and q <= s.get("alpha", 0.05)
                      and c["p"][i] is not None]
                thr = max(ok) if ok else None
            else:
                thr = s.get("alpha", 0.05)
            if thr:
                yv = -math.log10(thr)
                if y0 < yv < y1:
                    b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(Y(yv))}" y2="{_n(Y(yv))}" {dash}/>')
        for i, x, y in sorted(pts, key=lambda p: 1 if sig[p[0]] else 0):  # hits on top
            if sig[i]:
                b.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{_n(3.4 * ps)}" fill="{ink[sig[i]]}" '
                         f'stroke="{ink["surface"]}" stroke-width="{_n(ls)}"/>')
            else:
                b.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{_n(2.6 * ps)}" fill="{ink["ns"]}" fill-opacity="0.7"/>')
        names = d["f"]["label"]
        ids = d["f"]["id"]
        hits = [p for p in pts if sig[p[0]]]
        hits.sort(key=(lambda p: -abs(c["fc"][p[0]])) if fco else (lambda p: c["p"][p[0]]))
        b += _labels([(names[i] or ids[i] or "", X(x), Y(y)) for i, x, y in hits[: _label_count(d, style)]],
                     (L, W - R, T, H - B), 11.5, ink)
        if not fco and c.get("t2") and not ratio:  # which side is which
            b.append(_text(W - R - 4, H - B - 8, f"{c['t1']} ↑", 12.5, ink["up"], text_anchor="end", font_weight=600))
            b.append(_text(L + 6, H - B - 8, "↑ " + ("others" if c["t2"] == "others" else c["t2"]), 12.5, ink["down"],
                           font_weight=600))
        if not pts:
            b.append(_text((L + W - R) / 2, (T + H - B) / 2, "No features could be tested in this comparison", 13,
                           ink["text2"], text_anchor="middle"))
        return "".join(b)

    up = sum(1 for v in sig.values() if v == "up")
    down = sum(1 for v in sig.values() if v == "down")
    legend = [(ink["up"], f"Up {up:,}"), (ink["down"], f"Down {down:,}"),
              (ink["ns"], "Below the cut-off" if fco else "Not significant")]
    warn = {"low": "LOW CONFIDENCE: a group has one sample; p-values borrowed",
            "none": "FOLD CHANGE ONLY: no replicates, no p-values"}.get(c.get("conf") or "", "")
    what = "Volcano plot"
    return _compose(style, d, draw, what, c["name"], f"{what} · {d.get('title') or ''}", legend,
                    cut_text(d, c), warn, c["name"], generator)


def _cond_colors(d: dict, ink: dict) -> dict[str, str]:
    return {c: ink["c"][i % len(ink["c"])] for i, c in enumerate(d.get("conditions") or [])}


def figure_pca(d: dict, style: dict, generator: str = "") -> str:
    """The samples on the first two principal components, coloured by condition. "" when there is no PCA."""
    P = (d.get("qc") or {}).get("pca") or {}
    scores = P.get("scores") or []
    if not scores or len(scores[0]) < 2:
        return ""
    ink = _inks(style)
    L, R, T, B = 56, 20, 16, 44
    xs, ys = [r[0] for r in scores], [r[1] for r in scores]

    def pad(a):
        lo, hi = min(a), max(a)
        p = (hi - lo) * 0.12 or 1
        return lo - p, hi + p

    (x0, x1), (y0, y1) = pad(xs), pad(ys)

    col = _cond_colors(d, ink)
    samples, cond = d.get("samples") or [], d.get("cond") or []
    named = style["labels"] != "none" and len(samples) <= 24
    pct = P.get("percent") or [0, 0]

    def draw(W: int, H: int) -> str:
        def X(v):
            return L + (v - x0) / (x1 - x0) * (W - L - R)

        def Y(v):
            return H - B - (v - y0) / (y1 - y0) * (H - T - B)

        b = _axes(ink, X, Y, _inner_ticks(x0, x1, 6), _inner_ticks(y0, y1, 5), L, R, T, B, W, H,
                  f"PC1 ({pct[0]:.1f}%)", f"PC2 ({pct[1]:.1f}%)", style["line_scale"])
        for j, r in enumerate(scores):
            b.append(f'<circle cx="{_n(X(r[0]))}" cy="{_n(Y(r[1]))}" r="{_n(7 * style["point_scale"])}" '
                     f'fill="{col.get(cond[j], ink["c"][0])}" stroke="{ink["surface"]}" stroke-width="{_n(1.5 * style["line_scale"])}"/>')
            if named:
                b.append(_text(X(r[0]) + 9, Y(r[1]) + 4, samples[j], 11, ink["text2"]))
        return "".join(b)

    what = "PCA of the samples"
    return _compose(style, d, draw, what, what,
                    f"top {P.get('n', 0):,} most variable complete features · {d.get('title') or ''}",
                    [(v, k) for k, v in col.items()], generator=generator)


def _blend(a: str, b: str, t: float) -> str:
    """Colour a towards b by t (0..1)."""
    pa, pb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb, strict=True))


def figure_heatmap(d: dict, style: dict, generator: str = "") -> str:
    """The report's heatmap of the significant features: each row centred on its mean, rows and columns in
    their clustered order. "" when the report has none."""
    hm = (d.get("qc") or {}).get("heatmap") or {}
    rows, cols, vals = hm.get("rows") or [], hm.get("cols") or [], hm.get("values") or []
    if not rows or not cols:
        return ""
    ink = _inks(style)
    W, Hmax = _box(style)
    top = 90
    cell_h = max(2.0, min(16.0, (Hmax - top - 8) / len(rows)))
    names = len(rows) <= 80 and cell_h >= 9
    lab = 110 if names else 8
    cell_w = max(6.0, min(64.0, (W - lab - 14) / len(cols)))
    w, h = round(lab + len(cols) * cell_w + 10), round(top + len(rows) * cell_h + 8)
    flat = sorted(abs(v) for r in vals for v in r if v is not None)
    lim = max(0.5, flat[int(len(flat) * 0.95)] if flat else 1.0)
    grey = style["palette"] == "grey"

    def shade(v: float) -> str:
        t = max(-1.0, min(1.0, v / lim))
        if grey:  # without colour, one ramp from light (low) to dark (high)
            g = round(245 - (t + 1) / 2 * 225)
            return f"#{g:02x}{g:02x}{g:02x}"
        return _blend(ink["surface"], ink["up"] if t >= 0 else ink["down"], abs(t))

    col = _cond_colors(d, ink)
    samples, cond, labels, ids = d.get("samples") or [], d.get("cond") or [], d["f"]["label"], d["f"]["id"]
    b = []
    for k, j in enumerate(cols):
        x = lab + k * cell_w
        b.append(_text(0, 0, samples[j][:16], 11, ink["text2"],
                       transform=f"translate({_n(x + cell_w / 2 + 3)},{top - 14}) rotate(-60)"))
        b.append(f'<rect x="{_n(x)}" y="{top - 10}" width="{_n(cell_w - 1)}" height="7" fill="{col.get(cond[j], ink["c"][0])}"/>')
    gw, gh = cell_w - (1 if cell_w > 10 else 0), cell_h - (1 if cell_h > 6 else 0)
    for r, i in enumerate(rows):
        y = top + r * cell_h
        for k, v in enumerate(vals[r]):
            b.append(f'<rect x="{_n(lab + k * cell_w)}" y="{_n(y)}" width="{_n(gw)}" height="{_n(gh)}" '
                     f'fill="{ink["sunk"] if v is None else shade(v)}"/>')
        if names:
            b.append(_text(lab - 6, y + cell_h - 2, (labels[i] or ids[i] or "")[:16], 11, ink["text2"], text_anchor="end"))
    word = d.get("levelWord") or "feature"
    legend = [(shade(-lim), f"below the {word}'s mean"), (shade(lim), "above"),
              (None, f"full colour at ±{lim:.1f} log2 · {len(rows)} of {hm.get('total_significant', len(rows)):,} "
                     "significant, clustered")] + [(v, k) for k, v in col.items()]
    what = "Heatmap of significant features"
    return _compose(style, d, ("".join(b), w, h), what, what, d.get("title") or "", legend,
                    cut_text(d) + " (the report's saved cut-offs)", generator=generator)


def figure_correlation(d: dict, style: dict, generator: str = "") -> str:
    """Pearson correlation between the samples, in the report's clustered order. "" when there is none."""
    cc = (d.get("qc") or {}).get("correlation") or {}
    mx, order = cc.get("matrix") or [], cc.get("order") or []
    if not mx or not order:
        return ""
    ink = _inks(style)
    W, H = _box(style)
    n, lab = len(order), 130
    cell = max(10, min(36, int((min(W, H) - lab - 20) / n)))  # square: the smaller side sets the cells
    w, h = lab + n * cell + 12, lab + n * cell + 10
    off = [v for a, row in enumerate(mx) for b_, v in enumerate(row) if v is not None and a != b_]
    lo, hi = (min(off), max(off)) if off else (0.9, 1.0)
    if hi <= lo:  # off-diagonal range, so r = 1 on the diagonal doesn't flatten the scale
        lo, hi = min(lo, 0.9), 1.0
    col = _cond_colors(d, ink)
    samples, cond = d.get("samples") or [], d.get("cond") or []
    b = []
    for r, a in enumerate(order):
        ca = col.get(cond[a], ink["text2"])
        b.append(_text(lab - 6, lab + r * cell + cell / 2 + 4, samples[a][:18], 11, ca, text_anchor="end"))
        b.append(_text(0, 0, samples[a][:18], 11, ca,
                       transform=f"translate({_n(lab + r * cell + cell / 2 + 4)},{lab - 6}) rotate(-60)"))
        for k, c2 in enumerate(order):
            v = mx[a][c2]
            fill = ink["sunk"] if v is None else _blend(ink["surface"], ink["c"][0], max(0.0, min(1.0, (v - lo) / (hi - lo))))
            b.append(f'<rect x="{lab + k * cell}" y="{lab + r * cell}" width="{cell - 1}" height="{cell - 1}" fill="{fill}"/>')
    what = "Sample correlation"
    legend = [(v, k) for k, v in col.items()] + [(None, f"light = r {lo:.3f} · dark = r {hi:.3f}")]
    return _compose(style, d, ("".join(b), w, h), what, what,
                    f"Pearson r over {cc.get('complete_rows', 0):,} complete features · {d.get('title') or ''}", legend,
                    generator=generator)


def figures(d: dict, style: dict, which=None, generator: str = "") -> list[tuple[str, str, str]]:
    """Every static figure asked for that the report's data can draw: [(file name, what it is, the SVG)]."""
    which = list(STATIC_FIGURES) if which is None else list(which)
    out: list[tuple[str, str, str]] = []
    if "volcano" in which:
        for k, c in enumerate(d.get("comps") or []):
            out.append((safe_name(f"volcano_{c.get('slug') or k + 1}.svg"),
                        f"Volcano plot, {clean_text(c['name'])}. Cut-offs: {cut_text(d, c)}", figure_volcano(d, k, style, generator)))
    for name, what, fn in (("pca", "PCA of the samples", figure_pca),
                           ("heatmap", f"Heatmap of significant features. Cut-offs: {cut_text(d)}", figure_heatmap),
                           ("correlation", "Sample correlation", figure_correlation)):
        if name in which:
            svg = fn(d, style, generator)
            if svg:
                out.append((f"{name}.svg", what, svg))
    return out
