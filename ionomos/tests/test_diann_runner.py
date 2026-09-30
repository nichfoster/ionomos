"""DIA-NN run by the watcher (`engine: diann`, diann.py), end to end against the testbed's fake DIA-NN.

The fake (`ionomos fake-diann`) reads the diann.cfg the runner writes and fails like the real one on a
missing raw file / FASTA / library, so a job only reaches "done" if the inputs were prepared correctly."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ionomos import diann, fragpipe, testbed
from ionomos.config import ConfigError, MethodConfig, load
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker


def _with_diann(cfg, exe, **extra):
    m = cfg.methods["DIA"]
    new = MethodConfig(key="DIA", workflow="", fasta=m.fasta, data_type="DIA", postprocess=m.postprocess,
                       aliases=m.aliases, extra={"engine": "diann", "diann_exe": str(exe), **extra})
    return replace(cfg, methods={**cfg.methods, "DIA": new})


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE", raising=False)
    cfg = load(testbed.init(tmp_path / "bed"))
    exe = testbed.write_fake_diann(tmp_path / "diann")
    return {"root": tmp_path / "bed", "cfg": _with_diann(cfg, exe), "ledger": Ledger(cfg.database), "exe": exe}


def _queue(bed, sample="dia_good") -> Path:
    folder = testbed.drop(bed["root"], sample)
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    return Path(bed["ledger"].list()[-1].dest_dir)


def _status(dest: Path) -> dict:
    return json.loads((dest / "ionomos.json").read_text(encoding="utf-8"))


def test_dia_job_runs_with_diann_to_a_report(bed):
    dest = _queue(bed)
    w = Worker(bed["cfg"], bed["ledger"])
    assert w.run_once()
    job = bed["ledger"].get(1)
    assert job.status == "done", job.reason
    cfg_lines = (dest / "ionomos_run" / diann.CFG_NAME).read_text(encoding="utf-8").splitlines()
    raws = [ln[4:] for ln in cfg_lines if ln.startswith("--f ")]
    assert raws and all(Path(r).is_file() for r in raws)
    assert f"--fasta {bed['cfg'].fasta_dir / bed['cfg'].methods['DIA'].fasta}" in cfg_lines
    assert f"--out {dest / 'diann' / 'report.tsv'}" in cfg_lines
    assert "--fasta-search" in cfg_lines and "--matrices" in cfg_lines and "--cut K*,R*" in cfg_lines
    st = _status(dest)
    assert st["run"]["command"] == [str(bed["exe"]), "--cfg", str(dest / "ionomos_run" / diann.CFG_NAME)]
    assert st["run"]["workflow"].endswith(diann.CFG_NAME)
    assert (dest / "diann" / "report.pg_matrix.tsv").is_file() and not (dest / "fragpipe").exists()
    assert "FAKE DIA-NN: done" in (dest / "ionomos_run" / diann.CONSOLE_LOG).read_text(encoding="utf-8")
    # the analysis ran on DIA-NN's matrix and says so
    summary = json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))
    assert summary["engine"]["engine"] == "DIA-NN" and summary["engine"]["version"] == "2.2.0"
    assert summary["comparisons"] and (dest / "results" / "report.html").is_file()
    assert (dest / "DONE.txt").is_file()


def test_library_is_used_instead_of_a_fasta_search(bed):
    lib = bed["cfg"].workflow_dir / "human_lib.parquet"
    lib.write_bytes(b"PAR1")
    cfg = _with_diann(bed["cfg"], bed["exe"], library="human_lib.parquet", diann_args="--var-mods 1 --mass-acc 10")
    dest = _queue({**bed, "cfg": cfg})
    Worker(cfg, bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done", bed["ledger"].get(1).reason
    lines = (dest / "ionomos_run" / diann.CFG_NAME).read_text(encoding="utf-8").splitlines()
    assert f"--lib {lib}" in lines and "--fasta-search" not in lines
    assert "--var-mods 1" in lines and "--mass-acc 10" in lines


def test_missing_library_or_exe_holds_the_job_until_setup_is_fixed(bed, tmp_path):
    cfg = _with_diann(bed["cfg"], bed["exe"], library="nope.speclib")
    _queue({**bed, "cfg": cfg})
    Worker(cfg, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "spectral library 'nope.speclib'" in job.reason
    cfg2 = _with_diann(bed["cfg"], tmp_path / "missing" / "diann.exe")
    Worker(cfg2, bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "queued" and "DIA-NN not found" in job.reason and "diann_exe" in job.reason
    Worker(bed["cfg"], bed["ledger"]).run_once()  # fixed: runs
    assert bed["ledger"].get(1).status == "done"


def test_diann_failure_fails_the_job_and_retry_keeps_the_old_output(bed, monkeypatch):
    dest = _queue(bed)
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "fail")
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(1)
    assert job.status == "failed" and "DIA-NN exited with code 1" in job.reason
    from ionomos import attention

    titles = [i.title for i in attention.items(bed["cfg"].log_dir)]
    assert any(t.startswith("DIA-NN failed on") for t in titles), titles
    assert (dest / "FAILED.txt").is_file()
    (dest / "diann" / "partial.txt").write_text("from the failed attempt", encoding="utf-8")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE")
    bed["ledger"].requeue(1, "retry requested", reset_attempts=True)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    assert bed["ledger"].get(1).status == "done"
    kept = [p for p in dest.iterdir() if p.name.startswith("diann_previous_")]
    assert len(kept) == 1 and (kept[0] / "partial.txt").read_text(encoding="utf-8") == "from the failed attempt"


def test_paths_with_spaces_are_refused_before_starting(bed, tmp_path):
    job = bed["ledger"].get(1) if bed["ledger"].list() else None
    assert job is None
    dest = _queue(bed)
    spaced = tmp_path / "with space"
    spaced.mkdir()
    job = bed["ledger"].get(1)
    raw = dest / job.parsed["plan"]["manifest"][0]["file"]
    job.parsed["plan"]["manifest"][0]["file"] = str(spaced / raw.name)
    (spaced / raw.name).write_bytes(raw.read_bytes())
    with pytest.raises(fragpipe.JobError, match="space"):
        diann.prepare(job, bed["cfg"])


def test_config_accepts_diann_methods_and_rejects_bad_ones(tmp_path):
    import yaml

    base = yaml.safe_load(testbed.init(tmp_path / "b").read_text(encoding="utf-8"))
    base["methods"]["DIA"] = {"engine": "diann", "fasta": "human_reviewed_decoys.fas", "data_type": "DIA",
                              "diann_exe": "C:/DIA-NN/2.2.0/diann.exe"}
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(base), encoding="utf-8")
    cfg = load(p)
    assert diann.uses_diann(cfg, "DIA") and not diann.uses_diann(cfg, "isoDTB")
    assert cfg.methods["DIA"].workflow == ""
    lines = fragpipe.describe_method(cfg, "DIA")
    assert lines[0][0] is False and "DIA-NN not found" in lines[0][1]
    for bad, msg in (({"engine": "sage"}, "engine"), ({"data_type": "DDA"}, "needs data_type DIA")):
        base["methods"]["DIA"] = {**base["methods"]["DIA"], **bad}
        p.write_text(yaml.safe_dump(base), encoding="utf-8")
        with pytest.raises(ConfigError, match=msg):
            load(p)
        base["methods"]["DIA"] = {"engine": "diann", "fasta": "x.fas", "data_type": "DIA"}


def test_fragpipe_methods_are_unchanged(bed):
    """Methods without engine: diann still go to FragPipe (the default)."""
    folder = testbed.drop(bed["root"], "iso_good")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].list()[-1]
    assert job.status == "done", job.reason
    assert "--headless" in _status(Path(job.dest_dir))["run"]["command"]
