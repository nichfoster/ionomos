# Ionomos

**Ionomos** automates the lab's proteomics PC. Lab members drag an experiment
folder into an inbox; Ionomos files it into the owner's directory, runs
FragPipe (isoDTB / TMT / DIA) headlessly, reproduces the lab's R post-processing,
and writes statistics, volcano plots and a report — work that today is done by
hand. (Called *LabWatch* up to 0.4.0; existing installs upgrade in place.)

**Install / update / report a problem / uninstall on the PC:** see the table
at the top of [docs/DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md) — it's one
installer, one button, one button, and Settings → Apps.

```
Eclipse PC                    shared folder                 Proteomics PC
┌──────────────┐   ethernet   ┌──────────────────┐  drag   ┌─────────────────────────────┐
│ instrument   │ ──────────▶  │ Proteomics_File_ │ ──────▶ │ C:\Fragpipe_Auto\inbox\     │
│ writes .raw  │  user copies │ Sharing\         │  user   │   └─ <named experiment dir> │
└──────────────┘              └──────────────────┘         │            │ ionomos        │
                                                           │            ▼                 │
                                                           │ C:\Fragpipe_General\<user>\  │
                                                           │   └─ <experiment>\           │
                                                           │        ├─ raw\               │
                                                           │        ├─ fragpipe\  (out)   │
                                                           │        └─ ionomos.json      │
                                                           │            │                 │
                                                           │            ▼                 │
                                                           │ FragPipe headless → post-proc│
                                                           └─────────────────────────────┘
```

## Status

**Phase 2 in progress — automatic FragPipe searches built, awaiting the first
real run on the PC.** Folders dropped in the inbox are detected, interpreted
(flexible naming + a small resolver window for the rest), filed into the
owner's directory, then searched with headless FragPipe one at a time
(`DONE.txt` / `FAILED.txt` + retry). A tkinter app (`Ionomos.exe`, or
`ionomos setup`) is the setup wizard and control panel — folders, users,
methods, every parameter, start/stop, job status, startup task, and a built-in
testbed with a fake FragPipe. After each search: the lab's R-script outputs
(byte-identical Python ports) and FragPipe-Analyst's analysis — filtering,
normalisation, imputation, limma, PCA/QC, enrichment — ported from
FragPipeAnalystR and checked against it, in an interactive, self-contained
`results/report.html`. The app's Analysis tab re-runs any experiment with
other conditions, samples or comparisons. See
[docs/ROADMAP.md](docs/ROADMAP.md), and [docs/DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md)
to put it on the PC.

## Layout

| Path | What |
|---|---|
| [`docs/`](docs/) | Design docs — read these first |
| [`ionomos/`](ionomos/) | The watcher package (Python 3.11+, installed on the proteomics PC) |
| [`deploy/`](deploy/) | `dev_install.ps1` (PC runs from a git clone; updates = one button), `build_exe.ps1` (PyInstaller → `Ionomos.exe`, also run by GitHub Actions), `install.ps1` fallback |
| [`.github/workflows/`](.github/workflows/) | CI: tests on Linux + Windows every push; exe build + Release on `v*` tags |
| [`scripts/`](scripts/) | One-shot dev/test setup for macOS and Windows |
| [`tools/inventory/`](tools/inventory/) | PowerShell inventory collector to run on the lab PC (fixed version) |
| [`reference/pc-inventory/`](reference/pc-inventory/) | Raw output of the inventory runs (2026-09-15) |
| [`reference/lab-sops/`](reference/lab-sops/) | The lab's how-to docs for isoDTB and TMT in FragPipe (+ extracted text) |
| [`reference/lab-scripts/`](reference/lab-scripts/) | The R post-processing scripts ionomos will replace/wrap |
| [`reference/prior-work/proteomics-qc-pkg/`](reference/prior-work/proteomics-qc-pkg/) | Earlier QC-trending pipeline; its FragPipe runner and stability watcher are reused |

## Docs index

| Doc | Purpose |
|---|---|
| [SCOPE.md](docs/SCOPE.md) | Problem statement, in/out of scope, users, constraints |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow, job state machine, on-disk layout |
| [NAMING_CONVENTION.md](docs/NAMING_CONVENTION.md) | The folder/file naming spec users must follow + the optional `experiment.yaml` |
| [WORKFLOWS.md](docs/WORKFLOWS.md) | What isoDTB, TMT and DIA runs each need (from the lab SOPs) and how they map to headless FragPipe |
| [PROTEOMICS_PC.md](docs/PROTEOMICS_PC.md) | Facts about the target machine from the inventory, and what's still unknown |
| [DECISIONS.md](docs/DECISIONS.md) | Decision log (why polling, why folder-level, why Python not R, …) |
| [ROADMAP.md](docs/ROADMAP.md) | Phased build plan and open questions to resolve with the lab |
| [DEV_LOOP.md](docs/DEV_LOOP.md) | **Start here for prototyping:** Mac ↔ GitHub ↔ PC loop, updates and diagnostics |
| [DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md) | Step-by-step install of the finished tool on the proteomics PC |
| [TESTING.md](docs/TESTING.md) | Test suite + testbed on macOS and Windows |

## Quick start (dev, on this Mac)

```bash
scripts/test_mac.sh --bed        # venv + lint + tests + a fake lab in ./ionomos-testbed
ionomos/.venv/bin/ionomos setup   # the app, pointed at any config you like
```

On the PC: [docs/DEV_LOOP.md](docs/DEV_LOOP.md) while prototyping,
[docs/DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md) for the finished tool.

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)). The analysis in
`ionomos/src/ionomos/downstream/fpa.py`, `qc.py` and `enrich.py` is a Python
translation of [FragPipeAnalystR](https://github.com/Nesvilab/FragPipeAnalystR)
and [FragPipe-Analyst](https://github.com/MonashProteomics/FragPipe-Analyst)
(GPL-3); please cite Hsiao et al., *J. Proteome Res.* 2024,
doi:10.1021/acs.jproteome.4c00294, and limma (Ritchie et al., *Nucleic Acids Res.* 2015).
