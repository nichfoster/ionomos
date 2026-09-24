"""The FragPipeAnalystR port (downstream/fpa.py) against R.

Golden files in tests/golden/fpa/ come from make_fpa_inputs.py + run_fpa_reference.R
(limma 3.68 with FragPipeAnalystR's manual_impute() and test_limma() transcribed to base R).
"""
import math
from pathlib import Path

import pytest

from ionomos.downstream import fpa, qc, stats
from ionomos.downstream.quant import Feature, QuantMatrix
from ionomos.downstream.rrandom import RRandom, qnorm
from ionomos.downstream.tables import read_tsv

GOLD = Path(__file__).parent / "golden" / "fpa"


def _f(x: str) -> float:
    x = x.strip()
    return math.nan if x in ("NA", "NaN", "") else float(x)


def _matrix(path: Path = GOLD / "fpa_matrix.tsv") -> QuantMatrix:
    header, rows = read_tsv(path)
    samples = header[1:]
    vals = [[None if r[s].strip() == "NA" else float(r[s]) for s in samples] for r in rows]
    m = QuantMatrix("intensity", "protein", [Feature(r["ID"], r["ID"]) for r in rows], samples, vals,
                    {s: s.rsplit("_", 1)[0] for s in samples}, str(path))
    m.exp = "DIA"
    return m


def _close(a: float, b: float, rel: float = 1e-9, abs_: float = 1e-12) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


# ------------------------------------------------------------------- R's RNG --


def test_r_rng_matches_r():
    # R: set.seed(123); rnorm(5, 20.1, 0.4)   /  set.seed(42); c(runif(2), rnorm(3))
    assert RRandom(123).rnorm(5, 20.1, 0.4) == pytest.approx(
        [19.875809741379115, 20.007929004206691, 20.723483325659650, 20.128203356569831, 20.151715094064379], abs=1e-13)
    r = RRandom(42)
    assert r.runif(2) + r.rnorm(3) == pytest.approx(
        [0.91480604349635541, 0.93707541329786181, -0.56469817139608880, 0.36312841133733914, 0.63286260496104052],
        abs=1e-13)
    x = RRandom(123).rnorm(2000)  # crosses the 624-word state refill
    assert x[624] == pytest.approx(-0.87525547749551558, abs=1e-13)
    assert x[1999] == pytest.approx(-0.46048556597671125, abs=1e-13)
    assert qnorm(0.975) == pytest.approx(1.959963984540054, abs=1e-15) and qnorm(1e-12) < -7


# ---------------------------------------------------------------- imputation --


def test_perseus_imputation_identical_to_fragpipe_analyst():
    m = _matrix()
    im, mask, dropped, notes = fpa.impute(m, "perseus")
    _, rows = read_tsv(GOLD / "fpa_imputed.tsv")
    assert dropped == len(m.features) - len(rows) and not notes
    assert [f.id for f in im.features] == [r["ID"] for r in rows]
    worst = 0.0
    for vals, r in zip(im.values, rows, strict=True):
        for s, v in zip(im.samples, vals, strict=True):
            worst = max(worst, abs(v - float(r[s])))
    assert worst < 1e-9, worst  # R writes 15 significant digits
    assert sum(sum(x) for x in mask) == sum(v is None for r in m.values for v in r) - dropped * len(m.samples)


def test_other_imputations_fill_every_gap():
    m = _matrix()
    for method in ("min", "zero", "mindet", "minprob", "knn"):
        im, mask, _, _ = fpa.impute(m, method)
        assert all(v is not None for r in im.values for v in r), method
        assert any(any(r) for r in mask)
    im, _, _, _ = fpa.impute(m, "min")
    assert min(v for r in im.values for v in r) == min(v for r in m.values for v in r if v is not None)


# --------------------------------------------------------------------- limma --


def _gold(name: str) -> dict[tuple[str, str], dict]:
    _, rows = read_tsv(GOLD / name)
    return {(r["comparison"].strip(), r["ID"].strip()): {k: _f(v) for k, v in r.items() if k not in ("comparison", "ID")}
            for r in rows}


def _check(results: list[fpa.ContrastResult], m: QuantMatrix, gold: dict, name) -> int:
    n = 0
    for c in results:
        cname = name(c)
        for i, f in enumerate(m.features):
            g = gold.get((cname, f.id))
            assert g is not None, (cname, f.id)
            for ours, key in ((c.diff[i], "diff"), (c.ci_low[i], "CI.L"), (c.ci_high[i], "CI.R"), (c.t[i], "t"),
                              (c.p[i], "p.val"), (c.q[i], "p.adj")):
                assert _close(ours, g[key], rel=1e-8, abs_=1e-12), (cname, f.id, key, ours, g[key])
            n += 1
    return n


@pytest.mark.parametrize("type_", ["all", "control"])
def test_limma_on_imputed_data_matches_test_limma(type_):
    im, _, _, _ = fpa.impute(_matrix(), "perseus")
    conds = im.conditions
    pairs = fpa.all_pairs(conds, "DMSO") if type_ == "all" else [(c, "DMSO") for c in conds if c != "DMSO"]
    res = fpa.limma_contrasts(im.values, im.samples, im.condition, pairs)
    assert res[0].prior == pytest.approx((1.56481228147707, 0.26085092130773), rel=1e-9)
    n = _check(res, im, _gold(f"fpa_limma_{type_}_imputed.tsv"), lambda c: f"{c.treatment}_vs_{c.control}")
    assert n == len(pairs) * len(im.features)


def test_limma_one_vs_others_matches_test_limma():
    im, _, _, _ = fpa.impute(_matrix(), "perseus")
    res = fpa.limma_others(im.values, im.samples, im.condition)
    assert [c.treatment for c in res] == ["DMSO", "DrugA", "DrugB"]
    _check(res, im, _gold("fpa_limma_others_imputed.tsv"), lambda c: f"{c.treatment}_vs_others")


def test_limma_with_missing_values_matches_the_per_contrast_refit():
    m = _matrix()
    m.values = [r for r in m.values if any(v is not None for v in r)]
    m.features = [f for f, r in zip(_matrix().features, _matrix().values, strict=True) if any(v is not None for v in r)]
    res = fpa.limma_contrasts(m.values, m.samples, m.condition, [("DrugA", "DMSO"), ("DrugB", "DMSO")])
    assert res[0].prior == pytest.approx((3.71562731663674, 0.109342654105054), rel=1e-9)  # unequal-df ML prior
    _check(res, m, _gold("fpa_limma_control_missing.tsv"), lambda c: f"{c.treatment}_vs_{c.control}")
    untested = sum(math.isnan(p) for p in res[0].p)
    assert untested > 0  # proteins absent from DMSO can't be tested without imputation


def test_all_pairs_orientation_follows_test_limma():
    assert fpa.all_pairs(["DMSO", "A", "B"], "DMSO") == [("A", "DMSO"), ("B", "DMSO"), ("A", "B")]
    assert fpa.all_pairs(["A", "B", "C"]) == [("A", "B"), ("A", "C"), ("B", "C")]


def test_add_rejections_uses_inclusive_cut_offs():
    assert fpa.significant(1.0, 0.05, 0.05, 1.0) == "up"
    assert fpa.significant(-2.0, 0.01, 0.05, 1.0) == "down"
    assert fpa.significant(0.99, 0.001, 0.05, 1.0) == "" and fpa.significant(3, None, 0.05, 1) == ""


# ------------------------------------------------------------ filters, norm --


def test_filters_follow_fragpipe_analyst():
    samples = ["A_1", "A_2", "A_3", "B_1", "B_2", "B_3"]
    rows = [[1, 1, 1, None, None, None],   # 3/3 in A -> kept by condition filter
            [1, None, None, 1, None, None],  # 1/3 each -> dropped at 50 %
            [None] * 6]
    m = QuantMatrix("intensity", "protein", [Feature(str(i), str(i)) for i in range(3)], samples,
                    [[None if v is None else float(v) for v in r] for r in rows], {s: s[0] for s in samples})
    kept, removed = fpa.filter_missing(m, 0, 50)
    assert [f.id for f in kept.features] == ["0"] and removed == 2
    kept, removed = fpa.filter_missing(m, 30, 0)  # 3/6 and 2/6 >= 30 %
    assert [f.id for f in kept.features] == ["0", "1"]
    assert fpa.filter_missing(m, 0, 0)[1] == 0  # 0 % = step skipped, as in the app


def test_contaminants_removed():
    m = QuantMatrix("intensity", "protein", [Feature("P1", "A"), Feature("contam_sp|P00761|TRYP_PIG", "")],
                    ["x_1"], [[1.0], [2.0]], {"x_1": "x"})
    out, n = fpa.remove_contaminants(m)
    assert n == 1 and [f.id for f in out.features] == ["P1"]


def test_normalisations_align_samples_and_keep_the_scale():
    m = _matrix()
    shifted = [[None if v is None else v + (3.0 if j == 0 else 0.0) for j, v in enumerate(r)] for r in m.values]
    m.values = shifted
    for how in ("median", "gn"):
        out = fpa.normalize(m, how)
        meds = [stats.median([r[j] for r in out.values if r[j] is not None]) for j in range(len(out.samples))]
        assert max(meds) - min(meds) < 1e-9 and 20 < meds[0] < 28


def test_process_records_every_step_and_the_imputed_mask():
    p, notes = fpa.process(_matrix(), condition_pct=50, imputation="auto", normalization="median")
    names = [s["step"] for s in p.steps]
    assert names[0] == "loaded" and "missing-value filter" in names and "imputation" in names
    assert p.imputation == "perseus" and p.n_imputed > 0
    assert len(p.measured) == len(p.m.values) == len(p.imputed)
    for meas, val, msk in zip(p.measured, p.m.values, p.imputed, strict=True):
        for a, b, k in zip(meas, val, msk, strict=True):
            assert (a is None) == k and b is not None


def test_auto_imputation_is_none_for_tmt_and_ratios():
    m = _matrix()
    m.exp = "TMT"
    assert fpa.resolve_imputation("auto", m) == "none"
    m.kind = "ratio"
    assert fpa.resolve_imputation("perseus", m) == "none"


# ------------------------------------------------------------------------ QC --


def test_pca_matches_prcomp_on_a_small_case():
    # R: p <- prcomp(t(rbind(c(1,2,3,4), c(2,1,4,3), c(0,0,1,5)))): 82.94 / 14.32 / 2.74 % variance
    vals = [[1.0, 2, 3, 4], [2.0, 1, 4, 3], [0.0, 0, 1, 5]]
    res = qc.pca(vals, n_top=500)
    assert [round(v, 2) for v in res["percent"][:3]] == [82.94, 14.32, 2.74]
    pc1 = [s[0] for s in res["scores"]]  # p$x[, 1] = -2.080051 -1.912282 0.201526 3.790807 (sign is arbitrary)
    assert pc1 == pytest.approx([-2.080051, -1.912282, 0.201526, 3.790807], abs=1e-6)
    assert res["n"] == 3 and len(res["scores"]) == 4


def test_hierarchical_clustering_orders_similar_rows_together():
    rows = [[0.0, 0], [10.0, 10], [0.2, 0.1], [9.8, 10.1], [5.0, 5.0]]
    # R: hclust(dist(rbind(...)))$order = 2 4 5 1 3 ; $merge = (-2,-4) (-1,-3) (-5,2) (1,3)
    assert qc.cluster_order(rows) == [1, 3, 4, 0, 2]
    assert [m[:2] for m in qc.hclust(rows)] == [(-2, -4), (-1, -3), (-5, 2), (1, 3)]


# ----------------------------------------------- whole pipeline vs the package --


def test_whole_pipeline_matches_fragpipeanalystr(tmp_path):
    """Ionomos's analyze() vs the real FragPipeAnalystR 1.1.1 running the exported reproduce_in_R.R."""
    import shutil

    from ionomos import downstream

    e2e = GOLD / "e2e"
    dest = tmp_path / "exp"
    (dest / "fragpipe/dia-quant-output").mkdir(parents=True)
    shutil.copy(e2e / "report.pg_matrix.tsv", dest / "fragpipe/dia-quant-output/report.pg_matrix.tsv")
    conds = ("DMSO", "DrugA", "DrugB")
    record = {"plan": {"manifest": [{"file": f"EXP_{c}_{r}.raw", "experiment": c, "bioreplicate": r}
                                    for c in conds for r in (1, 2, 3)]}}
    out = downstream.analyze(dest, "DIA", {"enrichment": False}, record=record)
    assert not out.warnings
    _, ours = read_tsv(dest / "results/protein_results.tsv")
    _, theirs = read_tsv(e2e / "FragPipeAnalystR_results.tsv")
    assert len(ours) == len(theirs) == 399
    by_id = {r["ID"]: r for r in theirs}
    for o in ours:
        t = by_id[o["id"]]
        for comp in ("DrugA_vs_DMSO", "DrugB_vs_DMSO"):
            for a, b in (("log2fc", "diff"), ("p", "p.val"), ("p_adj", "p.adj"), ("ci_low", "CI.L"), ("ci_high", "CI.R")):
                assert _close(float(o[f"{comp}_{a}"]), float(t[f"{comp}_{b}"]), rel=1e-8, abs_=1e-10), (o["id"], comp, a)
            assert (o[f"{comp}_significant"] != "") == (t[f"{comp}_significant"] == "TRUE"), (o["id"], comp)
    _, ann = read_tsv(dest / "results/fragpipe-analyst/experiment_annotation.tsv")
    _, ann_gold = read_tsv(e2e / "experiment_annotation.tsv")
    assert ann == ann_gold


# ---------------------------------------------------------------- enrichment --


def test_hypergeometric_tail_matches_phyper():
    from ionomos.downstream import enrich

    # R: phyper(k - 1, K, N - K, n, lower.tail = FALSE)
    for (k, big_k, n, big_n), want in (((5, 30, 110, 2500), 8.8888286525109980e-03),
                                        ((28, 50, 110, 2491), 1.3378026678470101e-26),
                                        ((1, 10, 20, 1000), 1.8368205277375471e-01),
                                        ((3, 5, 10, 100), 6.6379128971176165e-03)):
        assert enrich.hyper_upper(k, big_k, n, big_n) == pytest.approx(want, rel=1e-9)


def test_enrichment_libraries_cache_offline_and_gmt(tmp_path, monkeypatch):
    from ionomos.downstream import enrich

    monkeypatch.setenv("IONOMOS_GENESETS", str(tmp_path / "gs"))
    monkeypatch.setenv("IONOMOS_OFFLINE", "1")
    libs, notes = enrich.load_libraries(["Hallmark"])
    assert libs == {} and "offline" in notes[0]  # explained, never raised
    (tmp_path / "gs").mkdir()
    (tmp_path / "gs" / "MSigDB_Hallmark_2020.txt").write_text(
        "TNF-alpha Signaling\t\tJUNB,1.0\tatf3\tNFKBIA\nEmpty\t\t\n", encoding="utf-8")
    gmt = tmp_path / "lab.gmt"
    gmt.write_text("MY_SET\tdesc\tA\tB\tC\n", encoding="utf-8")
    libs, notes = enrich.load_libraries(["Hallmark", "Nope"], str(gmt))
    assert libs["Hallmark"] == {"TNF-alpha Signaling": {"JUNB", "ATF3", "NFKBIA"}} and libs["lab"] == {"MY_SET": {"A", "B", "C"}}
    assert "unknown library 'Nope'" in notes[0]
    assert enrich.gene_symbol("CA9;CA9P1") == "CA9" and enrich.gene_symbol("ca9 C174") == "CA9"
    assert enrich.gene_symbol("ACTB.1") == "ACTB"


def test_ora_ranks_the_planted_term_first():
    from ionomos.downstream import enrich

    bg = [f"G{i}" for i in range(1000)]
    lib = {"planted": set(bg[:40]), "random": set(bg[500:540]), "tiny": {"G1", "G2"}}
    rows = enrich.ora(bg[:20] + bg[900:910], bg, lib)
    assert rows[0]["term"] == "planted" and rows[0]["k"] == 20 and rows[0]["K"] == 40 and rows[0]["q"] < 1e-10
    assert all(r["term"] != "tiny" for r in rows)  # fewer than 3 genes in the background: not tested
    assert enrich.ora([], bg, lib) == []
