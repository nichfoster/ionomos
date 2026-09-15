"""
Command line.

    labwatch run      [--config C:/Fragpipe_Auto/config.yaml]   watcher + worker, forever
    labwatch status   [--config ...] [--all]                    table of jobs from the ledger
    labwatch dry-run  <folder>                                   parse + validate + print the
                                                                 manifest that WOULD be written;
                                                                 moves nothing, touches nothing
    labwatch retry    <job-id>                                   failed -> queued

Default --config: LABWATCH_CONFIG env var, else ./config.yaml.
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    print("labwatch: not implemented yet (Phase 1). See docs/ROADMAP.md", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
