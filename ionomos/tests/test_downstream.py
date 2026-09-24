"""Downstream analysis: R-script ports vs the real R scripts, statistics vs scipy and limma,
planted-effect recovery end to end, loaders, report and plots.

Golden files (tests/golden/) were produced once by the generator scripts next to them:
  make_inputs.py + run_r_scripts.R     -> R_*.tsv          (the lab's own R scripts, unmodified logic)
  make_stats_reference.py (scipy)      -> stats_reference.json
  make_limma_reference.py + run_limma.R -> limma_*_out.tsv (limma 3.68)
"""
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import analysis, charts, isodtb, quant, simulate, stats, tmt
from ionomos.downstream.tables import num, read_header, read_tsv

GOLD = Path(__file__).parent / "golden"


# ------------------------------------------------------- the lab's R scripts --


def test_isodtb_sites_byte_identical_to_the_r_script(tmp_path):
    written = isodtb.write_site_tables(GOLD / "isodtb_label_quant.tsv", tmp_path)
    assert sorted(p.name for p in written) == ["isoDTB_EJQ_2_027_sites.tsv", "isoDTB_EJQ_2_028_sites.tsv"]
    for p in written:
        assert p.read_bytes() == (GOLD / f"R_{p.name}").read_bytes(), p.name


def test_tmt_annotation_byte_identical_to_the_r_script(tmp_path):
    out = tmt.write_annotation(GOLD / "tmt_abundance_gene_MD.tsv", tmp_path / "a.tsv")
    assert out.read_bytes() == (GOLD / "R_experimental_annotation.tsv").read_bytes()


def test_isodtb_edge_cases():
    header = ["Peptide Sequence", "Light Modified Peptide", "Start", "Protein", "X_1 Log2 Ratio HL"]
    with pytest.raises(isodtb.SiteError, match="no labelled sites"):
        isodtb.site_table(header, [{"Peptide Sequence": "AAK", "Light Modified Peptide": "AAK", "Start": "1",
                                    "Protein": "P", "X_1 Log2 Ratio HL": "1"}], "X")
    with pytest.raises(isodtb.SiteError, match="no 'Y_<n>"):
        isodtb.site_table(header, [], "Y")
    assert isodtb.ratio_prefixes(["a_b_1 Log2 Ratio HL", "a_b_2 Log2 Ratio HL", "c_1 Log2 Ratio HL", "junk"]) == {
        "a_b": ["a_b_1 Log2 Ratio HL", "a_b_2 Log2 Ratio HL"], "c": ["c_1 Log2 Ratio HL"]}


# ------------------------------------------------------------------ statistics --


def test_t_tests_and_bh_match_scipy():
    ref = json.loads((GOLD / "stats_reference.json").read_text(encoding="utf-8"))
    for c in ref["cases"]:
        t, _, p = stats.welch_t(c["a"], c["b"])
        assert t == pytest.approx(c["welch_t"], rel=1e-10) and p == pytest.approx(c["welch_p"], rel=1e-9)
        t1, _, p1 = stats.one_sample_t(c["a"], 20.0)
        assert t1 == pytest.approx(c["one_t"], rel=1e-10) and p1 == pytest.approx(c["one_p"], rel=1e-9)
    assert stats.bh_adjust(ref["bh_input"]) == pytest.approx(ref["bh"], rel=1e-12)


@pytest.mark.parametrize(("inp", "out", "design"), [
    ("limma_two_group.tsv", "limma_two_group_out.tsv", "two"),   # missing values -> unequal df (ML prior)
    ("limma_one_sample.tsv", "limma_one_sample_out.tsv", "one"),  # isoDTB-style ratios vs 0
])
def test_moderated_t_matches_limma(inp, out, design):
    h, rows = read_tsv(GOLD / inp)
    _, ref = read_tsv(GOLD / out)
    coef, s2, df, su = [], [], [], []
    for r in rows:
        if design == "two":
            a = [num(r[c]) for c in h[1:5] if num(r[c]) is not None]
            b = [num(r[c]) for c in h[5:] if num(r[c]) is not None]
            d = len(a) + len(b) - 2
            coef.append(stats.mean(a) - stats.mean(b))
            s2.append(((len(a) - 1) * stats.var(a) + (len(b) - 1) * stats.var(b)) / d)
            df.append(d)
            su.append(math.sqrt(1 / len(a) + 1 / len(b)))
        else:
            a = [num(r[c]) for c in h[1:] if num(r[c]) is not None]
            coef.append(stats.mean(a))
            s2.append(stats.var(a))
            df.append(len(a) - 1)
            su.append(1 / math.sqrt(len(a)))
    t, _, p, _, _ = stats.moderated_t(coef, s2, df, su)
    q = stats.bh_adjust(p)
    for i, r in enumerate(ref):
        assert t[i] == pytest.approx(num(r["t"]), rel=1e-8)
        assert p[i] == pytest.approx(num(r["p"]), rel=1e-6)
        assert q[i] == pytest.approx(num(r["q"]), rel=1e-6)


def test_moderated_t_equal_df_branch_and_small_n():
    # all features complete -> limma's method-of-moments prior
    rows = [[20 + 0.1 * ((i * 7) % 5), 20 + 0.1 * ((i * 3) % 7), 20.2, 20.4, 20.1, 20.3] for i in range(50)]
    coef = [stats.mean(r[:3]) - stats.mean(r[3:]) for r in rows]
    s2 = [(2 * stats.var(r[:3]) + 2 * stats.var(r[3:])) / 4 for r in rows]
    t, dft, p, d0, s0 = stats.moderated_t(coef, s2, [4] * 50, [math.sqrt(2 / 3)] * 50)
    assert d0 > 0 and s0 > 0 and all(0 <= x <= 1 for x in p)
    # two features can't carry a prior: falls back to ordinary t
    t, dft, p, d0, s0 = stats.moderated_t([1.0, -1.0], [0.1, 0.2], [4, 4], [0.8, 0.8])
    assert d0 == 0 and dft == [4, 4]


def test_special_functions():
    assert stats.digamma(1) == pytest.approx(-0.5772156649015329, rel=1e-12)
    assert stats.trigamma(1) == pytest.approx(math.pi ** 2 / 6, rel=1e-12)
    for y in (0.01, 0.5, 3.0, 50.0):
        assert stats.trigamma(stats.trigamma_inverse(y)) == pytest.approx(y, rel=1e-8)
    assert stats.brent_fmin(lambda x: (x - 0.7) ** 2, 0.5, 0.9998) == pytest.approx(0.7, abs=1e-4)


# ------------------------------------------------------- planted-effect recovery --


def _hits(dest: Path, summary: dict, key: str = "label") -> dict:
    _, rows = read_tsv(dest / summary["comparisons"][0]["table"])
    return {r[key]: r["significant"] for r in rows if r["significant"]}


def test_dia_recovers_planted_effects(tmp_path):
    runs = [(f"C:\\Fragpipe_General\\Isaac\\exp\\raw\\{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    truth = simulate.dia_pg_matrix(tmp_path / "e/fragpipe/diann-output/report.pg_matrix.tsv", runs, seed=21)["Drug"]
    record = {"plan": {"manifest": [{"file": f"raw/{c}_{r}.raw", "experiment": c, "bioreplicate": r}
                                    for c in ("DMSO", "Drug") for r in (1, 2, 3)]}}
    out = downstream.analyze(tmp_path / "e", "DIA", record=record)
    assert out.method == "DIA" and not out.warnings
    comp = out.summary["comparisons"][0]
    assert comp["name"] == "Drug vs DMSO" and out.summary["imputation"] == "perseus"
    recall, false = simulate.recall_and_false(_hits(tmp_path / "e", out.summary), truth)
    # FragPipe-Analyst's default (Perseus-type imputation) trades some power on randomly missing values
    assert recall >= 0.7 and false <= 2, (recall, false)
    assert out.summary["samples"] == {f"{c}_{r}": c for c in ("DMSO", "Drug") for r in (1, 2, 3)}
    out = downstream.analyze(tmp_path / "e", "DIA", {"imputation": "none"}, record=record)
    recall, false = simulate.recall_and_false(_hits(tmp_path / "e", out.summary), truth)
    assert recall >= 0.9 and false <= 2, (recall, false)


def test_isodtb_recovers_planted_sites(tmp_path):
    hits = simulate.isodtb_label_quant(tmp_path / "e/fragpipe" / isodtb.LABEL_FILE, {"EJQ_2_027": [1, 2, 3]}, seed=8)
    out = downstream.analyze(tmp_path / "e", "isoDTB")
    found = {k for k, v in _hits(tmp_path / "e", out.summary, "id").items() if v == "up"}
    assert len(found & hits) / len(hits) >= 0.85 and len(found - hits) <= 2
    assert (tmp_path / "e/results/EJQ_2_027_sites.tsv").is_file()  # the R-equivalent table is kept too


def test_tmt_recovers_planted_effects_and_writes_annotation(tmp_path):
    samples = [f"DMSO_1_{c}" for c in ("126", "127N", "127C")] + [f"Drug_1_{c}" for c in ("128N", "128C", "129N")]
    truth = simulate.tmt_abundance(tmp_path / "e/fragpipe/tmt-report/abundance_gene_MD.tsv", samples, seed=4)["Drug"]
    out = downstream.analyze(tmp_path / "e", None)  # method detected from the files
    assert out.method == "TMT"
    recall, false = simulate.recall_and_false(_hits(tmp_path / "e", out.summary), truth)
    assert recall >= 0.9 and false <= 2
    _, ann = read_tsv(tmp_path / "e/results/experimental_annotation.tsv")
    assert [a["condition"] for a in ann] == ["DMSO"] * 3 + ["Drug"] * 3


def test_moderated_beats_welch_on_three_replicates(tmp_path):
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    truth = simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", runs, seed=2)["Drug"]
    rec = {}
    for test in ("welch", "moderated"):
        out = downstream.analyze(tmp_path / "e", "DIA", {"test": test, "imputation": "none"})
        rec[test] = simulate.recall_and_false(_hits(tmp_path / "e", out.summary), truth)[0]
    assert rec["moderated"] > rec["welch"] + 0.3


# ---------------------------------------------------------------- comparisons --


def _matrix(conds: list[str]) -> quant.QuantMatrix:
    samples = [f"{c}_{i}" for c in conds for i in (1, 2, 3)]
    return quant.QuantMatrix("intensity", "protein", [quant.Feature("p", "P")], samples,
                             [[20.0] * len(samples)], {s: s.rsplit("_", 1)[0] for s in samples})


def test_control_detection_and_explicit_comparisons():
    s = analysis.Settings()
    comps, notes = analysis.choose_comparisons(_matrix(["DrugA", "DMSO", "DrugB"]), s)
    assert comps == [("DrugA", "DMSO"), ("DrugB", "DMSO")] and not notes
    comps, notes = analysis.choose_comparisons(_matrix(["WT_treated", "KO"]), s)
    assert comps == [("KO", "WT_treated")]  # 'WT' token recognised
    comps, notes = analysis.choose_comparisons(_matrix(["A", "B"]), s)
    assert comps == [("B", "A")] and "no control condition recognised" in notes[0]
    s2 = analysis.settings_from({"comparisons": ["DrugB vs DrugA", ["DrugA", "DMSO"]]})
    assert analysis.choose_comparisons(_matrix(["DrugA", "DMSO", "DrugB"]), s2)[0] == [("DrugB", "DrugA"), ("DrugA", "DMSO")]
    with pytest.raises(analysis.AnalysisError, match="not a condition"):
        analysis.choose_comparisons(_matrix(["A", "B"]), analysis.settings_from({"comparisons": ["C vs A"]}))
    comps, notes = analysis.choose_comparisons(_matrix(["Only"]), s)
    assert comps == [] and "quality control only" in notes[0]


@pytest.mark.parametrize(("bad", "msg"), [({"alpha": 2}, "between 0 and 1"), ({"test": "anova"}, "moderated"),
                                          ({"min_valid": 1}, "min_valid"), ({"colour": "red"}, "unknown"),
                                          ({"comparisons": ["A and B"]}, "Drug vs DMSO")])
def test_settings_validation(bad, msg):
    with pytest.raises(analysis.AnalysisError, match=msg):
        analysis.settings_from(bad)


def test_bad_settings_in_config_and_experiment_yaml(lab, tmp_path):
    import yaml

    from ionomos.config import ConfigError, load
    from ionomos.manifest import OverridesError, parse_overrides

    cfg = dict(lab["cfg_dict"], analysis={"alpha": 5})
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ConfigError, match="alpha"):
        load(p, check_paths=False)
    with pytest.raises(OverridesError, match="analysis"):
        parse_overrides({"analysis": {"test": "nope"}})
    assert parse_overrides({"analysis": {"control": "DMSO"}}).analysis == {"control": "DMSO"}


# ------------------------------------------------------------------- loaders --


def test_pg_matrix_windows_paths_and_unknown_runs(tmp_path):
    p = tmp_path / "report.pg_matrix.tsv"
    p.write_text("Protein.Group\tProtein.Ids\tProtein.Names\tGenes\tFirst.Protein.Description\tN.Sequences\t"
                 "C:\\data\\DMSO_1.raw\tC:\\data\\DMSO_2.raw\tD:/x/Drug_1.mzML\tDrug_2\n"
                 "P1\tP1\tA_HUMAN\tGENEA;GENEB\tdesc\t3\t1000\t2000\t0\t\n", encoding="utf-8")
    m = quant.from_pg_matrix(p, {"DMSO_1": ("DMSO", 1)})
    assert m.samples == ["DMSO_1", "DMSO_2", "Drug_1", "Drug_2"]
    assert m.condition == {"DMSO_1": "DMSO", "DMSO_2": "DMSO", "Drug_1": "Drug", "Drug_2": "Drug"}
    assert m.features[0].label == "GENEA"
    assert m.values[0] == [math.log2(1000), math.log2(2000), None, None]  # 0 and blank are missing


def test_combined_protein_prefers_maxlfq(tmp_path):
    p = tmp_path / "combined_protein.tsv"
    p.write_text("Protein\tProtein ID\tGene\tA_1 Intensity\tA_1 MaxLFQ Intensity\tB_1 MaxLFQ Intensity\n"
                 "sp|P1|X\tP1\tX\t5\t8\t16\n", encoding="utf-8")
    m = quant.from_combined_protein(p)
    assert m.samples == ["A_1", "B_1"] and m.values[0] == [3.0, 4.0] and m.condition == {"A_1": "A", "B_1": "B"}


def test_run_stem():
    assert quant.run_stem("C:\\x\\y\\DMSO_1.raw") == "DMSO_1"
    assert quant.run_stem("/data/Drug_2.mzML") == "Drug_2"
    assert quant.run_stem("plain") == "plain"


# ------------------------------------------------------------- report + plots --


def _report_data(html: str) -> dict:
    start = html.index("<script id='ionomos-data' type='application/json'>") + len("<script id='ionomos-data' type='application/json'>")
    return json.loads(html[start:html.index("</script>", start)])


def test_report_is_self_contained_and_interactive(tmp_path):
    runs = [(f"/x/{c}_{r}.raw", c) for c in ("DMSO", "Drug", "Drug2") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/report.pg_matrix.tsv", runs, seed=6)
    out = downstream.analyze(tmp_path / "e", "DIA", context={"experiment": "20260914_Isaac_DIA <test> & more"})
    html = out.report.read_text(encoding="utf-8")
    low = html.lower()
    for external in ("src='http", 'src="http', "<link ", "@import", "url(http"):
        assert external not in low  # nothing loaded from the internet
    assert "&lt;test&gt; &amp; more" in html and "</script><script>" not in html.split("ionomos-data")[1][:50]
    data = _report_data(html)
    assert data["marker"] == "ionomos-report-v2" and len(data["samples"]) == 9
    assert [c["name"] for c in data["comps"]] == ["Drug vs DMSO", "Drug2 vs DMSO"]
    assert len(data["comps"][0]["fc"]) == len(data["f"]["id"]) == len(data["v"])
    assert data["qc"]["pca"]["scores"] and data["qc"]["correlation"]["matrix"] and data["qc"]["heatmap"]["rows"]
    for section in ("id='volcano'", "id='table'", "id='heatmap'", "id='enrich'", "id='qc'", "id='methods'"):
        assert section in html
    assert [c["name"] for c in out.summary["comparisons"]] == ["Drug vs DMSO", "Drug2 vs DMSO"]
    for svg in (tmp_path / "e/results").glob("volcano_*.svg"):
        root = ET.fromstring(svg.read_text(encoding="utf-8"))
        assert root.tag.endswith("svg") and root.find("{http://www.w3.org/2000/svg}style") is not None
    summary = json.loads((tmp_path / "e/results/analysis.json").read_text(encoding="utf-8"))
    assert summary["settings"]["test"] == "limma" and summary["features_loaded"] == 600
    results = tmp_path / "e/results"
    for name in ("protein_results.tsv", "protein_matrix_processed.tsv", "fragpipe-analyst/experiment_annotation.tsv",
                 "fragpipe-analyst/reproduce_in_R.R"):
        assert (results / name).is_file(), name


def test_analysis_without_results_explains_instead_of_crashing(tmp_path):
    (tmp_path / "e" / "fragpipe").mkdir(parents=True)
    out = downstream.analyze(tmp_path / "e", "isoDTB")
    assert out.report.is_file() and "no combined_modified_peptide_label_quant.tsv" in out.warnings[0]
    out = downstream.analyze(tmp_path / "e", None)
    assert "don't know how to analyse" in out.warnings[0]


def test_analysis_of_a_corrupt_table_is_contained(tmp_path):
    p = tmp_path / "e/fragpipe" / isodtb.LABEL_FILE
    p.parent.mkdir(parents=True)
    p.write_bytes(b"\x00\xff garbage\n\x00")
    out = downstream.analyze(tmp_path / "e", "isoDTB")
    assert out.warnings  # explained, not raised


def test_nice_ticks_always_cover_the_data():
    for lo, hi in ((0, 473), (-3.2, 3.2), (0.91, 0.999), (0, 1e-3), (17, 17)):
        t = charts.nice_ticks(lo, hi)
        assert t[0] <= lo + 1e-9 and t[-1] >= hi - 1e-9 and len(t) >= 2


def test_header_reader():
    assert read_header(GOLD / "tmt_abundance_gene_MD.tsv")[:2] == ["Index", "NumberPSM"]


def test_dia_converted_names_and_nan_retain_six_runs(tmp_path):
    prefix = 'Example_Project_Drug-10uM'
    stems = [f'{prefix}_DMSO_1_20260101120000', f'{prefix}_DMSO_2_20260101130000',
             f'{prefix}_DMSO_3', *(f'{prefix}_Drug_{r}' for r in (1, 2, 3))]
    runs = [(f'C:\\data\\{stem}_uncalibrated.mzML', 'DMSO' if i < 3 else 'Drug')
            for i, stem in enumerate(stems)]
    dest = tmp_path / 'experiment'
    pg = dest / 'fragpipe/dia-quant-output/report.pg_matrix.tsv'
    simulate.dia_pg_matrix(pg, runs, seed=21)
    # DIA-NN can represent missing intensities with NaN rather than NA.
    pg.write_text(pg.read_text().replace('\tNA', '\tNaN'))
    record = {'plan': {'manifest': [
        {'file': f'C:\\data\\{stem}.raw', 'experiment': c, 'bioreplicate': i % 3 + 1}
        for i, (stem, (_, c)) in enumerate(zip(stems, runs, strict=True))]}}
    out = downstream.analyze(dest, 'DIA', record=record)
    assert not out.warnings
    assert out.summary['samples'] == {f'{c}_{r}': c for c in ('DMSO', 'Drug') for r in (1, 2, 3)}
    assert len(out.summary['comparisons']) == 1
    assert out.summary['comparisons'][0]['name'] == 'Drug vs DMSO'
    assert out.summary['comparisons'][0]['tested'] > 0


def test_dia_missing_controls_warns_in_report(tmp_path):
    dest = tmp_path / 'e'
    runs = [(f'{c}_{r}_uncalibrated.mzML', c) for c, reps in [('DMSO', [3]), ('Drug', [1, 2, 3])]
            for r in reps]
    simulate.dia_pg_matrix(dest / 'fragpipe/report.pg_matrix.tsv', runs, seed=21)
    record = {'plan': {'manifest': [{'file': f'{c}_{r}.raw', 'experiment': c, 'bioreplicate': r}
                                   for c in ('DMSO', 'Drug') for r in (1, 2, 3)]}}
    out = downstream.analyze(dest, 'DIA', record=record)
    assert any('Expected runs missing' in w and 'DMSO_1' in w and 'DMSO_2' in w for w in out.warnings)
    assert any('DMSO has 1 sample' in w for w in out.warnings)
    assert any('zero features' in w for w in out.warnings)
    assert 'zero features' in out.report.read_text()


def test_dia_nan_does_not_drop_unmapped_column(tmp_path):
    pg = tmp_path / 'report.pg_matrix.tsv'
    pg.write_text('Protein.Group\tDMSO_1_uncalibrated\tDMSO_2_uncalibrated\nP1\tNaN\t10\nP2\t20\t30\n')
    m = quant.from_pg_matrix(pg)
    assert len(m.samples) == 2 and m.values[0] == [None, math.log2(10)]
    assert set(m.condition.values()) == {'DMSO'}


def test_reanalysis_honors_updated_file_labels(tmp_path):
    import json

    from ionomos import names, postprocess
    from ionomos.manifest import FileOverride, Overrides, save_overrides

    dest = tmp_path / 'e'
    runs = [(f'{c}_{r}_uncalibrated.mzML', c) for c in ('DMSO', 'Drug') for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / 'fragpipe/report.pg_matrix.tsv', runs, seed=21)
    record = {'plan': {'folder': {'method': 'DIA'}, 'manifest': [
        {'file': f'{c}_{r}.raw', 'experiment': f'old_{c}', 'bioreplicate': r}
        for c in ('DMSO', 'Drug') for r in (1, 2, 3)]}}
    (dest / names.STATUS_FILE).write_text(json.dumps(record))
    save_overrides(dest, Overrides(files={f'{c}_{r}.raw': FileOverride(c, r, -1)
                                         for c in ('DMSO', 'Drug') for r in (1, 2, 3)}))
    out = postprocess.run_for_folder(dest, None)
    assert out.summary['comparisons'][0]['name'] == 'Drug vs DMSO'
    assert out.summary['comparisons'][0]['tested'] > 0
    assert json.loads((dest / names.STATUS_FILE).read_text()) == record
