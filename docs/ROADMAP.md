# Roadmap

Phases are ordered so that each one is independently useful and testable
without the next. Phase 1 can be developed and unit-tested entirely on a Mac
with fake folders; nothing before Phase 2 touches FragPipe.

## Phase 0 — Plan ✅ (this commit)

- Repo, docs, package skeleton, reference material organised.
- Fixed inventory script.

**Exit:** naming convention reviewed by 2–3 lab members; fixed inventory re-run
on the PC and results added to `reference/pc-inventory/`.

## Phase 1 — Watcher + intake (no FragPipe) ✅ code complete, not yet deployed

Build, in this order, each with tests:

1. `naming.py` — pure parsers for folder names and raw filenames, with the
   rejection reasons spelled out. Table-driven tests using the real names from
   the inventory (both the ones that should pass and the ones that should fail).
2. `config.py` — load/validate `config.yaml`; friendly errors for missing paths.
3. `watcher.py` — inbox polling with tree-fingerprint stability. Test with a
   thread that slowly writes files.
4. `ledger.py` — SQLite schema + transitions + startup recovery.
5. `intake.py` — validate → move → `ionomos.json` → ledger. Rejection writes
   `.REJECTED.txt`.
6. `cli.py` — `ionomos run`, `ionomos status`, `ionomos dry-run <folder>`
   (parse and print what *would* happen, move nothing).
7. `run_ionomos.bat` + Task Scheduler instructions.

Done 2026-09-15: `naming` (keyword/glued-initials/date formats/method from
file names), `config`, `watcher` (note-aware retry, backoff), `ledger`,
`intake` (experiment.yaml overrides, Windows lock retry), `resolve` (tkinter
window), `testbed`, `cli` (run / check / status / dry-run / retry / testbed),
`app` (setup wizard / control panel with a Testbed tab), `configio`,
`service`, PyInstaller exe (`Ionomos.exe` + `ionomos-cli.exe`), 149 tests incl.
real-GUI and end-to-end, `install.ps1` fallback.
2026-09-16: dev install on the PC (`deploy/dev_install.ps1`, `ionomos update`,
"Update from GitHub" in the app), `ionomos diagnose` / "Copy diagnostics",
GitHub Actions (tests on Linux+Windows; exe built on `v*` tags). See DEV_LOOP.md.
Remaining for exit: the week of real drops on the PC.

**Exit:** on the PC, dropping a correctly named folder of raws lands it in the
right user directory with `ionomos.json` saying `queued` and nothing else
happens. A bad name gets a `.REJECTED.txt`. Runs for a week without falling over.

## Phase 2 — isoDTB end-to-end

2026-09-22: automatic FragPipe runs built (`worker.py`, `fragpipe.py`,
`postprocess.py` hook; DONE/FAILED notes; retry; hold-until-configured;
stop/timeout kill the process tree; re-runs keep old output). Tested against
the testbed's fake FragPipe on macOS + Windows CI. **Not yet run against the
real FragPipe 24.0 on the PC** — first real test: pin `isoDTB.workflow`, drop a
small isoDTB folder, compare with a GUI run. Still to build: step 5 below.

1. Pin the lab's `isoDTB.workflow` (+ FASTA) into `C:\Fragpipe_Auto\workflows\`.
2. `manifest.py` — `.fp-manifest` from parsed raws.
3. `runners/fragpipe.py` — adapt prior-work runner; confirm launcher path on 24.0.
4. `worker.py` — pull queued job, run, record.
5. `runners/isodtb.py` — port the site-merge R script; golden-file test vs an
   existing lab output. → done 2026-09-24 (last entry of this section).
6. `DONE.txt` / `FAILED.txt` + `ionomos retry`.

**Exit:** one real isoDTB experiment processed with no manual steps; the
`_sites.tsv` matches the R output on the same input.

2026-09-23 (0.3.0): failsafes (single instance, supervised loops, crash
reports, heartbeats, graceful stop, ledger backup/rebuild/orphan adoption,
disk-space hold, never-loop intake), richer diagnostics (+ .zip bundle),
FragPipe friendliness (plain-English failure causes, live step progress,
cancel, pause/resume, install check, workflow import, FASTA decoy check),
Jobs tab, stress tester. See ARCHITECTURE.md "Failsafes".

2026-09-23 (0.4.0): downstream analysis built — R ports (byte-identical),
limma-style statistics (validated vs limma), volcano plots, self-contained
HTML report, `ionomos analyze`, app Analysis tab + Re-run analysis; setup
checklist tab + Auto-setup + `ionomos init`, unsafe layouts refused. Next:
run it on real lab output and compare with the lab's current analyses.

2026-09-23 (0.5.0): renamed LabWatch → Ionomos with full backward
compatibility (`names.py`); Windows installer (install / upgrade / uninstall,
retires LabWatch) tested in CI; in-app update from a downloaded Setup; Report
a problem → one zip on the Desktop; build stamp in every report; app log;
self-expiring detailed logging.

2026-09-23 (0.5.1), from the first real install report: Find FragPipe knows the
23/24 installer layout (C:/FragPipe/FragPipe-24.0/bin/FragPipe-24.0.exe) and never
picks a copy under a path with spaces; settings saved in the program folder move
next to the data; FragPipe copies / FASTA folders / "New folder" in the users
folder aren't treated as people; the checklist refreshes after every save.
Open: confirm FragPipe-24.0.exe runs headless with console output on the first
real search (a fragpipe.bat beside it is preferred if FragPipe ships one).

2026-09-23 (0.5.2): loose .raw files in the inbox are grouped into folders;
Xcalibur timestamp suffix understood; public repo → the app checks GitHub and
updates itself (verified download); web links open in the browser (the
Releases button showed "does not exist").

2026-09-23 (0.6.0): analysis = FragPipe-Analyst (FragPipeAnalystR ported and
checked against the real package): filters, median/GN normalisation, Perseus
/ MinProb / kNN imputation, limma with CIs, all-pairs / vs-control / vs-others;
PCA, correlation, missing values, CVs, heatmap of hits; local gene-set
enrichment (Enrichr libraries). Interactive report. Analysis tab: per-experiment
sample conditions, samples left out, comparisons, cut-offs (saved in
experiment.yaml) + Run. FragPipe-Analyst export (annotation + R script).
Open: compare against the lab's own FragPipe-Analyst sessions on a real DIA
experiment; decide the lab default for imputation (Perseus vs none) with that
data; not ported yet: VSN, MLE imputation, paired designs, TMT multi-plex
(plex-aware feature numbers), phospho site-level normalisation (PTM_normalization).

2026-09-24 (0.7.0): robustness + pop-ups. Attention queue and pop-up windows
(app or watcher) for analysis decisions, failed analyses, failed/held searches,
rejected folders, empty raw files — each with likely causes and the fixing
buttons (experiment editor, Retry, FragPipe log, Report a problem). Analysis
stages isolated with fallbacks; volcano plots guaranteed and verified; the
analysis doctor (15 checks) in every report; stress test checks volcanos and
notifications. Open: see real pop-ups on the PC; tune the LOW_SAMPLE threshold
and control keywords with real experiments.

2026-09-24: roadmap step 5 done — `runners/isodtb.py` landed as a thin
`merge_to_sites()` entry point that delegates to the port in
`downstream/isodtb.py` (shipped 2026-09-23 (0.4.0), already byte-identical to
the R goldens and wired into the live pipeline); new runner tests compare both
`_sites.tsv` outputs with the R goldens. Nothing left to build in this phase's
sandbox scope; what remains is the exit test above — the first real FragPipe
run on the PC.

2026-09-30: the maintainer reports a series of real runs done on the PC. Record
any differences from the GUI / R / FragPipe-Analyst path here, and keep one
small anonymised real experiment as a test fixture and public example data
(Phase 5A).

## Phase 3 — DIA, then TMT

- DIA: pin workflow, `data_type: DIA`, sort out DIA-NN version/`--config-diann`.
  No post-proc.
- TMT: `experiment.yaml` `tmt:` block → `annotation.txt`; confirm how headless
  24.0 finds it; port the annotation R script; resolve the "Peak Picking &
  zero Samples" pre-step question.

## Phase 4 — Operations & niceties

- Log rotation, disk-space check before accepting a job (C: has 99 GB free;
  refuse if < 2× raw size), email/Slack/Teams notify on done/failed.
- **Data presentation**: volcano plot + summary at the end of each run —
  either an HTML report (plotly, no server) or a small Shiny/Streamlit app.
  FragPipe Analyst ships R code that can be reused for the stats.
  → 2026-09-30: the report now also has deeper QC and discovery views (D35):
  - a sample scorecard and batch check
  - missingness vs intensity, and π0
  - on/off features and imputation-driven hits
  - rank-based gene sets
  - compare comparisons
  - power
  - list / wildcard / term search, highlight groups, and the view in the link

  Still open:
  - Tune the D35 warning thresholds on real lab experiments.
  - PSM-level technical QC from `psm.tsv`: mass error, missed cleavages,
    charge states.
  - Run-order drift, once acquisition times are recorded.
  - Protein complexes (CORUM, whose licence needs checking).
  - For isoDTB:
    - competition-ratio classes, once the lab confirms the ratio direction
      and thresholds
    - a CysDB overlay (liganded / hyperreactive / novel)
    - site selectivity across compounds
- `ionomos status` as a tiny local web page if people ask.
- Auto-archive finished experiments to `D:\<user>\` after N days.
- Optional: auto-pull from `C:\Proteomics_File_Sharing` (reversing D3) once
  the convention is trusted.

## Phase 5 — Beyond one lab (plan of 2026-09-30, D36)

Goal: other labs can use Ionomos. Research (2026-09-30) found no open tool
covering the whole path. The open pipelines need Linux, containers and a
bioinformatician: nf-core/quantms, Frag'n'Flow (headless FragPipe on
Nextflow), ProtPipe. AlphaPept has a watcher but only for its own engine.
MSAID's `watch` command is commercial and cloud-based. Downstream tools
(FragPipe-Analyst, MSstatsShiny, MS-DAP, AlphaPeptStats) all start from an
uploaded table.

**Positioning:**
- The open, on-premise, Windows-native last mile from instrument PC to a
  trustworthy report.
- Licence-clean: GPL, never bundles a restricted engine.
- Chemoproteomics (isoDTB / ABPP) is the niche nobody else automates.

Order of work: 5A → 5B, then 5C by need; 5D once 5A ships.

### 5A — Not one lab's tool

Installation and configuration kill lab tools: in one study, 28% of omics
tools failed to install within 2 hours, and tools on package managers always
installed.

- [x] **Analysis-only install from pip.** (2026-09-30, D40: `ionomos demo`, PyPI workflow; publishing awaits the maintainer's trusted-publisher setup) `pip install ionomos`, then
  `ionomos analyze <table or folder>` and `ionomos demo`, on Windows / macOS /
  Linux, with no Tk needed. The analysis has one dependency (PyYAML), so this
  is the cheapest adoption lever. Publishing to PyPI is the maintainer's step
  (trusted publishing from a tag).
- [x] **Engine adapters** (import side first). (2026-09-30: `downstream/engines.py`, docs/ENGINES.md; run side: DIA-NN, D39) One registry; each adapter can:
  - recognise its results folder / tables (with a confidence score)
  - load one canonical quantity matrix
  - report provenance: engine, version, settings, FDR filters

  FragPipe (isoDTB / TMT / DIA / LFQ) is adapter #1; the any-table loader is
  the fallback. The run side (build the command, locate outputs; the core
  runner keeps locking, logging, cancellation and the "failures leave data in
  place" rule) follows once import works.
- [x] **Provenance in every report** (2026-09-30: Methods → Data source, `analysis.json` `engine`) (MS-DAP-style audit trail): engine and
  version, workflow / parameter file, FDR filters, Ionomos settings.
- [x] **SDRF-Proteomics export** (2026-09-30, D38; SDRF *import* as a design still open) (`results/sdrf.tsv`), the PSI sample-metadata
  standard PRIDE promotes. Built from what the file names already say:
  condition, replicate, fraction, label channel. Later: accept SDRF as a
  design import.
- [x] **Configurable naming.** (2026-09-30, D37: `naming.methods`, `ionomos names test`, Methods tab → Test names…) Each method's file pattern and the condition
  codes become editable config, with a "test your names" check (CLI + setup
  window), so another lab's convention needs no code change. Keep
  NAMING_CONVENTION.md ↔ naming.py in sync.
- [ ] **Docs for a stranger:** (2026-09-30: QUICKSTART.md, ENGINES.md and the simulated demo done; still to do: a real-data example, and a "never done to your data" page)
  - a 10-minute quickstart
  - demo data, from the real fixture once it exists (simulated until then)
  - "what Ionomos will never do to your data"
  - a page per supported engine

### 5B — More engines (value ÷ effort ÷ licence risk)

| # | Engine | Mode | Notes |
|---|---|---|---|
| 1 ✅ | DIA-NN standalone (1.9 / 2.x) | import ✅, run ✅ (`engine: diann`, D39) | `pg_matrix` parser exists; 2.x `report.parquet` needs an optional Parquet reader. DIA-NN can't be redistributed from 1.9 on (Academia / Enterprise editions): the lab supplies the binary. |
| 2 | MaxQuant | import ✅ `proteinGroups.txt`; run still to do | Free incl. commercial use; not redistributable. Run mode patches an `mqpar.xml` made by the installed version (`--create`), never a shipped template. |
| 3 | MSstats long format + SDRF design | import ✅ label-free MSstats (MSstatsTMT, SDRF design still to do) | One importer covers quantms, Skyline and anything with an MSstats converter; protein summary by Tukey median polish (MSstats' default). |
| 4 ✅ | Spectronaut | import ✅ pivot + long reports (the `.rs` schema still to ship) | Common in cores; ship an Ionomos report schema (`.rs`), read `PG.Quantity` pivots or the long BGS report. |
| 5 | Sage | run + import | MIT and cross-platform: the only engine Ionomos could bundle. Needs ThermoRawFileParser (.raw → mzML) and a protein roll-up of `lfq.tsv`. |
| 6 ✅ | AlphaDIA | import ✅ `pg.matrix.tsv` | Apache-2.0, pip-installable; column names changed between 1.x and 2.x. |
| 7 ✅ | Proteome Discoverer | import ✅ (column format from the docs, not yet a real export) | Protein-table text export only; no supported headless mode. |

Skipped unless asked: MSFragger / Philosopher outside FragPipe, PEAKS,
CHIMERYS, our own search engine. MSFragger and DIA-NN licences make
bundling impossible, and each lab accepts its own; that is a selling point,
not a gap.

Found while building 5A/5B (2026-09-30), to fix:
- `…_DIA_CV-35.raw` is read as replicate 35, but CV-35 is a FAIMS
  compensation voltage.
- `naming.methods.<X>: {like: DIA}` borrows DIA's name rules only. The
  downstream analysis, TMT annotation and control detection still branch on
  the method's key. Decide whether `like:` should carry through to them.
- The demo's clean simulated data sometimes flags one sample as "warn". That
  is borderline: check the scorecard floors on real data.

### 5C — More analysis (value × feasibility; all possible in pure Python)

1. [ ] **Experimental designs**: paired samples, blocks (batch / plex /
   patient as fixed effects, Smyth's advice), covariates, time courses, and a
   moderated F-test. Every lab needs this.
2. [ ] **Dose-response** (CurveCurator, Apache-2.0):
   - a 4-parameter log-logistic fit
   - pEC50 with a confidence interval
   - the recalibrated F statistic and relevance score

   Compound titrations are central to chemoproteomics.
3. [ ] **Cysteine chemoproteomics:**
   - liganded-site calls with configurable thresholds (R ≥ 4 in ≥ 2 of 3
     replicates)
   - site changes corrected for protein abundance (MSstatsPTM formulas)
   - a site × compound selectivity map and a liganded fraction per compound
   - an optional CysDB annotation the user downloads (AGPL: not bundled)
4. [ ] **DEqMS** (variance tied to peptide count, which is now read). Later, a
   limpa-style detection-probability model, which would replace imputation
   for DIA and probably wants optional numpy.
5. [ ] **TMT across plexes**: IRS / bridge-channel normalisation, with a PCA
   by plex before and after.
6. [ ] **Instrument QC trending** on the recurring HeLa standard:
   - IDs, signal, peak width, mass error, RT drift
   - Levey-Jennings charts with run rules

   Intake already sees every run.
7. [ ] Phospho: localisation filter and KSEA kinase activity. Only if a lab
   runs phospho; PhosphoSitePlus is non-commercial, so it is a user download.
8. [ ] STRING / CORUM overlays: low priority.

Stay deterministic. The one credible published "AI interpretation"
(GeneAgent, Nat Methods 2025) verifies every claim against databases. Plain
templated summaries, like the key findings, are easier to trust.

### 5D — Adoption

- [ ] 2–3 pilot labs, ideally chemoproteomics groups already on FragPipe
  isoDTB / ABPP workflows.
- [ ] A J. Proteome Res. technical note once pilots exist. It should include
  the limma / FragPipeAnalystR parity tests and real-data comparisons. JOSS
  (2026 criteria) wants at least 6 months of public use.
- [ ] Interoperate rather than compete: open in FragPipe-Analyst (already
  exported), optional pmultiqc-compatible QC, SDRF.

### Risks

- **Engine dependency.** FragPipe is academic-only, and its headless interface
  changes between releases. Nesvilab could automate this themselves. Engine
  adapters are the hedge.
- **One maintainer.** Keep scope tight: no web server, cloud, or multi-user
  permissions.
- **Trust in a Python limma.** Publish the parity tests against R, and keep
  real-data fixtures in CI.

## Open questions (need a human)

Collected from the other docs; resolve before/during Phase 1.

**Lab process**
- [ ] Confirm the naming convention with users (NAMING_CONVENTION.md); collect initials → user-folder aliases for config.
- [ ] Trello board has a naming discussion — reconcile with NAMING_CONVENTION.md.
- [ ] Is `USER` == folder under `C:\Fragpipe_General\`? Who has multiple folders?
- [ ] Is plain `DDA` a real method in the lab or only isoDTB/TMT/DIA?
- [ ] Which DIA workflow is used, and what happens after a DIA run?
- [ ] What is the TMT "Peak Picking & zero Samples" pre-step (which app)? Still needed with FragPipe 24?
- [ ] Where are FASTA files kept, which ones, how often updated?

**Machine**
- [ ] Exact FragPipe 24.0 launcher path and whether `--headless` works on it as installed.
- [ ] Direction/type of the `Proteomics_File_Sharing` share.
- [ ] Sleep/power policy and whether Task Scheduler can run at logon for the shared account.
- [ ] Should results go to C: (fast, 99 GB free) or D: (slow USB, 14 TB free)? Proposal:
      run on C:, archive to D: (Phase 4).
- [ ] Install git on the PC, or deploy via zip/wheel?

**Software**
- [ ] How does FragPipe 24.0 headless locate the TMT `annotation.txt`?
- [ ] Does bundled DIA-NN suffice or is `--config-diann` needed for 2.3.2?
- [ ] Confirm the DIA condition codes with the lab: is `C` always "Compound" (not "Control")? Other codes
      in use (`V` vehicle, `T` treated …)? Set `naming.condition_codes` accordingly (D34).
- [ ] D35 warnings: are the sample-outlier / batch / imputation-mismatch thresholds right on real experiments?
      Should an outlier sample pop up (input) rather than stay a warning?
- [ ] isoDTB: which ratio direction and threshold call a cysteine "liganded" (e.g. R ≥ 4)? Then add ratio classes.
- [ ] Is a review window on every drop right long-term, or only for new users / methods / code patterns?
      Watch how it feels on the PC for a few weeks (`gui.review_drops`).
- [ ] Phase 5: allow numpy as an *optional* speed-up (dose-response, limpa)? The base install stays
      dependency-free either way.
- [ ] Phase 5: which pilot labs can we reach? Does this lab run titrations or phospho? (Orders 5C.)
- [ ] Phase 5: publish on PyPI as `ionomos` (needs a PyPI account / trusted publisher set up by the maintainer).
- [x] Agent auto-merge: removed 2026-09-27; a person merges (D31).
- [x] CI Python versions: 3.11 (floor), 3.12 (exe build), 3.14 (the PC) since 2026-09-27.
