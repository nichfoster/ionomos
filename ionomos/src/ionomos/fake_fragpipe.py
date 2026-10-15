"""
The testbed's stand-in for FragPipe (`ionomos fake-fragpipe ...`, reached through testbed.write_fake_launcher).

It takes FragPipe's headless options, makes FragPipe's checks in FragPipe's order, prints what FragPipe prints
and leaves the files FragPipe leaves, so that Ionomos' runner and parsers are tested against the real shapes.
What is copied, and from where (FragPipe 24.0 source, github.com/Nesvilab/FragPipe tag 24.0, checked against
23.1, and logs users attached to FragPipe's issue tracker):

    options, usage text, exit codes         FragPipeMain.main, Fragpipe.main0 / help()
    the checks before a run                 Fragpipe.main0, FragpipeRun.run / validateWd / prepareWd /
                                            checkInputLcmsFiles2 / checkFasta / checkDbConfig
    the manifest as FragPipe reads it       TabWorkflow.manifestLoad / showSkippedFiles
    experiment names, group folders         InputLcmsFile (only A-Z a-z 0-9 _; <experiment>_<bioreplicate>;
                                            an empty data type guessed from the path)
    the TMT annotation lookup and checks    TmtiPanel, CmdTmtIntegrator.configure
    console layout and step lines           FragpipeRun.run / printProcessDescription, ProcessBuilderInfo
                                            .toRunnable, ProcessManager
    ERROR lines ("<time> ERROR - text")     logback.xml (root level error: warnings are never printed);
                                            dialogs in headless mode: SwingUtils.showDialog
    console bytes and line ends             Java 17 with no -Dfile.encoding (FragPipe's build.gradle): the
                                            Windows code page (cp1252, '?' for what it lacks) and CRLF at the
                                            end of each println, LF inside a multi-line message
    log_<date>_<time>.txt                   TabRun.saveLogToFile, MessageSaveLog (the console's text, UTF-8)
    fragpipe.job                            TabBatch.saveJobsToFile, BatchRun.toString
    fragpipe-files.fp-manifest              TabWorkflow.manifestSave (written by a dry run too)
    output folder names                     CmdIonquant, CmdTmtIntegrator (tmt-report), CmdDiann (dia-quant-output)
    DIA-NN's steps and file list            CmdDiann (2.x: plot with diann-stats, Parquet -> TSV; the
                                            <raw>_uncalibrated.mzML beside each raw in filelist_diann.txt)
    sdrf.tsv                                SDRFtable, TabMsfragger.getSDRFenzymes / getSDRFmods / tolerances
    experiment_annotation.tsv               ToolingUtils.generateLFQExperimentAnnotation /
                                            writeIsobaricQuantExperimentAnnotation (written when IonQuant or
                                            DIA-NN is configured, before any step)
    TMT label types, the reference check    QuantLabel, CmdTmtIntegrator (tmtkit.LABEL_TYPES is the one source)
    TMT psm.tsv reporter columns            TMT-Integrator's PsmPreProcessor ("Intensity <sample>", "Intensity NA")
    tmt-report/ file names and columns      FragPipe's output guide ([abundance|ratio]_[level]_[norm].tsv)
    combined_protein.tsv layout             a real FragPipe file (tests/golden/example/AP-MS, CC BY 4.0)
    <raw>_uncalibrated.mzML beside the raws the lab PC's file inventory (reference/pc-inventory/2026-09-15)
    tool versions                           docs/REAL_RUNS.md job 2 (the PC's console): DIA-NN 2.3.2

What differed before round 4, and is now as the source says (source in brackets):
    - a manifest file that does not exist stops FragPipe ("Some loaded files don't exist. Please double
      check." and the paths, exit 1); it used to be dropped without a word [TabWorkflow.showSkippedFiles]
    - a bioreplicate that is not a Java int (e.g. a 14-digit Xcalibur stamp) drops that manifest line with
      "Manifest file contained some badly formatted lines", and the run goes on without it; "01" is 1
      [TabWorkflow.manifestLoad] -- this is what lost two runs of the lab's first real search (REAL_RUNS.md)
    - dialog messages keep their <b> tags and lose their line breaks in headless mode [SwingUtils.stripHtml]
    - the output folder and the group folders exist before the file and FASTA checks [FragpipeRun.prepareWd]
    - a dry run writes fragpipe-files.fp-manifest, fragpipe.workflow and fragpipe.job (and the file lists
      when the output folder exists) [FragpipeRun.run, CmdIonquant, CmdProteinProphet, CmdMSBooster]
    - fragpipe.job is a tab-separated table, not an ini section [TabBatch.saveJobsToFile]
    - log_<date>.txt holds the console text; a name already taken gets _1 [TabRun.saveLogToFile]; a check
      that fails after the output folder exists leaves an empty one (the shutdown hook) [Fragpipe.headless]
    - experiment_annotation.tsv and the file lists are written before the first step, not after the last
    - experiment_annotation.tsv for TMT reads conditions FragPipe's way: DMSO_1_126 is condition "1",
      replicate 126 [ToolingUtils.writeIsobaricQuantExperimentAnnotation]
    - "FASTA file contains 20% decoys." (DecimalFormat ##.#), and "Database looks to be empty"
      [FragpipeRun.checkDbConfig]
    - two raws with one name: FragPipe's whole message, <html> and file list included [checkInputLcmsFiles2]
    - the execution-order lines name each command once, with the output folder [FragpipeRun.run]
    - --ram 0 shows the memory FragPipe picks in -Xmx; --threads 0 is every core [OsUtils, Fragpipe.main0]
    - DIA: DIA-NN 2.3.2 (the PC's), its steps, --cfg without "--" on Windows, report columns named by the
      _uncalibrated.mzML files, a psm.tsv in the flat output folder [CmdDiann, REAL_RUNS.md]
    - an open search without "Localize mass shift" prints an ERROR line and goes on [FragpipeRun]
    - sdrf.tsv: the precursor window (40.0 ppm for +/-20), "NT=Trypsin/P;CS=(?<=[KR])", one column per
      modification, the organism from OS= in the FASTA [SDRFtable, TabMsfragger]

What is invented: the tools' own chatter between a step's start and end line (a few typical lines each), the
result tables (ionomos.downstream.simulate, with planted hits) and every number. Nothing is searched. Not
modelled: report.parquet (a fake one would be read as Parquet), the IonQuant "MS1 (TMT)" step, FragPipe's
GUI cache, what a manifest with no file line at all does (the fake refuses it with a line of its own).

Environment:
    IONOMOS_FAKE_FP_SECONDS   how long a run takes (default 4)
    IONOMOS_FAKE_FP_MODE      a failure to act out, see MODES; several joined by commas ("child,hang")
    IONOMOS_FAKE_FP_LOG_MB    how much the huge-log mode prints (default 3)
    IONOMOS_FAKE_FP_STRICT    1 = refuse paths with spaces as on Windows (always on under Windows)
    IONOMOS_FAKE_FP_ENCODING  the console's code page (default cp1252, the lab PC's; utf-8 = a Linux FragPipe)
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

from ionomos.tmtkit import LABEL_TYPES

VERSION = "24.0"
BUILD = "24.0-build27"
DIANN_VERSION = "2.3.2"  # the PC's (REAL_RUNS.md, job 2); a --config-diann path with 1.8 in it gets 1.8.2 beta 8
# name -> version, in the order of FragpipeRun.createVersionsString (the PC's tool versions where known)
TOOL_VERSIONS = (
    ("DIA-Umpire", "2.3.3"), ("diaTracer", "2.2.1"), ("MSFragger", "4.4.1"), ("Crystal-C", "1.5.10"),
    ("MSBooster", "1.4.14"), ("Percolator", "3.7.1"), ("PTMProphet", "6.3.2"), ("Metaproteomics", "1.0.1"),
    ("Philosopher", "5.1.3-RC9"), ("PTM-Shepherd", "3.0.11"), ("IonQuant", "1.11.20"), ("TMT-Integrator", "6.1.3"),
    ("FragPipe-SpecLib", "0.1.58"), ("DIA-NN", DIANN_VERSION), ("Skyline", "N/A"), ("Pandas", "2.3.3"),
    ("Numpy", "1.26.4"),
)
SYSTEM_LINES = ("System OS: Windows 11, Architecture: AMD64",
                "Java Info: 17.0.10, OpenJDK 64-Bit Server VM, Eclipse Adoptium",
                ".NET Core Info: N/A")
DEFAULT_XMX = 24  # what OsUtils.getDefaultXmx gives for --ram 0 (it depends on the free memory)
# the label types' channels in FragPipe's order (QuantLabel.java): tmtkit.LABEL_TYPES is the one source (D91)
TMT_CHANNELS = {name: lt.channels for name, lt in LABEL_TYPES.items()}

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
    # D91: what the TMT QC (downstream/tmtqc.py) must catch, in the psm.tsv files of a complete run
    "tmt-poor-labelling": "a TMT run whose psm.tsv has 20 % of peptide N-termini without TMT",
    "tmt-low-purity": "a TMT run whose psm.tsv precursor purity is around 0.55",
    "tmt-empty-channel": "a TMT run whose second named channel has almost no reporter signal",
    # round 4: what a healthy run on the lab PC also does
    "non-ascii": "a complete run whose tools print non-ASCII text (umlauts, µ, CJK): '?' where the console's "
                 "code page lacks a letter, the real text in log_<date>.txt",
    "fail-once": "the first attempt fails in IonQuant; once an earlier attempt's fragpipe_previous_* folder sits "
                 "beside the output folder (Retry), the run completes",
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
EOL = "\r\n" if os.name == "nt" else "\n"  # Java's println: line.separator; "\n" inside a message stays "\n"
_CONSOLE: list[str] | None = None  # TabRun's console since the run began: what log_<date>.txt repeats


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


def _encoding() -> str:
    return os.environ.get("IONOMOS_FAKE_FP_ENCODING") or "cp1252"


def _write(stream, text: str) -> None:
    """Text as Java's PrintStream writes it: in the console's code page, '?' for a letter it lacks."""
    stream.flush()
    stream.buffer.write(text.encode(_encoding(), errors="replace"))
    stream.buffer.flush()


def _out(text: str = "", console: bool = True) -> None:
    """toConsole(...) in headless mode: System.out.println, and the text into TabRun's console."""
    _write(sys.stdout, text + EOL)
    if console and _CONSOLE is not None:
        _CONSOLE.append(text + "\n")


def _err(text: str) -> None:
    """System.err.println (Fragpipe.main0's refusals, before any run)."""
    _write(sys.stderr, text + EOL)


def _error(text: str) -> None:
    """log.error(...): logback's "%d{ISO8601} %-5level - %msg%n" on stdout; never in the console or its log."""
    now = datetime.now()
    _write(sys.stdout, f"{now:%Y-%m-%d %H:%M:%S},{now.microsecond // 1000:03d} ERROR - {text}{EOL}")


def _dialog(text: str) -> None:
    """SwingUtils.showErrorDialog / showWarningDialog in headless mode: log.error(stripHtml(text)), and stripHtml
    only turns line breaks into spaces (the <b> tags stay)."""
    _error(re.sub(r"[\n\r]", " ", text))


def _java_int(text: str) -> int | None:
    """Integer.parseInt: an optional sign and digits, no spaces, within 32 bits; None where Java throws."""
    if not re.fullmatch(r"[+-]?[0-9]+", text):
        return None
    v = int(text)
    return v if -(2 ** 31) <= v < 2 ** 31 else None


def _java_pct(v: float) -> str:
    """DecimalFormat("##.#'%'"): one decimal at most, none when it is 0."""
    s = f"{v:.1f}"
    return (s[:-2] if s.endswith(".0") else s) + "%"


def _java_float(v: float) -> str:
    """How IonQuant writes an intensity (Java's Float.toString): 0.0, 123456.7, 1.6315267E7."""
    if v == 0:
        return "0.0"
    v = float(f"{v:.8g}")  # a float's eight digits
    if 1e-3 <= abs(v) < 1e7:
        s = repr(v)
        return s if "e" not in s else f"{v:f}"
    m, e = f"{v:.7e}".split("e")
    m = m.rstrip("0")
    return f"{m}0E{int(e)}" if m.endswith(".") else f"{m}E{int(e)}"


def _parse(argv: list[str]) -> dict | int:
    """FragPipeMain.main: every option by name, case-insensitive; anything else stops with exit code 1."""
    if len(argv) == 1 and argv[0].lower() in ("--help", "-h"):
        _write(sys.stdout, help_text())  # System.out.print: the help's own "\n", no println
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
        elif opt in takes:
            if i + 1 >= len(argv):  # args[++i] past the end
                _err(f'Exception in thread "main" java.lang.ArrayIndexOutOfBoundsException: Index {i + 1} out of '
                     f"bounds for length {len(argv)}\n\tat org.nesvilab.fragpipe.FragPipeMain.main(FragPipeMain.java)")
                return 1
            i += 1
            value = argv[i]
            if takes[opt] in ("ram", "threads"):  # Integer.parseInt, before any other check
                if _java_int(value) is None:
                    _err(f'Exception in thread "main" java.lang.NumberFormatException: For input string: "{value}"'
                         "\n\tat java.base/java.lang.Integer.parseInt(Unknown Source)")
                    return 1
            # --workflow and --manifest are taken as given (Paths.get), the others trimmed
            a[takes[opt]] = value if takes[opt] in ("workflow", "manifest") else value.strip()
        else:
            _err(f"Cannot recognize the argument {argv[i]}")
            return 1
        i += 1
    return a


def _props(text: str) -> dict[str, str]:
    from ionomos.fragpipe import read_properties

    return read_properties(text)


def _on(props: dict[str, str], key: str, default: bool = False) -> bool:
    return props[key].strip().lower() == "true" if key in props else default


def _data_type(path: Path, given: str | None) -> str:
    """InputLcmsFile: the manifest's type, else a guess from the path (DDA first, then DIA in capitals)."""
    if given is None:
        name = str(path)
        if "dda" in name.lower() or any(t in name for t in ("_Q1.", "_Q2.", "_Q3.", "_diatracer.")):
            return "DDA"
        return "DIA" if "DIA" in name else ("DDA+" if "WWA" in name else "DDA")
    return {"dia": "DIA", "gpf-dia": "GPF-DIA", "dia-quant": "DIA-Quant", "dia-lib": "DIA-Lib",
            "dda+": "DDA+"}.get(given.strip().lower(), "DDA")


def read_manifest(path: Path) -> tuple[list[tuple[Path, str, int | None, str]], list[str]]:
    """TabWorkflow.manifestLoad: ([(path, experiment as FragPipe keeps it, bioreplicate, data type)], bad lines).
    A line whose bioreplicate Integer.parseInt refuses is a bad line and left out."""
    from ionomos import fragpipe as fp

    files, bad = [], []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.startswith(("//", "#")):
            continue
        split = ln.strip().split("\t")
        while len(split) > 1 and split[-1] == "":  # Java's split drops trailing empty strings
            split.pop()
        exp = split[1].strip() if len(split) > 1 and split[1].strip() else None
        rep = None
        if len(split) > 2 and split[2].strip():
            rep = _java_int(split[2])
            if rep is None:
                bad.append(ln)
                continue
        dtype = split[3].strip() if len(split) > 3 and split[3].strip() else None
        p = Path(split[0])
        files.append((p, fp.fp_experiment(exp or ""), rep, _data_type(p, dtype)))
    return files, bad


def _save_log(wd: Path) -> Path | None:
    """FragPipe saves its console as log_<date>_<time>.txt in the output folder when a run ends, good or bad
    (TabRun.saveLogToFile, CREATE_NEW: a name already taken gets _1, _2, ...). Only into a folder that exists."""
    if not Path(wd).is_dir():
        return None
    base = Path(wd) / f"log_{time.strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    path, n = base, 0
    while True:
        try:
            with open(path, "x", encoding="utf-8", newline="") as fh:
                fh.write("".join(_CONSOLE or []))
            return path
        except FileExistsError:
            n += 1
            path = Path(f"{base}_{n}")
        except OSError:
            return None


def fake_fragpipe(argv: list[str]) -> int:
    global _CONSOLE
    _CONSOLE = None
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
    return _main0(a, modes)


def _main0(a: dict, modes: set[str]) -> int:
    # ---- Fragpipe.main0 --------------------------------------------------------------------------------------
    def stop(msg: str) -> int:
        _err(msg)
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
    if a.get("ram") is not None and int(a["ram"]) < 0:
        return stop("ram is smaller than 0.")
    if a.get("threads") is not None and int(a["threads"]) < 0:
        return stop("Number of threads is smaller than 0.")
    if not a.get("workdir"):
        return stop("The path to workdir does not look right.")
    if a.get("tools") is not None and (not a["tools"] or not Path(a["tools"]).exists()):
        return stop(f"Tools folder path {a['tools']} does not seem right.")
    if a.get("diann") is not None and not Path(a["diann"]).is_file() and os.environ.get("IONOMOS_FAKE_FP_STRICT") == "1":
        return stop(f"DIA-NN executable file path {a['diann']} does not seem right.")
    if a.get("python") is not None and (not a["python"] or not Path(a["python"]).exists()):
        return stop(f"Python path {a['python']} does not seem right.")
    if "silent-exit0" in modes:
        return 0

    # ---- Fragpipe.headless: the workflow, then the manifest, are loaded before the run starts ---------------
    wd = Path(a["workdir"]).resolve()
    wf_text = Path(a["workflow"]).read_text(encoding="utf-8", errors="replace")
    props = _props(wf_text)
    files, bad = read_manifest(Path(a["manifest"]))
    if bad:
        _dialog("Manifest file contained some badly formatted lines\n\n" + "\n".join(bad))
    missing = [p for p, *_x in files if not p.exists()]
    if missing:  # TabWorkflow.showSkippedFiles, headless: say which, then System.exit(1)
        _error("Some loaded files don't exist. Please double check.")
        for p in missing:
            _error(str(p.resolve()))
        _save_log(wd)  # the shutdown hook: an empty log when the output folder exists
        return 1
    return _run(a, modes, wd, wf_text, props, files)


def _run(a: dict, modes: set[str], wd: Path, wf_text: str, props: dict, files: list) -> int:
    """FragpipeRun.run."""
    global _CONSOLE
    from ionomos import fragpipe as fp

    _CONSOLE = []  # MessageClearConsole
    dry = a["dry"]
    strict = os.name == "nt" or os.environ.get("IONOMOS_FAKE_FP_STRICT") == "1"

    def refuse(text: str, dialog: bool = False) -> int:
        (_dialog if dialog else _error)(text)
        _save_log(wd)  # System.exit(1) -> the shutdown hook saves the (empty) console when the folder exists
        return 1

    # validateWd
    if strict and re.search(r"\s", str(wd)):
        return refuse("Output directory path contains whitespace characters. Some programs in the pipeline might "
                      "not work properly in this case. Please change output directory to one without spaces.")

    run_diann = _on(props, "diann.run-dia-nn", default=any(t.startswith(("DIA", "GPF")) for *_x, t in files))
    run_speclib = _on(props, "speclibgen.run-speclibgen", default=False)
    run_tmt = _on(props, "tmtintegrator.run-tmtintegrator", default="tmt" in Path(a["workflow"]).name.lower())
    flat = run_diann or run_speclib  # InputLcmsFile.getGroup: no per-experiment folders then

    def group(exp: str, rep: int | None) -> str:
        if flat:
            return ""
        if rep is not None:
            return f"{exp or 'exp'}_{rep}"
        return exp

    groups: dict[str, list[Path]] = {}
    for path, exp, rep, _t in files:
        groups.setdefault(group(exp, rep), []).append(path)
    gdirs = [wd / g if g else wd for g in groups]

    # prepareWd (not for a dry run): the output folder and every group folder, before any other check
    if not dry:
        try:
            for d in (wd, *gdirs):
                d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _error(f"Could not create directory structure. {exc}")
            return 1

    # checkInputLcmsFiles2: one name, one file
    by_name: dict[str, list[Path]] = {}
    for p, *_x in files:
        by_name.setdefault(p.name, []).append(p)
    same = {n: ps for n, ps in sorted(by_name.items()) if len(ps) > 1}
    if same:
        msg = "<html>Some input LCMS files have the same name, even though located in different folders:\n"
        for n, ps in same.items():
            msg += f"\n<html>File Name - <b>{n}</b>:" + "".join(f"\n    - {p}" for p in ps) + "\n"
        msg += "\nFiles might get overwritten and results might be not what's expected.\nConsider renaming input files."
        return refuse(msg)
    if not files:
        # what the real FragPipe does with no LC-MS file at all is not known; this line is the fake's own
        return refuse("FAKE FragPipe: the manifest lists no LC-MS file")

    # checkFasta
    db = props.get("database.db-path", "")
    if not db.strip():
        return refuse("FASTA file path is empty or the file is corrupted")
    if not Path(db).is_file():
        return refuse(f"Could not find fasta file at: {db}")

    # configureTaskGraph: while the graph is built ...
    tag = props.get("database.decoy-tag", "rev_")
    if not tag.strip():
        return refuse("<b>Decoy protein prefix</b> is empty. Please fix it in <b>Database</b> tab.", dialog=True)
    if "msfragger" in modes:
        return refuse("<b>MSFragger</b> is not valid but it is enabled in the workflow. Please disable MSFragger or "
                      "fix it in the <b>Config</b> tab.", dialog=True)
    # ... then the checks (checkDbConfig) ...
    info = fp.fasta_info(Path(db), tag)
    validation = any(_on(props, k) for k in ("percolator.run-percolator", "peptide-prophet.run-peptide-prophet",
                                             "phi-report.run-report"))
    if validation:
        if not info["entries"]:
            return refuse("Database looks to be empty", dialog=True)
        if info["gb"] * 1e9 < fp.BIG_FASTA_BYTES:
            share = info["decoys"] / info["entries"]
            if share <= 0:
                return refuse("No decoys found in the FASTA file.")
            if share >= 1:
                return refuse("All FASTA entries seem to be decoys.")
            if share < 0.4 or share > 0.6:
                return refuse(f"FASTA file contains {_java_pct(100 * share)} decoys.")
    # ... then each command's configure()
    open_search = (props.get("msfragger.precursor_mass_units", "1").strip() == "0"
                   and _num(props.get("msfragger.precursor_mass_lower")) < -3
                   and _num(props.get("msfragger.precursor_mass_upper")) > 3)
    if open_search and props.get("msfragger.localize_delta_mass", "0").strip() in ("0", "false") and not flat:
        _error("Mass-offset or open search with 'Localize mass shift (LOS)` was disabled. It is recommended to "
               "enable 'Localize mass shift (LOS)`.")  # an ERROR line, and the run goes on
    if "speclib" in modes or (run_speclib and a.get("python") is not None and not Path(a["python"]).exists()):
        return refuse("Spectral Library Generation module was not configured correctly. Please make sure that Python "
                      "and FragPipe-SpecLib have been installed.")

    # TMT annotations: one file ending in annotation.txt in the folder holding all of a plex's files, else
    # FragPipe makes its own <workdir>/<plex>/<plex>_annotation.txt with "<channel> <plex>_<channel>"
    annotations: dict[str, Path | None] = {}
    label = props.get("tmtintegrator.channel_num", "TMT-10")
    if run_tmt:
        problem = _tmt_checks(files, props, label, annotations)
        if problem is not None:
            _save_log(wd)
            return problem

    # ---- what would run ------------------------------------------------------------------------------------------
    java = r"C:\FragPipe\FragPipe-24.0\jre\bin\java.exe" if os.name == "nt" else "/opt/fragpipe/jre/bin/java"
    tools = Path(a["tools"]) if a.get("tools") else Path(java).parent.parent.parent / "tools"
    phil = tools / "Philosopher" / "philosopher-v5.1.3-RC9.exe"
    ram = int(a.get("ram") or props.get("workflow.ram", "0") or 0) or DEFAULT_XMX
    threads = a.get("threads") or props.get("workflow.threads") or str(max(1, (os.cpu_count() or 2) - 1))
    if str(threads).strip() == "0":
        threads = str(os.cpu_count() or 1)
    # without --config-diann FragPipe takes the DIA-NN its window was set to (the cache): on the PC, 2.3.2
    diann_exe = a.get("diann") or (r"C:\DIA-NN\2.3.2\DiaNN.exe" if os.name == "nt" else "/opt/diann/2.3.2/diann-linux")
    diann_v = "1.8.2 beta 8" if "1.8" in diann_exe else DIANN_VERSION
    uncal = {p: p.with_name(p.stem + "_uncalibrated.mzML") for p, *_x in files if p.suffix.lower() == ".raw"}
    steps = _steps(wd, files, groups, gdirs, props, a, modes, java, tools, phil, ram, str(threads), db, tag,
                   run_tmt, run_diann, run_speclib, flat, diann_exe, diann_v, uncal)
    ionquant = any(s[0] == "IonQuant" for s in steps)

    # the configure() steps write their file lists into the output folder when it exists (a dry run's too)
    if wd.is_dir():
        _config_files(wd, files, groups, gdirs, run_diann, ionquant, uncal)
    if not dry:
        (wd / "fragger.params").write_text("# FAKE MSFragger parameters\n", encoding="utf-8")
        if ionquant or run_diann:
            _experiment_annotation(wd, files, annotations, label, run_tmt, run_diann)

    # ---- the console, up to the run ----------------------------------------------------------------------------
    _out("\n".join(SYSTEM_LINES) + "\n")
    _out()
    _out("Version info:\n" + f"FragPipe version {VERSION}\n" + "".join(
        f"{n} version {diann_v if n == 'DIA-NN' else v}\n" for n, v in TOOL_VERSIONS))
    _out()
    lcms = "LCMS files:\n"
    for g, paths in groups.items():
        lcms += f"  Experiment/Group: {g}\n  (if \"spectral library generation\" is enabled, all files will be analyzed together)\n"
        for p in paths:
            dtype = next(t for q, _e, _r, t in files if q == p)
            lcms += f"  - {p}\t{dtype}\n"
    _out(lcms)
    _out()
    # MessageManifestSave, saveRuntimeConfig, the runtime job: a dry run writes them too
    used = wf_text.rstrip("\n") + f"\nworkdir={str(wd)}\nworkflow.threads={threads}\nworkflow.ram={a.get('ram') or 0}\n"
    wd.mkdir(parents=True, exist_ok=True)  # FileUtils.write makes the folder
    (wd / "fragpipe-files.fp-manifest").write_text(
        "\n".join(f"{p.resolve()}\t{e}\t{'' if r is None else r}\t{t}" for p, e, r, t in files), encoding="utf-8")
    (wd / "fragpipe.workflow").write_text(used, encoding="utf-8")
    (wd / "fragpipe.job").write_text(
        "# Job name (optional)\tWorkflow file path\tManifest file path\tOutput directory\tTools folder path "
        "(optional)\tFasta path (optional)\tRAM (optional)\tThreads (optional)\n"
        f"runtime-save\t{wd / 'fragpipe-workflow.workflow'}\t{wd / 'fragpipe-files.fp-manifest'}\t{wd}\t"
        f"{a.get('tools') or 'null'}\t{db}\t{ram}\t{threads}\n", encoding="utf-8")
    _out(f"{len(steps)} commands to execute:")
    for name, where, cmd, _said in steps:
        _out(name + (f" [Work dir: {where}]" if where else ""))
        _out(cmd)
    _out(DASHES)
    _out()
    _out("Execution order:\n")
    order: list[str] = ["START"]
    for name, *_x in steps:
        base = name.split(" ")[0].rstrip(":") if name.startswith(("MSFragger", "Percolator", "DIA-Quant")) else name
        if base not in order:
            order.append(base)
    for name in order:
        _out(f"    Cmd: [{name}], Work dir: [{wd}]")
    _out()
    _out(DASHES)
    _out()
    _out(f"~~~~~~Sample of {db}~~~~~~~")
    headers = sorted(ln.strip() for ln in Path(db).read_text(encoding="utf-8", errors="replace").splitlines()
                     if ln.startswith(">"))
    gap = 1 if len(headers) < 21 else (len(headers) - 1) // 20
    for h in headers[::gap]:
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
            elif dry:
                _out(f"Cannot read {shown}")
                _out(f"java.nio.file.NoSuchFileException: {shown}")
            else:
                for ch in TMT_CHANNELS.get(label, TMT_CHANNELS["TMT-10"]):
                    _out(f"{ch} {exp}_{ch}")
        _out(DASHES)
        _out()
    if dry:
        _out("\nIt's a dry-run, not running the commands.\n")
        _citations()
        return 0

    _out("~~~~~~~~~ fragpipe.config ~~~~~~~~~")
    _out(used.rstrip("\n"))
    _out("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")

    if "huge-log" in modes:  # a talkative run: lots of lines, and one without an end for a long while
        mb = float(os.environ.get("IONOMOS_FAKE_FP_LOG_MB", "3"))
        line = "MSFragger: processed scan block " + "0123456789" * 7 + "\n"
        _write(sys.stdout, line * int(mb * 1_000_000 / len(line)) + "x" * 1_000_000 + "\n")
    return _steps_run(wd, files, groups, gdirs, props, annotations, label, run_tmt, flat, a, modes, steps, db, uncal)


def _num(text: str | None) -> float:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return 0.0


def _tmt_checks(files, props: dict, label: str, annotations: dict) -> int | None:
    """TmtiPanel's annotation lookup and CmdTmtIntegrator.configure's checks, in their order; None = all good."""
    by_exp: dict[str, list[Path]] = {}
    for path, exp, _rep, _t in files:
        by_exp.setdefault(exp, []).append(path)
    for exp, paths in by_exp.items():
        folders = {p.resolve().parent for p in paths}
        found = None
        if len(folders) == 1:
            hits = sorted(p for p in next(iter(folders)).iterdir() if p.name.endswith("annotation.txt"))
            found = hits[0] if len(hits) == 1 else None
        annotations[exp] = found
    rows_of: dict[Path, list[list[str]]] = {}
    for found in annotations.values():
        if found is None:
            continue
        rows = [ln.split() for ln in found.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        if any(len(r) != 2 for r in rows):
            bad = next(" ".join(r) for r in rows if len(r) != 2)  # configure() catches it: the stack trace
            _error(f"java.lang.RuntimeException: Invalid line in annotation file {found}: {bad}\n\tat "
                   "org.nesvilab.fragpipe.tabs.TmtiPanel.parseTmtAnnotationFile(TmtiPanel.java)")
            return 1
        rows_of[found] = rows
    seen: set[str] = set()
    for rows in rows_of.values():
        if label in TMT_CHANNELS and len(rows) != len(TMT_CHANNELS[label]):
            _dialog("Number of the samples in the annotation file does not match the number of channels in the "
                    "'label type' of the Quant (Isobaric) tab.")
            return 1
        for _ch, sample in rows:
            if sample.lower() != "na" and sample in seen:
                _dialog("Duplicate samples found in annotation files. The sample names must be unique among all "
                        "experiments.")
                return 1
            seen.add(sample)
    ref_tag = props.get("tmtintegrator.ref_tag", "")
    if props.get("tmtintegrator.add_Ref", "").strip() == "-1":
        without = [str(f) for f, rows in rows_of.items() if not any(ref_tag in s for _c, s in rows)]
        if without:
            _dialog("Found annotation files without reference channel\nspecified:\n" + "\n".join(without)
                    + "\n\nOne sample name in each annotation file must start with\nthe reference tag you set. You can "
                      "change that in Quant tab, [Ref Sample Tag] text field.")
            return 1
    return None


def _steps(wd, files, groups, gdirs, props, a, modes, java, tools, phil, ram, threads, db, tag, run_tmt, run_diann,
           run_speclib, flat, diann_exe, diann_v, uncal) -> list[tuple[str, Path | None, str, list[str]]]:
    """Every step FragPipe would run: (name, work dir, command line, a few lines the tool prints)."""
    steps: list[tuple[str, Path | None, str, list[str]]] = []

    def add(name: str, where: Path | None, cmd: str, said: list[str] | None = None) -> None:
        steps.append((name, where, cmd, said or []))

    lib = tools.parent / "lib"
    add("CheckCentroid", None, f"{java} -Xmx{ram}G -cp {lib}/* org.nesvilab.fragpipe.util.CheckCentroid "
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
    add("MSFragger move pepxml", None, f"{java} -cp {lib}/fragpipe-{VERSION}.jar org.nesvilab.utils.FileMove "
                                       f"--no-err {files[0][0].with_suffix('.pepXML')} {gdirs[0]}")
    add("MSFragger move pin", None, f"{java} -cp {lib}/fragpipe-{VERSION}.jar org.nesvilab.utils.FileMove "
                                    f"--no-err {files[0][0].with_suffix('.pin')} {gdirs[0]}")
    if _on(props, "crystalc.run-crystalc"):
        for g in gdirs:
            add("Crystal-C", g, f"{java} -Xmx{ram}G -cp {tools / 'original-crystalc-1.5.10.jar'} Main "
                                f"{wd / 'crystalc-0-.params'}", ["Crystal-C (version: 1.5.10)", "Done"])
    if _on(props, "msbooster.run-msbooster", default=True):
        add("MSBooster", wd, f"{java} -Xmx{ram}G -cp {tools / 'MSBooster-1.4.14.jar'} mainsteps.MainClass --paramsList "
                             f"{wd / 'msbooster_params.txt'}",
            ["{d} [INFO] - MSBooster v1.4.14", f"{{d}} [INFO] - Using {threads} threads", "{d} [INFO] - Done in 0s"])
    if _on(props, "percolator.run-percolator"):
        for g in gdirs:
            add("Percolator", g, f"{tools / 'percolator_3_7_1' / 'windows' / 'percolator.exe'} --only-psms "
                                 f"--no-terminate --post-processing-tdc --num-threads {threads}",
                ["Percolator version 3.07.1, Build Date May 21 2024",
                 "Processing took 0.2650 cpu seconds or 0 seconds wall clock time."])
            add("Percolator: Convert to pepxml", g, f"{java} -cp {lib}/* "
                                                    f"org.nesvilab.fragpipe.tools.percolator.PercolatorOutputToPepXML")
            add("Percolator delete temp", None, f"{java} -cp {lib}/fragpipe-{VERSION}.jar "
                                                f"org.nesvilab.utils.FileDelete {g / 'percolator_target_psms.tsv'}")
    elif _on(props, "peptide-prophet.run-peptide-prophet"):
        for g in gdirs:
            add("PeptideProphet", g, f"{phil} peptideprophet --nonparam --expectscore --decoyprobs --masswidth 1000.0 "
                                     f"--clevel -2 --decoy {tag} --database {db} {g}",
                ['time="{t}" level=info msg="Executing PeptideProphet  v5.1.3-RC9"', 'time="{t}" level=info msg=Done'])
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
    if _on(props, "ptmshepherd.run-shepherd"):
        add("PTMShepherd", wd, f"{java} -Dlibs.thermo.dir={tools / 'MSFragger-4.4.1' / 'ext' / 'thermo'} -Xmx{ram}G "
                               f"-cp {tools / 'ptmshepherd-3.0.11.jar'} edu.umich.andykong.ptmshepherd.PTMShepherd "
                               f"{wd / 'shepherd.config'}",
            ["PTM-Shepherd version 3.0.11", "Building mass shift profiles", "Done"])
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
        two = not diann_v.startswith("1.")
        cfg = wd / "filelist_diann.txt"
        add("DIA-Quant run DIA-NN", wd, f"{diann_exe} --lib library.tsv --threads {threads} --verbose 1 --out "
                                        f"{Path('dia-quant-output') / 'report.tsv'} --qvalue 0.01 --matrix-qvalue 0.01 "
                                        f"--matrices --no-prot-inf --smart-profiling --no-quant-files --peak-center "
                                        f"--no-ifs-removal --report-lib-info --cfg "
                                        + (str(cfg) if os.name == "nt" else f"{cfg}--"),
            [f"DIA-NN {diann_v} {'Academia  ' if two else ''}(Data-Independent Acquisition by Neural Networks)",
             "Compiled on Sep 12 2025 10:31:05" if two else "Compiled on Dec  1 2022 14:47:06",
             f"Thread number set to {threads}", "[0:00] Loading spectral library library.tsv",
             f"[0:01] {len(files)} files will be processed", "[0:02] Cross-run analysis", "[0:02] Stats report saved to "
             f"{Path('dia-quant-output') / 'report.stats.tsv'}", "Finished"])
        if os.name == "nt":  # CmdDiann: the plot step is Windows only
            if two:
                add("DIA-Quant plot DIA-NN output", wd, f"{Path(diann_exe).with_name('diann-stats.exe')} "
                                                        f"{Path('dia-quant-output') / 'report.parquet'}")
            else:
                add("DIA-Quant plot DIA-NN output", wd, f"{Path(diann_exe).with_name('dia-nn-plotter.exe')} "
                                                        f"{Path('dia-quant-output') / 'report.stats.tsv'} "
                                                        f"{Path('dia-quant-output') / 'report.tsv'} "
                                                        f"{Path('dia-quant-output') / 'report.pdf'}")
        if two:
            add("DIA-Quant convert Parquet to Tsv", wd / "dia-quant-output",
                f"{java} -cp {lib}/* org.nesvilab.fragpipe.tools.diann.ParquetToTsv "
                f"{wd / 'dia-quant-output' / 'report.parquet'} {wd / 'dia-quant-output' / 'report.tsv'}")
        add("DIA-Quant propagate information", wd / "dia-quant-output",
            f"{java} -cp {lib}/* org.nesvilab.fragpipe.tools.diann.Propagation {wd} - - -")
    return steps


def _config_files(wd: Path, files, groups, gdirs, run_diann: bool, ionquant: bool, uncal: dict) -> None:
    """The file lists the configure() steps write when the output folder exists (dry run or not)."""
    nl = os.linesep  # BufferedWriter.newLine
    (wd / "filelist_proteinprophet.txt").write_text(
        "".join(str(g / f"interact-{p.stem}.pep.xml") + nl for g, ps in zip(gdirs, groups.values(), strict=True)
                for p in ps), encoding="utf-8", newline="")
    (wd / "msbooster_params.txt").write_text(f"fragger = {wd / 'fragger.params'}\n", encoding="utf-8")
    if ionquant:
        (wd / "filelist_ionquant.txt").write_text(
            "flag\tvalue\n" + "".join(f"--psm\t{Path(g.name) / 'psm.tsv'}\n" for g in gdirs)
            + "".join(f"--specdir\t{d}\n" for d in dict.fromkeys(p.resolve().parent for p, *_x in files)),
            encoding="utf-8")
        (wd / "modmasses_ionquant.txt").write_text("15.9949\n42.0106\n", encoding="utf-8")
    if run_diann:
        (wd / "filelist_diann.txt").write_text(
            "".join(f"--f {uncal.get(p, p).resolve()}\n" for p, *_x in files), encoding="utf-8")


def _experiment_annotation(wd: Path, files, annotations: dict, label: str, run_tmt: bool, run_diann: bool) -> None:
    """experiment_annotation.tsv as ToolingUtils writes it when IonQuant or DIA-NN is configured: for TMT from
    the annotation files (FragPipe's own reading of the names, quirks kept); else one row per file."""
    if run_tmt:
        lines = ["plex\tchannel\tsample\tsample_name\tcondition\treplicate"]
        for exp, found in annotations.items():
            pairs = ([ln.strip() for ln in found.read_text(encoding="utf-8").splitlines() if ln.strip()] if found
                     else [f"{ch} {exp}_{ch}" for ch in TMT_CHANNELS.get(label, TMT_CHANNELS["TMT-10"])])
            for ln in pairs:
                parts = re.split(r"\s", ln)
                if len(parts) < 2 or parts[1].strip().lower() == "na":
                    continue
                ch, sample = parts[0].strip(), parts[1].strip()
                p2 = sample.split("_")
                if len(p2) == 3:  # condition_plex_channel: FragPipe takes p2[1] as the condition, p2[2] as replicate
                    rep = _java_int(p2[2])
                    cond, rep = (p2[1], rep) if rep is not None else (p2[2], 1)
                elif len(p2) == 2:
                    rep = _java_int(p2[1])
                    cond, rep = (p2[0], rep) if rep is not None else (p2[1], 1)
                else:
                    cond, rep = sample, 1
                lines.append(f"{exp}\t{ch}\t{sample}\t{sample}\t{cond}\t{rep}")
    else:
        lines = ["file\tsample\tsample_name\tcondition\treplicate"]
        has_dia = any(t in ("DIA", "DIA-Quant") for *_x, t in files)
        for p, e, r, t in files:
            if t in ("GPF-DIA", "DIA-Lib") or (has_dia and t not in ("DIA", "DIA-Quant")):
                continue
            g2 = f"{e or 'exp'}_{r}" if r is not None else e
            sample = str(p.resolve()) if run_diann else g2
            lines.append(f"{p.resolve()}\t{sample}\t{g2}\t{e.split('_')[0]}\t{1 if r is None else r}")
    (wd / "experiment_annotation.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _steps_run(wd, files, groups, gdirs, props, annotations, label, run_tmt, flat, a, modes, steps, db, uncal) -> int:
    # ---- the run ---------------------------------------------------------------------------------------------------
    if "child" in modes:  # something FragPipe started that outlives a careless kill (Java -> MSFragger)
        log_fh = open(wd / "child.log", "wb")  # noqa: SIM115 - handed to the child; never a pipe
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], stdout=log_fh,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        (wd / "child.pid").write_text(str(child.pid), encoding="utf-8")
    total = float(os.environ.get("IONOMOS_FAKE_FP_SECONDS", "4"))
    started = time.monotonic()
    runtimes: dict[str, float] = {}
    first_attempt = not any(wd.parent.glob("fragpipe_previous_*"))

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
        if "non-ascii" in modes and name in ("MSFragger", "PhilosopherReport"):
            _out("Warnung: Präzisionsmasse außerhalb ±20 ppm für 5 µL Probe – Größe ok → 日本語のファイル名"
                 if name == "MSFragger" else
                 f'time="{now:%H:%M:%S}" level=info msg="Überprüfe Ergebnisse für Müller (µg)"')
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
                    _out("Checking spectral files... " + "." * 1000, console=False)
                    time.sleep(0.001)
            for raw, mzml in uncal.items():  # MSFragger converts each .raw beside it, for the later steps
                try:
                    mzml.write_text(f"<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<!-- FAKE, from {raw.name} -->\n"
                                    "<mzML/>\n", encoding="utf-8")
                except OSError:
                    pass
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
        if name == "IonQuant" and (any("FAKEFAIL" in str(f[0]) for f in files) or ("fail-once" in modes and first_attempt)):
            if "fail-once" in modes:  # what the crash left half-written, for the next attempt to move aside
                (wd / "combined_protein.tsv").write_text("Protein\tProtein ID\n", encoding="utf-8")
            _out(f"{now:%Y-%m-%d %H:%M:%S} [ERROR] - IonQuant crashed (this sample fails on purpose)")
            return fail(name, 1, left)
        if name == "DIA-Quant run DIA-NN" and ("diann" in modes or any("FAKEFAIL" in str(f[0]) for f in files)):
            _out("ERROR: DIA-NN crashed (this sample fails on purpose)" if "diann" not in modes
                 else "ERROR: cannot load the spectral library (acted out by the fake)")
            return fail(name, 1, left)
        _out(f"Process '{name}' finished, exit code: 0")
        runtimes[name] = runtimes.get(name, 0.0) + (time.monotonic() - t0) / 60

    # ---- the files a run leaves ------------------------------------------------------------------------------------
    _write_outputs(wd, files, groups, gdirs, props, annotations, label, run_tmt, flat, Path(a["workflow"]).name, modes,
                   uncal)
    _spoil_tables(wd, modes)

    # ---- the finalizer ---------------------------------------------------------------------------------------------
    if props.get("workflow.misc.save-sdrf", "false").strip().lower() == "true":
        _sdrf(wd, files, props, label if run_tmt else None, Path(db))
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
               "dia-quant-output/report.pg_matrix.tsv", "combined_protein.tsv")


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


# Unimod names of the modifications the testbed's workflows search (SDRFtable.matchUnimod; else "mass on site")
_UNIMOD = {(57.02146, "C"): "Carbamidomethyl", (15.9949, "M"): "Oxidation", (42.0106, "[^"): "Acetyl",
           (79.96633, "STY"): "Phospho", (229.16293, "K"): "TMT6plex", (229.16293, "n^"): "TMT6plex",
           (304.20715, "K"): "TMTpro", (304.20715, "n^"): "TMTpro", (561.3387, "C"): "isoDTB light",
           (567.3462, "C"): "isoDTB heavy"}


def _sdrf_mods(props: dict) -> list[str]:
    """TabMsfragger.getSDRFmods: NT=<name>;MT=<Fixed|Variable>;PP=<position>[;TA=<residues>];MM=<mass>."""
    out = []
    for key, kind in (("msfragger.table.var-mods", "Variable"), ("msfragger.table.fix-mods", "Fixed")):
        for item in props.get(key, "").split(";"):
            parts = [x.strip() for x in item.split(",")]
            if len(parts) < 3 or parts[2].lower() != "true" or _num(parts[0]) == 0:
                continue
            mass, site = _num(parts[0]), parts[1].split(" ")[0]
            pp = {"[^": "Protein N-term", "n^": "Any N-term", "]^": "Protein C-term", "c^": "Any C-term"}.get(
                site, "Anywhere")
            aas = "".join(re.findall(r"[A-Z]", site)) if pp == "Anywhere" else ""
            name = next((n for (m, s), n in _UNIMOD.items() if abs(m - mass) < 1e-3 and s == site), None)
            name = name or f"{mass:.5f} on {site}"
            out.append(f"NT={name};MT={kind};PP={pp}{';TA=' + aas if aas else ''};MM={mass:.5f}")
    return out


def _sdrf(wd: Path, files, props: dict, label: str | None, fasta: Path) -> None:
    """sdrf.tsv (SDRFtable, type Default), written by the finalizer when workflow.misc.save-sdrf=true (FragPipe
    24's stock workflows say so): one row per file (per file and channel for TMT), no conditions."""
    organisms: dict[str, int] = {}
    for ln in fasta.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"OS=([\w\s]+) (OX=|GN=|PE=|SV=)", ln) if ln.startswith(">") else None
        if m:
            organisms[m.group(1)] = organisms.get(m.group(1), 0) + 1
    organism = max(organisms, key=organisms.get) if organisms else "not available"
    mods = _sdrf_mods(props)
    head = ["source name", "characteristics[organism]", "characteristics[disease]", "characteristics[organism part]",
            "characteristics[cell type]", "characteristics[biological replicate]", "technology type", "assay name",
            "comment[data file]", "comment[technical replicate]", "comment[fraction identifier]", "comment[label]",
            "comment[instrument]", "comment[precursor mass tolerance]", "comment[fragment mass tolerance]",
            "comment[cleavage agent details]", *["comment[modification parameters]"] * len(mods)]
    lo = _num(props.get("msfragger.precursor_mass_lower")) or -20.0
    hi = _num(props.get("msfragger.precursor_mass_upper")) or 20.0
    units = "ppm" if props.get("msfragger.precursor_mass_units", "1").strip() == "1" else "Da"
    prec = f"{hi - lo:.1f} ppm" if units == "ppm" else f"{hi - lo:.2f} Da"
    frag = f"{_num(props.get('msfragger.fragment_mass_tolerance', '20')) or 20.0} ppm"
    enzyme = {"stricttrypsin": "Trypsin/P", "trypsin": "Trypsin"}.get(
        props.get("msfragger.search_enzyme_name_1", "stricttrypsin").strip(), "Trypsin/P")
    cuts = "(?<=[KR])" + ("(?![P])" if enzyme == "Trypsin" else "")
    na = "not available"
    out = ["\t".join(head)]
    for p, _e, r, _t in files:
        for lab in ([f"TMT{ch}" for ch in TMT_CHANNELS.get(label, TMT_CHANNELS["TMT-10"])] if label else [na]):
            out.append("\t".join([na, organism, na, na, na, na, na, na, p.name, na if r is None else str(r), na, lab,
                                  na, prec, frag, f"NT={enzyme};CS={cuts}", *mods]))
    (wd / "sdrf.tsv").write_text("\n".join(out), encoding="utf-8")


def _write_outputs(wd: Path, files, groups, gdirs, props, annotations, label: str, run_tmt: bool, flat: bool,
                   workflow_name: str, modes: set[str] | None = None, uncal: dict | None = None) -> None:
    from ionomos import testbed

    # intermediate files a search leaves beside its tables (names as in FragPipe's output documentation)
    (wd / "combined.prot.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<protein_summary/>\n', encoding="utf-8")
    for g, paths in zip(gdirs, groups.values(), strict=True):
        for p in paths:
            (g / f"interact-{p.stem}.pep.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n<msms_pipeline_analysis/>\n', encoding="utf-8")
        (g / "protein.fas").write_text(">sp|FAKE1|FAKE1_HUMAN fake protein 1\nMKVLAAGIVGLLLAC\n", encoding="utf-8")
        (g / "filter.log").write_text("FAKE philosopher filter log\n", encoding="utf-8")
    if _on(props, "ptmshepherd.run-shepherd"):
        testbed.fake_ptm_shepherd(wd, [str(p) for p, *_x in files])

    rows = [[str(p), e, "1" if r is None else str(r), t] for p, e, r, t in files]  # as the manifest FragPipe read
    runs = {str(p): str((uncal or {}).get(p, p).resolve()) for p, *_x in files}  # DIA-NN's run names
    testbed._fake_results(wd, rows, workflow_name, annotations=annotations if run_tmt else None, label=label,
                          props=props, modes=modes, runs=runs)
