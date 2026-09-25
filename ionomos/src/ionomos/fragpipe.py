"""
FragPipe headless runner: everything needed to turn a queued job into one
`fragpipe --headless` process, and nothing about queues or state.

    spec = prepare(job, cfg)          # -> RunSpec, or raises Hold / JobError
    write_inputs(spec)                # manifest, patched workflow, TMT annotation
    code = run(spec, stop_event)      # blocks; tees console output to spec.console_log

Per job, inside the experiment folder:

    <experiment>/
      ionomos_run/                     inputs we generate + FragPipe's console output
        fragpipe-files.fp-manifest
        <method>.workflow               the pinned workflow with database.db-path set
        fragpipe_console.log
      fragpipe/                         --workdir: FragPipe's own output, starts empty
      fragpipe_previous_<ts>/           an earlier attempt's workdir, moved aside (never deleted)

Hold vs JobError: a *Hold* is a setup problem that isn't the job's fault (no
FragPipe launcher, the method's pinned workflow/FASTA missing) — the job stays
queued and runs as soon as the file appears. A *JobError* is specific to the
job (its experiment.yaml names a workflow that doesn't exist, raw files were
moved away) — the job fails with that reason.
"""
from __future__ import annotations

import glob
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ionomos.config import Config
from ionomos.ledger import Job
from ionomos.manifest import OverridesError, fp_manifest_text, parse_overrides, tmt_annotation_files

RUN_DIR = "ionomos_run"  # == names.RUN_DIR; old jobs may have labwatch_run/ (names.run_dir finds it)
WORKDIR = "fragpipe"
MANIFEST_NAME = "fragpipe-files.fp-manifest"
CONSOLE_LOG = "fragpipe_console.log"
CANCEL_FILE = "CANCEL"  # created in ionomos_run/ by `ionomos cancel` / the app

# Where FragPipe lives on Windows. Two layouts exist:
#   zip builds (<= 22):          <root>/fragpipe/bin/fragpipe.bat (+ a GUI fragpipe.exe)
#   Windows installer (23, 24):  C:/FragPipe/FragPipe-24.0/bin/FragPipe-24.0.exe (+ fragpipe.bat if shipped)
LAUNCHER_GLOBS = (
    "C:/FragPipe/*/bin/fragpipe.bat",
    "C:/FragPipe/*/bin/FragPipe*.exe",
    "C:/FragPipe/*/fragpipe/bin/fragpipe.bat",
    "C:/FragPipe*/fragpipe/bin/fragpipe.bat",
    "C:/FragPipe*/bin/fragpipe.bat",
    "C:/FragPipe*/bin/FragPipe*.exe",
    "C:/Program Files/FragPipe*/bin/FragPipe*.exe",
    os.path.expanduser("~/FragPipe*/fragpipe/bin/fragpipe.bat"),
    os.path.expanduser("~/Downloads/FragPipe*/fragpipe/bin/fragpipe.bat"),
)


# Files that mean "the search produced its main table", per method. Missing
# ones are recorded as warnings, not failures (names vary between versions).
EXPECTED_OUTPUTS = {
    "isoDTB": ("combined_modified_peptide_label_quant.tsv", "combined_modified_peptide.tsv"),
    "TMT": ("tmt-report/abundance_gene_MD.tsv", "tmt-report"),
    "DIA": ("diann-output/report.tsv", "diann-output/report.parquet", "diann-output",
            "dia-quant-output/report.tsv", "dia-quant-output/report.parquet", "dia-quant-output/report.pg_matrix.tsv"),
}


class Hold(Exception):
    """Not runnable yet for a reason outside the job; keep it queued."""


class JobError(Exception):
    """This job can't run as specified; fail it with this message."""


@dataclass
class RunSpec:
    job_id: int
    method: str
    dest: Path
    exe: Path
    workflow_src: Path
    fasta: Path | None  # None -> use whatever the workflow already says
    manifest_lines: list[tuple[str, str, int, str]]
    threads: int
    ram_gb: int
    timeout_minutes: int
    config_tools_folder: str = ""
    config_diann: str = ""
    annotations: dict[str, str] = field(default_factory=dict)  # TMT: file name (in raw dir) -> content
    raw_dir: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def run_dir(self) -> Path:
        return self.dest / RUN_DIR

    @property
    def workdir(self) -> Path:
        return self.dest / WORKDIR

    @property
    def manifest(self) -> Path:
        return self.run_dir / MANIFEST_NAME

    @property
    def workflow(self) -> Path:
        return self.run_dir / f"{self.method}.workflow"

    @property
    def console_log(self) -> Path:
        return self.run_dir / CONSOLE_LOG

    def command(self) -> list[str]:
        cmd = [str(self.exe), "--headless", "--workflow", _fwd(self.workflow), "--manifest", _fwd(self.manifest),
               "--workdir", _fwd(self.workdir)]
        if self.threads:
            cmd += ["--threads", str(self.threads)]
        if self.ram_gb:
            cmd += ["--ram", str(self.ram_gb)]
        if self.config_tools_folder:
            cmd += ["--config-tools-folder", self.config_tools_folder]
        if self.config_diann and self.method_is_dia:
            cmd += ["--config-diann", self.config_diann]
        return cmd

    @property
    def method_is_dia(self) -> bool:
        return any(line[3] == "DIA" for line in self.manifest_lines)


def _fwd(p: Path) -> str:
    return str(p).replace("\\", "/")


# ----------------------------------------------------------------- lookups --


def _version_of(path: Path) -> tuple[int, ...]:
    m = re.search(r"FragPipe(?:-jre)?-(\d+(?:\.\d+)*)", str(path), re.IGNORECASE)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def launcher_candidates() -> list[Path]:
    """Every FragPipe launcher found, best first: newest version, then no spaces in the path
    (FragPipe can't run from one), then fragpipe.bat before an .exe in the same folder."""
    seen: dict[str, Path] = {}
    for pattern in LAUNCHER_GLOBS:
        for hit in glob.glob(pattern):
            p = Path(hit)
            if p.name.lower() == "fragpipe.exe" and (p.parent / "fragpipe.bat").is_file():
                continue  # the zip layout's GUI exe; its .bat is the headless launcher
            seen.setdefault(str(p).lower(), p)
    return sorted(seen.values(), key=lambda p: (" " in str(p), tuple(-x for x in _version_of(p)),
                                                p.suffix.lower() != ".bat", str(p)))


def detect_launcher() -> Path | None:
    """Best guess at FragPipe's headless launcher on this machine (never one under a path with spaces)."""
    return next((p for p in launcher_candidates() if " " not in str(p)), None)


def resolve_launcher(cfg: Config) -> Path:
    """The configured launcher, preferring a fragpipe.bat that sits next to a configured .exe."""
    exe = cfg.fragpipe_exe
    if exe.suffix.lower() == ".exe" and (exe.parent / "fragpipe.bat").is_file():
        return exe.parent / "fragpipe.bat"
    if not exe.is_file():
        found = detect_launcher()
        hint = f" (found one at {found} — set it in the app, tab 1)" if found else ""
        raise Hold(f"FragPipe launcher not found at {exe}{hint}")
    return exe


def _find_file(name: str, folder: Path, suffix: str = "") -> Path | None:
    p = Path(name)
    cands = [p] if p.is_absolute() else [folder / name]
    if suffix and not name.lower().endswith(suffix):
        cands.append(cands[0].with_name(cands[0].name + suffix))
    return next((c for c in cands if c.is_file()), None)


_DB_RE = re.compile(r"^database\.db-path\s*[=:]\s*(.*)$", re.MULTILINE)


def workflow_db_path(text: str) -> str:
    """The database.db-path value in a .workflow (Java properties) text, unescaped; '' if unset."""
    m = _DB_RE.search(text)
    if not m:
        return ""
    return m.group(1).strip().replace("\\:", ":").replace("\\\\", "\\")


def patch_workflow(text: str, fasta: Path) -> str:
    """Set database.db-path to `fasta` (forward slashes need no escaping in Java properties)."""
    line = f"database.db-path={_fwd(fasta)}"
    if _DB_RE.search(text):
        return _DB_RE.sub(lambda _: line, text, count=1)
    return text.rstrip("\n") + "\n" + line + "\n"


# ---------------------------------------------------------------- prepare --


def prepare(job: Job, cfg: Config) -> RunSpec:
    """Everything needed to run `job`, checked. Raises Hold or JobError."""
    rec = job.parsed or {}
    plan = rec.get("plan") or {}
    overrides = plan.get("overrides") or {}
    method_cfg = cfg.methods.get(job.method)
    if method_cfg is None:
        raise Hold(f"method {job.method!r} is not in config.yaml any more")

    exe = resolve_launcher(cfg)

    dest = Path(job.dest_dir)
    if not dest.is_dir():
        raise JobError(f"experiment folder is gone: {dest}")

    # workflow: experiment.yaml override is the job's own choice -> JobError if missing;
    # the method default is setup -> Hold until someone puts the file there.
    wf_name = overrides.get("workflow") or method_cfg.workflow  # current config, not the one at intake
    wf = _find_file(wf_name, cfg.workflow_dir, ".workflow")
    if wf is None:
        where = cfg.workflow_dir / wf_name
        if overrides.get("workflow"):
            raise JobError(f"experiment.yaml asks for workflow {wf_name!r}, which is not in {cfg.workflow_dir}")
        raise Hold(f"workflow file for {job.method} missing: {where} (export it from FragPipe, see DEPLOY_WINDOWS.md A4)")

    warnings: list[str] = []
    fasta_name = overrides.get("fasta") or method_cfg.fasta
    fasta = _find_file(fasta_name, cfg.fasta_dir) if fasta_name else None
    if fasta is None:
        in_wf = workflow_db_path(wf.read_text(encoding="utf-8", errors="replace"))
        if in_wf and Path(in_wf).is_file():
            warnings.append(f"FASTA {fasta_name!r} not in {cfg.fasta_dir}; using the workflow's own database {in_wf}")
        elif overrides.get("fasta"):
            raise JobError(f"experiment.yaml asks for FASTA {fasta_name!r}, which is not in {cfg.fasta_dir}")
        else:
            raise Hold(f"FASTA for {job.method} missing: {cfg.fasta_dir / fasta_name} "
                       f"(and the workflow has no usable database.db-path)")

    lines = []
    missing = []
    for m in plan.get("manifest") or []:
        raw = dest / m["file"]
        if not raw.is_file():
            missing.append(m["file"])
        lines.append((str(raw), str(m["experiment"]), int(m["bioreplicate"]), str(m["data_type"])))
    if not lines:
        raise JobError("job has no raw files in its plan")
    if missing:
        more = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
        raise JobError(f"raw file(s) missing from {dest}: {', '.join(missing[:3])}{more}")
    empty = [Path(line[0]).name for line in lines if Path(line[0]).suffix.lower() == ".raw"
             and Path(line[0]).stat().st_size == 0]
    if empty:
        more = f" (+{len(empty) - 3} more)" if len(empty) > 3 else ""
        raise JobError(f"raw file(s) are empty (0 bytes): {', '.join(empty[:3])}{more} — an aborted acquisition or "
                       f"an interrupted copy; replace or remove them, then Retry")

    need_free_gb = getattr(cfg, "min_free_gb", 0) or 0
    if need_free_gb:
        raw_gb = sum(Path(line[0]).stat().st_size for line in lines) / 1e9
        need = need_free_gb + raw_gb  # FragPipe's intermediates are roughly the size of the raws
        free = _disk_free_gb(dest)
        if free is not None and free < need:
            raise Hold(f"low disk space: {free:.0f} GB free on {dest.anchor or dest}, this search needs "
                       f"~{need:.0f} GB (fragpipe.min_free_gb {need_free_gb} + raws {raw_gb:.1f})")

    raw_dir = dest / plan["raw_dir"] if plan.get("raw_dir") else dest
    annotations: dict[str, str] = {}
    if job.method == "TMT":
        experiments = sorted({line[1] for line in lines})
        try:
            ov = parse_overrides({"tmt": overrides["tmt"]}) if overrides.get("tmt") else None
            per_exp = tmt_annotation_files(ov, experiments) if ov else {}
        except OverridesError as exc:
            raise JobError(str(exc)) from exc
        if not per_exp:
            warnings.append("TMT job without a tmt: channel map in experiment.yaml; FragPipe will use its "
                            "default channel names")
        elif len(per_exp) == 1:
            annotations["annotation.txt"] = next(iter(per_exp.values()))
        else:
            annotations = {f"{exp}_annotation.txt": text for exp, text in per_exp.items()}
            warnings.append("several TMT plexes share one folder; how FragPipe 24 headless picks each "
                            "plex's annotation file is unconfirmed (docs/WORKFLOWS.md)")

    return RunSpec(
        job_id=job.id or 0, method=job.method, dest=dest, exe=exe, workflow_src=wf, fasta=fasta,
        manifest_lines=lines, threads=cfg.threads, ram_gb=cfg.ram_gb, timeout_minutes=cfg.timeout_minutes,
        config_tools_folder=cfg.config_tools_folder, config_diann=cfg.config_diann,
        annotations=annotations, raw_dir=raw_dir, warnings=warnings,
    )


def write_inputs(spec: RunSpec) -> str | None:
    """Create ionomos_run/ inputs and an empty workdir. Returns the name an old workdir was moved to, if any."""
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    moved = None
    if spec.workdir.exists() and any(spec.workdir.iterdir()):
        # FragPipe wants an empty output folder; keep the old attempt, never delete it
        moved = f"{WORKDIR}_previous_{datetime.now():%Y%m%d-%H%M%S}"
        os.replace(spec.workdir, spec.dest / moved)
    spec.workdir.mkdir(exist_ok=True)

    spec.manifest.write_text(fp_manifest_text(spec.manifest_lines), encoding="utf-8", newline="\n")
    text = spec.workflow_src.read_text(encoding="utf-8", errors="replace")
    if spec.fasta is not None:
        text = patch_workflow(text, spec.fasta)
    spec.workflow.write_text(text, encoding="utf-8", newline="\n")
    for name, content in spec.annotations.items():
        target = (spec.raw_dir or spec.dest) / name
        if target.is_file() and target.read_text(encoding="utf-8", errors="replace") != content:
            spec.warnings.append(f"kept the existing {target.name} (differs from experiment.yaml's tmt: map)")
            continue
        target.write_text(content, encoding="utf-8", newline="\n")
    return moved


# -------------------------------------------------------------------- run --


class RunResult:
    def __init__(self, code: int | None, reason: str = "", stopped: bool = False, timed_out: bool = False,
                 cancelled: bool = False, hints: list[str] | None = None):
        self.code, self.reason, self.stopped, self.timed_out = code, reason, stopped, timed_out
        self.cancelled = cancelled
        self.hints = hints or []

    @property
    def ok(self) -> bool:
        return self.code == 0 and not (self.stopped or self.timed_out or self.cancelled)


def _popen_kwargs() -> dict:
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """FragPipe starts Java, which starts MSFragger/IonQuant/DIA-NN: stop the whole tree."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run(spec: RunSpec, stop: threading.Event | None = None, on_start=None, poll: float = 1.0,
        on_poll=None) -> RunResult:
    """Run FragPipe for `spec`; blocks until it exits, times out, is cancelled, or `stop` is set."""
    stop = stop or threading.Event()
    cmd = spec.command()
    started = time.monotonic()
    deadline = started + spec.timeout_minutes * 60 if spec.timeout_minutes else None
    with open(spec.console_log, "ab") as out:
        out.write((f"# ionomos job {spec.job_id}  {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                   f"# {' '.join(cmd)}\n\n").encode())
        out.flush()
        try:
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    cwd=str(spec.run_dir), **_popen_kwargs())
        except OSError as exc:
            return RunResult(None, f"could not start FragPipe ({spec.exe}): {exc}")
        if on_start:
            on_start(proc.pid, cmd)
        while True:
            try:
                code = proc.wait(timeout=poll)
                break
            except subprocess.TimeoutExpired:
                pass
            if on_poll:
                try:
                    on_poll()
                except Exception:  # noqa: BLE001 - progress reporting must not kill a search
                    pass
            if (spec.run_dir / CANCEL_FILE).exists():
                kill_tree(proc)
                out.write(b"\n# cancelled by user\n")
                return RunResult(proc.returncode, "cancelled by user", cancelled=True)
            if stop.is_set():
                kill_tree(proc)
                out.write(b"\n# stopped by ionomos\n")
                return RunResult(proc.returncode, "stopped (ionomos was shut down)", stopped=True)
            if deadline and time.monotonic() > deadline:
                kill_tree(proc)
                out.write(f"\n# TIMEOUT after {spec.timeout_minutes} min\n".encode())
                return RunResult(proc.returncode, f"timed out after {spec.timeout_minutes} min "
                                                  f"(fragpipe.timeout_minutes)", timed_out=True)
        out.write(f"\n# exit code {code} after {(time.monotonic() - started) / 60:.1f} min\n".encode())
    text = read_tail_text(spec.console_log)
    hints = explain(text)
    if code != 0:
        lead = f"{hints[0]} — " if hints else ""
        return RunResult(code, f"{lead}FragPipe exited with code {code}; last lines: {tail(spec.console_log, 4)}",
                         hints=hints)
    bad_step = _FAILED_STEP.findall(text)
    if bad_step:
        name, c = bad_step[-1]
        return RunResult(1, f"FragPipe step {name} failed (exit code {c}) although FragPipe exited 0; "
                            f"last lines: {tail(spec.console_log, 4)}", hints=hints)
    produced = [p for p in spec.workdir.iterdir()] if spec.workdir.is_dir() else []
    if not produced:
        return RunResult(1, "FragPipe exited 0 but wrote nothing to the output folder; see "
                            f"{spec.console_log}", hints=hints)
    return RunResult(0)


def missing_outputs(spec: RunSpec) -> list[str]:
    want = EXPECTED_OUTPUTS.get(spec.method)
    if not want or any((spec.workdir / w).exists() for w in want):
        return []
    return [f"none of the expected {spec.method} outputs found in {WORKDIR}/: {', '.join(want)}"]


def tail(path: Path, n: int = 20) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    lines = [ln for ln in lines if ln.strip() and not ln.startswith("# ")]  # skip ionomos's own markers
    return " | ".join(lines[-n:])[-600:]


def _disk_free_gb(path: Path) -> float | None:
    from ionomos.health import disk_free_gb

    return disk_free_gb(path)


def read_tail_text(path: Path, max_bytes: int = 400_000) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - max_bytes))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


# ------------------------------------------------------------ explanations --

_FAILED_STEP = re.compile(r"Process '([^']+)' finished, exit code: (-?[1-9]\d*)")

# (regex on FragPipe's console output, plain-English explanation + what to do). First match first.
EXPLANATIONS: list[tuple[str, str]] = [
    (r"raw file\(s\) are empty",
     "A raw file is 0 bytes: the acquisition was aborted or the copy was interrupted — copy it again from the "
     "instrument PC (or remove it from the experiment folder), then Retry"),
    (r"raw file\(s\) missing from",
     "Raw files were moved or deleted from the experiment folder after it was filed — put them back, then Retry"),
    (r"FASTA file path is empty|No FASTA file|database\.db-path",
     "No protein database: set this method's FASTA in the app (tab 3), or re-export the workflow after setting it"),
    (r"OutOfMemoryError|Java heap space|GC overhead limit",
     "FragPipe ran out of memory: lower Threads or raise RAM (GB) in Advanced, and close other programs"),
    (r"not enough space on the disk|No space left on device|disk is full",
     "The disk is full: free space on the drive holding the experiment folders, then Retry"),
    (r"(?i)msfragger.{0,80}(not found|could not find|missing|download|license|not configured)|"
     r"(download|install).{0,40}msfragger",
     "MSFragger isn't installed for FragPipe: open the FragPipe GUI once, Config tab -> Download/Update "
     "MSFragger (accept the licence), then Retry"),
    (r"(?i)ionquant.{0,80}(not found|could not find|missing|download|license)",
     "IonQuant isn't installed for FragPipe: FragPipe GUI -> Config tab -> Download/Update IonQuant, then Retry"),
    (r"(?i)diatracer.{0,80}(not found|could not find|missing|download|license)",
     "diaTracer isn't installed for FragPipe: FragPipe GUI -> Config tab -> Download/Update diaTracer"),
    (r"(?i)dia-?nn.{0,80}(not found|could not find|missing|not executable|no such file)",
     "FragPipe can't find DIA-NN: set 'DIA-NN exe' in Advanced (e.g. C:/DIA-NN/2.3.2/DiaNN.exe)"),
    (r"used by another process|cannot access the file",
     "A file was open in another program (Xcalibur, Excel, Explorer preview): close it, then Retry"),
    (r"(?i)(RawFileReader|ThermoRawFileParser|error (loading|reading).{0,60}\.raw|\.raw.{0,60}(corrupt|truncated))",
     "A .raw file couldn't be read: it may be incomplete or corrupt — re-copy it from the instrument PC"),
    (r"UnsupportedClassVersionError|Unsupported class file major version",
     "Wrong Java version: FragPipe must use its bundled Java — reinstall FragPipe or set its launcher again"),
    (r"Could not find or load main class|Unable to access jarfile",
     "The FragPipe installation looks broken: re-select fragpipe.bat (tab 1, Find FragPipe) or reinstall FragPipe"),
    (r"is not recognized as an internal or external command",
     "The FragPipe launcher path is wrong: tab 1 -> Find FragPipe"),
    (r"Access is denied|Permission denied",
     "Windows refused access to a file or folder: check the experiment folder isn't read-only / open elsewhere"),
    (r"(?i)output directory.{0,40}not empty|workdir.{0,40}not empty",
     "FragPipe wants an empty output folder: Retry (ionomos moves the old output aside)"),
    (r"(?i)philosopher.{0,120}(error|fatal)",
     "Philosopher (the FDR/report tool) failed — often a leftover lock from an interrupted run: Retry once"),
    (r"(?i)(no|0) (psms|peptides|proteins) (were )?(found|identified|passed)",
     "The search found no identifications: wrong FASTA/species, wrong method, or empty/blank runs"),
    (r"Exception in thread|java\.lang\.\w+Exception",
     "FragPipe (Java) hit an internal error: see the console log; Retry once, then send diagnostics"),
]
_EXPLAIN = [(re.compile(rx), msg) for rx, msg in EXPLANATIONS]


def explain(console_text: str) -> list[str]:
    """Plain-English causes for a failed run, most specific first (empty if nothing recognised)."""
    return [msg for rx, msg in _EXPLAIN if rx.search(console_text)]


# ----------------------------------------------------------------- progress --

_TASK = re.compile(r"^([A-Za-z][\w .\-]{1,50}?) \[Work dir: ", re.MULTILINE)
_DONE_TASK = re.compile(r"Process '([^']+)' finished, exit code: (\d+)")
KNOWN_STEPS = ("MSFragger", "MSBooster", "Percolator", "PeptideProphet", "ProteinProphet", "PTMProphet",
               "Philosopher", "FreeQuant", "IonQuant", "TMT-Integrator", "TMTIntegrator", "diaTracer",
               "DIA-NN", "EasyPQP", "Spectral library", "Crystal-C", "PTM-Shepherd", "Report")


def progress(console_log: Path) -> str:
    """Best-effort 'current step (n finished)' from FragPipe's console output."""
    text = read_tail_text(console_log, 200_000)
    if not text:
        return "starting"
    started = _TASK.findall(text)
    finished = _DONE_TASK.findall(text)
    if started:
        return f"{started[-1].strip()} ({len(finished)} step(s) done)"
    for ln in reversed(text.splitlines()[-60:]):
        for step in KNOWN_STEPS:
            if step.lower() in ln.lower():
                return step
    return "running"


# ------------------------------------------------------------ inspections --

_FASTA_CACHE: dict[tuple[str, float, int], dict] = {}


def fasta_info(path: Path, decoy_tag: str = "rev_") -> dict:
    """{'entries', 'decoys', 'gb'} for a FASTA; cached by (path, mtime, size)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return {"entries": 0, "decoys": 0, "gb": 0.0, "error": "not found"}
    key = (str(p), st.st_mtime, st.st_size)
    if key in _FASTA_CACHE:
        return _FASTA_CACHE[key]
    entries = decoys = 0
    tag = b">" + decoy_tag.encode()
    try:
        with open(p, "rb") as fh:
            for line in fh:
                if line.startswith(b">"):
                    entries += 1
                    if line.startswith(tag):
                        decoys += 1
    except OSError as exc:
        return {"entries": 0, "decoys": 0, "gb": 0.0, "error": str(exc)}
    info = {"entries": entries, "decoys": decoys, "gb": st.st_size / 1e9}
    _FASTA_CACHE[key] = info
    return info


# (workflow key, label). Shown when present, so a summary never lies about keys it doesn't know.
WORKFLOW_KEYS = (
    ("workflow.description", "description"),
    ("database.decoy-tag", "decoy tag"),
    ("msfragger.run-msfragger", "MSFragger"),
    ("ionquant.run-ionquant", "IonQuant"),
    ("ionquant.mbr", "match-between-runs"),
    ("tmtintegrator.run-tmtintegrator", "TMT-Integrator"),
    ("diann.run-dia-nn", "DIA-NN"),
    ("diatracer.run-diatracer", "diaTracer"),
    ("speclibgen.run-speclibgen", "spectral library"),
)


def read_properties(text: str) -> dict[str, str]:
    out = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("#", "!")):
            continue
        m = re.match(r"([^=:\s]+)\s*[=:]\s*(.*)$", ln)
        if m:
            out[m.group(1)] = m.group(2).replace("\\:", ":").replace("\\\\", "\\")
    return out


def inspect_workflow(path: Path) -> dict:
    """{'database', 'database_exists', 'settings': {label: value}, 'decoy_tag'} for a .workflow file."""
    try:
        props = read_properties(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return {"error": str(exc)}
    db = props.get("database.db-path", "")
    return {
        "database": db,
        "database_exists": bool(db) and Path(db).is_file(),
        "decoy_tag": props.get("database.decoy-tag", "rev_") or "rev_",
        "settings": {label: props[k] for k, label in WORKFLOW_KEYS if k in props and props[k] != ""},
    }


def describe_method(cfg: Config, key: str) -> list[tuple[bool | None, str]]:
    """Human-readable readiness lines for one method: [(ok?, text)]. ok None = warning."""
    m = cfg.methods[key]
    return describe_files(cfg.workflow_dir, cfg.fasta_dir, m.workflow, m.fasta)


def describe_files(workflow_dir: Path, fasta_dir: Path, workflow: str, fasta: str) -> list[tuple[bool | None, str]]:
    out: list[tuple[bool | None, str]] = []
    wf = _find_file(workflow, Path(workflow_dir), ".workflow") if workflow else None
    if wf is None:
        return [(False, f"workflow {workflow or '(none)'} not found in {workflow_dir}")]
    info = inspect_workflow(wf)
    settings = ", ".join(f"{k}={v}" for k, v in info.get("settings", {}).items() if k != "description")
    out.append((True, f"workflow {wf.name}" + (f" [{settings}]" if settings else "")))
    fa = _find_file(fasta, Path(fasta_dir)) if fasta else None
    source = "method setting"
    if fa is None and info.get("database_exists"):
        fa, source = Path(info["database"]), "workflow's own database"
    if fa is None:
        out.append((False, f"FASTA {fasta or '(none)'} not found in {fasta_dir}"))
        return out
    fi = fasta_info(fa, info.get("decoy_tag", "rev_"))
    if fi.get("error"):
        out.append((False, f"FASTA {fa}: {fi['error']}"))
    elif fi["decoys"] == 0:
        out.append((None, f"FASTA {fa.name} ({fi['entries']} entries, {source}) has NO decoys "
                          f"(no '>{info.get('decoy_tag', 'rev_')}' entries) — add decoys in FragPipe's Database tab"))
    else:
        out.append((True, f"FASTA {fa.name} ({fi['entries'] - fi['decoys']} targets + {fi['decoys']} decoys, {source})"))
    return out


def fragpipe_root(launcher: Path) -> Path | None:
    """The folder holding bin/, lib/, tools/: <x>/fragpipe (zip builds) or C:/FragPipe/FragPipe-24.0 (installer)."""
    p = Path(launcher)
    return p.parent.parent if p.parent.name.lower() == "bin" else None


def install_report(cfg: Config) -> list[tuple[bool | None, str, str]]:
    """What the FragPipe installation has: [(ok?, label, detail)] — static checks, starts nothing."""
    rows: list[tuple[bool | None, str, str]] = []
    try:
        exe = resolve_launcher(cfg)
    except Hold as exc:
        return [(False, "launcher", str(exc))]
    rows.append((True, "launcher", str(exe)))
    root = fragpipe_root(exe)
    if root is None or not root.is_dir():
        rows.append((None, "install folder", "launcher is not in a standard fragpipe/bin folder; skipping deeper checks"))
        return rows
    ver = re.search(r"FragPipe-([\d.]+)", str(root))
    jars = sorted(root.glob("lib/fragpipe*.jar"))
    rows.append((bool(jars) or None, "FragPipe", (f"version {ver.group(1)}" if ver else "version ?") +
                 (f", {jars[-1].name}" if jars else ", lib/fragpipe*.jar not found")))
    java = [j for j in (root.parent / "jre" / "bin" / "java.exe", root / "jre" / "bin" / "java.exe",
                        root.parent / "jre" / "bin" / "java", root / "jre" / "bin" / "java") if j.is_file()]
    rows.append((True if java else None, "bundled Java", str(java[0]) if java else "not found (FragPipe will use the system Java)"))
    tools_dirs = [Path(cfg.config_tools_folder)] if cfg.config_tools_folder else []
    tools_dirs += [root / "tools", root.parent / "tools"]

    def find(pattern: str) -> Path | None:
        for d in tools_dirs:
            if d.is_dir():
                hits = sorted(d.rglob(pattern))
                if hits:
                    return hits[-1]
        return None

    for label, pattern, needed_for in (("MSFragger", "MSFragger*.jar", "every search"),
                                       ("IonQuant", "IonQuant*.jar", "isoDTB / label-free / TMT quant"),
                                       ("diaTracer", "diaTracer*.jar", "some DIA workflows")):
        hit = find(pattern)
        rows.append((True if hit else None, label,
                     hit.name if hit else f"not found — needed for {needed_for}. FragPipe GUI -> Config tab -> "
                                          f"Download/Update {label} (licence)"))
    diann = Path(cfg.config_diann) if cfg.config_diann else find("DiaNN.exe") or find("diann*")
    rows.append((True if diann and Path(diann).exists() else None, "DIA-NN",
                 str(diann) if diann and Path(diann).exists() else "not found (only needed for DIA)"))
    return rows


def import_workflow(src: Path, method: str, workflow_dir: Path, fasta_dir: Path) -> dict:
    """Copy a .workflow (e.g. a good run's fragpipe.workflow) into workflow_dir as <method>.workflow.

    Also copies the FASTA it points at into fasta_dir if it isn't there yet.
    An existing <method>.workflow is kept as <method>.workflow.bak-<ts>.
    Returns {'workflow': name, 'fasta': name or '', 'notes': [...]}.
    """
    src = Path(src)
    notes: list[str] = []
    workflow_dir.mkdir(parents=True, exist_ok=True)
    dest = workflow_dir / f"{method}.workflow"
    if dest.exists() and dest.resolve() != src.resolve():
        bak = dest.with_name(f"{dest.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        os.replace(dest, bak)
        notes.append(f"previous {dest.name} kept as {bak.name}")
    if dest.resolve() != src.resolve():
        dest.write_bytes(src.read_bytes())
    info = inspect_workflow(dest)
    fasta_name = ""
    db = info.get("database") or ""
    if db and Path(db).is_file():
        fasta_dir.mkdir(parents=True, exist_ok=True)
        target = fasta_dir / Path(db).name
        if not target.exists():
            import shutil

            shutil.copy2(db, target)
            notes.append(f"copied FASTA {Path(db).name} into {fasta_dir}")
        fasta_name = target.name
    elif db:
        notes.append(f"the workflow's database {db} doesn't exist on this PC — pick a FASTA for {method}")
    else:
        notes.append(f"the workflow has no database set — pick a FASTA for {method}")
    return {"workflow": dest.name, "fasta": fasta_name, "notes": notes}
