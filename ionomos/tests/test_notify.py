"""Notifications (notify.py, D58) and the Windows-safe log rotation.

No test here talks to the network: webhooks go to an HTTP server in this process on 127.0.0.1, email
to a small fake SMTP server on 127.0.0.1 (or to a stub of smtplib.SMTP for STARTTLS + login).
"""
import json
import logging
import socketserver
import threading
import time
import zipfile
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from ionomos import cli, configio, fragpipe, health, names, notify, service, testbed
from ionomos.config import ConfigError, load
from ionomos.intake import intake
from ionomos.ledger import Job, Ledger
from ionomos.worker import Worker

# ------------------------------------------------------------ fake servers --


class _Hook(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server's name
        srv = self.server
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        srv.got.append({"path": self.path, "type": self.headers.get("Content-Type"), "json": json.loads(body)})
        if srv.mode == "hang":  # accept the request, never answer (until the test ends)
            srv.release.wait(30)
            return
        if srv.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{srv.server_address[1]}/elsewhere")
            self.end_headers()
            return
        self.send_response(srv.mode)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):  # keep the test output clean
        pass


@pytest.fixture
def hook():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Hook)
    srv.daemon_threads = True
    srv.got, srv.mode, srv.release = [], 200, threading.Event()
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.release.set()
    notify.flush(5)
    srv.shutdown()
    srv.server_close()


class _SMTP(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib: no TLS, no login."""

    def handle(self):
        def say(line: str):
            self.wfile.write(line.encode() + b"\r\n")

        say("220 fake ESMTP")
        mail = {"rcpt": []}
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if up.startswith(("EHLO", "HELO")):
                say("250 fake")
            elif up.startswith("MAIL FROM:"):
                mail["from"] = cmd[10:].split()[0].strip("<>")
                say("250 ok")
            elif up.startswith("RCPT TO:"):
                mail["rcpt"].append(cmd[8:].strip("<> "))
                say("250 ok")
            elif up == "DATA":
                say("354 go on")
                data = b""
                while not data.endswith(b"\r\n.\r\n"):
                    more = self.rfile.readline()
                    if not more:  # the client went away mid-message
                        return
                    data += more
                mail["data"] = data.decode("utf-8", "replace")
                self.server.got.append(mail)
                say("250 queued")
            elif up == "QUIT":
                say("221 bye")
                return
            else:
                say("502 not here")


@pytest.fixture
def smtp():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTP)
    srv.daemon_threads = True
    srv.got = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _clean():
    notify._failing.clear()
    notify._mem_held.clear()
    yield
    notify.flush(5)


def _job(**kw) -> Job:
    base = dict(id=7, inbox_name="20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h", user="EJQ", method="isoDTB",
                dest_dir=str(Path("C:/Fragpipe_General/EJQ/20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h")), parsed={})
    base.update(kw)
    return Job(**base)


SUMMARY = {"report": "results/report.html",
           "comparisons": [{"name": "Compound vs DMSO", "up": 12, "down": 3, "tested": 4021}]}


def _settings(hook=None, **kw) -> dict:
    raw = {"enabled": True, "timeout_seconds": 5, **kw}
    if hook is not None:
        raw.update(webhook={"url": hook.url + "/generic"}, teams={"url": hook.url + "/teams"},
                   slack={"url": hook.url + "/slack"})
    return notify.settings_from(raw)


# ---------------------------------------------------------------- settings --


def test_off_by_default_and_nothing_is_sent(lab):
    s = lab["cfg"].notify
    assert s["enabled"] is False and notify.channels(s) == [] and notify.describe(s) == "off"
    assert s["on"] == ["done", "failed", "held"] and s["include_names"] is True
    assert notify.announce(lab["cfg"], "done", _job(), summary=SUMMARY) is None
    assert not (lab["cfg"].log_dir / names.NOTIFY_STATE).exists()


def test_channels_set_but_not_enabled_sends_nothing(lab, hook):
    cfg = replace(lab["cfg"], notify=_settings(hook, enabled=False))
    assert notify.announce(cfg, "failed", _job(), "boom") is None
    assert notify.flush() and hook.got == []


@pytest.mark.parametrize("block, message", [
    ("notify: [slack]", "notify: must be a mapping"),
    ("notify: {enabld: true}", "notify.enabld: unknown setting"),
    ("notify: {enabled: 'yes'}", "notify.enabled must be true or false"),
    ("notify: {include_names: 0}", "notify.include_names must be true or false"),
    ("notify: {on: [done, finished]}", "notify.on must be a list of done, failed, held"),
    ("notify: {on: []}", "notify.on must be a list"),
    ("notify: {timeout_seconds: 600}", "notify.timeout_seconds must be a number from 1 to 60"),
    ("notify: {enabled: true}", "notify.enabled is true but no channel is set"),
    ("notify: {slack: {url: 'http://hooks.example.org/x'}}", "notify.slack.url must start with https://"),
    ("notify: {teams: {url: 'file:///C:/x'}}", "notify.teams.url must be an https:// address"),
    ("notify: {webhook: {uri: x}}", "notify.webhook.uri: unknown setting"),
    ("notify: {slack: {url_env: 'https://hooks.slack.com/services/T/B/x'}}", "notify.slack.url_env must be the NAME"),
    ("notify: {email: {host: smtp.example.org, from: a@example.org}}", "notify.email.to: give at least one address"),
    ("notify: {email: {host: smtp.example.org, to: [a@example.org]}}", "notify.email.from: give the address"),
    ("notify: {email: {host: h, from: a@example.org, to: [nobody]}}", "'nobody' is not an email address"),
    ("notify: {email: {host: h, from: a@b.org, to: [c@d.org], port: smtp}}", "notify.email.port must be a number"),
    ("notify: {email: {host: h, from: a@b.org, to: [c@d.org], security: tls}}", "notify.email.security must be one of"),
    ("notify: {email: {host: h, from: a@b.org, to: [c@d.org], password: p}}", "notify.email.username is needed"),
    ("notify: {email: {host: h, from: a@b.org, to: [c@d.org], username: u, password: p, security: none}}",
     "would send the password unencrypted"),
    ("notify: {email: {host: h, from: a@b.org, to: [c@d.org], pass: p}}", "notify.email.pass: unknown setting"),
])
def test_config_validation_errors(lab, block, message):
    lab["cfg_path"].write_text(yaml.safe_dump(lab["cfg_dict"]) + block + "\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load(lab["cfg_path"])
    assert message in str(exc.value)
    assert "hooks.slack.com/services" not in str(exc.value)  # an error never repeats an address


def test_a_bare_on_key_is_read_as_on(lab):
    # YAML 1.1 turns the key `on` into the boolean true; both spellings must work
    for line in ("on: [failed]", "'on': [failed]", "on: failed"):
        lab["cfg_path"].write_text(yaml.safe_dump(lab["cfg_dict"]) + f"notify:\n  {line}\n", encoding="utf-8")
        assert load(lab["cfg_path"]).notify["on"] == ["failed"], line


def test_config_writer_round_trips_the_block(tmp_path):
    p = tmp_path / "config.yaml"
    d = configio.defaults(str(tmp_path / "A"), str(tmp_path / "G"))
    assert d["notify"]["enabled"] is False
    d["notify"].update(enabled=True, on=["failed", "held"], include_names=False, timeout_seconds=4)
    d["notify"]["slack"] = {"url": "https://hooks.slack.com/services/T0/B0/k#x y", "url_env": ""}
    d["notify"]["teams"]["url_env"] = "IONOMOS_TEAMS_URL"
    d["notify"]["email"].update(host="smtp.example.org", port=465, security="ssl", username="lab",
                                password_env="IONOMOS_SMTP_PASSWORD", to=["a@example.org", "b@example.org"],
                                **{"from": "Ionomos <pc@example.org>"})
    configio.write_config(p, d)
    text = p.read_text(encoding="utf-8")
    assert "notify:   #" in text and "  on: [failed, held]" in text
    back = configio.read_config(p)
    assert back["notify"] == d["notify"]
    assert configio.dump_config(back) == text  # an app Save changes nothing
    cfg = load(p, check_paths=False)
    assert cfg.notify == notify.settings_from(d["notify"])
    assert notify.channels(cfg.notify) == ["teams", "slack", "email"]
    # a hand-written, partial block (shorthand address, bare `on:`) survives a Save too
    raw = yaml.safe_load(text)
    raw["notify"] = {True: ["done"], "enabled": True, "slack": "https://hooks.slack.com/services/T0/B0/k"}
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    again = configio.read_config(p)
    p.write_text(configio.dump_config(again), encoding="utf-8")
    s = load(p, check_paths=False).notify
    assert s["on"] == ["done"] and s["slack"]["url"].endswith("/B0/k") and s["email"]["host"] == ""


def test_example_config_has_the_block_off():
    example = Path(notify.__file__).resolve().parents[2] / "config.example.yaml"
    raw = yaml.safe_load(example.read_text(encoding="utf-8"))
    s = notify.settings_from(raw["notify"])
    assert s["enabled"] is False and notify.channels(s) == []


# ---------------------------------------------------------------- messages --


def test_message_with_names_carries_only_the_listed_things():
    s = _settings(slack="https://hooks.slack.com/services/T0/B0/k")
    msg = notify.build("done", _job(), s, summary=SUMMARY)
    f = msg.fields
    assert set(f) <= {"app", "version", "event", "job_id", "time", "experiment", "user", "method", "hits", "report",
                      "pc", "text"}
    assert (f["event"], f["job_id"], f["user"], f["method"]) == ("done", 7, "EJQ", "isoDTB")
    assert f["experiment"] == "20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h"
    assert f["hits"] == [{"comparison": "Compound vs DMSO", "up": 12, "down": 3, "tested": 4021}]
    assert Path(f["report"]) == Path(_job().dest_dir) / "results" / "report.html"
    assert msg.title == "Ionomos: EJQ/20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h done"
    assert "Compound vs DMSO: 12 up, 3 down of 4021" in msg.text

    failed = notify.build("failed", _job(), s, "FragPipe ran out of memory\n  (exit 1)" + " x" * 1000)
    assert failed.fields["reason"].startswith("FragPipe ran out of memory (exit 1)")
    assert len(failed.fields["reason"]) == notify.MAX_REASON and "hits" not in failed.fields
    held = notify.build("held", _job(), s, "low disk space: 3 GB free on C:\\")
    assert held.title.endswith(" waiting") and ("Waiting for", "low disk space: 3 GB free on C:\\") in held.facts


def test_include_names_false_sends_the_job_number_and_status_only(hook, smtp):
    s = _settings(hook, include_names=False,
                  email={"host": "127.0.0.1", "port": smtp.server_address[1], "security": "none",
                         "from": "pc@example.org", "to": ["a@example.org"]})
    for event, reason in (("done", ""), ("failed", "EJQ's FASTA is gone"), ("held", "FASTA for isoDTB missing")):
        msg = notify.build(event, _job(), s, reason, SUMMARY)
        assert set(msg.fields) == {"app", "version", "event", "job_id", "time", "text"}
        assert msg.facts == ()
        assert all(r.ok for r in notify.deliver(s, msg)), event
    sent = json.dumps([g["json"] for g in hook.got]) + "".join(m["data"] for m in smtp.got)
    assert len(hook.got) == 9 and len(smtp.got) == 3
    for private in ("EJQ", "isoDTB", "1uM", "Fragpipe_General", "report.html", "Compound", "FASTA", "4021",
                    notify._pc() or "no-pc-name"):
        assert private not in sent, private
    assert "Ionomos: job 7 done" in sent and "Ionomos: job 7 failed" in sent and "Ionomos: job 7 waiting" in sent


def test_payload_shapes(hook):
    s = _settings(hook)
    msg = notify.build("done", _job(), s, summary=SUMMARY)
    results = notify.deliver(s, msg)
    assert [(r.channel, r.ok, r.detail) for r in results] == [
        ("webhook", True, "HTTP 200"), ("teams", True, "HTTP 200"), ("slack", True, "HTTP 200")]
    got = {g["path"]: g for g in hook.got}
    assert all(g["type"] == "application/json" for g in got.values())
    assert got["/generic"]["json"] == msg.fields

    slack = got["/slack"]["json"]  # Slack incoming webhook: {"text": ...}
    assert list(slack) == ["text"] and slack["text"].startswith("*Ionomos: EJQ/20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h done*\n")
    assert "Compound vs DMSO: 12 up, 3 down of 4021" in slack["text"]

    teams = got["/teams"]["json"]  # Teams: a message with one Adaptive Card attachment
    assert teams["type"] == "message" and len(teams["attachments"]) == 1
    att = teams["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive" and att["contentUrl"] is None
    card = att["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.2"
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert card["body"][0] == {"type": "TextBlock", "text": msg.title, "weight": "Bolder", "size": "Medium", "wrap": True}
    assert card["body"][1]["type"] == "FactSet"
    assert {"title": "User", "value": "EJQ"} in card["body"][1]["facts"]
    assert all(set(f) == {"title", "value"} and isinstance(f["value"], str) for f in card["body"][1]["facts"])


def test_email_goes_out_over_smtp(smtp):
    s = notify.settings_from({"enabled": True, "email": {
        "host": "127.0.0.1", "port": smtp.server_address[1], "security": "none",
        "from": "Ionomos <pc@example.org>", "to": ["a@example.org", "b@example.org"]}})
    msg = notify.build("failed", _job(), s, "FragPipe ran out of memory")
    assert notify.deliver(s, msg) == [notify.Result("email", True, "accepted by 127.0.0.1")]
    (mail,) = smtp.got
    assert mail["from"] == "pc@example.org" and mail["rcpt"] == ["a@example.org", "b@example.org"]
    assert "Subject: Ionomos: EJQ/20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h failed" in mail["data"]
    assert "Reason: FragPipe ran out of memory" in mail["data"] and "To: a@example.org, b@example.org" in mail["data"]


def test_email_starts_tls_before_login_and_takes_the_password_from_the_environment(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(("connect", host, port, timeout))

        def starttls(self, context=None):
            calls.append(("starttls", context is not None))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, em):
            calls.append(("send", em["Subject"]))
            return {}

        def quit(self):
            calls.append(("quit",))

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("IONOMOS_TEST_SMTP_PW", "from-the-environment")
    s = notify.settings_from({"enabled": True, "timeout_seconds": 3, "email": {
        "host": "smtp.example.org", "username": "lab", "password": "from-the-file", "password_env": "IONOMOS_TEST_SMTP_PW",
        "from": "pc@example.org", "to": ["a@example.org"]}})
    assert notify.deliver(s, notify.sample_message(s))[0].ok
    assert calls == [("connect", "smtp.example.org", 587, 3), ("starttls", True),
                     ("login", "lab", "from-the-environment"), ("send", "Ionomos: test message"), ("quit",)]
    assert set(notify.secrets(s)) == {"from-the-environment", "from-the-file"}

    calls.clear()
    monkeypatch.delenv("IONOMOS_TEST_SMTP_PW")
    assert notify.deliver(s, notify.sample_message(s))[0].ok and ("login", "lab", "from-the-file") in calls
    only_env = notify.settings_from({"email": {"host": "h", "username": "lab", "password_env": "IONOMOS_TEST_SMTP_PW",
                                               "from": "pc@example.org", "to": ["a@example.org"]}})
    (r,) = notify.deliver(only_env, notify.sample_message(only_env))
    assert not r.ok and r.detail == "environment variable IONOMOS_TEST_SMTP_PW (email.password_env) is not set"


def test_webhook_address_from_the_environment(hook, monkeypatch):
    s = notify.settings_from({"enabled": True, "slack": {"url_env": "IONOMOS_TEST_SLACK_URL"}})
    (r,) = notify.send_test(s)
    assert not r.ok and r.detail == "environment variable IONOMOS_TEST_SLACK_URL (slack.url_env) is not set"
    monkeypatch.setenv("IONOMOS_TEST_SLACK_URL", hook.url + "/secret-key")
    assert notify.send_test(s) == [notify.Result("slack", True, "HTTP 200")]
    assert hook.got[0]["path"] == "/secret-key" and hook.got[0]["json"]["text"].startswith("*Ionomos: test message*")
    monkeypatch.setenv("IONOMOS_TEST_SLACK_URL", "ftp://example.org/x")
    (r,) = notify.send_test(s)
    assert not r.ok and "https://" in r.detail and len(hook.got) == 1


def test_errors_are_plain_and_never_show_the_address(hook):
    s = _settings(slack={"url": hook.url + "/services/T0/B0/secret-key"})
    hook.mode = 404
    (r,) = notify.send_test(s)
    assert (r.ok, r.detail) == (False, "the server answered HTTP 404 Not Found")
    hook.mode = "redirect"  # a redirect is not followed: the message goes to the configured address only
    (r,) = notify.send_test(s)
    assert not r.ok and "302" in r.detail and [g["path"] for g in hook.got].count("/elsewhere") == 0
    dead = notify.settings_from({"enabled": True, "timeout_seconds": 1,
                                 "webhook": {"url": "http://127.0.0.1:9/hook/secret-key"}})  # nothing listens there
    (r,) = notify.send_test(dead)
    assert not r.ok and r.detail.startswith(("could not connect", "no answer in time"))
    assert "secret-key" not in r.detail


# -------------------------------------------------------------- the worker --


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE", raising=False)
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": Ledger(cfg.database)}


def _queue(bed, sample="iso_good") -> Path:
    folder = testbed.drop(bed["root"], sample)
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def test_done_job_notifies_after_the_status_is_recorded(bed, hook, caplog):
    dest = _queue(bed)
    cfg = replace(bed["cfg"], notify=_settings(webhook={"url": hook.url + "/hook/secret-key"}))
    with caplog.at_level(logging.INFO):
        assert Worker(cfg, bed["ledger"]).run_once()
        assert notify.flush()
    job = bed["ledger"].get(1)
    assert job.status == "done"
    (got,) = hook.got
    f = got["json"]
    assert (f["event"], f["job_id"], f["user"], f["method"], f["experiment"]) == ("done", 1, job.user, "isoDTB", job.inbox_name)
    assert Path(f["report"]) == dest / "results" / "report.html" and Path(f["report"]).is_file()
    assert f["hits"] and all(set(h) == {"comparison", "up", "down", "tested"} for h in f["hits"])
    assert "notified by webhook: job 1 done" in caplog.text and "secret-key" not in caplog.text
    assert not [k for k in f if k not in ("app", "version", "event", "job_id", "time", "experiment", "user", "method",
                                          "reason", "hits", "report", "pc", "text")]


def test_failed_job_notifies_with_the_reason_and_a_cancel_does_not(bed, hook, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "oom")
    _queue(bed)
    cfg = replace(bed["cfg"], notify=_settings(slack={"url": hook.url + "/slack"}))
    w = Worker(cfg, bed["ledger"])
    assert w.run_once() and notify.flush()
    job = bed["ledger"].get(1)
    assert job.status == "failed"
    (got,) = hook.got
    assert got["json"]["text"].startswith(f"*Ionomos: {job.user}/{job.inbox_name} failed*")
    assert f"Reason: {' '.join(job.reason.split())[:80]}" in got["json"]["text"]
    w._fail(job, "cancelled by user")  # the person who cancelled knows
    assert notify.flush() and len(hook.got) == 1


def test_only_the_chosen_events_notify(bed, hook):
    _queue(bed)
    cfg = replace(bed["cfg"], notify=_settings(hook, on=["failed"]))
    assert Worker(cfg, bed["ledger"]).run_once() and notify.flush()
    assert bed["ledger"].get(1).status == "done" and hook.got == []


def test_held_notifies_once_per_reason_not_on_every_poll(bed, hook, monkeypatch):
    free = [1.0]
    monkeypatch.setattr(fragpipe, "_disk_free_gb", lambda p: free[0])
    _queue(bed)
    cfg = replace(bed["cfg"], min_free_gb=20, notify=_settings(webhook={"url": hook.url + "/hook"}))
    w = Worker(cfg, bed["ledger"])
    for _ in range(5):
        assert not w.run_once()
    assert notify.flush() and len(hook.got) == 1
    assert hook.got[0]["json"]["event"] == "held" and "low disk space" in hook.got[0]["json"]["reason"]

    free[0] = 2.0  # the same reason with another number is still the same reason
    assert not w.run_once()
    notify._mem_held.clear()  # a restart: the watcher forgets, the file in the log folder does not
    w2 = Worker(cfg, bed["ledger"])
    assert not w2.run_once() and not w2.run_once()
    assert notify.flush() and len(hook.got) == 1
    state = json.loads((cfg.log_dir / names.NOTIFY_STATE).read_text(encoding="utf-8"))
    assert list(state["held"]) == ["1"] and len(state["held"]["1"]) == 1

    free[0] = 500.0  # space is back, but now the FASTA is gone: a new reason, a second message
    fasta = cfg.fasta_dir / cfg.methods["isoDTB"].fasta
    fasta.rename(fasta.with_suffix(".away"))
    monkeypatch.setattr(fragpipe, "workflow_db_path", lambda text: None)
    assert not w2.run_once() and not w2.run_once()
    assert notify.flush() and len(hook.got) == 2 and "FASTA" in hook.got[1]["json"]["reason"]

    fasta.with_suffix(".away").rename(fasta)  # fixed: it runs, says done, and the held memory is cleared
    assert w2.run_once() and notify.flush()
    assert bed["ledger"].get(1).status == "done"
    assert [g["json"]["event"] for g in hook.got] == ["held", "held", "done"]
    assert json.loads((cfg.log_dir / names.NOTIFY_STATE).read_text(encoding="utf-8"))["held"] == {}


def test_held_event_switched_off_sends_nothing(bed, hook, monkeypatch):
    monkeypatch.setattr(fragpipe, "_disk_free_gb", lambda p: 1.0)
    _queue(bed)
    cfg = replace(bed["cfg"], min_free_gb=20, notify=_settings(hook, on=["done", "failed"]))
    assert not Worker(cfg, bed["ledger"]).run_once()
    assert notify.flush() and hook.got == []
    assert bed["ledger"].get(1).status == "queued"


def test_a_dead_webhook_does_not_change_or_delay_the_job(bed, hook, caplog):
    hook.mode = "hang"  # takes the request and never answers: each channel costs its whole timeout
    dest = _queue(bed)
    cfg = replace(bed["cfg"], notify=_settings(timeout_seconds=1, webhook={"url": hook.url + "/a"},
                                               slack={"url": hook.url + "/b"}))
    with caplog.at_level(logging.INFO):
        assert Worker(cfg, bed["ledger"]).run_once()
        # the worker is back, the job is finished and on disk, and the message is still waiting for an answer
        assert not notify.flush(0), "the send must run in its own thread, not in the worker"
        job = bed["ledger"].get(1)
        assert job.status == "done" and job.attempts == 1 and (dest / "DONE.txt").is_file()
        assert json.loads((dest / names.STATUS_FILE).read_text(encoding="utf-8"))["status"] == "done"
        assert notify.flush(20)
        t0 = time.monotonic()
        notify.announce(cfg, "failed", job, "x")  # what the worker calls: it returns at once
        assert time.monotonic() - t0 < 0.5
        assert notify.flush(20)
    warned = [r.getMessage() for r in caplog.records if r.name == "ionomos.notify" and r.levelno == logging.WARNING]
    # logged once per channel, although two messages failed; one try per channel and message, no retries
    assert len(warned) == 2 and all("no answer in time" in m and "job 1 done" in m for m in warned)
    assert len(hook.got) == 4
    assert hook.url not in caplog.text
    assert bed["ledger"].get(1).status == "done" and not (dest / "FAILED.txt").exists()

    caplog.clear()
    hook.mode = 200  # a channel that works again says so once
    with caplog.at_level(logging.INFO):
        notify.announce(cfg, "failed", job, "x")
        assert notify.flush(20)
        notify.announce(cfg, "failed", job, "x")
        assert notify.flush(20)
    assert caplog.text.count("(it works again)") == 2 and caplog.text.count("notified by") == 4


def test_announce_never_raises(lab, monkeypatch):
    cfg = replace(lab["cfg"], notify=_settings(slack="https://hooks.slack.com/services/T0/B0/k"))

    def boom(*a, **k):
        raise RuntimeError("https://hooks.slack.com/services/T0/B0/k")

    monkeypatch.setattr(notify, "build", boom)
    assert notify.announce(cfg, "done", _job()) is None
    monkeypatch.undo()
    monkeypatch.setattr(notify, "_post", boom)  # an unexpected error while sending: reported, address hidden
    (r,) = notify.send_test(cfg.notify)
    assert not r.ok and r.detail == "RuntimeError: ***"


# --------------------------------------------------------------- redaction --

SLACK = "https://hooks.slack.com/services/T0AAAA/B0BBBB/cccSECRETccc"
TEAMS = "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?sig=SIGSECRET"
GENERIC = "https://lab.example.org/hooks/ionomos?token=TOKENSECRET"


def test_redact_config_text():
    text = (f"notify:\n  enabled: true\n  slack:\n    url: {SLACK}   # ours\n  teams: {{url: '{TEAMS}'}}\n"
            f"  webhook:\n    url: \"{GENERIC}\"\n    url_env: ''   # empty\n"
            "  email:\n    host: smtp.example.org\n    password: hunter2 #1\n    password_env: SMTP_PW\n")
    out = notify.redact_config_text(text, notify.raw_secrets(yaml.safe_load(text)))
    for secret in (SLACK, TEAMS, GENERIC, "SECRET", "hunter2"):
        assert secret not in out, secret
    assert "    url: ***   # ours" in out and "password: ***" in out
    assert "url_env: ''   # empty" in out and "password_env: SMTP_PW" in out and "host: smtp.example.org" in out
    # a config that doesn't parse is still redacted, line by line
    broken = "notify:\n  email: {password: [oops\n    password: 'hunter2 # x'\n    url: https://x.example.org/a#b\n"
    out = notify.redact_config_text(broken, notify.raw_secrets(None))
    assert "hunter2" not in out and "x.example.org" not in out and out.count("***") == 2
    assert notify.scrub(f"POST {SLACK} failed") == "POST *** failed"


def test_diagnostics_and_bundle_never_hold_a_secret(bed, monkeypatch, capsys):
    monkeypatch.setenv("IONOMOS_TEST_TEAMS_URL", TEAMS)
    monkeypatch.setenv("IONOMOS_TEST_SMTP_PW", "env-pass-SECRET")
    raw = yaml.safe_load(bed["cfg_path"].read_text(encoding="utf-8"))
    raw["notify"] = {"enabled": True, "slack": {"url": SLACK}, "webhook": {"url": GENERIC},
                     "teams": {"url_env": "IONOMOS_TEST_TEAMS_URL"},
                     "email": {"host": "smtp.example.org", "username": "lab", "password": "file-pass-SECRET",
                               "password_env": "IONOMOS_TEST_SMTP_PW", "from": "pc@example.org",
                               "to": ["a@example.org"]}}
    bed["cfg_path"].write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load(bed["cfg_path"])
    # worst case: something wrote the addresses and the password into the watcher log and a crash file
    (cfg.log_dir / names.LOG_FILE).write_text(
        f"2026-10-01 10:00:00 WARNING ionomos.x: posting to {GENERIC} and {SLACK}\n"
        f"2026-10-01 10:00:01 ERROR   ionomos.x: login lab / file-pass-SECRET, env-pass-SECRET at {TEAMS}\n",
        encoding="utf-8")
    health.write_crash_file(cfg.log_dir, "notify", f"Traceback: urlopen({GENERIC!r})")

    def clean(text: str, where: str):
        for secret in (SLACK, TEAMS, GENERIC, "SECRET"):
            assert secret not in text, f"{secret} in {where}"

    text = service.diagnostics(bed["cfg_path"])
    clean(text, "diagnostics")
    assert "=== config.yaml" in text and "password: ***" in text and "url: ***" in text
    assert "notifications" in text and "on: webhook, teams, slack, email" in text  # check names channels, no addresses
    assert "posting to *** and ***" in text

    assert cli.main(["--config", str(bed["cfg_path"]), "diagnose"]) == 0
    clean(capsys.readouterr().out, "ionomos diagnose")
    for saved in cfg.log_dir.glob("diagnostics-*.txt"):
        clean(saved.read_text(encoding="utf-8"), saved.name)

    z = zipfile.ZipFile(service.save_diagnostics_zip(bed["cfg_path"]))
    assert {"report.txt", "config.yaml", f"logs/{names.LOG_FILE}"} <= set(z.namelist())
    for name in z.namelist():
        clean(z.read(name).decode("utf-8", "replace"), f"bundle/{name}")
    assert "smtp.example.org" in z.read("config.yaml").decode()  # the rest of the block is still there to debug with
    report = service.save_problem_report(bed["cfg_path"], note="it broke", dest_dir=bed["root"])
    z = zipfile.ZipFile(report)
    for name in z.namelist():
        clean(z.read(name).decode("utf-8", "replace"), f"report/{name}")
    # the real config is untouched
    assert SLACK in bed["cfg_path"].read_text(encoding="utf-8")


# --------------------------------------------------------------------- CLI --


def _with_notify(bed, block: dict) -> list[str]:
    raw = yaml.safe_load(bed["cfg_path"].read_text(encoding="utf-8"))
    raw["notify"] = block
    bed["cfg_path"].write_text(yaml.safe_dump(raw), encoding="utf-8")
    return ["--config", str(bed["cfg_path"]), "notify-test"]


def test_cli_notify_test(bed, hook, smtp, capsys):
    assert cli.main(["--config", str(bed["cfg_path"]), "notify-test"]) == 1
    assert "notifications are not set up" in capsys.readouterr().out and hook.got == []

    argv = _with_notify(bed, {"enabled": False, "slack": {"url": hook.url + "/slack/secret-key"},
                              "email": {"host": "127.0.0.1", "port": smtp.server_address[1], "security": "none",
                                        "from": "pc@example.org", "to": ["a@example.org"]}})
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "notify.enabled is false" in out and "✓ slack    sent: HTTP 200" in out
    assert "✓ email    sent: accepted by 127.0.0.1" in out and "all sent" in out and "secret-key" not in out
    assert len(hook.got) == 1 and len(smtp.got) == 1 and "Subject: Ionomos: test message" in smtp.got[0]["data"]

    hook.mode = 500
    argv = _with_notify(bed, {"enabled": True, "timeout_seconds": 1, "slack": {"url": hook.url + "/slack/secret-key"},
                              "webhook": {"url": "http://127.0.0.1:9/nothing-here"}})
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert "✗ slack    NOT sent: the server answered HTTP 500" in out and "✗ webhook  NOT sent: " in out
    assert "2 of 2 could not be sent; jobs are not affected" in out and "secret-key" not in out

    assert cli.main(["--config", str(bed["cfg_path"]), "check"]) in (0, 1)
    out = capsys.readouterr().out
    assert "notifications" in out and "on: webhook, slack" in out and "127.0.0.1" not in out


# ------------------------------------------------------------ log rotation --


def _logger(handler) -> logging.Logger:
    lg = logging.Logger("rotation-test")
    handler.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(handler)
    return lg


def test_log_rotates_by_size_and_keeps_n_files(tmp_path):
    h = health.rotating_log_handler(tmp_path / names.LOG_FILE, max_bytes=2000, backups=3)
    lg = _logger(h)
    for i in range(400):
        lg.info("line %04d %s", i, "x" * 40)
    h.close()
    files = names.log_files(tmp_path)
    assert [f.name for f in files] == ["ionomos.log", "ionomos.log.1", "ionomos.log.2", "ionomos.log.3"]
    assert all(f.stat().st_size <= 2000 for f in files)
    assert files[0].read_text(encoding="utf-8").splitlines()[-1].startswith("line 0399")
    (tmp_path / "labwatch.log").write_text("old\n", encoding="utf-8")  # a LabWatch-era log is still found
    assert names.log_files(tmp_path)[-1].name == "labwatch.log"
    default = health.rotating_log_handler(tmp_path / "other.log")
    assert (default.maxBytes, default.backupCount) == (names.LOG_MAX_BYTES, names.LOG_BACKUPS) == (5_000_000, 5)
    default.close()


def test_a_locked_log_file_loses_no_lines_and_rotates_later(tmp_path, monkeypatch, capsys):
    """Windows refuses to rename a file another process has open. The watcher must keep logging."""
    h = health.rotating_log_handler(tmp_path / names.LOG_FILE, max_bytes=1000, backups=2)
    lg = _logger(h)
    locked = [True]
    real = h.rotate

    def rotate(source, dest):
        if locked[0]:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real(source, dest)

    monkeypatch.setattr(h, "rotate", rotate)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    for i in range(100):
        lg.info("line %03d %s", i, "y" * 40)
    assert "PermissionError" not in capsys.readouterr().err  # nothing reached logging's error handler
    log_file = tmp_path / names.LOG_FILE
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert [ln[:8] for ln in lines] == [f"line {i:03d}" for i in range(100)]  # every line, in one growing file
    assert log_file.stat().st_size > 1000 and not (tmp_path / "ionomos.log.1").exists()

    locked[0] = False
    lg.info("still inside the retry pause")
    assert not (tmp_path / "ionomos.log.1").exists()
    h._retry_at = 0.0  # the minute is over
    lg.info("after the lock is gone")
    h.close()
    assert (tmp_path / "ionomos.log.1").read_text(encoding="utf-8").splitlines()[-1] == "still inside the retry pause"
    assert log_file.read_text(encoding="utf-8") == "after the lock is gone\n"
