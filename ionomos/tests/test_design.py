"""Experimental designs (blocks, covariates), the moderated F and DEqMS (downstream/design.py, deqms.py) against R.

Golden files in tests/golden/design/ come from make_design_inputs.py + run_design_reference.R
(limma 3.68.5: lmFit → contrasts.fit → eBayes → topTable / topTableF; DEqMS 1.30.0 spectraCounteBayes).
"""
import math
from pathlib import Path

import pytest

from ionomos.downstream import analysis, deqms, design, fpa
from ionomos.downstream.quant import Feature, QuantMatrix
from ionomos.downstream.tables import read_tsv

GOLD = Path(__file__).parent / "golden" / "design"
VS = [("DrugA", "DMSO"), ("DrugB", "DMSO")]
PAIRS = [*VS, ("DrugB", "DrugA")]


def _f(x: str) -> float:
    x = x.strip()
    return math.nan if x in ("NA", "NaN", "") else float(x)


def _close(a: float, b: float, rel: float = 1e-8, abs_: float = 1e-12) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def _matrix(name: str = "design_matrix.tsv") -> QuantMatrix:
    header, rows = read_tsv(GOLD / name)
    samples = header[1:]
    _, srows = read_tsv(GOLD / "design_samples.tsv")
    info = {r["sample"]: r for r in srows}
    _, crows = read_tsv(GOLD / "design_counts.tsv")
    counts = {r["ID"]: int(r["count"]) for r in crows}
    vals = [[None if r[s].strip() == "NA" else float(r[s]) for s in samples] for r in rows]
    m = QuantMatrix("intensity", "protein", [Feature(r["ID"], r["ID"], peptides=counts[r["ID"]]) for r in rows],
                    samples, vals, {s: info[s]["condition"] for s in samples}, str(GOLD / name))
    m.replicate = {s: int(info[s]["replicate"]) for s in samples}
    m.meta["age"] = {s: float(info[s]["age"]) for s in samples}
    m.meta["sex"] = {s: info[s]["sex"] for s in samples}
    return m


def _gold(name: str, key: str = "comparison") -> dict:
    _, rows = read_tsv(GOLD / name)
    return {((r[key].strip() if key in r else ""), r["ID"].strip()): {k: _f(v) for k, v in r.items()
                                                                     if k not in (key, "ID")} for r in rows}


def _priors() -> dict:
    _, rows = read_tsv(GOLD / "design_priors.tsv")
    return {r["model"]: (_f(r["df.prior"]), _f(r["s2.prior"])) for r in rows}


def _rname(a: str, b: str) -> str:
    return f"condition{a} - condition{b}"


def _check(results, m, gold, name=_rname) -> int:
    n = 0
    for c in results:
        for i, f in enumerate(m.features):
            g = gold[(name(c.treatment, c.control), f.id)]
            for ours, key in ((c.diff[i], "logFC"), (c.ci_low[i], "CI.L"), (c.ci_high[i], "CI.R"), (c.t[i], "t"),
                              (c.p[i], "P.Value"), (c.q[i], "adj.P.Val")):
                assert _close(ours, g[key]), (c.treatment, c.control, f.id, key, ours, g[key])
            n += 1
    return n


@pytest.mark.parametrize("matrix, gold, prior", [("design_matrix.tsv", "design_block.tsv", "block"),
                                                 ("design_matrix_missing.tsv", "design_block_missing.tsv",
                                                  "block_missing")])
def test_blocked_design_matches_limma(matrix, gold, prior):
    m = _matrix(matrix)
    d = design.build(m, block="replicate")
    assert d.formula == "~0 + condition + replicate" and d.columns[3:] == ["replicate2", "replicate3", "replicate4"]
    res, info = design.limma_design(m.values, d, PAIRS)
    assert res[0].prior == pytest.approx(_priors()[prior], rel=1e-9) and info == {}
    assert _check(res, m, _gold(gold)) == 3 * len(m.features)
    if "missing" in matrix:  # absent from DMSO: its contrasts can't be estimated, DrugB - DrugA still can
        i = next(i for i, r in enumerate(m.values) if all(v is None for v in r[:4]))
        assert math.isnan(res[0].p[i]) and not math.isnan(res[2].p[i])


def test_covariates_numeric_and_factor_match_limma():
    m = _matrix()
    d = design.build(m, block="replicate", covariates={"age": m.meta["age"], "sex": m.meta["sex"]})
    assert d.formula == "~0 + condition + replicate + age + sex" and d.columns[-2:] == ["age", "sexM"]
    assert [t["kind"] for t in d.terms] == ["block", "numeric", "factor"]
    res, _ = design.limma_design(m.values, d, VS)
    assert res[0].prior == pytest.approx(_priors()["covariates"], rel=1e-9)
    _check(res, m, _gold("design_covariates.tsv"))


def test_one_vs_others_with_a_block_matches_limma():
    m = _matrix()
    res, _ = design.limma_design_others(m.values, design.build(m, block="replicate"))
    pri = _priors()
    for r in res:
        assert r.prior == pytest.approx(pri[f"others_{r.treatment}"], rel=1e-9)
        assert r.n_control[0] == 8
    _check(res, m, _gold("design_block_others.tsv"), lambda a, b: f"{a}_vs_others")


@pytest.mark.parametrize("matrix, gold, block", [
    ("design_matrix.tsv", "block", "replicate"), ("design_matrix_missing.tsv", "block_missing", "replicate"),
    ("design_matrix.tsv", "covariates", "replicate"), ("design_matrix.tsv", "plain", ""),
    ("design_matrix_missing.tsv", "plain_missing", "")])
def test_moderated_f_matches_topTableF(matrix, gold, block):
    m = _matrix(matrix)
    cov = {"age": m.meta["age"], "sex": m.meta["sex"]} if gold == "covariates" else None
    d = design.build(m, block=block, covariates=cov) or design.plain(m)
    ft = design.f_test(m.values, d, "DMSO")
    assert ft.df1 == 2 and ft.reference == "DMSO"
    g = _gold(f"design_F_{gold}.tsv")
    for i, f in enumerate(m.features):
        want = g[("", f.id)]
        for ours, key in ((ft.f[i], "F"), (ft.p[i], "P.Value"), (ft.q[i], "adj.P.Val")):
            assert _close(ours, want[key]), (f.id, key, ours, want[key])
    assert sum(q <= 0.05 for q in ft.q if not math.isnan(q)) > 5  # the planted changes are found


def test_plain_design_equals_the_group_means_model():
    """With no block, the design machinery and FragPipe-Analyst's ~0 + condition agree (the F uses the former)."""
    m = _matrix("design_matrix_missing.tsv")
    ours, _ = design.limma_design(m.values, design.plain(m), VS)
    fpa_res = fpa.limma_contrasts(m.values, m.samples, m.condition, VS)
    for a, b in zip(ours, fpa_res, strict=True):
        for x, y in zip(a.p + a.ci_low, b.p + b.ci_low, strict=True):
            assert _close(x, y, rel=1e-11)


# ------------------------------------------------------------------- DEqMS --


def test_loess_matches_r_on_count_like_data():
    _, rows = read_tsv(GOLD / "design_deqms_block.tsv")
    first = [r for r in rows if r["comparison"] == _rname("DrugA", "DMSO")]
    m = _matrix()
    fit = design.lm_fit(m.values, design.build(m, block="replicate").x)
    counts = [f.peptides for f in m.features]
    y = deqms.loess([math.log2(c) for c in counts], [math.log(v) for v in fit.s2])
    for yi, r in zip(y, first, strict=True):
        assert _close(yi, _f(r["loess"]), rel=1e-10)


@pytest.mark.parametrize("matrix, gold", [("design_matrix.tsv", "block"), ("design_matrix_missing.tsv",
                                                                           "block_missing")])
def test_deqms_matches_spectraCounteBayes(matrix, gold):
    m = _matrix(matrix)
    counts = [f.peptides for f in m.features]
    res, info = design.limma_design(m.values, design.build(m, block="replicate"), VS, counts=counts,
                                    variance_prior="deqms")
    assert info["deqms_used"] and info["d0"] == _priors()[f"deqms_{gold}"][0]
    g = _gold(f"design_deqms_{gold}.tsv")
    for c in res:
        for i, f in enumerate(m.features):
            want = g[(_rname(c.treatment, c.control), f.id)]
            assert _close(c.diff[i], want["logFC"]), f.id
            assert _close(c.t[i], want["sca.t"]), (f.id, c.t[i], want["sca.t"])
            assert _close(c.p[i], want["sca.P.Value"]), f.id
            assert _close(c.q[i], want["sca.adj.pval"]), f.id


def test_deqms_on_the_plain_model_and_fallbacks():
    m = _matrix()
    counts = [f.peptides for f in m.features]
    res = fpa.limma_contrasts(m.values, m.samples, m.condition, VS[:1],
                              squeeze=design.squeezer(counts, "deqms"))
    g = _gold("design_deqms_plain.tsv", key="-")
    for i, f in enumerate(m.features):
        assert _close(res[0].t[i], g[("", f.id)]["sca.t"]), f.id
        assert _close(res[0].p[i], g[("", f.id)]["sca.P.Value"]), f.id
    # a feature without a count keeps limma's prior; too few counts -> limma's prior for all
    counts[0] = None
    mod = design.squeeze(design.lm_fit(m.values, design.plain(m).x).s2, [8] * len(counts), counts, "deqms")
    plain = design.squeeze(design.lm_fit(m.values, design.plain(m).x).s2, [8] * len(counts))
    assert mod.post[0] == plain.post[0] and mod.post[1] != plain.post[1]
    few = design.squeeze([0.1] * 30, [4] * 30, [1, 2] * 15, "deqms")
    assert few.info["deqms_used"] is False


# --------------------------------------------------------- designs that fail --


def test_confounded_and_incomplete_designs_are_explained_not_crashed():
    m = _matrix()
    with pytest.raises(design.DesignError, match="single condition"):
        design.build(m, block={s: m.condition[s] for s in m.samples})
    with pytest.raises(design.DesignError, match="no block"):
        design.build(m, block={"DMSO_1": "a"})
    with pytest.raises(design.DesignError, match="doesn't match"):
        design.build(m, block_from=r"^Drug")
    with pytest.raises(design.DesignError, match="residual degrees of freedom"):
        rng = __import__("random").Random(1)
        design.build(m, block="replicate", covariates={f"c{k}": {s: rng.gauss(0, 1) for s in m.samples}
                                                       for k in range(6)})  # 12 samples, 12 parameters
    m2 = _matrix()
    m2.replicate.pop("DMSO_1")
    with pytest.raises(design.DesignError, match="replicate number"):
        design.build(m2, block="replicate")
    with pytest.raises(design.DesignError, match="same value"):
        design.build(m, covariates={"age": {s: 3 for s in m.samples}})
    d = design.build(m, block_from=r"_(?P<block>\d)$")
    assert d.terms[0]["levels"] == ["1", "2", "3", "4"]
    assert design.build(m) is None


# ------------------------------------------------------------------ settings --


def test_design_settings_validate_with_friendly_errors():
    s = analysis.settings_from({"block": "replicate", "variance_prior": "DEqMS"})
    assert s.block == "replicate" and s.variance_prior == "deqms" and s.has_design
    s = analysis.settings_from({"block": {"DMSO_1": "A", "Drug_1": 1}})
    assert s.block == {"DMSO_1": "A", "Drug_1": "1"}
    assert analysis.settings_from({"covariates": {"DMSO_1": 54, "Drug_1": 61}}).covariates == {
        "covariate": {"DMSO_1": 54, "Drug_1": 61}}
    assert analysis.settings_from({"covariates": {"age": {"DMSO_1": 54}, "sex": {"DMSO_1": "F"}}}).covariates["sex"]
    # a later layer's block_from replaces the lab's block (and vice versa)
    s = analysis.settings_from({"block": "replicate"}, {"block_from": r"_(P\d+)_"})
    assert s.block == "" and s.block_from == r"_(P\d+)_"
    for bad, msg in (({"block": "batch"}, "use 'replicate'"), ({"block_from": "_P\\d+"}, "needs a group"),
                     ({"block_from": "(["}, "not a valid regular expression"),
                     ({"block": "replicate", "block_from": "(x)"}, "not both"),
                     ({"variance_prior": "robust"}, "limma .* or deqms"),
                     ({"covariates": {"age": {"a": 1}, "b": 2}}, "either one"),
                     ({"covariates": {"condition": {"a": 1}}}, "reserved")):
        with pytest.raises(analysis.AnalysisError, match=msg):
            analysis.settings_from(bad)
    s, notes = analysis.settings_lenient({"block": "batch", "log2fc": 2})
    assert s.block == "" and s.log2fc == 2 and "block" in notes[0]
    d = analysis.as_dict(analysis.settings_from({}))
    assert d["block"] == "" and d["covariates"] == {} and d["variance_prior"] == "limma"


# ---------------------------------------------------------------- end to end --


def _run(tmp_path, name, cfg, **kw):
    from ionomos import downstream
    from tests.test_insights import _dia

    dest, rec, truth = _dia(tmp_path / name, **kw)
    out = downstream.analyze(dest, "DIA", {"enrichment": False, "normalize": "none", **cfg}, record=rec)
    return dest, out, truth


def test_block_on_replicate_end_to_end(tmp_path):
    import json

    from tests.test_insights import _payload

    kw = {"batch": 1.6, "conds": ("DMSO", "Drug", "Drug2")}
    _, plain, truth = _run(tmp_path, "plain", {}, **kw)
    dest, out, _ = _run(tmp_path, "block", {"block": "replicate"}, **kw)
    codes = {i.code: i for i in out.issues}
    assert "DESIGN_NOT_USED" not in codes
    assert "already block" in codes["BATCH_SUSPECT"].fixes[0] and codes["BATCH_SUSPECT"].data["blocked"]
    assert "block: replicate" in {i.code: i for i in plain.issues}["BATCH_SUSPECT"].fixes[0]
    summary = json.loads((dest / "results/analysis.json").read_text(encoding="utf-8"))
    assert summary["model"]["formula"] == "~0 + condition + replicate" and summary["model"]["residual_df"] == 6
    assert summary["settings"]["block"] == "replicate"
    ft = summary["f_test"]
    assert ft["reference"] == "DMSO" and ft["df1"] == 2 and ft["any_change"] > 0
    header = (dest / "results/protein_results.tsv").read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert header[header.index("F"):header.index("F") + 3] == ["F", "F_p", "F_p_adj"]

    def found(o, comp):
        rows = next(d for d in o.summary["comparisons"] if d["name"] == comp)
        return rows["up"] + rows["down"]
    # the batch sits in the residuals of the plain model; blocking takes it out
    assert found(out, "Drug vs DMSO") > found(plain, "Drug vs DMSO")
    html = out.report.read_text(encoding="utf-8")
    assert "~0 + condition + replicate" in html and "moderated F-statistic" in html
    d = _payload(html)
    assert d["F"]["ref"] == "DMSO" and len(d["F"]["q"]) == len(d["f"]["id"])
    fa = dest / "results/fragpipe-analyst"
    assert "repeats the PLAIN model" in (fa / "reproduce_in_R.R").read_text(encoding="utf-8")
    script = (fa / "reproduce_design_in_R.R").read_text(encoding="utf-8")
    assert "model.matrix(~0 + condition + replicate)" in script


def test_a_confounded_block_falls_back_to_the_plain_model(tmp_path):
    conds = ("DMSO", "Drug")
    cfg = {"block": {f"{c}_{r}": c for c in conds for r in range(1, 5)}}  # the "batch" is the condition
    dest, out, _ = _run(tmp_path, "conf", cfg)
    pdest, plain, _ = _run(tmp_path, "plain", {})
    issue = next(i for i in out.issues if i.code == "DESIGN_NOT_USED")
    assert issue.severity == "input" and "single condition" in issue.message
    assert out.summary["state"] == "needs_input"
    same = "Drug_vs_DMSO_differential.tsv"
    assert (dest / "results" / same).read_bytes() == (pdest / "results" / same).read_bytes()
    assert "could not be used" in out.report.read_text(encoding="utf-8")


def test_deqms_end_to_end_and_without_counts(tmp_path):
    dest, out, _ = _run(tmp_path, "dq", {"variance_prior": "deqms"}, conds=("DMSO", "Drug", "Drug2"))
    model = out.summary["model"]
    assert model["variance_prior"] == "deqms" and model["prior"]["deqms_used"] and model["prior"]["d0"] > 0
    assert "DEqMS" in out.report.read_text(encoding="utf-8")
    assert not [i for i in out.issues if i.code == "DEQMS_NOT_USED"]
    # a bare table has no peptide counts: limma's prior, with a warning
    from ionomos import downstream

    table = tmp_path / "t.tsv"
    lines = ["Gene\t" + "\t".join(f"{c}_{r}" for c in ("A", "B") for r in (1, 2, 3))]
    for g in range(60):
        lines.append(f"G{g}\t" + "\t".join(f"{20 + (g % 7) * 0.3 + (0.9 if c == 'B' and g < 6 else 0) + r * 0.05:.3f}"
                                           for c in ("A", "B") for r in (1, 2, 3)))
    table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = downstream.analyze(tmp_path / "tbl", table=table, analysis_cfg={"enrichment": False,
                                                                           "variance_prior": "deqms"})
    issue = next(i for i in out.issues if i.code == "DEQMS_NOT_USED")
    assert issue.severity == "warning" and "no peptide" in issue.message


def test_exported_design_script_reproduces_the_model_in_r(tmp_path):
    """Dev-only: runs reproduce_design_in_R.R when R with limma is found (IONOMOS_R_LIBS names a library)."""
    import os
    import shutil
    import subprocess

    lib = os.environ.get("IONOMOS_R_LIBS", "")
    if not shutil.which("Rscript") or subprocess.run(
            ["Rscript", "-e", f".libPaths(c('{lib}', .libPaths())); library(limma)"] if lib else
            ["Rscript", "-e", "library(limma)"], capture_output=True, timeout=120).returncode:
        pytest.skip("R with limma not available (set IONOMOS_R_LIBS to its library)")
    conds = ("DMSO", "Drug", "Drug2")
    cov = {"age": {f"{c}_{r}": 30 + (7 * k + 3 * r) % 23 for k, c in enumerate(conds) for r in range(1, 5)},
           "sex": {f"{c}_{r}": "FM"[(k + r) % 2] for k, c in enumerate(conds) for r in range(1, 5)}}
    dest, out, _ = _run(tmp_path, "r", {"block": "replicate", "covariates": cov, "de_type": "all"}, batch=1.0,
                        conds=conds)
    assert out.summary["model"]["formula"] == "~0 + condition + replicate + age + sex"
    fa = dest / "results/fragpipe-analyst"
    script = fa / "reproduce_design_in_R.R"
    if lib:
        script.write_text(f".libPaths(c('{lib}', .libPaths()))\n" + script.read_text(encoding="utf-8"),
                          encoding="utf-8")
    subprocess.run(["Rscript", script.name], cwd=fa, check=True, capture_output=True, timeout=600)
    _, rows = read_tsv(fa / "limma_design_results.tsv")
    theirs = {(r["comparison"], r["id"]): r for r in rows}
    n = 0
    for d in out.summary["comparisons"]:
        _, ours = read_tsv(dest / d["table"])
        key = d["name"].replace(" vs ", "_vs_")
        for o in ours:
            t = theirs[(key, o["id"])]
            for a, b in (("log2fc", "logFC"), ("pvalue", "P.Value"), ("qvalue", "adj.P.Val"), ("ci_low", "CI.L")):
                if o[a] != "":
                    assert _close(float(o[a]), float(t[b]), rel=1e-6), (key, o["id"], a)
                    n += 1
    assert n > 1000
