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
| `naming.py` | Pure functions: folder name → fields; raw filename → (sample, rep, fraction). Per-method file rules are templates or regexes, date formats a list, both from `config.yaml` `naming:` (D37). `method_kind()` is the one place that says what a method key behaves as (its engine's kind, its `like:` target, else the key; D54): `Config.kind()` / `Config.analysis_method()` wrap it, and the search inputs, the review window and the analysis ask them instead of comparing keys | — |
| `namecheck.py` | "Test your names": how the live config reads folder / `.raw` names (`ionomos names test`, app Methods tab → **Test names…**); read-only | — |
| `manifest.py` | Read/validate `experiment.yaml`; build `.fp-manifest` + TMT `annotation.txt` | `prior-work/fragpipe_runner.build_manifest` |
| `intake.py` | Validate a stable folder, show it for review (or hand unresolvable names to the resolver), move it to the user dir, write `ionomos.json` (with each raw file's acquisition time, `acqtime.py`), insert ledger row. The watcher passes a `config.LiveConfig`, so edits to config.yaml apply to the next drop | — |
| `acqtime.py` | When each raw file was acquired, read-only: the Thermo `.raw` header (audit start FILETIME at 0x28, checked), else ThermoRawFileParser's mzML / metadata output, the Xcalibur stamp in the name, the file time (approximate) (D78). Used by intake, the QC trend and the run-order QC | unfinnigan / OpenTFRaw format notes |
| `resolve.py` | tkinter window for fixing user/method/file tails; writes `experiment.yaml` + learned aliases | — |
| `testbed.py` | Fake lab + sample drops + the fake engines for testing on any OS | — |
| `fake_fragpipe.py` | The testbed's FragPipe (`ionomos fake-fragpipe`): FragPipe 24's options, checks, messages, console layout, exit codes and output files, each copied from a named source; no search (D59) | FragPipe's source |
| `demo.py` | `ionomos demo`: a simulated DIA experiment (`downstream/simulate.py`, planted hits, on/off proteins and gene-set shifts; bundled `assets/demo_gene_sets.gmt`) written to a new folder, then analysed. Offline, no lab config, no Tk (the pip install; docs/QUICKSTART.md) | — |
| `app.py` | tkinter setup wizard / control panel: folders, users, methods, every parameter, start/stop, startup task, testbed | — |
| `configio.py` | config.yaml as a dict; writes a commented file | — |
| `service.py` | child processes, PID file, Task Scheduler, remembered config path, exe routing, the Desktop folder (also when OneDrive moved it); dev install: git update, the diagnostics text; `save_problem_report` / `save_diagnostics_zip` are the old names for a `diagnose` bundle | — |
| `bundle.py` | The troubleshooting / validation bundle (D63, see "The bundle" below): `collect` (what would go in; sizes only), `learn` + `Anonymiser` (the pseudonyms), `create` (streams the zip, then `verify` searches it for every original; the key file next to it), `inspect` (with `self_check`: every file searched for the real names this computer knows, D74) / `unpack` / `translate` for the reader, `anonymise_text` (the anonymised Copy diagnostics, the same Anonymiser and leak check, D74), the `ionomos bundle` command, and what the window says (`Choice`, `summary_lines`) | — |
| `bundle_dialog.py` | The **Report a problem** window (Tk only; every decision is in `bundle.py`) | — |
| `ledger.py` | SQLite job table + status transitions; source of truth for "what's queued" | `prior-work/store.py` |
| `worker.py` | Thread inside `ionomos run`: first runnable `queued` job → FragPipe or DIA-NN (runner.py) → done/failed; holds jobs whose setup files are missing. Sequential. | `prior-work/queue_worker.py` |
| `fragpipe.py` | Prepare a job (launcher `fragpipe.bat` with FragPipe's own Java, workflow with `database.db-path` patched to the method's FASTA, manifest, TMT annotation; a FASTA FragPipe would refuse holds the job), run headless with timeout/stop, kill the process tree, read the console (steps, exit codes, `ALL JOBS DONE`), explain failures (`EXPLANATIONS`), the install report | `prior-work/fragpipe_runner.py` |
| `preflight.py` | `ionomos preflight` / the app's Check FragPipe install: the install report, FragPipe started for `--help` and a `--dry-run` per method, PC checks (spaces, disk, RAM, long paths, permissions), each workflow's tools against the installation (D59) | — |
| `fingerprint.py` | After every search: `ionomos_run/run_fingerprint.json`, a small text record of what ran and what the parsers read, for checking them against a real FragPipe (D59) | — |
| `maxquant.py` | `engine: maxquant` methods: the lab's `mqpar` or MaxQuant's own `--create` template, patched with the job's raws, experiments, fractions, FASTA, threads and output folder into `ionomos_run/mqpar.xml`; output in `maxquant/` (D50) | — |
| `sage.py` | `engine: sage` methods: `ionomos_run/sage.json` (the lab's Sage JSON or Ionomos' defaults, with the job's FASTA, mzML paths and output folder) and `sage_job.json`; the job runs as `ionomos sage-job`, one process that converts each `.raw` to `sage_mzml/*.mzML` with ThermoRawFileParser (reused on retry) and then starts Sage with its telemetry off; output in `sage/` (D51). A lab `sage_config` with `quant.tmt` makes it a TMT job: `tmt.tsv` is expected, and the analysis rolls it up per plex and channel (D56) | — |
| `diann.py`, `runner.py` | `engine: diann` methods: prepare a DIA-NN job (the lab's `diann_exe`, FASTA or spectral library, `ionomos_run/diann.cfg`), output in `diann/`; `runner` picks FragPipe, DIA-NN, MaxQuant or Sage per method, and all use `fragpipe.run`'s start / cancel / stop / timeout loop (D39) | — |
| `postprocess.py` | After a done job: runs `downstream` (or only the R-port prep steps when analysis is off); `ionomos analyze` and the Analysis tab use it too (`prepare`, `inspect_folder`); then instrument QC trending for jobs with QC-standard runs | — |
| `qctrend.py` | Instrument QC trending (D45, [QC_TREND.md](QC_TREND.md)): which runs are the QC standard (`qc_trend.match` / `methods`), the metric store `<log_dir>/qc_trend.jsonl`, per-series baselines, Levey-Jennings z-scores, Westgard rules + CUSUM, plain-English verdicts, a `qc_trend` attention item; `after_job` (postprocess hook, never raises), `scan` (read-only, `ionomos qc-trend --rebuild`), `page_for` (the app's **Jobs → Instrument QC**). Metrics from the searches' own tables (`downstream/qcmetrics.py`: DIA-NN `stats.tsv` / `pg_matrix` / `report.tsv`, FragPipe `psm.tsv` / `combined_protein.tsv`, Sage `results.sage.tsv`, streamed and size-bounded); the page `<log_dir>/qc_trend.html` (`downstream/qcpage.py`: static SVG, report.css, no script) | `prior-work/parsers/`, `store.py` |
| `analysis_tab.py` | App tab 7: analyse one experiment (samples, conditions, comparisons → experiment.yaml, Run), the lab defaults, and the Figure style page (`analysis.export`) | — |
| `accuracy_page.py` | App tab 7 → Check accuracy: Compare / Run benchmark buttons; runs `accuracy.py` on a worker thread (D67) | — |
| `notify_tab.py` | App tab 8 Notifications: `notify:` with masked secrets and Send test (`notify.run_test`) (D67) | — |
| `forms.py` | No Tk: `analysis.export` and `notify:` ↔ the fields the app shows, checked by `charts.style_layer` / `notify.settings_from` (D67) | — |
| `accuracy.py` | No Tk: `ionomos compare` / `benchmark` as calls with a line callback, their command lines, and the checks before a run; the CLI and the app both use it (D67) | — |

| `experiment_editor.py` | One experiment's analysis choices as a Tk panel (samples, conditions, roles, comparisons, cut-offs, Run, issues); used by tab 7 and the pop-ups | — |
| `attention.py` | Durable "needs a person" queue (`<log_dir>/attention/*.json`): raised by intake, worker, analysis, QC trending (`qc_trend`, a warning: no pop-up unless `qc_trend.popup`); closed when fixed | — |
| `popups.py` | Pop-up windows + the "needs attention" list, in the app or (app closed) the watcher's own Tk loop; **More help** opens the help page at the item's topic | — |
| `help/` | The help for users (D46, HELP.md): `*.md` content (getting started, the report, glossary, troubleshooting, never-do, FAQ) parsed and rendered with the stdlib; `report_payload()` for every report, `page()` = `help.html` (`ionomos help`, the app's Help button, pop-ups), `text()` for the terminal, `topic()` / `topic_for_item()` | — |
| `assistant/` | The local assistant (D49, D57, D75, [ASSISTANT.md](ASSISTANT.md)): `ask()` runs one question through a model on this PC and shows the answer only if its citations check out, else Ionomos's own text (`fallback`). `client.py` (the OpenAI-compatible chat API over urllib, localhost only, no proxy or redirect), `tools.py` (seven read-only tools over the ledger, attention items, help, engine logs and analysis.json, with schema-checked arguments and cleaned, capped results), `helpsearch.py` (BM25 over the help: SQLite FTS5 or pure Python), `citations.py` (what the tools returned is what may be cited), `audit.py` (append-only JSONL in app data), `fake.py` (the scripted fake model for tests), `proposals.py` (five proposal tools: checked, read-only descriptions of one change), `actions.py` (applies a proposal; called only by the confirm window's Confirm, `popups.ProposalDialog`). Off unless `assistant.enabled` and a model are set; nothing else depends on it | — |
| `downstream/doctor.py` | The analysis check-up: issues with severity, likely causes, fixes; condition suggestions from file names | — |
| `downstream/` | FragPipe tables → `QuantMatrix` → FragPipe-Analyst processing + limma → interactive `results/report.html`. `isodtb.py`/`tmt.py` (R ports), `quant.py` (loaders), `engines.py` (other engines' outputs — DIA-NN standalone incl. 2.x Parquet, MaxQuant, Spectronaut, AlphaDIA, MSstats and MSstatsTMT format, Proteome Discoverer — and the provenance of any result; ENGINES.md, D36), `fpa.py` (FragPipeAnalystR port: filter, normalise, impute, `test_limma`), `design.py` (limma designs with blocks and covariates, the moderated F; D42), `deqms.py` (R's loess and DEqMS' peptide-count prior; D43), `doseresponse.py` (CurveCurator's dose-response curves for titrations; D44), `sdrfdesign.py` (an input SDRF as the design; D47), `plex.py` (IRS across TMT plexes; D48), `rrandom.py` (R's RNG), `analysis.py` (settings, comparisons), `qc.py` (PCA, clustering, CV, missingness), `insights.py` (sample scorecard, PC ↔ condition/replicate, missingness vs intensity, p-value shape + π0, on/off features, imputation-driven hits, power; D35), `enrich.py` (local ORA and a correlation-adjusted rank test on Enrichr libraries), `export.py` (result tables, FragPipe-Analyst annotation + R script), `sdrf.py` (SDRF-Proteomics sample metadata; D38), `report.py` + `assets/report.js`, `charts.py` (static SVG volcano), `stats.py`, `simulate.py` | the lab's R scripts; FragPipeAnalystR; limma |
| `downstream/guards.py`, `trust.py` | D60. `guards.check_input` makes a loaded matrix safe (implausible values to missing, repeated sample columns dropped) and says so; `guards.statistics` reports p-values that may not mean what they say (`NO_RESIDUAL_DF`, `VARIANCE_PRIOR`, `ZERO_VARIANCE`, `IDENTICAL_SAMPLES`); `trust.build` turns the analysis' own checks into the "How far to trust this" list (`analysis.json` → `trust`, static HTML at the top of the report) | — |
| `downstream/compare.py`, `benchmark.py`, `plots.py` | D60, [VALIDATION.md](VALIDATION.md). `ionomos compare`: an analysed folder against a reference result (another folder, or a results table read by `anytable.py`), per comparison matching, fold-change / hit / p-value agreement and a verdict. `ionomos benchmark`: the pipeline on `simulate.py` data over a grid of designs and settings (`--kind dia | isodtb | tmt`, D66), or an analysed mixed-species / spike-in experiment against expected ratios. `plots.py`: their static SVG charts and page shell, on `charts.py`'s helpers and `report.css`. Nothing in the analysis depends on them | — |
| `setupcheck.py` | The setup checklist (app ✓ Setup tab, `ionomos init`) | — |
| `names.py` | Every on-disk / system name, with its LabWatch-era twin; readers accept both, writers use the new one | — |
| `buildinfo.py` | `Ionomos 0.5.0 (build 3f2a9c1, date, installed)` — `--version`, app footer, every report | — |
| `updates.py` | Asks GitHub for the newest release, downloads the Setup (size + SHA-256 verified), or finds one in Downloads; stops the watcher, runs the installer, restarts the watcher after | — |
| `loose.py` | `.raw` files dropped without a folder: grouped by shared name into folders in the inbox once stable | — |
| `tkutil.py` | Tk variables that can be garbage-collected on any thread (plain ones abort the process on Windows) | — |
| `health.py` | Failsafes: single-instance lock, heartbeat, thread supervisor, crash hooks/files, disk/RAM facts, log-problem extraction, the rotating log handler | — |
| `notify.py` | Messages when a search is done / failed / held (D58): `notify:` settings, the message, the webhook / Teams / Slack / email senders, redaction of secrets. Off by default | — |
| `stress.py` | `ionomos testbed stress`: messy drops + chaos against a real watcher/worker, invariant checks; name fuzzer | — |
| `runners/isodtb.py` | Post-proc: modified-peptide → site merge — thin entry point delegating to `downstream/isodtb.py`, which holds the algorithm | port of `lab-scripts/isoDTB_…R` |
| `runners/tmt.py` | Post-proc: experimental annotation fix | port of `lab-scripts/correct_experimental_annotation…R` |
| `runners/dia.py` | Post-proc: (TBD — probably nothing beyond copying `report.tsv` up) | — |
| `cli.py` | `ionomos setup / run / check / preflight / status / dry-run / names test / retry / testbed / diagnose / bundle / spectronaut-columns / notify-test / update / help / ask / analyze / demo / compare / benchmark`; no args → app; hidden `fake-fragpipe` for the testbed | — |

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
                  stop a FragPipe that a killed Ionomos left on this folder (engine_pid.json, D69)
                  move an old non-empty dest\fragpipe\ aside (fragpipe_previous_<ts>[-n])
                  write dest\ionomos_run\fragpipe-files.fp-manifest
                  write dest\ionomos_run\<method>.workflow  (database.db-path = method FASTA)
                  (TMT) write annotation.txt next to each plex's raws (never over a user's own)
                  run fragpipe.bat --headless --workflow <wf> --manifest <mf>
                      --workdir dest\fragpipe --threads N --ram G      (JAVA_HOME = FragPipe's jre)
                  tee → dest\ionomos_run\fragpipe_console.log
                  write dest\ionomos_run\run_fingerprint.json        (whatever the outcome)
                  exit 0 + output + no failed step + whole tables → postproc(method) → status=done, DONE.txt
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
    ionomos.log                     ← rotating: 5 MB, then ionomos.log.1 … .5
    notify_state.json               ← which "waiting" messages were sent (only with notify: on, D58)
    qc_trend.jsonl                  ← instrument QC: one line per QC-standard run (D45)
    qc_trend.html                   ← the QC trend page (Levey-Jennings charts, Westgard rules)
    help\help.html                  ← the help page, rewritten each time it is opened (D46)
    preflight\<time>\               ← one FragPipe preflight's files: --help output, each method's dry run (D59)
  ionomos.db                        ← SQLite ledger

C:\Fragpipe_General\<user>\<experiment>\    ← where jobs land
  *.raw                              ← moved as-is (or raw\, or <plex>\ per TMT plex, if the user made them)
  experiment.yaml                    ← if the user wrote one
  ionomos.json                      ← status + provenance, rewritten on every transition; "acquisition": when
                                       each raw file was acquired, read at intake (acqtime.py, D78)
  ionomos_run\                      ← what ionomos gave FragPipe
    fragpipe-files.fp-manifest
    <method>.workflow                ← pinned workflow, database.db-path set
    fragpipe_console.log             ← FragPipe's console output (all attempts)
    run_fingerprint.json             ← what the latest search did (D59); earlier: run_fingerprint_<time>.json
    engine_pid.json                  ← only while a search runs: its process and start time (D69)
  fragpipe\                          ← --workdir; all FragPipe output
    fragpipe.workflow                ← FragPipe copies the workflow used here
  fragpipe_previous_<ts>\            ← an earlier attempt's output (never deleted)
    combined_modified_peptide_label_quant.tsv   (isoDTB)
    tmt-report\abundance_gene_MD.tsv            (TMT)
    dia-quant-output\report.tsv                  (DIA; diann-output\ before FragPipe 24)
  results\                           ← post-processing output
    <experiment>_sites.tsv           (isoDTB)
    experimental_annotation.tsv      (TMT)
    report.html, analysis.json, …    (the analysis; see "Downstream pipeline")
    compare.html / .tsv / .json      `ionomos compare`: this analysis against a reference result (D60)
    benchmark.html / .tsv / .json    `ionomos benchmark --expected`: measured against known ratios
    benchmark_simulated[_isodtb|_tmt].*   `ionomos benchmark --like`: these settings on simulated data (D60, D66)
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
  fragpipe_exe: C:/FragPipe/FragPipe-24.0/bin/fragpipe.bat   # the headless launcher, not FragPipe-24.0.exe
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
  config_tools_folder: ""   # only if FragPipe's window was never used with this installation
  config_python: ""         # FragPipe 24 on Windows uses its own python folder whatever this says

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

notify:                     # off by default; see "Notifications" below
  enabled: false
  on: [done, failed, held]
  include_names: true
  slack: {url: "", url_env: ""}   # also webhook:, teams:, email:

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

An optional `assistant:` block (`enabled`, `base_url`, `model`, `maintainer`,
`timeout_seconds`, `stream`, `allow_cloud`) configures `ionomos ask`. It is off
by default and its `base_url` must be on this PC. See ASSISTANT.md.

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
| FragPipe says exit 0 but a step failed / cancelled its tasks / only did a dry run / wrote nothing | parsed from the latest attempt's part of the console (`Process 'X' finished, exit code: N`, `Cancelling N remaining tasks`) → failed |
| The launcher is FragPipe's window `.exe` (returns at once, FragPipe runs on unseen) | never run: swapped for `fragpipe.bat`, else the job is held |
| `fragpipe.bat` finds no Java (none on PATH) | `JAVA_HOME` set to the installation's `jre` for the launcher |
| The FASTA has no decoys, or not about half | the job is held with the rule; FragPipe is not started |
| A search behaves in a way the parsers don't know | `ionomos_run\run_fingerprint.json` records what ran and what was read, for every search |
| An analysis stage crashes (QC, enrichment, export, report) | isolated: recorded in `analysis_error.txt` + an issue; the rest (volcanos, tables, report or its fallback page) is still made |
| Instrument QC trending crashes, or a QC table can't be read | isolated after the analysis: a note on the run's row, a log line; the job is done regardless |
| A result table that is malformed but plausible (repeated or empty column names, text, infinities, negative or absurd numbers, decimal commas) | read as far as it can be; values no instrument produces count as missing; each repair is a note in the report (`guards.check_input`, `anytable.py`; D60). A seeded fuzz in the tests fails on any stage crash |
| Statistics that run but may not mean what they say (no residual df, no variance prior, identical replicates, two identical samples) | computed as limma computes them, and reported as a warning each (`guards.statistics`); never repaired silently |
| The analysis can't decide (one condition, no control, a group of 1, unmatched runs) | runs on the best guess, then a pop-up with the experiment editor asks; the answer goes to experiment.yaml |
| A search fails / is held / a folder is rejected / a raw file is 0 bytes | an attention item → pop-up with likely causes, log tail, Retry; closes itself when fixed |
| A GUI button throws | `report_callback_exception` → dialog + crash file; the app keeps running |
| `check`/diagnose on a wedged display | the Tk probe runs in a child process with a timeout |
| The log grows without end | `ionomos.log` rotates at 5 MB, 5 old files kept (`names.LOG_MAX_BYTES`, `LOG_BACKUPS`) |
| Windows refuses to rotate the log (another program has it open) | the watcher keeps writing to the same file and tries again a minute later; no line is lost |
| A notification can't be sent (dead webhook, no network, wrong password) | sent from its own thread after the status is recorded, one try, a timeout; logged once; the job is unaffected |
| A webhook address or the SMTP password ends up in a report | never logged; `diagnose`, the bundle and Report a problem redact `config.yaml` and scrub every included file |
| A lab member's or an experiment's name leaves the PC in a bundle | names are replaced by default; the finished zip is searched for every original and is not saved if one is found (`BundleLeak`); the key file is never inside the zip |
| A bundle overwrites a file, or changes an experiment | it only reads experiment folders; the zip is written as `.part`, renamed when it passed the check, and an existing name gets `-2`, `-3` … |
| A multi-GB table is bundled | streamed line by line (a few MB of memory); a size limit per bundle and a row-sampled PSM table, both said in `BUNDLE.json`, `README.txt`, `inspect` and the window |

FragPipe faults (D69; each acted out by the fake FragPipe in `tests/test_faults.py`):

| Threat | Defence |
|---|---|
| Ionomos is ended (Task Manager, a crash) while FragPipe runs; FragPipe lives on in its own process group | `ionomos_run\engine_pid.json` holds the pid and the OS's start time of the process; the next start (`worker.recover`) and every attempt stop a recorded process that is still running **and** started then (never one that only has its number), before the job runs again; the folder says `queued` / `failed` |
| FragPipe hangs | the time limit kills the process tree; the cause names the limit and the step it was at |
| FragPipe is ended from outside, or crashes without a message | a POSIX signal, a Windows NTSTATUS, or a run that stopped mid-step without a word gets a cause ("ended from outside", "crashed") |
| Exit code 0, but the main result table is empty, binary or cut off mid-row | failed ("not written to the end"); a header alone: "no identifications"; `psm.tsv` problems: a note; no end line and no table: failed |
| A tool prints forever | the console log may grow 2 GB per search (`fragpipe.MAX_CONSOLE_BYTES`), then the search is stopped |
| Console text in the Windows code page, UTF-16, colour codes, binary junk, GB-sized | read line by line as UTF-8 else cp1252, NULs and control characters dropped; only the end of the log is ever read |
| The disk fills, or something else fails inside Ionomos, during a search | FragPipe killed, the job failed with that reason; Ionomos' own log lines are best effort |
| A raw file is still open (Xcalibur, a copy, antivirus) when its search would start | held until it can be read |
| A path FragPipe gets has a space (FASTA name, tools folder) | held on Windows; a note elsewhere |
| The workflow or FASTA vanishes just before the start | held, the attempt not counted |
| The earlier output can't be moved aside (a file open in Excel) | held; a new search is never written into it; `fragpipe_previous_<time>-2` when the name is taken |
| Two job rows for one experiment folder | the later one is failed as a duplicate in the job list only; the folder belongs to the first |
| Several TMT plexes in one folder | filed as dropped with a warning (FragPipe names the channels itself); a drop as `<plex>\*.raw` keeps that layout and gets an `annotation.txt` per plex |

## Notifications (D58)

Off unless `config.yaml` has `notify: enabled: true` and a channel. The
worker calls `notify.announce` after a job's status is recorded:

```
worker: status in ledger + ionomos.json + DONE.txt / FAILED.txt
   │
   ▼
notify.announce(cfg, event, job, reason, summary)        returns at once, never raises
   │  off, or event not in notify.on            → nothing
   │  held, same job and reason as before        → nothing (<log_dir>/notify_state.json)
   ▼
build(...) → Message                                     the only thing that leaves the PC
   ▼
daemon thread: deliver → webhook, teams, slack, email    one try each, timeout_seconds, no retries
   ▼
log: "notified by slack: job 12 done"  /  "could not notify by slack: …" (once per channel and error)
```

```yaml
notify:
  enabled: false              # nothing is sent while this is false
  on: [done, failed, held]    # held = a search is waiting (FASTA, workflow, disk space …)
  include_names: true         # false: job number, status and time only
  timeout_seconds: 10         # 1–60
  webhook: {url: "", url_env: ""}   # any service that takes a JSON POST
  teams:   {url: "", url_env: ""}   # a Teams Workflows webhook address
  slack:   {url: "", url_env: ""}   # a Slack incoming-webhook address
  email:
    host: ""                  # no host = no email
    port: 587
    security: starttls        # starttls | ssl | none (no password allowed with none)
    username: ""
    password: ""
    password_env: ""          # the NAME of an environment variable; it wins over password
    from: ""
    to: []
```

**What is sent.** Exactly these fields; the generic webhook gets them as
one JSON object, the others as a title and lines of text:

| Field | Example | With `include_names: false` |
|---|---|---|
| `app`, `version` | `Ionomos`, `0.12.0` | sent |
| `event` | `done`, `failed`, `held` | sent |
| `job_id` | `12` | sent |
| `time` | `2026-10-01T14:03:11-07:00` | sent |
| `text` | the message as plain text (the title and the lines below) | `Ionomos: job 12 done` |
| `experiment` | `20260902_EJQ_isoDTB_EJQ-2-027_1uM-3h` | left out |
| `user`, `method` | `EJQ`, `isoDTB` | left out |
| `reason` | `FragPipe ran out of memory …` (failed / held; at most 600 characters) | left out |
| `hits` | `[{"comparison": "Compound vs DMSO", "up": 12, "down": 3, "tested": 4021}]` | left out |
| `report` | `C:\Fragpipe_General\EJQ\…\results\report.html` (a path, not the file) | left out |
| `pc` | the computer's name | left out |

Never sent: raw files, result tables, protein or site names, intensities or
ratios, the report, the search log, `config.yaml`. An SMTP server also sees
what every mail server sees (the PC's address and host name).

**Payloads.** Slack: `{"text": "*title*\nlabel: value\n…"}`. Teams:
`{"type": "message", "attachments": [{"contentType":
"application/vnd.microsoft.card.adaptive", "contentUrl": null, "content":
{AdaptiveCard 1.2: a TextBlock title and a FactSet}}]}`. Email: the title
as the subject, the lines as plain text.

**Secrets.** A webhook address is a password: anyone who has it can post to
the channel. `url_env` / `password_env` keep it out of the file. Addresses
must be `https://` (`http://` only for `localhost`), and a redirect is not
followed. `notify.scrub` and `notify.redact_config_text` keep the values
out of the log, `ionomos diagnose`, `diagnostics-*.txt`, the bundle and
Report a problem. `config-backups/` holds full copies of `config.yaml` and
stays on the PC.

`ionomos notify-test` sends a test message on every configured channel
(also with `enabled: false`) and prints one line per channel. The worker
reads `notify:` when the watcher starts: restart it after a change.

## Downstream pipeline (0.6.0: FragPipe-Analyst)

```
fragpipe/ ─▶ method prep ─▶ QuantMatrix ─▶ fpa.process ────────────────▶ limma ─────────▶ QC + enrichment ─▶ report
             isodtb.py      features ×     samples chosen / renamed      one ~0+condition  PCA, correlation,   report.html (JSON +
             tmt.py         samples, log2  contaminants, % filters       model, eBayes,    missingness, CV,    report.js), TSVs,
             quant.py       + condition    median / GN normalisation     CIs, BH           heatmap, ORA        volcano_*.svg,
                                           Perseus / MinProb / … impute  add_rejections                        fragpipe-analyst/,
                                                                                                               figures/ (if asked)
                                                                                   insights (D35):
                                                                                   scorecard, batch,
                                                                                   missingness, π0,
                                                                                   on/off, power;
                                                                                   rank-based sets
```

**Figures for slides** (D62). One export style (size, text, colours, title,
legend; `charts.STYLE_DEFAULTS`, the same keys in `report.js`) is used in
three places:

| Where | What | How |
|---|---|---|
| The report | **SVG** / **PNG** / **Export…** on every chart, **Export for slides** (a .zip of every figure, the tables as CSV, the style, a README) | `report.js` "figure export": the chart's part of the report is drawn again with the style answering `css()`, `widthOf()` and `heightOf()`; `svgTools()` hands the SVG over; a title, a legend and the cut-offs are put around it. PNG through a canvas; the zip by a store-only writer. The style is kept in `localStorage` and starts from the payload's `exportDefaults` |
| `ionomos export <folder>` | volcano, PCA, heatmap, correlation, and the dose-response, time-course and liganded-site figures (`downstream/sectionfigs.py`) as SVG in `results/figures/` + `README.txt`; `--list`, `--figures`, `--features`, `--top`; PNG with `--format png\|both` | `downstream/slides.py` reads the JSON inside `report.html` and `charts.catalog()` / `figures()` draw from it: no browser, nothing analysed again. PNG is drawn by a renderer the computer has (`downstream/raster.py`: cairosvg, resvg, rsvg-convert or Inkscape), never a dependency (D68) |
| After each analysis | the same files, when `analysis.export.figures` lists any (default: none) | the `figures` stage of `downstream.analyze`, isolated like the others; listed in `analysis.json` → `figures` and in the report's Files |

The exported SVG has no CSS: text is `<text>`, colours are written out, and
`<desc>` holds the experiment, comparison, cut-offs and test. `volcano_*.svg`
(below) is older and different: it follows the browser's light / dark setting
through CSS and is what the fallback page embeds.

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

**Guards, the trust list and the accuracy commands** (D60,
[VALIDATION.md](VALIDATION.md)). Two small stages were added to `analyze()`,
isolated like the others:

```
read ─▶ input-check ─▶ plex ─▶ process ─▶ statistics ─▶ … ─▶ guards ─▶ doctor ─▶ trust ─▶ report
        guards.check_input                                  guards.statistics      trust.build
        |log2| > 100, NaN, inf → missing                    per comparison:        statements with numbers
        repeated sample column dropped                      zero residual df,      + compare.json / benchmark.json
        notes: duplicate / blank IDs,                       no prior, zero         found in results/
        a sample without values                             variance, identical    → analysis.json "trust",
                                                            samples → warnings       a static block in report.html
```

Nothing in these changes a number of a well-formed analysis. `ionomos
compare` and `ionomos benchmark` are separate commands that read
`results/analysis.json` and the `*_differential.tsv` files and write their
own `compare.*` / `benchmark.*` / `benchmark_simulated.*` files beside them;
the next `analyze()` shows their verdicts in the trust list, with whether the
settings are still the ones they were made on (`trust.settings_digest`).

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

Site ratios can be centred per replicate in `fpa.process` (`centre_ratios`,
`analysis.ratio_centre`, D70; off by default); the info, with every replicate's
offset on its stable sites, is `Processed.normalization["ratio_centre"]`, so the
doctor's `RATIO_OFFSET` can say when an uncentred replicate is off. With
`analysis.protein_correction`, `downstream/proteincorr.py` reads an analysed
proteome (an Ionomos results folder, an MSstats table or a differential table;
read only) after the site comparisons and adds one *protein-corrected*
`DiffResult` per matched condition (MSstatsPTM's adjustment, using the `se` and
`df` that every differential table now carries), so the report, the results
table and `analysis.json` show it like any comparison; the liganded calls get
the protein's ratio beside them.

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

**Run order** (`downstream/runorder.py`, D78) runs after the search quality,
for every analysis with processed samples, isolated like the others:

```
ionomos.json manifest ─▶ sample_files ─▶ acquisition time per file ─▶ run order of the samples
 (else the raws in       (run names,      (ionomos.json "acquisition",   (first fraction's time)
  the folder)             exp_rep, stem)   else acqtime.read, read-only)          │
scorecard (insights.py) + psm_qc payload (psmqc.py) ─▶ a value per sample and QC number
                                                       ▼
          trend(): stratified Mann-Kendall + Theil-Sen     confounding(): η² of the run
          within each condition                             positions by condition
                                                       ▼
          analysis.json "run_order", the report's Run order QC tab (d["qc"]["run"], via
          ctx["run_order"]), RUN_ORDER_DRIFT / RUN_ORDER_CONFOUNDED, the run_order figure
```

Samples that share raw files (TMT channels) get no run order; samples with
no readable time are listed and left out of the tests.

**Roles** (`downstream/roles.py`, D61). `roles.plan(matrix, settings)` gives
every condition a role and, when one is a competition, the default
comparisons. `analysis.choose_comparisons` asks it (a thin hook; explicit
`comparisons:` and `de_type: all | others` never reach it), and `analyze()`
keeps the plan for the doctor, `analysis.json` (`roles`) and the report:

```
conditions ─▶ roles.infer ─▶ roles.plan ─────────▶ comparisons ─▶ limma (unchanged)
 names,        control /      compound vs control                      │
 analysis.     compound /     competition vs compound                  ▼
 roles, SDRF   competition    competition vs control        roles.specific_targets
 role column   (of X) / ...   (nothing else by default)     up in the first AND down in the second
                                                            ─▶ specific_targets.tsv, analysis.json,
                                                               the report's Specific targets section
```

An SDRF's `characteristics[role]` column is read by `sdrfdesign.py` into
`meta["roles"]` and merged into the settings (which win) before anything
else looks at them. Unequal groups touch three places:
`analysis.group_needs` (what `min_valid` asks of the smaller group, passed
to `fpa.limma_contrasts` / `design.limma_design`), `insights.power`
(`pairs=`: each comparison with its own n) and `insights.sample_scorecard`
(the spread is scaled to the usual group size before it is compared). The
report recomputes the specific-targets calls from the comparisons with its
live cut-offs; the TSV has them at the saved ones.

The windows (D65) show the same thing before any data is analysed:
`roles.preview(sizes, settings, overrides, …)` builds an empty matrix of the
group sizes, runs `roles.plan` and `analysis.choose_comparisons` on it, and
returns rows, comparisons in words and the uneven-group lines.
`experiment_editor.py` (from `postprocess.inspect_folder`: samples, data type,
SDRF roles) and `resolve.role_view` (from the drop's files, replicates per
condition) only draw it; a choice goes through `roles.set_role` into
`analysis.roles`.

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
watcher restarted. Report: **Report a problem…** → the bundle window →
`Ionomos-bundle-<ts>-<level>-v<ver>.zip` and its key file on the Desktop →
selected in Explorer (next section). Detailed logging: `logs/DEBUG_UNTIL`,
honoured by the running watcher within 30 s, expires by itself.

## The bundle (D63)

One zip a person copies off the PC, for a developer who has never seen it:
to find why a search or analysis failed (`diagnose`) or to repeat the
analysis on the lab's tables (`validate`). Ionomos uploads nothing. The
round trip and the list of what each level holds are in
[DEV_LOOP.md](DEV_LOOP.md).

```
collect(config, jobs, Options)        file sizes only; the window's list and `--dry-run`
   │   system files, then each job's small files, then (validate) tables and results/,
   │   then the size limit: what does not fit is left out, never a log
   ▼
learn(plan) → Anonymiser              names from config.yaml (users, aliases, methods), users_root,
   │                                  the whole job list, the inbox, the bundled jobs' ionomos.json,
   │                                  experiment.yaml, annotation.txt, analysis.json, table headers,
   │                                  the OS account and host name
   ▼
_write → <name>.zip.part              per file: structured rewrite (ionomos.json: folder tokens;
   │                                  experiment.yaml: notes removed; config.yaml: secrets), then every
   │                                  line through notify.scrub + Anonymiser.text; tables keep their
   │                                  identifier columns; file names in the zip are rewritten too
   ▼
verify(zip, anonymiser)               every file and file name searched for every original
   │  hit → write once more (a name first met half-way), still a hit → BundleLeak, .part removed
   ▼
rename to <name>.zip (never over a file) + <name>-KEY-keep-in-the-lab-DO-NOT-SHARE.json beside it
```

With `Options.include` (`--include diann-report,peptides`, D74) the plan also takes DIA-NN's main report
and the peptide / ion tables, after every other table (the size limit drops them first), row-sampled above
`extra_mb`; a Parquet report is written as text (`_parquet_lines`), and `learn` reads the run columns of
a long report (`RUN_COLUMNS`). The same `learn` + `_Scrub` + leak check (`_Check`) anonymise the Copy
diagnostics text (`anonymise_text`), and `self_check` runs `_Check` over a finished zip with the lab's
names and the key's originals (`ionomos bundle inspect`).

**In the zip**: `BUNDLE.json` (format, version and build, level, whether and
how names were replaced, the kept words, per job: status, method, FASTA
facts, `reproducible`, `order_preserved`; every file with size and SHA-256;
what was capped, left out or not found), `README.txt` (the same for a
person), `note.txt`, `build.json`, `report.txt`, `config.yaml`, `logs/`,
`crashes/`, `attention/`, and `jobs/<id>-<folder>/…` laid out as the
experiment folder is. `unpack` turns `jobs/<id>-<folder>/` into
`<dir>/<folder>/` and writes `<dir>/config.yaml` from the bundled one with
every path under `<dir>/_lab/` and without `notify:` / `assistant:`.

**Pseudonyms** (`Anonymiser`). A name is split into words at `_`, `-` and
any other non-alphanumeric character. A word is *kept* when the analysis or
a file format reads meaning from it: numbers with a unit (`10uM`, `3h`,
`127N`, `9plex`), replicate / fraction marks (`F3`, `rep2`), control and
reference words (`analysis.DEFAULT_CONTROL_KEYWORDS`, the lab's
`control_keywords`, pool / bridge / norm …), the config's method keys and
aliases, the analysis' setting names, and a short list of Ionomos' own
words (results, sample, enrichment …). Every other word gets a pseudonym:

| What | Pseudonym | Replaced where |
|---|---|---|
| user folders, aliases, the OS account, any `C:\Users\<name>` | `user01`, aliases `user01a` … | everywhere it stands as a word |
| the PC name | `pc01` | everywhere |
| experiment / inbox folder names | `exp001` (the whole name) | everywhere |
| a folder path outside the lab's tree (raw paths in a foreign table) | `folder01` (the whole path) | everywhere |
| words of raw-file, sample and condition names | `condA` … (letters, no `_`, so `condition_of` and the dose / time readers see the same shape) | inside any registered name, its `_`-prefixes and re-joined forms; alone only if the word is itself a sample / condition name or has letters and digits (`KL6159A`) |
| words of other names (dropped files, inbox items, folder words) | `name01` … | as above |
| e-mail, IPv4 (first number ≥ 10, so `1.4.3.0` stays a version), IPv6 / MAC | `email01@example.invalid`, `ip01` | by pattern |

The `cond` pseudonyms are assigned in the byte order of the originals and
given a first letter (`condA`, `CondA`, `FcondA` …) that keeps their order
against the kept words too, because `fpa.impute` draws Perseus' random
numbers per sample in byte order of the sample names. `order_kept` checks
each job's names and `BUNDLE.json` says when the order changed. Matching is
case-insensitive and on whole words; a run of name characters that holds one
of the lab's names is treated as a name, so its short words are replaced
too. In tab-separated tables the protein / gene / peptide columns
(`bundle._ID_COLUMNS`, and `id`, `label`, `description` of Ionomos' own
tables) are never rewritten; an original found there is listed in the key
file as kept, not failed.
