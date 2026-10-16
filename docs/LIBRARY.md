# The workflow and FASTA library

`ionomos/src/ionomos/library.py` (D89). The lab PC holds well over a hundred
FragPipe `.workflow` files — one `fragpipe.workflow` inside every output folder
on `D:\<user>\`, custom ones such as Aman's `fragpipe_pax8.workflow`, the stock
ones of three FragPipe versions — and FASTAs in several places. Nobody could say
which workflow is the lab's TMT one, whether its FASTA still exists, or whether
it is current. The library finds them, says in plain words what each one is,
marks what is wrong with it, ranks the candidates for each method and copies the
chosen ones into the pinned folders.

The commands below and the Methods tab's panel "Workflows and FASTAs on this
PC" (end of this page) are its interface.

## Commands

```
ionomos library scan [--root DIR ...] [--only] [--replace] [--depth N] [--time S] [--json]
ionomos library list [workflows|fastas] [--kind TMT] [--flags] [--json]
ionomos library show PATH [--json]
ionomos library recommend [--apply] [--json]
ionomos library adopt PATH --method KEY [--with-fasta] [--no-config] [--force]
ionomos library check [--json]
```

- **scan** looks (read-only) and saves what it found in
  `<log_dir>/library.json` (`names.LIBRARY_FILE`). A FASTA that has not changed
  since the last scan is not read again. `--root DIR` *adds* a folder to the
  default ones (below); `--only` looks in the `--root` folders alone. A scan
  that stopped early (`--time`, Stop) or looked in fewer folders than the last
  one does not replace the fuller catalog: the files of the last scan it did
  not see, still on disk and unchanged, are kept (`library.save_scan`), and a
  note says so; `--replace` keeps only the new scan. A configured folder that
  does not exist (an unplugged drive in `library.roots`, a typo) is a note in
  the output, the catalog and `list`.
- **list** prints the last scan, one line per file plus its summary, and for a
  workflow that is not itself a run's, the finished searches with its settings
  ("used in 4 finished search(es), the latest 2026-10-15"); `--flags` adds
  every mark with what to do. `--kind K` with no workflow of that kind says so
  (and lists the kinds when K is not one; exit 2).
- **show** describes one file now (no scan needed): kind, every setting below,
  the tools it needs, what its search left (or, from the last scan, the
  finished searches with its settings), its flags.
- **recommend** prints the three best workflows and the best FASTA per method
  with the reasons, and *proposes* a file for each method whose workflow or
  FASTA is missing or has an error (✗). A method whose files are fine is never
  touched. `--apply` adopts the proposals and writes `config.yaml` through
  `configio.write_config` (the old one goes to `config-backups/`), after the
  real loader has accepted the new text.
- **adopt** copies a workflow into the workflows folder as `<method>.workflow`
  (or a FASTA into the FASTA folder) and sets it for the method in
  `config.yaml`. The method keeps its FASTA unless that is missing or
  `--with-fasta` is given. `--no-config` only copies, under a name no method
  of the saved config.yaml uses (`TMT-2.workflow` when `TMT.workflow` is what
  the method searches with), so the next search is not changed behind the
  config's back. A workflow of another kind than the method's
  (`WF_KIND_MISMATCH`: a DIA workflow for TMT) or a file with a ✗ mark (a FASTA
  without decoys, a TMT workflow without a label type, a decoy tag that does
  not match its FASTA) is refused, nothing copied, with each reason and its
  fix; `--force` adopts it anyway (`library.adopt_problems`).
- **check** compares each method's pinned files with what the PC has: a stock
  workflow of the same kind saved with a newer FragPipe, an old FASTA, a newer
  FASTA of the same proteome, a pinned file that is gone. It changes nothing;
  exit code 1 when a method's file is missing. Before any scan it can only see
  the pinned files, and says so: nothing has been scanned yet, run
  `ionomos library scan` (or the Methods tab's **Find on this PC…**) — never
  "everything is current".

Restart the watcher after `--apply` or `adopt` (it reads the methods at start).

## Where it looks

`default_roots(cfg, missing)`, each folder once, only those that exist (a
configured one that does not is added to `missing` as a note):

1. `paths.workflow_dir` and `paths.fasta_dir` (the pinned folders);
2. every FragPipe installation: the configured launcher's (`bin\..`), every
   launcher `fragpipe.launcher_candidates()` finds, `C:\FragPipe\*`,
   `C:\FragPipe*` and `%USERPROFILE%\Downloads\FragPipe*` (the 22.0 zip). A
   folder these patterns match counts only when it is an installation
   (`bin\fragpipe.bat` or `lib\fragpipe-*.jar` inside, or in its `fragpipe\`
   as in the zip): `C:\Fragpipe_Auto` and `C:\Fragpipe_General` are not, so their
   workflows are never called *stock*;
3. `paths.users_root`;
4. `library.roots` from `config.yaml` — **`D:/` is not looked at unless it is
   added here** (or given with `--root`), because it is an 18 TB USB drive. A
   UNC path (`//server/share`) is a plain folder.

Each root is asked whether it exists on a helper thread: one that gives no
answer within 5 s (`ROOT_ANSWER_S`; a share whose server is off takes about
20 s on Windows) is skipped with a note in the catalog. A network path written
*inside* a file the walk finds (a workflow's `database.db-path`, the FASTA in a
`fragpipe-ui.cache`) is never opened, by the scan or by adopt: opening it would
make Windows sign in to that server as the account Ionomos runs as, and the
file may come from anyone's drop. Such a workflow carries `WF_DATABASE_MISSING`
("on another computer"); copy that FASTA into `fasta_dir` by hand if it is the
one you want (docs/SECURITY_NOTES.md).

The walk is breadth-first over all roots together, so the small pinned folders
are done first. It is read-only, at most `max_depth` folders deep (default 6)
and at most `time_limit_s` seconds (default 120); a `stop` event ends it early.
Either way the catalog says it stopped, why, at which folder, and how many
folders it had looked at. A folder reached a second time through a junction
or a link is skipped (real paths are tracked; on Windows a reparse point
counts as a link). Skipped, and listed in the catalog's `skipped`:

- `$RECYCLE.BIN`, `System Volume Information`, hidden and system folders,
  names starting with `.` or `$`;
- `.venv`, `venv`, `node_modules`, `__pycache__`, `.git`, `site-packages`;
- Ionomos' own `ionomos_run` / `labwatch_run` (its per-job copies of the pinned
  workflows) and `results`, and `fragpipe_previous_*` attempts (the search's
  own `fragpipe/fragpipe.workflow` is read: it is the evidence that the
  pinned settings were used, see *Content hash*);
- Bruker `.d` run folders;
- inside a FragPipe installation: `jre`, `lib`, `tools`, `python`, `ext`,
  `fonts`, `images`, `jobs`;
- anything matching `library.exclude` (a folder name or a path pattern, case
  does not matter).

Files taken: `*.workflow`; `*.fas`, `*.fasta`, `*.fa` (so also
`.fasta.fas`); and `fragpipe-ui.cache`. Not a FASTA: the `protein.fas` that
Philosopher leaves in each group folder of a search (`<workdir>\<group>\`,
beside `psm.tsv` / `protein.tsv`, or under a folder holding
`fragpipe.workflow`), the proteins that search found, without decoys; it is
listed in `skipped` with that reason. A `protein.fas` anywhere else is read
like any FASTA. The last is the file in which
FragPipe's window keeps its state (`<install>\cache\fragpipe-ui.cache`; Java
properties with the workflow's own keys). From it the catalog keeps a *hint*:
the FASTA (`database.db-path`) and the workflow (`workflow.workflow-option`)
the window last used. A hinted FASTA outside every root is described too,
with source `cache`.

## A workflow, in plain words

`describe_workflow(path) -> WorkflowInfo`. The file is read once (a file over
5 MB is not a FragPipe workflow).

**Kind** (`kind`, plus `traits`): first what it quantifies, from its `run-*`
switches as `fpfolder.workflow_kind` reads them — TMT-Integrator on → `TMT`;
DIA-NN on → `DIA`; MS1 quant (`quantitation.run-label-free-quant`) without
`ionquant.use-labeling` → `LFQ`; with it, the label (`ionquant.light` /
`.medium` / `.heavy`, as FragPipe 24.0's stock workflows set them) says which:
561.3387 / 567.3462 → `isoDTB` (chemprot-ABPP-isoDTB), 464.286 / 470.300 →
`isoTOP`, 463.237 / 467.253 → `ipIAA`, 436.226 / 442.263 → `PAL` (chemprot-PAL),
labels on K and R only → `SILAC` (SILAC3), none of these → `labelled`
(without labels set, an isoDTB mass among the variable mods → `isoDTB`). An
isoDTB light/heavy mass among the enabled variable mods of a label-free
workflow also makes it `isoDTB` (trait `isoDTB label`). If none of isoDTB / TMT / DIA: a precursor
window in Da at least 50 wide (`msfragger.precursor_mass_units=0`, Open:
−150 to 500) or mass offsets (`msfragger.mass_offsets` other than 0, or
`mass_offsets_detailed`) → `open` (trait `open` or `mass-offset`);
79.9663 on STY switched on → `phospho`; label-free MS1 → `LFQ`; else `other`.
A TMT or DIA workflow with phospho on stays TMT / DIA with the trait
`phospho`. The method kinds of `config.yaml` (`Config.kind`: isoDTB, TMT,
DIA, LFQ) are compared with these.

**Settings** (`settings`, label → value; a key the workflow lacks is left
out): enzyme and missed cleavages; fixed and variable mods with names for the
common masses (carbamidomethyl 57.0215, oxidation 15.9949, acetyl 42.0106,
phospho 79.9663, TMT 229.1629, TMTpro 304.2071, isoDTB 561.3387 / 567.3462,
pyro-glu, GG, deamidation, iTRAQ-4, SILAC Lys8 / Arg10, methyl, dimethyl;
sites `[^` = protein N-term, `n^` = peptide N-term); precursor and fragment
tolerance (units `1` = ppm, `0` = Da); for TMT-Integrator: label type
(`tmtintegrator.channel_num`), the reference (a virtual one when
`tmtintegrator.add_Ref` is 0, 1 or 2 — sum, average, median — else the
channel whose name contains `tmtintegrator.ref_tag`), group-by, protein
normalisation, minimum purity; MS1 quant on/off/labelled; match-between-runs;
DIA-NN; spectral library; Percolator / PeptideProphet; protein FDR from
`phi-report.filter`.

SILAC, isoTOP, ipIAA and PAL are their own kinds, so a stock SILAC3 is never a
candidate for the isoDTB method and is never called an isoDTB search.

**Summary**, e.g. *"A TMT-10 MS3 search with TMT-Integrator: carbamidomethyl
C, TMT K fixed; oxidation M, acetyl protein N-term, TMT peptide N-term
variable; stricttrypsin, 2 missed cleavages. Saved with FragPipe 24.0; made
for 2026-01-30-decoys-contam-UP000005640_9606.fasta.fas."*

**Source**: `pinned` (inside `workflow_dir`), `stock` (in the `workflows`
folder of a FragPipe installation), `run` (a `fragpipe.workflow`, or a
workflow beside FragPipe's `fragpipe.job` / manifest: the output of a
search), `other`.

**What its search left** (`runs`, source `run` only): the result tables
`fpfolder.find_outputs` finds readable in that folder (one level down:
`tmt-report/`, `dia-quant-output/`, `combined_*.tsv`), whether FragPipe's log
says `ALL JOBS DONE`, the newest file's date. A search *finished* when a
readable table is there.

**Content hash** (`content_hash`): sha1 of every key and value except
`database.db-path`, `workflow.saved-with-ver`, `workflow.description`,
`workflow.workflow-option`, `workflow.ram`, `workflow.threads`,
`workflow.last-save-dir`, `workdir` (the output folder FragPipe writes into
a search's `fragpipe.workflow`), the `fragpipe-config.*` tool locations, any
key ending in `path`, `dir`, `folder` or `file`, and any key whose value is an
absolute path (`C:\…`, `C:/…`, `\\server`, `/Users/…`). The same settings
saved under another name, for another FASTA, by another FragPipe or as a
search's own copy hash the same, so recommend counts them as one, with
`copies`, and the searches Ionomos ran with a pinned workflow count as its
finished searches instead of as rival workflows.

## A FASTA, in plain words

`describe_fasta(path) -> FastaInfo`. The file is read as bytes in 8 MB chunks;
only header lines are looked at, so a 1 GB file is never decoded or split
into lines, and what is kept between chunks is at most one header line (a
header over 64 kB is cut), so a file without line breaks is read as fast as
any other. The result is cached by (path, modification time, size), in memory
and, through the catalog, across scans (a reading made by an older Ionomos,
`reader` below `FASTA_READER`, is read again). A reading that ran out of time or was
stopped says so (`complete: false`) and is not cached.

- entries, decoys and targets; the **decoy prefix is detected**, not assumed:
  the most common of `rev_`, `DECOY_`, `REV_`, `decoy_`;
- contaminants: targets starting `contam_` (FragPipe) or `CON__` (MaxQuant);
- organisms from `OX=` / `OS=` of the targets that are not contaminants, the
  top one and its share. The organism is named only when at least half of
  the proteins carry an `OX=`; otherwise (GENCODE / Ensembl / smORF headers,
  as in FragPipe's own `…Gencode_v42…FragPipe.2.fasta.fas`, whose few
  UniProt-style headers are its bovine contaminants) it is *organism
  unknown*, unless the name says it (`UP000005640_9606`), and ranking neither
  rewards nor punishes it for the organism;
- reviewed (`sp|`) and unreviewed (`tr|`) entries, isoforms (`sp|P12345-2|`);
  when fewer than half of the proteins have UniProt headers, the summary says
  *mostly not UniProt-style headers* instead of a reviewed share;
- from the name: the date FragPipe / Philosopher put first
  (`YYYY-MM-DD-decoys-…`), the UniProt proteome and taxon
  (`UP000005640_9606`), and whether the name has FragPipe's form
  (`<date>-decoys-[…-]UP…[_taxon].fasta.fas`);
- a fingerprint cheap for 1 GB: size, entry count and the sha1 of the first
  and last 1 MB;
- a summary, e.g. *"Human, reviewed UniProt (20,698 proteins) with decoys
  (rev_) and N contaminants, made 2026-01-30 (proteome UP000005640)."* (the counts are
  read from the file)

## Flags

Every flag has a code, a severity (`error` ✗: a search would fail or give wrong
results; `warn` !: check it; `info` ·), a sentence on what is wrong and one on
what to do. Each code has a help entry `library.<code in lower case>` in
`help/troubleshooting.md` (`ionomos help FASTA_NO_DECOYS`); a test fails when
a code in `library.py` has no entry or is missing here.

| Code | Severity | When | What to do |
|---|---|---|---|
| `WF_UNREADABLE` | error | not a FragPipe workflow, unreadable, over 5 MB | pick another; save it again from FragPipe |
| `WF_SPACE_IN_PATH` | info | a space in its path | adopt it (copied without spaces) |
| `WF_DATABASE_MISSING` | warn | its `database.db-path` is not on this PC | the method's FASTA is used; check it matches |
| `WF_DATABASE_ELSEWHERE` | info | its FASTA exists outside `fasta_dir` | adopting copies it in |
| `WF_OLDER_FRAGPIPE` | warn | saved with an older major version than installed | load, check and save it in the installed FragPipe |
| `WF_NEWER_FRAGPIPE` | warn | saved with a newer version than installed | update FragPipe or save it again |
| `WF_TOOL_MISSING` | error | needs DIA-NN / IonQuant / diaTracer / Python the installation lacks (`fragpipe.install_report`) | install it from FragPipe's Config tab |
| `WF_SDRF_ON` | warn | `workflow.misc.save-sdrf=true` (D59 item 11) | switch off Save SDRF, save again |
| `WF_LABEL_TYPE_UNSET` | error | TMT-Integrator on, no `tmtintegrator.channel_num` | set the label type in FragPipe |
| `WF_DECOY_TAG_MISMATCH` | error | its decoy tag differs from its FASTA's decoy prefix | set the tag to the FASTA's prefix |
| `WF_NO_RUNS` | info | a run folder with no readable table, or settings no finished search used | prefer one that has produced results |
| `WF_KIND_MISMATCH` | error | `adopt` / Use for: the workflow's kind is not the method's | pick one of the method's kind; `--force` if meant |
| `WF_DIFFERS_FROM_PINNED` | info | same kind as a method's pinned workflow, other settings (listed) | check each difference is meant |
| `FASTA_UNREADABLE` | error | can't be opened, or no `>` line | pick another |
| `FASTA_SPACE_IN_PATH` | warn | a space in its path | adopt it (copied without spaces) |
| `FASTA_NO_DECOYS` | error | no decoy prefix found | make one with decoys (FragPipe's Database tab, `ionomos fasta prepare`) |
| `FASTA_DECOY_SHARE` | error (info at ≥ 1 GB) | decoys outside 40–60 % of entries (`fragpipe.decoy_problem`'s rule) | add decoys once, to a file without any |
| `FASTA_DECOY_TAG` | info | decoys not `rev_` | the workflow's decoy tag must match |
| `FASTA_NO_CONTAMINANTS` | warn | no `contam_` / `CON__` entries | make it with Add contaminants |
| `FASTA_MIXED_SPECIES` | warn | the top organism under 90 % of the proteins that name one (only when the organism is known) | fine for a benchmark; else one organism |
| `FASTA_UNREVIEWED` | warn | more unreviewed (`tr`) than reviewed (`sp`) entries, and they are over half of the proteins | use a reviewed proteome |
| `FASTA_OLD` | warn | the date in its name is over 12 months ago | make a new one for new projects |
| `FASTA_DUPLICATE` | info | the same fingerprint at another path | keep the one in the FASTA folder |
| `PINNED_MISSING` | error | `check`: a method's workflow / FASTA is not in its folder | `recommend --apply` |
| `UPDATE_STOCK_NEWER` | info | `check`: a stock workflow of the same kind saved with a newer FragPipe | compare, redo the lab's changes, adopt |
| `UPDATE_FASTA_NEWER` | info | `check`: a newer FASTA of the same proteome is on this PC | adopt it for new projects |

## How candidates are ranked

`recommend(catalog, kind, installed_version, pinned)` gives one candidate per
content hash (its representative: the method's current file, else pinned,
stock, the newest run), best first, with the reasons in words, e.g. *"used in
6 finished search(es), the latest 2026-08-17; saved with the installed FragPipe
24.0; its FASTA exists"*. Points: 4 per finished search (at most 10 counted);
+10 saved with the installed major version (the newest version among the copies
with the same settings; that copy represents them), −3 per major version older,
−8 newer; +5 its FASTA exists; +6 it is the method's current workflow, +3 another
pinned one; +2 stock; FragPipe's own stock workflow for the kind
(`CANONICAL_STOCK`: isoDTB → `chemprot-ABPP-isoDTB` (22.0: `isoDTB-ABPP`), TMT →
`TMT10-MS3`, `TMT10`, `TMT16`, DIA → `DIA_SpecLib_Quant`, LFQ → `LFQ-MBR`) +4 for
the first, +3, +2 for the next; the stock workflow whose name the method's
`workflow:` asks for (e.g. `TMT16.workflow`) counts as the first; −40 per error
flag, −3 per warning. Files that are no longer on disk are left out. These
weights are Ionomos' choice; they order, the reasons explain. Candidates within
0.5 points of each other (`TIE`) are a tie: their order means nothing.

`recommend_fastas(catalog, taxon, kind, taxon_from)`: one candidate per
content (fingerprint), represented by the copy in the FASTA folder, then one
without a space in its path, with FragPipe's name, with a date in its name, the
newest file; +15 the organism asked for (−30 another, 0 when its organism is
unknown), 3 per finished search that used it (by path, else by file name), +10
usable decoys, +3 contaminants, +2 reviewed, up to +3 for a recent date, +4
already in the FASTA folder, and the same flag penalties. `auto_assign` asks
for the organism of the FASTAs already in the FASTA folder, else of those
finished searches used (`lab_taxon`), and the reason says so ("Human, the
organism of the FASTA folder's files"), never "as asked".

`auto_assign(cfg_dict, catalog, installed_version, notes) -> [Proposal]` proposes the
best candidate without an error for every method whose workflow (FragPipe
methods) or FASTA is missing or has an error, and for nothing else. When the
best candidates tie, it proposes none of them (never the first file name among
equals) and `notes` says so with the tied names and how to pick one; also when
nothing usable was found. `recommend` prints them under *Not proposed*, the
app's Review proposals… in its status line.
`apply_proposals` adopts them and edits the dict; the caller writes it.

## Adopting

`adopt(info, method, cfg)` copies, never moves:

- the workflow goes to `workflow_dir/<method>.workflow` through
  `fragpipe.import_workflow`, so a pinned file it replaces is kept as
  `<name>.bak-<time>`; the method key is made FragPipe-safe (letters, digits,
  `.`, `_`, `-`) for the file name; adopting the same workflow again copies
  nothing. `avoid` (`live_workflows`: the files the saved config.yaml's
  methods search with) is never replaced: the copy then gets `<method>-2.workflow`
  (`-3` …). The CLI passes the other methods' files (all of them with
  `--no-config`); the app's **Use for <method>** passes all of them, so the
  watcher's next search changes only when **Save** writes the new name;
- its FASTA, when it exists outside `fasta_dir`, is copied in first
  (`adopt_fasta`) and the copy's `database.db-path` points at it;
- `adopt_fasta` names the copy without spaces; a file of the same content
  already there is used as it is, a different file with that name is never
  replaced (the copy becomes `-2`, `-3` …); it is written as `.part` and
  renamed when complete;
- the catalog records each adopt (`adopted`: what, from where, to where, its
  fingerprint, when, `who: adopt`).

## config.yaml

```yaml
library:
  roots: []          # more folders, e.g. [D:/] or ['//server/share']
  exclude: []        # folder names or path patterns to leave out, e.g. ['*/raw']
  max_depth: 6       # 1–30
  time_limit_s: 120  # 5–3600
```

Checked by `library.settings_from` when the config is loaded; a typo is an
error naming it. A config without the block gets these defaults.

## What is confirmed, what is assumed

Confirmed on 2026-10-14 against FragPipe's repository on GitHub
(`Nesvilab/FragPipe`, `workflows/` on `master`, saved with `24.0-build27`) and
its source (`FragPipe-GUI/src/main/java/org/nesvilab/fragpipe/`):

- key names and value formats in the stock `TMT10-MS3`, `chemprot-ABPP-isoDTB`
  (there is no `isoDTB-ABPP` in 24.0; that name is the 22.0 zip's),
  `DIA_SpecLib_Quant`, `Open` and `Mass-Offset-CommonPTMs` workflows:
  `workflow.saved-with-ver`, `workflow.description` (HTML, `\=` and `\:`
  escaped), `workflow.misc.save-sdrf=true`, `database.decoy-tag=rev_` (no
  `database.db-path` in stock workflows), `tmtintegrator.channel_num=TMT-10`,
  `tmtintegrator.ref_tag=Bridge`, `tmtintegrator.add_Ref=1`,
  `tmtintegrator.groupby=-1`, `tmtintegrator.prot_norm=1`,
  `tmtintegrator.min_purity=0.5`, `msfragger.table.fix-mods` /
  `msfragger.table.var-mods` as `mass,site,enabled,max; …` (TMT: 229.16293 on
  K fixed and on `n^` variable), `msfragger.search_enzyme_name_1`,
  `msfragger.allowed_missed_cleavage_1`, `msfragger.precursor_mass_lower /
  _upper / _units / _mode`, `msfragger.fragment_mass_tolerance / _units`,
  `msfragger.mass_offsets(_detailed)`, `phi-report.filter`,
  `ionquant.use-labeling`, `ionquant.light / heavy`, `ionquant.mbr`,
  `diann.run-dia-nn`, `speclibgen.run-speclibgen`;
- `fragpipe-ui.cache` is FragPipe's UI-state file (`FragpipeLocations.FN_CACHE_UI`),
  saved as Java properties of the window's fields;
  `workflow.workflow-option`, `workflow.last-save-dir` and
  `workflow.saved-with-ver` are names the Workflow tab uses (`TabWorkflow`).

Assumed (not checked against a source):

- the meaning of TMT-Integrator's numeric codes shown as words
  (`add_Ref` −1 none / 0 sum / 1 average / 2 median; `groupby`, `prot_norm`),
  taken from TMT-Integrator's documentation as remembered; the raw code is
  what the setting holds;
- that `fragpipe-ui.cache` holds `database.db-path` under that name (it holds
  the form fields; the Database tab's field is expected to be that key) and
  that the PC's copy sits in `<install>\cache\` (the 2026-09-15 inventory saw
  one in the 22.0 zip's `fragpipe\cache\`);
- FragPipe's FASTA name pattern beyond the one real example
  (`2026-01-30-decoys-contam-UP000005640_9606.fasta.fas`); Agent B's
  `docs/FASTA.md` checks Philosopher's.

## Not verified

Everything here ran on a fake PC in `tests/test_library.py` (real folder and
file names from the 2026-09-15 inventory, workflows abridged from the stock
ones). Not yet on the lab PC: how long a scan of `C:\` roots and of `D:\`
takes, how many of the ~150 run folders still hold readable tables, whether
the window's cache names the last FASTA, and junctions on the real drives.

## The app

The Methods tab has "Find on this PC…": the scan runs off the Tk thread with
progress and a Stop button, then a table of candidates per method (kind,
summary, flags, why it is recommended) with Use / Adopt; pickers beside the
workflow and FASTA boxes; a FASTA panel with "Check for updates"; and on first
setup "Ionomos found … use the recommended ones?" (`auto_assign`, then Apply
writes `config.yaml` through `configio`). The public functions above are the
whole interface it uses: `scan`, `load_catalog`, `save_catalog`, `recommend`,
`recommend_fastas`, `auto_assign`, `apply_proposals`, `adopt`, `adopt_fasta`,
`update_check`, `describe_workflow`, `describe_fasta`, `default_roots`.

### What the app does (`library_tab.py`)

The Methods tab has a panel **Workflows and FASTAs on this PC** under the method
form. **Find on this PC…** opens a window that reads the last catalog (so it
opens at once) or, when there is none, scans with a progress line and a
**Stop** button; **Rescan** looks again. A scan stopped early keeps what the
last one found that it did not look at (`library.save_scan`, as the CLI). The table lists, for the selected method's kind, the
candidates `recommend` and `recommend_fastas` rank (kind, name, folder,
summary, marks ✗ ! ·, why recommended); **Show all** adds every other file
(other kinds, files with an error, copies), a filter box narrows by words.
The details pane shows the summary, the settings, what its search left and
every flag with its fix. **Use for <method>** copies the file in (`adopt` /
`adopt_fasta`, on a worker thread) under a name the saved config does not use
yet, puts it in the method's box and applies the method; **Save** keeps it. A
workflow of another kind or a file with a ✗ mark asks first (*Use it anyway?*,
default No), with each reason and its fix. **Open folder** opens the file's folder. A
**Choose…** button beside the workflow and the FASTA box opens the same window
showing only workflows or only FASTAs. Under the buttons, the method's FASTA
is described in words (`describe_fasta`, off the Tk thread, a newer choice
cancels an older reading). **Download from UniProt…** (organism → proteome →
reviewed / isoforms → `download_proteome` + `prepare_for_fragpipe` into
`fasta_dir`, with progress and Stop; refused when `fasta_dir` has a space) and
**Add decoys and contaminants…** (a FASTA you pick → a new file in
`fasta_dir`, the source untouched) end with the new file's path, its counts and
the notes, and **Use for <method>**. **Check for updates** shows `update_check`
and each method's FASTA against UniProt's release (`staleness`) in a text
window and changes nothing. When the Methods tab is shown and a FragPipe
method's workflow or FASTA is not set, not in its folder, or carries an error
flag in the catalog (a pinned workflow the catalog doesn't know is read; a
FASTA is not), a banner says "Ionomos found N workflows and M FASTAs on this
PC …" with **Review proposals…**: the `auto_assign` proposals with their
reasons and flags, from the catalog when it is under a day old, else after a
scan in the same window. Only **Apply** changes anything: `apply_proposals`
copies the files, then the app's own Save checks the config with the loader
and writes it through `configio.write_config`, which backs the old one up.
The ✓ Setup checklist has a line "Workflows and FASTAs: N pinned, flags: …"
from the saved catalog, judged again in memory (no scan, no FASTA read).
