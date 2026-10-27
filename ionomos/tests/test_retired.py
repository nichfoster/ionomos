"""Retired keys (retired.py, D133): docs/VISION.md promises that a key or option that goes away warns for at least
one minor release first. While a key is in retired.RETIRED, config.yaml and experiment.yaml that still hold it load
as before, a renamed key's value counts under its new name, and each says so: `ionomos check`, the app's checklist,
the analysis' notes, once in the log. These tests run the mechanism on an example table (the real one is empty
today) and pin the real table's entries to what the promise needs."""
from __future__ import annotations

import logging
import re
from dataclasses import fields

import pytest
import yaml

from ionomos import __version__, configio, manifest, retired, setupcheck
from ionomos.cli import main
from ionomos.config import load
from ionomos.downstream.analysis import Settings, settings_from

R = retired.Retired
CONFIG, EXPERIMENT = retired.CONFIG, retired.EXPERIMENT
EXAMPLE = (
    R("watcher.poll_secs", "watcher.poll_seconds", "0.20.0"),
    R("gui.sound", None, "0.20.0", "the app no longer plays a sound"),
    R("analysis.normalise", "analysis.normalize", "0.20.0", files=(CONFIG, EXPERIMENT)),
    R("allow_uneven", "allow_uneven_fractions", "0.20.0", files=(EXPERIMENT,)),
    R("files.*.rep", "files.*.bioreplicate", "0.20.0", files=(EXPERIMENT,)),
)


@pytest.fixture
def example(monkeypatch):
    monkeypatch.setattr(retired, "RETIRED", EXAMPLE)
    monkeypatch.setattr(retired, "_said", set())
    return EXAMPLE


def _version(v: str) -> tuple[int, ...]:
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), v
    return tuple(int(x) for x in v.split("."))


def _known(file: str, dotted: str) -> bool:
    """Whether a dotted key is one the loaders read today."""
    parts = dotted.split(".")
    if parts[0] == "analysis":
        return len(parts) == 2 and parts[1] in {f.name for f in fields(Settings)}
    if file == EXPERIMENT:
        if parts[0] == "files":
            return len(parts) == 3 and parts[2] in manifest._FILE_KEYS
        return len(parts) == 1 and parts[0] in manifest._TOP_KEYS
    d = configio.defaults()
    for part in parts:
        if not isinstance(d, dict) or part not in d:
            return False
        d = d[part]
    return True


@pytest.mark.parametrize("table", [retired.RETIRED, EXAMPLE], ids=["the real table", "the example"])
def test_every_entry_is_retired_for_good_and_names_where_it_went(table):
    """An entry names a version already released (or this one), a key no loader reads any more, and a new key that
    one does: else the warning would point at nothing, or a key in use would be renamed away."""
    for r in table:
        assert _version(r.since) <= _version(__version__), r
        assert r.files and set(r.files) <= {CONFIG, EXPERIMENT}, r
        for file in r.files:
            assert not _known(file, r.key.replace("*", "x")), f"{r.key} is still read in {file}"
            assert r.new is None or _known(file, r.new.replace("*", "x")), f"{r.new} is not a key of {file}"
    with pytest.raises(ValueError, match="within its own section"):
        R("watcher.poll_secs", "fragpipe.poll_seconds", "0.20.0")


def test_with_nothing_retired_nothing_changes():
    d = {"watcher": {"poll_secs": 3}, "analysis": {"normalise": "median"}}
    assert retired.apply(d, CONFIG, table=()) == (d, []) and retired.apply(d, CONFIG, table=())[0] is d
    assert retired.analysis(d["analysis"], table=()) == (d["analysis"], [])
    assert retired.apply(["not", "a", "mapping"], CONFIG, table=EXAMPLE) == (["not", "a", "mapping"], [])


# (what the file holds, what loads from it, the warnings: a part of each, in order)
CASES = [
    ("renamed", CONFIG, {"watcher": {"poll_secs": 30}}, {"watcher": {"poll_seconds": 30}},
     ["watcher.poll_secs was renamed watcher.poll_seconds in Ionomos 0.20.0: its value is used as "
      "watcher.poll_seconds for now. Rename it in config.yaml"]),
    ("both set: the new one wins", CONFIG, {"watcher": {"poll_secs": 30, "poll_seconds": 5}},
     {"watcher": {"poll_seconds": 5}}, ["and both are set: watcher.poll_seconds is used, watcher.poll_secs is "
                                        "ignored. Delete watcher.poll_secs from config.yaml."]),
    ("removed", CONFIG, {"gui": {"sound": True, "enabled": False}}, {"gui": {"enabled": False}},
     ["gui.sound was retired in Ionomos 0.20.0 and is ignored: the app no longer plays a sound. Delete it from "
      "config.yaml."]),
    ("an analysis key, in config.yaml", CONFIG, {"analysis": {"normalise": "median"}},
     {"analysis": {"normalize": "median"}}, ["analysis.normalise was renamed analysis.normalize"]),
    ("an analysis key, in experiment.yaml", EXPERIMENT, {"analysis": {"normalise": "none"}},
     {"analysis": {"normalize": "none"}}, ["Rename it in experiment.yaml"]),
    ("each file's own keys only", CONFIG, {"allow_uneven": True}, {"allow_uneven": True}, []),
    ("a key under every name of a mapping", EXPERIMENT, {"files": {"a.raw": {"rep": 2}, "b.raw": {"rep": 3}}},
     {"files": {"a.raw": {"bioreplicate": 2}, "b.raw": {"bioreplicate": 3}}},
     ["files.a.raw.rep was renamed files.a.raw.bioreplicate", "files.b.raw.rep was renamed"]),
    ("nothing retired in the file", EXPERIMENT, {"method": "DIA", "user": "EJQ"}, {"method": "DIA", "user": "EJQ"},
     []),
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_a_retired_key_is_renamed_or_left_out_and_said(example, case):
    _name, file, held, loads, said = case
    got, words = retired.apply(held, file)
    assert got == loads and len(words) == len(said)
    for w, part in zip(words, said, strict=True):
        assert part in w
    assert held == case[2], "the caller's dict is not changed"


def test_config_yaml_loads_with_retired_keys_and_check_and_the_checklist_say_so(example, lab, tmp_path, capsys):
    d = dict(lab["cfg_dict"])
    d["watcher"] = {**{k: v for k, v in d["watcher"].items() if k != "poll_seconds"}, "poll_secs": 30}
    d["gui"] = {"sound": True}
    d["analysis"] = {"normalise": "none"}
    p = tmp_path / "old.yaml"
    p.write_text(yaml.safe_dump(d), encoding="utf-8")
    cfg = load(p, check_paths=False)
    assert cfg.poll_seconds == 30 and settings_from(cfg.analysis).normalize == "none"
    assert len(cfg.retired_keys) == 3 and set(cfg.retired_keys) <= set(cfg.warnings) and cfg.unknown_keys == ()
    assert main(["--config", str(p), "check"]) in (0, 1)
    out = capsys.readouterr().out
    assert " ! config" in out and "watcher.poll_secs was renamed watcher.poll_seconds in Ionomos 0.20.0" in out
    assert "gui.sound was retired in Ionomos 0.20.0 and is ignored" in out
    item = next(i for i in setupcheck.run(p, probe_watcher=False) if i.key == "config")
    assert item.status == "warn" and "poll_secs" in item.detail
    # the app's Save writes the renamed key under its new name, with its value; the removed one is kept as it was
    saved = configio.write_config(tmp_path / "saved.yaml", configio.read_config(p), backup=False)
    raw = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert raw["watcher"]["poll_seconds"] == 30 and "poll_secs" not in raw["watcher"]
    assert raw["analysis"]["normalize"] == "none" and "normalise" not in raw["analysis"]
    assert raw["gui"]["sound"] is True
    again = load(saved, check_paths=False)
    assert again.poll_seconds == 30 and [w for w in again.retired_keys if "gui.sound" in w]
    assert not [w for w in again.retired_keys if "poll_secs" in w]


def test_experiment_yaml_with_retired_keys_loads_and_is_said_once_in_the_log(example, tmp_path, caplog):
    (tmp_path / "experiment.yaml").write_text(yaml.safe_dump(
        {"method": "DIA", "allow_uneven": True, "files": {"x_1.raw": {"rep": 2}}, "analysis": {"normalise": "none"}}),
        encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="ionomos.config"):
        ov = manifest.load_overrides(tmp_path)
        again = manifest.load_overrides(tmp_path)  # read again for every analysis: said once in the log
    assert ov.allow_uneven_fractions and ov.files["x_1.raw"].bioreplicate == 2 and ov.analysis == {"normalize": "none"}
    assert len(ov.warnings) == 3 and again.warnings == ov.warnings
    assert [r.getMessage() for r in caplog.records] == ov.warnings
    # a Save puts the renamed keys under their new names, never beside them
    text = manifest.overrides_text(tmp_path, manifest.Overrides(method="DIA"))
    data = yaml.safe_load(text)
    assert "allow_uneven" not in data and data["allow_uneven_fractions"] is True
    assert data["files"] == {"x_1.raw": {"bioreplicate": 2}} and data["analysis"] == {"normalize": "none"}


def test_the_analysis_notes_a_retired_key_of_experiment_yaml(example, tmp_path):
    from ionomos import postprocess

    (tmp_path / "experiment.yaml").write_text("analysis:\n  normalise: none\n", encoding="utf-8")
    p = postprocess.prepare(tmp_path, None)
    assert p["overrides"] == {"normalize": "none"}
    assert [n for n in p["notes"] if n.startswith("analysis.normalise was renamed analysis.normalize")]


def test_settings_from_anywhere_take_a_renamed_analysis_key(example, caplog):
    """A job record written before the rename holds the old key: it still analyses, and the log says so once."""
    with caplog.at_level(logging.WARNING, logger="ionomos.config"):
        assert settings_from({"normalise": "none"}).normalize == "none"
        assert settings_from({"normalise": "none"}, {"normalize": "median"}).normalize == "median"
    assert len(caplog.records) == 1 and "config.yaml or experiment.yaml" in caplog.records[0].getMessage()
