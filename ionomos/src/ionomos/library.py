"""
The workflow and FASTA library (D89, docs/LIBRARY.md): find every FragPipe `.workflow` and FASTA on this PC, say
in plain words what each one is, flag what is wrong with it, rank the candidates for each method and copy the chosen
ones into the pinned folders. No Tk; nothing here changes config.yaml (the caller does, through configio).

    info = describe_workflow(path)                  # -> WorkflowInfo: kind, settings, summary, flags
    info = describe_fasta(path)                     # -> FastaInfo: entries, decoys, species, summary, flags
    roots = default_roots(cfg)                      # -> [Path]: pinned folders, FragPipe installs, users_root, ...
    cat = scan(roots, cfg=cfg, progress=print, stop=event, time_limit_s=120)   # -> Catalog (read-only walk)
    save_catalog(cfg, cat); cat = load_catalog(cfg) # <log_dir>/library.json (a missing / broken file: empty)
    recommend(cat, "TMT", installed_version="24.0", pinned=info)   # -> [Candidate], best first, reasons in words
    recommend_fastas(cat, taxon="9606")             # -> [Candidate] for FASTAs
    auto_assign(cfg_dict, cat, "24.0")              # -> [Proposal] for methods whose files are missing or broken
    apply_proposals(cfg, cfg_dict, proposals, cat)  # adopts the files and edits cfg_dict; the caller writes it
    adopt(info, "TMT", cfg)                         # copy a workflow (and its FASTA) into the pinned folders
    adopt_fasta(info, cfg)                          # copy a FASTA into fasta_dir under a name without spaces
    update_check(cat, cfg, "24.0")                  # -> [Flag]: newer stock workflow, old or superseded FASTA
    settings_from(raw)                              # the config.yaml `library:` block, checked
    add_parser(sub), run_cli(args)                  # `ionomos library scan | list | show | recommend | adopt | check`

Rules: a scan only reads, is bounded in depth and time, can be stopped, and says how far it got. A FASTA is read as
bytes, once per (path, mtime, size). Adopting copies and never moves; a pinned file it replaces is kept as
`<name>.bak-<time>` (fragpipe.import_workflow), and a different FASTA under a taken name gets `-2`, `-3`.
Every Flag says what is wrong and what to do; its code has a help entry `library.<code lower-case>`.
"""
from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ionomos import fragpipe, names

WORKFLOW_SUFFIX = ".workflow"
FASTA_SUFFIXES = (".fas", ".fasta", ".fa")          # also .fasta.fas, FragPipe's own
UI_CACHE = "fragpipe-ui.cache"                       # FragpipeLocations.FN_CACHE_UI (FragPipe 24 source)
SEARCH_PROTEIN_LIST = "protein.fas"                  # Philosopher's per-group output beside psm.tsv: not a database
MAX_WORKFLOW_BYTES = 5_000_000                       # a FragPipe workflow is ~20 kB; bigger is something else
CHUNK = 8 << 20
HASH_BYTES = 1 << 20                                 # the fingerprint hashes the first and last 1 MB
DECOY_TAGS = ("rev_", "DECOY_", "REV_", "decoy_")    # detected, never assumed
CONTAM_TAGS = (b"contam_", b"CON__")
OLD_DAYS = 365
DEFAULTS = {"roots": [], "exclude": [], "max_depth": 6, "time_limit_s": 120}
SKIP_NAMES = {"$recycle.bin", "system volume information", ".venv", "venv", "node_modules", "__pycache__", ".git",
              "__macosx", names.RUN_DIR, *names.LEGACY_RUN_DIRS, "results", "site-packages"}
INSTALL_SKIP = {"jre", "lib", "tools", "python", "ext", "fonts", "images", "jobs"}   # inside a FragPipe install
INSTALL_GLOBS = ("C:/FragPipe/*", "C:/FragPipe*", "~/Downloads/FragPipe*")
SEVERITIES = ("error", "warn", "info")
KINDS = ("isoDTB", "TMT", "DIA", "LFQ", "SILAC", "isoTOP", "ipIAA", "PAL", "labelled", "phospho", "open", "other")
# MS1-labelled workflows (ionquant.use-labeling) by the light / heavy masses FragPipe 24.0's stock workflows set in
# ionquant.light / .heavy (chemprot-ABPP-isoDTB, -isoTOP, -ipIAA, chemprot-PAL); K/R labels are SILAC (SILAC3)
LABEL_KINDS = (("isoDTB", (561.3387, 567.3462)), ("isoTOP", (464.28596, 470.29977)), ("ipIAA", (463.2366, 467.2529)),
               ("PAL", (436.2256, 442.2633)))
LABELLED_KINDS = ("isoDTB", "SILAC", "isoTOP", "ipIAA", "PAL", "labelled")
# FragPipe's own stock workflow for a method kind, the usual one first (#148): a tie between stock workflows of the
# kind is broken by this, never by the file name
CANONICAL_STOCK = {"isoDTB": ("chemprot-ABPP-isoDTB", "isoDTB-ABPP"), "TMT": ("TMT10-MS3", "TMT10", "TMT16"),
                   "DIA": ("DIA_SpecLib_Quant",), "LFQ": ("LFQ-MBR",)}
TIE = 0.5                                            # candidates closer than this are a tie: nothing is proposed


class LibraryError(ValueError):
    """The `library:` block of config.yaml is wrong."""


# ------------------------------------------------------------------ data --


@dataclass
class Flag:
    """Something about a workflow, a FASTA or a method a person should know: what is wrong and what to do."""
    code: str
    severity: str        # error | warn | info
    text: str
    fix: str = ""
    subject: str = ""    # the file or method it is about

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "text": self.text, "fix": self.fix,
                "subject": self.subject}

    @classmethod
    def from_dict(cls, d: dict) -> Flag:
        return cls(str(d.get("code", "")), str(d.get("severity", "info")), str(d.get("text", "")),
                   str(d.get("fix", "")), str(d.get("subject", "")))

    @property
    def help_id(self) -> str:
        return f"library.{self.code.lower()}"


@dataclass
class WorkflowInfo:
    path: str
    name: str
    kind: str = "other"                       # isoDTB | TMT | DIA | LFQ | phospho | open | other
    traits: list[str] = field(default_factory=list)   # phospho, open, mass-offset, ...
    saved_with: str = ""                      # workflow.saved-with-ver, e.g. 24.0-build27
    description: str = ""
    database: str = ""
    database_exists: bool = False
    decoy_tag: str = "rev_"
    needs: dict = field(default_factory=dict)  # fragpipe.workflow_needs
    settings: dict = field(default_factory=dict)   # label -> value, the settings lab members care about
    label_type: str = ""                      # tmtintegrator.channel_num when TMT-Integrator runs
    sdrf: bool = False                        # workflow.misc.save-sdrf
    summary: str = ""
    source: str = "other"                     # pinned | stock | run | cache | other
    runs: dict = field(default_factory=dict)  # for a run: {"tables": [...], "finished": bool, "when": "YYYY-MM-DD"}
    content_hash: str = ""
    mtime: float = 0.0
    size: int = 0
    error: str = ""
    flags: list[Flag] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "flags"}
        d["flags"] = [f.as_dict() for f in self.flags]
        d["type"] = "workflow"
        return d

    @classmethod
    def from_dict(cls, d: dict) -> WorkflowInfo:
        kw = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "flags"}
        info = cls(**kw)
        info.flags = [Flag.from_dict(f) for f in d.get("flags") or []]
        return info

    @property
    def finished(self) -> bool:
        return bool(self.runs.get("finished"))


@dataclass
class FastaInfo:
    path: str
    name: str
    size: int = 0
    mtime: float = 0.0
    entries: int = 0
    targets: int = 0
    decoys: int = 0
    decoy_tag: str = ""                       # the prefix found ('' = none)
    decoy_tags: dict = field(default_factory=dict)
    contaminants: int = 0
    species: list = field(default_factory=list)   # [[taxon, name, count]], most first (targets, no contaminants)
    top_species: str = ""
    top_share: float = 0.0
    reviewed: int = 0                         # sp|
    unreviewed: int = 0                       # tr|
    isoforms: int = 0
    proteome: str = ""                        # UP000005640 from the file name
    taxon: str = ""                           # 9606 from the file name (or the headers)
    name_date: str = ""                       # 2026-01-30 from the file name
    fragpipe_style: bool = False
    fingerprint: str = ""
    complete: bool = True                     # False: the time ran out before the end of the file
    ox_share: float = 0.0                     # share of the proteins whose header names an organism (OX=)
    reader: int = 0                           # FASTA_READER that read it; an older reading is read again
    summary: str = ""
    source: str = "other"                     # pinned | cache | other
    error: str = ""
    flags: list[Flag] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "flags"}
        d["flags"] = [f.as_dict() for f in self.flags]
        d["type"] = "fasta"
        return d

    @classmethod
    def from_dict(cls, d: dict) -> FastaInfo:
        kw = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "flags"}
        info = cls(**kw)
        info.flags = [Flag.from_dict(f) for f in d.get("flags") or []]
        return info

    @property
    def proteins(self) -> int:
        return max(self.targets - self.contaminants, 0)


@dataclass
class Catalog:
    workflows: list[WorkflowInfo] = field(default_factory=list)
    fastas: list[FastaInfo] = field(default_factory=list)
    hints: list[dict] = field(default_factory=list)     # what FragPipe's window last used (fragpipe-ui.cache)
    roots: list[str] = field(default_factory=list)
    scanned_at: str = ""
    seconds: float = 0.0
    folders: int = 0
    complete: bool = True
    stopped: str = ""                         # "" | "time limit" | "stopped"
    reached: str = ""                         # the last folder looked at when it stopped
    skipped: list[str] = field(default_factory=list)
    skipped_count: int = 0
    notes: list[str] = field(default_factory=list)
    adopted: list[dict] = field(default_factory=list)   # provenance of every adopt

    def as_dict(self) -> dict:
        return {"version": 1, "workflows": [w.as_dict() for w in self.workflows],
                "fastas": [f.as_dict() for f in self.fastas],
                **{k: getattr(self, k) for k in ("hints", "roots", "scanned_at", "seconds", "folders", "complete",
                                                 "stopped", "reached", "skipped", "skipped_count", "notes",
                                                 "adopted")}}

    @classmethod
    def from_dict(cls, d: dict) -> Catalog:
        cat = cls(workflows=[WorkflowInfo.from_dict(w) for w in d.get("workflows") or []],
                  fastas=[FastaInfo.from_dict(f) for f in d.get("fastas") or []])
        for k in ("hints", "roots", "scanned_at", "seconds", "folders", "complete", "stopped", "reached",
                  "skipped", "skipped_count", "notes", "adopted"):
            if k in d:
                setattr(cat, k, d[k])
        return cat

    def find(self, path: str | Path) -> WorkflowInfo | FastaInfo | None:
        key = _key(path)
        return next((x for x in (*self.workflows, *self.fastas) if _key(x.path) == key), None)

    def how_far(self) -> str:
        """One line on what the scan covered."""
        done = (f"{len(self.workflows)} workflow(s) and {len(self.fastas)} FASTA(s) in {self.folders} folder(s), "
                f"{self.seconds:.0f} s")
        if self.stopped:
            return f"{done}; stopped ({self.stopped}) at {self.reached}: folders after it were not looked at"
        return done


@dataclass
class Candidate:
    info: WorkflowInfo | FastaInfo
    score: float
    reasons: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    copies: int = 1                           # files with the same settings (or the same FASTA content)

    def as_dict(self) -> dict:
        return {"path": self.info.path, "name": self.info.name, "summary": self.info.summary,
                "score": round(self.score, 1), "reasons": list(self.reasons), "copies": self.copies,
                "flags": [f.as_dict() for f in self.flags]}


@dataclass
class Proposal:
    method: str
    what: str                                 # workflow | fasta
    current: str
    why: str
    candidate: Candidate

    def as_dict(self) -> dict:
        return {"method": self.method, "what": self.what, "current": self.current, "why": self.why,
                "proposed": self.candidate.as_dict()}

    def text(self) -> str:
        return (f"{self.method}: {self.what} {self.current or '(none)'} — {self.why}. Proposed: "
                f"{self.candidate.info.path} ({'; '.join(self.candidate.reasons[:3])})")


@dataclass
class Context:
    """What the judgement of a file depends on beyond the file itself."""
    workflow_dir: Path | None = None
    fasta_dir: Path | None = None
    installed_version: str = ""
    tools: dict | None = None                 # tool -> installed? (None: not known)
    today: date | None = None
    pinned: dict = field(default_factory=dict)   # kind -> path of the pinned workflow a method uses
    install_roots: list[Path] = field(default_factory=list)


# ----------------------------------------------------------------- helpers --


def _key(p: str | Path) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(p)))
    except (OSError, ValueError):
        return str(p)


def _inside(child: str | Path, parent: Path | None) -> bool:
    if parent is None:
        return False
    c, p = _key(child), _key(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _ver(text: str) -> tuple[int, int] | None:
    m = re.match(r"\s*(\d+)(?:\.(\d+))?", text or "")
    return (int(m.group(1)), int(m.group(2) or 0)) if m else None


def _ver_text(text: str) -> str:
    v = _ver(text)
    return f"{v[0]}.{v[1]}" if v else (text or "?")


def _on(props: dict, key: str) -> bool:
    return props.get(key, "").strip().lower() == "true"


def _num(text: str, default: float | None = None) -> float | None:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return default


def _fmt_num(x: float) -> str:
    return f"{x:g}"


def _day(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


def safe_name(name: str) -> str:
    """A FragPipe-safe file name: no spaces, only letters, digits and . _ - (FragPipe refuses spaces in paths)."""
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("_")
    return out or "file"


# -------------------------------------------------------------- workflows --

# Modification masses lab members know by name (Da, as FragPipe writes them). Matched within 0.002 Da.
MOD_NAMES = (
    (57.02146, "carbamidomethyl"), (15.9949, "oxidation"), (42.0106, "acetyl"), (79.96633, "phospho"),
    (229.16293, "TMT"), (304.20715, "TMTpro"), (561.3387, "isoDTB light"), (567.3462, "isoDTB heavy"),
    (-17.0265, "pyro-glu"), (-18.0106, "pyro-glu"), (114.04293, "GG (ubiquitin)"), (0.98402, "deamidation"),
    (144.10207, "iTRAQ-4"), (28.0313, "dimethyl"), (14.01565, "methyl"), (8.0142, "Lys8 (SILAC)"),
    (10.00827, "Arg10 (SILAC)"), (4.025107, "Lys4 (SILAC)"), (6.020129, "Arg6 (SILAC)"),
    (464.28596, "isoTOP light"), (470.29977, "isoTOP heavy"), (463.2366, "ipIAA light"), (467.2529, "ipIAA heavy"),
)
_SITES = {"[^": "protein N-term", "n^": "peptide N-term", "c^": "peptide C-term", "]^": "protein C-term",
          "n-term peptide": "peptide N-term", "c-term peptide": "peptide C-term",
          "n-term protein": "protein N-term", "c-term protein": "protein C-term", "nqnc": "N-term Q/C",
          "ne": "N-term E"}
# TMT-Integrator's codes (its README and FragPipe's TMT-Integrator panel); shown as words, the code beside them
_GROUPBY = {"-1": "all levels", "0": "gene", "1": "protein", "2": "peptide", "3": "multi-site", "4": "single-site",
            "5": "glycan"}
_PROT_NORM = {"-1": "all (MD, GN and None tables)", "0": "none", "1": "median centring (MD)",
              "2": "median centring + variance scaling (GN)"}


def mod_name(mass: float) -> str:
    return next((n for m, n in MOD_NAMES if abs(m - mass) < 0.002), f"{mass:+.4f}")


def _site(text: str) -> str:
    s = text.split(" (")[0].strip()
    return _SITES.get(s.lower(), s)


def parse_mods(value: str, fixed: bool = False) -> list[tuple[float, str, bool]]:
    """FragPipe's mod tables: 'mass,site,enabled,max; ...' -> [(mass, site words, enabled)] with mass != 0."""
    out = []
    for part in (value or "").split(";"):
        cells = [c.strip() for c in part.split(",")]
        if len(cells) < 3:
            continue
        mass = _num(cells[0])
        if mass is None or abs(mass) < 1e-6:
            continue
        out.append((mass, _site(cells[1]), cells[2].lower() == "true"))
    return out


def _label_kind(props: dict, var: list) -> str:
    """What an MS1-labelled workflow (ionquant.use-labeling) labels, from ionquant.light / .medium / .heavy
    ('C561.3387', 'K0;R0', 'K8.014199;R10.008269', '*436.2256'): isoDTB / isoTOP / ipIAA / PAL by their masses,
    SILAC for labels on K and R only; without labels set, an isoDTB mass among the variable mods; else labelled."""
    masses, sites = [], set()
    for key in ("ionquant.light", "ionquant.medium", "ionquant.heavy"):
        for part in re.split(r"[;,\s]+", props.get(key, "")):
            m = re.match(r"^([A-Za-z*\[\]^]*)(-?\d+(?:\.\d+)?)$", part.strip())
            if m:
                sites.add(m.group(1).upper())
                masses.append(float(m.group(2)))
    for kind, label in LABEL_KINDS:
        if any(abs(x - y) < 0.002 for x in masses for y in label):
            return kind
    if sites and sites <= {"K", "R"}:
        return "SILAC"
    if not masses and any(abs(m - x) < 0.002 for m, _s, _e in var for x in (561.3387, 567.3462)):
        return "isoDTB"
    return "labelled"


def workflow_kind(props: dict) -> tuple[str, list[str]]:
    """(kind, traits) of a workflow. Kind: fpfolder.workflow_kind's TMT / DIA / LFQ, or for MS1-labelled
    quantification which label (isoDTB, SILAC, isoTOP, ipIAA, PAL, else labelled: _label_kind), then an isoDTB label
    in the variable mods, then open / mass-offset (a wide precursor window in Da, or mass offsets), then phospho
    (79.9663 on STY switched on), else other."""
    from ionomos.fpfolder import workflow_kind as quant_kind

    var = [m for m in parse_mods(props.get("msfragger.table.var-mods", "")) if m[2]]
    traits: list[str] = []
    if any(abs(m - 79.96633) < 0.002 for m, _s, _e in var):
        traits.append("phospho")
    lo = _num(props.get("msfragger.precursor_mass_lower", ""))
    hi = _num(props.get("msfragger.precursor_mass_upper", ""))
    if props.get("msfragger.precursor_mass_units", "").strip() == "0" and lo is not None and hi is not None \
            and hi - lo >= 50:
        traits.append("open")
    offsets = props.get("msfragger.mass_offsets", "").strip()
    if (offsets and offsets not in ("0", "0.0")) or props.get("msfragger.mass_offsets_detailed", "").strip():
        traits.append("mass-offset")
    base = quant_kind(props)
    if base == "isoDTB":   # fpfolder: MS1 quant with labelling on, whatever the label (#148)
        base = _label_kind(props, var)
    if base in (None, "LFQ") and any(abs(m - x) < 0.002 for m, _s, _e in var for x in (561.3387, 567.3462)):
        base = "isoDTB" if base == "LFQ" else base
        traits.append("isoDTB label")
    if base in ("TMT", "DIA", *LABELLED_KINDS):
        return base, traits
    if "open" in traits or "mass-offset" in traits:
        return "open", traits
    if "phospho" in traits:
        return "phospho", traits
    return (base or "other"), traits


def _tolerance(lo: str, hi: str, units: str) -> str:
    a, b = _num(lo), _num(hi)
    unit = "ppm" if units.strip() == "1" else "Da"
    if a is None or b is None:
        return ""
    if abs(a + b) < 1e-9:
        return f"±{_fmt_num(b)} {unit}"
    return f"{_fmt_num(a)} to {_fmt_num(b)} {unit}"


def _fdr(filt: str) -> str:
    m = re.search(r"--prot\s+([\d.]+)", filt or "")
    if not m:
        return ""
    v = _num(m.group(1))
    return f"{100 * v:g} % protein FDR" if v is not None else ""


def workflow_settings(props: dict, kind: str = "") -> dict[str, str]:
    """label -> value for the settings lab members care about; a key the workflow lacks is left out."""
    s: dict[str, str] = {}
    enz = [props.get(f"msfragger.search_enzyme_name_{i}", "") for i in (1, 2)]
    enz = [e for e in enz if e and e.lower() != "null"]
    if enz:
        s["enzyme"] = " + ".join(enz)
    mc = props.get("msfragger.allowed_missed_cleavage_1", "")
    if mc:
        s["missed cleavages"] = mc
    fixed = [f"{mod_name(m)} {site}" for m, site, on in parse_mods(props.get("msfragger.table.fix-mods", ""), True)
             if on]
    if fixed or "msfragger.table.fix-mods" in props:
        s["fixed mods"] = ", ".join(fixed) or "none"
    var = [f"{mod_name(m)} {site}" for m, site, on in parse_mods(props.get("msfragger.table.var-mods", "")) if on]
    if var or "msfragger.table.var-mods" in props:
        s["variable mods"] = ", ".join(var) or "none"
    tol = _tolerance(props.get("msfragger.precursor_mass_lower", ""), props.get("msfragger.precursor_mass_upper", ""),
                     props.get("msfragger.precursor_mass_units", "1"))
    if tol:
        s["precursor tolerance"] = tol
    ft = props.get("msfragger.fragment_mass_tolerance", "")
    if ft:
        s["fragment tolerance"] = f"{ft} {'ppm' if props.get('msfragger.fragment_mass_units', '').strip() == '1' else 'Da'}"
    if _on(props, "tmtintegrator.run-tmtintegrator") or kind == "TMT":
        s["TMT-Integrator"] = "on" if _on(props, "tmtintegrator.run-tmtintegrator") else "off"
        s["label type"] = props.get("tmtintegrator.channel_num", "") or "(not set)"
        add_ref = props.get("tmtintegrator.add_Ref", "").strip()
        if add_ref and add_ref != "-1":
            how = {"0": "sum", "1": "average", "2": "median"}.get(add_ref, add_ref)
            s["TMT reference"] = f"a virtual reference ({how} of the channels)"
        elif props.get("tmtintegrator.ref_tag"):
            s["TMT reference"] = f"the channel whose name contains '{props['tmtintegrator.ref_tag']}'"
        gb = props.get("tmtintegrator.groupby", "")
        if gb:
            s["TMT group by"] = _GROUPBY.get(gb.strip(), gb)
        pn = props.get("tmtintegrator.prot_norm", "")
        if pn:
            s["TMT normalisation"] = _PROT_NORM.get(pn.strip(), pn)
        if props.get("tmtintegrator.min_purity"):
            s["TMT min purity"] = props["tmtintegrator.min_purity"]
    if "quantitation.run-label-free-quant" in props:
        ms1 = _on(props, "quantitation.run-label-free-quant")
        s["MS1 quant"] = ("on, labelled (light/heavy)" if ms1 and _on(props, "ionquant.use-labeling")
                          else "on" if ms1 else "off")
        if ms1 and "ionquant.mbr" in props:
            s["match-between-runs"] = "on" if props["ionquant.mbr"].strip() in ("1", "true") else "off"
    if "diann.run-dia-nn" in props:
        s["DIA-NN"] = "on" if _on(props, "diann.run-dia-nn") else "off"
    if _on(props, "speclibgen.run-speclibgen"):
        s["spectral library"] = "built from the search"
    val = [n for k, n in (("percolator.run-percolator", "Percolator"),
                          ("peptide-prophet.run-peptide-prophet", "PeptideProphet")) if _on(props, k)]
    if val:
        s["PSM validation"] = " + ".join(val)
    fdr = _fdr(props.get("phi-report.filter", ""))
    if fdr:
        s["FDR"] = fdr
    return s


# `workdir` is the output folder FragPipe writes into a run's own fragpipe.workflow (`<workdir>/fragpipe.workflow`);
# `fragpipe-config.*` are tool locations (bin-diann, tools-folder ...): this PC's, not the search's
_HASH_SKIP = re.compile(r"(^database\.db-path$|^workflow\.(saved-with-ver|description|workflow-option|ram|threads"
                        r"|last-save-dir)$|^fragpipe-config\.|(^|[-_.])(work)?(path|dir|folder|file)$)", re.I)
_ABS_PATH = re.compile(r"^([A-Za-z]:[\\/]|\\\\|//|/[^/\s]+/)")   # C:\..., C:/..., \\server, //server, /Users/...


def content_hash(props: dict) -> str:
    """sha1 of the settings without the database, the FragPipe version, the description, threads/RAM and any
    path-like key or value: the same settings saved under another name, for another FASTA or as a run's copy (its
    `workdir`) hash the same."""
    h = hashlib.sha1()
    for k in sorted(props):
        v = props[k].strip()
        if not _HASH_SKIP.search(k) and not _ABS_PATH.match(v):
            h.update(f"{k}={v}\n".encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _clean_description(text: str) -> str:
    t = re.sub(r"<[^>]*>", " ", text.replace("\\=", "=").replace("\\:", ":").replace("\\n", " "))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:400]


def _kind_words(info: WorkflowInfo, props: dict) -> str:
    st = info.settings
    if info.kind == "TMT":
        lt = info.label_type or "TMT (label type not set)"
        ms3 = " MS3" if "ms3" in info.name.lower() or "MS3" in info.description else ""
        return f"A {lt}{ms3} search with TMT-Integrator"
    if info.kind in LABELLED_KINDS:
        mbr = st.get("match-between-runs")
        what = {"isoDTB": "An isoDTB search (light/heavy labelled cysteines, IonQuant)",
                "SILAC": "A SILAC search (heavy lysine and arginine, IonQuant)",
                "isoTOP": "An isoTOP-ABPP search (light/heavy TEV tags on cysteines, IonQuant)",
                "ipIAA": "An ipIAA search (light/heavy isopropyl iodoacetamide on cysteines, IonQuant)",
                "PAL": "A photoaffinity labelling (PAL) search (light/heavy probe, IonQuant)",
                }.get(info.kind, "A light/heavy labelled search (IonQuant)")
        return what + (f", MBR {mbr}" if mbr else "")
    if info.kind == "DIA":
        lib = " with a spectral library from MSFragger" if "spectral library" in st else ""
        return f"A DIA search with DIA-NN{lib}"
    if info.kind == "LFQ":
        mbr = st.get("match-between-runs")
        return "A label-free DDA search (IonQuant MS1 quant" + (f", MBR {mbr})" if mbr else ")")
    if info.kind == "open":
        if "mass-offset" in info.traits:
            return "A mass-offset search"
        return f"An open search (precursor window {st.get('precursor tolerance', '?')})"
    if info.kind == "phospho":
        return "A phospho search (spectral counts only, no MS1 quant)"
    return "A FragPipe search without quantification"


def _summary(info: WorkflowInfo, props: dict) -> str:
    st = info.settings
    first = _kind_words(info, props)
    if "phospho" in info.traits and info.kind != "phospho":
        first += ", phospho (STY) variable"
    parts = []
    if st.get("fixed mods") and st["fixed mods"] != "none":
        parts.append(f"{st['fixed mods']} fixed")
    if st.get("variable mods") and st["variable mods"] != "none":
        parts.append(f"{st['variable mods']} variable")
    if st.get("enzyme"):
        parts.append(st["enzyme"] + (f", {st['missed cleavages']} missed cleavages" if st.get("missed cleavages")
                                     else ""))
    text = first + (": " + "; ".join(parts) if parts else "") + "."
    tail = []
    if info.saved_with:
        tail.append(f"Saved with FragPipe {_ver_text(info.saved_with)}")
    if info.database:
        tail.append(f"made for {Path(info.database.replace(chr(92), '/')).name}"
                    + ("" if info.database_exists else " (not on this PC)"))
    if tail:
        text += " " + "; ".join(tail) + "."
    return text


def _source_of(path: Path, ctx: Context | None) -> str:
    if ctx is not None and _inside(path, ctx.workflow_dir):
        return "pinned"
    parent = path.parent
    if parent.name.lower() == "workflows" and (_is_install_dir(parent.parent) or any(
            _key(parent.parent) == _key(r) for r in (ctx.install_roots if ctx else []))):
        return "stock"
    if path.name.lower() == "fragpipe.workflow" or any((parent / m).is_file() for m in
                                                        ("fragpipe.job", "fragpipe-files.fp-manifest")):
        return "run"
    return "other"


def _is_install_dir(d: Path) -> bool:
    """A FragPipe installation: bin/fragpipe(.bat) (installer 23 / 24, the zip builds' fragpipe/) or
    lib/fragpipe-<version>.jar inside. A folder merely named like one (C:/Fragpipe_Auto) is not."""
    try:
        for sub, pats in (("bin", ("fragpipe*", "FragPipe*")), ("lib", ("fragpipe*.jar", "FragPipe*.jar"))):
            if (d / sub).is_dir() and any(any((d / sub).glob(pat)) for pat in pats):
                return True
    except OSError:
        pass
    return False


_ALL_DONE = re.compile(r"ALL JOBS DONE IN")


def run_evidence(folder: Path) -> dict:
    """For a fragpipe.workflow inside an output folder: the readable result tables there, whether FragPipe's log
    says ALL JOBS DONE, and the newest file's date. Only reads."""
    from ionomos import fpfolder

    try:
        outs = fpfolder.find_outputs(folder, depth=1)
    except OSError:
        outs = []
    out = next((o for o in outs if _key(o.root) == _key(folder)), outs[0] if outs else None)
    if out is None:
        return {"tables": [], "finished": False, "all_done": False, "when": ""}
    tables = out.readable
    when = _day(out.newest())
    return {"tables": tables, "finished": bool(tables), "all_done": bool(_ALL_DONE.search(out.log_text or "")),
            "when": when}


def describe_workflow(path: str | Path, ctx: Context | None = None, *, evidence: bool = True) -> WorkflowInfo:
    """What a .workflow file is, in plain words. Reads it once; for a run's workflow, also what the run left."""
    p = Path(path)
    info = WorkflowInfo(path=str(p), name=p.name)
    try:
        st = p.stat()
        info.mtime, info.size = st.st_mtime, st.st_size
        if st.st_size > MAX_WORKFLOW_BYTES:
            raise ValueError(f"{st.st_size / 1e6:.0f} MB, far bigger than a FragPipe workflow")
        text = p.read_bytes().decode("utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        info.error = str(exc)
        info.flags = workflow_flags(info, ctx)
        return info
    props = fragpipe.read_properties(text)
    if not any(k.startswith(("msfragger.", "workflow.", "database.", "tmtintegrator.", "diann.")) for k in props):
        info.error = "no FragPipe settings in it"
        info.flags = workflow_flags(info, ctx)
        return info
    info.kind, info.traits = workflow_kind(props)
    info.saved_with = props.get("workflow.saved-with-ver", "").strip()
    info.description = _clean_description(props.get("workflow.description", ""))
    db = props.get("database.db-path", "").strip()
    info.database = db
    info.database_exists = bool(db) and Path(db).is_file()
    info.decoy_tag = props.get("database.decoy-tag", "rev_").strip() or "rev_"
    info.needs = fragpipe.workflow_needs(props)
    info.settings = workflow_settings(props, info.kind)
    info.label_type = props.get("tmtintegrator.channel_num", "").strip() if _on(
        props, "tmtintegrator.run-tmtintegrator") else ""
    info.sdrf = _on(props, "workflow.misc.save-sdrf")
    info.content_hash = content_hash(props)
    info.source = _source_of(p, ctx)
    if evidence and info.source == "run":
        info.runs = run_evidence(p.parent)
    info.summary = _summary(info, props)
    info.flags = workflow_flags(info, ctx)
    return info


def workflow_flags(info: WorkflowInfo, ctx: Context | None = None) -> list[Flag]:
    """The flags one workflow carries by itself and in this context (not those that need the whole catalog)."""
    ctx = ctx or Context()
    out: list[Flag] = []
    subj = info.path

    def add(code, sev, text, fix):
        out.append(Flag(code, sev, text, fix, subj))

    if info.error:
        add("WF_UNREADABLE", "error", f"{info.name} can't be used as a FragPipe workflow: {info.error}",
            "Pick another workflow, or save this one again from FragPipe's Workflow tab (Save to custom folder).")
        return out
    if " " in info.path:
        add("WF_SPACE_IN_PATH", "info", f"{info.path} has a space in its path; FragPipe refuses such paths",
            "Adopt it (Ionomos copies it into the workflows folder under a name without spaces); don't point "
            "FragPipe at it where it is.")
    if info.database and not info.database_exists:
        add("WF_DATABASE_MISSING", "warn", f"The FASTA this workflow was saved with ({info.database}) is not on "
            f"this PC", "Ionomos searches with the method's FASTA instead; check that it is the same organism "
            "and pick one in the method's FASTA box.")
    elif info.database and ctx.fasta_dir is not None and not _inside(info.database, ctx.fasta_dir):
        add("WF_DATABASE_ELSEWHERE", "info", f"Its FASTA {info.database} is outside the FASTA folder "
            f"{ctx.fasta_dir}", "Adopting the workflow copies the FASTA into the FASTA folder too.")
    saved, inst = _ver(info.saved_with), _ver(ctx.installed_version)
    if saved and inst:
        if saved[0] < inst[0]:
            add("WF_OLDER_FRAGPIPE", "warn", f"Saved with FragPipe {_ver_text(info.saved_with)}; this PC runs "
                f"{_ver_text(ctx.installed_version)}", f"FragPipe loads it, but settings added since then take "
                f"their defaults. Load it in FragPipe {_ver_text(ctx.installed_version)}, check it, and save it "
                f"again (or use the installed version's own stock workflow of this kind).")
        elif saved > inst:
            add("WF_NEWER_FRAGPIPE", "warn", f"Saved with FragPipe {_ver_text(info.saved_with)}, newer than the "
                f"{_ver_text(ctx.installed_version)} on this PC", "Settings the older FragPipe doesn't know are "
                "ignored. Update FragPipe, or save the workflow again from the installed version.")
    if ctx.tools is not None:
        missing = [t for t, needed in info.needs.items() if needed and ctx.tools.get(t) is False]
        if missing:
            add("WF_TOOL_MISSING", "error", f"It needs {', '.join(missing)}, which the FragPipe installation "
                f"doesn't have", "Install the tool from FragPipe's Config tab (Download / Update), or set its "
                "path in the app's Advanced settings; `ionomos preflight` checks it.")
    if info.sdrf:
        add("WF_SDRF_ON", "warn", "It makes FragPipe write sdrf.tsv into the output (workflow.misc.save-sdrf=true)",
            "The analysis would read that file as your experiment's design (D59 item 11). Switch off 'Save SDRF' "
            "in FragPipe's Workflow tab and save the workflow again, or leave it: Ionomos then warns in the report.")
    if info.kind == "TMT" and not info.label_type:
        add("WF_LABEL_TYPE_UNSET", "error", "A TMT workflow without a label type (tmtintegrator.channel_num)",
            "Open it in FragPipe, choose the label type (TMT-10, TMT-16 ...) on the Quant (Isobaric) tab, and save "
            "it again.")
    if info.source == "run" and info.runs and not info.runs.get("finished"):
        add("WF_NO_RUNS", "info", "The search in this folder left no result table Ionomos can read",
            "It may have failed or been stopped, or its tables were moved. Prefer a workflow whose search finished.")
    return out


# ------------------------------------------------------------------ FASTAs --

_FASTA_CACHE: dict[tuple[str, float, int], FastaInfo] = {}
_OX = re.compile(rb"\bOX=(\d+)")
_OS = re.compile(rb"\bOS=(.+?)(?:\s[A-Z]{2}=|$)")
_ISOFORM = re.compile(rb"^(?:sp|tr)\|[A-Za-z0-9]+-\d+\|")
_NAME_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_PROTEOME = re.compile(r"(UP\d{9})(?:_(\d+))?")
_FP_STYLE = re.compile(r"^\d{4}-\d{2}-\d{2}-(?:td-|decoys-)(?:[a-z]+-)*UP\d{9}(?:_\d+)?\.fasta\.fas$", re.I)
FASTA_READER = 2                                     # 2: organism and reviewed share judged on every protein (#151)
ORGANISM_MIN_SHARE = 0.5                             # the organism is named only when this share of proteins says it
SPECIES_WORDS = {"9606": "Human", "10090": "Mouse", "10116": "Rat", "559292": "Yeast", "4932": "Yeast",
                 "83333": "E. coli", "562": "E. coli", "7227": "Fly", "6239": "Worm", "7955": "Zebrafish",
                 "3702": "Arabidopsis", "9913": "Cow", "9823": "Pig", "9031": "Chicken"}


def _headers(fh, stop: threading.Event | None, deadline: float | None):
    """Every header line (without '>') of a FASTA opened in binary mode, read in big chunks: a 1 GB file is
    never decoded or split into lines. Raises TimeoutError when stopped or out of time."""
    carry = b"\n"
    while True:
        if (stop is not None and stop.is_set()) or (deadline is not None and time.monotonic() > deadline):
            raise TimeoutError
        chunk = fh.read(CHUNK)
        if not chunk:
            break
        buf = carry + chunk
        pos = 0
        carry = b""
        while True:
            i = buf.find(b"\n>", pos)
            if i < 0:
                k = buf.rfind(b"\n", pos)
                carry = buf[k:] if k >= 0 else b""
                break
            j = buf.find(b"\n", i + 1)
            if j < 0:
                carry = buf[i:]
                break
            yield buf[i + 2:j].rstrip(b"\r")
            pos = j
    if carry.startswith(b"\n>"):
        yield carry[2:].rstrip(b"\r\n")


def _fingerprint(p: Path, size: int, entries: int) -> str:
    h = hashlib.sha1()
    try:
        with open(p, "rb") as fh:
            h.update(fh.read(HASH_BYTES))
            if size > HASH_BYTES:
                fh.seek(max(size - HASH_BYTES, HASH_BYTES))
                h.update(fh.read(HASH_BYTES))
    except OSError:
        return ""
    return f"{size}-{entries}-{h.hexdigest()[:16]}"


def describe_fasta(path: str | Path, ctx: Context | None = None, *, stop: threading.Event | None = None,
                   deadline: float | None = None) -> FastaInfo:
    """What a FASTA holds: entries, decoys (the tag is detected), contaminants, species, reviewed share, isoforms,
    the proteome and date in its name, a fingerprint, a summary and flags. Cached by (path, mtime, size)."""
    p = Path(path)
    info = FastaInfo(path=str(p), name=p.name)
    _from_name(info)
    try:
        st = p.stat()
    except OSError as exc:
        info.error = f"not readable: {exc}"
        info.flags = fasta_flags(info, ctx)
        return info
    info.size, info.mtime = st.st_size, st.st_mtime
    key = (_key(p), st.st_mtime, st.st_size)
    cached = _FASTA_CACHE.get(key)
    if cached is not None:
        out = FastaInfo.from_dict(cached.as_dict())
        out.flags = fasta_flags(out, ctx)
        return out
    tags = Counter()
    contam = reviewed = unreviewed = isoforms = entries = 0
    species: Counter = Counter()
    species_name: dict[str, str] = {}
    tag_bytes = [t.encode() for t in DECOY_TAGS]
    try:
        with open(p, "rb") as fh:
            for h in _headers(fh, stop, deadline):
                entries += 1
                tag = next((t for t in tag_bytes if h.startswith(t)), None)
                if tag is not None:
                    tags[tag.decode()] += 1
                    continue
                if h.startswith(CONTAM_TAGS):
                    contam += 1
                    continue
                if h.startswith(b"sp|"):
                    reviewed += 1
                elif h.startswith(b"tr|"):
                    unreviewed += 1
                if _ISOFORM.match(h):
                    isoforms += 1
                m = _OX.search(h)
                if m:
                    ox = m.group(1).decode()
                    species[ox] += 1
                    if ox not in species_name:
                        n = _OS.search(h)
                        species_name[ox] = n.group(1).decode("utf-8", "replace").strip() if n else ""
    except TimeoutError:
        info.complete = False
    except OSError as exc:
        info.error = f"not readable: {exc}"
        info.flags = fasta_flags(info, ctx)
        return info
    info.entries = entries
    if tags:
        info.decoy_tag, info.decoys = tags.most_common(1)[0]
        info.decoy_tags = dict(tags)
    info.targets = entries - sum(tags.values())
    info.contaminants, info.reviewed, info.unreviewed, info.isoforms = contam, reviewed, unreviewed, isoforms
    info.species = [[ox, species_name.get(ox, ""), n] for ox, n in species.most_common(8)]
    total = sum(species.values())
    info.ox_share = total / info.proteins if info.proteins else 0.0
    if total and info.ox_share >= ORGANISM_MIN_SHARE:
        ox, n = species.most_common(1)[0]
        info.top_species = SPECIES_WORDS.get(ox) or species_name.get(ox) or f"taxon {ox}"
        info.top_share = n / total
        info.taxon = info.taxon or ox
    elif info.taxon:   # from the file name (UP000005640_9606)
        info.top_species = SPECIES_WORDS.get(info.taxon, f"taxon {info.taxon}")
    # else: most targets name no organism (GENCODE / Ensembl / smORF headers carry no OX=): the few that do, often
    # its contaminants, don't say what the file is (#151)
    if entries == 0 and info.complete:
        info.error = "no protein entries (no line starts with '>'): not a FASTA"
    info.fingerprint = _fingerprint(p, info.size, entries) if info.complete else ""
    info.reader = FASTA_READER
    info.summary = _fasta_summary(info)
    if info.complete and not info.error:
        _FASTA_CACHE[key] = FastaInfo.from_dict(info.as_dict())
    info.flags = fasta_flags(info, ctx)
    return info


def _from_name(info: FastaInfo) -> None:
    m = _NAME_DATE.match(info.name)
    if m:
        try:
            datetime.strptime(m.group(1), "%Y-%m-%d")
            info.name_date = m.group(1)
        except ValueError:
            pass
    m = _PROTEOME.search(info.name)
    if m:
        info.proteome, info.taxon = m.group(1), m.group(2) or ""
    info.fragpipe_style = bool(_FP_STYLE.match(info.name))


def _mix_words(info: FastaInfo) -> str:
    total = sum(c for _o, _n, c in info.species) or 1
    return " / ".join(f"{SPECIES_WORDS.get(o) or n or 'taxon ' + o} {100 * c / total:.0f} %"
                      for o, n, c in info.species[:3])


def _mostly_not_uniprot(info: FastaInfo) -> bool:
    """Fewer than half of the proteins have UniProt headers (sp| / tr|): their reviewed share says little."""
    return (info.reviewed + info.unreviewed) < 0.5 * info.proteins


def _fasta_summary(info: FastaInfo) -> str:
    if info.error:
        return f"{info.name}: {info.error}."
    who = info.top_species or ("Organism unknown (most headers name none: no OX=)" if info.species
                               else "Unknown organism")
    if info.top_species and len(info.species) > 1 and info.top_share < 0.9:
        who = "Mixed: " + _mix_words(info)
    uni = info.reviewed + info.unreviewed
    if uni == 0:
        src = "not UniProt-style headers"
    elif _mostly_not_uniprot(info):
        src = f"mostly not UniProt-style headers ({uni:,} UniProt-style)"
    elif info.unreviewed == 0:
        src = "reviewed UniProt"
    elif info.reviewed == 0:
        src = "unreviewed UniProt (TrEMBL)"
    else:
        src = f"UniProt, {100 * info.unreviewed / uni:.0f} % unreviewed"
    iso = f", {info.isoforms:,} isoforms" if info.isoforms else ""
    what = []
    what.append(f"decoys ({info.decoy_tag})" if info.decoys else "NO decoys")
    what.append(f"{info.contaminants} contaminants" if info.contaminants else "no contaminants")
    text = f"{who}, {src} ({info.proteins:,} proteins{iso}) with {' and '.join(what)}"
    if info.name_date:
        text += f", made {info.name_date}"
    if info.proteome:
        text += f" (proteome {info.proteome})"
    if not info.complete:
        text += f"; only part of the file was read in the time allowed ({info.entries:,} entries so far)"
    return text + "."


def fasta_flags(info: FastaInfo, ctx: Context | None = None) -> list[Flag]:
    ctx = ctx or Context()
    out: list[Flag] = []
    subj = info.path

    def add(code, sev, text, fix):
        out.append(Flag(code, sev, text, fix, subj))

    if info.error:
        add("FASTA_UNREADABLE", "error", f"{info.name}: {info.error}", "Pick another FASTA.")
        return out
    if " " in info.path:
        add("FASTA_SPACE_IN_PATH", "warn", f"{info.path} has a space in its path, which FragPipe refuses",
            "Adopt it: Ionomos copies it into the FASTA folder under a name without spaces.")
    if not info.complete:
        return out
    big = info.size >= fragpipe.BIG_FASTA_BYTES
    if info.decoys == 0:
        add("FASTA_NO_DECOYS", "error", f"{info.name} has no decoys (no entry starts with "
            f"{' / '.join(DECOY_TAGS)}); headless FragPipe stops on that",
            "Make a FASTA with decoys and contaminants: FragPipe's Database tab (Download, or Browse + Add decoys), "
            "or `ionomos fasta prepare`.")
    else:
        share = info.decoys / info.entries if info.entries else 0
        if share < 0.4 or share > 0.6:
            add("FASTA_DECOY_SHARE", "info" if big else "error",
                f"{100 * share:.1f} % of the entries of {info.name} are decoys ({info.decoy_tag}); FragPipe stops "
                f"unless 40-60 % are" + (" (it doesn't count files of 1 GB or more)" if big else ""),
                "Add decoys once, to a FASTA without any (FragPipe's Database tab); a file with decoys added twice "
                "or only partly is not usable.")
        if info.decoy_tag != "rev_":
            add("FASTA_DECOY_TAG", "info", f"The decoys of {info.name} start with '{info.decoy_tag}', not FragPipe's "
                f"usual 'rev_'", f"The workflow's decoy tag (database.decoy-tag) must be '{info.decoy_tag}' for this "
                f"file.")
    if info.contaminants == 0:
        add("FASTA_NO_CONTAMINANTS", "warn", f"{info.name} has no contaminant entries (contam_ / CON__)",
            "Keratins, trypsin and BSA then end up as hits of your organism. Make the FASTA with contaminants "
            "(FragPipe's Database tab: Add contaminants).")
    if info.top_species and len(info.species) > 1 and info.top_share < 0.9:
        add("FASTA_MIXED_SPECIES", "warn", f"{info.name} mixes organisms: {_mix_words(info)}",
            "Fine on purpose (a mixed-species benchmark); otherwise use a FASTA of your organism alone.")
    if info.unreviewed > info.reviewed and info.unreviewed > 0.5 * info.proteins:
        add("FASTA_UNREVIEWED", "warn", f"Most entries of {info.name} are unreviewed UniProt (tr|, TrEMBL)",
            "The lab's searches use reviewed (Swiss-Prot) proteomes; unreviewed entries make the search slower and "
            "the protein groups larger. Use a reviewed FASTA unless you need TrEMBL.")
    if info.name_date:
        today = ctx.today or date.today()
        try:
            age = (today - datetime.strptime(info.name_date, "%Y-%m-%d").date()).days
        except ValueError:
            age = 0
        if age > OLD_DAYS:
            add("FASTA_OLD", "warn", f"{info.name} was made {info.name_date}, {age // 30} months ago",
                "UniProt changes every 8 weeks. Make a new one for new projects (FragPipe's Database tab, or "
                "`ionomos fasta download`); keep the old one for re-analysing old searches.")
    return out


# ----------------------------------------------------------------- context --


def _launchers() -> list[Path]:
    return fragpipe.launcher_candidates()


def installed_version(cfg=None) -> str:
    """The FragPipe version Ionomos runs ('24.0'), from the configured launcher's path, else the newest found."""
    paths = [str(cfg.fragpipe_exe)] if cfg is not None else []
    paths += [str(p) for p in _launchers()]
    for p in paths:
        m = re.search(r"FragPipe(?:-jre)?-(\d+(?:\.\d+)*)", p, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def install_roots(cfg=None) -> list[Path]:
    """Every FragPipe installation folder found (the configured one first)."""
    out: list[Path] = []
    if cfg is not None:
        r = fragpipe.fragpipe_root(Path(cfg.fragpipe_exe))
        if r is not None:
            out.append(r)
    for launcher in _launchers():
        r = fragpipe.fragpipe_root(launcher)
        if r is not None:
            out.append(r)
    for pat in INSTALL_GLOBS:   # C:/FragPipe* also matches C:/Fragpipe_Auto: only real installations count (#153)
        for hit in glob.glob(os.path.expanduser(pat)):
            for d in (Path(hit), Path(hit) / "fragpipe"):   # the zip: FragPipe-jre-22.0/fragpipe/bin/fragpipe.bat
                if _is_install_dir(d):
                    out.append(d)
                    break
    seen, uniq = set(), []
    for p in out:
        if p.is_dir() and _key(p) not in seen:
            seen.add(_key(p))
            uniq.append(p)
    return uniq


def _tools(cfg) -> dict | None:
    """tool -> installed, from fragpipe.install_report; None when the installation can't be looked at."""
    try:
        rows = fragpipe.install_report(cfg)
    except Exception:  # noqa: BLE001 - a judgement aid, never a reason for a scan to fail
        return None
    labels = {r[1]: r[0] for r in rows}
    if labels.get("launcher") is False or "MSFragger" not in labels:
        return None
    return {"MSFragger": labels.get("MSFragger") is True, "IonQuant": labels.get("IonQuant") is True,
            "diaTracer": labels.get("diaTracer") is True, "DIA-NN": labels.get("DIA-NN") is True,
            "Python": labels.get("Python for FragPipe") is True}


def _method_kind(cfg_dict: dict, key: str) -> str:
    m = (cfg_dict.get("methods") or {}).get(key) or {}
    engine = str(m.get("engine") or "fragpipe").lower()
    if engine == "diann":
        return "DIA"
    if engine in ("maxquant", "sage"):
        return "LFQ"
    rule = ((cfg_dict.get("naming") or {}).get("methods") or {}).get(key)
    if isinstance(rule, dict) and rule.get("like"):
        return str(rule["like"])
    return key


def context_from(cfg, *, tools: bool = True, today: date | None = None) -> Context:
    ctx = Context(workflow_dir=Path(cfg.workflow_dir), fasta_dir=Path(cfg.fasta_dir),
                  installed_version=installed_version(cfg), tools=_tools(cfg) if tools else None, today=today,
                  install_roots=install_roots(cfg))
    for key, m in cfg.methods.items():
        if str(m.extra.get("engine") or "fragpipe").lower() != "fragpipe" or not m.workflow:
            continue
        wf = _pinned_file(Path(cfg.workflow_dir), m.workflow, WORKFLOW_SUFFIX)
        if wf is not None:
            ctx.pinned.setdefault(cfg.kind(key), str(wf))
    return ctx


def _pinned_file(folder: Path, name: str, suffix: str = "") -> Path | None:
    p = Path(name)
    cands = [p] if p.is_absolute() else [folder / name]
    if suffix and not name.lower().endswith(suffix):
        cands.append(cands[0].with_name(cands[0].name + suffix))
    return next((c for c in cands if c.is_file()), None)


# -------------------------------------------------------------------- scan --


def settings_from(raw) -> dict:
    """The `library:` block of config.yaml, checked; {} / None = the defaults."""
    if raw is None:
        return dict(DEFAULTS, roots=[], exclude=[])
    if not isinstance(raw, dict):
        raise LibraryError("must be a mapping (roots, exclude, max_depth, time_limit_s)")
    bad = sorted(set(map(str, raw)) - set(DEFAULTS))
    if bad:
        raise LibraryError(f"{bad[0]}: unknown setting (known: {', '.join(DEFAULTS)})")
    out = dict(DEFAULTS, roots=[], exclude=[])
    for k in ("roots", "exclude"):
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) and x.strip() for x in v):
            raise LibraryError(f"{k} must be a list of folders" + (" or name patterns" if k == "exclude" else ""))
        out[k] = [x.strip() for x in v]
    for k, lo, hi in (("max_depth", 1, 30), ("time_limit_s", 5, 3600)):
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise LibraryError(f"{k} must be a number from {lo} to {hi}")
        out[k] = int(v) if k == "max_depth" else float(v)
    return out


def _settings(cfg) -> dict:
    lib = getattr(cfg, "library", None)
    try:
        return settings_from(lib or None)
    except LibraryError:
        return settings_from(None)


def default_roots(cfg, missing: list[str] | None = None) -> list[Path]:
    """Where a scan looks by default: workflow_dir, fasta_dir, every FragPipe installation, users_root and the
    config's library.roots (UNC paths are plain folders). Only folders that exist, each once; a configured folder
    that does not exist is said in `missing` (a list of notes), never dropped silently (#153)."""
    named = [(Path(cfg.workflow_dir), "paths.workflow_dir"), (Path(cfg.fasta_dir), "paths.fasta_dir")]
    named += [(p, "") for p in install_roots(cfg)]
    named += [(Path(cfg.users_root), "paths.users_root")]
    named += [(Path(os.path.expanduser(r)), "library.roots") for r in _settings(cfg)["roots"]]
    seen, out = set(), []
    for p, where in named:
        try:
            ok = p.is_dir()
        except OSError:
            ok = False
        if not ok:
            if where and missing is not None:
                missing.append(missing_note(p, where))
            continue
        if _key(p) not in seen:
            seen.add(_key(p))
            out.append(p)
    return out


def missing_note(p: str | Path, where: str = "") -> str:
    """The note for a folder to look in that is not there."""
    return (f"{p} does not exist on this PC: not looked at (a drive not plugged in, or a typo"
            + (f" in {where}" if where else "") + ")")


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith((".", "$")):
        return True
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & 0x6)   # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


def _skip_reason(entry: os.DirEntry, parent_is_install: bool, exclude: list[str]) -> str:
    low = entry.name.lower()
    if low in SKIP_NAMES:
        return "Ionomos' own or a system folder"
    if "_previous_" in low:
        return "an earlier attempt's output"
    if low.endswith(".d"):
        return "a Bruker run"
    if parent_is_install and low in INSTALL_SKIP:
        return "FragPipe's program files"
    for pat in exclude:
        pl = pat.lower().replace("\\", "/")
        if fnmatch.fnmatch(low, pl) or fnmatch.fnmatch(entry.path.lower().replace("\\", "/"), pl):
            return f"excluded ({pat})"
    if _hidden(entry):
        return "hidden or system folder"
    return ""


def _in_search_output(d: Path, here: set[str]) -> bool:
    """`d` is a folder of FragPipe's output: psm.tsv / protein.tsv beside, or fragpipe.workflow in it or one up
    (`<workdir>/<group>/protein.fas`). `here`: the lower-case names in `d`."""
    if here & {"psm.tsv", "protein.tsv", "fragpipe.workflow"}:
        return True
    try:
        return (d.parent / "fragpipe.workflow").is_file()
    except OSError:
        return False


def _read_ui_cache(path: Path) -> dict:
    """What FragPipe's window last used, from <install>/cache/fragpipe-ui.cache (Java properties with the
    workflow's own keys; FragPipe 24 source: Fragpipe.saveCache, TabWorkflow 'workflow.workflow-option')."""
    try:
        props = fragpipe.read_properties(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}
    return {"cache": str(path), "database": props.get("database.db-path", "").strip(),
            "workflow": props.get("workflow.workflow-option", "").strip(),
            "last_save_dir": props.get("workflow.last-save-dir", "").strip(),
            "saved_with": props.get("workflow.saved-with-ver", "").strip()}


def scan(roots: list[str | Path] | None = None, *, cfg=None, ctx: Context | None = None,
         exclude: list[str] | None = None, max_depth: int | None = None, progress=None,
         stop: threading.Event | None = None, time_limit_s: float | None = None,
         previous: Catalog | None = None) -> Catalog:
    """Walk `roots` (default: default_roots(cfg)) breadth-first, all roots together, and describe every
    .workflow, .fas / .fasta / .fa and fragpipe-ui.cache found. Read-only; at most `max_depth` folders deep, at most
    `time_limit_s` seconds; `stop` ends it early. A folder reached twice (a junction, a link) is looked at once.
    `progress(text)` is called now and then with a line on where it is. `previous` lends its FASTA readings to
    files that have not changed."""
    lib = _settings(cfg) if cfg is not None else settings_from(None)
    exclude = list(lib["exclude"] if exclude is None else exclude)
    max_depth = int(lib["max_depth"] if max_depth is None else max_depth)
    limit = float(lib["time_limit_s"] if time_limit_s is None else time_limit_s)
    if ctx is None:
        ctx = context_from(cfg) if cfg is not None else Context()
    missing: list[str] = []
    if roots is None:
        roots = default_roots(cfg, missing) if cfg is not None else []
    if previous is not None:
        for f in previous.fastas:
            if f.complete and not f.error and f.fingerprint and f.reader >= FASTA_READER:
                _FASTA_CACHE.setdefault((_key(f.path), f.mtime, f.size), f)
    started = time.monotonic()
    deadline = started + limit
    cat = Catalog(roots=[str(r) for r in roots], scanned_at=datetime.now().isoformat(timespec="seconds"),
                  notes=missing)
    queue: deque[tuple[Path, int]] = deque()
    seen: set[str] = set()
    for r in roots:
        p = Path(r)
        try:
            real = _key(os.path.realpath(p))
        except OSError:
            real = _key(p)
        if real in seen:
            continue
        if not p.is_dir():
            cat.notes.append(missing_note(p))
            continue
        seen.add(real)
        queue.append((p, 0))
    last_say = 0.0
    wf_seen: set[str] = set()
    fa_seen: set[str] = set()

    def say(text: str, force: bool = False) -> None:
        nonlocal last_say
        if progress is None:
            return
        now = time.monotonic()
        if force or now - last_say >= 0.5:
            last_say = now
            try:
                progress(text)
            except Exception:  # noqa: BLE001 - a broken progress callback must not end the scan
                pass

    def skip(path: str, why: str) -> None:
        cat.skipped_count += 1
        if len(cat.skipped) < 200:
            cat.skipped.append(f"{path}: {why}")

    def out_of_time() -> str:
        if stop is not None and stop.is_set():
            return "stopped"
        if time.monotonic() > deadline:
            return "time limit"
        return ""

    while queue:
        why = out_of_time()
        if why:
            cat.complete, cat.stopped = False, why
            break
        d, level = queue.popleft()
        cat.folders += 1
        cat.reached = str(d)
        say(f"{cat.folders} folders, {len(cat.workflows)} workflows, {len(cat.fastas)} FASTAs: {d}")
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError as exc:
            skip(str(d), f"can't be read ({exc.strerror or exc})")
            continue
        is_install = _is_install_dir(d)
        here = {e.name.lower() for e in entries}
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=True)
            except OSError:
                continue
            if is_dir:
                if level >= max_depth:
                    continue
                reason = _skip_reason(e, is_install, exclude)
                if reason:
                    skip(e.path, reason)
                    continue
                try:
                    link = e.is_symlink() or bool(getattr(e, "is_junction", lambda: False)()) or bool(
                        getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)  # reparse point
                    real = _key(os.path.realpath(e.path)) if link else _key(e.path)
                except OSError:
                    continue
                if real in seen:
                    skip(e.path, "already looked at (a link or junction to it)")
                    continue
                seen.add(real)
                queue.append((Path(e.path), level + 1))
                continue
            low = e.name.lower()
            why = out_of_time()
            if why:
                cat.complete, cat.stopped = False, why
                break
            if low.endswith(WORKFLOW_SUFFIX):
                if _key(e.path) not in wf_seen:
                    wf_seen.add(_key(e.path))
                    cat.workflows.append(describe_workflow(e.path, ctx))
                    say(f"workflow {e.path}", force=True)
            elif low.endswith(FASTA_SUFFIXES):
                if low == SEARCH_PROTEIN_LIST and _in_search_output(d, here):
                    skip(e.path, "the proteins one FragPipe search found (Philosopher's protein.fas beside "
                                 "psm.tsv), not a search database")
                elif _key(e.path) not in fa_seen:
                    fa_seen.add(_key(e.path))
                    say(f"reading FASTA {e.path}", force=True)
                    info = describe_fasta(e.path, ctx, stop=stop, deadline=deadline)
                    if _inside(e.path, ctx.fasta_dir):
                        info.source = "pinned"
                    cat.fastas.append(info)
            elif low == UI_CACHE:
                hint = _read_ui_cache(Path(e.path))
                if hint:
                    cat.hints.append(hint)
        if cat.stopped:
            break
    # FragPipe's window remembers the last FASTA: describe it when the walk didn't pass it
    for hint in cat.hints:
        db = hint.get("database")
        if db and Path(db).is_file() and _key(db) not in fa_seen and not out_of_time():
            fa_seen.add(_key(db))
            info = describe_fasta(db, ctx, stop=stop, deadline=deadline)
            info.source = "cache"
            cat.fastas.append(info)
    if previous is not None:
        cat.adopted = list(previous.adopted)
    cat.seconds = round(time.monotonic() - started, 1)
    judge_catalog(cat, ctx)
    say(cat.how_far(), force=True)
    return cat


def judge_catalog(cat: Catalog, ctx: Context | None = None) -> Catalog:
    """Re-do every flag: each file's own (workflow_flags / fasta_flags) and those that need the whole catalog
    (FASTA_DUPLICATE, WF_DIFFERS_FROM_PINNED, WF_DECOY_TAG_MISMATCH, WF_NO_RUNS for a group never run)."""
    ctx = ctx or Context()
    for w in cat.workflows:
        if ctx.workflow_dir is not None and _inside(w.path, ctx.workflow_dir):
            w.source = "pinned"
        w.flags = workflow_flags(w, ctx)
    for f in cat.fastas:
        if ctx.fasta_dir is not None and _inside(f.path, ctx.fasta_dir) and f.source != "cache":
            f.source = "pinned"
        f.flags = fasta_flags(f, ctx)
    by_print: dict[str, list[FastaInfo]] = {}
    for f in cat.fastas:
        if f.fingerprint:
            by_print.setdefault(f.fingerprint, []).append(f)
    for group in by_print.values():
        if len(group) > 1:
            for f in group:
                others = [g.path for g in group if g is not f]
                f.flags.append(Flag("FASTA_DUPLICATE", "info", f"The same file is also at {others[0]}"
                                    + (f" and {len(others) - 1} more place(s)" if len(others) > 1 else ""),
                                    "Nothing to fix; keep the one in the FASTA folder and use it everywhere.", f.path))
    fastas = {_key(f.path): f for f in cat.fastas}
    groups = _groups(cat.workflows)
    pinned = {kind: cat.find(p) or describe_workflow(p, ctx, evidence=False) for kind, p in ctx.pinned.items()
              if Path(p).is_file()}
    for w in cat.workflows:
        if w.error:
            continue
        fa = fastas.get(_key(w.database)) if w.database else None
        if fa is not None and fa.decoy_tag and fa.decoy_tag != w.decoy_tag:
            w.flags.append(Flag("WF_DECOY_TAG_MISMATCH", "error", f"The workflow's decoy tag is '{w.decoy_tag}', but "
                                f"the decoys of {fa.name} start with '{fa.decoy_tag}'", "FragPipe would find no "
                                "decoys and stop. Set the decoy tag on FragPipe's Database tab to match the FASTA "
                                "(or use a FASTA made by FragPipe), then save the workflow again.", w.path))
        group = groups.get(w.content_hash, [w])
        if w.source != "run" and not any(g.finished for g in group):
            w.flags.append(Flag("WF_NO_RUNS", "info", "No finished search on this PC used these settings",
                                "Not wrong in itself; a workflow that has produced results before is the safer "
                                "choice. Try it on a small search first.", w.path))
        pin = pinned.get(w.kind)
        if pin is not None and not pin.error and _key(pin.path) != _key(w.path) \
                and pin.content_hash != w.content_hash:
            diff = settings_diff(pin, w)
            if diff:
                w.flags.append(Flag("WF_DIFFERS_FROM_PINNED", "info", f"Differs from the pinned {w.kind} workflow "
                                    f"({pin.name}): " + "; ".join(diff[:6]) + (" ..." if len(diff) > 6 else ""),
                                    "Check that the difference is meant before adopting it for the method.", w.path))
    return cat


def settings_diff(a: WorkflowInfo, b: WorkflowInfo) -> list[str]:
    """'label: b's value (a's: …)' for every setting that differs."""
    out = []
    for k in [*a.settings, *[k for k in b.settings if k not in a.settings]]:
        va, vb = a.settings.get(k, "(not set)"), b.settings.get(k, "(not set)")
        if va != vb:
            out.append(f"{k}: {vb} (pinned: {va})")
    return out


def used_in(cat: Catalog, w: WorkflowInfo) -> str:
    """'used in N finished search(es), the latest YYYY-MM-DD' for the finished runs with w's settings (its own
    run, or the runs whose fragpipe.workflow hashes like it); "" when none."""
    runs = [x for x in cat.workflows if x.finished and (x is w or (w.content_hash and
                                                                   x.content_hash == w.content_hash))]
    if not runs:
        return ""
    latest = max((x.runs.get("when") or "" for x in runs), default="")
    return f"used in {len(runs)} finished search(es)" + (f", the latest {latest}" if latest else "")


def _groups(workflows: list[WorkflowInfo]) -> dict[str, list[WorkflowInfo]]:
    g: dict[str, list[WorkflowInfo]] = {}
    for w in workflows:
        if w.content_hash:
            g.setdefault(w.content_hash, []).append(w)
    return g


# ----------------------------------------------------------------- catalog --


def catalog_path(cfg) -> Path:
    return Path(cfg.log_dir) / names.LIBRARY_FILE


def save_catalog(cfg, cat: Catalog) -> Path:
    """Write <log_dir>/library.json (through a temporary file, so a crash never leaves half a file)."""
    p = catalog_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cat.as_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_catalog(cfg) -> Catalog:
    """The last scan; an empty catalog (with a note) when there is none or it can't be read."""
    p = catalog_path(cfg)
    if not p.is_file():
        return Catalog(notes=["no scan yet: run `ionomos library scan`"])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        return Catalog.from_dict(data)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return Catalog(notes=[f"{p} could not be read ({exc}); run `ionomos library scan` again"])


def _unchanged(x: WorkflowInfo | FastaInfo) -> bool:
    try:
        st = Path(x.path).stat()
    except OSError:
        return False
    return st.st_size == x.size and abs(st.st_mtime - x.mtime) < 1e-3


def covers(new: Catalog, prev: Catalog) -> bool:
    """`new` replaces `prev`: it ran to the end and looked in every folder `prev` looked in."""
    return new.complete and {_key(r) for r in prev.roots} <= {_key(r) for r in new.roots}


def merge_scan(prev: Catalog, new: Catalog, ctx: Context | None = None) -> Catalog:
    """A scan that stopped early or looked in fewer folders never loses what a fuller one found (#153): `new`, plus
    every file of `prev` it did not see that is still on disk unchanged (their roots kept too), judged again as one
    catalog. `new` itself when it covers `prev`."""
    if covers(new, prev) or not (prev.workflows or prev.fastas):
        return new
    seen = {_key(x.path) for x in (*new.workflows, *new.fastas)}
    kept_w = [w for w in prev.workflows if _key(w.path) not in seen and _unchanged(w)]
    kept_f = [f for f in prev.fastas if _key(f.path) not in seen and _unchanged(f)]
    if not kept_w and not kept_f:
        return new
    for w in kept_w:
        if w.source != "run":
            w.source = _source_of(Path(w.path), ctx)
    new_roots = {_key(r) for r in new.roots}
    others = [r for r in prev.roots if _key(r) not in new_roots]
    new.workflows += kept_w
    new.fastas += kept_f
    new.roots += others
    why = "stopped early" if not new.complete else "looked in fewer folders"
    new.notes.append(f"kept {len(kept_w)} workflow(s) and {len(kept_f)} FASTA(s) from the scan of "
                     f"{prev.scanned_at.replace('T', ' ') or 'before'}, because this one {why}"
                     + (f" (not looked in now: {', '.join(others[:3])}{' …' if len(others) > 3 else ''})"
                        if others else "") + "; `ionomos library scan --replace` keeps only this scan")
    judge_catalog(new, ctx)
    return new


def save_scan(cfg, new: Catalog, prev: Catalog | None = None, *, replace: bool = False,
              ctx: Context | None = None) -> tuple[Catalog, str]:
    """Save a scan's catalog, merged with the previous one unless it covers it or `replace` (merge_scan).
    Returns (the catalog saved, a line on what was saved)."""
    prev = load_catalog(cfg) if prev is None else prev
    found = len(new.workflows) + len(new.fastas)
    cat = new
    if not replace and not covers(new, prev) and (prev.workflows or prev.fastas):
        cat = merge_scan(prev, new, ctx or context_from(cfg))
    where = save_catalog(cfg, cat)
    kept = len(cat.workflows) + len(cat.fastas) - found
    return cat, f"saved in {where}" + (f"; kept {kept} file(s) from the last scan that this one did not look at"
                                       if kept else "")


# --------------------------------------------------------------- recommend --


def _penalty(flags: list[Flag]) -> tuple[float, list[str]]:
    errs = [f for f in flags if f.severity == "error"]
    warns = [f for f in flags if f.severity == "warn"]
    words = [f"problem: {f.text}" for f in errs] + [f"note: {f.text}" for f in warns[:2]]
    return -40.0 * len(errs) - 3.0 * len(warns), words


def _stem(name: str) -> str:
    return name[:-len(WORKFLOW_SUFFIX)] if name.lower().endswith(WORKFLOW_SUFFIX) else name


def canonical_rank(name: str, kind: str, prefer: str = "") -> int | None:
    """0 for the stock workflow FragPipe names for this kind of experiment (or the one the method asks for by name,
    `prefer`), 1, 2 ... for the next ones (CANONICAL_STOCK); None for any other."""
    names = ([_stem(prefer)] if prefer else []) + list(CANONICAL_STOCK.get(kind, ()))
    stem = _stem(name).lower()
    return next((i for i, n in enumerate(names) if n.lower() == stem), None)


def recommend(catalog: Catalog, kind: str, installed_version: str = "",
              pinned: WorkflowInfo | None = None, prefer: str = "") -> list[Candidate]:
    """Workflows of this kind, one candidate per set of settings (the same settings under several names count
    once), best first. The score and its reasons, in words: finished searches that used the settings, the
    FragPipe version it was saved with against the installed one, whether its FASTA exists, pinned / stock, and
    whether it is FragPipe's own stock workflow for this kind (CANONICAL_STOCK; `prefer`: the workflow name the
    method asks for). Candidates of equal score are in no meaningful order: auto_assign proposes none of them."""
    inst = _ver(installed_version)
    groups = _groups([w for w in catalog.workflows if w.kind.lower() == kind.lower() and not w.error
                      and Path(w.path).is_file()])   # a catalog can be older than the disk
    out: list[Candidate] = []
    for members in groups.values():
        order = {"pinned": 0, "stock": 1, "run": 2, "cache": 3, "other": 4}
        def newest(w):   # the newest FragPipe version first, then the newest file (#156)
            v = _ver(w.saved_with) or (-1, -1)
            return (-v[0], -v[1], -w.mtime)
        if pinned is not None:
            members.sort(key=lambda w: (_key(w.path) != _key(pinned.path), order.get(w.source, 9), *newest(w)))
        else:
            members.sort(key=lambda w: (order.get(w.source, 9), *newest(w)))
        rep = members[0]
        score, reasons = 0.0, []
        runs = [w for w in members if w.finished]
        if runs:
            latest = max((w.runs.get("when") or "" for w in runs), default="")
            score += 4.0 * min(len(runs), 10)
            reasons.append(f"used in {len(runs)} finished search(es)" + (f", the latest {latest}" if latest else ""))
        else:
            reasons.append("no finished search on this PC used these settings")
        saved = _ver(rep.saved_with)
        if saved and inst:
            if saved[0] == inst[0]:
                score += 10
                reasons.append(f"saved with the installed FragPipe {_ver_text(installed_version)}")
            elif saved < inst:
                score -= 3 * (inst[0] - saved[0])
                reasons.append(f"saved with FragPipe {_ver_text(rep.saved_with)}, older than the installed "
                               f"{_ver_text(installed_version)}")
            else:
                score -= 8
                reasons.append(f"saved with FragPipe {_ver_text(rep.saved_with)}, newer than the installed one")
        if any(w.database_exists for w in members):
            score += 5
            reasons.append("its FASTA exists")
        elif any(w.database for w in members):
            reasons.append("its FASTA is not on this PC")
        if pinned is not None and any(_key(w.path) == _key(pinned.path) for w in members):
            score += 6
            reasons.append("it is the one the method uses now")
        elif any(w.source == "pinned" for w in members):
            score += 3
            reasons.append("already in the workflows folder")
        if any(w.source == "stock" for w in members):
            score += 2
            reasons.append("a stock workflow of the FragPipe installation")
            ranks = [(r, w) for w in members if w.source == "stock"
                     for r in [canonical_rank(w.name, kind, prefer)] if r is not None]
            if ranks:
                r, w = min(ranks, key=lambda x: x[0])
                score += max(4.0 - r, 1.0)
                reasons.append(f"FragPipe's own {_stem(w.name)}, "
                               + ("the workflow name the method asks for" if prefer and r == 0
                                  else f"the usual stock workflow for {kind}"))
        flags = [f for f in rep.flags if f.code not in ("WF_NO_RUNS",)]
        pen, words = _penalty(flags)
        score += pen
        reasons += words
        out.append(Candidate(rep, score, reasons, list(rep.flags), copies=len(members)))
    out.sort(key=lambda c: (-c.score, -c.info.mtime, c.info.path))
    return out


def _used_fastas(catalog: Catalog) -> Counter:
    used: Counter = Counter()
    for w in catalog.workflows:
        if w.finished and w.database:
            used[_key(w.database)] += 1
            used["name:" + Path(w.database.replace("\\", "/")).name.lower()] += 1
    return used


def recommend_fastas(catalog: Catalog, taxon: str | None = None, kind: str | None = None) -> list[Candidate]:
    """FASTAs (one per content), best first: usable decoys, contaminants, reviewed, the organism asked for,
    made recently, used by finished searches, already in the FASTA folder. `kind` is not used yet: every search
    kind takes the same database (kept so the picker can ask per method)."""
    used = _used_fastas(catalog)
    groups: dict[str, list[FastaInfo]] = {}
    for f in catalog.fastas:
        if f.error or not Path(f.path).is_file():
            continue
        groups.setdefault(f.fingerprint or _key(f.path), []).append(f)
    out = []
    for members in groups.values():
        members.sort(key=lambda f: ({"pinned": 0, "cache": 1}.get(f.source, 2), " " in f.path, -f.mtime))
        rep = members[0]
        score, reasons = 0.0, []
        if taxon:
            if rep.taxon == str(taxon) or (rep.top_species and rep.species and rep.species[0][0] == str(taxon)):
                score += 15
                reasons.append(f"{rep.top_species or 'the organism'} as asked")
            elif not rep.top_species:
                reasons.append("its organism can't be read from its headers (no OX=) or its name")
            else:
                score -= 30
                reasons.append(f"another organism ({rep.top_species or 'unknown'})")
        n = sum(used.get(_key(f.path), 0) for f in members) or used.get("name:" + rep.name.lower(), 0)
        if n:
            score += 3.0 * min(n, 10)
            reasons.append(f"used by {n} finished search(es)")
        if rep.decoys and not any(f.code in ("FASTA_DECOY_SHARE", "FASTA_NO_DECOYS") for f in rep.flags):
            score += 10
            reasons.append(f"decoys ({rep.decoy_tag}) as FragPipe needs them")
        if rep.contaminants:
            score += 3
            reasons.append("contaminants included")
        if rep.reviewed and rep.reviewed >= rep.unreviewed:
            score += 2
            reasons.append("reviewed UniProt")
        if rep.name_date:
            score += max(0.0, 3.0 - _age_days(rep.name_date) / 365.0)
            reasons.append(f"made {rep.name_date}")
        if rep.source == "pinned":
            score += 4
            reasons.append("already in the FASTA folder")
        pen, words = _penalty(rep.flags)
        score += pen
        reasons += words
        out.append(Candidate(rep, score, reasons, list(rep.flags), copies=len(members)))
    out.sort(key=lambda c: (-c.score, -c.info.mtime, c.info.path))
    return out


def _age_days(day: str) -> int:
    try:
        return (date.today() - datetime.strptime(day, "%Y-%m-%d").date()).days
    except ValueError:
        return 0


def _lab_taxon(catalog: Catalog, fasta_dir: Path | None) -> str | None:
    """The organism of the FASTAs already in the FASTA folder, else of the FASTAs finished searches used."""
    pinned = Counter(f.taxon for f in catalog.fastas if f.taxon and _inside(f.path, fasta_dir))
    if pinned:
        return pinned.most_common(1)[0][0]
    used = _used_fastas(catalog)
    counts = Counter()
    for f in catalog.fastas:
        if f.taxon:
            counts[f.taxon] += used.get(_key(f.path), 0)
    best = counts.most_common(1)
    return best[0][0] if best and best[0][1] else None


def _clear_best(cands: list[Candidate]) -> tuple[Candidate | None, list[Candidate]]:
    """(the best candidate, []) when it is clearly ahead; (None, the tied ones) when the score can't tell (#148)."""
    if not cands:
        return None, []
    tied = [c for c in cands if cands[0].score - c.score < TIE]
    return (cands[0], []) if len(tied) == 1 else (None, tied)


def _tie_note(key: str, what: str, tied: list[Candidate], kind: str) -> str:
    names = ", ".join(c.info.name for c in tied[:4]) + (f" and {len(tied) - 4} more" if len(tied) > 4 else "")
    how = (f"`ionomos library list workflows --kind {kind}`" if what == "workflow"
           else "`ionomos library list fastas`")
    return (f"{key}: {len(tied)} {what}s fit equally well ({names}); none is proposed, so as not to pick by file "
            f"name. Pick one: {how}, then `ionomos library adopt PATH --method {key}` (or Find on this PC… in the "
            f"Methods tab)")


def auto_assign(cfg_dict: dict, catalog: Catalog, installed_version: str = "",
                notes: list[str] | None = None) -> list[Proposal]:
    """For every FragPipe method whose workflow or FASTA is missing or has an error, the best candidate.
    A method whose files are fine is left alone. When candidates tie (the score can't tell them apart) or there is
    none, nothing is proposed and `notes` says why. Nothing is changed: the caller applies the proposals
    (apply_proposals, then configio.write_config, which keeps a backup)."""
    notes = [] if notes is None else notes
    paths = cfg_dict.get("paths") or {}
    wf_dir = Path(paths["workflow_dir"]) if paths.get("workflow_dir") else None
    fa_dir = Path(paths["fasta_dir"]) if paths.get("fasta_dir") else None
    ctx = Context(workflow_dir=wf_dir, fasta_dir=fa_dir, installed_version=installed_version)
    taxon = _lab_taxon(catalog, fa_dir)
    out: list[Proposal] = []
    for key, m in (cfg_dict.get("methods") or {}).items():
        if not isinstance(m, dict):
            continue
        engine = str(m.get("engine") or "fragpipe").lower()
        kind = _method_kind(cfg_dict, key)
        current_wf = None
        if engine == "fragpipe":
            name = str(m.get("workflow") or "")
            current_wf = _pinned_file(wf_dir, name, WORKFLOW_SUFFIX) if (wf_dir and name) else None
            why = ""
            if current_wf is None:
                why = f"not found in {wf_dir}" if name else "no workflow set"
            else:
                info = catalog.find(current_wf) or describe_workflow(current_wf, ctx, evidence=False)
                errs = [f for f in info.flags if f.severity == "error"]
                if errs:
                    why = errs[0].text
            if why:
                cands = [c for c in recommend(catalog, kind, installed_version, prefer=name)
                         if not any(f.severity == "error" for f in c.flags)]
                best, tied = _clear_best(cands)
                if best is not None:
                    out.append(Proposal(key, "workflow", name, why, best))
                elif tied:
                    notes.append(_tie_note(key, "workflow", tied, kind))
                else:
                    notes.append(f"{key}: no {kind} workflow without a problem was found on this PC; nothing "
                                 f"proposed (Find on this PC… lists every workflow)")
        fname = str(m.get("fasta") or "")
        current_fa = _pinned_file(fa_dir, fname) if (fa_dir and fname) else None
        why = ""
        if current_fa is None:
            why = f"not found in {fa_dir}" if fname else "no FASTA set"
        else:
            info = catalog.find(current_fa) or describe_fasta(current_fa, ctx)
            errs = [f for f in info.flags if f.severity == "error"]
            if errs:
                why = errs[0].text
        if why:
            cands = [c for c in recommend_fastas(catalog, taxon, kind)
                     if not any(f.severity == "error" for f in c.flags)]
            best, tied = _clear_best(cands)
            if best is not None:
                out.append(Proposal(key, "fasta", fname, why, best))
            elif tied:
                notes.append(_tie_note(key, "FASTA", tied, kind))
            else:
                notes.append(f"{key}: no usable FASTA was found on this PC; nothing proposed (Download from UniProt… "
                             f"or `ionomos fasta download` makes one)")
    return out


# ------------------------------------------------------------------- adopt --


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _free_name(folder: Path, name: str) -> Path:
    """folder/name, or name-2, name-3 ... before the extension (.fasta.fas counts as one)."""
    target = folder / name
    if not target.exists():
        return target
    m = re.match(r"^(.*?)((?:\.fasta)?\.[A-Za-z]+)$", name)
    stem, ext = (m.group(1), m.group(2)) if m else (name, "")
    n = 2
    while (folder / f"{stem}-{n}{ext}").exists():
        n += 1
    return folder / f"{stem}-{n}{ext}"


def _record(cat: Catalog | None, cfg, entry: dict) -> None:
    entry = {"when": datetime.now().isoformat(timespec="seconds"), "who": "adopt", **entry}
    if cat is None:
        cat = load_catalog(cfg)
        cat.notes = [n for n in cat.notes if not n.startswith("no scan yet")]
    cat.adopted.append(entry)
    try:
        save_catalog(cfg, cat)
    except OSError:
        pass   # provenance is a record, never a reason for an adopt to fail


def adopt_fasta(info: FastaInfo | str | Path, cfg, catalog: Catalog | None = None) -> dict:
    """Copy a FASTA into fasta_dir under a FragPipe-safe name (no spaces). A file already there with the same
    content is used as it is; a different file under the same name is never replaced (the copy gets -2, -3 ...).
    Returns {'fasta': name in fasta_dir, 'path', 'copied': bool, 'notes': [...]}."""
    if not isinstance(info, FastaInfo):
        info = describe_fasta(info)
    src = Path(info.path)
    fasta_dir = Path(cfg.fasta_dir)
    notes: list[str] = []
    if not src.is_file():
        raise FileNotFoundError(f"{src} is not on this PC any more")
    if _inside(src, fasta_dir) and " " not in src.name:
        return {"fasta": src.name if src.parent == fasta_dir else str(src), "path": str(src), "copied": False,
                "notes": [f"{src.name} is already in the FASTA folder"]}
    fasta_dir.mkdir(parents=True, exist_ok=True)
    name = safe_name(src.name)
    target = fasta_dir / name
    if target.exists() and _same_file(src, target):
        notes.append(f"{name} is already in {fasta_dir} (same content); used as it is")
        copied = False
    else:
        if target.exists():
            target = _free_name(fasta_dir, name)
            notes.append(f"another file is already called {name}; this one is copied as {target.name}")
        part = target.with_name(target.name + ".part")
        shutil.copy2(src, part)
        os.replace(part, target)
        copied = True
        notes.append(f"copied {src} to {target}")
    if target.name != src.name:
        notes.append(f"named {target.name} (FragPipe can't use a path with spaces)" if " " in src.name else
                     f"named {target.name}")
    _record(catalog, cfg, {"type": "fasta", "origin": str(src), "copy": str(target), "fingerprint": info.fingerprint})
    return {"fasta": target.name, "path": str(target), "copied": copied, "notes": notes}


def _wf_name(name: str) -> str:
    n = Path(str(name or "").strip()).name
    return (n if not n or n.lower().endswith(WORKFLOW_SUFFIX) else n + WORKFLOW_SUFFIX).lower()


def live_workflows(cfg_or_path, but: str | None = None) -> set[str]:
    """The workflow file names (lower case) the methods of the SAVED config.yaml use, which the watcher searches
    with: a copy must not replace one before the config says so (#150). `cfg_or_path`: a Config read from disk or
    the config.yaml path; `but`: leave out this method's own file (its config is written right after)."""
    if isinstance(cfg_or_path, (str, Path)):
        path = Path(cfg_or_path)
        if not path.is_file():
            return set()
        from ionomos import configio

        try:
            methods = configio.read_config(path).get("methods") or {}
        except Exception:  # noqa: BLE001 - unreadable: nothing to protect that we can name
            return set()
        names = {k: str((m or {}).get("workflow") or "") for k, m in methods.items() if isinstance(m, dict)}
    else:
        names = {k: str(m.workflow or "") for k, m in cfg_or_path.methods.items()}
    return {_wf_name(n) for k, n in names.items() if n and k != but} - {""}


def adopt_problems(info: WorkflowInfo | FastaInfo, method_kind: str = "", ctx: Context | None = None) -> list[Flag]:
    """Why a file should not be adopted for a method without a second thought (#150): a workflow of another kind
    than the method's (WF_KIND_MISMATCH), and every error flag the file carries (FASTA_NO_DECOYS,
    FASTA_DECOY_SHARE, WF_LABEL_TYPE_UNSET, WF_TOOL_MISSING ...), for a workflow also WF_DECOY_TAG_MISMATCH against
    the FASTA it was saved with. [] = fine. The CLI refuses without --force, the app asks."""
    out = [f for f in info.flags if f.severity == "error"]
    if isinstance(info, WorkflowInfo) and not info.error:
        if method_kind in KINDS and info.kind != method_kind:
            out.insert(0, Flag("WF_KIND_MISMATCH", "error", f"{info.name} is a {info.kind} workflow "
                               f"({info.summary.split(':')[0].rstrip('.')}), not a {method_kind} one",
                               f"Pick a {method_kind} workflow (`ionomos library recommend` lists them; Find on this "
                               f"PC… in the app), or adopt it anyway if this is meant (--force).", info.path))
        if info.database_exists and not any(f.code == "WF_DECOY_TAG_MISMATCH" for f in out):
            fa = describe_fasta(info.database, ctx)
            if fa.decoy_tag and fa.decoy_tag != info.decoy_tag:
                out.append(Flag("WF_DECOY_TAG_MISMATCH", "error", f"The workflow's decoy tag is '{info.decoy_tag}', "
                                f"but the decoys of {fa.name} start with '{fa.decoy_tag}'", "FragPipe would find no "
                                "decoys and stop. Set the decoy tag on FragPipe's Database tab to match the FASTA, "
                                "then save the workflow again.", info.path))
    return out


def adopt(info: WorkflowInfo | str | Path, method: str, cfg, catalog: Catalog | None = None, *,
          avoid: set[str] | None = None) -> dict:
    """Copy a workflow into workflow_dir as <method>.workflow (a FragPipe-safe name), the way
    fragpipe.import_workflow does: the pinned file it replaces is kept as .bak-<time>. `avoid`: file names (any
    case) never to replace, the files the saved config's methods search with (live_workflows): then the copy gets
    a name of its own (<method>-2.workflow ...). Its FASTA is copied into fasta_dir too when it exists and is
    elsewhere (adopt_fasta), and the copy's database.db-path points at it.
    Changes no setting: returns {'workflow', 'fasta', 'notes', 'origin'} for the caller to put in config.yaml."""
    if not isinstance(info, WorkflowInfo):
        info = describe_workflow(info, evidence=False)
    src = Path(info.path)
    if not src.is_file():
        raise FileNotFoundError(f"{src} is not on this PC any more")
    if info.error:
        raise ValueError(f"{src.name} can't be adopted: {info.error}")
    workflow_dir, fasta_dir = Path(cfg.workflow_dir), Path(cfg.fasta_dir)
    stem = safe_name(method)
    notes: list[str] = []
    text = src.read_bytes().decode("utf-8", errors="replace")
    fasta_name = ""
    db = info.database
    if db and Path(db).is_file() and not _inside(db, fasta_dir):
        res = adopt_fasta(describe_fasta(db), cfg, catalog)
        notes += res["notes"]
        fasta_name = res["fasta"]
        text = fragpipe.patch_workflow(text, fasta_dir / fasta_name)
    dest = workflow_dir / f"{stem}{WORKFLOW_SUFFIX}"
    same = dest.is_file() and dest.read_bytes().decode("utf-8", errors="replace") == text
    if not same and dest.exists() and dest.name.lower() in {a.lower() for a in (avoid or ())}:
        live = dest.name
        dest = _free_name(workflow_dir, dest.name)
        stem = dest.name[:-len(WORKFLOW_SUFFIX)]
        notes.append(f"{live} is what a method searches with now, so it is left as it is; the copy is {dest.name}")
    if same:
        out = {"workflow": dest.name, "fasta": fasta_name or (Path(db).name if db and _inside(db, fasta_dir) else ""),
               "notes": notes + [f"{dest.name} already holds exactly this workflow; nothing copied"],
               "origin": str(src)}
        return out
    with tempfile.TemporaryDirectory(prefix="ionomos-adopt-") as tmp:
        staged = Path(tmp) / f"{stem}{WORKFLOW_SUFFIX}"
        staged.write_text(text, encoding="utf-8", newline="")
        res = fragpipe.import_workflow(staged, stem, workflow_dir, fasta_dir)
    notes += res["notes"]
    if stem != method and stem == safe_name(method):
        notes.append(f"saved as {dest.name} (FragPipe-safe name for method {method})")
    notes.append(f"copied from {src}")
    _record(catalog, cfg, {"type": "workflow", "method": method, "origin": str(src), "copy": str(dest),
                           "fingerprint": info.content_hash, "saved_with": info.saved_with})
    if catalog is not None:
        new = describe_workflow(dest, Context(workflow_dir=workflow_dir, fasta_dir=fasta_dir), evidence=False)
        new.source = "pinned"
        catalog.workflows = [w for w in catalog.workflows if _key(w.path) != _key(dest)] + [new]
    return {"workflow": res["workflow"], "fasta": fasta_name or res["fasta"], "notes": notes, "origin": str(src)}


def apply_proposals(cfg, cfg_dict: dict, proposals: list[Proposal], catalog: Catalog | None = None) -> list[str]:
    """Adopt each proposal's file and set it in cfg_dict['methods'] (in place). The caller writes cfg_dict with
    configio.write_config, which backs up the old config.yaml. Returns what was done, one line each."""
    lines = []
    for p in proposals:
        m = cfg_dict["methods"][p.method]
        if p.what == "workflow":
            res = adopt(p.candidate.info, p.method, cfg, catalog)
            m["workflow"] = res["workflow"]
            lines.append(f"{p.method}: workflow {res['workflow']} (from {p.candidate.info.path})")
            if res["fasta"] and not any(q.method == p.method and q.what == "fasta" for q in proposals) \
                    and not _pinned_file(Path(cfg.fasta_dir), str(m.get("fasta") or "")):
                m["fasta"] = res["fasta"]
                lines.append(f"{p.method}: FASTA {res['fasta']} (the workflow's own)")
        else:
            res = adopt_fasta(p.candidate.info, cfg, catalog)
            m["fasta"] = res["fasta"]
            lines.append(f"{p.method}: FASTA {res['fasta']} (from {p.candidate.info.path})")
    return lines


# ------------------------------------------------------------------- check --


def update_check(catalog: Catalog, cfg, installed_version: str = "") -> list[Flag]:
    """For each FragPipe method: is its pinned workflow or FASTA missing, is there a stock workflow of the same kind
    saved with a newer FragPipe in an installation, is its FASTA old, is there a newer FASTA of the same proteome.
    Changes nothing."""
    installed_version = installed_version or installed_version_of(cfg)
    ctx = Context(workflow_dir=Path(cfg.workflow_dir), fasta_dir=Path(cfg.fasta_dir),
                  installed_version=installed_version)
    out: list[Flag] = []
    for key, m in cfg.methods.items():
        engine = str(m.extra.get("engine") or "fragpipe").lower()
        if engine == "fragpipe" and m.workflow:
            wf = _pinned_file(Path(cfg.workflow_dir), m.workflow, WORKFLOW_SUFFIX)
            if wf is None:
                out.append(Flag("PINNED_MISSING", "error", f"{key}: its workflow {m.workflow} is not in "
                                f"{cfg.workflow_dir}", "Run `ionomos library recommend --apply`, or pick one in the "
                                "app's Methods tab.", key))
            else:
                info = describe_workflow(wf, ctx, evidence=False)
                saved = _ver(info.saved_with)
                order = sorted(catalog.workflows, key=lambda s: (
                    canonical_rank(s.name, info.kind, m.workflow) is None, canonical_rank(s.name, info.kind,
                                                                                          m.workflow) or 0, s.name))
                for s in order:
                    sv = _ver(s.saved_with)
                    if s.source == "stock" and s.kind == info.kind and sv and saved and sv > saved \
                            and (not _ver(installed_version) or sv[0] == _ver(installed_version)[0]):
                        out.append(Flag("UPDATE_STOCK_NEWER", "info", f"{key}: {info.name} was saved with FragPipe "
                                        f"{_ver_text(info.saved_with)}; the installation has {s.name}, saved with "
                                        f"{_ver_text(s.saved_with)}", "Compare them (`ionomos library show`); "
                                        "if the lab's changes still apply, make them in the new one and adopt it.",
                                        key))
                        break
        if m.fasta:
            fa = _pinned_file(Path(cfg.fasta_dir), m.fasta)
            if fa is None:
                out.append(Flag("PINNED_MISSING", "error", f"{key}: its FASTA {m.fasta} is not in {cfg.fasta_dir}",
                                "Run `ionomos library recommend --apply`, or pick one in the app's Methods tab.", key))
                continue
            info = describe_fasta(fa, ctx)
            out += [Flag(f.code, f.severity, f"{key}: {f.text}", f.fix, key) for f in info.flags
                    if f.code == "FASTA_OLD"]
            if info.proteome and info.name_date:
                newer = [f for f in catalog.fastas if f.proteome == info.proteome and f.name_date > info.name_date
                         and not any(x.severity == "error" for x in f.flags)]
                if newer:
                    best = max(newer, key=lambda f: f.name_date)
                    out.append(Flag("UPDATE_FASTA_NEWER", "info", f"{key}: a newer FASTA of {info.proteome} is on "
                                    f"this PC: {best.path} (made {best.name_date}; the method's is from "
                                    f"{info.name_date})", "Adopt it for new projects "
                                    "(`ionomos library adopt <path> --method ...`); results of old searches stay "
                                    "tied to the old one.", key))
    return out


def installed_version_of(cfg) -> str:
    return installed_version(cfg)


# --------------------------------------------------------------------- CLI --

CLI_HELP = """\
Find, understand and adopt FragPipe workflows and FASTAs on this PC (docs/LIBRARY.md).

  ionomos library scan [--root DIR ...] [--only] [--replace]
                                              look (read-only) and remember what was found
  ionomos library list [workflows|fastas] [--kind TMT] [--flags]
  ionomos library show PATH                   one workflow or FASTA in plain words, with its flags
  ionomos library recommend [--apply]         the best candidates per method; --apply copies them in and
                                              updates config.yaml (backed up first) for methods whose files
                                              are missing or broken
  ionomos library adopt PATH --method KEY [--no-config] [--force]
                                              copy a workflow (and its FASTA) into the pinned folders and use it
  ionomos library check                       newer stock workflows, old or superseded FASTAs
"""


def add_parser(sub):
    import argparse

    p = sub.add_parser("library", help="find, understand and adopt FragPipe workflows and FASTAs on this PC",
                       description=CLI_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    ls = p.add_subparsers(dest="library_cmd", required=True)
    sc = ls.add_parser("scan", help="look for workflows and FASTAs (reads only) and remember them")
    sc.add_argument("--root", action="append", metavar="DIR", help="also look in this folder (repeatable), besides "
                    "the pinned folders, FragPipe installations, users_root and library.roots")
    sc.add_argument("--only", action="store_true", help="look only in the --root folders")
    sc.add_argument("--replace", action="store_true", help="keep only this scan, even if it stopped early or looked "
                    "in fewer folders than the last one (default: what it did not look at is kept)")
    sc.add_argument("--depth", type=int, default=None, help="folders deep (default: library.max_depth, 6)")
    sc.add_argument("--time", type=float, default=None, help="seconds at most (default: library.time_limit_s, 120)")
    sc.add_argument("--json", action="store_true")
    li = ls.add_parser("list", help="what the last scan found")
    li.add_argument("what", nargs="?", choices=("workflows", "fastas"), default=None)
    li.add_argument("--kind", default=None, help=f"only workflows of this kind ({', '.join(KINDS)})")
    li.add_argument("--flags", action="store_true", help="also list each one's flags")
    li.add_argument("--json", action="store_true")
    sh = ls.add_parser("show", help="one workflow or FASTA in plain words")
    sh.add_argument("path")
    sh.add_argument("--json", action="store_true")
    rc = ls.add_parser("recommend", help="the best workflow and FASTA per method")
    rc.add_argument("--apply", action="store_true", help="adopt the proposals for methods whose files are missing "
                    "or broken, and save them in config.yaml (a backup is kept)")
    rc.add_argument("--json", action="store_true")
    ad = ls.add_parser("adopt", help="copy a workflow (and its FASTA) into the pinned folders for a method")
    ad.add_argument("path")
    ad.add_argument("--method", required=True)
    ad.add_argument("--with-fasta", action="store_true", help="also switch the method to the workflow's own FASTA "
                    "(default: only when the method's FASTA is missing)")
    ad.add_argument("--no-config", action="store_true", help="copy only, under a name no method uses; leave "
                    "config.yaml as it is")
    ad.add_argument("--force", action="store_true", help="adopt even a workflow of another kind than the method's, "
                    "or a file with an error mark (a FASTA without decoys ...)")
    ck = ls.add_parser("check", help="newer stock workflows, old or superseded FASTAs (changes nothing)")
    ck.add_argument("--json", action="store_true")
    return p


def _print_flags(flags: list[Flag], indent: str = "    ") -> None:
    for f in flags:
        mark = {"error": "✗", "warn": "!", "info": "·"}.get(f.severity, "·")
        print(f"{indent}{mark} [{f.code}] {f.text}")
        if f.fix:
            print(f"{indent}    → {f.fix}")


def _print_info(info, flags: bool = True, used: str = "") -> None:
    print(info.path)
    print(f"  {info.summary}")
    if used:
        print(f"  {used}")
    if isinstance(info, WorkflowInfo):
        print(f"  kind {info.kind}" + (f" ({', '.join(info.traits)})" if info.traits else "")
              + f", source {info.source}" + (f", saved with FragPipe {info.saved_with}" if info.saved_with else ""))
        for k, v in info.settings.items():
            print(f"    {k}: {v}")
        tools = [t for t, on in info.needs.items() if on]
        if tools:
            print(f"    tools: {', '.join(tools)}")
        if info.runs:
            print(f"    this search: {', '.join(info.runs.get('tables') or []) or 'no result table'}"
                  + (", ALL JOBS DONE" if info.runs.get("all_done") else "")
                  + (f", {info.runs['when']}" if info.runs.get("when") else ""))
    else:
        print(f"  {info.entries:,} entries: {info.targets:,} targets ({info.contaminants} contaminants), "
              f"{info.decoys:,} decoys" + (f" ({info.decoy_tag})" if info.decoy_tag else "")
              + f"; {info.size / 1e6:.1f} MB; source {info.source}")
    if flags and info.flags:
        _print_flags(info.flags)


def run_cli(args) -> int:
    from ionomos.config import ConfigError, load

    try:
        cfg = load(args.config, check_paths=False)
    except (ConfigError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    cmd = args.library_cmd
    try:
        if cmd == "scan":
            return _cli_scan(args, cfg)
        if cmd == "show":
            return _cli_show(args, cfg)
        if cmd == "adopt":
            return _cli_adopt(args, cfg)
        cat = load_catalog(cfg)
        if cmd == "list":
            return _cli_list(args, cat)
        if cmd == "recommend":
            return _cli_recommend(args, cfg, cat)
        if cmd == "check":
            flags = update_check(cat, cfg)
            scanned = bool(cat.scanned_at or cat.workflows or cat.fastas)
            if args.json:
                print(json.dumps([f.as_dict() for f in flags], indent=1))
            elif flags:
                _print_flags(flags, "")
            elif scanned:
                print("every method's workflow and FASTA is current as far as this PC can tell")
            if not scanned:
                print("nothing has been scanned yet, so newer workflows and FASTAs elsewhere on this PC are not "
                      "known: run `ionomos library scan` (or Find on this PC… in the app's Methods tab), then "
                      "check again", file=sys.stderr if args.json else sys.stdout)
            return 1 if any(f.severity == "error" for f in flags) else 0
    except (OSError, ValueError) as exc:
        print(f"library {cmd}: {exc}", file=sys.stderr)
        return 1
    return 2


def _cli_scan(args, cfg) -> int:
    if args.only and not args.root:
        print("--only needs at least one --root DIR", file=sys.stderr)
        return 2
    prev = load_catalog(cfg)
    ctx = context_from(cfg)
    roots, missing = None, []
    if args.root:   # --root adds to the default roots; --only looks in the --root folders alone (#153)
        extra = [Path(os.path.expanduser(r)) for r in args.root]
        roots = extra if args.only else default_roots(cfg, missing) + extra
    cat = scan(roots, cfg=cfg, ctx=ctx, max_depth=args.depth, time_limit_s=args.time, previous=prev,
               progress=None if args.json else (lambda t: print(f"  {t}", file=sys.stderr)))
    cat.notes[:0] = missing
    cat, saved = save_scan(cfg, cat, prev, replace=args.replace, ctx=ctx)
    if args.json:
        print(json.dumps(cat.as_dict(), indent=1, ensure_ascii=False))
        return 0
    print(f"{cat.how_far()}; {saved}")
    for kind in KINDS:
        ws = [w for w in cat.workflows if w.kind == kind]
        if ws:
            print(f"  {kind}: {len(ws)} workflow(s), {len(_groups(ws))} different setting(s)")
    errs = sum(1 for x in (*cat.workflows, *cat.fastas) for f in x.flags if f.severity == "error")
    if errs:
        print(f"  {errs} problem(s) marked ✗: `ionomos library list --flags`")
    for n in cat.notes:
        print(f"  note: {n}")
    print("Next: `ionomos library recommend`")
    return 0


def _cli_list(args, cat: Catalog) -> int:
    if not cat.workflows and not cat.fastas:
        print("; ".join(cat.notes) or "the last scan found nothing")
        return 1
    ws = [w for w in cat.workflows if args.kind is None or w.kind.lower() == args.kind.lower()]
    fs = [] if args.kind else cat.fastas
    if args.what == "workflows":
        fs = []
    elif args.what == "fastas":
        ws = []
    if args.json:
        print(json.dumps([x.as_dict() for x in (*ws, *fs)], indent=1, ensure_ascii=False))
        return 0
    print(f"scanned {cat.scanned_at}: {cat.how_far()}")
    for n in cat.notes:
        print(f"  note: {n}")
    for w in sorted(ws, key=lambda w: (KINDS.index(w.kind) if w.kind in KINDS else 99, w.path.lower())):
        marks = "".join("✗" if f.severity == "error" else "!" if f.severity == "warn" else "" for f in w.flags)[:3]
        print(f"[{w.kind}] {w.path}  ({w.source}) {marks}")
        print(f"    {w.summary}")
        used = used_in(cat, w) if w.source != "run" else ""
        if used:
            print(f"    {used}")
        if args.flags:
            _print_flags(w.flags, "      ")
    for f in fs:
        marks = "".join("✗" if x.severity == "error" else "!" if x.severity == "warn" else "" for x in f.flags)[:3]
        print(f"[FASTA] {f.path}  ({f.source}) {marks}")
        print(f"    {f.summary}")
        if args.flags:
            _print_flags(f.flags, "      ")
    return 0


def _cli_show(args, cfg) -> int:
    p = Path(args.path)
    if not p.is_file():
        print(f"{p} is not a file on this PC", file=sys.stderr)
        return 1
    ctx = context_from(cfg)
    if p.name.lower().endswith(WORKFLOW_SUFFIX):
        info = describe_workflow(p, ctx)
        cat = load_catalog(cfg)
        cat.workflows = [w for w in cat.workflows if _key(w.path) != _key(p)] + [info]
        judge_catalog(cat, ctx)
        info = cat.find(p)
        used = used_in(cat, info)
    else:
        info, used = describe_fasta(p, ctx), ""
    if args.json:
        print(json.dumps(info.as_dict(), indent=1, ensure_ascii=False))
    else:
        _print_info(info, used=used)
    return 0


def _write_cfg(cfg, d: dict) -> None:
    """Check the edited config with the real loader before writing it (configio keeps a backup)."""
    from ionomos import configio
    from ionomos.config import load

    with tempfile.TemporaryDirectory(prefix="ionomos-cfg-") as tmp:
        cand = Path(tmp) / "config.yaml"
        cand.write_text(configio.dump_config(d), encoding="utf-8")
        load(cand, check_paths=False)
    configio.write_config(cfg.config_path, d)


def _cli_recommend(args, cfg, cat: Catalog) -> int:
    from ionomos import configio

    if not cat.workflows and not cat.fastas:
        print("no scan yet: run `ionomos library scan` first", file=sys.stderr)
        return 1
    d = configio.read_config(cfg.config_path)
    inst = installed_version(cfg)
    ctx = context_from(cfg, tools=False)
    judge_catalog(cat, Context(workflow_dir=ctx.workflow_dir, fasta_dir=ctx.fasta_dir, installed_version=inst,
                               pinned=ctx.pinned, install_roots=ctx.install_roots))
    taxon = _lab_taxon(cat, Path(cfg.fasta_dir))
    report = {}
    for key in cfg.methods:
        kind = cfg.kind(key)
        pin = cat.find(ctx.pinned[kind]) if kind in ctx.pinned else None
        report[key] = {"kind": kind, "workflows": recommend(cat, kind, inst, pin, cfg.methods[key].workflow)[:3],
                       "fastas": recommend_fastas(cat, taxon, kind)[:3]}
    notes: list[str] = []
    proposals = auto_assign(d, cat, inst, notes)
    if args.json:
        print(json.dumps({"methods": {k: {"kind": v["kind"], "workflows": [c.as_dict() for c in v["workflows"]],
                                          "fastas": [c.as_dict() for c in v["fastas"]]} for k, v in report.items()},
                          "proposals": [p.as_dict() for p in proposals], "not_proposed": notes},
                         indent=1, ensure_ascii=False))
    else:
        for key, v in report.items():
            print(f"{key} ({v['kind']}):")
            for c in v["workflows"]:
                print(f"  workflow {c.info.path}  score {c.score:.0f}" + (f", {c.copies} copies" if c.copies > 1 else ""))
                print(f"      {'; '.join(c.reasons)}")
            for c in v["fastas"][:1]:
                print(f"  FASTA    {c.info.path}  score {c.score:.0f}")
                print(f"      {'; '.join(c.reasons)}")
        if proposals:
            print("Proposed (methods whose files are missing or broken):")
            for p in proposals:
                print(f"  {p.text()}")
        elif not notes:
            print("Every method's workflow and FASTA is there and usable: nothing proposed.")
        if notes:
            print("Not proposed:")
            for n in notes:
                print(f"  {n}")
    if args.apply and proposals:
        lines = apply_proposals(cfg, d, proposals, cat)
        _write_cfg(cfg, d)
        save_catalog(cfg, cat)
        for ln in lines:
            print(f"applied: {ln}")
        print(f"config.yaml saved (the previous one is in {Path(cfg.config_path).parent / 'config-backups'}); "
              f"restart the watcher to use it")
    return 0


def _cli_adopt(args, cfg) -> int:
    from ionomos import configio

    if args.method not in cfg.methods:
        print(f"no method {args.method!r} in config.yaml (methods: {', '.join(cfg.methods)})", file=sys.stderr)
        return 2
    p = Path(args.path)
    if not p.is_file():
        print(f"{p} is not a file on this PC", file=sys.stderr)
        return 1
    is_wf = p.name.lower().endswith(WORKFLOW_SUFFIX)
    ctx = context_from(cfg)
    info = describe_workflow(p, ctx, evidence=False) if is_wf else describe_fasta(p, ctx)
    problems = adopt_problems(info, cfg.kind(args.method), ctx)
    if problems and not args.force:   # #150: never a DIA workflow for TMT or a FASTA FragPipe refuses, silently
        print(f"not adopted for {args.method}: nothing copied, config.yaml not changed", file=sys.stderr)
        for f in problems:
            print(f"  ✗ [{f.code}] {f.text}", file=sys.stderr)
            print(f"      → {f.fix}", file=sys.stderr)
        print("  (--force adopts it anyway)", file=sys.stderr)
        return 1
    for f in problems:
        print(f"  adopted although: [{f.code}] {f.text}")
    current = getattr(cfg.methods[args.method], "workflow" if is_wf else "fasta", "") or ""
    if not is_wf:
        res = adopt_fasta(info, cfg)
        field_, value = "fasta", res["fasta"]
    else:
        res = adopt(info, args.method, cfg, avoid=live_workflows(cfg, None if args.no_config else args.method))
        field_, value = "workflow", res["workflow"]
    for n in res["notes"]:
        print(f"  {n}")
    if args.no_config:
        if value == current:
            print(f"copied; config.yaml not changed: {args.method} uses {value}, which already held exactly this file")
        else:
            print(f"copied as {value}; config.yaml not changed: {args.method} still uses {current or '(nothing)'}. To "
                  f"use the copy, run this again without --no-config, or set methods: {args.method}: {field_}: "
                  f"{value} in config.yaml (the app's Methods tab)")
        return 0
    d = configio.read_config(cfg.config_path)
    m = d["methods"][args.method]
    m[field_] = value
    if field_ == "workflow" and res.get("fasta"):
        current = _pinned_file(Path(cfg.fasta_dir), str(m.get("fasta") or ""))
        if args.with_fasta or current is None:
            m["fasta"] = res["fasta"]
            print(f"  {args.method} now uses FASTA {res['fasta']}")
        elif res["fasta"] != m.get("fasta"):
            print(f"  {args.method} keeps FASTA {m.get('fasta')} (the workflow was saved with {res['fasta']}; "
                  f"--with-fasta switches)")
    _write_cfg(cfg, d)
    print(f"{args.method} now uses {field_} {value}; config.yaml saved (backup in config-backups/); restart the "
          f"watcher to use it")
    return 0
