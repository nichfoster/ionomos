import logging
import os
import shutil
from pathlib import Path

from ionomos import loose
from ionomos.intake import IntakeResult, intake
from ionomos.ledger import Ledger
from ionomos.watcher import Watcher, count_raws, fingerprint
from tests.conftest import iso_raws, make_drop


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
    (inbox / "notes.txt").write_text("x")
    (inbox / "old.REJECTED.txt").write_text("x")
    (inbox / ".hidden").mkdir()
    w = Watcher(inbox, lambda p: IntakeResult.QUEUED, stable_seconds=0, group_loose=False)
    assert w.scan_once(now=0) == []
    assert w.scan_once(now=1) == []
    assert (inbox / "stray.raw").is_file()  # grouping off: loose raws stay where they are
    # grouping on (the default): the loose raw gets a folder; notes.txt stays
    w2 = Watcher(inbox, lambda p: IntakeResult.QUEUED, stable_seconds=0)
    w2.scan_once(now=0)
    w2.scan_once(now=1)
    assert (inbox / "stray" / "stray.raw").is_file() and (inbox / "notes.txt").is_file()


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


def test_inbox_outage_logs_once_then_recovers(lab, caplog):
    # The inbox (e.g. a network share) dropping out is logged once per outage,
    # not once per poll; recovery is announced once; a folder detected before
    # the outage survives it and is handed off afterwards.
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    seen = []
    w = Watcher(inbox, lambda p: seen.append(p) or IntakeResult.QUEUED, stable_seconds=10)

    w.scan_once(now=0)  # d detected while the inbox is still there
    gone = inbox.with_name(inbox.name + "-outage")
    inbox.rename(gone)
    with caplog.at_level(logging.INFO, logger="ionomos.watcher"):
        assert w.scan_once(now=1) == []
        assert w.scan_once(now=2) == []  # latched: no second error on the next poll
        errs = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errs) == 1 and "inbox is not reachable" in errs[0].getMessage()

    gone.rename(inbox)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="ionomos.watcher"):
        assert w.scan_once(now=3) == []  # recovery announced...
        assert w.scan_once(now=10) == [d]  # ...and the pre-outage folder is handed off
        back = [r for r in caplog.records if "inbox is back" in r.getMessage()]
        assert len(back) == 1 and back[0].levelno == logging.INFO
    assert seen == [d]


def test_file_vanishing_mid_fingerprint_reads_as_unstable(lab, monkeypatch):
    # A file that disappears between the os.walk listing and its stat() is
    # recorded as (-1, -1) — "changing" — so the folder must not be offered.
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw", "S_1_2.raw"])
    seen = []
    w = Watcher(inbox, lambda p: seen.append(p) or IntakeResult.QUEUED, stable_seconds=10)
    w.scan_once(now=0)

    real_stat = Path.stat
    state = {"vanished": True}

    def flaky_stat(self, **kwargs):
        if state["vanished"] and self.name == "S_1_2.raw":
            raise OSError("vanished mid-copy")
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    assert ("S_1_2.raw", -1, -1) in fingerprint(d)
    assert w.scan_once(now=11) == []  # past the stability deadline, but the vanish reset the clock

    state["vanished"] = False  # the file is back: a full stable window is required again
    assert w.scan_once(now=20) == []  # fingerprint differs from the vanished one: reset again
    assert w.scan_once(now=30) == [d]
    assert seen == [d]


def test_retry_backoff_doubles_up_to_the_300s_cap(lab):
    # RETRY delays double from retry_seconds and are capped at 300 s.
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    w = Watcher(inbox, lambda p: IntakeResult.RETRY, stable_seconds=0, retry_seconds=10)
    w.scan_once(now=0)

    now, delays = 1.0, []
    for _ in range(7):
        assert w.scan_once(now=now) == [d]
        delays.append(w._pending[d].retry_delay)
        now += delays[-1] + 0.5  # step past retry_at so the next poll re-offers
    assert delays == [10, 20, 40, 80, 160, 300, 300]


def test_redropped_rejected_folder_is_a_fresh_observation(lab):
    # CHARACTERIZATION: pulling a rejected folder out of the inbox makes the
    # watcher forget it, rejection state included — the same name re-dropped
    # unchanged (stale note still in the inbox) is a brand-new observation and
    # is re-offered after stability. Audit characterization (no issue); invert
    # or delete if the re-offer contract changes.
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
    assert w.scan_once(now=1) == [d] and len(calls) == 1  # rejected, note written

    pulled = inbox.parent / f"{d.name}-pulled"
    d.rename(pulled)
    assert w.scan_once(now=2) == []  # gone from the inbox: forgotten
    assert d not in w._pending

    pulled.rename(d)  # re-dropped unchanged; the stale note was never deleted
    assert note.is_file()
    assert w.scan_once(now=3) == []  # fresh observation: first sight again
    assert w.scan_once(now=4) == [d]  # re-offered despite the stale note and identical content
    assert len(calls) == 2


def test_mtime_only_touch_resets_the_stability_clock(lab):
    # Any fingerprint change resets the clock — including a touch that changes
    # only mtime_ns and not size.
    inbox = lab["inbox"]
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    seen = []
    w = Watcher(inbox, lambda p: seen.append(p) or IntakeResult.QUEUED, stable_seconds=10)
    w.scan_once(now=0)

    target = d / "S_1_1.raw"
    before = fingerprint(d)
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    after = fingerprint(d)
    assert [(n, s) for n, s, _ in before] == [(n, s) for n, s, _ in after]  # size untouched
    assert before != after  # only the mtime moved

    assert w.scan_once(now=10) == []  # without the touch this would have been handed off
    assert w.scan_once(now=19) == []  # still inside the fresh window
    assert w.scan_once(now=20) == [d]
    assert seen == [d]


def test_loose_grouping_failure_does_not_stop_folder_handling(lab, monkeypatch, caplog):
    # An exception during loose grouping is swallowed so the folder pass in the
    # same scan still runs; the loose file is left for a later attempt.
    inbox = lab["inbox"]
    (inbox / "stray.raw").write_bytes(b"x")
    d = make_drop(inbox, "EJQ_isoDTB_x", ["S_1_1.raw"])
    seen = []

    def boom(*args, **kwargs):
        raise RuntimeError("grouping exploded")

    monkeypatch.setattr(loose, "loose_raws", boom)
    w = Watcher(inbox, lambda p: seen.append(p) or IntakeResult.QUEUED, stable_seconds=0)
    with caplog.at_level(logging.ERROR, logger="ionomos.watcher"):
        assert w.scan_once(now=0) == []  # grouping blew up, but d was still detected this pass
        assert w.scan_once(now=1) == [d]  # and handed off on schedule
    assert any("could not group loose files" in r.getMessage() for r in caplog.records)
    assert seen == [d]
    assert (inbox / "stray.raw").is_file()


def test_raws_nested_deeper_than_top_level_or_raw_stabilize_then_intake_rejects(lab):
    # CHARACTERIZATION: count_raws counts .raw at any depth, but intake only
    # accepts top level or a raw/ subfolder — a folder with raws nested in e.g.
    # data/ stabilizes in the watcher, is handed off, and is then rejected with
    # a "no .raw files" note rather than queued. Audit characterization (no
    # issue); invert or delete if intake widens its search.
    inbox = lab["inbox"]
    d = inbox / "EJQ_isoDTB_x"
    (d / "data").mkdir(parents=True)
    for r in iso_raws():  # 21 raws, nested deeper than top level or raw/
        (d / "data" / r).write_bytes(b"\0" * 64)
    assert count_raws(fingerprint(d)) == 21  # the watcher sees raws at any depth

    handed = []
    ledger = Ledger(lab["cfg"].database)
    w = Watcher(inbox, lambda p: handed.append(p) or intake(p, lab["cfg"], ledger), stable_seconds=0)
    assert w.scan_once(now=0) == []   # first sight
    assert w.scan_once(now=1) == [d]  # stabilizes: nested raws count
    note = inbox / "EJQ_isoDTB_x.REJECTED.txt"
    assert note.is_file()             # ...but intake rejects them
    assert "no .raw files" in note.read_text()
    assert d.is_dir()                 # rejected in place: the watcher keeps watching it
