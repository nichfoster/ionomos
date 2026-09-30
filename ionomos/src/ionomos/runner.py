"""
Which engine runs a job: FragPipe (default) or DIA-NN (`engine: diann` on the method, diann.py). The worker
calls only these two functions; the run loop itself (fragpipe.run: start, cancel, stop, timeout, kill the
process tree) is shared.
"""
from __future__ import annotations

from ionomos import diann, fragpipe
from ionomos.config import Config
from ionomos.ledger import Job


def prepare(job: Job, cfg: Config) -> fragpipe.RunSpec:
    if diann.uses_diann(cfg, job.method):
        return diann.prepare(job, cfg)
    return fragpipe.prepare(job, cfg)


def write_inputs(spec: fragpipe.RunSpec) -> str | None:
    if isinstance(spec, diann.DiannSpec):
        return diann.write_inputs(spec)
    return fragpipe.write_inputs(spec)
