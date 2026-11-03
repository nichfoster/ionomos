"""Fault injection around intake and the watcher (round 4): the ways a drop reaches the inbox on the lab PC that are
not a clean folder of finished files, acted out with real files, the testbed's fake FragPipe and the real
intake / watcher / worker / ledger.

For each fault: nothing the user made is lost or overwritten, the drop ends filed, waiting with a reason, or
rejected with a note that says what to do — never "send diagnostics" for something that isn't a bug — and what
needs a person is told to one (an attention item), not only to the log.

Locally run with a short --basetemp (the fake FragPipe and Windows' 260-character limit): see docs/TESTING.md.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import ionomos.intake as intake_mod
from ionomos import attention, fragpipe, health, names, testbed
from ionomos import ledger as ledger_mod
from ionomos.config import load
from ionomos.intake import IntakeResult, draft, intake, note_path
from ionomos.ledger import FILING, Ledger
from ionomos.manifest import Overrides
from ionomos.watcher import Watcher
from ionomos.worker import Worker, recover
from tests.conftest import iso_raws, make_drop

WINDOWS = os.name == "nt"


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE", raising=False)
    monkeypatch.setattr(fragpipe, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(intake_mod, "_RETRYING", {})
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    led = Ledger(cfg.database)
    yield {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": led,
           "inbox": cfg.inbox, "users": cfg.users_root}
    led.close()


def _drop(bed, sample: str = "iso_good", name: str = "") -> Path:
    folder = testbed.drop(bed["root"], sample)
    return folder.rename(folder.with_name(name)) if name else folder


def _take(bed, folder: Path, resolver=None) -> IntakeResult:
    return intake(folder, bed["cfg"], bed["ledger"], resolver)


def _drain(bed, most: int = 10) -> None:
    w = Worker(bed["cfg"], bed["ledger"])
    for _ in range(most):
        if not w.run_once():
            return
    raise AssertionError(f"the worker still had work after {most} jobs")


def _sizes(folder: Path) -> dict[str, int]:
    return {p.relative_to(folder).as_posix(): p.stat().st_size for p in folder.rglob("*.raw")}


def _open_items(bed, key: str) -> list:
    return [i for i in attention.items(bed["cfg"].log_dir) if i.key == key]


# ------------------------------------------------------------- copies still running --


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses to move a folder while a file in it is open")
def test_a_drop_still_being_written_at_its_final_size_is_retried_not_filed_half_written(bed):
    """Explorer sets a file's final size first and fills it afterwards, so the fingerprint can stand still while the
    copy runs. The open handle is what stops the move: the watcher retries, and files the drop once it is let go."""
    folder = _drop(bed)
    before = _sizes(folder)
    raw = sorted(folder.glob("*.raw"))[-1]
    seen = []
    w = Watcher(bed["inbox"], lambda f: seen.append(_take(bed, f)) or seen[-1], stable_seconds=60, retry_seconds=15)
    fh = open(raw, "r+b")  # noqa: SIM115 - the copy still writing into it
    try:
        assert w.scan_once(now=0) == []
        assert w.scan_once(now=61) == [folder]  # stable by size and time ...
        assert seen == [IntakeResult.RETRY]  # ... but not filed while the copy holds the file
        assert _sizes(folder) == before and bed["ledger"].list() == []
        assert not (bed["users"] / "ABC" / folder.name).exists() and not note_path(folder).exists()
        assert w.scan_once(now=70) == []  # backing off
    finally:
        fh.close()
    assert w.scan_once(now=77) == [folder]
    assert seen[-1] == IntakeResult.QUEUED
    assert _sizes(bed["users"] / "ABC" / folder.name) == before


@pytest.mark.skipif(not WINDOWS, reason="the in-use check is a Windows sharing check")
def test_a_cross_volume_copy_waits_for_a_file_that_is_still_open(bed, monkeypatch):
    """Inbox and users_root on two drives: a copy would read the half-written file, file it, and the clean-up after
    it would delete the finished files from the inbox (the message then said: keep the filed copy). Now the drop
    waits, untouched, until nothing in it is open."""
    folder = _drop(bed)
    before = _sizes(folder)
    real = os.rename

    def other_drive(src, dst, *a, **kw):
        if Path(src) == folder:
            raise OSError(18, "Invalid cross-device link")  # errno.EXDEV
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "rename", other_drive)
    fh = open(sorted(folder.glob("*.raw"))[0], "r+b")  # noqa: SIM115
    try:
        assert _take(bed, folder) == IntakeResult.RETRY
        assert _sizes(folder) == before and not (bed["users"] / "ABC" / folder.name).exists()
    finally:
        fh.close()
    assert _take(bed, folder) == IntakeResult.QUEUED
    assert not folder.exists() and _sizes(bed["users"] / "ABC" / folder.name) == before


# ------------------------------------------------------------ renamed, removed, twice --


def test_a_drop_renamed_while_it_settles_is_waited_for_again_under_its_new_name(bed):
    folder = _drop(bed, "dia_good")
    got = []
    w = Watcher(bed["inbox"], lambda f: got.append(f) or _take(bed, f), stable_seconds=60)
    assert w.scan_once(now=0) == []
    new = folder.rename(folder.with_name(folder.name + "_v2"))  # the person fixes the name half way
    assert w.scan_once(now=30) == []  # the old name is forgotten, the new one seen for the first time
    assert w.scan_once(now=70) == []  # 40 s: not settled under its new name yet
    assert w.scan_once(now=91) == [new] and got == [new]
    assert (bed["users"] / "Sam" / new.name).is_dir() and not folder.exists()
    assert [j.inbox_name for j in bed["ledger"].list()] == [new.name]


def test_a_drop_taken_back_out_while_it_settles_is_forgotten(bed, tmp_path):
    folder = _drop(bed, "dia_good")
    got = []
    w = Watcher(bed["inbox"], lambda f: got.append(f) or _take(bed, f), stable_seconds=60)
    w.scan_once(now=0)
    kept = shutil.move(str(folder), str(tmp_path / "desk"))  # dragged back to the desktop
    assert w.scan_once(now=61) == [] and got == [] and w._pending == {}
    assert bed["ledger"].list() == [] and Path(kept).is_dir()


def test_a_drop_renamed_while_the_review_window_is_open_is_not_filed_under_the_old_reading(bed):
    class Renames:
        def resolve(self, d):
            return None

        def review(self, d):
            Path(d.source).rename(Path(d.source).with_name(Path(d.source).name + "_redo"))
            return Overrides()

    folder = _drop(bed, "dia_good")
    assert _take(bed, folder, Renames()) == IntakeResult.RETRY
    new = folder.with_name(folder.name + "_redo")
    assert new.is_dir() and not note_path(folder).exists() and not note_path(new).exists()
    assert bed["ledger"].list() == []


def test_two_drops_with_the_same_name_a_minute_apart_are_never_merged_or_overwritten(bed):
    first = _drop(bed, "dia_good")
    assert _take(bed, first) == IntakeResult.QUEUED
    dest = bed["users"] / "Sam" / first.name
    filed = _sizes(dest)
    second = _drop(bed, "dia_good")  # the same folder dragged again (or a re-acquisition under the old name)
    (second / "raw" / "Drug_3.raw").write_bytes(b"re-acquired" * 50)
    assert _take(bed, second) == IntakeResult.REJECTED
    reason = note_path(second).read_text(encoding="utf-8")
    assert "already processed" in reason and "_redo" in reason and "unexpected" not in reason
    assert _open_items(bed, f"intake:{second.name}")  # a person is told, not only the log
    assert _sizes(dest) == filed and len(bed["ledger"].list()) == 1  # the first is untouched
    redo = second.rename(second.with_name(second.name + "_redo"))
    note_path(second).unlink()
    assert _take(bed, redo) == IntakeResult.QUEUED
    _drain(bed)
    assert [j.status for j in bed["ledger"].list()] == ["done", "done"]
    assert _sizes(dest) == filed
    assert (bed["users"] / "Sam" / redo.name / "raw" / "Drug_3.raw").stat().st_size == len(b"re-acquired" * 50)


# ------------------------------------------------------------------------- names --


def test_a_word_in_another_script_is_never_silently_dropped_from_a_sample_name(bed):
    """実験_1_1.raw used to be filed as sample "1" (the word vanished in the clean-up and the numbers moved up)."""
    raws = [f"実験_{r}_{f}.raw" for r in (1, 2) for f in (1, 2)]
    folder = make_drop(bed["inbox"], "20260902_isoDTB_ABC_kanji", raws)
    assert _take(bed, folder) == IntakeResult.REJECTED
    reason = note_path(folder).read_text(encoding="utf-8")
    assert "実験 has no letters FragPipe can use" in reason and "rename the file" in reason
    assert sorted(p.name for p in folder.glob("*.raw")) == sorted(raws)  # nothing renamed
    d = draft(folder, bed["cfg"])
    assert d.method == "isoDTB" and all("no letters FragPipe can use" in f.error for f in d.files)


@pytest.mark.parametrize(("name", "raws", "safe", "samples"), [
    ("20260902_isoDTB_ABC_Müller Probe", ["Müller_1_1.raw", "Müller_2_1.raw"], "20260902_isoDTB_ABC_M-ller-Probe",
     {"M-ller"}),
    ("20260902_isoDTB_ABC_µM-dose", ["10µM_1_1.raw", "10µM_2_1.raw"], "20260902_isoDTB_ABC_M-dose", {"10-M"}),
    # names Windows reserves on their own are ordinary inside a longer name
    ("20260902_isoDTB_ABC_CON", ["CON_1_1.raw", "AUX_1_1.raw", "NUL_x_1_1.raw"], "20260902_isoDTB_ABC_CON",
     {"CON", "AUX", "NUL_x"}),
])
def test_unicode_and_reserved_looking_names_are_filed_with_safe_names(bed, name, raws, safe, samples):
    folder = make_drop(bed["inbox"], name, raws)
    assert _take(bed, folder) == IntakeResult.QUEUED, note_path(folder).read_text(encoding="utf-8")
    job = bed["ledger"].list()[-1]
    assert job.inbox_name == safe
    assert {m["experiment"] for m in job.parsed["plan"]["manifest"]} == samples
    assert all(p.name.isascii() for p in Path(job.dest_dir).glob("*.raw"))


def test_a_200_character_folder_name_is_filed_when_the_path_fits(bed):
    name = ("20260902_ABC_isoDTB_" + "long-description-" * 20)[:200]
    longest = bed["users"] / "ABC" / name / "fragpipe" / "ABC_PK_1_1" / "ABC_PK_1_1.pepXML"
    if WINDOWS and len(str(longest)) > 259:
        pytest.skip("too long for Windows without long paths here (issues #138/#139 cover that case)")
    folder = make_drop(bed["inbox"], name, ["ABC_PK_1_1.raw", "ABC_PK_2_1.raw"])
    assert _take(bed, folder) == IntakeResult.QUEUED
    assert (bed["users"] / "ABC" / name / "ABC_PK_1_1.raw").is_file()


@pytest.mark.skipif(WINDOWS, reason="Windows can't make such a folder by name (see the next test)")
def test_a_folder_name_ending_in_a_dot_is_filed_without_it(bed):
    folder = make_drop(bed["inbox"], "20260902_ABC_isoDTB_x.", ["S_1_1.raw"])
    assert _take(bed, folder) == IntakeResult.QUEUED
    assert (bed["users"] / "ABC" / "20260902_ABC_isoDTB_x" / "S_1_1.raw").is_file()


def _ext(p: Path) -> str:
    return "\\\\?\\" + os.path.abspath(str(p.parent)) + "\\" + p.name


@pytest.mark.skipif(not WINDOWS, reason="names ending in a dot or space only trouble Windows")
@pytest.mark.parametrize("end", [".", " ", ". ."])
def test_a_folder_windows_cannot_open_by_name_is_renamed_and_filed(bed, end):
    """Made on a Mac or in WSL: listed by Windows, but every normal path to it loses the dot, so the watcher used to
    skip it for ever without a word."""
    folder = bed["inbox"] / ("20260902_ABC_isoDTB_dots" + end)
    os.mkdir(_ext(folder))
    with open(_ext(folder) + "\\S_1_1.raw", "wb") as fh:
        fh.write(b"\0" * 64)
    got = []
    w = Watcher(bed["inbox"], lambda f: got.append(f) or _take(bed, f), stable_seconds=10)
    w.scan_once(now=0)  # renamed in the inbox
    fixed = bed["inbox"] / "20260902_ABC_isoDTB_dots"
    assert fixed.is_dir() and (fixed / "S_1_1.raw").is_file()
    w.scan_once(now=1)
    assert w.scan_once(now=12) == [fixed] and (bed["users"] / "ABC" / fixed.name / "S_1_1.raw").is_file()


@pytest.mark.skipif(not WINDOWS, reason="names ending in a dot or space only trouble Windows")
def test_a_folder_windows_cannot_open_is_left_alone_and_said_once_when_its_name_is_taken(bed, caplog):
    odd = bed["inbox"] / "20260902_ABC_isoDTB_x."
    os.mkdir(_ext(odd))
    taken = make_drop(bed["inbox"], "20260902_ABC_isoDTB_x", ["S_1_1.raw"])
    try:
        w = Watcher(bed["inbox"], lambda f: IntakeResult.REJECTED, stable_seconds=10)
        for t in range(3):
            w.scan_once(now=t)
        said = [r for r in caplog.records if "ends in a dot or a space" in r.getMessage()]
        assert len(said) == 1 and "ren " in said[0].getMessage()
        assert os.path.isdir(_ext(odd)) and taken.is_dir()
    finally:
        os.rmdir(_ext(odd))


def test_loose_files_named_like_a_windows_device_get_a_folder_windows_can_make(bed):
    from ionomos import loose

    assert loose.folder_name_for(["NUL_1", "NUL_2"]) == "NUL_files"
    assert loose.folder_name_for(["con"]) == "con_files"
    assert loose.folder_name_for(["CONTROL_1", "CONTROL_2"]) == "CONTROL"


# ---------------------------------------------------------------- read-only inbox --


def test_an_inbox_ionomos_cannot_change_is_retried_and_after_ten_minutes_a_person_is_told(bed, monkeypatch):
    """A share the watcher may read but not change, or a file some program never lets go of: each attempt is a
    RETRY (D17: no note can be written anyway). It used to go on every 5 minutes for ever with only a log line."""
    monkeypatch.setattr(intake_mod, "STUCK_AFTER", 0.3)
    good = _drop(bed, "dia_good")
    bad = make_drop(bed["inbox"], "no_user_no_method_here", ["a.raw"])
    real_rename, real_note = os.rename, intake_mod._write_note

    def refused(*a, **kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "rename", lambda s, d, *a, **kw: refused() if Path(s) == good else real_rename(s, d))
    monkeypatch.setattr(intake_mod, "_write_note", refused)
    assert _take(bed, good) == IntakeResult.RETRY and _take(bed, bad) == IntakeResult.RETRY
    assert not _open_items(bed, f"intake:{good.name}")  # quiet while it may clear by itself
    time.sleep(0.35)
    assert _take(bed, good) == IntakeResult.RETRY and _take(bed, bad) == IntakeResult.RETRY
    for f in (good, bad):
        (item,) = _open_items(bed, f"intake:{f.name}")
        assert item.severity == "error" and "Access is denied" in item.message
        assert any("read-only" in c for c in item.causes)
    assert good.is_dir() and bad.is_dir() and _sizes(good)  # nothing moved or lost

    monkeypatch.setattr(os, "rename", real_rename)  # the share is writable again
    monkeypatch.setattr(intake_mod, "_write_note", real_note)
    assert _take(bed, good) == IntakeResult.QUEUED
    assert not _open_items(bed, f"intake:{good.name}")  # filed: the message closes by itself
    assert _take(bed, bad) == IntakeResult.REJECTED and note_path(bad).is_file()
    assert intake_mod._RETRYING == {}


@pytest.mark.skipif(WINDOWS or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permissions (Windows ACLs are not changed by tests); root ignores them")
def test_a_really_read_only_inbox_keeps_the_drop_and_retries(bed):
    folder = _drop(bed, "dia_good")
    before = _sizes(folder)
    os.chmod(bed["inbox"], 0o555)
    try:
        assert _take(bed, folder) == IntakeResult.RETRY
        assert _sizes(folder) == before and bed["ledger"].list() == []
    finally:
        os.chmod(bed["inbox"], 0o755)
    assert _take(bed, folder) == IntakeResult.QUEUED


# ------------------------------------------------------------- experiment.yaml --


@pytest.mark.parametrize(("yaml_text", "said"), [
    ("methd: TMT\nusr: ABC\nworkflw: x\n", ["methd (did you mean method?)", "usr (did you mean user?)",
                                           "the keys it can have: allow_uneven_fractions"]),
    ("date: 2026-02-30\n", ["date that does not exist", "YYYY-MM-DD"]),
    ("date: [2026]\n", ["date must be YYYY-MM-DD"]),
    ("allow_uneven_fractions: maybe\n", ["allow_uneven_fractions must be true or false"]),
    ("user: [ABC, Sam]\n", ["user must be one name, not a list"]),
    ("method: {isoDTB: 1}\n", ["method must be one name, not a mapping"]),
    ("method: proteomics\n", ["method 'proteomics' is not one of"]),
    ("user: Nobody\n", ["user 'Nobody': no folder"]),
    ("files: [a.raw]\n", ["files: must be a mapping"]),
    ("files:\n  ABC_PK_ABC-2-027_isoDTB_1uM_3h_1_1.raw: {bioreplicat: 2}\n", ["bioreplicat (did you mean bioreplicate?)"]),
    ("files:\n  nothere.raw: {bioreplicate: 2}\n", ["not in folder: nothere.raw"]),
    ("tmt: 5\n", ["tmt"]),
    ("analysis: [x]\n", ["analysis: must be a mapping"]),
    ("- method: isoDTB\n", ["must be a mapping"]),
    ("method: isoDTB\n\tuser: ABC\n", ["not valid YAML"]),
    ("method: 'isoDTB\n", ["not valid YAML"]),
])
def test_experiment_yaml_with_every_key_wrong_says_which_key_and_what_to_write(bed, yaml_text, said):
    folder = _drop(bed)
    (folder / "experiment.yaml").write_text(yaml_text, encoding="utf-8")
    before = _sizes(folder)
    assert _take(bed, folder) == IntakeResult.REJECTED
    reason = note_path(folder).read_text(encoding="utf-8")
    assert "unexpected problem" not in reason and "send diagnostics" not in reason
    for s in said:
        assert s in reason, reason
    assert _sizes(folder) == before and (folder / "experiment.yaml").read_text(encoding="utf-8") == yaml_text
    draft(folder, bed["cfg"])  # the naming window's reading never raises


@pytest.mark.parametrize("flag", ["'false'", "'no'", "0", "off"])
def test_a_quoted_false_does_not_switch_the_fraction_check_off(bed, flag):
    folder = make_drop(bed["inbox"], "20260902_isoDTB_ABC_uneven", iso_raws(reps=(1, 2), fracs=(1, 2))[:-1])
    (folder / "experiment.yaml").write_text(f"allow_uneven_fractions: {flag}\n", encoding="utf-8")
    assert _take(bed, folder) == IntakeResult.REJECTED
    assert "incomplete copy" in note_path(folder).read_text(encoding="utf-8")


def test_an_experiment_yaml_that_is_not_utf8_is_still_read(bed):
    folder = _drop(bed)
    (folder / "experiment.yaml").write_bytes("notes: Probe — Müller\nuser: ABC\n".encode("cp1252"))
    assert _take(bed, folder) == IntakeResult.QUEUED  # D86


# --------------------------------------------------------------- naming history --


def test_a_damaged_naming_history_never_costs_a_drop(bed):
    hist = bed["cfg"].log_dir / names.NAMING_HISTORY_FILE
    hist.write_bytes(b'{"user": "ABC\x97", "method": "isoDTB"}\n\xff\xfe garbage\n[1, 2]\n"x"\n\n')
    folder = _drop(bed)
    assert _take(bed, folder) == IntakeResult.QUEUED


# ---------------------------------------------------------------- ionomos.json --


@pytest.mark.parametrize("body", ["null", "[]", '"queued"', "{\"status\": \"running\", \"run\": [1]}", "{"])
def test_a_restart_while_a_job_runs_survives_a_damaged_ionomos_json(bed, body):
    """recover() rewrites each interrupted job's ionomos.json at start-up; valid JSON of the wrong shape made it
    raise, and `ionomos run` then stopped before the watcher started."""
    _take(bed, _drop(bed, "dia_good"))
    job = bed["ledger"].list()[0]
    bed["ledger"].start_attempt(job.id)  # FragPipe was running when the PC restarted
    (Path(job.dest_dir) / names.STATUS_FILE).write_text(body, encoding="utf-8")
    assert recover(bed["cfg"], bed["ledger"]) == [(job.id, "queued")]
    rec = json.loads((Path(job.dest_dir) / names.STATUS_FILE).read_text(encoding="utf-8"))
    assert rec["status"] == "queued"  # a mapping is kept and updated; anything else is replaced by the job's record
    assert rec.get("plan", job.parsed["plan"])["folder"]["user"] == "Sam"
    _drain(bed)
    assert bed["ledger"].get(job.id).status == "done"


def test_damaged_status_files_are_skipped_by_the_rebuild_and_the_hourly_adoption(bed, caplog):
    _take(bed, _drop(bed, "dia_good"))
    _take(bed, _drop(bed, "iso_good"))
    for i, body in enumerate(["null", "[]", '{"plan": {"folder": "x"}, "status": "queued"}', '{"plan": 5}',
                              '{"plan": {"folder": null}}', "\xff"]):
        d = bed["users"] / "ABC" / f"odd{i}"
        d.mkdir()
        (d / names.STATUS_FILE).write_text(body, encoding="utf-8")
    assert ledger_mod.adopt_orphans(bed["ledger"], bed["users"]) == []
    bed["ledger"].close()
    moved, n = ledger_mod.rebuild_from_status_files(bed["cfg"].database, bed["users"])
    assert n == 2 and moved.is_file()
    assert sum("skipping unreadable status file" in r.getMessage() for r in caplog.records) >= 12
    bed["ledger"] = Ledger(bed["cfg"].database)
    assert sorted(j.user for j in bed["ledger"].list()) == ["ABC", "Sam"]


def test_ionomos_stopping_while_it_files_a_folder_never_loses_it(bed, monkeypatch):
    """Between the move and the ionomos.json (renames, the raw headers read): a folder moved out of the inbox with
    no status file was in no list and never searched. Now the first record is written straight after the move."""
    folder = _drop(bed, "dia_good")

    def power_cut(dest, p):
        raise SystemExit("the PC restarted")

    monkeypatch.setattr(intake_mod, "_acquisition", power_cut)
    with pytest.raises(SystemExit):
        _take(bed, folder)
    dest = bed["users"] / "Sam" / folder.name
    rec = json.loads((dest / names.STATUS_FILE).read_text(encoding="utf-8"))
    assert rec["status"] == "queued" and rec["reason"] == FILING and not folder.exists()
    assert bed["ledger"].list() == []
    assert ledger_mod.adopt_orphans(bed["ledger"], bed["users"]) == []  # young: intake may still be filing it
    monkeypatch.setattr(ledger_mod, "FILING_GRACE_SECONDS", 0)
    assert len(ledger_mod.adopt_orphans(bed["ledger"], bed["users"])) == 1  # the next hourly check
    _drain(bed)
    assert bed["ledger"].list()[0].status == "done"


# ---------------------------------------------------------------------- ledger --


def test_a_job_list_locked_for_longer_than_the_wait_is_a_retry_not_a_rejection(bed):
    """A database viewer with an unsaved edit: the drop used to get a "send diagnostics" note and then stayed
    rejected after the lock was gone."""
    holder = sqlite3.connect(bed["cfg"].database, timeout=1)
    holder.execute("BEGIN EXCLUSIVE")
    bed["ledger"]._conn.execute("PRAGMA busy_timeout = 100")
    folder = _drop(bed, "dia_good")
    try:
        assert _take(bed, folder) == IntakeResult.RETRY
        assert folder.is_dir() and not note_path(folder).exists()
    finally:
        holder.rollback()
        holder.close()
    assert _take(bed, folder) == IntakeResult.QUEUED


def test_repair_ledger_with_a_journal_it_cannot_move_changes_nothing(bed, monkeypatch, capsys):
    from ionomos.cli import main

    _take(bed, _drop(bed, "dia_good"))
    bed["ledger"].close()
    db = bed["cfg"].database
    db.write_bytes(b"not a database" * 300)
    journal = db.with_name(db.name + "-journal")
    journal.write_bytes(b"hot journal")
    real = Path.replace

    def stuck(self, target):
        if self.name.endswith("-journal"):
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", stuck)
    assert main(["--config", str(bed["cfg_path"]), "repair-ledger"]) == 1
    err = capsys.readouterr().err
    assert "open in another program" in err and "Nothing was changed" in err
    assert db.read_bytes() == b"not a database" * 300 and journal.read_bytes() == b"hot journal"
    assert not list(db.parent.glob("*.broken-*"))
    monkeypatch.setattr(Path, "replace", real)
    assert main(["--config", str(bed["cfg_path"]), "repair-ledger"]) == 0
    assert len(list(db.parent.glob("*.broken-*"))) == 2  # the file and its journal, side by side
    assert len(Ledger(db).list()) == 1


@pytest.mark.skipif(not WINDOWS, reason="only Windows refuses to rename a file another program has open")
def test_repair_ledger_while_another_program_has_the_job_list_open(bed, capsys):
    from ionomos.cli import main

    _take(bed, _drop(bed, "dia_good"))
    bed["ledger"].close()
    db = bed["cfg"].database
    db.write_bytes(b"\0" * 4096)
    viewer = open(db, "rb")  # noqa: SIM115 - DB Browser, the app, a backup
    try:
        assert main(["--config", str(bed["cfg_path"]), "repair-ledger"]) == 1
        assert "close the Ionomos app" in capsys.readouterr().err
        assert not list(db.parent.glob("*.broken-*"))
    finally:
        viewer.close()
    assert main(["--config", str(bed["cfg_path"]), "repair-ledger"]) == 0


# ------------------------------------------------------------------------ lock --


def test_the_lock_of_a_watcher_that_died_does_not_stop_the_next_one(bed):
    """The watcher holds an OS lock on <log_dir>/ionomos.lock; killed (Task Manager, a power cut), it leaves the file
    behind with its pid in it. The next watcher must start, and a second live one must not."""
    log_dir = bed["cfg"].log_dir
    code = ("import sys, time; from pathlib import Path; from ionomos import health; "
            "lock = health.InstanceLock(Path(sys.argv[1])); lock.acquire(); print('held', flush=True); time.sleep(120)")
    p = subprocess.Popen([sys.executable, "-c", code, str(log_dir)], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        assert health.is_locked(log_dir)
        with pytest.raises(health.AlreadyRunning):
            health.InstanceLock(log_dir).acquire()
    finally:
        p.kill()
        p.wait(timeout=30)
        p.stdout.close()
    assert (log_dir / names.LOCK_FILE).is_file()  # left behind
    deadline = time.monotonic() + 10
    while health.is_locked(log_dir) and time.monotonic() < deadline:
        time.sleep(0.05)
    lock = health.InstanceLock(log_dir)
    lock.acquire()
    try:
        assert health.is_locked(log_dir)
    finally:
        lock.release()


# ---------------------------------------------------------------------- worker --


def test_a_job_whose_record_in_the_job_list_is_unreadable_fails_alone_and_the_queue_goes_on(bed):
    """A damaged parsed_json made every ledger.list() raise: the worker, the app and `ionomos status` all stopped."""
    _take(bed, _drop(bed, "dia_good"))
    _take(bed, _drop(bed, "iso_good"))
    first, second = bed["ledger"].list()
    bed["ledger"]._conn.execute("UPDATE jobs SET parsed_json='{broken' WHERE id=?", (first.id,))
    bed["ledger"]._conn.commit()
    assert bed["ledger"].list()[0].parsed == {}
    _drain(bed)
    assert bed["ledger"].get(second.id).status == "done"
    assert bed["ledger"].get(first.id).status in ("failed", "done")  # never left queued


def test_a_job_that_ionomos_cannot_prepare_fails_and_the_jobs_behind_it_still_run(bed, monkeypatch):
    """An unexpected error in prepare() escaped run_once, and the same first queued job raised again every poll:
    nothing behind it was ever searched."""
    from ionomos import runner

    _take(bed, _drop(bed, "dia_good"))
    _take(bed, _drop(bed, "iso_good"))
    first, second = bed["ledger"].list()
    real = runner.prepare

    def broken(job, cfg):
        if job.id == first.id:
            raise KeyError("plan")
        return real(job, cfg)

    monkeypatch.setattr(runner, "prepare", broken)
    _drain(bed)
    bad = bed["ledger"].get(first.id)
    assert bad.status == "failed" and "could not prepare the search: KeyError" in bad.reason
    assert (Path(bad.dest_dir) / "FAILED.txt").is_file()
    assert bed["ledger"].get(second.id).status == "done"
