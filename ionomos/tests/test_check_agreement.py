"""`ionomos check` and the app's setup checklist (setupcheck.run) look at the same PC and must say the same thing:
each test builds one state of a lab, runs both, and compares, topic by topic, whether each found a problem.
Before: an inbox inside the users folder, a users.default folder that doesn't exist and a folder the account can't
write were 'all good' for `check` (it loaded the config without the folder checks) while the app's checklist
failed them, and the watcher refused to start."""
from __future__ import annotations

import os
import re
import shutil

import pytest
import yaml

from ionomos import service, setupcheck
from ionomos.cli import main

# check's row label (start) -> the checklist item it is about
TOPICS = {
    "paths.": "folders", "folder layout": "layout", "users": "users", "  alias": "users", "FragPipe launcher": "fragpipe",
    "disk free": "disk", "watcher": "watcher", "starts at logon": "task", "config": "config",
}


def _check_problems(cfg_path, capsys) -> tuple[int, set[str]]:
    code = main(["--config", str(cfg_path), "check"])
    out = capsys.readouterr().out
    found, method = set(), None
    for ln in out.splitlines():
        m = re.match(r" ([✓!✗]) (.{28}) ?(.*)$", ln)
        if not m:
            continue
        mark, label = m.group(1), m.group(2).rstrip()
        if label.startswith("methods."):
            method = label.split(".", 1)[1]
        topic = f"method:{method}" if (label.startswith("methods.") or (not label and method)) else next(
            (t for k, t in TOPICS.items() if label.startswith(k)), None)
        if label:
            method = method if label.startswith("methods.") else None
        if topic and mark != "✓":
            found.add(topic)
    return code, found


def _checklist_problems(cfg_path) -> set[str]:
    items = setupcheck.run(cfg_path, probe_watcher=True)
    return {i.key for i in items if i.status != "ok" and i.key != "library"}


def _both(cfg_path, capsys):
    code, check = _check_problems(cfg_path, capsys)
    return code, check, _checklist_problems(cfg_path)


def _save(lab, d):
    lab["cfg_path"].write_text(yaml.safe_dump(d), encoding="utf-8")


@pytest.fixture
def pc(lab, monkeypatch):
    """The lab fixture with a FragPipe that is there and every method's workflow + FASTA, so that only what a test
    breaks is a problem; the Task Scheduler answers 'installed' unless a test says otherwise."""
    auto = lab["auto"]
    exe = auto / "FragPipe-24.0" / "bin" / "fragpipe.bat"
    exe.parent.mkdir(parents=True)
    exe.write_text("@echo off\n", encoding="utf-8")
    for m in lab["cfg_dict"]["methods"].values():
        (auto / "workflows" / m["workflow"]).write_text("x", encoding="utf-8")
    (auto / "fasta" / "h.fas").write_text(">sp|P1|A_HUMAN\nMK\n>rev_sp|P1|A_HUMAN\nKM\n", encoding="utf-8")
    d = yaml.safe_load(yaml.safe_dump(lab["cfg_dict"]))
    d["paths"]["fragpipe_exe"] = str(exe)
    _save(lab, d)
    monkeypatch.setattr(service, "task_status", lambda: "installed")
    return dict(lab, d=d)


def _same(check: set, checklist: set, topics: set) -> None:
    """Each topic is a problem for both or for neither."""
    for t in topics:
        assert (t in check) == (t in checklist), f"{t}: check says {'problem' if t in check else 'fine'}, " \
                                                 f"the app's checklist {'problem' if t in checklist else 'fine'}"


SHARED = {"folders", "layout", "users", "disk", "watcher", "config"}


def test_a_clean_lab(pc, capsys):
    _, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED | {"task"})
    assert "folders" not in check and "layout" not in check


def test_a_missing_folder(pc, capsys):
    shutil.rmtree(pc["auto"] / "fasta")
    code, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED)
    assert "folders" in check and code == 1


def test_a_folder_that_cannot_be_written(pc, capsys, monkeypatch):
    real = setupcheck.writable
    monkeypatch.setattr(setupcheck, "writable",
                        lambda f: (False, "not writable (Access is denied)") if f == pc["inbox"] else real(f))
    code, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED)
    assert "folders" in check and code == 1


def test_an_inbox_inside_the_users_folder(pc, capsys):
    inbox = pc["general"] / "inbox"
    inbox.mkdir()
    pc["d"]["paths"]["inbox"] = str(inbox)
    _save(pc, pc["d"])
    code, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED - {"config"})  # the checklist also says the config fails its folder checks
    assert "layout" in check and code == 1


def test_a_default_user_without_a_folder(pc, capsys):
    pc["d"]["users"]["default"] = "_nobody"
    _save(pc, pc["d"])
    code, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED - {"config"})
    assert "layout" in check and code == 1


def test_no_users_yet(pc, capsys):
    for u in pc["general"].iterdir():
        u.rmdir()
    pc["d"]["users"] = {}
    _save(pc, pc["d"])
    _, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED)
    assert "users" in check


def test_initials_for_a_user_without_a_folder(pc, capsys):
    pc["d"]["users"]["aliases"]["Ghost"] = ["GH"]
    _save(pc, pc["d"])
    code, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED)
    assert "users" in check and code == 0  # a warning in both, not a failure


@pytest.mark.parametrize("free", [0, None])
def test_no_disk_space_or_no_answer(pc, capsys, monkeypatch, free):
    from ionomos import health

    monkeypatch.setattr(health, "disk_free_gb", lambda p: free)
    _, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED)
    assert "disk" in check


class _WindowsOS:
    """The os module as a Windows PC shows it, for one module."""
    name = "nt"

    def __getattr__(self, k):
        return getattr(os, k)


@pytest.mark.parametrize("state", ["missing", "unavailable", "legacy"])
def test_the_startup_task(pc, capsys, monkeypatch, state):
    from ionomos import cli

    monkeypatch.setattr(service, "task_status", lambda: state)
    monkeypatch.setattr(setupcheck, "os", _WindowsOS())  # the modules' os, never os.name itself (CLAUDE.md)
    monkeypatch.setattr(cli, "os", _WindowsOS())
    _, check, checklist = _both(pc["cfg_path"], capsys)
    assert "task" in check and "task" in checklist


def test_no_fragpipe(pc, capsys):
    shutil.rmtree(pc["auto"] / "FragPipe-24.0")
    _, check, checklist = _both(pc["cfg_path"], capsys)
    _same(check, checklist, SHARED | {"fragpipe"})
    assert "fragpipe" in check


def test_the_parser_of_check_s_output_sees_problems(pc, capsys):
    """Guard for the comparison itself: on the clean lab both see the watcher not running, and no method."""
    _, check, checklist = _both(pc["cfg_path"], capsys)
    assert "watcher" in check and "watcher" in checklist
    assert {"method:isoDTB", "method:TMT", "method:DIA"}.isdisjoint(check | checklist)
