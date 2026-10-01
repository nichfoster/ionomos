"""
Which engine runs a job: FragPipe (default), DIA-NN (`engine: diann`, diann.py), MaxQuant (`engine: maxquant`,
maxquant.py) or Sage (`engine: sage`, sage.py). The worker
calls only these two functions; the run loop itself (fragpipe.run: start, cancel, stop, timeout, kill the
process tree) is shared.
"""
from __future__ import annotations

from ionomos import diann, fragpipe, maxquant, sage
from ionomos.config import Config
from ionomos.ledger import Job


def prepare(job: Job, cfg: Config) -> fragpipe.RunSpec:
    if diann.uses_diann(cfg, job.method):
        return diann.prepare(job, cfg)
    if maxquant.uses_maxquant(cfg, job.method):
        return maxquant.prepare(job, cfg)
    if sage.uses_sage(cfg, job.method):
        return sage.prepare(job, cfg)
    return fragpipe.prepare(job, cfg)


def write_inputs(spec: fragpipe.RunSpec) -> str | None:
    if isinstance(spec, diann.DiannSpec):
        return diann.write_inputs(spec)
    if isinstance(spec, maxquant.MaxQuantSpec):
        return maxquant.write_inputs(spec)
    if isinstance(spec, sage.SageSpec):
        return sage.write_inputs(spec)
    return fragpipe.write_inputs(spec)
