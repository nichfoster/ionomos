"""Confirmed actions (ROADMAP Phase 6.2, D75): the proposal tools, what they refuse, the one path that applies a
proposal (the Confirm button of popups.ProposalDialog -> assistant/actions.apply), and `ionomos ask`, which only
prints one. The scenario corpus replays whole conversations (tests/test_assistant_scenarios.py); the windows'
tests open real Tk windows and skip on a developer's machine (they run in CI)."""
import ast
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from ionomos import assistant, attention, cli, names, popups, service, testbed
from ionomos.assistant import actions, askui, audit, fake, proposals, tools
from ionomos.config import load
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.manifest import EXPERIMENT_YAML, backup_experiment_yaml, save_analysis
from ionomos.worker import Worker
from tests.conftest import gui_tests, make_tk_root

SRC = Path(cli.__file__).parent
_gui_ok, _gui_why = gui_tests()
gui = pytest.mark.skipif(not _gui_ok, reason=f"no GUI: {_gui_why}")


@pytest.fixture
def bed(tmp_path, monkeypatch):
    """Job 1 failed (out of memory); job 2 done, analysed (DMSO_1-3, Drug_1-3)."""
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.setattr(service, "_appdata_base", lambda: tmp_path / "appdata")
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    led = Ledger(cfg.database)
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "oom")
    assert intake(testbed.drop(tmp_path / "bed", "iso_good"), cfg, led).value == "queued"
    Worker(cfg, led).run_once()
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE")
    assert intake(testbed.drop(tmp_path / "bed", "dia_good"), cfg, led).value == "queued"
    Worker(cfg, led).run_once()
    assert led.get(1).status == "failed" and led.get(2).status == "done"
    ready = replace(cfg, assistant=assistant.settings_from({"enabled": True, "model": fake.MODEL, "stream": False}))
    yield {"cfg": ready, "cfg_path": cfg_path, "ledger": led, "dest": Path(led.get(2).dest_dir),
           "audit": tmp_path / "audit.jsonl"}
    led.close()


def _ctx(bed):
    return tools.Context(bed["cfg"], bed["ledger"])


def _snapshot(bed):
    cfg = bed["cfg"]
    roots = (cfg.users_root, cfg.inbox, cfg.log_dir)
    files = {str(p): p.read_bytes() for r in roots for p in r.rglob("*") if p.is_file() and p.suffix != ".log"}
    return files, [(j.id, j.status, j.attempts, j.reason) for j in bed["ledger"].list()]


GOOD = [("propose_retry", {"job_id": 1}),
        ("propose_condition", {"job_id": 2, "sample": "Drug_3", "condition": "DMSO"}),
        ("propose_condition", {"job_id": 2, "sample": "Drug_3", "condition": "Drug_late"}),
        ("propose_leave_out", {"job_id": 2, "sample": "DMSO_2", "leave_out": True}),
        ("propose_setting", {"job_id": 2, "key": "imputation", "value": "none"}),
        ("propose_setting", {"job_id": 2, "key": "normalize", "value": "median"}),
        ("propose_setting", {"job_id": 2, "key": "alpha", "value": "0.01"}),
        ("propose_setting", {"job_id": 2, "key": "log2fc", "value": "0.58"}),
        ("propose_setting", {"job_id": 2, "key": "control", "value": "dmso"}),
        ("propose_setting", {"job_id": 2, "key": "de_type", "value": "all"}),
        ("propose_setting", {"job_id": 2, "key": "comparisons", "value": "Drug vs DMSO"}),
        ("propose_role", {"job_id": 2, "condition": "DMSO", "role": "vehicle"})]


def test_the_proposal_tools_are_a_closed_whitelist_without_paths():
    assert [t.name for t in proposals.TOOLS] == ["propose_retry", "propose_condition", "propose_leave_out",
                                                 "propose_setting", "propose_role"]
    assert proposals.SETTINGS == ("imputation", "normalize", "alpha", "log2fc", "control", "de_type", "comparisons")
    assert [s["function"]["name"] for s in assistant.schemas()] == [t.name for t in (*tools.TOOLS, *proposals.TOOLS)]
    for sc in proposals.schemas():
        params = sc["function"]["parameters"]
        assert params["additionalProperties"] is False and "job_id" in params["required"]
        for name, spec in params["properties"].items():
            assert not any(w in name for w in ("path", "file", "folder", "dir", "url", "cmd", "command")), name
            assert spec["type"] in ("integer", "string", "boolean")
            assert spec["type"] != "string" or "enum" in spec or spec["maxLength"] <= 200
    assert "propose_" in assistant.SYSTEM_PROMPT and "confirms nothing" in assistant.SYSTEM_PROMPT


@pytest.mark.parametrize(("name", "args"), GOOD, ids=[f"{n}-{a.get('key', a.get('condition', ''))}" for n, a in GOOD])
def test_every_proposal_is_built_from_checked_arguments_and_changes_nothing(bed, name, args):
    before = _snapshot(bed)
    ctx = _ctx(bed)
    res = proposals.call(ctx, name, args)
    p = ctx.proposal
    assert "error" not in res and p is not None and p.tool == name and p.job_id == args["job_id"], res
    assert "nothing has changed" in res["status"] and res["proposed"] == p.title
    assert p.title and p.changes and p.id and len(p.args_sha256) == 64
    if p.action == "analysis":
        assert p.diff.startswith(f"--- {EXPERIMENT_YAML} (now)") and "+++" in p.diff and p.analysis
        assert "App: Jobs tab → select job 2 → Analysis options…" in p.app_steps
    else:
        assert p.commands == ["ionomos retry 1"] and p.diff == ""
    assert _snapshot(bed) == before, "proposing reads only"
    assert proposals.call(ctx, name, args)["error"].startswith("one proposal per question")


@pytest.mark.parametrize(("name", "args", "why"), [
    ("propose_retry", {"job_id": 2}, "job 2 is done; only a failed search can be retried"),
    ("propose_retry", {"job_id": 99}, "there is no job 99"),
    ("propose_retry", {"job_id": "all"}, "job_id must be a whole number"),
    ("propose_retry", {}, "missing argument 'job_id'"),
    ("propose_leave_out", {"job_id": 2, "sample": "DMSO_9", "leave_out": True}, "'DMSO_9' is not one of this experiment's samples"),
    ("propose_leave_out", {"job_id": 2, "sample": "../../x", "leave_out": True}, "is not one of this experiment's samples"),
    ("propose_leave_out", {"job_id": 2, "sample": "DMSO_1", "leave_out": False}, "DMSO_1 is already used"),
    ("propose_leave_out", {"job_id": 2, "sample": "DMSO_1", "leave_out": True, "path": "/x"}, "unknown argument 'path'"),
    ("propose_leave_out", {"job_id": 1, "sample": "x_1", "leave_out": True}, "has no analysed samples yet"),
    ("propose_condition", {"job_id": 2, "sample": "Drug_1", "condition": "C:\\Windows"}, "a new condition must be a plain name"),
    ("propose_condition", {"job_id": 2, "sample": "Drug_1", "condition": "Drug"}, "Drug_1 is already in Drug"),
    ("propose_setting", {"job_id": 2, "key": "fasta", "value": "x.fas"}, "key must be one of"),
    ("propose_setting", {"job_id": 2, "key": "alpha", "value": "2"}, "alpha must be between 0 and 1"),
    ("propose_setting", {"job_id": 2, "key": "imputation", "value": "magic"}, "imputation must be one of"),
    ("propose_setting", {"job_id": 2, "key": "control", "value": "Placebo"}, "'Placebo' is not one of this experiment's conditions"),
    ("propose_setting", {"job_id": 2, "key": "comparisons", "value": "Drug vs Drug"}, "two different conditions"),
    ("propose_setting", {"job_id": 2, "key": "comparisons", "value": "Drug"}, "must look like 'Drug vs DMSO'"),
    ("propose_setting", {"job_id": 2, "key": "log2fc", "value": ""}, "already says that"),
    ("propose_role", {"job_id": 2, "condition": "DMSO", "role": "overlord"}, "role 'overlord' is not one of"),
    ("propose_role", {"job_id": 2, "condition": "Drug", "role": "competition of Placebo"}, "'Placebo' is not one of"),
    ("propose_nothing", {"job_id": 2}, "there is no proposal tool"),
])
def test_invalid_arguments_are_refused_before_any_window(bed, name, args, why):
    ctx = _ctx(bed)
    if name in proposals.BY_NAME:
        res = proposals.call(ctx, name, args)
        assert why in res["error"] and res["error"].startswith("not proposed"), res
    with pytest.raises(proposals.Refused, match=None) as exc:
        proposals.build(ctx, name, args)
    assert why in str(exc.value)
    assert ctx.proposal is None, "a refusal leaves nothing to show"


def test_leaving_every_sample_out_or_a_broken_experiment_yaml_is_refused(bed):
    dest = bed["dest"]
    save_analysis(dest, {"exclude_samples": ["DMSO_1", "DMSO_2", "DMSO_3", "Drug_1", "Drug_2"]})
    with pytest.raises(proposals.Refused, match="every sample out"):
        proposals.build(_ctx(bed), "propose_leave_out", {"job_id": 2, "sample": "Drug_3", "leave_out": True})
    p = proposals.build(_ctx(bed), "propose_leave_out", {"job_id": 2, "sample": "DMSO_1", "leave_out": False})
    assert p.analysis == {"exclude_samples": ["DMSO_2", "DMSO_3", "Drug_1", "Drug_2"]}
    (dest / EXPERIMENT_YAML).write_text("analysis: [unclosed\n", encoding="utf-8")
    with pytest.raises(proposals.Refused, match="experiment.yaml cannot be read"):
        proposals.build(_ctx(bed), "propose_setting", {"job_id": 2, "key": "alpha", "value": "0.01"})


@pytest.mark.parametrize(("name", "args"), GOOD, ids=[f"{n}-{a.get('key', a.get('condition', ''))}" for n, a in GOOD])
def test_confirm_applies_through_the_apps_own_code_with_a_backup(bed, name, args):
    p = proposals.build(_ctx(bed), name, args)
    dest = bed["dest"]
    (dest / EXPERIMENT_YAML).write_text("notes: kept by hand\n", encoding="utf-8")
    p = proposals.build(_ctx(bed), name, args)  # proposed against the file as it is now
    with pytest.raises(ValueError, match="only after its Confirm"):
        actions.apply(bed["cfg"], p, confirmed=False)
    done = actions.apply(bed["cfg"], p, confirmed=True, audit_path=bed["audit"])
    assert done.ok, done.message
    if p.action == "retry":
        job = bed["ledger"].get(1)
        assert job.status == "queued" and job.attempts == 0 and job.reason == "retry requested"
        assert not [i for i in attention.items(bed["cfg"].log_dir) if i.kind == "search_failed" and i.job_id == 1]
    else:
        data = yaml.safe_load((dest / EXPERIMENT_YAML).read_text(encoding="utf-8"))
        assert data["notes"] == "kept by hand" and data["analysis"] == p.analysis
        (backup,) = (dest / names.EXPERIMENT_BACKUP_DIR).iterdir()
        assert backup.read_text(encoding="utf-8").splitlines() == ["notes: kept by hand"] and done.backup == str(backup)
        new = (dest / EXPERIMENT_YAML).read_text(encoding="utf-8").splitlines()
        assert [ln[1:] for ln in p.diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")] == \
               [ln for ln in new if ln not in ("notes: kept by hand",)], "the window showed what was written"
    (rec,) = audit.read(bed["audit"])
    assert rec["event"] == "proposal_decision" and rec["confirmed"] is True and rec["applied"] is True
    assert rec["proposal"] == p.id and rec["args_sha256"] == p.args_sha256
    again = actions.apply(bed["cfg"], p, confirmed=True, audit_path=bed["audit"])
    assert not again.ok and "Nothing was changed" in again.message


def test_a_stale_proposal_is_not_applied(bed):
    p = proposals.build(_ctx(bed), "propose_setting", {"job_id": 2, "key": "alpha", "value": "0.01"})
    save_analysis(bed["dest"], {"log2fc": 2.0})  # someone saved in the editor meanwhile
    before = _snapshot(bed)
    done = actions.apply(bed["cfg"], p, confirmed=True, audit_path=bed["audit"])
    assert not done.ok and "changed after the assistant proposed this" in done.message
    assert _snapshot(bed) == before
    r = proposals.build(_ctx(bed), "propose_retry", {"job_id": 1})
    bed["ledger"].requeue(1, "someone pressed Retry")
    assert not actions.apply(bed["cfg"], r, confirmed=True, audit_path=bed["audit"]).ok
    assert [x["applied"] for x in audit.read(bed["audit"])] == [False, False]
    actions.decide(p, False, audit_path=bed["audit"])
    assert audit.read(bed["audit"])[-1]["confirmed"] is False


def test_backups_are_never_replaced(tmp_path):
    (tmp_path / EXPERIMENT_YAML).write_text("notes: one\n", encoding="utf-8")
    a = backup_experiment_yaml(tmp_path)
    (tmp_path / EXPERIMENT_YAML).write_text("notes: two\n", encoding="utf-8")
    b = backup_experiment_yaml(tmp_path)
    assert a != b and a.read_text(encoding="utf-8") == "notes: one\n" and b.read_text(encoding="utf-8") == "notes: two\n"
    assert backup_experiment_yaml(tmp_path / "nowhere") is None
    assert save_analysis(tmp_path, {"log2fc": 0.5})[1] is not None
    assert save_analysis(tmp_path, {"log2fc": 0.5}) == (None, None), "nothing to change: no write, no backup"
    assert len(list((tmp_path / names.EXPERIMENT_BACKUP_DIR).iterdir())) == 3


# ------------------------------------------------- who can apply: read the source --


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            yield node, (fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")), fn


def test_only_the_confirm_button_applies_a_proposal():
    """Every entry point: the chat loop, the tools, the proposal tools, askui, `ionomos ask`, ask-eval and the
    windows. actions.apply is called in one place only, ProposalDialog.confirm; nothing else in the assistant
    retries a job or saves experiment.yaml."""
    sites = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for _node, called, fn in _calls(tree):
            if called == "apply" and isinstance(fn, ast.Attribute) and getattr(fn.value, "id", "") == "actions":
                sites.append(path.relative_to(SRC).as_posix())
    assert sites == ["popups.py"]
    assert "actions.apply(" in inspect.getsource(popups.ProposalDialog.confirm)
    for cls_fn in (popups.ProposalDialog.cancel, popups.ProposalDialog.close, popups.ProposalDialog.__init__,
                   popups.AskWindow.show, popups.Popups.propose, cli.cmd_ask):
        assert "actions.apply" not in inspect.getsource(cls_fn)
    writers = {"request_retry", "requeue", "save_analysis", "save_overrides", "set_status", "write_text", "unlink"}
    for name in ("__init__.py", "tools.py", "proposals.py", "askui.py", "evaluate.py", "fake.py", "citations.py"):
        tree = ast.parse((SRC / "assistant" / name).read_text(encoding="utf-8"))
        bad = [called for _n, called, _f in _calls(tree) if called in writers]
        assert name == "evaluate.py" and set(bad) <= {"write_text"} or not bad, (name, bad)
    src = (SRC / "assistant" / "actions.py").read_text(encoding="utf-8")
    assert "request_retry(" in src and "save_analysis(" in src and "requeue(" not in src and "write_text" not in src


def test_every_retry_and_every_editor_save_is_the_same_code():
    assert "save_analysis(" in inspect.getsource(__import__("ionomos.experiment_editor").experiment_editor
                                                .ExperimentEditor.save_choices)
    assert "request_retry(" in inspect.getsource(cli.cmd_retry)
    assert "request_retry(" in inspect.getsource(popups.ItemWindow.retry)
    app_src = (SRC / "app.py").read_text(encoding="utf-8")
    assert "request_retry(led, j.id" in app_src and 'led.requeue(j.id, "retry requested"' not in app_src


# ------------------------------------------------------------ entry points --


def test_a_proposal_is_offered_only_with_a_grounded_answer_and_audited(bed):
    turns = [{"tool_calls": [{"name": "propose_retry", "arguments": {"job_id": 1}}]},
             {"content": "I proposed retrying job 1; confirm it in the window [job:1]."}]
    ans = assistant.ask(bed["cfg"], "retry it", experiment="2", transport=fake.ScriptedModel(turns),
                        audit_path=bed["audit"])
    assert ans.grounded and ans.proposal.tool == "propose_retry"
    assert ans.proposal.note == "the question was about job 2; this proposal changes job 1."
    shown = askui.render(ans)
    assert shown.proposal is ans.proposal and askui.PROPOSED.format(title=ans.proposal.title) in shown.text
    (rec,) = audit.read(bed["audit"])
    assert rec["proposals"] == [{"id": ans.proposal.id, "tool": "propose_retry", "args_sha256": ans.proposal.args_sha256,
                                 "job": 1, "title": ans.proposal.title, "offered": True}] and rec["confirmed"] == []
    # an answer that does not pass: the proposal is withheld, and audited as such
    bad = [turns[0], {"content": "Retried [job:9]."}, {"content": "Retried [job:9]."}]
    ans = assistant.ask(bed["cfg"], "retry it", experiment="1", transport=fake.ScriptedModel(bad),
                        audit_path=bed["audit"])
    assert ans.outcome == "fallback" and ans.proposal is None and "not offered" in ans.withheld
    assert askui.render(ans).proposal is None
    assert audit.read(bed["audit"])[-1]["proposals"][0]["offered"] is False
    assert bed["ledger"].get(1).status == "failed", "nothing was retried"


def test_cli_ask_prints_the_proposal_and_the_command_and_changes_nothing(bed, capsys, monkeypatch):
    from ionomos.assistant import client

    d = yaml.safe_load(bed["cfg_path"].read_text(encoding="utf-8"))
    d["assistant"] = {"enabled": True, "model": fake.MODEL, "stream": False}
    bed["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")
    before = _snapshot(bed)
    for args, cmd in (({"job_id": 1}, "  ionomos retry 1"),):
        model = fake.ScriptedModel([{"tool_calls": [{"name": "propose_retry", "arguments": args}]},
                                    {"content": "I proposed retrying job 1 [job:1]."}])
        monkeypatch.setattr(client, "_http", model)
        assert cli.main(["--config", str(bed["cfg_path"]), "ask", "retry", "job", "1"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert "Proposed change (nothing has changed; `ionomos ask` never makes a change):" in out
        assert cmd in out and "Retry job 1 (20260902-isoDTB_EJQ-2-027)" in out
    model = fake.ScriptedModel([{"tool_calls": [{"name": "propose_leave_out",
                                                 "arguments": {"job_id": 2, "sample": "DMSO_2", "leave_out": True}}]},
                                {"content": "I proposed leaving DMSO_2 out [job:2]."}])
    monkeypatch.setattr(client, "_http", model)
    assert cli.main(["--config", str(bed["cfg_path"]), "ask", "leave", "DMSO_2", "out", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["proposal"]["tool"] == "propose_leave_out" and data["proposal"]["commands"] == [
        "ionomos analyze 2 --exclude DMSO_2"]
    assert _snapshot(bed)[1] == before[1] and not (bed["dest"] / EXPERIMENT_YAML).exists()


def test_printed_commands_quote_names_or_leave_them_out():
    assert proposals._flags([("--exclude", "DMSO_2"), ("--exclude", "Drug 3")]) == [
        "--exclude DMSO_2", '--exclude "Drug 3"']
    for hostile in ('a"b', "x%PATH%", "x$(rm)", "a\\b", "a\nb"):
        assert proposals._flags([("--exclude", hostile)]) is None


# ------------------------------------------------------- real windows (CI) --


@pytest.fixture
def tk_pops(bed):
    root = make_tk_root()
    root.withdraw()
    host = popups.PopupHost(root=root, log_dir=lambda: bed["cfg"].log_dir, config_path=lambda: bed["cfg_path"],
                            open_path=lambda p: None)
    pops = popups.Popups(root, host, is_app=True)
    yield root, pops
    root.destroy()


@gui
def test_gui_cancel_escape_and_closing_change_nothing(bed, tk_pops, monkeypatch):
    monkeypatch.setattr(audit, "default_path", lambda: bed["audit"])
    _root, pops = tk_pops
    before = _snapshot(bed)
    for how in ("cancel", "escape", "close"):
        p = proposals.build(_ctx(bed), "propose_retry", {"job_id": 1})
        dlg = pops.propose(p)
        assert dlg is not None and dlg.win.focus_get() in (dlg.cancel_btn, None)
        assert p.title in dlg.body.get("1.0", "end")
        assert pops.propose(proposals.build(_ctx(bed), "propose_retry", {"job_id": 1})) is None, "one at a time"
        # Escape: check the binding and call its handler. A synthetic <Escape> reaches the window only while it has
        # keyboard focus, which a Windows CI runner often doesn't give it (the same as #45's <Return>).
        assert "on_escape" in dlg.win.bind("<Escape>")
        {"cancel": dlg.cancel, "escape": dlg.on_escape, "close": dlg.close}[how]()
        dlg.win.update() if dlg.alive() else None
        assert not dlg.alive() and dlg.done is None
    assert _snapshot(bed) == before
    assert [r["confirmed"] for r in audit.read(bed["audit"])] == [False] * 6


@gui
def test_gui_confirm_applies_once(bed, tk_pops, monkeypatch):
    monkeypatch.setattr(audit, "default_path", lambda: bed["audit"])
    _root, pops = tk_pops
    p = proposals.build(_ctx(bed), "propose_leave_out", {"job_id": 2, "sample": "DMSO_2", "leave_out": True})
    dlg = pops.propose(p)
    dlg.confirm()
    dlg.confirm()  # a second press does nothing
    assert dlg.done.ok and "Saved in job 2's experiment.yaml" in dlg.status.cget("text")
    assert yaml.safe_load((bed["dest"] / EXPERIMENT_YAML).read_text(encoding="utf-8"))["analysis"] == p.analysis
    assert [r["applied"] for r in audit.read(bed["audit"])] == [True]
    dlg.close()
    assert not dlg.alive()
