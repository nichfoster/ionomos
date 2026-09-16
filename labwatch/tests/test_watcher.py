import shutil

from labwatch.intake import IntakeResult
from labwatch.watcher import Watcher, count_raws, fingerprint
from tests.conftest import make_drop


def test_waits_for_stability_then_hands_off_once(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    seen = []
    w = Watcher(inbox, lambda p: seen.append(p) or IntakeResult.QUEUED, stable_seconds=10)

    assert w.scan_once(now=0) == []          # first sight
    assert w.scan_once(now=5) == []          # not stable yet
    (d / "S_1_2.raw").write_bytes(b"\0" * 10)  # copy still going
    assert w.scan_once(now=12) == []         # clock reset by change
    assert w.scan_once(now=23) == [d]        # 10s after last change
    assert seen == [d]
    assert w.scan_once(now=40) == []         # not offered again
    assert count_raws(fingerprint(d)) == 2


def test_ignores_loose_files_and_rejected_notes(lab):
    inbox = lab["inbox"]
    (inbox / "stray.raw").write_bytes(b"x")
    (inbox / "old.REJECTED.txt").write_text("x")
    (inbox / ".hidden").mkdir()
    w = Watcher(inbox, lambda p: IntakeResult.QUEUED, stable_seconds=0)
    assert w.scan_once(now=0) == []
    assert w.scan_once(now=1) == []


def test_needs_min_raw_files(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", [], others=["notes.txt"])
    w = Watcher(inbox, lambda p: IntakeResult.QUEUED, stable_seconds=0, min_raw_files=1)
    w.scan_once(now=0)
    assert w.scan_once(now=1) == []
    (d / "S_1_1.raw").write_bytes(b"x")
    w.scan_once(now=2)
    assert w.scan_once(now=3) == [d]


def test_rejected_is_reoffered_on_change_or_note_removal(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    note = inbox / "EJQ_isoDTB_x.REJECTED.txt"
    calls = []

    def cb(p):
        calls.append(p)
        note.write_text("nope")
        return IntakeResult.REJECTED

    w = Watcher(inbox, cb, stable_seconds=0)
    w.scan_once(now=0)
    assert w.scan_once(now=1) == [d] and len(calls) == 1
    assert w.scan_once(now=2) == []            # rejected + note present: leave it alone
    note.unlink()                              # user deleted the note -> retry
    assert w.scan_once(now=3) == [d] and len(calls) == 2
    (d / "S_1_2.raw").write_bytes(b"x")        # user fixed the folder -> retry
    w.scan_once(now=4)
    assert w.scan_once(now=5) == [d] and len(calls) == 3


def test_retry_backs_off(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    answers = iter([IntakeResult.RETRY, IntakeResult.RETRY, IntakeResult.QUEUED])
    w = Watcher(inbox, lambda p: next(answers), stable_seconds=0, retry_seconds=10)
    w.scan_once(now=0)
    assert w.scan_once(now=1) == [d]      # RETRY -> wait 10
    assert w.scan_once(now=5) == []
    assert w.scan_once(now=12) == [d]     # RETRY -> wait 20
    assert w.scan_once(now=25) == []
    assert w.scan_once(now=33) == [d]     # QUEUED
    assert w.scan_once(now=40) == []


def test_callback_exception_is_retry_not_crash(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])

    def boom(p):
        raise RuntimeError("disk on fire")

    w = Watcher(inbox, boom, stable_seconds=0, retry_seconds=5)
    w.scan_once(now=0)
    assert w.scan_once(now=1) == [d]
    assert w._pending[d].retry_at == 6


def test_forgets_folder_that_disappears(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    w = Watcher(inbox, lambda p: IntakeResult.QUEUED, stable_seconds=0)
    w.scan_once(now=0)
    shutil.rmtree(d)
    assert w.scan_once(now=1) == []
    assert d not in w._pending
