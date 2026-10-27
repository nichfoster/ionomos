"""
`ionomos example [FOLDER]`: real, published proteomics results downloaded and analysed, to see Ionomos on real data.

    ionomos example                     # downloads 20.9 MB once, writes ./ionomos_example/, three reports
    ionomos example --list              # what the data are, where they come from, their licence
    ionomos example --dataset apms      # only one of them (apms | dia | tmt; repeatable)
    ionomos example --zip PATH          # use a copy of the zip downloaded by hand (no network)

The data (docs/EXAMPLE.md): the example datasets of FragPipe-Analyst's own paper and tutorials (Hsiao et al.,
J. Proteome Res. 2024, doi:10.1021/acs.jproteome.4c00294), one zip on Zenodo (doi:10.5281/zenodo.13147961, CC BY
4.0). Ionomos never ships them (D36, D96): they are fetched from Zenodo, checked against the size and checksums
below, and only the tables used here are unpacked:

    ionomos_example/
      README.txt                       the source, the licence, the three experiments, what to look at
      download/FragPipe_Analyst_Manuscript_Datasets.zip   the download, kept (re-runs need no network)
      apms/  combined_protein.tsv      FragPipe label-free (IonQuant): CCND1 pull-down vs control, 3 vs 4
             experiment_annotation.tsv the authors' sample sheet for FragPipe-Analyst
             experiment.yaml           the analysis settings Ionomos reads
             README.txt                this experiment, and what the report should show
             results/report.html       written by the analysis
      dia/   diann-output.pg_matrix.tsv  DIA (DIA-NN in FragPipe): kidney tumour vs normal tissue (CPTAC ccRCC)
      tmt/   ratio_protein_MD.tsv      TMT-11, four plexes (TMT-Integrator): the same tumours

The tables are copied out of the zip byte for byte; experiment.yaml says which condition each sample is (from the
authors' experiment_annotation.tsv, as FragPipe-Analyst reads it) and leaves out the samples the authors left out.

Rules (as demo.py and fastasource.py): nothing that exists is written over. The folder must be new or empty (the
default name moves on to ionomos_example_2, ...); every file is created exclusively. A download goes to a temporary
`.part` file in the folder and gets its name only after its size, MD5 (Zenodo's) and SHA-256 match; a download
that fails or does not match removes only its own `.part`. A zip the user gave (--zip, or one found from an
earlier run) is read, never moved or changed. Network use: one HTTPS GET to zenodo.org, with a timeout, never
when IONOMOS_OFFLINE is set; offline, the message names the URL to fetch by hand and the command that uses it.
No Tk, standard library only; the analysis is the same code path as `ionomos analyze <folder>`, without a lab
config. The tests replace `urlopen` here (`example.urlopen`).
"""
from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

urlopen = urllib.request.urlopen  # the one way out to the network; tests replace it

DEFAULT_NAME = "ionomos_example"
OFFLINE_ENV = "IONOMOS_OFFLINE"
CHUNK = 1 << 16
TIMEOUT_S = 60          # per read; a stalled connection ends with a sentence, not a hang
TIME_LIMIT_S = 1800     # the whole download


@dataclass(frozen=True)
class Archive:
    name: str
    url: str
    size: int
    md5: str
    sha256: str
    record: str
    doi: str
    licence: str
    licence_url: str
    citation: str
    prefix: str  # the folder inside the zip


ARCHIVE = Archive(
    name="FragPipe_Analyst_Manuscript_Datasets.zip",
    url="https://zenodo.org/records/13147961/files/FragPipe_Analyst_Manuscript_Datasets.zip?download=1",
    size=20_856_553,
    md5="a4927977e8ab5b759c7cb23190b45d8f",  # as Zenodo lists it
    sha256="1a9d962e4e29b124a6ca26ce08d086183b975aca72479a55ece9fe0979eafa7d",  # computed 2026-10-15
    record="https://zenodo.org/records/13147961",
    doi="10.5281/zenodo.13147961",
    licence="Creative Commons Attribution 4.0 International (CC BY 4.0)",
    licence_url="https://creativecommons.org/licenses/by/4.0/",
    citation=("Yi Hsiao, Haijian Zhang, Ginny Xiaohe Li, Yamei Deng, Fengchao Yu, Hossein Valipour Kahrood, "
              "Joel R. Steele, Ralf B. Schittenhelm, Alexey I. Nesvizhskii. Analysis and Visualization of "
              "Quantitative Proteomics Data Using FragPipe-Analyst. J. Proteome Res. 2024, 23(10), 4303-4315. "
              "doi:10.1021/acs.jproteome.4c00294. Data: Yi Hsiao, FragPipe-Analyst Manuscript Datasets, Zenodo "
              "(2024), doi:10.5281/zenodo.13147961, CC BY 4.0."),
    prefix="FragPipe_Analyst_Manuscript_Datasets/",
)


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    reads: str                     # what Ionomos reads it as
    table: tuple[str, str]         # (member in the zip, name in the folder)
    annotation: tuple[str, str]
    control: str
    comparison: str
    samples_from: str              # how the table's sample names are found: names | runs | tmt
    about: str                     # README: the experiment
    look: str                      # README: what the report should show


DATASETS: dict[str, Dataset] = {d.key: d for d in (
    Dataset(
        key="apms", title="AP-MS, label-free: cyclin D1 pull-down vs control (FragPipe IonQuant)",
        reads="FragPipe label-free (combined_protein.tsv, MaxLFQ intensities)",
        table=("AP-MS/combined_protein.tsv", "combined_protein.tsv"),
        annotation=("AP-MS/experiment_annotation.tsv", "experiment_annotation.tsv"),
        control="CONTROL", comparison="CCND1 vs CONTROL", samples_from="names",
        about=("Affinity purification of cyclin D1 (CCND1, the bait; 3 pull-downs) against control pull-downs\n"
               "(4), from a head and neck cancer interactome study (Swantek et al., Science 2021,\n"
               "doi:10.1126/science.abf2911), searched with FragPipe (MSFragger + IonQuant). It is\n"
               "FragPipe-Analyst's LFQ AP-MS tutorial."),
        look=("CCND1 vs CONTROL: the bait CCND1 and its known partners - the CDKs it activates (CDK4, CDK6,\n"
              "and CDK2) and the CDK inhibitors that sit in the same complexes (CDKN1A/p21, CDKN1B/p27,\n"
              "CDKN1C/p57) - are the hits, and most are never seen in the control pull-downs ('Only in one\n"
              "condition'). Most hits rest on imputed control values: the report says so, and its 'hide\n"
              "imputation-driven' box shows what stands on measured values alone."),
    ),
    Dataset(
        key="dia", title="DIA: clear cell renal cell carcinoma, tumour vs normal adjacent tissue (DIA-NN)",
        reads="DIA-NN protein matrix (diann-output.pg_matrix.tsv)",
        table=("DIA_4plex/diann-output.pg_matrix.tsv", "diann-output.pg_matrix.tsv"),
        annotation=("DIA_4plex/experiment_annotation_clean.tsv", "experiment_annotation.tsv"),
        control="NAT", comparison="Tumor vs NAT", samples_from="runs",
        about=("DIA runs of clear cell renal cell carcinoma tumours and normal adjacent tissue (NAT) from the\n"
               "CPTAC study (Clark et al., Cell 2019, doi:10.1016/j.cell.2019.10.007): 20 tumours and 12 NAT\n"
               "samples of the four TMT plexes below, searched again by the FragPipe-Analyst authors with\n"
               "FragPipe (DIA-NN). It is FragPipe-Analyst's DIA tutorial."),
        look=("Tumor vs NAT: thousands of proteins change (this is cancer against normal kidney). Known ccRCC\n"
              "markers to search for in the volcano: CA9 (carbonic anhydrase 9, a target of the hypoxia\n"
              "response that VHL loss switches on) and AHNAK2 up; NDUFV2 (respiratory chain), PIGR and\n"
              "SFRP1 down. Gene sets: hypoxia and glycolysis up, oxidative phosphorylation down (these need\n"
              "the Enrichr download)."),
    ),
    Dataset(
        key="tmt", title="TMT: the same ccRCC tumours and normal tissue, four TMT-11 plexes (TMT-Integrator)",
        reads="TMT-Integrator protein ratios to the pooled reference (ratio_protein_MD.tsv)",
        table=("TMT_4plex/ratio_protein_MD.tsv", "ratio_protein_MD.tsv"),
        annotation=("TMT_4plex/experiment_annotation_clean.tsv", "experiment_annotation.tsv"),
        control="NAT", comparison="Tumor vs NAT", samples_from="tmt",
        about=("Four TMT-11 plexes of the CPTAC ccRCC study (Clark et al., Cell 2019): tumours and normal\n"
               "adjacent tissue, each plex with a pooled reference channel, searched with FragPipe and\n"
               "summarised by TMT-Integrator as log2 ratios to the pool. It is FragPipe-Analyst's TMT\n"
               "tutorial (protein level)."),
        look=("Tumor vs NAT: the same biology as the DIA experiment, measured another way: CA9 and AHNAK2\n"
              "up, NDUFV2, PIGR and SFRP1 down. TMT ratios are compressed, so the fold changes are smaller\n"
              "than DIA's for the same proteins; the hits agree in direction."),
    ),
)}

README_TOP = """\
Ionomos example: real, published proteomics results
===================================================

Written by `ionomos example`. The tables in this folder are not Ionomos'
and not simulated: they are the example datasets of FragPipe-Analyst's own
paper and tutorials, downloaded from Zenodo.

Source:   {record}  (doi:{doi})
File:     {name}  ({mb:.1f} MB, MD5 {md5})
Licence:  {licence}
          {licence_url}
Cite:     {citation}

Changes made (as CC BY 4.0 asks us to say): the tables below were copied out
of the zip unchanged; each folder's experiment_annotation.tsv is the authors'
(the _clean one where there are two); experiment.yaml and the README.txt
files are Ionomos' own. Nothing else in the zip is used.

The experiments (one folder each, analysed separately):
{datasets}
Each folder's README.txt says what the report should show. The walk through
the reports, and what Ionomos does differently from FragPipe-Analyst:
https://github.com/nichfoster/ionomos/blob/master/docs/EXAMPLE.md

Re-run one with other settings, e.g.:
  ionomos analyze {folder} --de-type all
  ionomos analyze {folder} --imputation none --open
"""

README_ONE = """\
{title}
{rule}

{about}

Data: {table} (Ionomos reads it as: {reads}).
Source and licence: ../README.txt ({licence}, {record}).

What to look for in results/report.html:
{look}

experiment.yaml holds the design: the control ({control}), the comparison
({comparison}){design}.
"""


class ExampleError(Exception):
    """Something the user can act on, in plain words."""


# ------------------------------------------------------------------ folder --


def pick_folder(folder: str | Path | None, cwd: Path | None = None) -> Path:
    """The folder to write. None: <cwd>/ionomos_example, or the first free ionomos_example_<n>. Given: it must not
    exist yet or be empty (nothing is ever written into a folder that holds anything)."""
    if folder is None:
        base = Path(cwd or Path.cwd())
        p = base / DEFAULT_NAME
        n = 2
        while p.exists():
            p = base / f"{DEFAULT_NAME}_{n}"
            n += 1
        return p
    p = Path(folder).expanduser().resolve()
    if p.exists() and (not p.is_dir() or any(p.iterdir())):
        raise ExampleError(f"{p} already exists and is not an empty folder; the example only writes to a new folder "
                           f"(leave the folder out to get ./{DEFAULT_NAME}, or {DEFAULT_NAME}_2 if that is taken)")
    return p


def _shown(p: Path) -> str:
    return f'"{p}"' if " " in str(p) else str(p)


def _write_new(path: Path, data: bytes) -> None:
    """Create path with data; never over an existing file (FileExistsError instead)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(data)


# ---------------------------------------------------------------- download --


def offline_reason() -> str:
    return "network use is off on this computer (IONOMOS_OFFLINE is set)" if os.environ.get(OFFLINE_ENV) else ""


def by_hand(reason: str, archive: Archive | None = None) -> str:
    """What to do when the download cannot happen here: the URL, and the command that uses the file."""
    archive = archive or ARCHIVE
    return (f"{reason}. Download {archive.url} by hand ({archive.size / 1e6:.1f} MB, MD5 {archive.md5}; the "
            f"record is {archive.record}), then run: ionomos example --zip <where you saved {archive.name}> "
            f"(or put it in the folder you run `ionomos example` from).")


def digests(path: Path) -> tuple[int, str, str]:
    """(size, md5, sha256) of a file, read once."""
    md5, sha = hashlib.md5(usedforsecurity=False), hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            size += len(chunk)
            md5.update(chunk)
            sha.update(chunk)
    return size, md5.hexdigest(), sha.hexdigest()


def mismatch(path: Path, archive: Archive | None = None) -> str:
    """'' when the file is the published zip, else what differs, in words."""
    archive = archive or ARCHIVE
    size, md5, sha = digests(path)
    if size != archive.size:
        return f"it is {size:,} bytes, not {archive.size:,}"
    if md5 != archive.md5:
        return f"its MD5 is {md5}, not {archive.md5}"
    if sha != archive.sha256:
        return f"its SHA-256 is {sha}, not {archive.sha256}"
    return ""


def _net_error(exc: BaseException, url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc or url
    if isinstance(exc, urllib.error.HTTPError):
        return f"{host} answered HTTP {exc.code}"
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, TimeoutError) or "timed out" in str(reason):
        return f"{host} did not answer in time"
    if isinstance(exc, (http.client.HTTPException, ConnectionError)):  # cut off mid-way: the network dropped (D131)
        return f"the connection to {host} broke off before the end ({type(exc).__name__})"
    if not isinstance(exc, urllib.error.URLError) and getattr(exc, "filename", None):  # this computer's disk
        return f"the download from {host} could not be saved ({exc})"
    return f"could not reach {host} ({reason})"


def download(dest_dir: Path, archive: Archive | None = None, progress=None, timeout: float = TIMEOUT_S,
             time_limit: float = TIME_LIMIT_S) -> Path:
    """Fetch the zip into dest_dir/<name>: a temporary .part first, renamed only when size and checksums match.
    Raises ExampleError (in words, with what to do) and leaves nothing of its own behind when it fails."""
    archive = archive or ARCHIVE
    why = offline_reason()
    if why:
        raise ExampleError(by_hand(why, archive))
    final = dest_dir / archive.name
    if final.exists():
        raise ExampleError(f"{final} already exists; it was not touched")
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{archive.name}-", suffix=".part", dir=dest_dir)
    os.close(fd)
    part = Path(tmp)
    done = False
    try:
        started = time.monotonic()
        req = urllib.request.Request(archive.url, headers={"User-Agent": _user_agent(),
                                                           "Accept-Encoding": "identity"})
        got = 0
        try:
            with urlopen(req, timeout=timeout) as r, open(part, "wb") as out:
                while chunk := r.read(CHUNK):
                    out.write(chunk)
                    got += len(chunk)
                    if got > archive.size:
                        raise ExampleError(f"the download from {archive.url} is larger than the published "
                                           f"{archive.size:,} bytes; it was not kept. " + _later())
                    if progress is not None:
                        progress(got, archive.size)
                    if time.monotonic() - started > time_limit:
                        raise ExampleError(by_hand(f"the download took longer than {time_limit / 60:.0f} minutes",
                                                   archive))
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:  # IncompleteRead too
            raise ExampleError(by_hand(_net_error(exc, archive.url), archive)) from exc
        bad = mismatch(part, archive)
        if bad:
            raise ExampleError(f"the file downloaded from {archive.url} is not the published one ({bad}); it was "
                               f"not kept. " + _later())
        if final.exists():  # appeared meanwhile: never written over
            raise ExampleError(f"{final} appeared during the download; it was not touched")
        os.replace(part, final)
        done = True
        return final
    finally:
        if not done:
            try:
                part.unlink()  # our own temporary file, never anything else
            except OSError:
                pass


def _later() -> str:
    return f"Try again later, or fetch it by hand: {ARCHIVE.url}"


def _user_agent() -> str:
    from ionomos import __version__

    return f"Ionomos/{__version__} (ionomos example; https://github.com/nichfoster/ionomos)"


def find_local(cwd: Path | None = None, archive: Archive | None = None) -> Path | None:
    """A copy of the zip that is already here: in the current folder (downloaded by hand), or kept by an earlier
    `ionomos example` (ionomos_example*/download/). Only a copy whose size matches is offered; it is checked in
    full before use."""
    archive = archive or ARCHIVE
    base = Path(cwd or Path.cwd())
    cands = [base / archive.name]
    try:
        cands += sorted(p / "download" / archive.name for p in base.glob(f"{DEFAULT_NAME}*") if p.is_dir())
    except OSError:
        pass
    for c in cands:
        try:
            if c.is_file() and c.stat().st_size == archive.size:
                return c
        except OSError:
            continue
    return None


# ------------------------------------------------------------------- write --


def _read_annotation(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")]


def table_samples(ds: Dataset, header: list[str]) -> list[str] | None:
    """The sample names Ionomos gives the table's columns (None: the names say the conditions)."""
    from ionomos.downstream.quant import run_stem

    if ds.samples_from == "runs":  # DIA-NN: one column per raw file, named by its path
        return [run_stem(c) for c in header if c.lower().endswith((".mzml", ".raw", ".d", ".dia", ".wiff"))]
    if ds.samples_from == "tmt":  # TMT-Integrator: the samples follow ReferenceIntensity
        return header[header.index("ReferenceIntensity") + 1:] if "ReferenceIntensity" in header else []
    return None


def design(ds: Dataset, header: list[str], annotation: list[dict]) -> tuple[dict[str, str], list[str]]:
    """({sample: condition}, [samples left out]) from the authors' annotation, for the table's sample names.
    Pooled reference channels are not samples of TMT-Integrator's ratio table, so they never appear."""
    from ionomos.downstream.quant import run_stem

    samples = table_samples(ds, header)
    if samples is None:
        return {}, []
    conds: dict[str, str] = {}
    for row in annotation:
        key = run_stem(row.get("file", "")) if ds.samples_from == "runs" else row.get("sample", "")
        cond = row.get("condition", "")
        if key and cond and not cond.lower().startswith("pool"):
            conds[key] = cond
    given = {s: conds[s] for s in samples if s in conds}
    left_out = [s for s in samples if s not in conds]
    return given, left_out


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a YAML double-quoted scalar


def experiment_yaml(ds: Dataset, conds: dict[str, str], left_out: list[str]) -> str:
    lines = [f"# Written by `ionomos example` ({ds.key}). Edit freely, then: ionomos analyze <this folder>",
             "# (keys: docs/NAMING_CONVENTION.md, \"analysis:\")",
             f"notes: {_q(ds.title + '. Public data, ' + ARCHIVE.licence + ': ' + ARCHIVE.record)}",
             "analysis:",
             f"  control: {_q(ds.control)}",
             f"  comparisons: [{_q(ds.comparison)}]"]
    if left_out:
        lines += ["  # in the table but not in the authors' experiment_annotation.tsv, so FragPipe-Analyst leaves",
                  "  # them out too (QC standards, and one sample the authors removed). Delete a name to include it.",
                  f"  exclude_samples: [{', '.join(_q(s) for s in left_out)}]"]
    if conds:
        lines += ["  # each sample's condition, from the authors' experiment_annotation.tsv (the names don't say it)",
                  "  sample_conditions:"]
        lines += [f"    {_q(s)}: {_q(c)}" for s, c in conds.items()]
    return "\n".join(lines) + "\n"


def _header(data: bytes) -> list[str]:
    first = data.split(b"\n", 1)[0].rstrip(b"\r")
    return first.decode("utf-8-sig", errors="replace").split("\t")


def write_example(folder: Path, zip_path: Path, keys: list[str] | None = None,
                  archive: Archive | None = None) -> dict[str, Path]:
    """Unpack the chosen datasets of the (verified) zip into folder/<key>/ with their experiment.yaml and
    README.txt, and folder/README.txt. Every file is created new. Returns {key: experiment folder}."""
    archive = archive or ARCHIVE
    keys = list(keys or DATASETS)
    unknown = [k for k in keys if k not in DATASETS]
    if unknown:
        raise ExampleError(f"no example called {', '.join(unknown)}; there are {', '.join(DATASETS)}")
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExampleError(f"{zip_path} cannot be read as a zip ({exc})") from exc
    with zf:
        names = set(zf.namelist())
        for key in keys:
            ds = DATASETS[key]
            members = {}
            for member, _local in (ds.table, ds.annotation):
                full = archive.prefix + member
                if full not in names:
                    raise ExampleError(f"{zip_path.name} has no {full}; is it the published zip?")
                try:
                    members[member] = zf.read(full)
                except (zipfile.BadZipFile, OSError, ValueError) as exc:  # a CRC error is a damaged copy
                    raise ExampleError(f"{full} in {zip_path} is damaged ({exc})") from exc
            exp = folder / key
            table_bytes, ann_bytes = members[ds.table[0]], members[ds.annotation[0]]
            _write_new(exp / ds.table[1], table_bytes)
            _write_new(exp / ds.annotation[1], ann_bytes)
            conds, left_out = design(ds, _header(table_bytes), _read_annotation(ann_bytes))
            _write_new(exp / "experiment.yaml", experiment_yaml(ds, conds, left_out).encode("utf-8"))
            if conds:
                extra = (" and each sample's condition, from the authors'\nexperiment_annotation.tsv"
                         + (f"; {len(left_out)} sample(s) they left out are left out\nhere too "
                            f"(exclude_samples)" if left_out else ""))
            else:
                extra = "; the conditions come from the sample names"
            _write_new(exp / "README.txt", README_ONE.format(
                title=ds.title, rule="=" * len(ds.title), about=ds.about, table=ds.table[1], reads=ds.reads,
                licence=archive.licence, record=archive.record, look=ds.look, control=ds.control,
                comparison=ds.comparison, design=extra).encode("utf-8"))
            out[key] = exp
    listing = "".join(f"  {k}/  {DATASETS[k].title}\n" for k in keys)
    first = _shown(out[keys[0]])
    _write_new(folder / "README.txt", README_TOP.format(
        record=archive.record, doi=archive.doi, name=archive.name, mb=archive.size / 1e6, md5=archive.md5,
        licence=archive.licence, licence_url=archive.licence_url, citation=archive.citation, datasets=listing,
        folder=first).encode("utf-8"))
    return out


def analyze(folder: Path, extra: dict | None = None, progress=None):
    """The same analysis `ionomos analyze <folder>` runs, without a lab config. Returns downstream.Outcome."""
    from ionomos import postprocess

    return postprocess.run_for_folder(Path(folder), None, None, extra or None, progress=progress)


# --------------------------------------------------------------------- CLI --


def listing() -> str:
    a = ARCHIVE
    lines = [f"Ionomos' real-data example: {a.name} ({a.size / 1e6:.1f} MB) from {a.record}",
             f"  licence: {a.licence}", f"  cite: {a.citation}", ""]
    for d in DATASETS.values():
        lines += [f"{d.key}: {d.title}", "  " + d.about.replace("\n", "\n  "), f"  reads: {d.reads}", ""]
    lines.append("ionomos example [FOLDER] [--dataset apms|dia|tmt] [--open]   (docs/EXAMPLE.md)")
    return "\n".join(lines)


def add_parser(sub):
    p = sub.add_parser("example", help="download a real, published experiment (20.9 MB, CC BY 4.0) and analyse it: "
                                       "label-free, DIA and TMT",
                       description="Download FragPipe-Analyst's published example datasets from Zenodo, check them, "
                                   "write them as Ionomos experiment folders and analyse each. Nothing existing is "
                                   "touched. docs/EXAMPLE.md walks through the reports.")
    p.add_argument("folder", nargs="?", help=f"a new or empty folder (default: ./{DEFAULT_NAME}, or "
                                             f"{DEFAULT_NAME}_2, ... if taken)")
    p.add_argument("--dataset", action="append", choices=list(DATASETS), metavar="NAME",
                   help=f"only this experiment: {', '.join(DATASETS)} (repeatable; default: all)")
    p.add_argument("--zip", metavar="PATH", help=f"use this copy of {ARCHIVE.name} (downloaded by hand) instead of "
                                                 "downloading; it is checked, not changed")
    p.add_argument("--list", action="store_true", help="say what the data are and where they come from; download "
                                                       "nothing")
    p.add_argument("--no-enrichment", action="store_true", help="skip gene sets (no Enrichr download)")
    p.add_argument("--quiet", action="store_true", help="no progress lines")
    p.add_argument("--open", action="store_true", help="open the reports when done")
    p.set_defaults(fn=run_cli)
    return p


def _progress_printer(stream=None):
    stream = stream or sys.stdout
    tty = getattr(stream, "isatty", lambda: False)()
    last = [-1]

    def show(done: int, total: int) -> None:
        pct = int(100 * done / total) if total else 0
        step = pct if tty else pct // 25 * 25
        if step == last[0]:
            return
        last[0] = step
        line = f"  … downloading: {done / 1e6:.1f} of {total / 1e6:.1f} MB ({pct}%)"
        if tty:
            stream.write("\r" + line + ("\n" if done >= total else ""))
        else:
            stream.write(line + "\n")
        stream.flush()

    return show


def _source(args, folder: Path) -> Path:
    """The verified zip to unpack: --zip, a copy already here, or a fresh download into folder/download/."""
    if args.zip:
        given = Path(args.zip).expanduser()
        if not given.is_file():
            raise ExampleError(f"{given} is not a file")
        bad = mismatch(given)
        if bad:
            raise ExampleError(f"{given} is not the published {ARCHIVE.name} ({bad}); it was not changed. "
                               f"Download it again from {ARCHIVE.url}")
        print(f"using {given} (checked: size, MD5 and SHA-256 match)")
        return given
    local = find_local()
    if local is not None and not mismatch(local):
        print(f"using the copy already here: {local} (checked: size, MD5 and SHA-256 match)")
        return local
    print(f"downloading {ARCHIVE.name} ({ARCHIVE.size / 1e6:.1f} MB) from {ARCHIVE.record}")
    return download(folder / "download", progress=None if args.quiet else _progress_printer())


def _drop_if_empty(folder: Path, existed: bool) -> None:
    """After a failed start: the download/ folder this run made, and the example folder when this run made it, go
    again if (and only if) they are empty, so the next run can use the same name. os.rmdir refuses a folder that
    holds anything; a folder the user gave that existed before is left as it was."""
    for d in (folder / "download",) + (() if existed else (folder,)):
        try:
            os.rmdir(d)
        except OSError:
            pass


def run_cli(args) -> int:
    from ionomos.cli import _open_report, _print_outcome

    if args.list:
        print(listing())
        return 0
    keys = list(dict.fromkeys(args.dataset or DATASETS))
    try:
        folder = pick_folder(args.folder)
    except ExampleError as exc:
        print(exc, file=sys.stderr)
        return 2
    existed = folder.exists()
    started = time.monotonic()
    try:
        src = _source(args, folder)
        exps = write_example(folder, src, keys)
    except ExampleError as exc:
        _drop_if_empty(folder, existed)
        print(exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        _drop_if_empty(folder, existed)
        print("stopped; nothing half-downloaded was kept", file=sys.stderr)
        return 130
    except OSError as exc:
        _drop_if_empty(folder, existed)
        print(f"cannot write the example into {folder}: {exc}", file=sys.stderr)
        return 2
    print(f"example data: {folder}  (source and licence: {folder / 'README.txt'})")
    extra = {"enrichment": False} if args.no_enrichment else None
    reports, failed = [], []
    for key, exp in exps.items():
        ds = DATASETS[key]
        print(f"\n{key}: {ds.title}")
        out = analyze(exp, extra, progress=(lambda m: print(f"  … {m}", flush=True)) if not args.quiet else None)
        _print_outcome(out)
        if out.report:
            print(f"report: {out.report}")
            print(f"what to look for: {exp / 'README.txt'}")
            reports.append(out.report)
        else:
            failed.append(key)
    print(f"\ndone in {time.monotonic() - started:.0f} s: {len(reports)} report(s). The walk through them: "
          "docs/EXAMPLE.md (https://github.com/nichfoster/ionomos/blob/master/docs/EXAMPLE.md)")
    if args.open:
        for r in reports:
            _open_report(r)
    elif reports:
        print("open them in a browser, or run again with --open")
    return 1 if failed else 0
