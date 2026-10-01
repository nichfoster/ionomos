"""`ionomos compare` (downstream/compare.py, D60): an Ionomos analysis against a reference result.

The references here are made from the Ionomos result itself (its own table, shifted, scaled, renamed,
reformatted as limma / MSstats / Perseus output), so the right answer of each comparison is known. No real
FragPipe-Analyst, MSstats or Perseus export was available: the column layouts are those the tools document."""
from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ionomos import cli, downstream
from ionomos.downstream import compare, simulate
from ionomos.downstream.compare import Rows, Side
from ionomos.downstream.tables import read_tsv

CFG = {"enrichment": False}
RUNS = [(f"C:\\d\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]


@pytest.fixture(scope="module")
def exp(tmp_path_factory):
    """One analysed simulated experiment: {"dir", "rows" (its differential table), "out"}."""
    d = tmp_path_factory.mktemp("cmp") / "exp"
    simulate.dia_pg_matrix(d / "fragpipe/report.pg_matrix.tsv", RUNS, seed=3, n_proteins=500, noise_spread=0.4)
    out = downstream.analyze(d, "DIA", analysis_cfg=CFG)
    _h, rows = read_tsv(d / "results/Drug_vs_DMSO_differential.tsv")
    return {"dir": d, "rows": rows, "out": out}


def _table(path: Path, header, rows, sep="\t") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sep.join(str(c) for c in r) for r in [header, *rows]) + "\n", encoding="utf-8")
    return path


def _limma(path: Path, rows, shift=0.0, scale=1.0, fc_name="logFC", ids=lambda r: r["id"]) -> Path:
    """The Ionomos result written as a limma topTable (ID, logFC, P.Value, adj.P.Val)."""
    out = [[ids(r), f"{float(r['log2fc']) * scale + shift:.6f}", r["pvalue"], r["qvalue"]]
           for r in rows if r["log2fc"] != "NA"]
    return _table(path, ["ID", fc_name, "P.Value", "adj.P.Val"], out)


def _one(a, b, **kw):
    res = compare.compare(compare.load_side(a), compare.load_side(b), **kw)
    assert len(res["pairs"]) == 1
    return res, res["pairs"][0]


# ----------------------------------------------------------------- verdicts --


def test_an_analysis_agrees_with_its_own_results_table(exp):
    res, p = _one(exp["dir"], exp["dir"] / "results/protein_results.tsv")
    assert p["verdict"].startswith("agrees (") and p["matched"] == len(exp["rows"]) and p["only_ionomos"] == p["only_reference"] == 0
    assert p["matched_by"] == "id" and p["pearson"] == pytest.approx(1.0) and p["slope"] == pytest.approx(1.0)
    assert p["offset"] == pytest.approx(0.0, abs=1e-9)
    own, common = p["hits"]["own"], p["hits"]["common"]
    hits = exp["out"].summary["comparisons"][0]
    assert own["both"] == common["both"] == hits["up"] + hits["down"] > 20
    assert own["only_ionomos"] == own["only_reference"] == own["opposite_direction"] == 0
    assert "its own calls" in own["reference_cutoffs"], "the table's <comparison>_significant column is used"
    assert p["p_values"]["spearman"] == pytest.approx(1.0) and p["p_values"]["within_factor_10"] == 1.0
    assert res["verdicts"] == [f"Drug vs DMSO: {p['verdict']}"]


def test_a_normalisation_difference_is_an_offset_not_a_disagreement(exp, tmp_path):
    ref = _limma(tmp_path / "limma.tsv", exp["rows"], shift=-0.30)
    res, p = _one(exp["dir"], ref)
    assert p["verdict"].startswith("agrees after an offset of +0.30 log2")
    assert p["offset"] == pytest.approx(0.30, abs=1e-6) and p["pearson"] == pytest.approx(1.0)
    assert "paired by position" in p["notes"][0], "a bare logFC table has no comparison name"
    assert any("hit lists were not judged" in r for r in p["verdict_reasons"])
    assert "assumed" in p["hits"]["own"]["reference_cutoffs"], "limma's table has no calls of its own"


def test_compressed_fold_changes_differ_by_their_slope(exp, tmp_path):
    res, p = _one(exp["dir"], _limma(tmp_path / "limma.tsv", exp["rows"], scale=2.0))
    assert p["verdict"].startswith("differs: the slope is 0.50") and "smaller than the reference's" in p["verdict"]
    assert p["pearson"] == pytest.approx(1.0) and p["slope"] == pytest.approx(0.5, abs=1e-6)


def test_noisy_fold_changes_differ_by_their_correlation(exp, tmp_path):
    rng = random.Random(1)
    rows = [{**r, "log2fc": f"{float(r['log2fc']) + rng.gauss(0, 0.8):.5f}"} for r in exp["rows"] if r["log2fc"] != "NA"]
    res, p = _one(exp["dir"], _limma(tmp_path / "limma.tsv", rows))
    assert p["verdict"].startswith("differs: the fold changes correlate at r = 0.") and p["pearson"] < 0.95
    big = p["largest_disagreements"]
    assert len(big) == compare.TOP and abs(big[0]["difference"]) >= abs(big[-1]["difference"]) > 1.0


def test_agreeing_fold_changes_with_other_hit_calls_differ(exp, tmp_path):
    """Same fold changes, p-values of a weaker test (here: the square root of each p): the hit lists part ways."""
    rows = [{**r, "pvalue": float(r["pvalue"]) ** 0.5, "qvalue": float(r["qvalue"]) ** 0.5}
            for r in exp["rows"] if r["log2fc"] != "NA"]
    res, p = _one(exp["dir"], _limma(tmp_path / "limma.tsv", rows))
    assert p["verdict"].startswith("differs: the fold changes agree (r = 1.000), but the hit lists share only")
    assert p["hits"]["common"]["only_ionomos"] > 10 and p["hits"]["common"]["only_reference"] == 0
    assert p["p_values"]["median_log10_ratio"] < -0.1 and p["p_values"]["spearman"] == pytest.approx(1.0)


def test_a_reference_the_other_way_round_is_flipped_by_its_name(exp, tmp_path):
    rows = [[r["id"], -float(r["log2fc"]), r["pvalue"], r["qvalue"]] for r in exp["rows"] if r["log2fc"] != "NA"]
    ref = _table(tmp_path / "ref.tsv", ["Protein", "DMSO_vs_Drug_log2FC", "DMSO_vs_Drug_p.val", "DMSO_vs_Drug_p.adj"], rows)
    res, p = _one(exp["dir"], ref)
    assert p["flipped"] and p["verdict"].startswith("agrees (") and "the other way round" in " ".join(p["notes"])
    # without a telling name the direction is not guessed: the verdict says what to do
    bare = _table(tmp_path / "bare.tsv", ["Protein", "log2FC", "p.val", "p.adj"], rows)
    res, p = _one(exp["dir"], bare)
    assert p["verdict"].startswith("differs: the fold changes point the opposite way") and "--flip" in p["verdict"]
    res, p = _one(exp["dir"], bare, flip=True)
    assert p["verdict"].startswith("agrees (")


def test_too_few_matches_are_not_judged(exp, tmp_path):
    rows = [{**r, "id": f"X{i}"} for i, r in enumerate(exp["rows"])]
    res, p = _one(exp["dir"], _limma(tmp_path / "limma.tsv", rows))
    assert p["verdict"].startswith("not judged: only 0 of") and p["only_ionomos"] == len(exp["rows"])


@pytest.mark.parametrize("r, slope, offset, jaccard, hits, want", [
    (0.99, 1.0, 0.0, 0.9, 40, "agrees ("),
    (0.99, 1.0, 0.0, None, 0, "agrees ("),                       # no hits anywhere: nothing to disagree on
    (0.99, 1.0, 0.0, 0.2, 6, "agrees ("),                        # too few hits to judge the lists
    (0.949, 1.0, 0.0, 0.9, 40, "differs: the fold changes correlate"),
    (0.99, 0.89, 0.0, 0.9, 40, "differs: the slope is 0.89"),
    (0.99, 1.11, 0.0, 0.9, 40, "differs: the slope is 1.11"),
    (0.99, 1.0, 0.11, 0.2, 40, "agrees after an offset of +0.11"),
    (0.99, 1.0, -0.5, 0.9, 40, "agrees after an offset of -0.50"),
    (0.99, 1.0, 0.0, 0.69, 40, "differs: the fold changes agree"),
    (-0.9, -1.0, 0.0, 0.0, 40, "differs: the fold changes point the opposite way"),
])
def test_the_verdict_at_its_thresholds(r, slope, offset, jaccard, hits, want):
    res = {"matched_with_fold_change": 300, "features_ionomos": 300, "features_reference": 300, "matched": 300,
           "matched_by": "id", "pearson": r, "slope": slope, "offset": offset,
           "hits": {"common": {"both": hits, "only_ionomos": 0, "only_reference": 0, "opposite_direction": 0,
                               "jaccard": jaccard}}}
    assert compare.verdict(res)[0].startswith(want)


# ----------------------------------------------------------------- matching --


def _side(name, ids, labels, fc, p=None, kind="table", **kw):
    p = p or [0.01] * len(ids)
    return Side(name, kind, Path(name), [Rows("Drug vs DMSO", ids, labels, fc, p, list(p))], **kw)


def test_features_match_by_accession_inside_protein_groups_and_fasta_style_ids():
    a = Rows("x", ["P1;P2", "sp|P3|GENE3_HUMAN", "P4", "p5"], ["G1", "G3", "G4", "G5"], [1.0] * 4, [0.1] * 4, [0.1] * 4)
    b = Rows("x", ["P2", "P3", "P9", "P5"], ["", "", "", ""], [1.0] * 4, [0.1] * 4, [0.1] * 4)
    pairs, key, _ = compare.match(a, b)
    assert key == "id" and pairs == [(0, 0), (1, 1), (3, 3)]


def test_features_match_by_gene_when_the_ids_are_of_another_kind():
    a = Rows("x", ["P1", "P2", "P3"], ["ACTB", "GAPDH;GAPDHS", "TUBB"], [1.0] * 3, [0.1] * 3, [0.1] * 3)
    b = Rows("x", ["ENSG01", "gapdh", "ENSG03"], ["actb", "gapdh", "VIM"], [1.0] * 3, [0.1] * 3, [0.1] * 3)
    pairs, key, _ = compare.match(a, b)
    assert key == "gene" and pairs == [(0, 0), (1, 1)]
    assert compare.match(a, b, by="id")[0] == []


def test_a_reference_row_is_matched_once_and_repeats_are_counted():
    a = Rows("x", ["P1", "P1", "P2"], ["a", "b", "c"], [1.0] * 3, [0.1] * 3, [0.1] * 3)
    b = Rows("x", ["P1", "P2", "P2"], ["a", "c", "c"], [1.0] * 3, [0.1] * 3, [0.1] * 3)
    pairs, key, extra = compare.match(a, b)
    assert pairs == [(0, 0), (2, 1)] and extra["repeated_keys"] == 1


def test_comparisons_pair_by_name_whatever_the_spelling():
    def side(*names):
        return Side("s", "table", Path("s"), [Rows(n, [], [], [], [], []) for n in names])

    a = side("Drug vs DMSO", "Other vs DMSO")
    got = compare.pair_comparisons(a, side("Other_vs_DMSO", "DMSO - Drug", "Unrelated vs X"))
    assert [(x.name, y.name, f) for x, y, f, _ in got] == [("Drug vs DMSO", "DMSO - Drug", True),
                                                           ("Other vs DMSO", "Other_vs_DMSO", False)]
    with pytest.raises(compare.CompareError, match="--comparison and --ref-comparison"):
        compare.pair_comparisons(a, side("A vs B", "C vs D"))
    got = compare.pair_comparisons(a, side("A vs B", "C vs D"), "Other vs DMSO", "C vs D")
    assert [(x.name, y.name) for x, y, _f, _n in got] == [("Other vs DMSO", "C vs D")]
    with pytest.raises(compare.CompareError, match="no single comparison named 'nope'"):
        compare.pair_comparisons(a, side("A vs B"), "nope")


# ------------------------------------------------------------------ formats --


def test_msstats_long_format_with_infinite_fold_changes(exp, tmp_path):
    rows = []
    for r in exp["rows"]:
        if r["log2fc"] == "NA":
            continue
        rows.append([r["id"], "Drug vs DMSO", r["log2fc"], 0.1, 3.0, 4, r["pvalue"], r["qvalue"], "NA", 0, 0])
        rows.append([r["id"], "Other vs DMSO", 0.0, 0.1, 0.0, 4, 0.9, 0.95, "NA", 0, 0])
    rows.append(["P_ONLY_IN_DRUG", "Drug vs DMSO", "Inf", "NA", "NA", "NA", "NA", 0, "oneConditionMissing", 0.5, 0])
    ref = _table(tmp_path / "msstats.csv", ["Protein", "Label", "log2FC", "SE", "Tvalue", "DF", "pvalue", "adj.pvalue",
                                            "issue", "MissingPercentage", "ImputationPercentage"], rows, sep=",")
    side = compare.load_side(ref)
    assert [c.name for c in side.comparisons] == ["Drug vs DMSO", "Other vs DMSO"]
    assert "1 fold changes are infinite" in " ".join(side.notes) and "long format" in " ".join(side.notes)
    res, p = _one(exp["dir"], ref)
    assert p["verdict"].startswith("agrees (") and p["only_reference"] == 1 and p["matched"] == len(exp["rows"])


def test_perseus_matrix_with_minus_log_p_and_plus_signs(exp, tmp_path):
    rows = [[r["id"], r["label"], r["log2fc"], f"{-math.log10(float(r['pvalue'])):.6f}", r["qvalue"],
             "+" if r["significant"] in ("up", "down") else ""] for r in exp["rows"] if r["log2fc"] != "NA"]
    ref = _table(tmp_path / "perseus.txt", ["Protein IDs", "Gene names", "Student's T-test Difference Drug_DMSO",
                                            "-Log Student's T-test p-value Drug_DMSO",
                                            "Student's T-test q-value Drug_DMSO",
                                            "Student's T-test Significant Drug_DMSO"], rows)
    res, p = _one(exp["dir"], ref)
    assert p["verdict"].startswith("agrees (") and p["reference_comparison"] == "Drug_DMSO"
    assert p["hits"]["own"]["reference_cutoffs"] == "its own calls" and p["hits"]["own"]["only_reference"] == 0
    assert p["p_values"]["spearman"] == pytest.approx(1.0, abs=1e-6)


def test_a_reference_by_gene_symbol_and_without_adjusted_p(exp, tmp_path):
    rows = [[r["label"], r["log2fc"], r["pvalue"]] for r in exp["rows"] if r["log2fc"] != "NA"]
    ref = _table(tmp_path / "old_r_output.csv", ["Gene", "log2FoldChange", "pvalue"], rows, sep=",")
    res, p = _one(exp["dir"], ref)
    assert p["matched_by"] == "gene" and p["matched"] == len(exp["rows"]) and p["verdict"].startswith("agrees (")
    assert any("Benjamini-Hochberg computed" in n for n in res["notes"])
    assert p["hits"]["common"]["only_ionomos"] == p["hits"]["common"]["only_reference"] == 0


def test_the_reference_own_cutoffs_can_be_given(exp, tmp_path):
    ref = _limma(tmp_path / "limma.tsv", exp["rows"])
    a, b = compare.load_side(exp["dir"]), compare.load_side(ref)
    p = compare.compare(a, b, ref_alpha=0.01, ref_log2fc=2.0)["pairs"][0]
    assert p["hits"]["own"]["reference_cutoffs"] == "adjusted p ≤ 0.01 and |log2FC| ≥ 2"
    assert p["hits"]["own"]["only_ionomos"] > 0 and p["hits"]["common"]["only_ionomos"] == 0
    strict = compare.compare(a, b, alpha=0.001, log2fc=1.5)["pairs"][0]
    assert strict["hits"]["common"]["cutoffs"] == "adjusted p ≤ 0.001 and |log2FC| ≥ 1.5"
    assert strict["hits"]["common"]["both"] < p["hits"]["common"]["both"]


def test_two_ionomos_analyses_with_other_settings(exp, tmp_path):
    simulate.dia_pg_matrix(tmp_path / "b/fragpipe/report.pg_matrix.tsv", RUNS, seed=3, n_proteins=500, noise_spread=0.4)
    downstream.analyze(tmp_path / "b", "DIA", analysis_cfg={**CFG, "normalize": "none", "alpha": 0.01})
    res, p = _one(exp["dir"], tmp_path / "b")
    assert res["reference_kind"] == "another Ionomos analysis"
    assert p["pearson"] > 0.999 and abs(p["offset"]) > 0.02, "the loading differences show as an offset"
    assert "adjusted p ≤ 0.01" in p["hits"]["own"]["reference_cutoffs"]
    assert p["hits"]["own"]["both"] <= p["hits"]["common"]["both"] + p["hits"]["common"]["only_reference"]


def test_what_cannot_be_compared_says_why(exp, tmp_path):
    with pytest.raises(compare.CompareError, match="holds no Ionomos analysis"):
        compare.load_side(tmp_path)
    with pytest.raises(compare.CompareError, match="holds quantities, not results"):
        compare.load_side(exp["dir"] / "fragpipe/report.pg_matrix.tsv")
    with pytest.raises(compare.CompareError, match="not a folder or a table"):
        compare.load_side(tmp_path / "nope.tsv")
    (tmp_path / "junk.tsv").write_text("just a line of text\n", encoding="utf-8")
    with pytest.raises(compare.CompareError, match="cannot read junk.tsv"):
        compare.load_side(tmp_path / "junk.tsv")


# ------------------------------------------------------------------- output --


def test_the_three_files(exp, tmp_path):
    rng = random.Random(4)
    rows = [{**r, "id": '<img src=x onerror="alert(1)">' if i == 0 else r["id"],
             "log2fc": f"{float(r['log2fc']) + rng.gauss(0, 0.3):.5f}"}
            for i, r in enumerate(x for x in exp["rows"] if x["log2fc"] != "NA")][:-40]
    ref = _limma(tmp_path / "ref & 'x'.tsv", rows)  # a name Windows allows too
    res = compare.compare(compare.load_side(exp["dir"]), compare.load_side(ref))
    files = compare.write(res, tmp_path / "out")
    assert [f.name for f in files] == ["compare.tsv", "compare.json", "compare.html"]
    header, table = read_tsv(files[0])
    assert header == compare.COLUMNS
    status = [r["status"] for r in table]
    p = res["pairs"][0]
    assert status.count("matched") == p["matched"] and status.count("only_ionomos") == p["only_ionomos"] == 41
    assert status.count("only_reference") == p["only_reference"] == 1
    data = json.loads(files[1].read_text(encoding="utf-8"), parse_constant=lambda c: pytest.fail(c))
    assert data["verdicts"] == res["verdicts"] and data["thresholds"]["pearson_min"] == compare.R_MIN
    assert not any(k.startswith("_") for k in data["pairs"][0]) and data["analysis"]["settings_digest"]
    html = files[2].read_text(encoding="utf-8")
    low = html.lower()
    for external in ("src=", "<link", "@import", "url(", "<script"):
        assert external not in low.replace("&lt;img src=x", ""), external
    assert "<img" not in html and "ref &amp; &#x27;x&#x27;.tsv" in html
    svgs = re.findall(r"<svg.*?</svg>", html, re.S)
    assert len(svgs) == 2
    for svg in svgs:
        root = ET.fromstring(svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1))
        assert len(root.findall(".//{http://www.w3.org/2000/svg}circle")) > 400
    assert "How the verdict is made" in html and f"≥ {compare.R_MIN}" in html and p["verdict"].split("(")[0] in html


def test_cli_compare(exp, tmp_path, capsys):
    ref = _limma(tmp_path / "limma.tsv", exp["rows"])
    before = {p: p.read_bytes() for p in [ref, *sorted((exp["dir"] / "results").glob("*.tsv"))]}
    assert cli.main(["compare", str(exp["dir"]), str(ref)]) == 0
    printed = capsys.readouterr().out
    assert "verdict: agrees (r = 1.000" in printed and f"{len(exp['rows'])} matched by id" in printed
    for name in ("compare.tsv", "compare.json", "compare.html"):
        assert (exp["dir"] / "results" / name).is_file()
    assert all(p.read_bytes() == b for p, b in before.items()), "neither result is changed"
    # a result that differs: exit 1, and --out writes elsewhere
    assert cli.main(["compare", str(exp["dir"]), str(_limma(tmp_path / "half.tsv", exp["rows"], scale=2.0)),
                     "--out", str(tmp_path / "o")]) == 1
    assert "verdict: differs: the slope is 0.50" in capsys.readouterr().out and (tmp_path / "o/compare.html").is_file()
    assert cli.main(["compare", str(tmp_path / "nothing"), str(ref)]) == 2
    assert "cannot compare" in capsys.readouterr().err
    assert cli.main(["compare", str(ref), str(ref)]) == 2  # a table first needs --out
    assert "--out" in capsys.readouterr().err
    assert cli.main(["compare", str(ref), str(ref), "--out", str(tmp_path / "tt")]) == 0


def test_the_next_analysis_shows_the_verdict_and_says_when_it_is_stale(tmp_path):
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", RUNS, seed=5, n_proteins=300)
    downstream.analyze(tmp_path / "e", "DIA", analysis_cfg=CFG)
    assert cli.main(["compare", str(tmp_path / "e"), str(tmp_path / "e/results/protein_results.tsv")]) == 0
    out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg=CFG)
    t = out.summary["trust"]["compare"]
    assert t["same_settings"] and t["reference"] == "protein_results.tsv" and t["lines"][0].startswith("Drug vs DMSO: agrees")
    html = out.report.read_text(encoding="utf-8")
    assert "Compared with a reference" in html and "href='compare.html'" in html and "pill warn'>other settings" not in html
    out = downstream.analyze(tmp_path / "e", "DIA", analysis_cfg={**CFG, "imputation": "none"})
    assert out.summary["trust"]["compare"]["same_settings"] is False
    assert "<span class='pill warn'>other settings</span>" in out.report.read_text(encoding="utf-8")
