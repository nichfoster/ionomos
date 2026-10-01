"""
After a successful FragPipe run: the downstream analysis (ionomos.downstream).

    warnings, summary = run_all(job, spec, cfg)

Method prep steps (the R-script ports) always run; statistics, volcano plots
and results/report.html run when config analysis.enabled is on (default).
A failure here never fails the job — FragPipe's output is still good — it
becomes a warning on the done job, and `ionomos analyze <folder>` re-runs it.
Then, for a job with QC-standard runs, instrument QC trending (qctrend.after_job, D45),
isolated the same way.
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


def analysis_method(cfg, method: str | None, record: dict | None = None) -> str | None:
    """What the analysis reads for a lab's method key (Config.analysis_method, D54): its engine's own table,
    its like: target, else the key. A key the config doesn't have falls back to what intake recorded, so a
    folder analysed without its lab's config still reads as it did there. Anything else passes through
    (a kind, another engine's name, "table", "auto")."""
    if cfg is not None and method in (getattr(cfg, "methods", None) or {}):
        return cfg.analysis_method(method)
    filed = ((record or {}).get("plan") or {}).get("folder") or {}
    if method and method == filed.get("method"):
        return ((record or {}).get("method_config") or {}).get("analysis_method") or method
    return method


def prepare(dest: Path, cfg, method: str | None = None, extra: dict | None = None) -> dict:
    """Everything analyze() needs for one folder: the status record (with experiment.yaml file corrections
    applied), lab settings, the experiment's analysis overrides, method, context."""
    dest = Path(dest)
    try:
        from ionomos.names import status_path

        record = json.loads(status_path(dest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    filed = ((record.get("plan") or {}).get("folder") or {}).get("method")  # the lab's key for this experiment
    method = method or filed
    overrides = ((record.get("plan") or {}).get("overrides") or {}).get("analysis") or {}
    try:  # an experiment.yaml edited after intake (e.g. new comparisons) wins
        from ionomos.manifest import load_overrides

        current = load_overrides(dest)
        overrides = current.analysis or overrides
        tmt = current.tmt or ((record.get("plan") or {}).get("overrides") or {}).get("tmt") or {}
        if isinstance(tmt, dict) and tmt.get("reference_channel") not in (None, "") and \
                "tmt_reference" not in overrides:  # experiment.yaml tmt.reference_channel = analysis.tmt_reference
            overrides = {**overrides, "tmt_reference": tmt["reference_channel"]}
        if current.tmt:  # the channel map as it is now names Sage's TMT channels (engines.load_sage_tmt)
            record.setdefault("plan", {}).setdefault("overrides", {})["tmt"] = current.tmt
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
    methods = (getattr(cfg, "methods", None) or {}) if cfg is not None else {}
    # the analysis follows the method's kind, whatever the lab calls it (naming.method_kind, D54)
    reads = analysis_method(cfg, method, record)
    label = method
    if method != filed and filed in methods and analysis_method(cfg, filed, record) == reads:
        method = label = filed  # asked for by its kind (the Analysis tab does): still this experiment's method
    mod_mass = "561.3387"
    if method in methods:
        mod_mass = str(methods[method].extra.get("isodtb_mod_mass", mod_mass))
    return {"dest": dest, "method": reads, "lab": lab, "overrides": {**overrides, **(extra or {})},
            "record": record, "context": context_for(dest, record, label or "?"), "mod_mass": mod_mass}


def table_workspace(table: Path) -> Path:
    """Where a table's analysis goes: <table stem>_ionomos/ next to it — a folder Ionomos owns, so results/ and
    experiment.yaml can never overwrite files of the same name that already sit beside someone's table."""
    table = Path(table)
    ws = table.parent / f"{table.stem}_ionomos"
    ws.mkdir(exist_ok=True)
    return ws


def run_for_folder(dest: Path, cfg, method: str | None = None, extra: dict | None = None, progress=None,
                   table: Path | None = None):
    """Analyse one experiment folder (used after a job, by `ionomos analyze` and the Analysis tab).
    table: analyse this file with the any-format loader (results go to results/ next to it).
    Returns downstream.Outcome."""
    from ionomos import downstream

    p = prepare(dest, cfg, method, extra)
    return downstream.analyze(p["dest"], p["method"], p["lab"], p["overrides"], p["record"], p["context"],
                              p["mod_mass"], progress=progress, table=table)


def inspect_folder(dest: Path, cfg, method: str | None = None, table: Path | None = None) -> dict:
    """What the Analysis tab shows before running: method, source table, samples with their conditions
    (as the data says, before this experiment's sample overrides), and the current overrides."""
    from ionomos import downstream

    p = prepare(dest, cfg, method)
    dest = p["dest"]
    workdir = dest / "fragpipe" if (dest / "fragpipe").is_dir() else dest
    found = "table" if table is not None else (
        p["method"] if p["method"] and p["method"] != "auto" else downstream.detect_method(workdir))
    try:
        factor = {**p["lab"], **p["overrides"]}.get("sdrf_factor")
        m, _files, notes = downstream.load_quantities(found, workdir, dest / downstream.RESULTS, p["record"],
                                                      p["mod_mass"], table, factor, dest)
    except ValueError as exc:  # isodtb.SiteError / anytable.TableError: the table holds nothing usable
        m, notes = None, [str(exc)]
    samples = []
    if m is not None:
        from ionomos.downstream.quant import run_stem

        samples = [{"sample": x, "condition": m.condition[x], "replicate": m.replicate.get(x),
                    "run": run_stem(m.columns[x]) if x in m.columns else x} for x in m.samples]
    issues = []
    try:  # what the last analysis found (the editor shows it until the next run)
        issues = json.loads((dest / downstream.RESULTS / "analysis.json").read_text(encoding="utf-8")).get("issues") or []
    except (OSError, ValueError):
        pass
    return {"method": found, "source": m.source if m else None, "features": len(m.features) if m else 0,
            "kind": m.kind if m else None, "samples": samples, "notes": notes, "overrides": p["overrides"],
            "report": dest / downstream.RESULTS / "report.html", "issues": issues,
            "job_id": (p["record"].get("job_id") if isinstance(p["record"], dict) else None)}


def record_issues(log_dir, dest: Path, out, job_id: int | None = None) -> None:
    """Turn an analysis Outcome's issues into one attention item for the experiment (a pop-up window
    asks or explains), or close that item when the analysis came back clean. Never raises."""
    from ionomos import attention
    from ionomos.downstream import doctor

    if log_dir is None:
        return
    dest = Path(dest)
    key = f"analysis:{dest}"
    try:
        pop = doctor.popups(out.issues or [])
        if not pop:
            for it in attention.items(log_dir):
                if it.key == key:
                    attention.resolve(log_dir, it.id)
            return
        errors = [i for i in pop if i.severity == "error"]
        first = (errors or pop)[0]
        kind = "analysis_failed" if errors else "analysis_input"
        more = f" (+{len(pop) - 1} more)" if len(pop) > 1 else ""
        causes, fixes = [], []
        for i in pop:
            causes += [c for c in i.causes if c not in causes]
            fixes += [x for x in i.fixes if x not in fixes]
        attention.raise_item(
            log_dir, kind, f"{dest.name}: {first.title}{more}", first.message, key=key,
            severity="error" if errors else "input", dest=dest, job_id=job_id, causes=causes[:6], fixes=fixes[:6],
            details="\n\n".join(f"[{i.severity}] {i.title}\n{i.message}" for i in out.issues),
            data={"issues": [i.as_dict() for i in out.issues], "method": out.method,
                  "report": str(out.report) if out.report else None})
    except Exception:  # noqa: BLE001
        log.exception("could not record analysis issues for %s", dest)


def run_all(job, spec, cfg) -> tuple[list[str], dict]:
    """The analysis, then instrument QC trending when the job holds QC-standard runs (qctrend.py, D45).
    Trending runs even when the analysis fails, and never raises: its verdicts go in the job's results."""
    warnings, summary = [], {}
    try:
        warnings, summary = _analyse(job, spec, cfg)
    finally:
        try:
            from ionomos import qctrend

            lines = qctrend.after_job(job, cfg)
        except Exception:  # noqa: BLE001 - after_job already catches; belt and braces for an import error
            log.exception("job %s: instrument QC trending failed", getattr(job, "id", "?"))
            lines = []
        if lines:
            summary = {**(summary or {}), "qc_trend": lines}
    return warnings, summary


def _analyse(job, spec, cfg) -> tuple[list[str], dict]:
    if not (getattr(cfg, "analysis", {}) or {}).get("enabled", True):
        # statistics off: still write the lab's R-script outputs (site table / TMT annotation)
        from ionomos import downstream

        dest = Path(job.dest_dir)
        try:
            _m, files, notes = downstream.load_quantities(analysis_method(cfg, job.method, getattr(job, "parsed", None)),
                                                          dest / "fragpipe", dest / downstream.RESULTS, None)
        except Exception as exc:  # noqa: BLE001
            return [f"post-processing failed: {exc}"], {}
        return [f"post-processing: {n}" for n in notes], {"files": [f"{downstream.RESULTS}/{f.name}" for f in files]}
    out = run_for_folder(Path(job.dest_dir), cfg, job.method)
    for w in out.warnings:
        log.warning("job %s analysis: %s", job.id, w)
    if out.report:
        log.info("job %s: report %s", job.id, out.report)
    record_issues(getattr(cfg, "log_dir", None), Path(job.dest_dir), out, job.id)
    return [f"analysis: {w}" for w in out.warnings], out.summary
