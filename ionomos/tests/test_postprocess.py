"""Postprocess orchestration edge cases (audit W4): the disabled-analysis branch of run_all,
prepare()'s recovery from a broken experiment.yaml, the Analysis tab's inspect_folder, and the
analysis attention items — plus the two knobs that flow through it (the isoDTB mod mass and the
TMT annotation).

test_downstream.py owns the analysis stages themselves (R ports, statistics, loaders); this file
pins the glue in postprocess.py and the load_quantities behaviours that surface through it.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ionomos import attention, config, downstream, names, postprocess
from ionomos.downstream import doctor, isodtb, simulate
from ionomos.downstream.tables import read_tsv


def _cfg(lab, tmp_path: Path, analysis: dict) -> config.Config:
    """The lab config with a patched analysis: section (house pattern from test_downstream.py)."""
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(dict(lab["cfg_dict"], analysis=analysis)), encoding="utf-8")
    return config.load(p, check_paths=False)


def _dia_folder(tmp_path: Path) -> Path:
    """A filed DIA experiment folder: fake FragPipe output, a status record, no results yet."""
    dest = tmp_path / "20260914_EJQ_DIA"
    runs = [(f"{c}_{r}_uncalibrated.mzML", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    simulate.dia_pg_matrix(dest / "fragpipe" / "report.pg_matrix.tsv", runs, seed=21)
    record = {"job_id": 9, "plan": {"folder": {"method": "DIA"}, "manifest": [
        {"file": f"{c}_{r}.raw", "experiment": c, "bioreplicate": r} for c in ("DMSO", "Drug") for r in (1, 2, 3)]}}
    (dest / names.STATUS_FILE).write_text(json.dumps(record), encoding="utf-8")
    return dest


# ------------------------------------------------- run_all with analysis disabled --


def test_run_all_with_analysis_disabled_does_r_port_prep_only(tmp_path, lab):
    """analysis.enabled: false — the R-port prep still runs, no report is made, and the summary
    carries the written files but no report path."""
    dest = tmp_path / "20260914_EJQ_isoDTB"
    simulate.isodtb_label_quant(dest / "fragpipe" / isodtb.LABEL_FILE, {"EJQ_2_027": [1, 2, 3]}, seed=8)
    job = SimpleNamespace(id=7, method="isoDTB", dest_dir=dest)

    warnings, summary = postprocess.run_all(job, None, _cfg(lab, tmp_path, {"enabled": False}))

    assert warnings == []
    assert summary == {"files": ["results/EJQ_2_027_sites.tsv"]}
    assert (dest / "results" / "EJQ_2_027_sites.tsv").is_file()  # the R-port table was written
    assert not (dest / "results" / "report.html").exists()  # statistics and the report skipped
    assert "report" not in summary


def test_run_all_with_analysis_disabled_never_fails_the_job(tmp_path, lab):
    """Even when the R-port prep cannot run at all (results/ exists as a file), the disabled
    branch returns a warning and an empty summary instead of raising."""
    dest = tmp_path / "20260914_EJQ_isoDTB"
    (dest / "fragpipe").mkdir(parents=True)
    (dest / "results").write_text("not a folder")  # load_quantities cannot mkdir results/
    job = SimpleNamespace(id=7, method="isoDTB", dest_dir=dest)

    warnings, summary = postprocess.run_all(job, None, _cfg(lab, tmp_path, {"enabled": False}))

    assert len(warnings) == 1 and warnings[0].startswith("post-processing failed: ")
    assert summary == {}


# ------------------------------------------------- prepare vs a broken experiment.yaml --


def test_prepare_with_broken_experiment_yaml_proceeds_from_the_record(tmp_path):
    # CHARACTERIZATION: the experiment.yaml corrections block in prepare() is wrapped in a bare
    # `except Exception: pass` (postprocess.py:55-56), so an experiment.yaml that breaks after
    # intake is silently ignored and the analysis proceeds from the recorded manifest, with the
    # record's own analysis overrides. Pinned deliberately — see umbrella issue #20 (the minor
    # silence cluster). Invert or delete when the silence is fixed.
    dest = tmp_path / "20260902_EJQ_isoDTB"
    dest.mkdir()
    record = {"job_id": 5, "plan": {"folder": {"method": "isoDTB", "original": "20260902_EJQ_isoDTB",
                                               "user": "EJQ", "date": "2026-09-02"},
                                    "manifest": [{"file": "EJQ_x_1.raw", "experiment": "EJQ_old",
                                                  "bioreplicate": 1}],
                                    "overrides": {"analysis": {"control": "DMSO"}}}}
    (dest / names.STATUS_FILE).write_text(json.dumps(record), encoding="utf-8")
    (dest / "experiment.yaml").write_text('files: "not a mapping"\n', encoding="utf-8")  # parses, breaks parse_overrides

    p = postprocess.prepare(dest, None)

    assert p["method"] == "isoDTB" and p["record"] == record
    assert p["overrides"] == {"control": "DMSO"}  # the record's overrides, not the broken file's
    assert p["record"]["plan"]["manifest"][0]["experiment"] == "EJQ_old"  # no corrections applied
    assert p["context"]["experiment"] == "20260902_EJQ_isoDTB" and p["context"]["user"] == "EJQ"
    assert p["mod_mass"] == "561.3387"  # no cfg: the default


# ------------------------------------------------- inspect_folder (Analysis tab) --


def test_inspect_folder_detects_method_auto_and_maps_run_stems(tmp_path):
    """Analysis-tab pre-flight on a real folder, headless: method "auto" is detected from the
    FragPipe output, and each sample carries condition, replicate and its run stem."""
    dest = _dia_folder(tmp_path)

    info = postprocess.inspect_folder(dest, None, "auto")

    assert info["method"] == "DIA" and info["kind"] == "intensity" and info["features"] > 0
    assert info["source"].endswith("report.pg_matrix.tsv")
    assert info["notes"] == [] and info["issues"] == []  # nothing analysed yet: no analysis.json
    assert info["job_id"] == 9 and info["overrides"] == {}
    assert info["report"] == dest / "results" / "report.html"
    assert [(s["sample"], s["condition"], s["replicate"]) for s in info["samples"]] == [
        ("DMSO_1", "DMSO", 1), ("DMSO_2", "DMSO", 2), ("DMSO_3", "DMSO", 3),
        ("Drug_1", "Drug", 1), ("Drug_2", "Drug", 2), ("Drug_3", "Drug", 3)]
    # the run stems come from the pg_matrix columns (…uncalibrated.mzML), not the sample labels
    assert [(s["sample"], s["run"]) for s in info["samples"]] == [
        (f"{c}_{r}", f"{c}_{r}_uncalibrated") for c in ("DMSO", "Drug") for r in (1, 2, 3)]


def test_inspect_folder_without_a_record_or_tables(tmp_path):
    """No status record and no known result table: the method is unknown, the source/features
    empty, and even a corrupt analysis.json yields issues: [] — never a crash."""
    dest = tmp_path / "20260914_EJQ_isoDTB"
    (dest / "fragpipe").mkdir(parents=True)
    results = dest / "results"
    results.mkdir()
    (results / "analysis.json").write_text("{not json", encoding="utf-8")

    info = postprocess.inspect_folder(dest, None, None)

    assert info["method"] is None and info["source"] is None and info["features"] == 0
    assert info["kind"] is None and info["job_id"] is None and info["overrides"] == {}
    assert info["issues"] == [] and len(info["notes"]) == 1  # load_quantities explains itself


# ------------------------------------------------- record_issues (attention items) --


def test_record_issues_without_a_log_dir_is_a_no_op(tmp_path):
    out = downstream.Outcome(method="DIA", results_dir=tmp_path,
                             issues=[doctor.Issue("NO_TABLE", "error", "No result table", "message", ["c"], ["f"])])
    assert postprocess.record_issues(None, tmp_path / "missing-folder", out) is None


@pytest.mark.parametrize(("code", "severity", "kind"), [
    ("NO_TABLE", "error", "analysis_failed"),
    ("ONE_CONDITION", "input", "analysis_input"),
])
def test_record_issues_kind_follows_issue_severity(tmp_path, code, severity, kind):
    """An error issue raises analysis_failed; input-only issues raise analysis_input."""
    log_dir, dest = tmp_path / "logs", tmp_path / "20260914_EJQ_DIA"
    issue = doctor.Issue(code, severity, "The title", "The message", ["a cause"], ["a fix"])

    postprocess.record_issues(log_dir, dest, downstream.Outcome(method="DIA", results_dir=dest / "results",
                                                                issues=[issue], report=dest / "results" / "report.html"), 9)

    [item] = attention.items(log_dir)
    assert (item.kind, item.severity) == (kind, severity)
    assert item.key == f"analysis:{dest}" and item.job_id == 9 and item.dest == str(dest)
    assert item.title.startswith(f"{dest.name}: The title")
    assert item.data["method"] == "DIA" and item.data["report"].endswith("report.html")


def test_record_issues_clean_outcome_closes_the_item(tmp_path):
    """A clean analysis (warnings alone raise no pop-up) resolves the experiment's open item
    instead of raising a new one."""
    log_dir, dest = tmp_path / "logs", tmp_path / "20260914_EJQ_DIA"
    error = doctor.Issue("NO_TABLE", "error", "No result table", "message", ["c"], ["f"])
    postprocess.record_issues(log_dir, dest, downstream.Outcome(method="DIA", results_dir=dest / "results",
                                                                issues=[error]), 9)
    assert len(attention.items(log_dir)) == 1

    clean = downstream.Outcome(method="DIA", results_dir=dest / "results",
                               issues=[doctor.Issue("NO_HITS", "warning", "No significant changes", "message")])
    postprocess.record_issues(log_dir, dest, clean, 9)

    assert attention.items(log_dir) == []  # closed, not raised anew
    [it] = attention.items(log_dir, open_only=False)
    assert it.state == "resolved" and it.kind == "analysis_failed"


def test_record_issues_dedupes_causes_and_fixes_with_a_cap_of_six(tmp_path):
    log_dir, dest = tmp_path / "logs", tmp_path / "20260914_EJQ_DIA"
    issues = [doctor.Issue("A", "error", "First", "message", [f"cause {i}" for i in range(5)],
                           ["fix 1", "fix 2", "fix 3"]),
              doctor.Issue("B", "input", "Second", "message", ["cause 4", "cause 5", "cause 6", "cause 7"],
                           ["fix 3", "fix 4", "fix 5"])]

    postprocess.record_issues(log_dir, dest, downstream.Outcome(method="DIA", results_dir=dest / "results",
                                                                issues=issues), 9)

    [item] = attention.items(log_dir)
    assert item.causes == [f"cause {i}" for i in range(6)]  # deduped in order, capped at 6
    assert item.fixes == ["fix 1", "fix 2", "fix 3", "fix 4", "fix 5"]
    assert "First" in item.title and "(+1 more)" in item.title  # one pop-up, both issues summarised
    assert item.kind == "analysis_failed"  # an error among the pop-ups decides the kind


# ------------------------------------------------- context_for --


def test_context_for_falls_back_on_an_empty_record(tmp_path):
    dest = tmp_path / "20260914_EJQ_DIA"
    assert postprocess.context_for(dest, {}, "DIA") == {"experiment": "20260914_EJQ_DIA", "user": "",
                                                        "method": "DIA", "date": "", "fragpipe": ""}
    # whatever the record does say wins, field by field
    record = {"run": {"workflow_source": "C:/Fragpipe_Auto/workflows/DIA.workflow"},
              "plan": {"folder": {"original": "My Exp", "user": "EJQ", "date": "2026-09-14"}}}
    assert postprocess.context_for(dest, record, "DIA") == {
        "experiment": "My Exp", "user": "EJQ", "method": "DIA", "date": "2026-09-14",
        "fragpipe": "workflow DIA.workflow"}


# ------------------------------------------------- knobs that flow through prepare --


def test_custom_isodtb_mod_mass_flows_into_the_site_tables(tmp_path, lab):
    """A lab-configured isodtb_mod_mass (not the 561.3387 default) reaches isodtb.write_site_tables
    through prepare(): only peptides carrying that tag become sites."""
    cfg_dict = copy.deepcopy(lab["cfg_dict"])
    cfg_dict["methods"]["isoDTB"]["isodtb_mod_mass"] = "562.1000"
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_dict), encoding="utf-8")
    cfg = config.load(cfg_path, check_paths=False)

    dest = tmp_path / "20260902_EJQ_isoDTB"
    lq = dest / "fragpipe" / isodtb.LABEL_FILE
    lq.parent.mkdir(parents=True)
    lq.write_text("Peptide Sequence\tLight Modified Peptide\tStart\tProtein\tGene\t"
                  "EJQ_1 Log2 Ratio HL\tEJQ_2 Log2 Ratio HL\n"
                  "AAK\tAAK[562.1000]K\t10\tP1\tG1\t1.0\t2.0\n"
                  "AGK\tAGK[561.3387]K\t20\tP1\tG1\t3.0\t4.0\n", encoding="utf-8")

    p = postprocess.prepare(dest, cfg, "isoDTB")
    assert p["mod_mass"] == "562.1000"

    m, files, notes = downstream.load_quantities("isoDTB", dest / "fragpipe", dest / "results", None, p["mod_mass"])
    assert notes == [] and [f.name for f in files] == ["EJQ_sites.tsv"]
    _header, rows = read_tsv(files[0])
    assert [r["ResiduePositionInProtein"] for r in rows] == ["12"]  # the 562.1000-tagged site…
    default_dir = tmp_path / "default"
    default_dir.mkdir()
    # …and with the default mass the same table would have found the other site instead
    _h, default_rows = read_tsv(isodtb.write_site_tables(lq, default_dir)[0])
    assert [r["ResiduePositionInProtein"] for r in default_rows] == ["22"]
    assert len(m.features) == 1 and m.samples == ["EJQ_1", "EJQ_2"]


def test_load_quantities_notes_empty_tmt_annotation(tmp_path):
    """Sample names that don't follow condition_1_channel make the lab's annotation empty; the
    loader writes it anyway and explains that conditions were taken from the names' prefixes."""
    dest = tmp_path / "20260914_EJQ_TMT"
    simulate.tmt_abundance(dest / "fragpipe" / "tmt-report" / "abundance_gene_MD.tsv", ["Run1", "Run2", "Run3"], seed=4)

    m, files, notes = downstream.load_quantities("TMT", dest / "fragpipe", dest / "results", None)

    assert any(n.startswith("TMT sample names don't follow condition_1_channel") for n in notes)
    assert m is not None and m.samples == ["Run1", "Run2", "Run3"]
    assert m.condition == {"Run1": "Run1", "Run2": "Run2", "Run3": "Run3"}  # conditions from the prefix
    assert [f.name for f in files] == ["experimental_annotation.tsv"]
    _header, ann_rows = read_tsv(dest / "results" / "experimental_annotation.tsv")
    assert ann_rows == []  # the lab's annotation file is written, but empty
