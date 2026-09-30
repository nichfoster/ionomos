# Engines: whose results Ionomos can analyse

Ionomos runs **FragPipe** itself: the watcher, the headless search, and the
lab's workflows. It can also **analyse results from other engines** with the
same statistics and report. Point `ionomos analyze` at the engine's output
folder or its main table; the Analysis tab's **Table…** button works too.

```bash
ionomos analyze path/to/combined/txt          # a MaxQuant folder
ionomos analyze path/to/report.parquet        # a DIA-NN 2.x report (needs: pip install pyarrow)
ionomos analyze path/to/Spectronaut_Report.tsv
```

The engine is recognised from file names and column headers
(`downstream/engines.py`, D36). Each report's **Methods → Data source** says
which engine, version, table, quantity and FDR filter were used, whenever the
folder records them. Ionomos only *reads* these files; it never writes into
the engine's folder. Results go to `results/` next to them, or to
`<table>_ionomos/` for a single file (D33).

Ionomos never ships or runs these engines for you, and their licences stay
between your lab and the vendor. MSFragger / FragPipe is free for academic
use only; DIA-NN is not redistributable from 1.9 on; MaxQuant is free, but
you install it yourself.

| Engine | What Ionomos reads | Quantity used | Filters applied by Ionomos | Version from |
|---|---|---|---|---|
| **FragPipe** (run by Ionomos) | isoDTB `combined_modified_peptide_label_quant.tsv`, TMT-Integrator `abundance_*_MD.tsv`, DIA-NN `*pg_matrix.tsv`, IonQuant `combined_protein.tsv` | per method (see WORKFLOWS.md) | none (FragPipe's own FDR) | `log_*.txt`, `fragpipe.workflow` (plus MSFragger / IonQuant / DIA-NN versions and the FASTA) |
| **DIA-NN** standalone | `*pg_matrix.tsv` (preferred), else the long report `report.tsv` (1.x) or `report.parquet` (2.x) | pg_matrix values, or `PG.MaxLFQ` | long report: `Q.Value` ≤ 1% and `PG.Q.Value` ≤ 1% | `*.log.txt` |
| **MaxQuant** | `combined/txt/proteinGroups.txt` | `LFQ intensity`, else `Intensity`; `Reporter intensity corrected` for TMT | rows marked `+` in Reverse / Potential contaminant / Only identified by site are left out; `CON__` contaminants removed | `parameters.txt`, `mqpar.xml` |
| **Spectronaut** | a protein pivot report (`<run>.PG.Quantity`) or the long BGS report (`R.FileName`, `R.Condition`, `R.Replicate`, `PG.ProteinGroups`, `PG.Quantity`) | `PG.Quantity` | long report: `PG.Qvalue` and `EG.Qvalue` ≤ 1% | not in the export |
| **AlphaDIA** | `pg.matrix.tsv` | protein-group matrix | none | `frozen_config.yaml` |
| **MSstats format** (quantms, Skyline, any MSstats converter) | `ProteinName, PeptideSequence, PrecursorCharge, FragmentIon, ProductCharge, IsotopeLabelType, Condition, BioReplicate, Run, Intensity` | proteins summarised per run by Tukey median polish (MSstats' default) | heavy / reference rows left out; intensities ≤ 1 count as missing | not in the file |
| **MSstatsTMT format** (MSstatsTMT's converters, quantms / OpenMS, FragPipe) | `ProteinName, PeptideSequence, Charge, PSM, Mixture, TechRepMixture, Run, Channel, Condition, BioReplicate, Intensity` (`Fraction` optional) | proteins summarised as MSstatsTMT's `proteinSummarization(method = "MedianPolish")`: fractions of a mixture combined, global median normalisation, median polish per run, reference normalisation on the `Norm` channels; one sample per mixture × channel (technical replicates averaged) | `Norm` and `Empty` channels left out after the normalisation; intensities < 1 count as missing | not in the file |
| **Proteome Discoverer** | a Proteins table exported as text | `Abundances (Normalized)`, else `Abundance`; TMT: `Abundance: F1: 126, …` is file (plex) F1, channel 126 | none | not in the export |
| **Any other table** | one ID column plus one numeric column per sample, or a results table with fold change and p (D33) | as found | none | — |

**Conditions and replicates** come from the engine when it records them
(Spectronaut `R.Condition` / `R.Replicate`, MSstats `Condition` /
`BioReplicate`, the text after the sample type in Proteome Discoverer column
names). Otherwise they come from the sample names (`DMSO_1`, `Drug_2`, …), as
for FragPipe. They can always be corrected on the Analysis tab or in
`experiment.yaml` (`sample_conditions`).

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
  switches IRS off.
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
    LFQ: {like: isoDTB}        # <sample>_<rep>[_<fraction>] names (or your own template, D37)
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

**Not supported yet:**
- MSstatsTMT's default summary (`method = "msstats"`) imputes censored values
  with a model (MBimpute); Ionomos summarises as `method = "MedianPolish"`
  and leaves missing values missing.
- Spectronaut's peptide-only reports.
- AlphaDIA's Parquet matrices: read `pg.matrix.tsv`, which holds the same
  numbers.
- Running Sage from the watcher (ROADMAP Phase 5B). DIA-NN and MaxQuant can
  be run: see above.

The formats were built from each vendor's documentation and tested with files
using the real column names (`tests/test_engines.py`, `tests/test_plexes.py`); the MSstatsTMT summary is checked against MSstatsTMT 2.20 itself. The Proteome Discoverer
column naming comes from the documentation, not from a real export. If a real
file from your engine isn't recognised, please send its header line with
**Report a problem**.
