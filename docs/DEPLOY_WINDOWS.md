# Deploying LabWatch on the proteomics PC

> **Still prototyping?** Use the *development install* instead —
> [DEV_LOOP.md](DEV_LOOP.md): one script, then every fix pushed from the Mac
> is one button in the app. The ways below are for the finished tool.

Two ways. **Way A** (the app) is what you want: one folder, double-click,
follow the tabs. **Way B** (script + wheel) is the fallback if you can't build
the exe.

Both happen on the PC while logged in as the shared lab account (the one that
runs FragPipe). No internet is needed on the PC.

---

## Way A — the LabWatch app (recommended)

### A1. Get the exe (2 min)

GitHub builds it. Either download `LabWatch-<version>-windows.zip` from the
repo's **Releases** page (created automatically when a version is tagged:
`git tag v0.2.0 && git push --tags`), or run *Actions → build-exe → Run
workflow* and take the zip from "Artifacts".

To build by hand instead (any Windows machine with Python 3.11+ incl. tcl/tk):
`powershell -ExecutionPolicy Bypass -File deploy\build_exe.ps1` → `dist\LabWatch-<version>-windows.zip`.

### A2. Install (2 min)

1. Unzip `LabWatch-<version>-windows.zip` to **`C:\Fragpipe_Auto`** (any
   folder is fine as long as the path has **no spaces**).
2. Double-click **`LabWatch.exe`**. The setup wizard opens on tab **1 Folders**.

### A3. Follow the tabs (10 min)

| Tab | What to do |
|---|---|
| **1 Folders** | Check the paths in *Quick fill* (`C:/Fragpipe_Auto` and `C:/Fragpipe_General`) → **Apply layout** → **Create all missing folders**. Then set **Inbox** to the shared drop folder if it's somewhere else (Browse…), and **FragPipe launcher** to the exe under `C:\FragPipe\FragPipe-24.0\` (needed only for searches — fine to leave for later). Green dots = exists. |
| **2 Users** | Type each lab member's name → **Add** (creates `C:\Fragpipe_General\<Name>`). Select a user, type their initials (`IJ, IJD`) → **Apply aliases**. |
| **3 Methods** | For each method pick the **Workflow file** and **FASTA file** (dropdowns list what's in the folders — put files there first, see A4). |
| **4 Advanced** | Usually nothing. Threads/RAM default to 28 / 48 GB for this PC. |
| **5 Run & Test** | **Save & Check** → all ✓ (a `!` on the FragPipe launcher / a method means those jobs will *wait* until it's fixed) → **Install startup task** → **Start watcher**. Optional: **Desktop shortcut**. |

Save writes `C:\Fragpipe_Auto\config.yaml`. Reopen `LabWatch.exe` any time to
change anything; it remembers where the config is.

### A4. Workflow + FASTA files (5 min, can be later)

For each method, in the **FragPipe GUI**:

1. Load the workflow the lab uses (or open a recent successful run's `fragpipe.workflow`).
2. **Database tab → set the FASTA (with decoys).** Saved without one, headless runs fail with "FASTA file path is empty".
3. Check quant settings (isoDTB: MBR on).
4. **Workflow tab → Save to custom folder** → `C:\Fragpipe_Auto\workflows\isoDTB.workflow`.
5. Copy the FASTA file into `C:\Fragpipe_Auto\fasta\`.

Back in the app: tab 3 → select the method → pick the files → **Apply changes** → **Save**.

### A4b. First real search (15–60 min, once)

1. Tab 1 → **Find FragPipe** (fills `…\fragpipe\bin\fragpipe.bat`) → **Save**.
2. Tab 5 → **Save & Check**: `FragPipe launcher` ✓ and `methods.isoDTB` ✓ (workflow + FASTA).
3. Drop a *small* real isoDTB folder (e.g. one replicate, 2–3 fractions).
4. Watch tab 5: *FragPipe: RUNNING job N* → *done*. In the experiment folder:
   `DONE.txt`, `fragpipe\` with the results, `labwatch_run\fragpipe_console.log`.
5. If it says FAILED: open `FAILED.txt` / the console log, fix, **Retry a failed
   job…**. Paste **Copy diagnostics** + the console log to Claude if it's unclear.

Advanced → *Run FragPipe automatically* off = experiments are only filed (the
Phase 1 behaviour).

### A5. Try it (3 min)

Tab 5 → **Testbed** → **Create testbed** → **Start TESTBED watcher** → pick
`iso_good` → **Drop**. Watch the output pane: "detected … stable … queued job 1
… FragPipe … done" (the testbed uses a fake FragPipe that takes a few seconds).
`fp_fail` shows a failed search and **Retry a failed job…**.
Try `gui_unknown_user` → the resolver window pops up. **Stop watcher** when done.

Then a real one: make a folder `20260916_<yourinitials>_isoDTB_test` with two
tiny files `test_1_1.raw`, `test_1_2.raw` (any content), drag it into the inbox.
Within ~70 s it moves to `C:\Fragpipe_General\<you>\` with a `labwatch.json`.

### A6. Tell the lab

The one-paragraph version of [NAMING_CONVENTION.md](NAMING_CONVENTION.md):

> Put **your initials** and **isoDTB / TMT / DIA** somewhere in the folder
> name. Raw files end in `_rep_fraction` (isoDTB), `_F#` (TMT) or
> `_condition_rep` (DIA). Drag the folder into `C:\Fragpipe_Auto\inbox`. If
> LabWatch can't work it out, a window will ask you.

---

## Way B — script install (no exe)

Use if you'd rather not build the exe. Same result, but the app is started
with a command instead of a double-click.

1. On the Mac: `deploy/make_release.sh` → `dist/labwatch-<version>-windows.zip` (wheel + offline deps + scripts).
2. On the PC: unzip to the Desktop, then in PowerShell:

```powershell
cd $HOME\Desktop\labwatch-<version>-windows
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

   This creates the folders, a venv in `C:\Fragpipe_Auto\venv`, a config, and the startup task.
3. Open the app: `C:\Fragpipe_Auto\venv\Scripts\labwatch.exe setup` — then follow A3–A6.

---

## Check Python (only needed for Way B, or to build the exe)

```powershell
py -3.14 -c "import tkinter; print('ok')"
```

- `ok` → fine.
- `No module named _tkinter` → Settings → Apps → Python 3.14 → **Modify** → tick **tcl/tk and IDLE**.
- `py` not recognised → install from [python.org](https://www.python.org/downloads/windows/): tick
  **Add python.exe to PATH**, then *Customize installation* → **tcl/tk and IDLE** + **py launcher**.

## Day-to-day

| Want to… | Do |
|---|---|
| change any setting | open `LabWatch.exe`, edit, **Save** (restart the watcher for timing changes) |
| see the queue | tab 5 → **Show queue**, or `labwatch-cli.exe status` |
| see the log | tab 5 → tick *follow the watcher log*, or `C:\Fragpipe_Auto\logs\labwatch.log` |
| report a problem | tab 5 → **Copy diagnostics** → paste (or `labwatch-cli.exe diagnose`) |
| stop / start the watcher | tab 5 buttons; the startup task restarts it at next logon |
| add a user | tab 2 → Add; or just create the folder under `C:\Fragpipe_General` |
| see what a folder would do | `labwatch-cli.exe dry-run "C:\path\to\folder"` |
| upgrade | unzip the new build over `C:\Fragpipe_Auto`; config/data untouched |
| uninstall | tab 5 → **Remove** startup task; delete `LabWatch.exe`/`labwatch-cli.exe`. Data stays. Full reset: `deploy\clean_slate.ps1`. |

## Troubleshooting

- **Nothing happens after a drop.** Is the watcher running (tab 5 status line)?
  The folder needs ≥1 `.raw` and must be unchanged for 60 s.
- **`<name>.REJECTED.txt` in the inbox.** Open it — it says why. Fix the folder
  (or add an `experiment.yaml`); it's retried automatically. Deleting the note also retries.
- **Job says "waiting: …"** (tab 5 or `status`). A setup file is missing — the
  FragPipe launcher, or that method's workflow/FASTA. Fix it; the job starts by itself.
- **Job FAILED.** `FAILED.txt` in the experiment folder has the reason;
  `labwatch_run\fragpipe_console.log` has FragPipe's full output. Fix, then
  tab 5 → **Retry a failed job…** (old output is kept as `fragpipe_previous_<time>\`).
- **Stopping/updating LabWatch during a search** kills that FragPipe run; the job
  re-runs from the start when the watcher starts again.
- **"cannot move … will retry"** in the log: Explorer/antivirus still had a
  file open. It retries with backoff.
- **Resolver window never appears.** Tab 5 → Check → "resolver window" line must be ✓.
  The startup task must be *interactive* (the app installs it that way). If
  someone changed it to "run whether user is logged on or not", reinstall from tab 5.
- **PC sleeps mid-run.** Settings → System → Power → Sleep: Never (plugged in).
- **`UnicodeDecodeError … byte 0x97`** when reading a config. A file was written
  in Windows-1252 (e.g. by an old build, or by Notepad "ANSI"). Re-save it as
  UTF-8 (Notepad → Save as → Encoding: UTF-8) or delete and recreate it
  (testbed: **Reset**/re-**Create testbed**). Builds from 0.1.1 always write UTF-8.
- **"contains a space" on Check.** Every path in the config must be space-free
  (FragPipe). Don't put the testbed or the install under `C:\Users\<First Last>\…`;
  use `C:\labwatch-testbed` / `C:\Fragpipe_Auto`.
- **Double-clicking `LabWatch.exe` flashes a console window and closes.** Look
  for `LabWatch-crash.txt` next to the exe — the full error is in there (the
  app also shows it in a dialog). Builds before 0.1.2 had a bug where the
  console exe overwrote the windowed one (Windows ignores the case of
  `LabWatch.exe` vs `labwatch.exe`); download a current build from Releases.
- **Start over completely.** `deploy\clean_slate.ps1` removes the program
  (task, exes, venvs, shortcut, remembered config path) and keeps all data and
  `config.yaml`; `-Config` also removes the config and ledger.
- **`LabWatch.exe` flagged by antivirus / SmartScreen.** Unsigned PyInstaller
  exes sometimes are. "More info → Run anyway", or use Way B.
