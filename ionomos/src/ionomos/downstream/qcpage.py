"""
qc_trend.html — the instrument QC trend page (D45): one self-contained file in the lab's log folder.

    html = render(series, settings, store=path)      # series from qctrend.analyse()

Per series (instrument · method · standard · amount): the newest run's verdict, a Levey-Jennings chart per
metric (baseline mean, ±1/2/3 SD, the baseline runs shaded, each run coloured by what the rules said, a
tooltip per point), and every run in a table, newest first, with a link to its experiment's report. Static
SVG and the report's stylesheet (report.css, inlined): no script, no network, opens anywhere.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from ionomos.downstream.charts import nice_ticks

MARKER = "ionomos-qc-trend-v1"
MAX_POINTS = 150  # most recent runs drawn per chart (the table lists all)

EXTRA_CSS = """
.pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;font-weight:600;border:1px solid var(--line)}
.pill.ok{color:var(--c2);border-color:var(--c2)}.pill.warning{color:var(--up);border-color:var(--up)}
.pill.watch{color:var(--c3);border-color:var(--c3)}.pill.baseline,.pill.nodata{color:var(--muted)}
.lj{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px}
.lj .card{margin:0;padding:10px 12px}.lj h4{margin:0 0 2px;font-size:13px}.lj .u{color:var(--muted);font-size:12px}
.lj svg{display:block;width:100%;height:auto}
.lj .sd3{stroke:var(--up);stroke-dasharray:2 3}.lj .sd2{stroke:var(--c3);stroke-dasharray:5 4}
.lj .sd1{stroke:var(--line)}.lj .mean{stroke:var(--text2)}.lj .band{fill:var(--sunk)}
.lj .ax{stroke:var(--axis)}.lj text{fill:var(--muted);font-size:10px}.lj .trace{fill:none;stroke:var(--ns)}
.lj .p{fill:var(--text2)}.lj .p.baseline{fill:var(--muted)}.lj .p.warn{fill:var(--c3)}.lj .p.bad{fill:var(--up)}
.lj .p.good{fill:var(--c2)}
td.verdict{white-space:normal;min-width:280px}
.rules td{white-space:normal}
"""


def _asset(name: str) -> str:
    from ionomos.downstream.report import _asset as report_asset

    return report_asset(name)


def fmt(key: str, v) -> str:
    if v is None:
        return "–"
    if key in ("precursors", "proteins", "peptides", "psms"):
        return f"{v:,.0f}"
    if key == "signal":
        return f"{v:.3g}"
    if key in ("fwhm",):
        return f"{v:.3f}"
    return f"{v:+.2f}" if key in ("ms1_ppm", "ms2_ppm", "rt_shift") else f"{v:.2f}"


def _point_class(run: dict, key: str, i: int, base: set) -> str:
    flags = (run.get("flags") or {}).get(key) or []
    if i in base:
        return "baseline"
    if any(f["bad"] and f["rule"] != "1-2s" for f in flags):
        return "bad"
    if any(f["bad"] for f in flags):
        return "warn"
    if flags:
        return "good"
    return ""


def chart(ser: dict, mt: dict, width: int = 360, height: int = 170) -> str:
    """One Levey-Jennings chart (static SVG, a tooltip on each point)."""
    runs = ser["runs"]
    start = max(0, len(runs) - MAX_POINTS)
    idx = [i for i in range(start, len(runs)) if mt["values"][i] is not None]
    if not idx:
        return ""
    mean, sd = mt["mean"], mt["sd"]
    ys = [mt["values"][i] for i in idx]
    lo, hi = min(ys), max(ys)
    if mean is not None:
        lo, hi = min(lo, mean - 3.4 * sd), max(hi, mean + 3.4 * sd)
    if hi - lo < 1e-12:
        lo, hi = lo - 1, hi + 1
    ticks = nice_ticks(lo, hi, 5)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    ml, mr, mtop, mb = 46, 8, 8, 20
    pw, ph = width - ml - mr, height - mtop - mb
    n = max(len(runs) - start - 1, 1)

    def X(i):
        return ml + (i - start) / n * pw if len(runs) - start > 1 else ml + pw / 2

    def Y(v):
        return mtop + (hi - v) / (hi - lo) * ph

    base = set(ser["baseline"])
    b = []
    shown = [i for i in ser["baseline"] if i >= start]
    if shown:
        x0, x1 = X(min(shown)) - 4, X(max(shown)) + 4
        b.append(f"<rect class='band' x='{x0:.1f}' y='{mtop}' width='{max(x1 - x0, 1):.1f}' height='{ph}'/>")
    for t in ticks:
        b.append(f"<text x='{ml - 5}' y='{Y(t) + 3:.1f}' text-anchor='end'>{escape(_tick(t))}</text>")
    b.append(f"<line class='ax' x1='{ml}' x2='{ml}' y1='{mtop}' y2='{mtop + ph}'/>")
    if mean is not None:
        for k, cls in ((3, "sd3"), (2, "sd2"), (1, "sd1")):
            for sgn in (1, -1):
                y = Y(mean + sgn * k * sd)
                b.append(f"<line class='{cls}' x1='{ml}' x2='{ml + pw}' y1='{y:.1f}' y2='{y:.1f}'/>")
        b.append(f"<line class='mean' x1='{ml}' x2='{ml + pw}' y1='{Y(mean):.1f}' y2='{Y(mean):.1f}'/>")
    pts = " ".join(f"{X(i):.1f},{Y(mt['values'][i]):.1f}" for i in idx)
    b.append(f"<polyline class='trace' points='{pts}'/>")
    for i in idx:
        r = runs[i]
        v = mt["values"][i]
        z = mt["z"][i]
        raw = (r.get("metrics") or {}).get(mt["key"])
        rules = ", ".join(f["rule"] for f in (r.get("flags") or {}).get(mt["key"], []))
        tip = (f"{r.get('run', '')} · {(r.get('acquired') or '')[:16].replace('T', ' ')}\n"
               f"{mt['label']}: {fmt(mt['key'], raw)}" + (f" · z {z:+.1f}" if z is not None else "")
               + (f" · {rules}" if rules else "") + (" · baseline" if i in base else ""))
        cls = _point_class(r, mt["key"], i, base)
        b.append(f"<circle class='p {cls}' cx='{X(i):.1f}' cy='{Y(v):.1f}' r='3.2'><title>{escape(tip)}</title></circle>")
    if start:
        b.append(f"<text x='{ml}' y='{height - 5}'>last {MAX_POINTS} of {len(runs)} runs</text>")
    first = (runs[start].get("acquired") or "")[:10]
    last = (runs[-1].get("acquired") or "")[:10]
    b.append(f"<text x='{ml}' y='{height - 5}'>{escape(first)}</text>" if not start else "")
    b.append(f"<text x='{ml + pw}' y='{height - 5}' text-anchor='end'>{escape(last)}</text>")
    label = f"Levey-Jennings chart of {mt['label']} for {ser['name']}"
    return (f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{escape(label)}'>" + "".join(b) + "</svg>")


def _tick(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:g}".replace("-", "−")


def _series_section(k: int, ser: dict) -> str:
    runs = ser["runs"]
    latest = runs[-1]
    out = [f"<section id='s{k}'><h2>{escape(ser['name'])} <span class='pill {escape(ser['status'])}'>"
           f"{escape(ser['status'])}</span></h2>",
           f"<p class='sub'>Newest run <b>{escape(latest.get('run', ''))}</b> "
           f"({escape((latest.get('acquired') or '')[:16].replace('T', ' '))}): {escape(ser['verdict'])}</p>",
           f"<p class='sub'>{len(runs)} run(s) · {escape(ser['acquisition'] or '?')} · baseline: "
           f"{escape(ser['baseline_text'])}" + ("" if ser["ready"] else " (still being collected)") + "</p>"]
    cards = []
    for mt in ser["metrics"]:
        svg = chart(ser, mt)
        if not svg:
            continue
        stat = ("baseline not set yet" if mt["mean"] is None else
                f"mean {fmt(mt['key'], 10 ** mt['mean'] if mt['log'] else mt['mean'])}, "
                f"SD {mt['sd']:.3g}{' (log10)' if mt['log'] else ''}, n = {mt['n']}")
        cards.append(f"<div class='card'><h4>{escape(mt['label'])}</h4><div class='u'>{escape(mt['unit'])}"
                     f"{' · ' if mt['unit'] else ''}{escape(stat)}</div>{svg}</div>")
    if cards:
        out.append("<div class='lj'>" + "".join(cards) + "</div>")
    keys = [mt["key"] for mt in ser["metrics"]]
    labels = {mt["key"]: mt["label"] for mt in ser["metrics"]}
    head = "".join(f"<th>{escape(labels[x])}</th>" for x in keys)
    rows = []
    for r in reversed(runs):
        when = (r.get("acquired") or "")[:16].replace("T", " ")
        src = {"raw header": "from the raw file's header", "ThermoRawFileParser": "from ThermoRawFileParser's output",
               "name": "from the file name", "file time": "the raw file's time (approximate)",
               "filed": "when it was filed"}.get(
            r.get("acquired_from") or "", "")
        exp = escape(r.get("experiment") or "")
        if r.get("report"):
            try:
                exp = f"<a href='{escape(Path(r['report']).as_uri())}'>{exp}</a>"
            except ValueError:
                pass
        cells = "".join(f"<td class='n'>{escape(fmt(x, (r.get('metrics') or {}).get(x)))}</td>" for x in keys)
        rows.append(f"<tr><td title='{escape(src)}'>{escape(when)}</td><td>{escape(r.get('run') or '')}</td>"
                    f"<td>{exp}</td><td><span class='pill {escape(r.get('status') or '')}'>"
                    f"{escape(r.get('status') or '')}</span></td><td class='verdict'>{escape(r.get('verdict') or '')}"
                    f"</td>{cells}</tr>")
    out.append("<div class='tablewrap'><table><thead><tr><th>Acquired</th><th>Run</th><th>Experiment</th>"
               f"<th>Status</th><th>Verdict</th>{head}</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
    out.append("</section>")
    return "".join(out)


def render(series: list[dict], s: dict, store: Path | None = None) -> str:
    from ionomos.buildinfo import one_line
    from ionomos.qctrend import RULES

    title = "Instrument QC" + (f" — {s.get('instrument')}" if s.get("instrument") else "")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    b = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
         f"content='width=device-width,initial-scale=1'><meta name='generator' content='{MARKER}'>"
         f"<title>{escape(title)}</title><style>{_asset('report.css')}{EXTRA_CSS}</style></head><body><main>",
         f"<div class='top'><div><h1>{escape(title)}</h1><div class='meta'>{escape(now)} · {escape(one_line())}"
         "</div></div></div>"]
    if not series:
        b.append("<section><div class='card'><p><b>No QC-standard runs yet.</b> Runs are trended once a search of "
                 "one finishes. A run counts when its folder or .raw name contains one of: "
                 + ", ".join(f"<code>{escape(p)}</code>" for p in s.get("match") or [])
                 + (" (or its method is " + ", ".join(escape(m) for m in s["methods"]) + ")" if s.get("methods")
                    else "") + ". Past runs: <code>ionomos qc-trend --rebuild</code>.</p></div></section>")
    else:
        b.append("<nav class='toc'>" + "".join(f"<a href='#s{k}'>{escape(ser['name'])}</a>"
                                               for k, ser in enumerate(series)) + "<a href='#how'>How to read</a></nav>")
        tiles = []
        for k, ser in enumerate(series):
            latest = ser["runs"][-1]
            tiles.append(f"<a class='tile' href='#s{k}' style='text-decoration:none;color:inherit'>"
                         f"<div class='k'>{escape(ser['name'])}</div><div><span class='pill {escape(ser['status'])}'>"
                         f"{escape(ser['status'])}</span></div><div class='d'>"
                         f"{escape((latest.get('acquired') or '')[:10])} · {escape(ser['verdict'][:140])}</div></a>")
        b.append("<div class='tiles'>" + "".join(tiles) + "</div>")
        b += [_series_section(k, ser) for k, ser in enumerate(series)]
    rules = "".join(f"<tr><td><b>{escape(k)}</b></td><td>{escape(v)}</td></tr>" for k, v in RULES.items())
    b.append("<section id='how'><h2>How to read this page</h2><p class='sub'>Each QC-standard run is compared with "
             "its series' baseline: the mean and standard deviation (SD) of the baseline runs (shaded). "
             "Lines: mean (solid), ±1 SD (faint), ±2 SD (amber, dashed), ±3 SD (red, dotted). A red point broke a "
             "rule in the direction that matters (fewer identifications, less signal, broader peaks, a shifted "
             "mass error or retention time); amber is a 2 SD warning; green is a change for the better. "
             "<b>warning</b> means a rule is broken and an item is on the attention list; <b>watch</b> means worth a "
             "look; <b>baseline</b> runs set the limits. Hover a point for its run and value.</p>"
             f"<div class='card'><table class='rules'><tbody>{rules}</tbody></table></div>"
             "<p class='sub'>Numbers come from the search's own tables (DIA-NN stats.tsv / pg_matrix / report.tsv, "
             "FragPipe psm.tsv / combined_protein.tsv); the RT shift is the median difference from the baseline over "
             "the most intense peptides both runs identified. Acquisition times come from the Xcalibur stamp in the "
             "file name, else the raw file's time (hover a date to see which). Settings: <code>qc_trend:</code> in "
             "config.yaml; see docs/QC_TREND.md."
             + (f" Data: <code>{escape(str(store))}</code>." if store else "") + "</p></section>")
    b.append("</main></body></html>")
    return "".join(b)
