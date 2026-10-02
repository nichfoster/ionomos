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
#   Windows installer (23, 24):  C:/FragPipe/FragPipe-24.0/bin/fragpipe.bat + FragPipe-24.0.exe, jre/, lib/, tools/
# fragpipe.bat is the headless launcher (FragPipe's headless tutorial; Gradle's start script, made by FragPipe's
# build for every release). The .exe beside it is a launch4j window program: see resolve_launcher.
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


# Files that mean "the search produced its main table", per method kind (Config.kind, D54).
# Missing ones are recorded as warnings, not failures (names vary between versions).
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
    kind: str = ""  # what the method behaves as (Config.kind); "" = its own key
    config_python: str = ""
    env: dict[str, str] = field(default_factory=dict)  # added to the engine's environment (launcher_env)
    notes: list[str] = field(default_factory=list)  # worth recording (log, ionomos.json), not worth a warning

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
        if self.config_python:
            cmd += ["--config-python", self.config_python]
        return cmd

    engine_name = "FragPipe"  # the program run, for messages

    def expected_outputs(self) -> tuple[str, ...]:
        return EXPECTED_OUTPUTS.get(self.kind or self.method, ())

    @property
    def method_is_dia(self) -> bool:
        return any(str(line[3]).upper().startswith(("DIA", "GPF-DIA")) for line in self.manifest_lines)


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


def is_window_exe(launcher: Path) -> bool:
    """FragPipe's own .exe in a real install (bin/FragPipe-24.0.exe, or the zip builds' bin/fragpipe.exe).

    It is a launch4j wrapper with the default "gui" header (FragPipe-GUI/build.gradle, 23.1 and 24.0): it starts
    javaw and, by launch4j's documentation, does not wait for it or pass its output on. Run by Ionomos it would
    return at once with code 0 while the search goes on unseen, so it is never run headless."""
    p = Path(launcher)
    root = fragpipe_root(p)
    return (p.suffix.lower() == ".exe" and p.name.lower().startswith("fragpipe") and root is not None
            and any(root.glob("lib/fragpipe*.jar")))


def resolve_launcher(cfg: Config) -> Path:
    """The configured launcher, preferring a fragpipe.bat that sits next to a configured .exe."""
    exe = cfg.fragpipe_exe
    if exe.suffix.lower() == ".exe" and (exe.parent / "fragpipe.bat").is_file():
        return exe.parent / "fragpipe.bat"
    if not exe.is_file():
        found = detect_launcher()
        hint = f" (found one at {found} — set it in the app, tab 1)" if found else ""
        raise Hold(f"FragPipe launcher not found at {exe}{hint}")
    if is_window_exe(exe):
        raise Hold(f"FragPipe launcher {exe} is FragPipe's window program and can't run a search unattended; "
                   f"{exe.parent / 'fragpipe.bat'} is missing (FragPipe 23 and 24 install it next to the .exe). "
                   f"Reinstall FragPipe, or set a launcher that has fragpipe.bat (tab 1)")
    return exe


def bundled_java_home(launcher: Path) -> Path | None:
    """The Java FragPipe ships with: <root>/jre (installer, 23 / 24) or beside <root> (zip builds), if it is there."""
    root = fragpipe_root(Path(launcher))
    if root is None:
        return None
    for home in (root / "jre", root.parent / "jre"):
        if (home / "bin" / "java.exe").is_file() or (home / "bin" / "java").is_file():
            return home
    return None


def launcher_env(launcher: Path) -> dict[str, str]:
    """What the launcher needs in its environment.

    fragpipe.bat is Gradle's start script: it runs %JAVA_HOME%\\bin\\java.exe, else `java` from PATH, and stops
    with "JAVA_HOME is not set and no 'java' command could be found in your PATH" when neither exists. The lab PC
    has no Java on PATH (docs/PROTEOMICS_PC.md), so JAVA_HOME is pointed at FragPipe's own JRE: the one its .exe
    uses (launch4j bundledJrePath ../jre) and the one FragPipe is tested with."""
    home = bundled_java_home(launcher)
    if home is None or Path(launcher).suffix.lower() == ".exe":
        return {}
    return {"JAVA_HOME": str(home)}


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


def fp_experiment(name: str) -> str:
    """The experiment name as FragPipe uses it: InputLcmsFile replaces everything but letters, digits and _."""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name).strip())


BIG_FASTA_BYTES = 1 << 30  # TabDatabase.databaseSizeLimit: FragPipe doesn't count the decoys of a bigger file
_VALIDATION_KEYS = ("percolator.run-percolator", "peptide-prophet.run-peptide-prophet", "phi-report.run-report")


def decoy_problem(props: dict[str, str], fasta: Path) -> str:
    """Why headless FragPipe would refuse this FASTA; '' when it wouldn't, or when that can't be told.

    FragpipeRun.checkDbConfig (24.0): when Percolator, PeptideProphet or the report runs, a headless run stops
    at once ("No decoys found in the FASTA file." / "FASTA file contains 12.5% decoys.") unless 40-60 % of the
    entries start with the workflow's decoy tag. In the GUI the same is a question a person can click through."""
    if not any(props.get(k, "").strip().lower() == "true" for k in _VALIDATION_KEYS):
        return ""
    tag = props.get("database.decoy-tag", "rev_") or "rev_"
    info = fasta_info(fasta, tag)
    if info.get("error") or info["gb"] * 1e9 >= BIG_FASTA_BYTES:
        return ""
    name = Path(fasta).name
    if info["entries"] == 0:
        return f"{name} has no protein entries"
    share = info["decoys"] / info["entries"]
    if share == 0:
        return (f"{name} has no decoys (no entry starts with '>{tag}'); FragPipe stops on that. Add them in "
                f"FragPipe's Database tab (Add decoys) and use that file")
    if share < 0.4 or share > 0.6:
        return (f"{100 * share:.1f} % of the entries of {name} are decoys ('>{tag}'); FragPipe stops unless "
                f"about half (40-60 %) are. Add decoys once, to a FASTA without any (FragPipe's Database tab)")
    return ""


def annotation_problems(per_exp: dict[str, str], label_type: str = "") -> list[str]:
    """What FragPipe would reject in TMT annotation files ({plex: text}); [] when nothing.

    From TmtiPanel.parseTmtAnnotationFile and CmdTmtIntegrator (24.0): a line is "<channel> <sample>" split on
    whitespace, so a sample name can't hold a space or be empty (NA leaves a channel out); a file lists as many
    channels as the workflow's label type (tmtintegrator.channel_num, e.g. TMT-10); a sample name is used once
    across all plexes."""
    out: list[str] = []
    m = re.search(r"-(\d+)$", label_type.strip())
    want = int(m.group(1)) if m else 0
    seen: dict[str, str] = {}
    for exp, text in per_exp.items():
        rows = [ln.split() for ln in text.splitlines() if ln.strip()]
        odd = [" ".join(r) for r in rows if len(r) != 2]
        if odd:
            out.append(f"plex {exp}: a channel needs one sample name without spaces (NA to leave it out), "
                       f"got {odd[0]!r}")
            continue
        if want and len(rows) != want:
            out.append(f"plex {exp} lists {len(rows)} channel(s) but the workflow's label type is {label_type} "
                       f"({want}); list every channel, with NA for the unused ones")
        for _ch, sample in rows:
            if sample.upper() == "NA":
                continue
            if sample in seen:
                out.append(f"sample name {sample} is used twice (plex {seen[sample]} and {exp}); FragPipe needs "
                           f"every name once across all plexes")
            seen.setdefault(sample, exp)
    return out


# ---------------------------------------------------------------- prepare --


def check_raws(dest: Path, plan: dict, cfg: Config) -> list[tuple[str, str, int, str]]:
    """The job's raw files as (path, experiment, bioreplicate, data type), checked: all there, none empty,
    enough disk space. Raises JobError / Hold. Shared by every engine's runner."""
    lines = []
    missing = []
    empty = []
    sizes: dict[str, int] = {}
    for m in plan.get("manifest") or []:
        raw = dest / m["file"]
        lines.append((str(raw), str(m["experiment"]), int(m["bioreplicate"]), str(m["data_type"])))
        if not raw.is_file():
            missing.append(m["file"])
            continue
        try:
            size = raw.stat().st_size  # the one stat per raw
        except FileNotFoundError:
            missing.append(m["file"])  # vanished since the is_file() check — same "missing" failure
            continue
        except OSError as exc:
            raise JobError(f"could not check raw file {raw.name}: {exc}") from exc
        sizes[str(raw)] = size
        if raw.suffix.lower() == ".raw" and size == 0:
            empty.append(raw.name)
    if not lines:
        raise JobError("job has no raw files in its plan")
    if missing:
        more = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
        raise JobError(f"raw file(s) missing from {dest}: {', '.join(missing[:3])}{more}")
    if empty:
        more = f" (+{len(empty) - 3} more)" if len(empty) > 3 else ""
        raise JobError(f"raw file(s) are empty (0 bytes): {', '.join(empty[:3])}{more} — an aborted acquisition or "
                       f"an interrupted copy; replace or remove them, then Retry")

    need_free_gb = getattr(cfg, "min_free_gb", 0) or 0
    if need_free_gb:
        raw_gb = sum(sizes.values()) / 1e9
        need = need_free_gb + raw_gb  # FragPipe's intermediates are roughly the size of the raws
        free = _disk_free_gb(dest)
        if free is not None and free < need:
            raise Hold(f"low disk space: {free:.0f} GB free on {dest.anchor or dest}, this search needs "
                       f"~{need:.0f} GB (fragpipe.min_free_gb {need_free_gb} + raws {raw_gb:.1f})")

    return lines


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

    props = read_properties(wf.read_text(encoding="utf-8", errors="replace"))
    used_fasta = fasta or Path(props.get("database.db-path", ""))
    problem = decoy_problem(props, used_fasta)
    if problem:
        raise Hold(f"FASTA for {job.method} can't be searched: {problem}")

    lines = check_raws(dest, plan, cfg)
    notes: list[str] = []
    renamed = sorted({line[1] for line in lines if fp_experiment(line[1]) != line[1]})
    if renamed:
        notes.append("FragPipe keeps only letters, digits and _ in experiment names: its output will say "
                     + ", ".join(f"{fp_experiment(e)} for {e}" for e in renamed[:3])
                     + (f" (+{len(renamed) - 3} more)" if len(renamed) > 3 else ""))

    raw_dir = dest / plan["raw_dir"] if plan.get("raw_dir") else dest
    annotations: dict[str, str] = {}
    kind = cfg.kind(job.method)
    if kind == "TMT":
        experiments = sorted({line[1] for line in lines})
        try:
            ov = parse_overrides({"tmt": overrides["tmt"]}) if overrides.get("tmt") else None
            per_exp = tmt_annotation_files(ov, experiments) if ov else {}
        except OverridesError as exc:
            raise JobError(str(exc)) from exc
        if not per_exp:
            warnings.append("TMT job without a tmt: channel map in experiment.yaml; FragPipe will use its "
                            "default channel names")
        else:
            # FragPipe (TmtiPanel, 23.1 and 24.0) takes a plex's annotation from the one folder that holds all
            # its files, and only when exactly one file ending in annotation.txt is in it.
            folders = {exp: {str(Path(line[0]).parent) for line in lines if line[1] == exp} for exp in per_exp}
            own_folder = (all(len(fs) == 1 for fs in folders.values())
                          and len({f for fs in folders.values() for f in fs}) == len(per_exp))
            if len(per_exp) == 1:
                annotations["annotation.txt"] = next(iter(per_exp.values()))
            elif own_folder:
                annotations = {str(Path(next(iter(folders[exp]))) / "annotation.txt"): text
                               for exp, text in per_exp.items()}
            else:
                warnings.append(f"{len(per_exp)} TMT plexes share one folder: FragPipe reads one annotation file "
                                "per folder, so none is written and it will name the channels <plex>_<channel>. "
                                "Put each plex's raw files in a folder of its own to get the sample names")
            if annotations:
                bad = annotation_problems(per_exp, props.get("tmtintegrator.channel_num", ""))
                if bad:
                    raise JobError("experiment.yaml tmt: " + "; ".join(bad))

    return RunSpec(
        job_id=job.id or 0, method=job.method, dest=dest, exe=exe, workflow_src=wf, fasta=fasta,
        manifest_lines=lines, threads=cfg.threads, ram_gb=cfg.ram_gb, timeout_minutes=cfg.timeout_minutes,
        config_tools_folder=cfg.config_tools_folder, config_diann=cfg.config_diann,
        annotations=annotations, raw_dir=raw_dir, warnings=warnings, kind=kind,
        config_python=cfg.config_python, env=launcher_env(exe), notes=notes,
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
        # FragPipe uses a folder's annotation only when exactly one file ending in annotation.txt is there: a
        # second one beside somebody's own file would make it use neither
        others = sorted(p.name for p in target.parent.iterdir()
                        if p.is_file() and p.name.endswith("annotation.txt") and p.name != target.name)
        if others:
            spec.warnings.append(
                f"kept {', '.join(others)} and wrote no {target.name} beside it (experiment.yaml's tmt: map was not "
                f"applied)" + ("; FragPipe uses a folder's annotation file only when there is exactly one, so it "
                               "will name the channels <plex>_<channel>" if len(others) > 1 else ""))
            continue
        target.write_text(content, encoding="utf-8", newline="\n")
    return moved


# -------------------------------------------------------------------- run --


class RunResult:
    def __init__(self, code: int | None, reason: str = "", stopped: bool = False, timed_out: bool = False,
                 cancelled: bool = False, hints: list[str] | None = None, warnings: list[str] | None = None):
        self.code, self.reason, self.stopped, self.timed_out = code, reason, stopped, timed_out
        self.cancelled = cancelled
        self.hints = hints or []
        self.warnings = warnings or []  # the run counts as done, but something about it should be looked at
        self.seconds: float | None = None  # how long the engine ran (set by run)
        self.started_at = ""
        self.console_offset = 0  # where this attempt begins in the console log

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
    """Run the engine for `spec` (FragPipe, or DIA-NN via diann.py); blocks until it exits, times out, is
    cancelled, or `stop` is set."""
    stop = stop or threading.Event()
    started = time.monotonic()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        offset = spec.console_log.stat().st_size  # the log keeps every attempt; this one starts here
    except OSError:
        offset = 0
    res = _run(spec, stop, on_start, poll, on_poll, started)
    res.seconds = round(time.monotonic() - started, 1)
    res.started_at = started_at
    res.console_offset = offset
    return res


def attempt_text(console_text: str) -> str:
    """The latest attempt's part of a console log (the log holds every attempt, each under a '# ionomos job' line)."""
    at = console_text.rfind("\n# ionomos job ")
    return console_text[at + 1:] if at >= 0 else console_text


def _run(spec: RunSpec, stop: threading.Event, on_start, poll: float, on_poll, started: float) -> RunResult:
    cmd = spec.command()
    env = getattr(spec, "env", None) or {}
    deadline = started + spec.timeout_minutes * 60 if spec.timeout_minutes else None
    with open(spec.console_log, "ab") as out:
        out.write((f"# ionomos job {spec.job_id}  {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                   f"# {' '.join(cmd)}\n"
                   + "".join(f"# {k}={v}\n" for k, v in env.items()) + "\n").encode())
        out.flush()
        try:
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    cwd=str(spec.run_dir), env={**os.environ, **env} if env else None,
                                    **_popen_kwargs())
        except OSError as exc:
            return RunResult(None, f"could not start {spec.engine_name} ({spec.exe}): {exc}")
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
    text = attempt_text(read_tail_text(spec.console_log))  # this attempt only: the log keeps the earlier ones
    hints = explain(text)
    bad_step = failed_step(text)  # the first failure is the cause; FragPipe cancels the rest
    if code != 0:
        lead = f"{hints[0]} — " if hints else ""
        if bad_step:
            name, c, said = bad_step
            return RunResult(code, f"{lead}FragPipe step {name} failed (exit code {c}); it said: {said}", hints=hints)
        return RunResult(code, f"{lead}{spec.engine_name} exited with code {code}; last lines: {tail(spec.console_log, 4)}",
                         hints=hints)
    if bad_step:
        name, c, said = bad_step
        return RunResult(1, f"FragPipe step {name} failed (exit code {c}) although FragPipe exited 0; "
                            f"it said: {said}", hints=hints)
    cancelled = _CANCELLED_TASKS.search(text)
    if cancelled:
        return RunResult(1, f"FragPipe stopped early (cancelled {cancelled.group(1)} remaining task(s)) although it "
                            f"exited 0; last lines: {tail(spec.console_log, 4)}", hints=hints)
    produced = [p for p in spec.workdir.iterdir()] if spec.workdir.is_dir() else []
    if not produced:
        return RunResult(1, f"{spec.engine_name} exited 0 but wrote nothing to the output folder; see "
                            f"{spec.console_log}", hints=hints)
    warnings = []
    if spec.engine_name == "FragPipe" and _DRY_RUN.search(text):
        return RunResult(1, "FragPipe only did a dry run (its log says so) and searched nothing", hints=hints)
    if spec.engine_name == "FragPipe" and not _ALL_DONE.search(text):
        # FragPipe's source prints this line at the end of every complete run. A warning, not a failure, until
        # a real headless run on the PC has shown the line in the console Ionomos captures (docs/ROADMAP.md)
        warnings.append("FragPipe's log has no 'ALL JOBS DONE' line: check the end of "
                        f"{spec.console_log.name} before trusting the output")
    return RunResult(0, warnings=warnings)


def failed_step(console_text: str) -> tuple[str, str, str] | None:
    """(step, exit code, the last lines it printed) for the first step FragPipe reports as failed. After that
    line FragPipe only prints "Process returned non-zero exit code, stopping" and "Cancelling N remaining tasks",
    so the step's own last lines are the useful ones."""
    m = _FAILED_STEP.search(console_text)
    if not m:
        return None
    said = [ln.strip() for ln in console_text[:m.start()].splitlines() if ln.strip() and not ln.startswith("# ")]
    return m.group(1), m.group(2), " | ".join(said[-4:])[-600:]


def missing_outputs(spec: RunSpec) -> list[str]:
    want = spec.expected_outputs()
    if not want or any((spec.workdir / w).exists() for w in want):
        return []
    return [f"none of the expected {spec.method} outputs found in {spec.workdir.name}/: {', '.join(want)}"]


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

# What FragPipe prints (24.0 source, checked against 23.1; the file each line comes from is named):
#   "Process 'MSFragger' finished, exit code: 1"       ProcessBuilderInfo.toRunnable, after every step
#   "Process returned non-zero exit code, stopping"    the same, then FragPipe exits with that step's code
#   "Cancelling 4 remaining tasks"                     ProcessManager, when a step failed or could not start
#   "=====...ALL JOBS DONE IN 12.3 MINUTES=====..."    FragpipeRun's finalizer: printed only when every step ran
#   "It's a dry-run, not running the commands."        FragpipeRun, with --dry-run
#   "2026-10-01 14:03:11,532 ERROR - <message>"        logback: a check failed before any step (exit code 1)
_FAILED_STEP = re.compile(r"Process '([^']+)' finished, exit code: (-?[1-9]\d*)")
_CANCELLED_TASKS = re.compile(r"^Cancelling (\d+) remaining tasks", re.MULTILINE)
_ALL_DONE = re.compile(r"ALL JOBS DONE IN ([\d.,]+) MINUTES")
_DRY_RUN = re.compile(r"It's a dry-run, not running the commands")

_RAW_ERR = r"\b(error|exception|fail(ed|ure|s)?|unable|cannot|could not|corrupt(ed)?)\b"

# (regex on FragPipe's console output, plain-English explanation + what to do). First match first.
# A regex must not match what a healthy run prints: FragPipe echoes every workflow key (the "fragpipe.config"
# block), every command line with its tool paths, and each tool's banner. tests/test_fragpipe_real.py runs all
# of them against such a log.
EXPLANATIONS: list[tuple[str, str]] = [
    (r"raw file\(s\) are empty",
     "A raw file is 0 bytes: the acquisition was aborted or the copy was interrupted — copy it again from the "
     "instrument PC (or remove it from the experiment folder), then Retry"),
    (r"raw file\(s\) missing from",
     "Raw files were moved or deleted from the experiment folder after it was filed — put them back, then Retry"),
    (r"JAVA_HOME is not set and no 'java' command could be found|JAVA_HOME is set to an invalid directory",
     "FragPipe's launcher found no Java. Ionomos points it at FragPipe's own (the jre folder beside bin): that "
     "folder is missing, so reinstall FragPipe (or install a 64-bit Java 17), then Retry"),
    (r"Cannot recognize the argument --|you did not add --headless flag",
     "This FragPipe doesn't know an option Ionomos passed: it is older than 23, or the launcher is not "
     "FragPipe's own fragpipe.bat — tab 1 -> Find FragPipe, then Retry"),
    (r"FASTA file path is empty|No FASTA file|Could not find fasta file at",
     "No protein database: set this method's FASTA in the app (tab 3), or re-export the workflow after setting it"),
    (r"No decoys found in the FASTA file|All FASTA entries seem to be decoys|FASTA file contains [\d.,]+% decoys|"
     r"Decoy protein prefix is empty",
     "The FASTA's decoys are not what FragPipe needs (about half the entries, each starting with the decoy "
     "tag, usually rev_): add decoys once in FragPipe's Database tab, put that file in the FASTA folder and "
     "pick it for the method (tab 3)"),
    (r"path contains whitespace characters|There are spaces in the path",
     "A path has a space in it, which FragPipe can't use: move FragPipe, its tools folder or the data folders "
     "to a path without spaces (tab 1), then Retry"),
    (r"No license file found\.|The license .{0,300}(has expired|is corrupted|is not valid for this product)",
     "MSFragger / IonQuant / diaTracer licence problem: open the FragPipe GUI, Config tab -> Download / Update "
     "(accept the licences); a commercial licence file (license.dat) that expired must be renewed"),
    (r"requires native (Thermo|Bruker) libraries",
     "MSFragger's 'ext' folder (the Thermo / Bruker reader libraries) is not next to its jar: FragPipe GUI -> "
     "Config tab -> Download / Update MSFragger again, then Retry"),
    (r"You must install or update \.NET to run this application|"
     r"It was not possible to find any compatible framework version|"
     r"A fatal error was encountered\. The library 'hostfxr\.dll'",
     "A FragPipe tool needs a .NET runtime this PC doesn't have: the lines after the message name the version "
     "— install that '.NET Runtime' (x64) from Microsoft, then Retry"),
    (r"Not enough memory allocated to MSFragger",
     "MSFragger didn't get enough memory for this search: raise RAM (GB) in Advanced if the PC has more, or "
     "set a database split in the workflow's MSFragger tab (FragPipe GUI), save it and import it again"),
    (r"There is insufficient memory for the Java Runtime Environment|paging file is too small|"
     r"errno=1455|WinError 1455",
     "Windows ran out of memory (RAM plus page file) during the search: close other programs, lower RAM (GB) "
     "and Threads in Advanced so the tools leave room, then Retry"),
    (r"OutOfMemoryError|Java heap space|GC overhead limit",
     "FragPipe ran out of memory: lower Threads or raise RAM (GB) in Advanced, and close other programs"),
    (r"not enough space on the disk|No space left on device|disk is full",
     "The disk is full: free space on the drive holding the experiment folders, then Retry"),
    (r"\berror=206\b|WinError 206|The filename or extension is too long|must be less than 260 characters",
     "A path or command line got longer than Windows allows: shorten the experiment folder and raw file names "
     "(or search fewer files at once), then Retry"),
    (r"(?i)msfragger.{0,80}(not found|could not find|missing|download|not configured)|"
     r"(download|install).{0,40}msfragger|MSFragger is not valid but it is enabled|MSFragger path is null|"
     r"Not a MSFragger jar",
     "MSFragger isn't installed for FragPipe: open the FragPipe GUI once, Config tab -> Download/Update "
     "MSFragger (accept the licence), then Retry. If the GUI has it, set Tools folder in Advanced"),
    (r"(?i)ionquant.{0,80}(not found|could not find|missing|download)|IonQuant is not valid but it is enabled|"
     r"IonQuant path is null|Not an IonQuant jar",
     "IonQuant isn't installed for FragPipe: FragPipe GUI -> Config tab -> Download/Update IonQuant, then Retry"),
    (r"(?i)diatracer.{0,80}(not found|could not find|missing|download)|diaTracer is not valid but it is enabled|"
     r"diaTracer path is null",
     "diaTracer isn't installed for FragPipe: FragPipe GUI -> Config tab -> Download/Update diaTracer"),
    (r"Version string not found for DIA-NN|Does not appear to be an executable file|"
     r"DIA-NN executable file path .{0,300} does not seem right",
     "FragPipe can't start DIA-NN: 'DIA-NN exe' in Advanced must be the file DiaNN.exe itself (not DIA-NN.exe "
     "or the installer), and DIA-NN needs the Visual C++ Redistributable (VC_redist.x64.exe in its folder)"),
    (r"(?i)dia-?nn.{0,80}(not found|could not find|missing|not executable|no such file)",
     "FragPipe can't find DIA-NN: set 'DIA-NN exe' in Advanced (e.g. C:/DIA-NN/2.3.2/DiaNN.exe)"),
    (r"Process 'DIA-Quant[^']*' finished, exit code: -?[1-9]",
     "DIA-NN (FragPipe's DIA quantification step) failed: its own message is just above that line in the log. "
     "FragPipe's DIA workflow notes ask for Thermo DIA files as mzML; .raw needs a DIA-NN that reads it"),
    (r"Spectral Library Generation (module was not configured|scripts did not initialize) correctly|"
     r"DbSplit was enabled, but Python was not configured|Python path .{0,300} does not seem right|"
     r"ModuleNotFoundError: No module named|ImportError: DLL load failed",
     "FragPipe's Python (for the spectral library / database split) isn't set up. FragPipe 24 uses the python "
     "folder inside its installation, and its installer puts the packages there: reinstall FragPipe with the "
     "installer (a copied folder lacks them), or FragPipe GUI -> Config tab -> Python: finish the install; "
     "then Retry"),
    (r"Number of the samples in the annotation file does not match|Duplicate samples found in annotation files|"
     r"Found annotation files without reference channel|Empty sample name found in annotation file|"
     r"Invalid line in annotation file|Annotation file not found or not readable",
     "The TMT annotation (channel -> sample) doesn't fit the workflow: list every channel of the label type "
     "once, one sample name without spaces per channel (NA for unused), names unique across plexes, and the "
     "reference channel named as the workflow expects — fix tmt: in experiment.yaml, then Retry"),
    (r"Some input LCMS files have the same name|multiple experimental groups, but one of them has empty name|"
     r"Manifest file contained some badly formatted lines",
     "FragPipe refused the file list: two raw files have the same name, or a name FragPipe can't use — rename "
     "the files so every one is unique, then Retry"),
    (r"(Percolator|PeptideProphet) was enabled but MSFragger's output formats|"
     r"Both FreeQuant and TMT-Integrator were enabled|cannot be run in the same workflow",
     "The workflow's settings contradict each other (FragPipe's message says which): fix them in the FragPipe "
     "GUI, save the workflow and import it again (tab 3)"),
    (r"Cannot deploy asset|Could not load file .{0,300}The file may be corrupted|"
     r"Did not get port information from server output",
     "A FragPipe tool could not write or start one of its own files: usually antivirus or a locked temp "
     "folder. Retry once; if it repeats, ask IT to exclude FragPipe and the experiment drive from real-time "
     "scanning"),
    (r"used by another process|cannot access the file|java\.nio\.file\.FileSystemException",
     "A file was open in another program (Xcalibur, Excel, Explorer preview) or locked by a sync / antivirus "
     "tool such as OneDrive: close it, keep experiments out of synced folders, then Retry"),
    # The reader's name alone is no error: FragPipe logs Thermo's "RawFileReader reading tool. Copyright ..." banner
    # on every .raw search and the converter's path holds its name, so the name needs an error word on its line
    # (or sage.py's own "converting X failed" line).
    (r"(?i)(converting .{0,200}failed \(ThermoRawFileParser|"
     r"(?<![\w./\\])(RawFileReader|ThermoRawFileParser)(?![\w/\\]|\.\w).{0,80}" + _RAW_ERR + r"|"
     + _RAW_ERR + r".{0,80}(?<![\w./\\])(RawFileReader|ThermoRawFileParser)(?![\w/\\]|\.\w)|"
     r"error (loading|reading).{0,60}\.raw|\.raw.{0,60}(corrupt|truncated))",
     "A .raw file couldn't be read: it may be incomplete or corrupt — re-copy it from the instrument PC"),
    (r"UnsupportedClassVersionError|Unsupported class file major version|requires Java 9\+",
     "Wrong Java version: FragPipe must use its bundled Java — reinstall FragPipe or set its launcher again"),
    (r"Could not find or load main class|Unable to access jarfile",
     "The FragPipe installation looks broken: re-select fragpipe.bat (tab 1, Find FragPipe) or reinstall FragPipe"),
    (r"is not recognized as an internal or external command",
     "The FragPipe launcher path is wrong: tab 1 -> Find FragPipe"),
    (r"Access is denied|Permission denied",
     "Windows refused access to a file or folder: check the experiment folder isn't read-only / open elsewhere"),
    (r"(?i)output directory.{0,40}not empty|workdir.{0,40}not empty",
     "FragPipe wants an empty output folder: Retry (ionomos moves the old output aside)"),
    (r"There were errors creating indexes",
     "IonQuant could not read FragPipe's tables: often a character in the FASTA that isn't an amino acid "
     "letter — check the FASTA (re-download it), then Retry"),
    (r"(?i)philosopher.{0,120}(error|fatal)|level=(error|fatal) msg=",
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

# A step's start line is its name, then (for most steps) its folder: "MSFragger [Work dir: C:\x]",
# "Quant (Isobaric) [Work dir: ...]", "Percolator: Convert to pepxml [Work dir: ...]". FragPipe also lists every
# step this way once before running anything ("18 commands to execute:"), so a start only counts after the
# line that closes that list.
_TASK = re.compile(r"^([A-Za-z][\w .:()+\-]{1,60}?) \[Work dir: ", re.MULTILINE)
_DONE_TASK = re.compile(r"Process '([^']+)' finished, exit code: (-?\d+)")
_N_COMMANDS = re.compile(r"^(\d+) commands to execute:", re.MULTILINE)
_LIST_END = re.compile(r"^Execution order:|^~{9} fragpipe\.config ~{9}", re.MULTILINE)
KNOWN_STEPS = ("MSFragger", "MSBooster", "Percolator", "PeptideProphet", "ProteinProphet", "PTMProphet",
               "Philosopher", "FreeQuant", "IonQuant", "TMT-Integrator", "TMTIntegrator", "TmtIntegrator",
               "diaTracer", "DIA-NN", "DIA-Quant", "EasyPQP", "SpecLibGen", "Spectral library", "Crystal-C",
               "PTM-Shepherd", "PTMShepherd", "Report")


def console_facts(text: str) -> dict:
    """What Ionomos reads out of one attempt's FragPipe console text (see the list of lines above)."""
    listed = _N_COMMANDS.search(text)
    body = text
    if listed:  # skip the list of commands: it names every step before any has started
        ends = [m.end() for m in _LIST_END.finditer(text, listed.end())]
        body = text[ends[-1]:] if ends else ""  # no end yet = still printing the list
    done = _ALL_DONE.search(text)
    cancelled = _CANCELLED_TASKS.search(text)
    return {
        "commands": int(listed.group(1)) if listed else None,
        "started": [s.strip() for s in _TASK.findall(body)],
        "finished": [(name, int(code)) for name, code in _DONE_TASK.findall(text)],
        "all_jobs_done_minutes": done.group(1) if done else None,
        "cancelled_tasks": int(cancelled.group(1)) if cancelled else None,
        "dry_run": bool(_DRY_RUN.search(text)),
        "error_lines": re.findall(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ ERROR +- (.*)$", text, re.MULTILINE)[:20],
    }


def progress(console_log: Path) -> str:
    """Best-effort 'current step (n finished)' from FragPipe's console output."""
    text = attempt_text(read_tail_text(console_log, 200_000))
    if not any(ln.strip() and not ln.startswith("# ") for ln in text.splitlines()):  # only Ionomos' own header
        return "starting"
    facts = console_facts(text)
    n = len(facts["finished"])
    of = f" of {facts['commands']}" if facts["commands"] and n <= facts["commands"] else ""
    if facts["started"]:
        return f"{facts['started'][-1]} ({n}{of} step(s) done)"
    if facts["commands"]:
        return f"starting ({n}{of} step(s) done)"
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
    ("quantitation.run-label-free-quant", "MS1 quant"),
    ("ionquant.mbr", "match-between-runs"),
    ("tmtintegrator.run-tmtintegrator", "TMT-Integrator"),
    ("tmtintegrator.channel_num", "label type"),
    ("diann.run-dia-nn", "DIA-NN"),
    ("diatracer.run-diatracer", "diaTracer"),
    ("speclibgen.run-speclibgen", "spectral library"),
    ("workflow.saved-with-ver", "saved with FragPipe"),
)


def workflow_needs(props: dict[str, str]) -> dict[str, bool]:
    """Which tools a workflow will start, from its run-* keys (as in FragPipe 24.0's stock workflows).

    IonQuant runs for MS1 quantification (quantitation.run-label-free-quant; isoDTB, label-free) and as
    TMT-Integrator's intensity extraction; ionquant.run-ionquant alone is true in workflows that never run it."""
    def on(key: str) -> bool:
        return props.get(key, "").strip().lower() == "true"

    try:
        split = int(props.get("msfragger.misc.slice-db", "1") or 1)
    except ValueError:
        split = 1
    tmt = on("tmtintegrator.run-tmtintegrator")
    return {
        "MSFragger": on("msfragger.run-msfragger"),
        "IonQuant": (on("ionquant.run-ionquant") and on("quantitation.run-label-free-quant"))
                    or (tmt and props.get("tmtintegrator.extraction_tool", "").strip().lower() == "ionquant"),
        "diaTracer": on("diatracer.run-diatracer"),
        "DIA-NN": on("diann.run-dia-nn"),
        "Python": on("speclibgen.run-speclibgen") or split > 1,
    }


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
    from ionomos import diann, maxquant, sage

    if diann.uses_diann(cfg, key):
        return diann.describe(cfg, key)
    if maxquant.uses_maxquant(cfg, key):
        return maxquant.describe(cfg, key)
    if sage.uses_sage(cfg, key):
        return sage.describe(cfg, key)
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
    try:
        refused = decoy_problem(read_properties(wf.read_text(encoding="utf-8", errors="replace")), fa)
    except OSError:
        refused = ""
    if fi.get("error"):
        out.append((False, f"FASTA {fa}: {fi['error']}"))
    elif refused:  # headless FragPipe stops on this (prepare holds the job with the same words)
        out.append((False, f"FASTA ({source}): {refused}"))
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
    if " " in str(exe):
        rows.append((False, "launcher path", "has a space in it; FragPipe can't run from such a folder — install "
                                             "it somewhere like C:/FragPipe"))
    root = fragpipe_root(exe)
    if root is None or not root.is_dir():
        rows.append((None, "install folder", "launcher is not in a standard fragpipe/bin folder; skipping deeper checks"))
        return rows
    ver = re.search(r"FragPipe-([\d.]+)", str(root))
    jars = sorted(root.glob("lib/fragpipe*.jar"))
    rows.append((bool(jars) or None, "FragPipe", (f"version {ver.group(1)}" if ver else "version ?") +
                 (f", {jars[-1].name}" if jars else ", lib/fragpipe*.jar not found")))
    home = bundled_java_home(exe)
    java = [j for j in ((home / "bin" / "java.exe", home / "bin" / "java") if home else ()) if j.is_file()]
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

    fragger = None
    for label, pattern, needed_for in (("MSFragger", "MSFragger*.jar", "every search"),
                                       ("IonQuant", "IonQuant*.jar", "isoDTB / label-free / TMT quant"),
                                       ("diaTracer", "diaTracer*.jar", "some DIA workflows")):
        hit = find(pattern)
        fragger = hit if label == "MSFragger" else fragger
        if hit and " " in str(hit):
            rows.append((False, label, f"{hit} has a space in its path, which FragPipe refuses — move the tools "
                                       f"folder"))
            continue
        rows.append((True if hit else None, label,
                     hit.name if hit else f"not found — needed for {needed_for}. FragPipe GUI -> Config tab -> "
                                          f"Download/Update {label} (licence)"))
    if fragger is not None:
        # the Thermo / Bruker readers MSFragger and IonQuant load come in the MSFragger download's ext folder
        ext = fragger.parent / "ext" / "thermo"
        rows.append((True if ext.is_dir() else None, "Thermo .raw reader",
                     str(ext) if ext.is_dir() else f"{ext} not found — .raw files can't be read without it. "
                                                   f"FragPipe GUI -> Config tab -> Download/Update MSFragger"))
    phil = sorted((root / "tools" / "Philosopher").glob("philosopher-v*")) if (root / "tools").is_dir() else []
    rows.append((True if phil else None, "Philosopher", phil[-1].name if phil else
                 f"no philosopher-v* in {root / 'tools' / 'Philosopher'} — reinstall FragPipe"))
    diann = Path(cfg.config_diann) if cfg.config_diann else find("DiaNN.exe") or find("diann*")
    rows.append((True if diann and Path(diann).exists() else None, "DIA-NN",
                 str(diann) if diann and Path(diann).exists() else "not found (only needed for DIA)"))
    py_dirs = [Path(cfg.config_python)] if cfg.config_python else [root / "python"]
    py = next((p for d in py_dirs for p in (d / "python.exe", d / "bin" / "python3", d / "python") if p.is_file()),
              None)
    rows.append((True if py else None, "Python for FragPipe",
                 str(py) if py else f"not found in {py_dirs[0]} (only needed for a spectral library, as in "
                                    f"FragPipe's DIA workflows, or a split database)"))
    # FragPipe keeps what its window was last set to in <install>/cache; a headless run reads the tools folder,
    # DIA-NN and Python from there unless they come on the command line (its headless tutorial: the first run
    # must name them)
    seen_gui = (root / "cache" / "fragpipe-ui.cache").is_file()
    rows.append((True if seen_gui or cfg.config_tools_folder else None, "FragPipe settings",
                 f"{root / 'cache'} (what FragPipe's window was last set to)" if seen_gui else
                 "Tools folder is set in Advanced" if cfg.config_tools_folder else
                 f"no {root / 'cache' / 'fragpipe-ui.cache'}: FragPipe's window was never used with this "
                 f"installation, so a search doesn't know where MSFragger is — open FragPipe once (Config tab), "
                 f"or set Tools folder in Advanced to {root / 'tools'}"))
    licence = next((p for d in (root, root.parent) if d.is_dir() for p in sorted(d.iterdir())
                    if p.is_file() and p.name.lower().startswith("license") and p.name.lower().endswith(".dat")), None)
    if licence is not None:
        rows.append((True, "licence file", f"{licence} (commercial licence; FragPipe passes it to its tools)"))
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
