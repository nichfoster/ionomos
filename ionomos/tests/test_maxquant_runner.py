"""MaxQuant run by the watcher (`engine: maxquant`, maxquant.py), end to end against the testbed's fake
MaxQuantCmd, which makes a template with --create and checks the patched mqpar.xml like the real one."""
import json
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ionomos import fragpipe, maxquant, testbed
from ionomos.config import ConfigError, MethodConfig, load
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker
from tests.conftest import make_drop

FILES = [f"{c}_{r}_{f}.raw" for c in ("DMSO", "Drug") for r in (1, 2, 3) for f in (1, 2)]


def _with_mq(cfg, exe, **extra):
    m = cfg.methods["isoDTB"]  # a DDA method with the <sample>_<rep>_<fraction> naming rule
    new = MethodConfig(key="isoDTB", workflow="", fasta=m.fasta, data_type="DDA", postprocess=(),
                       aliases=m.aliases, extra={"engine": "maxquant", "maxquant_exe": str(exe), **extra})
    return replace(cfg, methods={**cfg.methods, "isoDTB": new})


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE", raising=False)
    cfg = load(testbed.init(tmp_path / "bed"))
    exe = testbed.write_fake_maxquant(tmp_path / "mq")
    return {"root": tmp_path / "bed", "cfg": _with_mq(cfg, exe), "ledger": Ledger(cfg.database), "exe": exe}


def _queue(bed, cfg=None) -> Path:
    cfg = cfg or bed["cfg"]
    folder = make_drop(cfg.inbox, "20260930_EJQ_isoDTB_mq-lfq", FILES)
    assert intake(folder, cfg, bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def _mqpar(dest: Path) -> ET.Element:
    return ET.parse(dest / "ionomos_run" / maxquant.MQPAR).getroot()


def test_dda_job_runs_with_maxquant_to_a_report(bed):
    dest = _queue(bed)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done", job.reason
    root = _mqpar(dest)
    files = [e.text for e in root.findall("filePaths/string")]
    exps = [e.text for e in root.findall("experiments/string")]
    fracs = [e.text for e in root.findall("fractions/short")]
    assert len(files) == 12 and all(Path(f).is_file() for f in files)
    by = {Path(f).name: (x, fr) for f, x, fr in zip(files, exps, fracs, strict=True)}
    assert by["DMSO_1_1.raw"] == ("DMSO_1", "1") and by["DMSO_1_2.raw"] == ("DMSO_1", "2")
    assert by["Drug_3_2.raw"] == ("Drug_3", "2")
    assert root.findtext("fastaFiles/FastaFileInfo/fastaFilePath") == str(bed["cfg"].fasta_dir / bed["cfg"].methods["isoDTB"].fasta)
    assert root.findtext("fixedCombinedFolder") == str(dest / "maxquant")
    assert root.findtext("numThreads") == str(bed["cfg"].threads)
    assert root.findtext("parameterGroups/parameterGroup/lfqMode") == "1"  # MaxQuant's default template: LFQ on
    assert (dest / "ionomos_run" / maxquant.DEFAULT_TEMPLATE).is_file()     # made by --create
    st = json.loads((dest / "ionomos.json").read_text(encoding="utf-8"))
    assert st["run"]["command"] == [str(bed["exe"]), str(dest / "ionomos_run" / maxquant.MQPAR)]
    assert (dest / "maxquant" / "combined" / "txt" / "proteinGroups.txt").is_file()
    summary = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert summary["method"] == "MaxQuant"
    assert summary["engine"]["engine"] == "MaxQuant" and summary["engine"]["version"] == "2.6.7.0"
    assert [c["name"] for c in summary["comparisons"]] == ["Drug vs DMSO"]
    assert "MaxQuant finished" in (dest / "DONE.txt").read_text(encoding="utf-8")


def test_the_labs_own_mqpar_is_kept_except_for_the_job_parts(bed):
    lab = bed["cfg"].workflow_dir / "lab_lfq.xml"
    text = testbed.FAKE_MQPAR.replace("<numThreads>1</numThreads>", "<numThreads>1</numThreads>\n"
                                      "   <labSetting xsi:nil=\"true\" />\n   <matchBetweenRuns>True</matchBetweenRuns>")
    lab.write_text(text, encoding="utf-8")
    cfg = _with_mq(bed["cfg"], bed["exe"], mqpar="lab_lfq.xml")
    dest = _queue(bed, cfg)
    Worker(cfg, bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    raw = (dest / "ionomos_run" / maxquant.MQPAR).read_text(encoding="utf-8")
    root = ET.fromstring(raw)
    assert root.findtext("matchBetweenRuns") == "True"
    assert root.findtext("parameterGroups/parameterGroup/lfqMode") == "0"  # the lab's choice, untouched
    assert 'xsi:nil="true"' in raw and not (dest / "ionomos_run" / maxquant.DEFAULT_TEMPLATE).exists()
    assert len(root.findall("filePaths/string")) == 12


def test_missing_exe_or_mqpar_holds_the_job(bed, tmp_path):
    cfg = _with_mq(bed["cfg"], bed["exe"], mqpar="nope.xml")
    _queue(bed, cfg)
    Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "mqpar 'nope.xml'" in job.reason
    cfg2 = _with_mq(bed["cfg"], tmp_path / "none" / "MaxQuantCmd.exe")
    Worker(cfg2, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "MaxQuant not found" in job.reason and "maxquant_exe" in job.reason
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done"


def test_failure_fails_the_job_and_retry_keeps_the_old_output(bed, monkeypatch):
    dest = _queue(bed)
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "fail")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "MaxQuant exited with code 1" in job.reason
    (dest / "maxquant" / "partial.txt").write_text("from the failed attempt", encoding="utf-8")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE")
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done"
    kept = [p for p in dest.iterdir() if p.name.startswith("maxquant_previous_")]
    assert len(kept) == 1 and (kept[0] / "partial.txt").is_file()


def test_fractions_from_names_order_and_single_shot(bed):
    cfg = bed["cfg"]
    lines = [("/d/X_1_2.raw", "X", 1, "DDA"), ("/d/X_1_1.raw", "X", 1, "DDA"), ("/d/Y_1.raw", "Y", 1, "DDA"),
             ("/d/odd-a.raw", "Z", 1, "DDA"), ("/d/odd-b.raw", "Z", 1, "DDA")]
    got = maxquant.fractions_of(lines, "isoDTB", cfg)
    assert got["/d/X_1_2.raw"] == "2" and got["/d/X_1_1.raw"] == "1"
    assert got["/d/Y_1.raw"] == maxquant.NO_FRACTION
    assert {got["/d/odd-a.raw"], got["/d/odd-b.raw"]} == {"1", "2"}  # unreadable names: numbered in order


def test_config_accepts_maxquant_for_dda_only(tmp_path):
    import yaml

    base = yaml.safe_load(testbed.init(tmp_path / "b").read_text(encoding="utf-8"))
    base["methods"]["isoDTB"] = {"engine": "maxquant", "fasta": "human_reviewed_decoys.fas", "data_type": "DDA",
                                 "maxquant_exe": "C:/MaxQuant/2.6.7.0/bin/MaxQuantCmd.exe"}
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(base), encoding="utf-8")
    cfg = load(p)
    assert maxquant.uses_maxquant(cfg, "isoDTB")
    lines = fragpipe.describe_method(cfg, "isoDTB")
    assert lines[0][0] is False and "MaxQuant not found" in lines[0][1]
    base["methods"]["isoDTB"]["data_type"] = "DIA"
    p.write_text(yaml.safe_dump(base), encoding="utf-8")
    with pytest.raises(ConfigError, match="needs data_type DDA"):
        load(p)


def test_a_non_mqpar_template_is_refused(tmp_path):
    spec = maxquant.MaxQuantSpec(job_id=1, method="x", dest=tmp_path, exe=tmp_path / "m.exe", workflow_src=Path("x"),
                                 fasta=tmp_path / "f.fas", manifest_lines=[], threads=1, ram_gb=0, timeout_minutes=0)
    with pytest.raises(fragpipe.JobError, match="not a MaxQuant parameter file"):
        maxquant.patch_mqpar("<fragpipe/>", spec, {}, lfq=False)
