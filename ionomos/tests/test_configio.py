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


def test_a_file_that_does_not_parse_names_the_line(tmp_path):
    """#136: the app, setupcheck and `ionomos init` read config.yaml through read_config; a hand-editing slip
    must be a ConfigError with the line, the same message `ionomos check` prints, never a crash."""
    from ionomos.config import ConfigError

    p = tmp_path / "broken.yaml"
    p.write_text("paths:\n  inbox: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        configio.read_config(p)
    assert str(ei.value).startswith(f"could not parse {p}: line 2, column 10")
    with pytest.raises(ConfigError) as ei2:
        load(p, check_paths=False)
    assert str(ei2.value) == str(ei.value)
