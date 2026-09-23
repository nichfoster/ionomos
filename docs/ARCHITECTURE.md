# Architecture

## Components

```
 ionomos (one long-running Python process, started at login by Task Scheduler)
 ┌────────────────────────────────────────────────────────────────────────────┐
 │                                                                            │
 │  watcher ──▶ intake ──▶ ledger(SQLite) ◀── worker ──▶ runners ──▶ postproc │
 │   poll       validate     job rows          one job    fragpipe   isodtb   │
 │   inbox      parse names  + status          at a time  headless   tmt      │
 │   stability  move to      transitions                             dia      │
 │              user dir                                                      │
 │                                                                            │
 │                              cli: ionomos run | status | dry-run | retry  │
 └────────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility | Reuses |
|---|---|---|
| `config.py` | Load + validate `config.yaml`; resolve per-method defaults | `prior-work/config_loader.py` |
| `watcher.py` | Poll the inbox; detect new **folders**; wait for copy to finish | `prior-work/watcher.py` (size-stability idea, generalised to a tree) |
| `naming.py` | Pure functions: folder name → fields; raw filename → (sample, rep, fraction) | — |
| `manifest.py` | Read/validate `experiment.yaml`; build `.fp-manifest` + TMT `annotation.txt` | `prior-work/fragpipe_runner.build_manifest` |
| `intake.py` | Validate a stable folder, move it to the user dir, write `ionomos.json`, insert ledger row; hand unresolvable names to the resolver | — |
| `resolve.py` | tkinter window for fixing user/method/file tails; writes `experiment.yaml` + learned aliases | — |
| `testbed.py` | Fake lab + sample drops + fake FragPipe for testing on any OS | — |
| `app.py` | tkinter setup wizard / control panel: folders, users, methods, every parameter, start/stop, startup task, testbed | — |
| `configio.py` | config.yaml as a dict; writes a commented file | — |
| `service.py` | child processes, PID file, Task Scheduler, remembered config path, exe routing; dev install: git update, diagnostics bundle | — |
| `ledger.py` | SQLite job table + status transitions; source of truth for "what's queued" | `prior-work/store.py` |
| `worker.py` | Thread inside `ionomos run`: first runnable `queued` job → FragPipe → done/failed; holds jobs whose setup files are missing. Sequential. | `prior-work/queue_worker.py` |
| `fragpipe.py` | Prepare a job (launcher, workflow with `database.db-path` patched to the method's FASTA, manifest, TMT annotation), run headless with timeout/stop, kill the process tree | `prior-work/fragpipe_runner.py` |
| `postprocess.py` | After a done job: runs `downstream` (or only the R-port prep steps when analysis is off); `ionomos analyze` uses it too | — |
| `downstream/` | FragPipe tables → `QuantMatrix` → statistics → SVG plots + `results/report.html`. `isodtb.py`/`tmt.py` (R ports), `quant.py` (loaders), `analysis.py` (comparisons), `stats.py` (t-tests, limma-style moderated t, BH), `charts.py`, `report.py`, `simulate.py` (realistic test data with planted truth) | the lab's R scripts; limma |
| `setupcheck.py` | The setup checklist (app ✓ Setup tab, `ionomos init`) | — |
| `names.py` | Every on-disk / system name, with its LabWatch-era twin; readers accept both, writers use the new one | — |
| `buildinfo.py` | `Ionomos 0.5.0 (build 3f2a9c1, date, installed)` — `--version`, app footer, every report | — |
| `updates.py` | Finds a newer `Ionomos-Setup-*.exe` in Downloads, stops the watcher, runs the installer, restarts the watcher after | — |
| `tkutil.py` | Tk variables that can be garbage-collected on any thread (plain ones abort the process on Windows) | — |
| `health.py` | Failsafes: single-instance lock, heartbeat, thread supervisor, crash hooks/files, disk/RAM facts, log-problem extraction | — |
| `stress.py` | `ionomos testbed stress`: messy drops + chaos against a real watcher/worker, invariant checks; name fuzzer | — |
| `runners/isodtb.py` | Post-proc: modified-peptide → site merge | port of `lab-scripts/isoDTB_…R` |
| `runners/tmt.py` | Post-proc: experimental annotation fix | port of `lab-scripts/correct_experimental_annotation…R` |
| `runners/dia.py` | Post-proc: (TBD — probably nothing beyond copying `report.tsv` up) | — |
| `cli.py` | `ionomos setup / run / check / status / dry-run / retry / testbed / diagnose / update`; no args → app; hidden `fake-fragpipe` for the testbed | — |

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
                write dest\ionomos.json  {status: queued, parsed fields, …}
                insert ledger row.

 4. worker      first runnable queued job (a job whose launcher/workflow/FASTA
                is missing is *held*: stays queued with "waiting: …")  →
                  status=running, attempts+1
                  move an old non-empty dest\fragpipe\ aside (fragpipe_previous_<ts>)
                  write dest\ionomos_run\fragpipe-files.fp-manifest
                  write dest\ionomos_run\<method>.workflow  (database.db-path = method FASTA)
                  (TMT) write annotation.txt next to the raws (never over a user's own)
                  run fragpipe.bat --headless --workflow <wf> --manifest <mf>
                      --workdir dest\fragpipe --threads N --ram G
                  tee → dest\ionomos_run\fragpipe_console.log
                  exit 0 + output → postproc(method) → status=done, DONE.txt
                  else / timeout  → status=failed, FAILED.txt, reason in ionomos.json + ledger
                  ionomos stopped → FragPipe tree killed, job back to queued

 5. user        opens their folder, sees ionomos.json / DONE.txt / FAILED.txt
```

## On-disk layout (proteomics PC)

```
C:\Fragpipe_Auto\                    ← the app lives here (no spaces!)
  inbox\                             ← THE drop folder. Users only touch this.
  ionomos\                          ← the installed package + venv
  config.yaml
  workflows\                         ← pinned .workflow files, one per method
    isoDTB.workflow
    TMT10-MS3.workflow
    DIA.workflow
  fasta\                             ← pinned databases with decoys
  logs\
    ionomos.log                     ← rotating
  ionomos.db                        ← SQLite ledger

C:\Fragpipe_General\<user>\<experiment>\    ← where jobs land
  *.raw                              ← moved as-is (or raw\ if user made one)
  experiment.yaml                    ← if the user wrote one
  ionomos.json                      ← status + provenance, rewritten on every transition
  ionomos_run\                      ← what ionomos gave FragPipe
    fragpipe-files.fp-manifest
    <method>.workflow                ← pinned workflow, database.db-path set
    fragpipe_console.log             ← FragPipe's console output (all attempts)
  fragpipe\                          ← --workdir; all FragPipe output
    fragpipe.workflow                ← FragPipe copies the workflow used here
  fragpipe_previous_<ts>\            ← an earlier attempt's output (never deleted)
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
                 │  ionomos retry <id>           ▼
                 └─────────────────────────  ┌──────────┐
                                             │  failed  │
                                             └──────────┘
```

Persisted in both `ionomos.db` (queryable) and `ionomos.json` (visible to
the user next to their data). A job interrupted by a stop, crash or reboot
(found `running` on startup) goes back to `queued` and re-runs from scratch,
its half-written workdir moved aside; after 3 starts it is `failed` instead so
a job that takes the PC down can't loop. `ionomos retry` resets the count.

## Configuration (`config.yaml`)

```yaml
paths:
  inbox:        C:/Fragpipe_Auto/inbox
  users_root:   C:/Fragpipe_General          # dest = users_root/<user>/<experiment>
  fragpipe_exe: C:/FragPipe/FragPipe-24.0/fragpipe/bin/fragpipe.exe   # CONFIRM on install
  workflow_dir: C:/Fragpipe_Auto/workflows
  fasta_dir:    C:/Fragpipe_Auto/fasta
  database:     C:/Fragpipe_Auto/ionomos.db
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
  stay in the user dir with the log. Only `ionomos retry` re-runs.
- **Never overwrite.** If the destination exists, reject and say so.
- **Every failure is written where the user will look** (`FAILED.txt` next to
  their raws), not only in a log they'll never open.
- **Sequential.** One FragPipe at a time; the queue is the ledger.
- **Idempotent restart.** Inbox is re-scanned on start; the ledger says what's
  already been taken.

## Failsafes (0.3.0)

| Threat | Defence |
|---|---|
| Two watchers on one inbox (startup task + app, two users) | OS file lock `logs/ionomos.lock`; the second exits with code 3 |
| A loop crashes on a bug | `health.supervise` restarts it with backoff; `crash-*.txt` in `logs/`; excepthooks for every thread |
| A loop hangs | `logs/heartbeat.json` per part; app / `status` / diagnose show NOT RESPONDING after 90 s |
| Stop / update mid-search | `logs/STOP` → graceful shutdown: FragPipe tree killed, job re-queued; fallback `taskkill /T` (never a bare terminate, which orphans Java on Windows) |
| Ledger corrupt | integrity check at start → moved aside, rebuilt from every `ionomos.json`; daily backups in `logs/backups/`; `ionomos repair-ledger` |
| Ledger locked/unwritable right after a move | intake still reports QUEUED; `adopt_orphans` at start and hourly re-creates the job from `ionomos.json` |
| Folder can never be parsed (symbols-only names, a bug) | `intake()` never raises: transient OS errors → RETRY, anything else → `.REJECTED.txt` (no infinite retry loop) |
| Sanitised names collide | `plan()` rejects duplicate final names (case-insensitive); renames refuse to overwrite |
| Disk nearly full | a search is held ("waiting: low disk space") until `min_free_gb` + its raws are free |
| Inbox share disappears | logged once, watched until it's back |
| Invalid config saved from the app | validated as a candidate file first; the good file is never replaced; every save backed up to `config-backups/` |
| FragPipe says exit 0 but a step failed / wrote nothing | parsed from the console (`Process 'X' finished, exit code: N`) → failed |
| A GUI button throws | `report_callback_exception` → dialog + crash file; the app keeps running |
| `check`/diagnose on a wedged display | the Tk probe runs in a child process with a timeout |

## Downstream pipeline (0.4.0)

```
fragpipe/ ──▶ method prep ──────────▶ QuantMatrix ─────▶ statistics ──────────▶ presentation
              isodtb.py  (R port)      features×samples   analysis.choose_       charts.py  (SVG)
              tmt.py     (R port)      log2, condition     comparisons            report.py  (HTML)
              quant.py   (loaders)     per sample          stats (moderated t,
                                                           Welch, BH)
                                                  results/: report.html, *_differential.tsv,
                                                  volcano_*.svg, *_matrix_log2.tsv, analysis.json
```

Each stage only knows the one before it. New method = one loader; new plot =
one function in `charts.py` + one line in `report.py`. The whole pipeline is
wrapped so it can never fail a job or crash the watcher: problems become
notes in the report and warnings on the job, and `ionomos analyze` (or Jobs →
Re-run analysis) redoes it with new settings. Settings: `config.yaml
analysis:` (lab defaults, app tab 7) overridden by `experiment.yaml
analysis:` (per experiment).

## Packaging, updates, support (0.5.0)

```
git tag vX.Y.Z ──▶ GitHub Actions (Windows) ──▶ PyInstaller: Ionomos.exe + ionomos-cli.exe (build stamped)
                                             ├─▶ Inno Setup: Ionomos-Setup-X.Y.Z.exe  (deploy/ionomos.iss)
                                             ├─▶ tests: frozen pipeline + stress; install, retire LabWatch,
                                             │          upgrade, uninstall with data kept
                                             └─▶ Release: Setup.exe + portable zip

PC: C:\Ionomos\          program only (replaced by updates, removed by uninstall)
    C:\Fragpipe_Auto\    config.yaml, ledger, logs      (never touched by setup/uninstall)
    C:\Fragpipe_General\ experiments                     (never touched)
    %APPDATA%\Ionomos\   remembered config path, app.log
```

Update: app finds the downloaded Setup → `request_stop` (graceful; running
search re-queued) → `RESTART_WATCHER` note → silent Setup → app reopens →
watcher restarted. Report: **Report a problem…** → `save_problem_report` →
`Ionomos-report-<ts>-v<ver>.zip` on the Desktop (note, build.json, report.txt,
config, watcher + app logs, crash files, problem jobs' FragPipe logs; never
raw data) → selected in Explorer. Detailed logging: `logs/DEBUG_UNTIL`,
honoured by the running watcher within 30 s, expires by itself.
