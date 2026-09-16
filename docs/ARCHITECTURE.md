# Architecture

## Components

```
 labwatch (one long-running Python process, started at login by Task Scheduler)
 ┌────────────────────────────────────────────────────────────────────────────┐
 │                                                                            │
 │  watcher ──▶ intake ──▶ ledger(SQLite) ◀── worker ──▶ runners ──▶ postproc │
 │   poll       validate     job rows          one job    fragpipe   isodtb   │
 │   inbox      parse names  + status          at a time  headless   tmt      │
 │   stability  move to      transitions                             dia      │
 │              user dir                                                      │
 │                                                                            │
 │                              cli: labwatch run | status | dry-run | retry  │
 └────────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility | Reuses |
|---|---|---|
| `config.py` | Load + validate `config.yaml`; resolve per-method defaults | `prior-work/config_loader.py` |
| `watcher.py` | Poll the inbox; detect new **folders**; wait for copy to finish | `prior-work/watcher.py` (size-stability idea, generalised to a tree) |
| `naming.py` | Pure functions: folder name → fields; raw filename → (sample, rep, fraction) | — |
| `manifest.py` | Read/validate `experiment.yaml`; build `.fp-manifest` + TMT `annotation.txt` | `prior-work/fragpipe_runner.build_manifest` |
| `intake.py` | Validate a stable folder, move it to the user dir, write `labwatch.json`, insert ledger row; hand unresolvable names to the resolver | — |
| `resolve.py` | tkinter window for fixing user/method/file tails; writes `experiment.yaml` + learned aliases | — |
| `testbed.py` | Fake lab + sample drops + fake FragPipe for testing on any OS | — |
| `app.py` | tkinter setup wizard / control panel: folders, users, methods, every parameter, start/stop, startup task, testbed | — |
| `configio.py` | config.yaml as a dict; writes a commented file | — |
| `service.py` | child processes, PID file, Task Scheduler, remembered config path, exe routing; dev install: git update, diagnostics bundle | — |
| `ledger.py` | SQLite job table + status transitions; source of truth for "what's queued" | `prior-work/store.py` |
| `worker.py` | Pull next `queued` job, run it, record result. Sequential. | `prior-work/queue_worker.py` |
| `runners/fragpipe.py` | Build the headless command, run with timeout, tee log | `prior-work/fragpipe_runner.py` (nearly as-is) |
| `runners/isodtb.py` | Post-proc: modified-peptide → site merge | port of `lab-scripts/isoDTB_…R` |
| `runners/tmt.py` | Post-proc: experimental annotation fix | port of `lab-scripts/correct_experimental_annotation…R` |
| `runners/dia.py` | Post-proc: (TBD — probably nothing beyond copying `report.tsv` up) | — |
| `cli.py` | `labwatch setup / run / check / status / dry-run / retry / testbed / diagnose / update`; no args → app | — |

## Data flow for one job

```
 1. user drags  C:\Fragpipe_Auto\inbox\20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h\
                                          ├─ EJQ_PK_..._1_1.raw
                                          ├─ …
                                          └─ EJQ_PK_..._3_7.raw

 2. watcher     sees new dir. Every poll, walk the tree, sum (size, mtime) of
                all files. When unchanged for `stable_seconds` (default 60) AND
                ≥1 .raw present → hand to intake.

 3. intake      parse name → reject? write inbox\<name>.REJECTED.txt, stop.
                parse raws → reject if no raws / uneven fractions.
                dest = C:\Fragpipe_General\EJQ\20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h\
                reject if dest exists (never overwrite).
                move (os.replace; same volume ⇒ atomic rename. cross-volume
                ⇒ copy+verify+delete — see DECISIONS).
                write dest\labwatch.json  {status: queued, parsed fields, …}
                insert ledger row.

 4. worker      next queued job →
                  status=running
                  write dest\fragpipe\fragpipe-files.fp-manifest
                  (TMT) write dest\annotation.txt per plex
                  run fragpipe.exe --headless --workflow <wf> --manifest <mf>
                      --workdir dest\fragpipe --threads N --ram G
                  tee → dest\fragpipe\labwatch_fragpipe.log
                  exit 0 → postproc(method) → status=done
                  else   → status=failed, reason in labwatch.json + ledger

 5. user        opens their folder, sees labwatch.json / DONE.txt / FAILED.txt
```

## On-disk layout (proteomics PC)

```
C:\Fragpipe_Auto\                    ← the app lives here (no spaces!)
  inbox\                             ← THE drop folder. Users only touch this.
  labwatch\                          ← the installed package + venv
  config.yaml
  workflows\                         ← pinned .workflow files, one per method
    isoDTB.workflow
    TMT10-MS3.workflow
    DIA.workflow
  fasta\                             ← pinned databases with decoys
  logs\
    labwatch.log                     ← rotating
  labwatch.db                        ← SQLite ledger

C:\Fragpipe_General\<user>\<experiment>\    ← where jobs land
  *.raw                              ← moved as-is (or raw\ if user made one)
  experiment.yaml                    ← if the user wrote one
  labwatch.json                      ← status + provenance, rewritten on every transition
  fragpipe\                          ← --workdir; all FragPipe output
    fragpipe-files.fp-manifest
    fragpipe.workflow                ← FragPipe copies the workflow used here
    labwatch_fragpipe.log
    combined_modified_peptide_label_quant.tsv   (isoDTB)
    tmt-report\abundance_gene_MD.tsv            (TMT)
    report.tsv / diann-output\                   (DIA)
  results\                           ← post-processing output
    <experiment>_sites.tsv           (isoDTB)
    experimental_annotation.tsv      (TMT)
  DONE.txt | FAILED.txt              ← human-facing one-line status
```

## Job state machine

```
            ┌──────────┐  name/raw invalid  ┌──────────┐ human answers ┌──────────┐
 detected ─▶│stabilising│ ─────────────────▶│ resolver │──────────────▶│ (re-plan)│
            └──────────┘                    └──────────┘  skip / no GUI └──────────┘
                 │ stable + valid, moved          │                          │
                 │                                ▼                          │
                 │                          ┌──────────┐  folder changed or  │
                 │                          │ rejected │  note deleted ──────┘
                 │                          └──────────┘ (stays in inbox + .REJECTED.txt)
                 ▼
            ┌──────────┐    worker picks     ┌──────────┐   exit 0 + postproc ok   ┌──────┐
            │  queued  │ ───────────────────▶│ running  │ ────────────────────────▶│ done │
            └──────────┘                    └──────────┘                          └──────┘
                 ▲                                │ non-zero / timeout / postproc error
                 │  labwatch retry <id>           ▼
                 └─────────────────────────  ┌──────────┐
                                             │  failed  │
                                             └──────────┘
```

Persisted in both `labwatch.db` (queryable) and `labwatch.json` (visible to
the user next to their data). On startup, any job found `running` in the
ledger is set to `failed` with reason `interrupted` — FragPipe was killed with
the process, and its workdir may be half-written.

## Configuration (`config.yaml`)

```yaml
paths:
  inbox:        C:/Fragpipe_Auto/inbox
  users_root:   C:/Fragpipe_General          # dest = users_root/<user>/<experiment>
  fragpipe_exe: C:/FragPipe/FragPipe-24.0/fragpipe/bin/fragpipe.exe   # CONFIRM on install
  workflow_dir: C:/Fragpipe_Auto/workflows
  fasta_dir:    C:/Fragpipe_Auto/fasta
  database:     C:/Fragpipe_Auto/labwatch.db
  log_dir:      C:/Fragpipe_Auto/logs

watcher:
  poll_seconds: 10
  stable_seconds: 60        # drags of ~20 GB over USB/SMB can pause; be generous
  min_raw_files: 1

fragpipe:
  threads: 28               # leave some for the OS; machine has 32 logical
  ram_gb: 48                # of 64
  timeout_minutes: 240      # 3×7 isoDTB takes 30–60 min; TMT phospho can be longer
  config_diann: C:/DIA-NN/2.3.2/DiaNN.exe   # only if FragPipe can't find its bundled one

users:
  aliases:                  # initials / alternate spellings -> folder under users_root
    Isaac: [IJ, IJD]
    EJQ:   [EJQ_2]
  default: ""               # set to e.g. "_unsorted" to file unrecognised users there instead of rejecting
  # learned_aliases_file: C:/Fragpipe_Auto/learned_aliases.yaml   (written by the resolver window)

gui:
  enabled: true             # open the resolver window on naming problems (needs an interactive session)
  timeout_minutes: 0        # 0 = wait for a human; N = auto-skip (reject with note) after N minutes

methods:                    # keyed by canonical METHOD keyword; aliases are matched in folder names
  isoDTB:
    aliases: [isodtb, iso-dtb]
    workflow: isoDTB.workflow
    fasta:    human_reviewed_2025-01_decoys.fas
    data_type: DDA
    postprocess: [isodtb_sites]
    isodtb_mod_mass: "561.3387"   # the R script's hard-coded label mass
  TMT:
    workflow: TMT10-MS3.workflow
    fasta:    human_reviewed_2025-01_decoys.fas
    data_type: DDA
    postprocess: [tmt_annotation]
  DIA:
    workflow: DIA.workflow
    fasta:    human_reviewed_2025-01_decoys.fas
    data_type: DIA
    postprocess: []
```

Note: the FASTA path lives **inside** the `.workflow` file, not on the command
line. `fasta:` here is used to (a) sanity-check the workflow file references
that database and (b) record provenance. See WORKFLOWS.md.

## Failure handling principles

- **Never delete user data.** Rejected folders stay in the inbox. Failed jobs
  stay in the user dir with the log. Only `labwatch retry` re-runs.
- **Never overwrite.** If the destination exists, reject and say so.
- **Every failure is written where the user will look** (`FAILED.txt` next to
  their raws), not only in a log they'll never open.
- **Sequential.** One FragPipe at a time; the queue is the ledger.
- **Idempotent restart.** Inbox is re-scanned on start; the ledger says what's
  already been taken.
