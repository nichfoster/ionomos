"""Setup: layout safety rules, the checklist, headless `ionomos init`."""
import os

import pytest
import yaml

from ionomos import setupcheck
from ionomos.cli import main
from ionomos.config import ConfigError, load


def _cfg_with(lab, tmp_path, **paths):
    d = dict(lab["cfg_dict"])
    d["paths"] = {**d["paths"], **{k: str(v) for k, v in paths.items()}}
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(d), encoding="utf-8")
    return p


@pytest.mark.parametrize("case", ["same", "users_in_inbox", "inbox_in_users", "logs_in_inbox"])
def test_dangerous_layouts_are_refused(lab, tmp_path, case):
    inbox, general = lab["inbox"], lab["general"]
    if case == "same":
        p = _cfg_with(lab, tmp_path, users_root=inbox)
    elif case == "users_in_inbox":
        (inbox / "users").mkdir()
        p = _cfg_with(lab, tmp_path, users_root=inbox / "users")
    elif case == "inbox_in_users":
        (general / "inbox").mkdir()
        p = _cfg_with(lab, tmp_path, inbox=general / "inbox")
    else:
        (inbox / "logs").mkdir()
        p = _cfg_with(lab, tmp_path, log_dir=inbox / "logs")
    with pytest.raises(ConfigError, match="inbox"):
        load(p)


def test_checklist_on_a_working_lab(lab):
    items = {i.key: i for i in setupcheck.run(lab["cfg_path"], probe_watcher=True)}
    assert items["folders"].status == "ok" and items["layout"].status == "ok"
    assert items["users"].status == "ok" and items["config"].status == "ok"
    assert items["fragpipe"].status == "todo" and items["fragpipe"].action == "find_fragpipe"
    assert items["watcher"].status == "todo" and items["watcher"].action == "start_watcher"
    assert items["method:isoDTB"].status == "todo"  # FASTA missing in the fixture
    assert "Next:" in setupcheck.summary(list(items.values()))


def test_checklist_before_anything_exists(tmp_path):
    from ionomos import configio

    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    items = {i.key: i for i in setupcheck.run(tmp_path / "Auto" / "config.yaml", d)}
    assert items["folders"].status == "todo" and items["folders"].action == "create_folders"
    assert items["config"].status == "todo" and items["config"].action == "save"
    assert items["users"].status == "todo"
    assert "fragpipe" not in items  # needs a saved config first


@pytest.mark.skipif(os.name == "nt", reason="chmod read-only isn't enforced for directories on Windows")
def test_unwritable_folder_is_a_failure(lab):
    lab["inbox"].chmod(0o500)
    try:
        items = {i.key: i for i in setupcheck.run(lab["cfg_path"], probe_watcher=False)}
        assert items["folders"].status == "fail" and "not writable" in items["folders"].detail
    finally:
        lab["inbox"].chmod(0o700)


def test_init_creates_a_working_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    root, users = tmp_path / "Auto", tmp_path / "General"
    code = main(["init", "--root", str(root), "--users", str(users)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert (root / "config.yaml").is_file() and (root / "inbox").is_dir() and users.is_dir()
    assert "Folders exist and are writable" in out and "of" in out and "ready" in out
    cfg = load(root / "config.yaml")
    assert cfg.analysis["test"] == "moderated"
    # second run keeps the config
    (root / "config.yaml").write_text((root / "config.yaml").read_text(encoding="utf-8").replace(
        "stable_seconds: 60", "stable_seconds: 42"), encoding="utf-8")
    assert main(["init", "--root", str(root), "--users", str(users)]) == 0
    assert load(root / "config.yaml").stable_seconds == 42
