"""A method of the lab's own behaves as the method it is `like:` (or as what its `engine:` makes) through the
whole pipeline, not only for its names (D54): search inputs, TMT annotation, control detection, the analysis,
the SDRF and the doctor's messages. End to end against the testbed's fake FragPipe / DIA-NN / MaxQuant."""
import json
from pathlib import Path

import pytest
import yaml

from ionomos import downstream, fragpipe, postprocess, testbed
from ionomos.cli import main
from ionomos.config import load
from ionomos.intake import draft, intake
from ionomos.ledger import Ledger
from ionomos.namecheck import check_names, format_readings
from ionomos.naming import DEFAULT_FILE_RULES, analysis_method, file_rule, method_kind, parse_raw_name
from ionomos.resolve import Answer, has_control, summarize, validate
from ionomos.worker import Worker
from tests.conftest import make_drop

# custom key -> (the built-in it is like, the testbed sample with that method's files, its folder keyword)
CUSTOM = {
    "Cys": ("isoDTB", "iso_good", "cys"),
    "TMTpro": ("TMT", "tmt_good", "tmtpro"),
    "DIA_phospho": ("DIA", "dia_good", "phosdia"),
}


def _reload(cfg_path: Path, change) -> Path:
    d = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    change(d)
    cfg_path.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    return cfg_path


def _add_custom(d: dict) -> None:
    for key, (like, _sample, alias) in CUSTOM.items():
        d["methods"][key] = {**d["methods"][like], "aliases": [alias]}
    d["methods"]["Cys"]["isodtb_mod_mass"] = "561.3387"
    d["naming"] = {"methods": {key: {"like": like} for key, (like, _s, _a) in CUSTOM.items()}}


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE", raising=False)
    cfg_path = _reload(testbed.init(tmp_path / "bed"), _add_custom)
    cfg = load(cfg_path)
    return {"root": tmp_path / "bed", "cfg_path": cfg_path, "cfg": cfg, "ledger": Ledger(cfg.database)}


def _drop_as(bed, key: str) -> Path:
    """The built-in method's testbed sample, dropped under a folder name that says the custom method."""
    like, sample, alias = CUSTOM[key]
    spec = testbed.SAMPLES[sample]
    folder = make_drop(bed["cfg"].inbox, f"20260930_EJQ_{alias}_run", spec.get("raws") or spec["raw_sub"])
    if spec.get("yaml"):
        (folder / "experiment.yaml").write_text(yaml.safe_dump(spec["yaml"]), encoding="utf-8")
    return folder


def _run(bed, folder: Path) -> Path:
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    job = bed["ledger"].list()[-1]
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(job.id)
    assert job.status == "done", job.reason
    return Path(job.dest_dir)


def _analysis(dest: Path) -> dict:
    return json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------- the resolver --


@pytest.mark.parametrize(("method", "like", "engine", "kind", "reads"), [
    ("isoDTB", None, "", "isoDTB", "isoDTB"),          # built-in keys are themselves, as before
    ("TMT", None, "", "TMT", "TMT"),
    ("DIA", None, "fragpipe", "DIA", "DIA"),
    ("Cys", "isoDTB", "", "isoDTB", "isoDTB"),         # like: carries through
    ("TMTpro", "TMT", "", "TMT", "TMT"),
    ("DIA_phospho", "DIA", "", "DIA", "DIA"),
    ("LFQ", None, "", "LFQ", "LFQ"),                   # a method of the lab's own: its key (generic label-free)
    ("DIA", None, "diann", "DIA", "DIA"),
    ("SWATH", None, "diann", "DIA", "DIA"),            # the engine decides, whatever the key
    ("LFQ", "isoDTB", "maxquant", "LFQ", "MaxQuant"),  # ... and whatever like: lent it for its names
    ("LFQ", "isoDTB", "Sage", "LFQ", "Sage"),
])
def test_method_kind(method, like, engine, kind, reads):
    rules = dict(DEFAULT_FILE_RULES)
    if method not in rules:
        rules[method] = file_rule(method, like=like) if like else file_rule(method, files="{sample}_{rep}")
    assert method_kind(method, rules, engine) == kind
    assert analysis_method(method, rules, engine) == reads


def test_builtin_rules_have_no_like_and_unknown_methods_are_themselves():
    assert all(r.like == "" for r in DEFAULT_FILE_RULES.values())
    assert method_kind("isoDTB") == "isoDTB" and method_kind("Nope") == "Nope"
    assert file_rule("X", like="DIA", files="{sample}_rep{rep}").like == "DIA"   # like: with its own template
    assert file_rule("X", like="TMT", pattern=r"(?P<sample>.+)").like == "TMT"


# Real names from reference/pc-inventory: a method that is like: a built-in reads them as the built-in does.
@pytest.mark.parametrize(("fname", "like"), [
    ("CG_isoDTB_ELK_R1_F1.raw", "isoDTB"),
    ("CMZ_E6_isoDTB_ABPP_3_6.raw", "isoDTB"),
    ("EJQ_EJQ123_isoDTB_25uM_2h_2_4.raw", "isoDTB"),
    ("KC_22Rv1_isoDTB_HNRNPL_EN802_50uM_3h_1_3.raw", "isoDTB"),
    ("ZD_isoDTB_HT_ZD1186_50uM_16h_1_2.raw", "isoDTB"),
    ("CS_isoDTB_ELK_3_1-7_DIA.raw", "DIA"),
    ("CS_isoDTB_ELK_3_1-7_DIA_CV-35.raw", "DIA"),
    ("CS_isoDTB_ELK_3_1-7_DIA.raw", "TMT"),
    ("TH_THP1_TB10_isoDTB_1_4.raw", "TMT"),
])
def test_real_names_read_the_same_under_a_like_method(bed, fname, like):
    key = next(k for k, v in CUSTOM.items() if v[0] == like)
    cfg = bed["cfg"]
    assert cfg.kind(key) == like and cfg.kind(like) == like
    mine = parse_raw_name(fname, key, cfg.condition_codes, cfg.file_rules)
    theirs = parse_raw_name(fname, like, cfg.condition_codes, cfg.file_rules)
    assert (mine.sample, mine.rep, mine.fraction) == (theirs.sample, theirs.rep, theirs.fraction)


def test_names_test_says_what_a_custom_method_is_run_as(bed):
    text = format_readings(check_names(["20260930_EJQ_tmtpro_run", "KL6159A_TMT_F1.raw"], bed["cfg"]))
    assert "method     : TMTpro  (searched and analysed as TMT)" in text
    plain = format_readings(check_names(["20260930_EJQ_TMT_run", "KL6159A_TMT_F1.raw"], bed["cfg"]))
    assert "method     : TMT\n" in plain  # a built-in method reads as before


# ------------------------------------------------------- control detection --


@pytest.mark.parametrize("key", sorted(CUSTOM))
def test_review_reads_a_custom_method_as_its_kind(bed, key):
    like = CUSTOM[key][0]
    d = draft(_drop_as(bed, key), bed["cfg"], review=True)
    assert d.method == key and d.kind_of(key) == like and d.kind_of(like) == like
    assert has_control(d.kind_of(key)) == has_control(like) == (like == "DIA")
    assert summarize(d.files, d.kind_of(key), "DMSO") == summarize(d.files, like, "DMSO")
    # a control that isn't a condition only matters where there is a control
    a = Answer("EJQ", key, "", False, d.files, control="Nope")
    assert validate(a, d.known_methods, d.kinds) == validate(Answer("EJQ", like, "", False, d.files, control="Nope"),
                                                             d.known_methods, d.kinds)
    assert ("not one of the conditions" in validate(a, d.known_methods, d.kinds)) == (like == "DIA")


# ------------------------------------------------------------- end to end --


@pytest.mark.parametrize("key", sorted(CUSTOM))
def test_custom_method_runs_and_is_analysed_as_the_method_it_is_like(bed, key):
    like, sample, _alias = CUSTOM[key]
    dest = _run(bed, _drop_as(bed, key))
    cfg = bed["cfg"]
    st = json.loads((dest / "ionomos.json").read_text(encoding="utf-8"))
    assert st["plan"]["folder"]["method"] == key and st["method_config"]["analysis_method"] == like
    assert (dest / "ionomos_run" / f"{key}.workflow").is_file()   # the job keeps the lab's name
    assert not any("expected output" in w for w in (st.get("run") or {}).get("warnings") or [])

    mine = _analysis(dest)
    assert mine["method"] == like
    assert mine["comparisons"], mine
    assert f"· {key} ·" in (dest / "results" / "report.html").read_text(encoding="utf-8")  # shown under its name
    sdrf_mine = (dest / "results" / "sdrf.tsv").read_text(encoding="utf-8")
    if like == "isoDTB":
        assert list((dest / "results").glob("*_sites.tsv")) and mine["level"] == "site"
        assert "ICAT light" in sdrf_mine and "ICAT heavy" in sdrf_mine
    elif like == "TMT":
        ann = list(dest.rglob("annotation.txt"))   # the channel map from experiment.yaml, as for TMT
        assert len(ann) == 1 and "126\tDMSO_1_126" in ann[0].read_text(encoding="utf-8")
        assert (dest / "results" / "experimental_annotation.tsv").is_file()
        assert "TMT126" in sdrf_mine and [c["name"] for c in mine["comparisons"]] == ["Drug vs DMSO"]
    else:
        assert "label free sample" in sdrf_mine and [c["name"] for c in mine["comparisons"]] == ["Drug vs DMSO"]
        cmd = st["run"]["command"]
        assert "--workflow" in cmd and all(m["data_type"] == "DIA" for m in st["plan"]["manifest"])

    # the same folder analysed as the built-in method gives the same numbers, sample sheet and issues
    p = postprocess.prepare(dest, cfg, key)
    assert p["method"] == like and p["context"]["method"] == key
    out = downstream.analyze(dest, like, p["lab"], p["overrides"], p["record"], dict(p["context"]), p["mod_mass"])
    theirs = _analysis(dest)
    assert {**theirs, "generated_at": ""} == {**mine, "generated_at": ""}
    assert (dest / "results" / "sdrf.tsv").read_text(encoding="utf-8") == sdrf_mine
    again = postprocess.run_for_folder(dest, cfg)   # `ionomos analyze <folder>`: the method from ionomos.json
    assert again.method == like and [i.as_dict() for i in again.issues] == [i.as_dict() for i in out.issues]
    # the Analysis tab re-runs with the method the first look returned (the kind): still this experiment's method
    info = postprocess.inspect_folder(dest, cfg, key)
    assert info["method"] == like and info["features"] == mine["features_loaded"]
    assert postprocess.prepare(dest, cfg, info["method"])["context"]["method"] == key
    # without the lab's config, the folder still says what it is analysed as
    assert postprocess.prepare(dest, None)["method"] == like


@pytest.mark.parametrize("key", sorted(CUSTOM))
def test_custom_method_matches_the_builtin_job(bed, key):
    """The same files dropped once under the built-in method and once under the custom one."""
    like, sample, _alias = CUSTOM[key]
    theirs = _run(bed, testbed.drop(bed["root"], sample))
    mine = _run(bed, _drop_as(bed, key))
    a, b = _analysis(mine), _analysis(theirs)
    for k in ("method", "level"):
        assert a[k] == b[k], k
    # the fake search seeds its data from the raw paths, which differ between the two folders: a few simulated
    # isoDTB sites can coincide, so the count is close, not equal
    assert abs(a["features_loaded"] - b["features_loaded"]) <= 0.02 * b["features_loaded"]
    assert [c["name"] for c in a["comparisons"]] == [c["name"] for c in b["comparisons"]]
    assert sorted(a["samples"].values()) == sorted(b["samples"].values())
    assert a["engine"]["engine"] == b["engine"]["engine"] == "FragPipe"

    def table(dest):
        rows = [ln.split("\t") for ln in (dest / "results" / "sdrf.tsv").read_text(encoding="utf-8").splitlines()]
        head = rows[0]
        keep = [i for i, h in enumerate(head) if h in ("comment[label]", "comment[data file]", "technology type",
                                                       "comment[proteomics data acquisition method]",
                                                       "comment[fraction identifier]", "factor value[condition]")]
        return [[r[i] for i in keep] for r in rows]

    assert table(mine) == table(theirs)
    spec_mine = sorted(p.name for p in (mine / "ionomos_run").iterdir() if p.suffix != ".workflow")
    assert spec_mine == sorted(p.name for p in (theirs / "ionomos_run").iterdir() if p.suffix != ".workflow")


@pytest.mark.parametrize("key", sorted(CUSTOM))
def test_doctor_names_the_table_the_kind_needs(bed, key, tmp_path):
    like = CUSTOM[key][0]
    empty = tmp_path / "exp"
    (empty / "fragpipe").mkdir(parents=True)
    mine = postprocess.run_for_folder(empty, bed["cfg"], key)
    theirs = postprocess.run_for_folder(empty, bed["cfg"], like)
    assert [i.as_dict() for i in mine.issues] == [i.as_dict() for i in theirs.issues]
    want = downstream.doctor.EXPECTED[like][0]
    assert any(want in i.message for i in mine.issues), [i.message for i in mine.issues]


def test_expected_outputs_and_tmt_annotation_follow_the_kind(bed):
    folder = _drop_as(bed, "TMTpro")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    spec = fragpipe.prepare(bed["ledger"].list()[-1], bed["cfg"])
    assert spec.method == "TMTpro" and spec.kind == "TMT"
    assert spec.expected_outputs() == fragpipe.EXPECTED_OUTPUTS["TMT"]
    assert list(spec.annotations) == ["annotation.txt"]
    assert spec.workflow.name == "TMTpro.workflow"


def test_statistics_off_still_writes_the_kinds_files(bed):
    """analysis.enabled: false keeps the lab's R-script outputs (site table) for a like: isoDTB method."""
    cfg_path = _reload(bed["cfg_path"], lambda d: d.update(analysis={"enabled": False}))
    bed["cfg"] = load(cfg_path)
    dest = _run(bed, _drop_as(bed, "Cys"))
    assert list((dest / "results").glob("*_sites.tsv"))
    assert not (dest / "results" / "report.html").exists()


# ----------------------------------------------------------------- engines --


def test_diann_method_under_another_key_is_dia(bed, tmp_path):
    exe = testbed.write_fake_diann(tmp_path / "diann")

    def change(d):
        d["methods"]["SWATH"] = {"engine": "diann", "diann_exe": str(exe).replace("\\", "/"), "data_type": "DIA",
                                 "fasta": d["methods"]["DIA"]["fasta"], "aliases": ["swath"]}
        d["naming"]["methods"]["SWATH"] = "{sample}_{rep}"   # its own template, no like:
    bed["cfg"] = cfg = load(_reload(bed["cfg_path"], change))
    assert cfg.kind("SWATH") == "DIA" and cfg.analysis_method("SWATH") == "DIA"
    folder = make_drop(cfg.inbox, "20260930_EJQ_swath_run", [f"{c}_{r}.raw" for c in ("DMSO", "Drug") for r in (1, 2, 3)])
    assert has_control(draft(folder, cfg, review=True).kind_of("SWATH"))
    dest = _run(bed, folder)
    a = _analysis(dest)
    assert a["method"] == "DIA" and [c["name"] for c in a["comparisons"]] == ["Drug vs DMSO"]
    assert a["engine"]["engine"] == "DIA-NN"


def test_maxquant_method_stays_label_free_whatever_its_names_are_like(bed, tmp_path):
    """docs/ENGINES.md's example: `LFQ: {like: isoDTB}` lends the <sample>_<rep>_<fraction> names only;
    the engine decides the kind, so the job is label-free with a control, read from proteinGroups.txt."""
    exe = testbed.write_fake_maxquant(tmp_path / "mq")

    def change(d):
        d["methods"]["LFQ"] = {"engine": "maxquant", "maxquant_exe": str(exe).replace("\\", "/"), "data_type": "DDA",
                               "fasta": d["methods"]["isoDTB"]["fasta"], "aliases": ["lfq"]}
        d["naming"]["methods"]["LFQ"] = {"like": "isoDTB"}
    bed["cfg"] = cfg = load(_reload(bed["cfg_path"], change))
    assert cfg.file_rules["LFQ"].like == "isoDTB"
    assert cfg.kind("LFQ") == "LFQ" and cfg.analysis_method("LFQ") == "MaxQuant"
    files = [f"{c}_{r}_{f}.raw" for c in ("DMSO", "Drug") for r in (1, 2, 3) for f in (1, 2)]
    folder = make_drop(cfg.inbox, "20260930_EJQ_lfq_run", files)
    assert has_control(draft(folder, cfg, review=True).kind_of("LFQ"))
    dest = _run(bed, folder)
    a = _analysis(dest)
    assert a["method"] == "MaxQuant" and [c["name"] for c in a["comparisons"]] == ["Drug vs DMSO"]
    assert not list((dest / "results").glob("*_sites.tsv"))


# --------------------------------------------------------------------- CLI --


def test_analyze_method_takes_the_labs_own_keys(bed, capsys):
    dest = _run(bed, _drop_as(bed, "DIA_phospho"))
    cfg = str(bed["cfg_path"])
    assert main(["--config", cfg, "analyze", str(dest), "--method", "dia_phospho", "--quiet"]) == 0
    assert "method: DIA\n" in capsys.readouterr().out
    assert main(["--config", cfg, "analyze", str(dest), "--method", "DIA", "--quiet"]) == 0   # as before
    assert main(["--config", cfg, "analyze", str(dest), "--method", "nope"]) == 2
    err = capsys.readouterr().err
    assert "'nope' is not one of isoDTB, TMT, DIA, LFQ" in err and "DIA_phospho" in err
