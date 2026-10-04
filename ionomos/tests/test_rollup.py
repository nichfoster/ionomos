"""Protein roll-up (downstream/rollup.py, D76): MaxLFQ against R, the analysis.rollup setting in each loader.

The goldens in tests/golden/maxlfq/ come from iq::maxLFQ() 2.0.1 and diann::diann_maxlfq() 1.0.1 on 54 proteins x
8 samples (disconnected sample groups, missing values, one feature, one sample, a chain of samples). Regenerate:
    cd tests/golden/maxlfq && python make_maxlfq_input.py && Rscript run_maxlfq_reference.R <R libraries>
"""
from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

import pytest

from ionomos.downstream import analysis, engines, rollup

GOLD = Path(__file__).parent / "golden" / "maxlfq"
SAMPLES = [f"S{k}" for k in range(1, 9)]


def _input() -> dict[str, list[list[float | None]]]:
    data: dict[str, dict[str, dict[str, float]]] = {}
    with open(GOLD / "input.tsv", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            data.setdefault(r["protein"], {}).setdefault(r["feature"], {})[r["sample"]] = math.log2(float(r["intensity"]))
    return {p: [[f.get(s) for s in SAMPLES] for f in feats.values()] for p, feats in data.items()}


def _gold(name: str) -> dict[tuple[str, str], dict]:
    with open(GOLD / name, encoding="utf-8", newline="") as fh:
        return {(r["protein"], r["sample"]): r for r in csv.DictReader(fh, delimiter="\t")}


def _num(v: str) -> float | None:
    return None if v in ("NA", "") else float(v)


def test_maxlfq_matches_iq():
    """iq's scaling (mean of the log values) to 1e-9, and the summed-intensity scaling to 1e-9, every protein."""
    gold = _gold("iq.tsv")
    data = _input()
    assert len(data) == 54
    for prot, rows in data.items():
        mean, summed = rollup.maxlfq(rows, scale="mean"), rollup.maxlfq(rows)
        for j, s in enumerate(SAMPLES):
            want, want_sum = _num(gold[(prot, s)]["estimate"]), _num(gold[(prot, s)]["sum"])
            if want is None:
                assert mean[j] is None and summed[j] is None, (prot, s)
            else:
                assert mean[j] == pytest.approx(want, abs=1e-9), (prot, s)
                assert summed[j] == pytest.approx(want_sum, abs=1e-9), (prot, s)


def test_components_match_iq():
    """The connected components are iq's (its annotation, when a protein has more than one)."""
    gold = _gold("iq.tsv")
    split = 0
    for prot, rows in _input().items():
        comp = rollup.components(rows)
        ann = gold[(prot, "S1")]["annotation"]
        if ann and ann != "NA":
            split += 1
            want = [None if x == "NA" else int(x) - 1 for x in ann.split(";")]
            assert comp == want, prot
        else:
            assert len({c for c in comp if c is not None}) <= 1, prot
    assert split >= 2  # P_two_groups and P_three_groups


def test_maxlfq_profile_matches_diann():
    """DIA-NN's R package solves the same pairwise medians but pulls each sample weakly (1e-4) towards its most
    intense feature, so within a connected protein the profiles (centred) agree to ~1e-3."""
    gold = _gold("diann.tsv")
    worst = 0.0
    compared = 0
    for prot, rows in _input().items():
        comp = rollup.components(rows)
        if len({c for c in comp if c is not None}) != 1:
            continue
        mine = rollup.maxlfq(rows)
        theirs = [_num(gold[(prot, s)]["estimate"]) if (prot, s) in gold else None for s in SAMPLES]
        cols = [j for j in range(8) if mine[j] is not None]
        assert cols == [j for j in range(8) if theirs[j] is not None], prot
        if len(cols) < 2:
            continue
        a, b = sum(mine[j] for j in cols) / len(cols), sum(theirs[j] for j in cols) / len(cols)
        worst = max(worst, *(abs((mine[j] - a) - (theirs[j] - b)) for j in cols))
        compared += 1
    assert compared >= 40
    assert worst < 2e-3


def test_maxlfq_edge_cases():
    assert rollup.maxlfq([]) == []
    assert rollup.maxlfq([[None, None]]) == [None, None]
    # one feature: the feature itself, whatever the scaling
    one = rollup.maxlfq([[20.0, None, 21.5]])
    assert one[0] == pytest.approx(20.0) and one[1] is None and one[2] == pytest.approx(21.5)
    # a constant offset between two features is removed exactly: the profile is the shared ratio
    rows = [[20.0, 21.0, 19.0], [25.0, 26.0, 24.0]]
    w = rollup.maxlfq(rows, scale="mean")
    assert [w[1] - w[0], w[2] - w[0]] == pytest.approx([1.0, -1.0])
    assert sum(w) / 3 == pytest.approx(sum(v for r in rows for v in r) / 6)
    # summed scaling: the linear profile adds up to the features' summed intensity
    s = rollup.maxlfq(rows)
    assert sum(2 ** x for x in s) == pytest.approx(sum(2 ** v for r in rows for v in r))
    # min_ratio_count 2 splits samples linked by a single feature
    rows = [[20.0, 21.0, None], [22.0, 23.0, None], [None, 19.0, 20.0]]
    assert rollup.components(rows) == [0, 0, 0]
    assert rollup.components(rows, min_ratio_count=2) == [0, 0, 1]
    with pytest.raises(ValueError):
        rollup.maxlfq(rows, scale="median")


def test_solver():
    a = [[2.0, 1.0, -1.0], [-3.0, -1.0, 2.0], [-2.0, 1.0, 2.0]]
    assert rollup.solve(a, [8.0, -11.0, -3.0]) == pytest.approx([2.0, 3.0, -1.0])
    with pytest.raises(ValueError):
        rollup.solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])


def test_summarise_and_settings():
    rows = [[20.0, 21.0, None, 22.0], [24.0, None, 23.0, 26.0], [19.0, 20.5, 18.0, None]]
    assert rollup.summarise(rows, "median_polish") == engines.median_polish(rows)
    assert rollup.summarise(rows, "maxlfq") == rollup.maxlfq(rows)
    with pytest.raises(ValueError):
        rollup.summarise(rows, "top3")
    assert rollup.resolve(None, "median_polish") == "median_polish"
    assert rollup.resolve("auto", "engine") == "engine"
    assert rollup.resolve("MaxLFQ", "median_polish") == "maxlfq"
    s = analysis.settings_from({"rollup": "MaxLFQ"})
    assert s.rollup == "maxlfq"
    assert analysis.settings_from({"rollup": "median polish"}).rollup == "median_polish"
    assert analysis.Settings().rollup == "auto"
    with pytest.raises(analysis.AnalysisError):
        analysis.settings_from({"rollup": "top3"})
    assert analysis.Settings().f_test == "auto"
    assert analysis.settings_from({"f_test": False}).f_test == "off"   # YAML reads a bare `off` as false
    assert analysis.settings_from({"f_test": "OFF"}).f_test == "off"
    assert analysis.settings_from({"f_test": True}).f_test == "auto"
    with pytest.raises(analysis.AnalysisError):
        analysis.settings_from({"f_test": "always"})


# ------------------------------------------------------------------ the loaders --

RUNS = ["DMSO_1", "DMSO_2", "DMSO_3", "Drug_1", "Drug_2", "Drug_3"]


def _write(path: Path, header: list[str], rows: list[list], sep: str = "\t") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sep.join(header) + "\n" + "\n".join(sep.join("" if x is None else str(x) for x in r) for r in rows)
                    + "\n", encoding="utf-8")
    return path


def _proteins():
    """{protein: {feature: {run: linear}}}: some of the golden input's proteins, on six runs (S1-S6), with missing
    values and two disconnected groups (P_two_groups: DMSO_1-Drug_1 vs Drug_2-Drug_3)."""
    data: dict[str, dict[str, dict[str, float]]] = {}
    with open(GOLD / "input.tsv", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            k = int(r["sample"][1:]) - 1
            if r["protein"] in ("P_full", "P_missing", "P_two_groups", "P_even", "R01", "R02") and k < 6:
                data.setdefault(r["protein"], {}).setdefault(r["feature"], {})[RUNS[k]] = float(r["intensity"])
    return data


def _expect(fmap: dict[str, dict[str, float]], method: str) -> list[float | None]:
    return rollup.summarise([[math.log2(v[r]) if r in v else None for r in RUNS] for v in fmap.values()], method)


def _row(m, prot: str) -> list[float | None]:
    i = [f.id for f in m.features].index(prot)
    return [m.values[i][m.samples.index(r)] for r in RUNS]


def _same(got, want):
    assert [g is None for g in got] == [w is None for w in want]
    assert [g for g in got if g is not None] == pytest.approx([w for w in want if w is not None], abs=1e-9)


def test_msstats_format_rollup(tmp_path):
    head = ["ProteinName", "PeptideSequence", "PrecursorCharge", "FragmentIon", "ProductCharge", "IsotopeLabelType",
            "Condition", "BioReplicate", "Run", "Intensity"]
    rows = [[p, f, 2, "NA", "NA", "L", r.split("_")[0], r[-1], r, v.get(r, "NA")]
            for p, fm in _proteins().items() for f, v in fm.items() for r in RUNS]
    path = _write(tmp_path / "msstats.csv", head, rows, sep=",")
    default, mx = engines.load_msstats(path), engines.load_msstats(path, "maxlfq")
    assert default.meta["rollup"] == "median_polish" and mx.meta["rollup"] == "maxlfq"
    assert mx.meta["quantity"] == "MaxLFQ" and any("MaxLFQ" in n for n in mx.notes)
    assert any("share no feature" in n for n in mx.notes)  # P_two_groups
    for p, fm in _proteins().items():
        _same(_row(default, p), _expect(fm, "median_polish"))
        _same(_row(mx, p), _expect(fm, "maxlfq"))
    assert engines.load_msstats(path, "median_polish").values == default.values


def test_sage_rollup(tmp_path):
    head = ["peptide", "charge", "proteins", "q_value", "score", "spectral_angle", *[f"{r}.mzML" for r in RUNS]]
    rows = [[f"{f}K{p}", 2, f"sp|{p}|{p}_HUMAN", 0.001, 1.0, 0.9, *[v.get(r, 0) for r in RUNS]]
            for p, fm in _proteins().items() for f, v in fm.items()]
    path = _write(tmp_path / "sage" / "lfq.tsv", head, rows)
    default = engines.load_sage(path)
    mx, notes = engines.load("Sage", tmp_path / "sage", rollup="maxlfq")
    assert notes == [] and default.meta["rollup"] == "median_polish" and mx.meta["rollup"] == "maxlfq"
    assert mx.meta["quantity"].startswith("MaxLFQ") and any("share no peptide ion" in n for n in mx.notes)
    for p, fm in _proteins().items():
        _same(_row(default, p), _expect(fm, "median_polish"))
        _same(_row(mx, p), _expect(fm, "maxlfq"))


DIANN_HEAD = ["Run", "Protein.Group", "Protein.Ids", "Genes", "Precursor.Id", "Q.Value", "PG.Q.Value", "PG.MaxLFQ",
              "Precursor.Normalised"]


def _diann(folder: Path, head=DIANN_HEAD) -> Path:
    rows = []
    for p, fm in _proteins().items():
        for f, v in fm.items():
            for r in RUNS:
                if r in v:
                    cell = {"Run": f"D:\\raw\\{r}.raw", "Protein.Group": p, "Protein.Ids": p, "Genes": p,
                            "Precursor.Id": f + "2", "Q.Value": 0.001, "PG.Q.Value": 0.001, "PG.MaxLFQ": 1000.0,
                            "Precursor.Normalised": v[r]}
                    rows.append([cell[h] for h in head])
        bad = {"Run": f"D:\\raw\\{RUNS[0]}.raw", "Protein.Group": p, "Protein.Ids": p, "Genes": p,
               "Precursor.Id": "BAD2", "Q.Value": 0.2, "PG.Q.Value": 0.2, "PG.MaxLFQ": 1e12, "Precursor.Normalised": 1e12}
        rows.append([bad[h] for h in head])  # above 1% FDR: left out
    return _write(folder / "diann" / "report.tsv", head, rows)


def test_diann_long_rollup(tmp_path):
    path = _diann(tmp_path)
    engine = engines.load_diann_long(path)  # auto: DIA-NN's own PG.MaxLFQ, as before
    assert {v for row in engine.values for v in row if v is not None} == {math.log2(1000.0)}
    assert "rollup" not in engine.meta
    for method in ("maxlfq", "median_polish"):
        m = engines.load_diann_long(path, method)
        assert m.meta["rollup"] == method and "Precursor.Normalised" in m.meta["quantity"]
        assert "not DIA-NN's own PG.MaxLFQ" in m.notes[0]
        for p, fm in _proteins().items():
            _same(_row(m, p), _expect(fm, method))
    # without precursor quantities: DIA-NN's own, and a note says why
    bare = _diann(tmp_path / "bare", DIANN_HEAD[:-1])
    m = engines.load_diann_long(bare, "maxlfq")
    assert "rollup" not in m.meta and any("was used instead" in n for n in m.notes)


def test_spectronaut_long_rollup(tmp_path):
    head = ["R.FileName", "R.Condition", "R.Replicate", "PG.ProteinGroups", "PG.Quantity", "EG.PrecursorId",
            "FG.Quantity", "F.FrgIon"]
    rows = [[f"2026_{r}", r.split("_")[0], r[-1], p, 5000.0, f"_{f}_.2", v[r], ion]
            for p, fm in _proteins().items() for f, v in fm.items() for r in RUNS if r in v
            for ion in ("y4", "y5")]  # a fragment-level report: FG.Quantity on every fragment row
    path = _write(tmp_path / "sn" / "Report.tsv", head, rows)
    assert engines.load_spectronaut(path).meta["quantity"] == "PG.Quantity"
    m = engines.load_spectronaut(path, "maxlfq")
    assert m.meta["rollup"] == "maxlfq" and m.meta["quantity"] == "MaxLFQ of FG.Quantity (Ionomos)"
    for p, fm in _proteins().items():
        _same(_row(m, p), _expect(fm, "maxlfq"))
    no_fg = _write(tmp_path / "sn2" / "Report.tsv", head[:6], [r[:6] for r in rows])
    m = engines.load_spectronaut(no_fg, "maxlfq")
    assert m.meta["quantity"] == "PG.Quantity" and any("FG.Quantity" in n for n in m.notes)


def test_rollup_on_a_protein_table_is_said_not_used(tmp_path):
    _write(tmp_path / "ad" / "pg.matrix.tsv", ["pg", *RUNS], [["P1", *[1000.0 + k for k in range(6)]],
                                                             ["P2", *[2000.0 + k for k in range(6)]]])
    _m, notes = engines.load("AlphaDIA", tmp_path / "ad", rollup="maxlfq")
    assert notes == ["analysis.rollup: maxlfq was not used: AlphaDIA's pg.matrix.tsv holds proteins already"]
    assert engines.load("AlphaDIA", tmp_path / "ad")[1] == []


def _three_conditions(folder: Path) -> Path:
    runs = [f"{c}_{r}" for c in ("DMSO", "DrugA", "DrugB") for r in (1, 2, 3)]
    head = ["ProteinName", "PeptideSequence", "PrecursorCharge", "FragmentIon", "ProductCharge", "IsotopeLabelType",
            "Condition", "BioReplicate", "Run", "Intensity"]
    rng = random.Random(5)
    rows = []
    for i in range(80):
        base, eff = rng.gauss(22, 2), (2.0 if i < 8 else 0.0)
        for k in range(4):
            off = rng.gauss(0, 1)
            for r in runs:
                v = base + off + (eff if r.startswith("DrugA") else 0) + rng.gauss(0, 0.2)
                rows.append([f"P{i:03d}", f"PEP{k}K", 2, "NA", "NA", "L", r.rsplit("_", 1)[0], r[-1], r, 2 ** v])
    return _write(folder / "msstats.csv", head, rows, sep=",")


def test_analyze_with_maxlfq_and_the_f_test_switch(tmp_path):
    """End to end: analysis.rollup reaches the loader and the report's Data source; f_test: off leaves the moderated
    F out of analysis.json, the results table and the Methods (3 conditions)."""
    from ionomos import downstream

    _three_conditions(tmp_path / "e")
    out = downstream.analyze(tmp_path / "e", analysis_cfg={"enrichment": False, "rollup": "maxlfq"})
    s = json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["settings"]["rollup"] == "maxlfq" and s["engine"]["quantity"] == "MaxLFQ"
    assert s["f_test"] and s["f_test"]["any_change"] >= 5
    assert "moderated F-statistic" in out.report.read_text(encoding="utf-8")
    off = downstream.analyze(tmp_path / "e", analysis_cfg={"enrichment": False, "f_test": False})
    s = json.loads((off.results_dir / "analysis.json").read_text(encoding="utf-8"))
    assert s["settings"]["f_test"] == "off"
    assert s["f_test"] == {"off": True, "note": "switched off (analysis.f_test: off)"}
    assert s["settings"]["rollup"] == "auto"
    assert s["engine"]["quantity"] == "median polish (TMP)"
    html = off.report.read_text(encoding="utf-8")
    assert "moderated F-statistic" not in html and "switched off (f_test: off)" in html
    table = next(off.results_dir.glob("*_results.tsv"))
    assert "F_p_adj" not in table.read_text(encoding="utf-8").splitlines()[0]


def test_rollup_benchmark_guard():
    """The roll-up kind of `ionomos benchmark` (D76) on its guard grid: both roll-ups keep the FDP near the nominal
    5% and find the same share of 2-fold changes. Measured 2026-10-04 (6 seeds, 500 proteins): FDP 1.6-2.9%,
    sensitivity 66-70%, bias -0.03 to -0.05 log2; the standard grid's numbers are in D76."""
    from ionomos.downstream import benchmark

    res = benchmark.simulated("guard", kind="rollup", seeds=6)
    assert res["data"] == "rollup" and len(res["rows"]) == 4
    assert {r["rollup"] for r in res["rows"]} == {"median_polish", "maxlfq"}
    for r in res["rows"]:
        assert r["hits_alpha_only"] >= 150
        assert r["fdp_alpha_only"] <= 0.085, r
        assert 0.55 <= r["sensitivity_alpha_only"] <= 0.85, r
        assert abs(r["fc_bias_changed"]) < 0.15, r
    by = {(r["rollup"], r["controls"]): r for r in res["rows"]}
    for c in (3, 2):
        assert abs(by[("maxlfq", c)]["sensitivity_alpha_only"] - by[("median_polish", c)]["sensitivity_alpha_only"]) \
            < 0.06
    assert benchmark.simulated("guard", kind="rollup", seeds=6)["rows"] == res["rows"]


def test_rollup_benchmark_writes_its_own_files(tmp_path):
    from ionomos.downstream import benchmark

    res = benchmark.simulated({"designs": [(3, 3)], "effects": [1.0], "missing": ["typical"],
                               "settings": ["median polish + perseus (default)", "MaxLFQ + perseus"], "seeds": 1,
                               "proteins": 200}, kind="rollup")
    files = benchmark.write_simulated(res, tmp_path)
    assert [f.name for f in files] == ["benchmark_simulated_rollup.tsv", "benchmark_simulated_rollup.json",
                                       "benchmark_simulated_rollup.html"]
    page = files[-1].read_text(encoding="utf-8")
    assert "MaxLFQ" in page and "simulated peptide data (roll-up)" in page
