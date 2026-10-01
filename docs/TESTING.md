# Testing ionomos

Three layers, all runnable on macOS and Windows:

| Layer | What | Command |
|---|---|---|
| Unit + e2e (`pytest`) | 284 tests: naming, config, config I/O, ledger, watcher timing, intake, overrides, **FragPipe runner + worker** (done / failed / held / timeout / stop / re-run against the fake FragPipe), resolver logic, **real tkinter dialog and the setup app** (skipped if no display), and 6 end-to-end runs of watcher-thread + intake against testbed samples | `scripts/test_mac.sh` / `scripts\test_windows.ps1` |
| Testbed (manual) | A fake lab on disk with 12 sample drops covering every path, a fake FragPipe, and the real CLI | `ionomos testbed …` |
| The real PC | `dry-run` on real folders, then a throwaway drop; **Copy diagnostics** to report back | see DEV_LOOP.md |
| Downstream | other engines (`tests/test_engines.py`): each format with its real column names, FDR / reverse / contaminant filters, provenance, and the same simulated experiment giving identical hits through DIA-NN, MaxQuant, Spectronaut and Sage files (Sage's `lfq.tsv`: razor grouping, fractions added, peptide and protein q filters, median polish checked by hand); D35 insights: outlier / batch / missingness / p-value-shape detection on planted data and no false alarms on clean data, rank-based gene sets, end to end into the TSVs, analysis.json and the report (`tests/test_insights.py`); R-script ports byte-identical to the real scripts; t-tests/BH vs scipy; moderated t vs limma; planted-effect recovery for isoDTB/DIA/TMT; report self-contained with its data, SVG valid | `tests/test_downstream.py`, golden files in `tests/golden/` |
| JS harness (report front end) | dev-only jsdom tests of `report.js`: every section against the payload shapes that have bitten before (zero comparisons, one sample, ratio/isoDTB, 10k features × 50 samples, all p-values missing, dark/light) and hostile `<`/`&`/quote names through every dynamic-HTML sink incl. tooltips and the CSV export; the search grammar (lists, wildcards, regex, `desc:`, `term:`), suggestions, box selection, highlight groups, the address state, hit filters and every discovery / QC section (`discovery.test.mjs`); the help: the nav entry, a **?** on each section, QC tab and issue box, panels that open and close, every help link resolving in the page, escaped issue titles, a report without help (`help.test.mjs`); a pytest check fails when `tests/js/fixture.html` drifts from the shipped assets | `cd ionomos/tests/js && npm ci && npm test`; sync check runs with pytest |
| Help (D46) | the help content parses and every link resolves; every doctor issue code, attention kind, intake rejection kind and held-search reason found in the code has an entry; the Markdown renderer escapes everything and links only `#id` / `https://`; `help.html` is self-contained and every anchor on it resolves; `ionomos help` prints a topic and writes the page (log folder, else app data); the report embeds every entry its help links to, under 60 KB; the files ship in the wheel and the exe spec | `tests/test_help.py`; the report side in `tests/js/test/help.test.mjs` |
| Sage runner (D51) | `engine: sage` end to end against stand-ins for Sage and ThermoRawFileParser (`ionomos fake-sage`, `fake-rawparser`): conversion into `sage_mzml/` and reuse on retry, a failed or empty conversion, the lab's own settings kept, telemetry switched off (and an older Sage without the switch), held jobs with their help topics, fractions added in the analysis | `tests/test_sage_runner.py` |
| SDRF export | `results/sdrf.tsv` for DIA (manifest, exclusions, renamed conditions, no manifest), isoDTB (light/heavy rows), TMT (annotation.txt, `tmt:` plexes, channels from sample names), a table alone (none written), a failing stage; checked against the spec's structure rules every run, and with the official validator `sdrf-pipelines` when it is installed (dev-only; CI installs it on Linux) | `tests/test_sdrf.py`; `pip install sdrf-pipelines` to add the validator |
| SDRF import (design) | SDRFs in the spec's real layout (quantms examples: mixed-case headers, repeated columns, `NT=label free sample`, `TMT126`…, fractions): factor columns joined or picked (`sdrf_factor`), TMT plexes from file groups, pooled rows; `results/` and `*_ionomos/` never searched; precedence `sample_conditions` > SDRF > manifest > engine; FragPipe's `_uncalibrated.mzML` matched to `.raw`; unmatched runs and a foreign SDRF raise `SDRF_UNMATCHED_RUNS` | `tests/test_sdrf_design.py` |
| TMT across plexes | MSstatsTMT summary vs **MSstatsTMT 2.20** itself to 1e-9 (fractions, technical replicates, a protein without Norm in one run; golden made by `tests/golden/msstatstmt/run_msstatstmt.R`), a median polish checked by hand, the strongest PSM kept; IRS on a planted plex effect (PC1 R² by plex 0.999 → 0.001, by condition 0.000 → 0.984; 14 / 15 planted hits vs 10 / 15 without IRS); references from the setting, SDRF and names; plex means only for balanced designs, else `TMT_PLEXES_NOT_NORMALISED`; TMT-Integrator input untouched; MaxQuant experiments and PD files as plexes; the report's PCA by plex, before / after (`plexes.test.mjs`) | `tests/test_plexes.py`; regenerate the golden with `python make_input.py && Rscript run_msstatstmt.R <R lib>` |
| Dose-response (D44) | the CurveCurator port against CurveCurator 0.6.0's own output on two simulated designs (classes, pEC50, F, p; CurveCurator is not needed to run the test); the F tail and quantile vs scipy; planted pEC50s recovered, flat features not called; dose parsing (units, µ/u, `0p1`, two doses in a name); fewer than 4 doses skipped with a note; isoDTB-style ratios; bad `analysis.doses` → an issue; a crash in the stage still gives the report; the report section in jsdom (`tests/js/test/dose.test.mjs`) | `tests/test_dose_response.py`, `tests/golden/dose_response/` |
| Notifications, log rotation (D58) | no real network: webhooks go to an HTTP server inside the test process, email to a small SMTP server there (STARTTLS + login through a stub of `smtplib.SMTP`). Off by default; every `notify:` validation error; the Slack / Teams / generic payload shapes; `include_names: false` sends no name, reason, path or count; address and password from an environment variable; done / failed / held from the real worker against the fake FragPipe, held once per reason across polls and a restart, `on:` respected, a cancel silent; a webhook that never answers leaves the job done and the worker not waiting, logged once; no redirect followed; secrets absent from `diagnose`, the saved report, the bundle (config, logs, crash files) and Report a problem, also with a config that doesn't parse; `ionomos notify-test`; the config writer round-trips the block (bare `on:` too). Rotation: size and count, and a rename refused as on Windows loses no line and rotates later | `tests/test_notify.py` |
| Time courses (D53) | the F over time, the trend t and the interaction F against **limma 3.68.5** to 1e-8 on 150 features × 24 samples: the plain model, a replicate block, and missing values (golden made by `tests/golden/timecourse/run_timecourse_reference.R`); times read from names and `analysis.times`, series planning (a shared control as time 0, too few points, two conditions at one time), deterministic patterns, a simulated experiment end to end (planted shapes recovered, classes, peak time, TSV, report payload, the `TIMES` issue); the report section in the JS harness. Regenerate: `cd tests/golden/timecourse && python make_timecourse_inputs.py && Rscript run_timecourse_reference.R <R library>` | `tests/test_timecourse.py`, `tests/golden/timecourse/`, `tests/js/test/time.test.mjs` |
| Liganded cysteines (D52) | the call rule at and around the threshold, both ratio directions and the reversed-ratio warning, fewer replicates than the rule, selectivity (selective / shared / unresolved), the protein view, a CysDB-style annotation (key column, accession + residue columns, unusable files), and a simulated isoDTB experiment end to end (planted sites recovered, TSVs, report payload, issues); the report section in the JS harness | `tests/test_cys.py`, `tests/js/test/cys.test.mjs` |
| Instrument QC trending (D45) | per-run metrics from small tables with the real column names (DIA-NN `report.stats.tsv` / `pg_matrix` / `report.tsv`, FragPipe `psm.tsv` / `combined_protein.tsv`, Windows paths, stamped / calibrated run names, isotope-error ppm, oversized and broken tables); which runs are QC standards (words, separators, experiments left out, `exclude`, dedicated method, series by standard and amount); acquisition time from the stamp or the file; the store (a re-run updates its row, damaged lines skipped, compaction); every Westgard rule, the CUSUM drift flag, direction (better ≠ bad), log-scale signal, pinned baselines, RT shift; the page (self-contained, escaped, no network); config validation and the app's config writer; the CLI (`qc-trend`, `--rebuild` read-only, `--open`); the app button's helper; the attention item raised and closed, `popup`; a broken QC read never fails the job; end to end through the worker with the testbed's fake FragPipe (which now writes DIA-NN's `report.stats.tsv` and a `psm.tsv` per experiment) | `tests/test_qctrend.py` |
| Search quality per run (D55) | `psm.tsv` tables with FragPipe's documented column names and planted values (**not real FragPipe output**): mass-error quantiles, PSMs by missed cleavages, charge and length; every run read without a manifest; an earlier attempt's folder, an oversized and a broken table skipped with a note; the two warnings at, inside and beyond their limits, a run with too few PSMs not judged; mass offsets and missing columns; `psm_qc.tsv`, `analysis.json`, the report payload and the doctor end to end; the step switched off, absent, without a quant table, and crashing; the search output left untouched; the QC tab in the JS harness (table, four charts, DIA-NN's summary, many runs, help, hostile names) | `tests/test_psmqc.py`, `tests/js/test/psm.test.mjs` |
| FragPipe-Analyst port | R's RNG and Perseus imputation exact; `test_limma` all/control/others/missing vs limma; whole pipeline vs the real FragPipeAnalystR 1.1.1; PCA/hclust vs R; hypergeometric vs `phyper` | `tests/test_fpa.py`, `tests/golden/fpa/` |
| Designs, F-test, DEqMS (D42, D43) | against limma 3.68.5 / DEqMS 1.30.0 to 1e-8 on 200 proteins × 12 samples: a replicate block with complete data and with missing values per row (incl. a condition absent and an unestimable block), block + numeric + factor covariates, one-vs-others with a block, `topTableF` for the blocked, covariate and plain models; R's `loess` (k-d tree + vertex interpolation) and `spectraCounteBayes` on the blocked and plain fits; confounded / incomplete / no-df designs explained, never crashed; settings errors; end to end: blocking on a replicate batch finds more hits, a confounded block falls back byte-identically to the plain model, DEqMS without counts warns. The exported `reproduce_design_in_R.R` is run in R when `IONOMOS_R_LIBS` names a library with limma (dev-only; skipped otherwise). Regenerate: `cd tests/golden/design && python make_design_inputs.py && Rscript run_design_reference.R <R library>` | `tests/test_design.py`, `tests/golden/design/`, `tests/js/test/design.test.mjs` |
| Stress | `ionomos testbed stress --n 150`: messy drops (unicode/emoji/huge names, no raws, empty raws, bad tails, duplicates, slow copies, failing searches) + chaos (worker killed mid-run, ledger locked, corrupt status file, pause/resume, cancel), then invariant checks; plus a 5000-name parser fuzz | any machine; a smaller run is in pytest |
| GitHub Actions | the pytest suite and the JS report-harness on Linux **and Windows** on every push; on a version tag also the frozen exe (pipeline + stress) and the **installer**: install, retire a fake LabWatch, upgrade, uninstall, data kept | Actions tab |
| Migration | an install from the LabWatch era (old status files, run folders, logs, lock, config pointer, env var) keeps working | `tests/test_migration.py` |
| pip install / demo | `ionomos demo` finds what it planted (hits, on/off proteins, gene sets by both tests), never writes into an existing folder, is reproducible, works offline with no lab config; `demo` and `analyze <table\|folder>` run in a Python where `import tkinter` fails (a subprocess, so nothing Tk can creep into the analysis path); the demo gene sets and the LICENSE copy ship in the package | `tests/test_demo.py`, `tests/test_packaging.py`; the wheel itself: `publish-pypi` workflow (below) |

## One-shot setup

**macOS**
```bash
scripts/test_mac.sh --bed
```
If it says the Python has no tkinter: `brew install python-tk@3.14` (matches
Homebrew's python@3.14), delete `ionomos/.venv`, re-run.

**Windows**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_windows.ps1 -Bed
```
Needs Python 3.11+ from python.org with *tcl/tk* and *py launcher* ticked.

Both scripts create `ionomos/.venv`, install in editable mode, run ruff and
pytest, and (with `--bed`/`-Bed`) build the testbed: `./ionomos-testbed` on
macOS, **`C:\ionomos-testbed`** on Windows. (Not under your profile folder:
usernames like `Daniel Nomura` contain a space, and on Windows the config
loader refuses paths with spaces — the FragPipe rule.)

## Driving the testbed from the app

`ionomos setup` (or `Ionomos.exe`) → tab **5 Run & Test** → *Testbed*:
**Create testbed** → **Start TESTBED watcher** → pick a sample → **Drop** /
**Drop slowly** → watch the output pane (tick *follow the watcher log*).
**Resolver window demo** opens the dialog with sample data. **Reset testbed**
wipes it. Everything below is the same thing from the command line.

## Driving the testbed from the command line

Activate the venv first (`source ionomos/.venv/bin/activate` or
`ionomos\.venv\Scripts\Activate.ps1`), then, in **terminal 1**:

```bash
ionomos --config ionomos-testbed/Fragpipe_Auto/config.yaml run       # macOS
ionomos --config C:\ionomos-testbed\Fragpipe_Auto\config.yaml run   # Windows
```

(`--no-gui` to see what a headless install does — rejection notes instead of the window.)

**Terminal 2:**

```bash
ionomos testbed list                       # the 12 samples and what each proves
ionomos testbed drop iso_good              # -> queued under Fragpipe_General/EJQ
ionomos testbed drop iso_good --slow       # file-by-file copy: watch the "waiting for copy to settle" log line
ionomos testbed drop gui_unknown_user      # -> resolver window pops in terminal 1
ionomos testbed drop gui_incomplete        # -> window with "accept uneven fractions"
ionomos --config ionomos-testbed/Fragpipe_Auto/config.yaml status
ionomos testbed reset                      # wipe inbox / users / ledger / logs, keep samples
```

You can also drag folders from `ionomos-testbed/samples/` into
`ionomos-testbed/Fragpipe_Auto/inbox/` with Finder / Explorer — that is the
real user experience.

`ionomos testbed gui-demo` opens the resolver window with sample data
without needing a drop, to check the look and the keyboard flow (Tab between
fields, Enter = accept, Esc = skip).

Every filed sample is then "searched" by the testbed's fake FragPipe
(`ionomos fake-fragpipe`, ~4 s; `IONOMOS_FAKE_FP_SECONDS` changes that). It
checks what the real one checks — the workflow's FASTA exists, every manifest
file exists — so `done` means ionomos prepared the inputs correctly.

The testbed config uses fast timings (poll 1 s, stable 3 s). `ionomos testbed
init --slow-defaults` uses production timings (10 s / 60 s).

## What each sample proves

| sample | proves |
|---|---|
| `iso_good` | the happy path; date/user/method from the name; 3×3 layout |
| `iso_spaces` | spaces and `()` in folder *and* file names are sanitised, originals recorded in `ionomos.json` |
| `dia_good` | `raw/` subfolder; DIA tails (`cond_biorep`); manifest type `DIA` |
| `tmt_good` | TMT tails (`_TMT_F#`), biorep forced to 1, `experiment.yaml` channel map carried through |
| `glued_initials` | `IJD05` resolves to Isaac via alias `IJD` |
| `method_in_files` | method keyword only in the raw file names |
| `gui_unknown_user` | resolver: user combobox, "remember XYZ → user" writes `learned_aliases.yaml` |
| `gui_no_method` | resolver: choosing a method re-parses the file grid |
| `gui_bad_tail` | resolver: a file with no numeric tail gets its rep/frac typed in |
| `gui_incomplete` | resolver: uneven fractions → "accept uneven" checkbox |
| `reject_no_raws` | folder with no `.raw` is left alone forever (no note, no queue) |
| `reject_two_methods` | ambiguous method → resolver |
| `fp_fail` | filed fine, then the fake FragPipe fails → `failed` + `FAILED.txt`; retry re-runs it |

After a GUI answer, look at `experiment.yaml` inside the moved folder: that is
the persisted decision, and the same file can be hand-written by a user to
skip the window next time.

## Windows-specific things worth trying on the real PC

- Drag a folder from a **USB drive** (cross-volume for Explorer, but the inbox →
  users_root move is same-volume, so it should be an instant rename).
- Drop, then immediately open one of the raw files in another program: the log
  should show `cannot move … will retry` and succeed once the file is closed.
- Log out and back in: the Task Scheduler task should restart the watcher;
  anything left `running` in the ledger is marked `failed (interrupted)`.

## The frozen executable

`deploy/build_app_mac.sh` builds a single-file `dist/exe/ionomos` on macOS
from the same PyInstaller spec used for Windows, so packaging problems
(missing hidden imports, tkinter data files) show up here first:

```bash
deploy/build_app_mac.sh
dist/exe/ionomos --version
dist/exe/ionomos testbed init /tmp/bed && dist/exe/ionomos --config /tmp/bed/Fragpipe_Auto/config.yaml check
dist/exe/ionomos setup          # the app, frozen
```

The Windows build (`deploy\build_exe.ps1`) produces `Ionomos.exe` (windowed)
and `ionomos-cli.exe` (console) from the same spec.

## The pip package

What `pip install ionomos` gets (docs/QUICKSTART.md). To check a build by hand:

```bash
cd ionomos && .venv/bin/python -m build --outdir /tmp/ionomos-dist .   # wheel + sdist (`build` is in [dev])
python3 -m venv /tmp/fresh && /tmp/fresh/bin/pip install /tmp/ionomos-dist/*.whl
cd /tmp && /tmp/fresh/bin/ionomos demo --open      # from a folder that is not the checkout
```

`.github/workflows/publish-pypi.yml` does the same on every `v*` tag (or by
hand): builds, `twine check`, installs the wheel into a fresh venv on Windows,
macOS and Linux (Python 3.11 and 3.14) and the sdist on Linux, and runs
`ionomos demo` and `ionomos analyze` from them. It uploads to PyPI only once
the maintainer has set up trusted publishing (the steps are at the top of the
workflow file).

## Stress testing

```bash
ionomos testbed stress                 # 60 drops + chaos + 3000 fuzzed names, ~30 s
ionomos testbed stress --n 300 --seed 7 --keep   # bigger; keep the folder to look at
```

It prints the outcome counts and either `all invariants hold` (exit 0) or the
list of violations (exit 1). The invariants: no raw file lost or duplicated
(count + bytes), every drop filed or noted, no job stuck queued/running,
DONE/FAILED notes present, ledger integrity OK, no CRITICAL log record.
Failure modes of the fake FragPipe for manual testing:
`IONOMOS_FAKE_FP_MODE=oom | msfragger | step-fail-exit0 | silent-exit0`.

## Downstream golden files

`ionomos/tests/golden/` holds inputs + reference outputs, and the scripts that
made them. To regenerate (needs R with dplyr/stringr/readr/tidyr/purrr/tibble
and limma; scipy for the stats file):

```bash
cd ionomos/tests/golden
python3 make_inputs.py && Rscript run_r_scripts.R
python3 make_limma_reference.py && Rscript run_limma.R
python3 make_stats_reference.py        # in an env with scipy
```

The R ports must stay byte-identical to `R_*.tsv`. If the lab changes an R
script, regenerate and re-port.

### FragPipe-Analyst port (`tests/test_fpa.py`, `tests/golden/fpa/`)

```bash
cd ionomos/tests/golden/fpa
python3 make_fpa_inputs.py && Rscript run_fpa_reference.R <R library with limma>
```

`run_fpa_reference.R` is FragPipeAnalystR's `manual_impute()` and `test_limma()`
transcribed to base R + limma. `e2e/` is the real FragPipeAnalystR 1.1.1 run on
a simulated DIA-NN matrix with the `reproduce_in_R.R` Ionomos writes (how:
`e2e/README.md`). Installing FragPipeAnalystR into a scratch library:
`BiocManager::install(c("limma", "SummarizedExperiment", "MSnbase", ...))` then
`remotes::install_github("Nesvilab/FragPipeAnalystR@v1.1.1")` — see its README.

### Dose-response port (`tests/test_dose_response.py`, `tests/golden/dose_response/`)

```bash
cd ionomos
.venv/bin/python tests/golden/dose_response/make_dose_inputs.py     # simulated titrations A and B
uv venv --python 3.12 /tmp/ccvenv && uv pip install --python /tmp/ccvenv/bin/python curve-curator==0.6.0
/tmp/ccvenv/bin/python tests/golden/dose_response/run_curvecurator.py   # -> <design>_curvecurator.tsv
```

CurveCurator needs Python 3.11–3.13 and numpy / scipy / pandas, so it lives in
its own throwaway venv, never in Ionomos' dependencies. `run_curvecurator.py`
calls its pipeline functions with the defaults its TOML parser fills in (OLS,
"standard" speed) plus alpha 0.05 and fc_lim 0.45. Design A is decryptM-like
(8 doses, one replicate), B replicated (5 doses × 3); both keep ≤ 16 dosed
samples, so numpy's argsort keeps replicates in order as the port does.

**Windows during tests.** Tests that drive real Tk windows (the app, the naming
/ review window) skip on a dev machine so they don't cover the screen while you
work. They run in CI (GitHub Actions sets `CI`; the Windows runner has a
display). To run them locally:

```bash
cd ionomos && IONOMOS_GUI_TESTS=1 .venv/bin/pytest tests/test_app.py tests/test_review.py tests/test_resolve.py
```

