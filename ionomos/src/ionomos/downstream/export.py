"""
Result tables, and the files to open the same data in FragPipe-Analyst / FragPipeAnalystR.

    results_table()        <level>_results.tsv   one row per feature, every comparison side by side
                                                 (the layout of FragPipe-Analyst's "Results" download)
    processed_matrix()     <level>_matrix_processed.tsv   the values the statistics used (+ which were imputed)
    enrichment_table()     enrichment.tsv
    rank_enrichment_table() gene_set_ranks.tsv   rank-based gene-set test on every protein (no cut-off)
    sample_qc_table()      sample_qc.tsv         the per-sample scorecard (identifications, correlation, flags)
    presence_absence_table() presence_absence.tsv features measured in one group and never in the other
    fragpipe_analyst()     fragpipe-analyst/experiment_annotation.tsv + reproduce_in_R.R
                           upload the quant table + annotation to https://fragpipe-analyst.org, or run the
                           script with FragPipeAnalystR installed, to cross-check Ionomos's numbers.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from ionomos.downstream import fpa
from ionomos.downstream.analysis import DiffResult, Settings
from ionomos.downstream.tables import write_tsv


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def results_table(path: Path, p: fpa.Processed, diffs: list[DiffResult], ftest=None) -> Path:
    """ftest (design.FTest, 3+ conditions): the moderated F columns, "any change between the conditions"."""
    m = p.m
    by_index = []
    for d in diffs:
        idx = {r["index"]: r for r in d.rows}
        by_index.append(idx)
    header = ["id", "label", "description"]
    for d in diffs:
        k = _slug(d.name)
        header += [f"{k}_log2fc", f"{k}_ci_low", f"{k}_ci_high", f"{k}_p", f"{k}_p_adj", f"{k}_significant"]
    if ftest is not None:
        header += ["F", "F_p", "F_p_adj"]
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
        if ftest is not None:
            row += [None if x != x else x for x in (ftest.f[i], ftest.p[i], ftest.q[i])]
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


def rank_enrichment_table(path: Path, ranked: list[dict]) -> Path:
    header = ["comparison", "library", "term", "direction", "genes_measured", "z", "p", "p_adj", "median_score",
              "inter_gene_correlation", "leading_genes"]
    rows = [[b["comparison"], b["library"], t["term"], t["direction"], t["n"], t["z"], t["p"], t["q"], t["median"],
             t["corr"], ";".join(t["leading"])] for b in ranked for t in b["terms"]]
    return write_tsv(path, header, rows)


def sample_qc_table(path: Path, card: list[dict]) -> Path:
    header = ["sample", "condition", "status", "identifications", "missing_pct", "median_log2_before_norm",
              "shift_from_median", "median_r_own_group", "median_r_all", "median_abs_dev_from_group",
              "leave_one_out_cv_change", "flags"]
    rows = [[r["sample"], r["condition"], r["status"], r["ids"], r["missing_pct"], r["median"], r["shift"],
             r["corr_group"], r["corr_all"], r["spread"], r["loo_cv"], "; ".join(r["flags"])] for r in card]
    return write_tsv(path, header, rows)


def presence_absence_table(path: Path, arg) -> Path:
    p, onoff = arg
    m = p.m
    header = ["comparison", "only_in", "id", "label", "description", "detected", "of", "mean_log2"]
    rows = []
    for comp, items in onoff.items():
        for x in items:
            f = m.features[x["index"]]
            rows.append([comp, x["group"], f.id, f.label, f.description, x["detected"], x["of"], x["mean"]])
    return write_tsv(path, header, rows)


def _r_str(s: str) -> str:
    return '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'


def _design_script(folder: Path, p: fpa.Processed, d, comps, s: Settings) -> Path:
    """reproduce_design_in_R.R: Ionomos's blocked / covariate model in plain limma, on the values it tested
    (<level>_matrix_processed.tsv), since FragPipeAnalystR's test_limma fits only ~0 + condition."""
    m = p.m
    samples = list(d.samples)
    vec = lambda xs: "c(" + ", ".join(_r_str(x) for x in xs) + ")"  # noqa: E731
    lines = [
        "# Repeat Ionomos's linear model with limma (lmFit -> contrasts.fit -> eBayes -> topTable).",
        f"# Model: {d.formula}" + (f"  ({d.describe()})" if d.describe() else ""),
        "# Needs R with limma (Bioconductor). Run from this folder: Rscript reproduce_design_in_R.R",
        "suppressPackageStartupMessages(library(limma))",
        f"x <- read.delim({_r_str('../' + m.level + '_matrix_processed.tsv')}, check.names = FALSE)",
        f"samples <- {vec(samples)}",
        "y <- as.matrix(x[, samples]); rownames(y) <- x$id",
        f"condition <- factor({vec([m.condition[x] for x in samples])}, levels = {vec(d.conditions)})",
    ]
    k = len(d.conditions)
    parts = ["condition"]
    for t in d.terms:
        var = re.sub(r"[^A-Za-z0-9_]", "_", t["name"]) or "term"
        if t["kind"] == "numeric":
            col = d.columns.index(t["name"])
            lines.append(f"{var} <- c({', '.join(repr(float(row[col])) for row in d.x)})")
        else:
            start = next(j for j, c in enumerate(d.columns) if j >= k and c.startswith(t["name"]))
            width = len(t["levels"]) - 1
            lv = []
            for row in d.x:
                hit = next((i for i in range(width) if row[start + i] == 1.0), None)
                lv.append(t["levels"][0] if hit is None else t["levels"][hit + 1])
            lines.append(f"{var} <- factor({vec(lv)}, levels = {vec(t['levels'])})")
        parts.append(var)
    pairs = [(a, b) for a, b in comps if b not in (None, "others")]
    cont = ", ".join(f"{_r_str(f'{a}_vs_{b}')} = ifelse(conds == {_r_str(a)}, 1, ifelse(conds == {_r_str(b)}, -1, 0))"
                     for a, b in pairs)
    lines += [
        f"design <- model.matrix(~0 + {' + '.join(parts)})",
        "conds <- c(levels(condition), rep(\"\", ncol(design) - nlevels(condition)))",
        f"C <- cbind({cont})",
        "fit <- eBayes(contrasts.fit(lmFit(y, design), C))",
        "out <- do.call(rbind, lapply(colnames(C), function(k) {",
        "  tt <- topTable(fit, coef = k, number = Inf, sort.by = \"none\", confint = TRUE)",
        "  data.frame(comparison = k, id = rownames(tt), tt, check.names = FALSE)",
        "}))",
        "write.table(out, \"limma_design_results.tsv\", sep = \"\\t\", quote = FALSE, row.names = FALSE)",
    ]
    if any(b == "others" for _, b in comps):
        lines.append("# 'vs others' comparisons are not repeated here (one model per condition, as test_limma).")
    if p.imputation == "none" and s.min_valid:
        lines.append(f"# Ionomos leaves a feature untested where a group has fewer than {s.min_valid} measured values;"
                     " limma tests every estimable one.")
    if s.variance_prior == "deqms":
        lines += ["# Ionomos used DEqMS: with the peptide counts per row of y in `count`,",
                  "#   library(DEqMS); fit$count <- count; fit <- spectraCounteBayes(fit)   # then fit$sca.t, fit$sca.p"]
    path = folder / "reproduce_design_in_R.R"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def fragpipe_analyst(folder: Path, m_loaded, p: fpa.Processed, diffs: list[DiffResult], s: Settings,
                     comps: list[tuple[str, str | None]], model=None) -> list[Path]:
    """experiment_annotation.tsv in FragPipe-Analyst's format + an R script that repeats this analysis
    with FragPipeAnalystR. Not for ratio data (isoDTB), which FragPipe-Analyst doesn't take.
    model (analysis.Model): with blocks or covariates, test_limma can't repeat the model, so the script
    says so and reproduce_design_in_R.R repeats it in limma."""
    if p.m.kind != "intensity" or m_loaded.exp not in ("DIA", "LFQ", "TMT") or not m_loaded.columns:
        return []
    if m_loaded.level == "site":  # phosphosites (D79): the script would read the table as proteins
        return []
    design = getattr(model, "design", None)
    deqms = s.test == "limma" and s.variance_prior == "deqms" and (getattr(model, "prior", {}) or {}).get("deqms_used")
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
    ]
    if design is not None or deqms:
        what = " and ".join(x for x in ((f"the model {design.formula}" if design is not None else ""),
                                        ("DEqMS's peptide-count variance prior" if deqms else "")) if x)
        lines += [
            f"# NOTE: Ionomos used {what}. FragPipeAnalystR's test_limma fits only ~0 + condition with",
            "# limma's single prior, so this script repeats the PLAIN model: its p-values will differ from the report's."
            + (" reproduce_design_in_R.R repeats the model Ionomos used, in limma." if design is not None else ""),
        ]
    lines += [
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
    used = (p.normalization or {}).get("used") or s.normalize
    if used == "median":
        lines.append("se <- MD_normalization(se)")
    elif used == "gn":
        lines.append("se <- GN_normalization(se)")
    elif used == "ratio":
        lines += ["# Ionomos normalised these samples on feature ratios (many features change in one direction, so median",
                  "# centring would shift the conditions against each other). FragPipeAnalystR has no such step:",
                  "# MD_normalization below is the nearest, and its fold changes will differ by that shift.",
                  "se <- MD_normalization(se)"]
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
    if design is not None:
        return [ann, script, _design_script(folder, p, design, comps, s)]
    return [ann, script]
