import copy

import pytest
import yaml

from ionomos import configio
from ionomos.config import load


def test_defaults_roundtrip_and_validate(tmp_path):
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    d["users"]["aliases"] = {"Isaac": ["IJ", "IJD"]}
    p = configio.write_config(tmp_path / "config.yaml", d)
    text = p.read_text()
    assert "# ionomos configuration" in text and "isodtb_mod_mass" in text
    back = yaml.safe_load(text)
    assert back["paths"]["inbox"].endswith("/Auto/inbox")
    assert back["users"]["aliases"] == {"Isaac": ["IJ", "IJD"]}
    assert back["methods"]["isoDTB"]["postprocess"] == ["isodtb_sites"]
    cfg = load(p, check_paths=False)  # the real loader accepts what we wrote
    assert cfg.user_aliases["Isaac"] == ["IJ", "IJD"]
    assert configio.read_config(p)["methods"] == d["methods"]


def test_read_merges_defaults_for_partial_file(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("paths:\n  inbox: X/inbox\nmethods:\n  DIA: {workflow: d.workflow, fasta: f, data_type: DIA}\n")
    d = configio.read_config(p)
    assert d["paths"]["inbox"] == "X/inbox"
    assert d["watcher"]["stable_seconds"] == 60          # default filled in
    assert list(d["methods"]) == ["DIA"]                   # methods are replaced, not merged


def test_read_missing_is_defaults(tmp_path):
    assert configio.read_config(tmp_path / "nope.yaml")["watcher"]["poll_seconds"] == 10


def test_empty_yaml_entries_come_back_typed(tmp_path):
    """'aliases:' with nothing under it loads as None; the app crashed on startup with such a config."""
    p = tmp_path / "config.yaml"
    d = configio.defaults(str(tmp_path / "A"), str(tmp_path / "G"))
    configio.write_config(p, d)  # no aliases -> an empty 'aliases:' entry
    assert "  aliases:\n" in p.read_text(encoding="utf-8")
    back = configio.read_config(p)
    assert back["users"]["aliases"] == {} and back["users"]["default"] == ""
    p.write_text("users:\n  aliases:\n    Isaac: IJ\nmethods:\n  isoDTB:\n    workflow: a\n    postprocess:\n", encoding="utf-8")
    back = configio.read_config(p)
    assert back["users"]["aliases"] == {"Isaac": ["IJ"]}
    assert back["methods"]["isoDTB"]["postprocess"] == [] and back["methods"]["isoDTB"]["aliases"] == []
    p.write_text("paths:\nwatcher:\nusers:\nmethods:\n", encoding="utf-8")
    back = configio.read_config(p)
    assert back["paths"]["inbox"] and back["methods"]["isoDTB"]


def test_app_save_keeps_review_and_condition_codes(tmp_path):
    # the app rewrites config.yaml from a template: new keys must survive a Save, and a lab's own code list
    # must replace the defaults (not be merged with them, which would bring D / C back)
    from ionomos.configio import dump_config, read_config

    p = tmp_path / "config.yaml"
    d = read_config(p)
    assert d["gui"]["review_drops"] is True and d["naming"]["condition_codes"] == {"D": "DMSO", "C": "Compound"}
    d["gui"]["review_drops"] = False
    d["naming"]["condition_codes"] = {"V": "Vehicle"}
    p.write_text(dump_config(d), encoding="utf-8")
    back = read_config(p)
    assert back["gui"]["review_drops"] is False and back["naming"]["condition_codes"] == {"V": "Vehicle"}


def test_app_save_keeps_the_lab_wide_tmt_settings(tmp_path):
    # irs, tmt_reference and the TMT QC limits set in config.yaml survive the app's Save (D48, D91)
    from ionomos.configio import dump_config, read_config
    from ionomos.downstream.analysis import settings_from

    p = tmp_path / "config.yaml"
    d = read_config(p)
    d["analysis"].update({"irs": "reference", "tmt_reference": ["126", "134N"], "tmt_min_labelling": 0.9,
                          "tmt_min_purity": 0.6})
    p.write_text(dump_config(d), encoding="utf-8")
    back = read_config(p)["analysis"]
    assert (back["irs"], back["tmt_reference"], back["tmt_min_labelling"], back["tmt_min_purity"]) == \
        ("reference", ["126", "134N"], 0.9, 0.6)
    s = settings_from({k: v for k, v in back.items() if k != "enabled"})
    assert (s.irs, s.tmt_reference, s.tmt_min_labelling, s.tmt_min_purity) == ("reference", ["126", "134N"], 0.9, 0.6)
    assert load(p, check_paths=False).analysis["tmt_min_purity"] == 0.6   # the real loader accepts it
    # not set: nothing is written, so the defaults stay the analysis's own
    p.write_text(dump_config(read_config(tmp_path / "none.yaml")), encoding="utf-8")
    assert "tmt_min_purity" not in p.read_text(encoding="utf-8")


# ------------------------------------------------- what a Save changes (S2.5) --


def _base():
    return configio.defaults("C:/Fragpipe_Auto", "C:/Fragpipe_General")


def _change(edit):
    new = copy.deepcopy(_base())
    edit(new)
    return new


@pytest.mark.parametrize("edit, lines", [
    (lambda d: None, []),
    (lambda d: d["fragpipe"].update(threads=24), ["FragPipe → Threads: 28 → 24"]),
    (lambda d: d["fragpipe"].update(threads=28.0), []),                         # 28 and 28.0 are one setting
    (lambda d: d["users"]["aliases"].update(Isaac=["IJ", "IJD"]), ["Users → Aliases → Isaac: (not set) → IJ, IJD"]),
    (lambda d: d["users"].update(ignore=[]), ["Users → Ignore: FragPipe*, Fasta*, New folder*, ~* → (none)"]),
    (lambda d: d["methods"]["isoDTB"].update(fasta="b.fas"),
     ["Methods → isoDTB → fasta: human_reviewed_decoys.fas → b.fas"]),
    (lambda d: d["methods"]["isoDTB"].update(isodtb_mod_mass="1"),
     ["Methods → isoDTB → isodtb mod mass: 561.3387 → 1"]),
    (lambda d: d["gui"].update(popups=False), ["Windows → Popups: on → off"]),
    (lambda d: d["watcher"].update(min_raw_files=3, poll_seconds=5),
     ["Watcher → Poll seconds: 10 → 5", "Watcher → Min raw files: 1 → 3"]),     # in the order of the file
    (lambda d: d["fragpipe"].update(config_diann="C:/DIA-NN/DiaNN.exe"),
     ["FragPipe → Config diann: (empty) → C:/DIA-NN/DiaNN.exe"]),
    (lambda d: d["qc_trend"].update(instrument="Eclipse"), ["Instrument QC → Instrument: (empty) → Eclipse"]),
    (lambda d: d["methods"].pop("DIA"),
     ["Methods → DIA → aliases: dia, diann, dia-nn → (removed)", "Methods → DIA → workflow: DIA.workflow → (removed)",
      "Methods → DIA → fasta: human_reviewed_decoys.fas → (removed)", "Methods → DIA → data type: DIA → (removed)"]),
    (lambda d: d["analysis"].update(sdrf=None), []),                           # empty and not set say the same
    (lambda d: d.update(my_block={"a_key": 1}), ["my block → A key: (not set) → 1"]),
])
def test_diff_words(edit, lines):
    assert configio.diff_words(_base(), _change(edit)) == lines


def test_diff_words_masks_secrets_on_both_sides():
    old = _change(lambda d: d["notify"]["slack"].update(url="https://hooks.slack.com/services/T0/B0/old-secret"))
    new = _change(lambda d: d["notify"]["slack"].update(url="https://hooks.slack.com/services/T0/B0/new-secret"))
    new["notify"]["email"]["password"] = "hunter2"
    lines = configio.diff_words(old, new)
    assert lines == ["Notifications → Slack → url: ••• → •••", "Notifications → Email → password: (empty) → •••"]
    assert "secret" not in "".join(lines) and "hunter2" not in "".join(lines)


def test_diff_words_of_what_the_file_holds(tmp_path):
    # the app's case: the saved file read back against the window's dict
    p = tmp_path / "config.yaml"
    configio.write_config(p, _base(), backup=False)
    saved = configio.read_config(p)
    assert configio.diff_words(saved, configio.read_config(p)) == []
    assert configio.diff_words(saved, _change(lambda d: d["fragpipe"].update(ram_gb=64))) == \
        ["FragPipe → Ram gb: 48 → 64"]


@pytest.mark.parametrize("n, words", [(0, "nothing changes"), (1, "1 setting changes"), (3, "3 settings change")])
def test_diff_summary(n, words):
    assert configio.diff_summary(["x"] * n) == words


# -------------------------------------------------------- the backups (S2.4) --


def test_backups_newest_first_with_their_times(tmp_path):
    import datetime

    p = tmp_path / "config.yaml"
    assert configio.backups(p) == [] and configio.latest_backup(p) is None
    configio.write_config(p, _change(lambda d: d["fragpipe"].update(threads=1)))  # nothing to back up yet
    assert configio.backups(p) == []
    names = ["config-20010101-090000.yaml", "config-20031014-175959.yaml", "config-20020515-120000.yaml"]
    for i, name in enumerate(names):  # each Save backs up the file before it
        configio.write_config(p, _change(lambda d, i=i: d["fragpipe"].update(threads=i + 2)))
        made = configio.latest_backup(p)
        made.rename(made.parent / name)  # backup_config names by the second: give each its own time
    configio.write_config(p, _base())  # the newest backup, made now, holds threads: 4
    now = configio.backups(p)[0]
    assert configio.backup_time(now).date() == datetime.date.today()
    assert [b.name for b in configio.backups(p)[1:]] == sorted(names, reverse=True)
    assert configio.latest_backup(p) == now
    assert configio.read_config(now)["fragpipe"]["threads"] == 4
    assert configio.read_config(p.parent / "config-backups" / names[1])["fragpipe"]["threads"] == 2
    assert configio.backup_time(names[1]) == datetime.datetime(2003, 10, 14, 17, 59, 59)
    assert configio.backup_time("config.yaml") is None and configio.backup_time("config-2026-x.yaml") is None
