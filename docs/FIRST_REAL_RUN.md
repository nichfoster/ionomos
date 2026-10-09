# First real runs: the checklist

Two DIA searches ran on the PC on 2026-09-23 (0.5.1 and 0.5.3), before
the runner was checked against FragPipe's headless tutorial and the source
of FragPipe 24.0 (D59). What they showed, and every real run since, is in
[REAL_RUNS.md](REAL_RUNS.md). No isoDTB or TMT search, no preflight, and no
search on 0.14.0 or later (the D59 runner) has been reported yet. The test
suite runs the runner against a fake that copies FragPipe's output. This
page is what to do on the lab PC, in order, for each first run.
It closes [ROADMAP.md](ROADMAP.md) Phase 2. Installing is
[DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md); what each method needs is
[WORKFLOWS.md](WORKFLOWS.md).

Work as the shared lab account. `ionomos-cli.exe` is in `C:\Ionomos`.

## 1. Before any data (15 min)

- [ ] **Update Ionomos** (bottom bar → **Update to x.y.z**).
- [ ] **Launcher**: tab 1 → **Find FragPipe** → **Save**. It must end in
      `bin\fragpipe.bat`. If `C:\FragPipe\FragPipe-24.0\bin\` has only
      `FragPipe-24.0.exe`, stop and note it: Ionomos will not run the
      `.exe` (it opens FragPipe's window and returns at once).
- [ ] **Open FragPipe's own window once** with this installation and check
      its Config tab shows MSFragger, IonQuant and (for DIA) DIA-NN and
      Python as found. A headless run reads those settings from
      `C:\FragPipe\FragPipe-24.0\cache`.
- [ ] **Workflow + FASTA per method**: tab 3 → **Import workflow…** → the
      `fragpipe.workflow` of a GUI run that worked. The FASTA must have
      decoys (about half its entries start with `rev_`).
- [ ] **Preflight**: tab 6 → **Check FragPipe install**, or

      ```powershell
      C:\Ionomos\ionomos-cli.exe preflight
      ```

      It starts FragPipe (no window, no search) for `--help` and a dry run
      of each method. Takes a few minutes. Wanted: no ✗.

      | Line | If it is ✗ or ! |
      |---|---|
      | FragPipe starts | The launcher is not `fragpipe.bat`, or Java was not found. The line says which. |
      | MSFragger / IonQuant / Thermo .raw reader | FragPipe window → Config → **Download / Update**. |
      | FragPipe settings | FragPipe's window was never used with this installation: open it once, or set *Tools folder* (tab 4). |
      | `<method>`: tools it runs | The workflow runs a tool that is not installed. |
      | `<method>`: FASTA | No decoys, or not about half. Database tab → **Add decoys**, once. |
      | `<method>`: FragPipe dry run | FragPipe itself refused: the line quotes its message and gives the log. If it complains about the placeholder file, run `preflight --raw C:\path\to\one.raw`. |
      | Room for long paths | A warning only: keep experiment and raw file names short. |

- [ ] **Keep the preflight's output** (copy the text, or the folder named on
      its last line under `C:\Fragpipe_Auto\logs\preflight\`). It is the
      first real evidence of how FragPipe answers.
- [ ] Disk: at least twice the raw folder's size free on C:. Sleep: Never.
- [ ] **Watcher running** (tab 5), startup task installed.

## 2. First search: isoDTB, small

- [ ] Tab 4 → *Run FragPipe automatically* **on** → **Save**. (Anything
      already queued starts too: tab 6 → **Pause searches** first if needed.)
- [ ] Drop a **small** isoDTB folder (one replicate, 2-3 fractions), named
      `YYYYMMDD_<initials>_isoDTB_<what>`, raws `<sample>_<rep>_<fraction>.raw`.
      `ionomos-cli.exe dry-run "C:\path\to\folder"` shows how it is read
      without moving anything.
- [ ] Accept the review window. Watch tab 6: the job's line should move
      through FragPipe's steps ("MSFragger (4 of 31 step(s) done)").

**Done looks like**: `DONE.txt` next to the raws; `fragpipe\` with
`combined_modified_peptide_label_quant.tsv` and a `log_<date>.txt`;
`results\report.html`; `results\<prefix>_sites.tsv`.

**Whatever happened, collect these** from the experiment folder (small text
files, no data):

- [ ] `ionomos_run\run_fingerprint.json`  ← the important one
- [ ] `ionomos_run\fragpipe_console.log`
- [ ] `FAILED.txt` if there is one

or press **Report a problem…**, which bundles them.

| What you see | What it means |
|---|---|
| Job stays "waiting: …" | Not started; the reason names what is missing. Fix it, the job starts by itself. |
| FAILED within seconds, reason starts "FragPipe exited with code 1" | FragPipe refused before any step. The reason quotes its ERROR line; the preflight's dry run should have shown the same. |
| FAILED, "FragPipe step X failed (exit code N); it said: …" | A tool stopped. The quoted lines are that tool's last words. |
| FAILED, "exited 0 but wrote nothing" | The launcher returned without running FragPipe. Check tab 1 says `fragpipe.bat`. |
| DONE with the note "no 'ALL JOBS DONE' line" | FragPipe's end marker was not in the console Ionomos captured. Send the fingerprint: this decides whether the marker can be required. |
| Pop-up "The SDRF doesn't describe these runs" | Since 0.14.0 FragPipe's own `fragpipe\sdrf.tsv` is skipped, so this should not appear for it. If it does, the real file differs from what was assumed: send a bundle. |
| The progress line never changes from "starting" | The console is empty or in a shape Ionomos doesn't know: send `fragpipe_console.log`. |
| FAILED, "ended from outside" or "stopped in the middle of a step without saying why" | Something ended FragPipe (Task Manager, sleep, sign-out, Windows out of memory). Note what happened on the PC at that time. |
| FAILED, "result table … not written to the end" or "has only its header line" | The table was cut off (disk full?) or the search found nothing. Send the fingerprint: its `outputs` lists the table sizes. |
| FAILED, "exited 0 without its 'ALL JOBS DONE' line and without any of its result tables" | FragPipe said it succeeded but left nothing. Check tab 1 says `fragpipe.bat`; send the console log. |
| Waiting, "raw file(s) can't be read yet" | A raw file is still open (Xcalibur still acquiring it, a copy still running). It starts by itself when the file is free; if it never does, note which program held it. |
| Odd letters (`Ã¼`, `�`) in `FAILED.txt` | FragPipe's tools wrote another code page than the one assumed (cp1252): send the console log. |

**Try once, on purpose** (D69): during a long search, end `Ionomos` (not
FragPipe) in Task Manager, then start the watcher again. The log should say
FragPipe "was still running after Ionomos stopped" and that it was stopped;
the search then runs again from the start (attempt 2), and Task Manager
shows only one FragPipe `java.exe`.

After fixing a cause: **Retry**. The earlier output is kept as
`fragpipe_previous_<time>\`, the earlier fingerprint as
`run_fingerprint_<time>.json`.

## 3. Then

- [ ] A full isoDTB experiment (3 × 7; 30-60 min in the GUI).
- [ ] **The Phase 2 exit test**: run the lab's R script
      `isoDTB_Fragpipe_merge-individual-peptides-to-Site.R` on the same
      `fragpipe\combined_modified_peptide_label_quant.tsv` (R is not on the
      PC: use the machine the script normally runs on; `sample_prefix` is
      FragPipe's experiment name, as in the table's column names). Compare
      its output with `results\<prefix>_sites.tsv`: same rows (Protein +
      residue + position), same PeptideCount and ExamplePeptides, ratios
      equal to 10 decimals, NA in the same cells. Not byte equality. Keep
      the R output for `ionomos/tests/golden/`.
- [ ] **DIA** (no lab SOP yet): pin the workflow the lab uses, run the
      preflight for it, drop a small folder (`<condition>_<rep>.raw`).
      FragPipe 24 writes DIA-NN's tables to `fragpipe\dia-quant-output\`.
      `.raw` works (2026-09-23): MSFragger reads it and FragPipe writes the
      `_uncalibrated.mzML` DIA-NN reads. The pinned `DIA.workflow` already
      ran twice ([REAL_RUNS.md](REAL_RUNS.md)); what is left is a search on
      0.14.0 or later, with its fingerprint.
- [ ] **TMT**: one plex per experiment, or several plexes each in a folder
      of its own (`<plex>\*.raw`; the folder name is the plex name in
      `experiment.yaml` `tmt: plexes:`). List all channels of the label type
      in the review window / `experiment.yaml` (`NA` for unused). Several
      plexes in one folder are searched, but FragPipe then names the
      channels `<plex>_<channel>` (the job says so).

## 4. Report back

Send, per search: `run_fingerprint.json`, and for a failure also
`fragpipe_console.log`. Plus the preflight's output. With those, these
open questions close:

- [x] `bin\fragpipe.bat` exists on the PC and starts with FragPipe's own Java
      (2026-09-23: Java 17.0.10, even before Ionomos set `JAVA_HOME`; its
      tools ran on `FragPipe-24.0\jre\bin\java.exe`).
- [ ] A headless run prints its console to Ionomos (yes, 2026-09-23) and
      ends in `ALL JOBS DONE` (not seen yet: the one console in a bundle was
      taken mid-search).
- [ ] The dry run works with a placeholder file.
- [ ] The step names and exit-code lines are as the parsers expect. Half
      seen: `Process '…' finished, exit code: 0` lines and the progress line
      ("MSFragger (3 step(s) done)") on 2026-09-23; no failed step yet.
- [ ] isoDTB `_sites.tsv` equals the R output.
- [x] Which DIA workflow, which DIA-NN, and whether `.raw` works for DIA:
      MSFragger → MSBooster → Percolator → spectral library → DIA-NN 2.3.2;
      `.raw` works.
- [ ] How the pop-ups felt; the LOW_SAMPLE threshold; imputation default.

## What is verified and what is not

Verified from FragPipe's source and documentation: the launcher and its Java,
every option Ionomos passes, the manifest format, how the TMT annotation is
found, the decoy rule, the exit codes, and the log lines Ionomos reads.
Verified by tests on the fake: the whole run loop (done, failed step,
time limit, cancel, stop, the process tree being killed, re-runs keeping
earlier output) and the faults in D69 (hangs, kills, Ionomos itself killed
and restarted, a full disk, cut-off tables, garbled and huge logs, locked
or vanished files).

Seen on the PC (2026-09-23, 0.5.1 and 0.5.3, [REAL_RUNS.md](REAL_RUNS.md)):
two DIA searches through `fragpipe.bat --headless`, the console reaching
Ionomos, FragPipe's Java and tool versions, the DIA output folder. Not
verified: the D59 runner (0.14.0 and later) against a running FragPipe, a
failed step, isoDTB, TMT. That is what this page is for.
