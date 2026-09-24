# Testing ionomos

Three layers, all runnable on macOS and Windows:

| Layer | What | Command |
|---|---|---|
| Unit + e2e (`pytest`) | 284 tests: naming, config, config I/O, ledger, watcher timing, intake, overrides, **FragPipe runner + worker** (done / failed / held / timeout / stop / re-run against the fake FragPipe), resolver logic, **real tkinter dialog and the setup app** (skipped if no display), and 6 end-to-end runs of watcher-thread + intake against testbed samples | `scripts/test_mac.sh` / `scripts\test_windows.ps1` |
| Testbed (manual) | A fake lab on disk with 12 sample drops covering every path, a fake FragPipe, and the real CLI | `ionomos testbed …` |
| The real PC | `dry-run` on real folders, then a throwaway drop; **Copy diagnostics** to report back | see DEV_LOOP.md |
| Downstream | R-script ports byte-identical to the real scripts; t-tests/BH vs scipy; moderated t vs limma; planted-effect recovery for isoDTB/DIA/TMT; report self-contained with its data, SVG valid | `tests/test_downstream.py`, golden files in `tests/golden/` |
| JS harness (report front end) | dev-only jsdom tests of `report.js`: every section against the payload shapes that have bitten before (zero comparisons, one sample, ratio/isoDTB, 10k features × 50 samples, all p-values missing, dark/light) and hostile `<`/`&`/quote names through every dynamic-HTML sink incl. tooltips and the CSV export; a pytest check fails when `tests/js/fixture.html` drifts from the shipped assets | `cd ionomos/tests/js && npm ci && npm test`; sync check runs with pytest |
| FragPipe-Analyst port | R's RNG and Perseus imputation exact; `test_limma` all/control/others/missing vs limma; whole pipeline vs the real FragPipeAnalystR 1.1.1; PCA/hclust vs R; hypergeometric vs `phyper` | `tests/test_fpa.py`, `tests/golden/fpa/` |
| Stress | `ionomos testbed stress --n 150`: messy drops (unicode/emoji/huge names, no raws, empty raws, bad tails, duplicates, slow copies, failing searches) + chaos (worker killed mid-run, ledger locked, corrupt status file, pause/resume, cancel), then invariant checks; plus a 5000-name parser fuzz | any machine; a smaller run is in pytest |
| GitHub Actions | the pytest suite and the JS report-harness on Linux **and Windows** on every push; on a version tag also the frozen exe (pipeline + stress) and the **installer**: install, retire a fake LabWatch, upgrade, uninstall, data kept | Actions tab |
| Migration | an install from the LabWatch era (old status files, run folders, logs, lock, config pointer, env var) keeps working | `tests/test_migration.py` |

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
