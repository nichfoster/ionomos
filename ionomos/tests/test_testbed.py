"""The testbed's scenarios (testbed.SAMPLES) and the fake FragPipe's fidelity to FragPipe 24's source (round 4).

Each scenario added in round 4 is driven through the real intake and worker against the fake: a label-free
search, a label-free phospho search, a DIA in two fractions, an open search (with and without mass-shift
localisation), tools printing non-ASCII text, and a retry that leaves fragpipe_previous_*. The fake's own shapes
are checked against what FragPipe 24's source writes (fake_fragpipe.py's header names each source).
"""
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from ionomos import fake_fragpipe, fragpipe, names, testbed
from ionomos.config import load
from ionomos.intake import intake
from ionomos.ledger import Ledger
from ionomos.worker import Worker


@pytest.fixture
def bed(tmp_path, monkeypatch):
    monkeypatch.setenv("IONOMOS_FAKE_FP_SECONDS", "0")
    monkeypatch.delenv("IONOMOS_FAKE_FP_MODE", raising=False)
    monkeypatch.setattr(fragpipe, "POLL_SECONDS", 0.05)
    cfg_path = testbed.init(tmp_path / "bed")
    cfg = load(cfg_path)
    led = Ledger(cfg.database)
    yield {"root": tmp_path / "bed", "cfg": cfg, "cfg_path": cfg_path, "ledger": led}
    led.close()


def _search(bed, sample: str):
    """Drop a sample, file it, search it once; (job, its folder)."""
    folder = testbed.drop(bed["root"], sample)
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    job = bed["ledger"].list()[-1]
    Worker(bed["cfg"], bed["ledger"]).run_once()
    return bed["ledger"].get(job.id), Path(job.dest_dir)


def _analysis(dest: Path) -> dict:
    return json.loads((dest / "results" / "analysis.json").read_text(encoding="utf-8"))


def _console(dest: Path) -> str:
    return fragpipe.decode_console((dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG).read_bytes())


def _tsv(path: Path) -> list[list[str]]:
    return [ln.split("\t") for ln in path.read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------------------------------------- the list --


def test_testbed_list_documents_every_sample_and_every_mode(tmp_path):
    text = testbed.list_text()
    for name, spec in testbed.SAMPLES.items():
        assert re.search(rf"^  {re.escape(name)} +{re.escape(spec['folder'])} ", text, re.MULTILINE), name
        assert spec["shows"] in text
        if spec.get("mode"):
            assert spec["mode"] in text
    for mode, what in fake_fragpipe.MODES.items():
        assert mode in text and what in text
    out = subprocess.run([sys.executable, "-m", "ionomos", "testbed", "list"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0 and f"{len(testbed.SAMPLES)} samples." in out.stdout and "lfq_good" in out.stdout


def test_every_sample_builds_and_its_mode_file_names_a_known_mode(tmp_path):
    for name, spec in testbed.SAMPLES.items():
        d = testbed.build_sample(tmp_path, name)
        raws = list(d.rglob("*.raw"))
        assert len(raws) == len(spec.get("raws", [])) + len(spec.get("raw_sub", [])) + sum(
            len(v) for v in (spec.get("plex_subs") or {}).values())
        for mode in (spec.get("mode") or "").split(","):
            assert not mode or mode in fake_fragpipe.MODES, (name, mode)
        assert spec.get("mode") is None or (d / names.FAKE_FP_MODE_FILE).is_file()


# --------------------------------------------------------------------------------- the new scenarios --


def test_a_label_free_search_leaves_combined_protein_as_fragpipe_writes_it(bed):
    job, dest = _search(bed, "lfq_good")
    assert job.status == "done", job.reason
    wd = dest / "fragpipe"
    head, *rows = _tsv(wd / "combined_protein.tsv")
    samples = ["DMSO_1", "DMSO_2", "DMSO_3", "Drug_1", "Drug_2", "Drug_3"]
    blocks = ("Spectral Count", "Unique Spectral Count", "Total Spectral Count", "Intensity", "MaxLFQ Intensity")
    assert head[14:-1] == [f"{s} {b}" for b in blocks for s in samples] and head[-1] == "Indistinguishable Proteins"
    assert rows[0][0].startswith("contam_sp|") and all(len(r) == len(head) for r in rows)
    values = [v for r in rows for v in r[head.index("DMSO_1 MaxLFQ Intensity"):-1]]
    assert "0.0" in values and any(re.fullmatch(r"\d\.\d+E\d+", v) for v in values)  # Java's float text
    ann = _tsv(wd / "experiment_annotation.tsv")  # ToolingUtils, type 0: sample = <experiment>_<bioreplicate>
    assert ann[0] == ["file", "sample", "sample_name", "condition", "replicate"]
    assert [r[1:] for r in ann[1:3]] == [["DMSO_1", "DMSO_1", "DMSO", "1"], ["DMSO_2", "DMSO_2", "DMSO", "2"]]
    a = _analysis(dest)
    assert a["state"] == "ok" and [c["name"] for c in a["comparisons"]] == ["Drug vs DMSO"]
    assert (dest / "results" / "report.html").is_file()


def test_a_phospho_search_leaves_the_site_report_and_the_sites_are_analysed(bed):
    job, dest = _search(bed, "phospho_lfq")
    assert job.status == "done", job.reason
    sites = dest / "fragpipe" / "combined_site_STY_79.9663.tsv"
    head, *rows = _tsv(sites)
    assert head[:6] == ["Index", "Gene", "Protein", "Protein ID", "Peptide", "Best Localization Probability"]
    assert "Ctrl_1 Localization Probability" in head and "EGF_3 MaxLFQ Intensity" in head
    assert all(re.fullmatch(r"P\d+_[STY]\d+", r[0]) for r in rows)
    assert (dest / "fragpipe" / "combined_protein.tsv").is_file()
    a = _analysis(dest)
    assert a["state"] == "ok" and a["level"] == "site" and [c["name"] for c in a["comparisons"]] == ["EGF vs Ctrl"]
    assert any("phosphosites:" in n for n in a["notes"])


def test_a_two_fraction_dia_gives_dia_nn_a_run_per_fraction(bed):
    """FragPipe hands DIA-NN every file (filelist_diann.txt: the <raw>_uncalibrated.mzML MSFragger wrote beside
    each raw), so the matrix has a column per fraction: 12 runs for 6 samples."""
    job, dest = _search(bed, "dia_fractions")
    assert job.status == "done", job.reason
    wd = dest / "fragpipe"
    cfg = (wd / "filelist_diann.txt").read_text(encoding="utf-8").splitlines()
    assert len(cfg) == 12 and all(ln.startswith("--f ") and ln.endswith("_uncalibrated.mzML") for ln in cfg)
    assert all(Path(ln[4:]).is_file() for ln in cfg)  # beside the raws, in the experiment folder
    head = (wd / "dia-quant-output" / "report.pg_matrix.tsv").read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert len(head) == 7 + 12 and all(h.endswith("_uncalibrated.mzML") for h in head[7:])
    manifest = _tsv(wd / "fragpipe-files.fp-manifest")
    assert {(r[1], r[2]) for r in manifest} == {(c, str(n)) for c in ("DMSO", "Drug") for n in (1, 2, 3)}
    # the analysis sees the two fractions of a sample as two runs of it, and asks (issue filed: it calls them
    # re-acquisitions, ROADMAP); it never treats them as two replicates silently
    a = _analysis(dest)
    assert a["state"] == "needs_input" and "DUPLICATE_SAMPLES" in {i["code"] for i in a["issues"]}


@pytest.mark.parametrize("sample", ["open_search", "open_search_no_los"])
def test_an_open_search_is_done_and_leaves_its_mass_shifts(bed, sample):
    job, dest = _search(bed, sample)
    assert job.status == "done", job.reason
    wd = dest / "fragpipe"
    head, *rows = _tsv(wd / "HEK_1" / "psm.tsv")
    shifts = {round(float(r[head.index("Delta Mass")])) for r in rows}
    assert {0, 16, 80} <= shifts  # an open search's peaks, in Da
    assert (wd / "ptm-shepherd-output" / "global.profile.tsv").is_file()
    assert not (wd / "combined_protein.tsv").exists() and not (wd / "experiment_annotation.tsv").exists()
    console = _console(dest)
    assert "PTMShepherd [Work dir: " in console and "Crystal-C [Work dir: " in console
    los = "ERROR - Mass-offset or open search with 'Localize mass shift (LOS)` was disabled."
    assert (los in console) == (sample == "open_search_no_los")
    assert "ALL JOBS DONE IN" in console
    # nothing is quantified by design (#181): done, no "analysis had a problem", no attention item
    a = _analysis(dest)
    assert a["state"] == "ok" and "NO_TABLE" not in {i["code"] for i in a["issues"]}
    said = "this search quantifies nothing (an open search): no statistics; PTM-Shepherd's mass shifts are in " \
           "fragpipe/ptm-shepherd-output/"
    assert said in a["notes"] and a["quantification"] == {"ran": False, "reason": said}
    assert "analysis had a problem" not in (job.reason or "") and said in (job.reason or "")
    from ionomos import attention
    assert not [it for it in attention.items(bed["cfg"].log_dir) if it.kind == "analysis_failed"]


def test_tools_printing_non_ascii_text_reach_the_console_in_the_code_page_and_the_log_in_utf8(bed):
    job, dest = _search(bed, "non_ascii_log")
    assert job.status == "done", job.reason
    raw = (dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG).read_bytes()
    assert "Präzisionsmasse".encode("cp1252") in raw and "? 日本".encode() not in raw
    assert b"\xe2\x86\x92" not in raw and "Größe ok ? ?????????".encode("cp1252") in raw  # cp1252 lacks them: '?'
    text = _console(dest)
    assert "Präzisionsmasse außerhalb ±20 ppm für 5 µL" in text and "Müller (µg)" in text
    log = next((dest / "fragpipe").glob("log_*.txt")).read_text(encoding="utf-8")  # FragPipe's own: UTF-8
    assert "→ 日本語のファイル名" in log and "ALL JOBS DONE IN" in log
    fp = json.loads((dest / fragpipe.RUN_DIR / names.FINGERPRINT_FILE).read_text(encoding="utf-8"))
    assert fp["console"]["facts"]["all_jobs_done_minutes"] is not None


def test_a_retry_keeps_the_failed_attempt_beside_the_new_output(bed):
    job, dest = _search(bed, "retry_previous")
    assert job.status == "failed" and "step IonQuant failed (exit code 1)" in job.reason
    assert (dest / "FAILED.txt").is_file()
    bed["ledger"].requeue(job.id, "retry requested", reset_attempts=True)
    Worker(bed["cfg"], bed["ledger"]).run_once()
    job = bed["ledger"].get(job.id)
    assert job.status == "done" and "fragpipe_previous_" in job.reason, job.reason
    prev = list(dest.glob("fragpipe_previous_*"))
    assert len(prev) == 1 and (prev[0] / "combined_protein.tsv").read_text(encoding="utf-8") == "Protein\tProtein ID\n"
    assert list(prev[0].glob("log_*.txt"))  # FragPipe saved the failed attempt's console there
    a = _analysis(dest)  # from the new output only, never the half-written table of the first attempt
    assert a["state"] == "ok" and "_previous_" not in json.dumps(a.get("input") or "")


# -------------------------------------------------------------------------- the fake against the source --


def test_dia_runs_as_fragpipe_24_runs_dia_nn_2(bed):
    job, dest = _search(bed, "dia_good")
    assert job.status == "done", job.reason
    console = _console(dest)
    assert "DIA-NN version 2.3.2" in console and "DIA-Quant convert Parquet to Tsv [Work dir: " in console
    assert ("DIA-Quant plot DIA-NN output" in console) == (os.name == "nt")  # CmdDiann: Windows only
    cmd = next(ln for ln in console.splitlines() if " --cfg " in ln)
    assert cmd.rstrip().endswith("filelist_diann.txt" if os.name == "nt" else "filelist_diann.txt--")
    stats = _tsv(dest / "fragpipe" / "dia-quant-output" / "report.stats.tsv")
    assert all(r[0].endswith("_uncalibrated.mzML") for r in stats[1:])
    assert sorted(p.name for p in (dest / "raw").glob("*_uncalibrated.mzML")) == [
        f"{c}_{r}_uncalibrated.mzML" for c in ("DMSO", "Drug") for r in (1, 2, 3)]
    a = _analysis(dest)  # the _uncalibrated run names are matched back to the manifest (REAL_RUNS.md job 1)
    assert a["state"] == "ok" and [c["name"] for c in a["comparisons"]] == ["Drug vs DMSO"]
    assert (dest / "fragpipe" / "psm.tsv").is_file()  # Philosopher's report in the one flat folder


def test_the_tmt_sample_sheet_reads_names_fragpipes_way(bed):
    """ToolingUtils.writeIsobaricQuantExperimentAnnotation: DMSO_1_126 is condition "1", replicate 126 (its last
    part is a number); DMSO_1_127N is condition "127N", replicate 1. Ionomos reads its own conditions, never this."""
    job, dest = _search(bed, "tmt_good")
    assert job.status == "done", job.reason
    rows = {r[2]: r for r in _tsv(dest / "fragpipe" / "experiment_annotation.tsv")[1:]}
    assert rows["DMSO_1_126"][4:] == ["1", "126"] and rows["DMSO_1_127N"][4:] == ["127N", "1"]
    assert "NA" not in rows
    assert [c["name"] for c in _analysis(dest)["comparisons"]] == ["Drug vs DMSO"]


def test_files_fragpipe_writes_before_the_first_step(bed, monkeypatch):
    """prepareWd makes the folders and the configure() steps write the file lists and the sample sheet before
    anything runs: a run killed in MSFragger leaves them."""
    monkeypatch.setenv("IONOMOS_FAKE_FP_MODE", "killed")
    job, dest = _search(bed, "iso_good")
    assert job.status == "failed"
    wd = dest / "fragpipe"
    for f in ("fragpipe.workflow", "fragpipe-files.fp-manifest", "fragpipe.job", "fragger.params",
              "filelist_ionquant.txt", "filelist_proteinprophet.txt", "modmasses_ionquant.txt",
              "experiment_annotation.tsv"):
        assert (wd / f).is_file(), f
    assert sorted(p.name for p in wd.iterdir() if p.is_dir()) == [f"EJQ_PK_EJQ_2_027_isoDTB_1uM_3h_{r}" for r in (1, 2, 3)]
    assert not list(wd.glob("log_*.txt"))  # killed: no shutdown hook, no log
    ionquant = _tsv(wd / "filelist_ionquant.txt")
    assert ionquant[0] == ["flag", "value"] and [r[0] for r in ionquant[1:]] == ["--psm"] * 3 + ["--specdir"]


def test_fragpipe_job_and_log_as_fragpipe_writes_them(bed, monkeypatch):
    job, dest = _search(bed, "iso_good")
    wd = dest / "fragpipe"
    head, row = _tsv(wd / "fragpipe.job")
    assert head == ["# Job name (optional)", "Workflow file path", "Manifest file path", "Output directory",
                    "Tools folder path (optional)", "Fasta path (optional)", "RAM (optional)", "Threads (optional)"]
    assert row[0] == "runtime-save" and row[1].endswith("fragpipe-workflow.workflow") and Path(row[3]) == wd.resolve()
    log = next(wd.glob("log_*.txt")).read_text(encoding="utf-8")
    assert log.startswith("System OS: ") and "Version info:" in log and "ALL JOBS DONE IN" in log
    assert " ERROR - " not in log and "\r" not in log  # logback's lines never reach the console's text
    # a second log in the same second gets _1 (TabRun.saveLogToFile, CREATE_NEW), and nothing is written over
    monkeypatch.setattr(fake_fragpipe, "time", types.SimpleNamespace(strftime=lambda _f: "2026-01-01_00-00-00"))
    monkeypatch.setattr(fake_fragpipe, "_CONSOLE", ["first\n"])
    first = fake_fragpipe._save_log(wd)
    monkeypatch.setattr(fake_fragpipe, "_CONSOLE", ["second\n"])
    second = fake_fragpipe._save_log(wd)
    assert (first.name, second.name) == ("log_2026-01-01_00-00-00.txt", "log_2026-01-01_00-00-00.txt_1")
    assert first.read_text(encoding="utf-8") == "first\n" and second.read_text(encoding="utf-8") == "second\n"
    assert fake_fragpipe._save_log(wd / "not-there") is None and not (wd / "not-there").exists()


def test_sdrf_as_fragpipe_writes_it(bed):
    wf = bed["cfg"].workflow_dir / "TMT10-MS3.workflow"
    wf.write_text(wf.read_text(encoding="utf-8").replace("workflow.misc.save-sdrf=false", "workflow.misc.save-sdrf=true"),
                  encoding="utf-8")
    fa = bed["cfg"].fasta_dir / "human_reviewed_decoys.fas"
    fa.write_text(fa.read_text(encoding="utf-8").replace("fake protein 1", "fake protein 1 OS=Homo sapiens OX=9606"),
                  encoding="utf-8")
    job, dest = _search(bed, "tmt_good")
    head, *rows = _tsv(dest / "fragpipe" / "sdrf.tsv")
    assert head[:2] == ["source name", "characteristics[organism]"]
    mods = [i for i, h in enumerate(head) if h == "comment[modification parameters]"]
    assert len(mods) == 5 and len(rows) == 4 * 10  # 4 files x 10 channels
    r = rows[0]
    assert r[1] == "Homo sapiens" and r[head.index("comment[label]")] == "TMT126"
    assert r[head.index("comment[precursor mass tolerance]")] == "40.0 ppm"
    assert r[head.index("comment[cleavage agent details]")] == "NT=Trypsin/P;CS=(?<=[KR])"
    assert "NT=Carbamidomethyl;MT=Fixed;PP=Anywhere;TA=C;MM=57.02146" in [r[i] for i in mods]
    assert "NT=Acetyl;MT=Variable;PP=Protein N-term;MM=42.01060" in [r[i] for i in mods]


def _launcher(bed, *args: str, env: dict | None = None) -> tuple[int, bytes]:
    out = bed["root"] / "fake_out.bin"
    with open(out, "wb") as fh:
        code = subprocess.run([str(bed["cfg"].fragpipe_exe), *args], stdout=fh, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})}, timeout=120).returncode
    return code, out.read_bytes()


def test_the_console_is_the_code_page_with_javas_line_ends(bed):
    _job, dest = _search(bed, "iso_good")
    raw = (dest / fragpipe.RUN_DIR / fragpipe.CONSOLE_LOG).read_bytes()
    assert "mass spectrometry–based".encode("cp1252") in raw  # the citation's en dash: 0x96, not UTF-8
    eol = b"\r\n" if os.name == "nt" else b"\n"
    assert b"Version info:\nFragPipe version 24.0\n" in raw and b"Numpy version 1.26.4\n" + eol in raw
    code, text = _launcher(bed, "--help")
    assert code == 1 and text.startswith(b"FragPipe v24.0\n(c) University")  # print(), the help's own "\n"


@pytest.mark.parametrize(("args", "said"), [
    (["--headless", "--ram", "lots"], 'java.lang.NumberFormatException: For input string: "lots"'),
    (["--headless", "--threads", "4.5"], 'java.lang.NumberFormatException: For input string: "4.5"'),
    (["--headless", "--ram", "99999999999"], 'For input string: "99999999999"'),
    (["--headless", "--workdir"], "java.lang.ArrayIndexOutOfBoundsException: Index 2 out of bounds for length 2"),
    (["--headless", "--ram", "-1", "--workflow", "x", "--manifest", "y"], "Please provide --workflow"),
])
def test_option_values_java_refuses(bed, args, said):
    """FragPipeMain parses --ram / --threads with Integer.parseInt as it reads them (before any other check)
    and reads an option's value with args[++i]."""
    code, text = _launcher(bed, *args)
    assert code == 1 and said in text.decode("cp1252")


def test_a_check_that_fails_after_the_folders_exist_leaves_an_empty_log(bed):
    """FASTA missing: prepareWd already made the output and group folders; System.exit(1) runs the shutdown
    hook, which saves the console (empty: the ERROR line is logback's) as log_<date>.txt."""
    folder = testbed.drop(bed["root"], "iso_good")
    assert intake(folder, bed["cfg"], bed["ledger"]).value == "queued"
    spec = fragpipe.prepare(bed["ledger"].list()[-1], bed["cfg"])
    fragpipe.write_inputs(spec)
    wf = spec.workflow.read_text(encoding="utf-8")
    spec.workflow.write_text(re.sub(r"database\.db-path=.*", "database.db-path=C:/nowhere/x.fas", wf), encoding="utf-8")
    code, text = _launcher(bed, *spec.command()[1:])
    assert code == 1 and b"ERROR - Could not find fasta file at: C:/nowhere/x.fas" in text
    assert [p.stat().st_size for p in spec.workdir.glob("log_*.txt")] == [0]
    assert len([p for p in spec.workdir.iterdir() if p.is_dir()]) == 3


def test_the_execution_order_names_each_command_once_with_the_output_folder(bed):
    _job, dest = _search(bed, "iso_good")
    console = _console(dest)
    order = re.findall(r"^    Cmd: \[([^\]]+)\], Work dir: \[([^\]]+)\]", console, re.MULTILINE)
    assert order[0][0] == "START" and len({n for n, _w in order}) == len(order)
    assert {Path(w) for _n, w in order} == {(dest / "fragpipe").resolve()}


@pytest.mark.parametrize(("value", "want"), [(0.0, "0.0"), (123456.7, "123456.7"), (100.0, "100.0"),
                                             (16315267.0, "1.6315267E7"), (1e7, "1.0E7"), (2.5e-4, "2.5E-4")])
def test_intensities_are_written_as_java_writes_floats(value, want):
    assert fake_fragpipe._java_float(value) == want


@pytest.mark.parametrize(("share", "want"), [(20.0, "20%"), (33.3333, "33.3%"), (66.66, "66.7%"), (5.04, "5%")])
def test_the_decoy_share_is_written_as_decimalformat_does(share, want):
    assert fake_fragpipe._java_pct(share) == want
