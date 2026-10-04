# Glossary {#glossary}

The words the report and the app use, in plain language.

## Feature {#glossary.feature}

One row of the analysis: usually a protein (a protein group), for isoDTB a
cysteine site. The report says "proteins" or "sites" as fits the data.

## Condition {#glossary.condition}

A group of samples treated the same way: DMSO, Drug, WT, KO. It comes from
the file names (`DMSO_1.raw` is condition DMSO), and you can change it
([How?](#faq.condition)).

## Control {#glossary.control}

The condition the others are compared with, the "vs" side of every volcano
plot: the vehicle (DMSO), a mock or an untreated sample. Ionomos recognises
it by name (DMSO, vehicle, ctrl, control, mock, WT, …); otherwise it asks
you. It is one of the [roles](#glossary.role) a condition can have. A
control often has fewer samples than the treatments (two DMSO against four
of each compound); that is fine, see [Power](#qc.power).

## Role {#glossary.role}

What a condition is in the experiment: a [control](#glossary.control), a
[compound](#glossary.compound), a [competition](#glossary.competition), a
pooled reference, or a QC standard. Ionomos reads the roles from the
condition names and shows them under Methods → Settings used, in the
experiment editor and in the review window. Change them there
([How?](#faq.roles)), or with `roles:` under `analysis:` in `experiment.yaml`, for example
`roles: {DMSO: control, Probe: compound, Probe_Comp: competition of Probe}`.
With a competition among them, the comparisons follow the roles
([Specific targets](#report.specific)).

## Compound {#glossary.compound}

A condition treated with a probe or a drug alone. Every condition that is
not a control, a competition, a pooled reference or a QC standard is a
compound. Compared with the control it shows what the compound enriches or
engages.

## Competition {#glossary.competition}

A condition with the probe plus an excess of a competitor, usually the
parent compound without the handle. What the competitor takes off the probe
is bound at the competitor's site; what stays is not. Ionomos reads a
condition as a competition when its name has `comp`, `competition`,
`competitor` or `excess` as a word (`Probe_Comp`, `Probe+Comp`,
`ProbeComp`), and links it to the compound it competes by the rest of the
name. The lab has not confirmed these words: change them under
`competition_keywords`, or set the [role](#glossary.role) yourself. In an
isoDTB experiment every condition is a competition by construction (see
[Competition ratio](#glossary.competition-ratio)).

## Comparison {#glossary.comparison}

One question the statistics answer, written "treatment vs control", for
example "Drug vs DMSO". By default each condition is compared with the
control; "all pairs" and "each vs all others" are also possible. In a
[competition](#glossary.competition) experiment the default is compound vs
control, competition vs its compound, and competition vs control.

## Replicate {#glossary.replicate}

A separate sample of the same condition (a biological replicate: another dish,
another animal). Replicates are what let the statistics tell a real change from
noise. Three per condition is the usual minimum.

## Fraction {#glossary.fraction}

One piece of a sample that was split before the instrument (for example by
high-pH fractionation) and measured as its own run. The fractions of one
replicate are combined into one sample. They are *not* replicates.

## Method {#glossary.method}

How the experiment was acquired and labelled: isoDTB, TMT or DIA in this lab.
It decides which FragPipe workflow is used and how the file names are read.

## DIA {#glossary.dia}

Data-independent acquisition: the instrument fragments everything in wide
windows, so each run measures most peptides. Label-free; one sample per run.
FragPipe runs DIA-NN to quantify it.

## DDA {#glossary.dda}

Data-dependent acquisition: the instrument picks the most intense peptides to
fragment. isoDTB and TMT experiments are acquired this way.

## TMT {#glossary.tmt}

Tandem mass tags: up to 18 samples are labelled with different tags, mixed and
measured together in one run (a plex). Each sample is a "channel". Values are
compared within the plex.

## isoDTB {#glossary.isodtb}

Isotopically labelled desthiobiotin tags that label reactive cysteines. Two
samples get the light and the heavy tag and are measured together; the
heavy/light ratio of each cysteine site says how much a compound blocked it.
Ionomos tests each site's log2 ratio against 0.

## Competition ratio (R) {#glossary.competition-ratio}

In a competitive isoDTB experiment the compound-treated sample and the
control get different tags, so every condition is a
[competition](#glossary.competition) of a compound with the probe. For each cysteine, R is the control signal
divided by the treated signal. R = 1 means the compound left the site alone;
R = 4 means it blocked three quarters of the probe labelling (engagement
1 − 1/R = 75%); R = 10 means 90%. Ionomos takes R as heavy / light unless
`liganded_direction: low` says the tags are the other way round.

## Protein-corrected {#glossary.protein-corrected}

A site comparison named "… protein-corrected" is the site's log2 ratio minus
its protein's log2 change in an unenriched proteome of the same treatment
(`protein_correction` under `analysis:`), tested the way MSstatsPTM does it:
the two standard errors combined, Satterthwaite degrees of freedom,
Benjamini–Hochberg. It separates a change in the site (the compound engaging
it) from a change in how much protein there is. Sites whose protein is not in
the proteome have no corrected value and are marked "protein not found"; the
uncorrected comparison is always shown too.

## Liganded {#glossary.liganded}

A cysteine counts as liganded by a compound when its
[competition ratio](#glossary.competition-ratio) reaches a threshold in
enough replicates: by default R ≥ 4 in at least 2. A site that reaches it in
fewer replicates is *inconsistent*; one measured in too few replicates is
not assessed. See [Liganded sites](#report.cys).

## Localisation probability {#glossary.localisation}

How sure the search is that a phosphate sits on this residue and not on a
neighbouring S, T or Y of the same peptide (PTMProphet in FragPipe, DIA-NN's
own score). Sites below `phospho_min_localization` (0.75 by default) are left
out, because their changes may belong to another site.

## KSEA {#glossary.ksea}

Kinase–substrate enrichment analysis (Casado et al. 2013, as the KSEAapp
package computes it): the mean log2 fold change of a kinase's measured
substrates, compared with the mean of all sites, scaled by the number of
substrates and the spread of all sites. The kinase–substrate pairs come from
a table the lab downloads (PhosphoSitePlus), so a kinase with few known
substrates can't be scored. See [Phosphosites, kinases and partners](#report.phos).

## LFQ {#glossary.lfq}

Label-free quantification: each sample is its own run and is compared by its
measured intensities (FragPipe's IonQuant).

## Intensity {#glossary.intensity}

How much signal the instrument saw for a feature in a sample. More protein
gives more intensity, but intensities are only comparable for the same feature
across samples.

## pEC50 and EC50 {#glossary.pec50}

EC50 is the concentration that gives half of a compound's maximal effect.
pEC50 is −log10 of it in molar: an EC50 of 1 µM is a pEC50 of 6, and 10 nM is
8. Higher pEC50 means more potent. Each step of 1 is ten times more potent.

## log2 fold change {#glossary.log2fc}

How much a feature changed between two conditions, on a log2 scale: 1 means
twice as much in the treatment, −1 means half, 0 means no change, 2 means four
times. Log2 makes "up" and "down" symmetric.

## p-value {#glossary.pvalue}

How surprising the difference would be if the feature did not really change.
A small p (say 0.001) means random noise rarely gives a difference this large.
It is not the chance that the change is real. With thousands of features, some
small p-values happen by chance alone, which is why the report uses the
[adjusted p](#glossary.adjp).

## Adjusted p (FDR, BH) {#glossary.adjp}

The p-value corrected for testing thousands of features at once
(Benjamini–Hochberg). Calling everything with adjusted p ≤ 0.05 a hit means
that, on average, about 5% of those hits are false. That share is the
**false discovery rate (FDR)**. This is the default cut-off in the report.

## Significant, hit {#glossary.hit}

A feature that passes both cut-offs: a big enough fold change and a small
enough (adjusted) p. "Up" hits are higher in the treatment, "down" hits lower.

## limma, moderated t-test {#glossary.limma}

The standard statistics for proteomics with few replicates. With three
replicates a plain t-test's estimate of the noise is very rough. limma borrows
strength from all features: each feature's noise estimate is pulled towards
the typical noise of the experiment (empirical Bayes). The result, the
moderated t, gives more reliable p-values. It is what FragPipe-Analyst uses.

## Welch's t-test {#glossary.welch}

A plain two-group t-test that allows the groups to have different noise. Used
when limma can't run, and available as a setting.

## Confidence interval {#glossary.ci}

The range the true log2 fold change is likely to be in (95% of the time). A
narrow interval is a precise estimate; one that crosses 0 means the direction
of the change isn't certain.

## Imputation {#glossary.imputation}

Filling in missing values so that every feature can be tested. The default for
label-free data (Perseus-type) draws values from the low end of each sample's
measured values, because a feature is usually missing when there was too little
to detect. Imputed values are made up, so a hit that rests on them is flagged,
and the report draws them as hollow dots. TMT data is not imputed.

## MNAR and MAR (missing values) {#glossary.mnar}

**Missing not at random (MNAR)**: a value is missing because the feature was
below detection, so low-abundance features go missing more often. **Missing at
random (MAR)**: values go missing for other reasons (a failed run, a
mismatched identification) at any abundance. Low-value imputation only fits
MNAR. The QC tab "Missing vs intensity" tells which you have.

## Normalisation {#glossary.normalisation}

Making samples comparable when they had slightly different amounts of material
loaded. The default shifts each sample so that all samples have the same
median. It removes a loading difference, not biology. When many features
change in one direction (a pulldown), the median itself moves; Ionomos then
normalises on the features that stay stable instead
([why](#issue.NORMALISATION_COMPOSITION)).

## Contaminants {#glossary.contaminants}

Proteins that get into every sample from outside the experiment: keratins
from skin and dust, trypsin, serum albumin. The FASTA marks them (`contam_`),
and the analysis removes them by default.

## CV (coefficient of variation) {#glossary.cv}

The spread of a feature's replicate values relative to its mean (the standard
deviation divided by the mean). A CV of 20% means replicates typically differ
by about a fifth. Lower is more reproducible.

## PCA {#glossary.pca}

Principal component analysis: a way to draw thousands of values per sample as
one point, so that samples with similar profiles sit close together. PC1 is the
direction in which the samples differ most, PC2 the next, and so on; the %
says how much of all the variation each one holds.

## R² {#glossary.r2}

The share of something's variation that a grouping explains, from 0 (none) to
1 (all). In the PCA table, an R² of 0.9 for condition means the component is
almost entirely about the conditions.

## Batch {#glossary.batch}

A technical grouping that shows in the data: samples prepared on the same day,
digested together, or run next to each other. In this lab, replicate 1 of
every condition is usually prepared together, so a component that follows the
replicate number suggests a batch. A balanced batch weakens the statistics but
doesn't fake results; an unbalanced one can.

## Robust z-score {#glossary.zscore}

How far a sample is from the others, in units of their typical spread. It uses
the median and the median absolute deviation (MAD) instead of the mean and SD,
so one bad sample can't hide itself by stretching the scale. Beyond about 3 is
unusual.

## Correlation (Pearson r) {#glossary.correlation}

How closely two sets of values go up and down together, from −1 to 1.
Replicates of good quality usually correlate above 0.95.

## π0 {#glossary.pi0}

The estimated share of features that do not change in a comparison, read from
the p-value histogram (Storey's method). π0 = 0.8 means roughly 20% of
features change in some way, whether or not they pass your cut-offs.

## Power {#glossary.power}

The chance that an experiment detects a real change of a given size. More
replicates and less noise give more power. The Power tab turns it around: the
smallest change you can expect to detect.

## Gene set, pathway, GO term {#glossary.geneset}

A list of genes that belong together: a pathway (Reactome, KEGG), a Gene
Ontology term, or a Hallmark set. Enrichment asks whether a set stands out in
your results.

## Enrichment {#glossary.enrichment}

Testing whether gene sets stand out in a comparison.
[Over-representation (ORA)](#glossary.ora) looks at the hit list;
the [rank-based test](#glossary.rank-test) looks at every measured gene.

## Over-representation (ORA) {#glossary.ora}

Are a set's genes more common among the hits than among all genes measured in
this experiment? A one-sided Fisher (hypergeometric) test, adjusted for
testing many sets. It depends on the cut-offs: no hits, no result.

## Rank-based test {#glossary.rank-test}

Every measured gene is ranked by its statistic, from most up to most down. A
set scores when its members sit together near the top or the bottom, even if
none of them passes the cut-offs. Ionomos corrects for genes in a set that
move together anyway (the idea behind limma's camera), so it doesn't
over-state co-regulated sets.

## Volcano plot {#glossary.volcano}

Fold change (left–right) against −log10 p (up). Big changes with small
p-values end up in the top corners, like the plume of a volcano.

## MA plot {#glossary.ma}

Fold change (up–down) against mean abundance (left–right). Shows whether
changes depend on how abundant a feature is.

## Heatmap {#glossary.heatmap}

A grid of colours: one row per feature, one column per sample. Similar rows
and columns are placed next to each other (clustering).

## Peptides and PSMs {#glossary.peptides}

A protein is identified from its peptides (the pieces trypsin cuts it into).
A PSM is one spectrum matched to a peptide. A protein seen through a single
peptide is less certain.

## FASTA {#glossary.fasta}

The file of protein sequences the search compares spectra against, for
example all reviewed human proteins. It must match the species, and it holds
decoys and contaminants too.

## Decoy {#glossary.decoy}

Reversed protein sequences (named `rev_…`) added to the FASTA. Matches to
decoys can only be wrong, so counting them lets FragPipe control the false
discovery rate of its identifications. A FASTA without decoys can't be used.

## FDR (in the search) {#glossary.fdr}

In the search, the false discovery rate of the identifications: FragPipe keeps
peptides and proteins at 1% FDR by default. This is separate from the
[adjusted p](#glossary.adjp) of the statistics.

## FragPipe and DIA-NN {#glossary.fragpipe}

The search software. FragPipe (with MSFragger, IonQuant, TMT-Integrator)
identifies and quantifies peptides and proteins from the `.raw` files; for DIA
it runs DIA-NN. Ionomos runs them without their windows (headless) and reads
their result tables.

## Workflow {#glossary.workflow}

A FragPipe settings file (`.workflow`): every search parameter for one method.
The lab pins one per method, so every experiment of that method is searched the
same way.

## Inbox {#glossary.inbox}

The shared folder you drop experiments into (`C:\Fragpipe_Auto\inbox`). It is
the only folder you need to touch.

## experiment.yaml {#glossary.experiment-yaml}

A small text file in an experiment folder with the answers and choices for
that experiment: corrected conditions, the control, comparisons, samples left
out, cut-offs. The windows write it for you; you can also edit it by hand.

## SDRF {#glossary.sdrf}

SDRF-Proteomics: a standard table describing each raw file and its sample
(organism, condition, replicate, label, instrument). Public repositories such
as PRIDE ask for one.
