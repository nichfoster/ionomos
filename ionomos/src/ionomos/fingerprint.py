"""
The run fingerprint (D59): what one search did, in one small text file next to the job.

    path = write(spec, res, attempt=1)        # after fragpipe.run(); never raises, returns None on trouble

    <experiment>/ionomos_run/run_fingerprint.json            the latest search
    <experiment>/ionomos_run/run_fingerprint_<time>.json     earlier searches of the same experiment (kept)

Ionomos' FragPipe handling was written from FragPipe's documentation and source, not from a run: the first
real searches are what it gets checked against. The fingerprint holds what that check needs and nothing big:
the launcher and command line, FragPipe's version block, the workflow's key settings, the names and sizes of
the output files, the first and last lines of the console log, what Ionomos' parsers read out of the log
(steps, exit codes, the end marker) and the timings. It is JSON, a few tens of kB, and holds no measurements.
It does hold names (the experiment folder, raw files, the PC's paths), as the console log does: sending it on
is the diagnostics bundle's job, which strips them.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
from collections import deque
from datetime import datetime
from pathlib import Path

from ionomos import __version__, fragpipe, names

log = logging.getLogger("ionomos.fingerprint")

FORMAT = 1
HEAD_LINES = 80
TAIL_LINES = 120
LINE_CHARS = 300
MAX_OUTPUTS = 500

# The workflow settings worth comparing with a GUI run (besides every "<tool>.run-<tool>" switch).
WORKFLOW_SETTINGS = (
    "workflow.saved-with-ver", "workflow.input.data-type.regular-ms", "workflow.input.data-type.im-ms",
    "workflow.threads", "workflow.ram", "database.decoy-tag", "run-psm-validation", "run-validation-tab",
    "quantitation.run-label-free-quant", "msfragger.search_enzyme_name_1", "msfragger.allowed_missed_cleavage_1",
    "msfragger.precursor_mass_lower", "msfragger.precursor_mass_upper", "msfragger.precursor_mass_units",
    "msfragger.fragment_mass_tolerance", "msfragger.fragment_mass_units", "msfragger.calibrate_mass",
    "msfragger.misc.slice-db", "msfragger.data_type", "ionquant.mbr", "ionquant.maxlfq", "ionquant.requantify",
    "ionquant.use-labeling", "ionquant.use-lfq", "ionquant.light", "ionquant.medium", "ionquant.heavy",
    "ionquant.normalization", "tmtintegrator.channel_num", "tmtintegrator.extraction_tool",
    "tmtintegrator.quant_level", "tmtintegrator.ref_tag", "tmtintegrator.add_Ref", "diann.q-value",
    "diann.quantification-strategy", "tab-run.delete_temp_files", "tab-run.write_sub_mzml",
)

# Console lines the parsers look at; everything else is counted, not kept (a real log runs to many MB).
_KEEP = re.compile(r"^\d+ commands to execute:|^Execution order:|^~{9} fragpipe\.config ~{9}| \[Work dir: |"
                   r"^Process '|^Cancelling \d+ remaining tasks|ALL JOBS DONE IN|It's a dry-run|"
                   r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ ERROR")
_VERSION_LINE = re.compile(r"^([A-Za-z][\w .\-]{1,40}) version (.+)$")
_RUNTIME_LINE = re.compile(r"^  (.+): ([\d.,]+) minutes$")
_SYSTEM_LINE = re.compile(r"^(System OS|Java Info|\.NET Core Info): (.*)$")


def _clip(line: str) -> str:
    line = line.rstrip("\r\n")
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS] + f" …[{len(line)} chars]"


def scan_console(path: Path, offset: int = 0) -> dict:
    """One pass over the console log from `offset` (where this attempt began): head, tail, counts, and the
    lines the parsers read."""
    head: list[str] = []
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    kept: list[str] = []
    versions: dict[str, str] = {}
    system: dict[str, str] = {}
    runtimes: dict[str, str] = {}
    n = 0
    in_versions = in_runtimes = False
    size = 0
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(min(offset, size))
            for raw in fh:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                n += 1
                if len(head) < HEAD_LINES:
                    head.append(_clip(line))
                else:
                    tail.append(_clip(line))
                if _KEEP.search(line):
                    kept.append(line[:LINE_CHARS])
                m = _SYSTEM_LINE.match(line)
                if m:
                    system.setdefault(m.group(1), m.group(2))
                if line.startswith("Version info:"):
                    in_versions = True
                    continue
                if in_versions:
                    m = _VERSION_LINE.match(line)
                    if m:
                        versions.setdefault(m.group(1), m.group(2).strip())
                    else:
                        in_versions = False
                if line.startswith("Task Runtimes:"):
                    in_runtimes = True
                    continue
                if in_runtimes:
                    m = _RUNTIME_LINE.match(line)
                    if m:
                        runtimes[m.group(1)] = m.group(2)
                    elif line.strip():
                        in_runtimes = False
    except OSError as exc:
        return {"file": path.name, "error": str(exc)}
    facts = fragpipe.console_facts("\n".join(kept))
    facts["finished"] = [list(x) for x in facts["finished"]]
    return {"file": path.name, "bytes": max(0, size - offset), "lines": n, "head": head, "tail": list(tail),
            "system": system, "versions": versions, "task_runtimes_minutes": runtimes, "facts": facts}


def list_outputs(workdir: Path) -> dict:
    """Names and sizes under the engine's output folder (relative, forward slashes), biggest listing capped."""
    files: list[tuple[str, int]] = []
    total = 0
    count = 0
    if workdir.is_dir():
        for root, dirs, fnames in os.walk(workdir):
            dirs.sort()
            for f in sorted(fnames):
                p = Path(root) / f
                try:
                    size = p.stat().st_size
                except OSError:
                    size = -1
                count += 1
                total += max(size, 0)
                if len(files) < MAX_OUTPUTS:
                    files.append((p.relative_to(workdir).as_posix(), size))
    return {"folder": workdir.name, "files": count, "bytes": total, "listing": [list(x) for x in files],
            "truncated": count > len(files)}


def workflow_summary(path: Path) -> dict:
    try:
        props = fragpipe.read_properties(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return {"file": path.name, "error": str(exc)}
    keep = {k: props[k] for k in WORKFLOW_SETTINGS if k in props}
    keep.update({k: v for k, v in props.items() if ".run-" in k})
    db = props.get("database.db-path", "")
    return {"file": path.name, "keys": len(props), "database": Path(db).name if db else "",
            "needs": fragpipe.workflow_needs(props), "settings": dict(sorted(keep.items()))}


def build(spec: fragpipe.RunSpec, res: fragpipe.RunResult, attempt: int = 0, finished_at: str = "") -> dict:
    lines = spec.manifest_lines
    types: dict[str, int] = {}
    exts: dict[str, int] = {}
    raw_bytes = 0
    for path, _exp, _rep, dtype in lines:
        types[dtype] = types.get(dtype, 0) + 1
        ext = Path(path).suffix.lower()
        exts[ext] = exts.get(ext, 0) + 1
        try:
            raw_bytes += Path(path).stat().st_size
        except OSError:
            pass
    console = scan_console(spec.console_log, getattr(res, "console_offset", 0))
    root = fragpipe.fragpipe_root(spec.exe)
    fp_version = (console.get("versions") or {}).get("FragPipe") or ""
    doc = {
        "fingerprint": FORMAT,
        "ionomos": __version__,
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "platform": {"system": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()},
        "job": {"id": spec.job_id, "method": spec.method, "kind": spec.kind or spec.method, "attempt": attempt,
                "experiment_folder": spec.dest.name},
        "engine": {"name": spec.engine_name, "version": fp_version, "launcher": str(spec.exe),
                   "install_folder": str(root) if root else "", "command": spec.command(), "env": dict(spec.env),
                   "working_folder": str(spec.run_dir), "threads": spec.threads, "ram_gb": spec.ram_gb,
                   "system": console.pop("system", {}), "tool_versions": console.pop("versions", {})},
        "timing": {"started_at": res.started_at, "finished_at": finished_at, "seconds": res.seconds,
                   "timeout_minutes": spec.timeout_minutes,
                   "task_runtimes_minutes": console.pop("task_runtimes_minutes", {})},
        "result": {"ok": res.ok, "exit_code": res.code, "reason": res.reason, "timed_out": res.timed_out,
                   "cancelled": res.cancelled, "stopped": res.stopped, "hints": list(res.hints),
                   "warnings": list(dict.fromkeys([*spec.warnings, *res.warnings])), "notes": list(spec.notes),
                   "missing_outputs": fragpipe.missing_outputs(spec) if res.ok else []},
        "manifest": {"files": len(lines), "data_types": types, "extensions": exts, "raw_bytes": raw_bytes,
                     "experiments": len({(e, r) for _p, e, r, _t in lines})},
        "console": console,
        "outputs": list_outputs(spec.workdir),
    }
    if spec.engine_name == "FragPipe":
        doc["workflow"] = workflow_summary(spec.workflow)
        if spec.fasta is not None:
            info = fragpipe.fasta_info(spec.fasta, (doc["workflow"].get("settings") or {}).get("database.decoy-tag", "rev_"))
            doc["fasta"] = {"file": spec.fasta.name, "entries": info.get("entries"), "decoys": info.get("decoys")}
        own = spec.workdir / "fragpipe.workflow"  # FragPipe saves the settings it really used here
        if own.is_file():
            doc["workflow_as_run"] = workflow_summary(own)
        doc["parsed_progress"] = fragpipe.progress(spec.console_log)
    return doc


def write(spec: fragpipe.RunSpec, res: fragpipe.RunResult, attempt: int = 0, finished_at: str = "") -> Path | None:
    """Write the fingerprint for a search that just ended. An earlier one is kept under a dated name."""
    try:
        target = spec.run_dir / names.FINGERPRINT_FILE
        if target.exists():
            stamp = datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
            older = target.with_name(f"{target.stem}_{stamp}{target.suffix}")
            if not older.exists():
                os.replace(target, older)
        doc = build(spec, res, attempt, finished_at)
        target.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return target
    except Exception:  # noqa: BLE001 - a capture for the maintainer must never touch the job's outcome
        log.exception("could not write the run fingerprint for job %s", spec.job_id)
        return None
