# What Ionomos will never do to your data {#safety}

Your raw files are the one thing that can't be made again. These rules are
built into Ionomos and checked by its tests.

## It moves, it never deletes {#safety.moves}

Taking a folder in is a move. On the same drive it is a rename, which is
instant and can't half-happen. Between two drives, Ionomos copies the folder,
checks every copied file against the original (byte for byte, with a
checksum), and removes the inbox copy only when every file matches. If
anything doesn't match, the original stays where it was.

The **Delete** buttons in the app's Inbox tab and the naming window don't
delete either: they move the item into the hidden `inbox\.removed\` folder,
where you can get it back (**Open removed items**).

## It never overwrites {#safety.overwrite}

If a folder of the same name was already filed for you, the new drop is
refused with a note ("add `_redo`"); the old one is not touched. Renaming files
to remove spaces never replaces another file. A TMT `annotation.txt` you put
next to the raw files is kept as it is.

## A failure leaves everything in place {#safety.failures}

A folder that can't be taken in stays in the inbox with a `.REJECTED.txt`
note. A search that fails leaves the experiment folder as it was, with
`FAILED.txt` and the full log, until you press Retry. An analysis that fails
still writes every volcano plot it can and says what went wrong.

## Earlier attempts are kept {#safety.attempts}

When a search runs again (Retry, or after an interruption), the previous
output is moved aside as `fragpipe_previous_<time>\` (or
`diann_previous_<time>\`), never deleted. Importing a workflow keeps the
previous copy. Every change to the settings keeps a backup of the old
`config.yaml`.

## Where it writes {#safety.where}

In your experiment folder Ionomos only adds its own things: `ionomos.json`,
`ionomos_run\`, `fragpipe\` (FragPipe's output), `results\`, and `DONE.txt` or
`FAILED.txt`. Your answers in its windows are saved in `experiment.yaml`; your
other entries in that file are kept. File and folder names with spaces or
symbols are made safe for FragPipe on the way in, and the original names are
recorded in `ionomos.json`.

`results\` belongs to Ionomos: re-running the analysis replaces the report and
tables in it. Copy the folder first if you want to keep an old report. For a
table analysed on its own, everything goes into a new `<table name>_ionomos\`
folder next to it, and the files beside it are never touched. The demo only
writes into a new, empty folder.

## Your data stays on the computer {#safety.private}

Enrichment downloads public gene-set libraries once; after that the tests run
on the computer, and your gene lists are never sent anywhere. The report is
one file with no links that load anything from the internet (the UniProt,
STRING and GeneCards links open only when you click them). **Report a
problem…** collects settings, logs and the search logs of failed jobs, never
raw data or result tables, and saves it on your Desktop for you to send.

## Notifications: off unless the lab turns them on {#safety.notify}

Ionomos can send a message (Teams, Slack, email or another service) when a
search is done, failed or waiting. This is **off** until someone sets it up
in `config.yaml` under `notify:`. When it is on, a message holds exactly
this and nothing else:

- the status: done, failed or waiting
- the experiment's folder name, your user folder's name and the method
- the job number, the time and the name of the PC
- for a failure or a wait: the reason, as in `FAILED.txt` or the Jobs tab
- for a finished search: how many hits each comparison has (up, down, of
  how many tested), and where the report is on the PC

It never holds a raw file, a result table, a protein or site name, an
intensity or any other measured value, or the report itself. The path of
the report is only text: the report stays on the PC.

With `notify.include_names: false` a message holds only the job number,
the status and the time ("Ionomos: job 12 done").

The webhook addresses and the email password in `config.yaml` are secrets.
Ionomos does not write them to its log, and **Report a problem…** and
`ionomos diagnose` leave them out (three stars are shown instead). See
[Can I get a message when my search is done?](#faq.notify).

## Updates and uninstalling leave your data alone {#safety.updates}

Installing an update replaces only the program. Uninstalling removes the
program and keeps `C:\Fragpipe_Auto` (settings, job list, logs) and every
experiment in `C:\Fragpipe_General`.
