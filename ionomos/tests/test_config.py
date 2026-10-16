import types

import pytest
import yaml

from ionomos.config import ConfigError, load


def test_load_ok_with_warnings(lab):
    cfg = lab["cfg"]
    assert cfg.inbox.is_dir()
    assert set(cfg.methods) == {"isoDTB", "TMT", "DIA"}
    assert cfg.methods["isoDTB"].extra == {"isodtb_mod_mass": "561.3387"}
    assert cfg.methods["DIA"].aliases == ("dia", "diann", "dia-nn")
    assert cfg.known_users() == ["EJQ", "Isaac", "Chris", "Aman", "Taylor_Elements", "_unsorted"] or \
        set(cfg.known_users()) == {"EJQ", "Isaac", "Chris", "Aman", "Taylor_Elements", "_unsorted"}
    # fragpipe.exe and two workflows are missing -> warnings, not errors
    assert any("fragpipe_exe" in w for w in cfg.warnings)
    assert any("TMT" in w for w in cfg.warnings)


def test_missing_inbox_is_error(lab):
    d = lab["cfg_dict"]
    d["paths"]["inbox"] = str(lab["auto"] / "nope")
    lab["cfg_path"].write_text(yaml.safe_dump(d))
    with pytest.raises(ConfigError, match="inbox"):
        load(lab["cfg_path"])
    # but dry-run style loading skips the check
    assert load(lab["cfg_path"], check_paths=False).inbox.name == "nope"


def test_space_in_path(lab):
    import os

    d = lab["cfg_dict"]
    d["paths"]["users_root"] = "C:/Program Files/x"
    lab["cfg_path"].write_text(yaml.safe_dump(d))
    if os.name == "nt":
        with pytest.raises(ConfigError, match="space"):
            load(lab["cfg_path"], check_paths=False)
    else:
        cfg = load(lab["cfg_path"], check_paths=False)
        assert any("space" in w for w in cfg.warnings)


def test_bad_method(lab):
    d = lab["cfg_dict"]
    d["methods"]["DIA"]["data_type"] = "WTF"
    lab["cfg_path"].write_text(yaml.safe_dump(d))
    with pytest.raises(ConfigError, match="data_type"):
        load(lab["cfg_path"], check_paths=False)


def test_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load("/definitely/not/here.yaml")


# --- regression: Windows default encoding is cp1252, so every text file we write/read must say utf-8 -------------

def test_all_text_io_is_explicit_utf8():
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "ionomos"
    bad = []
    for f in src.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"\.(write_text|read_text)\(", text):
            # take the call up to its closing paren (calls here never nest parens deeper than one level)
            depth, j = 1, m.end()
            while depth and j < len(text):
                depth += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            if "encoding=" not in text[m.start():j]:
                bad.append(f"{f.name}:{text.count(chr(10), 0, m.start()) + 1}: {text[m.start():j][:80]}")
    assert not bad, "text I/O without encoding='utf-8' (breaks on Windows):\n" + "\n".join(bad)


def test_testbed_config_is_utf8_readable(tmp_path):
    from ionomos import testbed

    cfg = testbed.init(tmp_path / "bed")
    text = cfg.read_text(encoding="utf-8")
    assert text.startswith("# ionomos TESTBED config")
    assert text.splitlines()[0].isascii()


def test_testbed_default_root_has_no_spaces_on_windows(monkeypatch):
    from ionomos import testbed

    # stub testbed.os rather than patching os.name globally: on Python 3.11 that
    # makes every Path() built meanwhile a WindowsPath, which POSIX refuses
    monkeypatch.setattr(testbed, "os", types.SimpleNamespace(name="nt"))
    assert " " not in str(testbed.default_root())


def test_unknown_keys_are_warned_about_with_the_nearest_known_one(lab, tmp_path):
    """#137: a typo (poll_secnds, thread, timout_minutes, notifyy:) was silently ignored and every check said
    all good while the PC ran with the defaults. A warning, not an error: an older config still loads."""
    from ionomos import configio
    from ionomos.config import _CHECKED_SECTIONS, _SECTION_EXTRA, unknown_settings

    d = dict(lab["cfg_dict"])
    d["watcher"] = {**d["watcher"], "poll_secnds": 1}
    d["fragpipe"] = {**d["fragpipe"], "thread": 4, "timout_minutes": 1}
    d["notifyy"] = {"enabled": True}
    p = tmp_path / "typos.yaml"
    p.write_text(yaml.safe_dump(d), encoding="utf-8")
    cfg = load(p, check_paths=False)
    assert cfg.poll_seconds == lab["cfg_dict"]["watcher"]["poll_seconds"]  # the typo did not count
    w = list(cfg.warnings)
    assert "watcher.poll_secnds: unknown setting, did you mean poll_seconds? (ignored)" in w
    assert "fragpipe.thread: unknown setting, did you mean threads? (ignored)" in w
    assert "fragpipe.timout_minutes: unknown setting, did you mean timeout_minutes? (ignored)" in w
    assert "notifyy: unknown section, did you mean notify? (ignored)" in w
    assert set(cfg.unknown_keys) == {x for x in w if "unknown" in x} and len(cfg.unknown_keys) == 4
    with pytest.raises(ConfigError, match="notify.includes_names: unknown setting"):  # notify: checks its own
        p.write_text(yaml.safe_dump({**d, "notify": {"enabled": False, "includes_names": True}}), encoding="utf-8")
        load(p, check_paths=False)
    assert not [x for x in load(lab["cfg_path"], check_paths=False).warnings if "unknown" in x]
    assert unknown_settings(configio.defaults()) == []  # every key the defaults write is known
    assert set(_SECTION_EXTRA) <= set(_CHECKED_SECTIONS)


def test_check_and_the_checklist_show_an_unknown_key(lab, tmp_path, capsys):
    from ionomos import setupcheck
    from ionomos.cli import main

    d = dict(lab["cfg_dict"])
    d["watcher"] = {**d["watcher"], "stable_secs": 1}
    p = tmp_path / "typo.yaml"
    p.write_text(yaml.safe_dump(d), encoding="utf-8")
    assert main(["--config", str(p), "check"]) in (0, 1)
    out = capsys.readouterr().out
    assert " ! config" in out and "watcher.stable_secs: unknown setting, did you mean stable_seconds?" in out
    item = next(i for i in setupcheck.run(p, probe_watcher=False) if i.key == "config")
    assert item.status == "warn" and "stable_secs" in item.detail
