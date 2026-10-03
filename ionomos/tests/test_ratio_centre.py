"""isoDTB site ratios centred per replicate, opt-in (analysis.ratio_centre, fpa.centre_ratios, D70): the stable
centre against a mixing error and a promiscuous compound, auto's check, the doctor's RATIO_OFFSET, the liganded
calls saying which ratios they used, and the benchmark that measured the three options."""
from __future__ import annotations

import random

import pytest

from ionomos import downstream
from ionomos.downstream import benchmark, cys, fpa, simulate, stats
from ionomos.downstream.analysis import AnalysisError, Settings, settings_from
from ionomos.downstream.quant import Feature, QuantMatrix


def _matrix(offsets: list[float], up_share: float, n: int = 600, effect: float = 2.0, seed: int = 1) -> QuantMatrix:
    """One condition, a replicate per offset; up_share of the sites engaged (all up), replicate SD 0.35."""
    rng = random.Random(seed)
    samples = [f"Cmpd_{k + 1}" for k in range(len(offsets))]
    vals = []
    for i in range(n):
        mu = effect if i < up_share * n else 0.0
        vals.append([None if rng.random() < 0.05 else mu + o + rng.gauss(0, 0.35) for o in offsets])
    feats = [Feature(id=f"sp|P{i:05d}|G{i}_HUMAN|C{i}", label=f"G{i} C{i}", description="") for i in range(n)]
    return QuantMatrix("ratio", "site", feats, samples, vals, {s: "Cmpd" for s in samples}, "x", exp="isoDTB")


def test_settings():
    assert Settings().ratio_centre == "none"
    assert settings_from({"ratio_centre": "Median"}).ratio_centre == "median"
    assert settings_from({"ratio_centre": "off"}).ratio_centre == "none"
    with pytest.raises(AnalysisError):
        settings_from({"ratio_centre": "mean"})


def test_the_stable_centre_finds_the_mixing_error_and_ignores_engaged_sites():
    offsets = [0.3, -0.2, 0.0]
    for share in (0.0, 0.2):
        m = _matrix(offsets, share, n=2000)
        cols = [[row[j] for row in m.values] for j in range(3)]
        got = fpa.stable_centre(cols)
        for o, g in zip(offsets, got, strict=True):
            assert 0 < g["se"] < 0.02 and g["sites"] > 900
            assert g["offset"] == pytest.approx(o, abs=3 * g["se"])
    # the median is pulled by the 20 % engaged sites; the stable centre is not
    m = _matrix([0.0, 0.0, 0.0], 0.2, n=2000)
    med, info = fpa.centre_ratios(m, "median")
    stable = fpa.stable_centre([[row[j] for row in m.values] for j in range(3)])
    assert sum(r["median"] for r in info["conditions"]["Cmpd"]["replicates"].values()) / 3 > 0.08
    assert all(abs(r["offset"]) < 3 * r["se"] for r in stable) and abs(sum(r["offset"] for r in stable)) / 3 < 0.02


def test_none_measures_median_centres_and_auto_centres_only_a_clear_offset():
    m = _matrix([0.3, -0.2, 0.0], 0.05)
    same, info = fpa.centre_ratios(m, "none")
    assert same is m and info["used"] == "none" and info["centred"] == []
    assert info["conditions"]["Cmpd"]["clear"] == ["Cmpd_1", "Cmpd_2"]       # measured anyway, for the doctor
    med, info = fpa.centre_ratios(m, "median")
    assert info["used"] == "median" and info["centred"] == m.samples
    for j in range(3):
        assert stats.median([row[j] for row in med.values if row[j] is not None]) == pytest.approx(0.0, abs=1e-12)
    auto, info = fpa.centre_ratios(m, "auto")
    assert info["used"] == "stable" and info["conditions"]["Cmpd"]["centred"]
    shifts = [m.values[5][j] - auto.values[5][j] for j in range(3)]
    assert shifts == pytest.approx([info["conditions"]["Cmpd"]["replicates"][s]["offset"] for s in m.samples])
    assert "stable sites" in fpa.centre_label(info)
    # no mixing error: auto changes nothing, not even by a rounding error
    clean = _matrix([0.0, 0.0, 0.0], 0.2)
    out, info = fpa.centre_ratios(clean, "auto")
    assert out is clean and info["used"] == "none" and fpa.centre_label(info) == "as measured (not centred)"


def test_intensity_data_is_never_centred():
    m = QuantMatrix("intensity", "protein", [Feature("P1", "G1", "")], ["A_1"], [[20.0]], {"A_1": "A"}, "x")
    out, info = fpa.centre_ratios(m, "median")
    assert out is m and info["used"] == "none"
    p, _ = fpa.process(m, ratio_centre="median", imputation="none")
    assert "ratio_centre" not in p.normalization


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    iso = tmp_path_factory.mktemp("mix") / "iso"
    simulate.isodtb_ratios(iso / "fragpipe" / "combined_modified_peptide_label_quant.tsv", {"Cmpd": 3}, seed=4,
                           n_sites=600, mixing_sd=0.2)   # offsets about +0.01, +0.09, -0.08
    return iso


def test_analyze_warns_when_none_and_says_what_the_liganded_calls_used(mixed):
    out = downstream.analyze(mixed, method="isoDTB", analysis_cfg={"enrichment": False})
    issue = next(i for i in out.issues if i.code == "RATIO_OFFSET")
    assert issue.severity == "warning" and "Cmpd_2" in issue.message and "ratio_centre: none" in issue.message
    assert out.summary["cysteines"]["centred"] is False
    rc = out.summary["normalisation"]["ratio_centre"]
    assert rc["asked"] == "none" and rc["conditions"]["Cmpd"]["clear"]
    out = downstream.analyze(mixed, method="isoDTB", analysis_cfg={"enrichment": False, "ratio_centre": "auto"})
    assert not [i for i in out.issues if i.code == "RATIO_OFFSET"]
    assert out.summary["cysteines"]["centred"] is True
    assert "stable sites" in out.summary["cysteines"]["rule"]
    assert any(n.startswith("site ratios centred per replicate on the stable sites") for n in out.warnings)
    html = (mixed / "results" / "report.html").read_text(encoding="utf-8")
    assert "were centred on their stable sites" in html
    out = downstream.analyze(mixed, method="isoDTB", analysis_cfg={"enrichment": False, "ratio_centre": "median"})
    assert "median site" in out.summary["cysteines"]["rule"] and not [i for i in out.issues if i.code == "RATIO_OFFSET"]


def test_the_liganded_calls_follow_the_centred_ratios():
    m = _matrix([-1.2, -1.2, -1.2], 0.1, effect=3.0)   # 60 sites engaged 8-fold; light 2.3 times too much
    p_none, _ = fpa.process(m)
    p_auto, _ = fpa.process(m, ratio_centre="auto")
    lig_none = cys.run(p_none, Settings()).compounds[0].counts["liganded"]
    lig_auto = cys.run(p_auto, Settings()).compounds[0].counts["liganded"]
    assert lig_auto == pytest.approx(60, abs=4) and lig_none < lig_auto / 2


def test_centring_benchmark_d70():
    """The D70 grid, small: a mixing error moves the unchanged sites; median centring removes it but shifts them
    when 20 % go one way; auto does neither."""
    grid = {**benchmark.ISODTB_GRIDS["centring"], "effects": [2.0], "seeds": 4, "proteins": 600}
    rows = benchmark.simulated(grid, kind="isodtb")["rows"]
    get = {(r["setting"], r["changed_share"], r["mixing_sd"]): r for r in rows}
    none, med, auto = "limma (default)", "limma, ratios centred: median", "limma, ratios centred: auto"
    assert {r["ratio_centre"] for r in rows} == {"none", "median", "auto"}
    off = {k: r["fc_offset_unchanged_abs"] for k, r in get.items()}
    for share in (0.05, 0.2):
        assert get[(auto, share, 0.0)]["fdp_alpha_only"] == get[(none, share, 0.0)]["fdp_alpha_only"]  # untouched
        assert off[(auto, share, 0.2)] < 0.03 and off[(auto, share, 0.0)] < 0.03
    assert off[(none, 0.05, 0.2)] + off[(none, 0.2, 0.2)] > 0.08          # the mixing error, uncorrected
    assert get[(med, 0.2, 0.0)]["fc_offset_unchanged"] < -0.06             # the median follows the engaged sites
