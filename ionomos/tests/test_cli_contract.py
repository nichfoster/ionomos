"""The command line's contract, for every subcommand: the exit codes in cli.py's docstring, and a --help that
explains every option. A new subcommand fails these tests until it is listed here with what it does."""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3

import pytest

from ionomos import cli
from ionomos.cli import build_parser, main


def _walk(p: argparse.ArgumentParser, path=()):
    yield path, p
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                yield from _walk(sp, (*path, name))


def _subcommands(p: argparse.ArgumentParser) -> dict[str, str | None]:
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            helps = {c.dest: c.help for c in a._choices_actions}
            return {name: helps.get(name) for name in a.choices}
    return {}


TOP = list(_subcommands(build_parser()))

# --------------------------------------------------------------------------------------------- --help --


def _options(p):
    return [a for a in p._actions if not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))]


def _name(path, a) -> str:
    return " ".join([*path, a.option_strings[-1] if a.option_strings else a.dest])


def test_every_subcommand_has_a_one_line_help():
    for path, p in _walk(build_parser()):
        missing = [k for k, v in _subcommands(p).items() if not v]
        assert not missing, f"`ionomos {' '.join(path)}` lists {missing} without a help line"


def test_every_option_of_every_subcommand_is_explained_in_its_help():
    lacking = set()
    for path, p in _walk(build_parser()):
        text = p.format_help()
        for a in _options(p):
            if a.help == argparse.SUPPRESS:
                continue
            shown = a.option_strings or [p._get_formatter()._format_args(a, a.dest).strip("[]. ")]
            for s in shown:
                assert s in text, f"`ionomos {' '.join(path)} --help` does not mention {s}"
            if not (a.help or "").strip():  # every option and positional, in cli.py or a module's add_parser (#166)
                lacking.add(_name(path, a))
    assert not lacking, f"options without a help text: {sorted(lacking)}"


def test_the_docstring_has_the_exit_code_table():
    doc = cli.__doc__
    for code in ("0", "1", "2", "3", "70", "130"):
        assert re.search(rf"^\s+{code}\s+\S", doc, re.M), f"exit code {code} is not in cli.py's docstring"


@pytest.mark.parametrize("path", [p for p, _ in _walk(build_parser()) if p])
def test_wrong_usage_is_exit_2_everywhere(path, capsys):
    with pytest.raises(SystemExit) as exc:
        main([*path, "--certainly-not-an-option"])
    assert exc.value.code == 2
    assert "usage: ionomos" in capsys.readouterr().err


@pytest.mark.parametrize("path", [p for p, _ in _walk(build_parser())])
def test_help_is_exit_0_everywhere(path, capsys):
    with pytest.raises(SystemExit) as exc:
        main([*path, "--help"])
    assert exc.value.code == 0
    assert "usage: ionomos" in capsys.readouterr().out


# --------------------------------------------------------------------------------- an unreadable config --

BROKEN = "paths:\n  inbox: C:/x\nwatcher:\n  poll_seconds: [unclosed\n"

# argv after the subcommand; "config": the command needs config.yaml, so an unreadable one is exit 2 and the message
# names the file. "free": it works without one (a broken config gives the exit code a missing one gives).
# "own": covered by the module's own tests, with the reason.
CASES = {
    "setup": ("own", "opens the app: test_app.py (a broken config opens it on the defaults, #136)"),
    "run": ("config", ["--no-gui"]),
    "check": ("config", []),
    "doctor": ("own", "a check: an unreadable config is one of its ✗ rows, exit 1 (test_checks.py)"),
    "preflight": ("config", ["--static"]),
    "status": ("config", []),
    "dry-run": ("config", ["{tmp}"]),
    "names": ("config", ["test", "x_DIA"]),
    "retry": ("config", ["1"]),
    "diagnose": ("free", []),  # what you run when the config is broken: works, and shows it
    "spectronaut-columns": ("free", []),
    "bundle": ("own", "test_bundle.py: a diagnose bundle is made with a broken config on purpose"),
    "library": ("config", ["list"]),
    "stop": ("config", []),
    "init": ("config", []),  # an existing unreadable config is kept and named (#136, test_setup.py)
    "analyze": ("free", ["{tmp}/nothing-here"]),
    "check-folder": ("free", ["{tmp}"]),
    "export": ("free", ["{tmp}/nothing-here"]),
    "demo": ("free", ["{full}"]),
    "example": ("own", "downloads data: test_example.py"),
    "compare": ("free", ["{tmp}/nothing", "{tmp}/nothing2"]),
    "benchmark": ("free", ["--expected", "x.yaml"]),
    "help": ("free", ["pca", "--out", "{tmp}/help"]),
    "ask": ("config", ["why?"]),
    "ask-eval": ("config", ["--scripted"]),
    "attention": ("config", []),
    "qc-trend": ("config", []),
    "cancel": ("config", ["1"]),
    "pause": ("config", []),
    "resume": ("config", []),
    "notify-test": ("config", []),
    "repair-ledger": ("config", []),
    "update": ("free", []),
    "testbed": ("free", ["list", "{tmp}/nobed"]),
    "fasta": ("own", "network (UniProt): test_fastasource.py; every fasta command works without a config"),
    "tmt": ("free", ["suggest", "--tag", "TMT-10", "--conditions", "A,B", "--replicates", "2"]),
    "atlas": ("config", ["status"]),
}


def test_every_subcommand_is_in_the_contract_table():
    assert sorted(TOP) == sorted(CASES), "add the new subcommand to CASES with what an unreadable config does"


@pytest.fixture
def broken(tmp_path, monkeypatch):
    from ionomos import service

    monkeypatch.setattr(service, "source_checkout", lambda: None)  # `update` never runs git here
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = tmp_path / "broken.yaml"
    cfg.write_text(BROKEN, encoding="utf-8")
    full = tmp_path / "full"
    full.mkdir()
    (full / "x.txt").write_text("x", encoding="utf-8")
    return {"cfg": cfg, "missing": tmp_path / "missing.yaml", "tmp": tmp_path, "full": full}


def _argv(case, b):
    return [x.format(tmp=b["tmp"], full=b["full"]) for x in case]


@pytest.mark.parametrize("cmd", [c for c, (kind, _) in CASES.items() if kind == "config"])
def test_an_unreadable_config_is_exit_2_and_names_file_and_line(cmd, broken, capsys):
    code = main(["--config", str(broken["cfg"]), cmd, *_argv(CASES[cmd][1], broken)])
    out = capsys.readouterr()
    assert code == 2, (code, out)
    text = out.out + out.err
    assert str(broken["cfg"]) in text and "line 4" in text, text


@pytest.mark.parametrize("cmd", [c for c, (kind, _) in CASES.items() if kind == "free"])
def test_a_command_that_needs_no_config_does_not_care_if_it_is_broken(cmd, broken, capsys):
    argv = _argv(CASES[cmd][1], broken)
    with_missing = main(["--config", str(broken["missing"]), cmd, *argv])
    capsys.readouterr()
    assert main(["--config", str(broken["cfg"]), cmd, *argv]) == with_missing
    assert with_missing in (0, 1, 2)


# ------------------------------------------------------------------------- the codes on a working lab --


@pytest.mark.parametrize(("argv", "code"), [
    (["check"], 0),
    (["status"], 0),
    (["status", "--all"], 0),
    (["names", "test", "20260902-isoDTB_EJQ-2-027"], 0),
    (["names", "test", "nobody_isoDTB", "S_1_1.raw"], 1),
    (["names", "test", "--method", "nope", "x.raw"], 2),
    (["attention"], 0),
    (["attention", "show", "no-such-item"], 1),
    (["attention", "dismiss", "no-such-item"], 1),
    (["help", "certainly-not-a-topic"], 1),
    (["retry", "77"], 1),
    (["cancel", "77"], 1),
    (["analyze", "77"], 2),  # no ledger yet: give a folder
    (["stop"], 0),
    (["pause"], 0),
    (["resume"], 0),
    (["repair-ledger"], 0),
    (["spectronaut-columns"], 0),
    (["atlas", "status"], 0),
    (["atlas", "check"], 0),  # off: nothing is wrong
    (["atlas", "send", "1"], 1),  # no ledger yet: no job 1
])
def test_exit_codes_on_a_working_lab(lab, argv, code, capsys, monkeypatch):
    monkeypatch.setenv("APPDATA", str(lab["root"] / "appdata"))
    assert main(["--config", str(lab["cfg_path"]), *argv]) == code, capsys.readouterr()


def _with_ledger(lab):
    from ionomos.ledger import Ledger

    Ledger(lab["cfg"].database).close()


@pytest.mark.parametrize(("argv", "code"), [
    (["retry", "77"], 1), (["cancel", "77"], 1), (["analyze", "77"], 1), (["export", "77"], 1),
    (["atlas", "send", "77"], 1), (["atlas", "status", "77"], 1),
])
def test_a_job_that_is_not_in_the_ledger_is_exit_1(lab, argv, code, capsys):
    _with_ledger(lab)
    assert main(["--config", str(lab["cfg_path"]), *argv]) == code
    assert "no job 77" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["status"], ["retry", "1"], ["cancel", "1"]])
def test_a_damaged_ledger_is_exit_1_with_the_fix(lab, argv, capsys):
    lab["cfg"].database.write_bytes(b"this is not a database" * 100)
    assert main(["--config", str(lab["cfg_path"]), *argv]) == 1
    err = capsys.readouterr().err
    assert "repair-ledger" in err and str(lab["cfg"].database) in err


def test_a_damaged_ledger_is_never_touched_by_status(lab):
    lab["cfg"].database.write_bytes(b"garbage" * 100)
    main(["--config", str(lab["cfg_path"]), "status"])
    assert lab["cfg"].database.read_bytes() == b"garbage" * 100
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(lab["cfg"].database).execute("select 1 from sqlite_master").fetchall()


def test_run_with_a_log_folder_it_cannot_write_is_exit_2(lab, monkeypatch, capsys):
    from ionomos import health

    def refuse(self):
        raise PermissionError(13, "Access is denied", str(self.path))

    monkeypatch.setattr(health.InstanceLock, "acquire", refuse)
    root, before = logging.getLogger(), list(logging.getLogger().handlers)
    try:
        assert main(["--config", str(lab["cfg_path"]), "run", "--no-gui"]) == 2
    finally:  # run set up its own log handlers: put the test's back
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
        for h in before:
            if h not in root.handlers:
                root.addHandler(h)
    err = capsys.readouterr().err
    assert "Access is denied" in err and "log_dir" in err


def _app_traceback(err: str) -> bool:
    """A traceback from Ionomos itself; logging's own "--- Logging error ---" report of a handler an earlier
    test left on a closed stream (seen in the full suite only) is not one."""
    import re

    return "Traceback" in re.sub(r"--- Logging error ---.*?\nArguments: [^\n]*\n", "", err, flags=re.S)


def _run_keeping_log_handlers(argv):
    root, before = logging.getLogger(), list(logging.getLogger().handlers)
    try:
        return main(argv)
    finally:  # run set up its own log handlers: put the test's back
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
        for h in before:
            if h not in root.handlers:
                root.addHandler(h)


def test_run_with_a_read_only_lock_file_is_exit_2_with_the_fix(lab, capsys):
    """#172: a lock file restored from a backup (attrib +R) is a message and exit 2, not a traceback."""
    import os
    import stat

    from ionomos import health

    lock = lab["cfg"].log_dir / health.LOCK_NAME
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    os.chmod(lock, stat.S_IREAD)
    try:
        try:
            open(lock, "a+b").close()  # noqa: SIM115
            pytest.skip("this account writes read-only files (root)")
        except PermissionError:
            pass
        assert _run_keeping_log_handlers(["--config", str(lab["cfg_path"]), "run", "--no-gui"]) == 2
    finally:
        os.chmod(lock, stat.S_IREAD | stat.S_IWRITE)
    err = capsys.readouterr().err
    assert str(lock) in err and "read-only" in err and "Nothing was started" in err
    assert not _app_traceback(err), err
    assert lock.read_bytes() == b""


def test_run_with_a_damaged_ledger_open_elsewhere_is_exit_1_and_changes_nothing(lab, monkeypatch, capsys):
    """#172: a damaged ledger that can't be moved aside (open in DB Browser, the app) stops the watcher with a
    message and exit 1; the file is left as it was, and the lock is let go."""
    from pathlib import Path

    from ionomos import health

    db = lab["cfg"].database
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"this is not a database" * 100)
    real = Path.replace

    def held(self, target):
        if Path(self) == db:
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", held)
    assert _run_keeping_log_handlers(["--config", str(lab["cfg_path"]), "run", "--no-gui"]) == 1
    err = capsys.readouterr().err
    assert str(db) in err and "open in another program" in err and "Nothing was changed" in err
    assert not _app_traceback(err), err
    assert db.read_bytes() == b"this is not a database" * 100
    assert [p.name for p in db.parent.iterdir() if p.name.startswith(db.name + ".")] == []
    with health.InstanceLock(lab["cfg"].log_dir):  # released: the next start is not "already running"
        pass
