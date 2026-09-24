---
name: local-dev
description: How to get the Ionomos dev stack running and verified in this sandbox
---

# local-dev

Recorded by the onboarding run of 2026-09-24. Everything below was executed
and verified in this sandbox (Python 3.13, no display, Linux).

## Setup

```bash
python3 -m venv ionomos/.venv
ionomos/.venv/bin/pip install -e "ionomos[dev]"
```

There is no other infrastructure: SQLite ledger only, no Docker, no external
services. FragPipe itself is Windows-only and absent — the testbed's fake
FragPipe stands in for it everywhere (tests, CI, local runs).

## Verify

```bash
ionomos/.venv/bin/python -m ruff check ionomos/src ionomos/tests
IONOMOS_OFFLINE=1 ionomos/.venv/bin/python -m pytest ionomos -q
```

Expected: ruff clean; pytest `350 passed, 23 skipped` (~85s). The skips are
tkinter GUI tests — they skip automatically without a display.

## Primary flow (testbed e2e)

```bash
ionomos/.venv/bin/ionomos testbed init ./ionomos-testbed
nohup env IONOMOS_FAKE_FP_SECONDS=1 ionomos/.venv/bin/ionomos \
  --config ionomos-testbed/Fragpipe_Auto/config.yaml run --no-gui > /tmp/ionomos-watcher.out 2>&1 &
ionomos/.venv/bin/ionomos testbed drop iso_good ./ionomos-testbed
ionomos/.venv/bin/ionomos testbed drop dia_good ./ionomos-testbed
```

Poll `--config ionomos-testbed/Fragpipe_Auto/config.yaml status --all` until
both jobs show `done` (~10s with `IONOMOS_FAKE_FP_SECONDS=1`). Then check
`attention` (exit 0) and inspect
`ionomos-testbed/Fragpipe_General/<user>/<experiment>/results/report.html`
(should be self-contained HTML containing `ionomos-report-v2`) and
`analysis.json`. `ionomos analyze <experiment-dir> --test welch` re-runs the
analysis on demand.

## Gotchas

- No server/port to health-check — the "app" is a watcher process; verify via
  the CLI (`status --all`, `attention`) and result files.
- Stop the watcher before snapshotting/re-running: kill the `ionomos --config …`
  process. Note `pkill -f 'ionomos'` from a shell whose own command line
  contains the pattern kills that shell — match the full `ionomos --config`
  string from a separate command or use the recorded PID.
- `ionomos-testbed/` is disposable (gitignored); `ionomos testbed reset`
  wipes it. Keep it out of commits along with `*.db`, `*.log`, `*.raw`,
  `*.mzML`, `config.yaml`.
- The venv must sit at `ionomos/.venv` — `scripts/test_mac.sh` and the docs
  assume that path; `CLAUDE.md` says tests are `cd ionomos && .venv/bin/pytest`.
- FragPipe forbids spaces in paths; never create testbed/config paths with
  spaces, and never hard-code on-disk names (use `names.py`).
