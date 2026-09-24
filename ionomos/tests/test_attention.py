"""What needs a person ends up in the attention queue (pop-up windows), and leaves it once fixed."""
import json
import time
from pathlib import Path

import pytest

from ionomos import attention, testbed
from ionomos.config import load
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "ledger": Ledger(cfg.database), "cfg_path": cfg_path}


def _queue(bed, sample: str) -> Path:
    folder = testbed.drop(bed["root"], sample)
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def _open(bed, kind=None):
    return [i for i in attention.items(bed["cfg"].log_dir) if kind is None or i.kind == kind]


# ------------------------------------------------------------------- the store --


def test_items_dedupe_update_and_close(tmp_path):
    a = attention.raise_item(tmp_path, "search_failed", "t", "m1", key="k", job_id=3, causes=["c1"])
    b = attention.raise_item(tmp_path, "search_failed", "t", "m1", key="k", job_id=3, causes=["c1"])
    assert a.id == b.id and len(attention.items(tmp_path)) == 1
    attention.mark_shown(tmp_path, a.id)
    assert attention.get(tmp_path, a.id).shown == 1
    attention.raise_item(tmp_path, "search_failed", "t", "m2 (changed)", key="k", job_id=3)
    assert attention.get(tmp_path, a.id).shown == 0  # something new: pop up again
    attention.snooze(tmp_path, a.id, 10)
    assert not attention.get(tmp_path, a.id).due()
    assert attention.resolve_where(tmp_path, kind="search_failed", job_id=3) == 1
    assert attention.items(tmp_path) == [] and len(attention.items(tmp_path, open_only=False)) == 1
    again = attention.raise_item(tmp_path, "search_failed", "t", "m3", key="k")
    assert again.is_open and again.shown == 0  # a new occurrence after it was closed


def test_store_survives_junk_and_unwritable_dirs(tmp_path):
    d = attention.folder(tmp_path)
    d.mkdir()
    (d / "junk.json").write_text("{not json", encoding="utf-8")
    assert attention.items(tmp_path) == []
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert attention.raise_item(blocker, "search_failed", "t", "m") is None  # logged, never raised
    assert attention.raise_item(None, "search_failed", "t", "m") is None


def test_app_alive_heartbeat(tmp_path):
    assert not attention.app_is_open(tmp_path)
    attention.touch_app_alive(tmp_path)
    assert attention.app_is_open(tmp_path)
    attention.clear_app_alive(tmp_path)
    assert not attention.app_is_open(tmp_path)


def test_purge_removes_only_old_closed_items(tmp_path):
    import os

    a = attention.raise_item(tmp_path, "search_failed", "a", "m", key="a")
    attention.raise_item(tmp_path, "search_failed", "b", "m", key="b")
    attention.dismiss(tmp_path, a.id)
    old = time.time() - 40 * 86400
    for p in attention.folder(tmp_path).glob("*.json"):
        os.utime(p, (old, old))
    assert attention.purge(tmp_path) == 1 and len(attention.items(tmp_path)) == 1


# ----------------------------------------------------------------- the worker --


def test_failed_search_raises_an_item_that_retry_closes(bed):
    _queue(bed, "fp_fail")
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    it = _open(bed, "search_failed")[0]
    assert it.severity == "error" and it.job_id == 1 and "IonQuant crashed" in it.message
    assert it.details  # the log tail is in the window
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    from ionomos import worker as worker_mod

    worker_mod._close(bed["cfg"], bed["ledger"].get(1), "search_failed")
    assert not _open(bed, "search_failed")


def test_held_search_raises_a_waiting_item_that_closes_when_it_runs(bed):
    wf = bed["cfg"].workflow_dir / "isoDTB.workflow"
    saved = wf.read_text(encoding="utf-8")
    wf.unlink()
    _queue(bed, "iso_good")
    w = Worker(bed["cfg"], bed["ledger"])
    w.run_once()
    it = _open(bed, "search_waiting")[0]
    assert "workflow" in it.message and "Import workflow" in it.causes[0]
    wf.write_text(saved, encoding="utf-8")
    assert w.run_once()
    assert bed["ledger"].get(1).status == "done"
    assert not _open(bed, "search_waiting")


def test_done_job_whose_analysis_needs_input(bed, monkeypatch):
    dest = _queue(bed, "dia_good")
    # the analysis will find one condition only: every file given the same condition
    from ionomos import postprocess

    real = postprocess.run_for_folder

    def one_condition(d, cfg, method=None, extra=None, progress=None):
        rec_path = Path(d) / "ionomos.json"
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        for line in rec["plan"]["manifest"]:
            line["experiment"] = "Same"
        rec_path.write_text(json.dumps(rec), encoding="utf-8")
        return real(d, cfg, method, extra, progress)

    monkeypatch.setattr(postprocess, "run_for_folder", one_condition)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done" and job.reason.startswith("analysis needs your input")
    it = _open(bed, "analysis_input")[0]
    assert it.dest == str(dest) and it.job_id == 1
    assert "ONE_CONDITION" in {i["code"] for i in it.data["issues"]}


def test_rejected_folder_raises_an_item_that_clears_when_queued(bed):
    folder = testbed.drop(bed["root"], "gui_unknown_user")  # no resolver window here -> rejected with a note
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "rejected"
    it = _open(bed, "intake_rejected")[0]
    assert folder.name in it.title and it.data["folder"] == str(folder) and it.severity == "error"
    assert "naming" in " ".join(it.causes).lower()
    # the person fixes it (an experiment.yaml naming the user) -> taken in -> the item closes
    (folder / "experiment.yaml").write_text("user: EJQ\n", encoding="utf-8")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    assert not _open(bed, "intake_rejected")


def test_cli_attention_list_show_dismiss(bed, capsys):
    from ionomos.cli import main

    it = attention.raise_item(bed["cfg"].log_dir, "search_failed", "FragPipe failed on X", "exit 1", key="z",
                              causes=["out of memory"])
    assert main(["--config", str(bed["cfg_path"]), "attention"]) == 0
    assert "FragPipe failed on X" in capsys.readouterr().out
    assert main(["--config", str(bed["cfg_path"]), "attention", "show", it.id]) == 0
    assert "likely: out of memory" in capsys.readouterr().out
    assert main(["--config", str(bed["cfg_path"]), "attention", "dismiss", it.id]) == 0
    assert main(["--config", str(bed["cfg_path"]), "attention"]) == 0
    assert "nothing needs attention" in capsys.readouterr().out


def test_empty_raw_file_fails_before_fragpipe_with_a_plain_cause(bed):
    dest = _queue(bed, "iso_good")
    raw = next(dest.rglob("*.raw"))
    raw.write_bytes(b"")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "0 bytes" in job.reason and raw.name in job.reason
    it = _open(bed, "search_failed")[0]
    assert "aborted" in it.causes[0] and "copy it again" in it.causes[0]


def test_long_keys_that_share_a_prefix_stay_separate(tmp_path):
    base = "analysis:" + "/very/long/shared/prefix" * 6
    a = attention.raise_item(tmp_path, "analysis_input", "a", "m", key=base + "/EXP_A")
    b = attention.raise_item(tmp_path, "analysis_input", "b", "m", key=base + "/EXP_B")
    assert a.id != b.id and len(attention.items(tmp_path)) == 2 and "EXP_A" in a.id
