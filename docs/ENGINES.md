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
| **Proteome Discoverer** | a Proteins table exported as text | `Abundances (Normalized)`, else `Abundance` | none | not in the export |
| **Any other table** | one ID column plus one numeric column per sample, or a results table with fold change and p (D33) | as found | none | — |

**Conditions and replicates** come from the engine when it records them
(Spectronaut `R.Condition` / `R.Replicate`, MSstats `Condition` /
`BioReplicate`, the text after the sample type in Proteome Discoverer column
names). Otherwise they come from the sample names (`DMSO_1`, `Drug_2`, …), as
for FragPipe. They can always be corrected on the Analysis tab or in
`experiment.yaml` (`sample_conditions`).

**Not supported yet:**
- MSstatsTMT format.
- Spectronaut's peptide-only reports.
- AlphaDIA's Parquet matrices: read `pg.matrix.tsv`, which holds the same
  numbers.
- Running any engine other than FragPipe. Running DIA-NN, MaxQuant or Sage
  from the watcher is ROADMAP Phase 5B.

The formats were built from each vendor's documentation and tested with files
using the real column names (`tests/test_engines.py`). The Proteome Discoverer
column naming comes from the documentation, not from a real export. If a real
file from your engine isn't recognised, please send its header line with
**Report a problem**.
