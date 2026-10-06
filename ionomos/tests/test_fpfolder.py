"""fpfolder.py (D80): a folder of FragPipe output checked before it is analysed — searches run outside Ionomos,
folders filed as the wrong method, outputs nested or several in one folder, failed searches, raw files only."""
import json
import os

import pytest

from ionomos import dragdrop, fpfolder, postprocess
from ionomos.cli import main
from ionomos.downstream import simulate

RUNS = [(f"D:/raw/HeLa_{c}_{r}.raw", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)]
DONE = "FragPipe v23.1\nProcess 'DIA-NN' finished, exit code: 0\n=====ALL JOBS DONE IN 12.3 MINUTES=====\n"


def dia_output(out, runs=RUNS, named=True, log=DONE, workflow="diann.run-dia-nn=true\nworkflow.saved-with-ver=23.1\n"):
    """A DIA search as FragPipe 23 writes it: workflow, manifest, log, dia-quant-output/report.pg_matrix.tsv."""
    simulate.dia_pg_matrix(out / "dia-quant-output" / "report.pg_matrix.tsv", runs, seed=2, n_proteins=200)
    (out / "fragpipe.workflow").write_text(workflow, encoding="utf-8")
    (out / "fragpipe-files.fp-manifest").write_text(
        "".join(f"{p}\t{c if named else ''}\t{(i % 3) + 1 if named else ''}\tDIA\n" for i, (p, c) in enumerate(runs)),
        encoding="utf-8")
    if log is not None:
        (out / "log_2026-10-01_12-00-00.txt").write_text(log, encoding="utf-8")
    return out


def codes(sc):
    return {f.code: f.severity for f in sc.findings}


def test_a_dia_search_filed_as_tmt_is_analysed_as_dia(tmp_path):
    """The case from the lab: someone ran DIA in FragPipe themselves; the folder had been filed as TMT, so the
    analysis looked for TMT-Integrator's table and found nothing."""
    exp = tmp_path / "Ana" / "HeLa_DIA"
    dia_output(exp / "fp_out")
    (exp / "ionomos.json").write_text(json.dumps({"plan": {"folder": {"method": "TMT"}}}), encoding="utf-8")
    sc = postprocess.check_folder(exp)
    assert sc.filed == "TMT" and sc.method == "DIA" and sc.recommended == "DIA"
    assert codes(sc) == {"FILED_AS_OTHER": "input"} and not sc.blocking and sc.needs_a_look
    f = sc.findings[0]
    assert "workflow ran DIA" in f.message and "report.pg_matrix.tsv" in f.message
    assert f.actions == [("Analyse as DIA", "method:DIA")]
    assert sc.dest == exp and sc.workdir == exp / "fp_out"  # results/ beside the raw files' folder
    # the analysis itself, with no window: the method is corrected and the manifest names the samples
    info = postprocess.inspect_folder(exp, None)
    assert info["method"] == "DIA" and info["workdir"] == exp / "fp_out"
    assert "filed as TMT" in info["notes"][0]
    assert {(s["sample"], s["condition"]) for s in info["samples"]} == {
        (f"{c}_{r}", c) for c in ("DMSO", "Drug") for r in (1, 2, 3)}
    out = postprocess.run_for_folder(exp, None)
    assert out.method == "DIA" and out.report is not None and out.report.is_file()
    assert any("filed as TMT" in w for w in out.warnings)
    assert out.report.parent == exp / "results"


def test_an_explicit_method_is_kept(tmp_path):
    """`ionomos analyze --method TMT` on that folder reads TMT, as asked (strict)."""
    exp = tmp_path / "x"
    dia_output(exp / "fp_out")
    assert postprocess.prepare(exp, None, "TMT", strict=True)["method"] == "TMT"
    assert postprocess.prepare(exp, None, "TMT")["method"] == "DIA"


def test_a_failed_search_says_which_step_and_why(tmp_path):
    out = tmp_path / "failed"
    out.mkdir()
    log = ("FragPipe v23.1\njava.lang.OutOfMemoryError: Java heap space\nProcess 'DIA-NN' finished, exit code: 1\n"
           "Process returned non-zero exit code, stopping\nCancelling 2 remaining tasks\n")
    (out / "fragpipe.workflow").write_text("diann.run-dia-nn=true\n", encoding="utf-8")
    (out / "fragpipe-files.fp-manifest").write_text("C:/r/a_1.raw\t\t\tDIA\n", encoding="utf-8")
    (out / "log_2026.txt").write_text(log, encoding="utf-8")
    sc = fpfolder.scan(out)
    assert sc.blocking and sc.method is None
    assert codes(sc) == {"STEP_MISSING": "error"}  # one problem, not the same one twice
    f = sc.findings[0]
    assert "DIA-NN" in f.title and "failed (exit code 1)" in f.message and "out of memory" in f.message.lower()
    assert f.actions == [("Open FragPipe's log", f"open:{out / 'log_2026.txt'}")]


def test_raw_files_only_and_an_empty_folder(tmp_path):
    raws = tmp_path / "raws"
    raws.mkdir()
    (raws / "HeLa_1.raw").write_bytes(b"x")
    sc = fpfolder.scan(raws)
    assert codes(sc) == {"RAW_ONLY": "error"} and "inbox" in sc.findings[0].fix
    empty = tmp_path / "empty"
    empty.mkdir()
    sc = fpfolder.scan(empty)
    assert codes(sc) == {"NO_OUTPUT": "error"} and sc.blocking
    assert ("Choose another folder…", "choose") in sc.findings[0].actions
    assert codes(fpfolder.scan(tmp_path / "gone")) == {"NOT_FOUND": "error"}


def test_several_outputs_the_newest_is_chosen_and_another_can_be_picked(tmp_path):
    top = tmp_path / "project"
    old = dia_output(top / "run1")
    new = dia_output(top / "run2")
    t = old.stat().st_mtime - 3600
    for p in old.rglob("*"):
        os.utime(p, (t, t))
    sc = fpfolder.scan(top)
    assert sc.chosen.root == new and sc.dest == new and codes(sc)["SEVERAL_OUTPUTS"] == "input"
    assert (f"Use run1", f"root:{old}") in sc.findings[0].actions  # noqa: F541
    sc = fpfolder.scan(top, root=old)
    assert sc.chosen.root == old and sc.dest == old and sc.method == "DIA"
    # re-running on the chosen output finds it alone
    assert fpfolder.locate(old)[0] == old


def test_a_part_of_the_output_means_the_whole_output(tmp_path):
    out = dia_output(tmp_path / "fp")
    sc = fpfolder.scan(out / "dia-quant-output")
    assert sc.method == "DIA" and sc.workdir == out and sc.dest == out and "PARENT_USED" in codes(sc)
    sc = fpfolder.scan(out / "dia-quant-output" / "report.pg_matrix.tsv")  # a dropped table of the output
    assert sc.method == "DIA" and sc.workdir == out


def test_an_ionomos_job_folder_is_left_as_it_was(tmp_path):
    dest = tmp_path / "General" / "Chris" / "dia"
    dia_output(dest / "fragpipe", log=None)
    run = dest / "ionomos_run"
    run.mkdir()
    (run / "fragpipe_console.log").write_text(DONE, encoding="utf-8")
    (dest / "ionomos.json").write_text(json.dumps({"plan": {"folder": {"method": "DIA"}}}), encoding="utf-8")
    sc = postprocess.check_folder(dest, filed="DIA")
    assert sc.method == "DIA" and sc.dest == dest and sc.workdir == dest / "fragpipe"
    assert not sc.needs_a_look and ("Log", "fragpipe_console.log: finished (all steps done in 12.3 min)") in sc.found
    assert fpfolder.locate(dest)[0] == dest / "fragpipe"


def test_conditions_from_the_file_names_when_fragpipe_was_given_none(tmp_path):
    out = dia_output(tmp_path / "fp", named=False)
    sc = fpfolder.scan(out)
    assert codes(sc) == {"NO_CONDITIONS": "input"} and sc.findings[0].actions == [("Use these conditions", "guess")]
    assert set(sc.guess.values()) == {"DMSO", "Drug"} and "DMSO (3), Drug (3)" in sc.findings[0].message
    # one run per experiment: the replicates were given as experiments
    one_each = [(p, p.rsplit("/", 1)[1][:-4]) for p, _c in RUNS]
    sc = fpfolder.scan(dia_output(tmp_path / "fp2", runs=one_each))
    assert codes(sc) == {"EXPERIMENT_PER_RUN": "input"} and set(sc.guess.values()) == {"DMSO", "Drug"}


def test_tmt_normalised_otherwise_is_read(tmp_path):
    """TMT-Integrator writes abundance_gene_None.tsv when median centring is off: it is read, with a note."""
    out = tmp_path / "tmt"
    samples = [f"{c}_{r}_{ch}" for (c, r), ch in zip([(c, r) for c in ("DMSO", "Drug") for r in (1, 2, 3)],
                                                      ("126", "127N", "127C", "128N", "128C", "129N"), strict=True)]
    simulate.tmt_abundance(out / "tmt-report" / "abundance_gene_None.tsv", samples, seed=4, n_genes=150)
    (out / "fragpipe.workflow").write_text("tmtintegrator.run-tmtintegrator=true\n", encoding="utf-8")
    sc = fpfolder.scan(out)
    assert sc.method == "TMT" and not sc.needs_a_look
    res = postprocess.run_for_folder(out, None)
    assert res.method == "TMT" and res.report is not None
    assert any("abundance_gene_None.tsv" in w for w in res.warnings)


def test_spectral_counts_only_are_not_label_free_quantities(tmp_path):
    out = tmp_path / "dda"
    out.mkdir()
    (out / "combined_protein.tsv").write_text("Protein ID\tGene\tA_1 Spectral Count\tB_1 Spectral Count\n"
                                              "P1\tG1\t3\t4\n", encoding="utf-8")
    (out / "fragpipe.workflow").write_text("quantitation.run-label-free-quant=false\n", encoding="utf-8")
    sc = fpfolder.scan(out)
    assert sc.blocking and "LFQ" not in sc.choices
    assert "no Intensity columns" in next(f.message for f in sc.findings if f.code == "TABLE_BROKEN_LFQ")


def test_a_chosen_kind_with_no_table_is_refused(tmp_path):
    out = dia_output(tmp_path / "fp")
    sc = fpfolder.scan(out, method="TMT")
    assert sc.blocking and codes(sc)["METHOD_UNREADABLE"] == "error"
    assert ("Use DIA (recommended)", "method:DIA") in sc.findings[0].actions


@pytest.mark.parametrize("props,kind", [
    ({"tmtintegrator.run-tmtintegrator": "true", "diann.run-dia-nn": "false"}, "TMT"),
    ({"diann.run-dia-nn": "true"}, "DIA"),
    ({"quantitation.run-label-free-quant": "true", "ionquant.use-labeling": "true"}, "isoDTB"),
    ({"quantitation.run-label-free-quant": "true", "ionquant.use-labeling": "false"}, "LFQ"),
    ({"ionquant.run-ionquant": "true"}, None),  # true in workflows that never run it (fragpipe.workflow_needs)
    ({}, None),
])
def test_workflow_kind(props, kind):
    assert fpfolder.workflow_kind(props) == kind


def test_manifest_lines():
    R = fpfolder.Run
    assert fpfolder.manifest_lines([R("a/DMSO_1.raw", "DMSO", "1"), R("a/DMSO_2.raw", "DMSO", "2")]) == [
        {"file": "a/DMSO_1.raw", "experiment": "DMSO", "bioreplicate": 1},
        {"file": "a/DMSO_2.raw", "experiment": "DMSO", "bioreplicate": 2}]
    # "DMSO_2" with no bioreplicate: condition and replicate from the name; no number at all: counted
    assert [(x["experiment"], x["bioreplicate"]) for x in fpfolder.manifest_lines(
        [R("x.raw", "DMSO_2"), R("y.raw", "Drug"), R("z.raw", "Drug")])] == [("DMSO", 2), ("Drug", 1), ("Drug", 2)]
    assert fpfolder.manifest_lines([R("x.raw"), R("y.raw", "DMSO", "1")]) == []  # not every run named


def test_check_folder_command(tmp_path, capsys):
    out = dia_output(tmp_path / "fp")
    assert main(["--config", str(tmp_path / "none.yaml"), "check-folder", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Analyse as: DIA" in text and "ready: ionomos analyze" in text
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["--config", str(tmp_path / "none.yaml"), "check-folder", str(empty)]) == 1
    assert "No FragPipe results" in capsys.readouterr().out


def test_drops(monkeypatch):
    assert dragdrop.paths_from(['"C:\\a b\\x"', "", "C:\\a b\\x", "D:\\y"]) == ["C:\\a b\\x", "D:\\y"]

    class _Posix:
        name = "posix"

    monkeypatch.setattr(dragdrop, "os", _Posix)
    assert dragdrop.enable(object(), lambda paths: None) is False
