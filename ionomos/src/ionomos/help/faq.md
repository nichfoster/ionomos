# Questions {#faq}

## How do I re-run the analysis with other settings? {#faq.rerun}

On the lab PC: app → tab 7 **Analysis** → **Analyse an experiment**, pick the
experiment, change what you need (cut-offs, imputation, normalisation,
comparisons), then **Run analysis**. The choices are saved in the experiment's
`experiment.yaml` under `analysis:`, so later re-runs keep them. The Jobs tab
has **Re-run analysis** and **Analysis options…** for a finished job. The
search is not repeated; only the statistics and the report are made again.

On any computer: `ionomos analyze <folder> --log2fc 0.58 --imputation none`
(see `ionomos analyze --help`). Lab-wide defaults are on tab 7 → **Lab
defaults**.

## How do I change a sample's condition? {#faq.condition}

In the experiment editor (tab 7, or the pop-up window): double-click the
sample's condition, or select it and press **Change condition…**, then **Run
analysis**. **Guess from names** fills in conditions from the file names. The
change is saved in `experiment.yaml` as `sample_conditions`, for example:

- `analysis: {sample_conditions: {Drug_4: DMSO}}`

## How do I leave a sample out? {#faq.leave-out}

In the experiment editor, select the sample and press **Leave out / use**,
then **Run analysis**. Its raw file stays in the folder and is still listed in
the sample sheet; it is only left out of the statistics. Press the button again
to bring it back. On the command line: `ionomos analyze <folder> --exclude
DMSO_3`.

## How do I pick the control or the comparisons? {#faq.control}

In the experiment editor, under **Comparisons**: "each condition vs control"
with the control picked in the box beside it, "all pairs", "each condition vs
all others", or "just these" (for example `Drug vs DMSO; Drug2 vs DMSO`). Then **Run analysis**. On the command
line: `--control DMSO`, `--compare "Drug vs DMSO"`, `--de-type all`.

## How do I get a plot into my slides? {#faq.slides}

Every chart in the report has **SVG** and **PNG** buttons at its top right.
SVG stays sharp and can be edited in PowerPoint, Illustrator or Inkscape; PNG
is a high-resolution picture. Set up the view first (cut-offs, labels, search
matches); the download shows what you see. `results\volcano_<comparison>.svg`
has each volcano plot at the saved cut-offs.

## How do I share the report? {#faq.share}

`report.html` holds all its data and works offline, so you can email it or put
it on a shared drive; it opens in any browser. **Link** copies a link to the
current view (comparison, cut-offs, search, open protein). Send the link to
someone who has the same file (for example on the lab drive), or paste the
part after `#` into the address of their copy.

## How do I open the data in FragPipe-Analyst? {#faq.fpa}

In FragPipe-Analyst, upload the quantification table from `fragpipe\` (for
DIA the `pg_matrix.tsv`, for label-free `combined_protein.tsv`) and, as the
experiment annotation, `results\fragpipe-analyst\experiment_annotation.tsv`.
In R, `results\fragpipe-analyst\reproduce_in_R.R` repeats Ionomos's analysis
with FragPipeAnalystR.

## What does "low confidence" mean? {#faq.low-confidence}

A group in that comparison has only one sample. The comparison is still tested
(the noise is estimated from the other groups), but one sample can't show how
much it varies on its own, so treat its hits as leads to confirm. "Fold change
only" means no group had replicates: there are no p-values at all. See
[Low confidence and fold change only](#report.confidence).

## Why are there no hits? {#faq.no-hits}

Maybe there is no large difference. Check first: do the samples separate by
condition in the [PCA](#qc.pca)? Is one replicate an outlier in the
[scorecard](#qc.card)? The [p-value histogram](#report.phist) says whether
there is signal below the cut-offs (a peak near 0 with no hits means the
cut-offs are strict for the replicates you have). The [Power](#qc.power) tab
says how big a change your replicates can detect.

## Why don't the TSV files match what I see? {#faq.tsv}

The cut-offs in the report are for exploring and change only the page. The
TSV files, the heatmap and the enrichment use the saved settings. To change
those, re-run the analysis with the new settings ([How?](#faq.rerun)).

## How do I re-run the search itself? {#faq.redo-search}

Only a failed search can be retried (app → Jobs tab → **Retry**, or
`ionomos retry <job>`); the earlier output is kept. To search a finished
experiment again with other settings, drop a new copy of the raw files as a
new folder (for example with `_redo` in the name).

## Can I ask Ionomos a question in plain words? {#faq.assistant}

Yes, if your lab has set up the assistant: `ionomos ask "why did my search
fail?" --experiment 12` (the job number is in the Jobs tab; an experiment's
name works too). It answers from that job's log, what the analysis found and
this help, and each statement ends with its source in square brackets, such as
`[log:12#41]` (line 41 of job 12's search log) or `[help:faq.rerun]`
(`ionomos help faq.rerun` shows it).

- It only reads. It cannot retry, change, move or delete anything, whatever
  you type. Use the buttons in the windows for that.
- It runs on this computer. Nothing you ask and nothing about your data is
  sent anywhere ([Your data stays on the computer](#safety.private)).
- If it cannot back an answer with a source, it does not answer. You then get
  Ionomos's own text: the likely causes, what to do, and the help entry.
- It does not know FragPipe settings or statistics beyond what Ionomos did
  with your data. Ask the person who looks after Ionomos for those.

## The assistant says it is not set up {#faq.assistant-setup}

That is a normal state, and everything else works without it. You still get
Ionomos's own explanation of the problem and the matching help. Setting it up
is a job for the person who looks after Ionomos: a model has to be installed
on this computer and named under `assistant:` in `config.yaml`. `ionomos
check` has a row named assistant that shows its state. "Not this PC" means the
address in `config.yaml` points at another computer; Ionomos refuses that and
sends nothing.

## Where is this help, and can I change it? {#faq.help}

The report has a **Help** section at the end and a **?** beside each section.
The full help opens from the app's **Help** button, or with `ionomos help
--open` (`ionomos help NO_TABLE` jumps to one topic). It is written in plain
text files inside Ionomos (`ionomos/src/ionomos/help/`); corrections are
welcome through **Report a problem…** or on GitHub.
