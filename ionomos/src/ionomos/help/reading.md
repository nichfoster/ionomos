# Reading the report {#report}

`report.html` is one page per experiment, section by section from the top.
Every chart has **SVG** and **PNG** buttons (top right) for slides. Numbers
update live when you change the cut-offs; the TSV files next to the report
keep the saved settings.

## Overview {#report.overview}

The tiles at the top count the [features](#glossary.feature) (proteins or
sites) and samples, give a sample-quality verdict, and count the hits in each
[comparison](#glossary.comparison). Click a comparison's tile to open its
volcano plot.

Below them: the key findings, the issues Ionomos found, any notes, and a row of
steps showing what happened to the data (loaded → contaminants removed →
filtered → imputed → tested) with how many features each step removed.

## Key findings {#report.findings}

A short summary per comparison at the current cut-offs:

- how many features go up and down,
- roughly what share of all features change in some way, estimated from the
  p-values ([π0](#glossary.pi0)),
- the strongest hits (click a name to open it),
- features seen [only in one condition](#report.onoff),
- the top pathways from the [rank-based test](#glossary.rank-test),
- how many hits rest on [imputed](#glossary.imputation) values.

**Data quality** says whether any sample was flagged, whether the
[PCA](#glossary.pca) separates the conditions or hints at a
[batch](#glossary.batch), and how the missing values behave.

## Issue boxes {#report.issues}

After every analysis Ionomos checks its own work. Each problem is a box:

- **Problem** (red): something is missing or broken, for example no result
  table or no volcano plot.
- **Needs your decision** (orange): the analysis ran on a guess, for example
  which condition is the control. A window on the PC asks you too.
- **Worth knowing** (grey): advice about data that may still be fine.

Each box lists the most likely causes and what to do. The Help section at the
end of the report explains each issue found in this report in more detail.

## Differential abundance: the volcano plot {#report.differential}

One point per feature. Left–right is the [log2 fold change](#glossary.log2fc)
(treatment vs control); higher up means a smaller
[p-value](#glossary.pvalue). Red points go **up** in the treatment, blue go
**down**; grey points did not pass the [cut-offs](#report.cutoffs). The two
condition names in the bottom corners say which side is which.

- **Hover** a point for its numbers; **click** it to open the
  [protein panel](#report.detail).
- The dashed lines are the cut-offs. With "adjusted" ticked, the horizontal
  line sits at the p-value where the [adjusted p](#glossary.adjp) reaches your
  threshold.
- A **triangle** (△) marks a feature measured in one group only, tested with
  imputed values.
- **Options** → Plot: how many labels, point and label size, cut-off lines.

## Cut-offs {#report.cutoffs}

A feature is a hit when **|log2FC| ≥** the fold-change cut-off **and** its p is
at or below the p cut-off. Tick **adjusted** to apply the p cut-off to the
[adjusted p](#glossary.adjp) (the default, and the safer choice).

Changing them here updates the plot, the table, the counts and the key
findings at once. It does **not** change the TSV files, the heatmap or the
enrichment, which use the saved settings; a note under the bar says so.
**Reset** goes back to the saved values. To change the saved values, see
[How do I re-run with other settings?](#faq.rerun)

## MA plot {#report.ma}

The **MA** button shows the same comparison as fold change (up–down) against
mean abundance (left–right). It shows whether hits sit at low abundance, where
measurements are noisier. With no replicates at all, the report shows this
plot instead of a volcano, because there are no p-values.

## Search {#report.search}

The search box (press `/` to jump to it) marks matches on every chart and
filters the table. It understands:

- a **word**: anything whose gene, ID or description contains it (`kinase`),
- a **pasted list**, from Excel or with commas or spaces: exact genes or
  accessions; the line below says which were found and which were not,
- **wildcards**: `KRT*` (any ending), `RPL?` (one character),
- a **pattern** between slashes: `/^RPL\d+$/`,
- **`desc:`** words in the description only: `desc:ubiquitin ligase`,
- **`term:`** a gene set from the enrichment: `term:apoptosis`.

Suggestions appear as you type; use the arrow keys and Enter. After a search:
**Copy names**, **Save as group**, **Pin all**, **Clear**.

## Zoom, box select and pins {#report.select}

**Zoom** (the default): drag a box on the plot to zoom in; double-click to
zoom out. **Select**: drag a box to mark every point in it (Alt-drag does the
same in Zoom mode). The marked points get a ring on every chart and fill the
table.

**Shift-click** a point or a row to pin it. Pinned features keep their label
and appear as chips under the plot; **Profile of pinned** draws their values
side by side.

## Highlight groups {#report.groups}

**Options** → Highlight groups keeps lists of genes you care about (say, your
E3 ligases) and colours them on every volcano. Type a name and the genes, or
use **Save as group** after a search. **Export .gmt** saves them as a gene-set
file; **Import .gmt / .txt** reads one.

Groups are kept in this browser, so they appear in every Ionomos report you
open on this computer. They are not saved inside the report file.

## Hit filters {#report.hitfilters}

**Options** → Hits:

- **ignore imputation-driven hits**: a hit where at least half of one group's
  values were [imputed](#glossary.imputation) is not counted.
- **at least N peptides**: a hit identified by fewer peptides (or PSMs) is not
  counted.

Filtered hits stay on the plot in grey with a coloured ring, so you can see
what the filter removed.

## The protein panel {#report.detail}

Click a point or a row to see one feature:

- its values in every sample, per condition (a filled dot is measured, a
  hollow dot is [imputed](#glossary.imputation); the bar is the mean),
- its log2FC, [95% confidence interval](#glossary.ci) and adjusted p in every
  comparison,
- badges: *imputed*, *1 pep* (a single peptide), *only …* (on/off),
- **Behaves like**: features whose values go up and down with it across the
  samples (correlation above 0.6),
- **Copy values** (paste into Excel) and look-up links to UniProt, STRING and
  GeneCards (these open the web).

## The results table {#report.table}

One row per feature, sorted by p. Click a column header to sort. **Significant
only** shows the hits; untick it for everything. **Download CSV** saves the
rows you see (with every sample's value). **Copy up genes** / **Copy down
genes** copy gene symbols to paste into STRING, Enrichr and similar tools.

For isoDTB sites, **Proteins** groups the sites by protein. "Most sites move"
hints that the protein amount changed rather than one site.

## Low confidence and fold change only {#report.confidence}

A comparison where a group has **one sample** is still tested (it borrows the
spread from the other groups) and is labelled **Low confidence**. Treat its
hits as leads to confirm.

When no condition has replicates at all, nothing can estimate the noise, so
the comparison is **Fold change only**: no p-values, features ranked by fold
change, and a fold-change plot instead of a volcano. Ionomos never invents
p-values. See [What does "low confidence" mean?](#faq.low-confidence)

## p-value distribution {#report.phist}

A histogram of all p-values in the comparison. If most features don't change,
their p-values spread evenly (a flat floor); real changes add a peak near 0.
The dashed line is a perfectly flat floor; the coloured line is the floor
[π0](#glossary.pi0) estimates, so 1 − π0 is roughly the share that changes.

- **Peak at 0 over a flat floor**: healthy.
- **Flat**: little or no difference in this comparison.
- **Pile-up near 1**: often tied imputed values, or a model that overestimates
  the noise.
- **Bulge in the middle**: the model may not fit, for example an outlier
  sample or a hidden batch.

A strange shape means the p-values may not mean what they say; check the
[sample scorecard](#qc.card) and [PCA](#qc.pca).

## Compare comparisons {#report.compare}

Shown with two or more comparisons. The left chart puts one comparison's fold
change against another's for every feature:

- on the diagonal, **same direction in both**: a shared effect,
- **only one** comparison: a specific effect,
- **opposite** directions: worth a look.

The right chart (an UpSet plot) counts the hits found in exactly the
comparisons marked with a dark dot. Click a bar to mark those features on the
volcano.

## Only in one condition {#report.onoff}

Features measured in at least 75% (and at least two) of one group's samples
and **never** in the other. These are often the strongest biology, and a
t-test can't see them without [imputation](#glossary.imputation). Missing can
also mean "below detection", so confirm them another way. **Mark on the
volcano** shows where they landed; the table is sorted by how complete the
group is, then by abundance.

## Heatmap {#report.heatmap}

The significant features (at the saved cut-offs) in every sample. Each row is
centred on its own mean, so red means above that feature's average and blue
below; colour is not absolute abundance. Rows are clustered so similar
patterns sit together, and the coloured strip on top shows each sample's
condition. Replicates should look alike. Click a row to open it.

## Enrichment {#report.enrichment}

Which gene sets (pathways, GO terms) stand out. Two different questions:

- **Among the hits (over-representation)**: are a set's genes more common among
  the up (or down) hits than among all measured genes? Only the hits at the
  saved cut-offs count. The bars show the adjusted p (longer is stronger) and
  "k/K" how many of the set's measured genes are hits. Click a term to mark its
  genes on the volcano.
- **All ranked (no cut-off)**: a [rank-based test](#glossary.rank-test) on
  every measured gene. It finds a pathway whose members all move a little,
  which the hit lists miss. Click a term for its barcode plot: each tick is a
  member gene, placed by its rank from most up (left) to most down (right).

Gene sets are downloaded once from the Enrichr libraries and then used offline;
your gene lists never leave the computer. See
[Enrichment](#glossary.enrichment).

## Dose-response {#report.dose}

Shown when the experiment is a titration: a control (DMSO) plus at least four
doses of a compound. Doses come from `analysis.doses` in `experiment.yaml`,
or from the condition names (`Cmpd_10nM`, `Cmpd_0p1uM`, `10 µM`). Each
feature's values, as ratios to the control, get a sigmoid curve fitted
([pEC50](#glossary.pec50), slope, top and bottom), the way CurveCurator does
it.

- **Class**: *up* / *down* are real curves (significant and a big enough
  effect), *not* is flat, *unclear* is somewhere in between. Filter the
  table by class, or type a name to find a curve.
- **Potency vs effect**: each point is a curve. Right means more potent
  (higher pEC50), up and down means a bigger effect (curve fold change).
  Click a point or a table row to draw its curve over the measured points,
  with the control at the left.
- **Relevance** combines significance and effect size; sorting by it puts
  the best curves first.
- Several compounds in one experiment each get their own curves: pick one
  under **Compound**.

A curve fitted on few points or with a wide pEC50 interval is a lead to
confirm, not a measured potency. The numbers are also in
`results/dose_response.tsv`.

## Quality control {#report.quality}

Tabs of checks on the samples and the data. Start with the **Sample
scorecard** and **PCA**: if a sample stands out or the samples don't group by
condition, the comparisons can't be taken at face value. Each tab has its own
**?** with what it shows and what to do.

## Sample scorecard {#qc.card}

Each sample against the others, using [robust z-scores](#glossary.zscore):

- **IDs**: features measured in the sample,
- **missing**: the share it lacks,
- **loading**: how much more or less material it seems to have than the
  others (normalisation corrects this),
- **r with replicates**: how well it correlates with its own replicates,
- **spread**: how far its values scatter around its group,
- **CV without it**: how much its group's [CV](#glossary.cv) drops when this
  sample is left out (groups of 4 or more).

A sample gets **warn** for one strong flag and **fail** for two (or for far
fewer identifications). **What to do**: look at it in the PCA and correlation.
If it is a technical failure (a bad injection), leave it out and re-run
([How?](#faq.leave-out)). If it is real biology, keep it.

## PCA {#qc.pca}

[PCA](#glossary.pca) squeezes each sample's thousands of values into a point,
so that samples with similar profiles sit close together. Replicates of a
condition should cluster, and the conditions should separate.

The table below the plot says how much of each component is explained by the
condition and by the replicate number ([R²](#glossary.r2)). The condition
should explain the top components. If the **replicate number** explains one
(for example, all the "_1" samples together), the replicates were probably
prepared or run together: a [batch](#glossary.batch). **Colour by replicate
number** makes that visible. A sample far from its group is worth checking in
the scorecard.

## Correlation {#qc.corr}

How similar every pair of samples is (Pearson r over the features measured in
all samples), clustered; stronger colour is a higher r. Replicates of a
condition should form strongly coloured blocks. A sample that is pale against
everyone may be a failed run; a sample that sits in another condition's block
may be swapped or mislabelled.

## Missing values {#qc.missing}

Left: every feature with a gap, one row each (blue = measured, blank =
missing), one column per sample. A column with many blanks is a sample with
fewer identifications. Right: how many features have at most a given share
missing (the dashed line is 50%).

## Missing vs intensity {#qc.mnar}

For each abundance level, the share of samples in which a feature was
measured. It decides whether the [imputation](#glossary.imputation) fits:

- If low-abundance features go missing more (the line climbs to the right),
  missing values are mostly "below detection" ([MNAR](#glossary.mnar)).
  Filling them with low values (Perseus-type, the default) is appropriate.
- If features go missing at every abundance (a flat line), low-value
  imputation invents fold changes. Try imputation **knn** or **none** for this
  experiment ([How?](#faq.rerun)) and compare the hits.

## Distributions {#qc.dist}

A box per sample of its log2 values. The medians should line up after
[normalisation](#glossary.normalisation); **before** / **after** shows its
effect. A box much lower or higher before normalisation is a loading
difference. A box with a very different shape (squashed or stretched) is a
sample that behaves differently.

## CV {#qc.cv}

For each condition, a histogram of the [coefficient of variation](#glossary.cv)
of every feature across its replicates. Lower is more reproducible; the
dashed line is the median. A condition with a clearly higher median CV has a
noisy replicate (check the scorecard).

## Mean–variance {#qc.mv}

Each feature's spread across replicates (SD, up–down) against its mean
abundance (left–right), with a running median. Noise usually rises at low
abundance; hits there need a bigger fold change to be trusted.

## Abundance rank {#qc.rank}

Every feature ranked from most to least abundant: the dynamic range of the
experiment. The current comparison's hits are coloured. Hits piled at the low
end are the ones to double-check, because low-abundance values are noisier and
more often imputed.

## Identifications {#qc.ids}

Features measured per sample: before filtering (light) and in the analysis
(solid). The dashed line is the median. A sample far below the others is
probably a failed injection ([LOW_SAMPLE](#issue.LOW_SAMPLE)).

## Imputation {#qc.imp}

Where the [imputed](#glossary.imputation) values landed (orange) compared with
the measured ones (blue). With Perseus-type imputation, the imputed values
should sit at the low end of the measured values, standing in for "too little
to detect". If they overlap the middle, the imputation doesn't fit this data
(see [Missing vs intensity](#qc.mnar)).

## Power {#qc.power}

The smallest |log2 fold change| a test would detect reliably, against the
number of replicates per group, from this experiment's own noise. The band
covers the quieter and noisier half of the features. The box above it says
what your current replicates can find, and how many replicates your fold-change
cut-off would need. Use it to plan the next experiment. See
[Power](#glossary.power).

## Methods and settings {#report.methods}

A paragraph describing the analysis, ready to paste into a notebook or a
methods section, with the citations. **Settings used** lists every choice
(test, comparisons, cut-offs, filters, normalisation, imputation, samples left
out, conditions changed). The same values are in `results\analysis.json`.

## Data source {#report.source}

Where the numbers came from: the search engine and its version, the tools it
ran, the result table Ionomos read, which quantity column, the FDR filter, the
FASTA and the parameter files. Keep it with the report: it is the audit trail
for anyone who re-checks the analysis.

## The SDRF sample sheet {#report.sdrf}

`results\sdrf.tsv` describes each raw file and its sample in
[SDRF-Proteomics](#glossary.sdrf), the sample sheet PRIDE asks for when you
deposit data. Ionomos fills in what it can read (files, conditions,
replicates, fractions, labels, organism, enzyme, modifications). The report
names the columns it could not know, such as the instrument or cell type. Fill
them in before depositing, or set them once for the lab (`analysis.sdrf` in
the settings).

## Cross-check in FragPipe-Analyst {#report.fpa}

`results\fragpipe-analyst\` holds `experiment_annotation.tsv`, so the same
table can be uploaded to FragPipe-Analyst, and `reproduce_in_R.R`, which
repeats this analysis in FragPipeAnalystR. Ionomos's statistics are a port of
FragPipe-Analyst and are checked against it. See
[How do I open it in FragPipe-Analyst?](#faq.fpa)

## Files {#report.files}

Every file the analysis wrote, next to the report in `results\`:

- `<comparison>_differential.tsv`: every feature with log2FC, confidence
  interval, p, adjusted p and up/down,
- a table with all comparisons side by side,
- `volcano_<comparison>.svg`: the static plot, for slides,
- `enrichment.tsv`, `gene_set_ranks.tsv`, `sample_qc.tsv`,
  `presence_absence.tsv`, `analysis.json`, `sdrf.tsv`.

## Link, print and theme {#report.link}

The view (comparison, cut-offs, search, the open protein) is kept in the
address, so **Link** copies a link that reopens it. The link works for anyone
who opens the same report file. **Print** gives a clean page without the
buttons (or save it as PDF). **◐** switches between light and dark.
