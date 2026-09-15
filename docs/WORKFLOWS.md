# Lab workflows → headless FragPipe

What each method needs, distilled from the lab SOPs in
[`reference/lab-sops/`](../reference/lab-sops/), the R scripts in
[`reference/lab-scripts/`](../reference/lab-scripts/), and the FragPipe output
folders seen in the inventory. This is what the runners have to reproduce.

## Common: headless FragPipe

```
fragpipe.exe --headless --workflow <wf> --manifest <mf> --workdir <out>
             [--threads N] [--ram G]
             [--config-tools-folder <dir>] [--config-diann <DiaNN.exe>]
```

- Confirmed CLI shape in `prior-work/fragpipe_runner.py`; **re-confirm the
  launcher path on the 24.0 install** (`C:\FragPipe\FragPipe-24.0\fragpipe\bin\fragpipe.exe`
  vs `fragpipe.bat`). The inventory didn't capture the install tree.
- Manifest = `.fp-manifest`, tab-separated: `path \t experiment \t bioreplicate \t DDA|DIA`.
- The FASTA is baked into the `.workflow` file. Workflows must be saved from
  the GUI *after* setting the database (with decoys) via "Save to custom
  folder". A headless run with an empty FASTA fails with
  "FASTA file path is empty".
- **No spaces in any path** (SOP: "Directory must not contain any spaces!").
- Output dir must be empty (SOP). Our `--workdir` is always a fresh `fragpipe\` subfolder.
- Every existing run folder contains: `fragpipe.workflow`, `fragpipe.job`,
  `fragpipe-files.fp-manifest`, `filelist_ionquant.txt`, `modmasses_ionquant.txt`.
  These are written by FragPipe itself — good provenance, keep them.

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
- Port to Python/pandas is ~60 lines. Validate against one existing run's
  output (the lab has `combined_modified_peptide_label_quant_output.tsv`
  files on D:).

## TMT

**SOP summary** (`How-to-FragPipe-TMT`):
1. *(Pre-step in a Thermo tool — "Peak Picking & zero Samples", ~15 min.)*
   ⚠ This is a raw-file conversion step done outside FragPipe. Need to confirm
   what tool this is and whether FragPipe 24 still needs it (MSFragger reads
   `.raw` directly via the Thermo library; this step may be legacy).
2. Load **TMT-10 MS3** workflow. "Just load the folder."
3. Bioreplicate = 1 when replicates are within channels.
4. Define replicates, select tag set (TMT-10 etc.).
5. **Keep the channel token (126, 127N…) in each sample name**; save annotation
   to the raw-file folder.
6. Run. Then run the R annotation script; feed `abundance_gene_MD.tsv` +
   `experimental_annotation.tsv` to FragPipe Analyst; filter 100% non-missing.

**Workflow file**: stock `TMT10-MS3` (FragPipe ships TMT10, TMT10-MS3,
TMT10-phospho, TMT16…). Pin the lab's copy. Existing runs on D: used custom
names (`fragpipe_pax8.workflow`) — check those for lab-specific settings.

**Manifest**: each raw = one plex (or one fraction of a plex). experiment =
plex name, bioreplicate = 1 (per SOP) unless overridden. **Plus** an
`annotation.txt` per plex: `<channel>\t<sample_name>` lines — FragPipe looks
for it next to the raw files / in the workdir (confirm exact lookup rule for
24.0 headless: it is referenced from the workflow's TMT-Integrator section as
`tmtintegrator.annotation` or found by name — check).

**Key output**: `<workdir>\tmt-report\abundance_gene_MD.tsv` (and `_MD` variants
for peptide/site).

**Post-processing** (`correct_experimental_annotation_for_Fragpipe-TMT.R`):
- Reads only the header of `abundance_gene_MD.tsv`.
- Keeps columns matching `^[A-Za-z0-9]+_1_\d{3}[A-Z]?$` (condition_plex_channel).
- Builds table: plex, channel, sample, sample_name (drops `_1_`), condition
  (prefix before first `_`), replicate (row number within condition).
- Writes `experimental_annotation.tsv`.
- Note the regex assumes plex index `1` and a single-token condition. Real
  headers look like `DMSO_1_126`. Our port should derive this from the
  `experiment.yaml` channel map instead of re-parsing headers where possible,
  and fall back to the regex.

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

**Key output**: `<workdir>\diann-output\report.tsv` (+ `report.pg_matrix.tsv`, etc.).

**Post-processing**: none known. Leave `postprocess: []` and ask users what
they do next (FragPipe Analyst upload?).

## What to collect from the lab to finish this doc

- [ ] The three pinned `.workflow` files exported from the FragPipe 24.0 GUI
      (with FASTA set), and the FASTA files they reference.
- [ ] One complete, successful output folder per method (copy of the small
      files only — `*.tsv`, `*.workflow`, `*.fp-manifest`, `annotation.txt`,
      `log*.txt`) to develop parsers/post-proc against.
- [ ] One R-script output per method to diff the Python port against.
- [ ] Answer: what is the TMT "Peak Picking & zero Samples" pre-step tool?
- [ ] Answer: how does FragPipe 24.0 headless locate `annotation.txt`?
