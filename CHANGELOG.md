# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Analysis-only install from pip** (ROADMAP Phase 5A, D40). `pip install
  ionomos`, then `ionomos analyze <table or folder>`, on Windows, macOS or
  Linux with no Tk; a test runs the analysis in a Python where `import
  tkinter` fails. New [docs/QUICKSTART.md](docs/QUICKSTART.md): a 10-minute
  guide for someone new, covering what `analyze` reads and what the report
  shows.
- **`ionomos demo [FOLDER] [--open]`**: writes a simulated DIA experiment and
  analyses it, offline. It has DMSO / DrugA / DrugB × 4 replicates, planted
  hits, on/off proteins and gene-set shifts, with a bundled demo `.gmt`. The
  default folder is `./ionomos_demo`, then `_2`, `_3`, ... if taken; the demo
  never writes into a folder that holds anything.
- PyPI metadata in `ionomos/pyproject.toml`: SPDX licence, classifiers,
  keywords, project URLs, and a short PyPI-facing `ionomos/README.md`.
- `.github/workflows/publish-pypi.yml`. On `v*` tags or by hand, it builds the
  wheel and sdist, runs `twine check`, installs them into fresh venvs on
  Windows / macOS / Linux and runs `demo` and `analyze` from them. It uploads
  with PyPI trusted publishing only once the maintainer has configured it
  (steps at the top of the file).
- **SDRF-Proteomics sample sheet for every analysed experiment**
  (`results/sdrf.tsv`, `downstream/sdrf.py`, D38). It follows spec v1.1.0,
  template `ms-proteomics`, and has one row per raw file: per channel for TMT,
  and a light and a heavy row for isoDTB. Where the values come from:
  - samples, replicates and conditions: the manifest and the analysis
    (renamed conditions applied; left-out samples still listed)
  - fractions: the file names
  - TMT channels: `annotation.txt`, experiment.yaml `tmt:` or the sample names
  - organism: the FASTA's `OS=`
  - enzyme and Unimod modifications: FragPipe's workflow
  - instrument, organism part, cell type, disease: a new `analysis.sdrf`
    setting (config.yaml lab-wide, experiment.yaml per experiment)

  Anything unknown is `not available`. `analysis.json` → `sdrf` and the
  report's Methods name the columns to fill in before depositing to PRIDE.
  A table analysed on its own gets none. Checked with the official validator
  (`sdrf-pipelines`, dev-only, installed in CI on Linux).
- **Configurable naming** (`config.yaml` `naming:`, D37; ROADMAP Phase 5A).
  Another lab's convention is now a config change, not a code change:
  - `naming.methods.<name>`: each method's `.raw` rule as a template
    (`'{condition}_rep{rep}'`, `'{sample}_R{rep}_F{fraction}'`, `[ ]` for
    optional parts, `{any}` to skip text), a regex with named groups
    (`pattern:`), or `like: isoDTB | TMT | DIA`. New method keys get file
    rules this way.
  - `naming.date_formats`: `YYYYMMDD`, `MMDDYYYY`, `DDMMYYYY`, `MMDDYY`,
    `DDMMYY`, `YYMMDD`, tried in order.

  The built-in rules are now templates that compile to the same regexes as
  before, so nothing changes without the block. Mistakes are config errors
  that name the setting: a bad template, a regex without a `sample` group, an
  unknown field or date format, or a method keyword listed under two methods
  (or blank). Numbers read by any rule must be 1–999 (D30). The app's Save
  keeps the new settings.
- **`ionomos names test <names…>`** (`namecheck.py`): prints how the live
  config reads folder names, `.raw` names or folders on disk (user, method,
  file rule, date, and each file's sample / replicate / fraction), or why a
  name is rejected. `.raw` names after a folder name are read as its files.
  The app's Methods tab has the same check (**Test names…**), using the
  unsaved settings.
- A `.raw` name for a method with no file rule now says how to add one
  (`naming.methods.<name>`).
- **Results from other engines** (`downstream/engines.py`, docs/ENGINES.md,
  D36). `ionomos analyze` (and the Analysis tab's Table…) recognises and
  loads:
  - DIA-NN standalone: `pg_matrix`, the 1.x `report.tsv`, and the 2.x
    `report.parquet` with the optional `pip install ionomos[parquet]`
  - MaxQuant `proteinGroups.txt` (LFQ / Intensity / TMT reporters)
  - Spectronaut pivot and long reports
  - AlphaDIA `pg.matrix.tsv`
  - MSstats-format tables (quantms, Skyline, …), summarised by Tukey median
    polish
  - Proteome Discoverer protein exports

  Long reports are filtered at 1% precursor and protein FDR, and conditions
  and replicates are taken from the engine when it records them.
- **Data source in every report and in `analysis.json` (`engine`)**: engine,
  version, tools (MSFragger / IonQuant / DIA-NN versions under FragPipe),
  table, quantity, FDR filter, FASTA and parameter files, when the folder
  records them. The Methods paragraph names the engine that produced the
  numbers.
- MaxQuant `CON__` contaminants are removed like FragPipe's `contam_`.
- **The watcher can run DIA-NN** instead of FragPipe for a DIA method
  (`engine: diann`, `diann_exe`, optional `library` / `diann_args`; D39,
  docs/ENGINES.md):
  - each job's settings are written to `ionomos_run/diann.cfg`
  - results go to `diann/`
  - cancel / Retry / pop-ups / previous-attempt folders work as for FragPipe
  - the setup checklist checks the DIA-NN install and FASTA / library

  Done notes, failure pop-ups and logs name the engine that ran.
- **Instrument QC trending** (ROADMAP 5C #6, D45, new
  [docs/QC_TREND.md](docs/QC_TREND.md)). Runs of the lab's QC standard (a
  HeLa or K562 digest) are recognised by name: `qc_trend.match`, default
  `hela, k562, qc_std, qcstd, _qc_`. A dedicated QC method also counts
  (`qc_trend.methods`). A replicated design is never mistaken for one. After
  each search of one, Ionomos:
  - reads its numbers from the search's own tables: IDs, signal, peak width,
    MS1/MS2 mass error, missed cleavages, charge, and the RT of the most
    intense peptides (DIA-NN `report.stats.tsv` / `pg_matrix` / `report.tsv`,
    FragPipe `psm.tsv` / `combined_protein.tsv`)
  - stores them in `logs/qc_trend.jsonl` (a re-run updates its row)
  - judges them against a baseline (the first 10 runs, or pinned dates):
    Levey-Jennings z-scores, the Westgard rules 1-3s / 2-2s / R-4s / 10-x
    (1-2s warns) and a CUSUM drift flag, with a plain-English verdict
    ("Precursors 18% below baseline (1-3s) — check the column and the spray…")
  - rewrites `logs/qc_trend.html`: Levey-Jennings charts, every run,
    self-contained
  - when a rule is broken, raises a `qc_trend` attention item (a warning; no
    pop-up unless `qc_trend.popup: true`), which closes when a run is back
    within the baseline

  `ionomos qc-trend [--rebuild] [--open]` rebuilds the page; `--rebuild`
  re-reads past QC runs under users_root, read-only. The app's Jobs tab has an
  **Instrument QC** button. It is all isolated: a QC table that can't be read
  never fails the job.
- The testbed's fake FragPipe and fake DIA-NN write DIA-NN's
  `report.stats.tsv`; the fake FragPipe also writes a `psm.tsv` per DDA
  experiment (FragPipe's columns). A raw name containing `QCBAD` makes a bad
  injection.

- **The watcher can run MaxQuant** for a DDA method (`engine: maxquant`,
  `maxquant_exe`, optional lab `mqpar`; D50, docs/ENGINES.md). Each job's
  `ionomos_run/mqpar.xml` starts from the lab's saved parameters or MaxQuant's
  own `--create` template. The job's raws, `<condition>_<replicate>`
  experiments, fractions from the names, FASTA, threads and output folder are
  filled in. Results go to `maxquant/`, and the analysis reads
  `proteinGroups.txt`.

### Changed

- ROADMAP Phase 6 (D49): a local AI assistant on the proteomics PC. It runs local-first, grounded in citations,
  can only propose changes the user confirms, and is evaluated by a scenario scorecard. A Help section is added
  to Phase 5A.
- A relative `enrichment_gmt` (in `experiment.yaml` or the lab config) is read
  from the experiment folder when the file is there.
- `simulate.dia_pg_matrix` can name the proteins, plant chosen effects and
  leave proteins out of whole conditions. Output for existing seeds is
  unchanged.
- ROADMAP Phase 5: 5A and most of the 5B imports ticked off; issues found on the way listed.
- ROADMAP Phase 5 and D36: the plan for making Ionomos useful to other labs
  (pip-installable analysis, engine adapters, SDRF, configurable naming, more
  engines and analyses).

### Fixed

- An instrument setting at the end of a raw file name is part of the sample,
  never a replicate or fraction (D41, NAMING_CONVENTION.md). FAIMS runs
  `…_DIA_CV-35/-45/-55.raw` were read as one sample with replicates 35, 45 and
  55; they are now three samples. The same applies to `X_CV35` (was condition
  code CV, rep 35) and TMT `…_CV-40` (was fraction 40). isoDTB names ending
  in a setting are refused with a hint.

## [0.11.0] - 2026-09-30

### Added

- **Deeper QC in every report** (`downstream/insights.py`, D35). Each sample
  gets a scorecard, with robust z-scores and plain-word flags, in the report
  and in `sample_qc.tsv`. The scorecard covers:
  - identifications and missingness
  - loading before normalisation
  - correlation with its own replicates
  - spread around its group mean
  - the group's CV with the sample left out

  Also new:
  - Each principal component is related to condition and to replicate number,
    so a batch shows up as "PC1 follows the replicate number".
  - Missing values are checked against intensity (low-abundance dropouts or
    random gaps), which says whether low-value imputation fits.
  - The p-value histogram gets its shape and Storey's π0.
  - New doctor warnings (report and attention list, never a pop-up):
    `SAMPLE_OUTLIER`, `BATCH_SUSPECT`, `IMPUTATION_MISMATCH`,
    `P_VALUE_SHAPE`, `IMPUTATION_DRIVEN`.
- **Discovery views**:
  - **Key findings** at the top of the report: top hits, on/off features,
    pathways and the sample verdict per comparison.
  - **Only in one condition**: features measured in most replicates of one
    group and never in the other, the hits a t-test can't see. They are listed
    in the report, in `presence_absence.tsv`, and marked ▲ on the volcano.
  - **Compare comparisons**: log2FC against log2FC in four quadrants, plus an
    UpSet chart of the hit lists; a bar marks its features.
  - **Rank-based gene-set test** on every protein, with no cut-off. It is a
    Wilcoxon rank-sum test with the variance inflated by the set's
    inter-gene correlation, as in limma's camera, and comes with a barcode
    plot. Results are in `gene_set_ranks.tsv`.
  - A protein's **"Behaves like"** list: the proteins most correlated with it
    across samples.
  - For site-level (isoDTB) data: a **Proteins** view of how many of each
    protein's sites move.
  - New QC tabs: missingness vs intensity, mean–variance, abundance rank, and
    **power**. The power tab shows the smallest detectable fold change against
    replicates per group, from the experiment's own noise.
- **Volcano search**:
  - Accepts a single word, a pasted list (with "found 38 of 42, not found:
    …"), `KRT*`, `/^RPL\d/`, `desc:kinase` or `term:apoptosis`.
  - Suggestions as you type; <kbd>/</kbd> jumps to the box.
  - Matches are labelled and shown in the table, heatmap, compare plot and
    abundance rank.
- Other volcano and page features:
  - **Box select** mode on the volcano.
  - **Highlight groups**: named, coloured gene lists kept in the browser,
    shown in every report, with .gmt import and export.
  - **Hit filters**: ignore imputation-driven hits, or require at least N
    peptides / PSMs. Peptide / PSM counts are read from DIA-NN, FragPipe and
    TMT-Integrator tables.
  - Plot options for point size, label size and cut-off lines.
  - PNG export next to SVG.
  - "Copy up / down genes" for STRING or Enrichr.
  - The page state (comparison, cut-offs, search, open protein) is kept in the
    address, and a **Link** button copies it.
  - `ionomos analyze` prints the on/off count and π0 per comparison.

### Fixed

- A `<!--` inside a protein or sample name could stop the report from
  drawing (finding F-01). The data script now escapes it.
- One chart that fails to draw no longer blanks the rest of the page.
- Running the tests on a dev machine no longer covers the screen with Tk
  windows. Tests that open real windows run in CI; locally they run only
  with `IONOMOS_GUI_TESTS=1`.
- Flaky GUI test `test_open_window_picks_up_a_user_added_meanwhile` on macOS.
  Config reloading was fine. The test accepted the window with a synthetic
  Return key, which Tk drops whenever the window has lost keyboard focus (for
  example while someone uses the desktop). It now presses Accept directly, and
  its watchdog check runs after the window closes instead of before it opens.

## [0.10.1] - 2026-09-28

Same features as 0.10.0, which was tagged but never released: its installer
build stopped on a stress-test harness race.

### Fixed

- `ionomos testbed stress`: the "wait until settled" census no longer crashes
  when intake moves a folder between listing a raw file and reading its size.
  The final no-raw-lost check stays strict.

## [0.10.0] - 2026-09-28

### Added

- **Review before filing**: every drop that parses opens the naming window in
  review mode. It shows user, method, date, each file's condition / replicate /
  fraction (all editable), and one line per condition marking the CONTROL and
  the treated ones with their replicates and fractions. A Control picker pins
  a different control in `experiment.yaml`. On by default
  (`gui.review_drops`); drops are filed as read when there's no display. (D34)
- **DIA condition codes**: `X_D1.raw` = DMSO rep 1, `X_C2.raw` = Compound
  rep 2 (1–2 letter codes; `naming.condition_codes`, default `D: DMSO`,
  `C: Compound`). Instrument tails like `HCD33` are unaffected.

### Fixed

- A user or alias added in the app now counts without restarting the watcher:
  it re-reads `config.yaml` when it changes (keeping the last good copy if a
  file is mid-save), and an open naming window refreshes its user list and
  fills in a blank user when an alias like `KC` → Kosuke appears.
- Saving settings in the app keeps `gui.review_drops` and `naming:`, and a
  saved window answer that sets a control no longer wipes other
  `experiment.yaml` analysis settings.

## [0.9.0] - 2026-09-27

### Added

- **Every comparison gets a plot.** A group with one sample is tested anyway
  (limma borrows the variance from the replicated groups; Welch becomes a
  pooled t-test) and labelled *low confidence*. With no replicates anywhere,
  the plot is *fold change only* (log2FC against abundance, or rank for ratio
  data), with no invented p-values. Both labels appear in the report, the SVG,
  `analysis.json` and the CLI output. (D32)
- **Any table → volcano plots**: `ionomos analyze <file>` and Analysis tab →
  **Table…** read TSV / CSV / TXT / Excel `.xlsx`. Quantity tables (MaxQuant,
  Spectronaut, Proteome Discoverer, hand-made sheets) go through the full
  pipeline; results tables (limma, Perseus, DESeq2, FragPipe-Analyst exports)
  are plotted as given. Output goes to `<table>_ionomos/`, never beside the
  file. (D33)
- `EACH_OWN_CONDITION`: when every sample is its own condition (e.g. replicates
  named `_a`/`_b`), a grouping is suggested for you to confirm.

### Changed

- `SMALL_GROUP` no longer blocks a comparison; it remains only when not even a
  fold change can be computed. `ONE_SAMPLE` applies to intensity data only (a
  single isoDTB replicate still gets its fold-change plot).

## [0.8.0] - 2026-09-27

### Analysis robustness (from a 58-case edge-case sweep and a replay of a real 22Rv1 DIA run)

- Inputs that used to finish as `state: ok` with nothing to show now say what's
  wrong: no measured values at all (`NO_QUANTITIES`), a single sample
  (`ONE_SAMPLE`), every sample left out or filtered away (`NOTHING_LEFT`), and
  an isoDTB ratio test with one replicate (`SMALL_GROUP` now applies to every
  method).
- An isoDTB table without probe-labelled peptides or ratio columns is reported
  as a data problem (`UNUSABLE_TABLE`, naming the probe mass) instead of "the
  read step crashed".
- A few non-numeric cells in a DIA-NN run column no longer drop the whole run
  when there's no manifest; they count as missing, with a note.
- One invalid analysis setting (e.g. `min_valid: 1`) no longer throws away
  every other setting for the run; only that key falls back to its default, and
  the note names it. `config.yaml` / `experiment.yaml` validation stays strict.

### Fixed

- `.REJECTED.txt` notes are written whole (temp file + rename), so a person or
  the app never opens one that exists but is still empty. Windows CI caught the
  gap on Python 3.14.

### Added

- Added an `.obvious` onboarding contract — agent guidance (`.obvious/obvious.md`),
  codebase map, repo config, and a local-dev skill — generated from a verified
  dev stack (ruff clean, 350 passed / 23 skipped, testbed end-to-end with the
  fake FragPipe). (#1)
- `runners/isodtb.py`: the isoDTB site-merge runner entry, a thin adapter over
  the existing port in `downstream/isodtb.py`, with golden tests. (#9)
- jsdom test harness for the report front end, run in CI on both OSes. (#8)
- `docs/FIRST_REAL_RUN.md`: the runbook for the first real FragPipe run on the
  PC (isoDTB, then DIA). (#31)

### Changed

- Dev installs now pin `pip>=26.2` (`deploy/dev_install.ps1` and the `dev`
  extra). (#3)
- CI tests Python 3.14 (what the lab PC runs) and 3.12 (the exe build) on
  Windows, and 3.14 and 3.11 (the floor) on Linux; previously only 3.12. The
  test job has a 15-minute timeout. (#21)
- Agents no longer merge their own PRs: the automerge workflow (#10, #11) is
  removed, `.obvious/config.yml` requires a human merge, and `.github/CODEOWNERS`
  requests owner review on every PR (D31).
- Naming: replicate and fraction tails are bounded to 1–999, and a digit run
  longer than three is never read as one, so a date-shaped tail can't become a
  replicate or fraction. (#32, #41)
- Naming: the six-digit `MMDDYY` date only matches when its year is within the
  last 25 years through next year, so run IDs like `113056` aren't read as
  dates. (#35, #41)

### Fixed

- Intake hash-verifies cross-volume copies (per-file SHA-256 over source and
  destination) before deleting the source, so a same-size corrupted copy can no
  longer silently destroy the original experiment. (#2)
- Post-move intake failures now file a minimal queued record at the destination:
  the folder is already moved, so the reconciliation sweeps re-adopt it as a job
  instead of the experiment being silently lost from all tracking. (#4)
- Cross-volume source cleanup retries transient Windows locks
  (antivirus/indexer) with bounded backoff; a lock that survives the retries
  writes a truthful `.REJECTED.txt` at the source — the filed copy is complete
  and verified, so keep it and delete the partial inbox folder — instead of the
  partial folder being re-offered and filed as an incomplete experiment. (#4)
- Desktop-shortcut creation passes the PowerShell command as base64
  `-EncodedCommand` (UTF-16LE), so install paths containing apostrophes no
  longer break shortcut creation. (#3)
- `experiment.yaml` override path fields (`user:`, `files.*.experiment:`) are
  validated before any path join: path-like values are refused (surfaced as the
  existing inbox `.REJECTED.txt` note) instead of being able to file data
  outside `users_root`. (#5)
- Installer execution requires a verified SHA-256 digest for the exact version:
  digest-less releases are never downloaded or installed, and a Downloads-found
  installer is verified against the release whose version it claims before it
  is run. (#6)
- A cross-volume copy that fails hash verification now removes its own partial
  destination (the source is untouched), so re-filing isn't wedged by
  "destination already exists"; if removal fails, the rejection names the
  leftover. (#34) The same cleanup now runs when the copy itself dies
  partway (disk full, a locked file), which used to leave a half-copy that
  got the retry rejected as "destination already exists".
- Drops with `.raw` files both at the top level and in `raw/` are rejected
  instead of silently filing a manifest of only one set; the app's Inbox view
  and naming window show the reason instead of a Tk traceback. (#36, #42)
- A raw file that vanishes (or can't be read) while a search is being prepared
  fails the job with a clear reason instead of leaving it queued forever. (#39)
- A cancel delivered while a search is starting is no longer discarded. (#38)
- FragPipe steps that die with a negative exit code (killed by a signal) are
  flagged as failed. (#27)
- A job missing from the ledger when its attempt starts fails visibly
  (`LedgerError`) instead of crashing the worker; ledger rebuild/adoption log
  every status file they skip. (#37)
- Tests: Windows CI hangs in the Tk dialog tests fixed (deterministic dialog
  drive, leak-proof teardown, bounded Tk root retry); edge-behavior tests
  pinned for ledger, watcher, worker, postprocess. (#12, #23, #25, #26, #28,
  #29, #30, #33, #40, #43)
- Tests: the two "raw vanishes during prepare" worker tests no longer depend on
  how `Path.is_file()` is implemented, so they pass on Python 3.14 as well as
  CI's 3.12. A testbed test that faked Windows by patching `os.name` globally
  crashed the whole suite on Python 3.11; it now stubs the module instead.
