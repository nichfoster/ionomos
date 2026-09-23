"""
After a successful FragPipe run: the downstream analysis (ionomos.downstream).

    warnings, summary = run_all(job, spec, cfg)

Method prep steps (the R-script ports) always run; statistics, volcano plots
and results/report.html run when config analysis.enabled is on (default).
A failure here never fails the job — FragPipe's output is still good — it
becomes a warning on the done job, and `ionomos analyze <folder>` re-runs it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("ionomos.postprocess")


def context_for(dest: Path, record: dict, method: str) -> dict:
    folder = (record.get("plan") or {}).get("folder") or {}
    run = record.get("run") or {}
    wf = Path(run.get("workflow_source") or "").name
    return {"experiment": folder.get("original") or dest.name, "user": folder.get("user") or "",
            "method": method, "date": folder.get("date") or "", "fragpipe": f"workflow {wf}" if wf else ""}


def run_for_folder(dest: Path, cfg, method: str | None = None, extra: dict | None = None):
    """Analyse one experiment folder (used after a job and by `ionomos analyze`). Returns downstream.Outcome."""
    from ionomos import downstream

    dest = Path(dest)
    try:
        from ionomos.names import status_path

        record = json.loads(status_path(dest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    method = method or ((record.get("plan") or {}).get("folder") or {}).get("method")
    overrides = ((record.get("plan") or {}).get("overrides") or {}).get("analysis") or {}
    try:  # an experiment.yaml edited after intake (e.g. new comparisons) wins
        from ionomos.manifest import load_overrides

        overrides = load_overrides(dest).analysis or overrides
    except Exception:  # noqa: BLE001 - a broken experiment.yaml must not block the analysis
        pass
    lab = dict(getattr(cfg, "analysis", {}) or {}) if cfg is not None else {}
    lab.pop("enabled", None)
    mod_mass = "561.3387"
    if cfg is not None and method in getattr(cfg, "methods", {}):
        mod_mass = str(cfg.methods[method].extra.get("isodtb_mod_mass", mod_mass))
    layers = {**overrides, **(extra or {})}
    return downstream.analyze(dest, method, lab, layers, record, context_for(dest, record, method or "?"), mod_mass)


def run_all(job, spec, cfg) -> tuple[list[str], dict]:
    if not (getattr(cfg, "analysis", {}) or {}).get("enabled", True):
        # statistics off: still write the lab's R-script outputs (site table / TMT annotation)
        from ionomos import downstream

        dest = Path(job.dest_dir)
        try:
            _m, files, notes = downstream.load_quantities(job.method, dest / "fragpipe", dest / downstream.RESULTS, None)
        except Exception as exc:  # noqa: BLE001
            return [f"post-processing failed: {exc}"], {}
        return [f"post-processing: {n}" for n in notes], {"files": [f"{downstream.RESULTS}/{f.name}" for f in files]}
    out = run_for_folder(Path(job.dest_dir), cfg, job.method)
    for w in out.warnings:
        log.warning("job %s analysis: %s", job.id, w)
    if out.report:
        log.info("job %s: report %s", job.id, out.report)
    return [f"analysis: {w}" for w in out.warnings], out.summary
