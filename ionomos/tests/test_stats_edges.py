"""The statistics on degenerate input, and invariants that must hold on any design (round-4 stats audit).

    adversarial    n = 1 and n = 0 groups, all-missing features, constant columns, duplicate IDs, ±inf / NaN / 0 /
                   negative values, one sample, 500 samples, values rounded to 3 decimals, a block with one level,
                   a condition compared with itself, a sample without a single value under every imputation
    properties     seeded random designs (unequal groups, missing values, blocks): A vs B is the mirror of B vs A;
                   no p-value where a group has no value or nothing estimates a variance (D32); the missing-value
                   filter removes exactly the features that fail it and the comparisons report only the ones it
                   kept; IRS leaves a single plex unchanged (D48); the roles never compare a condition with itself
                   and a control is only ever the treatment against a pull-down's background (D61, D88)

Nothing here changes a golden: the goldens in tests/golden/ are checked by their own tests, these only add input
those references never see.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import analysis, fpa, plex, roles, stats
from ionomos.downstream import design as dz
from ionomos.downstream.quant import Feature, QuantMatrix

# ------------------------------------------------------------------ helpers --


def matrix(vals, samples, cond, exp="LFQ", kind="intensity") -> QuantMatrix:
    feats = [Feature(f"P{i:04d}", f"G{i}") for i in range(len(vals))]
    m = QuantMatrix(kind, "protein", feats, list(samples), [list(r) for r in vals], dict(cond), exp=exp)
    m.replicate = {s: int(s.rsplit("_", 1)[1]) for s in samples if s.rsplit("_", 1)[-1].isdigit()}
    return m


def sim(seed: int, n: int = 120, groups=(("DMSO", 3), ("Drug", 3)), miss: float = 0.1, every: int = 10,
        effect: float = 1.5) -> QuantMatrix:
    """Log2 intensities; every `every`-th feature `effect` up in every condition but the first."""
    rng = random.Random(seed)
    samples = [f"{c}_{r}" for c, k in groups for r in range(1, k + 1)]
    cond = {s: s.rsplit("_", 1)[0] for s in samples}
    first = groups[0][0]
    vals = []
    for i in range(n):
        b = rng.gauss(22, 2)
        vals.append([None if rng.random() < miss else
                     b + (effect if i % every == 0 and cond[s] != first else 0.0) + rng.gauss(0, 0.3)
                     for s in samples])
    return matrix(vals, samples, cond)


def random_design(rng: random.Random, n: int = 60, miss: float = 0.2, exp: str = "LFQ") -> QuantMatrix:
    """2-4 conditions of 1-5 samples in a shuffled order, features with different spreads and missing values."""
    conds = [f"C{i}" for i in range(rng.randint(2, 4))]
    samples = [f"{c}_{r}" for c in conds for r in range(1, rng.randint(1, 5) + 1)]
    rng.shuffle(samples)
    vals = []
    for _ in range(n):
        b, sd = rng.gauss(22, 2), rng.choice([0.1, 0.4, 1.0])
        vals.append([None if rng.random() < miss else b + rng.gauss(0, sd) for _ in samples])
    return matrix(vals, samples, {s: s.rsplit("_", 1)[0] for s in samples}, exp=exp)


def same(a, b, tol: float = 1e-9) -> bool:
    if a is None or b is None:
        return a is b
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def untested(r: fpa.ContrastResult) -> bool:
    return all(math.isnan(p) for p in r.p)


def write_table(path: Path, m: QuantMatrix, digits: int = 4, linear: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(["Protein", "Gene", *m.samples]) + "\n")
        for f, row in zip(m.features, m.values, strict=True):
            cells = ["" if v is None else (f"{2 ** v:.1f}" if linear else f"{v:.{digits}f}") for v in row]
            fh.write("\t".join([f.id, f.label, *cells]) + "\n")
    return path


def analyze_table(tmp_path: Path, header: list[str], rows: list[list[str]], **settings):
    tmp_path.mkdir(parents=True, exist_ok=True)
    table = tmp_path / "t.tsv"
    table.write_text("\n".join("\t".join(r) for r in [header, *rows]) + "\n", encoding="utf-8")
    ws = tmp_path / "t_ionomos"
    ws.mkdir(exist_ok=True)
    out = downstream.analyze(ws, analysis_cfg={"enrichment": False, **settings}, table=table)

    def refuse(token):
        raise ValueError(f"{token} is not JSON")

    summary = json.loads((ws / "results" / "analysis.json").read_text(encoding="utf-8"), parse_constant=refuse)
    crashed = [i.code for i in out.issues if i.code.startswith("CRASH")]
    assert not crashed, crashed
    assert out.report is not None and out.report.is_file()
    return out, summary


def table_rows(m: QuantMatrix, digits: int = 4) -> tuple[list[str], list[list[str]]]:
    return (["Protein", "Gene", *m.samples],
            [[f.id, f.label, *("" if v is None else f"{v:.{digits}f}" for v in row)]
             for f, row in zip(m.features, m.values, strict=True)])


# ------------------------------------------------- a condition against itself --


def test_a_comparison_of_a_condition_with_itself_is_refused_with_a_reason():
    for c in ("Drug vs Drug", ["Drug", "Drug"], " Drug  versus Drug "):
        with pytest.raises(analysis.AnalysisError, match="compares Drug with itself"):
            analysis.parse_comparison(c)
    with pytest.raises(analysis.AnalysisError, match="itself"):
        analysis.settings_from({"comparisons": ["Drug vs DMSO", "Drug vs Drug"]})
    s, notes = analysis.settings_lenient({"comparisons": ["Drug vs Drug"]})
    assert s.comparisons == [] and any("itself" in n for n in notes)
    assert analysis.parse_comparison("Drug vs drug") == ("Drug", "drug")  # two names: choose_comparisons judges them
    m = sim(1)
    with pytest.raises(analysis.AnalysisError, match="itself"):  # Settings made in code, not parsed
        analysis.choose_comparisons(m, replace(analysis.Settings(), comparisons=[("Drug", "Drug")]))


def test_a_contrast_of_a_condition_with_itself_is_never_tested():
    """D32: every p-value would be 1 (limma) or the design's fit divided by zero (it crashed the statistics stage
    and the blocked design was dropped for 'division by zero')."""
    m = sim(2)
    plain = fpa.limma_contrasts(m.values, m.samples, m.condition, [("Drug", "Drug"), ("Drug", "DMSO")])
    assert untested(plain[0]) and not untested(plain[1])
    blocked = dz.build(m, "replicate")
    res, _info = dz.limma_design(m.values, blocked, [("Drug", "Drug"), ("Drug", "DMSO")])
    assert untested(res[0]) and not untested(res[1])
    assert all(math.isnan(x) for x in res[0].se) and all(math.isnan(x) for x in res[0].ci_low)
    p, _ = fpa.process(m, imputation="none")
    for test in ("welch", "student"):
        r = analysis._classic(p, "Drug", "Drug", replace(analysis.Settings(), test=test))
        assert untested(r)


def test_a_self_comparison_in_the_settings_falls_back_to_the_defaults_end_to_end(tmp_path):
    h, rows = table_rows(sim(3))
    for k, extra in enumerate(({}, {"block": "replicate"})):
        out, summary = analyze_table(tmp_path / str(k), h, rows, comparisons=["Drug vs Drug"], **extra)
        names = [c["name"] for c in summary["comparisons"]]
        assert names == ["Drug vs DMSO"]
        assert any("itself" in w for w in out.warnings)
        assert "DESIGN_NOT_USED" not in [i.code for i in out.issues]  # no 'division by zero'


# ------------------------------------------------------------ small groups --


def test_groups_of_one_and_of_none_get_no_invented_p_value():
    rng = random.Random(4)
    one = matrix([[rng.gauss(20, 1), rng.gauss(20, 1)] for _ in range(30)], ["A_1", "B_1"], {"A_1": "A", "B_1": "B"})
    assert untested(fpa.limma_contrasts(one.values, one.samples, one.condition, [("B", "A")])[0])
    assert untested(dz.limma_design(one.values, dz.plain(one), [("B", "A")])[0][0])
    assert stats.welch_t([1.0], [2.0, 3.0])[2] != stats.welch_t([1.0], [2.0, 3.0])[2]
    assert math.isnan(stats.student_t([1.0], [2.0])[2])
    assert math.isnan(stats.one_sample_t([0.3])[2])
    assert untested(fpa.limma_one_sample([[0.5], [0.2], [0.1]], [0], "A", min_valid=1))
    # 1 against 3: tested, the variance comes from the group of three (D32: low confidence, never refused)
    m = sim(5, groups=(("DMSO", 1), ("Drug", 3)), miss=0)
    r = fpa.limma_contrasts(m.values, m.samples, m.condition, [("Drug", "DMSO")])[0]
    assert not any(math.isnan(p) for p in r.p)
    # a group without a value for a feature (n = 0): that feature only is untested
    vals = [list(row) for row in m.values]
    vals[3][0] = None
    r = fpa.limma_contrasts(vals, m.samples, m.condition, [("Drug", "DMSO")])[0]
    assert math.isnan(r.p[3]) and math.isnan(r.diff[3]) and r.n_control[3] == 0
    assert sum(math.isnan(p) for p in r.p) == 1


def test_a_single_condition_has_no_f_test():
    """The moderated F with no contrast gave F = 0, p = 1 for every feature."""
    m = sim(6, groups=(("DMSO", 3),))
    ft = dz.f_test(m.values, dz.plain(m), "DMSO")
    assert ft.df1 == 0 and all(math.isnan(p) for p in ft.p) and all(math.isnan(q) for q in ft.q)


def test_one_sample_in_total_and_one_sample_per_condition_end_to_end(tmp_path):
    h, rows = table_rows(sim(7, groups=(("DMSO", 1),)))
    out, summary = analyze_table(tmp_path / "one", h, rows)
    assert summary["comparisons"] == [] and "ONE_SAMPLE" in [i.code for i in out.issues]
    h, rows = table_rows(sim(8, groups=(("DMSO", 1), ("Drug", 1))))
    out, summary = analyze_table(tmp_path / "each", h, rows)
    (c,) = summary["comparisons"]
    assert c["confidence"] == "fold change only" or c.get("tested", 0) == 0
    tsv = (tmp_path / "each" / "t_ionomos" / c["table"]).read_text(encoding="utf-8").splitlines()
    col = tsv[0].split("\t").index("pvalue")
    assert all(line.split("\t")[col] in ("", "NA") for line in tsv[1:])  # fold change only: no p-value at all


# ----------------------------------------------------- missing and odd values --


def test_all_missing_features_are_dropped_once_and_never_tested():
    m = sim(9, miss=0)
    for i in (0, 5, 7):
        m.values[i] = [None] * len(m.samples)
    p, _notes = fpa.process(m, imputation="perseus")
    assert len(p.m.features) == len(m.features) - 3
    assert {f.id for f in p.m.features}.isdisjoint({"P0000", "P0005", "P0007"})
    assert [s for s in p.steps if s["step"] == "no values at all"][0]["removed"] == 3
    p, _ = fpa.process(m, imputation="none")
    r = fpa.limma_contrasts(p.m.values, p.m.samples, p.m.condition, [("Drug", "DMSO")])[0]
    assert len(r.p) == len(p.m.features) and not any(math.isnan(x) for x in r.p)


@pytest.mark.parametrize("method", ["mindet", "minprob", "knn"])
def test_a_sample_without_a_single_value_is_not_imputed(method):
    """mindet / minprob wrote NaN into every row (each feature then untested in every comparison, silently); knn
    wrote the log2 value 0. The sample stays missing, as under perseus, and says so."""
    m = sim(10, groups=(("DMSO", 4), ("Drug", 4)), miss=0.1)
    for row in m.values:
        row[0] = None
    p, notes = fpa.process(m, normalization="median", imputation=method)
    assert all(row[0] is None for row in p.m.values) and not any(mk[0] for mk in p.imputed)
    assert any("DMSO_1 has no values at all" in n for n in notes)
    assert all(v is not None and math.isfinite(v) for row in p.m.values for v in row[1:])
    r = fpa.limma_contrasts(p.m.values, p.m.samples, p.m.condition, [("Drug", "DMSO")])[0]
    assert sum(not math.isnan(x) for x in r.p) == len(p.m.features)


def test_an_empty_sample_takes_no_random_draws():
    """minprob's draws for the other samples do not depend on where an empty sample sits (R's rnorm draws nothing
    for a mean of NA)."""
    m = sim(11, groups=(("DMSO", 3), ("Drug", 3)), miss=0.3)
    empty_first = [[None, *row] for row in m.values]
    a = fpa.impute(matrix(empty_first, ["DMSO_0", *m.samples], {"DMSO_0": "DMSO", **m.condition}), "minprob")[0]
    empty_last = [[*row, None] for row in m.values]
    b = fpa.impute(matrix(empty_last, [*m.samples, "Drug_9"], {"Drug_9": "Drug", **m.condition}), "minprob")[0]
    assert [row[1:] for row in a.values] == [row[:-1] for row in b.values]


def test_non_finite_values_count_as_missing_in_both_models():
    """limma's lmFit drops what is not finite. An infinite value gave an infinite fold change with p = 0."""
    m = sim(12, miss=0)
    with_none = [list(r) for r in m.values]
    odd = [list(r) for r in m.values]
    for i, v in ((1, math.inf), (2, -math.inf), (3, math.nan)):
        odd[i][4] = v
        with_none[i][4] = None
    a = fpa.limma_contrasts(odd, m.samples, m.condition, [("Drug", "DMSO")])[0]
    b = fpa.limma_contrasts(with_none, m.samples, m.condition, [("Drug", "DMSO")])[0]
    for i in range(len(odd)):
        assert same(a.p[i], b.p[i], 0) and same(a.diff[i], b.diff[i], 0)
    assert all(math.isfinite(x) for x in a.diff) and min(a.p) > 0
    des = dz.build(m, "replicate")
    a = dz.limma_design(odd, des, [("Drug", "DMSO")], 2)[0][0]
    b = dz.limma_design(with_none, des, [("Drug", "DMSO")], 2)[0][0]
    for i in range(len(odd)):
        assert same(a.p[i], b.p[i], 0) and same(a.diff[i], b.diff[i], 0) and a.n_control[i] == b.n_control[i]


def test_zero_negative_and_non_numbers_in_a_table_end_to_end(tmp_path):
    m = sim(13, n=80)
    h, rows = ["Protein", "Gene", *m.samples], []
    for i, (f, row) in enumerate(zip(m.features, m.values, strict=True)):
        cells = ["" if v is None else f"{2 ** v:.1f}" for v in row]
        if i < 30:
            cells[i % 6] = ["0", "-5", "inf", "-inf", "NaN", "nan"][i % 6]
        rows.append([f.id, f.label, *cells])
    out, summary = analyze_table(tmp_path, h, rows)
    (c,) = summary["comparisons"]
    assert c["tested"] > 0
    tsv = (tmp_path / "t_ionomos" / c["table"]).read_text(encoding="utf-8")
    assert "inf" not in tsv.lower() or "inf\t" not in tsv.lower()


def test_constant_values_give_no_hit_and_are_reported(tmp_path):
    m = sim(14, miss=0)
    flat = [[20.0] * len(m.samples) for _ in m.values]
    r = fpa.limma_contrasts(flat, m.samples, m.condition, [("Drug", "DMSO")])[0]
    assert all(math.isnan(p) or p == 1.0 for p in r.p)
    for method in ("median", "gn", "ratio", "auto"):
        out, info = fpa.normalize_info(matrix(flat, m.samples, m.condition), method)
        assert out.values == flat, method
    one_col = [list(r) for r in m.values]
    for row in one_col:
        row[2] = 18.0  # a sample holding one value everywhere (a filled column)
    out, _ = fpa.normalize_info(matrix(one_col, m.samples, m.condition), "gn")
    assert all(v is not None and math.isfinite(v) for row in out.values for v in row)
    h = ["Protein", "Gene", *m.samples]
    rows = [[f.id, f.label, *["20.0"] * len(m.samples)] for f in m.features]
    out, summary = analyze_table(tmp_path, h, rows, normalize="none")
    codes = [i.code for i in out.issues]
    assert "ZERO_VARIANCE" in codes and "IDENTICAL_SAMPLES" in codes
    assert all(c["up"] == 0 and c["down"] == 0 for c in summary["comparisons"])


def test_duplicate_feature_ids_are_each_analysed_and_named(tmp_path):
    m = sim(15)
    h, rows = table_rows(m)
    for r in rows[:20]:
        r[0] = "P0000"
    out, summary = analyze_table(tmp_path, h, rows)
    (c,) = summary["comparisons"]
    lines = (tmp_path / "t_ionomos" / c["table"]).read_text(encoding="utf-8").splitlines()[1:]
    assert sum(1 for x in lines if x.startswith("P0000\t")) == 20
    assert any("share their ID" in n for n in out.warnings + summary.get("notes", []))


def test_values_rounded_to_three_decimals_give_the_same_answer():
    """A table written with 3 decimals instead of 10: each log2 value moves by at most 5e-4, so a fold change by at
    most 2e-3 (the median centring moves by as much again); the hits are the same."""
    m = sim(16, n=300)
    out = {}
    for digits in (10, 3):
        r = matrix([[None if v is None else round(v, digits) for v in row] for row in m.values], m.samples,
                   m.condition)
        p, _ = fpa.process(r, normalization="median", imputation="none")
        out[digits] = fpa.limma_contrasts(p.m.values, p.m.samples, p.m.condition, [("Drug", "DMSO")], 2)[0]
    a, b = out[10], out[3]
    assert max(abs(x - y) for x, y in zip(a.diff, b.diff, strict=True) if x == x) <= 2e-3
    assert max(abs(math.log10(x) - math.log10(y)) for x, y in zip(a.p, b.p, strict=True) if x == x) < 0.05
    hits = [{i for i, (q, d) in enumerate(zip(r.q, r.diff, strict=True)) if q <= 0.05 and abs(d) >= 1} for r in (a, b)]
    assert hits[0] == hits[1] and len(hits[0]) >= 20


def test_five_hundred_samples_are_processed_and_tested_quickly():
    import time

    m = sim(17, n=200, groups=tuple((f"C{k}", 50) for k in range(10)))
    t0 = time.perf_counter()
    p, _ = fpa.process(m, normalization="auto", imputation="perseus", condition_pct=50)
    comps = [(f"C{k}", "C0") for k in range(1, 10)]
    res = fpa.limma_contrasts(p.m.values, p.m.samples, p.m.condition, comps)
    ft = dz.f_test(p.m.values, dz.plain(p.m), "C0")
    p.m.replicate = {s: int(s.rsplit("_", 1)[1]) % 5 for s in p.m.samples}
    blocked, _ = dz.limma_design(p.m.values, dz.build(p.m, "replicate"), comps)
    assert time.perf_counter() - t0 < 30
    assert len(res) == 9 and all(not untested(r) for r in res + blocked) and not all(math.isnan(x) for x in ft.p)


def test_a_block_with_one_level_is_explained_and_the_plain_model_is_used(tmp_path):
    m = sim(18)
    with pytest.raises(dz.DesignError, match="single level"):
        dz.build(m, {s: "batch1" for s in m.samples})
    with pytest.raises(dz.DesignError, match="single level"):
        dz.build(m, "", block_from=r"^(\w)")  # every name starts with D
    h, rows = table_rows(m)
    out, summary = analyze_table(tmp_path / "block", h, rows, block={s: "batch1" for s in m.samples})
    assert "DESIGN_NOT_USED" in [i.code for i in out.issues]
    _, plain = analyze_table(tmp_path / "plain", h, rows)
    a = (tmp_path / "block" / "t_ionomos" / summary["comparisons"][0]["table"]).read_text(encoding="utf-8")
    b = (tmp_path / "plain" / "t_ionomos" / plain["comparisons"][0]["table"]).read_text(encoding="utf-8")
    assert a == b


# ------------------------------------------------------------- properties --


@pytest.mark.parametrize("seed", range(40))
def test_a_vs_b_is_the_mirror_of_b_vs_a(seed):
    rng = random.Random(seed)
    m = random_design(rng)
    a, b = rng.sample(m.conditions, 2)
    s = analysis.Settings()
    for mv in (0, 2):
        needs = analysis.group_needs(m, [(a, b), (b, a)], s, mv)
        assert needs[(a, b)] == needs[(b, a)][::-1]
        r1, r2 = fpa.limma_contrasts(m.values, m.samples, m.condition, [(a, b), (b, a)], min_valid=mv, needs=needs)
        fits = [(r1, r2)]
        try:
            des = dz.build(m, "replicate")
        except dz.DesignError:
            des = None
        if des is not None:
            fits.append(tuple(dz.limma_design(m.values, des, [(a, b), (b, a)], mv, needs=needs)[0]))
        for x, y in fits:
            for i in range(len(m.values)):
                assert same(x.diff[i], -y.diff[i]) and same(x.t[i], -y.t[i]), (seed, mv, i)
                assert same(x.p[i], y.p[i]) and same(x.q[i], y.q[i]), (seed, mv, i)
                assert same(x.ci_low[i], -y.ci_high[i]) and same(x.se[i], y.se[i]), (seed, mv, i)
                assert x.n_treatment[i] == y.n_control[i]
    for test in (stats.welch_t, stats.student_t):
        xa = [rng.gauss(0, 1) for _ in range(rng.randint(1, 5))]
        xb = [rng.gauss(0, 1) for _ in range(rng.randint(1, 5))]
        (t1, d1, p1), (t2, d2, p2) = test(xa, xb), test(xb, xa)
        assert same(t1, -t2) and same(d1, d2) and same(p1, p2)


@pytest.mark.parametrize("seed", range(20))
def test_the_mirror_holds_through_the_pipeline(seed):
    """run_contrasts + to_diff on processed data: fold change negated, the same p and q, up and down swapped."""
    rng = random.Random(100 + seed)
    m = random_design(rng, n=80, miss=0.25)
    a, b = rng.sample(m.conditions, 2)
    s = replace(analysis.Settings(), imputation=rng.choice(["none", "perseus"]), test=rng.choice(["limma", "welch"]),
                log2fc=0.5, alpha=0.2)
    p, _ = fpa.process(m, normalization="median", imputation=s.imputation)
    small = min(len(p.m.samples_of(a)), len(p.m.samples_of(b))) < s.min_valid
    comps = [(a, b), (b, a)]
    res = analysis.run_contrasts(p, comps, s, set(comps) if small else set(), analysis.Model())
    d1, d2 = (analysis.to_diff(p, r, c, s) for r, (_t, c) in zip(res, comps, strict=True))
    by = {r["index"]: r for r in d2.rows}
    flip = {"up": "down", "down": "up", "": ""}
    for r in d1.rows:
        o = by[r["index"]]
        assert same(r["log2fc"], None if o["log2fc"] is None else -o["log2fc"])
        assert same(r["pvalue"], o["pvalue"]) and same(r["qvalue"], o["qvalue"])
        assert r["significant"] == flip[o["significant"]]
        assert (r["n_treatment"], r["n_control"]) == (o["n_control"], o["n_treatment"])


@pytest.mark.parametrize("seed", range(30))
def test_no_p_value_is_invented(seed):
    """D32: a feature has a p-value only when both groups have a value (and, without imputation, the values the
    comparison needs) and something estimates a variance; an F only when every condition has a value."""
    rng = random.Random(200 + seed)
    m = random_design(rng, miss=0.35)
    s = replace(analysis.Settings(), imputation=rng.choice(["none", "perseus"]))
    p, _ = fpa.process(m, normalization="median", imputation=s.imputation)
    pm = p.m
    conds = pm.conditions
    comps = fpa.all_pairs(conds)
    small = {(t, c) for t, c in comps if min(len(pm.samples_of(t)), len(pm.samples_of(c))) < s.min_valid}
    res = analysis.run_contrasts(p, comps, s, small, analysis.Model())
    any_df = any(len(pm.samples_of(c)) > 1 for c in conds)
    for (t, c), r in zip(comps, res, strict=True):
        it, ic = pm.samples_of(t), pm.samples_of(c)
        need = analysis.group_needs(pm, [(t, c)], s, 0 if (t, c) in small else s.min_valid)[(t, c)]
        for i, row in enumerate(pm.values):
            if math.isnan(r.p[i]):
                continue
            nt = sum(row[pm.samples.index(x)] is not None for x in it)
            nc = sum(row[pm.samples.index(x)] is not None for x in ic)
            assert nt >= 1 and nc >= 1 and any_df, (seed, t, c, i)
            if s.imputation == "none":
                assert nt >= need[0] and nc >= need[1], (seed, t, c, i, nt, nc, need)
            assert 0.0 <= r.p[i] <= 1.0 and r.q[i] >= r.p[i] - 1e-15
    if len(conds) >= 3:
        ft = dz.f_test(pm.values, dz.plain(pm), conds[0], 0 if s.imputation != "none" else s.min_valid)
        for i, row in enumerate(pm.values):
            if not math.isnan(ft.p[i]):
                assert all(any(row[pm.samples.index(x)] is not None for x in pm.samples_of(c)) for c in conds)


@pytest.mark.parametrize("seed", range(30))
def test_the_filter_removes_exactly_what_fails_it_and_the_tests_report_only_what_it_kept(seed):
    rng = random.Random(300 + seed)
    m = random_design(rng, n=80, miss=rng.choice([0.2, 0.5, 0.7]))
    for k in range(rng.randint(0, 3)):  # a contaminant or two, and rows with nothing
        m.features[k] = Feature(f"contam_sp|P{k}|X", "")
    m.values[-1] = [None] * len(m.samples)
    g, c = rng.choice([0, 30, 50, 100]), rng.choice([0, 50, 70, 100])
    p, _ = fpa.process(m, global_pct=g, condition_pct=c, imputation=rng.choice(["none", "perseus"]))
    before = {f.id: row for f, row in zip(m.features, m.values, strict=True) if not fpa.is_contaminant(f)}

    def passes(row) -> bool:
        have = [v is not None for v in row]
        if not any(have):
            return False
        if g and sum(have) / len(have) < g / 100 - 1e-12:
            return False
        groups = [[j for j, x in enumerate(m.samples) if m.condition[x] == cc] for cc in m.conditions]
        return not c or any(sum(have[j] for j in grp) / len(grp) >= c / 100 - 1e-12 for grp in groups)

    kept = [f.id for f in p.m.features]
    assert len(kept) == len(set(kept))
    assert set(kept) == {fid for fid, row in before.items() if passes(row)}
    removed = sum(st.get("removed", 0) for st in p.steps)
    assert removed == len(m.features) - len(kept)  # each feature removed once, by one step
    assert len(p.measured) == len(p.imputed) == len(p.m.values) == len(kept)
    comps = fpa.all_pairs(p.m.conditions)
    res = analysis.run_contrasts(p, comps, analysis.Settings(), set(comps), analysis.Model())
    for (_t, cc), r in zip(comps, res, strict=True):
        d = analysis.to_diff(p, r, cc, analysis.Settings())
        assert sorted(x["id"] for x in d.rows) == sorted(kept)


@pytest.mark.parametrize("seed", range(10))
def test_irs_leaves_a_single_plex_and_label_free_data_unchanged(seed):
    rng = random.Random(400 + seed)
    for exp, plexes in (("TMT", 1), ("TMT", 0), ("LFQ", 2)):
        m = random_design(rng, exp=exp)
        if plexes:
            m.meta["plex"] = {s: str(1 + (j % plexes)) for j, s in enumerate(m.samples)}
        snapshot = ([list(r) for r in m.values], list(m.samples))
        for mode in plex.IRS_MODES:
            for ref in ([], ["C0"]):
                out, info, _notes = plex.normalise(m, replace(analysis.Settings(), irs=mode, tmt_reference=ref))
                assert (out.values, out.samples) == snapshot, (exp, plexes, mode, ref)
                assert info is None or not info.get("applied")
        p, _ = fpa.process(m)
        assert plex.df_spent(p) is None


ROLE_WORDS = ["DMSO", "Vehicle", "Mock", "Probe", "Probe_Comp", "Drug", "Drug_10x", "DrugComp", "Pool", "QC", "EV",
              "IgG", "Beads", "KO", "WT", "Probe_pre", "Cmpd", "Cmpd_Comp", "Bridge", "HeLa"]


@pytest.mark.parametrize("seed", range(60))
def test_the_roles_never_compare_a_condition_with_itself(seed):
    """D61 / D88: whatever the names, roles and settings, every comparison has two different conditions, none twice;
    a control is a treatment only against a pull-down's background; a background or a pool never is one."""
    rng = random.Random(500 + seed)
    conds = rng.sample(ROLE_WORDS, rng.randint(2, 6))
    sizes = {c: rng.randint(1, 4) for c in conds}
    s = analysis.Settings()
    if rng.random() < 0.3:
        s = replace(s, control=rng.choice(conds))
    if rng.random() < 0.3:
        c = rng.choice(conds)
        s = replace(s, roles={c: rng.choice(["control", "compound", "background", "reference", "qc"])})
    if rng.random() < 0.2:
        s = replace(s, de_type=rng.choice(["all", "others"]))
    name = rng.choice(["", "FLAG_IP", "KL_proteome"])
    view = roles.preview(sizes, s, name=name)
    m = QuantMatrix("intensity", "protein", [], [f"{c}#{i}" for c in conds for i in range(sizes[c])], [],
                    {f"{c}#{i}": c for c in conds for i in range(sizes[c])},
                    meta={"background": roles.background_evidence(None, s, name, conds)})
    plan = roles.plan(m, s)
    pairs = view.pairs
    assert len(pairs) == len(set(pairs)), pairs
    for t, c in pairs:
        assert t != c and t in conds and (c in conds or c in (None, "others")), (t, c)
    if plan.active:
        assert pairs == plan.comparisons
        role = {c: r.role for c, r in plan.roles.items()}
        for t, c in pairs:  # analysis.control set: every condition is still compared with it (D61), a QC too
            assert role[t] not in ("background",) + (() if s.control else ("reference", "qc")), (t, c, role)
            if role[t] == "control" and not s.control:  # a second vehicle is compared only with a named control
                assert role[c] == "background", (t, c, role)
        if plan.control is not None and plan.control not in s.roles:  # roles and control may disagree: a note
            assert role[plan.control] == "control"


def test_a_control_that_the_roles_make_a_compound_is_not_compared_with_itself():
    """analysis.control: EV with analysis.roles {EV: compound} (the Control picker and the role editor disagree)
    gave the comparison 'EV vs EV'."""
    s = replace(analysis.Settings(), control="EV", roles={"EV": "compound"})
    view = roles.preview({"Cmpd_Comp": 1, "EV": 3, "Cmpd": 3, "Probe": 3}, s)
    assert ("EV", "EV") not in view.pairs and ("Cmpd", "EV") in view.pairs and ("Cmpd_Comp", "Cmpd") in view.pairs
    m = sim(19, groups=(("Cmpd_Comp", 2), ("EV", 3), ("Cmpd", 3)))
    comps, notes = analysis.choose_comparisons(m, s)
    assert all(t != c for t, c in comps) and comps
    assert any("which analysis.roles makes a compound" in n for n in notes)


@pytest.mark.parametrize("missing", ["NA", "nan", float("nan"), "not available", "inf", None, " "])
def test_a_missing_covariate_or_block_value_is_named_not_made_a_level(missing):
    """'NA' next to 1 and 2 became a third factor level (the sample silently got a batch of its own), or, for a
    numeric covariate, turned every age into a level and was reported as 'confounded with condition'."""
    m = sim(20, groups=(("DMSO", 4), ("Drug", 4)))
    age = {s: 30 + 5 * k for k, s in enumerate(m.samples)}
    batch = {s: str(1 + k % 2) for k, s in enumerate(m.samples)}
    for spec in ({"age": {**age, "Drug_2": missing}}, {"batch": {**batch, "Drug_2": missing}}):
        with pytest.raises(dz.DesignError, match="no value for: Drug_2"):
            dz.build(m, covariates=spec)
    with pytest.raises(dz.DesignError, match="no block: Drug_2"):
        dz.build(m, {**batch, "Drug_2": missing})
    assert dz.build(m, covariates={"pre": {s: ("none" if k % 2 else "LPS") for k, s in enumerate(m.samples)}})
