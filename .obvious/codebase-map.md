# Codebase map

Folder-level overview (depth 2). `reference/` is read-only lab material —
port from it, never edit it. The docs in `docs/` are the spec.

| Path | What |
|---|---|
| `docs/` | Design docs — the spec. `ARCHITECTURE`, `NAMING_CONVENTION`, `WORKFLOWS`, `ROADMAP`, `DECISIONS`, `TESTING`, `DEV_LOOP` (Mac↔PC loop), `DEPLOY_WINDOWS`, `PROTEOMICS_PC`, `SCOPE` |
| `ionomos/` | The Python package: `pyproject.toml`, `config.example.yaml`, `src/ionomos/` (code), `tests/` (pytest suite + golden files) |
| `ionomos/src/ionomos/` | Core modules: `naming.py` (folder-name parsing), `config.py`, `watcher.py` (inbox polling), `intake.py` (file/move jobs), `ledger.py` (SQLite jobs), `worker.py` (job execution), `fragpipe.py` + `runners/` (headless FragPipe + post-process runners), `downstream/` (stats/volcano/report ports of FragPipeAnalystR), `resolve.py`/`popups.py`/`app.py`/`tkutil.py` (tkinter GUI), `testbed.py` + `stress.py` (fake lab + fake FragPipe), `cli.py` (entry point), `names.py` (every on-disk name, incl. LabWatch-era twins), `service.py` (child process/Task Scheduler) |
| `ionomos/tests/` | Unit + e2e tests; `golden/` holds expected outputs for the downstream-analysis ports |
| `deploy/` | Windows installers/build scripts: `build_exe.ps1` (PyInstaller → Ionomos.exe), `dev_install.ps1` (git-clone install), `install.ps1`, `make_release.sh`, `ionomos.iss`/`ionomos.spec` |
| `.github/workflows/` | CI: `tests.yml` (ruff + pytest on Linux AND Windows every push), `build-exe.yml` (installer + zip on `v*` tags, with a full frozen-pipeline smoke test) |
| `scripts/` | One-shot dev/test setup: `test_mac.sh`, `test_windows.ps1` |
| `tools/inventory/` | PowerShell inventory collector for the lab PC |
| `reference/` | Read-only: lab SOPs, the R post-processing scripts being replaced, PC inventory output, prior work (`proteomics-qc-pkg`) |
