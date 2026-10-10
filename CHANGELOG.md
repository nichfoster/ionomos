# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **A `config.yaml` saved in Notepad as "ANSI" no longer stops Ionomos**
  (D86). This had happened since LabWatch (2026-09-16 on the PC): an em dash
  in a comment became byte 0x97, and Ionomos stopped with a bare
  `UnicodeDecodeError`. Such a file is now read as Windows-1252, with a
  warning that names the file, the byte and its line, and says how to save
  it as UTF-8. A UTF-8 file with a BOM and a UTF-16 file ("Unicode" in
  Notepad) are read as well. A byte Windows-1252 doesn't have either stops
  with a config error saying the same. The same goes for `experiment.yaml`,
  learned aliases and benchmark specs. A running watcher keeps its settings
  on such an error instead of failing.
- **A support bundle or diagnostics from an "ANSI" config missed what it
  should hide** (D86). They read the file as empty. A webhook address was
  then left in the logs, and in the config itself when written the short way
  (`teams: <address>`); `url:` and `password:` lines were hidden anyway. The
  users and aliases listed only in the config were not anonymised.

### Documentation

- **`docs/REAL_RUNS.md`: the real runs on the lab PC** (D83). These are the
  two DIA searches of 2026-09-23 (0.5.1, 0.5.3) and the analysis of a
  FragPipe GUI result on 2026-10-06 (0.18.0), with what went wrong in each
  and which release fixed it. FIRST_REAL_RUN.md, ROADMAP.md, PROTEOMICS_PC.md,
  VALIDATION.md and TESTING.md no longer say nothing has run on a real
  FragPipe. They record what those runs answered: `fragpipe.bat` works, `.raw`
  works for DIA, FragPipe 24 uses its own DIA-NN 2.3.2, and the PC has .NET 6.
- **Why fragpipe-analyst.org showed a hit that Ionomos didn't**: the protein
  was measured in one condition only. The Ionomos run's global
  missing-value filter (≥ 66 % of all samples) removed it, while the web app
  has no filter by default and imputed its missing values. Without the
  filter, Ionomos gives 19 of the web's 21 hits, that protein included. The
  port was not changed. (A first version of this entry blamed the
  normalisation.)
- New open problems in ROADMAP.md:
  - contaminants are not removed from FragPipe DIA searches, because DIA-NN
    drops the `contam_` prefix;
  - a non-UTF-8 `config.yaml` still crashes;
  - `experiment.yaml` replicate numbers are not bounded;
  - an empty-vector control reads bait-enriched proteins as "down";
  - the FragPipeAnalystR golden is tidier than a real matrix;
  - a protein seen in one condition only vanishes when a global filter
    removes it: the "only in one condition" table is built after the filter;
  - leaving samples out makes the run-order stage fail.

## [0.18.1] - 2026-10-06

### Fixed

- **"Failed to load Python DLL … python314.dll" after installing or updating**
  (D81). This happened since 0.1: an update's installer inherited the old
  app's PyInstaller settings, so the new Ionomos.exe it opened looked for
  Python in the old app's unpacked `%TEMP%\_MEI…` folder, which was already
  deleted. The app now sets `PYINSTALLER_RESET_ENVIRONMENT` for every
  program it starts. The installer sets it too, so updating from an older
  version is fixed as well. The same reset fixes a watcher started from
  the app that lost files when the app was closed.
- **Dropping a folder on the app froze it** (0.18.0, D82). The drop handler
  called Tk from inside Windows' message handling, where Tk was already
  waiting, so the window hung on the first drop. The handler now only puts
  the dropped names in a queue, and the app's timer opens them. It gives
  Windows its own handler back when the window closes, and never raises into
  Windows. A dropped shortcut, zip file, missing drive or anything else that
  isn't a folder or table gets a message saying what to do instead of an
  error. The app comes to the front after a drop.

### Added

- **`Ionomos-fault.log`** next to the exe (D82): when the app or the watcher
  closes in a way Python can't catch, every thread's stack is written there.
  The diagnostics and **Report a problem…** include its end.

## [0.18.0] - 2026-10-06

### Added

- **Analyse FragPipe searches run outside Ionomos, checked first** (D80).
  The Analysis tab's **Folder…**, and a folder dragged from Explorer onto
  the app window (Windows), open a check window. It shows:
  - the FragPipe output it found, at any depth below the picked folder, or
    above it when a part such as `dia-quant-output/` or a table was picked
  - FragPipe's saved workflow, manifest, tables and log
  - what the folder will be analysed as, and why
  - every problem in plain words with what to do: a step that left no table
    (and the failed step and likely cause from FragPipe's log), a table cut
    off or holding only spectral counts, several outputs, no condition
    names, a read-only folder, raw files that were never searched

  Buttons fix what can be fixed there: analyse as another kind, use another
  output, take the conditions from the file names, open the log. Then
  **Review samples** or **Analyse now**. A finished job's folder only opens
  the window when something needs a look. `fpfolder.py`, `folder_check.py`,
  `dragdrop.py` (ctypes, no new dependency).
- **`ionomos check-folder <folder>`**: the same check in the terminal
  (exit 1 when the folder can't be analysed).

### Fixed

- A folder filed as one method but holding another kind of search is now
  analysed as what it holds. Before, a DIA search filed as TMT failed with
  "no tmt-report/abundance_*_MD.tsv found". When the filed method's table is
  missing and the tables (and the saved workflow, when there is one) clearly
  show another kind of search, that kind is read and a note says so.
  `ionomos analyze --method` still reads exactly the method asked for.
- A folder with no Ionomos record now takes its method from FragPipe's saved
  workflow before its table names.
- A FragPipe output in a sub-folder (`<experiment>/fp_out/`) is read from
  there. FragPipe's own `fragpipe-files.fp-manifest` names the samples and
  conditions, as Ionomos' manifest does for its own searches.
- TMT: `abundance_*_GN.tsv` and `abundance_*_None.tsv`, which TMT-Integrator
  writes when median centring is off, are read (with a note); before, only
  `_MD` was.

## [0.17.0] - 2026-10-04

### Added

- **MaxLFQ protein roll-up** (D76): `analysis.rollup: auto | median_polish |
  maxlfq`. It decides how a table of peptides or precursors becomes proteins:
  Sage's `lfq.tsv`, the MSstats format, and DIA-NN and Spectronaut long reports.
  MaxLFQ (Cox et al. 2014) is computed in pure Python
  (`downstream/rollup.py`). For each pair of samples it takes the median
  log-ratio of their shared features, solves least squares per connected
  component and scales to the summed intensity. It matches R's
  `iq::maxLFQ()` 2.0.1 to 1e-9 on 54 proteins, including disconnected sample
  groups, missing values, single features and single samples. Its profiles
  match `diann::diann_maxlfq()` to 1e-3. `auto` keeps every loader's
  previous behaviour: median polish for Sage and the MSstats format, and
  DIA-NN's `PG.MaxLFQ` and Spectronaut's `PG.Quantity` as they are. For a
  DIA-NN report, `maxlfq` / `median_polish` uses `Precursor.Normalised`; for
  Spectronaut it uses `FG.Quantity`, now an optional column in
  `ionomos spectronaut-columns`. When those columns are missing, or the table
  already holds proteins, a note says the setting was not used. When a
  protein's samples share no feature, a note gives how many proteins that
  affected.
- **`analysis.f_test: auto | off`** (D76): `off` switches off the moderated
  F-test ("any change") that limma runs on 3+ conditions of intensity data.
  The report's Methods and settings and `analysis.json` (`f_test: {off:
  true}`) then say it was switched off. The default `auto` behaves as
  before. Time-course F-tests are not affected; `time_course` controls those.
- **`ionomos benchmark --kind rollup`** (D76): simulated peptide tables with
  their own ionisation offsets, missing values and interferences, rolled up
  by median polish or MaxLFQ, with and without imputation
  (`benchmark_simulated_rollup.*`).

- **Spline fits for long time courses** (D77). A series with 7 or more time
  points (`analysis.time_model: auto`, the default) or any series with
  `time_model: spline` is fitted as a natural cubic spline in hours
  (`time_spline_df`, default 4, at most the time points − 2), as in the limma
  User's Guide for many time points: the moderated F on the spline
  coefficients, the series-vs-control test on the interaction of two curves
  (`~group * ns(time)`), and the profile is the fitted curve's change from
  the first time point. `downstream/splines.py` is R's `splines::ns`
  ported step by step. The report's profile and the static `time_profiles`
  figure draw the fitted curve on an axis in hours; `time_course.tsv` gains
  a `model` column, `analysis.json` → `time_course` a `time_model` and
  `spline` per series. A `time_spline_df` the series can't carry raises
  the new `TIME_SPLINE` issue. Series with up to 6 time points are tested
  exactly as before. Checked against R 4.6.1 `splines::ns` (1e-12) and
  limma 3.68.5 (10,350 values, worst relative difference 5.4e-10).

- **The assistant can propose a change; a person confirms it** (D75,
  [docs/ASSISTANT.md](docs/ASSISTANT.md#proposals-and-the-confirm-window)).
  Asked to act, the local assistant may propose one change per question:
  retry a failed search, give a sample another condition, leave a sample out
  (or use it again), one analysis setting (imputation, normalisation, p ≤,
  |log2FC| ≥, the control, what is compared, the comparisons) or a
  condition's role. Ionomos checks it first (a sample or condition the
  experiment has, a value the analysis accepts, a failed job for a retry) and
  opens a window written by Ionomos, not by the model: the job, what Confirm
  does and the `experiment.yaml` diff, with **Cancel** (focused) and
  **Confirm**. Typing "yes" does nothing. Confirm re-checks the proposal
  against the job and file as they are now and uses the app's own Retry and
  the experiment editor's Save. `ionomos ask` prints the proposal and the
  command or app steps; it never applies it. Proposals and decisions are in
  the assistant's audit log. 13 new scenarios (66 in all).
- **experiment.yaml backups**: the experiment editor's Save (and a confirmed
  proposal) keeps the version it replaces in the experiment's
  `experiment-backups/` folder (D75). Backups are never replaced or removed.

- **Acquisition time per raw file** (D78). Intake records when each raw file
  was acquired in `ionomos.json` (`acquisition`), read from the Thermo
  `.raw` file's own header (the acquisition start Xcalibur writes; only the
  first bytes are read, nothing is written), else from ThermoRawFileParser's
  output, the Xcalibur stamp in the name, or the file's modification time,
  marked approximate. Each entry says where its time came from.
- **Run order QC** (D78). A **Run order** tab under Quality control: each
  sample's identifications, missing values, signal, PSMs, mass error and
  missed cleavages (or DIA-NN's precursors and mass accuracy) in the order of
  acquisition, with a drift test within each condition and which runs each
  condition was in. Two new warnings: `RUN_ORDER_DRIFT` (a number drifts
  over the run) and `RUN_ORDER_CONFOUNDED` (the conditions were run in
  blocks, so a drift would look like biology). `analysis.json` →
  `run_order`; a `run_order` figure for slides (`ionomos export --figures
  run_order`, `analysis.export.figures`, the report's zip).

- **Phosphoproteomics, opt-in** (D79, `downstream/phospho.py`). Nothing
  changes unless `phospho: true` is set under `analysis:`:
  - The search's phosphosite table is analysed instead of its proteins:
    FragPipe's `combined_site_STY_79.9663.tsv` (IonQuant, label-free), TMT-Integrator's
    `abundance_single-site_MD.tsv`, or DIA-NN's `report.phosphosites_90.tsv`
    / `_99.tsv`. Sites are named like `MAPK1 T185` and go through the same
    filter, normalisation, imputation, limma, volcano and report as proteins.
  - A localisation filter, `phospho_min_localization` (0.75): applied on
    the best localisation probability of FragPipe's label-free table (and per
    sample with `phospho_localization_per_sample`); for TMT-Integrator and
    DIA-NN, which filter first, their threshold is read and compared
    (`PHOSPHO_LOCALISATION`).
  - `protein_correction` (D70) works on phosphosite comparisons: each site's
    change minus the same comparison's protein change in an unenriched
    proteome, MSstatsPTM's adjustment, as a second comparison.
  - **Kinase activity (KSEA)** with a kinase-substrate table the lab
    downloads (`kinase_substrates`: PhosphoSitePlus's
    `Kinase_Substrate_Dataset`, or KSEAapp's PSP&NetworKIN file; never
    shipped): a z-score per kinase and comparison, `ksea_min_substrates`
    (5), Benjamini-Hochberg; `results/kinase_activity.tsv`, a bar chart in
    the report and the `kinase_activity` figure for slides. The scores are
    KSEAapp 2.0's (checked against it); the p-value is two-sided.
- **STRING partners among the hits** (D79): `string_network` names a STRING
  download (protein.links with protein.info, or a website export), and the
  report lists each hit's partners among the same comparison's hits;
  `results/string_partners.tsv`. Works for proteins too.
- Doctor issues with help: `PHOSPHO_TABLE`, `PHOSPHO_LOCALISATION`,
  `KINASE_SUBSTRATES`, `STRING_NETWORK`.

### Changed

- Every Retry (the Jobs tab, the failed-search pop-up, `ionomos retry`, a
  confirmed proposal) is one function, `worker.request_retry`; the Jobs tab's
  Retry now also closes the job's "search failed" item, as the others did
  (D75).
- The assistant's system prompt and tools changed (five proposal tools), so
  its prompt digest changed: score real models again with `ionomos ask-eval`.

- **The QC trend orders runs by the raw file's header** (D78) when the
  experiment's `ionomos.json` or the file has one, before the name stamp and
  the file time.

- `proteincorr.correct_comparison`: the per-comparison MSstatsPTM
  adjustment is one function, shared by isoDTB site ratios and phosphosites
  (no change in results).
- A site-level analysis writes no FragPipe-Analyst `reproduce_in_R.R` (it
  would read the site table as proteins).


## [0.16.0] - 2026-10-03

### Added

- **Anonymised "Copy diagnostics"** (D74). The app's **Copy diagnostics**
  now replaces the lab's names with the same pseudonyms a bundle uses, and
  the text is searched for every name Ionomos knows before it reaches the
  clipboard (a name left: nothing is copied, and the message says so).
  **Copy with real names** copies the text as before. The saved copy in the
  log folder has its key file next to it, so `ionomos bundle translate`
  reads a pasted copy back. `ionomos diagnose --anonymise` does the same in
  a terminal.
- **DIA-NN's main report and peptide / ion tables in a bundle, on request**
  (D74): `ionomos bundle --include diann-report,peptides` (implies
  `--level validate`) and a box in the **Report a problem** window. Main
  report: `report.tsv`, or `report.parquet` written as text so its names can
  be replaced (needs pyarrow). Peptide level: FragPipe `peptide.tsv` /
  `ion.tsv`, DIA-NN's precursor matrix, MaxQuant `peptides.txt` /
  `modificationSpecificPeptides.txt`. Each is anonymised like the other
  tables, row-sampled above `--extra-mb` (200 MB), and the first thing the
  size limit leaves out. Spectral libraries never go in.
- **`ionomos bundle inspect` checks every file for real names** (D74): each
  file of the zip, and its name, is searched for the lab's user,
  experiment and sample names (on the lab's PC) and for the originals in the
  key file (next to the zip, or `--key`), and listed as clean or with what
  was found.
- **`ionomos spectronaut-columns [--out DIR]`** (D74): the columns a
  Spectronaut report needs for Ionomos and how to make a report schema with
  exactly those in Spectronaut. Ionomos does not ship an `.rs` schema file:
  its format is Spectronaut's own and not published.

- **Ask about this** (D72, [docs/ASSISTANT.md](docs/ASSISTANT.md#ask-about-this)):
  a button in every pop-up and in the needs-attention list. It asks the local
  assistant a question written for that kind of item (editable) and shows the
  answer with its sources, as plain text, in a window of its own; the answer
  is made off the Tk thread. Not set up, not answering or paused: Ionomos's own
  explanation and the help, as a normal state. Nothing from the item's names
  goes into the question, and an item without a job is named to the model by
  its kind and time.
- **`ionomos ask-eval`** (D72): scores a model on this PC over the assistant's
  scenario corpus with the same rubric as CI, timing the first token and each
  answer, idle and while a search runs, and writes a scorecard (JSON and a
  table) that is never written over. `--scripted` checks the runner without a
  model. Only an address on this PC is accepted, checked before anything is
  built. The corpus (`ionomos/assistant/scenarios/`) now ships with Ionomos;
  15 scenarios that only make sense with their script are marked
  `harness_only` and left out of the scorecard.
- **`assistant.keep_alive` and `assistant.while_searching`** (D72): how long
  the runtime keeps the model loaded (sent only when set; Ollama reads it),
  and what changes while the worker runs a search (another model or address,
  a shorter keep-alive, a longer timeout, or a pause), read from the worker's
  heartbeat. The table of what Ollama and llama-server honour is in
  docs/ASSISTANT.md.

- **isoDTB site changes corrected for protein abundance** (D70, ROADMAP 5C #3):
  `analysis.protein_correction: {proteome, match, conditions}` names an
  unenriched proteome (an analysed Ionomos experiment, an MSstats
  groupComparison table or an Ionomos `*_differential.tsv`; only read). Each
  site's log2 heavy / light minus its protein's change on the same scale
  (from `liganded_direction`), with MSstatsPTM's SE, Satterthwaite df and BH
  per condition, checked against MSstatsPTM 2.14.0 to 1e-9
  (`tests/golden/ptm/`). Reported beside the uncorrected comparison as
  `<condition> (log2 H/L vs 0, protein-corrected)` with its own volcano and
  table; sites whose protein is not found are flagged. Off by default.
  New issues `PROTEIN_CORRECTION_CONDITIONS` (a site condition without its
  proteome comparison; never guessed) and `PROTEIN_CORRECTION`.
- **Opt-in centring of isoDTB ratios** (D70): `analysis.ratio_centre: none |
  median | auto` (default `none`). `auto` centres a condition's replicates on
  their stable sites only when one is clearly off 0 (a heavy / light mixing
  error), without the median's shift when many sites go one way. The
  offsets are always measured; with `none` a clear one is the note
  `RATIO_OFFSET`. Liganded calls say which ratios they used. `ionomos
  benchmark --kind isodtb` runs the centring settings too; a `centring` grid
  holds the D70 numbers.
- Every `*_differential.tsv` has `se` and `df` columns (the standard error
  and degrees of freedom behind `t`).
- `cysteine_sites.tsv` gets `<compound> protein_log2_R` and
  `log2_R_corrected` when the protein correction ran.

### Changed

- CI's test jobs may run 25 minutes instead of 15: the Windows jobs took
  up to 14 min 46 s with the 0.16.0 suite (D73).

- `ionomos bundle translate KEY FILE` reads the file line by line (a main
  report can be gigabytes).
- The Spectronaut loader reads its columns from the same list
  (`engines.SPECTRONAUT_COLUMNS`) that `spectronaut-columns` prints.

- The assistant's time to first token waits for the first generated token
  (text, reasoning or a tool call), not the opening event. Answers and audit
  records say whether a search was running (`mode`).

- **TMT across plexes: the normalisation looks within each plex, and IRS on
  the plex means is no longer liberal** (D71, from the D66 benchmark's open
  questions):
  - With several TMT plexes that are not on one scale (IRS off, or not
    possible), `normalize: auto`'s composition check and the `ratio` method
    are worked out within each plex and combined. Across such plexes a
    protein jumps with the plex, which hid a pulldown: with the plex as a
    block, 59 % of the calls at adjusted p alone were false (unchanged
    proteins -0.19 to -0.26 log2 off); now 4.7 % (within 0.04). The doctor's
    `NORMALISATION_COMPOSITION` now also fires there, and says the check was
    made within plexes. Plexes joined by IRS are compared all together as
    before, so the default's numbers are unchanged (4.4 % / 3.8 %); without
    plexes the check is the D64 one to the last digit.
  - IRS on each plex's own mean (`irs: sum`, used when no reference channel
    is found) estimates the plex level from the channels it then tests;
    limma's residual df are now reduced by the plexes - 1 for each protein
    (`plex.df_spent`), unless the design already has a block per plex. FDP
    on the simulated grid 6.6 % → 5.1 % with changes both ways, 5.3 % → 4.0 %
    in a pulldown (worst scenario 9.3 % → 7.7 %), sensitivity unchanged; checked against limma 3.68.5 in R
    (`tests/golden/tmt_sum/`). `analysis.json`'s `model` says so
    (`plex_df`).
  - "How far to trust this" has a **TMT plexes** line: how the plexes were
    put on one scale (reference, plex means with the df reduced, a plex
    block), marked "check" when they are neither on one scale nor in the
    model, or when a t-test follows IRS on the plex means.
  - New doctor warning `TMT_PLEXES_NOT_IN_MODEL` (with help): `irs: none`
    and no block for the plex, which keeps the tests valid but finds 40 %
    instead of 95 % of 2-fold changes.
  - Two more calibration guards in the test suite (IRS on the plex means;
    a pulldown without IRS).

- The liganded-site rule text ends with the ratios it used ("on the ratios as
  measured (not centred)").

### Fixed

- **A hung test run now says where it hung and ends** (D73). The suite had
  hung on macOS three times with nothing on screen until a 30-minute limit
  killed it. It did not hang again in 14 full runs and about 3,300 more
  tests from the process- and thread-heavy files on macOS / Python 3.14,
  with up to three suites running at once, so its cause is not known.
  Instead:
  a test still running after 5 minutes prints every thread's stack and ends
  the run (`faulthandler_timeout` + `faulthandler_exit_on_timeout` in
  `pyproject.toml`; pytest ≥ 9 in `[dev]`); a process that can't exit after
  its last test is dumped and ended after 120 s (`tests/conftest.py`,
  `IONOMOS_TEST_EXIT_SECONDS`); every wait in the tests that had no time
  limit has one now (a `proc.wait()`, a `join()`, the `ps` / `tasklist`
  probes, `python -m ionomos` and `git` runs, the R probe), a second
  `ionomos run` whose output is read is killed and drained if it doesn't
  answer, `while worker.run_once()` loops are capped, and the fake SMTP
  server stops when its client goes away in the middle of a message instead of
  spinning. `tests/test_hang_guards.py` checks the guards themselves.
- **Windows: a `taskkill` or `tasklist` that never returns can't hold the
  worker** (D73). Stopping a search, the time limit, a cancel, stopping a
  leftover FragPipe and the app's Stop gave `taskkill /T` no time limit; it
  now gets 60 s, then Ionomos goes on as if it had failed (a stuck
  `tasklist` counts as "not running").


## [0.15.0] - 2026-10-02

### Added

- **Settings and checks in the app instead of `config.yaml` or a terminal**
  (D67):
  - **Analysis tab → Figure style**: the lab's style for exported figures
    (`analysis.export`, D62): size preset or a custom size, text size and
    font, colours (with a colour picker for a custom palette), background,
    what the "Export for slides" zip holds, and which figures are written to
    `results/figures/` after each analysis. Empty fields show and take the
    default; keys the page doesn't show are kept. **Check** says what the
    figures will look like, or what is wrong.
  - **Analysis tab → Check accuracy**: **Compare** an analysis with a
    reference result (a table or another analysed folder) and **Run
    benchmark** on simulated data (quick / standard, optionally with an
    experiment's settings) or on a benchmark sample with its expected
    ratios. Runs in the background, shows the verdict in colour and opens
    the page. Same code as `ionomos compare` / `ionomos benchmark`.
  - **8 Notifications** tab: `notify:` (Teams, Slack, a JSON webhook,
    SMTP email; which events; names or not), with **Send test** (the same
    code as `ionomos notify-test`, on the values in the window). Addresses
    and the password are masked unless "Show" is ticked, and never logged.
  - Help entries for each (`faq.figure-style`, `faq.check-accuracy`, and
    `faq.notify` rewritten for the tab); a Help button on each page.

- **Roles in the experiment editor and the review window** (D65,
  [docs/WORKFLOWS.md](docs/WORKFLOWS.md)). Each condition is listed with its
  number of samples and its role (control, compound, competition of a
  compound, pool / reference, QC standard) and where the role came from. A
  list changes it; **automatic** goes back to the name. A role read from a
  word that can mean something else (`pre`, `block`, `cold`, `10x`) is
  marked **?** with a **Confirm** button. Under the list: the comparisons
  that will be run, in words, and what uneven groups mean ("DMSO has 2
  samples, Probe 4: a feature needs 1 of 2 DMSO values"). Choices are saved
  as `analysis.roles` in `experiment.yaml`. The review window shows this for
  DIA and label-free drops.

- **Unequal groups checked against R's limma** (D66, [docs/VALIDATION.md](docs/VALIDATION.md)).
  A competition experiment with DMSO 2, Probe 4 and Probe_Comp 4, missing
  values and rows on each edge of the filters, goes through the analysis'
  own steps (filter, median normalisation, no imputation or Perseus-type,
  the role comparisons, `small_group_min_valid: half` and `same`, limma, BH)
  and agrees with limma 3.68.5 to 1e-8 for every feature
  (`tests/golden/unequal/`, with the R script that made it).
- **`ionomos benchmark --kind isodtb` and `--kind tmt`** (D66). The simulated
  benchmark now covers isoDTB site ratios (FragPipe's label quant through the
  lab's site table; replicates, sites changed one way, a heavy / light
  mixing error) and several TMT plexes with a pooled reference (MaxQuant's
  reporter intensities; IRS on the pool or on the plex means or none, `auto`
  or `median` normalisation, a pulldown). Each kind writes its own
  `benchmark_simulated_<kind>.*`; `--like` picks the experiment's kind. The
  test suite has a calibration guard for each. Measured: both defaults are
  calibrated (isoDTB 4.9 % false discoveries with 3 – 4 replicates, TMT 3.8 –
  4.4 % where 4 – 4.75 % is aimed at); in a TMT pulldown, median centring
  after IRS makes 67 % of the calls at adjusted p alone false and `auto`
  holds. What the grids found and was not changed (a heavy / light mixing
  error is not corrected; TMT without IRS; IRS on plex means slightly
  liberal; two isoDTB replicates) is in the roadmap's open questions with
  the numbers.

- **Figures for slides from the dose-response, time-course and liganded-site
  sections** (D68, `downstream/sectionfigs.py`). `ionomos export` and
  `analysis.export.figures` now also draw, in the same export style:
  - `dose_potency_<compound>.svg`: pEC50 against the curve's fold change,
  - `dose_curves_<compound>.svg`: a grid of curves (points, the fit, the 95%
    interval of pEC50), the six most relevant regulated or those you name,
  - `time_patterns_<series>.svg` and `time_profiles_<series>.svg`: the
    patterns of changing features, and the most significant features over
    time with the series they were compared with,
  - `liganded_rank_<compound>.svg` and `liganded_selectivity.svg`: sites
    ranked by competition ratio, and which sites each compound ligands.
  `figures:` takes these names or `dose`, `time`, `liganded`. The report's
  **Export for slides** .zip holds them too (one file per curve and feature
  there), as well as each series' patterns and the selectivity map.
- **Choose what to export**: `ionomos export --list` names every figure the
  report can draw and what can be chosen for it; `--figures` takes kinds,
  groups or those names (`volcano_Drug*`); `--features EGFR,BTK` and
  `--top N` choose the curves, profiles and sites drawn. A grid that would be
  too small to read at the chosen size draws fewer panels and says so.
- **PNG from `ionomos export`** (`--format png | both`, `--png-dpi`,
  `--png-scale`, `--renderer`), drawn by a program the computer already has:
  cairosvg, resvg, rsvg-convert or Inkscape (also found in its usual install
  folder). Ionomos still installs nothing for it; without one it says what to
  install and writes nothing. The PNG carries its print size and the
  cut-offs, as the report's does.

- **A fault-injection suite for FragPipe searches** (D69,
  `tests/test_faults.py`). The testbed's fake FragPipe acts out a hang, being
  ended from outside, a full disk, a raw file vanishing, garbled and huge
  console output, and empty, header-only, cut-off or missing result tables,
  per experiment (`fake_fragpipe_mode.txt`) or for all of them
  (`IONOMOS_FAKE_FP_MODE`, comma-separated). The testbed has `fp_cut_table`
  and `fp_hang` samples, and the stress tester mixes such faults into its drops.
- **TMT drops laid out one folder per plex** (`<plex>\*.raw`) are filed and
  searched as dropped: each folder is a plex, with its own `annotation.txt`.
  A flat drop with several plexes is filed as before, with a warning that
  FragPipe will name the channels itself (testbed samples `tmt_plexes`,
  `tmt_flat_plexes`).
- New plain-English causes for a failed search: ended from outside, a crash,
  the time limit, a runaway console log, a result table not written to the
  end, a run that did not finish, a raw file that disappeared during the
  search, an error inside Ionomos. New holds, with help pages: a raw file
  still open in another program, a path with a space (on Windows), earlier
  output that can't be moved aside.

### Changed

- `ionomos compare`, `ionomos benchmark` and `ionomos notify-test` now run
  through `accuracy.py` / `notify.run_test`, shared with the app. Their
  output is unchanged.

- In a report with more than one compound or time series, an exported
  dose-response curve or time-course feature is named after both
  (`dose_curve_CmpdA_EGFR.svg`), so the .zip keeps one of each.

### Fixed

- **A journal-size figure style got slide-size text.** An `analysis.export`
  block naming `size: col1` (or `col2`, `half`) without `font_pt` was read
  by the app with the slide's 14 pt and written back that way on the next
  Save. Now the size's own text size applies (7 pt for a journal column).

- **The experiment editor could not take a choice back.** It merged its
  choices into the saved `analysis:` block, so a sample used again, a role
  back to automatic or cleared comparisons stayed in `experiment.yaml`. The
  editor now writes the whole block (D65).

- **A raw file in a drop laid out one folder per plex could not be removed
  from the app** (D69). The inbox list, the review window's **Delete** and
  the name check now find raws there too (`intake.raw_paths`).

- **The experiment editor's Normalisation list lacked `auto` and `ratio`**
  (D64). It now offers the same choices as the Analysis tab.

- **A FragPipe left running by an Ionomos that was ended from Task Manager
  or crashed ran beside the re-run of its job.** The next start now stops it
  first, only when it is certainly the process Ionomos started.
- **A job found running at start-up** kept saying `running` in its folder;
  after three interruptions it was failed without a `FAILED.txt`.
- **A result table cut off by a full disk counted as a finished search**; so
  did exit code 0 without the end line and without any result table.
- **An error inside Ionomos during a search** (e.g. a full disk for its own
  files) left the job `running` and FragPipe possibly unwatched.
- **Re-running a job twice within one second** failed it (the earlier output's
  folder name was taken).
- **A second job row for the same experiment folder** searched it again over
  the first job's output; it is now refused as a duplicate.
- **Console logs**: the reason's "last lines" read the whole log into memory;
  text in the Windows code page, UTF-16 and colour codes are now read
  correctly in `FAILED.txt` and the hints.


- **A test of the slow-copy wait failed now and then on Windows CI.** Its
  settle time (0.3 s) was only six times the pause between copied files, so
  a slow runner made the watcher file a half-copied folder. The test now
  uses 2 s. The app's own settle time (`stable_seconds`, 60 s) is unchanged.

## [0.14.1] - 2026-10-01

### Changed

- **Normalisation: `auto` is the new default** (D64, [docs/WORKFLOWS.md](docs/WORKFLOWS.md)).
  It is median centring, with identical numbers, unless that would shift
  the conditions against each other; then the samples are normalised on the
  ratios of their stable features (`ratio`, also selectable). An existing
  `config.yaml` that says `normalize: median` keeps median centring; set it
  to `auto` on the Analysis tab.

### Fixed

- **A pulldown's unchanged proteins were shifted by the normalisation.** When
  many features are enriched in one direction, median centring moved every
  unchanged feature the other way (about 0.2 log2 with 8 % enriched in
  simulation), and with two controls some passed the fold-change cut-off.
  `auto` now detects this and normalises on stable features: the shift
  falls to about 0.01 log2 and the false hits from 11 to 2 over 8 simulated
  experiments without imputation (6 to 4 with it). With `median` or `gn`
  chosen, the report now asks (`NORMALISATION_COMPOSITION`). `analysis.json`
  → `normalisation` records what was used. Checked on simulated pulldowns
  only.

## [0.14.0] - 2026-10-01

### Added

- **The analysis knows the experiment's design** (D61; `downstream/roles.py`).
  Each condition gets a role: control, compound, competition (the probe plus
  a competitor), pooled reference or QC standard. Roles are read from the
  condition names, from `analysis.roles` in `experiment.yaml` /
  `config.yaml`, or from a `characteristics[role]` column of an SDRF, and
  are shown in the report (Methods → Settings used) and in `analysis.json`
  → `roles`.
  - With a competition condition (`Probe_Comp`, `Probe+Comp`, `ProbeComp`,
    `Comp`, `…_competition`, `…_excess`) the default comparisons follow the
    design: compound vs control, competition vs its compound, competition vs
    control. A second control, a pool and a QC standard are not compared.
    `comparisons:`, `de_type: all | others` and `role_comparisons: false`
    work as before; with `control:` set, every condition is still compared
    with it. Without a competition condition nothing changes.
  - **Specific targets**: per compound, the features enriched against the
    control and competed off by the competitor, each at the report's
    cut-offs. `results/specific_targets.tsv` (both fold changes, both
    adjusted p-values), `analysis.json` → `specific_targets`, and a report
    section with enrichment against competition and the quadrant of
    specific binders marked.
  - **Unequal groups** (DMSO n=2 against a compound n=4) are handled
    knowingly. The report says how many samples each side of each
    comparison had, and the Power tab gives the minimum detectable fold
    change per comparison with those numbers. Without imputation (TMT), the
    smaller group of an unequal comparison needs half its samples measured
    instead of `min_valid` (`small_group_min_valid: half`; `same` is the
    earlier rule): on simulated TMT data with two DMSO channels, 6.1 % of
    the features were untested against DMSO before and 0.1 % are now, with
    no false hit among them. The sample scorecard no longer scores a sample
    of a small group as scattering more than the others. A low-confidence
    label names the real group size.
  - Two new notes: `COMPETITION_DESIGN` (what was read, how to change it)
    and `ROLES_UNSURE` (asks, when a name is ambiguous: `Probe_pre`,
    `Probe_10x`, or a competition that can't be linked to one compound).
    Glossary entries for role, control, compound and competition.
  - New settings: `roles`, `competition_keywords`,
    `competition_keywords_weak`, `role_comparisons`,
    `small_group_min_valid`. Competition keywords are also on the Analysis
    tab (Lab defaults).
  - **Not confirmed by the lab**: the competition keywords (`Comp` is the
    one form seen on the lab PC) and the specific-targets rule. **Not
    tested on a real experiment**: simulated data only.

- **Export for slides, and one export style** (D62). Every chart in the
  report has **SVG**, **PNG** and **Export…**; the top bar has **Export for
  slides**.
  - **Export…** opens one dialog with a preview: the size (16:9 slide, 4:3
    slide, half a slide, one or two journal columns, or a custom width and
    height in px or mm), text size (pt) and font, line and point size, the
    palette (the default, colour-blind safe, greyscale, or your own up / down
    / neutral colours), a white, dark or transparent background, title and
    subtitle (on / off, editable), legend, the cut-offs line, which names are
    drawn, and the PNG resolution (1× to 4×, or 150 / 300 / 600 dpi). The
    figure can be downloaded as SVG or PNG, or copied to the clipboard as an
    image where the browser allows it.
  - The SVG keeps text as text, writes colours out (no CSS), names the font
    with fallbacks and uses no `foreignObject`. Every file records the
    experiment, comparison, cut-offs, hit filters and test (SVG `<title>` and
    `<desc>`; PNG a `Description` text chunk and its print size), so a figure
    on a slide can be traced back.
  - **Export for slides** saves one .zip, built in the browser: every figure
    (a volcano and p-value histogram per comparison, compare, heatmap,
    enrichment with a result, each QC tab, and what else the report shows) as
    SVG and / or PNG, the tables as CSV, the style, and a README that lists
    each file with its cut-offs. File names are safe on Windows.
  - The style is kept in the browser and used by every report opened in it.
    **Save style** / **Load style** share it as a small JSON file; **Reset to
    lab defaults** goes back to the lab's style. A loaded file is checked key
    by key.
  - **`ionomos export <experiment or results folder>`** writes the volcano,
    PCA, heatmap and correlation figures as SVG to `results/figures/` from
    the finished report's own data, with the same style options (`--preset`,
    `--palette`, `--font-pt`, `--style FILE` …). SVG only: PNG needs a
    renderer, and no dependency was added for it; the command says so.
  - **`analysis.export:`** in `config.yaml` (or `experiment.yaml`) holds the
    lab's style and `figures:`, the static figures written after every
    analysis (default: none). The config writer keeps the block, commented.
  - The heatmap, a canvas on screen, exports as SVG. The CV charts got export
    buttons.
  - **Options are easier to read**: plain labels with units, a tooltip on
    every control and QC tab, a **?** on the Plot and export groups, the
    cut-offs in force written under the options, and **Reset to lab
    defaults** (cut-offs, plot options, hit filters). **Reset** is now called
    **Reset cut-offs**. No option was removed and no default changed.
  - Changed with it: the SVG and PNG buttons now use the export style (before:
    the chart at its on-screen size, PNG at 3×). Text cells in every CSV
    export that start with `=`, `+`, `-` or `@` get a leading apostrophe.
  - **Not checked**: opening the SVG files in PowerPoint, Illustrator or
    Inkscape; any browser but Chromium; the real clipboard; Windows.
    docs/DECISIONS.md D62 lists what was verified and how.

- **FragPipe preflight** (D59; `preflight.py`): `ionomos preflight`, and the
  app's **Check FragPipe install** now runs it. Besides reading the
  installation from disk it starts FragPipe twice over, without a window and
  without a search: `fragpipe.bat --help` (version, Java, .NET) and one
  `--headless --dry-run` per FragPipe method, in which FragPipe itself
  checks its tools, the FASTA, the workflow and the TMT annotation and lists
  the commands it would run. It also checks: the launcher is `fragpipe.bat`,
  FragPipe's own Java, MSFragger / IonQuant / diaTracer and the Thermo
  reader folder, Philosopher, Python, DIA-NN, FragPipe's settings cache,
  each workflow's tools against what is installed, decoys, paths without
  spaces, free disk, RAM and threads against the settings, room for long
  paths, write permission. Each line says what to do. `--static` starts
  nothing; `--raw PATH` uses a real file for the dry run; `--method M`
  limits it. Its files go to a new folder under `<log_dir>\preflight\`.
- **Run fingerprint** (D59; `fingerprint.py`): every search, whatever its
  outcome, leaves `ionomos_run\run_fingerprint.json` next to the job: the
  launcher and command line, FragPipe's version block, the workflow's key
  settings, the names and sizes of the output files, the first and last
  lines of the console log, what Ionomos' parsers read out of it, and the
  timings. Text only, a few tens of kB; an earlier one is kept as
  `run_fingerprint_<time>.json`. It is for checking the parsers against the
  first real runs.
- **More failed searches get a plain cause** (`fragpipe.EXPLANATIONS`, from
  FragPipe's source and issue tracker): no Java for `fragpipe.bat`, an
  option FragPipe doesn't know, decoys missing or not about half, a path
  with a space, a licence that expired, MSFragger's `ext` folder missing, a
  missing .NET runtime, "Not enough memory allocated to MSFragger", the page
  file running out, a path or command line too long for Windows, DIA-NN that
  can't be started, a failed DIA-NN step, Python / FragPipe-SpecLib not set
  up, a TMT annotation FragPipe refuses, two raw files with one name,
  contradicting workflow settings, antivirus or a sync tool holding a file,
  odd characters in the FASTA.
- `fragpipe.config_python` (FragPipe's `--config-python`), in `config.yaml`
  and the app's Advanced tab. FragPipe 24 on Windows uses the Python inside
  its installation whatever this says; it is for other setups.

- **A bundle for troubleshooting and validation** (D63; `bundle.py`,
  docs/DEV_LOOP.md). **Report a problem…** is now a window with the note,
  the jobs, three boxes and a list of what will go into the zip with its
  size; it is also in the failed-search and failed-analysis pop-ups and on
  the Jobs tab (**Zip for troubleshooting…**). The zip is saved on the
  Desktop (also a OneDrive Desktop; else the log folder). Ionomos sends
  nothing. `ionomos bundle [JOB | FOLDER …] [--level diagnose|validate]
  [--out DIR] [--no-anonymise] [--keep-conditions] [--max-mb N] [--dry-run]`
  does the same from a terminal.
  - `diagnose`: settings, logs, crash files, needs-attention items, each
    job's status, run folder (workflow, manifest, console logs, a
    `run_fingerprint.json` when present) and `analysis.json`.
  - `validate`: also the search's result tables and `results/`, so that
    `ionomos bundle unpack ZIP DIR` and `ionomos --config DIR/config.yaml
    analyze DIR/<experiment>` repeat the analysis. `ionomos bundle inspect
    ZIP` says what a bundle holds.
  - Never raw / mzML / `.d` files, FASTA files (name, size, entry count and
    SHA-256 are recorded) or spectral libraries. A size limit (2,000 MB) and
    row-sampling of PSM tables over 25 MB, both listed in the zip.
  - Names are replaced by default: users, the PC, experiments, raw files,
    samples and conditions, e-mail and IP addresses, the same pseudonym for
    the same name in every file, table header and file name. Protein and
    gene identifiers and all numbers are kept. The key file is saved next to
    the zip, not in it (`ionomos bundle translate KEY FILE` turns an answer
    back). The finished zip is searched for every original; if one is found,
    no zip is saved.
  - `BUNDLE.json` and `README.txt` in the zip: version and build, level,
    what was included, capped or left out and why, file hashes.
  - `ionomos diagnose --zip` and the app's **Save diagnostics bundle** now
    make a `diagnose` bundle (anonymised, with a key file), and no longer
    replace a file of the same name.
  - Limits of the replacing (free text, names inside longer words, plain
    words of a name standing alone, pictures) are listed in the help
    (**What Ionomos will never do** → the zip for troubleshooting).
    **Not run on the lab's real folders or on Windows outside CI.**

- **Accuracy checks a lab member can run, and an analysis that holds up on
  messy tables** (D60; [docs/VALIDATION.md](docs/VALIDATION.md)).
  - **`ionomos compare <experiment folder> <reference>`**
    (`downstream/compare.py`): an Ionomos analysis against another result
    for the same experiment: another Ionomos run, or a results table
    (FragPipe-Analyst export, limma, MSstats long format, Perseus, the lab's
    R output). Features are matched by ID or gene. Per comparison it reports
    matched / only in one, Pearson and Spearman of log2FC, slope and offset,
    hit calls (both / only Ionomos / only reference, at each side's own
    cut-offs and at common ones), the largest disagreements, p-value
    agreement, and a verdict: `agrees`, `agrees after an offset of …`,
    `differs: …` or `not judged: …`, from thresholds the page states.
    Writes `compare.tsv`, `compare.json` and `compare.html` (scatter plots)
    into `results/`. Tested on references made from Ionomos' own result;
    **not on a real export of those tools**.
  - **`ionomos benchmark`** (`downstream/benchmark.py`). Without a folder:
    the pipeline on simulated tables with planted changes over a grid
    (2 to 6 replicates, 2 controls vs 4 treated, 1.5- / 2- / 4-fold, three
    levels of missing values) for each imputation / normalisation setting;
    it reports sensitivity, the observed false discovery proportion against
    the nominal alpha, and the fold-change bias. `--like <folder>` adds an
    experiment's own settings and group sizes. Measured on the standard
    grid: the default (Perseus + median) 4.0 % observed FDP at adjusted
    p ≤ 0.05 and 30 % / 71 % of 2- / 4-fold changes found; no imputation
    4.6 % and 47 % / 86 %; no normalisation 14 – 21 %. With a folder and
    `--expected hye.yaml`: an analysed mixed-species (human / yeast /
    E. coli) or spike-in experiment against its expected ratios per species
    or protein list: measured vs expected (median, spread, a box plot), the
    false positive rate in the unchanged background, sensitivity among the
    changed. Species come from UniProt entry names, FASTA `OS=`, a column
    or a FASTA. **No real benchmark run exists yet**; VALIDATION.md says how
    to make one.
  - **A calibration guard in the test suite**: a small simulated grid whose
    observed FDP must stay at or below 8.5 % (measured 1.7 – 6.3 %; the
    tolerance is from 30 other seed blocks).
  - **"How far to trust this"** under the key findings of every report and
    in `analysis.json` → `trust` (`downstream/trust.py`): samples per group
    and balance, replicate agreement, missing and imputed values, per
    comparison what was tested, hits resting on imputed values and the
    p-value histogram shape, and power, each with its number. No score. A
    line is marked "check" when its own check crossed its threshold. A
    compare or benchmark result in the results folder is shown there too.
  - **Guards on statistics that run but may not mean what they say**
    (`downstream/guards.py`), each a new warning: `NO_RESIDUAL_DF`
    (features tested with one value per group), `VARIANCE_PRIOR` (limma's
    prior could not be estimated or did not converge), `ZERO_VARIANCE`
    (identical replicates), `IDENTICAL_SAMPLES` (two samples with the same
    values).
  - **Messy input**: a seeded fuzz of `analyze()` and the loaders
    (`tests/test_robustness.py`), and notes for what a table held besides
    numbers: duplicate or blank IDs, repeated or missing column names,
    columns left out, text and infinite cells, negative intensities,
    ragged rows, implausible values. Decimal commas and thousands
    separators are now read in tab and comma files too.
  - The default output of a well-formed analysis is unchanged apart from the
    new list in the report and the `trust` key: the FragPipe-Analyst, limma
    and R golden tests pass as they were.

### Changed

- **A failed search's reason names the step and what it said**: "FragPipe
  step MSFragger failed (exit code 1); it said: …" with that step's last
  lines, instead of FragPipe's closing "Cancelling N remaining tasks".
- **The progress line** counts steps ("MSFragger (4 of 31 step(s) done)")
  and knows step names with brackets or a colon.
- **TMT**: a channel map FragPipe would stop on fails the job before the
  search, with the rule (every channel of the label type listed, `NA` for
  unused ones, one name without spaces per channel, no name twice). Plexes
  that share a folder get no annotation file and a warning; plexes each in
  their own folder get one `annotation.txt` each.
- **A FASTA without usable decoys holds the job** ("waiting: FASTA … can't
  be searched") instead of letting FragPipe fail on it, and is a ✗ on the
  setup checklist.
- **The testbed's fake FragPipe copies the real one** (`fake_fragpipe.py`):
  its options, checks, messages, console layout, exit codes and output
  files, with the source of each named. New failure modes for testing:
  `IONOMOS_FAKE_FP_MODE=speclib | no-java | locked | diann | cancel-exit0 |
  no-done-line | child`. The sample TMT drop lists all ten channels; the
  failing sample (`fp_fail`, a DIA drop) now fails in the DIA-NN step, as a
  DIA search has no IonQuant step.
- The default launcher path is `C:/FragPipe/FragPipe-24.0/bin/fragpipe.bat`
  (the 23 / 24 installer's layout).

### Fixed

- **`fragpipe.bat` found no Java on the lab PC's kind of setup.** The
  launcher is Gradle's start script and needs `JAVA_HOME` or `java` on PATH;
  the PC has neither. Ionomos now sets `JAVA_HOME` for it to the `jre`
  folder in the FragPipe installation.
- **`FragPipe-24.0.exe` without a `fragpipe.bat` beside it is no longer
  run.** It is a window program that returns at once; the job is held with
  that explanation.
- **Every failed real search would have been blamed on the FASTA.** The hint
  "No protein database" matched the bare setting name `database.db-path`,
  which FragPipe prints on every run with all its other settings. It now
  needs FragPipe's own messages. A test runs every hint against a healthy
  FragPipe log.
- **A successful retry could be marked failed** because of the failed step
  of an earlier attempt still in the console log. Only the latest attempt's
  part of the log is judged, also for the progress line.
- **The progress line named the last step from the start.** FragPipe lists
  every command before running any; that list was read as steps starting.
- **Two TMT plexes in one folder got two annotation files**, which makes
  FragPipe use neither. See Changed.
- A run in which FragPipe cancelled its remaining tasks, or only did a dry
  run, but exited 0 is now a failed search.
- FragPipe 24 writes DIA-NN's tables to `dia-quant-output\`; the fake and
  the docs said `diann-output\` (the analysis already read both).

- A failed FragPipe search on .raw files no longer gets the cause "A .raw
  file couldn't be read" from Thermo's `RawFileReader reading tool` banner,
  which FragPipe logs on every such search. 0.13.0 fixed this for the
  converter's name in Sage jobs; the reader's name was still matched alone.
  Either name now needs an error word on its line, or `ionomos sage-job`'s
  own `converting X failed` line (also with exit code 0 and no mzML
  written). A name inside a path does not count, so a converter that could
  not be started is no longer reported as a bad raw file.
- Found by fuzzing the analysis with messy tables (D60):
  - A table of raw intensities with one negative cell was read as log2
    values, so the statistics ran on numbers around a million and the QC
    crashed. The median now decides; negative intensities count as missing,
    with a note.
  - Two columns with the same name in a table were both read from the last
    of them (the same values twice). Each is now its own sample.
  - A numeric column without a header became a sample of its own condition
    (named `sample`, the next one `.2`). It is now left out, with a note.
  - A tab-separated table with decimal commas (`1234,5`) or thousands
    separators (`1,234,567.8`) was refused as having no numeric columns.
  - A sample column where a few cells held a number too large for a float
    (`1e400`) was dropped without a word; so was a column with no values.
    Such cells now count as missing, and columns left out are named.
  - A value beyond any measurement (an intensity of 1e300, `1e308` in a log2
    table) crashed the QC, insight, statistics or liganded-site steps with an
    overflow. Values beyond 2^±100 now count as missing, with a note.
  - A FragPipe table with two columns of one sample name counted that sample
    twice with identical values. The repeat is now dropped, with a note.
  - An isoDTB sample name with a character no file name can hold (a NUL
    byte, `:`, `?`) crashed the read step when its site table was written.
  - A table with one feature said "nothing could be tested" without the
    reason (median normalisation leaves nothing). A note now says it.
- **FragPipe's own `sdrf.tsv` is no longer taken for the experiment's design.**
  FragPipe 24's stock workflows write one into the output folder with no
  sample names and no conditions; the analysis read it as the user's SDRF and
  raised "needs your input" (`SDRF_UNMATCHED_RUNS`) on every search. It is now
  skipped with a note.

## [0.13.0] - 2026-10-01

### Added

- **Search quality per run** (ROADMAP Phase 4, D55; `downstream/psmqc.py`).
  For FragPipe results with `psm.tsv` files, the report's Quality control
  section gets a **Search quality** tab with, per raw file: PSMs, peptides
  and proteins; the precursor mass error (median, quartiles, 5th to 95th
  percentile, ppm); the share of PSMs with a missed cleavage; the charge
  states; the peptide length. With DIA, it shows DIA-NN's own per-run
  summary (`report.stats.tsv`). Output: `results/psm_qc.tsv` and
  `analysis.json` → `psm_qc`. The numbers come from the reader the
  instrument QC trend already uses (`qcmetrics.py`); files are streamed, and
  one over 4,096 MB is left unread with a note. Two new warnings, with wide
  limits the lab has not confirmed: `PSM_MASS_ERROR` (a run's median error
  is 10 ppm or more from 0) and `PSM_MISSED_CLEAVAGES` (half or more of a
  run's PSMs have a missed cleavage). New setting: `psm_qc` (false switches
  it off). **Not tested on real FragPipe output**: the `psm.tsv` column
  names are from the FragPipe documentation.

- **Notifications when a search is done, failed or waiting** (ROADMAP
  Phase 4, D58; `notify.py`). Off by default; set up under `notify:` in
  `config.yaml`:
  - channels: a generic JSON webhook, a Microsoft Teams webhook (an Adaptive
    Card), a Slack incoming webhook, and SMTP email (STARTTLS or SSL).
    Standard library only
  - a message holds the experiment name, user, method, status, the reason
    or the hit counts, and the local path of the report; never a file or a
    quantity. `notify.include_names: false` sends the job number and status
    only
  - `on: [done, failed, held]` picks the events. A waiting search sends one
    message per reason, also across a restart
  - a message is sent after the status is recorded, in its own thread, with
    a timeout and no retries: a dead webhook can't fail or delay a job. A
    channel's error is logged once
  - webhook addresses and the SMTP password can come from environment
    variables (`url_env`, `password_env`), are never logged, and are
    replaced by `***` in `ionomos diagnose`, the diagnostics bundle and
    **Report a problem…**
  - `ionomos notify-test` sends a test message to every channel and says
    what happened; `ionomos check` shows whether notifications are on
  - not in the app yet: edit `config.yaml` (the app keeps the block when it
    saves)
- **Log rotation that is safe on Windows** (`health.SafeRotatingFileHandler`).
  `ionomos.log` already rotated at 5 MB with 5 old files kept, but a rename
  refused by Windows (another program has the file open) made the stock
  handler drop every log line until the file was free. Now the watcher keeps
  writing to the same file and tries the rotation again a minute later.
  `app.log` uses the same handler.

- **Sage TMT** (ROADMAP 5B #5, D56, [docs/ENGINES.md](docs/ENGINES.md)).
  - **Import:** `ionomos analyze <Sage output folder>` reads `tmt.tsv`
    (reporter ions per spectrum) with `results.sage.tsv` (the PSMs) beside
    it. PSMs are targets of rank 1 at spectrum, peptide and protein
    q ≤ 1%, joined to their reporter ions by file and scan. Proteins are
    grouped by razor peptides and summarised per plex and channel as the
    MSstatsTMT format already is (one PSM per peptide ion, fractions
    combined, global median normalisation, Tukey median polish). Several
    plexes are then joined by IRS on the reference channel, as for the
    other engines (D48). `tmt_1 … tmt_n` are the kit's channels in order.
  - **Run:** a lab `sage_config` with `quant.tmt` (`Tmt6` … `Tmt18`) no
    longer holds the job. Label-free quantification is not switched on
    for it; the job expects `tmt.tsv`. The files of a plex are its
    fractions (`<plex>_F<fraction>.raw`, the TMT naming rule).
  - **Channel names:** experiment.yaml's `tmt:` map names the channels and
    gives them their condition (the text before the first `_`, as for
    FragPipe TMT); a channel it calls `NA` or `empty` is left out. Without
    a map the channels are `<plex>_<channel>` with condition `unassigned`,
    and the analysis asks for the conditions (Analysis tab, or an SDRF).
    Plexes whose channels have no condition yet are not scaled by their
    own means.
  - **Instrument QC from Sage:** a QC-standard run searched by Sage is
    trended from `results.sage.tsv`: PSMs, peptides, proteins, summed
    fragment signal, a signed precursor mass error, missed cleavages,
    charge and RT ([docs/QC_TREND.md](docs/QC_TREND.md)).
  - A method whose `sage_args` has `--parquet` is held: Sage's Parquet
    output is not read (Sage itself calls the format unstable).

  Tested against the stand-in Sage only (`ionomos fake-sage` now writes
  `tmt.tsv` and the full `results.sage.tsv` header). The `tmt.tsv` and
  `results.sage.tsv` layouts come from Sage's source, not from a real run.

- **The assistant, read-only "Explain"** (ROADMAP Phase 6.1, D49, D57;
  `ionomos/assistant/`, [docs/ASSISTANT.md](docs/ASSISTANT.md)).
  `ionomos ask "why did my search fail?" --experiment 12` answers from the
  job's log, the doctor's findings and the help, through a model running on
  the PC. **Built and tested against a scripted fake model only: no real
  model or runtime has been tried, none is recommended, and the assistant is
  off by default.**
  - It speaks the OpenAI-compatible chat API to `assistant.base_url`
    (standard library only). An address that is not this PC is refused before
    anything is sent.
  - Seven read-only tools with schema-checked arguments: list experiments, a
    job, attention items, an issue code in the help's words, a numbered log
    tail, the analysis summary, help search (BM25; SQLite FTS5 or pure
    Python). No file paths as arguments, no changes, no shell, no network.
  - Every paragraph of an answer must cite `[issue:CODE]`, `[log:JOB#LINE]`,
    `[help:ID]`, `[analysis:FIELD]` or `[job:ID]`, and each citation must be
    something a tool returned. Otherwise the answer is not shown and Ionomos
    prints its own text: likely causes, fixes, the help entry, and who to
    ask. The same text is the answer when the assistant is not set up.
  - Names, logs and `experiment.yaml` are treated as untrusted: tool results
    are passed as data, cleaned of control characters and capped.
  - Every question is appended to `assistant-audit.jsonl` in app data.
  - New: `assistant:` in `config.yaml`, an `assistant` row in `ionomos
    check`, help entries `faq.assistant` and `faq.assistant-setup`, and 53
    scenarios in `tests/assistant_scenarios/` replayed in CI.
  - Not built: the "Ask about this" button in the pop-ups, and a runner that
    scores real models. Phase 6.1's exit criteria are still open.
- **Time courses** (ROADMAP 5C #1, D53; `downstream/timecourse.py`). When the
  conditions are time points (`Drug_0h`, `Drug_1h`, `Drug_4h`, `Drug_24h`,
  or `analysis.times`) and a series has at least 3, every feature gets
  limma's time-course tests on the comparisons' own model (block and
  covariates included):
  - change over time: the moderated F on every time point against the first
  - a trend: the moderated t on the linear contrast over the ordered time
    points
  - with a control series (`DMSO_0h`, `DMSO_1h`, …): whether the series
    responds differently over time (the interaction F)

  Changing features (the report's alpha and |log2FC| cut-offs) are classed
  up / down / mixed and grouped into patterns by profile shape. Output:
  `results/time_course.tsv`, `analysis.json` → `time_course`, and a **Time
  course** report section: pattern charts to click, a sortable table, and
  each feature drawn over its replicates with the control series dashed.
  New settings: `times`, `time_unit`, `time_min_points`, `time_course`; a
  `TIMES` issue when a time point can't be read. Checked against limma
  3.68.5 to 1e-8 (plain model, replicate block, missing values).

- **Liganded cysteines for isoDTB** (ROADMAP 5C #3, D52;
  `downstream/cys.py`, [docs/WORKFLOWS.md](docs/WORKFLOWS.md)). Each site is
  called per compound by the chemoproteomics rule: liganded when the
  competition ratio R reaches 4 in at least 2 replicates, inconsistent when
  it does in fewer, not liganded, or too few replicates to say. Every part is
  a setting, for the lab or one experiment: `liganded_ratio`,
  `liganded_min_replicates`, `liganded_direction` (`high`: R = heavy /
  light; `low`: the other way round), `liganded`.
  - Per compound: the liganded fraction. Across compounds: selective, shared
    and unresolved sites. Per protein: how many of its cysteines are
    liganded, with "most sites" marked.
  - `site_annotation`: a site table the lab downloads (CysDB, or its own
    list) marks each site known liganded / known hyperreactive / seen before
    / new. CysDB is not shipped.
  - Output: `results/cysteine_sites.tsv`, `results/cysteine_proteins.tsv`,
    `analysis.json` → `cysteines`, and a **Liganded sites** report section
    (tiles, a rank plot of R against the threshold, a site × compound table
    shaded by R, a protein view), with help, glossary and Methods text.
  - Two new report notes: `LIGANDED_DIRECTION` when far more sites would be
    liganded with the ratio reversed, and `SITE_ANNOTATION` when the
    annotation file can't be used.

  The calls use measured ratios only (no normalisation, no imputation). Not
  included: correcting site changes for protein abundance. The defaults
  (R ≥ 4, 2 replicates, heavy / light) are the field's common ones and have
  not been confirmed by the lab; the CysDB layout is from its paper, not a
  real download.

- **Sage as a search engine** (ROADMAP 5B #5, D51, [docs/ENGINES.md](docs/ENGINES.md)).
  - **Run:** a DDA method with `engine: sage` is searched by the lab's own
    [Sage](https://github.com/lazear/sage) (open source, MIT). Thermo `.raw`
    files are first converted to mzML with the lab's ThermoRawFileParser
    (`raw_converter:`) into `<experiment>/sage_mzml/`; a retry reuses what is
    already converted, and the raw files are never touched. Settings are the
    lab's own Sage JSON (`sage_config:`) or Ionomos' defaults (tryptic,
    high-resolution MS2, label-free), written to `ionomos_run/sage.json`.
    Watching, holding, cancel, Retry, pop-ups and the analysis work as for
    FragPipe; results go to `<experiment>/sage/`, and a second run keeps the
    first as `sage_previous_<time>/`. Sage's usage telemetry is switched off
    whenever the installed Sage has the flag for it.
  - **Import:** `ionomos analyze <Sage output folder>` reads `lfq.tsv`:
    peptides at q ≤ 1% (and proteins at q ≤ 1% when `results.sage.tsv` is
    there), proteins grouped by razor peptides, each sample's fractions
    added, protein quantities by Tukey median polish, gene names from the
    search's FASTA. The report's Data source shows Sage's version and FASTA
    from `results.json`.
  - New help entries for a held Sage job (Sage not found, raw file converter
    not found, Sage settings missing or unusable); held MaxQuant jobs now
    open their own help entry too.

  Tested against stand-ins for Sage and ThermoRawFileParser only
  (`ionomos fake-sage`, `ionomos fake-rawparser`), with the file formats
  taken from Sage's source. Not yet run against a real Sage.

### Changed

- **A method that is `like:` a built-in method is that method everywhere**
  (D54; ROADMAP "found while building 5A/5B"). `naming.methods.<key>:
  {like: isoDTB | TMT | DIA}` used to borrow the name rule only, and the
  method was then analysed as generic label-free. Now a `DIA_phospho` or
  `TMTpro` method gets the built-in method's whole behaviour: the TMT
  `annotation.txt`, the expected FragPipe outputs, the control question in
  the review window, the analysis, the SDRF and the doctor's messages. A
  method run by another engine is what the engine makes (`engine: diann`:
  DIA under any key, which used to get no analysis; `maxquant` / `sage`:
  label-free), whatever `like:` says. One resolver decides
  (`naming.method_kind`, `Config.kind`). Built-in keys and configs without
  `like:` behave as before. A FragPipe method that used `like:` only for the
  shape of its names should now write the template out
  (`files: '{sample}_{rep}[_{fraction}]'`).
  `ionomos analyze --method` also takes the config's own method keys, and
  `ionomos names test` prints what a custom method is run as.

### Fixed

- A failed Sage search no longer gets the hint "A .raw file couldn't be read"
  just because the console log names the converter after successful
  conversions; the hint now needs the converter's own error or exit code.

## [0.12.0] - 2026-09-30

### Added

- **Dose-response curves for titrations** (ROADMAP 5C #2, D44;
  `downstream/doseresponse.py`). When the conditions are doses of a compound
  (`Cmpd_10nM`, `Cmpd_0p1uM`, … or experiment.yaml `analysis.doses`) and a
  compound has at least 4 doses above the control, every feature gets
  CurveCurator's fit (Bayer et al., *Nat. Commun.* 2023; Apache-2.0), ported
  to pure Python:
  - a 4-parameter log-logistic curve on ratios to the control, with pEC50
    and a 95% interval, EC50 in the doses' unit, slope and plateaus
  - the recalibrated F-statistic and its p-value, a BH q-value, the curve
    fold change and the relevance score
  - up / down / not / unclear classes with CurveCurator's defaults (alpha
    0.05, |log2 fold change| 0.45)

  Output: `results/dose_response.tsv`, `analysis.json` → `dose_response`, and
  a report section with a sortable, filterable table, each curve drawn over
  its measured points (control at the left), and a potency vs effect scatter;
  SVG / PNG export, and the report search rings matching curves. Works for
  intensities (ratio to the mean of the DMSO runs) and isoDTB ratios. New
  settings: `doses`, `dose_unit`, `dose_response`, `dose_min_doses`,
  `dose_alpha`, `dose_fc_lim`. Fewer doses: a note, no curves. Unreadable
  doses: a `DOSES` issue. Checked against CurveCurator 0.6.0 itself on 600
  simulated curves: identical classes, pEC50 within 0.05 for 263 of 266
  regulated curves (`tests/golden/dose_response/`, `tests/test_dose_response.py`).
  `simulate.dose_titration` / `dose_pg_matrix` make titrations with known
  pEC50s.

  The 95% pEC50 interval is clipped to the range a fit may take: a steep curve between two doses has no
  local error estimate, and it read like -907792 – 907806.
- **Help for users** (D46, [docs/HELP.md](docs/HELP.md)). One set of plain
  Markdown texts in `ionomos/help/` covers getting started, reading the
  report chart by chart, a glossary, troubleshooting (every analysis issue
  code, pop-up kind, rejected folder and held or failed search), what Ionomos
  never does to your data, and common questions. It is shown in three places:
  - **every report**: **Help** in the top bar, a **?** beside each section
    title, QC tab and issue box that opens its text in place, and a Help
    section at the end with the glossary and what to do about the issues in
    that report (offline, inside the file);
  - **`help.html`**, the whole help with a search box: the app's new **Help**
    button, **More help** in each pop-up (opened at the topic that explains
    it) and `ionomos help --open`;
  - **`ionomos help [TOPIC]`** prints one topic (`NO_TABLE`, `pca`,
    `glossary`, …).

  Tests fail when an issue code, attention kind, intake rejection or held-search
  reason has no help entry.

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
- **An SDRF as the design** (`downstream/sdrfdesign.py`, D47, docs/ENGINES.md). A
  `*.sdrf.tsv` / `sdrf.tsv` in the experiment folder, or next to the table
  given to `ionomos analyze`, sets each run's condition (`factor value[...]`;
  several joined, or picked with the new `analysis.sdrf_factor`), biological
  replicate, TMT plex and pooled reference. Runs are matched by
  `comment[data file]` (and `comment[label]` for TMT). `sample_conditions`
  still wins; the SDRF beats the engine's own columns, the manifest and the
  names. `results/sdrf.tsv` is never read back. `analysis.json` → `design`
  says where the conditions came from; runs the SDRF doesn't name raise
  `SDRF_UNMATCHED_RUNS`.
- **MSstatsTMT format import** (`engines.load_msstats_tmt`, D48): summarised
  as MSstatsTMT's `proteinSummarization(method = "MedianPolish")` (fractions
  combined, global median normalisation, median polish per run, Norm-channel
  normalisation between runs), identical to MSstatsTMT 2.20 to 1e-9 on a
  golden file; one sample per mixture and channel.
- **TMT across plexes: IRS** (`downstream/plex.py`, ROADMAP 5C #5, D48).
  Several plexes (MaxQuant experiments, MSstatsTMT mixtures, Proteome
  Discoverer files, SDRF file groups) are put on one scale on their reference
  channel (new `analysis.tmt_reference`, experiment.yaml
  `tmt.reference_channel`, the SDRF's pooled rows, or names like Pool / Norm),
  or on the plex means when every plex holds the same conditions (new
  `analysis.irs: auto | reference | sum | none`). Otherwise the doctor warns
  (`TMT_PLEXES_NOT_NORMALISED`). TMT-Integrator abundances are never scaled
  twice. The report's PCA colours by plex and shows the values before and
  after IRS; `analysis.json` → `tmt` records what was done.
- `ionomos analyze --method MSstatsTMT`.

- **Experimental designs** (ROADMAP 5C #1, D42): blocks (batch, plex, pair,
  patient) as fixed effects and numeric or factor covariates, in limma's
  general linear model (`downstream/design.py`). Set under `analysis:`:
  `block: replicate`, `block: {sample: block}`, `block_from: <regex>`,
  `covariates: {name: {sample: value}}`. It is fitted like lmFit →
  contrasts.fit → eBayes, with missing values per row, and checked against
  limma 3.68.5 to 1e-8 (`tests/test_design.py`). A design that can't be used
  raises `DESIGN_NOT_USED` and falls back to `~0 + condition`.
  `BATCH_SUSPECT` now suggests `block: replicate`. `fragpipe-analyst/`
  gains `reproduce_design_in_R.R`, since `test_limma` can't fit a block.
- **Moderated F-test** for experiments with 3+ conditions: "any change between
  the conditions" (limma topTableF). It appears as the `F` / `F_p` /
  `F_p_adj` columns of `<level>_results.tsv`, `analysis.json` → `f_test`,
  and an "Any change (F)" tile and table column in the report.
- **DEqMS** (ROADMAP 5C #4, D43): `variance_prior: deqms` gives each
  protein a prior variance from its peptide (or PSM) count. It is a port of
  DEqMS 1.30 spectraCounteBayes, including R's loess, and matches the package
  to 1e-8. Features without a count keep limma's prior; a table without counts
  warns `DEQMS_NOT_USED`.
- The report's Methods paragraph and Settings table and `analysis.json` →
  `model` describe the model used (formula, blocks, covariates, variance
  prior, F-test).

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
- MaxQuant TMT with several experiments no longer analyses the channel totals
  over experiments (`Reporter intensity corrected 1`) as extra samples.
- Proteome Discoverer TMT columns `Abundance: F1: 126, Sample, DMSO` are
  recognised (the pattern needed a space before the colon), and tab-separated
  files whose headers hold many commas are no longer read as comma-separated.

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
