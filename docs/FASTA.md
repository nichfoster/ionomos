# FASTA files: getting, preparing and refreshing them

`ionomos/src/ionomos/fastasource.py`, `ionomos fasta …` (D90). No Tk; standard library only. The app's buttons
for this on the Methods tab (Download from UniProt…, Add decoys and contaminants…, Check for updates; `library_tab.py`,
[LIBRARY.md](LIBRARY.md)) call the same functions off the Tk thread.

## What FragPipe needs from a FASTA

A FASTA is a text file of protein entries: a header line starting with `>` (for UniProt:
`>sp|P68871|HBB_HUMAN Hemoglobin subunit beta OS=Homo sapiens OX=9606 GN=HBB PE=1 SV=2`), then the sequence.
For a FragPipe search it needs two more kinds of entry:

- **Decoys**: every protein again, reversed, with `rev_` before its header. They estimate the false discovery
  rate. Headless FragPipe stops at once unless 40–60 % of the entries are decoys (D59 item 3,
  `fragpipe.decoy_problem`); Ionomos holds such a job.
- **Contaminants**: proteins that end up in samples by handling (keratins, trypsin, BSA …), with `contam_` before
  the header, so the search can assign their spectra and the analysis can remove them (FragPipe-Analyst's rule, and
  D87: Ionomos also reads the `contam_` accessions of the search's FASTA).

## How the lab made its FASTA so far

The PC's searches use `C:\Fragpipe_Auto\fasta\2026-01-30-decoys-contam-UP000005640_9606.fasta.fas` (human,
20,698 targets, `rev_` decoys, contaminants; PROTEOMICS_PC.md). The name is the one Philosopher gives when it is
run on an existing file (`--custom`, FragPipe's Database tab → **Add decoys**): `<date>-decoys-contam-<the file's
name>.fas`. So a file called `UP000005640_9606.fasta` was turned into it on 2026-01-30. That is the name UniProt's
FTP site gives a reference proteome's "one protein per gene" file, and 20,698 is about the size of that set for
human (the reviewed entries were 20,416 at release 2026_03). *Assumed, not confirmed with the lab:* which file it
was, and whether isoforms or unreviewed entries were wanted. Nobody had re-done it, and nothing said when it was
stale; this module does both.

## The commands

| Command | What it does |
|---|---|
| `ionomos fasta search human` | UniProt's reference proteomes for a word, a Latin name, a taxon (`9606`) or an id: id, organism, taxon, number of entries. `--all` includes non-reference proteomes |
| `ionomos fasta download UP000005640` | Downloads the reviewed entries (`--unreviewed` for all, `--isoforms` to add isoforms) as `UP000005640_9606.fasta`, then prepares it (below) into `<date>-decoys-reviewed-contam-UP000005640_9606.fasta.fas`. Default folder: `fasta_dir` from config.yaml; `--dest DIR` otherwise. `--raw-only` stops after the download |
| `ionomos fasta prepare FILE` | Adds decoys and contaminants to a FASTA without decoys: a new file beside it (or in `--dest`). `--no-contam`, `--contam FILE` (your own list), `--no-philosopher`, `--decoy-tag` |
| `ionomos fasta release [FASTA …]` | UniProt's current release, and for each FASTA (default: those in `fasta_dir`; a folder stands for the FASTAs in it) how old it is against it. A path that doesn't exist is said so, and the exit code is 1 |
| `ionomos fasta stock-workflows 24.0 [--get NAME --dest DIR]` | FragPipe's stock workflows of a release, from its source on GitHub; `--get` saves one |

All of them take `--json`. Nothing is ever overwritten: a file is written as `<name>.part` and renamed, and a name
that is taken gets `-2`, `-3`, …. The source FASTA is never changed. A destination folder with a space gets a note
(FragPipe can't use it) that suggests `fasta_dir` from config.yaml; spaces in the source's *name* become `_` in the
new file's name (`my human.fasta` → `<date>-decoys-contam-my_human.fasta.fas`), with a note.

### Download (`download_proteome`)

1. `GET https://rest.uniprot.org/uniprotkb/search?query=(proteome:UP…) AND (reviewed:true)&format=fasta&includeIsoform=false&size=0`:
   no entries, only the headers: `X-Total-Results` (the count), `X-UniProt-Release`, `X-UniProt-Release-Date`.
2. `GET https://rest.uniprot.org/uniprotkb/stream?…same query…&compressed=true`: the entries, gzip, read in 64 kB
   chunks to a hidden `.part` file (progress, stop and a time limit between chunks), unpacked to a second one.
3. The entries are counted and must equal `X-Total-Results` (the stream endpoint itself sends no count); the taxon
   is the commonest `OX=` of the first 2,000 headers. Only then is the file named `UP…_<taxon>.fasta`.

Anything that goes wrong (offline, a timeout, HTTP 400 for a bad query, a cut-off gzip, a short count, Stop)
returns an `error` in words; the `.part` files are removed and nothing else is left.

### Prepare (`prepare_for_fragpipe`)

**With FragPipe's Philosopher** (found by `find_philosopher` in `<FragPipe>\tools\Philosopher\philosopher-v*`), the
steps FragPipe's Database tab runs for **Add decoys** (`DownloadDbHelper.updateDb`, `CmdDatabaseUpdate`,
`CmdPhilosopherWorkspaceCleanInit`), in a new hidden folder inside the destination, with no window and a time
limit:

```
philosopher workspace --clean --nocheck
philosopher workspace --init --nocheck --temp <folder>\tmp
philosopher database --custom <fasta> --contam --contamprefix        (+ --prefix TAG when the tag isn't rev_)
philosopher workspace --clean --nocheck
```

Philosopher writes its `.fas` into the folder it runs in; that file is moved to its final name and the folder is
removed. If Philosopher fails (exit code, time limit, no file), the note quotes its last lines (the frames of a Go
crash report are left out: "then Philosopher crashed") and the file is made in Python instead.

A source saved as "UTF-8 with BOM" (Notepad's and Excel's UTF-8 option on Windows: `EF BB BF` before the first `>`)
crashes the Philosopher bundled with FragPipe (5.1.3-RC9; #152). Philosopher is then given a copy without the
byte-order mark, made in its hidden folder and removed with it, rather than the file being refused: Philosopher's
output depends only on the entries, so the result is byte for byte the one of the file saved without a BOM, and
the lab member has nothing to re-save. The Python way skips the mark too, and a note says it was there.

**In Python** (no FragPipe on this computer, `--no-philosopher`, or your own contaminant file), the same as
Philosopher's `dat.Create` + `dat.Save`:

- the file is read as Philosopher reads it (tabs in headers become spaces; sequence lines stripped and joined;
  `\r\n` accepted; a leading UTF-8 byte-order mark skipped); a header that appears twice keeps the later sequence;
- each contaminant is added as `contam_<header>` unless its accession (between the first two `|`) is already a
  target's: human keratins in a human proteome stay targets;
- every entry gets a decoy `rev_<header>` with its sequence reversed. Philosopher keeps a first `M` in place, and its
  loop stops one swap early for an odd-length sequence starting with `M` (the middle pair stays); copied as it is,
  so both ways give the same entries;
- entries are sorted by `>` + header (byte order), one sequence per line, `\n` line ends.

Contaminants, `contaminants='auto'` (the default): with Philosopher, its built-in list. In Python, in this order:
the `contam_` entries of an existing FragPipe FASTA on this PC (`contaminant_candidates`: the CLI passes the
FASTAs in `fasta_dir`; the library will pass what it found), else Philosopher's list from its source on GitHub
(`lib/dat/cRAPS.zip` → `crap-gpmdb.fas`, 119 entries, 3.4 MB download), else the GPM's cRAP, else none: the file
then has decoys only, its name has no `-contam`, and a note says what that costs. `PrepareResult.contaminant_source`
says which was used. A given contaminant file is always used in Python (Philosopher only adds its own list).

**Name**: `<YYYY-MM-DD>-decoys[-reviewed][-isoforms][-contam]-<source file name>.fas`, Philosopher's pattern
(`dat.Save`): the flags in its order, the source's full name as the base. Prepared from a download the base is
`UP…_<taxon>.fasta`, so the result reads like the lab's file; `reviewed` / `isoforms` are added as Philosopher adds
them when it downloads by itself.

**Checks**: a source that already has decoys is refused (decoys twice break the search), as is a file with no
entries (one saved as UTF-16, Notepad's "Unicode", is told to be saved as UTF-8), and a file with text before its
first header (`text_before_first_header`: a stray character before the first `>`, or sequence lines with no
header; the message names the line). Such text belongs to no entry, so Philosopher and a plain reader drop it, and
with it the first protein: refusing is the only way not to lose a protein silently. Blank lines and a byte-order
mark before the first header are fine. A source of 1 GB or more gets a note. The result is checked with
`fragpipe.decoy_problem` (the same 40–60 % rule as the search) and the counts are in the result: entries, targets,
decoys, contaminants, decoy share.

### Reading a FASTA

Every reader of a FASTA here skips a leading UTF-8 byte-order mark (so a file saved with one is counted in full)
and reads in bounded pieces, so a file of any size, including one without a single line break (a zero-filled or
binary file named `.fasta`), is read in linear time and with the memory of a few chunks (#156: a 1 GB zero-filled
file took 60 s and its size in memory):

- `fastasource.fasta_headers(fh, stop, deadline, chunk=…)`: the headers only, found with a byte search in big
  chunks and never split into lines; at most `HEADER_MAX` (64 kB) of one header is kept. It is the one header
  reader of `prepare_for_fragpipe`'s counts, `library.describe_fasta` (the library and `ionomos library show`) and
  `fragpipe.fasta_info` (`decoy_problem`, the fingerprint and the fake FragPipe);
- `fastasource.read_fasta` / `contaminants_from_fasta`: the entries, Philosopher's way, read in pieces of 64 kB;
  text that belongs to no kept entry is never held.

### Release and staleness

`uniprot_release()` makes one `size=0` request and reads `X-UniProt-Release` (`2026_03`) and
`X-UniProt-Release-Date` (`02-September-2026`, read without the computer's locale). `staleness(name, release)` reads
the date at the start of a FragPipe FASTA name and says, for example: *made 2026-01-30 (8 months ago); UniProt is at
2026_03 (2026-09-02), released 7 months after it: at least one release behind. For a new project, download it
again; keep this file for experiments already searched with it.* UniProt's API has no list of past release
dates, so it never claims a number of releases. Offline: `None`, and the reason in `notes`.

A newer FASTA changes results. The advice is to switch at the start of a project, never in the middle of one, and
to keep the old file (the searches name it in `ionomos.json` and in `fragpipe.workflow`; D87 reads it back).

### Stock workflows

`stock_workflows("24.0")` lists `https://api.github.com/repos/Nesvilab/FragPipe/contents/workflows?ref=24.0`
(FragPipe's release tags are `24.0`, `23.1`, … with no `v`; from 23.0 the folder is `workflows/`, up to 22.0 it was
`MSFragger-GUI/workflows/`; both are tried). `fetch_stock_workflow("24.0", "TMT10-MS3", dest)` downloads
`https://raw.githubusercontent.com/Nesvilab/FragPipe/24.0/workflows/TMT10-MS3.workflow`, checks it is `key=value`
text, and saves `TMT10-MS3.workflow` (`-2` when taken). This is only a source: the library (`library.py`, D89) ranks
workflows and the app adopts them. A stock workflow has no FASTA set; Ionomos sets the method's FASTA when it runs.
GitHub's API allows 60 requests an hour per network without a login (a 403 says so); raw files are not counted.

## Rules (all functions)

- Network requests only when a function is called (never at import), each with a timeout, none when the
  environment variable `IONOMOS_OFFLINE` is set (the tests set it). Offline or failing: a plain-words `error` or
  note in the result, never an exception to the caller.
- Long work takes `progress(message, done, total)` (total `None` when unknown) and `stop` (`threading.Event`).
- Results are dataclasses with `notes`, `error` and `as_dict()`: `DownloadResult`, `PrepareResult` (with
  `summary()`), `SearchResult`, `StockList`, `FetchResult`; `uniprot_release` returns a dict or `None`.
- Tests (`tests/test_fastasource.py`) replace `fastasource.urlopen` with canned UniProt / GitHub answers and run a
  Python stand-in for Philosopher as a real subprocess. There is no real network in a test.

## Checked against the sources (2026-10-14) vs. assumed

| Fact | How it is known |
|---|---|
| UniProt REST: `uniprotkb/search` and `uniprotkb/stream`, `query=(proteome:UP…) AND (reviewed:true)`, `format=fasta`, `compressed=true`, `includeIsoform`, `size` | **Confirmed**: UniProt's API help (`rest.uniprot.org/help/api_queries`) and requests made on 2026-10-14 (the uniprot.org help pages need a browser) |
| `X-Total-Results`, `X-UniProt-Release`, `X-UniProt-Release-Date` (format `02-September-2026`) | **Confirmed** on responses: search sends all three; stream sends the release headers but **no** count, hence the `size=0` request |
| `size=0` returns only headers | **Confirmed** (an empty body, `X-Total-Results: 20416` for human reviewed, release 2026_03) |
| Proteomes: `proteomes/search?query=…&format=tsv&fields=upid,organism,organism_id,protein_count`, `proteome_type:REFERENCE` | **Confirmed** (UniProt's `configure/proteomes/search-fields` and requests) |
| Philosopher `database` flags (`--custom`, `--contam`, `--contamprefix`, `--prefix`, `--reviewed`, `--isoform`, `--nodecoys`, `--id`) | **Confirmed**: Philosopher wiki (Database) and `cmd/database.go` (master) |
| FragPipe's Database tab runs `workspace --clean/--init`, then `database --custom <fasta> --contam --contamprefix` (Add decoys) or `database --reviewed --contam --contamprefix --id UP…` (Download) | **Confirmed**: FragPipe master `CmdDatabaseUpdate`, `CmdDatabaseDownload`, `DownloadDbHelper`, `CmdPhilosopherWorkspaceCleanInit` |
| Output name `<date>[-decoys][-reviewed][-isoforms][-contam]-<base>.fas`, base = the `--custom` file's name or the ids; written in the working folder | **Confirmed**: Philosopher `lib/dat/dat.go` `Save` |
| Contaminants deduplicated by accession; decoys = reversed with the first M kept (and the odd-length quirk); output sorted by header | **Confirmed**: `dat.go` `Create`, `reverseSeq`, `Save` (master). *Assumed*: the Philosopher bundled with FragPipe 24.0 behaves the same |
| Philosopher's contaminant list = `crap-gpmdb.fas` in `lib/dat/cRAPS.zip` (119 entries; its wiki says 116) | Zip contents **confirmed**; that the binary's embedded asset (`bindata.go.zip`) holds the same file is *assumed* |
| GPM cRAP at `ftp.thegpm.org/fasta/cRAP/crap.fasta` | *Failing on 2026-10-14* (TLS certificate for another name; plain HTTP 404). Kept as the last fallback only |
| FragPipe tags `24.0`, `23.1`, `23.0`, `22.0` (no `v`); `workflows/` from 23.0, `MSFragger-GUI/workflows/` at 22.0; raw URLs | **Confirmed** (GitHub API refs and contents) |
| Philosopher in `<FragPipe>\tools\Philosopher\philosopher-v*` (an `.exe` on Windows) | From `fragpipe.install_report`'s check; the exact file name on the PC is *not seen yet* |
| The lab's file came from UniProt's FTP "one protein per gene" `UP000005640_9606.fasta` via Add decoys | *Assumed* from its name and size (above) |

## Licences

UniProt data is CC BY 4.0 (cite UniProt). The cRAP list is free to use (The GPM). Philosopher is GPL-3.0 (checked
on GitHub); Ionomos runs the copy FragPipe installed, and its Python steps are a translation of Philosopher's
`database` logic, which Ionomos' own GPL-3.0-or-later allows. Philosopher's contaminant list is downloaded when
needed, never shipped with Ionomos.

## Open questions for the lab

- Which human FASTA do you want from now on: reviewed only (Ionomos' default, ~20,400), the one-per-gene set your
  current file looks like (~20,700, includes some unreviewed), or with isoforms? Reviewed vs. one-per-gene changes
  which proteins can be reported.
- How often to refresh (once a year? at the start of each project?), and who decides. Old files are never removed.
- Other organisms in use (mouse? yeast? spike-ins such as iRT?). Philosopher's `--add` (spike-ins) is not offered yet.
- Is the PC allowed to reach rest.uniprot.org, api.github.com and raw.githubusercontent.com? If not, download on
  another computer and use `ionomos fasta prepare` on the PC.
