"""Deeper QC and discovery (downstream/insights.py), the rank-based gene-set test (enrich.rank_test), the
doctor's new warnings, and how they reach the results folder, analysis.json and the report."""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import enrich, insights, quant, simulate
from ionomos.downstream.analysis import DiffResult, Settings


def _cond(samples):
    return {s: s.rsplit("_", 1)[0] for s in samples}


def _matrix(rng, n=400, groups=(("A", 4), ("B", 4)), noise=0.3):
    samples = [f"{g}_{k}" for g, n_ in groups for k in range(1, n_ + 1)]
    base = [rng.gauss(22, 2) for _ in range(n)]
    values = [[b + rng.gauss(0, noise) for _ in samples] for b in base]
    return samples, values


# ------------------------------------------------------------------ scorecard --


def test_scorecard_flags_the_noisy_replicate_and_only_it():
    rng = random.Random(1)
    samples, values = _matrix(rng)
    bad = samples.index("A_3")
    for r in values:
        r[bad] += rng.gauss(0, 1.2)
    card = insights.sample_scorecard(values, samples, _cond(samples))
    status = {r["sample"]: r["status"] for r in card}
    assert status["A_3"] == "fail", card[bad]["flags"]
    assert {s for s, v in status.items() if v != "ok"} == {"A_3"}
    flags = " ".join(card[bad]["flags"])
    assert "correlates poorly" in flags and "scatter" in flags and "CV drops" in flags


def test_scorecard_reports_few_identifications_and_loading():
    rng = random.Random(2)
    samples, values = _matrix(rng)
    few, heavy = samples.index("B_2"), samples.index("B_4")
    for i, r in enumerate(values):
        if i % 10:
            r[few] = None
    before = [[v + (1.5 if j == heavy else 0) if v is not None else None for j, v in enumerate(r)] for r in values]
    card = {r["sample"]: r for r in insights.sample_scorecard(values, samples, _cond(samples), before_norm=before)}
    assert card["B_2"]["status"] == "fail" and card["B_2"]["flags"][0].startswith("far fewer identifications")
    assert any("loaded 2.8× more" in f for f in card["B_4"]["flags"])
    assert card["B_4"]["status"] == "ok"  # loading alone is corrected by normalisation: informational


def test_scorecard_tolerates_tiny_and_odd_input():
    assert insights.sample_scorecard([], [], {}) == []
    assert insights.sample_scorecard([[1.0, 2.0]], ["a_1", "b_1"], {"a_1": "a", "b_1": "b"}) == []
    card = insights.sample_scorecard([[1.0, None, 3.0], [None, None, None]], ["a_1", "a_2", "b_1"],
                                     {"a_1": "a", "a_2": "a", "b_1": "b"})
    assert len(card) == 3 and card[2]["corr_group"] is None


# ------------------------------------------------------------ PC association --


def test_pc_association_spots_a_replicate_batch():
    samples = [f"{c}_{r}" for c in ("A", "B", "C") for r in (1, 2, 3)]
    rep = {s: int(s[-1]) for s in samples}
    # PC1 follows replicate number, PC2 condition
    pca = {"scores": [[{1: -3, 2: 0, 3: 3}[rep[s]] + 0.1 * k, {"A": -1, "B": 0, "C": 1}[s[0]]] for k, s in enumerate(samples)],
           "percent": [60.0, 25.0]}
    out = insights.pc_association(pca, samples, _cond(samples), rep)
    assert out["pcs"][0]["r2_replicate"] > 0.95 and out["pcs"][1]["r2_condition"] > 0.95
    assert out["batch"]["pc"] == 1
    by_condition = [[{"A": -2, "B": 0, "C": 2}[s[0]] + 0.1 * (rep[s] - 2), 0.3 * (k % 2)] for k, s in enumerate(samples)]
    clean = insights.pc_association({"scores": by_condition, "percent": [60.0, 25.0]}, samples, _cond(samples), rep)
    assert clean["batch"] is None


def test_pc_association_needs_replicates_to_mean_anything():
    samples = ["A_1", "B_1", "C_1", "D_1"]
    out = insights.pc_association({"scores": [[1, 0], [2, 0], [3, 0], [4, 1]], "percent": [70, 20]},
                                  samples, _cond(samples), {s: 1 for s in samples})
    assert all(p["r2_replicate"] is None for p in out["pcs"]) and out["batch"] is None


# ---------------------------------------------------------------- missingness --


def test_missingness_tells_low_abundance_dropouts_from_random_gaps():
    rng = random.Random(3)
    samples, values = _matrix(rng, n=1500)
    mnar = [[None if v < 20.5 and rng.random() < 0.6 else v for v in r] for r in values]
    mar = [[None if rng.random() < 0.15 else v for v in r] for r in values]
    a, b = insights.missingness(mnar), insights.missingness(mar)
    assert a["verdict"] == "intensity" and a["rho"] > 0.3 and a["gap"] > 1
    assert b["verdict"] == "random", b
    assert a["bins"][0]["detected"] < a["bins"][-1]["detected"]
    assert insights.missingness(values)["verdict"] == "few"
    assert insights.missingness([[1.0, 2.0]])["verdict"] == ""


# ------------------------------------------------------------------ p-values --


def test_pi0_and_histogram_shapes():
    rng = random.Random(4)
    null = [rng.random() for _ in range(2000)]
    assert insights.pi0(null) == pytest.approx(1.0, abs=0.08)
    mixed = null[:1400] + [rng.random() * 1e-3 for _ in range(600)]
    assert insights.pi0(mixed) == pytest.approx(0.7, abs=0.08)
    assert insights.pi0(null[:20]) is None
    assert insights.p_histogram(null)["shape"] == "flat"
    assert insights.p_histogram(mixed)["shape"] == "signal"
    piled = null[:1500] + [0.999] * 500
    assert insights.p_histogram(piled)["shape"] == "conservative"
    hump = null[:1000] + [0.3 + 0.2 * rng.random() for _ in range(1000)]
    assert insights.p_histogram(hump)["shape"] == "hump"
    assert insights.p_histogram(null[:50])["shape"] == ""


# -------------------------------------------------------------- on / off, imp --


def test_presence_absence_finds_features_measured_in_one_group_only():
    samples = ["A_1", "A_2", "A_3", "A_4", "B_1", "B_2", "B_3", "B_4"]
    m = [[20.0] * 4 + [None] * 4,          # on in A
         [None] * 4 + [21.0] * 3 + [None],  # on in B (3 of 4 = 75%)
         [20.0, None, None, None] + [None] * 4,  # one value: not enough
         [20.0] * 8]
    out = insights.presence_absence(m, samples, _cond(samples), "A", "B")
    assert [(x["index"], x["only_in"], x["group"], x["detected"]) for x in out] == [(0, "treatment", "A", 4),
                                                                                    (1, "control", "B", 3)]
    assert insights.presence_absence(m, samples, _cond(samples), "A", None) == []
    others = insights.presence_absence(m, samples, _cond(samples), "A", "others")
    assert {x["index"] for x in others} == {0, 1}


def _three_by_three():
    """A, B, C x 3 (the 2026-10-06 pull-down's shape) with 20 complete rows, then:
    on_A  every A, nothing else          (3 of 9: a global 66 % filter removes it)
    two_B two of three B                 (removed; not on/off, not complete)
    A_B1  every A and one B              (4 of 9: removed; complete in A, but B has it once)
    AB    every A and B, no C            (6 of 9 = 67 %: kept; on in A against C)"""
    samples = [f"{c}_{r}" for c in "ABC" for r in (1, 2, 3)]
    rng = random.Random(3)
    rows = [[rng.gauss(22, 1) for _ in samples] for _ in range(20)]
    rows += [[24.0, 24.2, 23.8] + [None] * 6,
             [None] * 3 + [21.0, 21.0, None] + [None] * 3,
             [20.0, 20.4, 20.2, 19.0] + [None] * 5,
             [23.0] * 6 + [None] * 3]
    names = [f"G{i}" for i in range(20)] + ["on_A", "two_B", "A_B1", "AB"]
    feats = [quant.Feature(id=f"P{i}", label=n, description=f"{n} protein") for i, n in enumerate(names)]
    return quant.QuantMatrix("intensity", "protein", feats, samples, rows, _cond(samples))


def test_on_off_is_found_before_the_missing_value_filter():
    """docs/REAL_RUNS.md 2026-10-06: a protein in every sample of one condition and none of the others was removed
    by a 66 % global filter and was then missing from 'only in one condition' too (D84)."""
    from ionomos.downstream import fpa

    m = _three_by_three()
    p, _notes = fpa.process(m, global_pct=66, condition_pct=50, normalization="median", imputation="none")
    assert [f.label for f in p.m.features[-1:]] == ["AB"] and len(p.m.features) == 21
    out = insights.presence_absence_unfiltered(p, "A", "C")
    got = {(x["feature"].label if x["filtered"] else p.m.features[x["index"]].label): x for x in out}
    assert set(got) == {"on_A", "A_B1", "AB"}
    assert got["on_A"]["filtered"] and got["on_A"]["index"] is None and got["on_A"]["detected"] == 3
    shift = [b - a for a, b in zip(p.normalized_from[0], p.measured[0], strict=True)]  # median centring
    assert got["on_A"]["mean"] == pytest.approx(sum(v + s for v, s in zip((24.0, 24.2, 23.8), shift[:3], strict=True)) / 3)
    assert not got["AB"]["filtered"] and got["AB"]["index"] == 20
    kept = p.measured[20]
    assert got["AB"]["mean"] == pytest.approx(sum(kept[:3]) / 3)  # the analysed (median-centred) values
    assert [x["feature"].label if x["filtered"] else "AB" for x in out][0] == "on_A"  # most abundant first
    assert insights.filter_loss(p, 66) == {"removed": 3, "complete": 2, "only_one": 1, "by_condition": {"A": 2}}

    q, _notes = fpa.process(m, global_pct=0, condition_pct=50, normalization="median", imputation="none")
    assert insights.filter_loss(q, 0) == {}
    plain = insights.presence_absence(q.measured, q.m.samples, q.m.condition, "A", "C")
    assert insights.presence_absence_unfiltered(q, "A", "C") == plain  # nothing filtered: exactly as before
    assert {q.m.features[x["index"]].label for x in plain} == {"on_A", "A_B1", "AB"}


def test_imputation_driven_hits():
    samples = ["A_1", "A_2", "B_1", "B_2"]
    rows = [{"index": 0, "significant": "up"}, {"index": 1, "significant": "up"}, {"index": 2, "significant": ""}]
    d = DiffResult("A vs B", "A", "B", "intensity", rows, Settings(), None)
    imputed = [[False, False, True, True], [False, False, True, False], [True, True, True, True]]
    assert insights.imputation_driven(imputed, samples, _cond(samples), d) == [0, 1]


# --------------------------------------------------------------------- power --


def test_power_falls_with_replicates_and_uses_the_prior():
    rng = random.Random(5)
    samples, values = _matrix(rng, noise=0.4)
    p = insights.power(values, samples, _cond(samples))
    q50 = [r["q50"] for r in p["curves"]["0.05"]]
    assert all(a > b for a, b in zip(q50, q50[1:], strict=False)) and p["current_n"] == 4
    assert p["sd"]["q50"] == pytest.approx(0.4, abs=0.06)
    strict = [r["q50"] for r in p["curves"]["0.001"]]
    assert all(s > n for s, n in zip(strict, q50, strict=True))
    # n=4: (qt(.975, 6) + qt(.8, 6)) * sd * sqrt(2/4)
    assert q50[2] == pytest.approx((2.4469 + 0.9057) * p["sd"]["q50"] * math.sqrt(0.5), rel=1e-3)
    moderated = insights.power(values, samples, _cond(samples), prior=(4.0, 0.16))
    assert moderated["moderated"] and moderated["curves"]["0.05"][0]["q50"] < q50[0]  # more df at n=2
    ratio = insights.power(values, samples, _cond(samples), kind="ratio")
    assert ratio["curves"]["0.05"][2]["q50"] < q50[2] * 1.5
    assert insights.power(values[:5], samples, _cond(samples)) == {}


# ----------------------------------------------------------- rank-based sets --


def test_rank_test_finds_a_shifted_set_and_the_correlation_adjustment_is_conservative():
    rng = random.Random(6)
    genes = [f"G{i}" for i in range(1000)]
    scores = {g: rng.gauss(0, 1) for g in genes}
    for g in genes[:30]:
        scores[g] += 1.2   # a modest shift, none of them a "hit" on its own
    lib = {"SHIFTED": set(genes[:30]) | {"NOT_MEASURED"}, "NULL": set(genes[500:540]), "TINY": set(genes[900:903])}
    rows = {r["term"]: r for r in enrich.rank_test(scores, lib)}
    assert "TINY" not in rows
    assert rows["SHIFTED"]["direction"] == "up" and rows["SHIFTED"]["q"] < 1e-4 and rows["SHIFTED"]["n"] == 30
    assert rows["NULL"]["q"] > 0.05
    assert rows["SHIFTED"]["leading"][0] == max(genes[:30], key=scores.get)
    # co-regulated members: the same residual pattern -> strong inter-gene correlation -> a weaker z
    resid = {g: [rng.gauss(0, 1) for _ in range(8)] for g in genes}
    shared = [rng.gauss(0, 1) for _ in range(8)]
    for g in genes[:30]:
        resid[g] = [s + 0.3 * rng.gauss(0, 1) for s in shared]
    adj = {r["term"]: r for r in enrich.rank_test(scores, lib, resid)}
    assert adj["SHIFTED"]["corr"] > 0.5 and abs(adj["SHIFTED"]["z"]) < abs(rows["SHIFTED"]["z"]) / 2
    assert enrich.rank_test({"A": 1.0}, lib) == []


def test_inter_gene_correlation():
    assert enrich.inter_gene_correlation([[1, 2, 3], [2, 4, 6], [0, 1, 2]]) == pytest.approx(1.0)
    assert enrich.inter_gene_correlation([[1, 2, 3], [3, 2, 1]]) == pytest.approx(-1.0)
    assert enrich.inter_gene_correlation([[1, 1, 1], None, [1, 2, 3]]) == 0.0


# ------------------------------------------------------------------ loaders --


def test_loaders_keep_peptide_and_psm_evidence(tmp_path):
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2)]
    pg = tmp_path / "report.pg_matrix.tsv"
    simulate.dia_pg_matrix(pg, runs, n_proteins=20)
    m = quant.from_pg_matrix(pg)
    assert all(isinstance(f.peptides, int) and f.peptides >= 1 for f in m.features) and m.meta["evidence"] == "peptides"
    ab = tmp_path / "abundance_gene_MD.tsv"
    simulate.tmt_abundance(ab, ["DMSO_1_126", "Drug_1_127N"], n_genes=10)
    t = quant.from_tmt_abundance(ab)
    assert all(f.peptides is not None for f in t.features) and t.meta["evidence"] == "PSMs"
    cp = tmp_path / "combined_protein.tsv"
    cp.write_text("Protein ID\tGene\tCombined Total Peptides\tA_1 MaxLFQ Intensity\tB_1 MaxLFQ Intensity\n"
                  "P1\tG1\t3\t100\t200\nP2\tG2\t\t100\t200\n", encoding="utf-8")
    lfq = quant.from_combined_protein(cp)
    assert [f.peptides for f in lfq.features] == [3, None]


# ------------------------------------------------------------- end to end --


def _dia(tmp_path, *, outlier=None, onoff=0, batch=0.0, conds=("DMSO", "Drug"), reps=4, seed=6, off=None):
    """onoff: the first rows are blanked in the conditions `off` (default the first); with `off` given they are
    measured in every sample of the other conditions."""
    dest = tmp_path / "e"
    runs = [(f"C:\\x\\raw\\{c}_{r}.raw", c) for c in conds for r in range(1, reps + 1)]
    pg = dest / "fragpipe/diann-output/report.pg_matrix.tsv"
    truth = simulate.dia_pg_matrix(pg, runs, seed=seed, n_proteins=500)
    lines = pg.read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    col = {h: k for k, h in enumerate(head)}
    rng = random.Random(9)
    out = [lines[0]]
    for n, line in enumerate(lines[1:]):
        v = line.split("\t")
        if n < onoff and off is None:
            for c, r in ((conds[0], r) for r in range(1, reps + 1)):
                v[col[f"C:\\x\\raw\\{c}_{r}.raw"]] = ""
        elif n < onoff:
            for c in conds:
                for r in range(1, reps + 1):
                    k = col[f"C:\\x\\raw\\{c}_{r}.raw"]
                    v[k] = "" if c in off else (v[k] or "1000000.0")
        for h, k in col.items():
            if not h.endswith(".raw") or not v[k]:
                continue
            x = float(v[k])
            if outlier and h.endswith(f"\\{outlier}.raw"):
                x *= 2 ** rng.gauss(0, 1.2)
            if batch:
                x *= 2 ** (batch * {1: -1, 2: 1, 3: -0.5, 4: 0.5}[int(h[-5])] * rng.gauss(1, 0.3))
            v[k] = f"{x:.1f}"
        out.append("\t".join(v))
    pg.write_text("\n".join(out) + "\n", encoding="utf-8")
    rec = {"plan": {"manifest": [{"file": f"raw/{c}_{r}.raw", "experiment": c, "bioreplicate": r}
                                 for c in conds for r in range(1, reps + 1)]}}
    return dest, rec, truth


def _gmt(tmp_path, truth, cond="Drug") -> Path:
    up = sorted(g for g, s in truth[cond].items() if s > 0)
    rng = random.Random(1)
    bg = [f"GENE{i}" for i in range(30, 500)]
    lines = ["PLANTED_UP\tx\t" + "\t".join(up[:25] + bg[:10])]
    lines += [f"RANDOM_{k}\tx\t" + "\t".join(rng.sample(bg, 30)) for k in range(30)]
    p = tmp_path / "sets.gmt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _payload(html: str) -> dict:
    tag = "<script id='ionomos-data' type='application/json'>"
    start = html.index(tag) + len(tag)
    return json.loads(html[start:html.index("</script>", start)])


NEW_WARNINGS = {"SAMPLE_OUTLIER", "BATCH_SUSPECT", "IMPUTATION_MISMATCH", "P_VALUE_SHAPE", "IMPUTATION_DRIVEN"}


def test_clean_experiment_raises_none_of_the_new_warnings(tmp_path):
    dest, rec, _ = _dia(tmp_path)
    out = downstream.analyze(dest, "DIA", {"enrichment": False}, record=rec)
    assert not NEW_WARNINGS & {i.code for i in out.issues}, [i.code for i in out.issues]
    q = json.loads((dest / "results/analysis.json").read_text(encoding="utf-8"))["quality"]
    assert q["samples_flagged"] == {} and q["batch"] is None and q["missingness"] in ("intensity", "few")
    assert q["p_value_shape"]["Drug vs DMSO"] == "signal" and 0 < q["pi0"]["Drug vs DMSO"] < 1
    assert (dest / "results/sample_qc.tsv").is_file() and not (dest / "results/presence_absence.tsv").exists()


def test_outlier_on_off_and_gene_sets_reach_every_output(tmp_path):
    dest, rec, truth = _dia(tmp_path, outlier="DMSO_3", onoff=8)
    gmt = _gmt(tmp_path, truth)
    out = downstream.analyze(dest, "DIA", {"enrichment_libraries": [], "enrichment_gmt": str(gmt)}, record=rec)
    codes = {i.code: i for i in out.issues}
    assert codes["SAMPLE_OUTLIER"].data["samples"] == ["DMSO_3"] and codes["SAMPLE_OUTLIER"].severity == "warning"
    results = dest / "results"
    card = (results / "sample_qc.tsv").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("DMSO_3\tDMSO\tfail") for line in card)
    onoff = (results / "presence_absence.tsv").read_text(encoding="utf-8").splitlines()
    assert len(onoff) - 1 >= 6 and all(line.split("\t")[1] == "Drug" for line in onoff[1:])
    ranks = (results / "gene_set_ranks.tsv").read_text(encoding="utf-8").splitlines()
    top = ranks[1].split("\t")
    assert top[2] == "PLANTED_UP" and top[3] == "up" and float(top[7]) < 0.01
    summary = json.loads((results / "analysis.json").read_text(encoding="utf-8"))
    assert summary["gene_set_ranks"][0]["top"][0] == "PLANTED_UP (up)"
    assert summary["quality"]["only_in_one_condition"]["Drug vs DMSO"] >= 6
    d = _payload(out.report.read_text(encoding="utf-8"))
    comp = d["comps"][0]
    assert len(comp["onoff"]) >= 6 and {x[1] for x in comp["onoff"]} == {"t"} and comp["pi0"] is not None
    assert d["gsea"][0]["terms"][0]["term"] == "PLANTED_UP" and d["gsea"][0]["adjusted"] is True
    assert [r["sample"] for r in d["qc"]["scorecard"] if r["status"] == "fail"] == ["DMSO_3"]
    assert d["qc"]["power"]["current"] == 4 and d["qc"]["mnar"]["verdict"]
    assert d["evidence"] == "peptides" and len(d["f"]["pep"]) == len(d["f"]["id"]) and d["rep"][0] == 1
    html = out.report.read_text(encoding="utf-8")
    for section in ("id='onoff'", "id='findings'", "id='search'", "id='optpanel'"):
        assert section in html
    assert "rank-sum" in html.lower() or "Wilcoxon" in html  # the methods paragraph describes the new test


PULLDOWN = ("DMSO", "FPS", "EV")


def test_a_global_filter_no_longer_hides_proteins_seen_in_one_condition(tmp_path):
    """The 2026-10-06 pull-down (docs/REAL_RUNS.md): three conditions of three and filter_global_pct 66. Proteins
    in every FPS sample and no other are removed before testing; they stay in presence_absence.tsv, analysis.json
    and the report, marked, and the doctor says what the filter did (D84)."""
    dest, rec, _ = _dia(tmp_path, conds=PULLDOWN, reps=3, onoff=3, off=("DMSO", "EV"))
    planted = ["ACTB", "GAPDH", "PKM"]
    out = downstream.analyze(dest, "DIA", {"enrichment": False, "filter_global_pct": 66}, record=rec)
    issue = next(i for i in out.issues if i.code == "FILTER_REMOVES_ONE_CONDITION")
    assert issue.severity == "warning" and issue.data["by_condition"]["FPS"] >= 3 and issue.data["only_one"] >= 3
    assert "at least 66% of all samples" in issue.message and "FPS" in issue.message
    assert any("0% of all samples and 50% of one condition" in x for x in issue.fixes)

    results = dest / "results"
    lines = (results / "presence_absence.tsv").read_text(encoding="utf-8").splitlines()
    rows = [dict(zip(lines[0].split("\t"), line.split("\t"), strict=True)) for line in lines[1:]]
    mine = {r["label"]: r for r in rows if r["comparison"] == "FPS vs DMSO" and r["label"] in planted}
    assert set(mine) == set(planted)
    assert all(r["only_in"] == "FPS" and r["detected"] == "3" and r["removed_by_filter"] == "TRUE"
               for r in mine.values())
    tested = (results / "protein_results.tsv").read_text(encoding="utf-8")
    assert not any(f"\t{g}\t" in tested for g in planted)  # really not tested

    q = json.loads((results / "analysis.json").read_text(encoding="utf-8"))["quality"]
    assert q["only_in_one_condition_filtered_out"]["FPS vs DMSO"] >= 3
    assert q["only_in_one_condition"]["FPS vs DMSO"] >= q["only_in_one_condition_filtered_out"]["FPS vs DMSO"]
    assert q["filter_removed_complete_in_one_condition"]["by_condition"]["FPS"] >= 3

    d = _payload(out.report.read_text(encoding="utf-8"))
    comp = next(c for c in d["comps"] if c["name"] == "FPS vs DMSO")
    gone = {x[0]: x for x in comp["onoffOut"]}
    assert set(planted) <= set(gone) and all(gone[g][2] == "t" and gone[g][3:5] == [3, 3] for g in planted)
    assert not set(planted) & {d["f"]["label"][x[0]] for x in comp["onoff"]}
    assert d["settings"]["filter"] == [66, 50]
    assert "issue.FILTER_REMOVES_ONE_CONDITION" in {x["id"] for x in d["help"]["issues"]}

    # Ionomos' default filter (0 % of all samples, 50 % of one condition) keeps and tests them, and stays quiet
    out = downstream.analyze(dest, "DIA", {"enrichment": False}, record=rec)
    assert "FILTER_REMOVES_ONE_CONDITION" not in {i.code for i in out.issues}
    d = _payload(out.report.read_text(encoding="utf-8"))
    comp = next(c for c in d["comps"] if c["name"] == "FPS vs DMSO")
    assert "onoffOut" not in comp and set(planted) <= {d["f"]["label"][x[0]] for x in comp["onoff"]}
    q = json.loads((results / "analysis.json").read_text(encoding="utf-8"))["quality"]
    assert q["only_in_one_condition_filtered_out"]["FPS vs DMSO"] == 0
    assert q["filter_removed_complete_in_one_condition"] is None


def test_replicate_batch_is_reported(tmp_path):
    dest, rec, _ = _dia(tmp_path, batch=1.6, conds=("DMSO", "Drug", "Drug2"))
    out = downstream.analyze(dest, "DIA", {"enrichment": False, "normalize": "none"}, record=rec)
    batch = next((i for i in out.issues if i.code == "BATCH_SUSPECT"), None)
    assert batch is not None, [i.code for i in out.issues]
    assert batch.data["r2_replicate"] >= 0.5


def test_insight_stage_crash_leaves_the_rest(tmp_path, monkeypatch):
    dest, rec, _ = _dia(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("scorecard exploded")

    monkeypatch.setattr(insights, "sample_scorecard", boom)
    out = downstream.analyze(dest, "DIA", {"enrichment": False}, record=rec)
    assert any(i.code == "CRASH_INSIGHTS" and i.severity == "warning" for i in out.issues)
    assert out.report.is_file() and list((dest / "results").glob("volcano_*.svg"))
    assert "id='volcano'" in out.report.read_text(encoding="utf-8")


def test_report_data_neutralises_html_comment_openers(tmp_path):
    dest, rec, _ = _dia(tmp_path)
    pg = next(dest.rglob("report.pg_matrix.tsv"))
    text = pg.read_text(encoding="utf-8").replace("\tGENE40\t", "\t<!--<script>x</script>\t", 1)
    pg.write_text(text, encoding="utf-8")
    out = downstream.analyze(dest, "DIA", {"enrichment": False}, record=rec)
    html = out.report.read_text(encoding="utf-8")
    data_tag = html[html.index("<script id='ionomos-data'"):]
    data_tag = data_tag[:data_tag.index("</script>")]
    assert "<!--" not in data_tag and "</" not in data_tag[10:]
    assert "<!--<script>x</script>" in _payload(html)["f"]["label"]


def test_site_level_report_offers_the_protein_view(tmp_path):
    lq = tmp_path / "e/fragpipe/combined_modified_peptide_label_quant.tsv"
    simulate.isodtb_label_quant(lq, {"EJQ_2_027": [1, 2, 3]}, seed=3)
    out = downstream.analyze(tmp_path / "e", "isoDTB", {"enrichment": False})
    html = out.report.read_text(encoding="utf-8")
    assert "id='rprot'" in html and "id='onoff'" not in html
    d = _payload(html)
    assert d["kind"] == "ratio" and d["comps"][0]["onoff"] == []
