# Deploying Ionomos on the proteomics PC

The whole job on the PC, logged in as the shared lab account (the one that
runs FragPipe):

| I want to… | Do this |
|---|---|
| **install** | download `Ionomos-Setup-<version>.exe` from [Releases](https://github.com/nichfoster/ionomos/releases/latest) → run it → Next, Finish. Ionomos opens on its **✓ Setup** checklist → **Auto-setup** → work down the list. |
| **update** | nothing to download by hand: Ionomos checks GitHub when it opens and every 6 hours. When a new version is out, **Update to x.y.z** appears in the bottom bar → click → Yes. It downloads (checksum-verified), stops the watcher cleanly, installs, reopens and restarts the watcher. Settings and data are kept. *Run & Test → Check for updates* checks right now. No internet on the PC: [bring it on a USB stick](#updating-a-pc-with-no-internet). |
| **report a problem** | **Report a problem…** (bottom right, every tab) → a sentence → **Save the zip** → a zip lands on the Desktop, selected → copy it to where it should go (Dropbox, a chat). Names in it are replaced by pseudonyms; the `KEY…DO-NOT-SHARE` file next to it stays on the PC. Tick the result-tables box when the numbers are to be checked. Details: [DEV_LOOP.md](DEV_LOOP.md#bundles-the-labs-files-on-the-developers-side-d63). |
| **uninstall** | Settings → Apps → **Ionomos** → Uninstall. Removes the program, startup task and shortcuts. Keeps `config.yaml`, the job list, logs and every experiment folder. |
| **start over completely** | `deploy\clean_slate.ps1` (also removes LabWatch leftovers; still keeps data). |

> **Upgrading from LabWatch (≤ 0.4.0)?** Just run the Ionomos Setup. It
> retires LabWatch (stops it, removes its `LabWatch.exe`, startup task and
> shortcut), and Ionomos picks up the same `config.yaml`, job list and
> experiment folders (it reads the old `labwatch.json` / `labwatch_run\`
> files and renames only its own status file on first use). Afterwards: ✓
> Setup → **Install** the startup task once.

> **Still prototyping?** The *development install* runs straight from the
> GitHub repository — [DEV_LOOP.md](DEV_LOOP.md). The installer is for normal use.

No internet is needed on the PC except to download the Setup file, and not
even for that: a USB stick does it ([Updating a PC with no internet](#updating-a-pc-with-no-internet)).

---

## Way A — the installer (recommended)

### A1. Get the installer (1 min)

[Releases → latest](https://github.com/nichfoster/ionomos/releases/latest) →
**Ionomos-Setup-<version>.exe** (public; no sign-in needed). Only the very first
install is manual — after that Ionomos updates itself (see the table above).
GitHub builds and tests it on a Windows machine for every version tag
(`git tag v0.5.0 && git push --tags`); a portable `Ionomos-<version>-windows.zip`
sits next to it for PCs where installers aren't allowed.

To build by hand instead (Windows, Python 3.11+ incl. tcl/tk, Inno Setup 6):
`powershell -ExecutionPolicy Bypass -File deploy\build_exe.ps1` → `dist\Ionomos-Setup-<version>.exe`.
Without Inno Setup the script installs it with winget or choco; add `-NoInstaller` to stop after the portable
zip instead (about a minute once the build venv exists). Making a release: [DEV_LOOP.md](DEV_LOOP.md), "Releasing".

### A2. Install (2 min)

1. Run `Ionomos-Setup-<version>.exe` → Next → Finish. It installs to
   **`C:\Ionomos`** (no admin rights needed; keep the path free of spaces), adds
   Start-menu and Desktop shortcuts, and opens Ionomos. SmartScreen may say
   "unrecognised app": *More info → Run anyway*.
2. Ionomos opens on the **✓ Setup** checklist. Press **Auto-setup** (standard
   folders `C:\Fragpipe_Auto` + `C:\Fragpipe_General`, creates them, finds
   FragPipe, saves), then work down the list — every open item has a button or
   says which tab to use. Headless alternative: `C:\Ionomos\ionomos-cli.exe init`.

The program lives in `C:\Ionomos`; your settings and data live in
`C:\Fragpipe_Auto` / `C:\Fragpipe_General`, so updating or uninstalling the
program never touches them.

### A3. Follow the tabs (10 min)

The quickest way is the **Setup** tab: its **Check everything** list says what
is missing, each item with a button, and **Auto-setup** does the standard
layout in one click. By hand, tab by tab:

| Tab | What to do |
|---|---|
| **Folders** | Check the paths in *Quick fill* (`C:/Fragpipe_Auto` and `C:/Fragpipe_General`) → **Apply layout** → **Create all missing folders**. Then set **Inbox** to the shared drop folder if it's somewhere else (Browse…), and **FragPipe launcher** to the exe under `C:\FragPipe\FragPipe-24.0\` (needed only for searches — fine to leave for later). Green dots = exists. |
| **Users** | Type each lab member's name → **Add** (creates `C:\Fragpipe_General\<Name>`). Select a user, type their initials (`IJ, IJD`) → **Apply aliases**. |
| **Methods** | For each method pick the **Workflow file** and **FASTA file** (dropdowns list what's in the folders — put files there first, see A4). |
| **Advanced** | Usually nothing. Threads/RAM default to 28 / 48 GB for this PC. |
| **Run & Test** | **Save & Check** (bottom bar) → all ✓ (a `!` on the FragPipe launcher / a method means those jobs will *wait* until it's fixed) → **Install startup task** → **Start watcher** (both on the *Watcher* page). Optional: **Desktop shortcut**. |

Save writes `C:\Fragpipe_Auto\config.yaml`. Reopen Ionomos (Start menu or
Desktop) any time to change anything; it remembers where the config is.

### A4. Workflow + FASTA files (5 min, can be later)

For each method, in the **FragPipe GUI**:

1. Load the workflow the lab uses (or open a recent successful run's `fragpipe.workflow`).
2. **Database tab → set the FASTA (with decoys).** Saved without one, headless runs fail with "FASTA file path is empty".
3. Check quant settings (isoDTB: MBR on).
4. **Workflow tab → Save to custom folder** → `C:\Fragpipe_Auto\workflows\isoDTB.workflow`.
5. Copy the FASTA file into `C:\Fragpipe_Auto\fasta\`.

Back in the app: **Methods** → select the method → pick the files → **Apply changes** → **Save**.

### A4b. First real search (15–60 min, once)

0. Easiest way to get the workflow: **Methods** → select the method → **Import workflow…** → pick the `fragpipe.workflow` inside a recent run that worked. Its FASTA is copied in too.
1. **Folders** → **Find FragPipe** (fills `C:\FragPipe\FragPipe-24.0\bin\fragpipe.bat`) → **Save**. **Jobs** → **Check FragPipe install** (or `ionomos-cli.exe preflight`): it starts FragPipe, without a window or a search, for its version and a dry run of each method, and lists what would stop a search. The full checklist for the first real run is [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md).
2. **Save & Check** (bottom bar): `FragPipe launcher` ✓ and `methods.isoDTB` ✓ (workflow + FASTA).
3. Drop a *small* real isoDTB folder (e.g. one replicate, 2–3 fractions).
4. Watch **Run & Test** (or the Jobs tab): *FragPipe: RUNNING job N* → *done*. In the experiment folder:
   `DONE.txt`, `fragpipe\` with the results, `ionomos_run\fragpipe_console.log`.
5. If it says FAILED: open `FAILED.txt` / the console log, fix, then select the job on the
   **Jobs** tab → **Retry**. Paste **Copy diagnostics** + the console log to Claude if it's unclear.

Advanced → *Run FragPipe automatically* off = experiments are only filed (the
Phase 1 behaviour).

### A5. Try it (3 min)

**Run & Test** → **Testing** → **Create testbed** → **Start TESTBED watcher** → pick
`iso_good` → **Drop**. Watch the output pane: "detected … stable … queued job 1
… FragPipe … done" (the testbed uses a fake FragPipe that takes a few seconds).
`fp_fail` shows a failed search and **Retry** on the Jobs tab.
Try `gui_unknown_user` → the resolver window pops up. **Stop watcher** when done.

Then a real one: make a folder `20260916_<yourinitials>_isoDTB_test` with two
tiny files `test_1_1.raw`, `test_1_2.raw` (any content), drag it into the inbox.
Within ~70 s it moves to `C:\Fragpipe_General\<you>\` with a `ionomos.json`.

### A6. Tell the lab

The one-paragraph version of [NAMING_CONVENTION.md](NAMING_CONVENTION.md):

> Put **your initials** and **isoDTB / TMT / DIA** somewhere in the folder
> name. Raw files end in `_rep_fraction` (isoDTB), `_F#` (TMT) or
> `_condition_rep` (DIA). Drag the folder into `C:\Fragpipe_Auto\inbox`
> (just the .raw files works too — Ionomos makes the folder). If
> Ionomos can't work it out, a window will ask you.

---

## Way B — script install (no exe)

Use if you'd rather not build the exe. Same result, but the app is started
with a command instead of a double-click.

1. On the Mac: `deploy/make_release.sh` → `dist/ionomos-<version>-windows.zip` (wheel + offline deps + scripts).
2. On the PC: unzip to the Desktop, then in PowerShell:

```powershell
cd $HOME\Desktop\ionomos-<version>-windows
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

   This creates the folders, a venv in `C:\Fragpipe_Auto\venv`, a config, and the startup task.
3. Open the app: `C:\Fragpipe_Auto\venv\Scripts\ionomos.exe setup` — then follow A3–A6.

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
| change any setting | open `Ionomos.exe`, edit, **Save** (restart the watcher for timing changes) |
| see the queue | **Run & Test** → **Show queue**, or `ionomos-cli.exe status` |
| see the log | **Run & Test** → tick *follow the watcher log*, or `C:\Fragpipe_Auto\logs\ionomos.log` |
| report a problem | **Report a problem…** (bottom right) → zip on the Desktop → send it, keep the `KEY` file (or `ionomos-cli.exe bundle`; `bundle 12 --level validate` adds job 12's result tables). Nothing is uploaded. Tick *detailed logging* for problems that come and go. |
| see / retry / cancel jobs | the **Jobs** tab (double-click = open the report; failed jobs show the likely cause) |
| something needs you | a window pops up by itself (analysis decision, failed search, rejected folder) with the likely cause and the fix; the bottom bar shows **⚠ N need attention** until it's dealt with |
| look at results | `results\report.html` in the experiment folder (interactive volcano, protein details, heatmap, enrichment, QC); **Jobs** → **Open report** |
| change comparisons / thresholds / sample groups | one experiment: **Analysis → Analyse an experiment** (pick it, edit, **Run analysis**); lab-wide: **Analysis → Lab defaults**, then **Save** |
| analyse an old FragPipe run | **Analysis** → **Analyse a folder…** or `ionomos-cli.exe analyze <folder>` |
| get a message when a search is done / failed / waiting | off by default. The app's **Notifications** tab (Teams, Slack, a webhook or email; what is sent: ARCHITECTURE.md "Notifications"): fill in, **Send test**, **Save**, restart the watcher. Or edit `notify:` in `C:\Fragpipe_Auto\config.yaml` and run `ionomos-cli.exe notify-test` |
| keep the PC free for a while | **Jobs** → **Pause searches** (the running search finishes; nothing new starts) |
| check FragPipe is installed right | **Jobs** → **Check FragPipe install**, or `ionomos-cli.exe preflight` (launcher, Java, MSFragger / IonQuant / DIA-NN / Python, decoys in each FASTA, and a FragPipe dry run of each method; a few minutes) |
| stop / start the watcher | **Run & Test** → *Watcher* buttons; the startup task restarts it at next logon |
| add a user | **Users** → Add; or just create the folder under `C:\Fragpipe_General` |
| see what a folder would do | `ionomos-cli.exe dry-run "C:\path\to\folder"` |
| check how names are read (after changing `naming:`) | **Methods** → **Test names…**, or `ionomos-cli.exe names test <folder name> <file.raw> …` |
| upgrade | **Update to …** in the bottom bar, or **Run & Test** → **Check for updates** → **Update now**; config/data untouched. Offline: a USB stick, [below](#updating-a-pc-with-no-internet) |
| uninstall | Settings → Apps → Ionomos → Uninstall (data stays). Full reset: `deploy\clean_slate.ps1`. |

## Updating a PC with no internet

The new version can come on a USB stick. Ionomos checks it and installs it the
same way as a download (D130).

**On a computer with internet**

1. Open the release on GitHub: [Releases → latest](https://github.com/nichfoster/ionomos/releases/latest)
   (or the version you want).
2. Download **both** files: `Ionomos-Setup-<version>.exe` and
   `Ionomos-Setup-<version>.exe.sha256` (a one-line text file: the installer's
   SHA-256, which Ionomos checks the installer against).
3. Copy both onto a USB stick, at its top level or into a folder called
   `Ionomos` on it. Keep their names (a Windows ` (1)` in a name is fine).

**On the PC**

1. Plug the stick in.
2. **Run & Test** → **Check for updates**. The Updates box says *Ready on the
   USB stick F:\\: Ionomos-Setup-x.y.z.exe* and **Update to x.y.z**
   appears in the bottom bar → click → Yes. Or **Update from a file…** (next to
   Check for updates) → pick the installer on the stick.
3. Ionomos checks the installer against the `.sha256` beside it, copies it into
   Downloads and checks the copy, then stops the watcher, installs, reopens and
   starts the watcher again, as an online update does. Leave the stick in until
   Ionomos closes for the install; the installer runs from the copy in
   Downloads, so the stick can come out after that.

From a terminal: `C:\Ionomos\ionomos-cli.exe update --from F:\Ionomos-Setup-<version>.exe`
does the same; `--check` only checks.

**What the check means.** The `.sha256` file catches a copy that went wrong: a
bad stick, a copy cut short, a download that didn't finish, two files from
different releases. It does not prove the installer is the real one: whoever
can change the installer on the stick can change the `.sha256` beside it. So
download both files yourself, from the release page, on a computer you trust.
When the PC can reach GitHub, the SHA-256 GitHub publishes is used instead, and
a `.sha256` that disagrees with it doesn't count.

**If it doesn't work**

- *"has nothing to be checked against … no Ionomos-Setup-x.y.z.exe.sha256
  beside it"*: copy the `.sha256` file too, into the same folder as the
  installer, and press **Check for updates** again.
- *"failed its checksum"*: copy both files from the release page again.
- **The stick isn't found.** Some USB disks and sticks say they are a fixed
  disk (the PC's own `D:` does), so Ionomos doesn't look there by itself: use
  **Update from a file…**.
- **The first time.** A PC on 0.20.0 or older doesn't know this way yet, and
  those releases have no `.sha256` file: copy the installer onto the PC (the
  Desktop) and run it yourself. It stops the watcher itself and starts it
  again afterwards; data and settings are kept.
- The copy in Downloads (and its `.sha256`) stays there after the update;
  delete it whenever you like. Ionomos never copies over a file: a different
  file of the same name in Downloads is left as it is, and the copy is called
  `Ionomos-Setup-<version>(1).exe`.

## Troubleshooting

- **Nothing happens after a drop.** Is the watcher running (the Run & Test tab's status line)?
  The folder needs ≥1 `.raw` and must be unchanged for 60 s.
- **`<name>.REJECTED.txt` in the inbox.** Open it — it says why. Fix the folder
  (or add an `experiment.yaml`); it's retried automatically. Deleting the note also retries.
- **Job says "waiting: …"** (the Jobs tab or `status`). A setup file is missing — the
  FragPipe launcher, or that method's workflow/FASTA. Fix it; the job starts by itself.
- **Watcher says NOT RESPONDING** (Run & Test). Stop watcher → Start watcher. Send the diagnostics bundle.
- **"another ionomos watcher is already running"**: the startup task already runs one — that's fine; use the app to see it.
- **Job list damaged** (check says ledger ✗): the watcher rebuilds it automatically at start; or `ionomos-cli.exe repair-ledger`.
- **Job FAILED.** The Jobs tab shows the *most likely cause* in plain English. `FAILED.txt` in the experiment folder has the reason
  (the FragPipe step that failed and its last lines); `ionomos_run\fragpipe_console.log` has FragPipe's full output and
  `ionomos_run\run_fingerprint.json` a short record of the run to send along. Fix, then
  select the job on the Jobs tab → **Retry** (old output is kept as `fragpipe_previous_<time>\`).
- **"Failed to load Python DLL … \_MEI…\python314.dll"** after installing or
  updating: click OK. Ionomos is installed and works; open it from the Start
  menu. The installer's last step started the new Ionomos.exe with the old
  app's settings. Fixed in 0.18.1 (D81): installers from then on never show it,
  including when an older Ionomos starts the update.
- **Ionomos closed by itself** (no message): look at `Ionomos-fault.log` next
  to `Ionomos.exe`. It holds the stack of every thread at the crash, and
  **Report a problem…** includes it.
- **Stopping/updating Ionomos during a search** kills that FragPipe run; the job
  re-runs from the start when the watcher starts again.
- **"cannot move … will retry"** in the log: Explorer/antivirus still had a
  file open. It retries with backoff.
- **Resolver window never appears.** **Save & Check** (bottom bar) → the "resolver window" line must be ✓.
  The startup task must be *interactive* (the app installs it that way). If
  someone changed it to "run whether user is logged on or not", reinstall it from Run & Test.
- **PC sleeps mid-run.** Settings → System → Power → Sleep: Never (plugged in).
- **"… is not UTF-8 (byte 0x97 on line 3); read it as Windows-1252"** for
  `config.yaml` or an `experiment.yaml`. The file was saved in Notepad as
  "ANSI" (or by a build before 0.1.1). Ionomos reads it anyway (D86); re-save
  it as UTF-8 (Notepad → Save as → Encoding: UTF-8) to make the note go away.
  Saving from the app's settings writes UTF-8 too. If it says "not
  Windows-1252 either", the file stops Ionomos until it is re-saved that way.
  Versions up to 0.18.1 stopped with a bare `UnicodeDecodeError … byte 0x97`
  instead.
- **"contains a space" on Check.** Every path in the config must be space-free
  (FragPipe). Don't put the testbed or the install under `C:\Users\<First Last>\…`;
  use `C:\ionomos-testbed` / `C:\Fragpipe_Auto`.
- **Double-clicking `Ionomos.exe` flashes a console window and closes.** Look
  for `Ionomos-crash.txt` next to the exe — the full error is in there (the
  app also shows it in a dialog). Builds before 0.1.2 had a bug where the
  console exe overwrote the windowed one (Windows ignores the case of
  `Ionomos.exe` vs `ionomos.exe`); download a current build from Releases.
- **Start over completely.** `deploy\clean_slate.ps1` removes the program
  (task, exes, venvs, shortcut, remembered config path) and keeps all data and
  `config.yaml`; `-Config` also removes the config and ledger.
- **`Ionomos.exe` flagged by antivirus / SmartScreen.** Unsigned PyInstaller
  exes sometimes are. "More info → Run anyway", or use Way B.
