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

## How do I say which condition is DMSO, the compound or the competition? {#faq.roles}

In the experiment editor (tab 7, or the pop-up window), **Roles** lists each
condition with its number of samples and its [role](#glossary.role): control,
compound, [competition](#glossary.competition) of a compound, pool /
reference, or QC standard, and where the role came from (usually the name).
Select a condition and pick another role in the list; **automatic** goes back
to what the name says. The review window before filing has the same list
for DIA and label-free drops.

- A role marked **?** is a guess: a word like `pre`, `block`, `cold` or `10x`
  in the name often, but not always, means "plus a competitor". Press
  **Confirm** if it is right, or pick **compound** if it is not.
- Under the list, the comparisons that will be run, in words, and what the
  group sizes mean. Two DMSO against four of each compound is normal: with
  missing values imputed (DIA) every protein is still tested; without (TMT) a
  protein needs one of the two DMSO values.
- Making a condition the control also sets the **Control** box.

The roles are saved in `experiment.yaml`, for example
`analysis: {roles: {Probe_pre: compound}}`, and the next **Run analysis** uses
them.

## How do I get a figure into a slide? {#faq.slides}

- **One figure**: set up the view (comparison, cut-offs, search), then use
  **SVG** or **PNG** at the chart's top right, or **Export…** and **Copy
  image**, and paste it into the slide.
- **All of them**: **Export for slides** in the report's top bar saves a .zip
  with every figure, the tables as CSV and a README that lists the files and
  the cut-offs.
- **The look**: **Export…** sets the size (16:9, 4:3, half a slide, a journal
  column), text size, font, colours and what is drawn. **Save style** gives a
  file the whole lab can load. The lab's defaults are set in the app
  ([Figure style](#faq.figure-style)).
- **Without opening the report**: `ionomos export <experiment folder>` writes
  the volcano, PCA, heatmap and correlation plots, and the dose-response,
  time-course and liganded-site figures, as SVG to `results\figures\`, at the
  saved cut-offs. `--list` shows what the report has; `--figures dose,pca`
  picks some; `--features EGFR,BTK` chooses the curves or sites drawn
  (default: the six most relevant, `--top N` for more). `--format png` (or
  `both`) also makes PNG if the PC has resvg, Inkscape or cairosvg; without
  one it says what to install.

SVG stays sharp and its text can be edited in PowerPoint, Illustrator or
Inkscape; PNG is a picture. Each file records the cut-offs it was made with,
so a figure on a slide can be traced back to its report.

## How do I set the lab's figure style? {#faq.figure-style}

In the app: **7 Analysis** → **Figure style**, then **Save** (bottom right).
Every report's **Export…** starts from this style, and `ionomos export` uses it.

- **Size**: a 16:9 or 4:3 slide, half a slide, a journal column (85 mm) or
  two columns (180 mm), or **Custom** with your own width, height and unit.
- **Text size (pt)**: the size of the text in the finished figure. Leave it
  empty for the size that suits the preset (14 pt on a slide, 7 pt in a
  journal column); it follows the preset when you change it.
- **Font**: its name, for example Arial or Segoe UI. It must be installed on
  the computer where the figure is opened.
- **Colours**: default, colorblind (a colour-blind-safe set), grey (for
  print), or custom: then type or **Pick…** the colours of up hits, down hits
  and the rest. **Background**: light, dark or transparent.
- **"Export for slides" .zip holds**: SVG and PNG of every figure, or only one.
- **Written after each analysis**: tick volcano, PCA, heatmap or correlation
  to get them as SVG in `results\figures\` of every new analysis. None
  ticked writes none (the report always has every figure).

**Check** shows the result in one line, or what is wrong. A field left empty
takes its default. Settings the page doesn't show (line width, title on or
off, …) can still be set in `config.yaml` under `analysis: export:`; the app
keeps them.

## How do I share the report? {#faq.share}

`report.html` holds all its data and works offline, so you can email it or put
it on a shared drive; it opens in any browser. **Link** copies a link to the
current view (comparison, cut-offs, search, open protein). Send the link to
someone who has the same file (for example on the lab drive), or paste the
part after `#` into the address of their copy.

## How do I send a problem, or my results, to be checked? {#faq.bundle}

Press **Report a problem…** (bottom right in the app, in a failed-search or
failed-analysis window, or **Zip for troubleshooting…** on the Jobs tab for
the selected job). Type a sentence, choose the jobs, and press **Save the
zip**. The window lists what will go in and how large it will be.

- Leave the boxes as they are to send a problem: settings, logs and the
  search logs.
- Tick **Include the search's result tables…** when the numbers are to be
  checked: the analysis can then be run again from the zip.
- Names are replaced by pseudonyms unless you untick that box.

The zip is saved on the Desktop and shown in a folder window. Ionomos sends
nothing: copy the zip to where it should go (a shared Dropbox folder, an
email). The file next to it with `KEY` in its name stays in the lab. See
[The zip for troubleshooting](#safety.bundle) for what is in it and what the
replacing of names cannot do.

From a terminal: `ionomos bundle 12 --level validate` (job 12), and
`ionomos bundle translate <key file> answer.txt` puts the real names back
into an answer you received.

## How do I open the data in FragPipe-Analyst? {#faq.fpa}

In FragPipe-Analyst, upload the quantification table from `fragpipe\` (for
DIA the `pg_matrix.tsv`, for label-free `combined_protein.tsv`) and, as the
experiment annotation, `results\fragpipe-analyst\experiment_annotation.tsv`.
In R, `results\fragpipe-analyst\reproduce_in_R.R` repeats Ionomos's analysis
with FragPipeAnalystR.

## Does Ionomos give the same result as my other analysis? {#faq.compare-reference}

Check it on your own experiment: in the app, **7 Analysis** →
[Check accuracy](#faq.check-accuracy) → **Compare**, or `ionomos compare
<experiment folder> <reference>`. It compares the Ionomos analysis with
another result for the same experiment. The reference can be:

- a results table with a fold-change and a p-value column per comparison: a
  FragPipe-Analyst export, a limma or MSstats table, a Perseus matrix, the
  output of the lab's R script (`.tsv`, `.csv`, `.txt`, `.xlsx`),
- or another folder analysed by Ionomos (for example with other settings).

It matches the proteins by ID or gene, then reports per comparison: how many
matched, how well the [log2 fold changes](#glossary.log2fc) agree (correlation,
slope, offset), which hits both call, the proteins that disagree most, and how
the p-values compare. It ends with one line: **agrees**, **agrees after an
offset of …** (a normalisation difference), **differs: …** with the reason, or
**not judged** when too few proteins matched.

- It writes `compare.html` (scatter plots, tables), `compare.tsv` and
  `compare.json` into the experiment's `results\` folder. Neither result is
  changed.
- The limits behind the verdict are printed at the bottom of `compare.html`.
  They are Ionomos's own choice; the numbers are what to read.
- "differs" is not "wrong". Two tools that impute missing values differently
  disagree on exactly those proteins; the list of largest disagreements shows
  them.
- After the next analysis of the folder, the verdict shows under
  [How far to trust this](#report.trust).

## How accurate is the analysis? {#faq.benchmark}

Two ways to measure it, both with `ionomos benchmark` or, in the app,
**7 Analysis** → [Check accuracy](#faq.check-accuracy) → **Run benchmark**:

- **On simulated data** (no data needed): `ionomos benchmark` runs the
  analysis on made-up protein tables where the changed proteins are known. It
  tries 2 to 6 replicates, 2 controls against 4 treated, small and large
  changes, few and many missing values, with each imputation and normalisation
  setting. For each it reports how many planted changes were found, what share
  of the calls were false (against the 5% the cut-off promises), and whether
  the fold changes are biased. `--like <experiment folder>` adds that
  experiment's own settings and group sizes. `--grid quick` takes seconds.
  `--kind isodtb` does the same for isoDTB site ratios (and shows what a
  heavy / light mixing error does), `--kind tmt` for several TMT plexes with
  a pooled reference (and what IRS and the normalisation do in a pulldown).
- **On a real sample with known ratios**: see
  [a benchmark sample](#faq.benchmark-sample).

The page (`benchmark_simulated.html`) says what each setting costs. A
simulation shows how the method behaves on data like the simulation. It does
not show how your samples behave: only a real benchmark sample does.

## How do I run a benchmark sample on our instrument? {#faq.benchmark-sample}

Use a mix whose ratios you know. The usual one is human, yeast and E. coli
digests mixed in two ratios ("HYE"): for example sample A = 65% human, 30%
yeast, 5% E. coli and sample B = 65% human, 15% yeast, 20% E. coli. Human is
then unchanged, yeast is halved and E. coli is 4 times higher in B.

1. Acquire at least 3 runs of A and 3 of B with your normal method.
2. Search them with a FASTA that holds all three species, and let Ionomos
   analyse the folder as usual (A is the control).
3. Write a small text file, for example `hye.yaml`:
   `expected: {HUMAN: 1, YEAST: 0.5, ECOLI: 4}` (the ratio B / A per species;
   1 means unchanged). Add `comparison: B vs A` if the analysis has several.
4. Run `ionomos benchmark <experiment folder> --expected hye.yaml --open`.

The page shows, per species, the measured against the expected ratio (median,
spread, a box plot), how many unchanged human proteins were called anyway
(false positives) and how many changed proteins were found. Species are read
from the UniProt names in the result table (`ACTB_HUMAN`); `fasta:` or
`species_column:` in the file help when they are not there. A spike-in works
the same way with `proteins:` lists instead of species. The result is also
shown under [How far to trust this](#report.trust) after the next analysis.

## How do I check the analysis from the app? {#faq.check-accuracy}

**7 Analysis** → **Check accuracy** runs the same checks as `ionomos compare`
and `ionomos benchmark`, with buttons. Both read results and change neither;
the page they write opens by itself when they finish, and the verdict is
shown in colour above the output (green: agrees or done, orange: differs,
red: could not run, with the reason).

- **Compare**: pick the **Ionomos analysis** (an analysed experiment folder;
  the experiment open on the first page is filled in) and the **Reference**:
  a results table (**Table…**) or another analysed folder (**Folder…**).
  [What the verdict means](#faq.compare-reference). Tick "the other way
  round" when the reference compares DMSO vs Drug instead of Drug vs DMSO.
- **Benchmark on simulated data**: **quick** takes seconds, **standard**
  about a minute. "With the settings of" an analysed experiment adds its
  settings and group sizes. The page goes into `logs\ionomos_benchmark\`, or
  into that experiment's `results\` folder.
- **Benchmark on a benchmark sample**: the analysed experiment and its
  expected-ratios file ([how to make one](#faq.benchmark-sample)).

The app stays usable while a check runs. **Copy the command line** gives the
same check for a terminal.

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

## Can I get a message when my search is done? {#faq.notify}

Yes, if the person who looks after Ionomos turns it on. It is off by
default, because nothing leaves the PC unless the lab asks for it
([what a message holds](#safety.notify)).

To set it up, open the app's **8 Notifications** tab, tick **Send
notifications** and fill in at least one of:

- **Slack**: a Slack incoming-webhook address
- **Microsoft Teams**: a Teams webhook address (in Teams: Workflows, "Post
  to a channel when a webhook request is received")
- **Webhook (JSON)**: any service that accepts a JSON POST
- **Email**: the server, port, security, From and To and, if the server
  needs them, the user name and password

The ticks "when a search is done / failed / waiting" choose which events
send a message; "waiting" means a search is waiting for something (a FASTA,
disk space). A waiting search sends one message per reason, not one every
few seconds. Addresses and the password are shown as dots; tick **Show
addresses and password** to check what you typed. To keep an address or the
password out of `config.yaml`, put it in an environment variable and give
its name under "or variable".

Press **Send test**: it sends a test message to each one and says which
arrived (same as `ionomos notify-test`). Then **Save**, and restart the
watcher (**5 Run & Test** → **Stop**, **Start**) so it uses the new
settings. Everything on the tab is also `notify:` in `config.yaml`. A message that can't be sent never fails or slows a
search ([A notification did not arrive](#trouble.notify)).

## Can I ask Ionomos a question in plain words? {#faq.assistant}

Yes, if your lab has set up the assistant. In a window that says something
needs attention, or in the **needs attention** list, press **Ask about this**.
A window opens with a question already written for that problem; change it if
you like and press **Ask**. A model on this computer can take a minute or two;
the rest of Ionomos keeps working meanwhile. In a terminal: `ionomos ask "why
did my search fail?" --experiment 12` (the job number is in the Jobs tab; an
experiment's name works too).

It answers from that job's log, what the analysis found and this help, and
each statement ends with its source in square brackets, such as
`[log:12#41]` (line 41 of job 12's search log) or `[help:faq.rerun]`
(`ionomos help faq.rerun` shows it). Under the answer, **Sources** shows what
each of those says, as Ionomos read it.

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

While a search runs, the lab may have set the assistant to use a smaller
model, or to pause so the search keeps the computer to itself
(`assistant.while_searching`). A paused assistant says so, and you get
Ionomos's own explanation, as above. Before choosing a model, the person who
looks after Ionomos measures it on this computer with `ionomos ask-eval`.

## Where is this help, and can I change it? {#faq.help}

The report has a **Help** section at the end and a **?** beside each section.
The full help opens from the app's **Help** button, or with `ionomos help
--open` (`ionomos help NO_TABLE` jumps to one topic). It is written in plain
text files inside Ionomos (`ionomos/src/ionomos/help/`); corrections are
welcome through **Report a problem…** or on GitHub.
