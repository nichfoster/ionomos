"""Every key of config.yaml survives the app's Save: configio.dump_config -> read_config gives back what went in.

The app (and `ionomos init`, `library adopt`) rewrite config.yaml from a template (configio.dump_config). A key the
template has no line for used to be dropped on the next Save: six analysis settings the loader takes
(comparisons, exclude_samples, sample_conditions, sdrf_factor, ratio_centre, protein_correction) and every typo
the loader would have named (D67 item 4). These tests walk configio.defaults(), every analysis setting there is
(downstream.analysis.Settings, so a new one is covered the day it is added) and the keys only a person writes."""
from __future__ import annotations

import copy
from dataclasses import fields, replace

import pytest
import yaml

from ionomos import configio
from ionomos.config import load
from ionomos.downstream.analysis import Settings, settings_from

# strings YAML would read back as something else if they were written bare
TRICKY = ["yes", "No", "~", "null", "1.0", "0x1F", "2026-10-15", "a: b", "#not a comment", "it's", 'say "hi"',
          " leading space", "trailing space ", "é ✓ 日本", "C:\\Users\\lab\\x", "{braces}", "[brackets]", "-", "*star",
          "&anchor", "!tag", "%percent", "@at", "`tick`", "line one\nline two", "tab\there", "x" * 150]


def _diff(a, b, where="") -> list[str]:
    """Every leaf that differs (value or type) between two nested dicts, as 'section.key: before -> after'."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in dict.fromkeys([*a, *b]):
            if k not in b:
                out.append(f"{where}{k}: lost ({a[k]!r})")
            elif k not in a:
                out.append(f"{where}{k}: appeared ({b[k]!r})")
            else:
                out += _diff(a[k], b[k], f"{where}{k}.")
    elif a != b or type(a) is not type(b):
        out.append(f"{where.rstrip('.')}: {a!r} -> {b!r}")
    return out


def _round_trip(tmp_path, d: dict, name="config.yaml") -> dict:
    p = tmp_path / name
    configio.write_config(p, d, backup=False)
    return configio.read_config(p)


def _mutated(v, where: str, i: int):
    """A different value of the same type for a default leaf."""
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return v + 3
    if isinstance(v, float):
        return v + 0.25
    if isinstance(v, str):
        return TRICKY[i % len(TRICKY)] + f" {where}"
    if isinstance(v, list):
        return [*v, TRICKY[i % len(TRICKY)]] if v else ["first", TRICKY[i % len(TRICKY)]]
    raise AssertionError(f"no mutation for {where}: {v!r}")


def _mutate_all(d: dict, where="", counter=None) -> None:
    counter = counter if counter is not None else [0]
    for k, v in d.items():
        if isinstance(v, dict) and v:
            _mutate_all(v, f"{where}{k}.", counter)
        elif not isinstance(v, dict):
            counter[0] += 1
            d[k] = _mutated(v, f"{where}{k}", counter[0])


@pytest.mark.parametrize(("root", "users"), [
    ("C:/Fragpipe_Auto", "C:/Fragpipe_General"),
    ("D:\\Lab\\Auto\\", "\\\\server\\share\\General"),  # backslashes, a trailing one, UNC
    ("/home/lab/ionomos", "/home/lab/Fragpipe_General"),
    ("C:/Données/Auto", "C:/Labor_Ä/Gen"),  # not ASCII
])
def test_the_defaults_come_back_exactly(tmp_path, root, users):
    d = configio.defaults(root, users)
    assert _diff(d, _round_trip(tmp_path, d)) == []


def test_every_default_key_changed_comes_back_exactly(tmp_path):
    """Each leaf of defaults() set to something else of its type (strings YAML would misread, lists grown) at once."""
    d = configio.defaults("C:/A", "C:/G")
    _mutate_all(d)
    back = _round_trip(tmp_path, d)
    assert _diff(d, back) == []
    # and written a second time, nothing moves (the app saves again and again)
    assert _diff(back, _round_trip(tmp_path, back, "again.yaml")) == []


def _probe(name: str, default):
    if isinstance(default, bool):
        return not default
    if isinstance(default, int):
        return default + 1
    if isinstance(default, float):
        return round(default + 0.125, 3)
    if isinstance(default, str):
        return f"probe {name}"
    if isinstance(default, dict):
        return {"DrugA": f"probe {name}", "DMSO": "x"}
    if isinstance(default, (list, tuple)):
        return [f"probe {name}", "second"]
    return f"probe {name}"  # None (control)


@pytest.mark.parametrize("name", [f.name for f in fields(Settings) if f.name != "export"])
def test_every_analysis_setting_survives_a_save(tmp_path, name):
    """A lab-wide analysis setting set by hand in config.yaml is still there after the app saves (was: six were
    dropped, and the analyses silently went back to the default)."""
    d = configio.defaults("C:/A", "C:/G")
    d["analysis"][name] = _probe(name, getattr(Settings(), name))
    back = _round_trip(tmp_path, d)
    assert _diff(d["analysis"], back["analysis"]) == []


def test_the_keys_only_a_person_writes_survive_a_save(tmp_path):
    d = configio.defaults("C:/A", "C:/G")
    d["users"]["aliases"] = {"Isaac": ["IJ", "IJD"], "No": ["NO", "yes"], "Émile": ["É"]}
    d["users"]["default"] = "_unsorted"
    d["naming"]["condition_codes"] = {"V": "Vehicle", "Y": "Yes", "N": "No"}
    d["naming"]["date_formats"] = ["YYYYMMDD", "YYMMDD"]
    d["naming"]["methods"] = {"DIA": "{sample}_{rep}", "TMT": {"like": "TMT", "files": "{sample}_F{fraction}",
                                                               "condition_codes": False}}
    d["analysis"]["roles"] = {"DrugA": "compound", "DrugA_Comp": "competition of DrugA"}
    d["analysis"]["times"] = {"T0": 0, "T6": 6.5}
    d["analysis"]["doses"] = {"D1": "1 uM", "D10": 10}
    d["analysis"]["sdrf"] = {"instrument": "Orbitrap Eclipse", "organism": "Homo sapiens"}
    d["analysis"]["export"].update({"width": 120, "height": 80, "unit": "mm", "up": "#d55e00", "title": False})
    d["qc_trend"]["max_file_mb"] = 50
    d["library"]["roots"] = ["D:/", "//server/share/fasta"]
    d["notify"]["enabled"] = True
    d["notify"]["webhook"] = {"url": "https://example.org/hook?a=1&b=2", "url_env": "", "headers": {"X-A": "1"}}
    d["notify"]["email"].update({"host": "smtp.example.org", "port": 465, "security": "ssl", "username": "lab",
                                 "password": "p@ss: #word", "to": ["a@example.org", "b@example.org"]})
    d["assistant"] = {"enabled": True, "base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b", "stream": True}
    d["methods"]["DIA"].update({"engine": "diann", "aliases": ["dia", "yes"]})
    d["methods"]["Sage_DDA"] = {"aliases": ["sage"], "workflow": "", "fasta": "f.fas", "data_type": "DDA",
                                "postprocess": [], "engine": "sage", "extra_knob": {"a": [1, 2]}}
    back = _round_trip(tmp_path, d)
    assert _diff(d, back) == []


def test_a_typo_is_kept_where_the_loader_can_name_it(tmp_path):
    """D67 item 4: the app never drops what it doesn't know, a typo included (the loader names it; #137)."""
    d = configio.defaults("C:/A", "C:/G")
    d["watcher"]["poll_secnds"] = 3
    d["fragpipe"]["thread"] = 4
    d["paths"]["fasta_dri"] = "C:/x"
    d["gui"]["enable"] = False
    d["users"]["alias"] = {"Isaac": ["IJ"]}
    d["qc_trend"]["mach"] = ["hela"]
    d["naming"]["date_format"] = ["YYMMDD"]
    d["analysis"]["normalise"] = "median"
    d["notifyy"] = {"enabled": True}
    back = _round_trip(tmp_path, d)
    assert _diff(d, back) == []


def test_what_the_loader_reads_is_the_same_after_a_save(lab, tmp_path):
    """The validated Config of a hand-written config.yaml equals that of the same settings saved by the app."""
    raw = copy.deepcopy(lab["cfg_dict"])
    raw["analysis"] = {"ratio_centre": "median", "exclude_samples": ["S3"], "log2fc": 0.8, "normalize": "median"}
    raw["naming"] = {"condition_codes": {"V": "Vehicle"}, "methods": {"DIA": "{sample}_{rep}"}}
    raw["gui"] = {"enabled": False, "timeout_minutes": 5}
    hand = tmp_path / "hand.yaml"
    hand.write_text(yaml.safe_dump(raw), encoding="utf-8")
    saved = configio.write_config(tmp_path / "saved.yaml", configio.read_config(hand), backup=False)
    a, b = load(hand), load(saved)
    for f in fields(a):
        if f.name not in ("config_path", "warnings", "learned_aliases_file", "unknown_keys", "analysis"):
            assert getattr(a, f.name) == getattr(b, f.name), f.name  # methods: was isoDTB's aliases narrowed
    # the saved file spells the defaults out, the figure style's too (the charts' own defaults)
    assert replace(settings_from(a.analysis), export={}) == replace(settings_from(b.analysis), export={})


def test_a_method_without_aliases_keeps_the_built_in_ones_after_a_save(tmp_path):
    """isoDTB with no aliases: means isodtb, iso-dtb and iso_dtb; the app's Save wrote [isodtb], and a folder
    named ..._iso-dtb_... was no longer recognised after it."""
    p = tmp_path / "c.yaml"
    p.write_text("methods:\n  isoDTB: {workflow: i.workflow, fasta: f.fas, data_type: DDA}\n", encoding="utf-8")
    configio.write_config(p, configio.read_config(p))
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["methods"]["isoDTB"]["aliases"] == \
        ["isodtb", "iso-dtb", "iso_dtb"]
