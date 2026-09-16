import pytest
import yaml

from labwatch.config import ConfigError, load


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
