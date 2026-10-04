"""
`ionomos benchmark`: how accurate the analysis is against known truth (D60).

Simulated (no data needed; `ionomos benchmark [--kind dia|isodtb|tmt]`):

    res = simulated("standard")          # the pipeline on simulate.py data with planted changes, over a grid
    res = simulated("standard", kind="tmt")
    write_simulated(res, out_dir)        # benchmark_simulated[_<kind>].tsv / .json / .html

    dia       label-free DIA protein matrices (D60): replicates 2 to 6 per group, an unbalanced 2 controls vs 4
              treated, planted effects of 1.5-, 2- and 4-fold, three levels of missing values; every imputation /
              normalisation setting (SETTINGS)
    isodtb    FragPipe's isoDTB label quant, through the lab's site table (D66): 2 to 4 replicates, a few or many
              sites changed one way, with and without a heavy / light mixing error; limma or a t-test against 0;
              the "centring" grid compares ratio_centre none / median / auto (D70)
    tmt       MaxQuant reporter intensities of 2 or 3 TMT plexes with a pooled reference (D66): balanced and
              unbalanced channels, changes both ways or a pulldown one way; IRS on the pool, on the plex means, or
              none, with auto or median normalisation, or the plex as a block
    rollup    label-free peptide intensities (MSstats format, D76): peptides with their own offsets, missing values
              and interferences, rolled up to proteins by Tukey median polish or MaxLFQ (analysis.rollup), with
              and without imputation
    measured  sensitivity (planted changes found, right direction), the observed false discovery proportion
              (FDP) against the nominal alpha, the bias of the fold changes, the offset of the unchanged features

Real (`ionomos benchmark FOLDER --expected hye.yaml`): an analysed mixed-species (human / yeast / E. coli)
or spike-in experiment run on the lab's instrument, with the expected ratio per species or protein list:

    exp = load_expected("hye.yaml")
    res = real(experiment_folder, exp)   # measured vs expected per group, false positives in the unchanged
    write_real(res, results_dir)         # background, sensitivity among the changed: benchmark.tsv / .json / .html

The simulation is the pipeline's own loader, processing and statistics (run_pipeline(), the calls analyze()
makes), not a re-implementation; tests/test_benchmark.py checks the two give the same hits. It is still a
simulation: log-normal noise, missing values that depend on abundance, changes of one size. What it shows
is how the settings behave on such data, not how they behave on the lab's.
"""
from __future__ import annotations

import json
import math
import re
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from ionomos.downstream import analysis, anytable, fpa, quant, simulate, stats
from ionomos.downstream.tables import num, read_tsv, write_tsv

# ------------------------------------------------------------------ simulated --

# (label, analysis settings): each is run on the same simulated tables
SETTINGS = [
    ("perseus + median (default)", {"imputation": "perseus", "normalize": "median"}),
    ("none + median", {"imputation": "none", "normalize": "median"}),
    ("knn + median", {"imputation": "knn", "normalize": "median"}),
    ("minprob + median", {"imputation": "minprob", "normalize": "median"}),
    ("mindet + median", {"imputation": "mindet", "normalize": "median"}),
    ("min + median", {"imputation": "min", "normalize": "median"}),
    ("zero + median", {"imputation": "zero", "normalize": "median"}),
    ("perseus + gn", {"imputation": "perseus", "normalize": "gn"}),
    ("perseus, not normalised", {"imputation": "perseus", "normalize": "none"}),
    ("none, not normalised", {"imputation": "none", "normalize": "none"}),
]
MISSING = {"none": 0.0, "typical": 1.0, "heavy": 3.0}   # simulate.dia_pg_matrix(missing=...)
GRIDS = {
    # designs: (controls, treated); effects: planted |log2 fold change|
    "standard": {"designs": [(2, 2), (3, 3), (4, 4), (6, 6), (2, 4)], "effects": [0.585, 1.0, 2.0],
                 "missing": ["none", "typical", "heavy"], "settings": [s[0] for s in SETTINGS], "seeds": 5,
                 "proteins": 1000},
    "quick": {"designs": [(3, 3), (2, 4)], "effects": [1.0, 2.0], "missing": ["typical"],
              "settings": ["perseus + median (default)", "none + median", "knn + median", "none, not normalised"],
              "seeds": 2, "proteins": 600},
    # the calibration guard in pytest (tests/test_benchmark.py)
    "guard": {"designs": [(3, 3), (4, 4), (2, 4)], "effects": [2.0], "missing": ["typical"],
              "settings": ["perseus + median (default)", "none + median"], "seeds": 4, "proteins": 600},
}
CHANGED_FRACTION = 0.1
NOISE, NOISE_SPREAD = 0.3, 0.5     # replicate SD in log2, and how much it differs between proteins (log-normal)
BASE_SEED = 20261001
MIN_CALLS = 50   # a scenario's FDP is only quoted in a range when it rests on at least this many calls

# isoDTB site ratios (simulate.isodtb_ratios): each condition is tested against 0. Imputation and normalisation
# do not apply to ratio data (fpa.normalize_info, resolve_imputation), so the settings are the test.
ISODTB_SETTINGS = [
    ("limma (default)", {"test": "limma"}),
    ("t-test", {"test": "welch"}),
    ("limma, ratios centred: median", {"test": "limma", "ratio_centre": "median"}),   # D70
    ("limma, ratios centred: auto", {"test": "limma", "ratio_centre": "auto"}),
]
ISODTB_NOISE = 0.35
ISODTB_GRIDS = {
    # designs: (0, replicates); changed: (share of the sites, direction); mixing: SD of the heavy / light mixing
    # error per replicate (log2)
    "standard": {"designs": [(0, 2), (0, 3), (0, 4)], "effects": [1.0, 2.0], "changed": [(0.05, "up"), (0.2, "up")],
                 "mixing": [0.0, 0.2], "missing": ["typical"], "settings": [s[0] for s in ISODTB_SETTINGS],
                 "seeds": 5, "proteins": 900},
    "quick": {"designs": [(0, 3)], "effects": [2.0], "changed": [(0.05, "up"), (0.2, "up")], "mixing": [0.0, 0.2],
              "missing": ["typical"], "settings": ["limma (default)", "t-test"], "seeds": 2, "proteins": 600},
    "guard": {"designs": [(0, 3), (0, 4)], "effects": [2.0], "changed": [(0.1, "up")], "mixing": [0.0],
              "missing": ["typical"], "settings": ["limma (default)"], "seeds": 4, "proteins": 600},
    # D70: ratio_centre none / median / auto, with and without a mixing error, few or many sites one way
    "centring": {"designs": [(0, 3)], "effects": [1.0, 2.0], "changed": [(0.05, "up"), (0.2, "up")],
                 "mixing": [0.0, 0.2], "missing": ["typical"],
                 "settings": ["limma (default)", "limma, ratios centred: median", "limma, ratios centred: auto"],
                 "seeds": 20, "proteins": 900},
}

# TMT (simulate.tmt_plexes, MaxQuant's proteinGroups.txt): POOL_CHANNELS / POOL_SAMPLES stand for the pooled
# reference channels of the simulated plexes (tmt_reference) or their samples (left out when IRS doesn't use them)
POOL_CHANNELS, POOL_SAMPLES = "<pool channels>", "<pool samples>"
TMT_SETTINGS = [
    ("IRS on the pool + auto (default)", {"irs": "auto", "tmt_reference": POOL_CHANNELS, "normalize": "auto"}),
    ("IRS on the pool + median", {"irs": "auto", "tmt_reference": POOL_CHANNELS, "normalize": "median"}),
    ("IRS on plex means + auto", {"irs": "sum", "exclude_samples": POOL_SAMPLES, "normalize": "auto"}),
    ("no IRS + auto", {"irs": "none", "exclude_samples": POOL_SAMPLES, "normalize": "auto"}),
    ("no IRS, plex as a block", {"irs": "none", "exclude_samples": POOL_SAMPLES, "normalize": "auto",
                                 "block_from": r" (Exp\d+)$"}),
]
TMT_PLEX_SD = 1.0
TMT_GRIDS = {
    # designs: (DMSO, Drug) channels per plex (a TMT 10-plex, the rest pooled reference); plexes: how many
    "standard": {"designs": [(4, 4), (2, 6)], "plexes": [2, 3], "effects": [1.0, 2.0],
                 "changed": [(0.1, "both"), (0.2, "up")], "missing": ["typical"],
                 "settings": [s[0] for s in TMT_SETTINGS], "seeds": 5, "proteins": 1000},
    "quick": {"designs": [(4, 4)], "plexes": [3], "effects": [1.0], "changed": [(0.1, "both"), (0.2, "up")],
              "missing": ["typical"], "settings": [s[0] for s in TMT_SETTINGS], "seeds": 2, "proteins": 600},
    "guard": {"designs": [(4, 4), (2, 6)], "plexes": [2, 3], "effects": [1.0], "changed": [(0.1, "both")],
              "missing": ["typical"], "settings": ["IRS on the pool + auto (default)"], "seeds": 4, "proteins": 600},
    # the D71 guards: IRS on the plex means (its residual df), and a pulldown without IRS (the composition check
    # within plexes)
    "guard_sum": {"designs": [(4, 4), (2, 6)], "plexes": [2, 3], "effects": [1.0], "changed": [(0.1, "both")],
                  "missing": ["typical"], "settings": ["IRS on plex means + auto"], "seeds": 4, "proteins": 600},
    "guard_pulldown": {"designs": [(4, 4), (2, 6)], "plexes": [3], "effects": [1.0], "changed": [(0.2, "up")],
                       "missing": ["typical"], "settings": ["no IRS, plex as a block", "no IRS + auto"], "seeds": 4,
                       "proteins": 600},
}
# the roll-up (D76): peptide tables (simulate.peptide_msstats) rolled up by each analysis.rollup, then the defaults
ROLLUP_KIND_SETTINGS = [
    ("median polish + perseus (default)", {"rollup": "median_polish", "imputation": "perseus", "normalize": "median"}),
    ("MaxLFQ + perseus", {"rollup": "maxlfq", "imputation": "perseus", "normalize": "median"}),
    ("median polish, no imputation", {"rollup": "median_polish", "imputation": "none", "normalize": "median"}),
    ("MaxLFQ, no imputation", {"rollup": "maxlfq", "imputation": "none", "normalize": "median"}),
]
ROLLUP_GRIDS = {
    "standard": {"designs": [(3, 3), (4, 4), (6, 6), (2, 4)], "effects": [0.585, 1.0, 2.0],
                 "missing": ["typical", "heavy"], "settings": [s[0] for s in ROLLUP_KIND_SETTINGS], "seeds": 5,
                 "proteins": 800},
    "quick": {"designs": [(3, 3)], "effects": [1.0], "missing": ["typical"],
              "settings": [s[0] for s in ROLLUP_KIND_SETTINGS], "seeds": 2, "proteins": 500},
    "guard": {"designs": [(3, 3), (2, 4)], "effects": [1.0], "missing": ["typical"],
              "settings": ["median polish + perseus (default)", "MaxLFQ + perseus"], "seeds": 3, "proteins": 500},
}
KINDS = {
    "dia": "label-free DIA protein matrices (DIA-NN's report.pg_matrix.tsv)",
    "isodtb": "isoDTB site ratios (FragPipe's label quant, merged into sites as the lab's R script does)",
    "tmt": "TMT reporter intensities of several plexes with a pooled reference (MaxQuant's proteinGroups.txt)",
    "rollup": "label-free peptide intensities (the MSstats format) rolled up to proteins: median polish vs MaxLFQ",
}
FILE_STEM = {"dia": "benchmark_simulated", "isodtb": "benchmark_simulated_isodtb", "tmt": "benchmark_simulated_tmt",
             "rollup": "benchmark_simulated_rollup"}
SIM_NAMES = {"dia": "DIA", "rollup": "peptide"}   # the data's name in reference_name, where it is not the kind

SIM_COLUMNS = ["setting", "imputation", "normalize", "controls", "treated", "effect_log2", "missing", "seeds",
               "planted", "tested_share", "hits", "false_hits", "sensitivity", "fdp", "sensitivity_alpha_only",
               "fdp_alpha_only", "fdp_alpha_only_max_seed", "fc_bias_changed", "fc_offset_unchanged",
               "fc_offset_unchanged_abs", "kind", "test", "irs", "plexes", "changed_share", "direction", "mixing_sd",
               "ratio_centre", "rollup"]


def run_pipeline(m, settings: analysis.Settings):
    """The processing and statistics of downstream.analyze() on a loaded matrix, without the report:
    (fpa.Processed, [analysis.DiffResult]). Several TMT plexes are put on one scale first (plex.normalise, as
    analyze() does); anything else passes that step unchanged."""
    from ionomos import downstream
    from ionomos.downstream import plex

    m = plex.normalise(m, settings)[0]
    p, _notes = fpa.process(m, exclude=settings.exclude_samples, conditions=settings.sample_conditions,
                            contaminants=settings.remove_contaminants, global_pct=settings.filter_global_pct,
                            condition_pct=settings.filter_condition_pct, normalization=settings.normalize,
                            imputation=settings.imputation, shift=settings.impute_shift, scale=settings.impute_scale,
                            seed=settings.seed, ratio_centre=settings.ratio_centre)
    if not p.m.features:
        return p, []
    comps, _ = analysis.choose_comparisons(p.m, settings)
    bad, _n, _small = downstream._guard(p, comps, settings)
    low = {comps[k] for k, _ in bad}
    model = analysis.make_model(p.m, settings, comps)
    results = analysis.run_contrasts(p, comps, settings, low, model)
    return p, [analysis.to_diff(p, r, c, settings) for r, (_t, c) in zip(results, comps, strict=True)]


def score(d, truth: dict[str, int], effect: float, alpha: float, key: str = "label") -> dict:
    """One comparison against the planted truth {gene: +1 / -1} (key: the row field the truth is keyed by).
    Counts, so seeds can be pooled; null_abs is this table's |mean fold change of the unchanged features|."""
    out = {"planted": len(truth), "tested": 0, "features": len(d.rows), "hits": 0, "false": 0, "true": 0,
           "hits_q": 0, "false_q": 0, "true_q": 0, "bias_sum": 0.0, "bias_n": 0, "null_sum": 0.0, "null_n": 0,
           "null_abs": 0.0}
    for r in d.rows:
        sign = truth.get(r[key], 0)
        fc, q = r["log2fc"], r["qvalue"]
        out["tested"] += r["pvalue"] is not None
        if fc is not None:
            if sign:
                out["bias_sum"] += (fc - sign * effect) * sign
                out["bias_n"] += 1
            else:
                out["null_sum"] += fc
                out["null_n"] += 1
        if r["significant"]:
            right = sign and (sign > 0) == (r["significant"] == "up")
            out["hits"] += 1
            out["true" if right else "false"] += 1
        if q is not None and fc is not None and q <= alpha:
            right = sign and (sign > 0) == (fc > 0)
            out["hits_q"] += 1
            out["true_q" if right else "false_q"] += 1
    out["null_abs"] = abs(out["null_sum"] / out["null_n"]) if out["null_n"] else 0.0
    return out


class _Table:
    """One simulated table, loaded by the pipeline's own loader, with its truth."""

    def __init__(self, m, truth: dict[str, int], comparison: tuple[str, str | None], key: str = "label",
                 base: dict | None = None, pools: tuple[list[str], list[str]] = ([], []),
                 by_rollup: dict | None = None):
        self.m, self.truth, self.comparison, self.key = m, truth, comparison, key
        self.by_rollup = by_rollup or {}  # analysis.rollup -> the table loaded with it (the roll-up kind, D76)
        self.base = base or {}        # settings every setting of this kind needs (the design of the TMT channels)
        self.pools = pools            # (pool channels, pool samples) of a TMT table


def _table(folder: Path, controls: int, treated: int, effect: float, missing: float, proteins: int, seed: int):
    runs = [(f"DMSO_{r}.raw", "DMSO") for r in range(1, controls + 1)] + \
           [(f"Drug_{r}.raw", "Drug") for r in range(1, treated + 1)]
    path = folder / "report.pg_matrix.tsv"
    truth = simulate.dia_pg_matrix(path, runs, seed=seed, n_proteins=proteins, changed_fraction=CHANGED_FRACTION,
                                   effect=effect, noise=NOISE, noise_spread=NOISE_SPREAD, missing=missing)
    return quant.from_pg_matrix(path), truth["Drug"]


def _dia_table(folder: Path, cell: dict, proteins: int, seed: int) -> _Table:
    m, truth = _table(folder, cell["controls"], cell["treated"], cell["effect_log2"], MISSING[cell["missing"]],
                      proteins, seed)
    return _Table(m, truth, ("Drug", "DMSO"))


def _isodtb_table(folder: Path, cell: dict, proteins: int, seed: int) -> _Table:
    """FragPipe's label quant -> the lab's site table (isodtb.py) -> the site matrix, as analyze() reads it."""
    from ionomos import downstream
    from ionomos.downstream import isodtb

    sub = folder / f"isodtb_{seed}"
    lq = sub / isodtb.LABEL_FILE
    truth = simulate.isodtb_ratios(lq, {"Cmpd": cell["treated"]}, seed=seed, n_sites=proteins,
                                   changed_fraction=cell["changed_share"], effect=cell["effect_log2"],
                                   direction=cell["direction"], mixing_sd=cell["mixing_sd"], noise=ISODTB_NOISE,
                                   noise_spread=NOISE_SPREAD, missing=MISSING[cell["missing"]])
    sites = isodtb.write_site_tables(lq, sub)
    return _Table(downstream._merge_ratio([quant.from_isodtb_sites(p) for p in sites]), truth["Cmpd"], ("Cmpd", None),
                  key="id")


def _tmt_table(folder: Path, cell: dict, proteins: int, seed: int) -> _Table:
    """Several TMT plexes as MaxQuant writes them (engines.load_maxquant: experiments are plexes); the channels'
    conditions come in as sample_conditions, as an SDRF or the Analysis tab would give them."""
    from ionomos.downstream import engines

    path = folder / f"tmt_{seed}" / "combined" / "txt" / "proteinGroups.txt"
    conds, truth = simulate.tmt_plexes(path, plexes=cell["plexes"], controls=cell["controls"], treated=cell["treated"],
                                       seed=seed, n_proteins=proteins, changed_fraction=cell["changed_share"],
                                       effect=cell["effect_log2"], direction=cell["direction"], plex_sd=TMT_PLEX_SD,
                                       noise=0.25, noise_spread=NOISE_SPREAD, missing=MISSING[cell["missing"]])
    m = engines.load_maxquant(path)
    channel = m.meta.get("channel") or {}
    pool_samples = [s for s, c in conds.items() if c == "Pool"]
    pool_channels = sorted({channel[s] for s in pool_samples if s in channel})
    return _Table(m, truth, ("Drug", "DMSO"), key="id", base={"sample_conditions": conds},
                  pools=(pool_channels, pool_samples))


def _rollup_table(folder: Path, cell: dict, proteins: int, seed: int) -> _Table:
    """Peptides in the MSstats format, loaded once per roll-up by the pipeline's own loader (engines.load_msstats)."""
    from ionomos.downstream import engines

    runs = [(f"DMSO_{r}", "DMSO") for r in range(1, cell["controls"] + 1)] + \
           [(f"Drug_{r}", "Drug") for r in range(1, cell["treated"] + 1)]
    path = folder / f"rollup_{seed}" / "msstats_input.tsv"
    truth = simulate.peptide_msstats(path, runs, seed=seed, n_proteins=proteins, changed_fraction=CHANGED_FRACTION,
                                     effect=cell["effect_log2"], noise=NOISE, noise_spread=NOISE_SPREAD,
                                     missing=MISSING[cell["missing"]])
    loaded = {method: engines.load_msstats(path, method) for method in ("median_polish", "maxlfq")}
    return _Table(loaded["median_polish"], truth["Drug"], ("Drug", "DMSO"), by_rollup=loaded)


def _cells(kind: str, g: dict, designs: list[tuple]) -> list[dict]:
    """The scenarios of a grid: one dict per cell, every kind with the same keys."""
    out = []
    for d in designs:
        c, t = d[0], d[1]
        for plexes in (([d[2]] if len(d) > 2 else g.get("plexes", [0])) if kind == "tmt" else [0]):
            for e in g["effects"]:
                for share, direction in g.get("changed", [(CHANGED_FRACTION, "both")]):
                    for mixing in g.get("mixing", [0.0]):
                        for miss in g["missing"]:
                            out.append({"controls": c, "treated": t, "plexes": plexes, "effect_log2": e,
                                        "changed_share": share, "direction": direction, "mixing_sd": mixing,
                                        "missing": miss})
    return out


def _describe(kind: str, cell: dict) -> str:
    fold = f"{2 ** cell['effect_log2']:.3g}-fold"
    if kind in ("dia", "rollup"):
        return f"{cell['controls']} vs {cell['treated']} samples, {fold}, missing values {cell['missing']}"
    changed = f"{cell['changed_share']:.0%} changed {'one way' if cell['direction'] == 'up' else 'both ways'}"
    if kind == "isodtb":
        return f"{cell['treated']} replicates, {fold}, {changed}, mixing error SD {cell['mixing_sd']:g}"
    return f"{cell['plexes']} plexes of {cell['controls']} vs {cell['treated']}, {fold}, {changed}"


def _resolve(over: dict, tab: _Table) -> dict:
    """A setting with the pool placeholders replaced by this table's pool channels / samples."""
    out = {**tab.base, **over}
    if out.get("tmt_reference") == POOL_CHANNELS:
        out["tmt_reference"] = list(tab.pools[0])
    if out.get("exclude_samples") == POOL_SAMPLES:
        out["exclude_samples"] = list(tab.pools[1])
    return out


def _pick(diffs: list, comparison: tuple[str, str | None]):
    return next((d for d in diffs if (d.treatment, d.control) == comparison), None)


def kind_settings(kind: str) -> list[tuple[str, dict]]:
    return {"dia": SETTINGS, "isodtb": ISODTB_SETTINGS, "tmt": TMT_SETTINGS, "rollup": ROLLUP_KIND_SETTINGS}[kind]


def simulated(grid: str | dict = "standard", extra_settings: list[tuple[str, dict]] | None = None,
              designs: list[tuple] | None = None, seeds: int | None = None, alpha: float = 0.05,
              log2fc: float = 1.0, progress=None, kind: str = "dia") -> dict:
    """Run the grid. extra_settings: [(label, analysis settings)] added to the grid's own (a lab's settings);
    designs: extra (controls, treated) — for isodtb (0, replicates), for tmt (controls, treated[, plexes]) per
    plex. Deterministic: the seeds are fixed."""
    if kind not in KINDS:
        raise BenchmarkError(f"unknown kind {kind!r} (known: {', '.join(KINDS)})")
    grids = {"dia": GRIDS, "isodtb": ISODTB_GRIDS, "tmt": TMT_GRIDS, "rollup": ROLLUP_GRIDS}[kind]
    g = dict(grids[grid]) if isinstance(grid, str) else dict(grid)
    known = dict(kind_settings(kind))
    todo = [(name, known[name]) for name in g["settings"]] + list(extra_settings or [])
    all_designs = list(g["designs"]) + [d for d in designs or [] if d not in g["designs"]]
    if kind == "tmt":
        all_designs = [d for d in all_designs if d[0] + d[1] <= 9]  # a TMT 10-plex, with a pooled reference
    n_seeds = seeds or g["seeds"]
    table = {"dia": _dia_table, "isodtb": _isodtb_table, "tmt": _tmt_table, "rollup": _rollup_table}[kind]
    rows = []
    cells = _cells(kind, g, all_designs)
    with tempfile.TemporaryDirectory(prefix="ionomos-benchmark-") as td:
        for k, cell in enumerate(cells):
            if progress:
                progress(f"{k + 1} of {len(cells)}: {_describe(kind, cell)}")
            pooled = {name: [] for name, _ in todo}
            for s in range(n_seeds):
                tab = table(Path(td), cell, g["proteins"], BASE_SEED + 1000 * k + s)
                for name, over in todo:
                    st = analysis.settings_from({"alpha": alpha, "log2fc": log2fc, "enrichment": False,
                                                 **_resolve(over, tab)})
                    _p, diffs = run_pipeline(tab.by_rollup.get(st.rollup, tab.m), st)
                    d = _pick(diffs, tab.comparison)
                    if d is not None:
                        pooled[name].append(score(d, tab.truth, cell["effect_log2"], alpha, tab.key))
            for name, over in todo:
                rows.append(_pool(name, over, cell, pooled[name], kind))
    noise = {"dia": NOISE, "isodtb": ISODTB_NOISE, "tmt": 0.25, "rollup": NOISE}[kind]
    return {"kind": "simulated", "data": kind, "data_description": KINDS[kind],
            "grid": grid if isinstance(grid, str) else "custom", "alpha": alpha, "log2fc": log2fc,
            "proteins": g["proteins"], "changed_fraction": CHANGED_FRACTION if kind in ("dia", "rollup") else None,
            "noise_sd": noise,
            "noise_spread": NOISE_SPREAD, "seeds": n_seeds, "rows": rows,
            "settings": [name for name, _ in todo], "generated_at": datetime.now().isoformat(timespec="seconds"),
            "reference_name": f"simulated {SIM_NAMES.get(kind, kind)} data with planted changes",
            "verdicts": _sim_verdicts(rows, alpha, log2fc)}


def like(folder: str | Path) -> dict:
    """An analysed experiment as a benchmark setting: its imputation, normalisation, IRS, filter and test, its
    cut-offs, its group sizes as a design, and the kind of data (dia: any protein intensities; isodtb: site
    ratios; tmt: TMT plexes). {"label", "settings", "design", "kind", "alpha", "log2fc", "results", "analysis"}."""
    from ionomos.downstream.compare import find_analysis

    results = find_analysis(Path(folder))
    if results is None:
        raise BenchmarkError(f"{folder} holds no Ionomos analysis (no results/analysis.json)")
    info = json.loads((results / "analysis.json").read_text(encoding="utf-8"))
    st = info.get("settings") or {}
    tmt = info.get("tmt") if isinstance(info.get("tmt"), dict) else None
    kind = "isodtb" if info.get("level") == "site" and not (info.get("phospho") or {}).get("ran") else "tmt" if tmt and tmt.get("plexes") else "dia"
    keys = {"dia": ("imputation", "normalize", "test", "filter_condition_pct", "filter_global_pct", "min_valid",
                    "small_group_min_valid", "impute_shift", "impute_scale"),
            "isodtb": ("test", "min_valid", "ratio_centre"),
            "tmt": ("normalize", "test", "filter_condition_pct", "filter_global_pct", "min_valid",
                    "small_group_min_valid", "irs")}[kind]
    over = {k: st[k] for k in keys if k in st}
    if over.get("ratio_centre") == "none":  # the default: the setting's label stays as it was before D70
        del over["ratio_centre"]
    if kind == "dia" and info.get("imputation") in fpa.IMPUTATION_METHODS:
        over["imputation"] = info["imputation"]  # what "auto" resolved to for this data
    if kind == "tmt":  # the simulated plexes' own pooled channels stand in for the experiment's reference
        if over.get("irs", "auto") in ("sum", "none"):
            over["exclude_samples"] = POOL_SAMPLES
        else:
            over["tmt_reference"] = POOL_CHANNELS
    sizes: dict[str, int] = {}
    for cond in (info.get("samples") or {}).values():
        sizes[cond] = sizes.get(cond, 0) + 1
    design = None
    for c in info.get("comparisons") or []:
        name = str(c.get("name", ""))
        if kind == "isodtb":
            t = name.split(" (")[0]
            if sizes.get(t):
                design = (0, sizes[t])
                break
            continue
        t, _, ctrl = name.partition(" vs ")
        if sizes.get(t) and sizes.get(ctrl):
            design = (sizes[ctrl], sizes[t])
            if kind == "tmt":
                n = len(tmt.get("plexes") or {}) or 1
                design = (max(1, round(sizes[ctrl] / n)), max(1, round(sizes[t] / n)), n)
            break
    exp = results.parent.name if results.name == "results" else results.name
    label = {"dia": f"{exp}: {over.get('imputation', 'auto')} + {over.get('normalize', 'auto')}",
             "isodtb": f"{exp}: {over.get('test', 'limma')}"
                       + (f", ratios centred: {over['ratio_centre']}" if over.get("ratio_centre", "none") != "none"
                          else ""),
             "tmt": f"{exp}: IRS {over.get('irs', 'auto')} + {over.get('normalize', 'auto')}"}[kind]
    return {"label": label, "settings": over, "design": design, "kind": kind, "alpha": float(st.get("alpha", 0.05)),
            "log2fc": float(st.get("log2fc", 1.0)), "results": results,
            "analysis": {"generated_at": info.get("generated_at"), "ionomos_version": info.get("ionomos_version"),
                         "settings_digest": (info.get("trust") or {}).get("settings_digest", "")}}


def _ratio(a: float, b: float) -> float | None:
    return a / b if b else None


def _pool(name: str, over: dict, cell: dict, runs: list[dict], kind: str = "dia") -> dict:
    tot = {k: sum(r[k] for r in runs) for k in (runs[0] if runs else {})}
    per_seed = [r["false_q"] / r["hits_q"] for r in runs if r["hits_q"]]
    ratio = kind == "isodtb"  # imputation and normalisation do not apply to ratios tested against 0
    return {"setting": name, "kind": kind, "imputation": "none" if ratio else over.get("imputation", "auto"),
            "normalize": "none" if ratio else over.get("normalize", "auto"), "test": over.get("test", "limma"),
            "irs": over.get("irs", "auto") if kind == "tmt" else "", "controls": cell["controls"],
            "ratio_centre": over.get("ratio_centre", "none") if ratio else "",
            "rollup": over.get("rollup", "") if kind == "rollup" else "",
            "treated": cell["treated"], "plexes": cell["plexes"] or "", "effect_log2": cell["effect_log2"],
            "changed_share": cell["changed_share"], "direction": cell["direction"], "mixing_sd": cell["mixing_sd"],
            "missing": cell["missing"], "seeds": len(runs),
            "fc_offset_unchanged_abs": _ratio(tot.get("null_abs", 0.0), len(runs)),
            "planted": tot.get("planted", 0), "tested_share": _ratio(tot.get("tested", 0), tot.get("features", 0)),
            "hits": tot.get("hits", 0), "false_hits": tot.get("false", 0),
            "sensitivity": _ratio(tot.get("true", 0), tot.get("planted", 0)),
            "fdp": _ratio(tot.get("false", 0), tot.get("hits", 0)),
            "sensitivity_alpha_only": _ratio(tot.get("true_q", 0), tot.get("planted", 0)),
            "fdp_alpha_only": _ratio(tot.get("false_q", 0), tot.get("hits_q", 0)),
            "fdp_alpha_only_max_seed": max(per_seed) if per_seed else None,
            "false_alpha_only": tot.get("false_q", 0), "hits_alpha_only": tot.get("hits_q", 0),
            "fc_bias_changed": _ratio(tot.get("bias_sum", 0.0), tot.get("bias_n", 0)),
            "fc_offset_unchanged": _ratio(tot.get("null_sum", 0.0), tot.get("null_n", 0))}


def by_setting(rows: list[dict]) -> list[dict]:
    """Each setting over the whole grid: pooled FDP and its range, mean sensitivity, mean bias."""
    out = []
    for name in dict.fromkeys(r["setting"] for r in rows):
        rs = [r for r in rows if r["setting"] == name]
        fdps = [r["fdp_alpha_only"] for r in rs if r["hits_alpha_only"] >= MIN_CALLS]
        sens = [r["sensitivity"] for r in rs if r["sensitivity"] is not None]
        sens_q = [r["sensitivity_alpha_only"] for r in rs if r["sensitivity_alpha_only"] is not None]
        bias = [r["fc_bias_changed"] for r in rs if r["fc_bias_changed"] is not None]
        offs = [r["fc_offset_unchanged_abs"] for r in rs if r.get("fc_offset_unchanged_abs") is not None]
        by_effect = {}
        for e in dict.fromkeys(r["effect_log2"] for r in rs):
            es = [r for r in rs if r["effect_log2"] == e]
            by_effect[f"{2 ** e:.3g}"] = {
                "sensitivity": _ratio(sum((r["sensitivity"] or 0) * r["planted"] for r in es),
                                      sum(r["planted"] for r in es)),
                "sensitivity_alpha_only": _ratio(sum((r["sensitivity_alpha_only"] or 0) * r["planted"] for r in es),
                                                 sum(r["planted"] for r in es))}
        out.append({"setting": name, "scenarios": len(rs), "scenarios_with_enough_calls": len(fdps),
                    "fdp_alpha_only": _ratio(sum(r["false_alpha_only"] for r in rs), sum(r["hits_alpha_only"] for r in rs)),
                    "fdp_alpha_only_min": min(fdps) if fdps else None, "fdp_alpha_only_max": max(fdps) if fdps else None,
                    "fdp": _ratio(sum(r["false_hits"] for r in rs), sum(r["hits"] for r in rs)),
                    "sensitivity": sum(sens) / len(sens) if sens else None,
                    "sensitivity_alpha_only": sum(sens_q) / len(sens_q) if sens_q else None,
                    "sensitivity_by_fold": by_effect,
                    "fc_bias_changed": sum(bias) / len(bias) if bias else None,
                    "fc_offset_unchanged_abs": sum(offs) / len(offs) if offs else None,
                    "kind": rs[0].get("kind", "dia")})
    return out


def _pc(v: float | None, digits: int = 1) -> str:
    return "–" if v is None else f"{100 * v:.{digits}f}%"


def _sim_verdicts(rows: list[dict], alpha: float, log2fc: float) -> list[str]:
    out = []
    for s in by_setting(rows):
        f = s["fdp_alpha_only"]
        if f is None:
            out.append(f"{s['setting']}: no hits in any scenario")
            continue
        word = "at or below" if f <= alpha else "above"
        span = (f"; {_pc(s['fdp_alpha_only_min'])} to {_pc(s['fdp_alpha_only_max'])} over the "
                f"{s['scenarios_with_enough_calls']} scenarios with at least {MIN_CALLS} calls"
                if s["scenarios_with_enough_calls"] else "")
        found = ", ".join(f"{fold}-fold {_pc(v['sensitivity_alpha_only'], 0)}" for fold, v in
                          s["sensitivity_by_fold"].items())
        out.append(f"{s['setting']}: observed FDP {_pc(f)} at adjusted p ≤ {alpha:g} ({word} the nominal "
                   f"{_pc(alpha, 0)}{span}), {_pc(s['fdp'])} with |log2FC| ≥ {log2fc:g} as well; planted changes "
                   f"found at adjusted p ≤ {alpha:g}: {found}; fold changes of real changes off by "
                   f"{s['fc_bias_changed']:+.2f} log2 on average" +
                   (f", unchanged features by {s['fc_offset_unchanged_abs']:.2f} (|mean| per table)"
                    if s["kind"] not in ("dia", "rollup") and s["fc_offset_unchanged_abs"] is not None else ""))
    return out


def _sim_page(res: dict) -> str:
    from ionomos.downstream import plots

    alpha = res["alpha"]
    kind = res.get("data", "dia")
    stem = FILE_STEM[kind]
    summ = by_setting(res["rows"])
    what = {
        "dia": (f"on simulated DIA protein tables with known truth: {res['proteins']:,} proteins, "
                f"{res.get('changed_fraction') or CHANGED_FRACTION:.0%} of them changed by a planted fold change, "
                "replicate SD "
                f"{res['noise_sd']} log2 (differing between proteins), a loading difference per run, and missing "
                "values that are more likely at low abundance"),
        "isodtb": ("on simulated isoDTB label quantification (FragPipe's combined_modified_peptide_label_quant.tsv, "
                   f"merged into sites as the lab's R script does) with known truth: {res['proteins']:,} "
                   f"cysteines, a share of them with a planted heavy / light ratio, replicate SD {res['noise_sd']} "
                   "log2 (differing between sites), one or two peptides per site, weak peptides missing more often, "
                   "and in some scenarios a mixing error per replicate (heavy and light not mixed exactly 1:1, which "
                   "moves every ratio of that replicate). Each compound is tested against 0; ratios are not "
                   "normalised or imputed"),
        "tmt": (f"on simulated TMT experiments with known truth (MaxQuant's proteinGroups.txt): {res['proteins']:,} "
                "proteins in 2 or 3 TMT 10-plexes, each with a pooled reference channel, a plex effect per protein "
                f"(SD {TMT_PLEX_SD} log2), a loading difference per channel, replicate SD {res['noise_sd']} log2 "
                "(differing between proteins), proteins missing from whole plexes more often at low abundance; "
                "a share of the proteins changed both ways, or one way (a pulldown)"),
        "rollup": (f"on simulated label-free peptide tables (the MSstats format) with known truth: {res['proteins']:,} "
                   "proteins of 1 to 30 peptides, each peptide with its own ionisation offset, "
                   f"{res.get('changed_fraction') or CHANGED_FRACTION:.0%} of the proteins changed by a planted fold "
                   f"change, replicate SD {res['noise_sd']} log2 (differing between proteins), a loading difference "
                   "per run, weak peptides missing more often, and 2% of the values off by an interference; the "
                   "peptides are rolled up to proteins by Tukey median polish or by MaxLFQ (analysis.rollup)"),
    }[kind]
    b = [f"<p class='sub'>The Ionomos pipeline (loader, plexes, filter, normalisation, imputation, limma, "
         f"Benjamini–Hochberg) {what}. {res['seeds']} simulated tables per scenario, pooled. Hits: adjusted p ≤ "
         f"{alpha:g} and |log2FC| ≥ {res['log2fc']:g}; \"alpha only\" drops the fold-change cut-off, which is the case "
         "Benjamini–Hochberg makes its promise about.</p>",
         "<div class='notes'>This is a simulation. It shows what each setting does to data of this kind; it does not "
         "show how the lab's samples behave. For that, run a sample with known ratios on the instrument "
         "(<code>ionomos benchmark FOLDER --expected hye.yaml</code>).</div>",
         "<section><h2>Each setting over the whole grid</h2><div class='card findings'><ul>" +
         "".join(f"<li>{escape(v)}</li>" for v in res["verdicts"]) + "</ul></div>"]
    b.append("<div class='card chart'>" + plots.dot_rows(
        [(s["setting"], [r["fdp_alpha_only"] for r in res["rows"] if r["setting"] == s["setting"]
                         and r["hits_alpha_only"] >= MIN_CALLS]) for s in summ],
        f"observed false discovery proportion at adjusted p ≤ {alpha:g}",
        "Observed false discovery proportion per setting", ref=alpha, ref_label=f"nominal {alpha:g}") + "</div>"
        f"<p class='sub'>A dot per scenario with at least {MIN_CALLS} calls; the bar is their mean; the dashed line is "
        "the nominal alpha.</p>")
    b.append("<div class='tablewrap'><table><thead><tr><th>Setting</th><th>FDP, alpha only</th><th>lowest to highest "
             "scenario</th><th>FDP, both cut-offs</th><th>sensitivity, both cut-offs</th><th>sensitivity, alpha only"
             "</th><th>log2FC bias of real changes</th></tr></thead><tbody>")
    for s in summ:
        b.append(f"<tr><td>{escape(s['setting'])}</td><td class='n'>{_pc(s['fdp_alpha_only'])}</td>"
                 f"<td class='n'>{_pc(s['fdp_alpha_only_min'])} to {_pc(s['fdp_alpha_only_max'])}</td>"
                 f"<td class='n'>{_pc(s['fdp'])}</td><td class='n'>{_pc(s['sensitivity'], 0)}</td>"
                 f"<td class='n'>{_pc(s['sensitivity_alpha_only'], 0)}</td>"
                 f"<td class='n'>{'–' if s['fc_bias_changed'] is None else format(s['fc_bias_changed'], '+.2f')}</td></tr>")
    b.append("</tbody></table></div></section><section><h2>Every scenario</h2><div class='tablewrap'><table><thead><tr>"
             "<th>Setting</th><th>samples</th><th>planted fold</th><th>changed</th><th>missing values</th>"
             "<th>tested</th><th>hits</th>"
             "<th>false</th><th>sensitivity</th><th>FDP</th><th>sensitivity, alpha only</th><th>FDP, alpha only</th>"
             "<th>worst seed</th><th>log2FC bias</th><th>offset of unchanged</th></tr></thead><tbody>")
    for r in res["rows"]:
        def f2(v):
            return "–" if v is None else format(v, "+.2f")

        rk = r.get("kind", "dia")
        samples = (f"{r['controls']} vs {r['treated']}" if rk in ("dia", "rollup") else
                   f"{r['treated']} replicates" + (f", mixing error SD {r['mixing_sd']:g}" if r["mixing_sd"] else "")
                   if rk == "isodtb" else f"{r['plexes']} plexes × {r['controls']} vs {r['treated']}")
        changed = f"{r['changed_share']:.0%} {'one way' if r['direction'] == 'up' else 'both ways'}"
        b.append(f"<tr><td>{escape(r['setting'])}</td><td class='n'>{escape(samples)}</td>"
                 f"<td class='n'>{2 ** r['effect_log2']:.3g}×</td><td>{changed}</td><td>{escape(r['missing'])}</td>"
                 f"<td class='n'>{_pc(r['tested_share'], 0)}</td><td class='n'>{r['hits']:,}</td>"
                 f"<td class='n'>{r['false_hits']:,}</td><td class='n'>{_pc(r['sensitivity'], 0)}</td>"
                 f"<td class='n'>{_pc(r['fdp'])}</td><td class='n'>{_pc(r['sensitivity_alpha_only'], 0)}</td>"
                 f"<td class='n'>{_pc(r['fdp_alpha_only'])}</td><td class='n'>{_pc(r['fdp_alpha_only_max_seed'])}</td>"
                 f"<td class='n'>{f2(r['fc_bias_changed'])}</td><td class='n'>{f2(r['fc_offset_unchanged'])}</td></tr>")
    b.append("</tbody></table></div><p class='sub'>Sensitivity: planted changes called, in the right direction, of all "
             "planted (also those the missing-value filter removed). FDP: false calls (unchanged proteins, or the "
             "wrong direction) among the calls. Bias: estimated minus planted log2 fold change, signed so that "
             "negative means underestimated. Offset of unchanged: their mean log2 fold change, pooled (signed; "
             "the per-table size without its sign is in the TSV). The same numbers are in "
             f"<a href='{stem}.tsv'>{stem}.tsv</a> and <a href='{stem}.json'>{stem}.json</a>.</p></section>")
    title = {"dia": "simulated data", "isodtb": "simulated isoDTB data", "tmt": "simulated TMT data",
             "rollup": "simulated peptide data (roll-up)"}[kind]
    return plots.page(f"Ionomos benchmark: {title}", f"generated {res['generated_at']} · grid {res['grid']}",
                      "".join(b))


def write_simulated(res: dict, out_dir: Path) -> list[Path]:
    """benchmark_simulated.tsv / .json / .html in out_dir (created; Ionomos' own files); isoDTB and TMT results
    are benchmark_simulated_isodtb.* and benchmark_simulated_tmt.*, so the three kinds can sit side by side."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = FILE_STEM[res.get("data", "dia")]
    files = [write_tsv(out_dir / f"{stem}.tsv", SIM_COLUMNS, res["rows"])]
    (out_dir / f"{stem}.json").write_text(
        json.dumps({**res, "by_setting": by_setting(res["rows"])}, indent=2, default=str), encoding="utf-8")
    (out_dir / f"{stem}.html").write_text(_sim_page(res), encoding="utf-8")
    return files + [out_dir / f"{stem}.json", out_dir / f"{stem}.html"]


# ----------------------------------------------------------------------- real --

# UniProt organism mnemonics and the names people write for them
SPECIES = {
    "HUMAN": ("homo sapiens", "human", "h. sapiens"),
    "YEAST": ("saccharomyces cerevisiae", "yeast", "s. cerevisiae", "baker's yeast"),
    "ECOLI": ("escherichia coli", "e. coli", "e.coli", "ecoli"),
    "MOUSE": ("mus musculus", "mouse"),
    "RAT": ("rattus norvegicus", "rat"),
    "CAEEL": ("caenorhabditis elegans", "c. elegans", "worm"),
    "ARATH": ("arabidopsis thaliana", "arabidopsis"),
    "DROME": ("drosophila melanogaster", "drosophila", "fly"),
    "BOVIN": ("bos taurus", "bovine", "cow"),
    "DANRE": ("danio rerio", "zebrafish"),
}
_ENTRY = re.compile(r"(?<![A-Za-z0-9])[A-Z0-9]{1,12}_([A-Z][A-Z0-9]{2,4})(?![A-Za-z0-9])")
_OS = re.compile(r"OS=(.+?)(?=\s+(?:OX|GN|PE|SV)=|$)")
REAL_COLUMNS = ["id", "label", "group", "expected_log2", "log2fc", "error", "pvalue", "qvalue", "significant", "call"]


class BenchmarkError(ValueError):
    pass


def _canon(name: str) -> str:
    """'e. coli' / 'Escherichia coli (strain K12)' / 'ecoli' -> 'ECOLI'; an unknown name -> itself, upper case."""
    low = re.sub(r"\s+", " ", str(name).strip().lower())
    for mnem, names in SPECIES.items():
        if low == mnem.lower() or any(low == n or low.startswith(n + " ") for n in names):
            return mnem
    if low.startswith("escherichia coli") or re.fullmatch(r"eco[a-z0-9]{2}", low):
        return "ECOLI"  # the strains have their own mnemonics (ECO57, ECOBD ...)
    return str(name).strip().upper()


def load_expected(path: str | Path) -> dict:
    """The small YAML that says what the truth is. See docs/VALIDATION.md.

        comparison: B vs A            # optional when the analysis has one comparison
        expected: {HUMAN: 1, YEAST: 2, ECOLI: 0.25}    # ratio B / A per group; 1 = the unchanged background
        log2: false                   # true: the numbers are log2 ratios
        species_column: Organism      # optional: a column of the quant table that names the species
        fasta: path/to/search.fasta   # optional: species per accession from the FASTA headers
        proteins: {UPS1: ups1.txt}    # optional: protein lists (a file, or the accessions / genes) as groups
        tolerance_log2: 0.25          # optional: how far a group's median may be from expected (default 0.25)
    """
    import yaml

    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BenchmarkError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("expected"), dict) or not data["expected"]:
        raise BenchmarkError(f"{path.name} needs an expected: mapping, e.g. expected: {{HUMAN: 1, YEAST: 2, ECOLI: 0.25}}")
    unknown = set(data) - {"comparison", "expected", "log2", "species_column", "fasta", "proteins", "tolerance_log2",
                           "table", "notes"}
    if unknown:
        raise BenchmarkError(f"{path.name}: unknown key(s) {', '.join(sorted(map(str, unknown)))}")
    is_log = bool(data.get("log2", False))
    groups: dict[str, float] = {}
    for name, v in data["expected"].items():
        x = num(v)
        if x is None or (not is_log and x <= 0):
            raise BenchmarkError(f"{path.name}: expected {name}: {v!r} is not a ratio above 0"
                                 + ("" if not is_log else " (a log2 number)"))
        groups[str(name)] = x if is_log else math.log2(x)
    lists: dict[str, set[str]] = {}
    for name, spec in (data.get("proteins") or {}).items():
        if isinstance(spec, str):
            f = Path(spec) if Path(spec).is_absolute() else path.parent / spec
            try:
                items = re.split(r"[\s,;]+", f.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError) as exc:
                raise BenchmarkError(f"{path.name}: protein list {name}: cannot read {f}: {exc}") from exc
        elif isinstance(spec, list):
            items = [str(x) for x in spec]
        else:
            raise BenchmarkError(f"{path.name}: proteins {name} must be a file name or a list")
        lists[str(name)] = {x.strip().upper() for x in items if x.strip()}
        if str(name) not in groups:
            raise BenchmarkError(f"{path.name}: protein list {name} has no expected ratio")
    tol = num(data.get("tolerance_log2", 0.25))
    if tol is None or tol <= 0:
        raise BenchmarkError(f"{path.name}: tolerance_log2 must be a number above 0")
    fasta = data.get("fasta")
    if fasta and not Path(str(fasta)).is_absolute():
        fasta = str(path.parent / str(fasta))
    table = data.get("table")
    if table and not Path(str(table)).is_absolute():
        table = str(path.parent / str(table))
    return {"file": path.name, "comparison": data.get("comparison"), "groups": groups, "lists": lists,
            "species_column": data.get("species_column"), "fasta": fasta, "table": table, "tolerance_log2": tol}


def read_fasta_species(path: str | Path) -> dict[str, set[str]]:
    """accession (upper case) -> the species its FASTA header names (entry-name suffix and OS=), canonical."""
    out: dict[str, set[str]] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith(">"):
                continue
            head = line[1:].strip()
            first = head.split(None, 1)[0]
            if first.lower().startswith(("rev_", "decoy_", "reverse_")):
                continue
            parts = first.split("|")
            acc = (parts[1] if len(parts) >= 3 else first).upper()
            found = {_canon(m.group(1)) for m in _ENTRY.finditer(first)}
            os_ = _OS.search(head)
            if os_:
                found.add(_canon(os_.group(1)))
            if found:
                out.setdefault(acc, set()).update(found)
    return out


def _species_in(text: str) -> set[str]:
    found = {_canon(m.group(1)) for m in _ENTRY.finditer(text)}
    found |= {_canon(m.group(1)) for m in _OS.finditer(text)}
    return found


def _source_species(table: Path, ids: list[str], column: str | None, notes: list[str]) -> dict[str, set[str]]:
    """id -> species named anywhere in its row of the quant table (or in species_column only)."""
    try:
        header, rows = anytable.read_table(table)
    except (anytable.TableError, OSError, UnicodeError) as exc:
        notes.append(f"the quant table {table.name} could not be read for species ({exc})")
        return {}
    want = set(ids)
    best, hits = None, 0
    for j in range(len(header)):
        n = sum(1 for r in rows if r[j].strip() in want)
        if n > hits:
            best, hits = j, n
    if best is None or hits < 0.5 * len(want):
        notes.append(f"{table.name}: no column holds the feature IDs of this analysis, so it was not used for species")
        return {}
    col = None
    if column:
        col = next((j for j, h in enumerate(header) if h.lower() == str(column).lower()), None)
        if col is None:
            raise BenchmarkError(f"species_column {column!r} is not a column of {table.name} (columns: " +
                                 ", ".join(header[:12]) + ("…" if len(header) > 12 else "") + ")")
    out: dict[str, set[str]] = {}
    for r in rows:
        fid = r[best].strip()
        if fid not in want:
            continue
        if col is not None:
            got = {_canon(x) for x in re.split(r"\s*;\s*", r[col].strip()) if x.strip()}
        else:
            got = _species_in(" ".join(r))
        if got:
            out.setdefault(fid, set()).update(got)
    return out


def assign_groups(ids: list[str], labels: list[str], descs: list[str], exp: dict, table: Path | None,
                  notes: list[str]) -> list[str]:
    """Each feature's group: a name from expected, "mixed" (members of several groups: left out), or ""
    (no group: left out). Evidence, in this order: the protein lists, species_column, entry names / OS= in the
    feature's own text and in its row of the quant table, the FASTA."""
    from ionomos.downstream.compare import _tokens

    canon = {_canon(g): g for g in exp["groups"]}
    lists = exp["lists"]
    from_table = _source_species(table, ids, exp.get("species_column"), notes) if table is not None else {}
    if exp.get("species_column") and table is None:
        raise BenchmarkError("species_column needs the quant table; it was not found (give table: in the YAML)")
    fasta: dict[str, set[str]] = {}
    if exp.get("fasta"):
        try:
            fasta = read_fasta_species(exp["fasta"])
        except OSError as exc:
            raise BenchmarkError(f"cannot read the FASTA {exp['fasta']}: {exc}") from exc
    out = []
    for fid, lab, desc in zip(ids, labels, descs, strict=True):
        toks = _tokens(fid)
        hit = {name for name, members in lists.items() if members & ({*toks, lab.strip().upper()} - {""})}
        if not hit:
            found = set(from_table.get(fid, ())) if exp.get("species_column") else \
                _species_in(f"{fid} {lab} {desc}") | from_table.get(fid, set())
            for t in toks:
                found |= fasta.get(t, set())
            hit = {canon[s] for s in found if s in canon}
            if hit and any(s in SPECIES and s not in canon for s in found):
                hit.add("?")  # a protein group that also holds a species without an expected ratio
        out.append("" if not hit else next(iter(hit)) if len(hit) == 1 else "mixed")
    return out


def real(folder: str | Path, exp: dict) -> dict:
    """Measured against expected for an analysed experiment. folder: the experiment folder or its results/."""
    from ionomos.downstream.compare import CompareError, find_analysis

    results = find_analysis(Path(folder))
    if results is None:
        raise BenchmarkError(f"{folder} holds no Ionomos analysis (no results/analysis.json). Run `ionomos analyze` "
                             "on the benchmark experiment first")
    info = json.loads((results / "analysis.json").read_text(encoding="utf-8"))
    comps = info.get("comparisons") or []
    if not comps:
        raise BenchmarkError("the analysis has no comparison: check its conditions and control, then analyse again")
    want = exp.get("comparison")
    if want:
        pick = [c for c in comps if c["name"].lower() == str(want).lower()] or \
               [c for c in comps if str(want).lower() in c["name"].lower()]
        flipped = False
        if not pick:  # named the other way round: flip the expected ratios
            from ionomos.downstream.compare import _split

            pick = [c for c in comps if _split(c["name"]) == _split(str(want))[::-1] and _split(str(want))[1]]
            flipped = bool(pick)
        if len(pick) != 1:
            raise BenchmarkError(f"comparison {want!r} is not in the analysis (it has: " +
                                 ", ".join(c["name"] for c in comps) + ")")
        comp = pick[0]
    elif len(comps) == 1:
        comp, flipped = comps[0], False
    else:
        raise BenchmarkError("the analysis has several comparisons; name one with comparison: in the YAML (" +
                             ", ".join(c["name"] for c in comps) + ")")
    tsv = results / Path(comp["table"]).name
    if not tsv.is_file():
        raise BenchmarkError(f"{tsv} is missing; analyse the experiment again")
    _h, rows = read_tsv(tsv)
    st = info.get("settings") or {}
    alpha, lfc, adj = float(st.get("alpha", 0.05)), float(st.get("log2fc", 1.0)), bool(st.get("use_adjusted", True))
    notes: list[str] = []
    table = None
    for cand in (exp.get("table"), info.get("source")):
        if cand and Path(str(cand)).is_file():
            table = Path(str(cand))
            break
    if table is None and info.get("source"):
        notes.append(f"the quant table {Path(str(info['source'])).name} is no longer where the analysis read it; "
                     "species come from the feature names" + (" and the FASTA" if exp.get("fasta") else ""))
    try:
        groups = assign_groups([r["id"] for r in rows], [r["label"] for r in rows], [r["description"] for r in rows],
                               exp, table, notes)
    except CompareError as exc:
        raise BenchmarkError(str(exc)) from exc
    sign = -1.0 if flipped else 1.0
    if flipped:
        notes.append(f"the analysis has {comp['name']}, the expected ratios are for {want}: they were inverted")
    expected = {g: sign * v for g, v in exp["groups"].items()}
    per: dict[str, dict] = {g: {"group": g, "expected_log2": v, "features": 0, "values": [], "tested": 0, "hits": 0,
                                "right": 0, "wrong": 0, "hits_q": 0, "right_q": 0, "wrong_q": 0}
                            for g, v in expected.items()}
    out_rows = []
    for r, g in zip(rows, groups, strict=True):
        fc, p, q = num(r["log2fc"]), num(r["pvalue"]), num(r["qvalue"])
        sig = r["significant"] if r["significant"] in ("up", "down") else ""
        call = ""
        if g in per:
            x = per[g]
            x["features"] += 1
            e = x["expected_log2"]
            if fc is not None:
                x["values"].append(fc)
            x["tested"] += p is not None
            want_dir = "" if e == 0 else "up" if e > 0 else "down"
            if sig:
                x["hits"] += 1
                x["right" if sig == want_dir else "wrong"] += 1
                call = "true positive" if sig == want_dir else "false positive"
            elif want_dir and p is not None:
                call = "missed"
            score_ = q if adj else p
            if score_ is not None and fc is not None and score_ <= alpha:
                d = "up" if fc > 0 else "down"
                x["hits_q"] += 1
                x["right_q" if d == want_dir else "wrong_q"] += 1
        out_rows.append([r["id"], r["label"], g, per[g]["expected_log2"] if g in per else None, fc,
                         fc - per[g]["expected_log2"] if g in per and fc is not None else None, p, q, sig, call])
    summary = []
    for x in per.values():
        v = x.pop("values")
        e = x["expected_log2"]
        x.update({"quantified": len(v), "median": stats.median(v) if v else None,
                  "mad": stats.mad(v) if len(v) >= 2 else None,
                  "q25": stats.quantile(v, 0.25) if v else None, "q75": stats.quantile(v, 0.75) if v else None,
                  "bias": (stats.median(v) - e) if v else None, "changed": e != 0,
                  "expected_ratio": 2 ** e})
        if e == 0:
            x["false_positive_rate"] = _ratio(x["hits"], x["tested"])
            x["false_positive_rate_alpha_only"] = _ratio(x["hits_q"], x["tested"])
        else:
            x["sensitivity"] = _ratio(x["right"], x["tested"])
            x["sensitivity_alpha_only"] = _ratio(x["right_q"], x["tested"])
        x["_values"] = v
        summary.append(x)
    unassigned = sum(1 for g in groups if g == "")
    mixed = sum(1 for g in groups if g == "mixed")
    if mixed:
        notes.append(f"{mixed:,} protein groups hold members of more than one group and were left out")
    if unassigned:
        notes.append(f"{unassigned:,} features belong to no group with an expected ratio and were left out")
    if not any(x["quantified"] for x in summary):
        raise BenchmarkError("no feature could be given a group. Species are read from UniProt entry names "
                             "(ACTB_HUMAN), OS= in FASTA headers, a species_column, or a FASTA (fasta:). " +
                             " ".join(notes))
    false = sum(x["hits"] for x in summary if not x["changed"]) + sum(x["wrong"] for x in summary if x["changed"])
    hits = sum(x["hits"] for x in summary)
    false_q = sum(x["hits_q"] for x in summary if not x["changed"]) + sum(x["wrong_q"] for x in summary if x["changed"])
    hits_q = sum(x["hits_q"] for x in summary)
    res = {"kind": "real", "experiment": results.parent.name if results.name == "results" else results.name,
           "comparison": comp["name"], "expected_file": exp["file"], "reference_name": exp["file"],
           "alpha": alpha, "log2fc": lfc, "use_adjusted": adj, "tolerance_log2": exp["tolerance_log2"],
           "groups": summary, "hits": hits, "false_hits": false, "fdp": _ratio(false, hits),
           "hits_alpha_only": hits_q, "false_hits_alpha_only": false_q, "fdp_alpha_only": _ratio(false_q, hits_q),
           "features": len(rows), "unassigned": unassigned, "mixed": mixed, "notes": notes,
           "analysis": {"generated_at": info.get("generated_at"), "ionomos_version": info.get("ionomos_version"),
                        "settings_digest": (info.get("trust") or {}).get("settings_digest", "")},
           "imputation": info.get("imputation"), "normalize": st.get("normalize"),
           "generated_at": datetime.now().isoformat(timespec="seconds"), "_rows": out_rows}
    res["verdicts"] = _real_verdicts(res)
    return res


def _real_verdicts(res: dict) -> list[str]:
    out = []
    tol = res["tolerance_log2"]
    cut = "adjusted p" if res["use_adjusted"] else "p"
    for x in res["groups"]:
        if not x["quantified"]:
            out.append(f"{x['group']}: no feature quantified")
            continue
        ok = abs(x["bias"]) <= tol
        line = (f"{x['group']}: measured median log2 ratio {x['median']:+.2f}, expected {x['expected_log2']:+.2f} "
                f"({'within' if ok else 'off by more than'} {tol:g}; bias {x['bias']:+.2f}, spread MAD "
                f"{0.0 if x['mad'] is None else x['mad']:.2f}, n {x['quantified']:,})")
        if x["changed"]:
            line += (f"; {_pc(x.get('sensitivity'), 0)} called at the analysis' cut-offs, "
                     f"{_pc(x.get('sensitivity_alpha_only'), 0)} at {cut} ≤ {res['alpha']:g} alone")
            if abs(x["expected_log2"]) <= res["log2fc"]:
                line += (f" (the expected change is not above the |log2FC| ≥ {res['log2fc']:g} cut-off, so at most "
                         "about half can pass it)")
        else:
            line += (f"; {_pc(x.get('false_positive_rate'), 2)} of this unchanged background called "
                     f"({x['hits']:,} of {x['tested']:,}; {_pc(x.get('false_positive_rate_alpha_only'), 2)} at {cut} ≤ "
                     f"{res['alpha']:g} alone)")
        out.append(line)
    if res["hits_alpha_only"]:
        out.append(f"false discovery proportion: {_pc(res['fdp_alpha_only'])} of {res['hits_alpha_only']:,} calls at "
                   f"{cut} ≤ {res['alpha']:g} (nominal {_pc(res['alpha'], 0)}); {_pc(res['fdp'])} of {res['hits']:,} "
                   f"calls with |log2FC| ≥ {res['log2fc']:g} as well")
    else:
        out.append("no feature was called, so there is no false discovery proportion")
    return out


def _real_page(res: dict) -> str:
    from ionomos.downstream import plots

    b = [f"<p class='sub'>Comparison <b>{escape(res['comparison'])}</b> of the Ionomos analysis of "
         f"<b>{escape(res['experiment'])}</b>, against the expected ratios in {escape(res['expected_file'])}. "
         f"Settings of the analysis: imputation {escape(str(res['imputation']))}, normalisation "
         f"{escape(str(res['normalize']))}, hits at {'adjusted p' if res['use_adjusted'] else 'p'} ≤ {res['alpha']:g} "
         f"and |log2FC| ≥ {res['log2fc']:g}.</p>",
         "<div class='card findings'><ul>" + "".join(f"<li>{escape(v)}</li>" for v in res["verdicts"]) + "</ul></div>"]
    if res["notes"]:
        b.append("<div class='notes'><ul>" + "".join(f"<li>{escape(n)}</li>" for n in res["notes"]) + "</ul></div>")
    b.append("<div class='card chart'>" + plots.group_boxes(
        [{"name": x["group"], "values": x.get("_values") or [], "expected": x["expected_log2"]} for x in res["groups"]],
        f"measured log2 fold change ({res['comparison']})", "Measured against expected log2 ratio per group") +
        "</div><p class='sub'>Box: quartiles; whiskers: 1.5 × the interquartile range; dashed line: the expected "
        "ratio. Median normalisation centres the whole sample: when most of the protein is the unchanged background, "
        "the background sits at 0; when the mix is far from that, every group shifts together.</p>")
    b.append("<div class='tablewrap'><table><thead><tr><th>Group</th><th>expected ratio</th><th>expected log2</th>"
             "<th>features</th><th>quantified</th><th>median log2</th><th>bias</th><th>MAD</th><th>quartiles</th>"
             "<th>called</th><th>right direction</th><th>wrong direction</th><th>rate</th></tr></thead><tbody>")
    for x in res["groups"]:
        def f2(v):
            return "–" if v is None else format(v, "+.2f")

        rate = (f"sensitivity {_pc(x.get('sensitivity'), 0)}" if x["changed"]
                else f"false positive rate {_pc(x.get('false_positive_rate'), 2)}")
        b.append(f"<tr><td>{escape(x['group'])}</td><td class='n'>{x['expected_ratio']:.3g}</td>"
                 f"<td class='n'>{f2(x['expected_log2'])}</td><td class='n'>{x['features']:,}</td>"
                 f"<td class='n'>{x['quantified']:,}</td><td class='n'>{f2(x['median'])}</td>"
                 f"<td class='n'>{f2(x['bias'])}</td><td class='n'>{'–' if x['mad'] is None else format(x['mad'], '.2f')}</td>"
                 f"<td class='n'>{f2(x['q25'])} to {f2(x['q75'])}</td><td class='n'>{x['hits']:,}</td>"
                 f"<td class='n'>{x['right']:,}</td><td class='n'>{x['wrong']:,}</td><td>{rate}</td></tr>")
    b.append("</tbody></table></div><p class='sub'>Called: significant at the analysis' own cut-offs. For the "
             "unchanged background every call is a false positive. Every feature with its group, expected and "
             "measured value is in <a href='benchmark.tsv'>benchmark.tsv</a>; these numbers are in "
             "<a href='benchmark.json'>benchmark.json</a>.</p>")
    return plots.page(f"{res['experiment']}: benchmark against known ratios", f"generated {res['generated_at']} · "
                      f"Ionomos {res['analysis'].get('ionomos_version') or ''}", "".join(b))


def write_real(res: dict, out_dir: Path) -> list[Path]:
    """benchmark.tsv / .json / .html in out_dir (the experiment's results folder; Ionomos' own files)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [write_tsv(out_dir / "benchmark.tsv", REAL_COLUMNS, res.get("_rows", []))]
    html = _real_page(res)
    public = {k: v for k, v in res.items() if not k.startswith("_")}
    public["groups"] = [{k: v for k, v in g.items() if not k.startswith("_")} for g in res["groups"]]
    (out_dir / "benchmark.json").write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    (out_dir / "benchmark.html").write_text(html, encoding="utf-8")
    return files + [out_dir / "benchmark.json", out_dir / "benchmark.html"]
