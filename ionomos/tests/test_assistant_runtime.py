"""Sharing the PC with a search (assistant/runtime.py, D72): keep_alive, while_searching:, the worker's state read
from its heartbeat, what goes into the request, and the time to the first token. The model is always the scripted
fake (assistant/fake.py): nothing here opens a socket."""
import json
import time
from dataclasses import replace

import pytest
import yaml

from ionomos import assistant, configio, health, testbed
from ionomos.assistant import audit, client, fake, runtime
from ionomos.config import ConfigError, load
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker


@pytest.fixture
def bed(tmp_path, monkeypatch):
    """A testbed with job 1 failed in the fake FragPipe (out of memory)."""
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "oom")
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    led = Ledger(cfg.database)
    assert intake(testbed.drop(tmp_path / "bed", "iso_good"), cfg, led).value == "queued"
    Worker(cfg, led).run_once()
    led.close()
    return {"cfg": cfg, "cfg_path": cfg_path, "audit": tmp_path / "audit.jsonl"}


def _with(cfg, **block):
    return replace(cfg, assistant=assistant.settings_from({"enabled": True, "model": "big", "stream": False, **block}))


ANSWER = [{"content": "FragPipe ran out of memory [log:1#257]."}]


# ----------------------------------------------------------------- settings --


@pytest.mark.parametrize(("given", "sent"), [
    ("", ""), (None, ""), ("5m", "5m"), ("30s", "30s"), ("1h30m", "1h30m"), ("0", 0), ("-1", -1), (300, 300),
    ("300", 300), (2.5, 2.5), (0, 0), (-1, -1), ("1.5h", "1.5h"),
])
def test_keep_alive_is_sent_as_ollama_reads_it(given, sent):
    assert runtime.keep_alive_value(given) == sent


@pytest.mark.parametrize("bad", ["soon", "5 minutes", "5min", "m5", True, "1d"])
def test_keep_alive_that_ollama_would_not_read_is_a_config_error(bad):
    with pytest.raises(ValueError, match="keep_alive must be a duration"):
        runtime.keep_alive_value(bad)


def test_settings_and_config_errors(lab):
    s = assistant.settings_from(None)
    assert s["keep_alive"] == "" and s["while_searching"] == {}
    s = assistant.settings_from({"keep_alive": "2m", "while_searching": {"model": " small ", "keep_alive": "30s",
                                                                        "timeout_seconds": "300", "pause": False,
                                                                        "base_url": ""}})
    assert s["keep_alive"] == "2m"
    assert s["while_searching"] == {"model": "small", "keep_alive": "30s", "timeout_seconds": 300.0, "pause": False}

    def with_block(block):
        lab["cfg_path"].write_text(yaml.safe_dump({**lab["cfg_dict"], "assistant": block}))
        return load(lab["cfg_path"])

    for block, msg in (({"keep_alive": "soon"}, "assistant.keep_alive must be a duration"),
                       ({"while_searching": "small"}, "assistant.while_searching must be a mapping"),
                       ({"while_searching": {"num_thread": 4}}, "assistant.while_searching.num_thread: unknown setting"),
                       ({"while_searching": {"pause": "yes"}}, "assistant.while_searching.pause must be true or false"),
                       ({"while_searching": {"timeout_seconds": 0}}, "between 1 and 3600"),
                       ({"while_searching": {"model": 4}}, "assistant.while_searching.model must be a text")):
        with pytest.raises(ConfigError, match=msg):
            with_block(block)
    # an address off this PC in while_searching is, like base_url, refused when asked, not at load
    cfg = with_block({"enabled": True, "model": "m", "while_searching": {"base_url": "http://10.0.0.5:8080/v1"}})
    assert cfg.assistant["while_searching"]["base_url"] == "http://10.0.0.5:8080/v1"


def test_the_app_keeps_the_new_settings_when_it_saves(lab):
    d = configio.read_config(lab["cfg_path"])
    d["assistant"] = {"enabled": True, "model": "m", "keep_alive": "2m",
                      "while_searching": {"model": "m-t4", "keep_alive": "30s", "pause": False}}
    back = yaml.safe_load(configio.dump_config(d))
    assert back["assistant"] == d["assistant"]
    assert assistant.settings_from(back["assistant"])["while_searching"]["model"] == "m-t4"


# -------------------------------------------------------- is a search running --


def test_a_search_is_running_while_the_worker_says_so_in_its_heartbeat(tmp_path):
    assert runtime.search_running(None) == (False, "")
    assert runtime.search_running(tmp_path) == (False, "")  # no heartbeat: no watcher, nothing searching
    hb = health.Heartbeat(tmp_path)
    hb.beat("watcher", "ok", force=True)
    assert runtime.search_running(tmp_path) == (False, "")
    hb.beat("worker", "running job 3: MSFragger", force=True)
    assert runtime.search_running(tmp_path) == (True, "job 3: MSFragger")
    assert runtime.search_running(tmp_path, now=time.time() + health.STALE_AFTER + 5) == (False, ""), \
        "a stale heartbeat: the watcher is gone, so nothing is searching"
    hb.beat("worker", "idle", force=True)
    assert runtime.search_running(tmp_path) == (False, "")
    hb.beat("worker", "paused", force=True)
    assert runtime.search_running(tmp_path)[0] is False
    (tmp_path / health.HEARTBEAT_NAME).write_text("{not json", encoding="utf-8")
    assert runtime.search_running(tmp_path) == (False, "")
    (tmp_path / health.HEARTBEAT_NAME).write_text(json.dumps({"parts": {"worker": {"at": "x", "state": "running job 1"}}}))
    assert runtime.search_running(tmp_path) == (False, "")


def test_effective_settings_change_only_while_a_search_runs():
    s = assistant.settings_from({"enabled": True, "model": "big", "keep_alive": "5m", "timeout_seconds": 60,
                                 "while_searching": {"model": "small", "keep_alive": "30s", "timeout_seconds": 300}})
    idle = runtime.effective(s, False)
    assert (idle["model"], idle["keep_alive"], idle["timeout_seconds"], idle["mode"]) == ("big", "5m", 60.0, "idle")
    busy = runtime.effective(s, True)
    assert (busy["model"], busy["keep_alive"], busy["timeout_seconds"], busy["mode"]) == ("small", "30s", 300.0, "searching")
    assert busy["base_url"] == s["base_url"] and not busy["pause"] and "while_searching" not in busy
    assert s["model"] == "big", "the configured settings are not changed"


# ------------------------------------------------------------------ requests --


def test_keep_alive_goes_into_the_request_only_when_set_and_threads_never_do():
    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "q"}]
    plain = json.loads(client.request_body({"model": "m"}, msgs, []))
    assert list(plain) == ["model", "messages", "tools", "tool_choice", "temperature", "stream"]
    body = json.loads(client.request_body({"model": "m", "keep_alive": "2m"}, msgs, []))
    assert list(body)[:6] == list(plain) and body["keep_alive"] == "2m"
    assert json.loads(client.request_body({"model": "m", "keep_alive": 0}, msgs, []))["keep_alive"] == 0
    # what the runtimes' OpenAI-compatible endpoint does not take is never sent (docs/ASSISTANT.md)
    full = assistant.settings_from({"enabled": True, "model": "m", "keep_alive": "2m",
                                    "while_searching": {"model": "s", "pause": False}})
    sent = json.loads(client.request_body(runtime.effective(full, True), msgs, []))
    assert set(sent) == {"model", "messages", "tools", "tool_choice", "temperature", "stream", "keep_alive"}
    assert "options" not in sent and "num_thread" not in json.dumps(sent)


def test_idle_and_searching_questions_use_their_own_model_and_keep_alive(bed):
    cfg = _with(bed["cfg"], keep_alive="5m", while_searching={"model": "small", "keep_alive": "30s"})
    model = fake.ScriptedModel(ANSWER)
    ans = assistant.ask(cfg, "why?", experiment="1", transport=model, audit_path=bed["audit"], searching=False)
    assert ans.grounded and ans.mode == "idle"
    assert (model.requests[0]["model"], model.requests[0]["keep_alive"]) == ("big", "5m")
    model = fake.ScriptedModel(ANSWER)
    ans = assistant.ask(cfg, "why?", experiment="1", transport=model, audit_path=bed["audit"], searching=True)
    assert ans.grounded and ans.mode == "searching"
    assert (model.requests[0]["model"], model.requests[0]["keep_alive"]) == ("small", "30s")
    assert [(r["mode"], r["model"]) for r in audit.read(bed["audit"])] == [("idle", fake.MODEL), ("searching", fake.MODEL)]
    assert audit.read(bed["audit"])[1]["base_url"] == cfg.assistant["base_url"]


def test_ask_reads_the_workers_state_itself(bed):
    cfg = _with(bed["cfg"], while_searching={"model": "small"})
    health.Heartbeat(cfg.log_dir).beat("worker", "running job 7: IonQuant", force=True)
    model = fake.ScriptedModel(ANSWER)
    ans = assistant.ask(cfg, "why?", experiment="1", transport=model, audit_path=bed["audit"])
    assert ans.mode == "searching" and model.requests[0]["model"] == "small"
    health.Heartbeat(cfg.log_dir).beat("worker", "idle", force=True)
    model = fake.ScriptedModel(ANSWER)
    assert assistant.ask(cfg, "why?", experiment="1", transport=model, audit_path=bed["audit"]).mode == "idle"
    assert model.requests[0]["model"] == "big"


def test_paused_while_searching_is_ionomos_own_text_and_asks_nothing(bed):
    cfg = _with(bed["cfg"], while_searching={"pause": True})
    model = fake.ScriptedModel(ANSWER)
    ans = assistant.ask(cfg, "why?", experiment="1", transport=model, audit_path=bed["audit"], searching=True)
    assert ans.outcome == "unavailable" and model.requests == [] and "paused while a search runs" in ans.text
    assert "likely: FragPipe ran out of memory" in ans.text and not ans.sources
    # idle, the same settings ask as usual
    assert assistant.ask(cfg, "why?", experiment="1", transport=fake.ScriptedModel(ANSWER), audit_path=bed["audit"],
                         searching=False).grounded


def test_a_search_address_off_this_pc_is_refused_like_any_other(bed):
    cfg = _with(bed["cfg"], while_searching={"base_url": "http://192.168.1.20:8080/v1"})
    model = fake.ScriptedModel(ANSWER)
    ans = assistant.ask(cfg, "why?", experiment="1", transport=model, audit_path=bed["audit"], searching=True)
    assert ans.outcome == "refused" and model.requests == [] and "192.168.1.20" in ans.text
    assert assistant.ask(cfg, "why?", experiment="1", transport=fake.ScriptedModel(ANSWER), audit_path=bed["audit"],
                         searching=False).grounded


# ------------------------------------------------------- time to first token --


def test_time_to_first_token_waits_for_a_token_not_the_role_event():
    def slow(url, data, headers, timeout):
        yield b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
        time.sleep(0.2)  # the model reads its prompt
        yield b'data: {"choices":[{"index":0,"delta":{"content":"It ran"}}]}\n\n'
        yield b'data: {"choices":[{"index":0,"delta":{"content":" out [log:1#2]."}}]}\n\n'
        yield b"data: [DONE]\n\n"

    reply = client.chat({"model": "m", "base_url": "http://127.0.0.1:1/v1", "stream": True}, [], [], slow)
    assert reply.content == "It ran out [log:1#2]." and reply.ttft >= 0.2

    def thinking(url, data, headers, timeout):
        yield b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n\n'
        yield b'data: {"choices":[{"index":0,"delta":{"reasoning_content":"hmm"}}]}\n\n'
        time.sleep(0.2)
        yield b'data: {"choices":[{"index":0,"delta":{"content":"x"}}]}\n\n'

    assert client.chat({"model": "m", "base_url": "http://127.0.0.1:1/v1"}, [], [], thinking).ttft < 0.2, \
        "a reasoning token is a token"
    assert client._first_token(b'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}')
    assert not client._first_token(b"data: [DONE]") and not client._first_token(b'{"delta": 1}')
