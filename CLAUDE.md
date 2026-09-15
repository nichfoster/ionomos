# lab-informatics — notes for Claude

Read `README.md` then `docs/` before touching code. The docs are the spec;
keep them in sync with code changes (especially NAMING_CONVENTION.md ↔
`labwatch/src/labwatch/naming.py`).

- Target machine is a Windows 11 lab PC (see docs/PROTEOMICS_PC.md). Code is
  developed on macOS; anything path-related must work on both.
- FragPipe forbids spaces in paths. Never introduce a default path with spaces.
- Never write code that deletes or overwrites user experiment data. Intake
  moves; failures leave everything in place.
- `reference/` is read-only lab material (SOPs, R scripts, inventory output).
  Don't edit it; port from it.
- Tests: `cd labwatch && .venv/bin/pytest`. Add table-driven cases using real
  names from `reference/pc-inventory/` when extending `naming.py`.
- Log decisions in `docs/DECISIONS.md`; open questions in `docs/ROADMAP.md`.
