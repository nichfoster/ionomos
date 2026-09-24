# Ionomos (nichfoster/ionomos)

Drop-folder watcher that files lab proteomics experiment folders and runs
FragPipe headlessly on a Windows lab PC. Python 3.11+ package lives in
`ionomos/`; `docs/` is the spec — read `README.md` then `docs/` (especially
`ARCHITECTURE.md`, `NAMING_CONVENTION.md`, `TESTING.md`) before touching code.
See `CLAUDE.md` for project rules.

## Stack

- **Language/runtime:** Python 3.11+ (sandbox runs 3.13). No Node, no web server.
- **Packaging:** setuptools, editable install into a venv at `ionomos/.venv` (pip).
- **Lint/tests:** ruff + pytest (`pytest>=8`, `ruff>=0.5` in `[dev]` extras).
- **Storage:** SQLite job ledger (no external DB). No Docker/Compose, no Postgres/Redis.
- **GUI:** tkinter (Windows-first). Tests skip GUI cases when there is no display.
- **External tool:** FragPipe (Windows-only, headless) — NOT present in this
  sandbox. The repo ships a **testbed with a fake FragPipe**; CI and local dev
  use it for all pipeline runs. Set `IONOMOS_FAKE_FP_SECONDS=1` to make the
  fake search fast.
- **Target machine:** Windows 11 lab PC. Never introduce paths with spaces
  (FragPipe rule). Never hard-code on-disk names — use `ionomos/src/ionomos/names.py`.

## Commands

Run from the repo root unless noted. Venv lives at `ionomos/.venv`.

```bash
# setup (once)
python3 -m venv ionomos/.venv
ionomos/.venv/bin/pip install -e "ionomos[dev]"

# lint (repo root, matches CI)
ionomos/.venv/bin/python -m ruff check ionomos/src ionomos/tests

# tests (repo root, matches CI; IONOMOS_OFFLINE=1 keeps tests offline)
IONOMOS_OFFLINE=1 ionomos/.venv/bin/python -m pytest ionomos -q

# primary end-to-end flow: testbed + watcher + fake FragPipe
ionomos/.venv/bin/ionomos testbed init ./ionomos-testbed
nohup env IONOMOS_FAKE_FP_SECONDS=1 ionomos/.venv/bin/ionomos \
  --config ionomos-testbed/Fragpipe_Auto/config.yaml run --no-gui > /tmp/ionomos-watcher.out 2>&1 &
ionomos/.venv/bin/ionomos testbed drop iso_good ./ionomos-testbed
ionomos/.venv/bin/ionomos testbed drop dia_good ./ionomos-testbed
ionomos/.venv/bin/ionomos --config ionomos-testbed/Fragpipe_Auto/config.yaml status --all
ionomos/.venv/bin/ionomos --config ionomos-testbed/Fragpipe_Auto/config.yaml attention
ionomos/.venv/bin/ionomos analyze ionomos-testbed/Fragpipe_General/EJQ/20260902-isoDTB_EJQ-2-027 --test welch
```

`ionomos-testbed/` is gitignored lab-simulation output — safe to delete/reset
(`ionomos testbed reset ./ionomos-testbed`). `config.yaml`, `*.db`, `*.log`,
`*.raw`, `*.mzML` are gitignored runtime artefacts.

## Environment variables

| Var | Purpose |
|---|---|
| `IONOMOS_OFFLINE=1` | Keep the test suite offline (set in CI). |
| `IONOMOS_FAKE_FP_SECONDS` | Seconds the testbed's fake FragPipe "search" takes (e.g. `1`). |

No other required env vars. No `.env` file; runtime config is a YAML file
(`config.yaml`, see `ionomos/config.example.yaml`) living outside the repo.

## Codebase map

See [codebase-map.md](codebase-map.md).

## Local Verification Summary

- dev_stack_healthy: **true** (2026-09-24)
- Editable install: `pip install -e "ionomos[dev]"` → OK (Python 3.13)
- ruff: `All checks passed!`
- pytest: `350 passed, 23 skipped` in ~84s (`IONOMOS_OFFLINE=1`; skipped = GUI
  tests without a display)
- E2E primary flow: testbed init → watcher (`run --no-gui`, fake FragPipe) →
  dropped `iso_good` + `dia_good` → both jobs reached `done` in ~10s →
  results filed to `Fragpipe_General/{EJQ,Isaac}/<experiment>/results/` with
  `report.html` (self-contained, `ionomos-report-v2` marker present),
  `analysis.json`, volcano SVGs, FragPipe-Analyst export for DIA →
  `ionomos attention` exit 0 ("nothing needs attention") →
  `ionomos analyze … --test welch` re-ran analysis and wrote the report, exit 0.
- `ionomos --version` → `Ionomos 0.7.0 (build 780c4f9, 2026-09-23, git checkout)`

## Snapshot

- snapshotId: `oy1evct1w3dtjc3m0x1k:default`
- Captured: 2026-09-24T20:19:29Z
- State: venv installed at `ionomos/.venv`, testbed initialized at
  `./ionomos-testbed`, watcher stopped, git tree clean on `master`.
