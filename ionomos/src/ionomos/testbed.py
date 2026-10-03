"""
Testbed — a fake lab you can build anywhere (Mac, Windows) to try the whole
tool without a real FragPipe or real raw files.

    ionomos testbed init [DIR]         build DIR (default ./ionomos-testbed): Fragpipe_Auto/,
                                        Fragpipe_General/, config.yaml, samples/, fake FragPipe
    ionomos testbed list [DIR]         the sample drops and what each one exercises
    ionomos testbed drop NAME [DIR]    copy samples/NAME into the inbox (--slow = file by file,
                                        like a real drag from a USB drive)
    ionomos testbed reset [DIR]        empty inbox, user folders, ledger, logs; keep samples
    ionomos testbed gui-demo           open the resolver window with sample data (no drop needed)

Then, in another terminal:   ionomos --config DIR/Fragpipe_Auto/config.yaml run

Sample .raw files are a few KB of random bytes — ionomos never reads raw
contents, only names and sizes.
"""
from __future__ import annotations

import os
import random
import shutil
import stat
import time
from pathlib import Path

import yaml


def default_root() -> Path:
    """Where the testbed goes when no DIR is given.

    Windows: C:/ionomos-testbed, never under the profile folder (usernames often
    contain spaces, and the config loader rejects spaces on Windows). Elsewhere:
    ./ionomos-testbed.
    """
    if os.name == "nt":
        return Path("C:/ionomos-testbed")
    return Path("ionomos-testbed")


DEFAULT_DIR = str(default_root())
USERS = ["EJQ", "Isaac", "Chris", "Aman", "Taylor_Elements"]


def _iso(prefix: str, reps=(1, 2, 3), fracs=(1, 2, 3)) -> list[str]:
    return [f"{prefix}_{r}_{f}.raw" for r in reps for f in fracs]


_TMT10 = ("126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N")

# name -> (folder name, files at top level, files in raw/, files in plex folders, other files, experiment.yaml
# dict|None, the fake FragPipe's fault mode for it, what it shows)
SAMPLES: dict[str, dict] = {
    "fp_fail": dict(
        folder="20260910_Chris_DIA_crash-test_FAKEFAIL",
        raws=["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"],
        shows="filed fine, then the fake FragPipe fails on purpose -> job failed, FAILED.txt; Retry re-runs it",
    ),
    "iso_good": dict(
        folder="20260902-isoDTB_EJQ-2-027",
        raws=_iso("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"), others=["EJQ-2-027_notes.xlsx"],
        shows="clean isoDTB drop: 3 reps x 3 fractions, user EJQ, date from name -> queued",
    ),
    "iso_spaces": dict(
        folder="20260902 isoDTB EJQ-2-027 (1uM 3h)",
        raws=_iso("EJQ PK isoDTB 1uM 3h", reps=(1, 2), fracs=(1, 2)),
        shows="spaces/parentheses in folder AND file names -> sanitised, queued",
    ),
    "dia_good": dict(
        folder="20260914_Isaac_DIA_FLAG-AR-pulldown",
        raw_sub=["DMSO_1.raw", "DMSO_2.raw", "DMSO_3.raw", "Drug_1.raw", "Drug_2.raw", "Drug_3.raw"],
        shows="DIA with raws in a raw/ subfolder: two conditions x 3 bioreps -> queued",
    ),
    "tmt_good": dict(
        folder="20260126_Aman_TMT_KL6159A-9plex",
        raws=[f"KL6159A_TMT_F{i}.raw" for i in range(1, 5)],
        # every channel of the label type is listed (FragPipe refuses a shorter list); NA = not used
        yaml={"tmt": {"tag": "TMT-10", "channels": {"126": "DMSO_1_126", "127N": "DMSO_1_127N", "127C": "DMSO_1_127C",
                                                       "128N": "Drug_1_128N", "128C": "Drug_1_128C",
                                                       "129N": "Drug_1_129N", "129C": "NA", "130N": "NA",
                                                       "130C": "NA", "131N": "NA"}}},
        shows="TMT, 4 fractions, biorep 1, channel map in experiment.yaml -> queued",
    ),
    "glued_initials": dict(
        folder="IJD05_isoDTB_FLAGpull",
        raws=_iso("IJD05_FLAG", reps=(1, 2), fracs=(1, 2)),
        shows="initials glued to an ID (IJD05) resolve via alias IJD -> Isaac -> queued",
    ),
    "method_in_files": dict(
        folder="EJQ_2027_pulldown",
        raws=_iso("EJQ_isoDTB_2027", reps=(1,), fracs=(1, 2)),
        shows="no method in folder name, but the raw file names say isoDTB -> queued",
    ),
    "gui_unknown_user": dict(
        folder="XYZ99_isoDTB_run",
        raws=_iso("XYZ99", reps=(1, 2), fracs=(1, 2)),
        shows="unknown initials -> resolver window (pick user, tick 'remember XYZ')",
    ),
    "gui_no_method": dict(
        folder="EJQ_2027_mystery",
        raws=["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"],
        shows="no method anywhere -> resolver window (pick DIA; files re-parse as cond_biorep)",
    ),
    "gui_bad_tail": dict(
        folder="EJQ_isoDTB_odd-names",
        raws=["Sample_1_1.raw", "Sample_1_2.raw", "Sample_extra.raw"],
        shows="one file has no rep/fraction tail -> resolver window (type its rep/frac)",
    ),
    "gui_incomplete": dict(
        folder="EJQ_isoDTB_partial-copy",
        raws=_iso("EJQ_partial")[:-1],
        shows="rep 3 is missing fraction 3 -> resolver window ('accept uneven' or skip)",
    ),
    "reject_no_raws": dict(
        folder="EJQ_isoDTB_empty",
        raws=[], others=["notes.txt"],
        shows="no .raw files -> watcher waits forever (min_raw_files); nothing happens",
    ),
    "reject_two_methods": dict(
        folder="EJQ_isoDTB_then_TMT",
        raws=["S_1_1.raw"],
        shows="two method keywords -> resolver window (pick one)",
    ),
    # D69: TMT plexes as a drop can hold them, and FragPipe faults the fake acts out per experiment
    "tmt_plexes": dict(
        folder="20260127_Aman_TMT_KL6160-2plex",
        plex_subs={"plexA": ["KL6160A_TMT_F1.raw", "KL6160A_TMT_F2.raw"],
                   "plexB": ["KL6160B_TMT_F1.raw", "KL6160B_TMT_F2.raw"]},
        yaml={"tmt": {"tag": "TMT-10", "plexes": {
            p: {"channels": {ch: (f"{c}_{p}_{ch}" if c else "NA") for ch, (c, r) in zip(_TMT10, (
                ("DMSO", 1), ("DMSO", 2), ("Drug", 1), ("Drug", 2), ("", 0), ("", 0), ("", 0), ("", 0), ("", 0),
                ("", 0)), strict=True)}} for p in ("plexA", "plexB")}}},
        shows="two TMT plexes, each in its own folder (<plex>/*.raw) -> kept as dropped, one annotation.txt per plex",
    ),
    "tmt_flat_plexes": dict(
        folder="20260128_Aman_TMT_KL6161-flat",
        raws=["KL6161A_TMT_F1.raw", "KL6161A_TMT_F2.raw", "KL6161B_TMT_F1.raw", "KL6161B_TMT_F2.raw"],
        shows="two TMT plexes in one folder -> filed as dropped, with a warning: FragPipe names the channels itself",
    ),
    "fp_cut_table": dict(
        folder="20260911_EJQ_isoDTB_cut-table",
        raws=_iso("EJQ_cut", reps=(1, 2), fracs=(1,)), mode="truncated-table",
        shows="the fake FragPipe leaves its main table cut off mid-row -> failed, 'not written to the end'",
    ),
    "fp_hang": dict(
        folder="20260912_EJQ_isoDTB_hang",
        raws=_iso("EJQ_hang", reps=(1, 2), fracs=(1,)), mode="hang,child",
        shows="the fake FragPipe hangs in MSFragger -> killed with everything it started at the time limit",
    ),
}


def _write_raw(p: Path, size: int) -> None:
    p.write_bytes(random.randbytes(size))


def build_sample(dest_parent: Path, name: str, size: int = 4096) -> Path:
    spec = SAMPLES[name]
    d = dest_parent / spec["folder"]
    d.mkdir(parents=True, exist_ok=True)
    for f in spec.get("raws", []):
        _write_raw(d / f, size)
    if spec.get("raw_sub"):
        (d / "raw").mkdir(exist_ok=True)
        for f in spec["raw_sub"]:
            _write_raw(d / "raw" / f, size)
    for sub, files in (spec.get("plex_subs") or {}).items():
        (d / sub).mkdir(exist_ok=True)
        for f in files:
            _write_raw(d / sub / f, size)
    for f in spec.get("others", []):
        (d / f).write_text("just a note\n", encoding="utf-8")
    if spec.get("mode"):
        from ionomos.names import FAKE_FP_MODE_FILE

        (d / FAKE_FP_MODE_FILE).write_text(spec["mode"] + "\n", encoding="utf-8")
    if spec.get("yaml"):
        (d / "experiment.yaml").write_text(yaml.safe_dump(spec["yaml"]), encoding="utf-8")
    return d


# ---------------------------------------------------------------------- init --

def fake_fragpipe(argv: list[str]) -> int:
    """Fake FragPipe (`ionomos fake-fragpipe ...`): FragPipe's headless options, checks, console output and
    output files, without a search. See fake_fragpipe.py for what is copied from the real one and from where."""
    from ionomos import fake_fragpipe as fake

    return fake.fake_fragpipe(argv)


def _fake_results(wd: Path, rows: list[list[str]], workflow_name: str, annotations: dict | None = None,
                  label: str = "TMT-10") -> None:
    """Realistic result tables with planted hits (ionomos.downstream.simulate), so reports have content.

    rows: the manifest as FragPipe read it (experiment names already FragPipe's). annotations (TMT):
    {plex: its annotation file or None}; a plex without one gets FragPipe's own names, <plex>_<channel>."""
    import zlib

    from ionomos.downstream import simulate
    from ionomos.fake_fragpipe import TMT_CHANNELS

    seed = zlib.crc32("".join(r[0] for r in rows).encode())
    if any(len(r) > 3 and r[3] == "DIA" for r in rows):
        out = wd / "dia-quant-output"  # FragPipe 24's folder (CmdDiann); earlier versions wrote diann-output
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.tsv").write_text("Run\tProtein.Group\tPrecursor.Quantity\n", encoding="utf-8")
        simulate.dia_pg_matrix(out / "report.pg_matrix.tsv", [(r[0], r[1]) for r in rows], seed)
        fake_diann_stats(out / "report.stats.tsv", [r[0] for r in rows])
    elif annotations is not None or "tmt" in workflow_name.lower():
        names: list[str] = []
        for exp in dict.fromkeys(r[1] for r in rows):
            ann = (annotations or {}).get(exp)
            if ann is not None and Path(ann).is_file():
                pairs = [ln.split() for ln in Path(ann).read_text(encoding="utf-8").splitlines() if ln.strip()]
                names += [p[1] for p in pairs if len(p) == 2 and p[1].lower() != "na"]
            else:
                names += [f"{exp}_{ch}" for ch in TMT_CHANNELS.get(label, TMT_CHANNELS["TMT-10"])]
        simulate.tmt_abundance(wd / "tmt-report" / "abundance_gene_MD.tsv", names, seed)
        for name, raws in _groups(rows).items():
            fake_psm(wd / name / "psm.tsv", raws)
    else:
        exps: dict[str, list[int]] = {}
        for r in rows:
            exps.setdefault(r[1], [])
            if int(r[2]) not in exps[r[1]]:
                exps[r[1]].append(int(r[2]))
        simulate.isodtb_label_quant(wd / "combined_modified_peptide_label_quant.tsv",
                                    {e: sorted(v) for e, v in exps.items()}, seed)
        for name, raws in _groups(rows).items():
            fake_psm(wd / name / "psm.tsv", raws)


def _groups(rows: list[list[str]]) -> dict[str, list[str]]:
    """FragPipe writes one psm.tsv per <experiment>_<bioreplicate> folder."""
    groups: dict[str, list[str]] = {}
    for r in rows:
        groups.setdefault(f"{r[1]}_{r[2]}", []).append(r[0])
    return groups


DIANN_STATS_HEADER = ["File.Name", "Precursors.Identified", "Proteins.Identified", "Total.Quantity", "MS1.Signal",
                      "MS2.Signal", "FWHM.Scans", "FWHM.RT", "Median.Mass.Acc.MS1", "Median.Mass.Acc.MS1.Corrected",
                      "Median.Mass.Acc.MS2", "Median.Mass.Acc.MS2.Corrected", "MS2.Mass.Instability",
                      "Normalisation.Instability", "Median.RT.Prediction.Acc", "Average.Peptide.Length",
                      "Average.Peptide.Charge", "Average.Missed.Tryptic.Cleavages"]
PSM_HEADER = ["Spectrum", "Spectrum File", "Peptide", "Modified Peptide", "Peptide Length", "Charge", "Retention",
              "Observed Mass", "Calibrated Observed Mass", "Observed M/Z", "Calibrated Observed M/Z",
              "Calculated Peptide Mass", "Calculated M/Z", "Delta Mass", "Expectation", "Hyperscore",
              "Probability", "Number of Enzymatic Termini", "Number of Missed Cleavages", "Intensity",
              "Assigned Modifications", "Is Unique", "Protein", "Protein ID", "Entry Name", "Gene"]


def _qc_rng(name: str) -> tuple[random.Random, float]:
    """Per-run noise, and a quality factor: a run whose name contains QCBAD is a bad injection (45 % fewer IDs)."""
    import zlib

    return random.Random(zlib.crc32(Path(name).name.encode())), (0.55 if "QCBAD" in name.upper() else 1.0)


def fake_diann_stats(path: Path, raws: list[str]) -> None:
    """A DIA-NN report.stats.tsv with DIA-NN's columns and plausible HeLa-like numbers, one row per run."""
    lines = ["\t".join(DIANN_STATS_HEADER)]
    for raw in raws:
        rng, q = _qc_rng(raw)
        prec = int(42000 * q * rng.gauss(1, 0.015))
        vals = [raw, prec, int(6200 * q * rng.gauss(1, 0.01)), f"{2.1e10 * q * rng.gauss(1, 0.04):.6g}",
                f"{8.0e9 * q:.6g}", f"{1.3e10 * q:.6g}", f"{rng.gauss(6.1, 0.1) / q:.3f}",
                f"{rng.gauss(0.152, 0.003) / q:.5f}", f"{rng.gauss(1.6, 0.15):.4f}", f"{rng.gauss(0.2, 0.05):.4f}",
                f"{rng.gauss(3.1, 0.2):.4f}", f"{rng.gauss(0.4, 0.05):.4f}", "0.02", "0.01", "0.03", "10.8",
                f"{rng.gauss(2.45, 0.01):.4f}", f"{rng.gauss(0.11, 0.004):.4f}"]
        lines.append("\t".join(str(v) for v in vals))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fake_psm(path: Path, raws: list[str], n_psms: int = 240) -> None:
    """A FragPipe psm.tsv with its real columns: n_psms PSMs per run from a fixed pool of peptides (stable RTs,
    ~2 ppm precursor error, ~10 % missed cleavages), so the instrument QC trend has DDA runs to read."""
    pool = random.Random(7)
    aa = "ACDEFGHIKLMNPQRSTVWY"
    peptides = ["".join(pool.choice(aa) for _ in range(pool.randint(7, 18))) + pool.choice("KR") for _ in range(160)]
    base_rt = {p: pool.uniform(600, 5400) for p in peptides}
    lines = ["\t".join(PSM_HEADER)]
    for raw in raws:
        rng, q = _qc_rng(raw)
        stem = Path(raw.replace("\\", "/")).stem
        for k in range(int(n_psms * q)):
            pep = peptides[k % len(peptides)]
            z = 2 if rng.random() < 0.62 else 3
            calc = 110.0 * len(pep) + 18.0106
            obs = calc * (1 + rng.gauss(2.0, 1.5) * 1e-6)
            rt = base_rt[pep] + rng.gauss(0, 6)
            mc = 1 if rng.random() < 0.1 else 0
            scan = f"{k + 1:05d}"
            lines.append("\t".join(str(v) for v in (
                f"{stem}.{scan}.{scan}.{z}", f"interact-{stem}.pep.xml", pep, "", len(pep), z, f"{rt:.3f}",
                f"{obs:.5f}", f"{obs:.5f}", f"{(obs + z * 1.00728) / z:.5f}", f"{(obs + z * 1.00728) / z:.5f}",
                f"{calc:.5f}", f"{(calc + z * 1.00728) / z:.5f}", f"{obs - calc:.5f}", "1e-5", "35", "0.999", 2, mc,
                f"{rng.lognormvariate(16, 1) * q:.1f}", "", "true", f"sp|P{k % 90:05d}|PROT{k % 90}_HUMAN",
                f"P{k % 90:05d}", f"PROT{k % 90}_HUMAN", f"GENE{k % 90}")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fake_diann(argv: list[str]) -> int:
    """Fake DIA-NN (`ionomos fake-diann --cfg ionomos_run/diann.cfg`): reads the cfg the runner writes, fails
    like the real one on a missing raw / FASTA / library, then writes report.pg_matrix.tsv, report.stats.tsv
    and report.log.txt.
    IONOMOS_FAKE_FP_MODE=fail exits 1; IONOMOS_FAKE_FP_SECONDS sets the run time (default 2)."""
    import re as _re

    from ionomos.downstream import simulate

    say = lambda *x: print("FAKE DIA-NN:", *x, flush=True)  # noqa: E731
    if "--cfg" not in argv or argv.index("--cfg") + 1 >= len(argv):
        say("usage: --cfg FILE")
        return 2
    opts: dict[str, list[str]] = {}
    for line in Path(argv[argv.index("--cfg") + 1]).read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 1)
        if parts[0].startswith("--"):
            opts.setdefault(parts[0], []).append(parts[1] if len(parts) > 1 else "")
    for flag in ("--f", "--fasta", "--lib"):
        for f in opts.get(flag, []):
            if not Path(f).is_file():
                say(f"ERROR: cannot open {f}")
                return 1
    if not opts.get("--f") or not opts.get("--out"):
        say("ERROR: no input files or no --out")
        return 1
    if os.environ.get("IONOMOS_FAKE_FP_MODE") == "fail":
        say("ERROR: fake failure")
        return 1
    total = float(os.environ.get("IONOMOS_FAKE_FP_SECONDS", "2"))
    print("DIA-NN 2.2.0 Academia (Data-Independent Acquisition by Neural Networks)", flush=True)
    for step in ("Loading spectral library", "Processing runs", "Protein inference", "Writing report"):
        print(f"[0:0{int(total)}] {step}", flush=True)
        time.sleep(total / 4)
    out = Path(opts["--out"][0])
    out.parent.mkdir(parents=True, exist_ok=True)
    runs = []
    for f in opts["--f"]:
        stem = Path(f).stem
        m = _re.match(r"^(.*?)[_-]?[A-Za-z]?\d+$", stem)
        runs.append((f, m.group(1) if m and m.group(1) else stem))
    stem = out.name.rsplit(".", 1)[0]
    simulate.dia_pg_matrix(out.parent / f"{stem}.pg_matrix.tsv", runs, seed=len(runs))
    fake_diann_stats(out.parent / f"{stem}.stats.tsv", opts["--f"])
    (out.parent / f"{stem}.log.txt").write_text("DIA-NN 2.2.0 Academia (Data-Independent Acquisition by Neural "
                                                "Networks)\nfake run by the Ionomos testbed\n", encoding="utf-8")
    say("done")
    return 0


def write_fake_diann(folder: Path) -> Path:
    """diann.bat / diann.sh in `folder` that runs `ionomos fake-diann` (for `engine: diann` methods in tests)."""
    from ionomos.service import ionomos_command

    cmd = ionomos_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / "diann.bat"
        exe.write_text("@echo off\r\n" + " ".join(f'"{c}"' for c in cmd) + " fake-diann %*\r\n", encoding="utf-8")
    else:
        exe = folder / "diann.sh"
        exe.write_text("#!/bin/sh\nexec " + " ".join(f"'{c}'" for c in cmd) + ' fake-diann "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


FAKE_MQPAR = """<?xml version="1.0" encoding="utf-8"?>
<MaxQuantParams xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
   <fastaFiles>
      <FastaFileInfo>
         <fastaFilePath></fastaFilePath>
         <identifierParseRule>&gt;.*\\|(.*)\\|</identifierParseRule>
         <descriptionParseRule>&gt;(.*)</descriptionParseRule>
         <taxonomyParseRule></taxonomyParseRule>
         <variationParseRule></variationParseRule>
         <modificationParseRule></modificationParseRule>
         <taxonomyId></taxonomyId>
      </FastaFileInfo>
   </fastaFiles>
   <fixedCombinedFolder></fixedCombinedFolder>
   <numThreads>1</numThreads>
   <maxQuantVersion>2.6.7.0</maxQuantVersion>
   <filePaths />
   <experiments />
   <fractions />
   <ptms />
   <paramGroupIndices />
   <referenceChannel />
   <parameterGroups>
      <parameterGroup>
         <lfqMode>0</lfqMode>
         <enzymes><string>Trypsin/P</string></enzymes>
      </parameterGroup>
   </parameterGroups>
</MaxQuantParams>
"""


def fake_maxquant(argv: list[str]) -> int:
    """Fake MaxQuantCmd (`ionomos fake-maxquant`): `--create FILE` writes a template like the real one;
    `MQPAR` checks the FASTA and raw files exist, then writes combined/txt/proteinGroups.txt (LFQ intensity
    per experiment, planted changes) and parameters.txt into fixedCombinedFolder.
    IONOMOS_FAKE_FP_MODE=fail exits 1."""
    import tempfile
    from xml.etree import ElementTree as ET

    from ionomos.downstream import simulate

    say = lambda *x: print("FAKE MaxQuant:", *x, flush=True)  # noqa: E731
    if argv[:1] == ["--create"] and len(argv) > 1:
        Path(argv[1]).write_text(FAKE_MQPAR, encoding="utf-8")
        say(f"template written to {argv[1]}")
        return 0
    if not argv or not Path(argv[-1]).is_file():
        say("usage: MaxQuantCmd mqpar.xml | --create mqpar.xml")
        return 2
    root = ET.parse(argv[-1]).getroot()
    fasta = root.findtext("fastaFiles/FastaFileInfo/fastaFilePath") or ""
    files = [e.text or "" for e in root.findall("filePaths/string")]
    exps = [e.text or "" for e in root.findall("experiments/string")]
    out = Path(root.findtext("fixedCombinedFolder") or ".")
    if not Path(fasta).is_file():
        say(f"ERROR: FASTA file not found: {fasta}")
        return 1
    for f in files:
        if not Path(f).is_file():
            say(f"ERROR: raw file not found: {f}")
            return 1
    if not files or len(exps) != len(files) or len(root.findall("fractions/short")) != len(files):
        say("ERROR: filePaths / experiments / fractions differ in length")
        return 1
    if os.environ.get("IONOMOS_FAKE_FP_MODE") == "fail":
        say("ERROR: fake failure")
        return 1
    for step in ("Configuring", "Feature detection", "MS/MS search", "Protein assembly", "LFQ", "Writing tables"):
        print(f"{step}...", flush=True)
    samples = list(dict.fromkeys(exps))
    with tempfile.TemporaryDirectory() as td:
        pg = Path(td) / "pg.tsv"
        simulate.dia_pg_matrix(pg, [(x, x.rsplit("_", 1)[0]) for x in samples], seed=len(samples), n_proteins=300)
        head, *rows = [ln.split("\t") for ln in pg.read_text(encoding="utf-8").splitlines()]
    txt = out / "combined" / "txt"
    txt.mkdir(parents=True, exist_ok=True)
    cols = ["Protein IDs", "Majority protein IDs", "Gene names", "Protein names", "Peptides",
            "Razor + unique peptides", "Reverse", "Potential contaminant", "Only identified by site",
            *[f"Intensity {x}" for x in samples], *[f"LFQ intensity {x}" for x in samples]]
    body = []
    for r in rows:
        vals = [v or "0" for v in r[7:]]
        body.append([r[0], r[0], r[3], r[4], r[5], r[5], "", "", "", *vals, *vals])
    (txt / "proteinGroups.txt").write_text("\t".join(cols) + "\n" + "\n".join("\t".join(b) for b in body) + "\n",
                                           encoding="utf-8")
    (txt / "parameters.txt").write_text("Parameter\tValue\nVersion\t2.6.7.0\nProtein FDR\t0.01\n", encoding="utf-8")
    say("done")
    return 0


def write_fake_maxquant(folder: Path) -> Path:
    """MaxQuantCmd.bat / .sh in `folder` running `ionomos fake-maxquant` (for `engine: maxquant` tests)."""
    from ionomos.service import ionomos_command

    cmd = ionomos_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / "MaxQuantCmd.bat"
        exe.write_text("@echo off\r\n" + " ".join(f'"{c}"' for c in cmd) + " fake-maxquant %*\r\n", encoding="utf-8")
    else:
        exe = folder / "MaxQuantCmd.sh"
        exe.write_text("#!/bin/sh\nexec " + " ".join(f"'{c}'" for c in cmd) + ' fake-maxquant "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def _write_wrapper(folder: Path, name: str, sub: str) -> Path:
    """<name>.bat / .sh in `folder` running `ionomos <sub>` with its arguments."""
    from ionomos.service import ionomos_command

    cmd = ionomos_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / f"{name}.bat"
        exe.write_text("@echo off\r\n" + " ".join(f'"{c}"' for c in cmd) + f" {sub} %*\r\n", encoding="utf-8")
    else:
        exe = folder / f"{name}.sh"
        exe.write_text("#!/bin/sh\nexec " + " ".join(f"'{c}'" for c in cmd) + f' {sub} "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


SAGE_NO_TELEMETRY = "--disable-telemetry-i-dont-want-to-improve-sage"
FAKE_SAGE_VERSION = "0.14.7"


def fake_sage(argv: list[str]) -> int:
    """Fake Sage (`ionomos fake-sage [options] sage.json`): --help prints the real usage (with the telemetry
    switch unless IONOMOS_FAKE_SAGE_OLD=1, an older Sage, which then refuses the flag); a run checks the FASTA
    and every mzML like the real one, then writes what the real one would into output_directory: results.sage.tsv
    and results.json, lfq.tsv when quant.lfq is on (_fake_sage_lfq) and tmt.tsv when quant.tmt names a kit
    (_fake_sage_tmt). IONOMOS_FAKE_FP_MODE=fail exits 1."""
    import json

    say = lambda *x: print("FAKE Sage:", *x, flush=True)  # noqa: E731
    old = os.environ.get("IONOMOS_FAKE_SAGE_OLD") == "1"
    if "--help" in argv or "-h" in argv:
        print("Usage: sage [OPTIONS] <parameters> [mzml_paths]...\n\nOptions:\n  -f, --fasta <fasta>\n"
              "  -o, --output_directory <output_directory>\n      --batch-size <batch-size>\n      --parquet\n"
              "      --write-pin\n" + ("" if old else f"      {SAGE_NO_TELEMETRY}\n          Disable sending "
                                                        "telemetry data\n") + "  -h, --help\n  -V, --version")
        return 0
    if SAGE_NO_TELEMETRY in argv and old:
        say(f"error: unexpected argument '{SAGE_NO_TELEMETRY}' found")
        return 2
    params = [a for a in argv if a.lower().endswith(".json")]
    if not params or not Path(params[-1]).is_file():
        say("error: the following required arguments were not provided: <parameters>")
        return 2
    cfg = json.loads(Path(params[-1]).read_text(encoding="utf-8"))
    fasta = (cfg.get("database") or {}).get("fasta") or ""
    paths = cfg.get("mzml_paths") or []
    out = cfg.get("output_directory")
    if not Path(fasta).is_file():
        say(f"Error: Failed to build database from `{fasta}`: No such file or directory")
        return 1
    if not paths or not out:
        say("Error: `mzml_paths` must be set. For more information try '--help'")
        return 1
    for f in paths:
        if not Path(f).exists():
            say(f"Error: failed to read {f}: No such file or directory")
            return 1
    if os.environ.get("IONOMOS_FAKE_FP_MODE") == "fail":
        say("Error: fake failure")
        return 1
    say("telemetry", "off" if SAGE_NO_TELEMETRY in argv else "ON",
        "| threads", os.environ.get("RAYON_NUM_THREADS", "all"))
    names = [Path(f).name for f in paths]
    quant = cfg.get("quant") or {}
    kit = quant.get("tmt")
    channels = SAGE_KITS.get(kit) if isinstance(kit, str) else len((kit or {}).get("User") or [])
    if kit and not channels:
        say(f"Error: unknown variant `{kit}`, expected one of `Tmt6`, `Tmt10`, `Tmt11`, `Tmt16`, `Tmt18`, `User`")
        return 1
    outdir = Path(out)
    outdir.mkdir(parents=True, exist_ok=True)
    psm: list[list[str]] = []
    if kit:
        head = "tmt" if isinstance(kit, str) else "user"
        tmt, psm = _fake_sage_tmt(names, channels)
        (outdir / "tmt.tsv").write_text("\n".join("\t".join(r) for r in [
            ["filename", "scannr", "ion_injection_time", *[f"{head}_{k + 1}" for k in range(channels)]], *tmt]) + "\n",
            encoding="utf-8")
    if quant.get("lfq") or not kit:
        lfq, lfq_psm = _fake_sage_lfq(names)
        psm = psm or lfq_psm
        (outdir / "lfq.tsv").write_text("\n".join("\t".join(r) for r in lfq) + "\n", encoding="utf-8")
    (outdir / "results.sage.tsv").write_text("\n".join("\t".join(r) for r in [SAGE_PSM_HEADER, *psm]) + "\n",
                                             encoding="utf-8")
    (outdir / "results.json").write_text(json.dumps({"version": FAKE_SAGE_VERSION, **cfg}, indent=2), encoding="utf-8")
    say("finished")
    return 0


SAGE_KITS = {"Tmt6": 6, "Tmt10": 10, "Tmt11": 11, "Tmt16": 16, "Tmt18": 18}
# results.sage.tsv as Sage 0.14 / 0.15 writes it (sage-cli runner.rs, write_features)
SAGE_PSM_HEADER = [
    "psm_id", "peptide", "proteins", "protein_groups", "num_proteins", "num_protein_groups", "filename", "scannr",
    "rank", "label", "expmass", "calcmass", "charge", "peptide_len", "missed_cleavages", "semi_enzymatic",
    "isotope_error", "precursor_ppm", "fragment_ppm", "hyperscore", "delta_next", "delta_best", "rt", "aligned_rt",
    "predicted_rt", "delta_rt_model", "ion_mobility", "predicted_mobility", "delta_mobility", "matched_peaks",
    "longest_b", "longest_y", "longest_y_pct", "matched_intensity_pct", "scored_candidates", "poisson",
    "sage_discriminant_score", "posterior_error", "spectrum_q", "peptide_q", "protein_q", "protein_group_q",
    "ms2_intensity"]


def _sage_scan(n: int) -> str:
    return f"controllerType=0 controllerNumber=1 scan={n}"


def _sage_psm(n: int, pep: str, prots: str, filename: str, scan: int, intensity: float = 0.0, label: int = 1,
              rank: int = 1, spectrum_q: float = 0.001, peptide_q: float = 0.001, protein_q: float = 0.001) -> list[str]:
    """One results.sage.tsv row. The mass error is +1.5 ppm with one PSM in seven on the first isotope peak; the
    RT (minutes) belongs to the peptide; every seventh peptide has a missed cleavage, every third is 3+."""
    k = sum(ord(c) for c in pep)
    calc = 900.0 + (k % 1500)
    iso = 1 if n % 7 == 0 else 0
    exp = calc * (1 + 1.5e-6) + iso * 1.00335
    rt = 5.0 + (k % 9000) / 100.0
    row = dict.fromkeys(SAGE_PSM_HEADER, "0.0")
    row.update({"psm_id": str(n), "peptide": pep, "proteins": prots, "protein_groups": "",
                "num_proteins": str(prots.count(";") + 1), "num_protein_groups": "0", "filename": filename,
                "scannr": _sage_scan(scan), "rank": str(rank), "label": str(label), "expmass": f"{exp:.5f}",
                "calcmass": f"{calc:.5f}", "charge": "3" if k % 3 == 0 else "2", "peptide_len": str(len(pep)),
                "missed_cleavages": "1" if k % 7 == 0 else "0", "semi_enzymatic": "0", "isotope_error": f"{iso}.0",
                "precursor_ppm": "1.5", "fragment_ppm": "2.5", "hyperscore": "30.0", "rt": f"{rt:.3f}",
                "aligned_rt": f"{rt / 100:.4f}", "matched_peaks": "12", "longest_b": "4", "longest_y": "8",
                "spectrum_q": f"{spectrum_q:g}", "peptide_q": f"{min(peptide_q, 1):g}", "protein_q": f"{protein_q:g}",
                "protein_group_q": "1.0", "ms2_intensity": f"{intensity:.1f}"})
    return [row[h] for h in SAGE_PSM_HEADER]


def _fake_sage_lfq(names: list[str]) -> tuple[list[list[str]], list[list[str]]]:
    """(lfq.tsv rows with the header, results.sage.tsv rows) of a label-free search: three peptide ions per
    protein, one shared between two proteins, a decoy-only and a high-q row; a PSM per ion and file."""
    import re as _re
    import tempfile

    from ionomos.downstream import simulate

    samples: dict[str, str] = {}  # file -> its sample: fractions (a second trailing number) share one
    for n in names:
        stem = _re.sub(r"(?i)\.mzml(\.gz)?$|\.d$", "", n)
        m = _re.match(r"^(.*_\d+)_\d+$", stem)
        samples[n] = m.group(1) if m else stem
    order = list(dict.fromkeys(samples.values()))
    with tempfile.TemporaryDirectory() as td:
        pg = Path(td) / "pg.tsv"
        simulate.dia_pg_matrix(pg, [(x, _re.sub(r"[_-]?\d+$", "", x) or x) for x in order], seed=len(order),
                               n_proteins=300)
        _head, *rows = [ln.split("\t") for ln in pg.read_text(encoding="utf-8").splitlines()]
    col = {x: 7 + j for j, x in enumerate(order)}
    share = {n: 1.0 / sum(1 for v in samples.values() if v == samples[n]) for n in names}  # split over fractions
    lfq = [["peptide", "charge", "proteins", "q_value", "score", "spectral_angle", *names]]
    psm: list[list[str]] = []
    scans = dict.fromkeys(names, 0)

    def ion(pep, prots, q, factor, r):
        vals = []
        for n in names:
            v = r[col[samples[n]]] if r is not None else ""
            vals.append(f"{float(v) * factor * share[n]:.1f}" if v else "0.0")
            if v:
                scans[n] += 1
                psm.append(_sage_psm(len(psm) + 1, pep, prots, n, scans[n], float(vals[-1]),
                                     label=-1 if prots.startswith("rev_") else 1, peptide_q=q,
                                     protein_q=0.2 if "FAILS" in pep else 0.001))
        lfq.append([pep, "-1", prots, f"{q:g}", "1.5", "0.9", *vals])

    for k, r in enumerate(rows):
        prot = f"sp|{r[0]}|{r[2]}"
        for j, factor in enumerate((1.0, 0.5, 0.25)):
            ion(f"PEPTIDE{k}K{'A' * j}R", prot, 0.001, factor, r)
        if k % 50 == 1:   # shared with the previous protein, which has as many peptides: the razor picks by name
            ion(f"SHARED{k}R", f"sp|{rows[k - 1][0]}|{rows[k - 1][2]};{prot}", 0.001, 0.3, r)
    ion("HIGHQPEPTIDEK", "sp|P99990|HIGHQ_HUMAN", 0.2, 1.0, rows[0])          # above the peptide q-value
    ion("PROTEINFAILSK", "sp|P99991|PROTQ_HUMAN", 0.001, 1.0, rows[0])        # its protein is above the protein q
    ion("DECOYONLYK", "rev_sp|P99992|DECOY_HUMAN", 0.001, 1.0, rows[0])
    return lfq, psm


def fake_sage_tmt_design(channels: int) -> list[str]:
    """The fake TMT experiment's condition per channel, in kit order: a pool in the first and last channel,
    DMSO in the first half of the rest and Drug in the second."""
    inner = channels - 2
    return ["Pool", *["DMSO"] * (inner - inner // 2), *["Drug"] * (inner // 2), "Pool"]


def _fake_sage_tmt(names: list[str], channels: int) -> tuple[list[list[str]], list[list[str]]]:
    """(tmt.tsv rows without the header, results.sage.tsv rows) of a TMT search. A plex is the files that share
    a name up to _F<fraction>; its channels follow fake_sage_tmt_design(); every protein has its own level in
    every plex (the plex effect IRS removes) and changes planted in Drug. Three PSMs per protein and plex,
    spread over the plex's fractions; a shared peptide; and rows the analysis must leave out, each with a huge
    reporter signal: a decoy, PSMs above the spectrum and protein q-values, a rank-2 PSM, plus a reporter row
    for a spectrum that was not identified."""
    import random
    import re as _re
    import tempfile

    from ionomos.downstream import simulate

    plex_of = {n: _re.sub(r"(?i)(?:_TMT)?[_-]F?\d+$", "", _re.sub(r"(?i)\.mzml(\.gz)?$|\.d$", "", n)) for n in names}
    plexes = list(dict.fromkeys(plex_of.values()))
    design = fake_sage_tmt_design(channels)
    cols = [(p, j) for p in plexes for j, c in enumerate(design) if c != "Pool"]
    with tempfile.TemporaryDirectory() as td:
        pg = Path(td) / "pg.tsv"
        simulate.dia_pg_matrix(pg, [(f"{p}_{j}", design[j]) for p, j in cols], seed=len(plexes), n_proteins=300)
        _head, *rows = [ln.split("\t") for ln in pg.read_text(encoding="utf-8").splitlines()]
    at = {pj: 7 + k for k, pj in enumerate(cols)}
    rng = random.Random(len(plexes))
    tmt: list[list[str]] = []
    psm: list[list[str]] = []
    scans = dict.fromkeys(names, 0)

    def spectrum(pep, prots, filename, vals, **kw):
        scans[filename] += 1
        if pep:
            psm.append(_sage_psm(len(psm) + 1, pep, prots, filename, scans[filename], sum(vals), **kw))
        if kw.get("rank", 1) == 1:  # a second PSM of a spectrum shares its reporter row
            tmt.append([filename, _sage_scan(scans[filename]), "50.0", *[f"{v:.2f}" for v in vals]])
        else:
            scans[filename] -= 1
            psm[-1][SAGE_PSM_HEADER.index("scannr")] = _sage_scan(scans[filename])

    for p in plexes:
        files = [n for n in names if plex_of[n] == p]
        huge = [1e12] * channels
        for k, r in enumerate(rows):
            shift = 2 ** rng.gauss(0, 1.2)
            seen = [float(r[at[(p, j)]]) for j, c in enumerate(design) if c != "Pool" and r[at[(p, j)]]]
            pool = sum(seen) / len(seen) if seen else 0.0
            level = [(pool if c == "Pool" else float(r[at[(p, j)]] or 0)) * shift for j, c in enumerate(design)]
            prot = f"sp|{r[0]}|{r[2]}"
            for j, factor in enumerate((1.0, 0.5, 0.25)):
                spectrum(f"PEPTIDE{k}K{'A' * j}R", prot, files[(k + j) % len(files)], [v * factor for v in level])
            if k % 50 == 1:
                spectrum(f"SHARED{k}R", f"sp|{rows[k - 1][0]}|{rows[k - 1][2]};{prot}", files[0],
                         [v * 0.3 for v in level])
        first = f"sp|{rows[0][0]}|{rows[0][2]}"
        spectrum("HIGHQPEPTIDEK", first, files[0], huge, spectrum_q=0.2)
        spectrum("PROTEINFAILSK", "sp|P99991|PROTQ_HUMAN", files[0], huge, protein_q=0.2)
        spectrum("DECOYONLYK", "rev_sp|P99992|DECOY_HUMAN", files[0], huge, label=-1)
        spectrum("SECONDRANKK", first, files[0], huge, rank=2)   # on the DECOYONLYK spectrum
        spectrum("", "", files[0], huge)                          # an MS3 scan whose MS2 was not identified
    return tmt, psm


def fake_rawparser(argv: list[str]) -> int:
    """Fake ThermoRawFileParser (`ionomos fake-rawparser -i=RAW -o=DIR -f=2`): writes DIR/<stem>.mzML.
    IONOMOS_FAKE_CONVERT_MODE=fail exits 1 without writing; =empty exits 0 without writing."""
    opts = dict(a.split("=", 1) for a in argv if a.startswith("-") and "=" in a)
    raw, out = Path(opts.get("-i", "")), Path(opts.get("-o", ""))
    if not raw.is_file() or not out.is_dir():
        print(f"FAKE ThermoRawFileParser: ERROR input {raw} or output folder {out} not found", flush=True)
        return 1
    mode = os.environ.get("IONOMOS_FAKE_CONVERT_MODE")
    if mode == "fail":
        print(f"FAKE ThermoRawFileParser: ERROR RawFileReader could not open {raw.name}", flush=True)
        return 1
    if mode != "empty":
        (out / f"{raw.stem}.mzML").write_text(f"<mzML><!-- fake, from {raw.name} --></mzML>\n", encoding="utf-8")
    print(f"FAKE ThermoRawFileParser: converted {raw.name}", flush=True)
    return 0


def write_fake_sage(folder: Path) -> Path:
    """sage.bat / .sh in `folder` running `ionomos fake-sage` (for `engine: sage` tests)."""
    return _write_wrapper(folder, "sage", "fake-sage")


def write_fake_rawparser(folder: Path) -> Path:
    """ThermoRawFileParser.bat / .sh in `folder` running `ionomos fake-rawparser`."""
    return _write_wrapper(folder, "ThermoRawFileParser", "fake-rawparser")


def write_fake_launcher(folder: Path) -> Path:
    """fragpipe.bat / fragpipe.sh in `folder` that runs `ionomos fake-fragpipe` (works frozen or from a venv)."""
    from ionomos.service import ionomos_command

    cmd = ionomos_command(console=True)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        exe = folder / "fragpipe.bat"
        quoted = " ".join(f'"{c}"' for c in cmd)
        exe.write_text(f"@echo off\r\n{quoted} fake-fragpipe %*\r\n", encoding="utf-8")
    else:
        exe = folder / "fragpipe.sh"
        quoted = " ".join(f"'{c}'" for c in cmd)
        exe.write_text(f'#!/bin/sh\nexec {quoted} fake-fragpipe "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def workflow_text(kind: str) -> str:
    """A small .workflow for the testbed: the switches Ionomos and the fake FragPipe read, with the values of
    FragPipe 24.0's stock chemprot-ABPP-isoDTB / TMT10-MS3 / DIA_SpecLib_Quant workflows (a real one has ~400
    keys). The isoDTB one has match-between-runs on, as the lab's SOP asks."""
    tmt, dia = kind == "TMT", kind == "DIA"
    keys = {
        "crystalc.run-crystalc": "false",
        "database.decoy-tag": "rev_",
        "diann.run-dia-nn": str(dia).lower(),
        "diatracer.run-diatracer": "false",
        "diaumpire.run-diaumpire": "false",
        "freequant.run-freequant": "false",
        "ionquant.heavy": "" if tmt or dia else "C567.3462",
        "ionquant.light": "" if tmt or dia else "C561.3387",
        "ionquant.mbr": "0" if tmt or dia else "1",
        "ionquant.run-ionquant": "true",
        "ionquant.use-labeling": str(not (tmt or dia)).lower(),
        "ionquant.use-lfq": "false",
        "msbooster.run-msbooster": "true",
        "msfragger.calibrate_mass": "2",
        "msfragger.misc.slice-db": "1",
        "msfragger.run-msfragger": "true",
        "msfragger.search_enzyme_name_1": "stricttrypsin",
        "peptide-prophet.run-peptide-prophet": "false",
        "percolator.run-percolator": "true",
        "phi-report.run-report": "true",
        "protein-prophet.run-protein-prophet": "true",
        "ptmprophet.run-ptmprophet": "false",
        "ptmshepherd.run-shepherd": "false",
        "quantitation.run-label-free-quant": str(not (tmt or dia)).lower(),
        "run-psm-validation": "true",
        "run-validation-tab": "true",
        "speclibgen.run-speclibgen": str(dia).lower(),
        "tab-run.delete_temp_files": "false",
        "tmtintegrator.add_Ref": "1" if tmt else "-1",
        "tmtintegrator.channel_num": "TMT-10" if tmt else "TMT-6",
        "tmtintegrator.extraction_tool": "IonQuant",
        "tmtintegrator.ref_tag": "Bridge",
        "tmtintegrator.run-tmtintegrator": str(tmt).lower(),
        "workflow.input.data-type.im-ms": "false",
        "workflow.input.data-type.regular-ms": "true",
        # FragPipe's stock workflows say true and then write <workdir>/sdrf.tsv, which the analysis reads as the
        # experiment's own design (downstream/sdrfdesign.py). Off here until that is settled (docs/ROADMAP.md);
        # tests/test_fragpipe_real.py runs one job with it on.
        "workflow.misc.save-sdrf": "false",
        "workflow.misc.sdrf-type": "Default",
        "workflow.saved-with-ver": "24.0-build27",
    }
    return (f"# Workflow: {kind} (Ionomos testbed)\n\n" + "".join(f"{k}={v}\n" for k, v in keys.items())
            + "database.db-path=FAKE.fas\n")


def init(root: Path, slow_defaults: bool = False) -> Path:
    root = Path(root).resolve()
    auto = root / "Fragpipe_Auto"
    general = root / "Fragpipe_General"
    for d in (auto / "inbox", auto / "workflows", auto / "fasta", auto / "logs", root / "samples"):
        d.mkdir(parents=True, exist_ok=True)
    for u in USERS:
        (general / u).mkdir(parents=True, exist_ok=True)
    for wf, kind in (("isoDTB.workflow", "isoDTB"), ("TMT10-MS3.workflow", "TMT"), ("DIA.workflow", "DIA")):
        p = auto / "workflows" / wf
        if not p.exists():
            p.write_text(workflow_text(kind), encoding="utf-8", newline="\n")
    (auto / "fasta" / "human_reviewed_decoys.fas").write_text(
        ">sp|FAKE1|FAKE1_HUMAN fake protein 1\nMKVLAAGIVGLLLAC\n>sp|FAKE2|FAKE2_HUMAN fake protein 2\nMSTNPKPQRKTKRNT\n"
        ">rev_sp|FAKE1|FAKE1_HUMAN\nCALLLGVIGAALVKM\n>rev_sp|FAKE2|FAKE2_HUMAN\nTNRKTKRQPKPNTSM\n", encoding="utf-8")

    exe = write_fake_launcher(auto)
    (auto / "fake_fragpipe.py").unlink(missing_ok=True)  # older testbeds

    def s(p: Path) -> str:  # forward slashes, as recommended in config
        return str(p).replace("\\", "/")

    cfg = {
        "paths": {
            "inbox": s(auto / "inbox"), "users_root": s(general), "fragpipe_exe": s(exe),
            "workflow_dir": s(auto / "workflows"), "fasta_dir": s(auto / "fasta"),
            "database": s(auto / "ionomos.db"), "log_dir": s(auto / "logs"),
        },
        "watcher": {"poll_seconds": 1 if not slow_defaults else 10, "stable_seconds": 3 if not slow_defaults else 60,
                    "min_raw_files": 1},
        "fragpipe": {"threads": 4, "ram_gb": 4, "timeout_minutes": 5, "min_free_gb": 0.1},
        "gui": {"enabled": True, "timeout_minutes": 0},
        "users": {"aliases": {"Isaac": ["IJ", "IJD"], "EJQ": ["EJQ_2"]}, "default": ""},
        "methods": {
            "isoDTB": {"aliases": ["isodtb", "iso-dtb"], "workflow": "isoDTB.workflow",
                       "fasta": "human_reviewed_decoys.fas", "data_type": "DDA",
                       "postprocess": ["isodtb_sites"], "isodtb_mod_mass": "561.3387"},
            "TMT": {"aliases": ["tmt"], "workflow": "TMT10-MS3.workflow", "fasta": "human_reviewed_decoys.fas",
                    "data_type": "DDA", "postprocess": ["tmt_annotation"]},
            "DIA": {"aliases": ["dia", "diann", "dia-nn"], "workflow": "DIA.workflow",
                    "fasta": "human_reviewed_decoys.fas", "data_type": "DIA", "postprocess": []},
        },
    }
    cfg_path = auto / "config.yaml"
    cfg_path.write_text("# ionomos TESTBED config - generated by `ionomos testbed init`\n" + yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    for name in SAMPLES:
        build_sample(root / "samples", name)
    (root / "README.txt").write_text(_readme(root, cfg_path), encoding="utf-8")
    return cfg_path


def _readme(root: Path, cfg_path: Path) -> str:
    return f"""ionomos testbed at {root}

Terminal 1 (the watcher):
    ionomos --config "{cfg_path}" run

Terminal 2 (you, being a lab member):
    ionomos testbed list "{root}"
    ionomos testbed drop iso_good "{root}"
    ionomos testbed drop gui_unknown_user "{root}"      # resolver window opens in terminal 1
    ionomos --config "{cfg_path}" status
    ionomos testbed reset "{root}"

Or drag folders from  {root / 'samples'}  into  {root / 'Fragpipe_Auto' / 'inbox'}  with Finder/Explorer.
Results land in  {root / 'Fragpipe_General'}/<user>/ .
"""


# --------------------------------------------------------------------- drop --


def drop(root: Path, name: str, slow: bool = False, delay: float = 0.4) -> Path:
    root = Path(root).resolve()
    src = root / "samples" / SAMPLES[name]["folder"]
    if not src.is_dir():
        build_sample(root / "samples", name)
    dst = root / "Fragpipe_Auto" / "inbox" / src.name
    if dst.exists():
        raise SystemExit(f"already in inbox: {dst}")
    if not slow:
        shutil.copytree(src, dst)
        return dst
    # file by file, with pauses: exercises the stability wait
    dst.mkdir()
    files = sorted(p for p in src.rglob("*") if p.is_file())
    for i, p in enumerate(files, 1):
        rel = p.relative_to(src)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst / rel)
        print(f"  copied {i}/{len(files)} {rel}")
        time.sleep(delay)
    return dst


def reset(root: Path) -> None:
    root = Path(root).resolve()
    auto = root / "Fragpipe_Auto"
    for d in (auto / "inbox", root / "Fragpipe_General"):
        if d.is_dir():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for f in [auto / "ionomos.db", auto / "learned_aliases.yaml", *(auto / "logs").glob("ionomos.log*")]:
        if f.exists():
            f.unlink()
    for u in USERS:
        (root / "Fragpipe_General" / u).mkdir(parents=True, exist_ok=True)


def gui_demo() -> int:
    from ionomos.intake import Draft, DraftFile, Kind
    from ionomos.resolve import TkResolver, gui_available

    ok, why = gui_available()
    if not ok:
        print(f"GUI not available: {why}")
        return 1
    import tkinter as tk

    files = [DraftFile(f"EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_{r}_{f}.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h", str(r), str(f))
             for r in (1, 2, 3) for f in range(1, 8)]
    files.append(DraftFile("EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra.raw", "EJQ_PK_EJQ-2-027_isoDTB_1uM_3h_extra", "1", "",
                           error="must end in _rep_fraction"))
    d = Draft(folder="20260902-isoDTB_XYZ-2-027 (1uM 3h)",
              problem="no known user in '20260902-isoDTB_XYZ-2-027 (1uM 3h)'; include your initials or folder name "
                      "(known: Aman, Chris, EJQ, Isaac)",
              kind=Kind.USER, user="", method="isoDTB", date="2026-09-02",
              known_users=USERS, known_methods=["isoDTB", "TMT", "DIA"], files=files)
    root = tk.Tk()
    root.withdraw()
    res = TkResolver(root, remember=lambda u, a: print(f"(would remember alias {a!r} -> {u})"))
    ov = res.resolve(d)
    root.destroy()
    print("answer:", "skipped" if ov is None else yaml.safe_dump(ov.to_dict(), sort_keys=False))
    return 0


# ---------------------------------------------------------------------- cli --


def add_parser(sub):
    p = sub.add_parser("testbed", help="build/drive a fake lab for testing", description=__doc__,
                       formatter_class=__import__("argparse").RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="tb_cmd", required=True)
    i = s.add_parser("init", help="create the testbed")
    i.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    i.add_argument("--slow-defaults", action="store_true", help="use production poll/stable timings")
    ls = s.add_parser("list", help="list sample drops")
    ls.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    d = s.add_parser("drop", help="copy a sample into the inbox")
    d.add_argument("name", choices=sorted(SAMPLES))
    d.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    d.add_argument("--slow", action="store_true", help="copy file by file with pauses")
    r = s.add_parser("reset", help="empty inbox/users/ledger/logs")
    r.add_argument("dir", nargs="?", default=DEFAULT_DIR)
    s.add_parser("gui-demo", help="open the resolver window with sample data")
    st = s.add_parser("stress", help="many messy drops + chaos against a real watcher/worker; checks invariants")
    st.add_argument("--n", type=int, default=60, help="number of drops (default 60)")
    st.add_argument("--seed", type=int, default=1)
    st.add_argument("--dir", default=None, help="where to build it (default: a temp folder, deleted afterwards)")
    st.add_argument("--keep", action="store_true", help="keep the temp folder to inspect")
    st.add_argument("--no-chaos", action="store_true")
    st.add_argument("--fuzz", type=int, default=3000, help="random names through the parsers (0 = skip)")
    return p


def main(args) -> int:
    if args.tb_cmd == "init":
        cfg = init(Path(args.dir), args.slow_defaults)
        print((Path(args.dir).resolve() / "README.txt").read_text(encoding="utf-8"))
        print(f"config: {cfg}")
        return 0
    if args.tb_cmd == "list":
        w = max(len(n) for n in SAMPLES)
        for n, spec in SAMPLES.items():
            print(f"  {n:<{w}}  {spec['folder']:<40}  {spec['shows']}")
        return 0
    if args.tb_cmd == "drop":
        dst = drop(Path(args.dir), args.name, slow=args.slow)
        print(f"dropped {dst}")
        return 0
    if args.tb_cmd == "stress":
        import logging

        from ionomos import stress

        logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
        bad = stress.fuzz_names(args.fuzz, args.seed) if args.fuzz else []
        print(f"fuzz: {args.fuzz} random names through the parsers — {len(bad)} failure(s)")
        for b in bad[:10]:
            print("  -", b)
        print(f"stress: {args.n} drops, seed {args.seed}{'' if not args.no_chaos else ', no chaos'} …", flush=True)
        rep = stress.run(args.n, args.seed, Path(args.dir) if args.dir else None, keep=args.keep,
                         chaos=not args.no_chaos)
        print(rep.text())
        return 0 if rep.ok and not bad else 1
    if args.tb_cmd == "reset":
        reset(Path(args.dir))
        print("testbed reset")
        return 0
    if args.tb_cmd == "gui-demo":
        return gui_demo()
    return 2
