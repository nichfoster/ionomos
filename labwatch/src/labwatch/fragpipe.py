"""
FragPipe headless runner: everything needed to turn a queued job into one
`fragpipe --headless` process, and nothing about queues or state.

    spec = prepare(job, cfg)          # -> RunSpec, or raises Hold / JobError
    write_inputs(spec)                # manifest, patched workflow, TMT annotation
    code = run(spec, stop_event)      # blocks; tees console output to spec.console_log

Per job, inside the experiment folder:

    <experiment>/
      labwatch_run/                     inputs we generate + FragPipe's console output
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

from labwatch.config import Config
from labwatch.ledger import Job
from labwatch.manifest import OverridesError, fp_manifest_text, parse_overrides, tmt_annotation_files

RUN_DIR = "labwatch_run"
WORKDIR = "fragpipe"
MANIFEST_NAME = "fragpipe-files.fp-manifest"
CONSOLE_LOG = "fragpipe_console.log"

# Where FragPipe usually lives on Windows. First match wins. The .bat is the
# headless launcher; fragpipe.exe is a GUI wrapper that may swallow console output.
LAUNCHER_GLOBS = (
    "C:/FragPipe/FragPipe-*/fragpipe/bin/fragpipe.bat",
    "C:/FragPipe/*/fragpipe/bin/fragpipe.bat",
    "C:/FragPipe*/fragpipe/bin/fragpipe.bat",
    "C:/Program Files/FragPipe*/fragpipe/bin/fragpipe.bat",
    os.path.expanduser("~/FragPipe*/fragpipe/bin/fragpipe.bat"),
    os.path.expanduser("~/Downloads/FragPipe*/fragpipe/bin/fragpipe.bat"),
)

# Files that mean "the search produced its main table", per method. Missing
# ones are recorded as warnings, not failures (names vary between versions).
EXPECTED_OUTPUTS = {
    "isoDTB": ("combined_modified_peptide_label_quant.tsv", "combined_modified_peptide.tsv"),
    "TMT": ("tmt-report/abundance_gene_MD.tsv", "tmt-report"),
    "DIA": ("diann-output/report.tsv", "diann-output/report.parquet", "diann-output"),
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


def detect_launcher() -> Path | None:
    """Best guess at FragPipe's headless launcher on this machine, or None."""
    for pattern in LAUNCHER_GLOBS:
        hits = sorted(glob.glob(pattern), reverse=True)  # newest version name first
        if hits:
            return Path(hits[0])
    return None


def resolve_launcher(cfg: Config) -> Path:
    """The configured launcher, preferring fragpipe.bat when fragpipe.exe was configured next to one."""
    exe = cfg.fragpipe_exe
    if exe.name.lower() == "fragpipe.exe" and (exe.parent / "fragpipe.bat").is_file():
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
    """Create labwatch_run/ inputs and an empty workdir. Returns the name an old workdir was moved to, if any."""
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
    def __init__(self, code: int | None, reason: str = "", stopped: bool = False, timed_out: bool = False):
        self.code, self.reason, self.stopped, self.timed_out = code, reason, stopped, timed_out

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.stopped and not self.timed_out


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


def run(spec: RunSpec, stop: threading.Event | None = None, on_start=None, poll: float = 1.0) -> RunResult:
    """Run FragPipe for `spec`; blocks until it exits, times out, or `stop` is set."""
    stop = stop or threading.Event()
    cmd = spec.command()
    started = time.monotonic()
    deadline = started + spec.timeout_minutes * 60 if spec.timeout_minutes else None
    with open(spec.console_log, "ab") as out:
        out.write((f"# labwatch job {spec.job_id}  {datetime.now():%Y-%m-%d %H:%M:%S}\n"
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
            if stop.is_set():
                kill_tree(proc)
                out.write(b"\n# stopped by labwatch\n")
                return RunResult(proc.returncode, "stopped (labwatch was shut down)", stopped=True)
            if deadline and time.monotonic() > deadline:
                kill_tree(proc)
                out.write(f"\n# TIMEOUT after {spec.timeout_minutes} min\n".encode())
                return RunResult(proc.returncode, f"timed out after {spec.timeout_minutes} min "
                                                  f"(fragpipe.timeout_minutes)", timed_out=True)
        out.write(f"\n# exit code {code} after {(time.monotonic() - started) / 60:.1f} min\n".encode())
    if code != 0:
        return RunResult(code, f"FragPipe exited with code {code}; last lines: {tail(spec.console_log, 4)}")
    produced = [p for p in spec.workdir.iterdir()] if spec.workdir.is_dir() else []
    if not produced:
        return RunResult(1, "FragPipe exited 0 but wrote nothing to the output folder; see "
                            f"{spec.console_log}")
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
    lines = [ln for ln in lines if ln.strip() and not ln.startswith("# ")]  # skip labwatch's own markers
    return " | ".join(lines[-n:])[-600:]
