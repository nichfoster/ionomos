# lab-informatics

Automation for the lab's proteomics PC. The first (and main) deliverable is
**labwatch**: a drop-folder watcher that picks up experiment folders dragged in
by lab members, validates them, files them into the owner's working directory,
and hands them to FragPipe (isoDTB / TMT / DIA) headlessly — then runs the
lab's post-processing scripts that today are done by hand.

```
Eclipse PC                    shared folder                 Proteomics PC
┌──────────────┐   ethernet   ┌──────────────────┐  drag   ┌─────────────────────────────┐
│ instrument   │ ──────────▶  │ Proteomics_File_ │ ──────▶ │ C:\Fragpipe_Auto\inbox\     │
│ writes .raw  │  user copies │ Sharing\         │  user   │   └─ <named experiment dir> │
└──────────────┘              └──────────────────┘         │            │ labwatch        │
                                                           │            ▼                 │
                                                           │ C:\Fragpipe_General\<user>\  │
                                                           │   └─ <experiment>\           │
                                                           │        ├─ raw\               │
                                                           │        ├─ fragpipe\  (out)   │
                                                           │        └─ labwatch.json      │
                                                           │            │                 │
                                                           │            ▼                 │
                                                           │ FragPipe headless → post-proc│
                                                           └─────────────────────────────┘
```

## Status

**Phase 1 — watcher + intake + setup app, ready to deploy.** Folders dropped
in the inbox are detected, interpreted (flexible naming + a small resolver
window for the rest), filed into the owner's directory and queued. A tkinter
app (`LabWatch.exe`, or `labwatch setup`) is the setup wizard and control
panel — folders, users, methods, every parameter, start/stop, startup task,
and a built-in testbed. FragPipe is not invoked yet (Phase 2). See
[docs/ROADMAP.md](docs/ROADMAP.md), and [docs/DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md)
to put it on the PC.

## Layout

| Path | What |
|---|---|
| [`docs/`](docs/) | Design docs — read these first |
| [`labwatch/`](labwatch/) | The watcher package (Python 3.11+, installed on the proteomics PC) |
| [`deploy/`](deploy/) | `build_exe.ps1` (PyInstaller → `LabWatch.exe`), `install.ps1` fallback, release-zip builder |
| [`scripts/`](scripts/) | One-shot dev/test setup for macOS and Windows |
| [`tools/inventory/`](tools/inventory/) | PowerShell inventory collector to run on the lab PC (fixed version) |
| [`reference/pc-inventory/`](reference/pc-inventory/) | Raw output of the inventory runs (2026-09-15) |
| [`reference/lab-sops/`](reference/lab-sops/) | The lab's how-to docs for isoDTB and TMT in FragPipe (+ extracted text) |
| [`reference/lab-scripts/`](reference/lab-scripts/) | The R post-processing scripts labwatch will replace/wrap |
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
| [DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md) | Step-by-step install on the proteomics PC |
| [TESTING.md](docs/TESTING.md) | Test suite + testbed on macOS and Windows |

## Quick start (dev, on this Mac)

```bash
scripts/test_mac.sh --bed        # venv + lint + 149 tests + a fake lab in ./labwatch-testbed
labwatch/.venv/bin/labwatch setup   # the app, pointed at any config you like
```

Deployment on the proteomics PC is described in [labwatch/README.md](labwatch/README.md).
