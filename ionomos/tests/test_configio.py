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
