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
| `naming.py` | Pure functions: folder name → fields; raw filename → (sample, rep, fraction). Per-method file rules are templates or regexes, date formats a list, both from `config.yaml` `naming:` (D37) | — |
| `namecheck.py` | "Test your names": how the live config reads folder / `.raw` names (`ionomos names test`, app Methods tab → **Test names…**); read-only | — |
| `manifest.py` | Read/validate `experiment.yaml`; build `.fp-manifest` + TMT `annotation.txt` | `prior-work/fragpipe_runner.build_manifest` |
| `intake.py` | Validate a stable folder, show it for review (or hand unresolvable names to the resolver), move it to the user dir, write `ionomos.json`, insert ledger row. The watcher passes a `config.LiveConfig`, so edits to config.yaml apply to the next drop | — |
| `resolve.py` | tkinter window for fixing user/method/file tails; writes `experiment.yaml` + learned aliases | — |
| `testbed.py` | Fake lab + sample drops + fake FragPipe for testing on any OS | — |
| `demo.py` | `ionomos demo`: a simulated DIA experiment (`downstream/simulate.py`, planted hits, on/off proteins and gene-set shifts; bundled `assets/demo_gene_sets.gmt`) written to a new folder, then analysed. Offline, no lab config, no Tk (the pip install; docs/QUICKSTART.md) | — |
| `app.py` | tkinter setup wizard / control panel: folders, users, methods, every parameter, start/stop, startup task, testbed | — |
| `configio.py` | config.yaml as a dict; writes a commented file | — |
| `service.py` | child processes, PID file, Task Scheduler, remembered config path, exe routing; dev install: git update, diagnostics bundle | — |
| `ledger.py` | SQLite job table + status transitions; source of truth for "what's queued" | `prior-work/store.py` |
| `worker.py` | Thread inside `ionomos run`: first runnable `queued` job → FragPipe or DIA-NN (runner.py) → done/failed; holds jobs whose setup files are missing. Sequential. | `prior-work/queue_worker.py` |
| `fragpipe.py` | Prepare a job (launcher, workflow with `database.db-path` patched to the method's FASTA, manifest, TMT annotation), run headless with timeout/stop, kill the process tree | `prior-work/fragpipe_runner.py` |
| `maxquant.py` | `engine: maxquant` methods: the lab's `mqpar` or MaxQuant's own `--create` template, patched with the job's raws, experiments, fractions, FASTA, threads and output folder into `ionomos_run/mqpar.xml`; output in `maxquant/` (D50) | — |
| `sage.py` | `engine: sage` methods: `ionomos_run/sage.json` (the lab's Sage JSON or Ionomos' defaults, with the job's FASTA, mzML paths and output folder) and `sage_job.json`; the job runs as `ionomos sage-job`, one process that converts each `.raw` to `sage_mzml/*.mzML` with ThermoRawFileParser (reused on retry) and then starts Sage with its telemetry off; output in `sage/` (D51) | — |
| `diann.py`, `runner.py` | `engine: diann` methods: prepare a DIA-NN job (the lab's `diann_exe`, FASTA or spectral library, `ionomos_run/diann.cfg`), output in `diann/`; `runner` picks FragPipe, DIA-NN, MaxQuant or Sage per method, and all use `fragpipe.run`'s start / cancel / stop / timeout loop (D39) | — |
| `postprocess.py` | After a done job: runs `downstream` (or only the R-port prep steps when analysis is off); `ionomos analyze` and the Analysis tab use it too (`prepare`, `inspect_folder`); then instrument QC trending for jobs with QC-standard runs | — |
| `qctrend.py` | Instrument QC trending (D45, [QC_TREND.md](QC_TREND.md)): which runs are the QC standard (`qc_trend.match` / `methods`), the metric store `<log_dir>/qc_trend.jsonl`, per-series baselines, Levey-Jennings z-scores, Westgard rules + CUSUM, plain-English verdicts, a `qc_trend` attention item; `after_job` (postprocess hook, never raises), `scan` (read-only, `ionomos qc-trend --rebuild`), `page_for` (the app's **Jobs → Instrument QC**). Metrics from the searches' own tables (`downstream/qcmetrics.py`: DIA-NN `stats.tsv` / `pg_matrix` / `report.tsv`, FragPipe `psm.tsv` / `combined_protein.tsv`, streamed and size-bounded); the page `<log_dir>/qc_trend.html` (`downstream/qcpage.py`: static SVG, report.css, no script) | `prior-work/parsers/`, `store.py` |
| `analysis_tab.py` | App tab 7: analyse one experiment (samples, conditions, comparisons → experiment.yaml, Run) and the lab defaults | — |
| `experiment_editor.py` | One experiment's analysis choices as a Tk panel (samples, conditions, comparisons, cut-offs, Run, issues); used by tab 7 and the pop-ups | — |
| `attention.py` | Durable "needs a person" queue (`<log_dir>/attention/*.json`): raised by intake, worker, analysis, QC trending (`qc_trend`, a warning: no pop-up unless `qc_trend.popup`); closed when fixed | — |
| `popups.py` | Pop-up windows + the "needs attention" list, in the app or (app closed) the watcher's own Tk loop; **More help** opens the help page at the item's topic | — |
| `help/` | The help for users (D46, HELP.md): `*.md` content (getting started, the report, glossary, troubleshooting, never-do, FAQ) parsed and rendered with the stdlib; `report_payload()` for every report, `page()` = `help.html` (`ionomos help`, the app's Help button, pop-ups), `text()` for the terminal, `topic()` / `topic_for_item()` | — |
| `downstream/doctor.py` | The analysis check-up: issues with severity, likely causes, fixes; condition suggestions from file names | — |
| `downstream/` | FragPipe tables → `QuantMatrix` → FragPipe-Analyst processing + limma → interactive `results/report.html`. `isodtb.py`/`tmt.py` (R ports), `quant.py` (loaders), `engines.py` (other engines' outputs — DIA-NN standalone incl. 2.x Parquet, MaxQuant, Spectronaut, AlphaDIA, MSstats and MSstatsTMT format, Proteome Discoverer — and the provenance of any result; ENGINES.md, D36), `fpa.py` (FragPipeAnalystR port: filter, normalise, impute, `test_limma`), `design.py` (limma designs with blocks and covariates, the moderated F; D42), `deqms.py` (R's loess and DEqMS' peptide-count prior; D43), `doseresponse.py` (CurveCurator's dose-response curves for titrations; D44), `sdrfdesign.py` (an input SDRF as the design; D47), `plex.py` (IRS across TMT plexes; D48), `rrandom.py` (R's RNG), `analysis.py` (settings, comparisons), `qc.py` (PCA, clustering, CV, missingness), `insights.py` (sample scorecard, PC ↔ condition/replicate, missingness vs intensity, p-value shape + π0, on/off features, imputation-driven hits, power; D35), `enrich.py` (local ORA and a correlation-adjusted rank test on Enrichr libraries), `export.py` (result tables, FragPipe-Analyst annotation + R script), `sdrf.py` (SDRF-Proteomics sample metadata; D38), `report.py` + `assets/report.js`, `charts.py` (static SVG volcano), `stats.py`, `simulate.py` | the lab's R scripts; FragPipeAnalystR; limma |
| `setupcheck.py` | The setup checklist (app ✓ Setup tab, `ionomos init`) | — |
| `names.py` | Every on-disk / system name, with its LabWatch-era twin; readers accept both, writers use the new one | — |
| `buildinfo.py` | `Ionomos 0.5.0 (build 3f2a9c1, date, installed)` — `--version`, app footer, every report | — |
| `updates.py` | Asks GitHub for the newest release, downloads the Setup (size + SHA-256 verified), or finds one in Downloads; stops the watcher, runs the installer, restarts the watcher after | — |
| `loose.py` | `.raw` files dropped without a folder: grouped by shared name into folders in the inbox once stable | — |
| `tkutil.py` | Tk variables that can be garbage-collected on any thread (plain ones abort the process on Windows) | — |
| `health.py` | Failsafes: single-instance lock, heartbeat, thread supervisor, crash hooks/files, disk/RAM facts, log-problem extraction | — |
| `stress.py` | `ionomos testbed stress`: messy drops + chaos against a real watcher/worker, invariant checks; name fuzzer | — |
| `runners/isodtb.py` | Post-proc: modified-peptide → site merge — thin entry point delegating to `downstream/isodtb.py`, which holds the algorithm | port of `lab-scripts/isoDTB_…R` |
| `runners/tmt.py` | Post-proc: experimental annotation fix | port of `lab-scripts/correct_experimental_annotation…R` |
| `runners/dia.py` | Post-proc: (TBD — probably nothing beyond copying `report.tsv` up) | — |
| `cli.py` | `ionomos setup / run / check / status / dry-run / names test / retry / testbed / diagnose / update / help`; no args → app; hidden `fake-fragpipe` for the testbed | — |

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
    qc_trend.jsonl                  ← instrument QC: one line per QC-standard run (D45)
    qc_trend.html                   ← the QC trend page (Levey-Jennings charts, Westgard rules)
    help\help.html                  ← the help page, rewritten each time it is opened (D46)
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
    report.html, analysis.json, …    (the analysis; see "Downstream pipeline")
    sdrf.tsv                         SDRF-Proteomics sample metadata, one row per raw file (and label)
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
  enabled: true             # the naming window: problems, and a review of every drop (needs an interactive session)
  review_drops: true        # show each drop's reading (conditions, replicates, control) before filing
  timeout_minutes: 0        # 0 = wait for a human; N = a review files as read, a problem is skipped (note)

naming:
  condition_codes: {D: DMSO, C: Compound}   # DIA X_D1 = DMSO rep 1

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
| An analysis stage crashes (QC, enrichment, export, report) | isolated: recorded in `analysis_error.txt` + an issue; the rest (volcanos, tables, report or its fallback page) is still made |
| Instrument QC trending crashes, or a QC table can't be read | isolated after the analysis: a note on the run's row, a log line; the job is done regardless |
| The analysis can't decide (one condition, no control, a group of 1, unmatched runs) | runs on the best guess, then a pop-up with the experiment editor asks; the answer goes to experiment.yaml |
| A search fails / is held / a folder is rejected / a raw file is 0 bytes | an attention item → pop-up with likely causes, log tail, Retry; closes itself when fixed |
| A GUI button throws | `report_callback_exception` → dialog + crash file; the app keeps running |
| `check`/diagnose on a wedged display | the Tk probe runs in a child process with a timeout |

## Downstream pipeline (0.6.0: FragPipe-Analyst)

```
fragpipe/ ─▶ method prep ─▶ QuantMatrix ─▶ fpa.process ────────────────▶ limma ─────────▶ QC + enrichment ─▶ report
             isodtb.py      features ×     samples chosen / renamed      one ~0+condition  PCA, correlation,   report.html (JSON +
             tmt.py         samples, log2  contaminants, % filters       model, eBayes,    missingness, CV,    report.js), TSVs,
             quant.py       + condition    median / GN normalisation     CIs, BH           heatmap, ORA        volcano_*.svg,
                                           Perseus / MinProb / … impute  add_rejections                        fragpipe-analyst/
                                                                                   insights (D35):
                                                                                   scorecard, batch,
                                                                                   missingness, π0,
                                                                                   on/off, power;
                                                                                   rank-based sets
```

The insights stage writes `sample_qc.tsv`, `presence_absence.tsv` and
`gene_set_ranks.tsv`, adds a `quality` block to `analysis.json`, and feeds
the doctor's warnings (`SAMPLE_OUTLIER`, `BATCH_SUSPECT`, …). Like QC, it is
isolated: if it crashes, the volcano plots and the report are still made.

Settings: `config.yaml analysis:` (lab defaults, Analysis tab → Lab defaults)
overridden by `experiment.yaml analysis:` (Analysis tab → Analyse an
experiment writes it: sample conditions, samples left out, comparisons,
cut-offs). Each stage only knows the one before it. The whole pipeline is
wrapped so it can never fail a job or crash the watcher: problems become
notes in the report and warnings on the job, and `ionomos analyze` / Jobs →
Re-run analysis / the Analysis tab redo it with new settings.
`results/fragpipe-analyst/` holds an `experiment_annotation.tsv` and a
`reproduce_in_R.R` that repeat the analysis in FragPipeAnalystR (D24).

**Designs, the F-test and DEqMS** (`downstream/design.py`, `deqms.py`; D42,
D43). With no `block` / `block_from` / `covariates` / `variance_prior` set,
the comparisons are FragPipe-Analyst's `~0 + condition` path, unchanged.
Otherwise `analysis.make_model` builds the design for the processed samples
and the comparisons use limma's general path:

```
settings ─▶ make_model ─────▶ design.build ─▶ X = [condition | block dummies | covariates]
            (block: replicate │                 checks: every sample has a value, full rank, residual df > 0
             / {sample: b} /  │                 fails ─▶ Model.problem ─▶ doctor DESIGN_NOT_USED (input),
             block_from,      │                          plain ~0 + condition used instead
             covariates)      ▼
                   lm_fit (per row, NA dropped, lm.fit pivoting) ─▶ contrasts_fit ─▶ squeeze ─▶ topTable
                                                                                  │ limma: squeezeVar (fpa._ebayes)
                                                                                  │ deqms: loess of log s² on
                                                                                  │  log2 peptide count (DEqMS)
                   f_test (3+ conditions, every design incl. the plain one) ─▶ F, F_p, F_p_adj
```

The F-test columns go in `<level>_results.tsv`, `analysis.json` → `f_test`,
and the report (an "Any change (F)" tile and a table column). `analysis.json`
→ `model` has the formula, the terms and the variance prior; the Methods
paragraph describes the same. FragPipeAnalystR's `test_limma` can't fit a
blocked model, so with a design `reproduce_in_R.R` says it repeats the plain
model, and `reproduce_design_in_R.R` repeats Ionomos's model in limma.

Every report also carries its help (D46): `report.py` embeds
`help.report_payload(issues)`, the report / QC / glossary entries of
`ionomos/help/*.md` and the entries for the issues found, as escaped HTML;
report.js adds a **?** beside each section title, QC tab and issue box and
builds the Help section at the end.

`results/sdrf.tsv` (`downstream/sdrf.py`, D38) is the experiment's sample
sheet in SDRF-Proteomics v1.1.0 (template `ms-proteomics`), the format PRIDE
and reanalysis pipelines read. It is its own isolated stage, after the
statistics and before the report:

| Column(s) | Source |
|---|---|
| rows | one per raw file in `ionomos.json` `plan.manifest` (experiment.yaml corrections applied); TMT: one per file × channel; isoDTB: a light and a heavy row per file (`ICAT light` / `ICAT heavy`, sharing the assay name). Without a manifest: the quant table's run columns, else the raws in the folder |
| `source name`, `characteristics[biological replicate]` | the sample (`DMSO_1`; TMT: the channel's sample name) and its replicate; a replicate number is never reused within a condition (a sample moved to another condition gets the next free one) |
| `comment[fraction identifier]` | read from the file name (`_<rep>_<fraction>`), else 1 |
| `comment[label]` | `label free sample`, `TMT126`…, `ICAT light/heavy`; TMT channels from `annotation.txt` next to the raws, else experiment.yaml `tmt:`, else names like `DMSO_1_126` |
| `factor value[condition]` | the condition the analysis used (after `sample_conditions`); samples left out are still listed with theirs |
| `characteristics[organism]` | `analysis.sdrf.organism`, else the FASTA's UniProt `OS=` (one species ≥ 90 % of targets) |
| `comment[cleavage agent details]`, `comment[modification parameters]` | `analysis.sdrf.cleavage_agent`, else the MSFragger enzyme in `fragpipe/fragpipe.workflow`; enabled fixed/variable mods mapped to Unimod (unknown masses keep `MM=`) |
| `comment[instrument]`, organism part, cell type, disease | `analysis.sdrf` only (config.yaml lab-wide, experiment.yaml per experiment) |

Unknown values are `not available`. `analysis.json` → `sdrf` lists the columns
a repository still needs filled (`fill_in`), and the report's Methods says so.
A table analysed on its own (D33) gets no SDRF: it names no raw files.

**The design can come in as an SDRF too** (`downstream/sdrfdesign.py`, D47). When
the experiment folder (two levels deep) or the folder of the table given to
`ionomos analyze` holds a `*.sdrf.tsv` / `sdrf.tsv`, `load_quantities` matches
each sample of the quant table to its rows and sets condition
(`factor value[...]`, or the ones `analysis.sdrf_factor` names), biological
replicate, and for TMT the plex and the pooled reference. `results/`, old runs
and `<table>_ionomos/` folders are never searched, so Ionomos' own
`results/sdrf.tsv` is never read back. Matching:

| Table | Matched on |
|---|---|
| label-free (DIA-NN, Spectronaut, MSstats, FragPipe) | the run column's raw-file stem ↔ `comment[data file]`, with the manifest's rules (exact, FragPipe's `_calibrated` suffix, Xcalibur stamp) |
| TMT (MSstatsTMT, MaxQuant with `summary.txt`, PD) | one of the plex's raw files + the channel ↔ `comment[data file]` + `comment[label]` (`TMT127N`); files sharing one channel → source map are one plex |
| anything else | the sample name ↔ `source name` / `assay name` |

Precedence: `sample_conditions` > SDRF > the engine's own condition column >
`ionomos.json` manifest > names. Samples keep the table's names. `analysis.json`
→ `design` records where the conditions came from, the SDRF's match counts and
the samples `sample_conditions` overrode; runs the SDRF doesn't describe are the
doctor's `SDRF_UNMATCHED_RUNS`.

**Several TMT plexes are put on one scale before the processing**
(`downstream/plex.py`, D48; an isolated `plex` stage between reading and
`fpa.process`). Each sample may carry a plex (MaxQuant experiment, MSstatsTMT
mixture, PD file `F1`, SDRF file group) and a channel. IRS (Plubell et al.
2017) scales every protein in every plex so that the plex's reference channels
(the geometric mean of them) agree; the references then leave the matrix.
References come from `analysis.tmt_reference` (or experiment.yaml
`tmt.reference_channel`), else the SDRF's pooled rows, else names like
`Pool` / `bridge` / `Norm`. Without one, the plexes' own means are used only
when every plex holds the same mix of conditions; otherwise nothing is scaled
and the doctor warns (`TMT_PLEXES_NOT_NORMALISED`). TMT-Integrator abundances
(already ratios to the reference) and MSstatsTMT input (normalised to its Norm
channels by the loader, as MSstatsTMT does) are never scaled again. The values
before IRS are kept in `meta["bridge_before"]`, so the report's PCA can show
before / after and colour by plex; `analysis.json` → `tmt` says what was done.

`results/time_course.tsv` (`downstream/timecourse.py`, D53) is made when the
conditions are time points: names like `Drug_4h` (or `analysis.times`) and at
least `time_min_points` (3) per series. It reuses the comparisons' own model
(`~0 + condition` plus any block / covariates) and variance prior, and asks
three questions as contrasts between the time points' coefficients: change
over time (moderated F, every time point against the first), a trend
(moderated t on the linear contrast over the ordered time points), and, with
a control series, whether the series responds differently (moderated F on
the interaction contrasts). The changing features are then grouped into
patterns by a k-means with a fixed start. Intensity data and limma only.

`results/cysteine_sites.tsv` (`downstream/cys.py`, D52) is made for site
ratio data (isoDTB), after the dose-response stage and isolated like it. It
reads the processed matrix's *measured* ratios and classifies each site per
compound (liganded / inconsistent / not liganded / too few) by
`liganded_ratio` in `liganded_min_replicates` replicates, then derives the
liganded fraction, selectivity across compounds, a per-protein view and, with
`site_annotation`, known / new sites. The report's Liganded sites section
only displays these calls. Rules and settings: WORKFLOWS.md.

`results/psm_qc.tsv` (`downstream/psmqc.py`, D55) is made when the search
output holds FragPipe `psm.tsv` files or a DIA-NN `stats.tsv`. The stage
runs for every analysis, also when no quant table was found, and is isolated
like the others. It parses nothing itself: `qcmetrics.search_tables` reads
the tables with the same row-by-row reader the instrument QC trend uses
(`read_psm`, `read_diann_stats`), for every run they name, and `psmqc.py`
turns the per-run records into the TSV, the `psm_qc` entry of
`analysis.json`, the report's Search quality QC tab (`d["qc"]["psm"]`) and
the two warnings. A table over 4,096 MB is not read. What is shown and the
limits: WORKFLOWS.md.

`results/dose_response.tsv` (`downstream/doseresponse.py`, D44) is made when
the conditions are a titration: names like `Cmpd_10nM` (or
`analysis.doses`) and at least `dose_min_doses` (4) doses above the control.
It runs after the insights stage, isolated like it, on the processed
matrix's *measured* values (no imputed ones):

```
conditions ─▶ plan_series ─▶ per compound, per feature:              ─▶ dose_response.tsv, analysis.json
 names or     dose per        ratio = 2^log2 / mean(2^log2 control)      dose_response, report section
 doses:       condition,      control = one point, ratio 1 at dose 0     (table, curve over its points,
              control = 0     4PL fit (CurveCurator's guesses + bounds,  potency vs effect)
                              bounded Levenberg-Marquardt)
                              F (n/k), p ~ F(5, dfd; loc .12), BH q,
                              relevance (s0), up / down / not / unclear
```

Fewer doses, no dose in the names, or `dose_response: false` skip it (a
note when doses were found); doses that can't be read become a `DOSES`
issue. The report never refits: it draws the curves from the parameters.

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

Update: app finds a newer release on GitHub (or a Setup in Downloads) → verified download → `request_stop` (graceful; running
search re-queued) → `RESTART_WATCHER` note → silent Setup → app reopens →
watcher restarted. Report: **Report a problem…** → `save_problem_report` →
`Ionomos-report-<ts>-v<ver>.zip` on the Desktop (note, build.json, report.txt,
config, watcher + app logs, crash files, problem jobs' FragPipe logs; never
raw data) → selected in Explorer. Detailed logging: `logs/DEBUG_UNTIL`,
honoured by the running watcher within 30 s, expires by itself.
