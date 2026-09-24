"""
Result tables, and the files to open the same data in FragPipe-Analyst / FragPipeAnalystR.

    results_table()        <level>_results.tsv   one row per feature, every comparison side by side
                                                 (the layout of FragPipe-Analyst's "Results" download)
    processed_matrix()     <level>_matrix_processed.tsv   the values the statistics used (+ which were imputed)
    enrichment_table()     enrichment.tsv
    fragpipe_analyst()     fragpipe-analyst/experiment_annotation.tsv + reproduce_in_R.R
                           upload the quant table + annotation to https://fragpipe-analyst.org, or run the
                           script with FragPipeAnalystR installed, to cross-check Ionomos's numbers.
"""
from __future__ import annotations

import math
from pathlib import Path

from ionomos.downstream import fpa
from ionomos.downstream.analysis import DiffResult, Settings
from ionomos.downstream.tables import write_tsv


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def results_table(path: Path, p: fpa.Processed, diffs: list[DiffResult]) -> Path:
    m = p.m
    by_index = []
    for d in diffs:
        idx = {r["index"]: r for r in d.rows}
        by_index.append(idx)
    header = ["id", "label", "description"]
    for d in diffs:
        k = _slug(d.name)
        header += [f"{k}_log2fc", f"{k}_ci_low", f"{k}_ci_high", f"{k}_p", f"{k}_p_adj", f"{k}_significant"]
    header += ["significant_any", "imputed", "num_missing"] + list(m.samples)
    rows = []
    for i, f in enumerate(m.features):
        row = [f.id, f.label, f.description]
        any_sig = False
        for idx in by_index:
            r = idx.get(i) or {}
            row += [r.get("log2fc"), r.get("ci_low"), r.get("ci_high"), r.get("pvalue"), r.get("qvalue"),
                    r.get("significant", "")]
            any_sig = any_sig or bool(r.get("significant"))
        n_missing = sum(1 for v in p.measured[i] if v is None)
        row += ["TRUE" if any_sig else "FALSE", "TRUE" if n_missing else "FALSE", n_missing]
        row += m.values[i]
        rows.append(row)
    return write_tsv(path, header, rows)


def processed_matrix(path: Path, p: fpa.Processed) -> Path:
    m = p.m
    header = ["id", "label", "description", *m.samples, "imputed_in"]
    rows = []
    for f, vals, mask in zip(m.features, m.values, p.imputed, strict=True):
        rows.append([f.id, f.label, f.description, *vals, ";".join(s for s, k in zip(m.samples, mask, strict=True) if k)])
    return write_tsv(path, header, rows)


def enrichment_table(path: Path, enrichment: list[dict]) -> Path:
    header = ["comparison", "direction", "library", "term", "overlap", "term_size_in_background", "hits",
              "background", "p", "p_adj", "log2_odds_ratio", "genes"]
    rows = []
    for block in enrichment:
        for t in block["terms"]:
            lo = t["log2_odds"]
            rows.append([block["comparison"], block["direction"], block["library"], t["term"], t["k"], t["K"], t["n"],
                         t["N"], t["p"], t["q"], lo if math.isfinite(lo) else ("Inf" if lo > 0 else "-Inf"),
                         ";".join(t["genes"])])
    return write_tsv(path, header, rows)


# ------------------------------------------------------------ FragPipe-Analyst --


def _r_str(s: str) -> str:
    return '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'


def fragpipe_analyst(folder: Path, m_loaded, p: fpa.Processed, diffs: list[DiffResult], s: Settings,
                     comps: list[tuple[str, str | None]]) -> list[Path]:
    """experiment_annotation.tsv in FragPipe-Analyst's format + an R script that repeats this analysis
    with FragPipeAnalystR. Not for ratio data (isoDTB), which FragPipe-Analyst doesn't take."""
    if p.m.kind != "intensity" or m_loaded.exp not in ("DIA", "LFQ", "TMT") or not m_loaded.columns:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    samples = [x for x in m_loaded.samples if x in p.m.samples]
    cond = p.m.condition
    rep = {}
    count: dict[str, int] = {}
    for x in samples:
        count[cond[x]] = count.get(cond[x], 0) + 1
        rep[x] = m_loaded.replicate.get(x) or count[cond[x]]
    if m_loaded.exp == "TMT":
        header = ["plex", "channel", "sample", "sample_name", "condition", "replicate"]
        rows = [[1, "", m_loaded.columns[x], x, cond[x], rep[x]] for x in samples]
    elif m_loaded.exp == "DIA":
        header = ["file", "sample", "sample_name", "condition", "replicate"]
        rows = [[m_loaded.columns[x], x, x, cond[x], rep[x]] for x in samples]
    else:  # LFQ: 'sample' = the experiment name that prefixes the intensity columns
        header = ["file", "sample", "sample_name", "condition", "replicate"]
        rows = [["", m_loaded.columns[x].rsplit(" ", 2)[0] if " " in m_loaded.columns[x] else x, x, cond[x], rep[x]]
                for x in samples]
    ann = write_tsv(folder / "experiment_annotation.tsv", header, rows)

    typ = {"DIA": "DIA", "LFQ": "LFQ", "TMT": "TMT"}[m_loaded.exp]
    level = "gene" if m_loaded.level == "gene" else "protein"
    lines = [
        "# Repeat this Ionomos analysis with FragPipeAnalystR (https://github.com/Nesvilab/FragPipeAnalystR).",
        "# Needs R >= 4.5 with FragPipeAnalystR installed (see its README). Run from this folder:",
        "#   Rscript reproduce_in_R.R",
        "# Or upload the quant table and experiment_annotation.tsv to FragPipe-Analyst (https://fragpipe-analyst.org).",
        "suppressPackageStartupMessages(library(FragPipeAnalystR))",
        f"quant <- {_r_str(m_loaded.source)}",
        f"se <- make_se_from_files(quant, \"experiment_annotation.tsv\", type = \"{typ}\", level = \"{level}\""
        + (", lfq_type = \"MaxLFQ\"" if typ == "LFQ" and "MaxLFQ" in " ".join(m_loaded.notes) else "") + ")",
    ]
    if s.filter_global_pct:
        lines.append(f"se <- se[rowSums(!is.na(assay(se))) / ncol(se) >= {s.filter_global_pct / 100:g}, ]  # FragPipe-Analyst global_filter")
    if s.filter_condition_pct:
        lines += [
            "keep <- rep(FALSE, nrow(se))  # FragPipe-Analyst filter_by_condition",
            "for (cnd in unique(colData(se)$condition)) {",
            "  sub <- assay(se)[, colData(se)$condition == cnd, drop = FALSE]",
            f"  keep <- keep | rowSums(!is.na(sub)) / ncol(sub) >= {s.filter_condition_pct / 100:g}",
            "}",
            "se <- se[keep, ]",
        ]
    if s.normalize == "median":
        lines.append("se <- MD_normalization(se)")
    elif s.normalize == "gn":
        lines.append("se <- GN_normalization(se)")
    imp = {"perseus": "man", "min": "min", "zero": "zero", "mindet": "MinDet", "minprob": "MinProb", "knn": "knn"}
    if p.imputation in imp:
        lines.append(f"se <- impute(se, fun = \"{imp[p.imputation]}\")")
    ctrl = next((b for _, b in comps if b not in (None, "others")), None)
    if any(b == "others" for _, b in comps):
        lines.append("de <- test_limma(se, type = \"others\")")
    elif s.comparisons:
        tests = ", ".join(_r_str(f"{a}_vs_{b}") for a, b in comps)
        lines.append(f"de <- test_limma(se, type = \"manual\", test = c({tests}))")
    elif s.de_type == "all":
        lines.append("de <- test_limma(se, type = \"all\"" + (f", control = {_r_str(ctrl)})" if ctrl else ")"))
    else:
        lines.append(f"de <- test_limma(se, type = \"control\", control = {_r_str(ctrl)})")
    lines += [
        f"de <- add_rejections(de, alpha = {s.alpha:g}, lfc = {s.log2fc:g})",
        "pdf(\"FragPipeAnalystR_plots.pdf\", width = 9, height = 7)",
        "print(plot_pca(de))",
        "print(plot_correlation_heatmap(de))",
    ]
    for d in diffs:
        if d.control and d.control != "others":
            lines.append(f"print(plot_volcano(de, {_r_str(f'{d.treatment}_vs_{d.control}')}))")
    lines += [
        "dev.off()",
        "write.table(as.data.frame(SummarizedExperiment::rowData(de)), \"FragPipeAnalystR_results.tsv\","
        " sep = \"\\t\", quote = FALSE, row.names = FALSE)",
        "",
    ]
    script = folder / "reproduce_in_R.R"
    script.write_text("\n".join(lines), encoding="utf-8")
    return [ann, script]
