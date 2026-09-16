import shutil

from labwatch.watcher import Watcher, count_raws, fingerprint
from tests.conftest import make_drop


def test_waits_for_stability_then_hands_off_once(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    seen = []
    w = Watcher(inbox, lambda p: seen.append(p) or True, stable_seconds=10)

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
    w = Watcher(inbox, lambda p: True, stable_seconds=0)
    assert w.scan_once(now=0) == []
    assert w.scan_once(now=1) == []


def test_needs_min_raw_files(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", [], others=["notes.txt"])
    w = Watcher(inbox, lambda p: True, stable_seconds=0, min_raw_files=1)
    w.scan_once(now=0)
    assert w.scan_once(now=1) == []
    (d / "S_1_1.raw").write_bytes(b"x")
    w.scan_once(now=2)
    assert w.scan_once(now=3) == [d]


def test_reoffers_after_change_if_callback_declined(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    answers = iter([False, True])
    w = Watcher(inbox, lambda p: next(answers), stable_seconds=0)
    w.scan_once(now=0)
    assert w.scan_once(now=1) == [d]     # declined
    assert w.scan_once(now=2) == []      # not re-offered while unchanged
    (d / "S_1_2.raw").write_bytes(b"x")
    w.scan_once(now=3)
    assert w.scan_once(now=4) == [d]     # accepted
    assert w.scan_once(now=5) == []


def test_forgets_folder_that_disappears(lab):
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    w = Watcher(inbox, lambda p: True, stable_seconds=0)
    w.scan_once(now=0)
    shutil.rmtree(d)
    assert w.scan_once(now=1) == []
    assert d not in w._pending
