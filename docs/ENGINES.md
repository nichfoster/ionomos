# Engines: whose results Ionomos can analyse

Ionomos runs **FragPipe** itself: the watcher, the headless search, and the
lab's workflows. It can also **analyse results from other engines** with the
same statistics and report. Point `ionomos analyze` at the engine's output
folder or its main table; the Analysis tab's **Table…** button works too.

```bash
ionomos analyze path/to/combined/txt          # a MaxQuant folder
ionomos analyze path/to/report.parquet        # a DIA-NN 2.x report (needs: pip install pyarrow)
ionomos analyze path/to/Spectronaut_Report.tsv
ionomos analyze path/to/sage_output           # Sage: lfq.tsv, or tmt.tsv + results.sage.tsv (+ results.json)
```

The engine is recognised from file names and column headers
(`downstream/engines.py`, D36). Each report's **Methods → Data source** says
which engine, version, table, quantity and FDR filter were used, whenever the
folder records them. Ionomos only *reads* these files; it never writes into
the engine's folder. Results go to `results/` next to them, or to
`<table>_ionomos/` for a single file (D33).

Ionomos never ships these engines, and their licences stay between your lab
and the vendor. MSFragger / FragPipe is free for academic use only; DIA-NN is
not redistributable from 1.9 on; MaxQuant is free, but you install it
yourself; Sage is open source (MIT), and you download it yourself. The
watcher can run DIA-NN, MaxQuant and Sage for you once they are installed:
see below.

| Engine | What Ionomos reads | Quantity used | Filters applied by Ionomos | Version from |
|---|---|---|---|---|
| **FragPipe** (run by Ionomos) | isoDTB `combined_modified_peptide_label_quant.tsv`, TMT-Integrator `abundance_*_MD.tsv`, DIA-NN `*pg_matrix.tsv`, IonQuant `combined_protein.tsv` | per method (see WORKFLOWS.md) | none (FragPipe's own FDR) | `log_*.txt`, `fragpipe.workflow` (plus MSFragger / IonQuant / DIA-NN versions and the FASTA) |
| **DIA-NN** standalone | `*pg_matrix.tsv` (preferred), else the long report `report.tsv` (1.x) or `report.parquet` (2.x) | pg_matrix values, or `PG.MaxLFQ` | long report: `Q.Value` ≤ 1% and `PG.Q.Value` ≤ 1% | `*.log.txt` |
| **MaxQuant** | `combined/txt/proteinGroups.txt` | `LFQ intensity`, else `Intensity`; `Reporter intensity corrected` for TMT | rows marked `+` in Reverse / Potential contaminant / Only identified by site are left out; `CON__` contaminants removed | `parameters.txt`, `mqpar.xml` |
| **Sage** | `lfq.tsv` (a row per peptide ion, a column per file); `results.sage.tsv` and `results.json` beside it when they are there | proteins summarised per sample by Tukey median polish of the ion intensities; proteins grouped by razor peptides (below) | `q_value` ≤ 1% per peptide; with `results.sage.tsv`, the peptide's best `protein_q` ≤ 1% too; decoys left out | `results.json` (also the FASTA and every search setting) |
| **Sage TMT** | `tmt.tsv` (a row per spectrum, a column per reporter channel) with `results.sage.tsv` beside it; read instead of `lfq.tsv` when both are there | proteins summarised per plex and channel as the MSstatsTMT format is (below); several plexes joined by IRS | PSMs: targets of `rank` 1 with `spectrum_q`, `peptide_q` and `protein_q` ≤ 1%; spectra with more than one such PSM left out | `results.json` |
| **Spectronaut** | a protein pivot report (`<run>.PG.Quantity`) or the long BGS report (`R.FileName`, `R.Condition`, `R.Replicate`, `PG.ProteinGroups`, `PG.Quantity`) | `PG.Quantity` | long report: `PG.Qvalue` and `EG.Qvalue` ≤ 1% | not in the export |
| **AlphaDIA** | `pg.matrix.tsv` | protein-group matrix | none | `frozen_config.yaml` |
| **MSstats format** (quantms, Skyline, any MSstats converter) | `ProteinName, PeptideSequence, PrecursorCharge, FragmentIon, ProductCharge, IsotopeLabelType, Condition, BioReplicate, Run, Intensity` | proteins summarised per run by Tukey median polish (MSstats' default) | heavy / reference rows left out; intensities ≤ 1 count as missing | not in the file |
| **MSstatsTMT format** (MSstatsTMT's converters, quantms / OpenMS, FragPipe) | `ProteinName, PeptideSequence, Charge, PSM, Mixture, TechRepMixture, Run, Channel, Condition, BioReplicate, Intensity` (`Fraction` optional) | proteins summarised as MSstatsTMT's `proteinSummarization(method = "MedianPolish")`: fractions of a mixture combined, global median normalisation, median polish per run, reference normalisation on the `Norm` channels; one sample per mixture × channel (technical replicates averaged) | `Norm` and `Empty` channels left out after the normalisation; intensities < 1 count as missing | not in the file |
| **Proteome Discoverer** | a Proteins table exported as text | `Abundances (Normalized)`, else `Abundance`; TMT: `Abundance: F1: 126, …` is file (plex) F1, channel 126 | none | not in the export |
| **Any other table** | one ID column plus one numeric column per sample, or a results table with fold change and p (D33) | as found | none | — |

**Peptides to proteins** (`analysis.rollup`, D76). For the engines whose
table holds peptides or precursors, `rollup:` under `analysis:` picks how
they are combined into one value per protein and sample:

| Table | `auto` (default) | `median_polish` | `maxlfq` |
|---|---|---|---|
| Sage `lfq.tsv` | Tukey median polish of the ion intensities | the same | MaxLFQ of the ion intensities |
| MSstats format | Tukey median polish of the features | the same | MaxLFQ of the features |
| DIA-NN long report | DIA-NN's own `PG.MaxLFQ` | median polish of `Precursor.Normalised` (else `Precursor.Quantity`) per `Precursor.Id` | Ionomos' MaxLFQ of the same precursors |
| Spectronaut long report | Spectronaut's `PG.Quantity` | median polish of `FG.Quantity` per `EG.PrecursorId` | Ionomos' MaxLFQ of the same precursors |

- **MaxLFQ** (Cox et al. 2014, `downstream/rollup.py`) works in three
  steps:
  - each pair of samples gets the median log2 ratio of the features it
    shares;
  - the protein's profile is the least-squares fit to those ratios, per
    group of samples linked by shared features;
  - the profile is scaled so its summed intensity equals the features'
    summed intensity.

  It agrees with R's `iq::maxLFQ()` to 1e-9 and with DIA-NN's R package to
  1e-3 (VALIDATION.md).
- **Samples that share no feature** with the rest of the protein's samples
  are scaled on their own, so a value across such groups is not a ratio.
  The notes say how many proteins are affected.
- **Without the precursor columns**, a DIA-NN or Spectronaut report keeps
  the engine's protein quantity, and a note says why. `ionomos
  spectronaut-columns` lists `FG.Quantity` as optional.
- **Tables that already hold proteins** (FragPipe, MaxQuant, AlphaDIA,
  Proteome Discoverer, pg_matrix, any table) ignore the setting with a note.
  So do Sage TMT and the MSstatsTMT format, which keep MSstatsTMT's median
  polish.
- The report's **Data source → Quantity** says which was used.

**Conditions and replicates** come from the engine when it records them
(Spectronaut `R.Condition` / `R.Replicate`, MSstats `Condition` /
`BioReplicate`, the text after the sample type in Proteome Discoverer column
names). Otherwise they come from the sample names (`DMSO_1`, `Drug_2`, …), as
for FragPipe. They can always be corrected on the Analysis tab or in
`experiment.yaml` (`sample_conditions`).

## Exporting a Spectronaut report for Ionomos

`ionomos spectronaut-columns` prints the columns Ionomos reads from a
Spectronaut Normal Report and how to make a report schema with exactly those
(`--out DIR` also saves it as `Ionomos_Spectronaut_report_columns.txt`). The
list is `engines.SPECTRONAUT_COLUMNS`, the same one the loader reads (D74):

| Column | | What for |
|---|---|---|
| `R.FileName` | needed | the run |
| `R.Condition` | optional | the run's condition (else from the run names) |
| `R.Replicate` | optional | the replicate number |
| `PG.ProteinGroups` | needed | the protein group |
| `PG.Genes`, `PG.ProteinDescriptions`, `PG.ProteinNames` | optional | labels and descriptions |
| `PG.Quantity` (or `PG.MS2Quantity`) | needed | the protein quantity |
| `PG.Qvalue`, `EG.Qvalue` | optional | rows above 1% are left out |
| `EG.PrecursorId` | optional | peptides counted per protein |

In Spectronaut: Report perspective → start from one of the preconfigured
Normal Report schemas → tick these columns in the column chooser (its search
field finds each) → save the schema under a name of your own → **Export
Report…** as a text file. Other columns are ignored, so an existing report
works if it has the three needed ones.

Ionomos does not ship a Spectronaut report-schema file (`.rs`): that format
is Spectronaut's own and is not published (the manual describes making and
passing a schema, `-rs` on the command line, but not the file). Once the
schema is made, Spectronaut can save it as an `.rs` for the rest of the lab.

## An SDRF as the design

Put the experiment's SDRF-Proteomics file (`*.sdrf.tsv` or `sdrf.tsv`, the
[PSI format](https://github.com/bigbio/proteomics-sample-metadata) PRIDE and
quantms use) in the experiment folder, or next to the table you give
`ionomos analyze`. Ionomos then takes each run's condition and replicates from
it (D47):

- **Runs are matched by `comment[data file]`** (the raw file, compared without
  its extension; FragPipe's `_calibrated.mzML` names match their `.raw`). For
  TMT, by the plex's raw files plus `comment[label]` (`TMT126`, `TMT127N`, …).
  Files that carry the same channels are one plex.
- **The condition is `factor value[...]`**. With several factor columns their
  values are joined (`Drug | 1 uM`); `analysis.sdrf_factor: [compound]` picks
  the column(s) to use instead.
- **Replicates** come from `characteristics[biological replicate]`. A row whose
  biological replicate is `pooled` (or whose `characteristics[pooled sample]`
  says so) is a pooled reference channel, used to join TMT plexes (below).
- **What wins**: `sample_conditions` (Analysis tab / experiment.yaml) >
  the SDRF > the engine's own condition column > `ionomos.json` > file names.
  `analysis.json` → `design` says which was used; runs the SDRF doesn't name
  keep their old condition and the analysis asks about them.
- Ionomos' own `results/sdrf.tsv` is output and is never read back.

## TMT across plexes (IRS)

A TMT experiment of several plexes (MaxQuant experiments, MSstatsTMT
mixtures, Proteome Discoverer files `F1`, `F2`, …, or SDRF file groups) is put
on one scale before the statistics by internal reference scaling (IRS, Plubell
et al., *Mol Cell Proteomics* 2017), D48:

```yaml
analysis:
  tmt_reference: [126]     # the pooled / bridge channel(s) in every plex (or a sample name)
  irs: auto                # auto | reference | sum | none
```

- Each protein is scaled per plex so the plex's reference channels meet at
  their geometric mean across plexes; the reference channels then leave the
  analysis.
- Without `tmt_reference`, the SDRF's pooled rows are used, else channels
  named pool / pooled / bridge / reference / norm.
- With no reference at all, `auto` uses each plex's own mean, but only when
  every plex holds the same mix of conditions; otherwise the plexes stay as
  they are and the report warns. `irs: sum` forces the plex means; `none`
  switches IRS off. Plex means are not used while a channel has no
  condition yet (Sage TMT without a channel map).
- **Plex means spend degrees of freedom**: each plex's mean is estimated
  from the channels that are then tested, so limma's residual df are
  reduced by the plexes - 1 for each protein (D71), unless the design
  already has a block per plex.
- **Not applied twice**: FragPipe's TMT-Integrator abundances are already
  ratios to the reference channel, and MSstatsTMT input is normalised to its
  `Norm` channels as MSstatsTMT does it.
- The report's PCA can be coloured by plex and switched between the values
  before and after IRS.

In MaxQuant, the channel totals over experiments
(`Reporter intensity corrected 1`, without an experiment name) are left out,
and `combined/txt/summary.txt` tells which raw files each experiment has.

## Running DIA-NN directly (instead of FragPipe)

A DIA method can be searched by the lab's own DIA-NN (1.9 or 2.x) rather than
FragPipe (D39):

```yaml
methods:
  DIA:
    engine: diann
    diann_exe: C:/DIA-NN/2.2.0/diann.exe   # your install; Ionomos never ships DIA-NN
    fasta: human_reviewed.fasta           # in fasta_dir
    data_type: DIA
    library: human_lib.parquet            # optional; otherwise predicted from the FASTA
    diann_args: "--var-mods 1 --var-mod UniMod:35,15.994915,M"   # optional, added to the defaults
```

The method's key is the lab's choice (D54). The engine decides what the
method is: any `engine: diann` method is searched and analysed as DIA, and
any `engine: maxquant` or `engine: sage` method as label-free, with a
control and that engine's own protein table. For a FragPipe method under
another key, `naming.methods.<key>: {like: isoDTB | TMT | DIA}` does the
same (docs/NAMING_CONVENTION.md).

Each job then writes `ionomos_run/diann.cfg` and runs
`diann.exe --cfg ionomos_run/diann.cfg`. The cfg is the job's full,
reproducible settings: every raw file, FASTA / library, output, threads and
options. Results go to `<experiment>/diann/`. Watching, holding, cancel,
Retry, pop-ups and the analysis work exactly as for FragPipe. A second run
keeps the first as `diann_previous_<time>/`. An experiment can pick another
`fasta` in its `experiment.yaml`, as for FragPipe.

Ionomos' defaults are close to DIA-NN's GUI defaults for a tryptic search:
- 1% q-value
- `--matrices`
- N-terminal Met excision
- `K*,R*` with 1 missed cleavage
- peptides of 7–30 residues, charge 1–4, m/z 300–1800
- carbamidomethyl C (`--unimod4`)
- MBR (`--reanalyse`) and `--rt-profiling`
- without a library: `--fasta-search --predictor --gen-spec-lib`

Check them against your lab's usual DIA-NN settings: DIA-NN prints the full
command line at the top of its log. Paths must not contain spaces, since
`diann.cfg` is split on them, and the experiment folders already can't have
any.

## Running MaxQuant directly (instead of FragPipe)

A DDA method can be searched by the lab's own MaxQuant (D50):

```yaml
methods:
  LFQ:
    engine: maxquant
    maxquant_exe: C:/MaxQuant/2.6.7.0/bin/MaxQuantCmd.exe   # or MaxQuantCmd.dll (run with dotnet)
    fasta: human_reviewed.fasta                             # in fasta_dir
    data_type: DDA
    mqpar: lab_lfq_mqpar.xml   # optional: File -> Save parameters in the MaxQuant GUI, put in workflow_dir
naming:
  methods:
    LFQ: '{sample}_{rep}[_{fraction}]'   # <sample>_<rep>[_<fraction>] names (or your own template, D37)
```

Ionomos never writes an `mqpar.xml` from scratch, because its layout
changes between MaxQuant versions. A job starts from one of two templates:
- the lab's own parameters (`mqpar:`), saved from a run that worked
- MaxQuant's default template, made by the installed MaxQuant itself
  (`MaxQuantCmd --create`) with label-free quantification switched on

Only the job's parts are replaced:
- the raw files
- the experiment names (`<condition>_<replicate>`, so the analysis reads
  `LFQ intensity DMSO_1` …)
- the fractions, read from the file names with the lab's naming rules
- the FASTA, threads and output folder

The result is `ionomos_run/mqpar.xml`, run as
`MaxQuantCmd ionomos_run/mqpar.xml`. Results go to
`<experiment>/maxquant/combined/txt/`, and the analysis reads
`proteinGroups.txt` with the version from `parameters.txt`. MaxQuant writes
its per-raw working folders next to the raw files, as it always does. A
second run keeps the first as `maxquant_previous_<time>/`.

This has been tested only against a stand-in MaxQuant
(`ionomos fake-maxquant`). Check the first real run's
`ionomos_run/mqpar.xml` in the MaxQuant GUI: open it with File → Load
parameters.

## Running Sage directly (instead of FragPipe)

[Sage](https://github.com/lazear/sage) is a fast open-source (MIT) search
engine for DDA data. A DDA method can be searched by it (D51):

```yaml
methods:
  LFQ:
    engine: sage
    sage_exe: C:/sage/sage.exe                 # your download; a folder without spaces
    raw_converter: C:/ThermoRawFileParser/ThermoRawFileParser.exe   # .raw -> mzML
    fasta: human_reviewed.fasta                # in fasta_dir
    data_type: DDA
    sage_config: lab_sage.json                 # optional: your own Sage settings, in workflow_dir
    sage_args: "--batch-size 2"                # optional: added to the sage command line
naming:
  methods:
    LFQ: '{sample}_{rep}[_{fraction}]'   # <sample>_<rep>[_<fraction>] names (or your own template, D37)
```

A job has two steps, both in `ionomos_run/sage_console.log`:

1. **Convert.** Sage reads mzML, not Thermo `.raw`. Each raw file is
   converted with
   [ThermoRawFileParser](https://github.com/compomics/ThermoRawFileParser)
   (`-f=2`, indexed mzML with Thermo's own peak picking) into
   `<experiment>/sage_mzml/`. A file is written to `sage_mzml/converting/`
   and moved into place only when it is complete. A retry reuses what is
   already converted. The raw files are never touched. mzML files and Bruker
   `.d` folders in a drop are searched as they are, and need no converter.
2. **Search.** `sage ionomos_run/sage.json`, with results in
   `<experiment>/sage/`: `results.sage.tsv`, `lfq.tsv`, `results.json`. A
   second run keeps the first as `sage_previous_<time>/`.

`ionomos_run/sage.json` is the job's full, reproducible settings. It is your
`sage_config` (the `results.json` of a search that worked is a complete one),
or else Ionomos' defaults:

- trypsin (`KR`, not before `P`), 2 missed cleavages, peptides of 7–50 residues
- carbamidomethyl C fixed, oxidised M variable (at most 2 per peptide)
- precursor ±20 ppm, fragments ±20 ppm, charge 2–4, isotope errors 0–2
- decoys made by Sage (`rev_`; decoys already in the FASTA are ignored)
- label-free quantification on, charge states combined, retention times
  predicted and aligned

The defaults suit high-resolution MS2 (Orbitrap HCD). For ion-trap MS2, TMT
or other modifications, give your own `sage_config`. Only the FASTA, the mzML
paths and the output folder are replaced in it, and label-free quantification
is switched on, because the analysis reads `lfq.tsv`. A TMT `sage_config` is
the exception: see below.

**The analysis** rolls `lfq.tsv` up to proteins:

- Proteins identified by exactly the same peptides are one group
  (`P1;P2`). A peptide shared between groups goes to the group with the most
  peptides, as MaxQuant's razor peptides do; a protein left with no peptide
  of its own disappears.
- The files of one sample (its fractions, from the job's file names) have
  their intensities added.
- Each group's quantity per sample is the Tukey median polish of its ions'
  log2 intensities, the summary MSstats uses.
- Gene names and descriptions come from the FASTA named in `results.json`,
  when it is still there; otherwise from the UniProt entry names.

### TMT with Sage

A `sage_config` whose `quant.tmt` names a kit makes the method a TMT method
(D56):

```json
{
  "database": {"static_mods": {"^": 304.2071, "K": 304.2071, "C": 57.021464}},
  "quant": {"tmt": "Tmt16", "tmt_settings": {"level": 3, "sn": false}}
}
```

```yaml
methods:
  TMT:
    engine: sage
    sage_config: lab_tmt_sage.json   # in workflow_dir; the rest as above
```

- `quant.tmt` is `Tmt6`, `Tmt10`, `Tmt11`, `Tmt16`, `Tmt18`, or
  `{"User": [reporter masses]}`. Anything else holds the job.
- Label-free quantification is not switched on for a TMT job. Sage writes
  `tmt.tsv`, and the job expects it.
- Name the files `<plex>_F<fraction>.raw`, the TMT rule in
  [NAMING_CONVENTION.md](NAMING_CONVENTION.md). The files of a plex are its
  fractions. With another naming rule that has replicates, every sample and
  replicate is read as its own plex, and the job says so.

**The analysis** joins `tmt.tsv` to `results.sage.tsv` on file and scan:

- PSMs are targets of rank 1 with spectrum, peptide and protein q-values
  ≤ 1%. A spectrum with more than one such PSM is left out. A reporter
  intensity of 0 is missing.
- `tmt_1 … tmt_n` are the kit's channels in order (126, 127N, 127C, …).
  Custom reporter masses (`user_1 …`) keep Sage's column names.
- Proteins are grouped by razor peptides, as for `lfq.tsv`.
- Each protein is summarised per plex and channel as MSstatsTMT's
  `MedianPolish` does it, with the same code as the MSstatsTMT format
  above: one PSM per peptide ion and file (the one with the largest total
  intensity), fractions combined, global median normalisation, Tukey median
  polish. No model-based imputation.
- Several plexes are joined by IRS (above). Sage has no `Norm` condition, so
  MSstatsTMT's reference normalisation is not used.

**Channel names and conditions** come from experiment.yaml's `tmt:` map, the
one a FragPipe TMT job uses:

```yaml
tmt:
  channels: {126: Pool, 127N: DMSO_1_127N, 127C: Drug_1_127C, 131: NA}
  reference_channel: 126
```

- The name is the sample. Its condition is the text before the first `_`.
- A channel called `NA` or `empty` is left out.
- A name used in several plexes gets the plex in front (`plexB_DMSO_1`).
- Without a map, the channels are `<plex>_<channel>` with the condition
  `unassigned`, and the analysis asks for the conditions. Give them on the
  Analysis tab, in the `tmt:` map, or with an SDRF (above).

**Telemetry.** Sage sends anonymous usage statistics after a search by
default (its version, index sizes, number of files, run time, OS, memory and
CPU count). Ionomos switches this off with Sage's own
`--disable-telemetry-i-dont-want-to-improve-sage` whenever the installed Sage
has that flag (it asks `sage --help`), and the console log says which
happened. Threads are capped at `fragpipe.threads` (`RAYON_NUM_THREADS`).
Sage loads several files at once (half the CPU count by default); on a PC
with little memory, set `sage_args: "--batch-size 2"`.

This has been tested only against stand-ins for Sage and ThermoRawFileParser
(`ionomos fake-sage`, `ionomos fake-rawparser`). The file formats, `tmt.tsv`
included, were taken from Sage's source (0.14 / 0.15 and master), not from a
real run. Check the first real run's console log and compare its hits with a
FragPipe search of the same files.

**Not supported yet:**
- Sage's Parquet output (`--parquet`): one `results.sage.parquet` with the
  reporter ions as a list column, and a long `lfq.parquet`. These are other
  layouts than the `.tsv` files, and Sage calls the format unstable. A
  method with `--parquet` in `sage_args` is held; run Sage without it.
- Filtering Sage TMT PSMs on reporter signal-to-noise or precursor purity:
  Sage reports neither.
- MSstatsTMT's default summary (`method = "msstats"`) imputes censored values
  with a model (MBimpute); Ionomos summarises as `method = "MedianPolish"`
  and leaves missing values missing.
- Spectronaut's peptide-only reports.
- AlphaDIA's Parquet matrices: read `pg.matrix.tsv`, which holds the same
  numbers.

The formats were built from each vendor's documentation and tested with files
using the real column names (`tests/test_engines.py`, `tests/test_plexes.py`); the MSstatsTMT summary is checked against MSstatsTMT 2.20 itself. The Proteome Discoverer
column naming comes from the documentation, not from a real export. If a real
file from your engine isn't recognised, please send its header line with
**Report a problem**. For checking a search itself, `ionomos bundle 12
--include diann-report,peptides` also puts DIA-NN's main report
(`report.parquet` as text) and the peptide / ion tables in the anonymised zip
(D74).
