# Quickstart: your proteomics table to a report in 10 minutes

This page is for someone who has never used Ionomos. It covers the
analysis-only install: a table or a results folder in, one interactive
`report.html` out. It runs on Windows, macOS and Linux and needs no lab setup,
no R and no Tk. (The drop-folder watcher that runs FragPipe on an instrument
PC is a separate Windows install: [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md).)

## 1. Install (2 minutes)

You need Python 3.11 or newer (`python3 --version`; on Windows `py --version`).

```bash
python3 -m pip install ionomos            # Windows: py -m pip install ionomos
```

Until the first release is on PyPI, install it from GitHub instead:

```bash
python3 -m pip install "git+https://github.com/nichfoster/ionomos#subdirectory=ionomos"
```

The only dependency is PyYAML. A virtual environment (`python3 -m venv
ionomos-env`) or `pipx install ionomos` keeps it apart from other tools. If the
shell says `ionomos: command not found`, pip's scripts folder is not on your
PATH: run `python3 -m ionomos ...` (Windows: `py -m ionomos ...`) instead.

## 2. The demo (1 minute)

```bash
ionomos demo --open
```

This writes `./ionomos_demo/`, a simulated DIA experiment with conditions
DMSO, DrugA and DrugB and 4 replicates each. It analyses the experiment and
opens the report in your browser. If `ionomos_demo` already exists, the demo
uses `ionomos_demo_2` and so on; it never writes into a folder that holds
anything. `ionomos demo some/new/folder` puts it somewhere else. The demo needs
no internet, because its gene sets come with the package.

The demo's `README.txt` says what was planted, so you can check the report
found it:
- proteins that go up or down
- proteins seen in only one condition
- gene sets moved strongly (found from the hit lists) or slightly
  (found only by the rank-based test)

Every number is invented; the demo gene sets are simplified, not curated
pathways.

## 3. Your own data (5 minutes)

```bash
ionomos analyze path/to/your_table.tsv --open
ionomos analyze path/to/a/results/folder --open
```

What it reads:

| You have | Point it at |
|---|---|
| FragPipe label-free (IonQuant) | the FragPipe output folder (`combined_protein.tsv`) |
| FragPipe DIA / DIA-NN | the folder, or `report.pg_matrix.tsv` itself |
| FragPipe TMT (TMT-Integrator) | the folder (`tmt-report/abundance_*_MD.tsv`) |
| FragPipe isoDTB | the folder (`combined_modified_peptide_label_quant.tsv`) |
| Any protein table: MaxQuant `proteinGroups.txt`, Spectronaut, Proteome Discoverer, your own sheet | the file (`.tsv`, `.csv`, `.txt`, `.xlsx`) |
| Results already computed: a log2 fold-change and a p-value column per comparison (limma, Perseus, DESeq2, a FragPipe-Analyst export) | the file; it is plotted as given, never recomputed |

A protein table needs an ID column (protein or gene) and one numeric column per
sample. Ionomos recognises the usual families of sample columns, such as
`LFQ intensity X`, `X MaxLFQ Intensity`, `X.PG.Quantity` and
`Abundance: X`, and ignores descriptive numbers such as peptide counts,
coverage and scores.

**Conditions come from the sample names.** The part every name shares and a
trailing replicate number are dropped, so `2026_HeLa_DMSO_1` and
`2026_HeLa_Drug_3` become the conditions `DMSO` and `Drug`. The control is
recognised by name (DMSO, vehicle, ctrl, control, mock, WT, ...).

Common options:

```bash
ionomos analyze table.tsv --control Vehicle           # the control, if its name isn't recognised
ionomos analyze table.tsv --compare "Drug vs DMSO"    # exactly these comparisons (repeatable)
ionomos analyze table.tsv --de-type all               # every pair, not only each vs the control
ionomos analyze table.tsv --exclude DMSO_3            # leave a sample out
ionomos analyze table.tsv --log2fc 0.58 --alpha 0.01  # thresholds (default 1 and 0.05 on adjusted p)
ionomos analyze table.tsv --no-enrichment             # skip gene sets
ionomos analyze --help                                # everything else
```

**Where results go.** For a folder, results go to `<folder>/results/`. For a
table, they go to `<table name>_ionomos/results/` next to it. Ionomos never
changes, moves or deletes your files; it only writes in that results folder. To
give a sample another condition, or to keep settings for the next run, edit
`experiment.yaml` in the same folder (the demo has one to copy from). For
example:

```yaml
analysis:
  control: DMSO
  sample_conditions: {Drug_4: DMSO}   # this sample is really a control
  enrichment_gmt: my_sets.gmt         # extra gene sets; a relative path is read from this folder
```

**Gene sets.** Enrichment uses the Enrichr libraries that FragPipe-Analyst uses
(Hallmark, GO Biological Process, Reactome). They are downloaded once and
cached, and the test runs on your computer: your gene lists are never sent
anywhere. With no internet, or with `IONOMOS_OFFLINE=1`, enrichment is skipped
with a note unless the libraries are already cached or you give a `.gmt` file.
Gene names are matched as human symbols.

## 4. What the report shows (2 minutes)

`report.html` is a single file with its data inside. It works offline, and you
can email it or put it on a lab drive. It contains:

- **Key findings**: top hits, on/off proteins, pathways and a verdict on the
  samples, per comparison.
- **Volcano plot** per comparison, interactive. Click a point for its
  details; search by gene, a pasted list, wildcards (`KRT*`) or a pathway
  term.
- **Only in one condition**: proteins measured in most replicates of one group
  and never in the other. These are the hits a t-test can't see.
- **Compare comparisons**: fold change against fold change, and the overlap of
  the hit lists.
- **Heatmap** of the significant proteins.
- **Enrichment**: over-representation of each hit list against the right
  background, and a rank-based test on every protein that finds pathways whose
  members all move a little.
- **Quality control**:
  - a scorecard for each sample, with outlier flags
  - PCA, and a check of whether a component follows the replicate number (a
    batch)
  - correlation and missing values
  - the p-value histogram, with the share of changed proteins it implies
  - a power curve (how many replicates would find what)
- **Methods and files**: the exact settings (also in `analysis.json`), and every
  table as TSV. `fragpipe-analyst/reproduce_in_R.R` re-runs the same analysis in
  FragPipeAnalystR.

The statistics are FragPipe-Analyst's (filter, normalise, impute, limma), ported
to Python and checked against the R package. Small groups are tested and
labelled as low confidence, never refused, and p-values are never invented
([DECISIONS.md](DECISIONS.md) D24, D28, D32, D35).

## Next

- A problem, or a table it doesn't read:
  <https://github.com/nichfoster/ionomos/issues> (attach the header row, not
  your data).
- Automating the whole path from instrument PC to report:
  [DEPLOY_WINDOWS.md](DEPLOY_WINDOWS.md) and [WORKFLOWS.md](WORKFLOWS.md).
