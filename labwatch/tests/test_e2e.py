"""End-to-end: real watcher thread + real intake + testbed samples, no GUI (scripted resolver).

Runs the same on macOS, Linux and Windows. This is the test to run when you
change anything about detection, stability, intake or the ledger.
"""
import json
import threading
import time
from pathlib import Path

import pytest

from labwatch import testbed
from labwatch.config import load
from labwatch.intake import intake
from labwatch.ledger import Ledger
from labwatch.manifest import Overrides
from labwatch.watcher import Watcher


class ScriptedResolver:
    """Answers with a fixed Overrides; records every Draft it saw."""

    def __init__(self, answer):
        self.answer, self.drafts = answer, []

    def resolve(self, d):
        self.drafts.append(d)
        return self.answer


@pytest.fixture
def bed(tmp_path):
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    ledger = Ledger(cfg.database)
    return {"root": tmp_path / "bed", "cfg": cfg, "ledger": ledger, "cfg_path": cfg_path}


def _run_watcher(bed, resolver=None, stable=0.3, poll=0.05):
    cfg, ledger = bed["cfg"], bed["ledger"]
    w = Watcher(cfg.inbox, lambda f: intake(f, cfg, ledger, resolver), poll_seconds=poll,
                stable_seconds=stable, min_raw_files=cfg.min_raw_files, retry_seconds=0.2)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    return w, t


def _wait(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_slow_drop_is_queued_once_stable(bed):
    w, _ = _run_watcher(bed)
    try:
        # slow copy: 9 raws + 1 note, 0.05s apart -> the watcher must not grab it early
        testbed.drop(bed["root"], "iso_good", slow=True, delay=0.05)
        assert _wait(lambda: bed["ledger"].next_queued() is not None)
        job = bed["ledger"].next_queued()
        assert job.user == "EJQ" and job.method == "isoDTB"
        dest = Path(job.dest_dir)
        assert len(list(dest.glob("*.raw"))) == 9 and (dest / "EJQ-2-027_notes.xlsx").is_file()
        rec = json.loads((dest / "labwatch.json").read_text())
        assert rec["plan"]["layout"] == {"EJQ_PK_EJQ-2-027_isoDTB_1uM_3h": {"1": [1, 2, 3], "2": [1, 2, 3], "3": [1, 2, 3]}}
        assert not (bed["cfg"].inbox / "20260902-isoDTB_EJQ-2-027").exists()
    finally:
        w.stop()


def test_all_good_samples_queue(bed):
    w, _ = _run_watcher(bed)
    try:
        good = ["iso_good", "iso_spaces", "dia_good", "tmt_good", "glued_initials", "method_in_files"]
        for n in good:
            testbed.drop(bed["root"], n)
        assert _wait(lambda: len(bed["ledger"].list("queued")) == len(good), timeout=15)
        jobs = {j.inbox_name: j for j in bed["ledger"].list()}
        assert jobs["20260902-isoDTB-EJQ-2-027-1uM-3h"].user == "EJQ"
        assert jobs["IJD05_isoDTB_FLAGpull"].user == "Isaac"
        assert jobs["EJQ_2027_pulldown"].method == "isoDTB"
        assert jobs["20260126_Aman_TMT_KL6159A-9plex"].parsed["plan"]["manifest"][0]["bioreplicate"] == 1
        assert not any(p.suffix == ".txt" for p in bed["cfg"].inbox.iterdir())
    finally:
        w.stop()


def test_gui_samples_go_to_resolver_and_get_queued(bed):
    res = ScriptedResolver(Overrides(user="Chris", method="DIA", allow_uneven_fractions=True))
    w, _ = _run_watcher(bed, resolver=res)
    try:
        names = ["gui_unknown_user", "gui_no_method", "gui_incomplete", "reject_two_methods"]
        for n in names:
            testbed.drop(bed["root"], n)
        assert _wait(lambda: len(bed["ledger"].list("queued")) == len(names), timeout=15)
        assert sorted(d.kind.value for d in res.drafts) == ["layout", "method", "method", "user"]
        for j in bed["ledger"].list():
            assert j.user == "Chris"
            assert (Path(j.dest_dir) / "experiment.yaml").is_file()
    finally:
        w.stop()


def test_resolver_skip_then_user_fixes_folder(bed):
    res = ScriptedResolver(None)
    w, _ = _run_watcher(bed, resolver=res)
    try:
        d = testbed.drop(bed["root"], "gui_unknown_user")
        note = bed["cfg"].inbox / f"{d.name}.REJECTED.txt"
        assert _wait(note.exists)
        assert bed["ledger"].list() == []
        # user fixes it by writing an experiment.yaml -> tree changes -> re-evaluated, no resolver needed
        (d / "experiment.yaml").write_text("user: EJQ\n")
        assert _wait(lambda: bed["ledger"].next_queued() is not None)
        assert bed["ledger"].next_queued().user == "EJQ"
        assert not note.exists() and len(res.drafts) == 1
    finally:
        w.stop()


def test_no_raws_never_queues_and_unresolvable_writes_note(bed):
    w, _ = _run_watcher(bed, resolver=ScriptedResolver(Overrides(user="EJQ")))
    try:
        testbed.drop(bed["root"], "reject_no_raws")
        testbed.drop(bed["root"], "iso_good")
        assert _wait(lambda: bed["ledger"].next_queued() is not None)
        testbed.drop(bed["root"], "iso_good")  # same name again -> dest exists -> note, no resolver
        note = bed["cfg"].inbox / "20260902-isoDTB_EJQ-2-027.REJECTED.txt"
        assert _wait(note.exists)
        assert "already" in note.read_text()
        time.sleep(0.5)
        assert len(bed["ledger"].list()) == 1
        assert (bed["cfg"].inbox / "EJQ_isoDTB_empty").is_dir()  # still waiting for raws, untouched
    finally:
        w.stop()


def test_restart_recovers_running_job(bed):
    testbed.drop(bed["root"], "iso_good")
    assert intake(bed["cfg"].inbox / "20260902-isoDTB_EJQ-2-027", bed["cfg"], bed["ledger"]).value == "queued"
    bed["ledger"].start_attempt(1)
    fresh = Ledger(bed["cfg"].database)
    assert fresh.recover_on_startup() == [(1, "queued")]
    assert fresh.get(1).status == "queued" and "interrupted" in fresh.get(1).reason
