"""Sage run by the watcher (`engine: sage`, sage.py), end to end against the testbed's stand-ins for Sage and
ThermoRawFileParser. The fake Sage checks the FASTA and every mzML it is given like the real one, so a job only
reaches "done" if the conversion and sage.json were prepared correctly. A lab sage_config with quant.tmt makes
it a TMT job: the fake Sage then writes tmt.tsv, and the analysis reads it (D56)."""
import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ionomos import fragpipe, sage, testbed
from ionomos.config import ConfigError, MethodConfig, load
from ionomos.help import hold_topic
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker
from tests.conftest import make_drop

FILES = [f"{c}_{r}_{f}.raw" for c in ("DMSO", "Drug") for r in (1, 2, 3) for f in (1, 2)]


def _with_sage(cfg, exe, converter, key="isoDTB", **extra):
    m = cfg.methods[key]  # isoDTB: a DDA method with the <sample>_<rep>_<fraction> naming rule; TMT: <plex>_F<fraction>
    more = {"engine": "sage", "sage_exe": str(exe), **({"raw_converter": str(converter)} if converter else {}), **extra}
    new = MethodConfig(key=key, workflow="", fasta=m.fasta, data_type="DDA", postprocess=(), aliases=m.aliases,
                       extra=more)
    return replace(cfg, methods={**cfg.methods, key: new})


@pytest.fixture
def bed(tmp_path, monkeypatch):
    for var in ("IONOMOS_FAKE_FP_MODE", "IONOMOS_FAKE_CONVERT_MODE", "IONOMOS_FAKE_SAGE_OLD"):
        monkeypatch.delenv(var, raising=False)
    cfg = load(testbed.init(tmp_path / "bed"))
    exe = testbed.write_fake_sage(tmp_path / "sage")
    conv = testbed.write_fake_rawparser(tmp_path / "trfp")
    return {"root": tmp_path / "bed", "cfg": _with_sage(cfg, exe, conv), "ledger": Ledger(cfg.database), "exe": exe,
            "conv": conv}


def _queue(bed, cfg=None) -> Path:
    cfg = cfg or bed["cfg"]
    folder = make_drop(cfg.inbox, "20260930_EJQ_isoDTB_sage-lfq", FILES)
    assert intake(folder, cfg, bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def _log(dest: Path) -> str:
    return (dest / "ionomos_run" / sage.CONSOLE_LOG).read_text(encoding="utf-8")


def test_dda_job_is_converted_searched_and_analysed(bed):
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done", job.reason
    cfg = json.loads((dest / "ionomos_run" / sage.CONFIG_NAME).read_text(encoding="utf-8"))
    mzml = [Path(p) for p in cfg["mzml_paths"]]
    assert sorted(p.name for p in mzml) == sorted(f[:-4] + ".mzML" for f in FILES)
    assert all(p.parent == dest / sage.MZML_DIR and p.is_file() for p in mzml)
    assert not list((dest / sage.MZML_DIR / "converting").iterdir())      # every conversion was moved into place
    assert cfg["database"]["fasta"] == str(bed["cfg"].fasta_dir / bed["cfg"].methods["isoDTB"].fasta)
    assert cfg["output_directory"] == str(dest / "sage") and cfg["quant"]["lfq"] is True
    assert cfg["database"]["static_mods"] == {"C": 57.021464} and cfg["database"]["generate_decoys"] is True
    st = json.loads((dest / "ionomos.json").read_text(encoding="utf-8"))
    assert st["run"]["command"][-2:] == ["sage-job", str(dest / "ionomos_run" / sage.JOB_NAME)]
    assert st["run"]["workflow"].endswith(sage.CONFIG_NAME)
    log = _log(dest)
    assert "[12/12] converting Drug_3_2.raw -> Drug_3_2.mzML" in log
    assert f"FAKE Sage: telemetry off | threads {bed['cfg'].threads}" in log   # switched off, threads capped
    assert all((dest / f).is_file() for f in FILES)                         # the raw files are where they were
    assert (dest / "sage" / "lfq.tsv").is_file() and not (dest / "fragpipe").exists()
    summary = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert summary["method"] == "Sage"
    assert summary["engine"]["engine"] == "Sage" and summary["engine"]["version"] == testbed.FAKE_SAGE_VERSION
    assert [c["name"] for c in summary["comparisons"]] == ["Drug vs DMSO"]
    assert summary["comparisons"][0]["up"] + summary["comparisons"][0]["down"] > 5
    assert any("fractions were added (6 sample(s) with 2–2 files)" in n for n in summary["notes"]), summary["notes"]
    assert "Sage finished" in (dest / "DONE.txt").read_text(encoding="utf-8")


def test_the_labs_own_settings_are_kept_except_for_the_job_parts(bed):
    lab = bed["cfg"].workflow_dir / "lab_sage.json"
    lab.write_text(json.dumps({"database": {"enzyme": {"cleave_at": "K"}, "fasta": "old.fasta",
                                            "static_mods": {"C": 57.0215, "K": 229.1629}},
                               "quant": {"lfq": False}, "precursor_tol": {"da": [-500, 100]},
                               "mzml_paths": ["old.mzML"], "output_directory": "elsewhere"}), encoding="utf-8")
    cfg = _with_sage(bed["cfg"], bed["exe"], bed["conv"], sage_config="lab_sage.json", sage_args="--batch-size 2")
    dest = _queue(bed, cfg)
    Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done", job.reason
    out = json.loads((dest / "ionomos_run" / sage.CONFIG_NAME).read_text(encoding="utf-8"))
    assert out["database"]["enzyme"] == {"cleave_at": "K"} and out["precursor_tol"] == {"da": [-500, 100]}
    assert out["database"]["static_mods"] == {"C": 57.0215, "K": 229.1629}
    assert out["database"]["fasta"].endswith(cfg.methods["isoDTB"].fasta) and len(out["mzml_paths"]) == 12
    assert out["output_directory"] == str(dest / "sage") and out["quant"]["lfq"] is True
    assert "label-free quantification off; switched on" in job.reason
    assert "--batch-size 2" in _log(dest)
    assert json.loads(lab.read_text(encoding="utf-8"))["mzml_paths"] == ["old.mzML"]   # the lab's file is not written


def test_missing_setup_holds_the_job_with_a_help_topic(bed, tmp_path):
    lab = bed["cfg"].workflow_dir / "tmt.json"
    cases = [
        (_with_sage(bed["cfg"], tmp_path / "no" / "sage.exe", bed["conv"]), "Sage not found", "search.hold-sage"),
        (_with_sage(bed["cfg"], bed["exe"], tmp_path / "no" / "trfp.exe"), "raw file converter not found",
         "search.hold-converter"),
        (_with_sage(bed["cfg"], bed["exe"], bed["conv"], sage_config="nope.json"), "Sage settings 'nope.json'",
         "search.hold-sage-config"),
        (_with_sage(bed["cfg"], bed["exe"], bed["conv"], sage_config="tmt.json"), "quant.tmt is 'Tmt12'",
         "search.hold-sage-config"),
        (_with_sage(bed["cfg"], bed["exe"], bed["conv"], sage_args="--batch-size 2 --parquet"),
         "sage_args has --parquet", "search.hold-sage-config"),
    ]
    lab.write_text(json.dumps({"quant": {"tmt": "Tmt12"}}), encoding="utf-8")   # not a kit Sage has
    _queue(bed)
    for cfg, text, topic in cases:
        Worker(cfg, bed["ledger"]).run_once()
        job = bed["ledger"].get(1)
        assert job.status == "queued" and text in job.reason, job.reason
        assert hold_topic(job.reason) == topic
    lab.write_text("{not json", encoding="utf-8")
    Worker(cases[-2][0], bed["ledger"]).run_once()
    assert "can't be read as JSON" in bed["ledger"].get(1).reason
    Worker(bed["cfg"], bed["ledger"]).run_once()   # fixed: runs
    assert bed["ledger"].get(1).status == "done"


TMT_FILES = [f"plex{p}_F{f}.raw" for p in "ABC" for f in (1, 2)]
TMT_SETTINGS = {"database": {"static_mods": {"^": 229.162932, "K": 229.162932, "C": 57.021464}},
                "quant": {"tmt": "Tmt10", "tmt_settings": {"level": 3, "sn": False}}}


def _queue_tmt(bed, channels: dict | None, kit="Tmt10", **tmt) -> tuple:
    """A TMT drop of three plexes with two fractions each, searched by Sage with the lab's TMT settings."""
    import yaml

    lab = bed["cfg"].workflow_dir / "lab_tmt.json"
    lab.write_text(json.dumps({**TMT_SETTINGS, "quant": {**TMT_SETTINGS["quant"], "tmt": kit}}), encoding="utf-8")
    cfg = _with_sage(bed["cfg"], bed["exe"], bed["conv"], key="TMT", sage_config="lab_tmt.json")
    folder = make_drop(cfg.inbox, "20260930_EJQ_TMT_sage-tmt", TMT_FILES)
    if channels or tmt:
        (folder / "experiment.yaml").write_text(yaml.safe_dump({"tmt": {**({"channels": channels} if channels else {}),
                                                                        **tmt}}), encoding="utf-8")
    assert intake(folder, cfg, bed["ledger"]).value == "queued"
    return cfg, Path(bed["ledger"].list()[-1].dest_dir)


def test_tmt_job_writes_tmt_tsv_and_is_analysed_per_plex_and_channel(bed):
    from ionomos.downstream.plex import TMT_ORDERS

    design = testbed.fake_sage_tmt_design(10)   # Pool, 4 x DMSO, 4 x Drug, Pool
    seen: dict[str, int] = {}
    channels = {}
    for ch, c in zip(TMT_ORDERS[10], design, strict=True):
        seen[c] = seen.get(c, 0) + 1
        channels[ch] = c if c == "Pool" else f"{c}_{seen[c]}"
    cfg, dest = _queue_tmt(bed, channels)
    Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done", job.reason
    out = json.loads((dest / "ionomos_run" / sage.CONFIG_NAME).read_text(encoding="utf-8"))
    assert out["quant"] == TMT_SETTINGS["quant"]                # TMT as the lab set it; label-free not switched on
    assert out["database"]["static_mods"]["K"] == 229.162932 and len(out["mzml_paths"]) == 6
    assert (dest / "sage" / "tmt.tsv").is_file() and not (dest / "sage" / "lfq.tsv").exists()
    assert "label-free quantification off" not in job.reason and "channel map" not in job.reason
    s = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert s["method"] == "Sage" and s["source"].endswith("tmt.tsv")
    assert s["engine"]["fdr"] == "spectrum, peptide and protein q ≤ 0.01" and s["engine"]["table"] == "sage/tmt.tsv"
    assert s["engine"]["quantity"].startswith("median polish of tmt.tsv reporter intensities")
    # three plexes of 10 channels; the two pools of each put the plexes on one scale and then leave
    assert s["tmt"]["applied"] and s["tmt"]["method"] == "IRS (reference channel)"
    assert s["tmt"]["plexes"] == {"plexA": 10, "plexB": 10, "plexC": 10} and len(s["tmt"]["removed"]) == 6
    assert len(s["samples"]) == 24 and set(s["samples"].values()) == {"DMSO", "Drug"}
    assert "plexB_Drug_3" in s["samples"] and s["design"]["conditions_from"] == "experiment.yaml tmt: channel map"
    assert [c["name"] for c in s["comparisons"]] == ["Drug vs DMSO"]
    assert s["comparisons"][0]["up"] + s["comparisons"][0]["down"] > 10
    notes = " | ".join(s["notes"])
    assert "left out: 3 decoy, 3 of lower rank, 6 above a q-value" in notes      # per plex: 1, 1 and 2
    assert "fractions combined within 3 mixture run(s)" in notes and "6 peptide(s) shared" in notes
    with open(dest / "results" / "protein_matrix_log2.tsv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert len(rows) == 300 and not any("P9999" in r["id"] for r in rows)        # nothing from the rows planted to fail
    assert max(float(v) for r in rows for k, v in r.items() if k.startswith("plex") and v not in ("", "NA")) < 36   # nor their 1e12
    sdrf = (dest / "results" / "sdrf.tsv").read_text(encoding="utf-8")
    assert "TMT127N" in sdrf and "plexB_F2.raw" in sdrf


def test_tmt_job_without_a_channel_map_asks_for_the_conditions(bed):
    cfg, dest = _queue_tmt(bed, None, kit="Tmt16")
    lines = sage.describe(cfg, "TMT")
    assert (True, "Sage settings lab_tmt.json (TMT quantification, Tmt16: the analysis reads tmt.tsv)") in lines
    Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done" and "TMT job without a tmt: channel map" in job.reason
    s = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert len(s["samples"]) == 48 and set(s["samples"].values()) == {"unassigned"} and "plexA_134N" in s["samples"]
    codes = {i["code"] for i in s["issues"]}
    assert "ONE_CONDITION" in codes and "TMT_PLEXES_NOT_NORMALISED" in codes   # no plex means on unknown channels
    assert not s["tmt"]["applied"] and "no condition yet" in s["tmt"]["reason"]
    assert any("give each its condition on the Analysis tab" in n for n in s["notes"])


def test_tmt_plexes_map_must_name_every_plex(bed):
    cfg, _dest = _queue_tmt(bed, None, plexes={"plexA": {"channels": {126: "Pool"}}})
    Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "tmt.plexes has no entry for experiment(s): plexB, plexC" in job.reason


def test_a_qc_standard_searched_by_sage_is_trended(bed):
    from ionomos import qctrend

    folder = make_drop(bed["cfg"].inbox, "20260930_EJQ_isoDTB_HeLa-200ng-QC", ["HeLa_200ng_1.raw", "HeLa_200ng_2.raw"])
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done", job.reason
    rows = sorted(qctrend.load(bed["cfg"].log_dir), key=lambda r: r["run"])
    assert [r["run"] for r in rows] == ["HeLa_200ng_1", "HeLa_200ng_2"]
    assert all(r["series"] == "isoDTB · HeLa · 200ng" and r["acquisition"] == "DDA" for r in rows)
    m = rows[0]["metrics"]
    assert m["psms"] > 700 and m["peptides"] > 700 and 250 < m["proteins"] <= 310
    assert m["ms1_ppm"] == pytest.approx(1.5, abs=0.05)         # the +1.5 ppm the fake plants, isotope peaks corrected
    assert 2 < m["charge"] < 3 and 0 < m["missed"] < 0.5 and len(rows[0]["rt"]) == 200
    assert "results.sage.tsv" in rows[0]["sources"]["psms"]
    st = json.loads((Path(job.dest_dir) / "ionomos.json").read_text(encoding="utf-8"))
    assert st["results"]["qc_trend"][0].startswith("HeLa_200ng_1: baseline")


def test_a_failed_conversion_fails_the_job_and_a_retry_converts_only_what_is_missing(bed, monkeypatch):
    dest = _queue(bed)
    monkeypatch.setenv("IONOMOS_FAKE_CONVERT_MODE", "fail")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "Sage exited with code 1" in job.reason, job.reason
    assert "converting DMSO_1_1.raw failed" in job.reason
    assert not list((dest / sage.MZML_DIR).glob("*.mzML")) and not (dest / "sage" / "lfq.tsv").exists()
    monkeypatch.setenv("IONOMOS_FAKE_CONVERT_MODE", "empty")   # exit 0 but no mzML: still a failure
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "failed" and "no mzML written" in bed["ledger"].get(1).reason
    # one file already converted: it is reused, the rest are converted
    monkeypatch.delenv("IONOMOS_FAKE_CONVERT_MODE")
    done = dest / sage.MZML_DIR / "DMSO_1_1.mzML"
    done.write_text("converted earlier", encoding="utf-8")
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    assert done.read_text(encoding="utf-8") == "converted earlier"
    assert "[1/12] DMSO_1_1.mzML already converted" in _log(dest)
    assert len(list((dest / sage.MZML_DIR).glob("*.mzML"))) == 12


def test_a_failed_search_keeps_its_output_on_retry(bed, monkeypatch):
    dest = _queue(bed)
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "fail")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "failed" and "fake failure" in bed["ledger"].get(1).reason
    (dest / "sage" / "partial.txt").write_text("from the failed attempt", encoding="utf-8")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE")
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done"
    kept = [p for p in dest.iterdir() if p.name.startswith("sage_previous_")]
    assert len(kept) == 1 and (kept[0] / "partial.txt").read_text(encoding="utf-8") == "from the failed attempt"
    assert _log(dest).count("already converted") == 12   # nothing converted twice


def test_a_sage_without_the_telemetry_switch_still_runs(bed, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_SAGE_OLD", "1")
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    assert "this Sage has no telemetry switch" in _log(dest) and "FAKE Sage: telemetry ON" in _log(dest)


def test_mzml_and_bruker_inputs_are_searched_as_they_are(bed, tmp_path):
    dest = _queue(bed)
    job = bed["ledger"].get(1)
    spec = sage.prepare(job, bed["cfg"])
    spec.manifest_lines = [(str(dest / "a_1.mzML"), "a", 1, "DDA"), (str(dest / "b_1.d"), "b", 1, "DDA"),
                           (str(dest / "c_1.raw"), "c", 1, "DDA")]
    assert spec.inputs() == [(str(dest / "a_1.mzML"), None), (str(dest / "b_1.d"), None),
                             (str(dest / sage.MZML_DIR / "c_1.mzML"), str(dest / "c_1.raw"))]
    assert [c["raw"] for c in spec.job()["convert"]] == [str(dest / "c_1.raw")]


def test_two_raw_files_with_one_name_are_refused(bed):
    dest = _queue(bed)
    job = bed["ledger"].get(1)
    (dest / "again").mkdir()
    (dest / "again" / "DMSO_1_1.raw").write_bytes(b"\0" * 64)
    job.parsed["plan"]["manifest"].append({**job.parsed["plan"]["manifest"][0], "file": "again/DMSO_1_1.raw"})
    with pytest.raises(fragpipe.JobError, match="share the name DMSO_1_1"):
        sage.prepare(job, bed["cfg"])


def test_config_and_setup_checklist(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setattr(sage, "CONVERTER_CANDIDATES", ())  # a PC with C:\ThermoRawFileParser\ would find it

    base = yaml.safe_load(testbed.init(tmp_path / "b").read_text(encoding="utf-8"))
    base["methods"]["LFQ"] = {"engine": "sage", "fasta": "human_reviewed_decoys.fas", "data_type": "DDA",
                              "sage_exe": "C:/sage/sage.exe"}
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(base), encoding="utf-8")
    cfg = load(p)
    assert sage.uses_sage(cfg, "LFQ") and not sage.uses_sage(cfg, "isoDTB") and cfg.methods["LFQ"].workflow == ""
    lines = fragpipe.describe_method(cfg, "LFQ")
    assert lines[0] == (False, "Sage not found (set sage_exe)")
    assert lines[1][0] is None and "only mzML drops" in lines[1][1]
    base["methods"]["LFQ"]["data_type"] = "DIA"
    p.write_text(yaml.safe_dump(base), encoding="utf-8")
    with pytest.raises(ConfigError, match="needs data_type DDA"):
        load(p)


def test_sage_is_never_taken_from_the_path():
    """`sage` on a PATH is usually SageMath: only a configured file or the Windows install folders count."""
    assert sage.find_exe("sage") is None
    assert not any("/usr" in c or c == "sage" for c in sage.EXE_CANDIDATES)


def test_a_failed_search_after_good_conversions_does_not_blame_the_raw_files(bed, monkeypatch):
    dest = _queue(bed)
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "fail")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "Sage exited with code 1" in job.reason
    assert _log(dest).count("FAKE ThermoRawFileParser: converted") == 12   # the converter is named, and it worked
    assert ".raw file couldn't be read" not in job.reason
    assert ".raw file couldn't be read" not in (dest / "FAILED.txt").read_text(encoding="utf-8")
    assert not any(".raw file" in h for h in fragpipe.explain(_log(dest)))


def test_a_failed_conversion_does_blame_the_raw_file(bed, monkeypatch):
    dest = _queue(bed)
    monkeypatch.setenv("IONOMOS_FAKE_CONVERT_MODE", "fail")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and job.reason.startswith("A .raw file couldn't be read")
    assert "A .raw file couldn't be read" in (dest / "FAILED.txt").read_text(encoding="utf-8")
