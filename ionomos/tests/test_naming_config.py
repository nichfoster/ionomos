"""Configurable naming (config.yaml `naming:`, D37): templates and regexes per method, date formats,
condition codes, validation, and the "test your names" check.

Three parts:
  1. the defaults: the built-in templates compile to exactly the old regexes, and real names from
     reference/pc-inventory read the same with no naming block, or with the defaults written out;
  2. a second, invented lab configured purely in config.yaml;
  3. the errors a lab gets for a bad template, regex, alias list or date format.
"""
import random
import re
from datetime import date

import pytest
import yaml

from ionomos import configio
from ionomos.cli import main
from ionomos.config import ConfigError, load
from ionomos.intake import IntakeError, Kind, plan
from ionomos.namecheck import (
    FileReading,
    FolderReading,
    check_names,
    format_readings,
    names_from_text,
    report_from_data,
)
from ionomos.naming import (
    _FRAC,
    _REP,
    _SEP,
    DEFAULT_DATE_FORMATS,
    DEFAULT_FILE_RULES,
    DEFAULT_FILE_TEMPLATES,
    NamingError,
    compile_template,
    file_rule,
    find_date,
    group_raws,
    parse_raw_name,
)
from tests.conftest import make_drop

# The regexes naming.py used before naming became configurable, copied verbatim.
OLD_TAIL = {
    "isoDTB": rf"^(?P<sample>.+?)(?:{_SEP}{_REP}(?P<rep>\d{{1,3}}))(?:{_SEP}{_FRAC}(?P<frac>\d{{1,3}}))?$",
    "TMT": rf"^(?P<sample>.+?)(?:{_SEP}[Tt][Mm][Tt])?(?:{_SEP}{_FRAC}(?P<frac>\d{{1,3}}))?$",
    "DIA": rf"^(?P<sample>.+?)(?:{_SEP}{_REP}(?P<rep>\d{{1,3}}))?$",
}


def _write(path, d):
    path.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    return path


# ============================================================== 1. defaults ==


@pytest.mark.parametrize("method", sorted(OLD_TAIL))
def test_default_templates_compile_to_the_old_regexes(method):
    assert compile_template(DEFAULT_FILE_TEMPLATES[method]).pattern == OLD_TAIL[method]
    assert DEFAULT_FILE_RULES[method].regex.pattern == OLD_TAIL[method]
    assert DEFAULT_FILE_RULES[method].codes is (method == "DIA")  # only DIA reads X_D1 short codes


def test_no_naming_block_means_the_built_in_rules(lab):
    cfg = lab["cfg"]
    assert {m: r.regex.pattern for m, r in cfg.file_rules.items()} == OLD_TAIL
    assert cfg.date_formats == DEFAULT_DATE_FORMATS == ("YYYYMMDD", "MMDDYYYY", "MMDDYY")


def _defaults_written_out(lab):
    d = dict(lab["cfg_dict"], naming={"date_formats": list(DEFAULT_DATE_FORMATS),
                                      "methods": dict(DEFAULT_FILE_TEMPLATES)})
    return load(_write(lab["cfg_path"], d))


def test_defaults_written_out_in_config_are_the_same_regexes(lab):
    cfg = _defaults_written_out(lab)
    assert {m: r.regex.pattern for m, r in cfg.file_rules.items()} == OLD_TAIL
    assert cfg.file_rules["DIA"].codes and not cfg.file_rules["isoDTB"].codes


# Real raw names from reference/pc-inventory/2026-09-15 (and KC_DIA_D1, the drop behind D34).
REAL_RAWS = [
    ("CG_isoDTB_ELK_R1_F1.raw", "isoDTB", ("CG_isoDTB_ELK", 1, 1)),
    ("CMZ_E6_isoDTB_ABPP_3_7.raw", "isoDTB", ("CMZ_E6_isoDTB_ABPP", 3, 7)),
    ("CMZ_WX48A_SiHa_isoDTB_2_4.raw", "isoDTB", ("CMZ_WX48A_SiHa_isoDTB", 2, 4)),
    ("IJ_isoDTB_D05_25uM_3h_22Rv1WT_R2_F6.raw", "isoDTB", ("IJ_isoDTB_D05_25uM_3h_22Rv1WT", 2, 6)),
    ("EST_EST1140_isoDTB_20uM_2H_22Rv1_2_5_20240206114519.raw", "isoDTB",  # Xcalibur stamp ignored
     ("EST_EST1140_isoDTB_20uM_2H_22Rv1", 2, 5)),
    ("QS_isoDTB_salivabactin_WenjunLab_2_F3.raw", "isoDTB", ("QS_isoDTB_salivabactin_WenjunLab", 2, 3)),
    ("MA_MA17-isoDTB_T47D-lysate.raw", "isoDTB", "isoDTB files must end in"),
    ("IJ_isoDTB_IJ510312_25mM_22Rv1_4h_3_6_dynex30.raw", "isoDTB", "isoDTB files must end in"),
    ("CS_isoDTB_ELK_3_1-7_DIA.raw", "DIA", ("CS_isoDTB_ELK_3_1-7_DIA", 1, None)),
    ("CS_isoDTB_ELK_3_1-7_DIA_HCD33.raw", "DIA", ("CS_isoDTB_ELK_3_1-7_DIA_HCD33", 1, None)),  # not a code
    ("KC_DIA_D1.raw", "DIA", ("KC_DIA_DMSO", 1, None)),
    ("KL6159A_TMT_F3.raw", "TMT", ("KL6159A", 1, 3)),
]


@pytest.mark.parametrize(("fname", "method", "want"), REAL_RAWS)
def test_real_names_read_the_same_with_and_without_the_block(lab, fname, method, want):
    explicit = _defaults_written_out(lab)
    for rules, codes in ((None, None), (lab["cfg"].file_rules, lab["cfg"].condition_codes),
                         (explicit.file_rules, explicit.condition_codes)):
        if isinstance(want, str):
            with pytest.raises(NamingError, match=want):
                parse_raw_name(fname, method, codes, rules)
        else:
            r = parse_raw_name(fname, method, codes, rules)
            assert (r.sample, r.rep, r.fraction) == want


# Real folder names (reference/pc-inventory) through `names test` with the default config.
@pytest.mark.parametrize(("folder", "user", "method", "d"), [
    ("20260804-isoDTB_IJ607061", "Isaac", "isoDTB", date(2026, 8, 4)),
    ("20260914-FLAGPull_IJD05_FLAGAR_DIA", "Isaac", "DIA", date(2026, 9, 14)),
    ("20260126_Aman_TMT_KL6159A-159B_9plex", "Aman", "TMT", date(2026, 1, 26)),
    ("EJQ_2 isoDTB 10uM 2h", "EJQ", "isoDTB", None),
])
def test_names_test_default_folders(lab, folder, user, method, d):
    (r,) = check_names([folder], lab["cfg"])
    assert isinstance(r, FolderReading) and r.ok
    assert (r.user, r.method, r.date) == (user, method, d)
    assert r.rule == DEFAULT_FILE_TEMPLATES[method]


def test_names_test_default_folder_with_its_files(lab):
    raws = [f"CG_isoDTB_ELK_R{r}_F{f}.raw" for r in (1, 2, 3) for f in range(1, 8)]
    (r,) = check_names(["20260902_EJQ_isoDTB_ELK", *raws], lab["cfg"])
    assert r.ok and len(r.files) == 21 and all(f.method == "isoDTB" for f in r.files)
    assert r.layout == ["CG_isoDTB_ELK: 3 replicate(s) (1, 2, 3), 7 fraction(s) each"]
    # a missing fraction is caught by the same check intake runs
    (bad,) = check_names(["20260902_EJQ_isoDTB_ELK", *raws[:-1]], lab["cfg"])
    assert not bad.ok and "incomplete copy" in bad.layout_error


# ========================================================= 2. another lab ==

# An invented lab: double-underscore folders with ISO dates or day-first dates, people by login,
# DIA files named <condition>_rep<N>, a label-free DDA method of its own, a DIA variant with its own
# condition codes, and a regex for a sample-sheet-style method. All of it is config.
LAB2_NAMING = {
    "date_formats": ["YYYYMMDD", "DDMMYYYY", "DDMMYY"],
    "condition_codes": {"V": "Vehicle", "T": "Treated"},
    "methods": {
        "DIA": "{condition}_rep{rep}",                       # shorthand for files:
        "LFQ": {"files": "{sample}_R{rep}_F{fraction}"},     # a new method
        "SWATH": {"like": "DIA"},                            # DIA's rule, under another name
        "PLATE": {"pattern": r"(?P<sample>[A-H]\d{2})-(?P<rep>\d+)(?:_(?P<fraction>\d+))?"},
    },
}


@pytest.fixture
def lab2(tmp_path):
    root = tmp_path / "lab2"
    for d in ("inbox", "users/jdoe", "users/asmith", "logs", "wf", "fa"):
        (root / d).mkdir(parents=True)
    d = {
        "paths": {"inbox": str(root / "inbox"), "users_root": str(root / "users"),
                  "fragpipe_exe": str(root / "fp"), "workflow_dir": str(root / "wf"),
                  "fasta_dir": str(root / "fa"), "database": str(root / "ionomos.db"),
                  "log_dir": str(root / "logs")},
        "methods": {
            "DIA": {"workflow": "DIA.workflow", "fasta": "m.fas", "data_type": "DIA", "aliases": ["dia"]},
            "LFQ": {"workflow": "LFQ.workflow", "fasta": "m.fas", "data_type": "DDA", "aliases": ["lfq", "dda"]},
            "SWATH": {"workflow": "SWATH.workflow", "fasta": "m.fas", "data_type": "DIA", "aliases": ["swath"]},
            "PLATE": {"workflow": "PLATE.workflow", "fasta": "m.fas", "data_type": "DDA", "aliases": ["plate"]},
        },
        "users": {"aliases": {"jdoe": ["JD"]}},
        "naming": LAB2_NAMING,
    }
    path = _write(root / "config.yaml", d)
    return {"root": root, "inbox": root / "inbox", "cfg_path": path, "cfg_dict": d, "cfg": load(path)}


@pytest.mark.parametrize(("fname", "method", "want"), [
    ("WT-a_rep1.raw", "DIA", ("WT-a", 1, None)),
    ("KO-b_rep2.raw", "DIA", ("KO-b", 2, None)),
    ("KO-b_Rep3.raw", "DIA", ("KO-b", 3, None)),                     # letters match either case
    ("WT-a_rep1_20260930101010.raw", "DIA", ("WT-a", 1, None)),      # Xcalibur stamp still ignored
    ("KO-b_2.raw", "DIA", "DIA files must look like {condition}_rep{rep}.raw"),
    ("WT-a_rep0.raw", "DIA", "replicate 0 is out of range"),         # D30: numbers must be plausible
    ("WT-a_rep1000.raw", "DIA", "must look like"),                   # 4 digits are never a number
    ("liver_R2_F11.raw", "LFQ", ("liver", 2, 11)),
    ("liver_R2.raw", "LFQ", "LFQ files must look like {sample}_R{rep}_F{fraction}.raw"),
    ("liver_V1.raw", "SWATH", ("liver_Vehicle", 1, None)),           # the lab's codes replace D/C
    ("liver_T3.raw", "SWATH", ("liver_Treated", 3, None)),
    ("liver_D1.raw", "SWATH", ("liver_D", 1, None)),                 # D isn't DMSO in this lab
    ("liver_2.raw", "SWATH", ("liver", 2, None)),
    ("B07-2_3.raw", "PLATE", ("B07", 2, 3)),
    ("B07-2.raw", "PLATE", ("B07", 2, None)),
    ("B07-1000.raw", "PLATE", "replicate 1000 is out of range"),     # a regex's numbers are checked too
    ("Z99-1.raw", "PLATE", "must match the pattern"),
    # the built-in isoDTB rule is still there for a lab that keeps the method
    ("X_1_1.raw", "isoDTB", ("X", 1, 1)),
])
def test_lab2_file_names(lab2, fname, method, want):
    cfg = lab2["cfg"]
    if isinstance(want, str):
        with pytest.raises(NamingError, match=re.escape(want)):
            parse_raw_name(fname, method, cfg.condition_codes, cfg.file_rules)
        return
    r = parse_raw_name(fname, method, cfg.condition_codes, cfg.file_rules)
    assert (r.sample, r.rep, r.fraction) == want


@pytest.mark.parametrize(("folder", "user", "method", "d"), [
    ("2026-09-30__jdoe__DIA__liver", "jdoe", "DIA", date(2026, 9, 30)),
    ("30.09.2026_asmith_LFQ_heart", "asmith", "LFQ", date(2026, 9, 30)),   # day first
    ("JD_SWATH_kidney_300926", "jdoe", "SWATH", date(2026, 9, 30)),          # DDMMYY, alias JD
    ("asmith_plate_run", "asmith", "PLATE", None),
])
def test_lab2_folder_names(lab2, folder, user, method, d):
    (r,) = check_names([folder], lab2["cfg"])
    assert r.ok, format_readings([r])
    assert (r.user, r.method, r.date) == (user, method, d)


def test_day_first_dates_need_the_config(lab):
    # the lab PC's default reads month first, so 30.09.2026 is no date at all there
    assert find_date("30.09.2026_EJQ_DIA") is None
    assert find_date("30.09.2026_EJQ_DIA", formats=("DDMMYYYY",)) == date(2026, 9, 30)
    # order decides between ambiguous forms, like the built-in list does
    assert find_date("03042026", formats=("MMDDYYYY", "DDMMYYYY")) == date(2026, 3, 4)
    assert find_date("03042026", formats=("DDMMYYYY", "MMDDYYYY")) == date(2026, 4, 3)
    assert find_date("260930", formats=("YYMMDD",), today=date(2026, 9, 30)) == date(2026, 9, 30)
    assert find_date("260930", formats=("YYMMDD",), today=date(2090, 1, 1)) is None  # D30 window
    assert find_date("20260930", formats=()) is None  # no formats: the drop date is used


def test_lab2_drop_plans_end_to_end(lab2):
    raws = ["WT-a_rep1.raw", "WT-a_rep2.raw", "KO-b_rep1.raw", "KO-b_rep2.raw"]
    folder = make_drop(lab2["inbox"], "2026-09-30__jdoe__DIA__liver", raws)
    p = plan(folder, lab2["cfg"])
    assert (p.folder.user, p.folder.method, p.folder.date) == ("jdoe", "DIA", date(2026, 9, 30))
    assert p.folder.safe == "2026-09-30_jdoe_DIA_liver"
    assert sorted((m.experiment, m.bioreplicate, m.data_type) for m in p.manifest) == [
        ("KO-b", 1, "DIA"), ("KO-b", 2, "DIA"), ("WT-a", 1, "DIA"), ("WT-a", 2, "DIA")]
    assert folder.is_dir()  # plan() touches nothing


def test_lab2_bad_file_is_a_fixable_raws_problem(lab2):
    folder = make_drop(lab2["inbox"], "2026-09-30__jdoe__DIA__liver", ["WT-a_rep1.raw", "WT-a_2.raw"])
    with pytest.raises(IntakeError) as exc:
        plan(folder, lab2["cfg"])
    assert exc.value.kind == Kind.RAWS and "must look like {condition}_rep{rep}.raw" in str(exc.value)


def test_lab2_uneven_fractions_are_still_caught(lab2):
    files = [f"liver_R{r}_F{f}.raw" for r in (1, 2) for f in (1, 2, 3)][:-1]
    cfg = lab2["cfg"]
    with pytest.raises(NamingError, match="incomplete copy"):
        group_raws(files, "LFQ", codes=cfg.condition_codes, rules=cfg.file_rules)


def test_names_test_lab2_folder_then_files(lab2):
    names = ["2026-09-30__jdoe__DIA__liver", "WT-a_rep1.raw", "KO-b_rep2.raw", "KO-b_2.raw"]
    (r,) = check_names(names, lab2["cfg"])
    assert not r.ok
    assert [(f.sample, f.rep) for f in r.files[:2]] == [("WT-a", 1), ("KO-b", 2)]
    assert "must look like" in r.files[2].error
    text = format_readings([r])
    assert text.startswith("REJECT  folder 2026-09-30__jdoe__DIA__liver")
    assert "file rule  : {condition}_rep{rep}" in text
    assert "WT-a_rep1.raw   sample WT-a · replicate 1 · no fraction   [DIA]" in text
    assert "KO-b_2.raw   ✗" in text


def test_names_test_loose_file_without_a_method(lab2):
    (r,) = check_names(["WT-a_rep1.raw"], lab2["cfg"])
    assert isinstance(r, FileReading) and r.ok and not r.method
    got = {a.method: (a.sample, a.rep) for a in r.alternatives if a.ok}
    assert got["DIA"] == ("WT-a", 1)
    assert "PLATE" not in got
    (m,) = check_names(["WT-a_rep1.raw"], lab2["cfg"], method="DIA")
    assert (m.method, m.sample, m.rep) == ("DIA", "WT-a", 1)
    (kw,) = check_names(["liver_lfq_R1_F2.raw"], lab2["cfg"])  # a keyword in the file name picks the method
    assert (kw.method, kw.sample, kw.rep, kw.fraction) == ("LFQ", "liver_lfq", 1, 2)


def test_names_test_reads_a_folder_on_disk(lab2):
    folder = make_drop(lab2["inbox"], "2026-09-30__jdoe__DIA__liver", ["WT-a_rep1.raw", "WT-a_rep2.raw"],
                       raw_sub=True)
    (r,) = check_names([str(folder)], lab2["cfg"])
    assert r.ok and r.on_disk and [f.name for f in r.files] == ["WT-a_rep1.raw", "WT-a_rep2.raw"]
    assert r.layout == ["WT-a: 2 replicate(s) (1, 2), single-shot"]


def test_cli_names_test(lab2, capsys):
    cfg = str(lab2["cfg_path"])
    assert main(["--config", cfg, "names", "test", "2026-09-30__jdoe__DIA__liver", "WT-a_rep1.raw"]) == 0
    out = capsys.readouterr().out
    assert "OK      folder 2026-09-30__jdoe__DIA__liver" in out and "user       : jdoe" in out
    assert "date       : 2026-09-30" in out
    assert main(["--config", cfg, "names", "test", "nobody_DIA", "X.raw"]) == 1
    out = capsys.readouterr().out
    assert "✗ no known user" in out and "X.raw   ✗" in out
    assert main(["--config", cfg, "names", "test", "--method", "swath", "liver_V2.raw"]) == 0
    assert "sample liver_Vehicle · replicate 2" in capsys.readouterr().out
    assert main(["--config", cfg, "names", "test", "--method", "nope", "x.raw"]) == 2


def test_cli_names_test_default_config(lab, capsys):
    assert main(["--config", str(lab["cfg_path"]), "names", "test", "20260902_EJQ_isoDTB_x",
                 "CG_isoDTB_ELK_R1_F1.raw", "CG_isoDTB_ELK_R2_F1.raw"]) == 0
    out = capsys.readouterr().out
    assert "file rule  : {sample}_{rep}[_{fraction}]" in out
    assert "CG_isoDTB_ELK: 2 replicate(s) (1, 2), 1 fraction(s) each" in out


def test_app_button_reads_the_settings_being_edited(lab2):
    # the Methods tab's "Test names…" window calls this with the app's unsaved settings
    data = configio.read_config(lab2["cfg_path"])
    data["naming"]["methods"]["DIA"] = "{condition}-{rep}"  # edited, not saved
    text = report_from_data(data, "2026-09-30__jdoe__DIA__liver\nWT-a-3.raw\n\n", lab2["cfg_path"])
    assert "WT-a-3.raw   sample WT-a · replicate 3" in text
    assert not list(lab2["root"].glob(".config.names.yaml"))  # the probe file is gone
    data["naming"]["methods"]["DIA"] = "{rep}"
    assert report_from_data(data, "x", lab2["cfg_path"]).startswith("config problem: naming.methods.DIA")
    assert "one per line" in report_from_data(configio.read_config(lab2["cfg_path"]), "\n", lab2["cfg_path"])
    assert names_from_text(" Taylor Elements TMT run 3 \n\nA_1.raw\n") == ["Taylor Elements TMT run 3", "A_1.raw"]


def test_app_save_keeps_the_naming_block(lab2):
    # the app rewrites config.yaml from its own template; a lab's naming rules must survive a Save
    data = configio.read_config(lab2["cfg_path"])
    configio.write_config(lab2["cfg_path"], data, backup=False)
    back = load(lab2["cfg_path"])
    assert back.date_formats == ("YYYYMMDD", "DDMMYYYY", "DDMMYY")
    assert {m: r.source for m, r in back.file_rules.items()} == {
        m: r.source for m, r in lab2["cfg"].file_rules.items()}
    assert back.condition_codes == {"V": "Vehicle", "T": "Treated"}


# ================================================================ 3. errors ==


@pytest.mark.parametrize(("rule", "match"), [
    ("{sample}_{rep", "unmatched '{'"),
    ("{rep}_{fraction}", "needs {sample} once"),
    ("[{sample}]_{rep}", "needs {sample} once, outside [ ]"),
    ("{sample}_{replicat}", "unknown field {replicat}"),
    ("{sample}_{rep}{fraction}", "between two numbers"),
    ("{sample}_{rep}_{biorep}", "has the rep field twice"),
    ("{sample}[_[R]{rep}]", "can't be nested"),
    ("{sample}_{rep}]", "']' without a matching '['"),
    ("{sample}[_{rep}", "'[' without a matching ']'"),
    ("{sample}[]", "empty optional part"),
    ("{sample} {rep}", "' ' can't be in a file name"),
    ("", "can't be empty"),
    ({"pattern": "(?P<sample>.+"}, "not a valid regular expression"),
    ({"pattern": r"(?P<name>.+)_(?P<rep>\d+)"}, "unknown group (?P<name>"),
    ({"pattern": r"(.+)_(?P<rep>\d+)"}, "needs a (?P<sample>...) group"),
    ({"pattern": r"(?P<sample>.+)_(?P<rep>\d+)_(?P<replicate>\d+)"}, "two groups for rep"),
    ({"files": "{sample}_{rep}", "pattern": "(?P<sample>.+)"}, "not both"),
    ({"like": "SWATH"}, "is not a built-in method"),
    ({"like": "DIA", "condition_codes": "yes"}, "condition_codes must be true or false"),
    ({"files": "{sample}", "shape": "x"}, "naming.methods.DIA.shape: unknown setting"),
    ({"files": {"sample": None}}, "must be a text (quote it"),  # YAML read an unquoted {sample} as a mapping
    ({}, "give files: (a template)"),
])
def test_bad_rules_are_config_errors(lab, rule, match):
    d = dict(lab["cfg_dict"], naming={"methods": {"DIA": rule}})
    with pytest.raises(ConfigError, match=re.escape(match)) as exc:
        load(_write(lab["cfg_path"], d))
    assert str(exc.value).startswith("naming.methods.DIA")


@pytest.mark.parametrize(("naming", "methods_extra", "match"), [
    ({"methods": {"LFQ": "{sample}_{rep}"}}, {}, "naming.methods.LFQ: there is no methods.LFQ"),
    ({"methods": {"LFQ": {"condition_codes": True}}}, {"LFQ": {}}, "no file rule for 'LFQ'"),
    ({"date_formats": ["YYYYMMDD", "YYYYDDMM"]}, {}, "unknown date format 'YYYYDDMM'"),
    ({"date_formats": ["YYYYMMDD", "yyyymmdd"]}, {}, "YYYYMMDD is listed twice"),
    ({"date_formats": "YYYYMMDD"}, {}, "must be a list"),
    ({"dates": ["YYYYMMDD"]}, {}, "naming.dates: unknown setting"),
    ("DIA: x", {}, "'naming:' must be a mapping"),
    ({}, {"LFQ": {"aliases": ["DIA"]}}, "keyword 'DIA' is listed for both DIA and LFQ"),
    ({}, {"LFQ": {"aliases": ["lfq", " "]}}, "blank keyword"),
])
def test_bad_naming_settings_are_config_errors(lab, naming, methods_extra, match):
    methods = dict(lab["cfg_dict"]["methods"])
    for k, extra in methods_extra.items():
        methods[k] = {"workflow": f"{k}.workflow", "fasta": "h.fas", "data_type": "DDA", **extra}
    d = dict(lab["cfg_dict"], naming=naming, methods=methods)
    with pytest.raises(ConfigError, match=re.escape(match)):
        load(_write(lab["cfg_path"], d))


def test_a_method_without_a_rule_is_left_alone_at_load(lab):
    # as before: a method key naming.py doesn't know loads fine; its files are an "unknown method" with a hint
    methods = dict(lab["cfg_dict"]["methods"], LFQ={"workflow": "L.workflow", "fasta": "h.fas", "data_type": "DDA"})
    cfg = load(_write(lab["cfg_path"], dict(lab["cfg_dict"], methods=methods)))
    with pytest.raises(NamingError, match=r"unknown method 'LFQ'.*naming\.methods\.LFQ"):
        parse_raw_name("S_1.raw", "LFQ", cfg.condition_codes, cfg.file_rules)


@pytest.mark.parametrize(("fname", "want"), [
    # real inventory names with instrument settings after the numbers: {any} skips them
    ("IJ_isoDTB_IJ510312_25mM_22Rv1_4h_1_combined_HCD27.raw", ("IJ_isoDTB_IJ510312_25mM_22Rv1_4h", 1, None)),
    ("IJ_isoDTB_IJ510312_25mM_22Rv1_4h_3_6_dynex30.raw", ("IJ_isoDTB_IJ510312_25mM_22Rv1_4h", 3, 6)),
    ("CMZ_E6_isoDTB_ABPP_3_7.raw", ("CMZ_E6_isoDTB_ABPP", 3, 7)),  # names without the extra read as before
])
def test_any_field_skips_trailing_text(fname, want):
    rules = {"isoDTB": file_rule("isoDTB", files="{sample}_{rep}[_{fraction}][_{any}]")}
    r = parse_raw_name(fname, "isoDTB", None, rules)
    assert (r.sample, r.rep, r.fraction) == want


def test_pattern_group_that_captures_letters_is_reported():
    rule = file_rule("X", pattern=r"(?P<sample>.+)_(?P<rep>[a-z]+)")
    with pytest.raises(NamingError, match="replicate 'ab' is not a number"):
        parse_raw_name("S_ab.raw", "X", None, {"X": rule})
    assert parse_raw_name("S_1.raw", "X", None, {"X": file_rule("X", pattern=r"(?i)(?P<SAMPLE>s)_(?P<rep>\d)")}).sample == "S"


def test_random_templates_only_ever_raise_naming_error():
    rng = random.Random(7)
    parts = ["{sample}", "{rep}", "{fraction}", "{any}", "{x}", "[", "]", "_", "-", "R", "F", "rep", ".",
             " ", "{", "}", "(", "9", "TMT", "{condition}"]
    for _ in range(3000):
        t = "".join(rng.choices(parts, k=rng.randint(0, 8)))
        try:
            rx = compile_template(t)
        except NamingError:
            continue
        rx.match("X_1_2")  # a compiled template is a usable regex
