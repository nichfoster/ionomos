from labwatch.ledger import Job, Ledger


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
