"""
Sequential job worker.

Contract (Phase 2):
    Worker(config, ledger).run_forever()   — polls ledger.next_queued(); one job at a time
    Worker.run_job(job)
        status=running
        manifest -> dest/fragpipe/fragpipe-files.fp-manifest
        (TMT) annotations
        runners.fragpipe.run(...)  -> raises FragPipeError on non-zero/timeout
        for step in method.postprocess: runners.<step>(...)
        status=done  + DONE.txt          |  status=failed + FAILED.txt (reason)
        rewrite labwatch.json on every transition

Runs in the same process as the watcher (one thread each) — see
reference/prior-work/proteomics-qc-pkg/queue_worker.py and main.py for the
threading shape.
"""
from __future__ import annotations


class Worker:
    def __init__(self, config, ledger):
        raise NotImplementedError("Phase 2")
