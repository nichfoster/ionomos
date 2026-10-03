"""
results/sdrf.tsv: the experiment's sample metadata in SDRF-Proteomics (PSI, spec v1.1.0, template ms-proteomics).

    sd, reason = build(method, dest, workdir, record, m, processed, settings, version)
    write(results / "sdrf.tsv", sd)

One row per raw data file, and per label within it: TMT gives a row per channel, isoDTB a light and a heavy
row (the spec's SILAC pattern: rows share the assay name, comment[label] tells them apart). Columns in the
spec's order: source name, characteristics[...], assay name, technology type, comment[...], factor value.

Where each value comes from:
    files, samples, replicates   ionomos.json plan.manifest (file, experiment, bioreplicate; experiment.yaml
                                 corrections already applied), fractions from the file names (naming.py);
                                 without a manifest, the run columns of the quant table or the raws in the folder
    TMT channels                 annotation.txt next to the raws (what FragPipe used), else experiment.yaml
                                 tmt:, else sample names ending in the channel (DMSO_1_126)
    condition                    the analysis' own: sample_conditions applied; left-out samples keep a row
    organism                     analysis.sdrf.organism, else the FASTA's UniProt OS= (one species >= 90 %)
    cleavage agent, mods         analysis.sdrf.cleavage_agent, else FragPipe's workflow (MSFragger enzyme and
                                 the enabled fixed / variable modifications)
    instrument, organism part,   analysis.sdrf.* only (config.yaml lab-wide, experiment.yaml per experiment)
    cell type, disease
Anything not known is the spec's reserved word "not available"; the columns the validator needs filled
before a repository accepts the file are returned in Sdrf.fill_in. A table analysed without FragPipe
(`ionomos analyze <table>`, D33) has no raw files to describe, so it gets no SDRF.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from ionomos.downstream.quant import RAW_EXTS, run_stem
from ionomos.downstream.tables import write_tsv

SPEC_VERSION = "v1.1.0"
TEMPLATE = "ms-proteomics v1.1.0"
NA = "not available"
RESERVED = ("not available", "not applicable", "anonymized", "pooled")
TECHNOLOGY = "proteomic profiling by mass spectrometry"
DDA = "NT=Data-dependent acquisition;AC=PRIDE:0000627"
DIA = "NT=Data-independent acquisition;AC=PRIDE:0000450"
LABEL_FREE = "label free sample"
# isoDTB tags are chemical, cysteine-directed, isotope-coded affinity tags; PRIDE has no isoDTB label term, and
# ICAT is the closest (SILAC would claim metabolic labelling). The isoDTB masses go in the modification columns.
ISODTB_LABELS = (("light", "ICAT light"), ("heavy", "ICAT heavy"))

# analysis.sdrf setting -> the column it fills
SETTINGS = {
    "organism": "characteristics[organism]",
    "organism_part": "characteristics[organism part]",
    "cell_type": "characteristics[cell type]",
    "disease": "characteristics[disease]",
    "instrument": "comment[instrument]",
    "cleavage_agent": "comment[cleavage agent details]",
}
# "not available" in these makes the official validator (sdrf-pipelines) refuse the file
MUST_FILL = ("characteristics[organism]", "comment[instrument]", "comment[cleavage agent details]",
             "comment[label]", "comment[data file]")

# MSFragger enzyme names -> PSI-MS cleavage agent. stricttrypsin (also cuts before P) is still trypsin at the bench.
ENZYMES = {
    "trypsin": "NT=Trypsin;AC=MS:1001251", "stricttrypsin": "NT=Trypsin;AC=MS:1001251",
    "trypsin/p": "NT=Trypsin/P;AC=MS:1001313", "lysc": "NT=Lys-C;AC=MS:1001309",
    "lys-c": "NT=Lys-C;AC=MS:1001309", "lysc-p": "NT=Lys-C/P;AC=MS:1001310", "lys-c/p": "NT=Lys-C/P;AC=MS:1001310",
    "lysn": "NT=Lys-N;AC=MS:1003093", "lys-n": "NT=Lys-N;AC=MS:1003093",
    "chymotrypsin": "NT=Chymotrypsin;AC=MS:1001306", "gluc": "NT=glutamyl endopeptidase;AC=MS:1001917",
    "gluc_bicarb": "NT=glutamyl endopeptidase;AC=MS:1001917", "glu-c": "NT=glutamyl endopeptidase;AC=MS:1001917",
    "aspn": "NT=Asp-N;AC=MS:1001304", "asp-n": "NT=Asp-N;AC=MS:1001304", "argc": "NT=Arg-C;AC=MS:1001303",
    "arg-c": "NT=Arg-C;AC=MS:1001303", "cnbr": "NT=CNBr;AC=MS:1001307",
    "nonspecific": "NT=unspecific cleavage;AC=MS:1001956", "unspecific cleavage": "NT=unspecific cleavage;AC=MS:1001956",
}

# monoisotopic mass shift -> (Unimod name, accession); matched to 0.001 Da (TMTpro and iTRAQ8plex are 0.0018 apart)
UNIMOD = [
    (57.021464, "Carbamidomethyl", "UNIMOD:4"), (15.994915, "Oxidation", "UNIMOD:35"),
    (42.010565, "Acetyl", "UNIMOD:1"), (79.966331, "Phospho", "UNIMOD:21"),
    (229.162932, "TMT6plex", "UNIMOD:737"), (304.207146, "TMTpro", "UNIMOD:2016"),
    (144.102063, "iTRAQ4plex", "UNIMOD:214"), (304.205360, "iTRAQ8plex", "UNIMOD:730"),
    (114.042927, "GG", "UNIMOD:121"), (0.984016, "Deamidated", "UNIMOD:7"),
    (-18.010565, "Glu->pyro-Glu", "UNIMOD:27"), (28.031300, "Dimethyl", "UNIMOD:36"),
    (14.015650, "Methyl", "UNIMOD:34"), (43.005814, "Carbamyl", "UNIMOD:5"),
    (8.014199, "Label:13C(6)15N(2)", "UNIMOD:259"), (10.008269, "Label:13C(6)15N(4)", "UNIMOD:267"),
    (4.025107, "Label:2H(4)", "UNIMOD:481"), (6.020129, "Label:13C(6)", "UNIMOD:188"),
]
# -17.0265 is two different Unimod entries depending on the residue
_MINUS_NH3 = {"Q": ("Gln->pyro-Glu", "UNIMOD:28")}
_MINUS_NH3_OTHER = ("Ammonia-loss", "UNIMOD:385")
_POSITION = {"": "Anywhere", "n": "Any N-term", "c": "Any C-term", "[": "Protein N-term", "]": "Protein C-term"}
_FIX_TERM = {"n-term peptide": "Any N-term", "c-term peptide": "Any C-term", "n-term protein": "Protein N-term",
             "c-term protein": "Protein C-term"}
_CHANNEL = re.compile(r"^\d{3}(?:[NC]D?|D)?$")
_POOL = re.compile(r"(?i)(?:^|[_\-\s.])(?:pool(?:ed)?|bridge|ref(?:erence)?)(?:$|[_\-\s.\d])")
_UNUSED = {"", "na", "n/a", "empty", "none", "blank", "-"}


@dataclass
class Run:
    file: str  # data file name (no folders)
    stem: str
    experiment: str
    rep: int
    fraction: int = 1
    data_type: str = ""


@dataclass
class Row:
    source: str
    rep: int | str  # biological replicate, or "pooled"
    assay: str
    label: str
    data_file: str
    condition: str
    fraction: int = 1
    tech: int = 1
    group: str = ""  # isoDTB: light / heavy (replicates are numbered per condition and label)
    experiment: str = ""
    data_type: str = ""


@dataclass
class Sdrf:
    header: list[str]
    rows: list[list[str]]
    fill_in: list[str] = field(default_factory=list)  # columns still "not available" that a repository needs
    notes: list[str] = field(default_factory=list)
    derived: dict = field(default_factory=dict)  # value -> where it came from, for analysis.json

    @property
    def data_files(self) -> int:
        j = self.header.index("comment[data file]")
        return len({r[j] for r in self.rows if r[j] != NA})


# ------------------------------------------------------------------ values --


def clean(v) -> str:
    """One cell: tabs / newlines / runs of spaces -> one space, reserved words lower-case, empty -> not available."""
    s = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
    if not s:
        return NA
    return s.lower() if s.lower() in RESERVED else s


def _basename(path: str) -> str:
    return PureWindowsPath(path).name if ("\\" in path or ":" in path[:3]) else Path(path).name


def _props(text: str) -> dict[str, str]:
    """A FragPipe .workflow (Java properties) as a dict, with its \\: and \\\\ escapes undone."""
    out = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("#", "!")):
            continue
        m = re.match(r"([^=:\s]+)\s*[=:]\s*(.*)$", ln)
        if m:
            out[m.group(1)] = m.group(2).replace("\\:", ":").replace("\\\\", "\\")
    return out


def workflow(record: dict | None, workdir: Path) -> tuple[dict[str, str], str]:
    """The workflow FragPipe ran: the copy it leaves in its output folder, else the one Ionomos gave it."""
    run = (record or {}).get("run") or {}
    for p in (Path(workdir) / "fragpipe.workflow", Path(run["workflow"]) if run.get("workflow") else None):
        if p is not None and p.is_file():
            try:
                return _props(p.read_text(encoding="utf-8", errors="replace")), p.name
            except OSError:
                continue
    return {}, ""


def enzyme(props: dict[str, str]) -> tuple[str | None, list[str]]:
    notes = []
    name = (props.get("msfragger.search_enzyme_name_1") or props.get("msfragger.search_enzyme_name") or "").strip()
    if not name or name == "null":
        return None, notes
    second = (props.get("msfragger.search_enzyme_name_2") or "").strip()
    if second and second != "null":
        notes.append(f"the workflow uses two enzymes ({name}, {second}); the SDRF names the first")
    value = ENZYMES.get(name.lower())
    if value is None:
        notes.append(f"enzyme {name!r} from the workflow has no PSI-MS term here; fill in cleavage agent details")
    return value, notes


def _unimod(mass: float, residue: str) -> tuple[str, str] | None:
    if abs(mass + 17.026549) < 0.001:
        return _MINUS_NH3.get(residue, _MINUS_NH3_OTHER)
    return next(((n, a) for m, n, a in UNIMOD if abs(m - mass) < 0.001), None)


def _mass(x: float) -> str:
    return f"{x:.6f}".rstrip("0").rstrip(".")


def _mod(name: str | None, acc: str | None, mass: float, mt: str, pp: str, ta: str) -> str:
    parts = [f"NT={name}" if name else f"NT=mass shift {_mass(mass)}"]
    if acc:
        parts.append(f"AC={acc}")
    if ta:
        parts.append(f"TA={ta}")
    parts += [f"MT={mt}", f"PP={pp}"]
    if not acc:
        parts.append(f"MM={_mass(mass)}")
    return ";".join(parts)


def modifications(props: dict[str, str], tag: str = "") -> tuple[list[str], list[str]]:
    """Enabled, non-zero fixed and variable modifications from MSFragger's tables in the workflow, one SDRF value
    each (the column repeats, as the spec says). Masses without a Unimod entry keep their mass (MM=); the
    light / heavy label masses IonQuant quantifies are named after them ("isoDTB light" with tag isoDTB)."""
    mods, notes, unknown = [], [], []
    labels = {}
    for key, w in (("ionquant.light", "light"), ("ionquant.heavy", "heavy")):
        for m in re.finditer(r"([A-Za-z])(-?\d+(?:\.\d+)?)", props.get(key) or ""):
            if float(m.group(2)):
                labels[(m.group(1).upper(), round(float(m.group(2)), 3))] = f"{tag} {w}" if tag else f"{w} label"

    def entries(key):
        for part in (props.get(key) or "").split(";"):
            bits = [b.strip() for b in part.split(",")]
            if len(bits) < 3 or bits[2].lower() != "true":
                continue
            try:
                mass = float(bits[0])
            except ValueError:
                continue
            if mass:
                yield mass, bits[1]

    for mass, site in entries("msfragger.table.fix-mods"):
        low = site.lower()
        if low in _FIX_TERM:
            hit = _unimod(mass, "")
            mods.append(_mod(*(hit or (None, None)), mass, "fixed", _FIX_TERM[low], ""))
        else:
            res = site.strip()[:1].upper()
            if not res.isalpha():
                continue
            hit = _unimod(mass, res)
            mods.append(_mod(*(hit or (None, None)), mass, "fixed", "Anywhere", res))
        if hit is None:
            unknown.append(f"{_mass(mass)} on {site.strip()}")
    for mass, sites in entries("msfragger.table.var-mods"):
        groups: dict[str, list[str]] = defaultdict(list)
        for m in re.finditer(r"([nc\[\]]?)([A-Z^*])", sites):
            groups[m.group(1)].append(m.group(2))
        for pos, residues in groups.items():
            real = [r for r in residues if r.isalpha()]
            by_name: dict[tuple, list[str]] = defaultdict(list)
            for r in real or [""]:
                hit = _unimod(mass, r) or ((labels.get((r, round(mass, 3))), None)
                                           if labels.get((r, round(mass, 3))) else None)
                by_name[hit or (None, None)].append(r)
            for (name, acc), rs in by_name.items():
                mods.append(_mod(name, acc, mass, "variable", _POSITION[pos], ",".join(x for x in rs if x)))
                if name is None:
                    unknown.append(f"{_mass(mass)} on {''.join(rs) or sites}")
    if unknown:
        notes.append("modifications without a Unimod term, kept by mass (MM=): " + ", ".join(unknown))
    return list(dict.fromkeys(mods)), notes


_FASTA_CACHE: dict[tuple[str, float, int], tuple[str | None, str]] = {}


def fasta_organism(path: str | Path | None, decoy: str = "rev_") -> tuple[str | None, str]:
    """(organism, note) from UniProt OS= headers of the target entries; one species must hold >= 90 %."""
    if not path:
        return None, ""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None, ""
    key = (str(p), st.st_mtime, st.st_size)
    if key in _FASTA_CACHE:
        return _FASTA_CACHE[key]
    counts: Counter = Counter()
    rx = re.compile(rb" OS=(.+?)(?: [A-Z]{2}=|$)")
    try:
        with open(p, "rb") as fh:
            for line in fh:
                if not line.startswith(b">") or line.startswith(b">" + decoy.encode()) or \
                        line[1:].lower().startswith((b"contam_", b"cont_")):
                    continue
                m = rx.search(line.rstrip())
                counts[m.group(1).decode("utf-8", "replace").strip() if m else ""] += 1
    except OSError:
        return None, ""
    named = [(k, n) for k, n in counts.most_common() if k]
    total = sum(counts.values())
    if not named or not total:
        out = (None, "")
    elif named[0][1] / total >= 0.9:
        out = (named[0][0].lower(), f"organism from {p.name} (OS= of {named[0][1]} of {total} entries)")
    else:
        out = (None, f"{p.name} holds several species ({', '.join(k for k, _ in named[:3])}); set analysis.sdrf.organism")
    _FASTA_CACHE[key] = out
    return out


# ------------------------------------------------------------------ inputs --


def _fraction(file: str, method: str) -> int:
    from ionomos.naming import NamingError, parse_raw_name

    try:
        f = parse_raw_name(Path(file).stem + ".raw", method).fraction if method in ("isoDTB", "TMT", "DIA") else None
    except NamingError:
        f = None
    return f or 1


def runs_from_record(record: dict | None, method: str) -> list[Run]:
    out = []
    for line in ((record or {}).get("plan") or {}).get("manifest") or []:
        try:
            name = _basename(str(line["file"]))
            out.append(Run(name, run_stem(name), str(line["experiment"]), int(line["bioreplicate"]),
                           _fraction(name, method), str(line.get("data_type") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def runs_from_folder(dest: Path, method: str) -> list[Run]:
    """Raw files next to the results (the folder, or its raw/ subfolder), named as intake would read them."""
    from ionomos.naming import NamingError, parse_raw_name

    out = []
    for d in (dest, dest / "raw"):
        try:
            items = sorted(d.iterdir())
        except OSError:
            continue
        for p in items:
            if p.suffix.lower() not in RAW_EXTS or p.name.startswith("."):
                continue
            try:
                r = parse_raw_name(p.stem + ".raw", method) if method in ("isoDTB", "TMT", "DIA") else None
            except NamingError:
                r = None
            out.append(Run(p.name, p.stem, r.sample if r else p.stem, r.rep if r else 1,
                           (r.fraction or 1) if r else 1))
    return out


def _read_annotation(path: Path) -> dict[str, str]:
    out = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for ln in text.splitlines():
        bits = ln.split(None, 1)
        if len(bits) == 2 and _CHANNEL.match(bits[0].strip()):
            out[bits[0].strip()] = bits[1].strip()
    return out


def tmt_channels(record: dict | None, dest: Path, m, experiments: list[str]) -> tuple[dict, str]:
    """{plex (None = every plex): {channel: sample}} and where it came from."""
    plan = (record or {}).get("plan") or {}
    raw_dir = dest / plan["raw_dir"] if plan.get("raw_dir") else dest
    found: dict = {}
    # a drop laid out one folder per plex (D69): each plex's annotation.txt is in the folder of its files
    folders: dict[str, set[str]] = {}
    for line in plan.get("manifest") or []:
        exp, parent = str(line.get("experiment")), str(Path(str(line.get("file", ""))).parent)
        for key in dict.fromkeys((exp, re.sub(r"[^A-Za-z0-9_]", "_", exp))):  # also as FragPipe writes it
            folders.setdefault(key, set()).add(parent)
    for exp in experiments:
        own = folders.get(exp) or set()
        if len(own) == 1 and next(iter(own)) not in ("", ".", str(plan.get("raw_dir") or "")):
            ch = _read_annotation(dest / next(iter(own)) / "annotation.txt")
            if ch:
                found[exp] = ch
    for d in dict.fromkeys((raw_dir, dest)):
        for exp in experiments:
            ch = _read_annotation(d / f"{exp}_annotation.txt")
            if ch:
                found.setdefault(exp, ch)
        if None not in found:
            ch = _read_annotation(d / "annotation.txt")
            if ch:
                found[None] = ch
    if found:
        return found, "annotation.txt"
    tmt = (plan.get("overrides") or {}).get("tmt") or {}
    if isinstance(tmt, dict) and (tmt.get("plexes") or tmt.get("channels")):
        if tmt.get("plexes"):
            maps = {str(k): {str(c): str(s) for c, s in ((v or {}).get("channels") or {}).items()}
                    for k, v in tmt["plexes"].items()}
        else:
            maps = {None: {str(c): str(s) for c, s in tmt["channels"].items()}}
        return {k: v for k, v in maps.items() if v}, "experiment.yaml tmt:"
    plex, chan = ((m.meta.get("plex") or {}), (m.meta.get("channel") or {})) if m is not None else ({}, {})
    if chan and len(set(plex.values())) > 1:  # several plexes the engine named (MSstatsTMT, MaxQuant, PD; plex.py)
        maps: dict = {}
        for s, ch in chan.items():
            maps.setdefault(plex.get(s), {})[ch] = s
        return maps, "the quant table's plexes"
    if m is not None and len(set(experiments)) <= 1:
        from ionomos.downstream import tmt as tmt_mod

        rows = tmt_mod.annotation_rows(list(m.samples))
        if rows:
            return {None: {r["channel"]: r["sample"] for r in rows}}, "sample names"
        ends = {s: re.search(r"(\d{3}(?:[NC]D?|D)?)$", s) for s in m.samples}
        if ends and all(ends.values()):
            return {None: {v.group(1): s for s, v in ends.items()}}, "sample names"
    return {}, ""


# ------------------------------------------------------------------- build --


def build(method: str | None, dest: Path, workdir: Path, record: dict | None, m, processed, settings,
          version: str = "") -> tuple[Sdrf | None, str]:
    """The SDRF for one analysed experiment, or (None, why not)."""
    exp_type = (m.exp if m is not None and m.exp else method) or ""
    if method == "table" or (m is not None and m.meta.get("table")):
        return None, ("a table analysed on its own (no FragPipe run) names no raw files, and linking samples to "
                      "raw files is what an SDRF is for")
    dest, workdir = Path(dest), Path(workdir)
    notes: list[str] = []
    naming_method = exp_type if exp_type in ("isoDTB", "TMT", "DIA") else (method or "")
    runs = runs_from_record(record, naming_method) or runs_from_folder(dest, naming_method)
    if not runs and m is None:
        return None, "no raw files and no samples to describe"
    pm = processed.m if processed is not None else None
    renamed = dict(getattr(settings, "sample_conditions", {}) or {})
    excluded = [s for s in (getattr(settings, "exclude_samples", []) or []) if m is None or s in m.condition]

    def cond_of(sample: str, fallback: str) -> str:
        if pm is not None and sample in pm.condition:
            return pm.condition[sample]
        if sample in renamed:
            return renamed[sample]
        if m is not None and sample in m.condition:
            return m.condition[sample]
        return fallback

    if exp_type == "TMT":
        rows = _tmt_rows(runs, m, cond_of, record, dest, notes)
    elif exp_type == "isoDTB":
        rows = _isodtb_rows(runs, m, cond_of)
    else:
        rows = _label_free_rows(runs, m, cond_of)
    if not rows:
        return None, "no samples to describe"
    _number(rows)

    meta = dict(getattr(settings, "sdrf", {}) or {})
    derived: dict = {}
    props, wf_name = workflow(record, workdir)
    organism = meta.get("organism")
    if organism:
        derived["organism"] = "analysis.sdrf"
    else:
        note = ""
        for db in dict.fromkeys(x for x in (props.get("database.db-path"),
                                            ((record or {}).get("run") or {}).get("fasta")) if x):
            organism, why = fasta_organism(db, props.get("database.decoy-tag") or "rev_")
            note = note or why
            if organism:
                derived["organism"] = "FASTA"
                break
        if note:
            notes.append(note)
    cleavage = meta.get("cleavage_agent")
    if cleavage:
        cleavage = ENZYMES.get(cleavage.strip().lower(), cleavage)
        derived["cleavage_agent"] = "analysis.sdrf"
    elif props:
        cleavage, enotes = enzyme(props)
        notes += enotes
        if cleavage:
            derived["cleavage_agent"] = wf_name
    mods, mnotes = modifications(props, "isoDTB" if exp_type == "isoDTB" else "") if props else ([], [])
    notes += mnotes
    if mods:
        derived["modifications"] = wf_name
    for k in ("instrument", "organism_part", "cell_type", "disease"):
        if meta.get(k):
            derived[k] = "analysis.sdrf"

    header = ["source name", "characteristics[organism]", "characteristics[organism part]",
              "characteristics[cell type]", "characteristics[disease]", "characteristics[biological replicate]",
              "assay name", "technology type", "comment[proteomics data acquisition method]", "comment[label]",
              "comment[instrument]", "comment[cleavage agent details]",
              *(["comment[modification parameters]"] * len(mods)),
              "comment[fraction identifier]", "comment[technical replicate]", "comment[data file]",
              "comment[sdrf version]", "comment[sdrf template]", "comment[sdrf annotation tool]",
              "factor value[condition]"]
    tool = f"Ionomos v{version}" if version else "Ionomos"
    out_rows, seen = [], set()
    for r in rows:
        dia = (r.data_type or ("DIA" if exp_type == "DIA" else "DDA")).upper() == "DIA"
        cells = [r.source, organism, meta.get("organism_part"), meta.get("cell_type"), meta.get("disease"),
                 r.rep, r.assay, TECHNOLOGY, DIA if dia else DDA, r.label, meta.get("instrument"), cleavage,
                 *mods, r.fraction, r.tech, r.data_file, SPEC_VERSION, TEMPLATE, tool, r.condition]
        cells = [clean(c) for c in cells]
        key = (cells[0].lower(), cells[6].lower(), cells[9].lower())
        if key in seen:  # the spec: source name + assay name + label must be unique
            notes.append(f"duplicate row for {cells[0]} in {cells[6]} ({cells[9]}) left out")
            continue
        seen.add(key)
        out_rows.append(cells)
    fill = [h for h in MUST_FILL if any(row[header.index(h)] == NA for row in out_rows)]
    if excluded:
        notes.append("samples left out of the analysis are still listed (their raw files belong to the "
                     "experiment): " + ", ".join(excluded))
    return Sdrf(header, out_rows, fill, notes, derived), ""


def _label_free_rows(runs: list[Run], m, cond_of) -> list[Row]:
    """DIA / DDA label-free: one row per raw file; a file's sample is the one the quant table matched it to."""
    rows: list[Row] = []
    sample_of: dict[str, str] = {}
    if m is not None:
        for s, stem in (m.meta.get("manifest_run") or {}).items():
            sample_of.setdefault(stem, s)
    linked = set()
    for r in runs:
        source = f"{r.experiment}_{r.rep}"
        sample = sample_of.get(r.stem) or source
        linked.add(sample)
        rows.append(Row(source, r.rep, r.stem, LABEL_FREE, r.file, cond_of(sample, r.experiment), r.fraction,
                        experiment=r.experiment, data_type=r.data_type))
    for s in (m.samples if m is not None else []):
        if s in linked:
            continue
        col = (m.columns or {}).get(s, "")
        name = _basename(col) if col and run_stem(col) != _basename(col) else ""
        rows.append(Row(re.sub(r"\.\d+$", "", s), m.replicate.get(s) or 1, run_stem(col) if name else s, LABEL_FREE,
                        name or NA, cond_of(s, m.condition[s]), experiment=m.condition[s]))
    return rows


def _isodtb_rows(runs: list[Run], m, cond_of) -> list[Row]:
    """isoDTB: every raw file holds a light- and a heavy-tagged sample (two rows, one assay)."""
    rows: list[Row] = []
    linked = set()
    for r in runs:
        s = f"{r.experiment}_{r.rep}"
        linked.add(s)
        for word, label in ISODTB_LABELS:
            rows.append(Row(f"{s}_{word}", r.rep, r.stem, label, r.file, cond_of(s, r.experiment), r.fraction,
                            group=word, experiment=r.experiment, data_type=r.data_type))
    for s in (m.samples if m is not None else []):
        if s not in linked:
            for word, label in ISODTB_LABELS:
                rows.append(Row(f"{s}_{word}", m.replicate.get(s) or 1, s, label, NA, cond_of(s, m.condition[s]),
                                group=word))
    return rows


def _tmt_rows(runs: list[Run], m, cond_of, record, dest: Path, notes: list[str]) -> list[Row]:
    """TMT: every raw file of a plex holds every channel of that plex (a row per file and channel)."""
    experiments = list(dict.fromkeys(r.experiment for r in runs))
    maps, source = tmt_channels(record, dest, m, experiments)
    rows: list[Row] = []
    in_matrix = set(m.samples) if m is not None else set()
    counts: dict[str, int] = defaultdict(int)
    listed = set()
    if not maps:
        notes.append("TMT channels unknown (no annotation.txt, no experiment.yaml tmt: map, and the sample names "
                     "don't end in a channel); fill in comment[label]")
        for s in (m.samples if m is not None else []):
            for r in runs or [None]:
                rows.append(Row(s, m.replicate.get(s) or 1, r.stem if r else s, NA, r.file if r else NA,
                                cond_of(s, m.condition[s]), r.fraction if r else 1, experiment=r.experiment if r else ""))
        return rows
    for plex, chmap in maps.items():
        files = [r for r in runs if plex is None or r.experiment == plex] or [None]
        channels = []
        for ch, sample in chmap.items():
            if sample.strip().lower() in _UNUSED:
                continue
            listed.add(sample)
            if sample not in in_matrix and _POOL.search(sample):
                rep, cond = "pooled", "pooled"
            else:
                cond = cond_of(sample, sample.split("_", 1)[0])
                counts[cond] += 1
                rep = (m.replicate.get(sample) if m is not None else None) or counts[cond]
            channels.append((ch, sample, rep, cond))
        for r in files:
            for ch, sample, rep, cond in channels:
                rows.append(Row(sample, rep, r.stem if r else f"{plex or 'plex'}", f"TMT{ch}", r.file if r else NA,
                                cond, r.fraction if r else 1, experiment=(r.experiment if r else plex or ""),
                                data_type=r.data_type if r else ""))
    missing = [s for s in (m.samples if m is not None else []) if s not in listed]
    if missing:
        notes.append(f"TMT samples not in the channel map ({source}), so not in the SDRF: " + ", ".join(missing))
    return rows


def _number(rows: list[Row]) -> None:
    """Biological replicates restart per condition (and label, for isoDTB) and never repeat within it — a sample
    moved to another condition gets the next free number; technical replicates count repeat injections."""
    used: dict[tuple, dict[int, str]] = defaultdict(dict)
    given: dict[str, int] = {}
    moved = []
    for r in rows:  # first every sample keeps its own number where it's free ...
        if r.rep == "pooled" or r.source in given:
            continue
        taken = used[(r.condition.lower(), r.group)]
        want = int(r.rep) if str(r.rep).isdigit() and int(r.rep) > 0 else 1
        if want in taken:
            moved.append(r)
            continue
        taken[want] = r.source
        given[r.source] = want
    for r in moved:  # ... then the ones that collide get the next free one
        if r.source not in given:
            taken = used[(r.condition.lower(), r.group)]
            given[r.source] = max(taken) + 1
            taken[given[r.source]] = r.source
    for r in rows:
        if r.rep != "pooled":
            r.rep = given[r.source]
    tech: Counter = Counter()
    for r in rows:
        if r.data_file == NA:
            continue
        k = (r.source, r.label, r.fraction, r.experiment)
        tech[k] += 1
        r.tech = tech[k]


def write(path: Path, sd: Sdrf) -> Path:
    return write_tsv(path, sd.header, sd.rows)


def summary(sd: Sdrf | None, reason: str, rel: str) -> dict:
    """The analysis.json entry."""
    if sd is None:
        return {"file": None, "reason": reason}
    return {"file": rel, "spec": SPEC_VERSION, "template": TEMPLATE, "rows": len(sd.rows),
            "data_files": sd.data_files, "complete": not sd.fill_in, "fill_in": sd.fill_in,
            "derived": sd.derived, "notes": sd.notes}
