"""Hand-edited YAML that isn't UTF-8 (D86): Notepad's "ANSI" (cp1252) is read with a warning, a BOM is fine,
and a file nothing can read stops with a ConfigError naming the byte and line, not a bare UnicodeDecodeError.

The lab PC's case (2026-09-16, LabWatch; still on 0.18.1): an em dash in a comment saved as byte 0x97."""
import logging
import os

import pytest
import yaml

from ionomos import configio, notify
from ionomos.config import ConfigError, LiveConfig, load, read_yaml_text
from ionomos.manifest import EXPERIMENT_YAML, OverridesError, load_overrides, overrides_text, parse_overrides

COMMENT = "# Lab settings — edited in Notepad on the PC\n"  # the em dash: 0x97 in cp1252, 3 bytes in UTF-8


def _write(path, text: str, encoding: str) -> None:
    path.write_bytes(text.encode(encoding))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))  # LiveConfig sees a change


def _config_text(lab, **users) -> str:
    d = dict(lab["cfg_dict"])
    if users:
        d["users"] = users
    return COMMENT + yaml.safe_dump(d, allow_unicode=True)


@pytest.mark.parametrize("encoding, warned", [
    ("utf-8", False),
    ("utf-8-sig", False),  # Notepad's "UTF-8 with BOM"
    ("utf-16", False),     # Notepad's "UTF-16 LE" (Python writes the BOM)
    ("cp1252", True),      # Notepad's "ANSI"
])
def test_config_loads_in_every_encoding_notepad_saves(lab, encoding, warned):
    _write(lab["cfg_path"], _config_text(lab, aliases={"Isaac": ["Müller"]}), encoding)
    cfg = load(lab["cfg_path"], check_paths=False)
    assert cfg.user_aliases["Isaac"] == ["Müller"]  # ü read as ü, not mojibake
    enc = [w for w in cfg.warnings if "not UTF-8" in w]
    assert bool(enc) is warned
    if warned:
        assert str(lab["cfg_path"]) in enc[0]
        assert "byte 0x97 on line 1" in enc[0]
        assert "Windows-1252" in enc[0] and "Save as" in enc[0]


def test_a_byte_cp1252_lacks_too_is_a_config_error_with_byte_and_line(lab):
    text = COMMENT.encode("cp1252") + yaml.safe_dump(lab["cfg_dict"]).encode() + b"# odd \x81 byte\n"
    lab["cfg_path"].write_bytes(text)
    line = text.count(b"\n")
    with pytest.raises(ConfigError) as e:
        load(lab["cfg_path"], check_paths=False)
    msg = str(e.value)
    assert str(lab["cfg_path"]) in msg
    assert "byte 0x97 on line 1" in msg and f"byte 0x81 on line {line}" in msg
    assert "UTF-8" in msg and "Save as" in msg


def test_a_broken_utf16_file_is_a_config_error(lab):
    lab["cfg_path"].write_bytes(b"\xff\xfe" + "paths: x\n".encode("utf-16-le") + b"\x00")  # odd byte count
    with pytest.raises(ConfigError, match="UTF-16"):
        load(lab["cfg_path"], check_paths=False)


def test_read_yaml_text_logs_when_no_list_is_given(tmp_path, caplog):
    p = tmp_path / "learned_aliases.yaml"
    p.write_bytes("Isaac: [Grün]\n".encode("cp1252"))
    with caplog.at_level(logging.WARNING, logger="ionomos.config"):
        assert read_yaml_text(p) == "Isaac: [Grün]\n"
    assert str(p) in caplog.text and "byte 0xFC on line 1" in caplog.text


def test_read_yaml_text_raises_the_callers_error(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_bytes(b"a: \x9d\n")
    with pytest.raises(OverridesError, match="0x9D on line 1"):
        read_yaml_text(p, error=OverridesError)


def test_live_config_reloads_an_ansi_save_and_survives_an_unreadable_one(lab, caplog):
    live = LiveConfig(lab["cfg"], check_paths=False)
    _write(lab["cfg_path"], _config_text(lab, aliases={"Isaac": ["IJ", "Grün"]}), "cp1252")
    with caplog.at_level(logging.WARNING, logger="ionomos.config"):
        assert live.get().user_aliases["Isaac"] == ["IJ", "Grün"]
    assert "is not UTF-8 (byte 0x97 on line 1)" in caplog.text  # the running watcher says so in its log
    good = live.get()
    lab["cfg_path"].write_bytes(_config_text(lab).encode("cp1252") + b"# \x81\n")  # 0x81: not UTF-8, not cp1252
    st = lab["cfg_path"].stat()
    os.utime(lab["cfg_path"], ns=(st.st_atime_ns, st.st_mtime_ns + 4_000_000_000))
    assert live.get() is good  # kept the last good config instead of the watcher dying on UnicodeDecodeError


def test_configio_reads_ansi_and_bom(lab):
    for encoding in ("cp1252", "utf-8-sig"):
        _write(lab["cfg_path"], _config_text(lab, aliases={"Isaac": ["Grün"]}), encoding)
        d = configio.read_config(lab["cfg_path"])
        assert d["users"]["aliases"]["Isaac"] == ["Grün"]
        assert "paths" in d  # the BOM didn't stick to the first key


def test_configio_unreadable_is_a_config_error(lab):
    lab["cfg_path"].write_bytes(b"# \x8d\n")
    with pytest.raises(ConfigError, match="0x8D on line 1"):
        configio.read_config(lab["cfg_path"])


def test_secrets_of_an_ansi_config_are_still_found_for_redaction(lab):
    d = dict(lab["cfg_dict"], notify={"teams": "https://example.invalid/hook/s3cret"})
    _write(lab["cfg_path"], COMMENT + yaml.safe_dump(d), "cp1252")
    assert notify.file_secrets(lab["cfg_path"]) == ["https://example.invalid/hook/s3cret"]


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8-sig"])
def test_experiment_yaml_in_ansi_or_with_bom(tmp_path, encoding):
    text = "# Kontrolle — DMSO\nmethod: DIA\nnotes: Grün lab\n"
    (tmp_path / EXPERIMENT_YAML).write_bytes(text.encode(encoding))
    ov = load_overrides(tmp_path)
    assert (ov.method, ov.notes) == ("DIA", "Grün lab")
    merged = overrides_text(tmp_path, parse_overrides({"user": "Chris"}))
    assert "notes: Grün lab" in merged and "user: Chris" in merged


def test_unreadable_experiment_yaml_is_an_overrides_error_and_is_never_replaced(tmp_path):
    p = tmp_path / EXPERIMENT_YAML
    p.write_bytes(b"notes: \x81\n")
    with pytest.raises(OverridesError, match="0x81 on line 1"):
        load_overrides(tmp_path)
    with pytest.raises(OverridesError):  # merging over it would drop what it says
        overrides_text(tmp_path, parse_overrides({"user": "Chris"}))
    assert p.read_bytes() == b"notes: \x81\n"


def test_a_support_bundle_still_scrubs_the_names_of_an_ansi_config(tmp_path, monkeypatch):
    from ionomos import bundle, testbed

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    cfg_path = testbed.init(tmp_path / "bed")
    d = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    d.setdefault("users", {}).setdefault("aliases", {})["Isaac"] = ["IJ", "Grünwald"]
    _write(cfg_path, COMMENT + yaml.safe_dump(d, allow_unicode=True), "cp1252")
    anon = bundle.learn(bundle.collect(cfg_path))  # read the config as {} before D86: its names went unscrubbed
    assert [o for _w, o, _n in bundle.check_text(anon, "run by Grünwald\n", "pasted")] == ["Grünwald"]


def test_an_unreadable_learned_aliases_file_is_never_rewritten(lab):
    from ionomos.config import remember_alias

    learned = lab["cfg"].learned_aliases_file
    learned.write_bytes(b"Isaac: [IJ, \x81]\n")
    assert load(lab["cfg_path"], check_paths=False).user_aliases["Isaac"] == ["IJ", "IJD"]  # config still loads
    with pytest.raises(ConfigError, match="left as it is"):
        remember_alias(lab["cfg"], "Chris", "CS")
    assert learned.read_bytes() == b"Isaac: [IJ, \x81]\n"


def test_an_ansi_learned_aliases_file_is_read_and_kept(lab):
    from ionomos.config import remember_alias

    learned = lab["cfg"].learned_aliases_file
    learned.write_bytes("Isaac: [Grün]\n".encode("cp1252"))
    remember_alias(lab["cfg"], "Chris", "CS")
    assert yaml.safe_load(learned.read_text(encoding="utf-8")) == {"Isaac": ["Grün"], "Chris": ["CS"]}
