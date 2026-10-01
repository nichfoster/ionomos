"""
"How far to trust this": a short list of plain statements about one analysis, each with its numbers (D60).

    t = build(m, processed, diffs, insight, qcd, settings, guard_findings, results_dir)
    t["statements"]   [{"key", "title", "level": "ok" | "check", "text", "numbers": {...}}]
    t["compare"]      the verdicts of an `ionomos compare` result found in the results folder, or None
    t["benchmark"]    the summary of an `ionomos benchmark` result found there, or None
    html(t)           the block at the top of report.html

There is no score. Every statement repeats a check that exists elsewhere in the analysis (the sample
scorecard, the missingness and p-value checks of insights.py, the power estimate, the doctor's and the
guards' thresholds) and gives the number behind it. "check" marks a statement whose own check crossed
its threshold; the thresholds are the ones those checks already use:

    replicates     a group of fewer than 3 samples, or groups that differ 2-fold or more in size
    agreement      a sample the scorecard fails (the doctor's SAMPLE_OUTLIER / LOW_SAMPLE), or a batch-like
                   principal component (BATCH_SUSPECT); a scorecard warning is named, not marked
    missing        more than 40 % of the values imputed (HIGH_IMPUTATION), or random missingness imputed as
                   low values (IMPUTATION_MISMATCH)
    imputed hits   5 or more hits, and 30 % or more of a comparison's hits, resting on imputed values
    p-values       a histogram shape of "conservative" or "hump"
    power          the fold change detectable at 80 % power (p 0.05, median spread) is above the cut-off
    statistics     any guard finding (guards.py), a low-confidence or fold-change-only comparison

A compare / benchmark result is shown with whether it was made on an analysis with these settings
(settings_digest); one made with other settings is shown as such, never dropped silently.
"""
from __future__ import annotations

import hashlib
import json
import math
from html import escape
from pathlib import Path

from ionomos.downstream import guards, stats

COMPARE_JSON = "compare.json"
BENCHMARK_JSON = "benchmark.json"
SIMULATED_JSON = "benchmark_simulated.json"
LEFT_CENSORED = ("perseus", "mindet", "minprob", "min", "zero")


def settings_digest(settings: dict) -> str:
    """A short fingerprint of the analysis settings, the same from a Settings dict and from analysis.json."""
    plain = json.loads(json.dumps(settings, default=str))  # as analysis.json stores them
    return hashlib.sha1(json.dumps(plain, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _st(key: str, title: str, check: bool, text: str, **numbers) -> dict:
    return {"key": key, "title": title, "level": "check" if check else "ok", "text": text, "numbers": numbers}


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%" if x >= 0.1 or x == 0 else f"{100 * x:.1f}%"


def _groups(pm) -> dict[str, int]:
    return {c: len(pm.samples_of(c)) for c in pm.conditions}


def build(m, p, diffs: list, insight: dict | None, qcd: dict | None, settings, guard_findings: list | None = None,
          results_dir: Path | None = None) -> dict:
    """The statements for one analysis. Everything optional: with nothing analysed the list is empty."""
    from ionomos.downstream import analysis

    insight, qcd = insight or {}, qcd or {}
    out: list[dict] = []
    pm = p.m if p is not None else None
    if pm is not None and pm.samples:
        sizes = _groups(pm)
        ns = sorted(sizes.values())
        listed = ", ".join(f"{c} {n}" for c, n in sizes.items())
        uneven = ns[-1] >= 2 * ns[0] and len(ns) > 1
        if len(sizes) < 2 and pm.kind == "intensity":
            text = f"One condition ({listed} samples): nothing was compared."
        elif ns[0] < 2:
            text = (f"Samples per group: {listed}. A group of one has no replicate spread of its own; where it was "
                    "tested, the spread was borrowed from the other groups.")
        elif ns[0] < 3:
            text = (f"Samples per group: {listed}. With 2 replicates a single odd sample can make or hide a change; "
                    "treat hits as leads.")
        elif uneven:
            text = (f"Samples per group: {listed} (uneven). The tests allow for it; a comparison is about as precise "
                    "as its smaller group.")
        else:
            text = f"Samples per group: {listed}."
        out.append(_st("replicates", "Replicates", ns[0] < 3 or uneven or (len(sizes) < 2 and pm.kind == "intensity"),
                       text, groups=sizes, smallest=ns[0], largest=ns[-1]))

        card = insight.get("scorecard") or []
        rs = [r["corr_group"] for r in card if r.get("corr_group") is not None]
        cvs = {c: v.get("median") for c, v in (qcd.get("cv") or {}).items() if v.get("median") is not None
               and not math.isnan(v["median"])}
        flagged = [r["sample"] for r in card if r.get("status") == "fail"]
        warned = [r["sample"] for r in card if r.get("status") == "warn"]
        batch = (insight.get("pcs") or {}).get("batch")
        if rs or cvs:
            parts = []
            if rs:
                parts.append(f"median correlation of a sample with its own replicates r = {stats.median(rs):.3f} "
                             f"(lowest {min(rs):.3f})")
            if cvs:
                parts.append("median CV " + ", ".join(f"{_pct(v)} ({c})" for c, v in cvs.items()))
            text = "Replicate agreement: " + "; ".join(parts) + "."
            if flagged:
                text += f" The sample scorecard fails {len(flagged)}: {', '.join(flagged[:5])}."
            if warned:
                text += f" It has a warning for {len(warned)}: {', '.join(warned[:5])}."
            if batch:
                text += (f" PC{batch['pc']} follows the replicate number ({_pct(batch['r2_replicate'])} explained), "
                         "which looks like a batch.")
            out.append(_st("agreement", "Replicate agreement", bool(flagged or batch), text,
                           median_r=stats.median(rs) if rs else None, lowest_r=min(rs) if rs else None,
                           median_cv=cvs, flagged=flagged, warned=warned))

        total = len(p.measured) * len(pm.samples)
        if total:
            missing = sum(1 for row in p.measured for v in row if v is None)
            share = missing / total
            verdict = (insight.get("missingness") or {}).get("verdict", "")
            how = {"intensity": "mostly low-abundance features go missing", "random": "features go missing at every "
                   "abundance", "mixed": "missing values are partly abundance-related", "few": ""}.get(verdict, "")
            imputed = p.n_imputed / total
            text = f"Missing values: {_pct(share)} of the values were not measured" + (f" ({how})" if how else "") + "."
            if p.n_imputed:
                text += f" {_pct(imputed)} of the values in the statistics are imputed ({p.imputation})."
            elif missing:
                text += " Nothing was imputed: a feature is tested where it has enough measured values."
            mismatch = verdict == "random" and p.imputation in LEFT_CENSORED and imputed > 0.05
            if mismatch:
                text += " Random missingness imputed as low values can create fold changes."
            out.append(_st("missing", "Missing values", imputed > 0.4 or mismatch, text, missing_share=share,
                           imputed_share=imputed, imputation=p.imputation, pattern=verdict))

    for d in diffs:
        hits = d.up + d.down
        nums = {"comparison": d.name, "tested": d.tested, "up": d.up, "down": d.down}
        if d.confidence == "none":
            out.append(_st("comparison", d.name, True, f"{d.name}: fold change only. {hits:,} candidates by "
                           "|log2FC| alone; no p-values exist for this comparison.", **nums))
            continue
        text = f"{d.name}: {d.tested:,} features tested, {d.up:,} up and {d.down:,} down."
        check = d.confidence == "low"
        if check:
            text += " Low confidence: a group has one sample."
        driven = len((insight.get("imputation_driven") or {}).get(d.name) or [])
        if hits and p is not None and p.n_imputed:
            text += f" Hits resting on imputed values (half or more of a group imputed): {driven:,} of {hits:,}."
            check = check or (driven >= 5 and driven / hits >= 0.3)
            nums["imputation_driven_hits"] = driven
        ph = (insight.get("phist") or {}).get(d.name) or {}
        shape = ph.get("shape", "")
        if shape:
            words = {"signal": "a peak near 0 on a flat background, as expected when some features change",
                     "flat": "flat: no sign of differences", "conservative": "piled up near 1: the p-values are "
                     "probably too large", "hump": "a bulge in the middle: the model does not fit the data well"}[shape]
            text += f" p-value histogram: {words}"
            if ph.get("pi0") is not None and ph["pi0"] < 0.99:
                text += f" (about {_pct(1 - ph['pi0'])} of the features estimated to change)"
            text += "."
            check = check or shape in ("conservative", "hump")
            nums["p_value_shape"], nums["pi0"] = shape, ph.get("pi0")
        if d.test_used == "limma" and d.prior and d.prior[0] == d.prior[0]:
            nums["prior_df"] = d.prior[0] if math.isfinite(d.prior[0]) else "inf"
            if guards.pooled_prior(d.prior[0]):
                text += " limma found the features' variances alike and used one pooled variance for all."
        out.append(_st("comparison", d.name, check, text, **nums))

    pw = insight.get("power") or {}
    rows = (pw.get("curves") or {}).get("0.05")
    if rows and pw.get("current_n") in (pw.get("n") or []):
        at = rows[pw["n"].index(pw["current_n"])]
        strict = ((pw["curves"].get("0.001") or [None] * len(rows))[pw["n"].index(pw["current_n"])] or {})
        if at:
            need = at["q50"]
            text = (f"Power: with {pw['current_n']} samples per group and this experiment's median spread (SD "
                    f"{pw['sd']['q50']:.2f} log2), a change of |log2FC| ≥ {need:.2f} is found 80% of the time at "
                    f"p 0.05" + (f" (≥ {strict['q50']:.2f} at p 0.001, closer to what survives the correction for "
                                 "many tests)" if strict.get("q50") else "") + ".")
            low = settings.log2fc > 0 and need > settings.log2fc
            if low:
                text += f" That is above the cut-off of {settings.log2fc:g}: changes near the cut-off are often missed."
            out.append(_st("power", "Power", low, text, n=pw["current_n"], sd_median=pw["sd"]["q50"],
                           detectable_p05=need, detectable_p001=strict.get("q50"), log2fc_cutoff=settings.log2fc))

    for g in guard_findings or []:
        code = g.get("code")
        if code == "IDENTICAL_SAMPLES":
            text = "Samples with exactly the same values: " + "; ".join(" = ".join(x) for x in g["groups"][:3]) + "."
        elif code == "NO_RESIDUAL_DF":
            text = (f"{g['comparison']}: {g['features']:,} of {g['tested']:,} tested features have one value per group; "
                    "their p-values come from the variance prior alone.")
        elif code == "ZERO_VARIANCE":
            text = (f"{g['comparison']}: {g['features']:,} of {g['tested']:,} tested features have identical "
                    "replicates; their p-values are not meaningful.")
        elif code == "VARIANCE_PRIOR":
            text = (f"{g['comparison']}: no variance prior could be estimated; the p-values are ordinary t-tests."
                    if g.get("what") == "none" else
                    f"{g['comparison']}: the variance prior did not converge; the p-values are close to ordinary "
                    "t-tests.")
        else:
            continue
        out.append(_st("statistics", "Statistics", True, text, **{k: v for k, v in g.items()}))

    digest = settings_digest(analysis.as_dict(settings)) if settings is not None else ""
    return {"statements": out, "settings_digest": digest,
            "compare": _external(results_dir, COMPARE_JSON, digest),
            "benchmark": _external(results_dir, BENCHMARK_JSON, digest),
            "benchmark_simulated": _external(results_dir, SIMULATED_JSON, digest),
            "basis": "Each statement repeats a check of this analysis with its number. There is no overall score."}


def _external(results_dir: Path | None, name: str, digest: str) -> dict | None:
    """A compare.json / benchmark.json in the results folder, reduced to what the report shows."""
    if results_dir is None:
        return None
    try:
        data = json.loads((Path(results_dir) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    made_on = (data.get("analysis") or {}).get("settings_digest", "")
    return {"file": name, "page": name.replace(".json", ".html"), "generated_at": str(data.get("generated_at", "")),
            "reference": str(data.get("reference_name", "")), "same_settings": bool(made_on) and made_on == digest,
            "lines": [str(x) for x in (data.get("headline") or data.get("verdicts") or [])][:8]}


def html(t: dict | None) -> str:
    """The block under the tiles of report.html (static: it needs no script and uses the report's own classes)."""
    if not t or not (t.get("statements") or t.get("compare") or t.get("benchmark") or t.get("benchmark_simulated")):
        return ""
    items = []
    for s in t.get("statements") or []:
        pill = "<span class='pill warn'>check</span> " if s["level"] == "check" else ""
        items.append(f"<li data-key='{escape(s['key'])}' data-level='{escape(s['level'])}'>{pill}{escape(s['text'])}</li>")
    for key, title in (("compare", "Compared with a reference"), ("benchmark", "Benchmark against known ratios"),
                       ("benchmark_simulated", "These settings on simulated data")):
        x = t.get(key)
        if not x:
            continue
        stale = "" if x["same_settings"] else (" <span class='pill warn'>other settings</span> made on an analysis "
                                                "with other settings than this one; run it again to update it.")
        ref = f" ({escape(x['reference'])})" if x.get("reference") else ""
        lines = "".join(f"<li>{escape(v)}</li>" for v in x["lines"])
        items.append(f"<li data-key='{key}'><b>{title}</b>{ref}, {escape(x['generated_at'])}: "
                     f"<a href='{escape(x['page'])}'>{escape(x['page'])}</a>{stale}<ul>{lines}</ul></li>")
    return ("<div class='card findings' id='trust'><h3>How far to trust this</h3><ul>" + "".join(items) + "</ul>"
            f"<div class='meta'>{escape(t.get('basis', ''))} Accuracy against a reference or known ratios: "
            "<code>ionomos compare</code>, <code>ionomos benchmark</code>.</div></div>")
