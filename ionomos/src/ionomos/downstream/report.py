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
    Specific targets (a competition experiment, roles.py) per compound: enrichment against the control on one
                   axis, how much the competitor takes off on the other, the quadrant of specific binders marked
    Only in one    features measured in one group and never in the other (the hits a t-test can't see)
    Heatmap        significant features, row-centred, clustered
    Enrichment     over-represented gene sets among the hits, and a rank-based test on every protein
    Dose-response  (a titration) CurveCurator's curves: a table by class, each curve over its points, potency vs effect
    Time course    (3+ time points) change over time (limma's F), trend, patterns of the changing features, each
                   feature's profile over its replicates
    Liganded sites (isoDTB) per compound the liganded fraction and a ratio rank plot; a site x compound table of
                   competition ratios with the call, selectivity and, with a site annotation, known / new
    QC             sample scorecard, PCA (with what explains each PC), correlation, missing values, missingness
                   against intensity, distributions, CV, mean-variance, abundance rank, identifications,
                   imputation, power (minimum detectable fold change against replicates), search quality per
                   run (psm.tsv: PSMs, precursor mass error, missed cleavages, charge states, peptide length)
    Methods        a paragraph ready for a notebook, the exact settings, and FragPipe-Analyst export files
    Help           what each section shows (also behind a "?" beside each title and QC tab), a glossary, and
                   what to do about the issues found in this report (content: ionomos/help/*.md)

Every chart has SVG / PNG / Export… buttons, and the top bar has "Export for slides": one export style (size,
text, colours, title, legend; kept in the browser, saved and loaded as a JSON file, starting from the lab's
analysis.export in the payload's exportDefaults) for single figures, the clipboard, and a .zip of every
figure with the tables and a README (report.js "figure export"; D62).

The page state (comparison, cut-offs, search) is kept in the address (#...), so a link or a bookmark
reopens the same view. The static volcano_*.svg files next to it are for slides and for viewing without scripts.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from html import escape
from importlib import resources
from pathlib import Path

from ionomos.downstream import fpa, qc, trust
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
            insight: dict | None = None, dose: dict | None = None, cys: dict | None = None,
            time: dict | None = None, psm: dict | None = None, phos: dict | None = None) -> dict:
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
                     "normalize": (p.normalization.get("used") if p and p.normalization else None) or s.normalize,
                     "test": s.test},
        "imputationLabel": fpa.IMPUTATION_LABELS.get(p.imputation, "") if p else "",
        "qc": {}, "enr": [], "enrNote": "", "gsea": [], "evidence": "", "rep": [],
        "dose": dose or {"ran": False, "found": False, "reason": "No dose-response curves were fitted."},
        "cys": cys or {"ran": False, "reason": ""},
        "time": time or {"ran": False, "found": False, "reason": "No time course was found."},
        "phos": phos or {"ran": False},
        "help": _help_payload(ctx.get("issues")),
        "roles": _roles_payload(ctx, diffs),
        "exportDefaults": _export_defaults(s),
    }
    if psm:  # search quality per run (psmqc.py): shown even when there are no quantities
        d["qc"]["psm"] = psm
    if pm is None:
        return d
    d["f"] = {"id": [f.id for f in pm.features], "label": [f.label for f in pm.features],
              "desc": [f.description for f in pm.features]}
    if any(f.peptides is not None for f in pm.features):
        d["f"]["pep"] = [f.peptides for f in pm.features]
        d["evidence"] = pm.meta.get("evidence") or "peptides"
    d["rep"] = [pm.replicate.get(x) for x in pm.samples]
    plexes = pm.meta.get("plex") or {}
    if len({plexes.get(x) for x in pm.samples} - {None}) > 1:  # TMT plexes: the PCA can colour by them (D48)
        d["plex"] = [plexes.get(x) for x in pm.samples]
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
                           "size": list(dr.groups), "kind": dr.role,
                           "pi0": _r(ph.get("pi0"), 3), "pshape": ph.get("shape", ""), "onoff": onoff})
    ft = ctx.get("ftest")
    if ft is not None and len(ft.q) == n:  # the moderated F (3+ conditions): "any change" tile + table column
        d["F"] = {"f": [_r(x, 4) for x in ft.f], "p": [_r(x, 8) for x in ft.p], "q": [_r(x, 8) for x in ft.q],
                  "df1": ft.df1, "ref": ft.reference, "conds": ft.conditions}
    if qcd:
        cv = {c: {"hist": qc.histogram(v["cvs"], 0.0, 1.0, 20), "median": _r(v["median"], 4)}
              for c, v in (qcd.get("cv") or {}).items()}
        pca = qcd.get("pca") or {}
        corr = qcd.get("correlation") or {}
        d["qc"] = {
            **d["qc"],
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
        before = qcd.get("pca_before") or {}
        if before.get("scores"):  # the same PCA before the plexes were put on one scale (plex.py)
            d["qc"]["pcaBefore"] = {"scores": [[_r(x, 4) for x in row] for row in before["scores"]],
                                    "percent": [_r(x, 2) for x in before.get("percent", [])], "n": before.get("n", 0),
                                    "label": (pm.meta.get("bridge") or {}).get("method", "plex normalisation")}
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


def _roles_payload(ctx: dict, diffs: list[DiffResult]) -> dict:
    """The conditions' roles and the specific-targets views (roles.py). Never costs a report."""
    try:
        from ionomos.downstream import roles

        return roles.report_payload(ctx.get("roles"), ctx.get("specific") or [], diffs)
    except Exception:  # noqa: BLE001
        return {}


def _export_defaults(s: Settings) -> dict:
    """The lab's export style (analysis.export), complete, for the report's Export dialog to start from."""
    try:
        from ionomos.downstream import charts

        return charts.style_from(s.export, lenient=True)
    except Exception:  # noqa: BLE001 - a style must never cost a report: the page has its own defaults
        return {}


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
        if pw.get("comparisons"):  # each comparison with the samples it has (unequal groups, D61)
            out["power"]["comparisons"] = [
                {"name": c["name"], "n": c["n"], "df": _r(c["df"], 1), "balanced": _r(c["balanced_n"], 2),
                 "mdfc": {a: None if r is None else {k: _r(v, 4) for k, v in r.items()} for a, r in c["mdfc"].items()}}
                for c in pw["comparisons"]]
            out["power"]["unbalanced"] = bool(pw.get("unbalanced"))
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
                 engine: dict | None = None, model=None, ftest=None, roles=None, specific=None) -> str:
    """model: analysis.Model (the design and variance prior used); ftest: design.FTest (3+ conditions);
    roles: roles.Plan and specific: [roles.Specific] (a competition experiment, D61)."""
    parts = [_source_sentence(method, fragpipe_note, engine or {})]
    if m is None:
        return " ".join(parts)
    if m.kind == "ratio":
        parts.append(f"Labelled peptides were merged to {m.level}s (mean log2 heavy/light ratio per replicate, as in "
                     "the lab's isoDTB site script). For each site, the replicate ratios were tested against 0 with "
                     + ("a moderated one-sample t-test (limma: lmFit, eBayes)." if s.test == "limma"
                        else "a two-sided one-sample t-test.")
                     + " This is a competition experiment by construction: each condition is a compound competing "
                     "with the probe, and its ratio compares it with the vehicle in the same run.")
        parts += _ratio_sentences(p, diffs)
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
        used = (p.normalization.get("used") if p and p.normalization else None) or s.normalize
        if used in ("median", "gn"):
            steps.append({"median": "samples median-centred", "gn": "samples median-centred and MAD-scaled"}[used])
        elif used == "ratio":
            comp = (p.normalization.get("composition") or {}) if p else {}
            steps.append("samples normalised on feature ratios (each sample shifted by the median, over the "
                         f"{comp.get('features', 0):,} features with the most stable profile among those measured in "
                         "every sample, of the feature's value minus its mean across samples)"
                         + (", because median centring would have shifted the conditions against each other by "
                            f"{comp['shift']:.2f} log2" if s.normalize == "auto" and comp.get("shift") else ""))
        imp = p.imputation if p else "none"
        if imp == "perseus":
            steps.append(f"missing values imputed from a normal distribution down-shifted by {s.impute_shift:g} SD "
                         f"with {s.impute_scale:g}× the SD of each sample (Perseus-type)")
        elif imp != "none":
            steps.append(f"missing values imputed ({fpa.IMPUTATION_LABELS[imp]})")
        parts.append(", ".join(steps) + ".")
        parts += _design_sentences(m)
        des = getattr(model, "design", None)
        prior = getattr(model, "prior", None) or {}
        if s.test == "limma" and des is None:
            parts.append("Differential abundance was tested with limma (linear model ~0 + condition, empirical-Bayes "
                         "moderated t-statistics, 95% confidence intervals), following FragPipe-Analyst's test_limma.")
        elif s.test == "limma":
            parts.append(f"Differential abundance was tested with limma: a linear model {escape(des.formula)}, with "
                         f"{escape(des.describe())} (fixed effects), fitted per feature with missing values dropped "
                         "(lmFit), each comparison a contrast between conditions (contrasts.fit), with empirical-Bayes "
                         "moderated t-statistics and 95% confidence intervals (eBayes).")
        if s.test == "limma" and getattr(model, "problem", ""):
            parts.append(f"The requested design could not be used ({escape(model.problem)}), so the comparisons use "
                         "~0 + condition.")
        if s.test == "limma" and prior.get("deqms_used"):
            parts.append(f"Each feature's prior variance came from a loess fit of log residual variance against log2 "
                         f"of its number of {escape(m.meta.get('evidence') or 'peptides')} (DEqMS spectraCounteBayes, "
                         "Zhu et al., "
                         f"Mol. Cell. Proteomics 2020; prior df {prior['d0']:g})"
                         + (f"; {prior['limma_prior_features']:,} features without a count kept limma's single prior"
                            if prior.get("limma_prior_features") else "") + ".")
        if ftest is not None:
            parts.append(f"Whether a feature changes between any of the {len(ftest.conditions)} conditions was tested "
                         f"with limma's moderated F-statistic on the contrasts of every condition against "
                         f"{escape(ftest.reference)} (topTableF, {ftest.df1} numerator df), BH-adjusted.")
        if s.test != "limma":
            parts.append(f"Conditions were compared with a two-sided {TESTS[s.test]}.")
        parts += _roles_sentences(m, p, diffs, s, roles, specific)
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


def _ratio_sentences(p: fpa.Processed | None, diffs: list[DiffResult]) -> list[str]:
    """Methods for site ratios (D70): how they were centred, and the protein-abundance correction."""
    out = []
    centring = ((p.normalization or {}).get("ratio_centre") or {}) if p is not None else {}
    used = centring.get("used", "none")
    if used == "median":
        out.append("Each replicate's site ratios were centred on their median (ratio_centre: median), to remove a "
                   "heavy / light mixing error.")
    elif used == "stable":
        out.append("Replicates whose ratios sat off 0 were centred on their stable sites (ratio_centre: auto): each "
                   "replicate's median over the half of the sites nearest its centre, iterated, applied to a "
                   "condition when a replicate was more than 0.05 log2 and 3 standard errors away from 0.")
    else:
        out.append("The site ratios were not centred (ratio_centre: none).")
    corr = [d for d in diffs if d.correction]
    if corr:
        c = corr[0].correction
        src = escape(Path(c["proteome"]).name or c["proteome"])
        out.append(f"Site changes were also corrected for protein abundance with an unenriched proteome ({src}; "
                   f"proteins matched by {escape(c['match'])}): the protein's log2 fold change, on the heavy / light "
                   "scale, was subtracted from the site's, with SE = √(SE_site² + SE_protein²), Satterthwaite degrees "
                   "of freedom and Benjamini–Hochberg per condition, as MSstatsPTM's adjustment (Kohler et al., Mol. "
                   "Cell. Proteomics 2023). Sites whose protein was not found keep only the uncorrected result.")
    return out


def _roles_sentences(m: QuantMatrix, p, diffs: list[DiffResult], s: Settings, plan, specific) -> list[str]:
    """Methods: the conditions' roles and the comparisons they gave (roles.py), how many samples each side of
    each comparison had, what unequal groups mean for the test, and the specific-targets rule."""
    out = []
    if plan is not None and getattr(plan, "active", False):
        out.append("Each condition was given a role (" + escape(plan.describe()) + "), and the comparisons follow "
                   "that design: each compound against the control (enrichment), each competition against the "
                   "compound it competes (what the competitor displaces) and against the control (what is left).")
        if plan.skipped:
            out.append("Not compared: " + escape("; ".join(f"{w} ({y})" for w, y in plan.skipped)) + ".")
    sized = [d for d in diffs if len(d.groups) == 2]
    if sized:
        out.append("Samples in each comparison: " + escape("; ".join(
            f"{d.name}, {d.groups[0]} against {d.groups[1]}" for d in sized)) + ".")
        if any(d.groups[0] != d.groups[1] for d in sized) and s.test == "limma":
            n, k = len(m.samples), len(m.conditions)
            out.append(f"The groups are not the same size. limma estimates each feature's residual variance from "
                       f"all {n} samples of the {k} conditions ({n - k} residual degrees of freedom, plus the "
                       "prior's), so a comparison with a small group uses the variance of the larger groups too; "
                       "its fold change has the standard error of the two groups it compares, "
                       "s·√(1/n₁ + 1/n₂).")
        if any(d.relaxed for d in sized):
            out.append(f"Without imputation a group needs {s.min_valid} measured values for a feature to be tested; "
                       "the smaller group of an unequal comparison needed half of its samples "
                       "(small_group_min_valid: half), and the number measured is in each table.")
    for sp in specific or []:
        which = "adjusted p" if s.use_adjusted else "p"
        out.append(f"A feature was called a specific target of {escape(sp.compound)} when it was enriched against "
                   f"{escape(sp.control)} (log2FC ≥ {s.log2fc:g}, {which} ≤ {s.alpha:g} in {escape(sp.enrichment)}) "
                   f"and competed off by the competitor (log2FC ≤ −{s.log2fc:g}, {which} ≤ {s.alpha:g} in "
                   f"{escape(sp.displaced)}): {sp.counts['specific']:,} of {sp.counts['tested']:,} tested.")
    return out


def _design_sentences(m: QuantMatrix) -> list[str]:
    """Methods: where the design came from (an input SDRF, D47) and how TMT plexes were joined (D48)."""
    out = []
    sd = m.meta.get("sdrf") or {}
    if sd.get("used"):
        out.append(f"Sample conditions and replicates were taken from the SDRF-Proteomics file {escape(sd['file'])}"
                   + (f" (factor {escape(', '.join(sd['factors']))})" if sd.get("factors") else "") + ".")
    b = m.meta.get("bridge") or {}
    if b.get("applied"):
        n = len(b.get("plexes") or {})
        if b["method"].startswith("MSstatsTMT"):
            out.append(f"The {n} TMT mixtures were normalised to their reference (Norm) channels as in MSstatsTMT "
                       "(Huang et al., Mol Cell Proteomics 2020).")
        elif b["method"] == "IRS (reference channel)":
            out.append(f"The {n} TMT plexes were put on one scale by internal reference scaling (IRS; Plubell et al., "
                       "Mol Cell Proteomics 2017) on the pooled reference channel"
                       f"{'s' if len(b.get('reference') or []) > 1 else ''}, which were then left out.")
        else:
            out.append(f"The {n} TMT plexes were put on one scale by internal reference scaling (IRS; Plubell et al., "
                       "Mol Cell Proteomics 2017) on each plex's mean, as every plex holds the same mix of conditions.")
    return out


def _dose_methods(dose: dict) -> str:
    names = ", ".join(escape(x["name"] or "the compound") for x in dose.get("series") or [])
    return (f"Dose-response curves ({names}) were fitted per feature as in CurveCurator (Bayer et al., Nat. Commun. "
            "2023, doi:10.1038/s41467-023-43696-z): values as ratios to the mean of the control, a 4-parameter "
            "log-logistic model by least squares within CurveCurator's bounds, significance from its recalibrated "
            f"F-statistic, and up / down / not classes from the relevance score (alpha {dose.get('alpha', 0.05):g}, "
            f"|log2 curve fold change| ≥ {dose.get('fcLim', 0.45):g}). 95% intervals for pEC50 are from the fit's "
            "Jacobian; q-values are Benjamini–Hochberg on the curve p-values.")


def _time_methods(time: dict) -> str:
    names = ", ".join(escape(x["name"] or "the experiment") + " (" + escape(", ".join(x["labels"])) + ")"
                      for x in time.get("series") or [])
    vs = sorted({x["vs"] for x in time.get("series") or [] if x.get("vs")})
    return (f"Time courses: {names}. Time was a factor in the comparisons' linear model "
            f"({escape(time.get('model', '~0 + condition'))}; limma User's Guide, time course experiments). Change "
            "over time was tested per feature with the moderated F-statistic on the contrasts of every time point "
            "against the first, and a trend with the moderated t-statistic of the linear contrast over the ordered "
            "time points, both BH-adjusted."
            + (f" Whether a series responds differently from {escape(', '.join(vs))} was tested with the moderated "
               "F on the interaction contrasts (the change from the first time point in one series minus that in "
               "the other)." if vs else "")
            + f" Features with F adjusted p ≤ {time.get('alpha', 0.05):g} and a largest |log2 fold change| ≥ "
            f"{time.get('lfc', 1):g} against the first time point were grouped into patterns by k-means on their "
            "profiles scaled to the largest change.")


def _cys_methods(cys: dict) -> str:
    ann = cys.get("annotation") or {}
    return (f"A cysteine was called liganded by a compound when its competition ratio reached {escape(cys['rule'])} "
            "(never imputed); sites reaching it in fewer replicates "
            "were called inconsistent, and sites measured in fewer replicates than that were not assessed. A site "
            "was called selective when one compound liganded it and every other compound was measured as not "
            "liganding it." + (f" Sites were compared with the lab's site annotation {escape(ann['file'])} "
                               f"({ann['sites_in_file']:,} sites)." if ann else "")
            + (" The protein's own ratio from the proteome is listed beside each call in cysteine_sites.tsv; the "
               "call itself uses the site ratio." if cys.get("proteinShown") else ""))


def _phos_title(phos: dict | None) -> str:
    phos = phos or {}
    words = ", ".join((["phosphosites"] if phos.get("ran") else []) + (["kinases"] if phos.get("ksea") else [])
                      + (["partners"] if phos.get("string") else [])) or "phosphosites"
    return words[0].upper() + words[1:]


def _phos_methods(phos: dict) -> str:
    """Methods for the phospho section (phospho.py, D79)."""
    out = []
    loc = phos.get("loc") or {}
    if phos.get("ran"):
        out.append(f"Phosphosites were read from {escape(str(loc.get('table', '')))} ({escape(str(loc.get('source', '')))}, "
                   f"quantity: {escape(str(loc.get('quantity', '')))}); localisation filter: "
                   f"{escape(str(loc.get('filter', '')))}. {loc.get('sites_kept', 0):,} of "
                   f"{loc.get('sites_in_table', 0):,} sites were kept and analysed like proteins."
                   + (" Each site comparison was also corrected for its protein's change in an unenriched proteome "
                      "(MSstatsPTM's adjustment)." if phos.get("corrected") else ""))
    k = phos.get("ksea") or {}
    if k.get("ran"):
        out.append("Kinase activity was inferred with KSEA (Casado et al., Sci. Signal. 2013; the scores of KSEAapp "
                   "2.0, Wiredja et al., Bioinformatics 2017) from the kinase-substrate table "
                   f"{escape(str(k.get('file', '')))} (substrates matched by {escape(str(k.get('match', 'gene')))} and "
                   "residue" + (f", NetworKIN predictions with score ≥ {k.get('networkin_score')}" if k.get("networkin") else
                                ", PhosphoSitePlus relationships") + "): z = (mean log2FC of a kinase's measured "
                   "substrates − mean log2FC of every site) × √m / SD; p two-sided from the normal distribution; "
                   f"Benjamini-Hochberg over the kinases with at least {k.get('min_substrates')} substrates.")
    st = phos.get("string") or {}
    if st.get("ran"):
        out.append(f"Interaction partners among the hits were taken from the STRING network "
                   f"{escape(str(st.get('file', '')))} (combined score ≥ {st.get('min_score')}).")
    return " ".join(out)


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


def _settings_table(s: Settings, p: fpa.Processed | None, model=None, plan=None) -> str:
    rows = [("Test", TESTS[s.test])]
    if model is not None and (model.design is not None or model.problem):
        rows.append(("Model", model.design.formula if model.design is not None
                     else f"~0 + condition (the design asked for wasn't used: {model.problem})"))
    if s.test == "limma" and s.variance_prior != "limma":
        used = (getattr(model, "prior", None) or {}).get("deqms_used")
        rows.append(("Variance prior", "DEqMS (by peptide count)" if used else
                     "limma (DEqMS asked for, but " + ((getattr(model, "prior", None) or {}).get("reason") or
                                                       "not applicable") + ")"))
    by_roles = plan is not None and getattr(plan, "active", False)
    if plan is not None and plan.roles:
        rows.append(("Roles", plan.describe()))
    rows += [("Comparisons", ("by role: compound vs control, competition vs its compound and vs the control"
                              if by_roles else
                              {"control": "each condition vs the control", "all": "all pairs",
                               "others": "each condition vs all others"}[s.de_type])
             + (f" (control: {s.control})" if s.control else "")),
            ("Cut-offs", s.describe()),
            ("Contaminants", "removed" if s.remove_contaminants else "kept"),
            ("Missing-value filter", f"≥{s.filter_global_pct:g}% of all samples, ≥{s.filter_condition_pct:g}% in one condition"),
            ("Normalisation", s.normalize + (f" → {p.normalization['used']}" if p and p.normalization.get("used")
                                             not in (None, s.normalize) else "")),
            ("Imputation", f"{s.imputation}" + (f" → {fpa.IMPUTATION_LABELS[p.imputation]}" if p else "")),
            ("Enrichment", ", ".join(s.enrichment_libraries) if s.enrichment else "off")]
    if p is not None and p.m.kind == "ratio":
        rows.append(("Site ratios", s.ratio_centre + " → " + fpa.centre_label((p.normalization or {}).get("ratio_centre")
                                                                               or {})))
        if s.protein_correction.get("proteome"):
            rows.append(("Protein correction", f"{s.protein_correction['proteome']} (match: "
                                               f"{s.protein_correction.get('match', 'gene')})"))
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
           enrichment: list[dict] | None = None, ranked: list[dict] | None = None, insight: dict | None = None,
           dose: dict | None = None, cys: dict | None = None, time: dict | None = None,
           psm: dict | None = None, phos: dict | None = None) -> str:
    s = s or Settings()
    enrichment = enrichment or []
    ranked = [b for b in ranked or [] if b["terms"]]
    title = ctx.get("experiment") or "Experiment"
    meta = " · ".join(x for x in (ctx.get("user"), ctx.get("method"), ctx.get("date"),
                                  f"generated {datetime.now():%Y-%m-%d %H:%M}", f"Ionomos {ctx.get('version', '')}") if x)
    data = json.dumps(payload(ctx, m, p, diffs, notes, files, s, qcd or {}, enrichment, ranked, insight, dose, cys, time,
                              psm, phos),
                      separators=(",", ":"), allow_nan=False).replace("</", "<\\/").replace("<!--", "<\\u0021--")
    pm = p.m if p else m
    ratio = pm is not None and pm.kind == "ratio"
    dose_shown = bool(dose and (dose.get("ran") or dose.get("found")))
    cys_shown = bool(cys and cys.get("ran"))
    time_shown = bool(time and (time.get("ran") or time.get("found")))
    spec_shown = bool(ctx.get("specific"))
    phos_shown = bool(phos and (phos.get("ran") or (phos.get("ksea") or {}).get("ran") is not None
                                or (phos.get("string") or {}).get("ran") is not None))
    b = [f"<main><div class='top'><div><h1>{escape(title)}</h1><div class='meta'>{escape(meta)}</div></div>"
         "<div><span id='slidesmsg' class='muted' role='status'></span> <button id='theme' title='Light / dark'>◐</button> "
         "<button id='share' title='Copy a link to this view (comparison, cut-offs, search)'>Link</button> "
         "<button id='slides' title='Every figure of this report at the export settings, the tables as CSV and a "
         "README, in one .zip. Set the size, text and colours under Options, Figure export'>Export for slides</button> "
         "<button onclick='window.print()'>Print</button></div></div>",
         "<nav class='toc'><a href='#overview'>Overview</a><a href='#differential'>Differential</a>"
         + "<a href='#compare' id='navcompare'" + ("" if len(diffs) >= 2 else " hidden") + ">Compare</a>"
         + "<a href='#specific' id='navspecific'" + ("" if spec_shown else " hidden") + ">Specific targets</a>"
         + ("" if ratio else "<a href='#onoff'>Only in one</a>")
         + "<a href='#heat'>Heatmap</a><a href='#enrichment'>Enrichment</a>"
         + "<a href='#dose' id='navdose'" + ("" if dose_shown else " hidden") + ">Dose-response</a>"
         + "<a href='#time' id='navtime'" + ("" if time_shown else " hidden") + ">Time course</a>"
         + "<a href='#cys' id='navcys'" + ("" if cys_shown else " hidden") + ">Liganded sites</a>"
         + "<a href='#phos' id='navphos'" + ("" if phos_shown else " hidden") + ">" + _phos_title(phos) + "</a>"
         + "<a href='#quality'>Quality control</a>"
         "<a href='#methods'>Methods</a><a href='#files'>Files</a><a href='#help'>Help</a></nav>",
         "<noscript><div class='notes'>This report draws its charts with JavaScript. The volcano_*.svg and *.tsv "
         "files in this folder hold the same results.</div></noscript>",
         "<section id='overview'><div class='tiles' id='tiles'></div><div id='findings'></div>"]
    b.append(trust.html(ctx.get("trust")))  # "How far to trust this": static, from the analysis' own checks (D60)
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
             "<div class='bar'><label class='ctl' title='Which two groups are compared'>Comparison "
             "<select id='comp'></select></label>"
             "<label class='ctl' title='How large a change must be to count as a hit, in log2 units: 1 = 2-fold, "
             "2 = 4-fold, 0.58 = 1.5-fold'>Fold change |log2FC| ≥ <input type='number' id='lfc' step='0.1' min='0'>"
             "<span id='foldhint' class='muted'></span></label>"
             "<label class='ctl' title='How small the p-value must be to count as a hit (0.05 = 5%)'>p-value ≤ "
             "<input type='number' id='alpha' step='0.01' min='0' max='1'></label>"
             "<label class='ctl' title='Apply the p-value cut-off to the adjusted p (Benjamini-Hochberg, corrected for "
             "testing many features). Leave it ticked unless you know why not'><input type='checkbox' id='adj'> "
             "adjusted</label>"
             "<div class='searchbox'><input type='search' id='search' autocomplete='off' spellcheck='false' "
             "placeholder='Find genes, proteins, a list, KRT*, term:…' aria-label='Search' aria-autocomplete='list'>"
             "<div id='suggest' class='suggest' role='listbox' hidden></div></div>"
             "<span class='seg'><button id='volc' class='on'>Volcano</button><button id='ma'>MA</button></span>"
             "<span class='seg' title='What dragging on the plot does'><button id='dzoom' class='on'>Zoom</button>"
             "<button id='dsel'>Select</button></span>"
             "<button id='opts' aria-expanded='false' title='Plot options, hit filters, highlight groups, figure "
             "export'>Options</button>"
             "<button id='reset' title='The fold-change and p-value cut-offs back to the ones saved with the report'>"
             "Reset cut-offs</button><span id='hl'></span></div>"
             "<div id='optpanel' class='card optpanel' hidden>"
             "<div><h4>Plot</h4>"
             "<label class='ctl' title='How many of the most significant hits have their name written on the plot'>"
             "Names on the plot <input type='number' id='labels' min='0' max='200' value='" + str(s.top_labels) + "'> top hits</label>"
             "<label class='ctl' title='Size of the points, as a multiple of the normal size'>Point size "
             "<input type='range' id='ptsize' min='0.5' max='2.5' step='0.1' value='1'><span id='ptsizev' class='muted'></span></label>"
             "<label class='ctl' title='Size of the names on the plot, in pixels'>Name size "
             "<input type='range' id='labsize' min='8' max='18' step='0.5' value='11.5'><span id='labsizev' class='muted'></span></label>"
             "<label class='ctl' title='Also name what the search found or a selection marked (the 40 most significant)'>"
             "<input type='checkbox' id='labmatch' checked> Name search matches</label>"
             "<label class='ctl' title='Dashed lines at the fold-change and p-value cut-offs'>"
             "<input type='checkbox' id='lines' checked> Cut-off lines</label>"
             "<label class='ctl' title='A triangle on features measured in one group and never in the other'>"
             "<input type='checkbox' id='markonoff' checked> Mark features only in one condition (△)</label></div>"
             "<div><h4>Hits</h4>"
             "<label class='ctl' title='A hit whose group has at least half of its values imputed is not counted'>"
             "<input type='checkbox' id='hideimp'> Ignore imputation-driven hits</label>"
             "<label class='ctl' id='minpepwrap' title='A hit identified by fewer than this is not counted (0 = no filter)'>"
             "A hit needs at least <input type='number' id='minpep' min='0' max='20' value='0'> "
             "<span id='evword'>peptides</span></label>"
             "<p class='muted'>Filtered hits are drawn grey with a coloured ring, so you can see what the filter removed.</p></div>"
             "<div><h4>Highlight groups</h4><div id='groups'></div>"
             "<input type='text' id='gname' placeholder='Group name'>"
             "<textarea id='ggenes' rows='3' placeholder='Genes or proteins (any separator)'></textarea>"
             "<div class='chips'><button id='gadd'>Add group</button><button id='gexport'>Export .gmt</button>"
             "<label class='filebtn'>Import .gmt / .txt<input type='file' id='gimport' accept='.gmt,.txt,.tsv,.csv'></label></div>"
             "<p class='muted'>Groups are kept in this browser and show in every Ionomos report.</p></div>"
             "<div><h4>Figures for slides</h4>"
             "<p class='muted'>One style for every exported figure: size, text, colours, title, legend.</p>"
             "<div class='chips'><button id='xopen' title='Choose the size, text and colours, see the figure, download "
             "or copy it'>Figure export…</button><button id='xzipnow' title='Every figure and table of this report "
             "in one .zip, at the export settings'>Export for slides (.zip)</button></div></div>"
             "<div class='optfoot'><button id='optreset' title='The cut-offs, the plot options and the hit filters back "
             "to how this report was made. Highlight groups and the export style are kept'>Reset to lab defaults</button> "
             "<span id='optmsg' class='muted' role='status'></span></div></div>"
             "<div class='muted' id='viewnote'></div>"
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
    b.append("<section id='specific'" + ("" if spec_shown else " hidden") + "><h2>Specific targets</h2><p class='sub'>"
             "A competition experiment: what the compound enriches against the control (across), and how much of "
             "that the competitor takes off (up). Specific targets are both, at the cut-offs above: the marked "
             "quadrant. Enriched but not competed points at unspecific binding. Click a point or a row.</p>"
             "<div id='specificbody'></div></section>")
    if not ratio:
        b.append("<section id='onoff'><h2>Only in one condition</h2><p class='sub'>Measured in at least 75% (and at "
                 "least two) of one group's samples and in none of the other: often the strongest biology, and "
                 "invisible to a t-test without imputation. Missing can also mean below detection, so confirm them.</p>"
                 "<div id='onoffbody'></div></section>")
    b.append("<section id='heat'><h2>Heatmap of significant features</h2><p class='sub'>Each row centred on its "
             "mean, so colour shows where a feature is high or low across samples.</p>"
             "<div class='card chart' id='heatmap'></div><div class='legend' id='heatlegend'></div></section>")
    b.append("<section id='enrichment'><h2>Enrichment</h2><div id='enrich'></div></section>")
    b.append("<section id='dose'" + ("" if dose_shown else " hidden") + "><h2>Dose-response</h2><p class='sub'>"
             "A curve per feature across the doses (CurveCurator's 4-parameter log-logistic fit, ratio to the "
             "control). Only curves classed up or down have a potency worth reading; click a row or a point to "
             "draw its curve over the measured values.</p><div id='dosebody'></div></section>")
    b.append("<section id='time'" + ("" if time_shown else " hidden") + "><h2>Time course</h2><p class='sub'>"
             "Which features change over time (a moderated F-test across the time points), in which direction, "
             "and with which shape. Click a pattern to list its features; click a row to draw the feature over "
             "its replicates.</p><div id='timebody'></div></section>")
    b.append("<section id='cys'" + ("" if cys_shown else " hidden") + "><h2>Liganded sites</h2><p class='sub'>"
             "Which cysteines each compound engages: a site is liganded when its competition ratio R reaches the "
             "threshold in enough replicates. This is the chemoproteomics convention, not a p-value; the volcano "
             "above tests whether a ratio differs from 1. Click a row to see the site's replicates.</p>"
             "<div id='cysbody'></div></section>")
    b.append("<section id='phos'" + ("" if phos_shown else " hidden") + "><h2>" + _phos_title(phos) + "</h2>"
             "<p class='sub'>What the phosphosite table kept after the localisation filter, which kinases' known "
             "substrates move together (KSEA: a z-score from the substrates' fold changes, not a measurement of the "
             "kinase), and which hits are known to interact (STRING). The kinase-substrate table and the network "
             "are the lab's downloads.</p><div id='phosbody'></div></section>")
    b.append("<section id='quality'><h2>Quality control</h2><div id='qc'></div></section>")
    method = ctx.get("method", "")
    text = methods_text(pm, p, diffs, s, method, ctx.get("fragpipe", ""), enrichment, ranked, ctx.get("engine"),
                        ctx.get("model"), ctx.get("ftest"), ctx.get("roles"), ctx.get("specific"))
    b.append(f"<section id='methods'><h2>Methods</h2><p class='methods'>{text}</p>")
    if dose and dose.get("ran"):
        b.append(f"<p class='methods'>{_dose_methods(dose)}</p>")
    if time and time.get("ran"):
        b.append(f"<p class='methods'>{_time_methods(time)}</p>")
    if cys_shown:
        b.append(f"<p class='methods'>{_cys_methods(cys)}</p>")
    if phos_shown:
        b.append(f"<p class='methods'>{_phos_methods(phos)}</p>")
    b.append(_provenance_table(ctx.get("engine") or {}))
    b.append("<h3>Settings used</h3>" + _settings_table(s, p, ctx.get("model"), ctx.get("roles")))
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
            trust.html(ctx.get("trust")), issues_html(ctx.get("issues") or [])]
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
