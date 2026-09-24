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


def prepare(dest: Path, cfg, method: str | None = None, extra: dict | None = None) -> dict:
    """Everything analyze() needs for one folder: the status record (with experiment.yaml file corrections
    applied), lab settings, the experiment's analysis overrides, method, context."""
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

        current = load_overrides(dest)
        overrides = current.analysis or overrides
        from ionomos.downstream.quant import run_stem

        by_stem = {run_stem(name): value for name, value in current.files.items()}
        for line in (record.get("plan") or {}).get("manifest") or []:
            correction = by_stem.get(run_stem(line["file"]))
            if correction is not None:
                if correction.experiment:
                    line["experiment"] = correction.experiment
                if correction.bioreplicate is not None:
                    line["bioreplicate"] = correction.bioreplicate
    except Exception:  # noqa: BLE001 - a broken experiment.yaml must not block the analysis
        pass
    lab = dict(getattr(cfg, "analysis", {}) or {}) if cfg is not None else {}
    lab.pop("enabled", None)
    mod_mass = "561.3387"
    if cfg is not None and method in getattr(cfg, "methods", {}):
        mod_mass = str(cfg.methods[method].extra.get("isodtb_mod_mass", mod_mass))
    return {"dest": dest, "method": method, "lab": lab, "overrides": {**overrides, **(extra or {})}, "record": record,
            "context": context_for(dest, record, method or "?"), "mod_mass": mod_mass}


def run_for_folder(dest: Path, cfg, method: str | None = None, extra: dict | None = None, progress=None):
    """Analyse one experiment folder (used after a job, by `ionomos analyze` and the Analysis tab).
    Returns downstream.Outcome."""
    from ionomos import downstream

    p = prepare(dest, cfg, method, extra)
    return downstream.analyze(p["dest"], p["method"], p["lab"], p["overrides"], p["record"], p["context"],
                              p["mod_mass"], progress=progress)


def inspect_folder(dest: Path, cfg, method: str | None = None) -> dict:
    """What the Analysis tab shows before running: method, source table, samples with their conditions
    (as the data says, before this experiment's sample overrides), and the current overrides."""
    from ionomos import downstream

    p = prepare(dest, cfg, method)
    dest = p["dest"]
    workdir = dest / "fragpipe" if (dest / "fragpipe").is_dir() else dest
    found = p["method"] if p["method"] and p["method"] != "auto" else downstream.detect_method(workdir)
    m, _files, notes = downstream.load_quantities(found, workdir, dest / downstream.RESULTS, p["record"], p["mod_mass"])
    samples = []
    if m is not None:
        samples = [{"sample": x, "condition": m.condition[x], "replicate": m.replicate.get(x)} for x in m.samples]
    return {"method": found, "source": m.source if m else None, "features": len(m.features) if m else 0,
            "kind": m.kind if m else None, "samples": samples, "notes": notes, "overrides": p["overrides"],
            "report": dest / downstream.RESULTS / "report.html"}


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
