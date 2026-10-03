"""`ionomos benchmark` (downstream/benchmark.py, D60): accuracy against known truth.

    calibration guard   the pipeline on simulated tables with planted changes: the observed false discovery
                        proportion must stay near the nominal alpha, the sensitivity must not drop; one guard
                        each for label-free DIA, isoDTB site ratios and TMT plexes (D66)
    simulated           what the grid measures and writes; that it is the pipeline analyze() runs; what a
                        mixing error (isoDTB) and a pulldown (TMT) do, which the roadmap holds as open questions
    real                a simulated mixed-species (human / yeast / E. coli) experiment through analyze() and
                        then against its expected ratios; species from entry names, a column, a FASTA, lists

No real benchmark run exists yet: the "real" path is tested on simulate.mixed_species_pg_matrix, which has
the layout of a DIA-NN matrix of such a sample, not its noise."""
from __future__ import annotations

import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ionomos import cli, downstream
from ionomos.downstream import analysis, benchmark, engines, quant, simulate
from ionomos.downstream.tables import read_tsv

CFG = {"enrichment": False}

# ---- tolerances of the calibration guard, and where they come from (measured 2026-10-01).
# The guard pools 10 simulated tables (600 proteins, 60 planted 4-fold changes each) per design. With 30
# other blocks of 10 seeds, the pooled FDP at adjusted p <= 0.05 was, per design:
#     imputation none:     mean 4.9 - 5.3 %, SD 0.9 - 1.1 %, range 2.9 - 7.9 %
#     Perseus (default):   mean 2.9 - 3.3 %, SD 0.7 - 1.0 %, range 1.6 - 5.3 %
# (Benjamini-Hochberg with 90 % unchanged proteins aims at 4.5 %.) The limit is the worst mean plus about
# 3.5 SD. Sensitivity ranged 0.72 - 0.81 (Perseus) and 0.84 - 0.96 (none); the floors sit below the lowest.
FDP_MAX = 0.085
FDP_POOLED_RANGE = (0.02, 0.07)      # "none + median" over the three designs together (30 tables)
SENSITIVITY_MIN = {"perseus + median (default)": 0.68, "none + median": 0.80}
BIAS_MAX = 0.15


@pytest.fixture(scope="module")
def guard():
    return benchmark.simulated("guard", seeds=10)


def test_calibration_guard_fdp_stays_near_nominal(guard):
    assert guard["alpha"] == 0.05 and len(guard["rows"]) == 6
    for r in guard["rows"]:
        where = f"{r['setting']}, {r['controls']} vs {r['treated']}"
        assert r["seeds"] == 10 and r["planted"] > 500 and r["hits_alpha_only"] > 400, where
        assert r["fdp_alpha_only"] <= FDP_MAX, f"{where}: observed FDP {r['fdp_alpha_only']:.3f}"
        assert r["fdp"] <= r["fdp_alpha_only"] + 0.01, f"{where}: the fold-change cut-off must not add false calls"
    none = [r for r in guard["rows"] if r["setting"] == "none + median"]
    pooled = sum(r["false_alpha_only"] for r in none) / sum(r["hits_alpha_only"] for r in none)
    assert FDP_POOLED_RANGE[0] <= pooled <= FDP_POOLED_RANGE[1], f"pooled FDP {pooled:.3f}: a test that never errs is broken too"


def test_calibration_guard_sensitivity_and_bias(guard):
    for r in guard["rows"]:
        where = f"{r['setting']}, {r['controls']} vs {r['treated']}"
        assert r["sensitivity_alpha_only"] >= SENSITIVITY_MIN[r["setting"]], f"{where}: {r['sensitivity_alpha_only']:.3f}"
        assert abs(r["fc_bias_changed"]) <= BIAS_MAX and abs(r["fc_offset_unchanged"]) <= BIAS_MAX, where
        assert r["tested_share"] > 0.9
    by = {(r["setting"], r["controls"], r["treated"]): r for r in guard["rows"]}
    for design in ((3, 3), (4, 4), (2, 4)):  # what Perseus-type imputation costs on this data: fewer found
        assert by[("none + median", *design)]["sensitivity_alpha_only"] > \
            by[("perseus + median (default)", *design)]["sensitivity_alpha_only"]


def test_the_simulated_benchmark_is_deterministic(guard):
    again = benchmark.simulated("guard", seeds=10)
    assert again["rows"] == guard["rows"] and again["verdicts"] == guard["verdicts"]


def test_what_the_settings_cost_shows_in_the_numbers():
    """Heavy missingness, 3 vs 3, 4-fold changes: zeros as imputation bias the fold changes, no normalisation
    leaves the loading differences in (an offset of the unchanged proteins, more false calls)."""
    grid = {"designs": [(3, 3)], "effects": [2.0], "missing": ["heavy"], "seeds": 6, "proteins": 600,
            "settings": ["perseus + median (default)", "none + median", "zero + median", "none, not normalised"]}
    res = benchmark.simulated(grid)
    r = {x["setting"]: x for x in res["rows"]}
    assert r["zero + median"]["fc_bias_changed"] > 1.0 > abs(r["none + median"]["fc_bias_changed"]) * 5
    assert abs(r["none, not normalised"]["fc_offset_unchanged"]) > 0.1 > abs(r["none + median"]["fc_offset_unchanged"])
    assert r["none, not normalised"]["fdp_alpha_only"] > r["none + median"]["fdp_alpha_only"] + 0.02
    assert r["none + median"]["tested_share"] < 1.0 == r["perseus + median (default)"]["tested_share"]
    assert any(v.startswith("zero + median: observed FDP") and "off by +1." in v for v in res["verdicts"])
    by = benchmark.by_setting(res["rows"])
    assert [s["setting"] for s in by] == grid["settings"] and by[0]["sensitivity_by_fold"]["4"]["sensitivity"] > 0.4


def test_the_benchmark_runs_the_pipeline_analyze_runs(tmp_path):
    runs = [(f"{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    table = tmp_path / "e/fragpipe/report.pg_matrix.tsv"
    simulate.dia_pg_matrix(table, runs, seed=4, n_proteins=400, noise_spread=0.5)
    for over in ({}, {"imputation": "none"}, {"imputation": "knn", "normalize": "gn"}, {"test": "welch"}):
        out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg={**CFG, **over})
        _h, want = read_tsv(tmp_path / "e/results/Drug_vs_DMSO_differential.tsv")
        _p, diffs = benchmark.run_pipeline(quant.from_pg_matrix(table), analysis.settings_from({**CFG, **over}))
        got = diffs[0]
        assert [r["id"] for r in got.rows] == [r["id"] for r in want]
        assert [r["significant"] or "" for r in got.rows] == [r["significant"] if r["significant"] != "NA" else ""
                                                             for r in want]
        assert (got.up, got.down) == (out.summary["comparisons"][0]["up"], out.summary["comparisons"][0]["down"])


def test_score_counts_right_wrong_and_missed():
    rows = [{"label": "UP1", "log2fc": 2.1, "pvalue": 1e-6, "qvalue": 1e-4, "significant": "up"},      # found
            {"label": "UP2", "log2fc": 0.8, "pvalue": 1e-3, "qvalue": 0.01, "significant": ""},        # only at alpha
            {"label": "DOWN1", "log2fc": 1.5, "pvalue": 1e-4, "qvalue": 0.02, "significant": "up"},    # wrong direction
            {"label": "NULL1", "log2fc": -1.2, "pvalue": 1e-4, "qvalue": 0.03, "significant": "down"},  # false
            {"label": "NULL2", "log2fc": 0.1, "pvalue": 0.6, "qvalue": 0.8, "significant": ""},
            {"label": "UP3", "log2fc": None, "pvalue": None, "qvalue": None, "significant": ""}]       # not tested

    class D:
        pass

    d = D()
    d.rows = rows
    s = benchmark.score(d, {"UP1": 1, "UP2": 1, "UP3": 1, "DOWN1": -1, "GONE": 1}, 2.0, 0.05)
    assert (s["planted"], s["tested"], s["hits"], s["true"], s["false"]) == (5, 5, 3, 1, 2)
    assert (s["hits_q"], s["true_q"], s["false_q"]) == (4, 2, 2)
    assert s["bias_n"] == 3 and s["bias_sum"] == pytest.approx((2.1 - 2) + (0.8 - 2) + (-1.5 - 2))
    assert s["null_n"] == 2 and s["null_sum"] == pytest.approx(-1.1)


def test_the_new_simulation_options_leave_the_old_tables_as_they_were(tmp_path):
    runs = [(f"{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    a = simulate.dia_pg_matrix(tmp_path / "a.tsv", runs, seed=6, n_proteins=90)
    b = simulate.dia_pg_matrix(tmp_path / "b.tsv", runs, seed=6, n_proteins=90, noise=0.3, noise_spread=0.0, missing=1.0)
    assert a == b and (tmp_path / "a.tsv").read_bytes() == (tmp_path / "b.tsv").read_bytes()
    simulate.dia_pg_matrix(tmp_path / "c.tsv", runs, seed=6, n_proteins=90, missing=0.0)
    assert "\t\t" not in (tmp_path / "c.tsv").read_text(encoding="utf-8") and "\t\n" not in (tmp_path / "c.tsv").read_text(encoding="utf-8")


def _self_contained(html: str) -> list[ET.Element]:
    low = html.lower()
    for external in ("src=", "<link", "@import", "url(", "<script"):
        assert external not in low, external
    svgs = re.findall(r"<svg.*?</svg>", html, re.S)
    return [ET.fromstring(s.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)) for s in svgs]


def test_simulated_files_and_cli(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--grid", "quick", "--quiet"]) == 0
    printed = capsys.readouterr().out
    assert "simulated benchmark, grid quick" in printed and "perseus + median (default): observed FDP" in printed
    out = tmp_path / "ionomos_benchmark"
    header, rows = read_tsv(out / "benchmark_simulated.tsv")
    assert header == benchmark.SIM_COLUMNS and len(rows) == 2 * 2 * 1 * 4
    data = json.loads((out / "benchmark_simulated.json").read_text(encoding="utf-8"), parse_constant=lambda c: pytest.fail(c))
    assert data["kind"] == "simulated" and len(data["by_setting"]) == 4 and data["seeds"] == 2
    svgs = _self_contained((out / "benchmark_simulated.html").read_text(encoding="utf-8"))
    assert len(svgs) == 1 and "This is a simulation" in (out / "benchmark_simulated.html").read_text(encoding="utf-8")
    assert cli.main(["benchmark", "--expected", "x.yaml"]) == 2 and "needs both" in capsys.readouterr().err


# ------------------------------------------------------- isoDTB and TMT (D66) --

# Tolerances of the two guards below, and where they come from (measured 2026-10-02, 30 other blocks of 10 seeds
# each, the guard grids of benchmark.ISODTB_GRIDS / TMT_GRIDS, pooled FDP at adjusted p <= 0.05 per row):
#     isoDTB, limma, 3 and 4 replicates, 10 % of 600 sites up 4-fold:
#         mean 4.9 - 5.1 %, SD 1.0 %, range 3.3 - 7.5 %; sensitivity 0.94 - 0.99; |bias| <= 0.024
#     TMT, IRS on the pool + auto, 2 and 3 plexes of 4 vs 4 and 2 vs 6, 10 % of 600 proteins 2-fold, both ways:
#         mean 4.5 - 4.8 %, SD 0.8 - 1.0 %, range 2.6 - 7.0 %; sensitivity 0.89 - 0.98; |bias| <= 0.016
# (Benjamini-Hochberg with 90 % unchanged aims at 4.5 %.) The limits are the worst mean plus about 3.5 SD.
ISODTB_FDP_MAX, ISODTB_SENSITIVITY_MIN, ISODTB_BIAS_MAX = 0.09, 0.90, 0.06
TMT_FDP_MAX, TMT_SENSITIVITY_MIN, TMT_BIAS_MAX = 0.085, 0.85, 0.05


@pytest.fixture(scope="module")
def iso_guard():
    return benchmark.simulated("guard", seeds=10, kind="isodtb")


@pytest.fixture(scope="module")
def tmt_guard():
    return benchmark.simulated("guard", seeds=10, kind="tmt")


def test_isodtb_calibration_guard(iso_guard):
    """Site ratios from FragPipe's label quant through the lab's site table, each tested against 0 with limma."""
    assert iso_guard["data"] == "isodtb" and len(iso_guard["rows"]) == 2
    for r in iso_guard["rows"]:
        where = f"{r['treated']} replicates"
        assert r["kind"] == "isodtb" and r["imputation"] == r["normalize"] == "none" and r["planted"] > 500, where
        assert r["hits_alpha_only"] > 400 and r["fdp_alpha_only"] <= ISODTB_FDP_MAX, (where, r["fdp_alpha_only"])
        assert r["fdp"] <= r["fdp_alpha_only"] + 0.01, where
        assert r["sensitivity_alpha_only"] >= ISODTB_SENSITIVITY_MIN, (where, r["sensitivity_alpha_only"])
        assert abs(r["fc_bias_changed"]) <= ISODTB_BIAS_MAX and r["fc_offset_unchanged_abs"] < 0.03, where
        assert r["tested_share"] > 0.95


def test_tmt_calibration_guard(tmt_guard):
    """Two and three TMT plexes as MaxQuant writes them, a pooled reference in each: IRS, normalisation, limma."""
    assert tmt_guard["data"] == "tmt" and len(tmt_guard["rows"]) == 4
    for r in tmt_guard["rows"]:
        where = f"{r['plexes']} plexes of {r['controls']} vs {r['treated']}"
        assert r["kind"] == "tmt" and r["irs"] == "auto" and r["planted"] > 400, where
        assert r["hits_alpha_only"] > 400 and r["fdp_alpha_only"] <= TMT_FDP_MAX, (where, r["fdp_alpha_only"])
        assert r["fdp"] <= r["fdp_alpha_only"] + 0.01, where
        assert r["sensitivity_alpha_only"] >= TMT_SENSITIVITY_MIN, (where, r["sensitivity_alpha_only"])
        assert abs(r["fc_bias_changed"]) <= TMT_BIAS_MAX and r["fc_offset_unchanged_abs"] < 0.05, where
        assert r["tested_share"] > 0.95


def test_the_isodtb_and_tmt_benchmarks_are_deterministic(iso_guard):
    assert benchmark.simulated("guard", seeds=10, kind="isodtb")["rows"] == iso_guard["rows"]
    a, b = (benchmark.simulated("guard", seeds=2, kind="tmt") for _ in range(2))
    assert a["rows"] == b["rows"] and a["verdicts"] == b["verdicts"]


def test_a_mixing_error_moves_every_isodtb_ratio_and_nothing_corrects_it():
    """Heavy and light mixed 15 % off 1:1 (SD 0.2 log2 per replicate) moves every ratio of the replicate: ratio data
    is not normalised (fpa.normalize_info), so the unchanged sites sit off 0 and the test against 0 calls more of
    them. A known limitation, on the roadmap with these numbers (D66)."""
    grid = {"designs": [(0, 3)], "effects": [2.0], "changed": [(0.05, "up")], "mixing": [0.0, 0.2], "missing": ["typical"],
            "settings": ["limma (default)"], "seeds": 8, "proteins": 900}
    clean, mixed = benchmark.simulated(grid, kind="isodtb")["rows"]
    assert clean["mixing_sd"] == 0 and mixed["mixing_sd"] == 0.2
    assert clean["fc_offset_unchanged_abs"] < 0.02 and mixed["fc_offset_unchanged_abs"] > 0.06
    assert mixed["fdp_alpha_only"] > clean["fdp_alpha_only"]


def test_a_tmt_pulldown_needs_irs_and_auto_normalisation():
    """20 % of the proteins up 2-fold in Drug (a pulldown) over 3 plexes: median centring after IRS shifts every
    unchanged protein down and the FDP at alpha alone runs away; auto (D64) switches to the ratio method and holds.
    Without IRS the plex effect stays in: the plain model loses most of its power, and a plex block gets the power
    back but not the normalisation, which can't see the composition through the plex effect."""
    grid = {"designs": [(4, 4)], "plexes": [3], "effects": [1.0], "changed": [(0.2, "up")], "missing": ["typical"],
            "settings": [s for s, _ in benchmark.TMT_SETTINGS], "seeds": 4, "proteins": 600}
    r = {x["setting"]: x for x in benchmark.simulated(grid, kind="tmt")["rows"]}
    default, median = r["IRS on the pool + auto (default)"], r["IRS on the pool + median"]
    assert abs(default["fc_offset_unchanged"]) < 0.03 and default["fdp_alpha_only"] <= TMT_FDP_MAX
    assert median["fc_offset_unchanged"] < -0.12 and median["fdp_alpha_only"] > 0.2
    assert median["fdp"] <= 0.01  # the fold-change cut-off still keeps them out of the hits
    assert r["no IRS + auto"]["sensitivity_alpha_only"] < 0.6 * default["sensitivity_alpha_only"]
    block = r["no IRS, plex as a block"]
    assert block["sensitivity_alpha_only"] > 0.9 and block["fc_offset_unchanged"] < -0.12
    assert abs(r["IRS on plex means + auto"]["fc_offset_unchanged"]) < 0.03


def test_isodtb_and_tmt_benchmarks_run_the_pipeline_analyze_runs(tmp_path):
    # isoDTB: the label quant of an experiment folder, analysed, against the benchmark's own path
    iso = tmp_path / "iso"
    lq = iso / "fragpipe" / "combined_modified_peptide_label_quant.tsv"
    simulate.isodtb_ratios(lq, {"Cmpd": 3}, seed=8, n_sites=300, changed_fraction=0.1, mixing_sd=0.1)
    downstream.analyze(iso, "isoDTB", analysis_cfg=CFG)
    _h, want = read_tsv(iso / "results" / "Cmpd_log2_H_L_vs_0_differential.tsv")
    tab = benchmark._isodtb_table(tmp_path / "t", {"treated": 3, "changed_share": 0.1, "effect_log2": 2.0,
                                                   "direction": "up", "mixing_sd": 0.1, "missing": "typical"}, 300, 8)
    _p, diffs = benchmark.run_pipeline(tab.m, analysis.settings_from(CFG))
    assert [r["id"] for r in diffs[0].rows] == [r["id"] for r in want]
    assert [r["significant"] or "" for r in diffs[0].rows] == [r["significant"].replace("NA", "") for r in want]
    # TMT: MaxQuant's proteinGroups.txt of three plexes, analysed with the channels' conditions and the pool
    mq = tmp_path / "mq"
    conds, _truth = simulate.tmt_plexes(mq / "combined" / "txt" / "proteinGroups.txt", plexes=3, seed=8, n_proteins=300)
    cfg = {**CFG, "sample_conditions": conds, "tmt_reference": ["126", "131"]}
    out = downstream.analyze(mq, analysis_cfg=cfg)
    _h, want = read_tsv(out.results_dir / "Drug_vs_DMSO_differential.tsv")
    assert json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))["tmt"]["applied"]
    m = engines.load_maxquant(mq / "combined" / "txt" / "proteinGroups.txt")
    _p, diffs = benchmark.run_pipeline(m, analysis.settings_from(cfg))
    got = next(d for d in diffs if d.name == "Drug vs DMSO")
    assert [r["id"] for r in got.rows] == [r["id"] for r in want]
    assert [r["significant"] or "" for r in got.rows] == [r["significant"].replace("NA", "") for r in want]


def test_isodtb_and_tmt_files_and_cli(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--grid", "quick", "--kind", "isodtb", "--quiet"]) == 0
    printed = capsys.readouterr().out
    assert "simulated benchmark, grid quick, isodtb data" in printed and "limma (default): observed FDP" in printed
    assert "unchanged features by" in printed
    out = tmp_path / "ionomos_benchmark"
    header, rows = read_tsv(out / "benchmark_simulated_isodtb.tsv")
    assert header == benchmark.SIM_COLUMNS and len(rows) == 2 * 2 * 2 and {r["kind"] for r in rows} == {"isodtb"}
    data = json.loads((out / "benchmark_simulated_isodtb.json").read_text(encoding="utf-8"),
                      parse_constant=lambda c: pytest.fail(c))
    assert data["data"] == "isodtb" and data["reference_name"] == "simulated isodtb data with planted changes"
    html = (out / "benchmark_simulated_isodtb.html").read_text(encoding="utf-8")
    assert len(_self_contained(html)) == 1 and "mixing error SD 0.2" in html and "tested against 0" in html
    assert not (out / "benchmark_simulated.json").exists()  # the three kinds sit side by side
    assert cli.main(["benchmark", "--grid", "quick", "--kind", "tmt", "--quiet", "--seeds", "1"]) == 0
    assert "IRS on the pool + auto (default): observed FDP" in capsys.readouterr().out
    header, rows = read_tsv(out / "benchmark_simulated_tmt.tsv")
    assert len(rows) == 2 * len(benchmark.TMT_SETTINGS) and {r["plexes"] for r in rows} == {"3"}
    assert "3 plexes × 4 vs 4" in (out / "benchmark_simulated_tmt.html").read_text(encoding="utf-8")
    with pytest.raises(benchmark.BenchmarkError, match="unknown kind"):
        benchmark.simulated("quick", kind="lfq")


def test_like_an_isodtb_experiment(tmp_path, capsys):
    dest = tmp_path / "20261002_EJQ_isoDTB_x"
    simulate.isodtb_ratios(dest / "fragpipe" / "combined_modified_peptide_label_quant.tsv", {"CmpdA": 3}, seed=3,
                           n_sites=240)
    downstream.analyze(dest, "isoDTB", analysis_cfg={**CFG, "test": "welch"})
    lk = benchmark.like(dest)
    assert lk["kind"] == "isodtb" and lk["design"] == (0, 3) and lk["settings"] == {"test": "welch", "min_valid": 2}
    assert lk["label"] == "20261002_EJQ_isoDTB_x: welch"
    assert cli.main(["benchmark", "--grid", "quick", "--like", str(dest), "--kind", "tmt"]) == 2
    assert "holds isodtb data" in capsys.readouterr().err
    assert cli.main(["benchmark", "--grid", "quick", "--like", str(dest), "--quiet", "--seeds", "1"]) == 0
    sim = json.loads((dest / "results" / "benchmark_simulated_isodtb.json").read_text(encoding="utf-8"))
    assert sim["settings"][-1] == lk["label"] and sim["headline"][0].startswith(lk["label"] + ": observed FDP")
    out = downstream.analyze(dest, "isoDTB", analysis_cfg={**CFG, "test": "welch"})
    t = out.summary["trust"]["benchmark_simulated"]
    assert t["file"] == "benchmark_simulated_isodtb.json" and t["same_settings"] and t["lines"] == sim["headline"]


def test_like_a_tmt_experiment(tmp_path):
    mq = tmp_path / "mq"
    conds, _truth = simulate.tmt_plexes(mq / "combined" / "txt" / "proteinGroups.txt", plexes=2, controls=2,
                                        treated=6, seed=4, n_proteins=200)
    downstream.analyze(mq, analysis_cfg={**CFG, "sample_conditions": conds, "tmt_reference": ["126"],
                                         "normalize": "median"})
    lk = benchmark.like(mq)
    assert lk["kind"] == "tmt" and lk["design"] == (2, 6, 2)
    assert lk["settings"]["normalize"] == "median" and lk["settings"]["tmt_reference"] == benchmark.POOL_CHANNELS
    res = benchmark.simulated({**benchmark.TMT_GRIDS["quick"], "settings": [], "designs": []}, [(lk["label"],
                              lk["settings"])], [lk["design"]], seeds=1, kind="tmt")
    assert [(r["plexes"], r["controls"], r["treated"]) for r in res["rows"]] == [(2, 2, 6)] * 2
    assert res["rows"][0]["setting"] == "mq: IRS auto + median" and res["rows"][0]["hits_alpha_only"] > 20


# ----------------------------------------------------------------------- real --

RATIOS = {"HUMAN": 0.0, "YEAST": 1.0, "ECOLI": -2.0}


@pytest.fixture(scope="module")
def hye(tmp_path_factory):
    d = tmp_path_factory.mktemp("hye") / "20260930_QC_DIA_HYE"
    runs = [(f"C:\\d\\{c}_{r}.raw", c) for c in ("A", "B") for r in (1, 2, 3, 4)]
    truth = simulate.mixed_species_pg_matrix(d / "fragpipe/report.pg_matrix.tsv", runs, RATIOS, seed=5, n_proteins=1200)
    downstream.analyze(d, "DIA", analysis_cfg={**CFG, "control": "A"})
    return {"dir": d, "truth": truth}


def _yaml(path: Path, text: str) -> dict:
    path.write_text(text, encoding="utf-8")
    return benchmark.load_expected(path)


def test_a_mixed_species_benchmark_recovers_its_ratios(hye, tmp_path):
    exp = _yaml(tmp_path / "hye.yaml", "comparison: B vs A\nexpected: {human: 1, yeast: 2, 'E. coli': 0.25}\n")
    assert exp["groups"] == {"human": 0.0, "yeast": 1.0, "E. coli": -2.0}
    res = benchmark.real(hye["dir"], exp)
    g = {x["group"]: x for x in res["groups"]}
    counts = {sp: sum(1 for v in hye["truth"].values() if v == sp) for sp in RATIOS}
    assert g["human"]["features"] + g["yeast"]["features"] + g["E. coli"]["features"] == res["features"]
    assert res["unassigned"] == res["mixed"] == 0 and g["human"]["features"] <= counts["HUMAN"]
    for name, want in (("human", 0.0), ("yeast", 1.0), ("E. coli", -2.0)):
        assert g[name]["median"] == pytest.approx(want, abs=0.12) and abs(g[name]["bias"]) < 0.12
        assert 0.1 < g[name]["mad"] < 0.5 and g[name]["q25"] < g[name]["median"] < g[name]["q75"]
    assert not g["human"]["changed"] and g["human"]["false_positive_rate"] < 0.01
    assert g["human"]["false_positive_rate_alpha_only"] < 0.05
    assert g["E. coli"]["sensitivity"] > 0.8 and g["E. coli"]["wrong"] == 0
    # a 2-fold change sits on the |log2FC| >= 1 cut-off: about half pass it, most pass alpha alone
    assert 0.2 < g["yeast"]["sensitivity"] < 0.7 < g["yeast"]["sensitivity_alpha_only"]
    assert res["fdp_alpha_only"] < 0.05 and res["fdp"] <= res["fdp_alpha_only"]
    lines = res["verdicts"]
    assert lines[0].startswith("human: measured median log2 ratio") and "of this unchanged background called" in lines[0]
    assert "within 0.25" in lines[1] and "at most about half can pass it" in lines[1]
    assert lines[-1].startswith("false discovery proportion:") and "(nominal 5%)" in lines[-1]


def test_expected_ratios_for_the_comparison_the_other_way_round(hye, tmp_path):
    exp = _yaml(tmp_path / "hye.yaml", "comparison: A vs B\nexpected: {HUMAN: 1, YEAST: 0.5, ECOLI: 4}\n")
    res = benchmark.real(hye["dir"], exp)
    g = {x["group"]: x for x in res["groups"]}
    assert g["YEAST"]["expected_log2"] == 1.0 and g["ECOLI"]["expected_log2"] == -2.0
    assert abs(g["YEAST"]["bias"]) < 0.12 and "they were inverted" in " ".join(res["notes"])


def test_species_from_a_column_a_fasta_or_protein_lists(hye, tmp_path):
    truth = hye["truth"]
    # the same quantities without entry names: nothing says which species a protein is
    bare = tmp_path / "bare/fragpipe/report.pg_matrix.tsv"
    bare.parent.mkdir(parents=True)
    lines = (hye["dir"] / "fragpipe/report.pg_matrix.tsv").read_text(encoding="utf-8").split("\n")
    head = lines[0].split("\t")
    k = head.index("Protein.Names")
    out = ["\t".join([*head, "Organism"])]
    names = {"HUMAN": "Homo sapiens", "YEAST": "Saccharomyces cerevisiae (strain ATCC 204508 / S288c)",
             "ECOLI": "Escherichia coli (strain K12)"}
    for ln in lines[1:]:
        if ln:
            c = ln.split("\t")
            c[k] = c[3] = "x"
            out.append("\t".join([*c, names[truth[c[0]]]]))
    bare.write_text("\n".join(out) + "\n", encoding="utf-8")
    downstream.analyze(tmp_path / "bare", "DIA", analysis_cfg={**CFG, "control": "A"})
    with pytest.raises(benchmark.BenchmarkError, match="no feature could be given a group"):
        benchmark.real(tmp_path / "bare", _yaml(tmp_path / "a.yaml", "expected: {HUMAN: 1, YEAST: 2, ECOLI: 0.25}\n"))
    # ... a column of the quant table
    res = benchmark.real(tmp_path / "bare", _yaml(tmp_path / "b.yaml", "expected: {HUMAN: 1, YEAST: 2, ECOLI: 0.25}\n"
                                                                      "species_column: organism\n"))
    assert {x["group"]: x["features"] > 100 for x in res["groups"]} == {"HUMAN": True, "YEAST": True, "ECOLI": True}
    with pytest.raises(benchmark.BenchmarkError, match="species_column 'Species' is not a column"):
        benchmark.real(tmp_path / "bare", _yaml(tmp_path / "b2.yaml", "expected: {HUMAN: 1}\nspecies_column: Species\n"))
    # ... the FASTA the search used (decoys ignored; OS= and entry names both read)
    fasta = tmp_path / "hye.fasta"
    fasta.write_text("".join(
        (f">sp|{pid}|G{i}_{sp} Some protein OS={names[sp]} OX=1 GN=G{i} PE=1 SV=1\nMKV\n" if i % 2 else
         f">tr|{pid}|G{i}_{sp} Some protein\nMKV\n") + f">rev_sp|{pid}|G{i}_HUMAN decoy\nVKM\n"
        for i, (pid, sp) in enumerate(truth.items())), encoding="utf-8")
    res = benchmark.real(tmp_path / "bare", _yaml(tmp_path / "c.yaml", "expected: {HUMAN: 1, YEAST: 2, ECOLI: 0.25}\n"
                                                                      "fasta: hye.fasta\n"))
    by = {x["group"]: x for x in res["groups"]}
    assert res["unassigned"] == 0 and abs(by["ECOLI"]["bias"]) < 0.12 and by["YEAST"]["features"] > 100
    # ... protein lists (a spike-in): a file and an inline list; everything else is left out and counted
    spikes = [pid for pid, sp in truth.items() if sp == "ECOLI"]
    (tmp_path / "spikes.txt").write_text("\n".join(spikes[10:]) + "\n", encoding="utf-8")
    res = benchmark.real(tmp_path / "bare", _yaml(tmp_path / "d.yaml", "expected: {UPS: 0.25, more: 0.25}\nproteins:\n"
                                                  f"  UPS: spikes.txt\n  more: [{', '.join(spikes[:10])}]\n"))
    by = {x["group"]: x for x in res["groups"]}
    assert by["UPS"]["features"] + by["more"]["features"] <= len(spikes) and by["more"]["features"] >= 8
    assert abs(by["UPS"]["bias"]) < 0.12 and res["unassigned"] > 800 and "belong to no group" in " ".join(res["notes"])


def test_a_protein_group_of_two_species_is_left_out():
    exp = {"groups": {"HUMAN": 0.0, "YEAST": 1.0}, "lists": {}}
    notes: list[str] = []
    got = benchmark.assign_groups(
        ["P1", "P2;P3", "P4", "P5", "sp|P6|X_YEAST"], ["A", "B", "C", "D", "E"],
        ["ACTB_HUMAN", "TBB_HUMAN;TBB_YEAST", "ALBU_BOVIN;ALBU_HUMAN", "no species here", ""], exp, None, notes)
    assert got == ["HUMAN", "mixed", "mixed", "", "YEAST"]


@pytest.mark.parametrize("name, want", [
    ("human", "HUMAN"), ("Homo sapiens", "HUMAN"), ("HUMAN", "HUMAN"), ("E. coli", "ECOLI"), ("ecoli", "ECOLI"),
    ("Escherichia coli (strain K12)", "ECOLI"), ("Escherichia coli O157:H7", "ECOLI"), ("ECO57", "ECOLI"),
    ("Saccharomyces cerevisiae (strain ATCC 204508 / S288c)", "YEAST"), ("yeast", "YEAST"), ("UPS1", "UPS1"),
    ("Mus musculus", "MOUSE"),
])
def test_species_names(name, want):
    assert benchmark._canon(name) == want


@pytest.mark.parametrize("text, message", [
    ("just: text\n", "needs an expected: mapping"),
    ("expected: {HUMAN: 1, YEAST: zero}\n", "is not a ratio above 0"),
    ("expected: {HUMAN: 1, YEAST: -2}\n", "is not a ratio above 0"),
    ("expected: {HUMAN: 1}\nspecies: x\n", "unknown key(s) species"),
    ("expected: {HUMAN: 1}\nproteins: {UPS: [P1]}\n", "protein list UPS has no expected ratio"),
    ("expected: {UPS: 2}\nproteins: {UPS: nowhere.txt}\n", "cannot read"),
    ("expected: {HUMAN: 1}\ntolerance_log2: 0\n", "tolerance_log2 must be a number above 0"),
    ("expected: [1, 2\n", "cannot read"),
])
def test_a_bad_expected_file_says_what_is_wrong(tmp_path, text, message):
    with pytest.raises(benchmark.BenchmarkError, match=re.escape(message)):
        _yaml(tmp_path / "e.yaml", text)


def test_log2_expected_values(tmp_path):
    exp = _yaml(tmp_path / "e.yaml", "log2: true\nexpected: {HUMAN: 0, YEAST: -1, ECOLI: 2}\ntolerance_log2: 0.1\n")
    assert exp["groups"] == {"HUMAN": 0.0, "YEAST": -1.0, "ECOLI": 2.0} and exp["tolerance_log2"] == 0.1


def test_real_benchmark_files_cli_and_the_report(hye, tmp_path, capsys):
    (tmp_path / "hye.yaml").write_text("expected: {HUMAN: 1, YEAST: 2, ECOLI: 0.25}\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in sorted((hye["dir"] / "results").glob("*differential.tsv"))}
    assert cli.main(["benchmark", str(hye["dir"]), "--expected", str(tmp_path / "hye.yaml")]) == 0
    printed = capsys.readouterr().out
    assert "20260930_QC_DIA_HYE, B vs A, against hye.yaml" in printed and "false discovery proportion:" in printed
    results = hye["dir"] / "results"
    header, rows = read_tsv(results / "benchmark.tsv")
    assert header == benchmark.REAL_COLUMNS and {r["group"] for r in rows} == {"HUMAN", "YEAST", "ECOLI"}
    assert {r["call"] for r in rows} == {"", "true positive", "missed"} | ({"false positive"} & {r["call"] for r in rows})
    data = json.loads((results / "benchmark.json").read_text(encoding="utf-8"), parse_constant=lambda c: pytest.fail(c))
    assert data["kind"] == "real" and data["analysis"]["settings_digest"] and "_values" not in data["groups"][0]
    html = (results / "benchmark.html").read_text(encoding="utf-8")
    svg = _self_contained(html)
    ns = "{http://www.w3.org/2000/svg}"
    assert len(svg) == 1 and len(svg[0].findall(f".//{ns}rect")) >= 4 and html.count("expected ") >= 3
    assert all(p.read_bytes() == b for p, b in before.items())
    # the settings of this experiment on simulated data, written beside it
    assert cli.main(["benchmark", "--grid", "quick", "--like", str(hye["dir"]), "--quiet"]) == 0
    assert "20260930_QC_DIA_HYE: perseus + auto: observed FDP" in capsys.readouterr().out
    sim = json.loads((results / "benchmark_simulated.json").read_text(encoding="utf-8"))
    assert {(r["controls"], r["treated"]) for r in sim["rows"]} == {(3, 3), (2, 4), (4, 4)}
    assert len(sim["headline"]) == 1 and sim["settings"][-1] == "20260930_QC_DIA_HYE: perseus + auto"
    # the next analysis shows both under "How far to trust this"
    out = downstream.analyze(hye["dir"], "DIA", analysis_cfg={**CFG, "control": "A"})
    t = out.summary["trust"]
    assert t["benchmark"]["same_settings"] and t["benchmark"]["lines"][0].startswith("HUMAN: measured median")
    assert t["benchmark_simulated"]["lines"] == sim["headline"]
    page = out.report.read_text(encoding="utf-8")
    assert "Benchmark against known ratios" in page and "href='benchmark.html'" in page
    assert "These settings on simulated data" in page and "href='benchmark_simulated.html'" in page


def test_what_cannot_be_benchmarked_says_why(hye, tmp_path, capsys):
    exp = _yaml(tmp_path / "e.yaml", "expected: {HUMAN: 1}\n")
    with pytest.raises(benchmark.BenchmarkError, match="holds no Ionomos analysis"):
        benchmark.real(tmp_path, exp)
    with pytest.raises(benchmark.BenchmarkError, match="comparison 'C vs D' is not in the analysis"):
        benchmark.real(hye["dir"], {**exp, "comparison": "C vs D"})
    assert cli.main(["benchmark", str(tmp_path), "--expected", str(tmp_path / "e.yaml")]) == 2
    assert "cannot benchmark" in capsys.readouterr().err
    assert cli.main(["benchmark", "--like", str(tmp_path), "--grid", "quick"]) == 2
