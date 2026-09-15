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

**Phase 0 — planning.** Nothing runs yet. The repo holds the design docs, the
package skeleton, and the reference material gathered from the lab PC. See
[docs/ROADMAP.md](docs/ROADMAP.md) for what's next and
[docs/SCOPE.md](docs/SCOPE.md) for what is and isn't in scope.

## Layout

| Path | What |
|---|---|
| [`docs/`](docs/) | Design docs — read these first |
| [`labwatch/`](labwatch/) | The watcher package (Python 3.11+, installed on the proteomics PC) |
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

## Quick start (dev, on this Mac)

```bash
cd labwatch && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && pytest
```

Deployment on the proteomics PC is described in [labwatch/README.md](labwatch/README.md).
