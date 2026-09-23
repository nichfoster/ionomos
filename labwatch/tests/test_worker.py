"""FragPipe runner + worker, end to end against the testbed's fake FragPipe.

The fake (`labwatch fake-fragpipe`) checks what the real one checks — the
workflow's database.db-path exists, every manifest file exists — so a job only
reaches "done" if labwatch prepared the inputs correctly.
"""
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from labwatch import fragpipe, testbed
from labwatch.config import load
from labwatch.intake import intake
from labwatch.ledger import Ledger
from labwatch.worker import Worker


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("LABWATCH_FAKE_FP_SECONDS", "0")
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "ledger": Ledger(cfg.database)}


def _queue(bed, sample: str) -> Path:
    folder = testbed.drop(bed["root"], sample)
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def _status(dest: Path) -> dict:
    return json.loads((dest / "labwatch.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ happy path --


def test_isodtb_job_runs_to_done(bed):
    dest = _queue(bed, "iso_good")
    w = Worker(bed["cfg"], bed["ledger"])
    assert w.run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done", job.reason
    assert job.attempts == 1

    run = dest / fragpipe.RUN_DIR
    manifest = (run / fragpipe.MANIFEST_NAME).read_text(encoding="utf-8").splitlines()
    assert len(manifest) == 9
    path, exp, rep, dtype = manifest[0].split("\t")
    assert Path(path).is_file() and "\\" not in path
    assert exp == "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h" and rep == "1" and dtype == "DDA"
    wf = (run / "isoDTB.workflow").read_text(encoding="utf-8")
    fasta = bed["cfg"].fasta_dir / "human_reviewed_decoys.fas"
    assert fragpipe.workflow_db_path(wf) == str(fasta).replace("\\", "/")
    assert (dest / "fragpipe" / "combined_modified_peptide_label_quant.tsv").is_file()
    assert "FAKE FragPipe: done" in (run / fragpipe.CONSOLE_LOG).read_text(encoding="utf-8")

    st = _status(dest)
    assert st["status"] == "done" and st["run"]["exit_code"] == 0 and st["run"]["attempt"] == 1
    assert "--headless" in st["run"]["command"]
    assert (dest / "DONE.txt").is_file() and not (dest / "FAILED.txt").exists()
    assert not w.run_once()  # queue empty


def test_dia_job_uses_dia_type_and_diann_flag(bed):
    cfg = replace(bed["cfg"], config_diann="C:/DIA-NN/DiaNN.exe")
    dest = _queue(bed, "dia_good")
    w = Worker(cfg, bed["ledger"])
    w.run_once()
    assert bed["ledger"].get(1).status == "done"
    assert all(line.endswith("\tDIA") for line in
               (dest / fragpipe.RUN_DIR / fragpipe.MANIFEST_NAME).read_text(encoding="utf-8").splitlines())
    cmd = _status(dest)["run"]["command"]
    assert cmd[cmd.index("--config-diann") + 1] == "C:/DIA-NN/DiaNN.exe"
    assert (dest / "fragpipe" / "diann-output" / "report.tsv").is_file()


def test_tmt_job_writes_annotation_from_experiment_yaml(bed):
    dest = _queue(bed, "tmt_good")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    ann = list(dest.rglob("annotation.txt"))
    assert len(ann) == 1 and "126\t" in ann[0].read_text(encoding="utf-8")
    assert (dest / "fragpipe" / "tmt-report" / "abundance_gene_MD.tsv").is_file()


# ------------------------------------------------------------------- failures --


def test_fragpipe_failure_fails_job_and_retry_reruns(bed):
    dest = _queue(bed, "fp_fail")
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "exit" in job.reason and "IonQuant crashed" in job.reason
    note = (dest / "FAILED.txt").read_text(encoding="utf-8")
    assert "IonQuant crashed" in note and "labwatch retry 1" in note
    assert _status(dest)["status"] == "failed"

    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    w.run_once()
    assert bed["ledger"].get(1).status == "failed" and bed["ledger"].get(1).attempts == 1


def test_rerun_keeps_previous_output(bed):
    dest = _queue(bed, "iso_good")
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    bed["ledger"].requeue(1)
    w.run_once()
    assert bed["ledger"].get(1).status == "done"
    prev = list(dest.glob("fragpipe_previous_*"))
    assert len(prev) == 1 and (prev[0] / "combined_modified_peptide_label_quant.tsv").is_file()
    assert (dest / "fragpipe" / "combined_modified_peptide_label_quant.tsv").is_file()


def test_missing_workflow_holds_job_until_it_appears(bed):
    wf = bed["cfg"].workflow_dir / "isoDTB.workflow"
    saved = wf.read_text(encoding="utf-8")
    wf.unlink()
    dest = _queue(bed, "iso_good")
    w = Worker(bed["cfg"], bed["ledger"])
    assert not w.run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and job.reason.startswith("waiting: workflow file for isoDTB missing")
    assert _status(dest)["reason"].startswith("waiting:")

    wf.write_text(saved, encoding="utf-8")
    assert w.run_once()
    assert bed["ledger"].get(1).status == "done"


def test_held_job_does_not_block_other_methods(bed):
    (bed["cfg"].workflow_dir / "isoDTB.workflow").unlink()
    _queue(bed, "iso_good")
    _queue(bed, "dia_good")
    w = Worker(bed["cfg"], bed["ledger"])
    assert w.run_once()
    assert [j.status for j in bed["ledger"].list()] == ["queued", "done"]


def test_missing_launcher_holds_everything(bed):
    cfg = replace(bed["cfg"], fragpipe_exe=bed["root"] / "nope" / "fragpipe.bat")
    _queue(bed, "iso_good")
    assert not Worker(cfg, bed["ledger"]).run_once()
    assert "FragPipe launcher not found" in bed["ledger"].get(1).reason


def test_override_workflow_missing_fails_job(bed):
    folder = testbed.drop(bed["root"], "iso_good")
    (folder / "experiment.yaml").write_text("workflow: does_not_exist\n", encoding="utf-8")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "does_not_exist" in job.reason


def test_raw_files_removed_after_queueing_fails_job(bed):
    dest = _queue(bed, "iso_good")
    next(dest.glob("*.raw")).rename(dest / "moved_away.txt")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "missing" in job.reason


def test_timeout_fails_job(bed, monkeypatch):
    monkeypatch.setenv("LABWATCH_FAKE_FP_SECONDS", "30")
    cfg = replace(bed["cfg"], timeout_minutes=0.03)  # ~2 s
    _queue(bed, "iso_good")
    t0 = time.monotonic()
    Worker(cfg, bed["ledger"]).run_once()
    assert time.monotonic() - t0 < 20
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "timed out" in job.reason


def test_stop_kills_fragpipe_and_requeues(bed, monkeypatch):
    monkeypatch.setenv("LABWATCH_FAKE_FP_SECONDS", "30")
    dest = _queue(bed, "iso_good")
    w = Worker(bed["cfg"], bed["ledger"], poll_seconds=0.1)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    for _ in range(100):
        if bed["ledger"].get(1).status == "running" and _status(dest).get("run", {}).get("pid"):
            break
        time.sleep(0.1)
    assert bed["ledger"].get(1).status == "running"
    w.stop()
    t.join(timeout=20)
    assert not t.is_alive()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "interrupted" in job.reason
    assert "stopped by labwatch" in (dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG).read_text(encoding="utf-8")


def test_watcher_and_worker_together(bed):
    """What `labwatch run` does: watcher thread files the folder, worker thread searches it."""
    from labwatch.watcher import Watcher

    cfg = bed["cfg"]
    wat = Watcher(cfg.inbox, lambda f: intake(f, cfg, Ledger(cfg.database)), poll_seconds=0.05,
                  stable_seconds=0.3, min_raw_files=1)
    wrk = Worker(cfg, Ledger(cfg.database), poll_seconds=0.1)
    threads = [threading.Thread(target=x.run_forever, daemon=True) for x in (wat, wrk)]
    for t in threads:
        t.start()
    try:
        testbed.drop(bed["root"], "iso_good")
        for _ in range(200):
            jobs = bed["ledger"].list()
            if jobs and jobs[0].status == "done":
                break
            time.sleep(0.05)
        assert jobs and jobs[0].status == "done", jobs and jobs[0].reason
    finally:
        wat.stop()
        wrk.stop()
        for t in threads:
            t.join(timeout=20)


# ------------------------------------------------------------------ unit bits --


def test_workflow_db_path_and_patch():
    escaped = "a=1\ndatabase.db-path=C\\:\\\\Fragpipe_Auto\\\\fasta\\\\x.fas\nb=2\n"
    assert fragpipe.workflow_db_path(escaped) == "C:\\Fragpipe_Auto\\fasta\\x.fas"
    out = fragpipe.patch_workflow(escaped, Path("C:/Fragpipe_Auto/fasta/y.fas"))
    assert "database.db-path=C:/Fragpipe_Auto/fasta/y.fas" in out and "a=1" in out and "b=2" in out
    assert out.count("database.db-path") == 1
    added = fragpipe.patch_workflow("a=1\n", Path("/f/z.fas"))
    assert added.endswith("database.db-path=/f/z.fas\n")
    assert fragpipe.workflow_db_path("database.db-path=\n") == ""


def test_launcher_prefers_bat_next_to_exe(bed, tmp_path):
    bin_ = tmp_path / "FragPipe-24.0" / "fragpipe" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "fragpipe.exe").write_text("", encoding="utf-8")
    (bin_ / "fragpipe.bat").write_text("", encoding="utf-8")
    cfg = replace(bed["cfg"], fragpipe_exe=bin_ / "fragpipe.exe")
    assert fragpipe.resolve_launcher(cfg) == bin_ / "fragpipe.bat"


def test_fasta_falls_back_to_workflow_database(bed):
    cfg = bed["cfg"]
    real = cfg.fasta_dir / "human_reviewed_decoys.fas"
    elsewhere = bed["root"] / "db.fas"
    real.rename(elsewhere)
    (cfg.workflow_dir / "isoDTB.workflow").write_text(f"database.db-path={elsewhere.as_posix()}\n", encoding="utf-8")
    _queue(bed, "iso_good")
    Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done" and "using the workflow's own database" in (job.reason or "")
