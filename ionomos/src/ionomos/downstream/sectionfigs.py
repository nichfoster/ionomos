"""
Figures for slides from the report's dose-response, time-course, liganded-site (D68) and kinase-activity
(D79) sections.

    catalog(payload, which, features, top)   the figures these sections can draw: [charts.Figure]

They are drawn the way charts.py draws the volcano and the PCA (charts._compose: title, subtitle, legend,
the plot, the cut-offs line, a <desc> that says where the figure came from), from the data the report carries:
    dose_potency          per compound: pEC50 against the log2 curve fold change, every fitted curve
    dose_curves           per compound: a grid of curves (measured points, the 4PL fit, the 95% interval of
                          pEC50): the `top` most relevant regulated curves, or the features asked for
    time_patterns         per series: the median profile of each pattern of changing features
    time_profiles         per series: a grid of features over time (every replicate, the mean, the series it
                          was compared with dashed): the `top` most significant changing ones, or those asked for
    liganded_rank         per compound: every measured site ranked by its competition ratio, against the threshold
    liganded_selectivity  sites x compounds: the median competition ratio of each site liganded by any compound
                          (or of the sites asked for), marked where it is liganded
    kinase_activity       per comparison (phosphosites with a kinase-substrate table, D79): the KSEA z-score of every
                          kinase with enough substrates, coloured where it is significant

Nothing is analysed again: the numbers are the report's. Colours are written out, text is <text>, no CSS.
"""
from __future__ import annotations

import fnmatch
import math
import re

from ionomos.downstream import charts
from ionomos.downstream.charts import _axes, _compose, _inks, _inner_ticks, _labels, _n, _text, clean_text, safe_name
from ionomos.downstream.doseresponse import fmt_dose

TOP_PANELS = 6     # curves / profiles drawn when no feature is named (report.js TOP_PANELS)
MAX_PANELS = 24
SECTION_FIGURES = ("dose_potency", "dose_curves", "time_patterns", "time_profiles", "liganded_rank",
                   "liganded_selectivity", "kinase_activity")
_DOSE_CLS = ("up", "down", "not", "unclear")
_TIME_CLS = ("up", "down", "mixed", "not")


def _name(d: dict, i: int) -> str:
    f = d.get("f") or {}
    lab, ids = f.get("label") or [], f.get("id") or []
    return clean_text((lab[i] if i < len(lab) else "") or (ids[i] if i < len(ids) else "") or "")


def match_features(d: dict, wanted) -> tuple[list[int], list[str]]:
    """The features named (a gene, a protein accession, a site, or a pattern with * and ?), in the order
    asked, and the names that matched nothing. Matching ignores case; a label or id is also matched by its
    parts (GAPDH in "GAPDH;GAPDHS", P04406 in "sp|P04406|G3P_HUMAN")."""
    f = d.get("f") or {}
    labels, ids = f.get("label") or [], f.get("id") or []
    keys = []
    for i in range(max(len(labels), len(ids))):
        ks = set()
        for s, split in ((labels[i] if i < len(labels) else "", r"[;,\s]+"), (ids[i] if i < len(ids) else "", r"[|;,\s]+")):
            s = (s or "").strip()
            if s:
                ks.add(s.upper())
                ks.update(t.upper() for t in re.split(split, s) if t and t.lower() not in ("sp", "tr"))
        keys.append(ks)
    out, missing = [], []
    for w in wanted or []:
        w = str(w).strip()
        if not w:
            continue
        pat = w.upper()
        wild = any(c in pat for c in "*?[")
        hit = [i for i, ks in enumerate(keys) if (any(fnmatch.fnmatchcase(k, pat) for k in ks) if wild else pat in ks)]
        if not hit:
            missing.append(w)
        out += [i for i in hit if i not in out]
    return out, missing


def _grid(n: int, W: float, H: float, aspect: float = 1.3) -> tuple[int, int, float, float]:
    """Columns, rows and the size of each panel: the layout that gives the largest panels of about this shape."""
    best = (0.0, 1, n)
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        size = min(W / cols / aspect, H / rows)
        if size > best[0] + 1e-9:
            best = (size, cols, rows)
    _, cols, rows = best
    return cols, rows, W / cols, H / rows


MIN_PANEL = (135, 115)  # drawing units (axis text is 12): a panel smaller than this cannot be read


def _room(style: dict, n: int, plot_top: float = 38, plot_bottom: float = 42) -> tuple[int, bool]:
    """How many of n panels fit the figure's size at a readable size (at least one), and whether a y-axis title
    fits beside a panel's plot (else it goes into the legend)."""
    W, H = charts._box(style)
    H -= 22  # a second legend row
    k = n
    while k > 1 and not all(a >= b for a, b in zip(_grid(k, W, H)[2:], MIN_PANEL, strict=True)):
        k -= 1
    ph = _grid(k, W, H)[3]
    return k, ph - plot_top - plot_bottom >= 120  # "ratio to control" at 13 is about 115 units long


def _room_notes(asked: int, shown: int, ytitle: bool, yl: str, top: bool = True) -> list[tuple[None, str]]:
    """What the legend says when panels were left out or the y-axis title had no room."""
    out = [] if ytitle else [(None, f"y: {yl}")]
    if shown < asked:
        out.append((None, f"{shown} of {asked}: the rest do not fit this size" + (f" (a larger size, or --top {shown})" if top else " (a larger size shows them)")))
    return out


def _panels(n: int, W: int, H: int, one) -> str:
    """n panels in a grid filling W x H; one(p, w, h, first_col, last_row) draws panel p at the origin."""
    cols, rows, pw, ph = _grid(n, W, H)
    b = []
    for p in range(n):
        r, c = divmod(p, cols)
        last = r == rows - 1 or p + cols >= n
        b.append(f'<g transform="translate({_n(c * pw)} {_n(r * ph)})">{one(p, pw, ph, c == 0, last)}</g>')
    return "".join(b)


def _fit(s: str, width: float, fs: float) -> str:
    n = max(1, int(width / (fs * 0.56)))
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


def _heading(name: str, word: str, col: str, line2: str, w: float, ink: dict) -> list[str]:
    """A panel's two title lines: the feature (and its class, in its colour), then its numbers."""
    name = _fit(name, w - 12 - (len(word) * 11.5 * 0.56 + 10 if word else 0), 12.5)
    b = [_text(6, 14, name, 12.5, ink["text"], font_weight=600)]
    if word:
        b.append(_text(6 + len(name) * 12.5 * 0.56 + 10, 14, word, 11.5, col, font_weight=600))
    if line2:
        b.append(_text(6, 29, _fit(line2, w - 12, 11), 11, ink["text2"]))
    return b


def _f(v, d: int = 2) -> str:
    return "–" if v is None else f"{v:.{d}f}"


def _p(v) -> str:
    if v is None:
        return "–"
    return f"{v:.1e}" if v < 1e-3 else f"{v:.3f}"


# ------------------------------------------------------------- dose-response --


def _dose_ink(ink: dict, cls: str) -> str:
    return ink["up"] if cls == "up" else ink["down"] if cls == "down" else ink["ns"] if cls == "not" else ink["muted"]


def _dose_cuts(X: dict) -> str:
    return f"regulated: relevance at alpha {X.get('alpha', 0.05):g} and |log2 curve fold change| ≥ {X.get('fcLim', 0.45):g}"


def figure_dose_potency(d: dict, si: int, style: dict, generator: str = "") -> str:
    """Every fitted curve of compound si: pEC50 (x) against the log2 curve fold change (y), coloured by class."""
    X = d.get("dose") or {}
    S = X["series"][si]
    ks = [k for k in range(len(S["i"])) if S["pec50"][k] is not None and S["fc"][k] is not None]
    if not ks:
        return ""
    ink = _inks(style)
    fcl = X.get("fcLim", 0.45)
    xs, ys = [S["pec50"][k] for k in ks], [S["fc"][k] for k in ks] + [fcl, -fcl]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    px, py = (x1 - x0) * 0.05 or 0.5, (y1 - y0) * 0.06 or 0.5
    x0, x1, y0, y1 = x0 - px, x1 + px, y0 - py, y1 + py
    L, R, T, B = 56, 18, 16, 44
    ls, ps = style["line_scale"], style["point_scale"]
    reg = [k for k in ks if S["cls"][k] in ("up", "down")]  # payload order: most relevant first

    def draw(W: int, H: int) -> str:
        def Xf(v):
            return L + (v - x0) / (x1 - x0) * (W - L - R)

        def Yf(v):
            return H - B - (v - y0) / (y1 - y0) * (H - T - B)

        b = _axes(ink, Xf, Yf, _inner_ticks(x0, x1, 7), _inner_ticks(y0, y1, 6), L, R, T, B, W, H,
                  "pEC50 (−log10 M; higher = more potent)", "log2 curve fold change", ls)
        for v in (-fcl, fcl):
            b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(Yf(v))}" y2="{_n(Yf(v))}" stroke="{ink["muted"]}" '
                     f'stroke-width="{_n(ls)}" stroke-dasharray="4 4"/>')
        for k in sorted(ks, key=lambda k: 1 if S["cls"][k] in ("up", "down") else 0):
            on = S["cls"][k] in ("up", "down")
            b.append(f'<circle cx="{Xf(S["pec50"][k]):.1f}" cy="{Yf(S["fc"][k]):.1f}" r="{_n((3.6 if on else 2.6) * ps)}" '
                     f'fill="{_dose_ink(ink, S["cls"][k])}" fill-opacity="{0.85 if on else 0.45}"/>')
        b += _labels([(_name(d, S["i"][k]), Xf(S["pec50"][k]), Yf(S["fc"][k])) for k in reg[: charts._label_count(d, style)]],
                     (L, W - R, T, H - B), 11.5, ink)
        return "".join(b)

    legend = [(_dose_ink(ink, c), f"{c} {sum(1 for k in ks if S['cls'][k] == c):,}") for c in _DOSE_CLS
              if any(S["cls"][k] == c for k in ks)]
    what = "Dose-response: potency against effect"
    name = clean_text(S.get("name")) or "Dose-response"
    return _compose(style, d, draw, what, name, f"{what} · {d.get('title') or ''}", legend, _dose_cuts(X),
                    about=name, generator=generator)


def _dose_panel(d: dict, S: dict, k: int, W: float, H: float, ink: dict, style: dict, xl: str, yl: str) -> str:
    L, R, T, B = 52, 10, 38, 42
    ls, ps = style["line_scale"], style["point_scale"]
    lx = [math.log10(v) for v in S["doses"] if v and v > 0]
    xa, xb = min(lx) - 0.5, max(lx) + 0.5
    gap = max(0.6, (xb - xa) * 0.12)
    xc, x0 = xa - gap, xa - gap * 1.4  # the control's place, left of a break in the axis
    pts = []
    for jj, v in enumerate(S["y"][k]):
        if v is None or jj >= len(S["sdose"]):
            continue
        dose = S["sdose"][jj]
        pts.append((math.log10(dose) if dose > 0 else xc, 2.0 ** v, dose == 0))
    front, back, slope, pec = S["front"][k], S["back"][k], S["slope"][k], S["pec50"][k]
    fitted = None not in (front, back, slope, pec)
    ymax = max([1.4] + ([front, back] if fitted else []) + [p[1] for p in pts]) * 1.08

    def X(v):
        return L + (v - x0) / (xb - x0) * (W - L - R)

    def Y(v):
        return T + (1 - v / ymax) * (H - T - B)

    b = _axes(ink, X, Y, [], _inner_ticks(0, ymax, 5), L, R, T, B, W, H, xl, yl, ls)
    xt = list(range(math.ceil(xa), math.floor(xb) + 1))
    b.append(_text(X(xc), H - B + 16, "ctrl", 10.5, ink["muted"], text_anchor="middle"))
    edge = X(xc) + 4 * 10.5 * 0.56 / 2  # labels left to right, each only where it clears the one before
    for v in xt:
        b.append(f'<line x1="{_n(X(v))}" x2="{_n(X(v))}" y1="{T}" y2="{_n(H - B)}" stroke="{ink["grid"]}" stroke-width="{_n(ls)}"/>')
        label = fmt_dose(10.0 ** v)
        half = len(label) * 10.5 * 0.56 / 2
        if X(v) - half >= edge + 6 and X(v) + half <= W - 2:
            b.append(_text(X(v), H - B + 16, label, 10.5, ink["muted"], text_anchor="middle"))
            edge = X(v) + half
    bx = X(xc + gap / 2)
    b.append(f'<path d="M{_n(bx - 4)} {_n(H - B + 4)}l4 -8m0 8l4 -8" stroke="{ink["axis"]}" stroke-width="{_n(ls)}" fill="none"/>')
    b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(Y(1))}" y2="{_n(Y(1))}" stroke="{ink["muted"]}" '
             f'stroke-width="{_n(ls)}" stroke-dasharray="3 3"/>')
    col = _dose_ink(ink, S["cls"][k])
    if fitted:
        lo, hi = S["ciL"][k], S["ciR"][k]
        if lo is not None and hi is not None:  # pEC50 is -log10 dose: its interval runs the other way on the axis
            a, z = max(xa, -hi), min(xb, -lo)
            if z > a:
                b.append(f'<rect x="{_n(X(a))}" y="{T}" width="{_n(X(z) - X(a))}" height="{_n(H - T - B)}" fill="{col}" '
                         f'fill-opacity="0.13"/>')
        if xa <= -pec <= xb:
            b.append(f'<line x1="{_n(X(-pec))}" x2="{_n(X(-pec))}" y1="{T}" y2="{_n(H - B)}" stroke="{ink["muted"]}" '
                     f'stroke-width="{_n(ls)}" stroke-dasharray="2 4"/>')
        path = []
        for n in range(121):
            x = xa + (xb - xa) * n / 120
            y = (front - back) / (1 + 10 ** min(300.0, slope * (x + pec))) + back
            path.append(f"{'L' if n else 'M'}{X(x):.1f} {Y(max(0.0, min(ymax, y))):.1f}")
        b.append(f'<path d="{"".join(path)}" fill="none" stroke="{col}" stroke-width="{_n(2 * ls)}"/>')
        b.append(f'<line x1="{_n(X(xc) - 10)}" x2="{_n(X(xc) + 10)}" y1="{_n(Y(front))}" y2="{_n(Y(front))}" stroke="{col}" '
                 f'stroke-width="{_n(2 * ls)}" stroke-opacity="0.6"/>')
    for x, y, ctrl in pts:
        b.append(f'<circle cx="{X(x):.1f}" cy="{Y(min(y, ymax)):.1f}" r="{_n(3.4 * ps)}" fill="{"none" if ctrl else ink["text2"]}" '
                 f'stroke="{ink["text2"]}" stroke-width="{_n(1.2 * ls)}"/>')
    reg = S["cls"][k] in ("up", "down")
    if not fitted:
        line2 = "no fit"
    elif reg:
        ci = f" ({_f(S['ciL'][k])}–{_f(S['ciR'][k])})" if S["ciL"][k] is not None else ""
        ec = f" · EC50 {S['ec50'][k]:.3g} {S.get('unit') or ''}".rstrip() if S["ec50"][k] is not None else ""
        line2 = f"pEC50 {_f(pec)}{ci}{ec}"
    else:
        line2 = "not a regulated curve: its pEC50 is not read"
    return "".join(_heading(_name(d, S["i"][k]), S["cls"][k], col, line2, W, ink) + b)


def figure_dose_curves(d: dict, si: int, ks: list[int], style: dict, generator: str = "", chosen: bool = False) -> str:
    """Curves ks of compound si in a grid: measured ratios to the control, the fit, the 95% interval of pEC50."""
    X = d.get("dose") or {}
    S = X["series"][si]
    if not ks or not [v for v in S["doses"] if v and v > 0]:
        return ""
    ink = _inks(style)
    asked = len(ks)
    fits, ytitle = _room(style, asked)
    ks = ks[:fits]

    def draw(W: int, H: int) -> str:
        return _panels(len(ks), W, H, lambda p, w, h, first, last: _dose_panel(
            d, S, ks[p], w, h, ink, style, "dose" if last else "", "ratio to control" if first and ytitle else ""))

    legend = [(ink["up"], "up"), (ink["down"], "down"), (ink["ns"], "not"), (ink["muted"], "unclear"),
              (None, "points: replicates (open: control)"), (None, "line: 4-parameter fit"),
              (None, "band: 95% interval of pEC50")]
    legend = [x for x in legend if x[0] is None or any(S["cls"][k] == x[1] for k in ks)]
    legend += _room_notes(asked, len(ks), ytitle, "ratio to control")
    what = "Dose-response curves"
    name = clean_text(S.get("name")) or "Dose-response"
    which = "chosen" if chosen else f"the {len(ks)} most relevant regulated"
    return _compose(style, d, draw, what, f"{name}: {len(ks)} curve{'s' if len(ks) != 1 else ''}",
                    f"{what} ({which}) · {d.get('title') or ''}", legend, _dose_cuts(X), about=name, generator=generator)


# --------------------------------------------------------------- time course --


def _time_ink(ink: dict, cls: str) -> str:
    return ink["up"] if cls == "up" else ink["down"] if cls == "down" else ink["c"][3] if cls == "mixed" else ink["text2"]


def _time_cuts(X: dict, S: dict) -> str:
    first = (S.get("labels") or ["the first time point"])[0]
    return f"changing: F adjusted p ≤ {X.get('alpha', 0.05):g} and |log2FC| ≥ {X.get('lfc', 1):g} against {first}"


def _time_axis(b: list, ink: dict, S: dict, Xs, H: float, B: float, W: float, L: float, R: float) -> None:
    labels = [clean_text(x) for x in S.get("labels") or []]
    room = (W - L - R) / max(1, len(labels))
    step = max(1, math.ceil(max((len(x) for x in labels), default=1) * 10.5 * 0.56 / max(room, 1)))
    for a, lab in enumerate(labels):
        if a % step == 0:
            b.append(_text(Xs(a), H - B + 16, lab, 10.5, ink["muted"], text_anchor="middle"))


def figure_time_patterns(d: dict, si: int, style: dict, generator: str = "") -> str:
    """Each pattern of changing features of series si: its median log2 fold change at each time point."""
    X = d.get("time") or {}
    S = X["series"][si]
    pats = S.get("patterns") or []
    if not pats:
        return ""
    ink = _inks(style)
    ls, ps = style["line_scale"], style["point_scale"]
    lim = max([abs(v) for p in pats for v in p["profile"] if v is not None] + [0.5]) * 1.1
    first = (S.get("labels") or [""])[0]
    asked = len(pats)
    fits, ytitle = _room(style, asked, 24, 40)
    pats = pats[:fits]

    def one(p, W, H, first_col, last):
        L, R, T, B = 52, 12, 24, 40
        prof = pats[p]["profile"]
        n = len(prof)

        def Xs(a):
            return L + 12 + a / max(1, n - 1) * (W - L - R - 24)

        def Y(v):
            return T + (1 - (v + lim) / (2 * lim)) * (H - T - B)

        b = _axes(ink, Xs, Y, [], _inner_ticks(-lim, lim, 5), L, R, T, B, W, H, "time" if last else "",
                  f"log2 FC vs {first}" if first_col and ytitle else "", ls)
        _time_axis(b, ink, S, Xs, H, B, W, L, R)
        b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(Y(0))}" y2="{_n(Y(0))}" stroke="{ink["axis"]}" stroke-width="{_n(ls)}"/>')
        big = max((v for v in prof if v is not None), key=abs, default=0)
        col = ink["up"] if big >= 0 else ink["down"]
        path = "".join(f"{'L' if a else 'M'}{Xs(a):.1f} {Y(v):.1f}" for a, v in enumerate(prof) if v is not None)
        if path:
            b.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="{_n(2 * ls)}"/>')
        for a, v in enumerate(prof):
            if v is not None:
                b.append(f'<circle cx="{Xs(a):.1f}" cy="{Y(v):.1f}" r="{_n(3.2 * ps)}" fill="{col}"/>')
        nf = pats[p]["n"]
        b.append(_text(6, 15, f"Pattern {p + 1} · {nf:,} feature{'s' if nf != 1 else ''}", 12.5, ink["text"], font_weight=600))
        return "".join(b)

    def draw(W: int, H: int) -> str:
        return _panels(len(pats), W, H, one)

    what = "Time-course patterns"
    name = clean_text(S.get("name")) or "Time course"
    total = sum(p["n"] for p in S["patterns"])
    legend = [(ink["up"], "rises"), (ink["down"], "falls"),
              (None, f"median of each pattern's features · {total:,} changing features in {asked} patterns")]
    legend += _room_notes(asked, len(pats), ytitle, f"log2 FC vs {first}", top=False)
    return _compose(style, d, draw, what, name, f"{what} · {d.get('title') or ''}", legend, _time_cuts(X, S),
                    about=name, generator=generator)


def _time_panel(d: dict, X: dict, S: dict, k: int, W: float, H: float, ink: dict, style: dict, xl: str, yl: str) -> str:
    L, R, T, B = 52, 12, 38, 40
    ls, ps = style["line_scale"], style["point_scale"]
    i = S["i"][k]
    row = (d.get("v") or [])[i] if i < len(d.get("v") or []) else []
    imp = d.get("imp")
    times = S["times"]

    def points(Q):
        out = []
        for a, j in enumerate(Q["samples"]):
            t = Q["stime"][a] if a < len(Q["stime"]) else None
            if t in Q["times"] and j < len(row) and row[j] is not None:
                out.append((Q["times"].index(t), row[j], j))
        return out

    def means(pts, n):
        out = []
        for a in range(n):
            v = [p[1] for p in pts if p[0] == a]
            out.append(sum(v) / len(v) if v else None)
        return out

    pts = points(S)
    other = next((q for q in X.get("series") or [] if S.get("vs") and q.get("name") == S["vs"]), None)
    opts = []
    if other:  # the series it was compared with, on this series' time axis (the shared time points)
        opts = [(times.index(other["times"][a]), v, j) for a, v, j in points(other) if other["times"][a] in times]
    ys = [p[1] for p in pts + opts]
    cls = S["cls"][k]
    col = _time_ink(ink, cls)
    line2 = f"adj. p {_p(S['q'][k])} · largest {_f(S['max'][k])} at {clean_text(S['labels'][S['peak'][k]])}" \
        if S["peak"][k] is not None and S["peak"][k] < len(S["labels"]) else f"adj. p {_p(S['q'][k])}"
    head = _heading(_name(d, i), cls, col, line2, W, ink)
    if not ys:
        return "".join(head + [_text(W / 2, H / 2, "no measured values", 11, ink["text2"], text_anchor="middle")])
    y0, y1 = min(ys), max(ys)
    py = (y1 - y0) * 0.1 or 0.5
    y0, y1 = y0 - py, y1 + py
    n = len(times)

    def Xs(a):
        return L + 14 + a / max(1, n - 1) * (W - L - R - 28)

    def Y(v):
        return T + (1 - (v - y0) / (y1 - y0)) * (H - T - B)

    b = _axes(ink, Xs, Y, [], _inner_ticks(y0, y1, 5), L, R, T, B, W, H, xl, yl, ls)
    _time_axis(b, ink, S, Xs, H, B, W, L, R)

    def line(m, c, dash):
        d_, pen = "", False
        for a, v in enumerate(m):
            if v is None:
                pen = False
                continue
            d_ += f"{'L' if pen else 'M'}{Xs(a):.1f} {Y(v):.1f}"
            pen = True
        if d_:
            b.append(f'<path d="{d_}" fill="none" stroke="{c}" stroke-width="{_n(2 * ls)}"' +
                     (f' stroke-dasharray="{dash}"' if dash else "") + "/>")

    shift = 5 if other else 0
    if other:
        line(means(opts, n), ink["muted"], "5 4")
        for a, v, _j in opts:
            b.append(f'<circle cx="{Xs(a) + shift:.1f}" cy="{Y(v):.1f}" r="{_n(2.8 * ps)}" fill="none" stroke="{ink["muted"]}" '
                     f'stroke-width="{_n(1.2 * ls)}"/>')
    line(means(pts, n), col, "")
    for a, v, j in pts:
        faint = bool(imp) and i < len(imp) and j < len(imp[i]) and imp[i][j] == "1"
        b.append(f'<circle cx="{Xs(a) - shift:.1f}" cy="{Y(v):.1f}" r="{_n(3.2 * ps)}" fill="{col}" '
                 f'fill-opacity="{0.3 if faint else 0.85}"/>')
    return "".join(head + b)


def figure_time_profiles(d: dict, si: int, ks: list[int], style: dict, generator: str = "", chosen: bool = False) -> str:
    """Features ks of series si over time, in a grid: every replicate, the mean, the series it was compared with."""
    X = d.get("time") or {}
    S = X["series"][si]
    if not ks or not d.get("v"):
        return ""
    ink = _inks(style)
    yl = "log2 ratio" if d.get("kind") == "ratio" else "log2 intensity"
    asked = len(ks)
    fits, ytitle = _room(style, asked, 38, 40)
    ks = ks[:fits]

    def draw(W: int, H: int) -> str:
        return _panels(len(ks), W, H, lambda p, w, h, first, last: _time_panel(
            d, X, S, ks[p], w, h, ink, style, "time" if last else "", yl if first and ytitle else ""))

    name = clean_text(S.get("name")) or "Time course"
    legend = [(c, w) for c, w in ((ink["up"], "up"), (ink["down"], "down"), (ink["c"][3], "mixed"), (ink["text2"], "not"))
              if any(_time_ink(ink, S["cls"][k]) == c and S["cls"][k] == w for k in ks)]
    legend += [(None, "points: replicates (faint: imputed)"), (None, "line: mean per time point")]
    if S.get("vs"):
        legend.append((ink["muted"], f"dashed: {clean_text(S['vs'])}"))
    legend += _room_notes(asked, len(ks), ytitle, yl)
    what = "Time course"
    which = "chosen" if chosen else f"the {len(ks)} most significant changing"
    return _compose(style, d, draw, what, f"{name}: {len(ks)} feature{'s' if len(ks) != 1 else ''} over time",
                    f"{what} ({which}) · {d.get('title') or ''}", legend, _time_cuts(X, S), about=name, generator=generator)


# ------------------------------------------------------------ liganded sites --


def _cys_ink(ink: dict, c: int) -> str:
    return ink["up"] if c == 0 else ink["c"][3] if c == 1 else ink["ns"] if c == 2 else ink["c"][6]


def figure_liganded_rank(d: dict, ci: int, style: dict, generator: str = "", named: list[int] | None = None) -> str:
    """Every measured site of compound ci ranked by its competition ratio, against the liganded threshold."""
    X = d.get("cys") or {}
    C = X["compounds"][ci]
    ks = sorted((k for k in range(len(X["i"])) if C["r"][k] is not None), key=lambda k: -C["r"][k])
    if not ks:
        return ""
    ink = _inks(style)
    thr = math.log2(X.get("ratio") or 4)
    y0, y1 = min(C["r"][ks[-1]], -thr), max(C["r"][ks[0]], thr)
    py = (y1 - y0) * 0.06 or 0.5
    y0, y1 = y0 - py, y1 + py
    n = len(ks)
    L, R, T, B = 56, 18, 16, 44
    ls, ps = style["line_scale"], style["point_scale"]
    classes = X.get("classes") or ["liganded", "inconsistent", "not liganded", "too few"]
    if named is not None:
        pos = {X["i"][k]: k for k in ks}
        show = [pos[i] for i in named if i in pos]
    else:
        show = [k for k in ks if C["cls"][k] == 0][: charts._label_count(d, style)]
    rank = {k: r for r, k in enumerate(ks)}

    def draw(W: int, H: int) -> str:
        def Xs(v):
            return L + (v / (n - 1) if n > 1 else 0.5) * (W - L - R)

        def Y(v):
            return T + (1 - (v - y0) / (y1 - y0)) * (H - T - B)

        xt = [v for v in charts.nice_ticks(1, n, 6) if 1 <= v <= n]
        b = _axes(ink, lambda v: Xs(v - 1), Y, xt, _inner_ticks(y0, y1, 6), L, R, T, B, W, H,
                  "sites, ranked by competition ratio",
                  f"log2 R ({'heavy / light' if X.get('dir', 'high') == 'high' else 'light / heavy'})", ls)
        b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(Y(thr))}" y2="{_n(Y(thr))}" stroke="{ink["muted"]}" '
                 f'stroke-width="{_n(ls)}" stroke-dasharray="4 4"/>')
        b.append(_text(W - R - 4, Y(thr) - 5, f"R = {X.get('ratio', 4):g}", 12, ink["muted"], text_anchor="end"))
        b.append(f'<line x1="{L}" x2="{_n(W - R)}" y1="{_n(Y(0))}" y2="{_n(Y(0))}" stroke="{ink["axis"]}" stroke-width="{_n(ls)}"/>')
        for k in sorted(ks, key=lambda k: 1 if C["cls"][k] == 0 else 0):
            lig = C["cls"][k] == 0
            b.append(f'<circle cx="{Xs(rank[k]):.1f}" cy="{Y(C["r"][k]):.1f}" r="{_n((3.2 if lig else 2.2) * ps)}" '
                     f'fill="{_cys_ink(ink, C["cls"][k])}" fill-opacity="{0.9 if lig else 0.55}"/>')
        b += _labels([(_name(d, X["i"][k]), Xs(rank[k]), Y(C["r"][k])) for k in show], (L, W - R, T, H - B), 11.5, ink)
        return "".join(b)

    legend = [(_cys_ink(ink, c), f"{classes[c]} {sum(1 for k in ks if C['cls'][k] == c):,}")
              for c in range(len(classes)) if any(C["cls"][k] == c for k in ks)]
    what = "Liganded sites"
    name = clean_text(C.get("name")) or "Compound"
    return _compose(style, d, draw, what, name, f"{what}, ranked by competition ratio · {d.get('title') or ''}", legend,
                    f"liganded: {clean_text(X.get('rule'))}", about=name, generator=generator)


def _selectivity_rows(X: dict) -> list[int]:
    """Sites liganded by any compound: selective ones first (grouped by their compound), then shared, then
    unresolved; within a group the strongest ratio first."""
    comps = X["compounds"]

    def best(k):
        return max((c["r"][k] for c in comps if c["r"][k] is not None), default=-math.inf)

    def owner(k):
        return next((j for j, c in enumerate(comps) if c["cls"][k] == 0), len(comps))

    ks = [k for k in range(len(X["i"])) if X["nlig"][k]]
    order = {1: 0, 2: 1, 3: 2, 0: 3}  # selective, shared, unresolved, (none)
    return sorted(ks, key=lambda k: (order.get(X["sel"][k], 3), owner(k) if X["sel"][k] == 1 else 0, -best(k)))


def figure_liganded_selectivity(d: dict, style: dict, generator: str = "", named: list[int] | None = None) -> str:
    """Sites x compounds: the median competition ratio of each site liganded by any compound (or of the sites
    named), a dot where it is liganded, and the site's selectivity. "" with fewer than two compounds."""
    X = d.get("cys") or {}
    comps = X.get("compounds") or []
    if len(comps) < 2:
        return ""
    if named is not None:
        pos = {i: k for k, i in enumerate(X["i"])}
        ks = [pos[i] for i in named if i in pos]
    else:
        ks = _selectivity_rows(X)
    if not ks:
        return ""
    ink = _inks(style)
    W, Hmax = charts._box(style)
    thr = math.log2(X.get("ratio") or 4)
    names = [clean_text(c["name"]) for c in comps]
    lab, right = 130, 96
    cell_w = max(28.0, min(72.0, (W - lab - right) / len(comps)))
    # compound names across the top: level when a cell is wide enough, else at 45 degrees
    head = 24 if cell_w >= 60 else 16 + min(max(len(x) for x in names), 18) * 11 * 0.56 * 0.71
    fits = max(1, int((Hmax - head - 8) / 9))
    total = len(ks)
    ks = ks[:fits]
    cell_h = max(9.0, min(18.0, (Hmax - head - 8) / len(ks)))
    w, h = round(lab + len(comps) * cell_w + right), round(head + len(ks) * cell_h + 8)
    sel_names = X.get("selNames") or ["", "selective", "shared", "unresolved"]

    def shade(r):
        return charts._blend(ink["surface"], ink["up"], max(0.0, min(1.0, r / (2 * thr))))

    b = []
    for j, nm in enumerate(names):
        cx = lab + j * cell_w + cell_w / 2
        if cell_w >= 60:
            b.append(_text(cx, head - 8, nm[: int(cell_w / 6)], 11, ink["text2"], text_anchor="middle"))
        else:
            b.append(_text(0, 0, nm[:18], 11, ink["text2"], transform=f"translate({_n(cx - 2)},{_n(head - 6)}) rotate(-45)"))
    for r, k in enumerate(ks):
        y = head + r * cell_h
        b.append(_text(lab - 6, y + cell_h / 2 + 4, _name(d, X["i"][k])[:20], 11, ink["text2"], text_anchor="end"))
        for j, c in enumerate(comps):
            v = c["r"][k]
            x = lab + j * cell_w
            b.append(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(cell_w - 1)}" height="{_n(cell_h - 1)}" '
                     f'fill="{ink["sunk"] if v is None else shade(v)}"/>')
            if c["cls"][k] == 0:
                dark = v is not None and v / (2 * thr) > 0.55
                b.append(f'<circle cx="{_n(x + cell_w / 2)}" cy="{_n(y + cell_h / 2 - 0.5)}" r="{_n(min(3.0, cell_h / 3.5))}" '
                         f'fill="{ink["surface"] if dark else ink["text"]}"/>')
        word = sel_names[X["sel"][k]] if X["sel"][k] < len(sel_names) else ""
        b.append(_text(lab + len(comps) * cell_w + 8, y + cell_h / 2 + 4, word, 10.5, ink["muted"]))
    legend = [(shade(thr), f"R = {X.get('ratio', 4):g}"), (shade(2 * thr), f"R ≥ {X.get('ratio', 4) ** 2:g}"),
              (None, "white: R ≤ 1 · grey: not measured · dot: liganded"),
              (None, f"{len(ks)} of {total} sites" + (" asked for" if named is not None else " liganded by any compound")
               + ("" if len(ks) == total else " (the rest do not fit this size)"))]
    what = "Liganded sites across compounds"
    return _compose(style, d, ("".join(b), w, h), what, what, f"median competition ratio per compound · {d.get('title') or ''}",
                    legend, f"liganded: {clean_text(X.get('rule'))}", about=", ".join(names), generator=generator)


# ----------------------------------------------------------- kinase activity --


def kinase_rows(C: dict, fits: int) -> list[list]:
    """The kinases a bar chart shows: every scored one, or the `fits` with the largest |z|; highest z first."""
    ks = list(C.get("k") or [])
    if len(ks) > fits:
        ks = sorted(ks, key=lambda r: (-abs(r[3] or 0), r[0]))[:fits]
    return sorted(ks, key=lambda r: (-(r[3] or 0), r[0]))


def figure_kinase_activity(d: dict, ci: int, style: dict, generator: str = "") -> str:
    """Comparison ci's kinase activity (KSEA z-scores, phospho.py D79): a bar per kinase with enough substrates,
    coloured when its adjusted p is at or below the report's p-value cut-off."""
    X = (d.get("phos") or {}).get("ksea") or {}
    C = (X.get("comps") or [])[ci]
    total = len(C.get("k") or [])
    if not total:
        return ""
    ink = _inks(style)
    _W, Hbox = charts._box(style)
    fits = max(3, int((Hbox - 60) / 11))
    ks = kinase_rows(C, fits)
    ls = style["line_scale"]
    lim = max(2.5, max(abs(r[3] or 0) for r in ks) * 1.1)
    name_w = min(18, max(len(clean_text(r[0])) for r in ks)) * 11 * 0.56

    def colour(r):
        return ink["up"] if r[6] == "up" else ink["down"] if r[6] == "down" else ink["ns"]

    def draw(W: int, H: int) -> str:
        L, R, T, B = 22 + name_w, 18, 10, 44
        n = len(ks)
        row = (H - T - B) / n

        def Xs(v):
            return L + (v + lim) / (2 * lim) * (W - L - R)

        b = _axes(ink, Xs, lambda v: 0, _inner_ticks(-lim, lim, 6), [], L, R, T, B, W, H,
                  "kinase activity (KSEA z-score)", "", ls)
        b.append(f'<line x1="{_n(Xs(0))}" x2="{_n(Xs(0))}" y1="{T}" y2="{_n(H - B)}" stroke="{ink["axis"]}" '
                 f'stroke-width="{_n(ls)}"/>')
        bar = max(2.0, min(14.0, row * 0.72))
        for k, r in enumerate(ks):
            y = T + k * row + (row - bar) / 2
            z = r[3] or 0.0
            x0, x1 = sorted((Xs(0), Xs(z)))
            b.append(f'<rect x="{_n(x0)}" y="{_n(y)}" width="{_n(max(0.5, x1 - x0))}" height="{_n(bar)}" '
                     f'fill="{colour(r)}"/>')
            if row >= 9:
                b.append(_text(L - 6, y + bar / 2 + 4, clean_text(r[0])[:18] + f" ({r[1]})", min(11.0, row * 0.9),
                               ink["text2"], text_anchor="end"))
        return "".join(b)

    alpha = X.get("alpha", (d.get("settings") or {}).get("alpha", 0.05))
    legend = [(ink["up"], f"more active {sum(1 for r in C['k'] if r[6] == 'up')}"),
              (ink["down"], f"less active {sum(1 for r in C['k'] if r[6] == 'down')}"),
              (ink["ns"], "not significant"), (None, "(m) = measured substrates")]
    if len(ks) < total:
        legend.append((None, f"{len(ks)} of {total} kinases with the largest |z| (the rest do not fit this size)"))
    name = clean_text(C.get("name")) or "Comparison"
    rule = (f"KSEA on {clean_text(X.get('file'))}: kinases with ≥ {X.get('min_substrates')} substrates, "
            f"coloured at adjusted p ≤ {alpha:g}")
    return _compose(style, d, draw, "Kinase activity", name, f"Kinase activity (KSEA) · {d.get('title') or ''}",
                    legend, rule, about=name, generator=generator)


# ------------------------------------------------------------------ catalog --


def catalog(d: dict, which, features=None, top: int | None = None) -> list:
    """The section figures asked for (`which`: kinds) that the report's data can draw. `features`: names of
    features for the curve and profile grids, the rank plot's labels and the selectivity map's rows (default:
    the `top` most relevant, the liganded sites). Each Figure has notes for names that were not found."""
    Figure = charts.Figure
    top = max(1, min(MAX_PANELS, int(top or TOP_PANELS)))
    named, missing = match_features(d, features) if features else (None, [])
    if named is not None:
        named = named[:MAX_PANELS * 4]
    out = []
    note = [f"not found in the report: {', '.join(missing)}"] if missing else []
    X = d.get("dose") or {}
    for si, S in enumerate(X.get("series") or [] if X.get("ran") else []):
        nm = clean_text(S.get("name"))
        tag = nm or "curves"
        if "dose_potency" in which:
            out.append(Figure(safe_name(f"dose_potency_{tag}.svg"), "dose_potency",
                              f"Dose-response, {nm or 'every curve'}: potency (pEC50) against effect (log2 curve fold change)",
                              lambda st, g, si=si: figure_dose_potency(d, si, st, g), "--labels N: names on the N most relevant"))
        if "dose_curves" in which:
            if named is not None:
                pos = {i: k for k, i in enumerate(S["i"])}
                ks, chosen = [pos[i] for i in named if i in pos][:MAX_PANELS], True
                gone = [_name(d, i) for i in named if i not in pos]
            else:
                ks, chosen, gone = [k for k in range(len(S["i"])) if S["cls"][k] in ("up", "down")][:top], False, []
            if ks:
                out.append(Figure(safe_name(f"dose_curves_{tag}.svg"), "dose_curves",
                                  f"Dose-response curves, {nm or 'the compound'}: {len(ks)} "
                                  f"({'chosen' if chosen else 'the most relevant regulated'}): points, fit, 95% interval of pEC50",
                                  lambda st, g, si=si, ks=ks, chosen=chosen: figure_dose_curves(d, si, ks, st, g, chosen),
                                  f"--features NAME,... or --top N (now {top})",
                                  note + ([f"no curve for {', '.join(gone)} in {nm or 'this compound'}"] if gone else [])))
    X = d.get("time") or {}
    for si, S in enumerate(X.get("series") or [] if X.get("ran") else []):
        nm = clean_text(S.get("name"))
        tag = nm or "series"
        if "time_patterns" in which and S.get("patterns"):
            out.append(Figure(safe_name(f"time_patterns_{tag}.svg"), "time_patterns",
                              f"Time course, {nm or 'the series'}: {len(S['patterns'])} patterns of changing features "
                              "(median log2 fold change against the first time point)",
                              lambda st, g, si=si: figure_time_patterns(d, si, st, g)))
        if "time_profiles" in which:
            if named is not None:
                pos = {i: k for k, i in enumerate(S["i"])}
                ks, chosen = [pos[i] for i in named if i in pos][:MAX_PANELS], True
                gone = [_name(d, i) for i in named if i not in pos]
            else:
                ks, chosen, gone = [k for k in range(len(S["i"])) if S["cls"][k] != "not"][:top], False, []
            if ks and d.get("v"):
                out.append(Figure(safe_name(f"time_profiles_{tag}.svg"), "time_profiles",
                                  f"Time course, {nm or 'the series'}: {len(ks)} features over time "
                                  f"({'chosen' if chosen else 'the most significant changing'}), every replicate and the mean"
                                  + (f", {clean_text(S['vs'])} dashed" if S.get("vs") else ""),
                                  lambda st, g, si=si, ks=ks, chosen=chosen: figure_time_profiles(d, si, ks, st, g, chosen),
                                  f"--features NAME,... or --top N (now {top})",
                                  note + ([f"{', '.join(gone)} not tested in {nm or 'this series'}"] if gone else [])))
    X = d.get("cys") or {}
    comps = X.get("compounds") or [] if X.get("ran") else []
    for ci, C in enumerate(comps):
        if "liganded_rank" in which and any(v is not None for v in C["r"]):
            nm = clean_text(C.get("name"))
            out.append(Figure(safe_name(f"liganded_rank_{nm or 'compound'}.svg"), "liganded_rank",
                              f"Liganded sites, {nm}: every measured site ranked by competition ratio "
                              f"(liganded: {clean_text(X.get('rule'))})",
                              lambda st, g, ci=ci: figure_liganded_rank(d, ci, st, g, named),
                              "--features NAME,... names those sites; else --labels N names the strongest liganded", note))
    if "liganded_selectivity" in which and len(comps) >= 2 and (named is not None or any(X.get("nlig") or [])):
        out.append(Figure("liganded_selectivity.svg", "liganded_selectivity",
                          "Liganded sites across compounds: median competition ratio of each "
                          + ("chosen site" if named is not None else "site liganded by any compound") + ", and its selectivity",
                          lambda st, g: figure_liganded_selectivity(d, st, g, named),
                          "--features NAME,... for other sites", note))
    K = (d.get("phos") or {}).get("ksea") or {}
    for ci, C in enumerate(K.get("comps") or [] if K.get("ran") else []):
        if "kinase_activity" in which and C.get("k"):
            nm = clean_text(C.get("name"))
            out.append(Figure(safe_name(f"kinase_activity_{C.get('slug') or nm or ci + 1}.svg"), "kinase_activity",
                              f"Kinase activity, {nm}: KSEA z-score of every kinase with enough measured substrates",
                              lambda st, g, ci=ci: figure_kinase_activity(d, ci, st, g)))
    return out
