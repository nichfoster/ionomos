"""
Entry point for the proteomics QC pipeline.

Loads config, opens the SQLite store, starts the sequential worker thread, then
runs the folder watcher in the foreground. Ctrl-C shuts everything down cleanly.

Run it directly:
    python main.py
or via the bundled launcher:
    run_qc.bat
"""
from __future__ import annotations

import logging
import sys

from config_loader import load_config
from queue_worker import QueueWorker
from store import Store
from watcher import Watcher


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    setup_logging()
    log = logging.getLogger("qc.main")

    try:
        cfg = load_config()
    except (FileNotFoundError, ValueError) as exc:
        log.error("Could not load config.yaml: %s", exc)
        return 1

    # Light sanity check so misconfiguration fails fast and clearly rather than
    # halfway through the first search.
    paths = cfg.get("paths", {})
    required = ["incoming_dda", "incoming_dia", "fragpipe_exe",
                "workflow_dda", "workflow_dia", "output_root", "database"]
    missing = [k for k in required if not paths.get(k)]
    if missing:
        log.error("config.yaml is missing required paths: %s", missing)
        return 1

    store = Store(paths["database"])
    log.info("Database ready at %s", paths["database"])

    worker = QueueWorker(cfg, store)
    worker.start()

    watcher = Watcher(cfg, worker)
    try:
        watcher.run_forever()  # blocks until Ctrl-C
    finally:
        log.info("Shutting down — waiting for the current search to finish...")
        worker.stop(drain=True)
        log.info("Goodbye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
