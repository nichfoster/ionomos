"""Site changes corrected for protein abundance (downstream/proteincorr.py, D70): MSstatsPTM's adjustment against
MSstatsPTM itself (tests/golden/ptm/, no R needed here), the proteome readers, the condition matching and the
doctor's issues, and the whole analysis of a simulated isoDTB experiment with a simulated proteome."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import analysis, fpa, proteincorr, simulate
from ionomos.downstream.analysis import AnalysisError, Settings, settings_from
from ionomos.downstream.quant import Feature, QuantMatrix
from ionomos.downstream.tables import num, read_tsv, write_tsv

GOLDEN = Path(__file__).parent / "golden" / "ptm"


def _site_matrix(path: Path) -> QuantMatrix:
    header, rows = read_tsv(path)
    samples = header[1:]
    feats = [Feature(id=r["id"], label=r["id"].split("|")[2].split("_")[0] + " " + r["id"].rsplit("|", 1)[1],
                     description="") for r in rows]
    vals = [[num(r[s]) for s in samples] for r in rows]
    return QuantMatrix("ratio", "site", feats, samples, vals, {s: s.rsplit("_", 1)[0] for s in samples}, str(path),
                       exp="isoDTB")


def _site_diffs(m: QuantMatrix, s: Settings):
    p, _ = fpa.process(m)
    comps, _ = analysis.choose_comparisons(p.m, s)
    res = analysis.run_contrasts(p, comps, s)
    return p, [analysis.to_diff(p, r, c, s) for r, (_t, c) in zip(res, comps, strict=True)]


def test_the_adjustment_matches_msstatsptm():
    """Rscript run_msstatsptm.R: limma's one-sample fit of the sites, MSstatsPTM 2.14.0's .applyPtmAdjustment."""
    s = Settings()
    p, diffs = _site_diffs(_site_matrix(GOLDEN / "ptm_sites.tsv"), s)
    proteome = proteincorr.load(GOLDEN / "ptm_protein.tsv", "protein")
    assert proteome.kind == "msstats" and proteome.names() == ["Cmpd-DMSO"]
    assert proteome.comparisons[0].treatment == "Cmpd" and proteome.comparisons[0].control == "DMSO"
    res = proteincorr.run(p, diffs, s, proteome)
    assert len(res.diffs) == 1 and not res.problems
    got = {r["id"]: r for r in res.diffs[0].rows}
    site = {r["id"]: r for r in diffs[0].rows}
    _h, ref = read_tsv(GOLDEN / "ptm_adjusted.tsv")
    assert len(ref) == 213
    for r in ref:
        g = got[r["Site"]]
        assert g["protein_status"] == "corrected", r["Site"]
        for mine, theirs in (("log2fc", "log2FC"), ("se", "SE"), ("df", "DF"), ("t", "Tvalue"), ("pvalue", "pvalue"),
                             ("qvalue", "adj.pvalue"), ("site_log2fc", "site_log2FC"), ("site_se", "site_SE"),
                             ("site_df", "site_DF")):
            assert g[mine] == pytest.approx(float(r[theirs]), rel=1e-9, abs=1e-13), (r["Site"], mine)
        assert site[r["Site"]]["se"] == pytest.approx(float(r["site_SE"]), rel=1e-9)
    # MSstatsPTM leaves out the sites whose protein is not in the proteome; Ionomos keeps and flags them
    flagged = [r for r in res.diffs[0].rows if r["protein_status"] == "protein not found"]
    tested = sum(1 for r in diffs[0].rows if r["pvalue"] is not None)
    assert sum(1 for r in flagged if r["site_pvalue"] is not None) == tested - 213 > 0
    assert all(r["pvalue"] is None and r["log2fc"] is None for r in flagged)
    # the proteins given DF = Inf took the other branch of the Satterthwaite sum
    inf = {r["protein_key"] for r in res.diffs[0].rows if r["protein_df"] == math.inf}
    assert len(inf) == 2 and all(math.isfinite(r["df"]) for r in res.diffs[0].rows if r["protein_key"] in inf)


def test_the_formula_by_hand():
    fc, se, df, t, p = proteincorr.adjust(2.0, 0.3, 4.0, -1.0, 0.4, 10.0)
    assert fc == pytest.approx(3.0) and se == pytest.approx(0.5)
    assert df == pytest.approx(0.25 ** 2 / (0.09 ** 2 / 4 + 0.16 ** 2 / 10))
    assert t == pytest.approx(6.0) and 0 < p < 0.01
    fc, se, df, t, p = proteincorr.adjust(1.0, 0.3, math.inf, 0.0, 0.4, math.inf)  # both pooled: normal
    assert df == math.inf and p == pytest.approx(math.erfc(2.0 / math.sqrt(2)))


def test_keys():
    assert proteincorr.protein_keys("sp|P04406|G3P_HUMAN") == ["P04406"]
    assert proteincorr.protein_keys("P04406-2;Q9Y6K9") == ["P04406", "Q9Y6K9"]
    assert proteincorr.protein_keys("GAPDH") == ["GAPDH"]           # an accession-looking word is kept
    assert proteincorr.gene_keys("gapdh;GAPDHS") == ["GAPDH", "GAPDHS"]
    f = Feature(id="sp|P04406-2|G3P_HUMAN|C152", label="GAPDH C152", description="")
    assert proteincorr.site_keys(f, "protein") == ["P04406"] and proteincorr.site_keys(f, "gene") == ["GAPDH"]


def test_settings():
    s = settings_from({"protein_correction": {"proteome": "D:/p", "match": "Proteins"}},
                      {"protein_correction": {"conditions": {"EJQ_2_027": "Cmpd vs DMSO"}}})
    assert s.protein_correction == {"proteome": "D:/p", "match": "protein", "conditions": {"EJQ_2_027": "Cmpd vs DMSO"}}
    assert settings_from({"protein_correction": "D:/p"}).protein_correction == {"proteome": "D:/p", "match": "gene"}
    assert settings_from({"protein_correction": {"proteome": "D:/p"}}, {"protein_correction": False}).protein_correction == {}
    assert Settings().protein_correction == {}
    for bad in ({"match": "gene"}, {"proteome": "x", "match": "peptide"}, {"proteome": "x", "conditions": ["a"]},
                {"proteome": "x", "typo": 1}, 7):
        with pytest.raises(AnalysisError):
            settings_from({"protein_correction": bad})


def _differential(path: Path, rows: list[dict], cols=analysis.DIFF_COLUMNS) -> Path:
    return write_tsv(path, cols, rows)


def test_a_differential_table_without_se_and_df_is_read_from_t_and_the_interval(tmp_path):
    s = Settings()
    m = QuantMatrix("intensity", "protein",
                    [Feature(id=f"sp|P{i:05d}|G{i}_HUMAN", label=f"G{i}", description="") for i in range(40)],
                    [f"{c}_{r}" for c in ("DMSO", "Cmpd") for r in (1, 2, 3)],
                    [[20.0 + (i % 7) * 0.1 + ((j * 7 + i * 3) % 5) * 0.11 + (0.8 if j >= 3 and i % 4 == 0 else 0)
                      for j in range(6)] for i in range(40)],
                    {f"{c}_{r}": c for c in ("DMSO", "Cmpd") for r in (1, 2, 3)}, "x")
    p, _ = fpa.process(m, imputation="none")
    r = analysis.run_contrasts(p, [("Cmpd", "DMSO")], s)[0]
    d = analysis.to_diff(p, r, "DMSO", s)
    full = _differential(tmp_path / "Cmpd_vs_DMSO_differential.tsv", d.rows)
    old = _differential(tmp_path / "old" / "Cmpd_vs_DMSO_differential.tsv", d.rows,
                        [c for c in analysis.DIFF_COLUMNS if c not in ("se", "df")])
    a = proteincorr.load(full, "gene").comparisons[0]
    b = proteincorr.load(old, "protein").comparisons[0]
    assert (a.name, a.treatment, a.control, a.scale) == ("Cmpd_vs_DMSO", "Cmpd", "DMSO", "fc")
    for i in range(40):
        fc, se, df, _pid = a.rows[f"G{i}"]
        fc2, se2, df2, _ = b.rows[f"P{i:05d}"]
        assert fc2 == fc and se2 == pytest.approx(se, rel=1e-9) and df2 == pytest.approx(df, rel=1e-6)


def test_condition_matching_never_guesses():
    comp = [proteincorr.Comparison("Cmpd vs DMSO", "Cmpd", "DMSO", "fc"),
            proteincorr.Comparison("Cmpd vs Probe", "Cmpd", "Probe", "fc"),
            proteincorr.Comparison("Other vs DMSO", "Other", "DMSO", "fc")]
    prot = proteincorr.Proteome(Path("x"), "ionomos", "gene", comp)
    assert proteincorr._pick(prot, "other", {})[0].name == "Other vs DMSO"
    c, why = proteincorr._pick(prot, "Cmpd", {})
    assert c is None and "2 comparisons of Cmpd" in why
    assert proteincorr._pick(prot, "EJQ_2_027", {"ejq_2_027": "cmpd vs dmso"})[0].name == "Cmpd vs DMSO"
    c, why = proteincorr._pick(prot, "EJQ_2_027", {})
    assert c is None and "no proteome comparison" in why
    c, why = proteincorr._pick(prot, "EJQ_2_027", {"EJQ_2_027": "Nope vs DMSO"})
    assert c is None and "is not a comparison" in why


def test_scale_follows_the_tag_of_the_treated_sample():
    fc = proteincorr.Comparison("Cmpd vs DMSO", "Cmpd", "DMSO", "fc")
    hl = proteincorr.Comparison("P (log2 H/L vs 0)", "P", "", "hl")
    assert proteincorr.to_hl(fc, 1.5, "high") == -1.5 and proteincorr.to_hl(fc, 1.5, "low") == 1.5
    assert proteincorr.to_hl(hl, 1.5, "high") == 1.5
    assert proteincorr._split("EJQ_2_030 (log2 H/L vs 0)") == ("EJQ_2_030", "", "hl")


# ------------------------------------------------------------------- end to end --


@pytest.fixture(scope="module")
def experiment(tmp_path_factory):
    """A proteome (DIA, Cmpd vs DMSO, GENE30 down 4-fold) and an isoDTB experiment whose sites share its genes
    (all but GENE90 to GENE99, which are not in the proteome)."""
    root = tmp_path_factory.mktemp("ptm")
    prot = root / "proteome"
    runs = [(f"DMSO_{r}.raw", "DMSO") for r in (1, 2, 3)] + [(f"Cmpd_{r}.raw", "Cmpd") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(prot / "fragpipe" / "report.pg_matrix.tsv", runs, seed=5, n_proteins=90,
                           changed_fraction=0.0, planted={"Cmpd": {"GENE30": -2.0}})
    downstream.analyze(prot, method="DIA", analysis_cfg={"enrichment": False})
    iso = root / "iso"
    simulate.isodtb_ratios(iso / "fragpipe" / "combined_modified_peptide_label_quant.tsv", {"Cmpd": 3}, seed=6,
                           n_sites=300, changed_fraction=0.05)
    return root, prot, iso


def _run(iso, **pc):
    return downstream.analyze(iso, method="isoDTB", analysis_cfg={"enrichment": False},
                              overrides={"protein_correction": pc} if pc else None)


def test_analyze_reports_both_and_flags_missing_proteins(experiment):
    _root, prot, iso = experiment
    out = _run(iso, proteome=str(prot))
    names = [c["name"] for c in out.summary["comparisons"]]
    assert names == ["Cmpd (log2 H/L vs 0)", "Cmpd (log2 H/L vs 0, protein-corrected)"]
    pc = out.summary["protein_correction"]
    assert pc["ran"] and pc["source"] == "ionomos" and pc["match"] == "gene" and "light tag" in pc["orientation"]
    c = pc["conditions"][0]
    assert c["proteome_comparison"] == "Cmpd vs DMSO" and c["protein_not_found"] == 30   # GENE90-99 x 3 sites
    res = iso / "results"
    _h, rows = read_tsv(res / "Cmpd_log2_H_L_vs_0_protein-corrected_differential.tsv")
    _h2, plain = read_tsv(res / "Cmpd_log2_H_L_vs_0_differential.tsv")
    assert "se" in _h2 and "df" in _h2
    by = {r["id"]: r for r in plain}
    g30 = [r for r in rows if r["label"].startswith("GENE30 ")]
    assert len(g30) == 3
    for r in g30:  # the protein went down 4-fold with the compound: heavy / light up by 2, removed from the site
        assert r["protein_status"] == "corrected" and float(r["protein_log2_hl"]) == pytest.approx(2.0, abs=0.5)
        assert float(r["log2fc"]) == pytest.approx(float(by[r["id"]]["log2fc"]) - float(r["protein_log2_hl"]))
        assert float(by[r["id"]]["log2fc"]) < 0.8 and float(r["log2fc"]) < -1   # unchanged site, falling protein
    missing = [r for r in rows if r["protein_status"] == "protein not found"]
    assert missing and all(r["log2fc"] == "NA" and r["pvalue"] == "NA" for r in missing)
    assert by[missing[0]["id"]]["pvalue"] != "NA"                       # the uncorrected result is still there
    assert (res / "volcano_Cmpd_log2_H_L_vs_0_protein-corrected.svg").is_file()
    _h3, cysrows = read_tsv(res / "cysteine_sites.tsv")
    assert "Cmpd protein_log2_R" in _h3 and "Cmpd log2_R_corrected" in _h3
    html = (res / "report.html").read_text(encoding="utf-8")
    assert "MSstatsPTM" in html and "protein-corrected" in html
    assert json.loads((res / "analysis.json").read_text(encoding="utf-8"))["protein_correction"]["ran"]
    assert not [i for i in out.issues if i.code.startswith("PROTEIN_CORRECTION")]


def test_direction_low_and_protein_matching(experiment):
    _root, prot, iso = experiment
    out = downstream.analyze(iso, method="isoDTB", analysis_cfg={"enrichment": False, "liganded_direction": "low"},
                             overrides={"protein_correction": {"proteome": str(prot / "results"), "match": "protein"}})
    c = out.summary["protein_correction"]["conditions"][0]
    assert c["corrected"] == 0 and c["protein_not_found"] == c["sites"]   # different accessions on the two sides
    assert [i.code for i in out.issues if i.code.startswith("PROTEIN")] == ["PROTEIN_CORRECTION"]
    assert "low" in out.summary["protein_correction"]["orientation"]


def test_unmatched_conditions_and_a_missing_proteome_are_issues(experiment, tmp_path):
    root, prot, iso = experiment
    out = _run(iso, proteome=str(prot), conditions={"Cmpd": "Other vs DMSO"})
    issue = next(i for i in out.issues if i.code == "PROTEIN_CORRECTION_CONDITIONS")
    assert issue.severity == "input" and "Cmpd vs DMSO" in issue.message and "Other vs DMSO" in issue.message
    assert not out.summary["protein_correction"]["ran"]
    assert [c["name"] for c in out.summary["comparisons"]] == ["Cmpd (log2 H/L vs 0)"]
    out = _run(iso, proteome="no_such_folder")
    issue = next(i for i in out.issues if i.code == "PROTEIN_CORRECTION")
    assert issue.severity == "input" and "no_such_folder" in issue.message
    assert len(out.summary["comparisons"]) == 1
    out = _run(iso, proteome=str(iso))                        # a site analysis is not a proteome
    assert "not a proteome" in next(i for i in out.issues if i.code == "PROTEIN_CORRECTION").message
    bad = write_tsv(tmp_path / "x.tsv", ["a", "b"], [[1, 2]])
    out = _run(iso, proteome=str(bad))
    assert "MSstats" in next(i for i in out.issues if i.code == "PROTEIN_CORRECTION").message


def test_a_relative_proteome_is_read_from_the_experiment_folder_and_left_alone(experiment):
    root, prot, iso = experiment
    table = iso / "proteome_Cmpd_vs_DMSO_differential.tsv"
    src = prot / "results" / "Cmpd_vs_DMSO_differential.tsv"
    table.write_bytes(src.read_bytes())
    before = {p: p.stat().st_mtime_ns for p in (prot / "results").iterdir()}
    out = _run(iso, proteome=table.name, conditions={"Cmpd": "proteome_Cmpd_vs_DMSO"})
    assert out.summary["protein_correction"]["source"] == "differential"
    assert out.summary["protein_correction"]["conditions"][0]["corrected"] > 100
    assert {p: p.stat().st_mtime_ns for p in (prot / "results").iterdir()} == before
    table.unlink()


def test_intensity_data_ignores_the_setting(experiment):
    _root, prot, _iso = experiment
    out = downstream.analyze(prot, method="DIA", analysis_cfg={"enrichment": False},
                             overrides={"protein_correction": {"proteome": "x"}})
    assert out.summary["protein_correction"] == {"ran": False,
                                                 "reason": "protein_correction applies to site data (isoDTB ratios, "
                                                           "phosphosites) only"}
