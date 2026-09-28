"""The analysis doctor and the guarantees around it: every problem becomes an issue with causes
and fixes, one failing stage never takes the volcano plots or the report down."""
from pathlib import Path

import pytest

from ionomos import downstream
from ionomos.downstream import doctor, simulate
from ionomos.downstream.tables import read_tsv


def _dia(dest: Path, conds=("DMSO", "Drug"), reps=(1, 2, 3), seed=4, names=None, record=True, n=400):
    runs = names or [(f"/x/{c}_{r}.raw", c) for c in conds for r in reps]
    simulate.dia_pg_matrix(dest / "fragpipe/report.pg_matrix.tsv", runs, seed=seed, n_proteins=n)
    if not record:
        return None
    return {"plan": {"manifest": [{"file": f"{c}_{r}.raw", "experiment": c, "bioreplicate": r}
                                  for c in conds for r in reps]}}


def _codes(out) -> dict:
    return {i.code: i for i in out.issues}


def _volcanos_ok(dest: Path, out) -> None:
    assert out.summary["comparisons"], "no comparison"
    for c in out.summary["comparisons"]:
        p = dest / c["volcano"]
        assert p.is_file() and p.stat().st_size > 200, c


def test_clean_experiment_has_no_popup_issues(tmp_path):
    rec = _dia(tmp_path / "e")
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    assert not doctor.popups(out.issues) and out.summary["state"] == "ok"
    _volcanos_ok(tmp_path / "e", out)


def test_one_condition_asks_for_conditions_with_a_suggestion(tmp_path):
    # the condition sits in the middle of the name, the part Ionomos reads is the same for all
    names = [(f"/x/CS_22rv1_{c}_{r}.raw", "CS") for c in ("DMSO", "MA25") for r in (1, 2, 3)]
    _dia(tmp_path / "e", names=names, record=False)
    rec = {"plan": {"manifest": [{"file": f"CS_22rv1_{c}_{r}.raw", "experiment": "CS_22rv1", "bioreplicate": k}
                                 for k, (c, r) in enumerate(((c, r) for c in ("DMSO", "MA25") for r in (1, 2, 3)), 1)]}}
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    i = _codes(out)["ONE_CONDITION"]
    assert i.severity == "input" and out.summary["state"] == "needs_input"
    sug = i.data["suggested"]
    assert {sug[s] for s in i.data["samples"]} == {"DMSO", "MA25"}
    # the user's answer (sample_conditions) gives a real comparison and volcano
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, {"sample_conditions": sug}, record=rec)
    assert "ONE_CONDITION" not in _codes(out) and out.summary["comparisons"][0]["name"] == "MA25 vs DMSO"
    _volcanos_ok(tmp_path / "e", out)


def test_unrecognised_control_still_makes_volcanos_and_asks(tmp_path):
    rec = _dia(tmp_path / "e", conds=("Alpha", "Beta", "Gamma"))
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    i = _codes(out)["NO_CONTROL"]
    assert i.data["guessed"] == "Alpha" and set(i.data["conditions"]) == {"Alpha", "Beta", "Gamma"}
    _volcanos_ok(tmp_path / "e", out)
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, {"control": "Beta"}, record=rec)
    assert "NO_CONTROL" not in _codes(out)
    assert [c["name"] for c in out.summary["comparisons"]] == ["Alpha vs Beta", "Gamma vs Beta"]


def test_bad_explicit_comparisons_fall_back_to_defaults(tmp_path):
    rec = _dia(tmp_path / "e")
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, {"comparisons": ["Drug vs Placebo"]},
                             record=rec)
    assert _codes(out)["BAD_COMPARISON"].severity == "input"
    assert [c["name"] for c in out.summary["comparisons"]] == ["Drug vs DMSO"]
    _volcanos_ok(tmp_path / "e", out)


def test_a_failed_injection_is_flagged(tmp_path):
    dest = tmp_path / "e"
    rec = _dia(dest)
    pg = dest / "fragpipe/report.pg_matrix.tsv"
    header, rows = read_tsv(pg)
    # DMSO_3 lost most identifications (a failed injection)
    col = "/x/DMSO_3.raw"
    lines = ["\t".join(header)]
    for k, r in enumerate(rows):
        if k % 5:
            r[col] = ""
        lines.append("\t".join(r[h] for h in header))
    pg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rec["plan"]["manifest"].append({"file": "DMSO_1_20260508180610.raw", "experiment": "DMSO", "bioreplicate": 1})
    out = downstream.analyze(dest, "DIA", {"enrichment": False}, record=rec)
    low = _codes(out)["LOW_SAMPLE"]
    assert low.data["samples"] == ["DMSO_3"] and low.severity == "input"
    out = downstream.analyze(dest, "DIA", {"enrichment": False}, {"exclude_samples": ["DMSO_3"]}, record=rec)
    assert "LOW_SAMPLE" not in _codes(out)


def test_no_result_table_explains_by_method(tmp_path):
    (tmp_path / "e/fragpipe/some-step").mkdir(parents=True)
    (tmp_path / "e/fragpipe/some-step/psm.tsv").write_text("a\n", encoding="utf-8")
    out = downstream.analyze(tmp_path / "e", "DIA")
    i = _codes(out)["NO_TABLE"]
    assert i.severity == "error" and "DIA-NN" in " ".join(i.causes)
    assert i.data["tables_found"] == ["some-step/psm.tsv"]
    assert out.report.is_file() and "No result table to analyse" in out.report.read_text(encoding="utf-8")


def test_a_crashing_qc_step_leaves_volcanos_and_report(tmp_path, monkeypatch):
    rec = _dia(tmp_path / "e")

    def boom(*a, **k):
        raise ZeroDivisionError("simulated")

    monkeypatch.setattr(downstream, "_qc", boom)
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    i = _codes(out)["CRASH_QC"]
    assert i.severity == "warning" and "simulated" in i.message
    _volcanos_ok(tmp_path / "e", out)
    assert "ionomos-report-v2" in out.report.read_text(encoding="utf-8")
    assert "ZeroDivisionError" in (tmp_path / "e/results/analysis_error.txt").read_text(encoding="utf-8")


def test_a_crashing_report_gets_the_fallback_page(tmp_path, monkeypatch):
    from ionomos.downstream import report

    rec = _dia(tmp_path / "e")
    monkeypatch.setattr(report, "render", lambda *a, **k: 1 / 0)
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    html = out.report.read_text(encoding="utf-8")
    assert "simplified report" in html and "<svg" in html and "The report step failed" in html
    assert _codes(out)["CRASH_REPORT"].severity == "error"


def test_limma_failure_falls_back_to_welch(tmp_path, monkeypatch):
    from ionomos.downstream import analysis

    rec = _dia(tmp_path / "e")
    real = analysis.run_contrasts

    def flaky(p, comps, s, low=frozenset()):
        if s.test == "limma":
            raise ValueError("simulated limma failure")
        return real(p, comps, s, low)

    monkeypatch.setattr(analysis, "run_contrasts", flaky)
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    assert any("Welch" in w for w in out.warnings) and out.summary["comparisons"][0]["tested"] > 0
    _volcanos_ok(tmp_path / "e", out)


def test_unwritable_volcano_is_an_error(tmp_path, monkeypatch):
    from ionomos.downstream import charts

    rec = _dia(tmp_path / "e")
    monkeypatch.setattr(charts, "volcano", lambda *a, **k: "")
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    assert _codes(out)["NO_VOLCANO"].severity == "error"


@pytest.mark.parametrize(("names", "want"), [
    (["CS_22rv1_MA25_DMSO_1", "CS_22rv1_MA25_DMSO_2", "CS_22rv1_MA25_Drug_1"], ["DMSO", "DMSO", "Drug"]),
    (["DMSO_1", "DMSO_2_20260508204737", "MA25-3"], ["DMSO", "DMSO", "MA25"]),
    (["A_rep1", "B_rep2"], ["A", "B"]),
    (["DMSO_1.2", "DMSO_1"], ["DMSO", "DMSO"]),
])
def test_suggest_conditions(names, want):
    got = doctor.suggest_conditions(names)
    assert [got[n] for n in names] == want


# ---- inputs that used to come back "ok" (or as a crash) with nothing to show — 2026-09-27 sweep


def _rewrite(path: Path, fn) -> None:
    header, rows = read_tsv(path)
    header, rows = fn(header, rows)
    path.write_text("\n".join(["\t".join(header)] + ["\t".join(r.get(h, "") for h in header) for r in rows]) + "\n",
                    encoding="utf-8")


def test_all_blank_quantities_is_an_error_not_ok(tmp_path):
    rec = _dia(tmp_path / "e")
    pg = tmp_path / "e/fragpipe/report.pg_matrix.tsv"
    runs = [h for h in read_tsv(pg)[0] if h.endswith(".raw")]
    _rewrite(pg, lambda h, rows: (h, [{**r, **dict.fromkeys(runs, "")} for r in rows]))
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    assert out.summary["state"] == "failed" and "NO_QUANTITIES" in _codes(out)


def test_single_sample_says_so(tmp_path):
    _dia(tmp_path / "e", conds=("DMSO",), reps=(1,), record=False)
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False})
    assert out.summary["state"] == "failed" and "ONE_SAMPLE" in _codes(out)


def test_excluding_every_sample_is_reported(tmp_path):
    rec = _dia(tmp_path / "e")
    everyone = [f"{c}_{r}" for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, {"exclude_samples": everyone}, record=rec)
    assert out.summary["state"] == "failed" and "NOTHING_LEFT" in _codes(out)


def test_isodtb_table_without_probe_is_a_data_problem_not_a_crash(tmp_path):
    lq = tmp_path / "e/fragpipe/combined_modified_peptide_label_quant.tsv"
    simulate.isodtb_label_quant(lq, {"EJQ_2_027": [1, 2, 3]})
    lq.write_text(lq.read_text(encoding="utf-8").replace("[561.3387]", "[57.0215]"), encoding="utf-8")
    out = downstream.analyze(tmp_path / "e", "isoDTB", {"enrichment": False})
    codes = _codes(out)
    assert "UNUSABLE_TABLE" in codes and "CRASH_READ" not in codes
    assert "561.3387" in codes["UNUSABLE_TABLE"].message
    assert not (tmp_path / "e/results/analysis_error.txt").exists()


def test_isodtb_single_replicate_is_a_labelled_fold_change_plot(tmp_path):
    simulate.isodtb_label_quant(tmp_path / "e/fragpipe/combined_modified_peptide_label_quant.tsv", {"EJQ_2_027": [1]})
    out = downstream.analyze(tmp_path / "e", "isoDTB", {"enrichment": False})
    comp = out.summary["comparisons"][0]
    assert comp["confidence"] == "none" and comp["tested"] == 0 and comp["up"] > 0
    assert "FOLD_CHANGE_ONLY" in _codes(out) and "SMALL_GROUP" not in _codes(out) and "ONE_SAMPLE" not in _codes(out)
    assert out.summary["state"] == "ok"
    svg = (tmp_path / "e" / comp["volcano"]).read_text(encoding="utf-8")
    assert "FOLD CHANGE ONLY" in svg and "rank (sorted by log2 H/L)" in svg


def test_a_few_junk_cells_do_not_drop_a_run(tmp_path):
    # without a manifest, run columns are recognised by holding numbers; one bad cell used to drop the run
    _dia(tmp_path / "e", record=False)
    pg = tmp_path / "e/fragpipe/report.pg_matrix.tsv"
    first_run = next(h for h in read_tsv(pg)[0] if h.endswith(".raw"))
    _rewrite(pg, lambda h, rows: (h, [{**r, first_run: "abc"} if k == 0 else r for k, r in enumerate(rows)]))
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False})
    assert len(out.summary["samples"]) == 6
    assert any("nonnumeric" in n for n in out.warnings)


def test_one_bad_setting_keeps_the_others(tmp_path):
    rec = _dia(tmp_path / "e")
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False},
                             {"min_valid": 1, "log2fc": 0.5, "test": "welch"}, record=rec)
    s = out.summary["settings"]
    assert s["min_valid"] == 2 and s["log2fc"] == 0.5 and s["test"] == "welch"
    assert any("'min_valid' ignored" in n and "log2fc" not in n for n in out.warnings)


def test_real_dia_nn_uncalibrated_names_group_by_condition(tmp_path):
    # Chris's 2026-09 22Rv1 run (Ionomos 0.5.3 made every run its own condition): DIA-NN column headers are
    # the converted *_uncalibrated.mzML paths; one DMSO run against three MA25 runs.
    stems = ["CS_22rv1_FLAG-AR_MA25-10uM_DMSO_3", "CS_22rv1_FLAG-AR_MA25-10uM_MA25_1",
             "CS_22rv1_FLAG-AR_MA25-10uM_MA25_2", "CS_22rv1_FLAG-AR_MA25-10uM_MA25_3"]
    base = "C:\\Fragpipe_Auto_Users\\Chris\\CS_22rv1_FLAG_AR_MA25\\fragpipe\\"
    names = [(f"{base}{s}_uncalibrated.mzML", s.rsplit("_", 1)[0]) for s in stems]
    simulate.dia_pg_matrix(tmp_path / "e/fragpipe/dia-quant-output/report.pg_matrix.tsv", names, seed=4, n_proteins=400)
    for rec in (None, {"plan": {"manifest": [{"file": f"{s}.raw", "experiment": s.rsplit("_", 1)[0],
                                              "bioreplicate": int(s.rsplit("_", 1)[1])} for s in stems]}}):
        out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
        assert set(out.summary["samples"].values()) == {"CS_22rv1_FLAG-AR_MA25-10uM_DMSO",
                                                        "CS_22rv1_FLAG-AR_MA25-10uM_MA25"}
        assert [c["name"] for c in out.summary["comparisons"]] == [
            "CS_22rv1_FLAG-AR_MA25-10uM_MA25 vs CS_22rv1_FLAG-AR_MA25-10uM_DMSO"]
        # one DMSO replicate: tested with the MA25 replicates' spread, labelled low confidence
        comp = out.summary["comparisons"][0]
        assert comp["confidence"] == "low" and comp["tested"] > 0 and "LOW_CONFIDENCE" in _codes(out)


# ---- 1 vs N and 1 vs 1: every comparison gets a plot, labelled by what the data can support


def test_one_vs_one_alone_is_fold_change_only(tmp_path):
    _dia(tmp_path / "e", reps=(1,), record=False)
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False})
    comp = out.summary["comparisons"][0]
    assert comp["name"] == "Drug vs DMSO" and comp["confidence"] == "none"
    assert comp["tested"] == 0 and comp["up"] + comp["down"] > 0  # candidates by fold change, no p-values
    rows = read_tsv(tmp_path / "e" / comp["table"])[1]
    assert all(r["pvalue"] == "NA" for r in rows)
    assert all(abs(float(r["log2fc"])) >= 1 for r in rows if r["significant"])
    svg = (tmp_path / "e" / comp["volcano"]).read_text(encoding="utf-8")
    assert "FOLD CHANGE ONLY" in svg and "mean log2 abundance" in svg
    html = out.report.read_text(encoding="utf-8")
    assert '"conf":"none"' in html


def test_one_vs_one_borrows_variance_from_a_replicated_condition(tmp_path):
    names = [("/x/DMSO_1.raw", "DMSO"), ("/x/DrugA_1.raw", "DrugA")] + [(f"/x/DrugB_{r}.raw", "DrugB") for r in (1, 2, 3)]
    _dia(tmp_path / "e", names=names, record=False)
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False})
    by = {c["name"]: c for c in out.summary["comparisons"]}
    assert by["DrugA vs DMSO"]["confidence"] == "low" and by["DrugA vs DMSO"]["tested"] > 0
    assert by["DrugB vs DMSO"]["confidence"] == "low"


def test_welch_with_a_group_of_one_uses_a_pooled_t_test(tmp_path):
    names = [("/x/DMSO_1.raw", "DMSO")] + [(f"/x/Drug_{r}.raw", "Drug") for r in (1, 2, 3)]
    _dia(tmp_path / "e", names=names, record=False)
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, {"test": "welch"})
    comp = out.summary["comparisons"][0]
    assert comp["confidence"] == "low" and comp["tested"] > 0


def test_replicated_comparisons_are_unlabelled(tmp_path):
    rec = _dia(tmp_path / "e")
    out = downstream.analyze(tmp_path / "e", "DIA", {"enrichment": False}, record=rec)
    assert out.summary["comparisons"][0]["confidence"] == "normal"
    assert "LOW CONFIDENCE" not in (tmp_path / "e" / out.summary["comparisons"][0]["volcano"]).read_text(encoding="utf-8")
