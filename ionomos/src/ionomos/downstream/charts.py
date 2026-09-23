"""
Charts as hand-written SVG: no plotting library, works offline, light and dark.

    volcano(diff)            the differential result
    id_bars(m)               features quantified per sample (QC)
    box_plots(m)             log2 value distribution per sample (QC)
    correlation_heatmap(m)   sample-sample Pearson r (QC)

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
from html import escape

from ionomos.downstream import stats
from ionomos.downstream.analysis import DiffResult
from ionomos.downstream.quant import QuantMatrix

CATEGORICAL = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
               ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#4a3aa7", "#9085e9"), ("#e34948", "#e66767")]
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
       "#1c5cab", "#184f95", "#104281", "#0d366b"]

_TOKENS_LIGHT = """--vz-surface:#fcfcfb;--vz-text:#0b0b0b;--vz-text2:#52514e;--vz-muted:#898781;
--vz-grid:#e1e0d9;--vz-axis:#c3c2b7;--vz-up:#e34948;--vz-down:#2a78d6;--vz-ns:#c3c2b7;"""
_TOKENS_DARK = """--vz-surface:#1a1a19;--vz-text:#ffffff;--vz-text2:#c3c2b7;--vz-muted:#898781;
--vz-grid:#2c2c2a;--vz-axis:#383835;--vz-up:#e66767;--vz-down:#3987e5;--vz-ns:#52514e;"""


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


def volcano(d: DiffResult, standalone: bool = False, width: int = 760, height: int = 520) -> str:
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

    lg = [("up", "Up", d.up), ("down", "Down", d.down), ("ns", "Not significant", d.tested - d.up - d.down)]
    x = ml
    for cls, name, n in lg:
        b.append(f'<circle class="{cls}" cx="{x + 5}" cy="{mt - 16}" r="5"/>')
        label = f"{name} {n:,}"
        b.append(f'<text class="lgd" x="{x + 14}" y="{mt - 12}">{label}</text>')
        x += 30 + 7 * len(label)
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
