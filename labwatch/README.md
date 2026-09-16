# labwatch

The watcher package. Design is in [`../docs/`](../docs/); this file is the
developer/operator view.

## Status

Phase 1 complete and hardened (watcher + intake + resolver window + testbed;
no FragPipe yet). See `../docs/ROADMAP.md`. Testing: `../docs/TESTING.md`.
Deploying: `../docs/DEPLOY_WINDOWS.md`.

## Develop (Mac/Linux)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
```

## Deploy (proteomics PC, Windows)

Build a release on the Mac, copy the zip over, run the installer — full
walkthrough in [`../docs/DEPLOY_WINDOWS.md`](../docs/DEPLOY_WINDOWS.md).

```bash
../deploy/make_release.sh        # -> ../dist/labwatch-<version>-windows.zip
```

## Modules

| Module | State |
|---|---|
| `naming.py` | ✅ keyword/user/date matching, per-method raw tails |
| `config.py` | ✅ YAML load + validation (FragPipe paths are warnings until Phase 2) |
| `watcher.py` | ✅ inbox polling with tree-fingerprint stability |
| `ledger.py` | ✅ SQLite jobs table, startup recovery |
| `intake.py` | ✅ plan → move → labwatch.json → ledger; `.REJECTED.txt` on failure |
| `resolve.py` | ✅ tkinter resolver window; pure validation logic separately testable |
| `testbed.py` | ✅ `labwatch testbed init/list/drop/reset/gui-demo` |
| `cli.py` | ✅ `run [--no-gui]`, `check`, `status`, `dry-run`, `retry`, `testbed` |
| `manifest.py` | stub |
| `worker.py` | stub |
| `runners/fragpipe.py` | stub (port from `reference/prior-work`) |
| `runners/isodtb.py` | stub (port from `reference/lab-scripts`) |
| `runners/tmt.py` | stub (port from `reference/lab-scripts`) |
| `runners/dia.py` | stub |
