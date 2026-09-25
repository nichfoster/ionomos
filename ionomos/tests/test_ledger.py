import json
import logging
import sqlite3
from datetime import datetime, timedelta

import pytest

from ionomos.ledger import Job, Ledger, LedgerError, adopt_orphans, backup, integrity, rebuild_from_status_files


def test_roundtrip_and_recovery(tmp_path):
    led = Ledger(tmp_path / "l.db")
    jid = led.insert(Job(inbox_name="a", user="EJQ", method="isoDTB", dest_dir="/x/a", parsed={"k": 1}))
    assert jid == 1
    assert led.already_taken("a") and not led.already_taken("b")
    j = led.next_queued()
    assert j.id == 1 and j.parsed == {"k": 1} and j.status == "queued"

    assert led.start_attempt(1) == 1
    assert led.get(1).started_at and led.get(1).status == "running"
    assert led.next_queued() is None

    # simulate restart: an interrupted job is re-queued...
    assert led.recover_on_startup() == [(1, "queued")]
    j = led.get(1)
    assert j.status == "queued" and "interrupted" in j.reason
    # ...until it has been started MAX_ATTEMPTS times
    led.start_attempt(1)
    led.start_attempt(1)
    assert led.recover_on_startup() == [(1, "failed")]
    j = led.get(1)
    assert j.status == "failed" and "interrupted 3 times" in j.reason and j.finished_at
    led.requeue(1, "retry", reset_attempts=True)
    assert led.get(1).attempts == 0 and led.get(1).status == "queued"

    led.set_status(1, "queued")
    assert led.next_queued().id == 1
    assert [x.id for x in led.list("queued")] == [1]


def test_migrates_old_ledger_without_attempts(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, inbox_name TEXT NOT NULL UNIQUE,
        user TEXT NOT NULL, method TEXT NOT NULL, dest_dir TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
        created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, parsed_json TEXT NOT NULL);
        INSERT INTO jobs (inbox_name,user,method,dest_dir,status,created_at,parsed_json)
        VALUES ('a','EJQ','isoDTB','/x','queued','2026-01-01','{}');""")
    c.commit()
    c.close()
    led = Ledger(db)
    assert led.get(1).attempts == 0
    assert led.start_attempt(1) == 1


# ------------------------------------------------- audit W1: contract edges --


def _job(name: str, dest: str | None = None) -> Job:
    return Job(inbox_name=name, user="EJQ", method="isoDTB", dest_dir=dest or f"/x/{name}", parsed={"k": 1})


def _write_status(dest, safe: str, status: str, user: str = "EJQ", method: str = "isoDTB",
                  queued_at: str = "2026-09-24T10:00:00+00:00") -> None:
    """An ionomos.json shaped like intake files it: plan.folder carries the filing identity."""
    rec = {"status": status, "queued_at": queued_at, "reason": None,
           "plan": {"folder": {"safe": safe, "user": user, "method": method}}}
    dest.mkdir(parents=True)
    (dest / "ionomos.json").write_text(json.dumps(rec), encoding="utf-8")


def test_duplicate_inbox_name_insert_raises_integrity_error(tmp_path):
    """UNIQUE(inbox_name) surfaces as sqlite3.IntegrityError; the rejected Job keeps id=None
    (insert only assigns cur.lastrowid after the INSERT succeeds)."""
    led = Ledger(tmp_path / "l.db")
    led.insert(_job("dup"))
    dup = _job("dup", dest="/x/other")
    with pytest.raises(sqlite3.IntegrityError):
        led.insert(dup)
    assert dup.id is None
    assert [j.inbox_name for j in led.list()] == ["dup"]


def test_set_status_invalid_status_raises_value_error(tmp_path):
    led = Ledger(tmp_path / "l.db")
    jid = led.insert(_job("a"))
    with pytest.raises(ValueError):
        led.set_status(jid, "cancelled")  # plausible real-world typo, not in STATUSES
    assert led.get(jid).status == "queued"


def test_set_status_queued_keeps_stale_timestamps(tmp_path):
    # CHARACTERIZATION: set_status only writes a timestamp column for running/done/failed, so
    # rewinding a finished job via set_status(id, "queued") leaves the previous run's
    # started_at AND finished_at on a queued row. Pinned deliberately — issue tracked by the
    # audit W5 pass. Invert or delete when fixed.
    led = Ledger(tmp_path / "l.db")
    jid = led.insert(_job("a"))
    led.start_attempt(jid)
    led.set_status(jid, "done", "ok")
    led.set_status(jid, "queued", "rewound")
    j = led.get(jid)
    assert j.status == "queued" and j.started_at and j.finished_at


def test_requeue_clears_finished_at_but_keeps_stale_started_at(tmp_path):
    # CHARACTERIZATION: requeue sets finished_at=NULL but leaves started_at from the last
    # attempt, so a requeued row still claims it was started. Pinned deliberately — issue
    # tracked by the audit W5 pass. Invert or delete when fixed.
    led = Ledger(tmp_path / "l.db")
    jid = led.insert(_job("a"))
    led.start_attempt(jid)
    led.set_status(jid, "failed", "boom")
    led.requeue(jid, "retry")
    j = led.get(jid)
    assert j.status == "queued" and j.finished_at is None and j.started_at is not None


def test_nonexistent_id_set_status_and_requeue_are_silent_no_ops(tmp_path):
    # CHARACTERIZATION: both UPDATE-by-id statements match no row and commit anyway, so a
    # wrong/removed id is silently ignored — no exception, no log. (The contrast is
    # start_attempt, which raises TypeError on a missing id — next test.) Pinned
    # deliberately — issue tracked by the audit W5 pass. Invert or delete when fixed.
    led = Ledger(tmp_path / "l.db")
    jid = led.insert(_job("a"))
    led.set_status(999, "done", "why")
    led.requeue(999, "why")
    j = led.get(jid)
    assert j.status == "queued" and j.reason is None


def test_start_attempt_on_missing_id_raises_ledger_error(tmp_path):
    # Inverted characterization (was: raw TypeError from None["attempts"]): a missing job
    # row now raises the typed LedgerError instead, and nothing is written for it.
    led = Ledger(tmp_path / "l.db")
    jid = led.insert(_job("a"))
    with pytest.raises(LedgerError, match="is not in the ledger"):
        led.start_attempt(jid + 12345)
    assert led.start_attempt(jid) == 1  # existing rows untouched: still the new attempt number


def test_recover_on_startup_fails_jobs_attempted_past_max(tmp_path):
    """A job interrupted more than MAX_ATTEMPTS times must fail, not re-queue, so a
    PC-killing job cannot loop forever (the == MAX boundary is covered by the roundtrip)."""
    led = Ledger(tmp_path / "l.db")
    jid = led.insert(_job("a"))
    for _ in range(4):  # one past MAX_ATTEMPTS (3)
        led.start_attempt(jid)
    assert led.recover_on_startup() == [(jid, "failed")]
    j = led.get(jid)
    assert j.status == "failed" and "interrupted 4 times" in j.reason and j.finished_at


def test_recover_on_startup_requeues_several_running_rows_in_one_pass(tmp_path):
    led = Ledger(tmp_path / "l.db")
    ids = [led.insert(_job(n)) for n in ("a", "b", "c")]
    for jid in ids:
        led.start_attempt(jid)
    led.set_status(led.insert(_job("finished-thing")), "done")  # not running: recovery must leave it
    assert sorted(led.recover_on_startup()) == [(1, "queued"), (2, "queued"), (3, "queued")]
    assert [j.status for j in led.list()] == ["queued", "queued", "queued", "done"]


def test_backup_missing_db_returns_none(tmp_path):
    assert backup(tmp_path / "nope.db", tmp_path / "backups") is None


def test_backup_rotates_old_dailies_beyond_keep(tmp_path):
    db = tmp_path / "l.db"
    Ledger(db).close()
    bdir = tmp_path / "backups"
    bdir.mkdir()
    today = datetime.now()
    for age in range(2, 18):  # 16 pre-existing dailies, dates safely in the past
        (bdir / f"ionomos-{(today - timedelta(days=age)):%Y%m%d}.db").write_bytes(b"x")
    dest = backup(db, bdir)  # default keep=14
    assert dest.is_file()
    files = sorted(bdir.glob("ionomos-*.db"))
    assert len(files) == 14  # 16 fakes + today's, the 3 oldest pruned
    assert f"ionomos-{(today - timedelta(days=17)):%Y%m%d}" not in {f.stem for f in files}  # oldest gone
    assert dest.name in {f.name for f in files}


def test_backup_same_day_early_return_skips_rotation(tmp_path):
    # CHARACTERIZATION: backup() returns today's existing file before reaching the rotation
    # loop (`if dest.exists(): return dest`), so a same-day second call never prunes — stale
    # dailies beyond `keep` survive until the next day's backup. Pinned deliberately —
    # issue tracked by the audit W5 pass. Invert or delete when fixed.
    db = tmp_path / "l.db"
    Ledger(db).close()
    bdir = tmp_path / "backups"
    first = backup(db, bdir, keep=2)
    today = datetime.now()
    for age in range(1, 4):  # 3 old dailies; with keep=2 any rotation run must prune one
        (bdir / f"ionomos-{(today - timedelta(days=age)):%Y%m%d}.db").write_bytes(b"x")
    second = backup(db, bdir, keep=2)
    assert second == first  # early return of today's file
    assert len(list(bdir.glob("ionomos-*.db"))) == 4  # unpruned: rotation was skipped


def test_integrity_missing_db_reports_missing(tmp_path):
    assert integrity(tmp_path / "nope.db") == "missing"


def test_rebuild_from_status_files_logs_corrupt_status_file(tmp_path, caplog):
    # Inverted characterization (was: silent skip): an unparseable ionomos.json is still
    # dropped from the rebuilt ledger, but a WARNING from ionomos.ledger now names it —
    # a rebuild must show what it left out.
    db = tmp_path / "l.db"
    Ledger(db).close()
    users = tmp_path / "users"
    _write_status(users / "EJQ" / "20260902-isoDTB_EJQ-2-027", "20260902-isoDTB_EJQ-2-027", "queued")
    broken = users / "Isaac" / "20260903-TMT_Isaac-1-001"
    broken.mkdir(parents=True)
    (broken / "ionomos.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="ionomos.ledger"):
        moved, n = rebuild_from_status_files(db, users)
    assert n == 1 and moved is not None and moved.is_file() and "broken" in moved.name
    assert [j.inbox_name for j in Ledger(db).list()] == ["20260902-isoDTB_EJQ-2-027"]
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "20260903-TMT_Isaac-1-001" in warns[0].getMessage()


def test_rebuild_from_status_files_logs_name_collision(tmp_path, caplog):
    # Inverted characterization (was: silent drop): still one survivor (insertion order by
    # queued_at picks it), but the dropped folder is now named in a WARNING.
    db = tmp_path / "l.db"
    Ledger(db).close()
    users = tmp_path / "users"
    _write_status(users / "EJQ" / "exp-1", "same-experiment", "queued",
                  queued_at="2026-09-24T10:00:00+00:00")
    _write_status(users / "Isaac" / "exp-2", "same-experiment", "queued", user="Isaac",
                  queued_at="2026-09-24T11:00:00+00:00")
    with caplog.at_level(logging.WARNING, logger="ionomos.ledger"):
        moved, n = rebuild_from_status_files(db, users)
    assert n == 1 and moved.is_file()
    jobs = Ledger(db).list()
    assert [j.inbox_name for j in jobs] == ["same-experiment"]  # one survivor, the other dropped
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "exp-2" in warns[0].getMessage()  # the loser is named


def test_adopt_orphans_skips_done_and_failed_records(tmp_path):
    # CHARACTERIZATION: adopt_orphans only re-queues records whose status is queued/running;
    # done/failed folders are left alone (and the skip is silent). Pinned deliberately —
    # issue tracked by the audit W5 pass. Invert or delete when fixed.
    led = Ledger(tmp_path / "l.db")
    users = tmp_path / "users"
    _write_status(users / "EJQ" / "exp-queued", "exp-queued", "queued")
    _write_status(users / "EJQ" / "exp-done", "exp-done", "done")
    _write_status(users / "Isaac" / "exp-failed", "exp-failed", "failed", user="Isaac")
    assert adopt_orphans(led, users) == [1]
    assert [j.inbox_name for j in led.list()] == ["exp-queued"]


def test_adopt_orphans_logs_name_collisions(tmp_path, caplog):
    # Inverted characterization (was: silent skip): colliding folders stay unadopted, but a
    # WARNING now names each one — a filed experiment missing from the ledger must be visible.
    led = Ledger(tmp_path / "l.db")
    led.insert(_job("same-experiment", dest=str(tmp_path / "elsewhere")))
    users = tmp_path / "users"
    _write_status(users / "EJQ" / "exp-1", "same-experiment", "queued")
    _write_status(users / "Isaac" / "exp-2", "same-experiment", "queued", user="Isaac")
    with caplog.at_level(logging.WARNING, logger="ionomos.ledger"):
        assert adopt_orphans(led, users) == []  # both collide with the existing row; no crash
    assert [j.dest_dir for j in led.list()] == [str(tmp_path / "elsewhere")]  # nothing adopted
    msgs = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "exp-1" in msgs and "exp-2" in msgs  # every collision is named


def test_adopt_orphans_by_design_skips_stay_quiet(tmp_path, caplog):
    # Hourly-noise guard: adopt_orphans runs every hour over every filed folder, so its
    # by-design skips (already in the ledger, done/failed records) must stay silent — only
    # abnormal skips (unreadable file, collision) may warn.
    led = Ledger(tmp_path / "l.db")
    users = tmp_path / "users"
    _write_status(users / "EJQ" / "exp-known", "exp-known", "queued")
    led.insert(_job("exp-known", dest=str(users / "EJQ" / "exp-known")))  # dest matches: already known
    _write_status(users / "EJQ" / "exp-done", "exp-done", "done")
    _write_status(users / "Isaac" / "exp-failed", "exp-failed", "failed", user="Isaac")
    _write_status(users / "Isaac" / "exp-fresh", "exp-fresh", "queued", user="Isaac")
    with caplog.at_level(logging.WARNING, logger="ionomos.ledger"):
        new = adopt_orphans(led, users)
    assert len(new) == 1  # only the genuinely orphaned folder is adopted (positive control)
    assert [j.inbox_name for j in led.list()] == ["exp-known", "exp-fresh"]
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_adopt_orphans_logs_unreadable_status_file(tmp_path, caplog):
    # Abnormal skip: an unparseable ionomos.json stays unadopted but is named in a WARNING;
    # the good folder beside it is still adopted.
    led = Ledger(tmp_path / "l.db")
    users = tmp_path / "users"
    broken = users / "EJQ" / "20260903-TMT_EJQ-1-001"
    broken.mkdir(parents=True)
    (broken / "ionomos.json").write_text("{not json", encoding="utf-8")
    _write_status(users / "Isaac" / "exp-good", "exp-good", "queued", user="Isaac")
    with caplog.at_level(logging.WARNING, logger="ionomos.ledger"):
        assert adopt_orphans(led, users) == [1]
    assert [j.inbox_name for j in led.list()] == ["exp-good"]
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "20260903-TMT_EJQ-1-001" in warns[0].getMessage()
