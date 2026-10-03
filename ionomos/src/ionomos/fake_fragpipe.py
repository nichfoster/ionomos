"""
The testbed's stand-in for FragPipe (`ionomos fake-fragpipe ...`, reached through testbed.write_fake_launcher).

It takes FragPipe's headless options, makes FragPipe's checks in FragPipe's order, prints what FragPipe prints
and leaves the files FragPipe leaves, so that Ionomos' runner and parsers are tested against the real shapes.
What is copied, and from where (FragPipe 24.0 source, checked against 23.1, and logs users attached to
FragPipe's issue tracker):

    options, usage text, exit codes         FragPipeMain.java, Fragpipe.main0 / help()
    the checks before a run                 Fragpipe.main0, FragpipeRun.run / validateWd / checkFasta / checkDbConfig
    experiment names, group folders         InputLcmsFile (only A-Z a-z 0-9 _; <experiment>_<bioreplicate>)
    the TMT annotation lookup               TmtiPanel (one file ending in annotation.txt in the plex's folder)
    console layout and step lines           FragpipeRun.run, ProcessBuilderInfo.toRunnable, ProcessManager
    ERROR lines ("<time> ERROR - text")     logback.xml
    output folder names                     CmdIonquant, CmdTmtIntegrator (tmt-report), CmdDiann (dia-quant-output)
    sdrf.tsv, experiment_annotation.tsv     SDRFtable, ToolingUtils

What is invented: the tools' own chatter between a step's start and end line (a few typical lines each), the
result tables (ionomos.downstream.simulate, with planted hits) and every number. Nothing is searched.

Environment:
    IONOMOS_FAKE_FP_SECONDS   how long a run takes (default 4)
    IONOMOS_FAKE_FP_MODE      a failure to act out, see MODES; several joined by commas ("child,hang")
    IONOMOS_FAKE_FP_LOG_MB    how much the huge-log mode prints (default 3)
    IONOMOS_FAKE_FP_STRICT    1 = refuse paths with spaces as on Windows (always on under Windows)
One experiment only: a file fake_fragpipe_mode.txt (names.FAKE_FP_MODE_FILE) in the experiment folder holds the
mode(s) for that experiment and wins over the environment, so a testbed or stress run can mix faults (D69).
A raw file path containing FAKEFAIL makes IonQuant fail, as before.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

VERSION = "24.0"
BUILD = "24.0-build27"
# name -> version, in the order of FragpipeRun.createVersionsString (the PC's tool versions where known)
TOOL_VERSIONS = (
    ("DIA-Umpire", "2.3.3"), ("diaTracer", "2.2.1"), ("MSFragger", "4.4.1"), ("Crystal-C", "1.5.10"),
    ("MSBooster", "1.4.14"), ("Percolator", "3.7.1"), ("PTMProphet", "6.3.2"), ("Metaproteomics", "1.0.1"),
    ("Philosopher", "5.1.3-RC9"), ("PTM-Shepherd", "3.0.11"), ("IonQuant", "1.11.20"), ("TMT-Integrator", "6.1.3"),
    ("FragPipe-SpecLib", "0.1.58"), ("DIA-NN", "1.8.2 beta 8"), ("Skyline", "N/A"), ("Pandas", "2.3.3"),
    ("Numpy", "1.26.4"),
)
SYSTEM_LINES = ("System OS: Windows 11, Architecture: AMD64",
                "Java Info: 17.0.10, OpenJDK 64-Bit Server VM, Eclipse Adoptium",
                ".NET Core Info: N/A")
TMT_CHANNELS = {"TMT-6": ("126", "127", "128", "129", "130", "131"),
                "TMT-10": ("126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N"),
                "TMT-11": ("126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N", "131C"),
                "TMT-16": ("126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N", "131C",
                           "132N", "132C", "133N", "133C", "134N")}

# IONOMOS_FAKE_FP_MODE -> what it acts out
MODES = {
    "oom": "MSFragger dies with java.lang.OutOfMemoryError; FragPipe stops and exits 1",
    "msfragger": "MSFragger is not configured: FragPipe refuses before any step, exits 1",
    "speclib": "Python / FragPipe-SpecLib is not configured: FragPipe refuses before any step, exits 1",
    "no-java": "what fragpipe.bat prints when it finds no Java, exit 1",
    "locked": "PhilosopherReport can't write psm.tsv (a sync tool holds it), exits 1",
    "diann": "the DIA-NN step fails, exits 1",
    "step-fail-exit0": "a step reports exit code 137 but the launcher exits 0",
    "step-fail-neg-exit0": "a step reports exit code -11 but the launcher exits 0",
    "cancel-exit0": "FragPipe cancels its remaining tasks but the launcher exits 0",
    "silent-exit0": "prints nothing, writes nothing, exits 0 (a launcher that does not wait for FragPipe)",
    "no-done-line": "a complete run whose log lacks the ALL JOBS DONE line",
    "child": "a normal run that also starts a long-lived child process (pid in <workdir>/child.pid)",
    # D69: the fault-injection suite (tests/test_faults.py)
    "hang": "MSFragger starts and never ends (prints nothing more) until it is killed",
    "killed": "FragPipe is ended from outside in the middle of MSFragger: no message, SIGKILL / exit code 1",
    "disk-full": "MSFragger stops on Java's 'There is not enough space on the disk', leaving a 0-byte file",
    "raw-vanished": "MSFragger stops on a FileNotFoundException for the first raw file",
    "garbled-log": "the console gets Windows code page text, UTF-16, colour codes and binary junk, then "
                   "PhilosopherReport fails on a locked file under a non-ASCII path",
    "huge-log": "a complete run that prints IONOMOS_FAKE_FP_LOG_MB MB of chatter, one line of 1 MB",
    "runaway-log": "a tool prints the same line forever, until it is killed",
    "empty-table": "a complete run whose main result table is 0 bytes",
    "header-only": "a complete run whose main result table has its header and no rows",
    "truncated-table": "a complete run whose main result table stops in the middle of a row",
    "missing-table": "a complete run that writes no main result table",
    "truncated-psm": "a complete run whose psm.tsv files stop in the middle of a row",
}


def modes_for(workdir: Path | None) -> set[str]:
    """The faults to act out: the experiment's own fake_fragpipe_mode.txt, else IONOMOS_FAKE_FP_MODE."""
    from ionomos.names import FAKE_FP_MODE_FILE

    text = os.environ.get("IONOMOS_FAKE_FP_MODE", "")
    if workdir is not None:
        try:
            text = (Path(workdir).resolve().parent / FAKE_FP_MODE_FILE).read_text(encoding="utf-8")
        except OSError:
            pass
    return {m.strip() for m in text.replace("\n", ",").split(",") if m.strip()}

DASHES = "~~~~~~~~~~~~~~~~~~~~~~"


def help_text() -> str:
    """Fragpipe.help(), 24.0."""
    return (
        f"FragPipe v{VERSION}\n(c) University of Michigan\n" + "\n".join(SYSTEM_LINES) + "\n"
        "Running without GUI. Usage:\n"
        "\tWindows: fragpipe.bat --headless --workflow <path to workflow file> --manifest <path to manifest file> "
        "--workdir <path to result directory>\n"
        "\tLinux: fragpipe --headless --workflow <path to workflow file> --manifest <path to manifest file> "
        "--workdir <path to result directory>\n"
        "Options:\n\t-h\n"
        "\t--help                          # Print this help message.\n"
        "\t--headless                      # Running in headless mode.\n"
        "\t--workflow <string>             # Specify path to workflow file.\n"
        "\t--manifest <string>             # Specify path to manifest file.\n"
        "\t--workdir <string>              # Specify the result directory.\n"
        "\t--dry-run                       # (optional) Dry run, not really run FragPipe.\n"
        "\t--ram <integer>                 # (optional) Specify the maximum allowed memory size. The unit is GB. "
        "Set it to 0 to let FragPipe decide. Default = 0\n"
        "\t--threads <integer>             # (optional) Specify the number of threads. Default = core number - 1\n"
        "\t--config-tools-folder <string>  # (optional) specify the folder containing MSFragger, IonQuant, and "
        "dirTracer. If not specified, using the one in the cache.\n"
        "\t--config-diann <string>         # (optional) specify the location of the DIA-NN binary file (the actual "
        "executable file `DiaNN.exe`, not the DIA-NN installation file). If not specified, using the one in the "
        "cache. It could be from the previously configured or the build-in one.\n"
        "\t--config-python <string>        # (optional) specify the location of the Python directory. If not "
        "specified, using the one in the cache.\n"
        "To let FragPipe find the TMT annotation file, put the mzML files from the same experiment in the same "
        "folder. Then, create the annotation file with the name ending with annotation.txt in the folder.Note: "
        "There must be only one annotation file in each folder.\n")


def _out(text: str = "") -> None:
    print(text, flush=True)


def _error(text: str) -> None:
    """A check that failed before any step: logback's "%d{ISO8601} %-5level - %msg"."""
    now = datetime.now()
    _out(f"{now:%Y-%m-%d %H:%M:%S},{now.microsecond // 1000:03d} ERROR - {text}")


def _parse(argv: list[str]) -> dict | int:
    """FragPipeMain.main: every option by name, case-insensitive; anything else stops with exit code 1."""
    if len(argv) == 1 and argv[0].lower() in ("--help", "-h"):
        sys.stdout.write(help_text())
        sys.stdout.flush()
        return 1  # FragPipe really exits 1 after printing its help
    a: dict = {"headless": False, "dry": False}
    takes = {"--workflow": "workflow", "--manifest": "manifest", "--ram": "ram", "--threads": "threads",
             "--workdir": "workdir", "--config-tools-folder": "tools", "--config-diann": "diann",
             "--config-python": "python"}
    i = 0
    while i < len(argv):
        opt = argv[i].lower()
        if opt == "--headless":
            a["headless"] = True
        elif opt == "--dry-run":
            a["dry"] = True
        elif opt in takes and i + 1 < len(argv):
            i += 1
            a[takes[opt]] = argv[i].strip()
        else:
            print(f"Cannot recognize the argument {argv[i]}", file=sys.stderr, flush=True)
            return 1
        i += 1
    return a


def _props(text: str) -> dict[str, str]:
    from ionomos.fragpipe import read_properties

    return read_properties(text)


def _on(props: dict[str, str], key: str, default: bool = False) -> bool:
    return props[key].strip().lower() == "true" if key in props else default


def fake_fragpipe(argv: list[str]) -> int:
    from ionomos import fragpipe as fp

    a = _parse(argv)
    modes = modes_for(Path(a["workdir"]) if isinstance(a, dict) and a.get("workdir") else None)
    if "no-java" in modes:  # Gradle's start script, before Java (and so FragPipe) ever starts
        print("\nERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH.\n\n"
              "Please set the JAVA_HOME variable in your environment to match the\nlocation of your Java "
              "installation.", file=sys.stderr, flush=True)
        return 1
    if isinstance(a, int):
        return a
    _out("FAKE FragPipe (the Ionomos testbed's stand-in): nothing is searched, every number below is made up")

    # ---- Fragpipe.main0 --------------------------------------------------------------------------------------
    def stop(msg: str) -> int:
        print(msg, file=sys.stderr, flush=True)
        return 1

    if not a["headless"]:
        if any(a.get(k) for k in ("workflow", "manifest", "workdir")):
            return stop("It looks like you want to run FragPipe in headless mode, but you did not add --headless "
                        "flag. Please double check your command.")
        return stop("FAKE FragPipe has no window: run it with --headless")
    if not a.get("workflow") or not Path(a["workflow"]).is_file():
        return stop("Please provide --workflow <path to workflow file> in the headless mode.")
    if not a.get("manifest") or not Path(a["manifest"]).is_file():
        return stop("Please provide --manifest <path to manifest file> in the headless mode.")
    for key, what in (("ram", "ram is smaller than 0."), ("threads", "Number of threads is smaller than 0.")):
        if a.get(key) is not None:
            try:
                if int(a[key]) < 0:
                    return stop(what)
            except ValueError:
                return stop(f'Exception in thread "main" java.lang.NumberFormatException: For input string: "{a[key]}"')
    if not a.get("workdir"):
        return stop("The path to workdir does not look right.")
    if a.get("tools") is not None and not Path(a["tools"]).exists():
        return stop(f"Tools folder path {a['tools']} does not seem right.")
    if a.get("diann") is not None and not Path(a["diann"]).is_file() and os.environ.get("IONOMOS_FAKE_FP_STRICT") == "1":
        return stop(f"DIA-NN executable file path {a['diann']} does not seem right.")
    if a.get("python") is not None and not Path(a["python"]).exists():
        return stop(f"Python path {a['python']} does not seem right.")
    if "silent-exit0" in modes:
        return 0

    wd = Path(a["workdir"]).resolve()
    wf_text = Path(a["workflow"]).read_text(encoding="utf-8", errors="replace")
    props = _props(wf_text)
    strict = os.name == "nt" or os.environ.get("IONOMOS_FAKE_FP_STRICT") == "1"

    # ---- FragpipeRun.run: the checks ---------------------------------------------------------------------------
    if strict and re.search(r"\s", str(wd)):
        _error("Output directory path contains whitespace characters. Some programs in the pipeline might not "
               "work properly in this case. Please change output directory to one without spaces.")
        return 1

    # the manifest: path, experiment, bioreplicate, data type; files that don't exist are dropped without a word
    files: list[tuple[Path, str, str, str]] = []
    for ln in Path(a["manifest"]).read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.startswith(("//", "#")):
            continue
        parts = ln.strip().split("\t")
        if not Path(parts[0]).exists():
            continue
        exp = fp.fp_experiment(parts[1]) if len(parts) > 1 and parts[1].strip() else ""
        rep = parts[2].strip() if len(parts) > 2 else ""
        dtype = {"dia": "DIA", "gpf-dia": "GPF-DIA", "dia-quant": "DIA-Quant", "dia-lib": "DIA-Lib",
                 "dda+": "DDA+"}.get(parts[3].strip().lower(), "DDA") if len(parts) > 3 else "DDA"
        files.append((Path(parts[0]), exp, rep, dtype))
    if not files:
        # what the real FragPipe prints with no usable file is not known; this line is the fake's own
        _error("FAKE FragPipe: no LC-MS file of the manifest exists")
        return 1
    names = [f[0].name for f in files]
    if len(set(names)) != len(names):
        _error("Some input LCMS files have the same name, even though located in different folders:")
        return 1

    is_dia = any(t.startswith(("DIA", "GPF")) for *_x, t in files)
    run_tmt = _on(props, "tmtintegrator.run-tmtintegrator", default="tmt" in Path(a["workflow"]).name.lower())
    run_diann = _on(props, "diann.run-dia-nn", default=is_dia)
    run_speclib = _on(props, "speclibgen.run-speclibgen", default=False)
    flat = run_diann or run_speclib  # InputLcmsFile.getGroup: no per-experiment folders then

    def group(exp: str, rep: str) -> str:
        if flat:
            return ""
        return (f"{exp or 'exp'}_{rep}" if rep else exp)

    groups: dict[str, list[Path]] = {}
    for path, exp, rep, _t in files:
        groups.setdefault(group(exp, rep), []).append(path)

    db = props.get("database.db-path", "")
    if not db.strip():
        _error("FASTA file path is empty or the file is corrupted")
        return 1
    if not Path(db).is_file():
        _error(f"Could not find fasta file at: {db}")
        return 1
    if "msfragger" in modes:
        _error("MSFragger is not valid but it is enabled in the workflow. Please disable MSFragger or fix it in "
               "the Config tab.")
        return 1
    tag = props.get("database.decoy-tag", "rev_")
    info = fp.fasta_info(Path(db), tag or "rev_")
    validation = any(_on(props, k) for k in ("percolator.run-percolator", "peptide-prophet.run-peptide-prophet",
                                             "phi-report.run-report"))
    if validation and info["entries"] and info["gb"] * 1e9 < fp.BIG_FASTA_BYTES:
        share = info["decoys"] / info["entries"]
        if share <= 0:
            _error("No decoys found in the FASTA file.")
            return 1
        if share >= 1:
            _error("All FASTA entries seem to be decoys.")
            return 1
        if share < 0.4 or share > 0.6:
            _error(f"FASTA file contains {100 * share:.1f}% decoys.")
            return 1
    if "speclib" in modes or (run_speclib and a.get("python") is not None and not Path(a["python"]).exists()):
        _error("Spectral Library Generation module was not configured correctly. Please make sure that Python "
               "and FragPipe-SpecLib have been installed.")
        return 1

    # TMT annotations: one file ending in annotation.txt in the folder holding all of a plex's files, else
    # FragPipe makes its own <workdir>/<plex>/<plex>_annotation.txt with "<channel> <plex>_<channel>"
    annotations: dict[str, Path | None] = {}
    label = props.get("tmtintegrator.channel_num", "TMT-10")
    if run_tmt:
        by_exp: dict[str, list[Path]] = {}
        for path, exp, _rep, _t in files:
            by_exp.setdefault(exp, []).append(path)
        seen: set[str] = set()
        for exp, paths in by_exp.items():
            folders = {p.resolve().parent for p in paths}
            found = None
            if len(folders) == 1:
                hits = sorted(p for p in next(iter(folders)).iterdir() if p.name.endswith("annotation.txt"))
                found = hits[0] if len(hits) == 1 else None
            annotations[exp] = found
            if found is None:
                continue
            rows = [ln.split() for ln in found.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            if any(len(r) != 2 for r in rows):
                bad = next(" ".join(r) for r in rows if len(r) != 2)
                _out(f'Exception in thread "main" java.lang.RuntimeException: Invalid line in annotation file '
                     f"{found}: {bad}")
                return 1
            if label in TMT_CHANNELS and len(rows) != len(TMT_CHANNELS[label]):
                _error("Number of the samples in the annotation file does not match the number of channels in "
                       "the 'label type' of the Quant (Isobaric) tab.")
                return 1
            for _ch, sample in rows:
                if sample.lower() != "na" and sample in seen:
                    _error("Duplicate samples found in annotation files. The sample names must be unique among "
                           "all experiments.")
                    return 1
                seen.add(sample)

    # ---- what would run ------------------------------------------------------------------------------------------
    java = r"C:\FragPipe\FragPipe-24.0\jre\bin\java.exe" if os.name == "nt" else "/opt/fragpipe/jre/bin/java"
    tools = Path(a["tools"]) if a.get("tools") else Path(java).parent.parent.parent / "tools"
    phil = tools / "Philosopher" / "philosopher-v5.1.3-RC9.exe"
    ram = a.get("ram") or "0"
    threads = a.get("threads") or "31"
    gdirs = [wd / g if g else wd for g in groups]
    steps: list[tuple[str, Path | None, str, list[str]]] = []

    def add(name: str, where: Path | None, cmd: str, said: list[str] | None = None) -> None:
        steps.append((name, where, cmd, said or []))

    add("CheckCentroid", None, f"{java} -Xmx{ram}G -cp {tools.parent / 'lib'}/* org.nesvilab.fragpipe.util.CheckCentroid "
                               f"{files[0][0]} {threads}", ["Done in 0.1 s."])
    phil_done = ['time="{t}" level=info msg=Done']
    for g in gdirs + ([wd] if wd not in gdirs else []):
        add("WorkspaceCleanInit", g, f"{phil} workspace --clean --nocheck", phil_done)
        add("WorkspaceCleanInit", g, f"{phil} workspace --init --nocheck --temp {wd / 'tmp'}", phil_done)
    add("MSFragger", wd, f"{java} -jar -Dfile.encoding=UTF-8 -Xmx{ram}G {tools / 'MSFragger-4.4.1' / 'MSFragger-4.4.1.jar'} "
                         f"{wd / 'fragger.params'} " + " ".join(str(f[0]) for f in files),
        ["MSFragger version MSFragger-4.4.1", "Batmass-IO version 1.36.5", "timsdata library version timsdata-2-21-0-4",
         "(c) University of Michigan",
         "RawFileReader reading tool. Copyright (c) 2016 by Thermo Fisher Scientific, Inc. All rights reserved.",
         f"JVM started with {ram} GB memory", "Checking database...", "Checking spectral files...",
         "***********************************FIRST SEARCH DONE IN 0.012 MIN**********************************",
         "************************************MAIN SEARCH DONE IN 0.020 MIN***********************************",
         "*******************************TOTAL TIME 0.468 MIN*****"])
    add("MSFragger move pepxml", None, f"{java} -cp {tools.parent / 'lib'}/fragpipe-{VERSION}.jar org.nesvilab.utils.FileMove "
                                       f"--no-err {files[0][0].with_suffix('.pepXML')} {gdirs[0]}")
    add("MSFragger move pin", None, f"{java} -cp {tools.parent / 'lib'}/fragpipe-{VERSION}.jar org.nesvilab.utils.FileMove "
                                    f"--no-err {files[0][0].with_suffix('.pin')} {gdirs[0]}")
    if _on(props, "msbooster.run-msbooster", default=True):
        add("MSBooster", wd, f"{java} -Xmx{ram}G -cp {tools / 'MSBooster-1.4.14.jar'} mainsteps.MainClass --paramsList "
                             f"{wd / 'msbooster_params.txt'}",
            ["{d} [INFO] - MSBooster v1.4.14", f"{{d}} [INFO] - Using {threads} threads", "{d} [INFO] - Done in 0s"])
    for g in gdirs:
        add("Percolator", g, f"{tools / 'percolator_3_7_1' / 'windows' / 'percolator.exe'} --only-psms --no-terminate "
                             f"--post-processing-tdc --num-threads {threads}",
            ["Percolator version 3.07.1, Build Date May 21 2024", "Processing took 0.2650 cpu seconds or 0 seconds wall clock time."])
        add("Percolator: Convert to pepxml", g, f"{java} -cp {tools.parent / 'lib'}/* "
                                                f"org.nesvilab.fragpipe.tools.percolator.PercolatorOutputToPepXML")
        add("Percolator delete temp", None, f"{java} -cp {tools.parent / 'lib'}/fragpipe-{VERSION}.jar "
                                            f"org.nesvilab.utils.FileDelete {g / 'percolator_target_psms.tsv'}")
    add("ProteinProphet", wd, f"{phil} proteinprophet --maxppmdiff 2000000 --output combined "
                              f"{wd / 'filelist_proteinprophet.txt'}",
        ['time="{t}" level=info msg="Executing ProteinProphet  v5.1.3-RC9"', 'time="{t}" level=info msg=Done'])
    for g in gdirs:
        add("PhilosopherDbAnnotate", g, f"{phil} database --annotate {db} --prefix {tag}",
            ['time="{t}" level=info msg="Executing Database  v5.1.3-RC9"', 'time="{t}" level=info msg=Done'])
        add("PhilosopherFilter", g, f"{phil} filter --sequential --prot 0.01 --tag {tag} --pepxml {g} --protxml "
                                    f"{wd / 'combined.prot.xml'} --razor",
            ['time="{t}" level=info msg="Executing Filter  v5.1.3-RC9"',
             'time="{t}" level=info msg="Converged to 1.00 % FDR with 240 PSMs" decoy=2 threshold=0.5 total=242',
             'time="{t}" level=info msg=Done'])
        add("PhilosopherReport", g, f"{phil} report",
            ['time="{t}" level=info msg="Executing Report  v5.1.3-RC9"', 'time="{t}" level=info msg="Creating reports"',
             'time="{t}" level=info msg=Done'])
    for g in gdirs + ([wd] if wd not in gdirs else []):
        add("WorkspaceClean", g, f"{phil} workspace --clean --nocheck", phil_done)
    ionquant = (_on(props, "ionquant.run-ionquant", default=True)
                and _on(props, "quantitation.run-label-free-quant", default=not flat and not run_tmt)) or run_tmt
    if ionquant and not flat:
        add("IonQuant", wd, f"{java} -Xmx{ram}G -Dlibs.bruker.dir={tools / 'MSFragger-4.4.1' / 'ext' / 'bruker'} "
                            f"-Dlibs.thermo.dir={tools / 'MSFragger-4.4.1' / 'ext' / 'thermo'} -cp "
                            f"{tools / 'IonQuant-1.11.20.jar'} ionquant.IonQuant --threads {threads} --filelist "
                            f"{wd / 'filelist_ionquant.txt'} --modlist {wd / 'modmasses_ionquant.txt'}",
            ["IonQuant version IonQuant-1.11.20", "Batmass-IO version 1.36.5", "(c) University of Michigan",
             f"JVM started with {ram} GB memory",
             "{d} [INFO] - Collecting variable modifications from all psm.tsv files...",
             "{d} [INFO] - Loading and indexing all psm.tsv files...", "{d} [INFO] - Updating Philosopher's tables...",
             "{d} [INFO] - Combining experiments and estimating protein intensity...", "{d} [INFO] - Done!"])
    if run_tmt:
        add("TmtIntegrator", wd, f"{java} -Xmx{ram}G -jar {tools / 'TMT-Integrator-6.1.3.jar'} "
                                 f"{wd / 'tmt-integrator-conf.yml'} " + " ".join(str(g / 'psm.tsv') for g in gdirs),
            ["Loading parameters and psm.tsv files...", "Finish!!!"])
    if run_speclib:
        add("SpecLibGen", wd, f"python -u {tools / 'speclib' / 'gen_con_spec_lib.py'} {db} {wd} unused {wd} True unused "
                              f"use_easypqp noiRT:noIM {threads}",
            ["Spectral library building", "Done generating spectral library"])
    if run_diann:
        add("DIA-Quant run DIA-NN", wd, f"{a.get('diann') or tools / 'diann' / '1.8.2_beta_8' / 'windows' / 'DiaNN.exe'} "
                                        f"--lib library.tsv --threads {threads} --verbose 1 --out "
                                        f"{Path('dia-quant-output') / 'report.tsv'} --qvalue 0.01 --matrix-qvalue 0.01 "
                                        f"--matrices --no-prot-inf --smart-profiling --no-quant-files --peak-center "
                                        f"--no-ifs-removal --report-lib-info --cfg {wd / 'filelist_diann.txt'}--",
            ["DIA-NN 1.8.2 beta 8 (Data-Independent Acquisition by Neural Networks)",
             "Compiled on Dec  1 2022 14:47:06", f"Thread number set to {threads}", "[0:00] Loading spectral library library.tsv",
             f"[0:01] {len(files)} files will be processed", "[0:02] Cross-run analysis", "[0:02] Stats report saved to "
             f"{Path('dia-quant-output') / 'report.stats.tsv'}", "Finished"])
        add("DIA-Quant propagate information", wd, f"{java} -cp {tools.parent / 'lib'}/* "
                                                   f"org.nesvilab.fragpipe.tools.diann.Propagation {wd}")

    # ---- the console, up to the run ----------------------------------------------------------------------------
    _out("\n".join(SYSTEM_LINES) + "\n")
    _out()
    _out("Version info:\n" + f"FragPipe version {VERSION}\n" + "".join(f"{n} version {v}\n" for n, v in TOOL_VERSIONS))
    _out()
    lcms = "LCMS files:\n"
    for g, paths in groups.items():
        lcms += f"  Experiment/Group: {g}\n  (if \"spectral library generation\" is enabled, all files will be analyzed together)\n"
        for p in paths:
            dtype = next(t for q, _e, _r, t in files if q == p)
            lcms += f"  - {p}\t{dtype}\n"
    _out(lcms)
    _out()
    _out(f"{len(steps)} commands to execute:")
    for name, where, cmd, _said in steps:
        _out(name + (f" [Work dir: {where}]" if where else ""))
        _out(cmd)
    _out(DASHES)
    _out()
    _out("Execution order:\n")
    order: list[tuple[str, Path]] = [("START", wd)]
    for name, where, _cmd, _said in steps:
        base = name.split(" ")[0].rstrip(":") if name.startswith(("MSFragger", "Percolator", "DIA-Quant")) else name
        if (base, where or wd) not in order:
            order.append((base, where or wd))
    for name, where in order:
        _out(f"    Cmd: [{name}], Work dir: [{where}]")
    _out()
    _out(DASHES)
    _out()
    _out(f"~~~~~~Sample of {db}~~~~~~~")
    headers = sorted(ln.strip() for ln in Path(db).read_text(encoding="utf-8", errors="replace").splitlines()
                     if ln.startswith(">"))
    for h in headers[::max(1, (len(headers) - 1) // 20 if len(headers) > 20 else 1)]:
        _out(h)
    _out(DASHES)
    _out()
    if run_tmt:
        _out("~~~~~~annotation files~~~~~~~")
        for exp, found in annotations.items():
            shown = found or wd / exp / f"{exp}_annotation.txt"
            _out(f"{shown}:")
            if found:
                for ln in found.read_text(encoding="utf-8", errors="replace").splitlines():
                    _out(ln.strip())
            elif a["dry"]:
                _out(f"Cannot read {shown}")
            else:
                for ch in TMT_CHANNELS.get(label, TMT_CHANNELS["TMT-10"]):
                    _out(f"{ch} {exp}_{ch}")
        _out(DASHES)
        _out()
    if a["dry"]:
        _out("\nIt's a dry-run, not running the commands.\n")
        _citations()
        return 0

    wd.mkdir(parents=True, exist_ok=True)
    for g in gdirs:
        g.mkdir(parents=True, exist_ok=True)
    # written before any step, as FragPipe does: the settings used, the file list, the job
    used = wf_text.rstrip("\n") + f"\nworkdir={str(wd)}\nworkflow.threads={threads}\nworkflow.ram={ram}\n"
    (wd / "fragpipe.workflow").write_text(used, encoding="utf-8")
    (wd / "fragpipe-files.fp-manifest").write_text(
        "\n".join(f"{p}\t{e}\t{r}\t{t}" for p, e, r, t in files), encoding="utf-8")
    (wd / "fragpipe.job").write_text(f"[runtime-save]\nworkflow={wd / 'fragpipe-workflow.workflow'}\nmanifest="
                                     f"{wd / 'fragpipe-files.fp-manifest'}\noutput={wd}\ntools={a.get('tools') or ''}\n"
                                     f"fasta={db}\nram={ram}\nthreads={threads}\n", encoding="utf-8")
    _out("~~~~~~~~~ fragpipe.config ~~~~~~~~~")
    _out(used.rstrip("\n"))
    _out("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    if "huge-log" in modes:  # a talkative run: lots of lines, and one without an end for a long while
        mb = float(os.environ.get("IONOMOS_FAKE_FP_LOG_MB", "3"))
        line = "MSFragger: processed scan block " + "0123456789" * 7 + "\n"
        for _ in range(int(mb * 1_000_000 / len(line))):
            sys.stdout.write(line)
        sys.stdout.write("x" * 1_000_000 + "\n")
        sys.stdout.flush()

    # ---- the run ---------------------------------------------------------------------------------------------------
    if "child" in modes:  # something FragPipe started that outlives a careless kill (Java -> MSFragger)
        log_fh = open(wd / "child.log", "wb")  # noqa: SIM115 - handed to the child; never a pipe
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], stdout=log_fh,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        (wd / "child.pid").write_text(str(child.pid), encoding="utf-8")
    total = float(os.environ.get("IONOMOS_FAKE_FP_SECONDS", "4"))
    started = time.monotonic()
    runtimes: dict[str, float] = {}

    def fail(name: str, code: int, remaining: int, exit_code: int | None = None) -> int:
        _out(f"Process '{name}' finished, exit code: {code}")
        _out("Process returned non-zero exit code, stopping")
        _out(f"\n~~~~~~~~~~~~~~~~~~~~\nCancelling {remaining} remaining tasks")
        _save_log(wd)
        return code if exit_code is None else exit_code

    for i, (name, where, cmd, said) in enumerate(steps):
        t0 = time.monotonic()
        _out(name + (f" [Work dir: {where}]" if where else ""))
        _out(cmd)
        now = datetime.now()
        dies = name == "MSFragger" and bool(modes & {"oom", "step-fail-exit0", "step-fail-neg-exit0", "cancel-exit0",
                                                      "hang", "killed", "disk-full", "raw-vanished"})
        for ln in (said[:8] if dies else said):  # a step that dies doesn't print its closing lines
            _out(ln.replace("{t}", f"{now:%H:%M:%S}").replace("{d}", f"{now:%Y-%m-%d %H:%M:%S}"))
        time.sleep(total / len(steps))
        left = len(steps) - i - 1 + 1  # the steps not started, and the finalizer
        if name == "MSFragger":
            if "oom" in modes:
                _out('Exception in thread "main" java.lang.OutOfMemoryError: Java heap space\n'
                     "\tat java.base/java.util.Arrays.copyOf(Unknown Source)\n\tat umich.ms.fragger.Main.main(Main.java:123)")
                return fail(name, 1, left)
            if "step-fail-exit0" in modes:
                (wd / "partial.txt").write_text("x", encoding="utf-8")
                return fail(name, 137, left, exit_code=0)
            if "step-fail-neg-exit0" in modes:
                (wd / "partial.txt").write_text("x", encoding="utf-8")
                return fail(name, -11, left, exit_code=0)
            if "cancel-exit0" in modes:
                _out(f"\n~~~~~~~~~~~~~~~~~~~~\nCancelling {left} remaining tasks")
                return 0
            if "hang" in modes:  # a tool waiting on something that never comes: no output, no end
                while True:
                    time.sleep(60)
            if "killed" in modes:  # Task Manager / taskkill /F: no last words from anybody
                _die()
            if "disk-full" in modes:
                (wd / f"{files[0][0].stem}.pepXML").write_bytes(b"")
                _out("java.io.IOException: There is not enough space on the disk\n\tat java.base/java.io."
                     "FileOutputStream.writeBytes(Native Method)\n\tat umich.ms.fragger.Main.main(Main.java:123)")
                return fail(name, 1, left)
            if "raw-vanished" in modes:
                _out(f"java.io.FileNotFoundException: {files[0][0]} (The system cannot find the file specified)\n"
                     f"\tat java.base/java.io.FileInputStream.open0(Native Method)")
                return fail(name, 1, left)
            if "runaway-log" in modes:
                while True:
                    _out("Checking spectral files... " + "." * 1000)
                    time.sleep(0.001)
        if name == "PhilosopherReport" and "garbled-log" in modes:
            where_cp = "C:\\Users\\Müller\\Proben\\psm.tsv"  # a Windows path, also when the fake runs elsewhere
            _raw(b"\x1b[33mWARN\x1b[0m Pr\xfcfe Eingabe f\xfcr 5 \xb5L Probe\r\n")  # cp1252, colour codes
            _raw("Reading spectra 50%\n".encode("utf-16-le"))  # a tool writing UTF-16
            _raw(bytes(range(0, 32)) + b"\xff\xfe\x81\x9d\x00\x00garbage\n")  # a crashed tool's binary junk
            _raw((f'time="{now:%H:%M:%S}" level=error msg="Cannot write file. cannot create report file, open '
                  f"{where_cp}: The process cannot access the file because it is being used by another "
                  f'process."\n').encode("cp1252"))
            return fail(name, 1, left)
        if name == "PhilosopherReport" and "locked" in modes:
            _out(f'time="{now:%H:%M:%S}" level=error msg="Cannot write file. cannot create report file, open '
                 f"{str(where / 'psm.tsv').replace(chr(92), chr(92) * 2)}: The process cannot access the file "
                 f'because it is being used by another process."')
            return fail(name, 1, left)
        if name == "IonQuant" and any("FAKEFAIL" in str(f[0]) for f in files):
            _out(f"{now:%Y-%m-%d %H:%M:%S} [ERROR] - IonQuant crashed (this sample fails on purpose)")
            return fail(name, 1, left)
        if name == "DIA-Quant run DIA-NN" and ("diann" in modes or any("FAKEFAIL" in str(f[0]) for f in files)):
            _out("ERROR: DIA-NN crashed (this sample fails on purpose)" if "diann" not in modes
                 else "ERROR: cannot load the spectral library (acted out by the fake)")
            return fail(name, 1, left)
        _out(f"Process '{name}' finished, exit code: 0")
        runtimes[name] = runtimes.get(name, 0.0) + (time.monotonic() - t0) / 60

    # ---- the files a run leaves ------------------------------------------------------------------------------------
    _write_outputs(wd, files, groups, gdirs, props, annotations, label, run_tmt, flat, Path(a["workflow"]).name)
    _spoil_tables(wd, modes)

    _citations()
    _out("\nTask Runtimes:")
    for name, minutes in runtimes.items():
        _out(f"  {name}: {minutes:.2f} minutes")
    _out("  Finalizer Task: 0.00 minutes")
    if "no-done-line" not in modes:
        _out("\n=============================================================ALL JOBS DONE IN "
             f"{(time.monotonic() - started) / 60:.1f} MINUTES"
             "=============================================================")
    _save_log(wd)
    return 0


def _raw(data: bytes) -> None:
    """Bytes straight to the console, as a tool that doesn't write UTF-8 does."""
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _die() -> None:
    """Ended from outside: SIGKILL where there are signals, else the exit code taskkill /F leaves (1)."""
    sys.stdout.flush()
    if os.name != "nt":
        import signal

        os.kill(os.getpid(), signal.SIGKILL)
    os._exit(1)


MAIN_TABLES = ("combined_modified_peptide_label_quant.tsv", "tmt-report/abundance_gene_MD.tsv",
               "dia-quant-output/report.pg_matrix.tsv")


def _cut(path: Path) -> None:
    """Keep a table up to the middle of its third row (or second), as a write that stopped half-way leaves it."""
    data = path.read_bytes()
    rows = data.split(b"\n")
    keep = min(3, len(rows) - 1)
    head = b"\n".join(rows[:keep]) + b"\n"
    tabs = [i for i, ch in enumerate(rows[keep]) if ch == 9]
    path.write_bytes(head + rows[keep][: tabs[len(tabs) // 2] if tabs else len(rows[keep]) // 2])


def _spoil_tables(wd: Path, modes: set[str]) -> None:
    main = next((wd / t for t in MAIN_TABLES if (wd / t).is_file()), None)
    if main is not None:
        if "empty-table" in modes:
            main.write_bytes(b"")
        elif "header-only" in modes:
            main.write_bytes(main.read_bytes().split(b"\n", 1)[0] + b"\n")
        elif "truncated-table" in modes:
            _cut(main)
        elif "missing-table" in modes:
            main.unlink()
    if "truncated-psm" in modes:
        for psm in wd.glob("*/psm.tsv"):
            _cut(psm)


def _citations() -> None:
    _out("\nPlease cite:")
    _out("(Any searches) MSFragger: ultrafast and comprehensive peptide identification in mass spectrometry–based "
         "proteomics. Nat Methods. 14:513 (2017)")
    _out("(FDR filtering and reporting) Philosopher: a versatile toolkit for shotgun proteomics data analysis. "
         "Nat Methods. 17:869 (2020)")


def _save_log(wd: Path) -> None:
    """FragPipe saves its console as log_<date>_<time>.txt in the output folder when a run ends, good or bad.
    The fake can't re-read its own stdout, so the file says so."""
    try:
        wd.mkdir(parents=True, exist_ok=True)
        (wd / f"log_{time.strftime('%Y-%m-%d_%H-%M-%S')}.txt").write_text(
            "FAKE FragPipe log (the real file repeats the console output)\n", encoding="utf-8")
    except OSError:
        pass


def _write_outputs(wd: Path, files, groups, gdirs, props, annotations, label: str, run_tmt: bool, flat: bool,
                   workflow_name: str) -> None:
    from ionomos import testbed

    # intermediate files a search leaves beside its tables (names as in FragPipe's output documentation)
    (wd / "fragger.params").write_text("# FAKE MSFragger parameters\n", encoding="utf-8")
    (wd / "filelist_proteinprophet.txt").write_text(
        "\n".join(str(g / f"interact-{p.stem}.pep.xml") for g, ps in zip(gdirs, groups.values(), strict=True) for p in ps),
        encoding="utf-8")
    (wd / "combined.prot.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<protein_summary/>\n', encoding="utf-8")
    for g, paths in zip(gdirs, groups.values(), strict=True):
        for p in paths:
            (g / f"interact-{p.stem}.pep.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n<msms_pipeline_analysis/>\n', encoding="utf-8")
        (g / "protein.fas").write_text(">sp|FAKE1|FAKE1_HUMAN fake protein 1\nMKVLAAGIVGLLLAC\n", encoding="utf-8")
        (g / "filter.log").write_text("FAKE philosopher filter log\n", encoding="utf-8")
    if not flat:
        (wd / "filelist_ionquant.txt").write_text(
            "flag\tvalue\n" + "".join(f"--psm\t{Path(g.name) / 'psm.tsv'}\n" for g in gdirs)
            + f"--specdir\t{files[0][0].parent}\n", encoding="utf-8")
        (wd / "modmasses_ionquant.txt").write_text("15.9949\n42.0106\n", encoding="utf-8")

    rows = [[str(p), e, r or "1", t] for p, e, r, t in files]  # as the manifest FragPipe read
    testbed._fake_results(wd, rows, workflow_name, annotations=annotations if run_tmt else None, label=label)

    # experiment_annotation.tsv (ToolingUtils): the sample sheet FragPipe-Analyst reads
    if run_tmt:
        lines = ["plex\tchannel\tsample\tsample_name\tcondition\treplicate"]
        for exp, found in annotations.items():
            pairs = ([ln.split() for ln in found.read_text(encoding="utf-8").splitlines() if ln.strip()] if found
                     else [[ch, f"{exp}_{ch}"] for ch in TMT_CHANNELS.get(label, TMT_CHANNELS["TMT-10"])])
            for ch, sample in pairs:
                if sample.lower() == "na":
                    continue
                parts = sample.split("_")
                cond = parts[1] if len(parts) == 3 and not parts[2].isdigit() else parts[0]
                lines.append(f"{exp}\t{ch}\t{sample}\t{sample}\t{cond}\t1")
    else:
        lines = ["file\tsample\tsample_name\tcondition\treplicate"]
        for p, e, r, _t in files:
            g = f"{e}_{r}" if r else e
            lines.append(f"{p}\t{p if flat else g}\t{g}\t{e.split('_')[0]}\t{r or 1}")
    (wd / "experiment_annotation.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # sdrf.tsv (SDRFtable, type Default): one row per file (per file and channel for TMT), no conditions, written
    # when the workflow says workflow.misc.save-sdrf=true (FragPipe 24's stock workflows do)
    if props.get("workflow.misc.save-sdrf", "false").strip().lower() == "true":
        head = ["source name", "characteristics[organism]", "characteristics[disease]", "characteristics[organism part]",
                "characteristics[cell type]", "characteristics[biological replicate]", "technology type", "assay name",
                "comment[data file]", "comment[technical replicate]", "comment[fraction identifier]", "comment[label]",
                "comment[instrument]", "comment[precursor mass tolerance]", "comment[fragment mass tolerance]",
                "comment[cleavage agent details]"]
        na = "not available"
        out = ["\t".join(head)]
        for p, _e, r, _t in files:
            for lab in ([f"TMT{ch}" for ch in TMT_CHANNELS.get(label, TMT_CHANNELS["TMT-10"])] if run_tmt else [na]):
                out.append("\t".join([na, na, na, na, na, na, na, na, p.name, r, na, lab, na, "20 ppm",
                                      "20 ppm", "NT=Trypsin/P"]))
        (wd / "sdrf.tsv").write_text("\n".join(out), encoding="utf-8")
