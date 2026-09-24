"""
results/report.html — one self-contained, interactive page per experiment.

The page carries its data as JSON and draws everything in the browser
(assets/report.js + report.css, inlined): no internet, no other files needed.

    Overview       tiles, notes, the processing pipeline (loaded -> filtered -> imputed -> tested)
    Differential   volcano / MA plot with live cut-offs, zoom, search and labels; click a protein for its
                   values per condition (imputed points hollow) and its statistics in every comparison;
                   sortable, paginated table with CSV export; p-value histogram
    Heatmap        significant features, row-centred, clustered
    Enrichment     over-represented gene sets per comparison and direction; click a term to mark its genes
    QC             PCA, sample correlation, missing values, distributions, CV, identifications, imputation
    Methods        a paragraph ready for a notebook, the exact settings, and FragPipe-Analyst export files

The static volcano_*.svg files next to it are for slides and for viewing without scripts.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from html import escape
from importlib import resources

from ionomos.downstream import fpa, qc
from ionomos.downstream.analysis import TESTS, DiffResult, Settings
from ionomos.downstream.quant import QuantMatrix

MARKER = "ionomos-report-v2"


def _asset(name: str) -> str:
    return resources.files("ionomos.downstream").joinpath("assets", name).read_text(encoding="utf-8")


def _r(v, digits: int = 5):
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        if v != 0 and abs(v) < 1e-4:
            return float(f"{v:.4g}")
        return round(v, digits)
    return v


def _level_word(m: QuantMatrix | None) -> tuple[str, str]:
    lvl = (m.level if m else "feature") or "feature"
    return f"{lvl.capitalize()}s", lvl


def payload(ctx: dict, m: QuantMatrix | None, p: fpa.Processed | None, diffs: list[DiffResult], notes: list[str],
            files: list[str], s: Settings, qcd: dict, enrichment: list[dict]) -> dict:
    pm = p.m if p else m
    title, word = _level_word(pm)
    d: dict = {
        "marker": MARKER,
        "title": ctx.get("experiment") or "Experiment",
        "kind": pm.kind if pm else "intensity",
        "levelTitle": title, "levelWord": word,
        "sourceName": (pm.source.replace("\\", "/").split("/")[-1] if pm else ""),
        "samples": list(pm.samples) if pm else [],
        "cond": [pm.condition[x] for x in pm.samples] if pm else [],
        "conditions": pm.conditions if pm else [],
        "f": {"id": [], "label": [], "desc": []},
        "v": [], "imp": None, "comps": [], "notes": notes, "files": files,
        "settings": {"log2fc": s.log2fc, "alpha": s.alpha, "use_adjusted": s.use_adjusted, "top_labels": s.top_labels,
                     "normalize": s.normalize, "test": s.test},
        "imputationLabel": fpa.IMPUTATION_LABELS.get(p.imputation, "") if p else "",
        "qc": {}, "enr": [], "enrNote": "",
    }
    if pm is None:
        return d
    d["f"] = {"id": [f.id for f in pm.features], "label": [f.label for f in pm.features],
              "desc": [f.description for f in pm.features]}
    d["v"] = [[_r(v, 4) for v in row] for row in pm.values]
    if p and p.n_imputed:
        d["imp"] = ["".join("1" if k else "0" for k in row) for row in p.imputed]
    n = len(pm.features)
    for dr in diffs:
        by = {r["index"]: r for r in dr.rows}
        cols = {k: [None] * n for k in ("fc", "p", "q", "ciL", "ciR", "nt", "nc", "a")}
        for i in range(n):
            r = by.get(i)
            if not r:
                continue
            cols["fc"][i] = _r(r["log2fc"])
            cols["p"][i] = _r(r["pvalue"], 8)
            cols["q"][i] = _r(r["qvalue"], 8)
            cols["ciL"][i] = _r(r["ci_low"], 4)
            cols["ciR"][i] = _r(r["ci_high"], 4)
            cols["nt"][i] = r["n_treatment"]
            cols["nc"][i] = r["n_control"]
            mt, mc = r["mean_treatment"], r["mean_control"]
            cols["a"][i] = _r((mt + mc) / 2, 4) if mt is not None and mc is not None else _r(mt, 4)
        d["comps"].append({"name": dr.name, "slug": dr.slug(), "t1": dr.treatment, "t2": dr.control, **cols})
    if qcd:
        cv = {c: {"hist": qc.histogram(v["cvs"], 0.0, 1.0, 20), "median": _r(v["median"], 4)}
              for c, v in (qcd.get("cv") or {}).items()}
        pca = qcd.get("pca") or {}
        corr = qcd.get("correlation") or {}
        d["qc"] = {
            "pca": {"scores": [[_r(x, 4) for x in row] for row in pca.get("scores", [])],
                    "percent": [_r(x, 2) for x in pca.get("percent", [])], "n": pca.get("n", 0)},
            "correlation": {"matrix": [[_r(x, 4) for x in row] for row in corr.get("matrix", [])],
                            "order": corr.get("order", []), "complete_rows": corr.get("complete_rows", 0)},
            "cv": cv,
            "features_per_sample": qcd.get("features_per_sample", []),
            "features_per_sample_after": qcd.get("features_per_sample_after", []),
            "missing": {**(qcd.get("missing") or {}), "curve": [[_r(a, 4), b] for a, b in (qcd.get("missing") or {}).get("curve", [])]},
            "box_before": [{k: _r(v, 4) for k, v in b.items()} if b else None for b in qcd.get("box_before", [])],
            "box_after": [{k: _r(v, 4) for k, v in b.items()} if b else None for b in qcd.get("box_after", [])],
        }
        hm = qcd.get("heatmap")
        if hm:
            d["qc"]["heatmap"] = {"rows": hm["rows"], "cols": hm["cols"], "total_significant": hm["total_significant"],
                                  "values": [[_r(v, 3) for v in row] for row in hm["values"]]}
    d["enr"] = [{**b, "terms": [{**t, "p": _r(t["p"], 8), "q": _r(t["q"], 8),
                                 "log2_odds": (_r(t["log2_odds"], 3) if math.isfinite(t["log2_odds"]) else "Inf")}
                                for t in b["terms"]]} for b in enrichment]
    if not enrichment:
        why = "; ".join(ctx.get("enrichment_notes") or [])
        d["enrNote"] = ("Enrichment is off (Analysis tab)." if not s.enrichment else
                        (why + "." if why else "Enrichment needs at least one comparison with hits."))
    elif ctx.get("enrichment_notes"):
        d["enrNote"] = "; ".join(ctx["enrichment_notes"])
    return d


# --------------------------------------------------------------------- text --


def methods_text(m: QuantMatrix | None, p: fpa.Processed | None, diffs: list[DiffResult], s: Settings, method: str,
                 fragpipe_note: str, enrichment: list[dict]) -> str:
    parts = [f"Raw files were searched with FragPipe{(' (' + escape(fragpipe_note) + ')') if fragpipe_note else ''} "
             f"using the lab's pinned {escape(method)} workflow, run automatically by Ionomos."]
    if m is None:
        return " ".join(parts)
    if m.kind == "ratio":
        parts.append(f"Labelled peptides were merged to {m.level}s (mean log2 heavy/light ratio per replicate, as in "
                     "the lab's isoDTB site script). For each site, the replicate ratios were tested against 0 with "
                     + ("a moderated one-sample t-test (limma: lmFit, eBayes)." if s.test == "limma"
                        else "a two-sided one-sample t-test."))
    else:
        steps = [f"{m.level.capitalize()} intensities were log2-transformed"]
        if s.remove_contaminants:
            steps.append("contaminants removed")
        if s.filter_global_pct or s.filter_condition_pct:
            rule = []
            if s.filter_global_pct:
                rule.append(f"in at least {s.filter_global_pct:g}% of all samples")
            if s.filter_condition_pct:
                rule.append(f"in at least {s.filter_condition_pct:g}% of the samples of at least one condition")
            steps.append("features kept when quantified " + " and ".join(rule))
        if s.normalize != "none":
            steps.append({"median": "samples median-centred", "gn": "samples median-centred and MAD-scaled"}[s.normalize])
        imp = p.imputation if p else "none"
        if imp == "perseus":
            steps.append(f"missing values imputed from a normal distribution down-shifted by {s.impute_shift:g} SD "
                         f"with {s.impute_scale:g}× the SD of each sample (Perseus-type)")
        elif imp != "none":
            steps.append(f"missing values imputed ({fpa.IMPUTATION_LABELS[imp]})")
        parts.append(", ".join(steps) + ".")
        if s.test == "limma":
            parts.append("Differential abundance was tested with limma (linear model ~0 + condition, empirical-Bayes "
                         "moderated t-statistics, 95% confidence intervals), following FragPipe-Analyst's test_limma.")
        else:
            parts.append(f"Conditions were compared with a two-sided {TESTS[s.test]}.")
    parts.append(f"P-values were adjusted with the Benjamini–Hochberg procedure; features were called significant at "
                 f"{escape(s.describe())}.")
    if enrichment:
        libs = sorted({b["library"] for b in enrichment})
        parts.append("Over-representation of up- and down-regulated genes in gene sets (" + escape(", ".join(libs)) +
                     "; Enrichr libraries) was tested with a one-sided hypergeometric test against all quantified "
                     "genes, BH-adjusted.")
    parts.append("Processing and statistics port FragPipeAnalystR / FragPipe-Analyst (Hsiao et al., J. Proteome Res. "
                 "2024, doi:10.1021/acs.jproteome.4c00294) and limma (Ritchie et al., Nucleic Acids Res. 2015).")
    return " ".join(parts)


def _pipeline(p: fpa.Processed | None, diffs: list[DiffResult]) -> str:
    if p is None:
        return ""
    chips = []
    for st in p.steps:
        name = st["step"]
        if name == "loaded":
            chips.append(f"<span class='step'>loaded <b>{st['features']:,}</b> × {st['samples']} samples</span>")
        elif name == "samples chosen":
            chips.append(f"<span class='step'>{st['samples']} samples used</span>")
        elif name == "imputation":
            chips.append(f"<span class='step'>imputed <b>{st['values']:,}</b> values ({st['percent']}%)</span>")
        elif name == "normalisation":
            chips.append(f"<span class='step'>{escape(st['method'])}</span>")
        elif "removed" in st:
            label = {"contaminants removed": "contaminants", "missing-value filter": "missing-value filter",
                     "no values at all": "empty rows"}.get(name, name)
            tip = escape(st.get("rule", ""))
            chips.append(f"<span class='step' title='{tip}'>{label} <b>−{st['removed']:,}</b></span>")
    tested = max((d.tested for d in diffs), default=0)
    if diffs:
        chips.append(f"<span class='step'>tested <b>{tested:,}</b></span>")
    return "<div class='pipeline'>" + "<span class='arrow'>→</span>".join(chips) + "</div>"


def _settings_table(s: Settings, p: fpa.Processed | None) -> str:
    rows = [("Test", TESTS[s.test]), ("Comparisons", {"control": "each condition vs the control", "all": "all pairs",
                                                      "others": "each condition vs all others"}[s.de_type]
             + (f" (control: {s.control})" if s.control else "")),
            ("Cut-offs", s.describe()),
            ("Contaminants", "removed" if s.remove_contaminants else "kept"),
            ("Missing-value filter", f"≥{s.filter_global_pct:g}% of all samples, ≥{s.filter_condition_pct:g}% in one condition"),
            ("Normalisation", s.normalize),
            ("Imputation", f"{s.imputation}" + (f" → {fpa.IMPUTATION_LABELS[p.imputation]}" if p else "")),
            ("Enrichment", ", ".join(s.enrichment_libraries) if s.enrichment else "off")]
    if s.exclude_samples:
        rows.append(("Samples left out", ", ".join(s.exclude_samples)))
    if s.sample_conditions:
        rows.append(("Conditions changed", ", ".join(f"{a} → {b}" for a, b in s.sample_conditions.items())))
    return "<div class='kv card'>" + "".join(f"<div>{escape(k)}</div><div>{escape(str(v))}</div>" for k, v in rows) + "</div>"


def render(ctx: dict, m: QuantMatrix | None, p: fpa.Processed | None, diffs: list[DiffResult], notes: list[str],
           files: list[str], s: Settings | None = None, qcd: dict | None = None,
           enrichment: list[dict] | None = None) -> str:
    s = s or Settings()
    enrichment = enrichment or []
    title = ctx.get("experiment") or "Experiment"
    meta = " · ".join(x for x in (ctx.get("user"), ctx.get("method"), ctx.get("date"),
                                  f"generated {datetime.now():%Y-%m-%d %H:%M}", f"Ionomos {ctx.get('version', '')}") if x)
    data = json.dumps(payload(ctx, m, p, diffs, notes, files, s, qcd or {}, enrichment), separators=(",", ":"),
                      allow_nan=False).replace("</", "<\\/")
    pm = p.m if p else m
    b = [f"<main><div class='top'><div><h1>{escape(title)}</h1><div class='meta'>{escape(meta)}</div></div>"
         "<div><button id='theme' title='Light / dark'>◐</button> <button onclick='window.print()'>Print</button></div></div>",
         "<nav class='toc'><a href='#overview'>Overview</a><a href='#differential'>Differential</a>"
         "<a href='#heat'>Heatmap</a><a href='#enrichment'>Enrichment</a><a href='#quality'>Quality control</a>"
         "<a href='#methods'>Methods</a><a href='#files'>Files</a></nav>",
         "<noscript><div class='notes'>This report draws its charts with JavaScript. The volcano_*.svg and *.tsv "
         "files in this folder hold the same results.</div></noscript>",
         "<section id='overview'><div class='tiles' id='tiles'></div>"]
    if notes:
        b.append("<div class='notes'><b>Notes</b><ul>" + "".join(f"<li>{escape(n)}</li>" for n in notes) + "</ul></div>")
    b.append(_pipeline(p, diffs) + "</section>")
    b.append("<section id='differential'><h2>Differential abundance</h2><p class='sub'>"
             + (escape(s.describe()) if pm is not None else "") + ". Change the cut-offs to explore; click a point or "
             "row for details.</p><div id='differential-body'>"
             "<div class='bar'><label class='ctl'>Comparison <select id='comp'></select></label>"
             "<label class='ctl'>|log2FC| ≥ <input type='number' id='lfc' step='0.1' min='0'></label>"
             "<label class='ctl'>p ≤ <input type='number' id='alpha' step='0.01' min='0' max='1'></label>"
             "<label class='ctl'><input type='checkbox' id='adj'> adjusted</label>"
             "<label class='ctl'>labels <input type='number' id='labels' min='0' max='200' value='" + str(s.top_labels) + "'></label>"
             "<input type='search' id='search' placeholder='Find gene, protein or description'>"
             "<span><button id='volc' class='on'>Volcano</button> <button id='ma'>MA</button></span>"
             "<button id='reset' title='Back to the saved cut-offs'>Reset</button><span id='hl'></span></div>"
             "<div class='muted' id='cutnote'></div>"
             "<div class='split'><div><div class='card chart' id='volcano'></div><div id='vcount' class='meta'></div>"
             "<div class='chips' id='pins'></div></div>"
             "<div class='card detail' id='detail'></div></div>"
             "<div class='row' style='display:flex;gap:12px;align-items:center;margin-top:10px'>"
             "<label class='ctl'><input type='checkbox' id='sigonly' checked> significant only</label>"
             "<button id='csv'>Download CSV</button></div><div id='table'></div>"
             "<h3>p-value distribution</h3><p class='sub'>Mostly flat with a peak near 0 when there are real "
             "differences; a hump in the middle hints at a problem with the model or the data.</p>"
             "<div class='card chart' id='phist' style='max-width:520px'></div></div></section>")
    b.append("<section id='heat'><h2>Heatmap of significant features</h2><p class='sub'>Each row centred on its "
             "mean, so colour shows where a feature is high or low across samples.</p>"
             "<div class='card chart' id='heatmap'></div><div class='legend' id='heatlegend'></div></section>")
    b.append("<section id='enrichment'><h2>Enrichment</h2><div id='enrich'></div></section>")
    b.append("<section id='quality'><h2>Quality control</h2><div id='qc'></div></section>")
    method = ctx.get("method", "")
    b.append(f"<section id='methods'><h2>Methods</h2><p class='methods'>"
             f"{methods_text(pm, p, diffs, s, method, ctx.get('fragpipe', ''), enrichment)}</p>")
    b.append("<h3>Settings used</h3>" + _settings_table(s, p))
    if any(f.startswith("fragpipe-analyst/") for f in files):
        b.append("<h3>Cross-check in FragPipe-Analyst</h3><p class='sub'>The folder <a href='fragpipe-analyst/'>"
                 "fragpipe-analyst/</a> holds an experiment_annotation.tsv for the quant table, so the same data can be "
                 "opened in FragPipe-Analyst, and reproduce_in_R.R, which repeats this analysis with FragPipeAnalystR.</p>")
    b.append("</section>")
    if files:
        b.append("<section id='files'><h2>Files</h2><ul class='files'>" + "".join(
            f"<li><a href='{escape(f)}'>{escape(f)}</a></li>" for f in files) + "</ul></section>")
    b.append("</main><div id='tip'></div>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><meta name='generator' content='{MARKER}'>"
            f"<title>{escape(title)} — Ionomos report</title><style>{_asset('report.css')}</style></head><body>"
            f"{''.join(b)}<script id='ionomos-data' type='application/json'>{data}</script>"
            f"<script>{_asset('report.js')}</script></body></html>")
