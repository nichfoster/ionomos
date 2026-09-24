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

    def flaky(p, comps, s):
        if s.test == "limma":
            raise ValueError("simulated limma failure")
        return real(p, comps, s)

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
