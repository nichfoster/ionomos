# Proteomics PC — facts from the 2026-09-15 inventory

Source: [`reference/pc-inventory/2026-09-15/`](../reference/pc-inventory/2026-09-15/)
(`system_report.txt`, `software_path_candidates.csv`), collected 15:24 on
2026-09-15 with the script now at `tools/inventory/collect_pc_inventory.ps1`.

## Machine

| | |
|---|---|
| Hostname | `DESKTOP-NI5V92B` |
| Account | `DESKTOP-NI5V92B\Daniel Nomura` (local; WORKGROUP, not domain-joined) |
| OS | Windows 11 Pro for Workstations, build 10.0.26200 |
| CPU | Xeon Silver 4216 — 16 cores / 32 threads @ 2.1 GHz |
| RAM | 64 GB |
| PowerShell | 5.1 (no pwsh 7) |
| C: | 953 GB NVMe (RAID), **99 GB free** — OS + FragPipe + `C:\Fragpipe_General` |
| D: | 18.6 TB WD Elements **USB HDD**, 14.5 TB free — per-user archive (`D:\<Name>\`) |
| E: | 30 GB USB flash, "Proteome Discoverer 2.5" installer media |
| Network drives | none mapped at inventory time. `C:\Proteomics_File_Sharing` (the Eclipse share) does not exist yet / wasn't verifiable |

## Software

| Software | Version | Location / notes |
|---|---|---|
| FragPipe | **24.0** | `C:\FragPipe\FragPipe-24.0\` — `javaw.exe` running from `…\jre\bin\`, so it has a bundled JRE. Launcher: the first Ionomos report (2026-09-23) saw `bin\FragPipe-24.0.exe`; FragPipe's build also puts the headless launcher `bin\fragpipe.bat` there (D59). **Confirmed 2026-09-23**: the setup check found `bin\fragpipe.bat` and two DIA searches ran through it ([REAL_RUNS.md](REAL_RUNS.md)) |
| FragPipe | 23.1 | `C:\FragPipe\FragPipe-23.1\` |
| FragPipe | 22.0 | unzipped in Downloads (`FragPipe-jre-22.0\`), has stock `workflows\isoDTB-ABPP.workflow`, MSFragger 4.1, IonQuant 1.10.27 |
| DIA-NN | 2.3.2 Academia | installed (MSI); path not captured. FragPipe 24.0 also has its own copy, `tools\diann\2.3.2\DiaNN.exe`, and used it with `config_diann` empty (2026-09-23) |
| Proteome Discoverer | 2.5.0.400 | `C:\Program Files\Thermo\Proteome Discoverer 2.5` |
| Python | 3.14.5 (user) | `C:\Users\Daniel Nomura\AppData\Local\Python\pythoncore-3.14-64` |
| Python | 3.9.13 | system-wide; `py` launcher at `C:\Windows\py.exe` |
| `python` on PATH | — | resolves to the **WindowsApps store stub** (`0.0.0.0`). Do not rely on `python`; use `py -3.14` or the absolute path |
| Java on PATH | — | **not found**. `fragpipe.bat` needs `JAVA_HOME` or `java` on PATH, so Ionomos sets `JAVA_HOME` to FragPipe's own `jre` (D59). Yet on 2026-09-23 (0.5.1, before that) `fragpipe.bat` started FragPipe on Java 17.0.10 by itself; how it found Java is not known |
| R / Rscript | — | **not installed** → the lab's R scripts are currently run… somewhere else? Or R was removed. Either way: port to Python |
| git | — | not installed. Deploy by copying / `pip install` from a wheel, or install git |
| .NET | 10.0, 6.0.20 | 10.0 from the inventory. FragPipe's console printed ".NET Core Info: 6.0.20" (2026-09-23), and its Thermo reader read the `.raw` files |

Seen in the support bundles of 2026-09-23 ([REAL_RUNS.md](REAL_RUNS.md)):
the startup task was missing in all three (the watcher was running, so it
was started from the app or by hand); `C:` had 75 GB free at 14:23 and 55 GB at 16:45, after one DIA search
of six raws and a second one under way.

## How the lab currently works (inferred)

- Working area appears to be `C:\Fragpipe_General\<user>\<experiment>\` (both
  R scripts and the "representative experiment" answers point there). **Not
  scanned** by the inventory — needs the fixed script.
- `D:\<user>\` holds completed experiments with raws, mzML/mzBIN conversions,
  and FragPipe output — ~15 user folders: Aman, Carolyn, Chris, CMZ, EJQ,
  Isaac, Kosuke, Melissa, Melody, Phillip, Qian, RUI, Sheena, Taylor_Elements,
  Thang, Yun, Zoe.
- Folder naming is ad hoc (see NAMING_CONVENTION.md). Raw naming is consistent:
  `<prefix>_<rep>_<fraction>.raw`.
- FragPipe outputs seen: per-replicate subfolders (`EJQ_2_027_1\`) with
  `*_ion_label_quant.tsv`, `*_modified_peptide_label_quant.tsv`,
  `*_protein_label_quant.tsv`, `interact-*.pep.xml`; top-level
  `combined_*.tsv`, `fragpipe.workflow`, `fragpipe.job`,
  `fragpipe-files.fp-manifest`, `filelist_ionquant.txt`, `modmasses_ionquant.txt`.
- People re-run: `Fragpipe-DIANN_RERUN`, `Redo\`, `Again (see this one)\`
  folders exist. The watcher's "never overwrite" rule matters.
- Lab SOP docs and R scripts live on the Desktop of the shared account.
- The user-entered "planned watcher folder" was `C:\Fragipe_Auto` (sic —
  typo). Proposal: `C:\Fragpipe_Auto`.

## No internet on the PC

Since 2026-10-26 the PC has no internet connection, possibly for weeks (D131). Ionomos does its work on the
PC, so a whole day without internet goes as usual. Every request Ionomos makes has a time limit, none runs on
the app's window thread or in the watcher's inbox loop, each says once what didn't happen and what to do, and
each works again by itself when the connection is back. On a PC with no network at all the OS refuses at once;
on a network that drops packets each request waits out its own time limit (below), once.

**Works as before**: taking in drops; the searches (FragPipe, DIA-NN, MaxQuant and Sage are programs on the
PC); the analysis and `report.html` (one offline file; its UniProt, STRING and GeneCards links need the internet
only when clicked); instrument QC; the app and its help (a page on the PC); the assistant (a model on this PC,
`localhost` only: with no model running it says so at once and shows Ionomos's own text); **Add decoys and
contaminants…** (FragPipe's Philosopher has its list built in; without it, the `contam_` entries of a FASTA
already on the PC); **Report a problem…** (the zip is saved on the Desktop; it goes to the maintainer by USB
stick); the atlas hand-off and the drive sweep, which write into the Dropbox folder on this PC. The Setup
checklist's atlas line and `ionomos atlas check` look only at that folder, so Dropbox being offline is not an
error for them.

FragPipe: Ionomos never asks FragPipe to download anything, and FragPipe's tools are on disk. What FragPipe
24.0 itself does without internet has not been seen on the PC. `ionomos preflight` (the app's **Check FragPipe
install**) starts FragPipe as a search does, with a time limit (90 s for `--help`, 300 s per method's dry run),
so a FragPipe that wanted the network would show there. Its fix for a missing tool ("FragPipe GUI → Config tab
→ Download / Update") needs the internet; offline, copy the tool from another FragPipe installation.

**Waits, and goes out by itself later**

| What | Without internet | When it is back | In the app |
|---|---|---|---|
| Notifications (`notify:`) | each message is tried once per channel. One that never reached the service (no connection, no name lookup, refused, or "try later": HTTP 429 / 5xx, SMTP 4xx) is kept in `<log_dir>\notify_state.json`: the newest about each job, at most 50, for 7 days. One line in the log per channel and error. A message the service refused (HTTP 404, a wrong password) is not kept | the watcher tries again after 5 minutes, then 10, 20 and every 30 minutes, and at once when another message gets through or the watcher starts. Each goes as written, plus "Sent late: this happened …" | one item, **Notifications are waiting for a connection** (a warning: no pop-up), in the needs-attention list; it closes itself when they have gone. Dismissed, it stays away until they have |
| The Dropbox side of the atlas hand-off (and the drive sweep) | Ionomos writes into the Dropbox folder on this PC as usual: the job's hand-off is `sent` | Dropbox uploads the files | nothing to see: the hand-off worked |

Dropbox can't upload while offline, so what the hand-off and the sweep copy stays on `C:`. The sweep stops below
`atlas.sweep_min_free_gb` (40 GB) free and a job's hand-off below `fragpipe.min_free_gb`, so the disk is not
filled; over weeks, keep an eye on `C:`'s free space (the Setup checklist and `ionomos check` show it). Not seen
on the PC: how Dropbox's online-only files behave offline. A hand-off that can't read a file already in the
Dropbox folder fails and is tried again like any other (`atlas_handoff_failed`, up to `atlas.max_tries`).

**Does not work until the internet is back**

| What | What it says | What to do |
|---|---|---|
| **Download from UniProt…** (Methods tab), `ionomos fasta search` / `download` | "could not reach rest.uniprot.org (…); check this computer's internet connection, or download the file on another computer and use `ionomos fasta prepare` on it" (15 s for a search, 60 s per read of a download) | use a FASTA already on the PC, or copy one over on a USB stick, then **Add decoys and contaminants…** ([FASTA.md](FASTA.md)) |
| UniProt's current release, in the Methods tab's **Check for updates** and `ionomos fasta release` | "UniProt's current release: unknown (…)"; the rest of the check (the files on this PC) is done (10 s) | nothing; ask again when online |
| FragPipe's stock workflows from GitHub (`ionomos fasta stock-workflows [--get NAME]`) | "could not reach api.github.com …" (15 s) | copy the workflow from a FragPipe installation's `workflows\` folder |
| `ionomos example` (Zenodo) | the URL of the zip and `ionomos example --zip` (60 s per read) | download the zip elsewhere, then `ionomos example --zip <file>` ([EXAMPLE.md](EXAMPLE.md)) |
| Gene-set libraries the PC has never downloaded (enrichment) | the report's **Enrichment was incomplete**: "… not on this PC yet and could not be downloaded from maayanlab.cloud (…)"; one try per analysis (30 s), not one per library. Libraries downloaded before work offline | **Re-run analysis** once the PC is online |
| Update checks | the app's Updates line: "Couldn't check GitHub just now (…)"; a development install: "could not check: …" | [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md), "Updating a PC with no internet" (a USB stick) |

`IONOMOS_OFFLINE=1` (an environment variable, set for the account the watcher runs as) makes the downloads and
the update checks above say "off" without trying. It is not needed: without it they fail at once on a PC with no
network. It does not hold back notifications, which wait as above.

## Known defects in the 2026-09-15 inventory

1. **All `PATH:` sections report `DOES NOT EXIST YET`** because the paths were
   entered with surrounding double-quotes and the script passed them verbatim
   to `Test-Path -LiteralPath`. Evidence: `C:\FragPipe\FragPipe-24.0` "doesn't
   exist" yet `javaw.exe` is running from inside it. The `NETWORK PATH
   CONNECTIVITY` table shows the quotes. Fixed in `tools/inventory/`.
2. Because of (1), **no experiment manifests or per-folder file-type summaries
   were produced**, and the FragPipe install tree was not listed.
3. `software_path_candidates.csv` only matched names containing
   `FragPipe|MSFragger|IonQuant|Philosopher|isoDTB|DIA-NN|Spectronaut`, so it is
   biased toward isoDTB experiments; TMT/DIA/other folders are under-represented.
4. Search roots did not include `C:\Fragpipe_General` or `C:\FragPipe`.

## Still needed from the PC (re-run the fixed inventory script)

- [ ] Listing of `C:\FragPipe\FragPipe-24.0\` — launcher name, `workflows\`, `tools\`
- [ ] Listing of `C:\Fragpipe_General\` (users and one full experiment tree)
- [ ] Does `C:\Proteomics_File_Sharing` exist; is it a local folder shared out
      over SMB, or a mapped drive from the Eclipse PC? Which direction is the share?
- [ ] DIA-NN 2.3.2 install path
- [x] Where FASTA databases live (probably inside a `fragpipe.workflow` as
      `database.db-path=…`; grep an existing one) → Ionomos' searches use
      `C:\Fragpipe_Auto\fasta\2026-01-30-decoys-contam-UP000005640_9606.fasta.fas`
      (human, 20,698 targets, `rev_` decoys, FragPipe's contaminants), 2026-09-23
- [ ] Whether the account has an unattended-login / Task Scheduler policy
      (the watcher must survive logout/reboot)
- [ ] Power settings: does the PC sleep? (Would kill long runs.)
