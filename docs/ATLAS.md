# The hand-off to the lab's cysteine atlas

The lab keeps a **cysteine atlas**: one database of every cysteine site the lab's isoDTB experiments have
seen (`%USERPROFILE%\nomura-data\atlas\atlas.duckdb`, built by the Nomura-Lab repository's `Lab Informatics\tools`).
FragPipe isoDTB runs reach it through the lab Dropbox: `harvest_fragpipe.ps1` copies a run's result files into
`Nicholas Foster Data\fragpipe-runs`, `ingest_fragpipe.py` turns them into evidence rows, and
`build_site_atlas.py` rebuilds the atlas. Until 2026-10-19 a person ran that by hand.

Ionomos runs the lab's FragPipe searches, so it is the natural sender: when an isoDTB search is done, it copies the
run into the same Dropbox folder, the same way, and says so with a small record the atlas side reads. The atlas
publishes a site lookup back, and the isoDTB report shows it beside each site. Both sides implement one contract,
below, word for word; the Nomura-Lab side is built in that repository.

Since 2026-10-21 (D127, contract `/2`) the hand-off also carries what a model of cysteine engagement trained on the
lab's competition runs needs, and what a decoy check of FragPipe's results needs: TMT searches (one switch; the atlas
stores them and counts nothing from them), each run's sample design (`experiment.yaml`, the annotation files) and
its decoys (each replicate's `.pin`, gzip-compressed). **Not on the atlas side yet** (checked 2026-10-26 on Nomura-Lab
main): `atlas_intake.py` still reads only `/1` and `harvest_fragpipe.ps1` still takes no `.pin` or design file; the
receiver for `/2` (and item 7) is being written in that repository. Until it is, `/2` records wait in Dropbox,
uncounted.

Since 2026-10-26 (D128) Ionomos also does what a person did with `harvest_fragpipe.ps1`: the **drive sweep** searches
the PC's drives once a day for the FragPipe runs Ionomos did not search (the years of runs on `D:\<user>\` and in
`C:\Fragpipe_General`) and hands each isoDTB run, and each TMT run with the TMT switch on, to the atlas the same way,
decoys and sample design included ([The drive sweep](#the-drive-sweep), contract item 7).

Decisions: D120 (the contract and the settings), D121 (when a job is handed off), D122 (how files are copied),
D123 (the command line), D124 (atlas context in the report), D125 (the app), D127 (TMT, the sample design and the
decoys: contract `/2`), D128 (the drive sweep). Code: `ionomos/src/ionomos/atlas.py`,
`ionomos/src/ionomos/atlassweep.py`, `ionomos/src/ionomos/downstream/atlasctx.py`.

## The contract: "ionomos-atlas-handoff/2"

Both sides implement exactly this.

1. **Destination.** `<dest>` is configured as `atlas.handoff_dir`. It is normally the lab Dropbox folder `...\Nicholas Foster Data\fragpipe-runs`, the same folder `harvest_fragpipe.ps1` copies into.
   - There is no default, because that path has spaces and CLAUDE.md forbids default paths with spaces.
   - The app may offer a detected Dropbox folder as a suggestion the person confirms.
   - The feature is off unless `atlas.enabled: true` is set.
2. **What is copied.** For a done job whose method is in `atlas.methods` (default `[isoDTB]`; FragPipe isoDTB or TMT methods), copy the FragPipe run folder's result files using the SAME selection rule as `harvest_fragpipe.ps1`. Read the script and `Lab Informatics/tools/HARVEST-README.md` "What it takes and what it leaves" in the Nomura-Lab repository, read-only.
   - From the top folder: `*.workflow`, `*.fp-manifest`, `*.job`, `*.tsv`, `*.txt`, `*.csv`, `*.log`, `*.fas`, `*.params`, `*.json`, `*.xml`.
   - From each replicate subfolder: `psm.tsv`, `peptide.tsv`, `protein.tsv` and logs.
   - **The decoys** (`/2`): every `*.pin` of the top folder and of each replicate subfolder, gzip-compressed to `<name>.pin.gz`, if it is no larger than `atlas.pin_max_mb` (default 100 MB, before compression; 0 = none). When `<x>_edited.pin` (MSBooster's) is beside `<x>.pin`, only the `_edited` one.
   - **The sample design** (`/2`), from beside the run folder: the experiment folder's `experiment.yaml` and `results\fragpipe-analyst\experiment_annotation.tsv`, and every file whose name ends in `annotation.txt` in each folder holding the manifest's raw files (where FragPipe reads a TMT annotation). Ionomos's experiment folder is the job's; harvest takes the run folder's parent when it holds `experiment.yaml` or Ionomos's status file (`ionomos.json`, or the LabWatch-era `labwatch.json`): `experiment.yaml` is there only once someone saved an analysis choice.
   - Never raw data, `.pepXML`, `.prot.xml`, speclibs, or any file over 500 MB.
3. **Layout and copying.**
   - Files go to `<dest>\<DRIVE LETTER>\<original path without the drive>\`, exactly as harvest's `Get-RelativeDestination`, so `ingest_fragpipe.local_run_dir` finds them.
   - Every copy is verified by SHA-256.
   - An existing file with the same size and hash is skipped.
   - A file that exists with different content is a conflict: nothing is overwritten, the hand-off stops, and an attention item is raised.
   - A `.pin.gz` is compared by what it holds: one at the destination whose decompressed content has the `.pin`'s SHA-256 is there already, whoever compressed it (harvest's .NET and Ionomos' Python give different bytes). A compressed copy is verified by decompressing it.
   - A sample-design file goes to its own path by the same layout (`<dest>\C\...\<experiment>\experiment.yaml`). When a different file is there (the design was edited since an earlier send), it goes beside it as `<name>.<first 12 hex of its SHA-256>.<ext>`; the earlier copy stays. Only when that name too holds other content is it a conflict.
4. **Completion record.** Only after every file is verified:
   - Write `<run dest>\ionomos-handoff.json`: contract `ionomos-atlas-handoff/2`; `probe_class` (`isoDTB` or `TMT`); every run-folder file with its relative path, size and SHA-256 (a `.pin.gz` also with `gzip_of`: the `.pin`'s path, size and SHA-256); `design`, each sample-design file with its `kind`, its `source` path on the PC, its `path` relative to `<dest>`, size and SHA-256; `left`, the `.pin` files and the files over a cap that stayed behind, with why; and the job's summary (job number, user, experiment, method, FragPipe version, workflow, FASTA, raw files, replicate plan, Ionomos version).
   - Then write `<dest>\_harvest-logs\runs-ionomos-<YYYYMMDD-HHMMSS>-job<N>.csv` under a temporary name and rename it, so its presence means the copy is complete. It is UTF-8 with BOM, as PowerShell's Export-Csv writes.
   - The CSV has one row. First come harvest's runs-CSV columns in harvest's order: Run, Owner, RunName, LastModified, FragPipeVersion, ProbeClass, Database, Enzyme, VarMods, HasWorkflow, HasManifest, PsmFiles, RawFiles, RawMissing, Experiments, RawPaths, FilesSelected, MBSelected, FilesTooLarge, Destination.
   - Then: IonomosJob, IonomosUser, IonomosExperiment, IonomosMethod, IonomosVersion, HandoffSha256 (the SHA-256 of `ionomos-handoff.json`).
   - Run = the FragPipe output folder's absolute Windows path on the PC. Owner = the Ionomos user folder name. ProbeClass = the method's kind, `isoDTB` or `TMT`. FilesSelected and MBSelected count the `.pin` files (at their size before compression) and the sample design; FilesTooLarge counts what either cap left.
   - The atlas counts sites from `isoDTB` runs only. A `TMT` run is verified and stored with its design and decoys; no counting rule reads it.
5. **Re-sending.** Re-sending a job (Retry or re-run) writes a new record. The atlas side keys a run by Run + HandoffSha256 and never double-counts. The atlas side is to accept `/1` records as before.
6. **Return path.** The atlas side publishes `<dest>\_atlas\atlas_sites_lookup.tsv` and `atlas_sites_lookup.json`.
   - TSV columns: accession, site (1-based position of the C in the canonical UniProt sequence), tier (high/medium/low by Dan's spectra rule of 2026-10-14), experiments, spectra, qc_suspect (0/1). Human only, sorted by accession then site.
   - JSON: contract `ionomos-atlas-lookup/1`, atlas_sha256, built, rule, sources, set, rows, quotable (bool).
   - Ionomos reads it read-only and only if it is present. A malformed file is ignored with a note.
7. **A run Ionomos found on the drives** (D128: not an Ionomos job; the drive sweep). Items 1-5 hold, with these differences.
   - It goes when harvest's reading of its workflow's modification masses is exactly `isoDTB`, or exactly `TMT` while `atlas.methods` names a TMT method. ProbeClass and `probe_class` are that reading.
   - Its experiment folder (for `experiment.yaml` and `experiment_annotation.tsv`) is harvest's: the run folder's parent when it holds `experiment.yaml` or an Ionomos status file.
   - `ionomos-handoff.json` has `found` in place of `job`: owner, experiment, experiment_folder, FragPipe version, workflow, FASTA, raw files, replicate plan, last_modified, probe_class_by_masses, Ionomos version. It holds no send time either.
   - The CSV is `<dest>\_harvest-logs\runs-ionomos-<YYYYMMDD-HHMMSS>-found-<run id>.csv`; `<run id>` is the first 12 hex of the SHA-256 of Run in lower case. Same columns. Owner and IonomosUser = harvest's Get-OwnerGuess of Run; IonomosExperiment = `found.experiment`; IonomosJob and IonomosMethod are empty.
   - The atlas side keys it by Run + HandoffSha256, as an Ionomos job's. A run harvest copied earlier has the same Run: its files are there already (skipped, by item 3), and the atlas side's same-inputs rule keeps it from being counted twice.
   - A receiver that doesn't know item 7 yet ignores the record (its name matches neither `runs-<time>.csv` nor `runs-ionomos-<time>-job<N>.csv`). The files wait in Dropbox; nothing is counted wrongly.

### What the contract leaves open, as Ionomos does it

- **The selection is the script's, not the summary in item 2.** `harvest_fragpipe.ps1`'s `$RootAllow` also takes
  `*.fasta`, `*.yml`, `*.yaml` and `*.manifest` from the top folder, and its `$ReplicateAllow` is `psm.tsv`,
  `peptide.tsv`, `protein.tsv`, `*.log`, `log_*.txt`. `atlas.py` copies the script's constants (`ROOT_ALLOW`,
  `REPLICATE_ALLOW`, `NEVER_COPY`, `SKIP_DIR_NAMES`) in its order. Case is ignored, as PowerShell's `-like` ignores
  it. A replicate subfolder is one level down; `MSBooster\`, `tmp\` and the script's other skipped names are not
  entered, and a junction or link is never followed. "Over 500 MB" is the script's: more than 500 × 1024 × 1024
  bytes. No `ion.tsv` (harvest takes it only with `-IncludeIonTsv`).
- **The decoys, measured (D127).** The lab PC's harvest inventory of 2026-10-12 lists 4,642 `.pin` files in 379
  runs: median 8 MiB, the largest 57 MiB, 42.5 GiB in all; 2,199 are MSBooster's `_edited.pin`. An `_edited.pin`
  holds every row and every column of its `.pin` with the same values (checked on six pairs of a lab run) plus
  seven MSBooster features, and Percolator scored it, so it alone is taken: 2,449 files, 23.6 GiB before
  compression. gzip level 6 makes a `.pin` 0.336 of its size (level 9: 0.336, twice as slow; level 1: 0.51), so
  all of them come to about 8 GiB in Dropbox (isoDTB runs 2.4 GiB, TMT 1.6 GiB), beside the 32 GiB harvest takes
  today. The 100 MB cap leaves none of them behind; it is there for a run unlike the lab's so far. The `.gz` has
  no name or time in its header (Python's `GzipFile(mtime=0)`), so Ionomos gives the same bytes every time.
- **A `.pin` is taken at either level**: FragPipe writes one per raw file into the experiment's subfolder, or into
  the top folder when the manifest names no experiment. `$NeverCopy`'s `*.pin` and `*.gz` still keep every other
  such file out; the `.pin` rule is applied first.
- **The sample design's kinds** are `experiment.yaml`, `experiment_annotation.tsv` (Ionomos's analysis wrote it
  for FragPipe-Analyst) and `annotation.txt` (any file whose name ends so, beside a raw file of the run). A folder
  of raw files that is gone or can't be listed adds nothing (harvest's RawMissing counts those raw files). A file
  inside `<dest>` is never taken. A file inside the run folder (raw files kept in FragPipe's output folder, with
  their `annotation.txt`) is the run's own: it goes once, in `files[]`, and not in `design`. The 500 MB cap applies.
- **Run** is `os.path.abspath` of the job's FragPipe output folder (`ionomos.json` `run.workdir`, else
  `<experiment>\fragpipe`), with backslashes and no `\\?\`. A UNC path lands under `<dest>\<server>_<share>\`,
  as harvest's does.
- **`ionomos-handoff.json`** is UTF-8 without a BOM, indented by two:

  ```json
  {
    "contract": "ionomos-atlas-handoff/2",
    "run": "C:\\Fragpipe_General\\Nicholas\\20261019_isoDTB_x\\fragpipe",
    "probe_class": "isoDTB",
    "files": [{"path": "exp_1/psm.tsv", "size": 123456, "sha256": "<64 lowercase hex>"},
              {"path": "exp_1/a_edited.pin.gz", "size": 3812345, "sha256": "<of the .gz>",
               "gzip_of": {"path": "exp_1/a_edited.pin", "size": 11345678, "sha256": "<of the .pin>"}}],
    "design": [{"kind": "experiment.yaml",
                "source": "C:\\Fragpipe_General\\Nicholas\\20261019_isoDTB_x\\experiment.yaml",
                "path": "C/Fragpipe_General/Nicholas/20261019_isoDTB_x/experiment.yaml", "size": 412,
                "sha256": "…"}],
    "left": [{"path": "exp_1/a.pin", "size": 9654321, "reason": "its _edited.pin is taken"}],
    "job": {"number": 12, "user": "Nicholas", "experiment": "20261019_isoDTB_x", "inbox_name": "…",
            "method": "isoDTB", "fragpipe_version": "24.0", "workflow": "…\\isoDTB.workflow", "fasta": "…",
            "raw_files": ["…\\a.raw"], "replicate_plan": [{"raw": "…", "experiment": "…", "bioreplicate": "1",
            "data_type": "DDA"}], "search_finished_at": "2026-10-19T14:02:11-07:00",
            "probe_class_by_masses": "isoDTB", "ionomos_version": "0.18.1"}
  }
  ```

  A run the drive sweep found has, in place of `job` (item 7):

  ```json
    "found": {"owner": "EJQ", "experiment": "2025-03-04_isoDTB_EJQ-1-100",
              "experiment_folder": "D:\\EJQ\\2025-03-04_isoDTB_EJQ-1-100", "fragpipe_version": "22.0",
              "workflow": "D:\\EJQ\\2025-03-04_isoDTB_EJQ-1-100\\fragpipe\\fragpipe.workflow", "fasta": "…",
              "raw_files": ["…\\S_1.raw"], "replicate_plan": [{"raw": "…", "experiment": "exp_1", "bioreplicate": "",
              "data_type": "DDA"}], "last_modified": "2025-03-05T09:12:44", "probe_class_by_masses": "isoDTB",
              "ionomos_version": "0.19.0"}
  ```

  `experiment` is the experiment folder's name, else the run folder's; `experiment_folder` is "" when harvest's
  rule finds none.

  `files[].path` is relative to the run folder, with `/`, sorted; the record is not in its own list.
  `replicate_plan` is the rows of the run's `fragpipe-files.fp-manifest`. `probe_class_by_masses` is harvest's
  reading of the workflow's modification masses, for information; `probe_class` and the CSV's ProbeClass are the
  method's kind (only FragPipe isoDTB and TMT methods may be in `atlas.methods`). `design[].path` is relative to
  `<dest>`, with `/`; `left[].reason` is `its _edited.pin is taken`, `pins off`, `pin larger than N MB` or
  `larger than 500 MB` (a design file's `path` there is its source). The record holds no send time, so sending
  the same files and job again gives the same bytes and the same HandoffSha256: nothing new for the atlas to
  count. A design file edited since the last send changes the record, as it should: the atlas side stores the
  new version and counts nothing twice (the run's `psm.tsv` inputs are the same).
- **The runs CSV** is written as PowerShell 5.1's `Export-Csv -NoTypeInformation -Encoding UTF8` writes: a BOM,
  every field in double quotes, CRLF. Values are rendered as harvest renders them: `True` / `False`,
  `MBSelected` as `[math]::Round(MiB, 1)` (`12.3`, `12`, `0`), `LastModified` as the local time of the newest
  file in the run folder (`yyyy-MM-ddTHH:mm:ss`), `Experiments` and `RawPaths` joined with ` | `.
  `FragPipeVersion` is the workflow's `# FragPipe (x)` line, else the run fingerprint's. The time in the name is
  local; a second send in the same second takes the next second's name.
- **Only one file at the destination is ever replaced**: `ionomos-handoff.json`, and only when the job's summary
  changed (Ionomos was updated between two sends). A runs CSV whose HandoffSha256 no longer matches the record was
  superseded by a newer CSV of the same Run.
- **SHA-256 is lowercase hex** (compare without case against PowerShell's `Get-FileHash`).

## The flow on the lab PC

```
worker            job done (FragPipe exit 0, the analysis finished, status=done)
  │               Worker.run_once → _atlas_queue: config.yaml read again; if atlas.enabled and the
  │               method is in atlas.methods → ionomos.json  "atlas": {"state": "pending", …}
  ▼               (a few bytes; nothing is copied here, so the next search starts at once)
atlas thread      HandoffLoop, in the watcher (cli.cmd_run), every 30 s: each done job of atlas.methods whose
  │               hand-off is pending, or failed and due → atlas.send
  ▼
send              lock <log_dir>\atlas-send-job<N>.lock (one process per job at a time)
                  select the files (harvest's rule) and the sample design → compare with what is at the
                  destination: same size + SHA-256 (a .pin.gz: the same content) → skipped; a design file that
                  differs → beside it as <name>.<sha12>.<ext>; anything else that differs → CONFLICT: nothing is
                  copied, stop
                  copy each missing file → <name>.partial (hashed while copying; a .pin gzip-compressed) → read
                    back (decompressed), compare → rename to <name> (never over a file) → keep the modified time
                  write <run dest>\ionomos-handoff.json
                  write _harvest-logs\runs-ionomos-<time>-job<N>.csv.partial → rename: complete
                  ionomos.json "atlas": state sent / failed / conflict / gave_up, with the reason
                  attention item atlas_handoff_failed on failure; closed when it is sent
```

| State (`ionomos.json` `atlas.state`) | Meaning | What happens next |
|---|---|---|
| (no `atlas` block) | never asked for: the job was done before the hand-off was turned on, or it is not an atlas method | only by hand: **Send to atlas** (Jobs tab) or `ionomos atlas send <job>` |
| `pending` | the worker queued it | the atlas thread sends it within 30 seconds |
| `sending` | a process holds the job's lock and is copying | a lock older than 6 hours (its process died) is taken over |
| `sent` | every file verified, record and CSV written | nothing; a manual send writes a new record (no double count) |
| `failed` | it could not finish (Dropbox offline, the folder missing, a copy that did not verify) | tried again by itself after `atlas.retry_minutes`, doubling each time, at most 12 hours apart |
| `gave_up` | it failed `atlas.max_tries` times in a row | waits for a person: fix the cause, then **Send to atlas** |
| `conflict` | a different file is already at the destination | waits for a person (below) |

Older searches are not sent when the hand-off is turned on: the atlas side already has what `harvest_fragpipe.ps1`
copied, and sending a year of runs at once is a decision for a person. **Send to atlas** sends any done job. Turning
the drive sweep on is that decision (D128): its first pass queues every done Ionomos job of `atlas.methods` that has
no hand-off yet, and hands off every other run on the drives.

**A conflict** means the Dropbox folder already holds a different copy of one of the run's files: a copy someone
edited, or an earlier search harvested from the same folder. Ionomos never overwrites it. The attention item names
each file that differs. Ask the person who keeps the atlas which copy is right; they move the old one aside in
Dropbox, then **Send to atlas**.

A changed **sample design** is not a conflict: `experiment.yaml` is edited whenever someone changes an analysis
choice, and `experiment_annotation.tsv` is written again by every analysis. The new version goes beside the earlier
copy (`experiment.<sha12>.yaml`); the record names the one this send used.

**Never touched**: the experiment folder. The hand-off only reads it; the one write is the `atlas` block of its
`ionomos.json`. Nothing at the destination is deleted, and nothing outside `<dest>` is written except that
`ionomos.json` block and the lock file in Ionomos' log folder.

## The drive sweep

The runs the lab searched before Ionomos, or searched by hand in FragPipe, are on the PC's drives: about 380 runs
with 4,642 `.pin` files in the harvest inventory of 2026-10-12. `harvest_fragpipe.ps1` copied their result files,
but not their decoys or sample design, and only when a person ran it. With `atlas.sweep` on, Ionomos does it by
itself (`atlassweep.py`, D128):

```
atlas-sweep thread   SweepLoop, in the watcher (cli.cmd_run), every minute: is a pass due? (atlas.enabled and a
  │                  folder; atlas.sweep on and the last pass atlas.sweep_hours old, or stopped 10 minutes ago;
  │                  or Search now in the app). Never while Ionomos is searching. Windows: background priority
  ▼
walk                 harvest's pass 1, read only: every fixed drive (or atlas.sweep_roots), harvest's skipped names
  │                  (Windows, AppData, MSBooster, tmp, ...), no junctions or links; the atlas folder, the Dropbox
  │                  folders (and any folder holding Dropbox's .dropbox marker), Ionomos' log folder (its FragPipe
  │                  preflights are dry runs of real workflows) and inbox, and atlas.sweep_exclude left out. A run folder holds a run marker (fragpipe.workflow,
  │                  the manifest, fragpipe.job, a combined_*.tsv), or psm.tsv with no run folder one or two above
  ▼
each run             in an Ionomos experiment folder only its latest search: a ledger job's → the job's hand-off
  │                  (queued if it has none); an earlier attempt (fragpipe_previous_<time>) → left
  │                  probe class by the workflow's masses, of the modifications turned on, not isoDTB (or TMT with
  │                  the switch) → left, with why; no psm.tsv (a dry run) or the test bed's fake FragPipe → left
  │                  a file made or changed in the last hour (Windows: its creation time too, which a copy sets)
  │                  → waits for the next pass
  │                  sent before, same files (names, sizes, times) → unchanged: not read again
  │                  the atlas folder's drive would fall below atlas.min_free_gb → nothing copied, the pass stops
  ▼
atlas.transfer       as a job's hand-off: compare, copy through .partial, verify, ionomos-handoff.json with `found`,
                     runs-ionomos-<time>-found-<run id>.csv renamed into place last
                     <log_dir>\atlas-sweep.json: what each run came to; attention item atlas_sweep_problem when a
                     run couldn't go or the pass stopped, closed by a pass with nothing left to a person
```

| What a run came to | Why | What happens next |
|---|---|---|
| `sent` | every file verified, record and CSV written | nothing, until its files change: then it is sent again (a new record; only the new files are copied) |
| `unchanged` | sent before, the same files since | nothing |
| `job` | the run of an Ionomos job | the job's hand-off (atlas.py) sends it, with its job record |
| `left` | not for the atlas: probe class isoTOP, other or unknown (no workflow), TMT with the switch off, a probe row turned off, no `psm.tsv`, another method's job, an earlier attempt in an Ionomos experiment folder, the test bed's fake run | nothing |
| `waiting` | a file made or changed in the last hour, or its Ionomos experiment folder is still being searched | the next pass |
| `failed` | it couldn't be listed or copied (Dropbox offline) | the next pass tries again |
| `conflict` | a different copy of one of its files is already in Dropbox (usually harvest's copy of a folder that changed since) | waits for a person: move the old copy aside, then **Search now** or `ionomos atlas sweep` (a pass a person asks for tries conflicts again; the daily pass doesn't) |

- **The first pass is long**: the walk lists every folder of every fixed drive (harvest took 10-30 minutes), and
  every run's files are compared with what harvest copied (read and hashed both sides), the `.pin` files
  compressed: about 8 GiB more in Dropbox (D127). It runs at background priority and stops between two runs
  when a search starts (the walk itself goes on: it only lists folders); the next pass, 10 minutes after Ionomos
  is idle again, reuses the walk and skips what was sent. A pass that stopped for a cause (Dropbox gone, the
  drive full, a bug) is tried again after an hour and raises the attention item; one that stopped for a search
  or a restart does neither. Later passes read only what changed.
- **Free space**: the Dropbox folder is on `C:`, where FragPipe writes. Nothing of a run is copied when its copy
  would leave less than `atlas.min_free_gb` (40 GB; FragPipe holds a search below `fragpipe.min_free_gb`, 20) on
  that drive: the pass stops and says so in the attention list, and a job's hand-off fails and is tried again
  later, as when Dropbox is offline.
- **Only reads**: nothing on the drives is written, moved or renamed. Outside `<dest>` the sweep writes Ionomos'
  own `atlas-sweep.json` (and its lock) in the log folder, and, when it queues a done Ionomos job, that job's
  `atlas` block in its `ionomos.json`, as the worker does. Deleting `atlas-sweep.json` is safe: the next pass
  compares every run with the destination again and copies nothing twice.
- **Locks**: a pass's lock (`atlas-sweep.lock`, and a job's `atlas-send-job<N>.lock`) names its process; one left
  by a process that is gone (Ionomos restarted mid-copy) is taken over at once, not after 6 hours.
- `ionomos atlas sweep --dry-run` (also while the hand-off is off) lists what a pass would send, by probe class
  and owner, and how many files and MB are not in Dropbox yet (by name and size: it reads no content); it writes
  nothing anywhere.

### The first run on the lab PC

What to do at the PC, in order (`ionomos-cli.exe` is `C:\Ionomos\ionomos-cli.exe`, in PowerShell). Nothing here
deletes, moves or overwrites anything; each step can be stopped and done again.

1. **Install 0.20.0 or later** (the installer from the GitHub release, or the app's update check), then restart
   the app and the watcher.
2. **Set the folder and the switches, not the sweep yet.** Folders tab → Cysteine atlas: **Use** the Dropbox
   folder Ionomos found (`…\Nicholas Foster Data\fragpipe-runs`), tick **Also TMT searches**, leave **Also search
   this PC's drives** off, **Save**. Then `ionomos-cli.exe atlas check`: every line ✓ (the folder there and
   writable; the site lookup may be missing). Ticking the hand-off itself is not needed for the dry run.
3. **Dry run** (10-30 minutes: it lists every folder of C: and D:; it copies and writes nothing):
   ```powershell
   C:\Ionomos\ionomos-cli.exe atlas sweep --dry-run
   C:\Ionomos\ionomos-cli.exe atlas sweep --dry-run --json > C:\Fragpipe_Auto\logs\atlas-sweep-dry-run.json
   ```
   The first prints the runs that would go by probe class and owner, and why the others are left; the second has
   every run with its path, owner, probe class, FragPipe version, modification tables (which TMT, which probe on
   C), `.pin` and annotation counts, and the MB not in Dropbox yet. Copy the JSON into `Nicholas Foster Data\`
   in Dropbox (not into `fragpipe-runs`) for the atlas side. Check: about 380 runs in all (harvest's inventory of
   2026-10-12), isoDTB and TMT ones under the owners you expect, and `C:`'s free space above 40 GB plus about
   8 GB (the `.pin.gz`).
4. **One folder first, by hand**, an owner with TMT competition runs (the hand-off on: tick it, **Save**):
   ```powershell
   C:\Ionomos\ionomos-cli.exe atlas sweep --root "D:\<owner>"
   ```
   It prints each run it sends. In Dropbox, under `fragpipe-runs\D\<owner>\…\`, a sent TMT run has its
   `psm.tsv` files, its `*.pin.gz`, `ionomos-handoff.json` (`probe_class` TMT, `found`, `design` naming each
   plex's `annotation.txt`), and `_harvest-logs\runs-ionomos-<time>-found-<id>.csv`. A conflict means harvest's
   copy of that run differs: leave it for the person who keeps the atlas.
5. **Then the rest, in the background**: tick **Also search this PC's drives every day**, **Save**, **Search
   now**. The watcher does the first pass at background priority and pauses for each search; the Folders tab's
   line, `ionomos-cli.exe atlas status` and the attention list say how far it got and what was left. Runs found
   but not counted yet: the atlas side reads them once it knows contract item 7.

## Settings

```yaml
atlas:   # hand finished isoDTB (and TMT) searches to the lab's cysteine atlas. Off by default
  enabled: false
  handoff_dir: ''      # the lab Dropbox folder ...\Nicholas Foster Data\fragpipe-runs
  methods: [isoDTB]    # FragPipe isoDTB or TMT methods whose searches are handed off (the atlas counts isoDTB only)
  pin_max_mb: 100      # each .pin (the decoys) goes gzip-compressed if no larger than this many MB; 0 = none
  max_tries: 10        # a hand-off that keeps failing (Dropbox offline) stops after this many tries
  retry_minutes: 15    # the first wait before trying again; it doubles, up to 12 hours
  sweep: false         # also search this PC's drives for FragPipe runs Ionomos did not search (D128)
  sweep_roots: []      # the folders searched; [] = every fixed drive
  sweep_exclude: []    # folders never searched (the atlas and Dropbox folders never are)
  sweep_hours: 24      # a search every this many hours, never while a search runs
  min_free_gb: 40      # copying to the atlas folder (a job's hand-off, the sweep) stops before its drive has less free
```

- `handoff_dir` may have spaces: FragPipe never sees it. It must be a full path (`C:\…` or `\\server\share\…`).
  When the folder is not there yet but its parent is (Dropbox has `Nicholas Foster Data` but no `fragpipe-runs`),
  the first hand-off makes it, as harvest does; when the parent is missing too, the hand-off waits (Dropbox not
  running, not signed in, or the folder not synced to this PC).
- `methods` may name only methods searched by FragPipe whose kind is isoDTB or TMT (`isoDTB`, `TMT`, or a method
  with `naming.methods.<key>.like: isoDTB` or `TMT`); while the hand-off is on, anything else stops config.yaml from
  loading with the line named, because the runs CSV's `ProbeClass` is that kind and the atlas counts an isoDTB run.
  While it is off they are not checked, so a lab without an isoDTB method loads (every Save writes the default
  `[isoDTB]`).
- `pin_max_mb` is a whole number from 0 to 4096, in MiB before compression, like harvest's `-MaxPinMB`; 0 sends no
  `.pin` at all.
- `sweep_roots` and `sweep_exclude` are lists of full folder paths (spaces are fine: they are only read). With no
  `sweep_roots`, every fixed drive (Windows' DRIVE_FIXED, as harvest's Win32_LogicalDisk DriveType 3): the lab
  PC's `C:` and its USB archive disk `D:`, not its flash drive. `sweep_hours` is a number from 1 to 720,
  `min_free_gb` from 0 (0 = no floor), for a job's hand-off as for the sweep. The sweep needs `enabled` and `handoff_dir`; **Search now** and `ionomos atlas
  sweep` work while `sweep` is off.
- The watcher reads `atlas:` again on every pass, so turning the hand-off on or off in the app counts without a
  restart.

## The command line

`ionomos atlas send JOB_ID` hands one finished job over now, whatever its state (exit 0 sent; 1 not sent, with why
and every file that differs; 2 the hand-off is off or has no folder). `ionomos atlas status [JOB_ID] [--json]`
lists each finished atlas job and where its hand-off stands. `ionomos atlas check [--no-write]` checks that the
hand-off is on, the folder is set, there and writable (it makes and removes one small test file), and that the
atlas's site lookup is there and reads; exit 1 when something is wrong. `ionomos atlas sweep [--dry-run] [--root
DIR]... [--json]` runs a pass of the drive sweep now, in the terminal (conflicts are tried again; `--root` searches
those folders instead; exit 0 went through; 1 a run not sent, or the pass stopped; 2 the hand-off is off or has no
folder, or there is nothing to search). `ionomos atlas status` ends with a line for the last pass. Exit codes
follow D102.

## In the app

- **Folders tab → Cysteine atlas**: "Hand finished isoDTB searches to the cysteine atlas" (`atlas.enabled`), the
  destination folder (spaces allowed) with **Browse…**, and the Dropbox folder Ionomos found (as
  `harvest_fragpipe.ps1` finds it: `%LOCALAPPDATA%\Dropbox\info.json`, then `Nicholas Foster Data` up to two
  levels down), offered with **Use**, never filled in by itself. **Help** opens `faq.atlas`. **Also TMT searches**
  (D127) is on while `atlas.methods` names one of the lab's FragPipe TMT methods; ticking it adds every such method
  (`atlas.methods_of_kind`), clearing it takes them out. **Save** writes `enabled`, `handoff_dir` and that change
  of `methods`, and keeps the other `atlas:` keys (and the other names in `methods`) as the file has them.
  **Also search this PC's drives every day** is `atlas.sweep` (D128); **Search now** asks the watcher for a pass at
  once (`atlas-sweep-now` in the log folder; the saved settings only), and the line under it says how the last one
  went (read off the window's thread when the tab is shown).
- **Jobs tab**: an **atlas** column with each isoDTB (or TMT) job's hand-off state (`atlas.describe`), shown while the
  hand-off is on or a listed job has a hand-off; the detail pane says the same, with the first files that differ
  after a conflict. **Send to atlas** sends the selected done job now; a job not done, the hand-off off or a folder
  typed but not saved are explained instead.
- **Setup checklist**: "Cysteine atlas hand-off", only while the hand-off is on, from `atlas.check` (to do: no
  folder; warning: the folder can't be reached or the lookup is malformed; problem: the settings don't load), with
  **Fix** to the folder field.
- **Attention list**: a failed hand-off is an item without a pop-up window; its window (opened from the list) has
  **Send to atlas** and **More help** ([help](../ionomos/src/ionomos/help/troubleshooting.md)). A pass of the drive
  sweep that left runs to a person, or stopped, is one item `atlas_sweep_problem` (no pop-up), with each run and why.

Sending runs off the window's thread (`App.run_bg`), and so does the Dropbox search (once, when the Folders tab is
first shown). The atlas column reads each done atlas job's `ionomos.json` on a background thread, again only when
the file changed.

## In the report

When the atlas has published its lookup (`<dest>\_atlas\`), the isoDTB report's site table shows, for each human
cysteine site, the atlas tier (high / medium / low), how many atlas experiments saw it, and a "new to the atlas"
mark for a site the atlas has never seen. One line under the table says these are the lab's internal numbers, when
the atlas was built and by which rule, and whether the set may be quoted (`quotable` in the JSON). An isoform, a
non-UniProt protein or a non-human protein gets no atlas context and is never called new. With no lookup, nothing
in the report changes; a malformed lookup is ignored and the report says so in its notes.

## What the lab must confirm

See [ROADMAP.md](ROADMAP.md) ("The cysteine atlas hand-off"): whether the lab PC's Dropbox client is signed in to
the lab Dropbox and syncs `Nicholas Foster Data`, whether the Windows account the watcher runs as can write there,
and who decides a conflict; for the drive sweep, that the atlas side reads item 7's records.
