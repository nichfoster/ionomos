from labwatch.ledger import Job, Ledger


def test_roundtrip_and_recovery(tmp_path):
    led = Ledger(tmp_path / "l.db")
    jid = led.insert(Job(inbox_name="a", user="EJQ", method="isoDTB", dest_dir="/x/a", parsed={"k": 1}))
    assert jid == 1
    assert led.already_taken("a") and not led.already_taken("b")
    j = led.next_queued()
    assert j.id == 1 and j.parsed == {"k": 1} and j.status == "queued"

    led.set_status(1, "running")
    assert led.get(1).started_at
    assert led.next_queued() is None

    # simulate restart
    assert led.recover_on_startup() == [1]
    j = led.get(1)
    assert j.status == "failed" and "interrupted" in j.reason and j.finished_at

    led.set_status(1, "queued")
    assert led.next_queued().id == 1
    assert [x.id for x in led.list("queued")] == [1]
