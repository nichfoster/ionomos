# Testing labwatch

Three layers, all runnable on macOS and Windows:

| Layer | What | Command |
|---|---|---|
| Unit + e2e (`pytest`) | 180 tests: naming, config, config I/O, ledger, watcher timing, intake, overrides, **FragPipe runner + worker** (done / failed / held / timeout / stop / re-run against the fake FragPipe), resolver logic, **real tkinter dialog and the setup app** (skipped if no display), and 6 end-to-end runs of watcher-thread + intake against testbed samples | `scripts/test_mac.sh` / `scripts\test_windows.ps1` |
| Testbed (manual) | A fake lab on disk with 12 sample drops covering every path, a fake FragPipe, and the real CLI | `labwatch testbed …` |
| The real PC | `dry-run` on real folders, then a throwaway drop; **Copy diagnostics** to report back | see DEV_LOOP.md |
| GitHub Actions | the pytest suite on Linux **and Windows** on every push | Actions tab |

## One-shot setup

**macOS**
```bash
scripts/test_mac.sh --bed
```
If it says the Python has no tkinter: `brew install python-tk@3.14` (matches
Homebrew's python@3.14), delete `labwatch/.venv`, re-run.

**Windows**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_windows.ps1 -Bed
```
Needs Python 3.11+ from python.org with *tcl/tk* and *py launcher* ticked.

Both scripts create `labwatch/.venv`, install in editable mode, run ruff and
pytest, and (with `--bed`/`-Bed`) build the testbed: `./labwatch-testbed` on
macOS, **`C:\labwatch-testbed`** on Windows. (Not under your profile folder:
usernames like `Daniel Nomura` contain a space, and on Windows the config
loader refuses paths with spaces — the FragPipe rule.)

## Driving the testbed from the app

`labwatch setup` (or `LabWatch.exe`) → tab **5 Run & Test** → *Testbed*:
**Create testbed** → **Start TESTBED watcher** → pick a sample → **Drop** /
**Drop slowly** → watch the output pane (tick *follow the watcher log*).
**Resolver window demo** opens the dialog with sample data. **Reset testbed**
wipes it. Everything below is the same thing from the command line.

## Driving the testbed from the command line

Activate the venv first (`source labwatch/.venv/bin/activate` or
`labwatch\.venv\Scripts\Activate.ps1`), then, in **terminal 1**:

```bash
labwatch --config labwatch-testbed/Fragpipe_Auto/config.yaml run       # macOS
labwatch --config C:\labwatch-testbed\Fragpipe_Auto\config.yaml run   # Windows
```

(`--no-gui` to see what a headless install does — rejection notes instead of the window.)

**Terminal 2:**

```bash
labwatch testbed list                       # the 12 samples and what each proves
labwatch testbed drop iso_good              # -> queued under Fragpipe_General/EJQ
labwatch testbed drop iso_good --slow       # file-by-file copy: watch the "waiting for copy to settle" log line
labwatch testbed drop gui_unknown_user      # -> resolver window pops in terminal 1
labwatch testbed drop gui_incomplete        # -> window with "accept uneven fractions"
labwatch --config labwatch-testbed/Fragpipe_Auto/config.yaml status
labwatch testbed reset                      # wipe inbox / users / ledger / logs, keep samples
```

You can also drag folders from `labwatch-testbed/samples/` into
`labwatch-testbed/Fragpipe_Auto/inbox/` with Finder / Explorer — that is the
real user experience.

`labwatch testbed gui-demo` opens the resolver window with sample data
without needing a drop, to check the look and the keyboard flow (Tab between
fields, Enter = accept, Esc = skip).

Every filed sample is then "searched" by the testbed's fake FragPipe
(`labwatch fake-fragpipe`, ~4 s; `LABWATCH_FAKE_FP_SECONDS` changes that). It
checks what the real one checks — the workflow's FASTA exists, every manifest
file exists — so `done` means labwatch prepared the inputs correctly.

The testbed config uses fast timings (poll 1 s, stable 3 s). `labwatch testbed
init --slow-defaults` uses production timings (10 s / 60 s).

## What each sample proves

| sample | proves |
|---|---|
| `iso_good` | the happy path; date/user/method from the name; 3×3 layout |
| `iso_spaces` | spaces and `()` in folder *and* file names are sanitised, originals recorded in `labwatch.json` |
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

`deploy/build_app_mac.sh` builds a single-file `dist/exe/labwatch` on macOS
from the same PyInstaller spec used for Windows, so packaging problems
(missing hidden imports, tkinter data files) show up here first:

```bash
deploy/build_app_mac.sh
dist/exe/labwatch --version
dist/exe/labwatch testbed init /tmp/bed && dist/exe/labwatch --config /tmp/bed/Fragpipe_Auto/config.yaml check
dist/exe/labwatch setup          # the app, frozen
```

The Windows build (`deploy\build_exe.ps1`) produces `LabWatch.exe` (windowed)
and `labwatch-cli.exe` (console) from the same spec.
