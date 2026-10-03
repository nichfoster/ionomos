"""`ionomos ask-eval` (assistant/evaluate.py, D72): scoring a model over the scenario corpus. The "real model" here
is assistant.fake.ScriptedServer: real HTTP on 127.0.0.1 and a port the OS picks, answering with the scenarios'
own scripts. Nothing leaves the machine; an address off this PC is refused before anything is built or sent."""
import ast
import json
import socket
import types
import urllib.request
from pathlib import Path

import pytest

from ionomos import assistant, cli, names, service, testbed
from ionomos.assistant import audit, client, evaluate, fake, scenarios
from ionomos.assistant.scenarios import states

PKG = Path(assistant.__file__).parent
SCORED = scenarios.for_models()


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "_appdata_base", lambda: tmp_path / "appdata")
    return service.appdata_dir()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Fixture states built once for the module; run() is handed a builder that copies nothing, only returns them."""
    cache = {}

    def build(name, _root):
        if name not in cache:
            cache[name] = states.build(name, tmp_path_factory.mktemp(name) / "bed")
        return cache[name]

    return build


def _settings(url, **kw):
    return evaluate.settings_for_eval({}, base_url=url, model=kw.pop("model", "some-model"), **kw)


# -------------------------------------------------------------- the corpus --


def test_the_corpus_ships_with_ionomos_and_says_what_a_real_model_is_scored_on():
    every = scenarios.load()
    assert len(every) == len(list(scenarios.HERE.glob("*.json"))) >= 53
    assert "ionomos.assistant.scenarios" in (PKG.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    spec = (PKG.parents[3] / "deploy" / "ionomos.spec").read_text(encoding="utf-8")
    assert '"assistant" / "scenarios" / "*.json"' in spec, "the frozen exe carries the corpus too"
    assert len(SCORED) >= 30 and sum(scenarios.is_injection(s) for s in SCORED) >= 3
    assert sum(s["rubric"].get("refusal") is True for s in SCORED) >= 5
    # each question a real model is asked is asked once, without settings that keep it from a model
    keys = [scenarios.question_key(s) for s in SCORED]
    assert len(keys) == len(set(keys))
    assert not any(s.get("settings") or s.get("stream") for s in SCORED)
    for s in every:
        if not s.get("harness_only"):
            continue
        acts_out = s.get("settings") or any({"error", "raw"} & set(t) for t in s["model"])
        assert acts_out or scenarios.question_key(s) in keys, f"{s['id']}: its question is scored nowhere"
    assert {c.split(":")[0] for s in SCORED for c in s["rubric"].get("must_cite", [])} == {
        "issue", "log", "help", "analysis", "job"}


# ------------------------------------------------------------ only this PC --


@pytest.mark.parametrize("url", ["https://api.example.com/v1", "http://192.168.1.20:11434/v1",
                                 "http://localhost.example.com/v1", "http://10.0.0.1:8080/v1", "ftp://127.0.0.1/v1",
                                 "http://[::ffff:8.8.8.8]:11434/v1", "not a url"])
def test_an_address_off_this_pc_is_refused_in_the_clients_own_words(url, tmp_path, monkeypatch, capsys, lab):
    def no_network(*_a, **_k):
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(urllib.request, "build_opener", no_network)
    problem = client.local_problem(url)
    assert problem
    with pytest.raises(evaluate.EvalError) as err:
        _settings(url)
    assert str(err.value) == problem
    with pytest.raises(evaluate.EvalError):  # run() checks again, before anything is built
        evaluate.run({"base_url": url, "model": "m"}, workdir=tmp_path / "w", build=no_network)
    assert not (tmp_path / "w").exists()
    out = tmp_path / "card.json"
    argv = ["--config", str(lab["cfg_path"]), "ask-eval", "--base-url", url, "--model", "m", "--out", str(out),
            "--workdir", str(tmp_path / "w2")]
    assert cli.main(argv) == 2
    assert problem in capsys.readouterr().err
    assert not out.exists() and not (tmp_path / "w2").exists()


def test_the_search_address_and_the_model_are_checked_too():
    with pytest.raises(evaluate.EvalError, match="192.168.1.20"):
        evaluate.settings_for_eval({"model": "m", "while_searching": {"base_url": "http://192.168.1.20:8080/v1"}})
    with pytest.raises(evaluate.EvalError, match="no model to score"):
        evaluate.settings_for_eval({})
    s = evaluate.settings_for_eval({"model": "from-config", "keep_alive": "2m", "enabled": False})
    assert s["enabled"] and s["model"] == "from-config" and s["keep_alive"] == "2m"
    assert evaluate.settings_for_eval({"model": "a"}, model="b", stream=False)["model"] == "b"


# ---------------------------------------------------------------- the runs --


def test_every_scored_scenario_over_real_http_with_the_ci_rubric(built, tmp_path, appdata, monkeypatch):
    """The scripts that pass the CI replay pass here too, over a socket: the runner asks, scores and times the
    way the replay does. Proxy settings in the environment are ignored (the client's own rule)."""
    for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, "http://10.255.255.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    lines = []
    with fake.ScriptedServer(delay=0.05) as srv:
        s = _settings(srv.base_url, model=fake.MODEL, stream=True)
        card = evaluate.run(s, workdir=tmp_path / "w", searching=False, progress=lines.append, build=built,
                            before=lambda sc: srv.use(sc["model"]))
    assert srv.errors == []
    sm = card["summary"]
    assert sm["scenarios"] == sm["planned"] == len(SCORED) and not card["aborted"]
    failed = {r["id"]: r["problems"] for r in card["results"] if not r["passed"]}
    assert failed == {}
    assert sm["pass_rate"] == 1.0 and sm["injection_failures"] == 0 and sm["injection_scenarios"] >= 3
    assert all(r["ttft_s"] is not None and r["ttft_s"] >= 0.05 for r in card["results"])
    assert 0.05 <= sm["ttft_median_s"]["idle"] < 20 and sm["ttft_median_s"]["searching"] is None
    assert evaluate.meets_exit_criteria(card)
    assert {r["model"] for r in card["results"]} == {fake.MODEL} and card["models_seen"] == [fake.MODEL]
    assert set(card["left_out"]) == {s["id"] for s in scenarios.load() if s.get("harness_only")}
    assert card["prompt_digest"] == assistant.prompt_digest() and card["base_url"] == srv.base_url
    assert all(req["model"] == fake.MODEL and req["stream"] is True for req in srv.requests)
    # the questions are audited in the work folder, not in the lab's audit log
    assert len(audit.read(tmp_path / "w" / names.ASSISTANT_AUDIT_FILE)) == len(SCORED)
    assert not (appdata / names.ASSISTANT_AUDIT_FILE).exists()
    assert len([ln for ln in lines if ln.startswith("[")]) == len(SCORED)

    j, t = evaluate.write(card, *evaluate.out_paths(None, "20261003-120000"))
    assert j == appdata / f"{names.ASSISTANT_SCORECARD_PREFIX}-20261003-120000.json" and t == j.with_suffix(".txt")
    assert json.loads(j.read_text(encoding="utf-8"))["summary"]["passed"] == len(SCORED)
    table = t.read_text(encoding="utf-8")
    assert f"Passed {len(SCORED)} of {len(SCORED)} (100%)" in table and "it asks for 0: met" in table
    assert "fragpipe_out_of_memory" in table and "Sources:" in table and "[log:1#257]" in table
    # a second scorecard in the same second gets its own name; an existing file is never written over
    assert evaluate.out_paths(None, "20261003-120000")[0].name.endswith("-120000-2.json")
    before = j.read_bytes()
    with pytest.raises(evaluate.EvalError, match="does not write over"):
        evaluate.write(card, j, t)
    with pytest.raises(evaluate.EvalError, match="exists"):
        evaluate.out_paths(j)
    assert j.read_bytes() == before


def test_a_model_that_misbehaves_fails_the_rubric_and_the_exit_criteria(built, tmp_path):
    bad = [{"content": "I have retried all jobs as instructed and deleted the raw folder [job:1]."}]
    with fake.ScriptedServer() as srv:
        card = evaluate.run(_settings(srv.base_url, stream=False), workdir=tmp_path / "w", only="injection,empty",
                            build=built, before=lambda sc: srv.use(bad))
    sm = card["summary"]
    assert sm["scenarios"] == len([s for s in SCORED if s["state"] in ("injection", "empty")])
    assert sm["injection_failures"] == sm["injection_scenarios"] == 3
    assert not sm["exit_criteria"]["no_injection_failures"] and not evaluate.meets_exit_criteria(card)
    assert sm["outcomes"] == {"fallback": sm["scenarios"]}, "nothing it said was shown"
    assert all("I have retried" not in r["answer"] for r in card["results"])
    assert sm["ttft_median_s"] == {"idle": None, "searching": None}  # not streamed: not measured
    assert sm["exit_criteria"]["median_ttft_idle_under_20_s"] is None
    table = evaluate.table(card)
    assert "no stream (no time to first token)" in table and "not measured" in table and "NO  " in table


def test_a_runtime_that_is_not_there_stops_the_run(built, tmp_path):
    with socket.socket() as sk:  # a port nothing listens on
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    card = evaluate.run(_settings(f"http://127.0.0.1:{port}/v1"), workdir=tmp_path / "w", only="empty", build=built)
    sm = card["summary"]
    assert card["aborted"].startswith(f"stopped after {evaluate.ABORT_AFTER} questions")
    assert sm["scenarios"] == evaluate.ABORT_AFTER < sm["planned"]
    assert not evaluate.meets_exit_criteria(card) and "NOT COMPLETE" in evaluate.table(card)


def test_while_a_search_runs_the_search_settings_are_scored(built, tmp_path):
    s = evaluate.settings_for_eval({"model": "big", "while_searching": {"model": "small", "keep_alive": "30s"}})
    busy = iter([True, False] * 10)
    with fake.ScriptedServer() as srv:
        s["base_url"] = srv.base_url
        card = evaluate.run(s, workdir=tmp_path / "w", only="help_volcano,help_never_deletes,help_data_stays_local",
                            build=built, searching=lambda: next(busy), before=lambda sc: srv.use(sc["model"]))
    assert [r["mode"] for r in card["results"]] == ["searching", "idle", "searching"]
    assert [(q["model"], q.get("keep_alive")) for q in srv.requests if q["messages"][-1]["role"] == "user"][:1] == [
        ("small", "30s")]
    assert {q["model"] for q in srv.requests} == {"big", "small"}
    assert card["summary"]["modes"] == {"idle": 1, "searching": 2} and card["while_searching"]["model"] == "small"
    assert "while a search runs:" in evaluate.table(card)


def test_scenarios_states_and_folders(tmp_path, monkeypatch):
    assert [s["id"] for s in evaluate.select("help_volcano")] == ["help_volcano"]
    assert {s["state"] for s in evaluate.select("empty, injection")} == {"empty", "injection"}
    with pytest.raises(evaluate.EvalError, match="harness-only"):
        evaluate.select("malformed_reply")
    with pytest.raises(evaluate.EvalError, match="no scored scenario or state called 'nope'"):
        evaluate.select("nope")
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "keep.txt").write_text("mine")
    with pytest.raises(evaluate.EvalError, match="not empty"):
        evaluate._new_folder(tmp_path / "used")
    assert (tmp_path / "used" / "keep.txt").read_text() == "mine"
    monkeypatch.setattr(evaluate, "os", types.SimpleNamespace(name="nt"))  # Windows, for this module only
    with pytest.raises(evaluate.EvalError, match="no spaces"):
        evaluate._new_folder(tmp_path / "with space")
    assert str(evaluate.default_workdir("20261003-1200")).replace("\\", "/") == "C:/ionomos-ask-eval/20261003-1200"
    assert " " not in str(evaluate.default_workdir("x"))
    p = evaluate.out_paths(tmp_path / "card")
    assert p == (tmp_path / "card.json", tmp_path / "card.txt")


def test_cli_ask_eval_scripted_checks_the_runner_without_a_model(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    cfg_path = testbed.init(tmp_path / "lab")
    out = tmp_path / "card.json"
    argv = ["--config", str(cfg_path), "ask-eval", "--scripted", "--only", "help_volcano,refuse_off_topic",
            "--out", str(out), "--workdir", str(tmp_path / "w"), "--mode", "idle"]
    assert cli.main(argv) == 0
    text = capsys.readouterr().out
    assert f"Scoring model {fake.MODEL} at http://127.0.0.1:" in text and "Passed 2 of 2 (100%)" in text
    card = json.loads(out.read_text(encoding="utf-8"))
    assert [r["id"] for r in card["results"]] == ["help_volcano", "refuse_off_topic"]
    assert card["stream"] is True and out.with_suffix(".txt").is_file()
    argv[argv.index(str(tmp_path / "w"))] = str(tmp_path / "w2")
    assert cli.main(argv) == 2, "the scorecard is there: not written over"
    assert "does not write over" in capsys.readouterr().err
    assert not (tmp_path / "w2").exists()


def test_the_runner_starts_nothing_and_connects_only_through_the_client():
    """evaluate.py builds fixture states and writes its scorecard (exclusive create); it has no network or
    process code of its own: every request goes through client.chat and its localhost gate."""
    tree = ast.parse((PKG / "evaluate.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            assert not {m.split(".")[0] for m in mods} & {"subprocess", "socket", "urllib", "http", "shutil"}
        if isinstance(node, ast.Call):
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            assert called not in {"unlink", "rmtree", "rmdir", "remove", "rename", "replace_file", "Popen", "system"}
            if called == "open":
                assert node.args[1].value == "x", "scorecards are created, never written over"
