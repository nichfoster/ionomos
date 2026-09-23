# ionomos

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

Preferred: build `Ionomos.exe` on a Windows machine with
`deploy\build_exe.ps1`, unzip on the PC, double-click. Fallback: wheel +
`install.ps1` via `deploy/make_release.sh`. Full walkthrough in
[`../docs/DEPLOY_WINDOWS.md`](../docs/DEPLOY_WINDOWS.md).

## Modules

| Module | State |
|---|---|
| `naming.py` | ✅ keyword/user/date matching, per-method raw tails |
| `config.py` | ✅ YAML load + validation (FragPipe paths are warnings until Phase 2) |
| `watcher.py` | ✅ inbox polling with tree-fingerprint stability |
| `ledger.py` | ✅ SQLite jobs table, startup recovery |
| `intake.py` | ✅ plan → move → ionomos.json → ledger; `.REJECTED.txt` on failure |
| `resolve.py` | ✅ tkinter resolver window; pure validation logic separately testable |
| `testbed.py` | ✅ `ionomos testbed init/list/drop/reset/gui-demo` |
| `app.py` | ✅ setup wizard / control panel (`ionomos setup`, or the exe with no args) |
| `configio.py` | ✅ commented config.yaml writer used by the app |
| `service.py` | ✅ child watcher process, PID file, Task Scheduler, exe routing |
| `cli.py` | ✅ `setup`, `run [--no-gui]`, `check`, `status`, `dry-run`, `retry`, `testbed` |
| `manifest.py` | stub |
| `worker.py` | stub |
| `runners/fragpipe.py` | stub (port from `reference/prior-work`) |
| `runners/isodtb.py` | stub (port from `reference/lab-scripts`) |
| `runners/tmt.py` | stub (port from `reference/lab-scripts`) |
| `runners/dia.py` | stub |
