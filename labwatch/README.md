# labwatch

The watcher package. Design is in [`../docs/`](../docs/); this file is the
developer/operator view.

## Status

Phase 0/1. `naming.py` is implemented and tested; everything else is a stub
with its contract in the module docstring. See `../docs/ROADMAP.md`.

## Develop (Mac/Linux)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
```

## Deploy (proteomics PC, Windows)

Target layout is `C:\Fragpipe_Auto\` — see `docs/ARCHITECTURE.md`.

```powershell
# python on PATH is the Store stub on this machine; use the py launcher.
py -3.14 -m venv C:\Fragpipe_Auto\labwatch\.venv
C:\Fragpipe_Auto\labwatch\.venv\Scripts\pip install <path-to-this-folder>
copy config.example.yaml C:\Fragpipe_Auto\config.yaml   # then edit
C:\Fragpipe_Auto\labwatch\.venv\Scripts\labwatch dry-run C:\Fragpipe_Auto\inbox\<some_folder>
C:\Fragpipe_Auto\labwatch\.venv\Scripts\labwatch run
```

`run_labwatch.bat` wraps the last line for Task Scheduler (run at log on,
"run whether user is logged on or not" if the account policy allows it).

## Modules

| Module | State |
|---|---|
| `naming.py` | ✅ implemented + tests |
| `config.py` | stub |
| `watcher.py` | stub |
| `ledger.py` | stub |
| `intake.py` | stub |
| `manifest.py` | stub |
| `worker.py` | stub |
| `runners/fragpipe.py` | stub (port from `reference/prior-work`) |
| `runners/isodtb.py` | stub (port from `reference/lab-scripts`) |
| `runners/tmt.py` | stub (port from `reference/lab-scripts`) |
| `runners/dia.py` | stub |
| `cli.py` | stub |
