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


# --- regression: Windows default encoding is cp1252, so every text file we write/read must say utf-8 -------------

def test_all_text_io_is_explicit_utf8():
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "labwatch"
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
    from labwatch import testbed

    cfg = testbed.init(tmp_path / "bed")
    text = cfg.read_text(encoding="utf-8")
    assert text.startswith("# labwatch TESTBED config")
    assert text.splitlines()[0].isascii()


def test_testbed_default_root_has_no_spaces_on_windows(monkeypatch):
    from labwatch import testbed

    monkeypatch.setattr(testbed.os, "name", "nt")
    assert " " not in str(testbed.default_root())
