"""The read-only "Explain" assistant (ionomos/assistant, ROADMAP Phase 6.1, D49 / D57), piece by piece:
settings, the localhost gate, the chat client, the tools and their validators, help search, the citation
check, the audit log and `ionomos ask`. The model is always assistant.fake.ScriptedModel: no test opens a
socket. The scenario corpus (tests/test_assistant_scenarios.py) replays whole conversations."""
import ast
import io
import json
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from ionomos import assistant, attention, cli, configio, fragpipe, names, service, testbed
from ionomos import help as helpdoc
from ionomos.assistant import audit, citations, client, fake, helpsearch, proposals, tools
from ionomos.config import ConfigError, load
from ionomos.intake import intake
from ionomos.ledger import Job, Ledger
from ionomos.worker import Worker

PKG = Path(assistant.__file__).parent


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
    assert led.get(1).status == "failed"
    ready = replace(cfg, assistant=assistant.settings_from({"enabled": True, "model": fake.MODEL, "stream": False}))
    yield {"cfg": cfg, "ready": ready, "cfg_path": cfg_path, "ledger": led, "audit": tmp_path / "audit.jsonl"}
    led.close()


def _ctx(bed) -> tools.Context:
    return tools.Context(bed["cfg"], bed["ledger"])


# ----------------------------------------------------------------- settings --


def test_settings_default_to_off_and_typos_fail_at_load(lab):
    assert lab["cfg"].assistant == assistant.DEFAULTS and not assistant.DEFAULTS["enabled"]
    assert assistant.DEFAULTS["model"] == "", "no default model: it is chosen by the scorecard (ROADMAP 6.0)"
    assert assistant.state(lab["cfg"].assistant)[0] == "not_set_up"

    def with_block(block):
        lab["cfg_path"].write_text(yaml.safe_dump({**lab["cfg_dict"], "assistant": block}))
        return load(lab["cfg_path"])

    cfg = with_block({"enabled": True, "model": "some-model", "maintainer": " Nick (ext. 123) "})
    assert cfg.assistant["maintainer"] == "Nick (ext. 123)" and assistant.state(cfg.assistant)[0] == "ready"
    for block, msg in (({"enabld": True}, "assistant.enabld: unknown setting"),
                       ({"enabled": "yes"}, "assistant.enabled must be true or false"),
                       ({"timeout_seconds": "soon"}, "assistant.timeout_seconds must be a number"),
                       ({"model": 7}, "assistant.model must be a text"), ("on", "assistant: must be a mapping")):
        with pytest.raises(ConfigError, match=msg):
            with_block(block)
    # an address off this PC is not a config error (the watcher must still start): it is refused when asked
    cfg = with_block({"enabled": True, "model": "m", "base_url": "https://api.example.com/v1"})
    assert assistant.state(cfg.assistant)[0] == "refused"


def test_example_config_has_the_block_off_by_default_and_the_app_keeps_it(lab):
    example = yaml.safe_load((Path(cli.__file__).parents[2] / "config.example.yaml").read_text(encoding="utf-8"))
    s = assistant.settings_from(example["assistant"])
    assert s["enabled"] is False and s["model"] == "" and s["allow_cloud"] is False
    assert client.local_problem(s["base_url"]) is None
    # the app rewrites config.yaml from a dict: an assistant: block someone wrote by hand survives a save
    d = configio.read_config(lab["cfg_path"])
    assert "assistant:" not in configio.dump_config(d)
    d["assistant"] = {"enabled": True, "model": "some-model:4b", "maintainer": "Nick"}
    back = yaml.safe_load(configio.dump_config(d))
    assert back["assistant"] == d["assistant"]


@pytest.mark.parametrize(("url", "local"), [
    ("http://127.0.0.1:11434/v1", True), ("http://localhost:8080/v1", True), ("http://[::1]:8080/v1", True),
    ("http://127.8.9.10/v1", True), ("https://localhost/v1", True),
    ("https://api.openai.com/v1", False), ("http://192.168.1.20:11434/v1", False), ("http://0.0.0.0:11434/v1", False),
    ("http://127.0.0.1.example.com/v1", False), ("http://localhost.example.com/v1", False),
    ("http://127.0.0.1@example.com/v1", False), ("http://example.com/?x=localhost", False),
    ("ftp://127.0.0.1/v1", False), ("127.0.0.1:11434", False), ("", False), ("http://127.0.0.1:port/v1", False),
])
def test_only_this_pc_is_local(url, local):
    assert (client.local_problem(url) is None) is local


def test_a_non_local_address_is_refused_before_anything_is_sent(bed):
    model = fake.ScriptedModel([{"content": "hello [job:1]"}])
    for extra in ({}, {"allow_cloud": True}):
        s = assistant.settings_from({"enabled": True, "model": "m", "base_url": "https://api.example.com/v1", **extra})
        with pytest.raises(client.NotLocal, match="not this PC"):
            client.chat(s, [{"role": "user", "content": "x"}], [], model)
        ans = assistant.ask(replace(bed["cfg"], assistant=s), "why?", experiment="1", transport=model,
                            audit_path=bed["audit"])
        assert ans.outcome == "refused" and "nothing was sent" in ans.text and "ran out of memory" in ans.text
        assert ("6.4" in ans.text) is bool(extra)
    assert model.requests == []


# ------------------------------------------------------------------- client --


def test_prompt_and_schemas_are_small_and_byte_stable():
    blob = assistant.SYSTEM_PROMPT + json.dumps(assistant.schemas(), separators=(",", ":"))
    # about 1.6k tokens at 4 bytes per token with the proposal tools (D75); the roadmap's budget is ~2k
    assert len(blob.encode("utf-8")) < 6500
    assert assistant.prompt_digest() == audit.digest(blob) == assistant.prompt_digest()
    # Pinned: a changed prompt or schema is a decision. Update this digest with it, and score the real models
    # again (docs/ASSISTANT.md): an audit record's prompt_digest says which prompt an answer came from.
    assert assistant.prompt_digest() == "4f72891f1234baf9d4beceed027f66432cea752330ce7b7699af2d581d7dcd48"
    # nothing that changes between requests (dates, versions, paths, job numbers) is in the prefix
    from ionomos import __version__

    assert __version__ not in blob and "20" not in assistant.SYSTEM_PROMPT
    s = assistant.settings_from({"enabled": True, "model": "m"})
    a = client.request_body(s, [{"role": "system", "content": assistant.SYSTEM_PROMPT}], assistant.schemas())
    assert a == client.request_body(s, [{"role": "system", "content": assistant.SYSTEM_PROMPT}], assistant.schemas())
    body = json.loads(a)
    assert list(body) == ["model", "messages", "tools", "tool_choice", "temperature", "stream"]
    assert body["temperature"] == 0 and body["tools"] == assistant.schemas() == tools.schemas() + proposals.schemas()


@pytest.mark.parametrize("stream", [False, True])
def test_client_reads_a_reply_whole_or_streamed(stream):
    s = assistant.settings_from({"enabled": True, "model": "m", "stream": stream})
    model = fake.ScriptedModel([{"content": "thinking aloud", "tool_calls": [
        {"name": "get_job", "arguments": {"job_id": 12}}, {"name": "search_help", "arguments": {"query": "naïve μ"}}]},
        {"content": "<think>hidden</think>It failed [job:12]."}], stream=stream)
    r = client.chat(s, [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}], tools.schemas(), model)
    assert [(c["name"], json.loads(c["arguments"])) for c in r.tool_calls] == [
        ("get_job", {"job_id": 12}), ("search_help", {"query": "naïve μ"})]
    assert r.content == "thinking aloud" and r.model == fake.MODEL and (r.ttft is not None) is stream
    assert model.requests[0]["stream"] is stream and model.requests[0]["model"] == "m"
    r = client.chat(s, [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}], tools.schemas(), model)
    assert r.content == "It failed [job:12]." and r.tool_calls == []


@pytest.mark.parametrize("raw", ["", "<html>nope</html>", "{}", '{"choices": []}', '{"choices": [{"message": 3}]}',
                                 'data: {"choices": "x"}\n\n', '{"error": {"message": "model not found"}}'])
def test_client_turns_any_other_reply_into_a_chat_error(raw):
    s = assistant.settings_from({"enabled": True, "model": "m"})
    with pytest.raises(client.ChatError):
        client.chat(s, [{"role": "user", "content": "q"}], [], lambda *a: iter([raw.encode()]))


def test_http_transport_goes_straight_to_the_address_without_proxy_or_redirect(monkeypatch):
    """The real transport, with urllib's opener stubbed: no socket is opened."""
    seen = {}

    class Opener:
        def open(self, req, timeout):
            seen.update(url=req.full_url, method=req.get_method(), body=req.data, timeout=timeout,
                        ctype=req.get_header("Content-type"))
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "hi"}}], "model": "x"}).encode())

    def build(*handlers):
        seen["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(client.urllib.request, "build_opener", build)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.com:3128")
    s = assistant.settings_from({"enabled": True, "model": "m", "base_url": "http://127.0.0.1:11434/v1/",
                                 "timeout_seconds": 7})
    assert client.chat(s, [{"role": "user", "content": "q"}], []).content == "hi"
    assert seen["url"] == "http://127.0.0.1:11434/v1/chat/completions" and seen["method"] == "POST"
    assert seen["timeout"] == 7 and seen["ctype"] == "application/json" and json.loads(seen["body"])["model"] == "m"
    proxy, redirect = seen["handlers"]
    assert isinstance(proxy, client.urllib.request.ProxyHandler) and proxy.proxies == {}
    assert redirect is client._NoRedirect and client._NoRedirect().redirect_request(None, None, 302, "", {}, "x") is None

    class Down:
        def open(self, req, timeout):
            raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(client.urllib.request, "build_opener", lambda *h: Down())
    with pytest.raises(client.ChatError, match="no answer from the model runtime.*connection refused"):
        client.chat(s, [{"role": "user", "content": "q"}], [])


# -------------------------------------------------------------------- tools --


def test_tools_are_the_seven_read_only_ones_with_closed_schemas():
    assert [t.name for t in tools.TOOLS] == ["list_experiments", "get_job", "list_attention", "explain_issue",
                                              "log_tail", "analysis_summary", "search_help"]
    for sc in tools.schemas():
        fn = sc["function"]
        assert sc["type"] == "function" and fn["description"] and fn["parameters"]["additionalProperties"] is False
        for name, spec in fn["parameters"]["properties"].items():
            assert not any(w in name for w in ("path", "file", "folder", "dir", "url", "cmd", "command")), name
            assert spec["type"] in ("integer", "string", "boolean")
            if spec["type"] == "string":
                assert "enum" in spec or spec["maxLength"] <= 200
            if spec["type"] == "integer":
                assert "minimum" in spec and "maximum" in spec


def test_the_assistant_package_cannot_write_delete_start_or_connect():
    """Read the source: the tools, the citation check, help search and the loop hold no call that changes a
    file, starts a program or opens a connection. (client.py speaks HTTP to localhost; audit.py appends.)"""
    banned_calls = {"unlink", "rmdir", "rmtree", "remove", "rename", "replace", "move", "copy", "copy2", "copytree",
                    "write_text", "write_bytes", "mkdir", "makedirs", "touch", "chmod", "system", "popen", "Popen",
                    "check_call", "check_output", "startfile", "urlopen", "requeue", "set_status", "insert",
                    "start_attempt", "raise_item", "resolve", "dismiss", "snooze", "save_overrides", "exec", "eval"}
    banned_imports = {"subprocess", "shutil", "socket", "urllib", "http", "ftplib", "smtplib", "ctypes", "os"}
    for name in ("tools.py", "citations.py", "helpsearch.py", "__init__.py", "proposals.py"):
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if called == "replace" and isinstance(fn, ast.Attribute) and isinstance(fn.value, (ast.Name, ast.Constant)):
                    continue  # str.replace on a local string
                assert called not in banned_calls, f"{name}:{node.lineno}: {called}()"
                if called == "open":
                    mode = node.args[1].value if len(node.args) > 1 else "r"
                    assert mode in ("r", "rb"), f"{name}:{node.lineno}: open(..., {mode!r})"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                assert not {m.split(".")[0] for m in mods} & banned_imports, f"{name}:{node.lineno}: {mods}"
    for name in ("client.py", "audit.py"):
        src = (PKG / name).read_text(encoding="utf-8")
        assert "subprocess" not in src and "unlink" not in src and "shutil" not in src
    assert '"a"' in (PKG / "audit.py").read_text(encoding="utf-8") and '"w"' not in (PKG / "audit.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(("name", "args", "problem"), [
    ("get_job", {}, "missing argument 'job_id'"),
    ("get_job", {"job_id": "1"}, "job_id must be a whole number"),
    ("get_job", {"job_id": True}, "job_id must be a whole number"),
    ("get_job", {"job_id": 1.5}, "job_id must be a whole number"),
    ("get_job", {"job_id": 0}, "job_id must be between"),
    ("get_job", {"job_id": 1, "dest_dir": "C:/Fragpipe_General"}, "unknown argument 'dest_dir'"),
    ("log_tail", {"job_id": 1, "path": "/etc/passwd"}, "unknown argument 'path'"),
    ("log_tail", {"job_id": 1, "lines": 10_000}, "lines must be between 1 and 60"),
    ("log_tail", {"job_id": 1, "contains": "x" * 61}, "contains is too long"),
    ("log_tail", {"job_id": 1, "errors_only": "yes"}, "errors_only must be true or false"),
    ("list_experiments", {"status": "deleted"}, "status must be one of"),
    ("list_experiments", {"user": "../Isaac"}, "user has characters that are not allowed"),
    ("list_experiments", {"user": "C:\\Users"}, "user has characters that are not allowed"),
    ("explain_issue", {"code": "NO_TABLE; rm -rf"}, "code has characters that are not allowed"),
    ("search_help", {"query": 7}, "query must be text"),
    ("search_help", {"query": "x" * 201}, "query is too long"),
    ("search_help", ["query"], "arguments must be a JSON object"),
    ("search_help", '{"query": ', "not valid JSON"),
    ("retry_job", {"job_id": 1}, "there is no tool 'retry_job'"),
    ("", {}, "there is no tool"),
])
def test_bad_calls_are_refused_with_a_reason_and_never_raise(bed, name, args, problem):
    res = tools.call(_ctx(bed), name, args)
    assert problem in res["error"], res


def test_tools_wrap_the_ledger_attention_help_and_the_log(bed):
    ctx = _ctx(bed)
    jobs = tools.call(ctx, "list_experiments", {})
    assert jobs["total"] == 1 and jobs["experiments"][0]["job"] == 1 and jobs["experiments"][0]["status"] == "failed"
    assert tools.call(ctx, "list_experiments", {"status": "done"})["total"] == 0
    assert tools.call(ctx, "list_experiments", {"user": "ejq"})["total"] == 1

    job = tools.call(ctx, "get_job", {"job_id": 1})
    led = bed["ledger"].get(1)
    assert (job["name"], job["status"], job["attempts"]) == (led.inbox_name, "failed", 1) and job["exit_code"] == 1
    assert job["likely_causes"][0].startswith("FragPipe ran out of memory")
    assert tools.call(ctx, "get_job", {"job_id": 99})["error"] == "there is no job 99"

    (item,) = attention.items(bed["cfg"].log_dir)
    att = tools.call(ctx, "list_attention", {"job_id": 1})
    row = att["items"][0]
    assert (row["id"], row["kind"], row["help"]) == (item.id, "search_failed", helpdoc.topic_for_item(item))
    assert row["causes"] == item.causes and row["fixes"] == item.fixes
    assert tools.call(ctx, "list_attention", {"job_id": 2})["items"] == []

    tail = tools.call(ctx, "log_tail", {"job_id": 1})
    log_lines = names.console_log(Path(led.dest_dir)).read_text(encoding="utf-8").splitlines()
    assert tail["log"] == fragpipe.CONSOLE_LOG and tail["lines_in_file"] == len(log_lines)
    for line in tail["lines"]:  # the number before | is the line's number in the file
        n, text = line.split("| ", 1)
        assert tools.clean(log_lines[int(n) - 1], tools.MAX_LOG_LINE).strip() == text.strip()  # long lines are cut
    # FragPipe's own last lines follow the step's ("Process 'MSFragger' finished, exit code: 1", "Cancelling ...")
    assert any("OutOfMemoryError" in ln for ln in tail["lines"]) and "Cancelling" in tail["lines"][-1]
    assert tail["likely_causes"] == fragpipe.explain("\n".join(log_lines))
    assert not any(ln.split("| ", 1)[1].startswith("# ") for ln in tail["lines"]), "Ionomos's own markers are left out"
    only = tools.call(ctx, "log_tail", {"job_id": 1, "contains": "outofmemory"})
    assert len(only["lines"]) == 1 and only["matching"] == 1
    errors = tools.call(ctx, "log_tail", {"job_id": 1, "errors_only": True})["lines"]
    assert only["lines"][0] in errors and any("exit code: 1" in ln for ln in errors)

    assert tools.call(ctx, "analysis_summary", {"job_id": 1})["analysis"] is None

    exp = tools.call(ctx, "explain_issue", {"code": "no_table"})
    assert exp["code"] == "NO_TABLE" and exp["help"] == "issue.NO_TABLE"
    assert exp["text"] == helpdoc.to_text(helpdoc.entries()["issue.NO_TABLE"].body, width=10_000), "the help's own words"
    assert tools.call(ctx, "explain_issue", {"code": "CRASH_QC"})["help"] == "issue.CRASH"
    assert "there is no issue code NOPE" in tools.call(ctx, "explain_issue", {"code": "NOPE"})["error"]

    found = tools.call(ctx, "search_help", {"query": "why are there no hits", "limit": 2})
    assert [e["help"] for e in found["entries"]][0] == "faq.no-hits" and len(found["entries"]) == 2

    # what each tool returned is what can be cited, and nothing else
    reg = ctx.registry
    assert reg.has("job", "1") and not reg.has("job", "99") and not reg.has("job", "2")
    assert reg.has("log", f"1#{tail['lines'][-1].split('|')[0]}") and not reg.has("log", "1#1")  # line 1: a marker
    assert reg.has("issue", "NO_TABLE") and reg.has("issue", "crash_qc") and not reg.has("issue", "NOPE")
    assert reg.has("help", "faq.no-hits") and reg.has("help", "attention.search_failed") and not reg.has("help", "faq.rerun")


def test_tools_work_without_a_ledger_and_on_a_labwatch_era_folder(lab, tmp_path):
    ctx = tools.Context(lab["cfg"], None)  # a new install: no ledger file yet, and none is created
    assert tools.call(ctx, "list_experiments", {}) == {"total": 0, "shown": 0, "experiments": []}
    assert "there is no job 1" in tools.call(ctx, "get_job", {"job_id": 1})["error"]
    assert assistant._open_ledger(lab["cfg"]) is None and not lab["cfg"].database.exists()
    # an experiment filed by LabWatch 0.4: labwatch.json and labwatch_run/ (names.py), read the same
    dest = lab["general"] / "EJQ" / "old_experiment"
    run = dest / names.LEGACY_RUN_DIRS[0]
    run.mkdir(parents=True)
    (run / fragpipe.CONSOLE_LOG).write_text("# marker\nMSFragger jar not found. Please download MSFragger\n", encoding="utf-8")
    (dest / names.LEGACY_STATUS_FILES[0]).write_text(json.dumps({"run": {"exit_code": 1, "hints": ["from LabWatch"]}}))
    led = Ledger(lab["cfg"].database)
    led.insert(Job(inbox_name="old_experiment", user="EJQ", method="isoDTB", dest_dir=str(dest), status="failed"))
    ctx = tools.Context(lab["cfg"], led)
    assert tools.call(ctx, "get_job", {"job_id": 1})["likely_causes"] == ["from LabWatch"]
    tail = tools.call(ctx, "log_tail", {"job_id": 1})
    assert tail["lines"] == ["2| MSFragger jar not found. Please download MSFragger"]
    assert "MSFragger isn't installed" in tail["likely_causes"][0]
    led.close()


def test_log_line_numbers_are_the_files_even_for_a_log_longer_than_what_is_read(lab, monkeypatch):
    dest = lab["general"] / "EJQ" / "big"
    run = dest / names.RUN_DIR
    run.mkdir(parents=True)
    (run / fragpipe.CONSOLE_LOG).write_text("".join(f"line {n}\n" for n in range(1, 5001)), encoding="utf-8")
    (run / "notes.log").write_text("not an engine log\n", encoding="utf-8")
    led = Ledger(lab["cfg"].database)
    led.insert(Job(inbox_name="big", user="EJQ", method="DIA", dest_dir=str(dest)))
    monkeypatch.setattr(tools, "LOG_SCAN_BYTES", 2000)
    real = fragpipe.read_tail_text
    monkeypatch.setattr(fragpipe, "read_tail_text", lambda p, n=2000: real(p, 2000))
    tail = tools.call(tools.Context(lab["cfg"], led), "log_tail", {"job_id": 1, "lines": 3})
    assert tail["lines"] == ["4998| line 4998", "4999| line 4999", "5000| line 5000"] and tail["lines_in_file"] == 5000
    # a console_log path written in the status file is not followed: only the job's own run folder is read
    (dest / names.STATUS_FILE).write_text(json.dumps({"run": {"console_log": str(lab["cfg_path"])}}), encoding="utf-8")
    assert tools.call(tools.Context(lab["cfg"], led), "log_tail", {"job_id": 1})["log"] == fragpipe.CONSOLE_LOG
    led.close()


def test_untrusted_text_is_cleaned_capped_and_fitted(lab):
    dirty = "ok\x1b[31mRED\x1b[0m\x07 zero\u200bwidth \u202eoverride\x00 tab\there"
    assert tools.clean(dirty) == "okRED zerowidth override tab here"
    assert tools.clean("x" * 1000, 50) == "x" * 49 + "…" and tools.clean("a\n\nb") == "a\n\nb"
    big = {"rows": [{"n": i, "text": "y" * 300} for i in range(100)], "note": "z" * 400}
    small = tools.fit(json.loads(json.dumps(big)), 2000)
    assert len(json.dumps(small)) <= 2000 and small["truncated"] is True and 1 <= len(small["rows"]) < 100
    assert tools.fit({"a": 1}) == {"a": 1}
    # an attention item written by something hostile comes out clean and bounded
    attention.raise_item(lab["cfg"].log_dir, "search_failed", "T\x1b]0;evil\x07itle", "m" * 5000, key="k",
                         causes=["c\u200b" * 300] * 30, data={"issues": [{"code": "no_table\x00", "title": "t" * 900}]})
    res = tools.call(tools.Context(lab["cfg"], None), "list_attention", {})
    text = json.dumps(res)
    assert len(text) <= tools.MAX_RESULT_CHARS and "\\u001b" not in text and "\\u200b" not in text and "\\u0000" not in text
    row = res["items"][0]
    assert len(row["message"]) <= tools.MAX_STRING and len(row["causes"]) <= 6
    assert row["issues"][0]["code"] == "NO_TABLE" and row["issues"][0]["help"] == "issue.NO_TABLE"


# -------------------------------------------------------------- help search --


@pytest.mark.parametrize("engine", ["fts5", "python"])
@pytest.mark.parametrize(("query", "want"), [
    ("why are there no hits", "faq.no-hits"),
    ("samples group by replicate number", "issue.BATCH_SUSPECT"),
    ("how do I leave a sample out", "faq.leave-out"),
    ("FASTA missing", "search.hold-fasta"),
    ("which condition is the control", "issue.NO_CONTROL"),
    ("what is a volcano plot", "glossary.volcano"),
    ("is my data sent anywhere", "safety.private"),
    ("NO_TABLE", "issue.NO_TABLE"),
    ("search_failed", "attention.search_failed"),
    ("can I ask a question in plain words", "faq.assistant"),
])
def test_help_search_finds_the_entry_with_either_engine(engine, query, want):
    if engine == "fts5" and not helpsearch.fts5_available():
        pytest.skip("this Python's SQLite has no FTS5; the pure-Python BM25 is used")
    assert helpsearch.search(query, 3, engine=engine)[0].id == want


def test_help_search_edges():
    assert helpsearch.search("") == [] and helpsearch.search("the of and") == [] and helpsearch.search("zzzqqq") == []
    assert helpsearch.search('"quotes" AND (parens) OR NEAR* -minus col:on') is not None  # no FTS5 syntax gets through
    assert len(helpsearch.search("search", 50)) == 10
    hits = helpsearch.search("imputation", 5, engine="python")
    assert hits == sorted(hits, key=lambda h: (-h.score, h.id)) and len({h.id for h in hits}) == 5
    assert helpsearch.stem("deleted") == helpsearch.stem("deletes") == helpsearch.stem("delete") == "delet"
    assert {i for i, _t, _b in helpsearch._docs()} == set(helpdoc.entries()), "the index is the help, entry for entry"


# ---------------------------------------------------------------- citations --


def test_citations_are_valid_only_if_a_tool_returned_them():
    reg = citations.Registry()
    reg.add("log", "3#41", "Java heap space")
    reg.add("log", "3#42")
    reg.add("issue", "no_table", "No result table")
    reg.add("help", "faq.rerun")
    reg.add("analysis", "comparisons")
    reg.add("job", 3)

    def check(text):
        v = citations.check(text, reg)
        return v.ok, v.valid, v.invalid

    assert check("It ran out of memory [log:3#41].") == (True, ["log:3#41"], [])
    assert check("See [issue:NO_TABLE] and [issue:no_table], [help:faq.rerun]; [analysis:comparisons] [job:3] [job:#3].")[0]
    assert check("Both lines [log:3#41-42] say so.") == (True, ["log:3#41-42"], [])
    assert check("Several at once [log:3#41, help:faq.rerun; job:3].") == (True, ["log:3#41", "help:faq.rerun", "job:3"], [])
    assert check("Spaces [ Log : 3#41 ] are fine.")[0]
    assert check("Made up [log:3#43].") == (False, [], ["log:3#43"])
    assert check("A range with a gap [log:3#41-43].") == (False, [], ["log:3#41-43"])
    assert check("Another job [log:4#41] [job:4].") == (False, [], ["log:4#41", "job:4"])
    assert check("One good [log:3#41], one invented [help:made.up].") == (False, ["log:3#41"], ["help:made.up"])
    assert check("No sources at all.") == (False, [], [])
    assert check("[file:C:/x] and [1] and [see below] are not citations.") == (False, [], [])
    v = citations.check("Grounded [job:3].\n\nNot grounded, though plausible.\n\nGrounded again [log:3#42].", reg)
    assert not v.ok and v.uncited == ["Not grounded, though plausible."] and "1 paragraph(s) without" in v.why()
    assert citations.check("", reg).why() == "no citation"
    assert citations.sources(["log:3#41", "job:3"], reg) == ["[log:3#41] Java heap space", "[job:3]"]


# ---------------------------------------------------------------------- ask --


def test_a_grounded_answer_is_shown_with_its_sources_and_audited(bed):
    model = fake.ScriptedModel([
        {"tool_calls": [{"name": "search_help", "arguments": {"query": "search failed"}}]},
        {"content": "FragPipe ran out of memory [log:1#257].\n\nLower Threads, then Retry [job:1] [help:attention.search_failed]."}])
    ans = assistant.ask(bed["ready"], "Why did my search fail?\x1b[2J", experiment="1", transport=model,
                        audit_path=bed["audit"])
    assert ans.grounded and ans.text.startswith("FragPipe ran out of memory [log:1#257].") and ans.rounds == 2
    assert ans.citations == ["log:1#257", "job:1", "help:attention.search_failed"] and ans.job_id == 1
    assert ans.sources[0].startswith("[log:1#257] Exception in thread") and "OutOfMemoryError" in ans.sources[0]
    # Ionomos looked the job up itself, as tool calls the model sees as data; then the model's own call
    assert [(c["name"], c["by"]) for c in ans.tool_calls] == [
        ("get_job", "ionomos"), ("list_attention", "ionomos"), ("log_tail", "ionomos"), ("search_help", "model")]
    first = model.requests[0]["messages"]
    assert [m["role"] for m in first] == ["system", "user", "assistant", "tool", "tool", "tool"]
    assert first[1]["content"] == "Why did my search fail?\n\n(This question is about job 1.)"
    assert json.loads(first[3]["content"])["status"] == "failed"
    (rec,) = audit.read(bed["audit"])
    assert rec["outcome"] == "grounded" and rec["model"] == fake.MODEL and rec["job"] == 1 and rec["rounds"] == 2
    assert rec["question"] == "Why did my search fail?" and rec["ts"] and rec["seconds"] >= 0
    assert rec["tool_calls"][3] == {"name": "search_help", "ok": True, "by": "model",
                                    "args_sha256": audit.digest('{"query": "search failed"}')}
    assert "search failed" not in json.dumps(rec["tool_calls"]), "arguments are hashed, not stored"


def test_not_set_up_is_a_normal_state_with_the_doctor_text(bed):
    model = fake.ScriptedModel([{"content": "should never be asked [job:1]"}])
    ans = assistant.ask(bed["cfg"], "Why did my search fail?", experiment="1", transport=model, audit_path=bed["audit"])
    (item,) = attention.items(bed["cfg"].log_dir)
    assert ans.outcome == "not_set_up" and not ans.grounded and model.requests == []
    assert ans.text.startswith("The assistant is off (assistant.enabled in config.yaml).")
    for own in (item.title, f"likely: {item.causes[0]}", f"do: {item.fixes[0]}",
                helpdoc.text("attention.search_failed"), "ask the person who looks after Ionomos"):
        assert own in ans.text
    named = replace(bed["cfg"], assistant={**assistant.DEFAULTS, "maintainer": "Nick (ext. 123)"})
    assert "ask the maintainer: Nick (ext. 123)." in assistant.ask(named, "why?", audit_path=bed["audit"]).text
    assert [r["outcome"] for r in audit.read(bed["audit"])] == ["not_set_up", "not_set_up"]
    # a question with no job: the best help entries, and how many things wait for a person
    ans = assistant.ask(bed["cfg"], "how do I leave a sample out", audit_path=bed["audit"])
    assert "(ionomos help faq.leave-out)" in ans.text and "1 thing(s) need a person right now" in ans.text
    assert "No question was asked" in assistant.ask(bed["cfg"], "  ", audit_path=bed["audit"]).text


def test_ask_about_an_attention_item_and_an_unknown_one(bed):
    (item,) = attention.items(bed["cfg"].log_dir)
    model = fake.ScriptedModel([{"content": "It ran out of memory [log:1#257]."}])
    ans = assistant.ask(bed["ready"], "what happened?", item_id=item.id, transport=model, audit_path=bed["audit"])
    assert ans.grounded and ans.job_id == 1 and audit.read(bed["audit"])[0]["item"] == item.id
    ans = assistant.ask(bed["ready"], "what happened?", item_id="no-such-item", transport=fake.ScriptedModel([]),
                        audit_path=bed["audit"])
    assert ans.outcome == "fallback" and ans.text.startswith("Note: there is no attention item no-such-item")
    assert assistant.find_job(bed["ledger"], "20260902-isoDTB_EJQ-2-027") == (1, "")
    assert assistant.find_job(bed["ledger"], "#1") == (1, "") and assistant.find_job(bed["ledger"], "ejq-2-027") == (1, "")
    assert assistant.find_job(bed["ledger"], "7")[1] == "there is no job 7"
    assert "no experiment is called" in assistant.find_job(bed["ledger"], "nothing\x1b like it")[1]


def test_audit_log_is_append_only_jsonl_in_appdata_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "_appdata_base", lambda: tmp_path / "appdata")
    assert audit.default_path() == service.appdata_dir() / names.ASSISTANT_AUDIT_FILE
    assert names.ASSISTANT_AUDIT_FILE == "assistant-audit.jsonl"
    p = audit.append({"question": "one", "outcome": "fallback"})
    audit.append({"question": "two é", "outcome": "grounded"})
    assert p == audit.default_path() and [r["question"] for r in audit.read()] == ["one", "two é"]
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(json.loads(ln)["ts"] for ln in lines)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("not json\n")
    audit.append({"question": "three"})
    assert [r["question"] for r in audit.read()] == ["one", "two é", "three"], "a damaged line is skipped, not fatal"
    assert audit.append({"question": "x"}, tmp_path / "appdata") is None  # a folder, not a file: logged, not raised
    assert audit.read(tmp_path / "nowhere.jsonl") == []


# ---------------------------------------------------------------------- CLI --


def test_cli_ask_prints_the_fallback_when_not_set_up_and_a_grounded_answer_when_it_is(bed, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_appdata_base", lambda: tmp_path / "appdata")
    argv = ["--config", str(bed["cfg_path"]), "ask", "why", "did", "it", "fail?", "--experiment", "1"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "The assistant is off" in out and "likely: FragPipe ran out of memory" in out and "(assistant: not_set_up)" in out

    d = yaml.safe_load(bed["cfg_path"].read_text(encoding="utf-8"))
    d["assistant"] = {"enabled": True, "model": fake.MODEL, "maintainer": "Nick"}
    bed["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    model = fake.ScriptedModel([{"content": "FragPipe ran out of memory [log:1#257]."}], stream=True)
    monkeypatch.setattr(client, "_http", model)
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert out.startswith("FragPipe ran out of memory [log:1#257].\n\nSources:\n  [log:1#257] Exception in thread")
    assert f"(assistant: grounded, model {fake.MODEL})" in out
    assert model.requests[0]["messages"][1]["content"].startswith("why did it fail?")

    assert cli.main([*argv, "--json"]) == 0  # the script has run out: an empty answer -> Ionomos's own text
    data = json.loads(capsys.readouterr().out)
    assert data["outcome"] == "fallback" and data["job_id"] == 1 and "ask the maintainer: Nick." in data["text"]
    assert [r["outcome"] for r in audit.read()] == ["not_set_up", "grounded", "fallback"]

    assert cli.main(["--config", str(bed["cfg_path"]), "check"]) in (0, 1)
    assert f"assistant                    model {fake.MODEL} at http://127.0.0.1:11434/v1" in capsys.readouterr().out


def test_the_help_explains_the_assistant():
    ents = helpdoc.entries()
    assert {"faq.assistant", "faq.assistant-setup"} <= set(ents)
    assert "ionomos ask" in ents["faq.assistant"].body and "changes nothing itself" in ents["faq.assistant"].body
    assert "faq.assistant-proposal" in ents and "Confirm" in ents["faq.assistant-proposal"].body
