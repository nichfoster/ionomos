"""
results/report.html — one self-contained, interactive page per experiment.

The page carries its data as JSON and draws everything in the browser
(assets/report.js + report.css, inlined): no internet, no other files needed.

    Overview       tiles, key findings (top hits, on/off features, pathways, sample QC verdict), issues,
                   notes, the processing pipeline (loaded -> filtered -> imputed -> tested)
    Differential   volcano / MA plot with live cut-offs, zoom or box-select, plot options, PNG/SVG export;
                   search by gene lists, wildcards, regex, gene-set terms, with suggestions; saved highlight
                   groups; flags for imputation-driven and single-peptide hits; click a protein for its values
                   per condition, its statistics in every comparison and the proteins that behave like it;
                   sortable, paginated table (sites or proteins) with CSV export; p-value histogram with pi0
    Compare        (2+ comparisons) fold change against fold change, four quadrants; UpSet of the hit sets
    Only in one    features measured in one group and never in the other (the hits a t-test can't see)
    Heatmap        significant features, row-centred, clustered
    Enrichment     over-represented gene sets among the hits, and a rank-based test on every protein
    QC             sample scorecard, PCA (with what explains each PC), correlation, missing values, missingness
                   against intensity, distributions, CV, mean-variance, abundance rank, identifications,
                   imputation, power (minimum detectable fold change against replicates)
    Methods        a paragraph ready for a notebook, the exact settings, and FragPipe-Analyst export files
    Help           what each section shows (also behind a "?" beside each title and QC tab), a glossary, and
                   what to do about the issues found in this report (content: ionomos/help/*.md)

The page state (comparison, cut-offs, search) is kept in the address (#...), so a link or a bookmark
reopens the same view. The static volcano_*.svg files next to it are for slides and for viewing without scripts.
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
            files: list[str], s: Settings, qcd: dict, enrichment: list[dict], ranked: list[dict] | None = None,
            insight: dict | None = None) -> dict:
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
        "qc": {}, "enr": [], "enrNote": "", "gsea": [], "evidence": "", "rep": [],
        "help": _help_payload(ctx.get("issues")),
    }
    if pm is None:
        return d
    d["f"] = {"id": [f.id for f in pm.features], "label": [f.label for f in pm.features],
              "desc": [f.description for f in pm.features]}
    if any(f.peptides is not None for f in pm.features):
        d["f"]["pep"] = [f.peptides for f in pm.features]
        d["evidence"] = pm.meta.get("evidence") or "peptides"
    d["rep"] = [pm.replicate.get(x) for x in pm.samples]
    insight = insight or {}
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
        rank = dr.confidence == "none" and (dr.control is None or all(v is None for v in cols["a"]))
        if rank:  # fold change only with no abundance (ratio data, a bare fold-change table): plot against rank
            order = sorted((i for i in range(n) if cols["fc"][i] is not None), key=lambda i: cols["fc"][i])
            for k, i in enumerate(order, 1):
                cols["a"][i] = k
        ph = (insight.get("phist") or {}).get(dr.name) or {}
        onoff = [[x["index"], "t" if x["only_in"] == "treatment" else "c", x["detected"], x["of"]]
                 for x in (insight.get("onoff") or {}).get(dr.name, [])]
        d["comps"].append({"name": dr.name, "slug": dr.slug(), "t1": dr.treatment, "t2": dr.control,
                           "conf": dr.confidence, "confNote": dr.confidence_note, "aRank": rank, **cols,
                           "pi0": _r(ph.get("pi0"), 3), "pshape": ph.get("shape", ""), "onoff": onoff})
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
        d["qc"].update(_insight_payload(insight))
        hm = qcd.get("heatmap")
        if hm:
            d["qc"]["heatmap"] = {"rows": hm["rows"], "cols": hm["cols"], "total_significant": hm["total_significant"],
                                  "values": [[_r(v, 3) for v in row] for row in hm["values"]]}
    d["enr"] = [{**b, "terms": [{**t, "p": _r(t["p"], 8), "q": _r(t["q"], 8),
                                 "log2_odds": (_r(t["log2_odds"], 3) if math.isfinite(t["log2_odds"]) else "Inf")}
                                for t in b["terms"]]} for b in enrichment]
    d["gsea"] = [{"comparison": b["comparison"], "library": b["library"], "tested": b["tested"],
                  "adjusted": b.get("adjusted", False),
                  "terms": [{"term": t["term"], "n": t["n"], "z": _r(t["z"], 3), "p": _r(t["p"], 8), "q": _r(t["q"], 8),
                             "dir": t["direction"], "median": _r(t["median"], 3), "corr": _r(t["corr"], 3),
                             "leading": t["leading"], "genes": t["genes"][:300]} for t in b["terms"]]}
                 for b in ranked or [] if b["terms"]]
    if not enrichment:
        why = "; ".join(ctx.get("enrichment_notes") or [])
        d["enrNote"] = ("Enrichment is off (Analysis tab)." if not s.enrichment else
                        (why + "." if why else "Enrichment needs at least one comparison with hits."))
    elif ctx.get("enrichment_notes"):
        d["enrNote"] = "; ".join(ctx["enrichment_notes"])
    return d


def _help_payload(issues) -> dict:
    """The plain-language help the page shows (ionomos/help: its sections, QC tabs, the glossary and this
    report's issues). Help must never cost a report, so any problem leaves it out."""
    try:
        from ionomos import help as helpdoc

        return helpdoc.report_payload(issues or [])
    except Exception:  # noqa: BLE001
        return {}


def _insight_payload(ins: dict) -> dict:
    """insights.py results, rounded, for d["qc"]."""
    out: dict = {}
    card = ins.get("scorecard") or []
    if card:
        out["scorecard"] = [{k: (_r(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in card]
    pcs = ins.get("pcs") or {}
    if pcs.get("pcs"):
        out["pcs"] = {"pcs": [{k: _r(v, 4) for k, v in x.items()} for x in pcs["pcs"]],
                      "batch": {k: _r(v, 4) for k, v in pcs["batch"].items()} if pcs.get("batch") else None}
    miss = ins.get("missingness") or {}
    if miss.get("bins"):
        out["mnar"] = {"bins": [{k: _r(v, 4) for k, v in b.items()} for b in miss["bins"]], "rho": _r(miss.get("rho"), 3),
                       "gap": _r(miss.get("gap"), 3), "verdict": miss.get("verdict", ""),
                       "incomplete": miss.get("incomplete", 0), "features": miss.get("features", 0)}
    pw = ins.get("power") or {}
    if pw.get("curves"):
        out["power"] = {"n": pw["n"], "sd": {k: _r(v, 4) for k, v in pw["sd"].items()}, "moderated": pw["moderated"],
                        "current": pw["current_n"], "beta": pw["beta"],
                        "curves": {a: [None if r is None else {k: _r(v, 4) for k, v in r.items()} for r in rows]
                                   for a, rows in pw["curves"].items()}}
    return out


# --------------------------------------------------------------------- text --


def _source_sentence(method: str, fragpipe_note: str, engine: dict) -> str:
    """Who produced the numbers: FragPipe run by Ionomos, or another engine's results read by it (engines.py)."""
    name = engine.get("engine") or ("FragPipe" if fragpipe_note or method in ("isoDTB", "TMT", "DIA", "LFQ") else "")
    ver = f" {engine['version']}" if engine.get("version") else ""
    tools = ", ".join(f"{k} {v}" for k, v in (engine.get("tools") or {}).items())
    if name == "FragPipe":
        extra = "; ".join(x for x in (fragpipe_note, tools) if x)
        if fragpipe_note:
            return (f"Raw files were searched with FragPipe{escape(ver)}{(' (' + escape(extra) + ')') if extra else ''} "
                    f"using the lab's pinned {escape(method)} workflow, run automatically by Ionomos.")
        return f"Raw files were searched with FragPipe{escape(ver)}{(' (' + escape(tools) + ')') if tools else ''}."
    what = f"{name}{ver}" if name and name != "a table" else "a results table"
    table = f" ({engine['table']})" if engine.get("table") else ""
    qty = f", using {engine['quantity']}" if engine.get("quantity") else ""
    fdr = f" at {engine['fdr']}" if engine.get("fdr") else ""
    return f"Quantities from {escape(what)}{escape(table)} were read by Ionomos{escape(qty)}{escape(fdr)}."


def methods_text(m: QuantMatrix | None, p: fpa.Processed | None, diffs: list[DiffResult], s: Settings, method: str,
                 fragpipe_note: str, enrichment: list[dict], ranked: list[dict] | None = None,
                 engine: dict | None = None) -> str:
    parts = [_source_sentence(method, fragpipe_note, engine or {})]
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
    if m.kind == "intensity" and diffs:
        parts.append("Features measured in at least 75% (and at least two) of the samples of one group and in none "
                     "of the other were listed separately as present/absent; their t-test p-values, where any, rest "
                     "on imputed values.")
    if enrichment:
        libs = sorted({b["library"] for b in enrichment})
        parts.append("Over-representation of up- and down-regulated genes in gene sets (" + escape(", ".join(libs)) +
                     "; Enrichr libraries) was tested with a one-sided hypergeometric test against all quantified "
                     "genes, BH-adjusted.")
    if ranked:
        parts.append("Coordinated shifts of whole gene sets were tested on every quantified gene, ranked by the "
                     "moderated t-statistic, with a Wilcoxon rank-sum test whose variance is inflated by the set's "
                     "inter-gene correlation (the approach of limma's camera), BH-adjusted.")
    parts.append("Sample quality was scored by identifications, correlation with replicates, spread around the group "
                 "mean and leave-one-out CVs (robust z-scores); principal components were related to condition and "
                 "replicate number (one-way ANOVA R²), and the share of unchanged features estimated from the "
                 "p-value distribution (Storey's π0).")
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


def _provenance_table(engine: dict) -> str:
    """Where the numbers came from (engines.provenance): an audit trail printed with every report."""
    rows = [("Engine", engine.get("engine", "")), ("Version", engine.get("version", "") or engine.get("note", "")),
            ("Tools", ", ".join(f"{k} {v}" for k, v in (engine.get("tools") or {}).items())),
            ("Result table", engine.get("table", "")), ("Quantity", engine.get("quantity", "")),
            ("FDR filter", engine.get("fdr", "")), ("FASTA", engine.get("fasta", "")),
            ("Parameter / log files", ", ".join(engine.get("files") or []))]
    rows = [(k, v) for k, v in rows if v]
    if not rows:
        return ""
    return "<h3>Data source</h3><div class='kv card'>" + "".join(
        f"<div>{escape(k)}</div><div>{escape(str(v))}</div>" for k, v in rows) + "</div>"


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


def _sdrf_note(info: dict) -> str:
    """Methods → Sample metadata: what sdrf.tsv is and what to fill in before depositing it."""
    if not info.get("file"):
        return ""
    name = info["file"].split("/")[-1]
    text = (f"<a href='{escape(name)}'>{escape(name)}</a> describes the samples and raw files in SDRF-Proteomics "
            f"({escape(info.get('spec', ''))}, {info.get('rows', 0)} rows, {info.get('data_files', 0)} raw files), "
            "the sample sheet PRIDE and reanalysis pipelines read.")
    if info.get("fill_in"):
        text += (" Fill in " + escape(", ".join(info["fill_in"])) + " before depositing it (\"not available\" "
                 "there fails the validator); set them once in the lab's analysis settings (analysis.sdrf).")
    return f"<h3>Sample metadata (SDRF)</h3><p class='sub'>{text}</p>"


def render(ctx: dict, m: QuantMatrix | None, p: fpa.Processed | None, diffs: list[DiffResult], notes: list[str],
           files: list[str], s: Settings | None = None, qcd: dict | None = None,
           enrichment: list[dict] | None = None, ranked: list[dict] | None = None, insight: dict | None = None) -> str:
    s = s or Settings()
    enrichment = enrichment or []
    ranked = [b for b in ranked or [] if b["terms"]]
    title = ctx.get("experiment") or "Experiment"
    meta = " · ".join(x for x in (ctx.get("user"), ctx.get("method"), ctx.get("date"),
                                  f"generated {datetime.now():%Y-%m-%d %H:%M}", f"Ionomos {ctx.get('version', '')}") if x)
    data = json.dumps(payload(ctx, m, p, diffs, notes, files, s, qcd or {}, enrichment, ranked, insight),
                      separators=(",", ":"), allow_nan=False).replace("</", "<\\/").replace("<!--", "<\\u0021--")
    pm = p.m if p else m
    ratio = pm is not None and pm.kind == "ratio"
    b = [f"<main><div class='top'><div><h1>{escape(title)}</h1><div class='meta'>{escape(meta)}</div></div>"
         "<div><button id='theme' title='Light / dark'>◐</button> <button id='share' title='Copy a link to this view "
         "(comparison, cut-offs, search)'>Link</button> <button onclick='window.print()'>Print</button></div></div>",
         "<nav class='toc'><a href='#overview'>Overview</a><a href='#differential'>Differential</a>"
         + "<a href='#compare' id='navcompare'" + ("" if len(diffs) >= 2 else " hidden") + ">Compare</a>"
         + ("" if ratio else "<a href='#onoff'>Only in one</a>")
         + "<a href='#heat'>Heatmap</a><a href='#enrichment'>Enrichment</a><a href='#quality'>Quality control</a>"
         "<a href='#methods'>Methods</a><a href='#files'>Files</a><a href='#help'>Help</a></nav>",
         "<noscript><div class='notes'>This report draws its charts with JavaScript. The volcano_*.svg and *.tsv "
         "files in this folder hold the same results.</div></noscript>",
         "<section id='overview'><div class='tiles' id='tiles'></div><div id='findings'></div>"]
    b.append(issues_html(ctx.get("issues") or []))
    if notes:
        b.append("<details class='notes'><summary><b>Notes</b> (" + str(len(notes)) + ")</summary><ul>" +
                 "".join(f"<li>{escape(n)}</li>" for n in notes) + "</ul></details>")
    b.append(_pipeline(p, diffs) + "</section>")
    b.append("<section id='differential'><h2>Differential abundance</h2><p class='sub'>"
             + (escape(s.describe()) if pm is not None else "") + ". Change the cut-offs to explore; click a point or "
             "row for details. Search takes a gene, a pasted list, <code>KRT*</code>, <code>/^RPL\\d/</code>, "
             "<code>desc:kinase</code> or <code>term:apoptosis</code>; press <kbd>/</kbd> to jump to it.</p>"
             "<div id='differential-body'>"
             "<div class='bar'><label class='ctl'>Comparison <select id='comp'></select></label>"
             "<label class='ctl'>|log2FC| ≥ <input type='number' id='lfc' step='0.1' min='0'></label>"
             "<label class='ctl'>p ≤ <input type='number' id='alpha' step='0.01' min='0' max='1'></label>"
             "<label class='ctl'><input type='checkbox' id='adj'> adjusted</label>"
             "<div class='searchbox'><input type='search' id='search' autocomplete='off' spellcheck='false' "
             "placeholder='Find genes, proteins, a list, KRT*, term:…' aria-label='Search' aria-autocomplete='list'>"
             "<div id='suggest' class='suggest' role='listbox' hidden></div></div>"
             "<span class='seg'><button id='volc' class='on'>Volcano</button><button id='ma'>MA</button></span>"
             "<span class='seg' title='What dragging on the plot does'><button id='dzoom' class='on'>Zoom</button>"
             "<button id='dsel'>Select</button></span>"
             "<button id='opts' aria-expanded='false'>Options</button>"
             "<button id='reset' title='Back to the saved cut-offs'>Reset</button><span id='hl'></span></div>"
             "<div id='optpanel' class='card optpanel' hidden>"
             "<div><h4>Plot</h4>"
             "<label class='ctl'>labels <input type='number' id='labels' min='0' max='200' value='" + str(s.top_labels) + "'></label>"
             "<label class='ctl'>point size <input type='range' id='ptsize' min='0.5' max='2.5' step='0.1' value='1'></label>"
             "<label class='ctl'>label size <input type='range' id='labsize' min='8' max='18' step='0.5' value='11.5'></label>"
             "<label class='ctl'><input type='checkbox' id='labmatch' checked> label search matches</label>"
             "<label class='ctl'><input type='checkbox' id='lines' checked> cut-off lines</label>"
             "<label class='ctl'><input type='checkbox' id='markonoff' checked> mark on/off features (▲)</label></div>"
             "<div><h4>Hits</h4>"
             "<label class='ctl' title='A hit whose group has at least half of its values imputed is not counted'>"
             "<input type='checkbox' id='hideimp'> ignore imputation-driven hits</label>"
             "<label class='ctl' id='minpepwrap'>at least <input type='number' id='minpep' min='0' max='20' value='0'> "
             "<span id='evword'>peptides</span></label>"
             "<p class='muted'>Filtered hits are drawn grey with a coloured ring, so you can see what the filter removed.</p></div>"
             "<div><h4>Highlight groups</h4><div id='groups'></div>"
             "<input type='text' id='gname' placeholder='Group name'>"
             "<textarea id='ggenes' rows='3' placeholder='Genes or proteins (any separator)'></textarea>"
             "<div class='chips'><button id='gadd'>Add group</button><button id='gexport'>Export .gmt</button>"
             "<label class='filebtn'>Import .gmt / .txt<input type='file' id='gimport' accept='.gmt,.txt,.tsv,.csv'></label></div>"
             "<p class='muted'>Groups are kept in this browser and show in every Ionomos report.</p></div></div>"
             "<div class='muted' id='searchinfo'></div>"
             "<div class='muted' id='cutnote'></div>"
             "<div class='split'><div><div class='card chart' id='volcano'></div><div id='vcount' class='meta'></div>"
             "<div class='legend' id='glegend'></div><div class='chips' id='pins'></div></div>"
             "<div class='card detail' id='detail'></div></div>"
             "<div class='row tablebar'>"
             "<label class='ctl'><input type='checkbox' id='sigonly' checked> significant only</label>"
             + ("<span class='seg'><button id='rsites' class='on'>Sites</button><button id='rprot'>Proteins</button></span>"
                if ratio else "")
             + "<button id='csv'>Download CSV</button><button id='copyup'>Copy up genes</button>"
             "<button id='copydown'>Copy down genes</button><span id='copied' class='muted'></span></div><div id='table'></div>"
             "<h3>p-value distribution</h3><p class='sub'>Mostly flat with a peak near 0 when there are real "
             "differences; a pile-up near 1 or a hump in the middle hints at a problem with the model or the data.</p>"
             "<div class='card chart' id='phist' style='max-width:560px'></div><div id='pinfo' class='meta'></div>"
             "</div></section>")
    b.append("<section id='compare'" + ("" if len(diffs) >= 2 else " hidden") + "><h2>Compare comparisons</h2>"
             "<p class='sub'>Which changes are shared, which are specific. Fold change in one comparison against "
             "another (each quadrant tells a story), and the overlap of the hit lists at the current cut-offs.</p>"
             "<div id='comparebody'></div></section>")
    if not ratio:
        b.append("<section id='onoff'><h2>Only in one condition</h2><p class='sub'>Measured in at least 75% (and at "
                 "least two) of one group's samples and in none of the other: often the strongest biology, and "
                 "invisible to a t-test without imputation. Missing can also mean below detection, so confirm them.</p>"
                 "<div id='onoffbody'></div></section>")
    b.append("<section id='heat'><h2>Heatmap of significant features</h2><p class='sub'>Each row centred on its "
             "mean, so colour shows where a feature is high or low across samples.</p>"
             "<div class='card chart' id='heatmap'></div><div class='legend' id='heatlegend'></div></section>")
    b.append("<section id='enrichment'><h2>Enrichment</h2><div id='enrich'></div></section>")
    b.append("<section id='quality'><h2>Quality control</h2><div id='qc'></div></section>")
    method = ctx.get("method", "")
    b.append(f"<section id='methods'><h2>Methods</h2><p class='methods'>"
             f"{methods_text(pm, p, diffs, s, method, ctx.get('fragpipe', ''), enrichment, ranked, ctx.get('engine'))}</p>")
    b.append(_provenance_table(ctx.get("engine") or {}))
    b.append("<h3>Settings used</h3>" + _settings_table(s, p))
    if any(f.startswith("fragpipe-analyst/") for f in files):
        b.append("<h3>Cross-check in FragPipe-Analyst</h3><p class='sub'>The folder <a href='fragpipe-analyst/'>"
                 "fragpipe-analyst/</a> holds an experiment_annotation.tsv for the quant table, so the same data can be "
                 "opened in FragPipe-Analyst, and reproduce_in_R.R, which repeats this analysis with FragPipeAnalystR.</p>")
    b.append(_sdrf_note(ctx.get("sdrf") or {}))
    b.append("</section>")
    if files:
        b.append("<section id='files'><h2>Files</h2><ul class='files'>" + "".join(
            f"<li><a href='{escape(f)}'>{escape(f)}</a></li>" for f in files) + "</ul></section>")
    b.append("<section id='help'><h2>Help</h2><p class='sub'>What each part of this report shows, what the words "
             "mean, and what to do about the issues found here. A <b>?</b> beside a title opens its part.</p>"
             "<div id='helpbody'></div></section>")
    b.append("</main><div id='tip'></div>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><meta name='generator' content='{MARKER}'>"
            f"<title>{escape(title)} — Ionomos report</title><style>{_asset('report.css')}</style></head><body>"
            f"{''.join(b)}<script id='ionomos-data' type='application/json'>{data}</script>"
            f"<script>{_asset('report.js')}</script></body></html>")


SEVERITY_LABEL = {"error": "Problem", "input": "Needs your decision", "warning": "Worth knowing"}


def issues_html(issues: list[dict]) -> str:
    """The doctor's findings, most serious first, each with likely causes and what to do."""
    if not issues:
        return ""
    order = {"error": 0, "input": 1, "warning": 2}
    out = ["<div class='issues'>"]
    for i in sorted(issues, key=lambda x: order.get(x.get("severity"), 3)):
        sev = i.get("severity", "warning")
        causes = "".join(f"<li>{escape(c)}</li>" for c in i.get("causes") or [])
        fixes = "".join(f"<li>{escape(c)}</li>" for c in i.get("fixes") or [])
        out.append(f"<div class='issue {escape(sev)}'><div class='sev'>{SEVERITY_LABEL.get(sev, sev)}</div>"
                   f"<b>{escape(i.get('title', ''))}</b><div>{escape(i.get('message', ''))}</div>"
                   + (f"<div class='cz'>Most likely:<ul>{causes}</ul></div>" if causes else "")
                   + (f"<div class='cz'>What to do:<ul>{fixes}</ul></div>" if fixes else "") + "</div>")
    out.append("</div>")
    return "".join(out)


def fallback(ctx: dict, diffs: list[DiffResult], notes: list[str], files: list[str]) -> str:
    """A plain page for when the full report couldn't be made: issues, notes, and every volcano plot."""
    from ionomos.downstream import charts

    title = ctx.get("experiment") or "Experiment"
    body = [f"<main><h1>{escape(title)}</h1><div class='meta'>Ionomos {escape(str(ctx.get('version', '')))} — "
            "simplified report (the full interactive report could not be made; see the issues below)</div>",
            issues_html(ctx.get("issues") or [])]
    if notes:
        body.append("<div class='notes'><ul>" + "".join(f"<li>{escape(n)}</li>" for n in notes) + "</ul></div>")
    for d in diffs:
        try:
            svg = charts.volcano(d)
        except Exception:  # noqa: BLE001
            svg = "<p>(plot unavailable)</p>"
        body.append(f"<h2>{escape(d.name)}</h2><p>{d.up} up, {d.down} down of {d.tested} tested</p>"
                    f"<div class='card chart'>{svg}</div>")
    body.append("<h2>Files</h2><ul>" + "".join(f"<li><a href='{escape(x)}'>{escape(x)}</a></li>" for x in files) +
                "</ul></main>")
    try:
        css = _asset("report.css")
    except OSError:
        css = ""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{escape(title)} — Ionomos report"
            f"</title><style>{css}{charts.STYLE}</style></head><body>{''.join(body)}</body></html>")
