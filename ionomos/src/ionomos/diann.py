"""
DIA-NN run directly by the watcher, for a method with `engine: diann` (D37, ROADMAP 5B).

    methods:
      DIA:
        engine: diann
        diann_exe: C:/DIA-NN/2.2.0/diann.exe   # the lab's own DIA-NN (never shipped with Ionomos)
        fasta: human_reviewed.fasta           # in fasta_dir
        data_type: DIA
        library: human_lib.parquet            # optional spectral library (workflow_dir or fasta_dir);
                                              # without one DIA-NN predicts it from the FASTA
        diann_args: ["--var-mods", "1", "--var-mod", "UniMod:35,15.994915,M"]   # optional, appended

Everything a job needs is written to ionomos_run/diann.cfg, the job's reproducible "workflow",
and DIA-NN is started as `diann.exe --cfg ionomos_run/diann.cfg`. Output goes to <experiment>/diann/.
The job's report, analysis and pop-ups work as for FragPipe: the downstream analysis finds
report.pg_matrix.tsv, and engines.provenance reads DIA-NN's version from its log.

DIA-NN's licence is the lab's business: from 1.9 on it is not redistributable, and 2.x comes as an
Academia and an Enterprise edition. Ionomos only starts the executable it is pointed at.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ionomos.config import Config
from ionomos.fragpipe import Hold, JobError, RunSpec, _find_file, check_raws
from ionomos.ledger import Job

WORKDIR = "diann"
CFG_NAME = "diann.cfg"
CONSOLE_LOG = "diann_console.log"
# FASTA-search defaults close to DIA-NN's GUI defaults for a tryptic human search; diann_args add to them
DEFAULT_ARGS = ["--qvalue", "0.01", "--matrices", "--met-excision", "--cut", "K*,R*", "--missed-cleavages", "1",
                "--min-pep-len", "7", "--max-pep-len", "30", "--min-pr-charge", "1", "--max-pr-charge", "4",
                "--min-pr-mz", "300", "--max-pr-mz", "1800", "--unimod4", "--reanalyse", "--rt-profiling",
                "--verbose", "1"]
LIBRARY_FREE = ["--fasta-search", "--predictor", "--gen-spec-lib"]
EXE_CANDIDATES = ("C:/DIA-NN/*/diann.exe", "C:/DIA-NN/*/DiaNN.exe", "C:/Program Files/DIA-NN/*/diann.exe",
                  "C:/Program Files/DIA-NN/*/DiaNN.exe", "/usr/local/bin/diann", "/opt/diann/*/diann-linux")


def uses_diann(cfg: Config, method: str) -> bool:
    m = cfg.methods.get(method)
    return bool(m and str(m.extra.get("engine", "fragpipe")).lower() == "diann")


def find_exe(configured: str = "") -> Path | None:
    """The configured diann executable, else the newest one in the usual install folders."""
    import glob

    if configured:
        p = Path(configured)
        return p if p.is_file() else None
    hits = []
    for pat in EXE_CANDIDATES:
        hits += [Path(x) for x in glob.glob(pat)]
    return sorted(hits, key=lambda p: str(p))[-1] if hits else None


@dataclass
class DiannSpec(RunSpec):
    library: Path | None = None
    args: list[str] = field(default_factory=list)
    engine_name = "DIA-NN"

    @property
    def workdir(self) -> Path:
        return self.dest / WORKDIR

    @property
    def workflow(self) -> Path:  # the job's settings: what `workflow` is for a FragPipe job
        return self.run_dir / CFG_NAME

    @property
    def manifest(self) -> Path:
        return self.workflow

    @property
    def console_log(self) -> Path:
        return self.run_dir / CONSOLE_LOG

    def expected_outputs(self) -> tuple[str, ...]:
        return ("report.pg_matrix.tsv",)

    def command(self) -> list[str]:
        return [str(self.exe), "--cfg", str(self.workflow)]

    def cfg_lines(self) -> list[str]:
        """The options DIA-NN reads from diann.cfg (one per line)."""
        out = [f"--f {raw}" for raw, *_ in self.manifest_lines]
        if self.library is not None:
            out.append(f"--lib {self.library}")
        if self.fasta is not None:
            out.append(f"--fasta {self.fasta}")
        out += [f"--out {self.workdir / 'report.tsv'}", f"--temp {self.workdir / 'temp'}"]
        if self.threads:
            out.append(f"--threads {self.threads}")
        args = list(DEFAULT_ARGS) + ([] if self.library is not None else list(LIBRARY_FREE)) + list(self.args)
        # one option per line: '--flag value' pairs stay together
        line: list[str] = []
        for a in args:
            if a.startswith("--") and line:
                out.append(" ".join(line))
                line = []
            line.append(a)
        if line:
            out.append(" ".join(line))
        return out


def _args(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return v.split()
    return [str(x) for x in v]


def prepare(job: Job, cfg: Config) -> DiannSpec:
    """Everything needed to run `job` with DIA-NN, checked. Raises Hold (setup missing) or JobError."""
    rec = job.parsed or {}
    plan = rec.get("plan") or {}
    overrides = plan.get("overrides") or {}
    mcfg = cfg.methods.get(job.method)
    if mcfg is None:
        raise Hold(f"method {job.method!r} is not in config.yaml any more")
    exe = find_exe(str(mcfg.extra.get("diann_exe") or ""))
    if exe is None:
        where = mcfg.extra.get("diann_exe")
        raise Hold(f"DIA-NN not found{f' at {where}' if where else ''}: install it (your lab's own licence) and set "
                   f"methods.{job.method}.diann_exe in config.yaml")
    dest = Path(job.dest_dir)
    if not dest.is_dir():
        raise JobError(f"experiment folder is gone: {dest}")
    fasta_name = overrides.get("fasta") or mcfg.fasta
    fasta = _find_file(fasta_name, cfg.fasta_dir) if fasta_name else None
    if fasta is None:
        if overrides.get("fasta"):
            raise JobError(f"experiment.yaml asks for FASTA {fasta_name!r}, which is not in {cfg.fasta_dir}")
        raise Hold(f"FASTA for {job.method} missing: {cfg.fasta_dir / (fasta_name or '?')}")
    library = None
    lib_name = mcfg.extra.get("library")
    if lib_name:
        library = _find_file(str(lib_name), cfg.workflow_dir) or _find_file(str(lib_name), cfg.fasta_dir)
        if library is None:
            raise Hold(f"spectral library {lib_name!r} for {job.method} not in {cfg.workflow_dir} or {cfg.fasta_dir}")
    lines = check_raws(dest, plan, cfg)
    spaced = [p for p in [dest, fasta, library, *(Path(r) for r, *_ in lines)] if p is not None and " " in str(p)]
    if spaced:  # diann.cfg is split on spaces
        raise JobError(f"a path has a space in it, which DIA-NN's --cfg can't take: {spaced[0]}")
    return DiannSpec(job_id=job.id or 0, method=job.method, dest=dest, exe=exe, workflow_src=Path(CFG_NAME),
                     fasta=fasta, manifest_lines=lines, threads=cfg.threads, ram_gb=0,
                     timeout_minutes=cfg.timeout_minutes, raw_dir=dest / plan["raw_dir"] if plan.get("raw_dir") else dest,
                     library=library, args=_args(mcfg.extra.get("diann_args")))


def write_inputs(spec: DiannSpec) -> str | None:
    """ionomos_run/diann.cfg and an empty diann/ folder; an old diann/ is kept, renamed, never deleted."""
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    moved = None
    if spec.workdir.exists() and any(spec.workdir.iterdir()):
        moved = f"{WORKDIR}_previous_{datetime.now():%Y%m%d-%H%M%S}"
        os.replace(spec.workdir, spec.dest / moved)
    spec.workdir.mkdir(exist_ok=True)
    spec.workflow.write_text("\n".join(spec.cfg_lines()) + "\n", encoding="utf-8", newline="\n")
    return moved


def describe(cfg: Config, key: str) -> list[tuple[bool | None, str]]:
    """Setup checklist lines for a DIA-NN method (fragpipe.describe_method's counterpart)."""
    m = cfg.methods[key]
    exe = find_exe(str(m.extra.get("diann_exe") or ""))
    out: list[tuple[bool | None, str]] = [(exe is not None, f"DIA-NN: {exe}" if exe else
                                           "DIA-NN not found (set diann_exe)")]
    fasta = _find_file(m.fasta, cfg.fasta_dir) if m.fasta else None
    out.append((fasta is not None, f"FASTA {m.fasta}" + ("" if fasta else f" missing from {cfg.fasta_dir}")))
    lib = m.extra.get("library")
    if lib:
        found = _find_file(str(lib), cfg.workflow_dir) or _find_file(str(lib), cfg.fasta_dir)
        out.append((found is not None, f"library {lib}" + ("" if found else " missing")))
    else:
        out.append((None, "no spectral library: DIA-NN predicts one from the FASTA (slower)"))
    return out
