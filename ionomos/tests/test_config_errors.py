"""config.yaml as a lab member edits it: every error names the file, the key, its line and what to do; a value of
the wrong type is an error, never a crash or a silent surprise; a Save that dies half-way loses nothing."""
from __future__ import annotations

import datetime as dt
import os
import re

import pytest
import yaml

from ionomos import configio
from ionomos.config import ConfigError, LiveConfig, load, remember_alias, write_text_atomic


def _line_of(text: str, key: str) -> int:
    """1-based line where the last part of a dotted key is written (the test's own reading of the file)."""
    parts = key.split(".")
    depth, line = 0, 0
    for i, ln in enumerate(text.splitlines(), 1):
        if depth < len(parts) and re.match(rf"\s*['\"]?{re.escape(parts[depth])}['\"]?\s*:", ln):
            depth, line = depth + 1, i
    return line


def _write(lab, mutate) -> tuple:
    d = yaml.safe_load(yaml.safe_dump(lab["cfg_dict"]))
    mutate(d)
    text = yaml.safe_dump(d, sort_keys=False)
    lab["cfg_path"].write_text(text, encoding="utf-8")
    return lab["cfg_path"], text


def _set(path: str, value):
    def f(d):
        *head, last = path.split(".")
        cur = d
        for h in head:
            cur = cur.setdefault(h, {})
        cur[last] = value
    return f


# (what is wrong, the key the message must name, words of the fix)
CASES = [
    (_set("watcher.poll_seconds", 0), "watcher.poll_seconds", "above 0"),
    (_set("watcher.poll_seconds", "ten"), "watcher.poll_seconds", "a number, e.g. 10"),
    (_set("watcher.stable_seconds", -5), "watcher.stable_seconds", "at least 0"),
    (_set("watcher.min_raw_files", 1.5), "watcher.min_raw_files", "a whole number"),
    (_set("fragpipe.threads", "eight"), "fragpipe.threads", "a whole number, e.g. 8"),
    (_set("fragpipe.timeout_minutes", float("nan")), "fragpipe.timeout_minutes", "at least 0"),
    (_set("fragpipe.auto_run", "false"), "fragpipe.auto_run", "true or false (without quotes)"),
    (_set("gui.review_drops", "no"), "gui.review_drops", "true or false"),
    (_set("gui.timeout_minutes", [5]), "gui.timeout_minutes", "a number"),
    (_set("gui", "off"), "gui", "must be a mapping"),
    (_set("watcher", 5), "watcher", "must be a mapping"),
    (_set("users", ["Isaac"]), "users", "must be a mapping"),
    (_set("users.aliases", ["IJ"]), "users.aliases", "must be a mapping"),
    (_set("users.aliases.Isaac", {"IJ": 1}), "users.aliases.Isaac", "a list of names"),
    (_set("users.learned_aliases_file", 5), "users.learned_aliases_file", "a file path"),
    (_set("paths.inbox", ""), "paths.inbox", "folder or file path, e.g."),
    (_set("paths.log_dir", 7), "paths.log_dir", "folder or file path"),
    (lambda d: d["paths"].pop("database"), "paths", "add 'database:' under 'paths:'"),
    (lambda d: d.pop("paths"), None, "missing section 'paths:'; add it"),
    (_set("methods", {}), "methods", "e.g. DIA: {workflow"),
    (_set("methods.DIA", "DIA.workflow"), "methods.DIA", "workflow, fasta, data_type"),
    (_set("methods.DIA.data_type", "dia"), "methods.DIA.data_type", "DDA or DIA (in capitals)"),
    (lambda d: d["methods"]["TMT"].pop("fasta"), "methods.TMT", "add 'fasta:' under methods.TMT"),
    (_set("methods.TMT.engine", "msfragger"), "methods.TMT.engine", "fragpipe, diann, maxquant or sage"),
    (_set("methods.TMT.aliases", {"a": 1}), "methods.TMT.aliases", "a list of names"),
    (_set("naming.condition_codes", ["D"]), "naming.condition_codes", "e.g. {D: DMSO"),
    (_set("analysis.normalise", "median"), "analysis.normalise", "known:"),
    (_set("analysis.log2fc", "big"), "analysis", ""),
]


@pytest.mark.parametrize(("mutate", "key", "fix"), CASES)
def test_every_error_names_the_file_the_key_its_line_and_the_fix(lab, mutate, key, fix):
    path, text = _write(lab, mutate)
    with pytest.raises(ConfigError) as exc:
        load(path, check_paths=False)
    msg = str(exc.value)
    assert msg.startswith(str(path)), msg
    assert fix in msg, msg
    if key:
        assert key.split(".")[-1] in msg, msg
        want = _line_of(text, key)
        assert want and f"line {want}:" in msg, (want, msg)


def test_the_watcher_s_folder_checks_name_each_key_s_line(lab):
    path, text = _write(lab, _set("paths.users_root", str(lab["root"] / "gone")))
    with pytest.raises(ConfigError) as exc:
        load(path)
    msg = str(exc.value)
    assert msg.startswith(f"{path}: config problems:")
    assert "paths.users_root: folder does not exist" in msg and f"(line {_line_of(text, 'paths.users_root')})" in msg


def test_a_missing_file_says_how_to_make_one(tmp_path):
    with pytest.raises(ConfigError, match=r"config file not found: .*nope\.yaml\. Create one .*ionomos init"):
        load(tmp_path / "nope.yaml")


def test_the_app_s_probe_file_is_not_named(lab, tmp_path):
    """The app validates unsaved settings in .config.checking.yaml: that hidden file is not the person's file."""
    probe = lab["cfg_path"].with_name(".config.checking.yaml")
    d = dict(lab["cfg_dict"], watcher={"poll_seconds": 0})
    probe.write_text(yaml.safe_dump(d), encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load(probe, check_paths=False)
    assert str(exc.value).startswith("'watcher.poll_seconds' must be above 0")


@pytest.mark.parametrize(("where", "value", "expect"), [
    ("users.aliases.Isaac", "IJ", ["IJ"]),  # was ['I', 'J']: 'I' and 'J' then meant Isaac
    ("users.aliases.Isaac", None, []),
    ("users.aliases.Isaac", 42, ["42"]),
])
def test_one_alias_written_alone_is_one_alias(lab, where, value, expect):
    path, _ = _write(lab, _set(where, value))
    assert load(path, check_paths=False).user_aliases["Isaac"] == expect


def test_one_method_alias_or_postprocess_step_alone_is_one(lab):
    def m(d):
        d["methods"]["TMT"]["aliases"] = "tmt"  # was ('t', 'm', 't'): every name with a 't' meant TMT
        d["methods"]["TMT"]["postprocess"] = "tmt_annotation"
        d["users"] = {"ignore": "FragPipe*"}  # was 'F', 'r', 'a' ... as patterns
    path, _ = _write(lab, m)
    cfg = load(path, check_paths=False)
    assert cfg.methods["TMT"].aliases == ("tmt",) and cfg.methods["TMT"].postprocess == ("tmt_annotation",)
    assert cfg.user_ignore == ("FragPipe*",)


def test_empty_values_mean_the_default(lab):
    path, _ = _write(lab, lambda d: d.update(watcher={"poll_seconds": None, "stable_seconds": None},
                                            fragpipe={"threads": None, "min_free_gb": None}, gui=None, users=None))
    cfg = load(path, check_paths=False)
    assert (cfg.poll_seconds, cfg.stable_seconds, cfg.threads, cfg.min_free_gb, cfg.gui_enabled) == (10, 60, 8, 0, True)


def test_yes_no_and_ints_are_still_flags(lab):
    path, _ = _write(lab, lambda d: d.update(fragpipe={"auto_run": 0}, gui={"enabled": False, "popups": 1}))
    cfg = load(path, check_paths=False)
    assert (cfg.auto_run, cfg.gui_enabled, cfg.gui_popups) == (False, False, True)


def test_a_learned_alias_written_alone_is_one(lab):
    (lab["auto"] / "learned_aliases.yaml").write_text("Chris: CK\n", encoding="utf-8")
    assert load(lab["cfg_path"], check_paths=False).user_aliases["Chris"] == ["CK"]


# ------------------------------------------------------------ a Save that dies half-way --


def _cfg(tmp_path):
    p = tmp_path / "config.yaml"
    d = configio.defaults(str(tmp_path / "A"), str(tmp_path / "G"))
    configio.write_config(p, d)
    return p, d


@pytest.mark.parametrize("dies_in", ["write", "fsync", "replace"])
def test_a_save_that_dies_half_way_leaves_the_previous_config(tmp_path, monkeypatch, dies_in):
    p, d = _cfg(tmp_path)
    before = p.read_bytes()
    d["watcher"]["stable_seconds"] = 99

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    if dies_in == "write":
        real_open = open

        class Half:
            def __init__(self, fh):
                self.fh = fh

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.fh.close()

            def write(self, text):
                self.fh.write(text[: len(text) // 2])
                raise OSError(28, "No space left on device")

        monkeypatch.setattr("builtins.open", lambda f, m="r", *a, **k: Half(real_open(f, m, *a, **k))
                            if str(f).endswith(".tmp") else real_open(f, m, *a, **k))
    else:
        monkeypatch.setattr(os, dies_in, boom)
    with pytest.raises(OSError):
        configio.write_config(p, d)
    monkeypatch.undo()
    assert p.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))  # nothing half-written left beside it
    assert load(p, check_paths=False).stable_seconds == 60


def test_a_dump_that_fails_touches_nothing(tmp_path):
    p, d = _cfg(tmp_path)
    before = p.read_bytes()
    del d["paths"]
    with pytest.raises(KeyError):
        configio.write_config(p, d)
    assert p.read_bytes() == before and not (tmp_path / configio.BACKUP_DIR).exists()


def test_a_tmp_left_by_a_killed_save_is_harmless(tmp_path):
    p, d = _cfg(tmp_path)
    (tmp_path / "config.yaml.tmp").write_text("paths:\n  inbox: [half", encoding="utf-8")
    assert load(p, check_paths=False).stable_seconds == 60
    d["watcher"]["stable_seconds"] = 7
    configio.write_config(p, d)
    assert load(p, check_paths=False).stable_seconds == 7 and not (tmp_path / "config.yaml.tmp").exists()


def test_two_saves_in_one_second_keep_both_backups(tmp_path, monkeypatch):
    """The first Save's backup is the version before both; the second used to overwrite it."""
    p, d = _cfg(tmp_path)

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 15, 12, 0, 0)

    monkeypatch.setattr(dt, "datetime", Frozen)
    d["watcher"]["stable_seconds"] = 1
    configio.write_config(p, d)
    d["watcher"]["stable_seconds"] = 2
    configio.write_config(p, d)
    monkeypatch.undo()
    backups = sorted((tmp_path / configio.BACKUP_DIR).glob("config-*.yaml"))
    texts = [b.read_text(encoding="utf-8") for b in backups]
    assert len(backups) == 2 and any("stable_seconds: 60" in t for t in texts)


def test_live_config_keeps_the_last_good_one_while_a_save_is_half_done(lab):
    live = LiveConfig(lab["cfg"])
    text = lab["cfg_path"].read_text(encoding="utf-8")
    lab["cfg_path"].write_text(text[: len(text) // 3], encoding="utf-8")  # a writer caught mid-save
    os.utime(lab["cfg_path"], ns=(1, 1))
    assert live.get() is lab["cfg"]


def test_remembered_aliases_are_written_all_at_once(lab, monkeypatch):
    remember_alias(lab["cfg"], "Chris", "CK")
    f = lab["cfg"].learned_aliases_file
    before = f.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError(28, "disk full")))
    with pytest.raises(OSError):
        remember_alias(lab["cfg"], "Chris", "CKK")
    assert f.read_bytes() == before and not list(f.parent.glob("*.tmp"))


def test_write_text_atomic_replaces_the_file(tmp_path):
    p = tmp_path / "x.yaml"
    write_text_atomic(p, "a: 1\n")
    write_text_atomic(p, "a: 2\n")
    assert yaml.safe_load(p.read_text(encoding="utf-8")) == {"a": 2}
