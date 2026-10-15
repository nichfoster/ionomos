# Lab workflows → headless FragPipe

What each method needs, distilled from the lab SOPs in
[`reference/lab-sops/`](../reference/lab-sops/), the R scripts in
[`reference/lab-scripts/`](../reference/lab-scripts/), and the FragPipe output
folders seen in the inventory. This is what the runners have to reproduce.

## Common: headless FragPipe

Checked on 2026-10-01 against FragPipe's
[headless tutorial](https://fragpipe.nesvilab.org/docs/tutorial_headless.html)
and the source of FragPipe 24.0, compared with 23.1 where it could differ
(D59). **Not yet seen on the PC**: every line below is from documentation or
source, and [FIRST_REAL_RUN.md](FIRST_REAL_RUN.md) is how it gets confirmed.

```
bin\fragpipe.bat --headless --workflow <wf> --manifest <mf> --workdir <out>
                 [--threads N] [--ram G] [--dry-run]
                 [--config-tools-folder <dir>] [--config-diann <DiaNN.exe>] [--config-python <dir>]
```

- **Launcher.** The 23 / 24 installer puts `bin\fragpipe.bat`,
  `bin\FragPipe-24.0.exe`, `lib\`, `jre\`, `tools\`, `python\`,
  `workflows\` and (after first use) `cache\` under
  `C:\FragPipe\FragPipe-24.0\`. `fragpipe.bat` is the headless launcher. It
  needs `JAVA_HOME` or `java` on PATH; Ionomos sets `JAVA_HOME` to the
  installation's `jre`. The `.exe` is a window program (launch4j) that
  returns at once: Ionomos swaps it for the `.bat` beside it and holds the
  job if there is none. Zip builds up to 22 have `fragpipe\bin\fragpipe.bat`.
- **Options.** The eleven above are all FragPipe knows; any other stops it
  ("Cannot recognize the argument", exit code 1). `--help` alone prints the
  version, OS, Java and .NET lines and the usage, and exits with code 1.
  There is no `--version`. `--ram 0` lets FragPipe decide; `--threads`
  defaults to cores − 1. Command-line values override the workflow's
  `workflow.ram` / `workflow.threads`.
- **`--dry-run`** makes every check of a real run (workflow, files, FASTA,
  decoys, tools, TMT annotation), prints the commands it would run and "It's
  a dry-run, not running the commands.", and exits 0. `ionomos preflight`
  uses it.
- **Tools, DIA-NN, Python.** Without `--config-*`, FragPipe uses what its
  window was last set to (`<install>\cache\fragpipe-ui.cache`); its tutorial
  says a first headless run must name them. DIA-NN falls back to the bundled
  `tools\diann\1.8.2_beta_8\windows\DiaNN.exe`; `--config-diann` must name
  `DiaNN.exe` itself. On Windows FragPipe 24 always uses the `python\`
  folder in its installation (packages are put there by the installer).
  MSFragger, IonQuant and diaTracer jars and MSFragger's `ext\` folder (the
  Thermo / Bruker readers) come from FragPipe's Config tab → Download /
  Update; a commercial `license.dat` beside the installation is passed on.
- **Manifest** = `.fp-manifest`, UTF-8, tab-separated:
  `path \t experiment \t bioreplicate \t data type`. Lines starting `#` or
  `//` are skipped. Data types: `DDA`, `DDA+`, `DIA`, `GPF-DIA`,
  `DIA-Quant`, `DIA-Lib` (anything else is read as `DDA`; an empty type is
  guessed from the path); Ionomos writes `DDA` or `DIA`. Bioreplicate is a
  whole number that fits Java's `int` or empty: **a line whose bioreplicate
  `Integer.parseInt` refuses (a 14-digit Xcalibur stamp, a space) is left
  out** with one ERROR line ("Manifest file contained some badly formatted
  lines") **and the run goes on without it** (`TabWorkflow.manifestLoad`).
  **A file that does not exist stops a headless run** ("Some loaded files
  don't exist. Please double check.", each path, exit code 1;
  `showSkippedFiles`, 23.1 and 24.0; only the window skips it). Ionomos
  checks the raws itself before every run and bounds replicates to 1–999
  (D85). Two files with the same name stop the run.
- **Experiment names** keep only letters, digits and `_`: FragPipe turns
  every other character into `_` (`EJQ-2-027` → `EJQ_2_027`). Output
  folders are `<experiment>_<bioreplicate>\`, and one flat folder when the
  spectral library or DIA-NN runs. Ionomos warns when a name will change.
- The isoDTB / TMT / DIA sections below apply to a method by its kind, not its
  key: a method under another key that is `like:` one of them (or run by
  another engine) is treated as that method throughout (D54,
  NAMING_CONVENTION.md "Other conventions").
- **FASTA.** It is in the `.workflow` (`database.db-path`); stock workflows
  have none. Ionomos writes a per-job copy of the pinned workflow with
  `database.db-path` set to the method's FASTA from `fasta_dir`. If that
  FASTA isn't there, the workflow's own path is used when it exists.
  **Decoys**: headless FragPipe stops unless 40-60 % of the entries start
  with `database.decoy-tag` (`rev_`); files of 1 GB or more are not
  counted. Ionomos holds a job whose FASTA would be refused.
- **No spaces in any path.** FragPipe refuses an output folder or a tools
  path with whitespace.
- **Output folder.** A non-empty one is only a warning to FragPipe; Ionomos
  still gives every attempt a fresh `fragpipe\` and moves the old one aside.
- **What FragPipe prints** (the lines Ionomos reads):

  | Line | When | Ionomos |
  |---|---|---|
  | `N commands to execute:` then every step's name and command | before the run | the total for "4 of 31 step(s) done"; not counted as steps starting |
  | `MSFragger [Work dir: C:\…]` | a step starts (steps like `CheckCentroid` have no folder) | the progress line |
  | `Process 'MSFragger' finished, exit code: 0` | a step ends | steps done; a non-zero code fails the job and names the step |
  | `Process returned non-zero exit code, stopping` / `Cancelling N remaining tasks` | after a failed step | failed, also when the launcher exits 0 |
  | `2026-10-01 14:03:11,532 ERROR - <message>` | a check failed before any step (a window dialog keeps its `<b>` tags and loses its line breaks: `SwingUtils.stripHtml`) | quoted in the reason; explained in plain words |
  | `=====…ALL JOBS DONE IN 12.3 MINUTES=====…` | every step ran | missing = a warning on a done job (D59) |

- **Exit code**: 0 when every step ran; the failing step's code otherwise;
  1 when a check fails before any step.
- **Files FragPipe writes itself** in the output folder: `fragpipe.workflow`
  (the settings used), `fragpipe-files.fp-manifest`, `fragpipe.job` (a
  tab-separated batch table), `log_<date>_<time>.txt` (its console in
  UTF-8, good run or bad; an empty one when a check failed after the folder
  existed), `experiment_annotation.tsv` (when IonQuant or DIA-NN is set up,
  before the first step), `sdrf.tsv` (when the workflow has
  `workflow.misc.save-sdrf=true`, as the stock ones do), `filelist_*.txt`,
  `modmasses_ionquant.txt`. A dry run writes the manifest, `fragpipe.workflow`
  and `fragpipe.job` too. MSFragger writes `<raw>_uncalibrated.mzML`
  **beside each raw file**, in the experiment folder (the PC's inventory),
  and DIA-NN is given those. Good provenance, keep them. ⚠ The analysis
  currently reads that `sdrf.tsv` as the experiment's own design: open in
  ROADMAP.
- **The console** on the PC is in the Windows code page (cp1252: Java 17
  without `-Dfile.encoding`), `?` for a letter it lacks, CRLF at the end of
  each line; the `log_*.txt` FragPipe saves is UTF-8.
- **After every search** Ionomos writes `ionomos_run\run_fingerprint.json`:
  the command line, FragPipe's version block, key settings, output file
  names and sizes, the ends of the console log and what the parsers read.

## isoDTB (best-defined; build first)

**SOP summary** (`How-to-FragPipe-isoDTB`):
1. Load raws, define experiment name and replicates. Files come as
   `<prefix>_<rep>_<fraction>.raw`, 3 reps × 7 fractions typical.
2. Check database = human whole proteome.
3. Quantification tab: **Match Between Runs (MBR) checked**.
4. Empty output dir. ~30–60 min for 3×7.
5. Run the R script to group peptides → labelled sites.

**Workflow file**: a stock `isoDTB-ABPP.workflow` ships with FragPipe (seen in
the 22.0 bundle). The lab may have tweaked it (MBR, mods). Action: export the
lab's actual working workflow from the 24.0 GUI and pin it as
`C:\Fragpipe_Auto\workflows\isoDTB.workflow`.

**Manifest**: one line per raw. experiment = `<prefix>`, bioreplicate = `<rep>`,
type `DDA`. Fractions share experiment+rep and FragPipe merges them. The
existing run `20260902-isoDTB_EJQ-2-027` used experiment `EJQ_2_027`,
bioreps 1–3 — output subfolders `EJQ_2_027_1/`, `_2/`, `_3/`.

**Key output**: `<workdir>\combined_modified_peptide_label_quant.tsv`.

**Post-processing** (`isoDTB_Fragpipe_merge-individual-peptides-to-Site.R`):
- Reads `combined_modified_peptide_label_quant.tsv`.
- Finds every `[561.3387]` (the isoDTB light-label mass) in `Light Modified Peptide`.
- Residue position in protein = `Start` + (letters before the bracket) − 1.
- Joins protein metadata + all `<sample_prefix>_<n> Log2 Ratio HL` columns.
- Groups by (Protein, ProteinID, EntryName, Gene, Description, Residue, Position);
  summarises PeptideCount, ExamplePeptides, per-replicate mean ratio, overall mean.
- Writes `<…>_output.tsv`.
- Inputs we must supply: `input_tsv`, `output_tsv`, `sample_prefix` (== FragPipe
  experiment name), `mod_mass` (config, default `561.3387`).
- **Ported** (`ionomos/downstream/isodtb.py`, 2026-09-23). The output is
  byte-identical to the R script on a deliberately awkward test table
  (`ionomos/tests/golden/`), run with the real script. The sample prefix is
  read from the column names instead of typed in, so several samples in one
  folder each get a table.
- ⚠ Quirk kept for identical output — worth a look by the lab: the script counts
  *every letter* before the label, so an N-terminal mod written `n[42.0106]`
  shifts that site's position by one. Easy to change once confirmed.

## TMT

How a TMT experiment goes from the SOP (`How-to-FragPipe-TMT`) to a headless
search and a report (D91). Checked on 2026-10-14 against the source of
FragPipe 24.0 (`CmdTmtIntegrator`, `TmtiPanel`, `QuantLabel`, `TmtiConfProps`),
TMT-Integrator's source (`PsmPreProcessor`, `tmt-i_param.yml`), FragPipe's
stock `TMT10-MS3`, `TMT16`, `TMT10-bridge` and `TMT18-Astral` workflows, the
FragPipe output guide and ProteoWizard's filter documentation. **Not yet seen
on the PC**: no TMT search has run there under Ionomos
([REAL_RUNS.md](REAL_RUNS.md)); the list at the end of this section says what
the first one must confirm.

### The SOP, step by step

| SOP | Headless | Ionomos |
|---|---|---|
| "Select Peak Picking & zero Samples … file conversion ~15 min" | not a FragPipe step: MSConvert (below) | not done; FragPipe 24 reads `.raw` |
| Load the **TMT-10 MS3** workflow | `--workflow` | the method's pinned workflow (`TMT10-MS3.workflow`); `experiment.yaml` `workflow:` picks another (e.g. `TMT16`) |
| "Just load the folder" | `--manifest`, one line per raw file | the manifest from the drop: experiment = plex, `DDA` |
| Bioreplicate = 1 when the replicates are in the channels | manifest column 3 | always 1 for TMT (`naming.py`); another number is a warning (`TMT_BIOREPLICATE`) |
| Select the tag set | `tmtintegrator.channel_num` | `tmt: tag:` must equal the workflow's label type, else the job fails before the search (`TMT_TAG_MISMATCH`) |
| Define the samples; keep 126, 127N … in each name; save the annotation in the raw-file folder | one `*annotation.txt` per plex folder | written from `experiment.yaml` `tmt:` in the kit's order beside each plex's raw files, or the user's own file read as it is (never changed) |
| Run | `fragpipe.bat --headless …` | the worker |
| Run the R annotation script; FragPipe-Analyst on `abundance_gene_MD.tsv` + `experimental_annotation.tsv` | — | `downstream/tmt.py` (the R script, byte-identical), the analysis, `results/report.html`, the FragPipe-Analyst export in `results/fragpipe-analyst/` |
| "Filter for 100 % non-missing values" | — | `analysis.filter_global_pct` / `filter_condition_pct`; set 100 for the SOP's filter |

### The pre-step: MSConvert "Peak Picking" and "Zero Samples"

The SOP's first screenshots are **MSConvertGUI** (ProteoWizard), not a Thermo
tool: two filters picked from its filter drop-down, then the `.raw` files
converted to mzML (~15 min). Confirmed against ProteoWizard's filter
documentation (proteowizard.sourceforge.io/tools/filters.html):

- `peakPicking [vendor|cwt] msLevel=<levels>`: centroids the spectra (vendor:
  Thermo's own algorithm, which must be the first filter);
- `zeroSamples removeExtra [<levels>]`: drops runs of zero-intensity points
  from profile spectra (it makes files smaller; it changes no peak).

**Most likely legacy.** FragPipe 24 reads Thermo `.raw` itself (MSFragger and
IonQuant through the Thermo library in MSFragger's `ext\` folder;
`CmdTmtIntegrator.SUPPORTED_FORMATS` is `mzML`, `raw`, `d`), and the DIA
searches on the PC ran on `.raw` directly ([REAL_RUNS.md](REAL_RUNS.md)). The
Eclipse's TMT methods most likely record MS2 / MS3 centroided already, and then
peak picking changes nothing (assumed: the raw files will tell). Ionomos does **not** convert, and searches the
`.raw` files. A drop of only `.mzML` files is not taken in today (intake
looks for `.raw`; open in ROADMAP). **To test on the first real TMT run**:
search the same raws once as `.raw` (the Ionomos job) and once as MSConvert
mzML (the SOP, by hand in FragPipe's window), and compare PSMs, proteins and
`abundance_gene_MD.tsv`; if they agree, the step can leave the SOP.

### Label types (`tmtkit.LABEL_TYPES`, FragPipe's `QuantLabel`)

| Label type (`channel_num`) | Kit | Channels, in order | Mass on K and N-term |
|---|---|---|---|
| `TMT-6` | TMT 6-plex | 126 127N 128C 129N 130C 131N | 229.1629 |
| `TMT-10` | TMT 10-plex | 126 127N 127C 128N 128C 129N 129C 130N 130C 131N | 229.1629 |
| `TMT-11` | TMT 11-plex | TMT-10 + 131C | 229.1629 |
| `TMT-16` | TMTpro 16-plex | TMT-11 + 132N 132C 133N 133C 134N | 304.2071 |
| `TMT-18` | TMTpro 18-plex | TMT-16 + 134C 135N | 304.2071 |

`tag:` accepts any common spelling (`TMT10`, `tmt-10`, `TMTpro`, `TMTpro-18`,
Sage's `Tmt16`). FragPipe also knows TMT-0, TMT-2, TMT-35, iodoTMT, iTRAQ,
DiLeu and others; Ionomos passes a map for those on unchecked
(`TMT_LABEL_UNKNOWN`). Masses are the stock workflows' (229.16293, 304.20715).

### The channel map: where it comes from

1. **`experiment.yaml` `tmt:`** (NAMING_CONVENTION.md): `channels:` for one
   plex (or the same names for every plex), `plexes:` for several. Ionomos
   writes `annotation.txt` from it, in the kit's order, beside each plex's
   raw files (the drop's top level, `raw\`, or `<plex>\`).
2. **The user's own `*annotation.txt`** in the folder with a plex's raw files
   (made in FragPipe's window, as the SOP does). FragPipe uses it, so Ionomos
   reads it (`tmtkit.channel_map_from_folder`), checks it with the same rules,
   records it in `ionomos.json` (`plan.tmt`) and writes nothing beside it; a
   differing `experiment.yaml` map is a warning (`TMT_ANNOTATION_DIFFERS`):
   the file wins.
3. **Nothing**: FragPipe names the channels `<plex>_<channel>`; the job is
   searched with a warning (`TMT_NO_MAP`), and the report's conditions are
   unknown (`TMT_CONDITIONS_UNKNOWN`) until they are set on the Analysis tab.

`ionomos tmt plan <folder>` shows the result for a drop or a filed
experiment without changing anything; `ionomos tmt suggest --tag TMT-16
--conditions DMSO,Drug --replicates 3 --reference 126` prints a `tmt:` block;
`ionomos tmt annotation <folder> --write` writes the files (never over one).

### FragPipe's rules and Ionomos' checks

What FragPipe 24 stops on (`CmdTmtIntegrator.configure`,
`TmtiPanel.parseTmtAnnotationFile`), each a `JobError` before the search with
the rule in words (D59 item 4):

| Code | Rule |
|---|---|
| `TMT_BAD_LINE` | a line is a channel and one sample name, split on whitespace ("Invalid line in annotation file"); an empty name is refused, `NA` leaves a channel out |
| `TMT_CHANNEL_COUNT` | as many lines as the label type has channels |
| `TMT_DUPLICATE_NAME` | a name (not `NA`) once across all plexes, case-sensitive |
| `TMT_NO_REFERENCE`, `TMT_REF_TAG_EMPTY` | with *Define reference: Reference sample* (`tmtintegrator.add_Ref=-1`) the *Ref sample tag* (`ref_tag`) is set and every file has a name containing it (`String.contains`) |
| `TMT_SHARED_FILE` | one annotation file in a folder shared by several plexes is used for all of them, so the names repeat |

Ionomos' own (`tmtkit.validate` / `plan`): errors `TMT_TAG_MISMATCH` (the
`tag:` is not the workflow's label type: change one; the message says which),
`TMT_UNKNOWN_CHANNEL` and `TMT_CHANNEL_TWICE` (FragPipe's window refuses such
a file; headless does not check, but the names would land on the wrong
reporters), `TMT_BLOCK` (an unknown key or tag in `tmt:`); warnings
`TMT_NAME_CHANNEL` (`127N: DMSO_1_126`), `TMT_ONE_CHANNEL` (a condition with
one channel: compared anyway, low confidence, D32), `TMT_REFERENCE_MISSING`
(several plexes, and one has no sample, or not its pool, on the reference
channel), `TMT_FRACTIONS_DIFFER`, `TMT_BIOREPLICATE`, `TMT_ALL_NA`,
`TMT_CHANNEL_ORDER` (a user's file not in the kit's order),
`TMT_SEVERAL_FILES`, `TMT_ANNOTATION_DIFFERS`, `TMT_NO_MAP`,
`TMT_SHARED_FOLDER`, `TMT_LABEL_UNSET`, `TMT_LABEL_UNKNOWN`; notes
`TMT_NAME_PATTERN` (not `condition_1_channel`: the lab's R script skips the
name, the report takes the text before the first `_`) and `TMT_NO_BRIDGE`.
Intake records all of them in `ionomos.json` (`plan.tmt`, and the warnings in
`plan.warnings`); nothing a TMT check finds refuses a drop.

### Plexes and folders

A plex is one FragPipe experiment (the raw files' `<sample>` before
`_TMT_F<n>`); its fractions are combined. FragPipe takes a plex's annotation
from the one folder holding all its files and only when exactly one file
ending in `annotation.txt` is there, so several plexes need a folder each
(`<plex>\*.raw`, D69): each gets its own `annotation.txt`. Plexes sharing a
folder are searched with FragPipe's own channel names and a warning.

### Reference channels

Two different things:

- **FragPipe's** (TMT-Integrator): with *Define reference: Virtual*
  (`add_Ref=1`, the stock workflows) every ratio is to the mean of the plex's
  channels; with *Reference sample* (`add_Ref=-1`, the stock `TMT10-bridge`)
  to the channel whose name contains `ref_tag` (`Bridge`, `pool`).
- **Ionomos'** (IRS, D48): `tmt.reference_channel` / a plex's own
  `reference:` / `analysis.tmt_reference` name the bridge channel that joins
  plexes. TMT-Integrator's tables are already ratios to a reference, so
  Ionomos never applies IRS to them again; it does for MaxQuant, Sage,
  Proteome Discoverer and MSstatsTMT input (ENGINES.md). The TMT QC uses the
  reference to check that the bridge sits near the other channels.

**How a reference channel is treated in the analysis.** A bridge / pooled
channel is never a treatment, so its condition takes the existing
*reference* role (`roles.py`, D61/D65/D88) and is left out of the
comparisons: there is no "Pool vs DMSO". It is used only to join the plexes
(IRS, which then drops it from the data) and by the TMT QC. The role comes
from `analysis.roles` (the channel editor writes `{Pool: reference}` for a
channel marked *reference (bridge, pooled)*), else from the `tmt:` map
(`reference_channel` / a plex's `reference:`, or a name with a pool / bridge
word or the workflow's ref tag: `tmtkit.reference_conditions`, applied by
`postprocess.prepare` unless a person gave that condition another role), else
from the name in TMT data (`roles.kept_apart`). It holds also when
`analysis.control` is set (the Analysis tab always writes it) and in a
competition design; only an explicit `comparisons:` list can still name it.
A condition that is also on sample channels (DMSO on 126 in one plex) is not
made a reference. In other data a pool read from its name alone keeps D61's
rule (left out of a competition design's comparisons only).

### The channel editor (`tmt_editor.py`)

The review window (for a TMT drop, under the file table) and the Analysis
tab's experiment editor (for a TMT experiment) show a grid per plex: channel,
sample name, condition, replicate (counted: the k-th channel of a condition
across the plexes) and role (sample, reference (bridge, pooled), NA). The
label type is pre-set from the method's workflow (`tmtintegrator.channel_num`)
or `experiment.yaml`'s `tag:`; another choice is said in orange (the job
would fail before the search). Typing a condition names the channel
`<condition>_<plex>_<channel>` (the plex's position, as `tmtkit.suggest_channels`);
a name typed by hand is kept and its condition is the text before the first
`_`. **Read annotation.txt from the folder** reads a person's own files
(never changed); **Suggest…** asks for the conditions, replicates per
condition and a reference channel; **Clear** empties the grid. Under the grid
are `tmtkit.validate`'s own texts (what FragPipe would stop on in red) and a
summary in words. The map is written as `experiment.yaml` `tmt:` only by the
window's own Accept (review; the drop's experiment.yaml) or Save choices /
Run analysis (editor; the old file kept in `experiment-backups/`), together
with `analysis.roles` for the reference channels. In the review window a map
FragPipe would stop on keeps the window open with the reason (Clear to
decide later); a cleared grid removes an old `tmt:` block. For a filed
experiment the map is used by the next search (Retry); this analysis reads the
sample names in TMT-Integrator's tables, so a condition in it is changed in
the sample list on the left.

### After the search: the TMT QC (`downstream/tmtqc.py`)

Read from each plex's `psm.tsv` (TMT-Integrator's own input; columns
confirmed from its `PsmPreProcessor`): `Peptide`, `Assigned Modifications`
(`N-term(229.1629), 7K(229.1629), 3C(57.0215)`), `Purity`, `Intensity` and the
reporter block, one column per channel of the label type in the kit's order,
named `Intensity <sample name>` (`Intensity NA` for an unused channel; older
Philosopher tables used the bare sample name, which is also read). Then
`results/tmt_qc.json`, `results/tmt_qc_<plex>.tsv`, `analysis.json` →
`tmt_qc`, and the report section **TMT plexes and channels** (a channel chart
per plex, labelling bars, a purity histogram; every figure exports).

| Check | Measure | Issue (warning) and limit |
|---|---|---|
| Channels | PSMs with signal, log2 quantiles, median log2 to the PSM's median over the named channels | `TMT_CHANNEL_EMPTY`: a named channel with signal in < 50 % of PSMs or ≤ 10-fold below |
| Reference | the same for the reference channel(s) | `TMT_REFERENCE_WEAK`: more than 2-fold from the median |
| Labelling | N-termini labelled (acetyl / pyro-Glu excepted), K labelled, PSMs fully labelled, S/T/Y over-labelling | `TMT_LOW_LABELLING`: fully labelled < `analysis.tmt_min_labelling` (0.95). With TMT on K fixed (the stock workflows) every identified K carries it: only the N-terminus is a real measurement, and the report says so |
| Purity | histogram, median per plex, share below `tmtintegrator.min_purity` | `TMT_LOW_PURITY`: a plex's median < `analysis.tmt_min_purity` (0.7); MS2 vs MS3 from `tmtintegrator.quant_level` |
| Tables vs map | reporter columns vs label type, names vs the map | `TMT_LABEL_TYPE_MISMATCH` |
| Names | `<plex>_<channel>` or no `_` | `TMT_CONDITIONS_UNKNOWN` |

A plex needs 50 PSMs before its labelling or purity is judged. The limits
are Ionomos' own, set wide; open for the lab (ROADMAP). Without `psm.tsv` (a
TMT-Integrator table dropped in by hand) only the `tmt-report` header is
checked. The QC runs for TMT kinds only, also when `analysis.enabled` is off
(then without the report).

### What the analysis reads

TMT-Integrator's `tmt-report/` (`quant.TMT_TABLES`, in this order):
`abundance_gene_MD`, `abundance_protein_MD`, then `_GN`, then `_None` (not
normalised: Ionomos' normalisation is the only one), then `ratio_gene_*` /
`ratio_protein_*` (log2 ratios to the reference, when no abundance table is
there), then any other abundance table. Layout (FragPipe's output guide):
`Index, NumberPSM, ProteinID | Gene, MaxPepProb, ReferenceIntensity`, a
column per sample. The sample names are the annotation's; conditions come
from the R script's `experimental_annotation.tsv` when the names follow
`condition_1_channel`, else the text before the first `_`. Peptide tables are
not analysed; single-site tables go through `analysis.phospho` (ENGINES.md).

### Confirmed and assumed

- **Confirmed from source / documentation**: the rules above; the label types
  and their channel order; `add_Ref` −1 = reference sample, 1 = virtual;
  `prot_norm` 0 None / 1 MD / 2 GN / −1 all; `groupby` 0 gene, 1 protein,
  2 peptide, 3 multi-site, 4 single-site, −1 all; `quant_level` 2 / 3; the
  stock workflows' `min_purity` 0.5, TMT on K fixed and on the N-terminus
  variable; the `Intensity <sample>` reporter columns; the `tmt-report` file
  names and column layouts; MSConvert's two filters.
- **Assumed** (from the fake and the source, not a PC run): that FragPipe 24
  on the PC writes exactly these `psm.tsv` columns with IonQuant as the
  extraction tool; that `Assigned Modifications` lists the fixed K mod; that
  the headless run writes `abundance_*_None.tsv` / `ratio_*` beside `_MD`
  with the stock `prot_norm=1`; that the `.raw` search equals the mzML one.

### Verified against the fake, not yet on the PC

The testbed's fake FragPipe writes the TMT `psm.tsv` (reporter columns,
Purity, the labels, a share unlabelled with mode `tmt-poor-labelling`;
`tmt-low-purity`, `tmt-empty-channel`) and the `tmt-report` set (gene and
protein abundance and ratio, `_None`, peptide; single-site when the workflow
searches phospho), and stops as FragPipe does on a missing reference tag.
Scenarios: `tmt_good`, `tmt_plexes`, `tmt_flat_plexes`, `tmt16_na` (TMTpro,
NA channels), `tmt_tag_mismatch`, `tmt_own_annotation`, `tmt_bridge`,
`tmt_poor_label` (`tests/test_tmtkit.py`, `tests/test_tmtqc.py`). **The first
real TMT run must confirm**: the `psm.tsv` header (reporter columns and their
order, `Purity`), that FragPipe found Ionomos' `annotation.txt` (the names in
`abundance_gene_MD.tsv`), the labelling and purity numbers against what the
lab expects, the `.raw` versus mzML comparison above, and how long a 10-plex
with 8 fractions takes (`timeout_minutes`).

**Post-processing** (`correct_experimental_annotation_for_Fragpipe-TMT.R`):
- Reads only the header of `abundance_gene_MD.tsv`.
- Keeps columns matching `^[A-Za-z0-9]+_1_\d{3}[A-Z]?$` (condition_plex_channel).
- Builds table: plex, channel, sample, sample_name (drops `_1_`), condition
  (prefix before first `_`), replicate (row number within condition).
- Writes `experimental_annotation.tsv`.
- Note the regex assumes plex index `1` and a single-token condition. Real
  headers look like `DMSO_1_126`.
- **Ported** (`ionomos/downstream/tmt.py`), byte-identical to the R script.
  When names don't follow the pattern the file is empty (as in R); the report
  then takes the condition from the text before the first `_` and says so.

## DIA

No lab SOP yet. From the inventory: DIA runs exist for Chris, EJQ, Isaac
(`Fragpipe-DIANN\`, `FRAGPIPE-DIANN\` folders, some with `_RERUN`,
`_incl-Peptide` variants — i.e. people iterate on DIA settings).

**Workflow file**: stock `DIA_SpecLib_Quant` or `DIA_DIA-Umpire_SpecLib_Quant`.
Ask which the lab uses. Pin it.

**Manifest**: experiment = sample prefix, bioreplicate = rep, type **`DIA`**.

**DIA-NN**: FragPipe bundles DIA-NN (1.8.x, license-restricted) and 24.0 may
require pointing at an external `DiaNN.exe` for 2.x. DIA-NN 2.3.2 is installed
on the PC. Try the bundled one first; if the headless run complains, set
`fragpipe.config_diann` in config.

**Key output**: `<workdir>\dia-quant-output\report.tsv` (+
`report.pg_matrix.tsv`, `report.stats.tsv`) in FragPipe 24; earlier versions
wrote `diann-output\`. With DIA-NN 2.x (the PC's 2.3.2) DIA-NN writes
`report.parquet` and FragPipe's step "DIA-Quant convert Parquet to Tsv"
makes `report.tsv` from it (`CmdDiann`); the run columns are the
`<raw>_uncalibrated.mzML` files MSFragger wrote beside the raws. With a spectral library or DIA-NN in the workflow
there are no per-experiment folders. ⚠ The stock workflow's own notes say
"For quantification using DIA-NN, Thermo/Sciex DIA files should be in mzML
format": whether `.raw` works on the PC is for the first DIA run to show.

**Post-processing**: none known. Leave `postprocess: []` and ask users what
they do next (FragPipe Analyst upload?).

## What to collect from the lab to finish this doc

- [ ] The three pinned `.workflow` files exported from the FragPipe 24.0 GUI
      (with FASTA set), and the FASTA files they reference.
- [ ] One complete, successful output folder per method (copy of the small
      files only — `*.tsv`, `*.workflow`, `*.fp-manifest`, `annotation.txt`,
      `log*.txt`) to develop parsers/post-proc against.
- [ ] One R-script output per method to diff the Python port against.
- [x] Answer: what is the TMT "Peak Picking & zero Samples" pre-step tool? →
      MSConvertGUI (ProteoWizard), raw → mzML; most likely legacy (TMT section above).
- [ ] Does the lab still convert TMT raws with MSConvert, and does a `.raw`
      search give the same result (the first real TMT run)?
- [x] Answer: how does FragPipe 24.0 headless locate `annotation.txt`? → the one
      `*annotation.txt` in the plex's folder (TMT section above).
- [ ] The `run_fingerprint.json` of the first real search of each method.

## Downstream (all methods): statistics, volcano plots, report

After FragPipe, `ionomos/downstream/` turns each method's main table into one
features × samples matrix of log2 values and runs the same statistics:

| Method | Table read | Level | Test |
|---|---|---|---|
| isoDTB | `<prefix>_sites.tsv` (from the R port) | site | each sample's replicate log2 H/L vs 0 |
| DIA | DIA-NN `*pg_matrix.tsv` (runs mapped to conditions via the manifest) | protein | condition vs control |
| TMT | `tmt-report/abundance_gene_MD.tsv` (+ R-port annotation) | gene | condition vs control |
| label-free DDA (future) | `combined_protein.tsv` (MaxLFQ if present) | protein | condition vs control |
| any of these with `phospho: true` (D79) | the search's phosphosite table: IonQuant `combined_site_STY_79.9663.tsv`, TMT-Integrator `abundance_single-site_MD.tsv`, DIA-NN `report.phosphosites_90.tsv` | site | condition vs control |

- **Phosphoproteomics (opt-in, D79).** For a phospho search, set
  `phospho: true` under `analysis:` (usually in `experiment.yaml`): the site
  table is analysed instead of the proteins, after a localisation filter
  (`phospho_min_localization`, 0.75). In FragPipe this needs **PSM site
  localisation (PTMProphet)** in the Validation tab and the site reports
  (IonQuant writes `combined_site_STY_79.9663.tsv` only with localisation on;
  TMT-Integrator filters single sites with its own `min_site_prob`). Optional
  downloads the lab keeps itself: `kinase_substrates` (PhosphoSitePlus
  `Kinase_Substrate_Dataset`) for kinase activity (KSEA), `string_network`
  (STRING) for partners among the hits, and `protein_correction` for the
  unenriched proteome of the same treatment.

- **Pipeline:** FragPipe-Analyst's, ported from FragPipeAnalystR (D24) and
  checked against the real package: contaminants removed (`contam_` in the name, or a
  `contam_` entry of the search's FASTA, D87) → features kept when
  measured in ≥ 50 % of at least one condition (`filter_condition_pct`;
  FragPipe-Analyst: 0) → normalisation (`normalize`: auto | median | gn | ratio | none; see below) →
  imputation (`imputation: auto` = Perseus-type down-shifted draws for DIA and
  label-free, none for TMT; also none | min | zero | mindet | minprob | knn; a
  sample without a single value is not imputed and stays missing, with a note) →
  limma (`~0 + condition`, eBayes, 95 % CIs) → Benjamini–Hochberg. Hits need
  |log2FC| ≥ `log2fc` *and* adjusted p ≤ `alpha` (inclusive, as `add_rejections`).
  Welch and Student remain selectable (`test`).
- **Comparisons:** `de_type: control` (each condition vs the recognised
  control, default), `all` (every pair, control as reference), `others` (each
  vs the rest), or an explicit `comparisons:` list (two different conditions
  each: "Drug vs Drug" is refused). isoDTB: each condition's
  ratios vs 0 (moderated one-sample test).
- **Roles** (`downstream/roles.py`, D61). Each condition is a control, a
  compound, a competition (probe plus competitor), a pooled reference, a
  QC standard or a pull-down's background (negative control, D88), read from
  its name, from `analysis.roles`, or from an SDRF's
  `characteristics[role]` column. With a competition condition the default
  comparisons are compound vs control, competition vs its compound and
  competition vs control (see "Competition experiments" below). isoDTB is
  a competition experiment by construction and is unchanged. The experiment
  editor and the review window show and change the roles (D65, below).
- **Per experiment** (Analysis tab or `experiment.yaml analysis:`):
  `sample_conditions: {Drug_4: DMSO}`, `exclude_samples: [DMSO_3]`,
  comparisons and any setting above.
- **Enrichment:** each comparison's up and down hits vs the quantified genes,
  hypergeometric test, BH; Enrichr libraries (Hallmark, GO BP/MF/CC, KEGG,
  Reactome, WikiPathways) downloaded once, or a lab `.gmt` (D26).
- **Outputs** (`results/`): `report.html` (interactive, self-contained, works
  offline), `<comparison>_differential.tsv`, `<level>_results.tsv` (all
  comparisons + values used), `<level>_matrix_log2.tsv` (as loaded),
  `<level>_matrix_processed.tsv` (filtered/normalised/imputed + which values
  were imputed), `enrichment.tsv`, `volcano_<comparison>.svg`,
  `fragpipe-analyst/` (annotation + `reproduce_in_R.R`), `analysis.json`
  (settings and every processing step), and `figures/` when asked for (below).

- **Figures for slides** (D62). In the report, every chart has **SVG**,
  **PNG** and **Export…** (size, text, colours, title, legend; copy as an
  image), and **Export for slides** saves every figure, the tables and a
  README in one .zip. Without the report:

  ```
  ionomos export <experiment or results folder>            # results/figures/*.svg + README.txt
  ionomos export 12 --preset col1 --palette colorblind     # job 12, one journal column, colour-blind safe
  ionomos export <folder> --style export_style.json --figures volcano,pca --out D:/talk
  ```

  It draws the volcano of each comparison, the PCA, the heatmap of the hits
  and the sample correlation from the finished report's data, at the saved
  cut-offs. SVG only (PNG: the report's Export). The lab's style is
  `analysis.export` in `config.yaml` (an experiment can change it in
  `experiment.yaml`): `size` (`slide169` | `slide43` | `half` | `col1` |
  `col2` | `custom` with `width`, `height`, `unit`), `font_pt`,
  `font_family`, `palette` (`default` | `colorblind` | `grey` | `custom` with
  `up`, `down`, `neutral`), `background`, `line_scale`, `point_scale`,
  `title`, `subtitle`, `legend`, `note`, `label_count`, and `figures`: the
  static figures written to `results/figures/` after every analysis (any of
  `volcano`, `pca`, `heatmap`, `correlation`; default none). Style order for
  `ionomos export`: the report's own, the lab's now, `--style`, the flags.
  A figure file of the same name is replaced only if Ionomos wrote it.

- **Small groups** (D32). A comparison is never refused for having too few
  replicates. When a group has one sample, limma still fits the model over
  every sample, so it borrows the variance from the replicated groups (a Welch
  test becomes a pooled t-test). The comparison is labelled **low confidence**
  in the report, on the volcano SVG, in `analysis.json` (`"confidence":
  "low"`) and on the command line. With no replicates anywhere (1 vs 1, a
  single isoDTB replicate), there's nothing to estimate variance from, so the
  plot is **fold change only**. It shows log2FC against mean abundance (or
  rank, for ratio data), and candidates are features with |log2FC| ≥ the
  cut-off. No p-values are invented.

- **Unequal groups** (D61). Two DMSO against four of each compound is a
  normal design and is not flagged. limma estimates each feature's variance
  from all samples of all conditions, so a comparison with a small group
  borrows the variance from the larger ones; its standard error is
  `s·√(1/n₁ + 1/n₂)`. The report gives the samples on each side of every
  comparison (Methods, under the volcano, `analysis.json` → `comparisons[].samples`)
  and the minimum detectable fold change per comparison (Quality control →
  Power; `analysis.json` → `quality.detectable_log2fc`). Without imputation
  (TMT), the smaller group of an unequal comparison needs half its samples
  measured instead of `min_valid` (`small_group_min_valid: half`; `same`
  for the earlier rule); the features this lets in are counted in the notes
  and their n is in the table. A group of one is low confidence as before.

- **Any table** (`downstream/anytable.py`, D33): `ionomos analyze <file>` or
  Analysis tab → **Table…**. TSV, CSV (including `;` with decimal commas),
  TXT and Excel `.xlsx` (first sheet). Two shapes are recognised:
  - Quantities: an ID column plus one numeric column per sample. This covers
    MaxQuant `proteinGroups.txt` (LFQ intensity; `+`-flagged rows dropped),
    Spectronaut `.PG.Quantity`, Proteome Discoverer abundances, FragPipe or
    DIA-NN matrices, and any hand-made sheet. Raw intensities are
    log2-transformed; log2 values are kept. They go through the full pipeline.
  - Results: a fold-change column and a p-value column per comparison, e.g.
    limma topTable, Perseus (`-Log p-value`), DESeq2 (linear FoldChange →
    log2), or a FragPipe-Analyst export with several comparisons. These are
    plotted as given, with the lab's cut-offs; q is BH-computed if missing.

  Output goes to `<table name>_ionomos/` next to the file, never beside it,
  so nothing of the user's is overwritten. With no file given, a folder
  without FragPipe tables is scanned for the best table. Long-format
  intermediates (`psm.tsv`, `report.tsv`, …) and files over 100 MB are
  skipped. A file that is neither shape gets an `UNUSABLE_TABLE` note saying
  what to export.

- **Checks after every analysis** (`downstream/doctor.py`, shown at the top of
  the report; *decide* and *problem* ones also pop up a window with the fix):
  | code | severity | when |
  |---|---|---|
  | NO_TABLE / EMPTY_TABLE | problem | FragPipe produced no (or an empty) result table for the method |
  | UNUSABLE_TABLE | problem | the table exists but holds nothing usable (isoDTB: no probe-labelled peptides / ratio columns) |
  | NO_QUANTITIES | problem | rows but not one measured value in the sample columns |
  | ONE_SAMPLE | problem | a single sample of intensity data was quantified — nothing to compare |
  | NOTHING_LEFT | problem | every sample was left out, or the filters removed every feature |
  | MISSING_RUNS | problem | searched runs with no quantities in the table |
  | UNMATCHED_RUNS | decide | runs not in the experiment's file list (conditions guessed) |
  | DUPLICATE_SAMPLES | decide | two runs with the same sample name (re-acquisitions) |
  | ONE_CONDITION | decide | every sample in one condition — conditions suggested from the file names |
  | NO_CONTROL | decide | no condition looks like a control; the guess is used until confirmed |
  | BAD_COMPARISON | decide | chosen comparisons don't fit; defaults used meanwhile |
  | EACH_OWN_CONDITION | decide | every sample is its own condition (replicates named with letters?) — grouping suggested |
  | SMALL_GROUP | decide | a group is too small and not even a fold change could be computed |
  | ROLES_UNSURE | decide | a condition's role can't be told from its name (`Probe_pre`, `Probe_10x`), or a competition can't be linked to one compound; the guess is used meanwhile (D61) |
  | COMPETITION_DESIGN | note | read as a competition experiment: the roles and comparisons chosen, and how to change them (D61) |
  | PULLDOWN_DESIGN | note | read as a pull-down with a negative control (empty vector, beads, IgG): the comparisons against it, the filter and normalisation that leave it apart, and how to change it (D88) |
  | LOW_CONFIDENCE | note | a group has one sample (or fewer than `min_valid`): tested anyway, p borrowed from the replicated groups (D32) |
  | FOLD_CHANGE_ONLY | note | no replicates anywhere (1 vs 1, one isoDTB replicate, a table without p): fold change only |
  | LOW_SAMPLE | decide | a sample has < 40 % of the median identifications (failed injection?) |
  | ZERO_TESTED / NO_VOLCANO / CRASH_* | problem | nothing testable, plot not written, a step crashed |
  | HIGH_IMPUTATION, FEW_FEATURES, NO_HITS, ENRICHMENT | note | worth knowing |
  | NORMALISATION_COMPOSITION | decide / note | median centring would shift the conditions against each other (many features change one way): asks when `median` / `gn` is chosen, a note when `auto` switched to the ratio method |
  | TMT_PLEXES_NOT_IN_MODEL | note | several TMT plexes, `irs: none` and no block for the plex: the plex effect counts as replicate spread, so the tests miss changes (D71) |
  | TIMES | decide / note | a time course whose time points can't all be read (a name in `analysis.times` that isn't a condition, two times in one name) |
  | TIME_SPLINE | note | a spline time course (D77) not fitted as asked: `time_spline_df` too high for the series' time points (lowered to time points − 2), or the spline model can't be fitted (time is a factor for that series) |
  | LIGANDED_DIRECTION, SITE_ANNOTATION | note | isoDTB: the competition ratio looks reversed; the site annotation file can't be used |
  | RATIO_OFFSET | note | isoDTB: a replicate's ratios sit clearly off 0 on the stable sites (a mixing error?) and `ratio_centre` is none (D70) |
  | PROTEIN_CORRECTION_CONDITIONS / PROTEIN_CORRECTION | decide / note | isoDTB with `protein_correction`: a site condition has no proteome comparison; the proteome is missing or unusable, or few sites find their protein (D70) |
  | PSM_MASS_ERROR, PSM_MISSED_CLEAVAGES | note | a run's median precursor mass error is 10 ppm or more from 0; half or more of a run's PSMs have a missed cleavage |
  | RUN_ORDER_DRIFT, RUN_ORDER_CONFOUNDED | note | a QC number drifts with the order of acquisition (within the conditions, p < 0.01, a material change); the conditions were run in blocks (η² ≥ 0.6) (D78) |
  | NO_RESIDUAL_DF | note | features tested with one value per group: their p-values come from limma's variance prior alone (D60) |
  | VARIANCE_PRIOR | note | limma's variance prior could not be estimated (fewer than 3 features with replicate spread), or its fit did not converge |
  | ZERO_VARIANCE | note | 5 % or more of the tested features, or any hit, have identical replicates in every group (rounded, copied or constant-imputed values) |
  | IDENTICAL_SAMPLES | note | two samples hold exactly the same values (a file or column loaded twice) |

- **Messy tables** (D60, `downstream/guards.py`). A table is read as far as
  it can be, and every repair is a note in the report: duplicate or blank
  IDs (each row stays a feature), a sample column named twice (any table:
  `name`, `name.2`; FragPipe tables: the repeat is dropped), a column without
  a name or without values (left out), text or infinite cells and negative
  intensities (missing), values beyond 2^±100 (missing). In a table given to
  `ionomos analyze`, decimal commas (`1234,5`) and thousands separators
  (`1,234,567.8`) are read in tab and comma files too; `1,234` alone is read
  as 1234 and the note says so. The full list: [VALIDATION.md](VALIDATION.md).

- **How far to trust this** (D60, `downstream/trust.py`). Under the key
  findings of every report, and in `analysis.json` → `trust`: samples per
  group, replicate agreement, missing and imputed values, what each
  comparison tested and found, the p-value histogram shape, power, and any
  guard finding, each with its number. No score. `ionomos compare` (this
  analysis against a reference result) and `ionomos benchmark` (simulated
  data with planted changes, or a mixed-species run with known ratios)
  measure accuracy; their verdicts show in the same list. See
  [VALIDATION.md](VALIDATION.md).

**Search quality per run** (`downstream/psmqc.py`, D55). When the search
wrote `psm.tsv` files (FragPipe DDA: isoDTB, TMT, LFQ), the report's Quality
control section has a **Search quality** tab with one row per raw file:

| Shown | From `psm.tsv` |
|---|---|
| PSMs, peptides, proteins | rows, distinct `Peptide`, distinct `Protein` of the run (the run is read from `Spectrum`) |
| Mass error (ppm) | `Observed Mass` against `Calculated Peptide Mass`, isotope-error corrected: median, quartiles, 5th and 95th percentile. Errors over 50 ppm are mass offsets and are left out |
| Missed cleavage | share of PSMs with `Number of Missed Cleavages` ≥ 1; the chart splits 0 / 1 / 2 or more |
| Charge states | share of PSMs per `Charge` |
| Length | median `Peptide Length`; the chart shows all runs together |

- A run is a raw file. For TMT that is a fraction of a plex; the folder its
  `psm.tsv` is in is shown as the sample.
- With DIA the tab shows DIA-NN's own summary of each run
  (`report.stats.tsv`): precursors, proteins, MS1 / MS2 mass accuracy, mean
  missed cleavages and charge, peak width. Nothing is flagged on it.
- A run with at least 100 PSMs is flagged when its median mass error is
  10 ppm or more from 0 (`PSM_MASS_ERROR`), or when half or more of its PSMs
  have a missed cleavage (`PSM_MISSED_CLEAVAGES`). Both limits are wide and
  are not the lab's: they are constants in `psmqc.py` until the lab has
  looked at its own runs.
- `psm.tsv` files are read row by row. One over 4,096 MB is not read; the
  report notes it.
- Setting: `psm_qc` (false switches it off).
- Output: `results/psm_qc.tsv`, `analysis.json` → `psm_qc`.
- Not tested on real FragPipe output: the column names are from the FragPipe
  documentation.

**Run order** (`downstream/runorder.py`, `acqtime.py`, D78). When the
samples' raw files have an acquisition time, the Quality control section has a
**Run order** tab: each sample's numbers in the order of acquisition.

| Number | From | Flagged when the change over the run is at least |
|---|---|---|
| identifications, missing values | the sample scorecard | 10 % of the median; 5 percentage points |
| median log2 intensity before normalisation | the scorecard (intensity data only) | 0.5 log2 |
| PSMs, median precursor mass error, missed-cleavage rate | Search quality (`psm.tsv`) | 10 %; 3 ppm; 5 points |
| precursors, MS1 mass accuracy | DIA-NN's `stats.tsv` | 10 %; 3 ppm |

- **Time per raw file**: `ionomos.json` → `acquisition` (intake), else read
  now: the Thermo header's acquisition start, ThermoRawFileParser's output,
  the Xcalibur stamp, the file time (approximate; said in the tab and the
  warning). A sample's time is its first fraction's.
- **Drift** (`RUN_ORDER_DRIFT`): a stratified Mann-Kendall test, comparing
  only samples of the same condition (exact p up to 1,500 pairs), p < 0.01,
  and a Theil-Sen change over the run at least the limit above. Needs 6
  samples with a time. Missing values are not warned about beside
  identifications (the same fact).
- **Blocks** (`RUN_ORDER_CONFOUNDED`): the condition explains 60 % or more of
  the run positions (η² of the ranks), with two or more conditions of two or
  more samples.
- TMT channels share their raw files, so a TMT experiment has no run order.
- Output: `analysis.json` → `run_order`, the `run_order` export figure.
- Not checked on real raw files: the header layout is from published format
  notes and the tests build headers to it.

**Competition experiments** (`downstream/roles.py`, D61). A condition whose
name has `comp`, `competition`, `competitor`, `competed`, `compete`,
`competing` or `excess` as a word (`Probe_Comp`, `Probe+Comp`, `ProbeComp`,
`KL6283A_Comp_KL6159A`) is the compound plus a competitor, linked to the
compound named by the rest of it (or the only compound).

| Comparison | Reads as |
|---|---|
| compound vs control | enrichment / engagement |
| competition vs its compound | what the competitor displaces (down = competed off) |
| competition vs control | what is left with the competitor |

- **Specific targets** of a compound: significant *up* in compound vs
  control and significant *down* in competition vs compound, each at the
  report's cut-offs. *Enriched, not competed* is listed beside them.
- Not compared by default: two controls, a pool, a QC standard, one
  compound's competition with another compound.
- `pre`, `pretreat…`, `block…`, `cold` and `10x` count only when the rest
  of the name is another condition (`Probe_pre` next to `Probe`); the
  analysis runs on that reading and asks (`ROLES_UNSURE`).

**Pull-downs with a negative control** (`downstream/roles.py`, D88). An IP
with an empty vector, beads only, IgG or an untagged bait next to the
pull-downs: that condition is the *background*. Its name has `EV`, `empty`,
`emptyvector`, `vector`, `beads`, `IgG`, `isotype`, `untagged`, `nobait` or
`mockIP` as a word (`background_keywords`), **and** its samples miss
clearly more proteins than the others (at least 1.5 times as many and 3
points more, *and* at least 10 proteins measured in every other sample and
none of its). The name alone is not enough: an empty vector is also the
plain control of an over-expression. With the data alone it is only
suggested. When the folder's name says pull-down (`IP`, `coIP`, `pulldown`,
`APMS`, `BioID`, `TurboID`, `APEX`; not a tag such as FLAG or HA, which
over-expressions carry too) but the data don't show it, the condition is
read as the background and compared against, marked unsure, and processed
with the others.

| Comparison | Reads as |
|---|---|
| each pull-down (control, compound, competition) vs the background | enrichment over the negative control (up = pulled down) |
| compound vs control | what the compound changes in the pull-down |

- The background is not compared with the control (`EV vs DMSO` would put
  everything the bait pulls down as *down*).
- When the data agree (or `analysis.roles` says so): the missing-value
  filter across all samples doesn't count the background samples, as most
  proteins are expected to be missing there, and
- normalisation is done within the pull-downs and within the background,
  never one against the other (`fpa.normalize_apart`); `auto`'s composition
  check looks at the pull-downs only.
- Set it yourself with `roles: {EV: background}` (or `negative control`), or
  `roles: {EV: control}` to stop it.

```yaml
analysis:                      # experiment.yaml (one experiment) or config.yaml (lab)
  roles: {DMSO: control, Probe: compound, Probe_Comp: competition of Probe}
  competition_keywords: [comp, competition, competitor, competed, compete, competing, excess]
  competition_keywords_weak: [pre, pretreat, pretreated, pretreatment, block, blocked, blocking, cold]
  background_keywords: [ev, empty, emptyvector, vector, beads, bead, beadsonly, igg, isotype, untagged, nobait, mockip]
  role_comparisons: true       # false: every condition vs the control, as before
```

- `comparisons:`, `de_type: all | others` work as before. With `control:`
  set, every condition is still compared with it and competition vs
  compound is added.
- Output: `results/specific_targets.tsv` (per compound and feature: the
  call, enrichment log2FC / p / adjusted p, competition log2FC / p /
  adjusted p, % competed off, what is left, measured n per group),
  `analysis.json` → `roles`, `specific_targets`, and the report's
  **Specific targets** section (enrichment across, competed off up, the
  quadrant of specific binders marked; the calls follow the live cut-offs).
- Not confirmed by the lab: the keywords and the rule. Not tested on a real
  experiment.

**Roles in the windows** (D65; the logic is `roles.preview`, the Tk code only
draws it). The experiment editor (Analysis tab, or the pop-up after an
analysis) has a **Roles** box; the review window before filing has the same
rows for DIA and label-free drops (TMT conditions come from the channel
annotation, isoDTB is a competition by construction).

- One row per condition: samples (replicates in the review window), role,
  and where it came from (the name and the word that decided, the Control
  choice, the lab's or this experiment's `analysis.roles`, an SDRF).
- A list changes the role: **automatic** (what the name gives), control,
  compound, competition of each compound condition, competition (compound
  from the name), pool / reference, QC standard. Making a condition the
  control also sets the Control box.
- A competition read from a weak word (`pre`, `pretreat…`, `block…`, `cold`,
  `10x`) is marked **?** with **Confirm**; one that can't be linked to a
  compound asks for the compound. The editor's "Run anyway?" lists them.
- Below: the comparisons that will be run (from `choose_comparisons`, so
  exactly the analysis' own) with their meaning and the samples on each
  side, the specific-targets rule when there is one, what is not compared and
  why, and one line per smaller group: low confidence under `min_valid`;
  with imputation "every feature is tested, the smaller group makes the
  comparison less sensitive"; without it the values a feature needs from it
  (`small_group_min_valid`).
- A choice is saved as `analysis.roles` in the experiment's
  `experiment.yaml` (the key above); **automatic** removes the entry.

**Normalisation** (`fpa.normalize_info`, D64). `auto`, the default for new
set-ups, is FragPipe-Analyst's median centring unless that would shift the
conditions against each other:

- Median centring lines up the middle of each sample's abundance
  distribution. When a share of the features is enriched in one direction (a
  pulldown, a depletion), that middle moves and every unchanged feature is
  pushed the other way: about 0.2 log2 with 8 % enriched in simulation.
- The **ratio** method shifts each sample by the median, over the stable
  three quarters of the features measured in every sample, of the feature's
  value minus its mean across samples. A feature's own ratio is narrow, so
  enrichment barely moves it; the features that vary most are left out first.
  It needs 20 features measured in every sample, else median centring is used.
- **The check**: the two methods' sample shifts are averaged per condition.
  If they disagree by more than 0.1 log2 between two conditions, and by more
  than 3 times the scatter among replicates, `auto` uses the ratio method and
  says so (`NORMALISATION_COMPOSITION`, a note). With `median` or `gn` chosen
  explicitly, the same finding asks the user to switch.
- **TMT plexes not on one scale** (IRS off or not possible): the ratio
  method and the check are worked out within each plex and combined, since
  across such plexes a protein jumps with the plex and hides the
  composition (D71). Plexes joined by IRS are compared all together.
- When nothing changes in one direction, `auto` is median centring, with
  identical numbers.
- `analysis.json` → `normalisation` records the method asked, the method
  used and the check.

**Time courses** (`downstream/timecourse.py`, D53). When a series has three
or more time points (`Drug_0h`, `Drug_1h`, `Drug_4h`, … or `analysis.times`),
the report adds, per series and feature:

| Question | Test (limma, on the comparisons' model) |
|---|---|
| Does it change over time? | moderated F on every time point against the first |
| Is there a steady rise or fall? | moderated t on the linear contrast over the ordered time points |
| Does it respond differently from the control series? | moderated F on the interaction contrasts (two series sharing time points, one a control such as `DMSO_0h` …) |

- *Changing* = F adjusted p ≤ `alpha` and a largest |log2FC| ≥ `log2fc`
  against the first time point; classed up / down / mixed. Changing features
  are grouped into at most 6 patterns by profile shape.
- The trend uses the order of the time points, not the hours, so `0 / 1 h /
  24 h` is not dominated by the long gap.
- Up to 6 time points, time is a factor: no curve is fitted. Features not
  measured at every time point of a series are not tested (unless imputed).
- **Many time points** (D77, limma User's Guide "many time points"): from 7
  time points (`time_model: auto`), or always with `time_model: spline`, a
  series is a natural cubic spline in hours (R's `ns()`, knots at quantiles
  of the sample times) with `time_spline_df` degrees of freedom (`auto`: 4,
  at most the time points − 2). The series gets its own model (the
  comparisons' model with its conditions replaced by a level and the spline
  terms; other conditions, the block and covariates stay):

  | Question | Test |
  |---|---|
  | Does it change over time? | moderated F on the spline coefficients |
  | Does it respond differently from the control series? | moderated F on the interaction terms of both series as curves on one basis (`~group * ns(time)`) |

  log2FC, the largest change, the class and the patterns use the fitted
  curve's change from the first time point; the trend t stays the factor
  model's. The report's profile and `time_profiles` draw the fitted curve
  on an axis in hours. A `time_spline_df` the series can't carry is lowered
  to the time points − 2 with a `TIME_SPLINE` issue.
- Settings: `times`, `time_unit`, `time_min_points` (3), `time_course`
  (false switches it off), `time_model` (auto | factor | spline),
  `time_spline_df` (auto). Intensity data with the limma test only.
- Output: `results/time_course.tsv`, `analysis.json` → `time_course`, the
  report's **Time course** section. The pairwise comparisons are unchanged.

**isoDTB: liganded sites** (`downstream/cys.py`, D52). Besides the test
against 0, every site gets the chemoproteomics call per compound (each
sample prefix is a compound):

| Call | Rule (defaults) |
|---|---|
| liganded | competition ratio R ≥ 4 in at least 2 replicates |
| inconsistent | R ≥ 4 in some replicates, but fewer than 2 |
| not liganded | measured in at least 2 replicates, R ≥ 4 in none |
| too few | measured in fewer than 2 replicates (not assessed) |

```yaml
analysis:                      # config.yaml (lab) or experiment.yaml (one experiment)
  liganded_ratio: 4            # R a replicate must reach
  liganded_min_replicates: 2   # ... in at least this many replicates
  liganded_direction: high     # high: R = heavy / light (treated sample = light tag) | low: R = light / heavy
  site_annotation: cysdb.csv   # optional: a site table you downloaded (CysDB), in the experiment folder or a full path
  liganded: true               # false: no calls
```

- The calls use the ratios as measured (no imputation), or centred per
  replicate when `ratio_centre` is set (D70, below); the rule says which. A
  compound with fewer replicates than the rule asks for is judged on the
  replicates it has, with a note.
- **Liganded fraction** per compound = liganded / sites measured in enough
  replicates.
- **Selectivity** (2+ compounds): *selective* = liganded by one compound and
  measured as not liganded by every other; *shared* = liganded by several;
  *unresolved* = liganded by one, the others not measured well enough.
- **Proteins**: for each protein with a liganded cysteine, how many of its
  assessed cysteines are liganded. Three or more assessed with at least half
  liganded is marked "most sites": suspect the protein amount, not a site.
- **Site annotation**: Ionomos does not ship CysDB (its licence is the
  lab's business). A table the lab downloads is matched by UniProt accession
  and residue number: one column of keys like `P04406_C152`, or an accession
  column plus a residue column. Its yes / no columns become flags;
  `ligandable`, `hyperreactive` and `identified` give *known liganded*,
  *known hyperreactive*, *seen before*; a site not in the table is *new*.
- **Direction check**: if at least 10 sites, and more than three times as
  many as are liganded, would be liganded with the ratio the other way
  round, the report warns (`LIGANDED_DIRECTION`).
- Output: `results/cysteine_sites.tsv` (every site: per compound log2 R, R,
  engagement % = 100 × (1 − 1/R), replicates, replicates over, call; then
  liganded_by, selectivity, annotation), `results/cysteine_proteins.tsv`,
  `analysis.json` → `cysteines`, and the report's **Liganded sites** section.

**isoDTB: centring the ratios** (`fpa.centre_ratios`, D70). A heavy / light
mixing error moves every ratio of a replicate by the same amount. Off by
default; the lab decides:

```yaml
analysis:
  ratio_centre: none     # none: as measured (default) | median: each replicate's median site to 0
                         # | auto: each replicate centred on its stable sites, only when one is clearly off
```

- `auto` takes the median over the half of the sites nearest the replicate's
  centre (five passes), so sites a compound engages, which all move one way,
  don't pull it; a condition is centred when one of its replicates is more
  than 0.05 log2 and 3 standard errors off 0, and then all its replicates.
- `median` is simpler but follows the engaged sites: with 20 % of the sites
  up it shifts every unchanged site by about -0.09 log2.
- The offsets are measured either way (`analysis.json` →
  `normalisation.ratio_centre`). With `none`, a replicate clearly off raises
  `RATIO_OFFSET`, a note. The liganded calls use the ratios the analysis used
  and say which ("on the ratios as measured" / "centred …").
- Simulated numbers for the three options: D70 and
  [VALIDATION.md](VALIDATION.md).

**isoDTB: site changes corrected for protein abundance**
(`downstream/proteincorr.py`, D70). A site's ratio also moves when the
compound changes the amount of its protein. With an unenriched proteome of
the same treatment, Ionomos subtracts the protein's change, as MSstatsPTM
does. Off unless the proteome is named; nothing is guessed:

```yaml
analysis:                      # usually experiment.yaml
  protein_correction:
    proteome: D:/Fragpipe_General/EJQ/20261001-DIA_EJQ-2-030   # an analysed Ionomos experiment (folder or results/),
                                                               # or a protein table (MSstats groupComparison output:
                                                               # Protein, Label, log2FC, SE, DF; or an Ionomos
                                                               # *_differential.tsv); relative = in the experiment folder
    match: gene                # gene | protein (UniProt accessions, isoforms joined)
    conditions:                # site condition -> proteome comparison, compound first
      EJQ_2_027: Cmpd vs DMSO  # default: the comparison whose first condition has the site condition's name
```

- **Scale**: site ratios are log2 heavy / light. A proteome comparison
  "Cmpd vs DMSO" is log2(Cmpd / DMSO), which on the site's scale is −log2FC
  when the treated sample carries the light tag (`liganded_direction: high`)
  and +log2FC when it is heavy (`low`). A proteome analysed as ratios
  ("X (log2 H/L vs 0)") is used as it is.
- **Statistics** (MSstatsPTM's adjustment, checked against MSstatsPTM 2.14.0):
  log2FC = site − protein, SE = √(SE_site² + SE_protein²), Satterthwaite df,
  two-sided p, Benjamini–Hochberg per condition over the sites that were
  corrected. The site's SE and df are the moderated one-sample test's;
  every `*_differential.tsv` now has `se` and `df` columns (older tables are
  read from `t` and the 95 % interval).
- **Output**: both results. The uncorrected comparison stays as it was; a
  second one, `<condition> (log2 H/L vs 0, protein-corrected)`, gets its own
  volcano and `…_protein-corrected_differential.tsv` with the protein's key,
  status (*corrected*, *protein not found*, *protein ambiguous*, *protein
  without SE*, *site not tested*), its log2 H/L, SE and df, and the site's
  uncorrected numbers. Sites whose protein is not found keep only the
  uncorrected result (MSstatsPTM drops them). `analysis.json` →
  `protein_correction` counts them per condition. `cysteine_sites.tsv` gets
  `<compound> protein_log2_R` and `log2_R_corrected`; the liganded call stays
  on the site ratio.
- **Checks**: `PROTEIN_CORRECTION_CONDITIONS` (decide) when a site condition
  has no proteome comparison, or several; `PROTEIN_CORRECTION` when the
  proteome is missing or unusable (decide) or fewer than half the sites find
  their protein (note). The proteome is only read.

Open questions for the lab: which words mark a competition condition and
what the lab calls a specific target (D61, ROADMAP); which tag the compound-treated sample carries
(`liganded_direction`) and which R and replicate count the lab calls
liganded; which comparisons matter for isoDTB (vs 0, or
compound vs compound?); whether DIA should use FragPipe's own
`combined_protein.tsv` or DIA-NN's matrix; which thresholds people use today.
