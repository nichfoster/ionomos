"""
Post-processing after a successful FragPipe run.

Each method lists step names in config (methods.<X>.postprocess). A step is a
function (job, spec, cfg) -> list of warnings; it writes into <experiment>/results/.
A step that raises doesn't fail the job — the FragPipe output is still good —
it is reported as a warning on the done job.

Registered steps so far: none. Planned (docs/WORKFLOWS.md):
    isodtb_sites     port of isoDTB_Fragpipe_merge-individual-peptides-to-Site.R
    tmt_annotation   port of correct_experimental_annotation_for_Fragpipe-TMT.R
"""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger("labwatch.postprocess")

STEPS: dict[str, Callable] = {}
PLANNED = {"isodtb_sites", "tmt_annotation"}


def run_all(job, spec, cfg) -> list[str]:
    method = cfg.methods.get(job.method)
    warnings: list[str] = []
    for name in (method.postprocess if method else ()):
        fn = STEPS.get(name)
        if fn is None:
            if name in PLANNED:
                log.info("job %s: post-processing step %r is not built yet; skipped", job.id, name)
            else:
                warnings.append(f"unknown post-processing step {name!r} in config; skipped")
            continue
        try:
            warnings += fn(job, spec, cfg) or []
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s: post-processing %s failed", job.id, name)
            warnings.append(f"post-processing {name} failed: {exc}")
    return warnings
