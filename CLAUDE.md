# Ionomos — notes for Claude

Read `README.md` then `docs/` before touching code. The docs are the spec;
keep them in sync with code changes (especially NAMING_CONVENTION.md ↔
`ionomos/src/ionomos/naming.py`).

- Target machine is a Windows 11 lab PC (see docs/PROTEOMICS_PC.md). Code is
  developed on macOS; anything path-related must work on both.
- FragPipe forbids spaces in paths. Never introduce a default path with spaces.
- Never write code that deletes or overwrites user experiment data. Intake
  moves; failures leave everything in place.
- `reference/` is read-only lab material (SOPs, R scripts, inventory output).
  Don't edit it; port from it.
- Agents open PRs; a person merges them (docs/DECISIONS.md D31). Keep
  CHANGELOG.md, DECISIONS.md and ROADMAP.md in sync in the same PR.
- CI runs Python 3.11, 3.12 and 3.14 (the lab PC runs 3.14). In tests, never
  patch `os.name` globally to fake Windows; stub the module's `os` instead.
- Tests: `cd ionomos && .venv/bin/pytest`. Tests that open real Tk windows skip locally
  (they run in CI); never switch them on (`IONOMOS_GUI_TESTS=1`) unless the user asks: the windows
  cover their screen. If the repo folder moved, delete
  `ionomos/.venv` and re-run `scripts/test_mac.sh`: venv paths are absolute. Add table-driven cases using real
  names from `reference/pc-inventory/` when extending `naming.py`.
- Log decisions in `docs/DECISIONS.md`; open questions in `docs/ROADMAP.md`.
- The project was called LabWatch up to 0.4.0. Every on-disk/system name and
  its LabWatch-era twin lives in `ionomos/src/ionomos/names.py`; read through
  it, never hard-code `ionomos.json`, `ionomos_run`, log/lock/pid names, the task
  or appdata names. Don't remove the legacy names: the lab PC has LabWatch data.
- Releases: bump `__version__` + `pyproject.toml`, push, wait for Windows CI,
  then `git tag vX.Y.Z && git push --tags` (builds + tests the installer).
