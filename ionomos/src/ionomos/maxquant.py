"""
MaxQuant run directly by the watcher, for a DDA method with `engine: maxquant` (D50, ROADMAP 5B).

    methods:
      LFQ:
        engine: maxquant
        maxquant_exe: C:/MaxQuant/2.6.7.0/bin/MaxQuantCmd.exe   # the lab's own MaxQuant (never shipped)
        fasta: human_reviewed.fasta                             # in fasta_dir
        data_type: DDA
        mqpar: lab_lfq_mqpar.xml   # optional: parameters saved from the MaxQuant GUI, in workflow_dir

mqpar.xml changes between MaxQuant versions, so Ionomos never writes one from scratch. The job starts
from the lab's own mqpar (their search settings, from a run that worked) or, without one, from the
template the installed MaxQuant makes itself (`MaxQuantCmd --create`, label-free quantification switched
on). Only the job-specific parts are replaced: raw files, experiment names (condition_replicate, so the
analysis reads LFQ intensity DMSO_1, ...), fractions, FASTA, threads and the output folder. The result is
ionomos_run/mqpar.xml, the job's reproducible settings, run as `MaxQuantCmd ionomos_run/mqpar.xml`
(`dotnet MaxQuantCmd.dll ...` when maxquant_exe is the .dll, as on Linux). Output goes to
<experiment>/maxquant/combined/txt/; MaxQuant also writes its per-raw working folders next to the raw
files, as it always does. The analysis then reads proteinGroups.txt (downstream/engines.py).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from ionomos.config import Config
from ionomos.fragpipe import Hold, JobError, RunSpec, _find_file, _popen_kwargs, check_raws
from ionomos.ledger import Job

WORKDIR = "maxquant"
MQPAR = "mqpar.xml"
DEFAULT_TEMPLATE = "mqpar_default.xml"
CONSOLE_LOG = "maxquant_console.log"
NO_FRACTION = "32767"  # MaxQuant's "not fractionated"
EXE_CANDIDATES = ("C:/MaxQuant/*/bin/MaxQuantCmd.exe", "C:/Program Files/MaxQuant/*/bin/MaxQuantCmd.exe",
                  "/opt/MaxQuant*/bin/MaxQuantCmd.dll")


def uses_maxquant(cfg: Config, method: str) -> bool:
    m = cfg.methods.get(method)
    return bool(m and str(m.extra.get("engine", "fragpipe")).lower() == "maxquant")


def find_exe(configured: str = "") -> Path | None:
    import glob

    if configured:
        p = Path(configured)
        return p if p.is_file() else None
    hits = [Path(x) for pat in EXE_CANDIDATES for x in glob.glob(pat)]
    return sorted(hits, key=str)[-1] if hits else None


def _launch(exe: Path) -> list[str]:
    return ["dotnet", str(exe)] if exe.suffix.lower() == ".dll" else [str(exe)]


@dataclass
class MaxQuantSpec(RunSpec):
    template: Path | None = None   # the lab's mqpar; None -> made by `--create` in write_inputs
    fractions: dict[str, str] = field(default_factory=dict)  # raw path -> MaxQuant fraction
    engine_name = "MaxQuant"

    @property
    def workdir(self) -> Path:
        return self.dest / WORKDIR

    @property
    def workflow(self) -> Path:
        return self.run_dir / MQPAR

    @property
    def manifest(self) -> Path:
        return self.workflow

    @property
    def console_log(self) -> Path:
        return self.run_dir / CONSOLE_LOG

    def expected_outputs(self) -> tuple[str, ...]:
        return ("combined/txt/proteinGroups.txt",)

    def command(self) -> list[str]:
        return _launch(self.exe) + [str(self.workflow)]


def prepare(job: Job, cfg: Config) -> MaxQuantSpec:
    """Everything needed to run `job` with MaxQuant, checked. Raises Hold (setup missing) or JobError."""
    plan = (job.parsed or {}).get("plan") or {}
    overrides = plan.get("overrides") or {}
    mcfg = cfg.methods.get(job.method)
    if mcfg is None:
        raise Hold(f"method {job.method!r} is not in config.yaml any more")
    exe = find_exe(str(mcfg.extra.get("maxquant_exe") or ""))
    if exe is None:
        where = mcfg.extra.get("maxquant_exe")
        raise Hold(f"MaxQuant not found{f' at {where}' if where else ''}: install it and set "
                   f"methods.{job.method}.maxquant_exe to its bin/MaxQuantCmd.exe")
    dest = Path(job.dest_dir)
    if not dest.is_dir():
        raise JobError(f"experiment folder is gone: {dest}")
    fasta_name = overrides.get("fasta") or mcfg.fasta
    fasta = _find_file(fasta_name, cfg.fasta_dir) if fasta_name else None
    if fasta is None:
        if overrides.get("fasta"):
            raise JobError(f"experiment.yaml asks for FASTA {fasta_name!r}, which is not in {cfg.fasta_dir}")
        raise Hold(f"FASTA for {job.method} missing: {cfg.fasta_dir / (fasta_name or '?')}")
    template = None
    if mcfg.extra.get("mqpar"):
        template = _find_file(str(mcfg.extra["mqpar"]), cfg.workflow_dir, ".xml")
        if template is None:
            raise Hold(f"mqpar {mcfg.extra['mqpar']!r} for {job.method} not in {cfg.workflow_dir} "
                       "(save the parameters from the MaxQuant GUI: File -> Save parameters)")
    lines = check_raws(dest, plan, cfg)
    return MaxQuantSpec(job_id=job.id or 0, method=job.method, dest=dest, exe=exe, workflow_src=template or Path(MQPAR),
                        fasta=fasta, manifest_lines=lines, threads=cfg.threads, ram_gb=0,
                        timeout_minutes=cfg.timeout_minutes,
                        raw_dir=dest / plan["raw_dir"] if plan.get("raw_dir") else dest, template=template,
                        fractions=fractions_of(lines, job.method, cfg))


def fractions_of(lines, method: str, cfg: Config) -> dict[str, str]:
    """raw path -> MaxQuant fraction. The job's manifest has no fraction column (FragPipe works fractions out
    from files sharing experiment + replicate), so each file's fraction is read from its name with the lab's
    naming rules; files of one replicate with no readable fraction are numbered in order; a single-shot
    sample gets MaxQuant's 32767 ("not fractionated")."""
    from ionomos.naming import NamingError, parse_raw_name

    groups: dict[tuple[str, int], list[str]] = {}
    for raw, exp, rep, _dt in lines:
        groups.setdefault((exp, rep), []).append(raw)
    out: dict[str, str] = {}
    for files in groups.values():
        read: dict[str, int | None] = {}
        for raw in files:
            try:
                read[raw] = parse_raw_name(Path(raw).name, method, getattr(cfg, "condition_codes", None),
                                           getattr(cfg, "file_rules", None)).fraction
            except NamingError:
                read[raw] = None
        if len(files) == 1 and read[files[0]] is None:
            out[files[0]] = NO_FRACTION
        elif all(v is not None for v in read.values()):
            out.update({raw: str(v) for raw, v in read.items()})
        else:
            out.update({raw: str(k) for k, raw in enumerate(files, 1)})
    return out


def _list(root: ET.Element, tag: str, child: str, values: list[str]) -> None:
    el = root.find(tag)
    if el is None:
        el = ET.SubElement(root, tag)
    for c in list(el):
        el.remove(c)
    for v in values:
        ET.SubElement(el, child).text = v


def _set(root: ET.Element, tag: str, value: str) -> None:
    el = root.find(tag)
    if el is None:
        el = ET.SubElement(root, tag)
    el.text = value


def patch_mqpar(text: str, spec: MaxQuantSpec, fractions: dict[str, str], lfq: bool) -> str:
    """The template with this job's files, experiments, fractions, FASTA, threads and output folder."""
    ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root = ET.fromstring(text)
    if root.tag != "MaxQuantParams":
        raise JobError(f"the mqpar template is not a MaxQuant parameter file (root <{root.tag}>)")
    fastas = root.find("fastaFiles")
    if fastas is None:
        fastas = ET.SubElement(root, "fastaFiles")
    info = fastas.find("FastaFileInfo")
    keep = ET.fromstring(ET.tostring(info)) if info is not None else None
    for c in list(fastas):
        fastas.remove(c)
    if keep is None:
        keep = ET.Element("FastaFileInfo")
        for tag, val in (("identifierParseRule", r">.*\|(.*)\|"), ("descriptionParseRule", ">(.*)"),
                         ("taxonomyParseRule", ""), ("variationParseRule", ""), ("modificationParseRule", ""),
                         ("taxonomyId", "")):
            ET.SubElement(keep, tag).text = val
    path_el = keep.find("fastaFilePath")
    if path_el is None:
        path_el = ET.Element("fastaFilePath")
        keep.insert(0, path_el)
    path_el.text = str(spec.fasta)
    fastas.append(keep)
    raws = [line[0] for line in spec.manifest_lines]
    _list(root, "filePaths", "string", raws)
    _list(root, "experiments", "string", [f"{line[1]}_{line[2]}" for line in spec.manifest_lines])
    _list(root, "fractions", "short", [fractions.get(r, NO_FRACTION) for r in raws])
    _list(root, "ptms", "boolean", ["False"] * len(raws))
    _list(root, "paramGroupIndices", "int", ["0"] * len(raws))
    _list(root, "referenceChannel", "string", [""] * len(raws))
    _set(root, "fixedCombinedFolder", str(spec.workdir))
    if spec.threads:
        _set(root, "numThreads", str(spec.threads))
    if lfq:
        for group in root.iter("parameterGroup"):
            _set(group, "lfqMode", "1")
    ET.indent(root, space="   ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def create_template(spec: MaxQuantSpec) -> Path:
    """`MaxQuantCmd --create <file>`: the installed version's default parameters."""
    target = spec.run_dir / DEFAULT_TEMPLATE
    cmd = _launch(spec.exe) + ["--create", str(target)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                           cwd=str(spec.run_dir), **_popen_kwargs())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JobError(f"could not make a MaxQuant parameter template ({' '.join(cmd)}): {exc}") from exc
    if r.returncode != 0 or not target.is_file():
        raise JobError(f"MaxQuant --create failed (exit {r.returncode}): {(r.stdout + r.stderr).strip()[-300:]}")
    return target


def write_inputs(spec: MaxQuantSpec) -> str | None:
    """ionomos_run/mqpar.xml and an empty maxquant/ folder; an old maxquant/ is kept, renamed, never deleted."""
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    moved = None
    if spec.workdir.exists() and any(spec.workdir.iterdir()):
        moved = f"{WORKDIR}_previous_{datetime.now():%Y%m%d-%H%M%S}"
        os.replace(spec.workdir, spec.dest / moved)
    spec.workdir.mkdir(exist_ok=True)
    template = spec.template or create_template(spec)
    text = patch_mqpar(template.read_text(encoding="utf-8-sig", errors="replace"), spec, spec.fractions,
                       lfq=spec.template is None)
    spec.workflow.write_text(text, encoding="utf-8", newline="\n")
    return moved


def describe(cfg: Config, key: str) -> list[tuple[bool | None, str]]:
    """Setup checklist lines for a MaxQuant method."""
    m = cfg.methods[key]
    exe = find_exe(str(m.extra.get("maxquant_exe") or ""))
    out: list[tuple[bool | None, str]] = [(exe is not None, f"MaxQuant: {exe}" if exe else
                                           "MaxQuant not found (set maxquant_exe)")]
    fasta = _find_file(m.fasta, cfg.fasta_dir) if m.fasta else None
    out.append((fasta is not None, f"FASTA {m.fasta}" + ("" if fasta else f" missing from {cfg.fasta_dir}")))
    if m.extra.get("mqpar"):
        found = _find_file(str(m.extra["mqpar"]), cfg.workflow_dir, ".xml")
        out.append((found is not None, f"mqpar {m.extra['mqpar']}" + ("" if found else " missing")))
    else:
        out.append((None, "no lab mqpar: MaxQuant's defaults with label-free quantification (save your own "
                          "from the GUI for your usual settings)"))
    return out
