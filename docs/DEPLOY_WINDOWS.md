# Deploying labwatch on the proteomics PC

Time: ~20 minutes the first time, ~2 minutes for an upgrade.
Everything happens on the Windows PC in **one PowerShell window**, logged in
as the shared lab account (the one that will run FragPipe).

## 0. What you need before you start

| Item | Where from | Notes |
|---|---|---|
| `labwatch-<version>-windows.zip` | build on your Mac: `deploy/make_release.sh` → `dist/` | copy via USB / Dropbox / the shared folder |
| Python 3.11+ (3.14 is already on the PC) | [python.org/downloads/windows](https://www.python.org/downloads/windows/) | **only if `py -3.14 -c "import tkinter"` fails** — see step 1 |
| One `.workflow` file per method | export from FragPipe GUI — step 4 | isoDTB first; TMT and DIA can come later |
| The FASTA file(s) those workflows use | wherever the lab keeps them | a copy goes in `C:\Fragpipe_Auto\fasta\` |

No internet is needed on the PC — the zip carries all dependencies.

## 1. Check Python (30 seconds)

Open PowerShell and run:

```powershell
py -3.14 -c "import tkinter; print('ok')"
```

- prints `ok` → go to step 2.
- `No module named _tkinter` → the resolver window can't work. Fix: Settings →
  Apps → Python 3.14 → Modify → tick **tcl/tk and IDLE** → Next. (Or install
  fresh from python.org: on the first screen tick **Add python.exe to PATH**,
  then *Customize installation* → tick **tcl/tk and IDLE** and **py launcher**.)
- `py` not recognised → install Python from python.org as above.

## 2. Unzip and install (2 minutes)

1. Copy `labwatch-<version>-windows.zip` to the Desktop and **Extract All**.
2. In PowerShell:

```powershell
cd $HOME\Desktop\labwatch-<version>-windows
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer creates this layout and registers a Task Scheduler task that
starts the watcher at logon:

```
C:\Fragpipe_Auto\
  inbox\               ← users drop folders here
  workflows\           ← you put .workflow files here (step 4)
  fasta\               ← you put FASTA files here (step 4)
  logs\labwatch.log
  config.yaml          ← you edit this (step 3)
  run_labwatch.bat     ← what Task Scheduler runs; double-click to run by hand
  venv\                ← the Python environment (don't touch)
C:\Fragpipe_General\   ← results land in <user>\<experiment>\
```

It ends by running `labwatch check`. Red ✗ lines are things to fix; `!` lines
are fine for now (FragPipe isn't wired in until Phase 2).

To upgrade later: unzip the new release, run `install.ps1` again. Config,
inbox, logs and data are kept.

## 3. Edit `C:\Fragpipe_Auto\config.yaml` (5 minutes)

Open it in Notepad. Three things to set:

**a) users** — every lab member is a folder under `C:\Fragpipe_General\`.
Create the folders (`EJQ`, `Isaac`, `Chris`, …) in Explorer. Then add the
initials people actually put in folder names:

```yaml
users:
  aliases:
    Isaac: [IJ, IJD]
    EJQ:   [EJQ_2]
    Chris: [CS]
  default: ""          # or "_unsorted" to accept unknown users into that folder
```

**b) fragpipe_exe** — find the launcher (Phase 2 needs it; harmless now):

```powershell
Get-ChildItem C:\FragPipe\FragPipe-24.0 -Recurse -Include fragpipe.exe,fragpipe.bat | Select-Object FullName
```

and put the path (forward slashes) in `paths.fragpipe_exe`.

**c) methods** — the `workflow:` names must match the files you export in step 4.

Everything else can stay as is. Save, then:

```powershell
C:\Fragpipe_Auto\venv\Scripts\labwatch.exe --config C:\Fragpipe_Auto\config.yaml check
```

## 4. Export workflows from FragPipe (5 minutes, can be done later)

For each method, in the FragPipe **GUI**:

1. Load the workflow the lab actually uses (e.g. `isoDTB-ABPP`, or the tweaked
   one from a recent successful run — open that run's `fragpipe.workflow`).
2. **Database tab → set the FASTA (with decoys).** A workflow saved without a
   database fails headless with "FASTA file path is empty".
3. Check Quant tab settings (isoDTB: MBR on).
4. **Workflow tab → Save to custom folder** → `C:\Fragpipe_Auto\workflows\isoDTB.workflow`
   (name must match `methods.isoDTB.workflow` in config.yaml).
5. Copy the FASTA file itself into `C:\Fragpipe_Auto\fasta\`.

Repeat for `TMT10-MS3.workflow` and `DIA.workflow` when ready.

## 5. Start it and try a drop (3 minutes)

```powershell
schtasks /Run /TN labwatch
```

A console window appears and stays open (that's the watcher). Then:

1. Make a test folder on the Desktop, e.g. `20260916_<yourinitials>_isoDTB_test`,
   put two tiny files in it named `test_1_1.raw` and `test_1_2.raw` (any
   content — rename a text file), and drag it into `C:\Fragpipe_Auto\inbox`.
2. Within ~70 s it disappears from the inbox and shows up under
   `C:\Fragpipe_General\<you>\` with a `labwatch.json` inside.
3. Try a bad one: a folder named `mystery_run` with `x_1_1.raw` in it → the
   **resolver window** pops up asking for user and method. Pick, press Enter.
4. `labwatch status` shows both as `queued`:

```powershell
C:\Fragpipe_Auto\venv\Scripts\labwatch.exe --config C:\Fragpipe_Auto\config.yaml status
```

To see what labwatch *would* do with any folder without touching it:

```powershell
C:\Fragpipe_Auto\venv\Scripts\labwatch.exe --config C:\Fragpipe_Auto\config.yaml dry-run "C:\path\to\folder"
```

## 6. Tell the lab

Send them [NAMING_CONVENTION.md](NAMING_CONVENTION.md) — the short version:

> Name your folder with **your initials** and **isoDTB / TMT / DIA** somewhere
> in it. Raw files end in `_rep_fraction` (isoDTB), `_F#` (TMT) or
> `_condition_rep` (DIA). Drag the folder into `C:\Fragpipe_Auto\inbox`. If
> labwatch can't work it out, a window will ask you.

## Day-to-day

| Want to… | Do |
|---|---|
| see the queue | `labwatch status` (add `--all` for finished jobs) |
| see logs | `C:\Fragpipe_Auto\logs\labwatch.log` |
| stop the watcher | close its console window, or `schtasks /End /TN labwatch` |
| start it by hand | double-click `C:\Fragpipe_Auto\run_labwatch.bat` |
| add a user | create `C:\Fragpipe_General\<Name>\`; add their initials under `users.aliases` |
| change timing / threads | edit `config.yaml`, restart the watcher |
| disable the popup window | `gui: {enabled: false}` in config.yaml → problems get a `.REJECTED.txt` note instead |
| uninstall | `powershell -File uninstall.ps1` (keeps config, inbox, data) |

## Troubleshooting

- **Nothing happens after a drop.** Is the watcher console open? `labwatch check`
  green? The folder needs ≥1 `.raw` file and must be unchanged for 60 s
  (`watcher.stable_seconds`).
- **`<name>.REJECTED.txt` appeared in the inbox.** Open it; it says why. Fix the
  folder (or add an `experiment.yaml`) and it's retried automatically. Deleting
  the note also triggers a retry.
- **"cannot move … will retry"** in the log: Explorer or antivirus still had a
  file open. It retries with backoff; nothing to do.
- **Resolver window never appears.** `labwatch check` → "resolver window" line.
  Task Scheduler must run the task *interactively* (`/IT`, which install.ps1
  sets). If the task was set to "run whether user is logged on or not", the
  window can't show — re-run `install.ps1` or edit the task.
- **The PC went to sleep mid-run.** Settings → System → Power → Sleep: Never
  (when plugged in).
