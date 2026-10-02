"""Normalisation when many features change in one direction (fpa.normalize_info, D64): the ratio method, the
composition check behind `normalize: auto`, and a simulated pulldown end to end."""
from __future__ import annotations

import csv
import json
import random
import statistics
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import fpa, simulate
from ionomos.downstream.analysis import AnalysisError, Settings, settings_from
from ionomos.downstream.quant import Feature, QuantMatrix


def _matrix(n=400, loading=(0.3, -0.2, 0.1, 0.0, -0.4, 0.2), enriched=0.0, effect=3.0, seed=1, missing=0.0):
    """Two conditions x three samples; `enriched` share of the features is up by `effect` in condition B."""
    rng = random.Random(seed)
    samples = [f"{c}_{r}" for c in ("A", "B") for r in (1, 2, 3)]
    vals, changed = [], set()
    for i in range(n):
        base = rng.gauss(22, 2.2)
        up = i < enriched * n
        if up:
            changed.add(i)
        row = []
        for j, s in enumerate(samples):
            v = base + loading[j] + (effect if up and s.startswith("B") else 0.0) + rng.gauss(0, 0.25)
            row.append(None if rng.random() < missing else v)
        vals.append(row)
    m = QuantMatrix("intensity", "protein", [Feature(id=f"P{i}", label=f"G{i}") for i in range(n)], samples, vals,
                    {s: s.split("_")[0] for s in samples}, "sim")
    return m, changed, list(loading)


def _between(m, changed):
    """Median log2 difference B - A over the unchanged features."""
    d = []
    for i, row in enumerate(m.values):
        if i in changed or any(v is None for v in row):
            continue
        d.append(sum(row[3:]) / 3 - sum(row[:3]) / 3)
    return statistics.median(d)


def test_ratio_shifts_recover_the_loading_with_and_without_enrichment():
    for enriched in (0.0, 0.1, 0.3):
        m, _changed, loading = _matrix(enriched=enriched)
        cols = [[row[j] for row in m.values] for j in range(6)]
        shifts, used = fpa.ratio_shifts(cols)
        want = [x - sum(loading) / 6 for x in loading]
        assert used == 300                                  # the stable three quarters of 400 complete features
        assert max(abs(a - b) for a, b in zip(shifts, want, strict=True)) < 0.06, (enriched, shifts)
    few = [[1.0] * 19 for _ in range(4)]
    assert fpa.ratio_shifts(few) == (None, 19)
    assert fpa.ratio_shifts([[1.0] * 50]) == (None, 50)      # one sample: nothing to compare with


def test_median_centring_is_shifted_by_enrichment_and_ratio_is_not():
    m, changed, _ = _matrix(enriched=0.1)
    med, info = fpa.normalize_info(m, "median")
    assert _between(med, changed) < -0.2                     # every unchanged feature pushed down in B
    assert info["used"] == "median" and info["composition"]["exceeded"]
    assert info["composition"]["between"] == ["B", "A"] and info["composition"]["shift"] == pytest.approx(0.27, abs=0.08)
    rat, info = fpa.normalize_info(m, "ratio")
    assert abs(_between(rat, changed)) < 0.03 and info["used"] == "ratio"
    auto, info = fpa.normalize_info(m, "auto")
    assert info == {**info, "asked": "auto", "used": "ratio"} and auto.values == rat.values


def test_auto_is_median_centring_when_nothing_changes_in_one_direction():
    for seed, enriched, effect in ((1, 0.0, 0.0), (2, 0.02, 2.0), (3, 0.0, 0.0)):
        m, _changed, _ = _matrix(enriched=enriched, effect=effect, seed=seed, missing=0.05)
        auto, info = fpa.normalize_info(m, "auto")
        assert info["used"] == "median" and not info["composition"]["exceeded"]
        assert info["composition"]["shift"] < 0.1
        assert auto.values == fpa.normalize(m, "median").values           # identical, not merely close
    # balanced up and down changes do not move the median either
    m, _c, _ = _matrix(enriched=0.1)
    for i in range(40, 80):
        m.values[i] = [v + (-3.0 if j >= 3 else 0.0) for j, v in enumerate(m.values[i])]
    assert fpa.normalize_info(m, "auto")[1]["used"] == "median"


def test_ratio_needs_complete_features_and_other_kinds_are_left_alone():
    m, _changed, _ = _matrix(n=15)
    out, info = fpa.normalize_info(m, "ratio")
    assert info["used"] == "median" and "fewer than 20 features" in info["fallback"]
    assert out.values == fpa.normalize(m, "median").values
    assert fpa.normalize_info(m, "auto")[1]["used"] == "median"
    assert fpa.normalize_info(m, "none")[1] == {"asked": "none", "used": "none"}
    ratio = QuantMatrix("ratio", "site", m.features, m.samples, m.values, m.condition, "x")
    assert fpa.normalize_info(ratio, "auto")[0] is ratio
    p, notes = fpa.process(m, normalization="ratio")
    assert any("ratio was asked for, but fewer than 20" in n for n in notes) and p.normalization["used"] == "median"


def test_setting():
    assert Settings().normalize == "auto"
    assert settings_from({"normalize": "Ratio"}).normalize == "ratio" and settings_from({"normalize": "md"}).normalize == "median"
    with pytest.raises(AnalysisError, match="auto, median, gn, ratio or none"):
        settings_from({"normalize": "quantile"})


# ------------------------------------------------------------- whole analysis --


def _pulldown(tmp_path: Path, seed: int, **cfg):
    d = tmp_path / f"exp{seed}"
    truth = simulate.competition_pg_matrix(d / "report.pg_matrix.tsv", sizes={"DMSO": 2, "Probe": 4, "Probe_Comp": 4},
                                           seed=seed)
    changed = set().union(*truth.values())
    out = downstream.analyze(d, analysis_cfg={"enrichment": False, "imputation": "none", **cfg})
    with open(out.results_dir / "Probe_vs_DMSO_differential.tsv", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter="\t") if r["log2fc"] not in ("", "NA")]
    unchanged = [float(r["log2fc"]) for r in rows if r["label"] not in changed]
    false = sum(1 for r in rows if r["label"] not in changed and r["significant"])
    return out, statistics.median(unchanged), false, json.loads((out.results_dir / "analysis.json").read_text(encoding="utf-8"))


def test_a_pulldown_is_normalised_on_stable_features_by_default(tmp_path):
    shifts, false = {"auto": [], "median": []}, {"auto": 0, "median": 0}
    for seed in (1, 2, 3, 4):
        for norm in ("auto", "median"):
            out, shift, n_false, s = _pulldown(tmp_path / norm, seed, normalize=norm)
            shifts[norm].append(shift)
            false[norm] += n_false
    assert statistics.mean(shifts["median"]) < -0.12            # the problem (D61 point 10)
    assert abs(statistics.mean(shifts["auto"])) < 0.04          # gone
    assert false["auto"] <= false["median"]
    out, _shift, _f, s = _pulldown(tmp_path / "one", 1)        # the default
    issue = next(i for i in out.issues if i.code == "NORMALISATION_COMPOSITION")
    assert issue.severity == "warning" and "normalised on the ratios" in issue.message
    assert s["normalisation"]["asked"] == "auto" and s["normalisation"]["used"] == "ratio"
    assert s["state"] != "needs_input" or [i["code"] for i in s["issues"] if i["severity"] == "input"] != ["NORMALISATION_COMPOSITION"]
    assert {"step": "normalisation", "method": "ratio normalisation (stable features)"}.items() <= next(
        x for x in s["processing"] if x["step"] == "normalisation").items()
    assert any("normalised on the ratios of" in n for n in s["notes"])
    html = out.report.read_text(encoding="utf-8")
    assert "samples normalised on feature ratios" in html and "auto → ratio" in html
    r_script = (out.results_dir / "fragpipe-analyst" / "reproduce_in_R.R")
    if r_script.is_file():
        assert "FragPipeAnalystR has no such step" in r_script.read_text(encoding="utf-8")


def test_median_chosen_on_a_pulldown_asks(tmp_path):
    out, shift, _f, s = _pulldown(tmp_path, 1, normalize="median")
    issue = next(i for i in out.issues if i.code == "NORMALISATION_COMPOSITION")
    assert issue.severity == "input" and "Set Normalisation to auto" in issue.fixes[0]
    assert s["normalisation"]["used"] == "median" and s["state"] == "needs_input" and shift < -0.1
    forced = _pulldown(tmp_path / "r", 1, normalize="ratio")[0]
    assert not [i for i in forced.issues if i.code == "NORMALISATION_COMPOSITION"]


def test_an_ordinary_experiment_is_unchanged_by_the_new_default(tmp_path):
    runs = [(f"{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        simulate.dia_pg_matrix(d / "report.pg_matrix.tsv", runs, seed=7, n_proteins=300)
    auto = downstream.analyze(a, analysis_cfg={"enrichment": False})
    downstream.analyze(b, analysis_cfg={"enrichment": False, "normalize": "median"})
    assert auto.summary["normalisation"]["used"] == "median"
    for name in ("Drug_vs_DMSO_differential.tsv", "protein_matrix_processed.tsv"):
        assert (a / "results" / name).read_bytes() == (b / "results" / name).read_bytes()
    assert not [i for i in auto.issues if i.code == "NORMALISATION_COMPOSITION"]
