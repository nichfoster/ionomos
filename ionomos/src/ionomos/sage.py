"""
Sage run directly by the watcher, for a DDA method with `engine: sage` (D51, ROADMAP 5B).

    methods:
      LFQ:
        engine: sage
        sage_exe: C:/sage/sage.exe                 # the lab's own download (github.com/lazear/sage, MIT)
        raw_converter: C:/ThermoRawFileParser/ThermoRawFileParser.exe   # .raw -> mzML; not needed for mzML drops
        fasta: human_reviewed.fasta                # in fasta_dir
        data_type: DDA
        sage_config: lab_sage.json                 # optional: the lab's own Sage parameters, in workflow_dir
        sage_args: "--batch-size 2"                # optional, added to the sage command line

Sage reads mzML, not Thermo .raw, so a job has two steps: ThermoRawFileParser converts each .raw to
<experiment>/sage_mzml/<name>.mzML (kept, so a retry doesn't convert again), then Sage searches them with
ionomos_run/sage.json, the job's reproducible settings. The settings are the lab's own Sage JSON, or
Ionomos' defaults for high-resolution DDA with label-free quantification; only the FASTA, the mzML paths
and the output folder are replaced. Both steps run inside one process, `ionomos sage-job
ionomos_run/sage_job.json`, so the shared run loop (fragpipe.run: log, cancel, stop, timeout, kill the
process tree) covers them as it covers FragPipe. Output goes to <experiment>/sage/; the analysis rolls
lfq.tsv up to proteins (downstream/engines.py).

Sage sends anonymous usage telemetry by default (version, sizes, run time, OS). Ionomos switches it off
whenever the installed Sage has the flag for it: nothing about a lab's runs should leave the PC unasked.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ionomos.config import Config
from ionomos.fragpipe import Hold, JobError, RunSpec, _find_file, check_raws
from ionomos.ledger import Job

WORKDIR = "sage"
MZML_DIR = "sage_mzml"       # converted raw files; Ionomos' own, safe to delete once the search is done
CONFIG_NAME = "sage.json"
JOB_NAME = "sage_job.json"
CONSOLE_LOG = "sage_console.log"
NO_TELEMETRY = "--disable-telemetry-i-dont-want-to-improve-sage"
EXE_CANDIDATES = ("C:/sage*/sage.exe", "C:/sage*/*/sage.exe", "C:/Program Files/sage*/sage.exe")
CONVERTER_CANDIDATES = ("C:/ThermoRawFileParser*/ThermoRawFileParser.exe",
                        "C:/ThermoRawFileParser*/*/ThermoRawFileParser.exe",
                        "C:/Program Files/ThermoRawFileParser*/ThermoRawFileParser.exe")
# read directly by Sage (Bruker .d from 0.14 on)
DIRECT = (".mzml", ".mzml.gz", ".d")

# Tryptic, high-resolution MS1 and MS2 (Orbitrap HCD), carbamidomethyl C, oxidised M, label-free
# quantification with match-between-runs style feature mapping. A lab with other needs gives sage_config.
DEFAULT_CONFIG: dict = {
    "database": {
        "bucket_size": 32768,
        "enzyme": {"missed_cleavages": 2, "min_len": 7, "max_len": 50, "cleave_at": "KR", "restrict": "P"},
        "peptide_min_mass": 500.0,
        "peptide_max_mass": 5000.0,
        "ion_kinds": ["b", "y"],
        "min_ion_index": 2,
        "static_mods": {"C": 57.021464},
        "variable_mods": {"M": [15.994915]},
        "max_variable_mods": 2,
        "decoy_tag": "rev_",
        "generate_decoys": True,   # decoys already in the FASTA (rev_) are ignored and made again by Sage
    },
    "quant": {
        "lfq": True,
        "lfq_settings": {"peak_scoring": "Hybrid", "integration": "Sum", "spectral_angle": 0.7,
                         "ppm_tolerance": 5.0, "combine_charge_states": True},
    },
    "precursor_tol": {"ppm": [-20, 20]},
    "fragment_tol": {"ppm": [-20, 20]},
    "precursor_charge": [2, 4],
    "isotope_errors": [0, 2],
    "deisotope": True,
    "chimera": False,
    "wide_window": False,
    "predict_rt": True,
    "min_peaks": 15,
    "max_peaks": 150,
    "min_matched_peaks": 4,
    "report_psms": 1,
}


def uses_sage(cfg: Config, method: str) -> bool:
    m = cfg.methods.get(method)
    return bool(m and str(m.extra.get("engine", "fragpipe")).lower() == "sage")


def _find(configured: str, candidates: tuple[str, ...]) -> Path | None:
    import glob

    if configured:
        p = Path(configured)
        return p if p.is_file() else None
    hits = [Path(x) for pat in candidates for x in glob.glob(pat)]
    return sorted(hits, key=str)[-1] if hits else None


def find_exe(configured: str = "") -> Path | None:
    """The configured sage executable, else one in the usual Windows folders. Never `sage` from the PATH:
    on many machines that is SageMath."""
    return _find(configured, EXE_CANDIDATES)


def find_converter(configured: str = "") -> Path | None:
    return _find(configured, CONVERTER_CANDIDATES)


def _launch(exe: Path) -> list[str]:
    return ["dotnet", str(exe)] if exe.suffix.lower() == ".dll" else [str(exe)]


def _is_direct(path: str) -> bool:
    return path.lower().endswith(DIRECT)


def _args(v) -> list[str]:
    if not v:
        return []
    return v.split() if isinstance(v, str) else [str(x) for x in v]


def load_lab_config(path: Path) -> dict:
    """The lab's Sage JSON, checked for what Ionomos can analyse. Raises Hold with what to fix."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise Hold(f"Sage settings {path.name} can't be read as JSON ({exc}); fix the file in {path.parent}") from exc
    if not isinstance(data, dict):
        raise Hold(f"Sage settings {path.name} can't be read as JSON (it is not an object); fix the file in "
                   f"{path.parent}")
    if (data.get("quant") or {}).get("tmt"):
        raise Hold(f"Sage settings {path.name} ask for TMT quantification, which Ionomos can't analyse from Sage "
                   f"yet; remove quant.tmt (label-free), or search TMT with FragPipe")
    return data


@dataclass
class SageSpec(RunSpec):
    converter: Path | None = None
    lab_config: dict | None = None      # the lab's sage_config, parsed; None -> DEFAULT_CONFIG
    args: list[str] = field(default_factory=list)
    engine_name = "Sage"

    @property
    def workdir(self) -> Path:
        return self.dest / WORKDIR

    @property
    def mzml_dir(self) -> Path:
        return self.dest / MZML_DIR

    @property
    def workflow(self) -> Path:  # the job's settings: what `workflow` is for a FragPipe job
        return self.run_dir / CONFIG_NAME

    @property
    def manifest(self) -> Path:  # the two steps and their files
        return self.run_dir / JOB_NAME

    @property
    def console_log(self) -> Path:
        return self.run_dir / CONSOLE_LOG

    def expected_outputs(self) -> tuple[str, ...]:
        return ("lfq.tsv",)

    def command(self) -> list[str]:
        from ionomos.service import ionomos_command

        return ionomos_command(console=True) + ["sage-job", str(self.manifest)]

    def inputs(self) -> list[tuple[str, str | None]]:
        """(file Sage reads, the .raw it is converted from or None), in manifest order."""
        out = []
        for raw, *_ in self.manifest_lines:
            if _is_direct(raw):
                out.append((raw, None))
            else:
                out.append((str(self.mzml_dir / (Path(raw).stem + ".mzML")), raw))
        return out

    def config(self) -> dict:
        """sage.json: the lab's settings or the defaults, with this job's FASTA, files and output folder."""
        cfg = json.loads(json.dumps(self.lab_config if self.lab_config is not None else DEFAULT_CONFIG))
        cfg.setdefault("database", {})["fasta"] = str(self.fasta)
        quant = cfg.setdefault("quant", {})
        quant["lfq"] = True   # the analysis reads lfq.tsv
        cfg["mzml_paths"] = [p for p, _raw in self.inputs()]
        cfg["output_directory"] = str(self.workdir)
        return cfg

    def job(self) -> dict:
        return {"sage": [str(self.exe)], "config": str(self.workflow), "args": list(self.args),
                "threads": self.threads, "converter": _launch(self.converter) if self.converter else [],
                "convert": [{"raw": raw, "mzml": mzml} for mzml, raw in self.inputs() if raw]}


def prepare(job: Job, cfg: Config) -> SageSpec:
    """Everything needed to run `job` with Sage, checked. Raises Hold (setup missing) or JobError."""
    plan = (job.parsed or {}).get("plan") or {}
    overrides = plan.get("overrides") or {}
    mcfg = cfg.methods.get(job.method)
    if mcfg is None:
        raise Hold(f"method {job.method!r} is not in config.yaml any more")
    exe = find_exe(str(mcfg.extra.get("sage_exe") or ""))
    if exe is None:
        where = mcfg.extra.get("sage_exe")
        raise Hold(f"Sage not found{f' at {where}' if where else ''}: download it (github.com/lazear/sage) and set "
                   f"methods.{job.method}.sage_exe in config.yaml")
    dest = Path(job.dest_dir)
    if not dest.is_dir():
        raise JobError(f"experiment folder is gone: {dest}")
    fasta_name = overrides.get("fasta") or mcfg.fasta
    fasta = _find_file(fasta_name, cfg.fasta_dir) if fasta_name else None
    if fasta is None:
        if overrides.get("fasta"):
            raise JobError(f"experiment.yaml asks for FASTA {fasta_name!r}, which is not in {cfg.fasta_dir}")
        raise Hold(f"FASTA for {job.method} missing: {cfg.fasta_dir / (fasta_name or '?')}")
    lab, template = None, None
    if mcfg.extra.get("sage_config"):
        template = _find_file(str(mcfg.extra["sage_config"]), cfg.workflow_dir, ".json")
        if template is None:
            raise Hold(f"Sage settings {mcfg.extra['sage_config']!r} for {job.method} not in {cfg.workflow_dir}")
        lab = load_lab_config(template)
    lines = check_raws(dest, plan, cfg)
    converter = None
    if any(not _is_direct(raw) for raw, *_ in lines):
        converter = find_converter(str(mcfg.extra.get("raw_converter") or ""))
        if converter is None:
            where = mcfg.extra.get("raw_converter")
            raise Hold(f"raw file converter not found{f' at {where}' if where else ''}: Sage reads mzML, so install "
                       f"ThermoRawFileParser and set methods.{job.method}.raw_converter in config.yaml")
    stems: dict[str, str] = {}
    for raw, *_ in lines:  # two folders holding the same file name would convert onto one mzML
        key = Path(raw).stem.lower()
        if key in stems and stems[key] != raw:
            raise JobError(f"two raw files share the name {Path(raw).stem}: {stems[key]} and {raw}")
        stems[key] = raw
    spec = SageSpec(job_id=job.id or 0, method=job.method, dest=dest, exe=exe,
                    workflow_src=template or Path(CONFIG_NAME), fasta=fasta, manifest_lines=lines,
                    threads=cfg.threads, ram_gb=0, timeout_minutes=cfg.timeout_minutes,
                    raw_dir=dest / plan["raw_dir"] if plan.get("raw_dir") else dest, converter=converter,
                    lab_config=lab, args=_args(mcfg.extra.get("sage_args")))
    if lab is not None and (lab.get("quant") or {}).get("lfq") is False:
        spec.warnings.append(f"{template.name} has label-free quantification off; switched on for this job "
                             "(the analysis reads lfq.tsv)")
    return spec


def write_inputs(spec: SageSpec) -> str | None:
    """ionomos_run/sage.json + sage_job.json and an empty sage/ folder; an old sage/ is kept, renamed, never
    deleted. sage_mzml/ is left as it is: mzML files already converted are reused."""
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    moved = None
    if spec.workdir.exists() and any(spec.workdir.iterdir()):
        moved = f"{WORKDIR}_previous_{datetime.now():%Y%m%d-%H%M%S}"
        os.replace(spec.workdir, spec.dest / moved)
    spec.workdir.mkdir(exist_ok=True)
    spec.workflow.write_text(json.dumps(spec.config(), indent=2) + "\n", encoding="utf-8", newline="\n")
    spec.manifest.write_text(json.dumps(spec.job(), indent=2) + "\n", encoding="utf-8", newline="\n")
    return moved


def describe(cfg: Config, key: str) -> list[tuple[bool | None, str]]:
    """Setup checklist lines for a Sage method."""
    m = cfg.methods[key]
    exe = find_exe(str(m.extra.get("sage_exe") or ""))
    out: list[tuple[bool | None, str]] = [(exe is not None, f"Sage: {exe}" if exe else "Sage not found (set sage_exe)")]
    conv = find_converter(str(m.extra.get("raw_converter") or ""))
    out.append((True, f"raw converter: {conv}") if conv else
               (None, "no raw converter (set raw_converter to ThermoRawFileParser): only mzML drops can be searched"))
    fasta = _find_file(m.fasta, cfg.fasta_dir) if m.fasta else None
    out.append((fasta is not None, f"FASTA {m.fasta}" + ("" if fasta else f" missing from {cfg.fasta_dir}")))
    if m.extra.get("sage_config"):
        found = _find_file(str(m.extra["sage_config"]), cfg.workflow_dir, ".json")
        ok, text = found is not None, f"Sage settings {m.extra['sage_config']}" + ("" if found else " missing")
        if found:
            try:
                load_lab_config(found)
            except Hold as exc:
                ok, text = False, str(exc)
        out.append((ok, text))
    else:
        out.append((None, "no lab Sage settings: Ionomos' defaults (tryptic, high-resolution MS2, label-free); "
                          "give sage_config for your own"))
    return out


# ------------------------------------------------------------- the job itself --


def _say(*parts) -> None:
    print("ionomos sage-job:", *parts, flush=True)


def _fresh(mzml: Path, raw: Path) -> bool:
    try:
        return mzml.stat().st_size > 0 and mzml.stat().st_mtime >= raw.stat().st_mtime
    except OSError:
        return False


def convert(converter: list[str], raw: Path, mzml: Path) -> int:
    """One .raw -> indexed mzML with ThermoRawFileParser. It is written to sage_mzml/converting/ and moved
    into place when complete, so a conversion that was cancelled half-way is never taken for a finished one."""
    tmp_dir = mzml.parent / "converting"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cmd = converter + [f"-i={raw}", f"-o={tmp_dir}", "-f=2"]
    try:
        code = subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode
    except OSError as exc:
        _say(f"ERROR: could not start the raw converter ({converter[0]}): {exc}")
        return 1
    made = tmp_dir / mzml.name
    if code != 0 or not made.is_file() or made.stat().st_size == 0:
        _say(f"ERROR: converting {raw.name} failed (ThermoRawFileParser exit code {code}"
             f"{'' if made.is_file() else ', no mzML written'})")
        return code or 1
    os.replace(made, mzml)
    return 0


def telemetry_flag(sage: list[str]) -> list[str]:
    """[the flag that switches Sage's telemetry off] when this Sage knows it (asked with --help), else []."""
    try:
        r = subprocess.run(sage + ["--help"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [NO_TELEMETRY] if NO_TELEMETRY in (r.stdout + r.stderr) else []


def run_job(argv: list[str]) -> int:
    """`ionomos sage-job ionomos_run/sage_job.json`: convert what isn't converted yet, then run Sage. The exit
    code is the first failing step's."""
    if len(argv) != 1 or not Path(argv[0]).is_file():
        _say("usage: ionomos sage-job <sage_job.json>")
        return 2
    job = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    todo = [(Path(c["raw"]), Path(c["mzml"])) for c in job.get("convert") or []]
    for k, (raw, mzml) in enumerate(todo, 1):
        if _fresh(mzml, raw):
            _say(f"[{k}/{len(todo)}] {mzml.name} already converted")
            continue
        _say(f"[{k}/{len(todo)}] converting {raw.name} -> {mzml.name}")
        code = convert(job["converter"], raw, mzml)
        if code != 0:
            return code
    sage = [str(x) for x in job["sage"]]
    quiet = telemetry_flag(sage)
    _say("Sage telemetry switched off" if quiet else
         "this Sage has no telemetry switch (versions before the flag sent none, or check its --help)")
    env = dict(os.environ)
    if job.get("threads"):
        env["RAYON_NUM_THREADS"] = str(job["threads"])
    cmd = sage + quiet + [str(a) for a in job.get("args") or []] + [str(job["config"])]
    _say("searching:", " ".join(cmd))
    sys.stdout.flush()
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, env=env).returncode
    except OSError as exc:
        _say(f"ERROR: could not start Sage ({sage[0]}): {exc}")
        return 1
