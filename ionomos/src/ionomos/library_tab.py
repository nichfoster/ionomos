"""
The app side of the workflow / FASTA library (library.py, D89) and of FASTA sources (fastasource.py, D90): a panel
under the Methods tab's form, its dialogs and the first-setup proposal banner.

    panel = LibraryPanel(app, methods_frame)      # built by App._tab_methods

  Panel "Workflows and FASTAs" (Methods tab, under the form)
    Find on this PC…               LibraryDialog: library.scan off the Tk thread (progress line, Stop), saved with
                                   save_catalog, so the next open is instant (Rescan looks again); a table of
                                   candidates (recommend / recommend_fastas for the method's kind, "Show all" for
                                   every file), the details of the selected one (summary, settings, every flag and
                                   its fix), Use for <method> (library.adopt / adopt_fasta, then the form's boxes)
                                   and Open folder
    Choose… (beside the boxes)     the same dialog, showing only workflows or only FASTAs
    the FASTA line                 describe_fasta of the method's FASTA (off the Tk thread: FASTAs can be 1 GB)
    Download from UniProt…         DownloadDialog: search_proteomes -> pick -> download_proteome +
                                   prepare_for_fragpipe into fasta_dir, progress and Stop; then Use for <method>
    Add decoys and contaminants…   a FASTA you pick -> prepare_for_fragpipe into fasta_dir (the source is untouched)
    Check for updates              update_check + uniprot_release / staleness in a text window; changes nothing
  Banner (first setup)             when a method's workflow or FASTA is missing or has an error: "Ionomos found …
                                   Review proposals…" -> ProposalDialog: auto_assign's proposals with their reasons;
                                   only Apply changes anything (apply_proposals, then the app's own Save, which
                                   checks the config and backs the old one up)

The logic without Tk is the top half of this file (rows, filters, texts, the banner rule, the download dialog's
states) and is tested everywhere (tests/test_library_tab.py); the Tk classes only copy values in and out. Every
long operation runs on a daemon thread and comes back through App.post (D67 item 7); a worker never calls Tk.
Files are only ever copied (library.adopt*, fastasource never overwrites); nothing here moves or deletes one.
"""
from __future__ import annotations

import copy
import logging
import tempfile
import threading
import time
import tkinter as tk
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ionomos import fastasource, library, tkutil

log = logging.getLogger("ionomos.app")

PAD = {"padx": 6, "pady": 3}
MARKS = {"error": "✗", "warn": "!", "info": "·"}
COLORS = {"error": "#c62828", "warn": "#b26a00", "info": "#444", "ok": "#2e7d32"}
FRESH_HOURS = 24          # a catalog younger than this is reused by the first-setup proposal
SHOW_CHOICES = {"both": "workflows and FASTAs", "workflow": "workflows", "fasta": "FASTAs"}
FASTA_SUFFIXES = (".fas", ".fasta", ".fa")


# ======================================================================= logic (no Tk) ===


def config_from_data(d: dict):
    """A Config from the app's (possibly unsaved) settings dict, through the real loader (paths not checked)."""
    from ionomos import configio
    from ionomos.config import load

    with tempfile.TemporaryDirectory(prefix="ionomos-lib-") as tmp:
        p = Path(tmp) / "config.yaml"
        p.write_text(configio.dump_config(d), encoding="utf-8")
        return load(p, check_paths=False)


def resolve_file(folder: str | Path | None, name: str, suffix: str = "") -> Path | None:
    """The method's file: `name` inside `folder` (or `name` itself when absolute), also without its suffix typed."""
    name = (name or "").strip()
    if not name:
        return None
    p = Path(name)
    cands = [p] if p.is_absolute() else ([Path(folder) / name] if folder else [])
    if suffix and cands and not name.lower().endswith(suffix):
        cands.append(cands[0].with_name(cands[0].name + suffix))
    return next((c for c in cands if c.is_file()), None)


def flag_marks(flags: list) -> str:
    """'✗1 !2 ·1' — how many flags of each severity (empty when there are none)."""
    n = Counter(f.severity for f in flags)
    return " ".join(f"{MARKS[s]}{n[s]}" for s in library.SEVERITIES if n.get(s))


def worst(flags: list) -> str:
    """The worst severity among flags: error | warn | info | ok."""
    sev = {f.severity for f in flags}
    return next((s for s in library.SEVERITIES if s in sev), "ok")


def flag_lines(flags: list, indent: str = "  ") -> list[str]:
    out = []
    for f in flags:
        out.append(f"{indent}{MARKS.get(f.severity, '·')} {f.text}  [{f.code}]")
        if f.fix:
            out.append(f"{indent}    → {f.fix}")
    return out


@dataclass
class Row:
    """One line of the Find on this PC table."""
    type: str                 # workflow | fasta
    kind: str                 # what the table's kind column says: 'workflow · TMT', 'FASTA · Human'
    name: str
    where: str                # its folder
    summary: str
    flags: str                # flag_marks
    reasons: str              # why it is recommended, in words ('' when it is not a candidate)
    path: str
    info: object = None       # library.WorkflowInfo | library.FastaInfo
    score: float | None = None
    copies: int = 1
    all_reasons: list[str] = field(default_factory=list)

    @property
    def recommended(self) -> bool:
        return self.score is not None

    def values(self) -> tuple[str, ...]:
        return (self.kind, self.name, self.where, self.summary, self.flags, self.reasons)


def _row(info, cand=None) -> Row:
    is_wf = isinstance(info, library.WorkflowInfo)
    if is_wf:
        kind = f"workflow · {info.kind}" + (f" ({', '.join(info.traits)})" if info.traits else "")
    else:
        kind = "FASTA" + (f" · {info.top_species}" if info.top_species else "")
    reasons = list(cand.reasons) if cand else []
    return Row(type="workflow" if is_wf else "fasta", kind=kind, name=info.name, where=str(Path(info.path).parent),
               summary=info.summary or info.error, flags=flag_marks(info.flags), reasons="; ".join(reasons),
               path=info.path, info=info, score=cand.score if cand else None,
               copies=cand.copies if cand else 1, all_reasons=reasons)


def _ranked(by_path: dict, extra: Callable) -> Callable:
    """Sort key for Show all: the recommended first (best score first), then `extra`, then by name."""
    def key(x):
        c = by_path.get(library._key(x.path))
        return (c is None, -(c.score if c else 0.0), *extra(x), x.name.lower(), x.path)
    return key


def candidate_rows(catalog, kind: str, *, what: str = "both", show_all: bool = False, installed_version: str = "",
                   pinned=None, taxon: str | None = None, prefer: str = "", taxon_from: str = "") -> list[Row]:
    """The table of the Find on this PC dialog. By default the candidates library.recommend ranks for the method's
    kind (one per set of settings, no file with an error) and every usable FASTA (recommend_fastas), best first.
    `show_all`: every workflow and FASTA of the catalog, recommended ones first, then the rest (other kinds, files
    with problems, copies) by name. `what`: 'both' | 'workflow' | 'fasta'."""
    rows: list[Row] = []
    if what in ("both", "workflow"):
        cands = library.recommend(catalog, kind, installed_version, pinned, prefer)
        if show_all:
            by_path = {library._key(c.info.path): c for c in cands}
            ws = sorted(catalog.workflows, key=_ranked(by_path, lambda w: (w.kind.lower() != kind.lower(),)))
            rows += [_row(w, by_path.get(library._key(w.path))) for w in ws]
        else:
            rows += [_row(c.info, c) for c in cands]
    if what in ("both", "fasta"):
        cands = library.recommend_fastas(catalog, taxon, kind, taxon_from)
        if show_all:
            by_path = {library._key(c.info.path): c for c in cands}
            fs = sorted(catalog.fastas, key=_ranked(by_path, lambda f: ()))
            rows += [_row(f, by_path.get(library._key(f.path))) for f in fs]
        else:
            rows += [_row(c.info, c) for c in cands]
    return rows


def details_text(row: Row) -> str:
    """Everything about the selected row, for the details pane: summary, what it is, why it is (not) recommended,
    every flag with what to do."""
    info = row.info
    out = [row.path, "", info.summary or info.error or "(no summary)", ""]
    if isinstance(info, library.WorkflowInfo):
        out.append(f"Kind: {info.kind}" + (f" ({', '.join(info.traits)})" if info.traits else "")
                   + f"; found as: {info.source}" + (f"; saved with FragPipe {info.saved_with}" if info.saved_with else ""))
        if info.database:
            out.append(f"Its FASTA: {info.database}" + ("" if info.database_exists else " (not on this PC)"))
        tools = [t for t, on in (info.needs or {}).items() if on]
        if tools:
            out.append(f"Needs: {', '.join(tools)}")
        if info.runs:
            tables = ", ".join(info.runs.get("tables") or []) or "no result table"
            out.append(f"Its search: {tables}" + (", finished" if info.runs.get("finished") else "")
                       + (f", {info.runs['when']}" if info.runs.get("when") else ""))
        if info.settings:
            out.append("Settings:")
            out += [f"  {k}: {v}" for k, v in info.settings.items()]
    else:
        out.append(f"{info.entries:,} entries: {info.targets:,} targets ({info.contaminants:,} contaminants), "
                   f"{info.decoys:,} decoys" + (f" ({info.decoy_tag})" if info.decoy_tag else "")
                   + f"; {info.size / 1e6:.1f} MB; found as: {info.source}")
        if not info.complete:
            out.append("Not read to the end (time ran out or it was stopped): the counts are partial.")
    out.append("")
    if row.recommended:
        out.append(f"Recommended (score {row.score:.0f}" + (f", {row.copies} files with the same content"
                                                             if row.copies > 1 else "") + "):")
        out += [f"  {r}" for r in row.all_reasons]
    else:
        out.append("Not among the recommendations for this method (another kind, a problem, or a copy of one "
                   "listed above).")
    out.append("")
    if info.flags:
        out.append("Marks (✗ must be fixed, ! check it, · for information):")
        out += flag_lines(info.flags)
    else:
        out.append("No marks: nothing wrong found.")
    return "\n".join(out)


def filter_rows(rows: list[Row], text: str) -> list[Row]:
    """Rows whose name, folder, kind or summary contain every word of `text` (case does not matter)."""
    words = text.lower().split()
    if not words:
        return rows
    return [r for r in rows if all(w in f"{r.kind} {r.name} {r.where} {r.summary}".lower() for w in words)]


def method_taxon_from(catalog, fasta_path: Path | None, fasta_dir: Path | None) -> tuple[str | None, str]:
    """(the organism to rank FASTAs for, where it came from in words): the method's current FASTA's, else the
    commonest in the FASTA folder; (None, '') when neither says."""
    if fasta_path is not None:
        cur = catalog.find(fasta_path)
        if cur is not None and cur.taxon:
            return cur.taxon, "the method's current FASTA"
    n = Counter(f.taxon for f in catalog.fastas if f.taxon and library._inside(f.path, fasta_dir))
    return (n.most_common(1)[0][0], "the FASTA folder's files") if n else (None, "")


def method_taxon(catalog, fasta_path: Path | None, fasta_dir: Path | None) -> str | None:
    """The organism to rank FASTAs for (method_taxon_from without the words)."""
    return method_taxon_from(catalog, fasta_path, fasta_dir)[0]


def catalog_age_hours(catalog, now: datetime | None = None) -> float | None:
    """Hours since the catalog's scan; None when there was none (or the time can't be read)."""
    if not catalog.scanned_at:
        return None
    try:
        when = datetime.fromisoformat(catalog.scanned_at)
    except ValueError:
        return None
    now = now or datetime.now()
    return max(0.0, (now - when) / timedelta(hours=1))


def catalog_fresh(catalog, now: datetime | None = None, hours: float = FRESH_HOURS) -> bool:
    age = catalog_age_hours(catalog, now)
    return age is not None and age < hours


def catalog_line(catalog) -> str:
    """One line on the last scan for the dialog's status."""
    if not catalog.scanned_at:
        return "Not looked for yet on this PC: press Rescan (it only reads; nothing is moved)."
    when = catalog.scanned_at.replace("T", " ")[:16]
    notes = [n for n in catalog.notes if not n.startswith("no scan yet")]
    return f"Last looked {when}: {catalog.how_far()}." + "".join(f" Note: {n}." for n in notes[:2]) \
        + (f" (and {len(notes) - 2} more notes)" if len(notes) > 2 else "")


def method_problems(cfg_dict: dict, catalog, describe_workflow: Callable | None = None) -> list[str]:
    """What is wrong with each FragPipe method's files, one line each: a workflow or FASTA that is not set, not in
    its folder, or has an error flag (as the catalog judged it; a pinned workflow the catalog doesn't know is read
    now, it is small; a FASTA it doesn't know is not read here). The banner shows when this is not empty."""
    describe_workflow = describe_workflow or (lambda p: library.describe_workflow(p, evidence=False))
    paths = cfg_dict.get("paths") or {}
    wf_dir, fa_dir = paths.get("workflow_dir") or "", paths.get("fasta_dir") or ""
    out: list[str] = []
    for key, m in (cfg_dict.get("methods") or {}).items():
        if not isinstance(m, dict):
            continue
        if str(m.get("engine") or "fragpipe").lower() == "fragpipe":
            name = str(m.get("workflow") or "")
            p = resolve_file(wf_dir, name, library.WORKFLOW_SUFFIX)
            if not name:
                out.append(f"{key}: no workflow set")
            elif p is None:
                out.append(f"{key}: workflow {name} is not in the workflows folder")
            else:
                info = catalog.find(p)
                if info is None:
                    try:
                        info = describe_workflow(p)
                    except Exception:  # noqa: BLE001 - a banner hint, never a crash
                        info = None
                errs = [f for f in (info.flags if info else []) if f.severity == "error"]
                if errs:
                    out.append(f"{key}: workflow {name}: {errs[0].text}")
        name = str(m.get("fasta") or "")
        p = resolve_file(fa_dir, name)
        if not name:
            out.append(f"{key}: no FASTA set")
        elif p is None:
            out.append(f"{key}: FASTA {name} is not in the FASTA folder")
        else:
            info = catalog.find(p)
            errs = [f for f in (info.flags if info else []) if f.severity == "error"]
            if errs:
                out.append(f"{key}: FASTA {name}: {errs[0].text}")
    return out


def banner_text(problems: list[str], catalog) -> str:
    """The first-setup banner's sentence ('' = no banner)."""
    if not problems:
        return ""
    more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
    if catalog.workflows or catalog.fastas:
        return (f"Ionomos found {len(catalog.workflows)} workflows and {len(catalog.fastas)} FASTAs on this PC. "
                f"{problems[0]}{more}. Review proposals… shows what it would use; nothing changes until you Apply.")
    return (f"{problems[0]}{more}. Ionomos can look for workflows and FASTAs on this PC and propose one: "
            f"Review proposals… (it only reads; nothing changes until you Apply).")


def proposal_values(p) -> tuple[str, str, str, str, str]:
    """A proposal as the table shows it: method, file, now, proposed, why."""
    return (p.method, "workflow" if p.what == "workflow" else "FASTA", p.current or "(none)",
            p.candidate.info.name, p.why)


def proposal_text(p) -> str:
    """The details of one proposal: what is wrong now, what would be used and why, its marks."""
    c = p.candidate
    out = [f"{p.method}: its {'workflow' if p.what == 'workflow' else 'FASTA'} "
           f"{p.current or '(none set)'} — {p.why}.", "",
           f"Proposed: {c.info.path}", f"  {c.info.summary}", "", "Why this one:"]
    out += [f"  {r}" for r in c.reasons]
    if c.flags:
        out += ["", "Its marks:"] + flag_lines(c.flags)
    out += ["", "Apply copies it into the " + ("workflows" if p.what == "workflow" else "FASTA")
            + " folder (a file it replaces is kept as .bak-<time>) and sets it for the method; config.yaml is "
              "backed up first. The original stays where it is."]
    return "\n".join(out)


def proposals_summary(proposals: list, notes: list[str] = ()) -> str:
    """The Review proposals window's status: how many, and what was not proposed and why (auto_assign's notes:
    candidates that tie, none found)."""
    why_not = "".join(f" Not proposed: {n}." for n in notes)
    if not proposals:
        return ("Nothing to propose" + (":" + why_not if notes else
                ": every method's workflow and FASTA is usable, or nothing better was found on this PC (Find on "
                "this PC… lists everything)."))
    return (f"{len(proposals)} proposal(s). Nothing is changed until you press Apply; config.yaml is backed up "
            f"first.{why_not}")


def run_scan(cfg, *, progress: Callable[[str], None] | None = None,
             stop: threading.Event | None = None) -> tuple[object, str]:
    """library.scan with the previous catalog's FASTA readings, then library.save_scan: a scan stopped early (or
    one that looked in fewer folders) keeps what the last one found that it did not see. Returns (catalog, a line
    on what was saved)."""
    prev = library.load_catalog(cfg)
    ctx = library.context_from(cfg)
    cat = library.scan(cfg=cfg, ctx=ctx, progress=progress, stop=stop, previous=prev)
    return library.save_scan(cfg, cat, prev, ctx=ctx)


def use_problems(row: Row, kind: str) -> str:
    """Why Use for <method> should ask first (library.adopt_problems: a workflow of another kind than the method's,
    a file with an error mark such as a FASTA without decoys), as the question's text; '' = nothing to ask (#150)."""
    flags = library.adopt_problems(row.info, kind) if row.info is not None else []
    if not flags:
        return ""
    lines = [f"{row.name} has a problem for this method:", ""] + flag_lines(flags, "") + [
        "", "Use it anyway? (No leaves everything as it is.)"]
    return "\n".join(lines)


def adopt_row(row: Row, method: str, cfg, catalog=None, avoid: set[str] | None = None) -> dict:
    """Copy the row's file into the pinned folders for `method` (library.adopt / adopt_fasta). `avoid`: the workflow
    files the saved config's methods search with (library.live_workflows), never replaced before Save. Returns
    {'workflow': name or None, 'fasta': name or '', 'notes': [...]}; the caller sets them in the form."""
    if row.type == "workflow":
        res = library.adopt(row.info, method, cfg, catalog, avoid=avoid)
        return {"workflow": res["workflow"], "fasta": res.get("fasta") or "", "notes": list(res["notes"])}
    res = library.adopt_fasta(row.info, cfg, catalog)
    return {"workflow": None, "fasta": res["fasta"], "notes": list(res["notes"])}


def fasta_panel_text(name: str, info) -> tuple[str, str]:
    """(text, severity) for the FASTA line under the method form."""
    if not name:
        return "No FASTA chosen for this method.", "info"
    if info is None:
        return f"✗ {name} is not in the FASTA folder. Choose… picks one, Download from UniProt… makes one.", "error"
    lines = [f"{info.name}: {info.summary or info.error}"]
    lines += flag_lines([f for f in info.flags if f.severity != "info"], "")
    return "\n".join(lines), worst(info.flags) if info.flags else "ok"


def fasta_candidates(folder: Path | None) -> list[Path]:
    """FASTAs in a folder (the contaminant source fastasource can copy contam_ entries from)."""
    if folder is None:
        return []
    try:
        return sorted(p for p in Path(folder).iterdir() if p.is_file() and p.name.lower().endswith(FASTA_SUFFIXES))
    except OSError:
        return []


def philosopher_for(cfg) -> Path | None:
    from ionomos import fragpipe

    root = fragpipe.fragpipe_root(Path(cfg.fragpipe_exe))
    return fastasource.find_philosopher([root] if root else []) or fastasource.find_philosopher()


def progress_text(message: str, done: int = 0, total: int | None = None) -> str:
    """fastasource's progress(message, done, total) as one line."""
    if total:
        return f"{message} ({min(100, int(100 * done / total))} %)"
    return message


def update_report(flags: list, release: dict | None, notes: list[str], fastas: dict[str, str],
                  scanned: str = "") -> str:
    """The Check for updates window's text: what library.update_check found, how old each method's FASTA is against
    UniProt's current release. `fastas`: method -> FASTA file name."""
    out = ["Workflows and FASTAs: what could be newer (nothing has been changed)", ""]
    if release:
        out.append(f"UniProt's current release: {release.get('release', '?')} ({release.get('date', '?')})")
    else:
        out.append("UniProt's current release: unknown" + (f" ({notes[0]})" if notes else ""))
    for method, name in fastas.items():
        if name:
            out.append(f"  {method}: {name}: {fastasource.staleness(name, release)}")
    out.append("")
    if flags:
        out.append("Found:")
        out += flag_lines(flags)
    elif scanned:
        out.append("Every method's workflow and FASTA is current as far as this PC can tell.")
    if not scanned:
        out += [""] * bool(flags) + ["Newer stock workflows and newer FASTAs elsewhere on this PC are found from the last scan; "
                    "there is none yet: Find on this PC… first."]
    out += ["", "A newer FASTA or workflow changes results: switch at the start of a project, never in the middle "
                "of one, and keep the old files for experiments already searched with them."]
    return "\n".join(out)


def check_updates(cfg) -> str:
    """Worker body of Check for updates (no Tk; one UniProt request with a timeout)."""
    cat = library.load_catalog(cfg)
    flags = library.update_check(cat, cfg, library.installed_version(cfg))
    notes: list[str] = []
    rel = fastasource.uniprot_release(notes=notes)
    fastas = {k: m.fasta for k, m in cfg.methods.items()}
    return update_report(flags, rel, notes, fastas, cat.scanned_at)


class DownloadFlow:
    """The Download from UniProt dialog's states, without Tk.

    idle -> searching -> results (rows) -> pick a row -> downloading -> done | failed | stopped;
    a new search or a new download can start from results / done / failed / stopped."""

    BUSY = ("searching", "downloading")

    def __init__(self, dest: Path | None):
        self.dest = Path(dest) if dest else None
        self.state = "idle"
        self.rows: list[dict] = []
        self.picked: int | None = None
        self.message = "Type an organism (human, mouse, yeast …), a taxon number (9606) or a proteome id."
        self.final: Path | None = None
        self.notes: list[str] = []

    @property
    def busy(self) -> bool:
        return self.state in self.BUSY

    def dest_problem(self) -> str:
        if self.dest is None or not str(self.dest).strip():
            return "No FASTA folder is set (tab 1 Folders)."
        if " " in str(self.dest):
            return (f"The FASTA folder {self.dest} has a space in its path, which FragPipe can't use: pick a folder "
                    f"without spaces on tab 1 first.")
        return ""

    def can_search(self, text: str) -> bool:
        return not self.busy and bool(text.strip())

    def start_search(self, text: str) -> None:
        if not self.can_search(text):
            raise RuntimeError("busy" if self.busy else "nothing typed")
        self.state, self.rows, self.picked = "searching", [], None
        self.message = f"Asking UniProt for '{text.strip()}'…"

    def search_done(self, res) -> None:
        self.rows = list(res.rows)
        if res.error or not self.rows:
            self.state = "failed"
            self.message = f"Search failed: {res.error}" if res.error else (
                f"UniProt has no reference proteome for '{res.query}'. Try the Latin name or the taxon number.")
            return
        self.state = "results"
        self.message = f"{len(self.rows)} proteome(s); pick one, then Download and prepare." + (
            " " + " ".join(res.notes) if res.notes else "")

    def pick(self, index: int | None) -> None:
        self.picked = index if index is not None and 0 <= index < len(self.rows) else None

    def can_download(self) -> bool:
        return not self.busy and self.picked is not None and not self.dest_problem()

    def start_download(self) -> dict:
        if not self.can_download():
            raise RuntimeError(self.dest_problem() or ("busy" if self.busy else "no proteome picked"))
        row = self.rows[self.picked]
        self.state, self.final, self.notes = "downloading", None, []
        self.message = f"Downloading {row['id']} ({row.get('organism', '')}) from UniProt…"
        return row

    def progress(self, message: str, done: int = 0, total: int | None = None) -> None:
        if self.state == "downloading":
            self.message = progress_text(message, done, total)

    def finished(self, got, prep) -> None:
        """After run_download: done (with the prepared file), failed or stopped."""
        self.notes = list(got.notes) + (list(prep.notes) if prep is not None else [])
        err = got.error or (prep.error if prep is not None else "")
        if err == "stopped":
            self.state, self.message = "stopped", "Stopped; nothing was kept from this download."
            return
        if err or prep is None or prep.path is None:
            self.state = "failed"
            kept = f" The downloaded file without decoys is {got.path}." if got.ok else ""
            self.message = f"Not done: {err or 'nothing was made'}.{kept}"
            return
        self.state, self.final = "done", Path(prep.path)
        self.message = f"Saved {prep.path}\n{prep.summary()}"


def run_download(proteome: str, dest: Path, *, reviewed: bool = True, isoforms: bool = False,
                 philosopher: Path | None = None, candidates: list[Path] | None = None,
                 progress=None, stop: threading.Event | None = None):
    """Worker body of the Download dialog: download_proteome, then prepare_for_fragpipe into the same folder, as
    `ionomos fasta download` does. Returns (DownloadResult, PrepareResult | None)."""
    got = fastasource.download_proteome(proteome, dest, reviewed=reviewed, isoforms=isoforms, progress=progress,
                                        stop=stop)
    if not got.ok:
        return got, None
    tags = (["reviewed"] if got.reviewed else []) + (["isoforms"] if got.isoforms else [])
    prep = fastasource.prepare_for_fragpipe(got.path, dest, contaminant_candidates=candidates or [],
                                            philosopher=philosopher, tags=tags, progress=progress, stop=stop)
    return got, prep


# ============================================================================ Tk ===


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


class _Window:
    """A Toplevel that knows whether it is still open; workers post into it through the app."""

    def __init__(self, panel, title: str, geometry: str = ""):
        self.panel, self.app = panel, panel.app
        self.win = tk.Toplevel(self.app.root)
        self.win.title(title)
        self.win.transient(self.app.root)
        if geometry:
            self.win.geometry(geometry)
        self.alive = True
        self.stop_ev = threading.Event()
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    def post(self, fn: Callable[[], None]) -> None:
        """From any thread: run fn on the Tk thread if the window is still open."""
        self.app.post(lambda: fn() if self.alive else None)

    def close(self) -> None:
        self.stop_ev.set()
        self.alive = False
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def _text_box(parent, height: int = 10, width: int = 100) -> scrolledtext.ScrolledText:
    t = scrolledtext.ScrolledText(parent, height=height, width=width, wrap="word")
    t.configure(state="disabled")
    return t


def _set_text(box, text: str) -> None:
    box.configure(state="normal")
    box.delete("1.0", "end")
    box.insert("1.0", text)
    box.configure(state="disabled")


class LibraryDialog(_Window):
    """Find on this PC… / Choose…: the catalog's candidates for one method."""

    COLS = (("kind", "kind", 130), ("name", "name", 220), ("where", "where", 220), ("summary", "summary", 330),
            ("flags", "marks", 60), ("why", "why recommended", 300))

    def __init__(self, panel, method: str, cfg, cfg_dict: dict, what: str = "both"):
        super().__init__(panel, "Workflows and FASTAs on this PC", "1150x640")
        self.method, self.cfg, self.cfg_dict = method, cfg, cfg_dict
        self.kind = cfg.kind(method) if method else ""
        self.catalog = None
        self.installed = ""
        self.rows: list[Row] = []
        self.scanning = False
        self.scan_stop: threading.Event | None = None
        f = ttk.Frame(self.win, padding=8)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=3)
        f.rowconfigure(3, weight=2)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text=(f"For method {method} ({self.kind})" if method else "No method selected: Use is off")
                  ).pack(side="left", padx=4)
        ttk.Label(top, text="   Show").pack(side="left")
        self.what = tkutil.StringVar(value=SHOW_CHOICES.get(what, SHOW_CHOICES["both"]))
        cb = ttk.Combobox(top, textvariable=self.what, values=list(SHOW_CHOICES.values()), state="readonly", width=20)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.show())
        self.show_all = tkutil.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Show all (every kind, files with problems, copies)", variable=self.show_all,
                        command=self.show).pack(side="left", padx=8)
        ttk.Label(top, text="Filter").pack(side="left")
        self.filter = tkutil.StringVar(value="")
        e = ttk.Entry(top, textvariable=self.filter, width=18)
        e.pack(side="left", padx=4)
        e.bind("<KeyRelease>", lambda ev: self.show())
        st = ttk.Frame(f)
        st.grid(row=1, column=0, sticky="ew", pady=4)
        st.columnconfigure(0, weight=1)
        self.status = ttk.Label(st, text="reading the last scan…", foreground="#444", wraplength=900, justify="left")
        self.status.grid(row=0, column=0, sticky="w", padx=4)
        self.rescan_btn = ttk.Button(st, text="Rescan", command=self.rescan)
        self.rescan_btn.grid(row=0, column=1, padx=4)
        self.stop_btn = ttk.Button(st, text="Stop", command=self.stop_scan, state="disabled")
        self.stop_btn.grid(row=0, column=2, padx=4)
        tf = ttk.Frame(f)
        tf.grid(row=2, column=0, sticky="nsew")
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tf, columns=[c for c, _, _ in self.COLS], show="headings", height=12)
        for c, label, w in self.COLS:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w", stretch=c in ("summary", "why"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("error", foreground=COLORS["error"])
        self.tree.tag_configure("warn", foreground=COLORS["warn"])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.selected())
        self.tree.bind("<Double-1>", lambda e: self.use())
        self.details = _text_box(f, height=10)
        self.details.grid(row=3, column=0, sticky="nsew", pady=4)
        bb = ttk.Frame(f)
        bb.grid(row=4, column=0, sticky="ew")
        self.use_btn = ttk.Button(bb, text=f"Use for {method}" if method else "Use for (select a method first)",
                                  command=self.use, state="disabled")
        self.use_btn.pack(side="left", padx=4)
        self.open_btn = ttk.Button(bb, text="Open folder", command=self.open_folder, state="disabled")
        self.open_btn.pack(side="left", padx=4)
        ttk.Button(bb, text="Close", command=self.close).pack(side="right", padx=4)
        self._load()

    # -- data
    def _load(self) -> None:
        cfg = self.cfg

        def work():
            cat = library.load_catalog(cfg)
            inst = library.installed_version(cfg)
            self.post(lambda: self._loaded(cat, inst))

        _thread(work)

    def _loaded(self, cat, inst: str) -> None:
        self.catalog, self.installed = cat, inst
        if not cat.scanned_at and not (cat.workflows or cat.fastas):
            self.rescan()
            return
        self.status.configure(text=catalog_line(cat))
        self.show()

    def rescan(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.scan_stop = ev = threading.Event()
        self.rescan_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.configure(text="Looking on this PC (it only reads; Stop ends it early)…")
        cfg = self.cfg

        def work():
            try:
                cat, saved = run_scan(cfg, progress=lambda t: self.post(lambda t=t: self._progress(t)), stop=ev)
            except Exception as exc:  # noqa: BLE001 - shown in the window, the app keeps running
                log.exception("library scan failed")
                self.post(lambda exc=exc: self._scan_failed(exc))
                return
            self.post(lambda: self._scanned(cat, saved))

        _thread(work)

    def _progress(self, text: str) -> None:
        if self.scanning:
            self.status.configure(text=text)

    def _scan_done_buttons(self) -> None:
        self.scanning = False
        self.rescan_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _scan_failed(self, exc) -> None:
        self._scan_done_buttons()
        self.status.configure(text=f"Could not look: {exc}", foreground=COLORS["error"])

    def _scanned(self, cat, saved: str) -> None:
        self._scan_done_buttons()
        self.catalog = cat
        self.status.configure(text=f"{catalog_line(cat)} ({saved})", foreground="#444")
        self.show()

    def stop_scan(self) -> None:
        if self.scan_stop is not None:
            self.scan_stop.set()
        self.status.configure(text="stopping…")

    def close(self) -> None:
        if self.scan_stop is not None:
            self.scan_stop.set()
        super().close()

    def _what(self) -> str:
        label = self.what.get()
        return next((k for k, v in SHOW_CHOICES.items() if v == label), "both")

    def show(self) -> None:
        if self.catalog is None:
            return
        m = (self.cfg_dict.get("methods") or {}).get(self.method) or {}
        paths = self.cfg_dict.get("paths") or {}
        wf = resolve_file(paths.get("workflow_dir"), str(m.get("workflow") or ""), library.WORKFLOW_SUFFIX)
        fa = resolve_file(paths.get("fasta_dir"), str(m.get("fasta") or ""))
        pinned = self.catalog.find(wf) if wf else None
        taxon, taxon_from = method_taxon_from(self.catalog, fa,
                                              Path(paths["fasta_dir"]) if paths.get("fasta_dir") else None)
        rows = candidate_rows(self.catalog, self.kind or "other", what=self._what(), show_all=self.show_all.get(),
                              installed_version=self.installed, pinned=pinned, taxon=taxon,
                              prefer=str(m.get("workflow") or ""), taxon_from=taxon_from)
        self.rows = filter_rows(rows, self.filter.get())
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rows):
            sev = worst(r.info.flags) if r.info is not None else "ok"
            self.tree.insert("", "end", iid=str(i), values=r.values(), tags=(sev,) if sev in ("error", "warn") else ())
        if not self.rows:
            _set_text(self.details, "Nothing to show. Tick Show all, choose another Show, or Rescan (folders to look "
                                    "in: library.roots in config.yaml; D:\\ only when added there).")
        else:
            self.tree.selection_set("0")
            self.tree.see("0")

    def _row(self) -> Row | None:
        sel = self.tree.selection()
        return self.rows[int(sel[0])] if sel and int(sel[0]) < len(self.rows) else None

    def selected(self) -> None:
        r = self._row()
        if r is None:
            return
        _set_text(self.details, details_text(r))
        self.open_btn.configure(state="normal")
        self.use_btn.configure(state="normal" if self.method and not r.info.error else "disabled")

    def open_folder(self) -> None:
        r = self._row()
        if r is not None:
            self.app._open(str(Path(r.path).parent))

    def use(self) -> None:
        r = self._row()
        if r is None or not self.method or r.info.error:
            return
        if self.app.v("m.key").get().strip() != self.method:
            messagebox.showinfo("Use", f"The Methods form now shows another method. Select {self.method} again "
                                       f"and reopen this window.", parent=self.win)
            return
        ask = use_problems(r, self.kind)
        if ask and not messagebox.askyesno(f"Use for {self.method}?", ask, icon="warning", default="no",
                                           parent=self.win):
            return
        self.use_btn.configure(state="disabled")
        self.status.configure(text=f"copying {r.name} into the pinned folder…")
        cfg, cat, method = self.cfg, self.catalog, self.method
        # the files the watcher searches with now (the saved config.yaml): Use copies beside them, Save switches
        avoid = library.live_workflows(getattr(self.app, "config_path", None) or "")

        def work():
            try:
                res = adopt_row(r, method, cfg, cat, avoid)
            except (OSError, ValueError) as exc:
                self.post(lambda exc=exc: (self.status.configure(text=f"Not copied: {exc}",
                                                                 foreground=COLORS["error"]),
                                           self.use_btn.configure(state="normal")))
                return
            self.post(lambda: self._used(r, res))

        _thread(work)

    def _used(self, r: Row, res: dict) -> None:
        self.use_btn.configure(state="normal")
        self.status.configure(text=f"{self.method} now uses {res.get('workflow') or res.get('fasta')} — press Save "
                                   f"to keep it", foreground=COLORS["ok"])
        self.panel.adopted(self.method, res, parent=self.win)


class ProposalDialog(_Window):
    """Review proposals…: auto_assign for the methods whose files are missing or broken; only Apply changes."""

    COLS = (("method", "method", 90), ("what", "file", 70), ("now", "now", 200), ("proposed", "proposed", 260),
            ("why", "why", 380))

    def __init__(self, panel, cfg, cfg_dict: dict):
        super().__init__(panel, "Ionomos proposes workflows and FASTAs", "1000x560")
        self.cfg, self.cfg_dict = cfg, cfg_dict
        self.catalog = None
        self.proposals: list = []
        f = ttk.Frame(self.win, padding=8)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=2)
        f.rowconfigure(3, weight=2)
        ttk.Label(f, text="For each method whose workflow or FASTA is missing or has a problem, the best file found "
                          "on this PC. Methods whose files are fine are left alone. Nothing changes until you press "
                          "Apply: then the files are copied into the pinned folders (originals stay where they are) "
                          "and config.yaml is saved, the old one backed up.", wraplength=960, justify="left"
                  ).grid(row=0, column=0, sticky="w")
        st = ttk.Frame(f)
        st.grid(row=1, column=0, sticky="ew", pady=4)
        st.columnconfigure(0, weight=1)
        self.status = ttk.Label(st, text="reading the last scan…", foreground="#444", wraplength=820, justify="left")
        self.status.grid(row=0, column=0, sticky="w")
        self.stop_btn = ttk.Button(st, text="Stop", command=self.stop_ev.set, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=4)
        self.tree = ttk.Treeview(f, columns=[c for c, _, _ in self.COLS], show="headings", height=8)
        for c, label, w in self.COLS:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w")
        self.tree.grid(row=2, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.selected())
        self.details = _text_box(f, height=9)
        self.details.grid(row=3, column=0, sticky="nsew", pady=4)
        bb = ttk.Frame(f)
        bb.grid(row=4, column=0, sticky="ew")
        self.apply_btn = ttk.Button(bb, text="Apply", command=self.apply, state="disabled")
        self.apply_btn.pack(side="left", padx=4)
        ttk.Button(bb, text="Find on this PC…", command=lambda: panel.find()).pack(side="left", padx=4)
        ttk.Button(bb, text="Close", command=self.close).pack(side="right", padx=4)
        self._start()

    def _start(self) -> None:
        cfg, d, ev = self.cfg, copy.deepcopy(self.cfg_dict), self.stop_ev

        def work():
            try:
                cat = library.load_catalog(cfg)
                saved = ""
                if not catalog_fresh(cat):
                    self.post(lambda: (self.status.configure(text="Looking on this PC (it only reads; Stop ends it "
                                                                  "early)…"),
                                       self.stop_btn.configure(state="normal")))
                    cat, saved = run_scan(cfg, progress=lambda t: self.post(lambda t=t: self.status.configure(text=t)),
                                          stop=ev)
                inst = library.installed_version(cfg)
                notes: list[str] = []
                props = library.auto_assign(d, cat, inst, notes)
            except Exception as exc:  # noqa: BLE001 - shown in the window
                log.exception("library proposals failed")
                self.post(lambda exc=exc: self.status.configure(text=f"Could not make proposals: {exc}",
                                                                foreground=COLORS["error"]))
                return
            self.post(lambda: self._show(cat, props, saved, notes))

        _thread(work)

    def _show(self, cat, props: list, saved: str, notes: list[str] = ()) -> None:
        self.stop_btn.configure(state="disabled")
        self.catalog, self.proposals = cat, props
        self.status.configure(text=f"{catalog_line(cat)} {proposals_summary(props, notes)}"
                                   + (f" ({saved})" if saved else ""))
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(props):
            self.tree.insert("", "end", iid=str(i), values=proposal_values(p))
        if props:
            self.tree.selection_set("0")
            self.apply_btn.configure(state="normal")

    def selected(self) -> None:
        sel = self.tree.selection()
        if sel:
            _set_text(self.details, proposal_text(self.proposals[int(sel[0])]))

    def apply(self) -> None:
        if not self.proposals:
            return
        self.apply_btn.configure(state="disabled")
        self.status.configure(text="copying the files…")
        cfg, d, props, cat = self.cfg, copy.deepcopy(self.cfg_dict), list(self.proposals), self.catalog

        def work():
            try:
                lines = library.apply_proposals(cfg, d, props, cat)
            except (OSError, ValueError, KeyError) as exc:
                self.post(lambda exc=exc: (self.status.configure(text=f"Not applied: {exc}",
                                                                 foreground=COLORS["error"]),
                                           self.apply_btn.configure(state="normal")))
                return
            self.post(lambda: self._applied(d, lines))

        _thread(work)

    def _applied(self, d: dict, lines: list[str]) -> None:
        saved = self.panel.save_methods(d["methods"])
        text = "\n".join(lines)
        if saved:
            self.status.configure(text="Applied and saved; config.yaml backed up first.", foreground=COLORS["ok"])
            messagebox.showinfo("Proposals applied", f"{text}\n\nconfig.yaml was saved (the previous one is in "
                                                     f"config-backups). Restart the watcher to use the new files.",
                                parent=self.win)
            self.close()
        else:
            self.status.configure(text="The files were copied, but config.yaml was not saved (see the message). "
                                       "Fix it and press Save.", foreground=COLORS["error"])


class DownloadDialog(_Window):
    """Download from UniProt…: organism -> proteome -> download + decoys and contaminants into fasta_dir."""

    def __init__(self, panel, cfg, method: str):
        super().__init__(panel, "Download a FASTA from UniProt", "820x520")
        self.cfg, self.method = cfg, method
        self.flow = DownloadFlow(cfg.fasta_dir)
        f = ttk.Frame(self.win, padding=8)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text="Organism").grid(row=0, column=0, sticky="e", **PAD)
        self.text = tkutil.StringVar(value="human")
        e = ttk.Entry(f, textvariable=self.text, width=40)
        e.grid(row=0, column=1, sticky="ew", **PAD)
        e.bind("<Return>", lambda ev: self.search())
        self.search_btn = ttk.Button(f, text="Search", command=self.search)
        self.search_btn.grid(row=0, column=2, **PAD)
        self.tree = ttk.Treeview(f, columns=("id", "organism", "taxon", "proteins"), show="headings", height=6)
        for c, w in (("id", 120), ("organism", 360), ("taxon", 80), ("proteins", 120)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        self.tree.grid(row=1, column=0, columnspan=3, sticky="nsew", **PAD)
        self.tree.bind("<<TreeviewSelect>>", lambda ev: self._picked())
        opts = ttk.Frame(f)
        opts.grid(row=2, column=0, columnspan=3, sticky="w", **PAD)
        self.reviewed = tkutil.BooleanVar(value=True)
        self.isoforms = tkutil.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Reviewed entries only (Swiss-Prot)", variable=self.reviewed).pack(side="left")
        ttk.Checkbutton(opts, text="With isoforms", variable=self.isoforms).pack(side="left", padx=12)
        ttk.Label(f, text=f"Saved in the FASTA folder: {cfg.fasta_dir} (decoys rev_ and contaminants contam_ are "
                          f"added as FragPipe's Database tab does; nothing is overwritten)", foreground="#666",
                  wraplength=780, justify="left").grid(row=3, column=0, columnspan=3, sticky="w", **PAD)
        self.msg = _text_box(f, height=7)
        self.msg.grid(row=4, column=0, columnspan=3, sticky="nsew", **PAD)
        bb = ttk.Frame(f)
        bb.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.go_btn = ttk.Button(bb, text="Download and prepare", command=self.download, state="disabled")
        self.go_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(bb, text="Stop", command=self.stop_ev.set, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.use_btn = ttk.Button(bb, text=f"Use for {method}" if method else "Use for (select a method first)",
                                  command=self.use, state="disabled")
        self.use_btn.pack(side="left", padx=12)
        ttk.Button(bb, text="Close", command=self.close).pack(side="right", padx=4)
        self.render()

    def render(self) -> None:
        fl = self.flow
        _set_text(self.msg, fl.message + ("\n\n" + "\n".join(f"note: {n}" for n in fl.notes) if fl.notes else "")
                  + ("\n\n" + fl.dest_problem() if fl.dest_problem() else ""))
        self.search_btn.configure(state="disabled" if fl.busy else "normal")
        self.go_btn.configure(state="normal" if fl.can_download() else "disabled")
        self.stop_btn.configure(state="normal" if fl.state == "downloading" else "disabled")
        self.use_btn.configure(state="normal" if fl.state == "done" and self.method else "disabled")

    def search(self) -> None:
        text = self.text.get()
        if not self.flow.can_search(text):
            return
        self.flow.start_search(text)
        self.tree.delete(*self.tree.get_children())
        self.render()

        def work():
            res = fastasource.search_proteomes(text)
            self.post(lambda: self._found(res))

        _thread(work)

    def _found(self, res) -> None:
        self.flow.search_done(res)
        for i, r in enumerate(self.flow.rows):
            self.tree.insert("", "end", iid=str(i), values=(r.get("id", ""), r.get("organism", ""), r.get("taxon", ""),
                                                            f"{r.get('proteins') or 0:,}"))
        if self.flow.rows:
            self.tree.selection_set("0")
        self.render()

    def _picked(self) -> None:
        sel = self.tree.selection()
        self.flow.pick(int(sel[0]) if sel else None)
        self.render()

    def download(self) -> None:
        if not self.flow.can_download():
            return
        row = self.flow.start_download()
        self.stop_ev.clear()
        self.render()
        cfg, ev = self.cfg, self.stop_ev
        reviewed, isoforms = self.reviewed.get(), self.isoforms.get()

        def say(message, done=0, total=None):
            self.post(lambda: (self.flow.progress(message, done, total), self.render()))

        def work():
            try:
                got, prep = run_download(row["id"], Path(cfg.fasta_dir), reviewed=reviewed, isoforms=isoforms,
                                         philosopher=philosopher_for(cfg), candidates=fasta_candidates(cfg.fasta_dir),
                                         progress=say, stop=ev)
            except Exception as exc:  # noqa: BLE001 - fastasource returns errors; anything else is shown too
                log.exception("FASTA download failed")
                got, prep = fastasource.DownloadResult(proteome=row["id"], error=str(exc)), None
            self.post(lambda: (self.flow.finished(got, prep), self.render(), self.panel.refresh_fastas()))

        _thread(work)

    def use(self) -> None:
        if self.flow.final is not None:
            self.panel.use_fasta(self.method, self.flow.final, parent=self.win)


class PrepareDialog(_Window):
    """Add decoys and contaminants…: one FASTA without decoys -> a FragPipe-ready copy in fasta_dir."""

    def __init__(self, panel, cfg, method: str, src: Path):
        super().__init__(panel, "Add decoys and contaminants", "760x360")
        self.cfg, self.method, self.src = cfg, method, Path(src)
        self.final: Path | None = None
        f = ttk.Frame(self.win, padding=8)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        ttk.Label(f, text=f"{self.src}\n→ a new file in {cfg.fasta_dir} (the file you picked is not changed)",
                  wraplength=720, justify="left").grid(row=0, column=0, sticky="w")
        self.msg = _text_box(f, height=9)
        self.msg.grid(row=1, column=0, sticky="nsew", pady=4)
        bb = ttk.Frame(f)
        bb.grid(row=2, column=0, sticky="ew")
        self.stop_btn = ttk.Button(bb, text="Stop", command=self.stop_ev.set)
        self.stop_btn.pack(side="left", padx=4)
        self.use_btn = ttk.Button(bb, text=f"Use for {method}" if method else "Use for (select a method first)",
                                  command=self.use, state="disabled")
        self.use_btn.pack(side="left", padx=12)
        ttk.Button(bb, text="Close", command=self.close).pack(side="right", padx=4)
        _set_text(self.msg, "starting…")
        src, dest, ev = self.src, Path(cfg.fasta_dir), self.stop_ev

        def say(message, done=0, total=None):
            self.post(lambda: _set_text(self.msg, progress_text(message, done, total)))

        def work():
            try:
                res = fastasource.prepare_for_fragpipe(src, dest, contaminant_candidates=fasta_candidates(dest),
                                                       philosopher=philosopher_for(cfg), progress=say, stop=ev)
            except Exception as exc:  # noqa: BLE001
                log.exception("FASTA prepare failed")
                res = fastasource.PrepareResult(source=src, error=str(exc))
            self.post(lambda: self._done(res))

        _thread(work)

    def _done(self, res) -> None:
        self.stop_btn.configure(state="disabled")
        notes = "\n".join(f"note: {n}" for n in res.notes)
        if res.ok:
            self.final = Path(res.path)
            _set_text(self.msg, f"Saved {res.path}\n{res.summary()}"
                      + (f"\nContaminants from: {res.contaminant_source}" if res.contaminant_source else "")
                      + (f"\n\n{notes}" if notes else ""))
            self.use_btn.configure(state="normal" if self.method else "disabled")
            self.panel.refresh_fastas()
        else:
            _set_text(self.msg, f"Not made: {res.error}" + (f"\n\n{notes}" if notes else ""))

    def use(self) -> None:
        if self.final is not None:
            self.panel.use_fasta(self.method, self.final, parent=self.win)


class LibraryPanel:
    """The Methods tab's 'Workflows and FASTAs' panel, the Choose… buttons and the first-setup banner."""

    def __init__(self, app, parent, row: int = 3):
        self.app = app
        self._fa_job = None
        self._fa_gen = 0
        self._fa_stop: threading.Event | None = None
        self._banner_hidden = False
        self._banner_busy = False
        # banner (hidden until a method needs a file)
        self.banner = tk.Frame(parent, background="#fff4e5", highlightthickness=1, highlightbackground="#e0b070")
        self.banner_lbl = tk.Label(self.banner, text="", background="#fff4e5", wraplength=640, justify="left")
        self.banner_lbl.pack(side="left", padx=6, pady=4, fill="x", expand=True)
        ttk.Button(self.banner, text="Hide", command=self.hide_banner).pack(side="right", padx=4, pady=4)
        ttk.Button(self.banner, text="Review proposals…", command=self.review).pack(side="right", padx=4, pady=4)
        self.banner.grid(row=row, column=0, sticky="ew", **PAD)
        self.banner.grid_remove()
        # the panel
        p = ttk.LabelFrame(parent, text="Workflows and FASTAs on this PC", padding=6)
        p.grid(row=row + 1, column=0, sticky="ew", **PAD)
        p.columnconfigure(0, weight=1)
        self.frame = p
        b = ttk.Frame(p)
        b.grid(row=0, column=0, sticky="w")
        ttk.Button(b, text="Find on this PC…", command=self.find).pack(side="left", padx=4)
        ttk.Button(b, text="Download from UniProt…", command=self.download).pack(side="left", padx=4)
        ttk.Button(b, text="Add decoys and contaminants…", command=self.add_decoys).pack(side="left", padx=4)
        ttk.Button(b, text="Check for updates", command=self.check_updates).pack(side="left", padx=4)
        self.fasta_lbl = ttk.Label(p, text="Select a method to see its FASTA.", foreground="#444", wraplength=820,
                                   justify="left")
        self.fasta_lbl.grid(row=1, column=0, sticky="w", **PAD)
        # Choose… beside the workflow and FASTA boxes (their frames, App._tab_methods)
        for box, what in ((app.m_wf, "workflow"), (app.m_fa, "fasta")):
            ttk.Button(box.master, text="Choose…", width=9, command=lambda w=what: self.find(w)).grid(
                row=0, column=1, padx=(0, 4))
        app.v("m.fasta").trace_add("write", lambda *_: self._fasta_changed())
        app.nb.bind("<<NotebookTabChanged>>", self._on_tab, add="+")

    # -- helpers
    def _method(self) -> str:
        return self.app.v("m.key").get().strip()

    def _context(self, quiet: bool = False):
        """(cfg_dict, Config) from the window's settings, saved or not; None (with a message) when they don't load."""
        try:
            d = self.app._collect()
            return d, config_from_data(d)
        except Exception as exc:  # noqa: BLE001 - ConfigError, a broken config.yaml on disk, a full disk …
            if not quiet:
                messagebox.showerror("Workflows and FASTAs", f"The settings in this window can't be used yet:\n{exc}"
                                     f"\n\nFix them (or press Save to see where), then try again.")
            return None

    def _on_tab(self, _e=None) -> None:
        try:
            if self.app.nb.select() == str(self.app.tab_methods):
                self.check_banner()
        except Exception:  # noqa: BLE001
            pass

    # -- the banner
    def check_banner(self) -> None:
        """Off the Tk thread: does a method miss a usable workflow or FASTA? Then show the banner."""
        if self._banner_hidden or self._banner_busy:
            return
        ctx = self._context(quiet=True)
        if ctx is None:
            return
        d, cfg = ctx
        d = copy.deepcopy(d)
        self._banner_busy = True

        def work():
            try:
                cat = library.load_catalog(cfg)
                text = banner_text(method_problems(d, cat), cat)
            except Exception:  # noqa: BLE001 - a hint only
                log.exception("library banner check failed")
                text = ""
            self.app.post(lambda: self._banner(text))

        _thread(work)

    def _banner(self, text: str) -> None:
        self._banner_busy = False
        if text and not self._banner_hidden:
            self.banner_lbl.configure(text=text)
            self.banner.grid()
        else:
            self.banner.grid_remove()

    def hide_banner(self) -> None:
        self._banner_hidden = True
        self.banner.grid_remove()

    def review(self) -> ProposalDialog | None:
        ctx = self._context()
        if ctx is None:
            return None
        d, cfg = ctx
        return ProposalDialog(self, cfg, copy.deepcopy(d))

    def save_methods(self, methods: dict) -> bool:
        """After Apply: the new methods into the app, saved through its own Save (checked, backed up)."""
        self.app.data["methods"] = methods
        self.app._refresh_methods()
        ok = self.app.save()
        if ok:
            self.banner.grid_remove()
            key = self._method()
            if key in methods:
                self.app.v("m.workflow").set(methods[key].get("workflow", ""))
                self.app.v("m.fasta").set(methods[key].get("fasta", ""))
                self.app._describe_method(methods[key].get("workflow", ""), methods[key].get("fasta", ""))
        return ok

    # -- the dialogs
    def find(self, what: str = "both") -> LibraryDialog | None:
        ctx = self._context()
        if ctx is None:
            return None
        d, cfg = ctx
        return LibraryDialog(self, self._method(), cfg, copy.deepcopy(d), what)

    def download(self) -> DownloadDialog | None:
        ctx = self._context()
        if ctx is None:
            return None
        return DownloadDialog(self, ctx[1], self._method())

    def add_decoys(self) -> PrepareDialog | None:
        ctx = self._context()
        if ctx is None:
            return None
        cfg = ctx[1]
        start = str(cfg.fasta_dir) if Path(cfg.fasta_dir).is_dir() else str(Path.home())
        src = filedialog.askopenfilename(title="A FASTA without decoys (e.g. downloaded from UniProt)",
                                         initialdir=start, filetypes=[("FASTA", "*.fasta *.fas *.fa *.txt"),
                                                                      ("all", "*.*")])
        if not src:
            return None
        return PrepareDialog(self, cfg, self._method(), Path(src))

    def check_updates(self) -> None:
        ctx = self._context()
        if ctx is None:
            return
        cfg = ctx[1]
        win = _Window(self, "Check for updates", "860x420")
        box = _text_box(win.win, height=20)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(win.win, text="Close", command=win.close).pack(anchor="e", padx=8, pady=(0, 8))
        _set_text(box, "Checking (reads the pinned files; asks UniProt for its current release)…")

        def work():
            try:
                text = check_updates(cfg)
            except Exception as exc:  # noqa: BLE001
                log.exception("library update check failed")
                text = f"Could not check: {exc}"
            win.post(lambda: _set_text(box, text))

        _thread(work)
        win.box = box
        self.update_window = win

    # -- results into the form
    def adopted(self, method: str, res: dict, parent=None) -> None:
        """After Use: the method's boxes, the method itself (Apply changes), the readiness line."""
        app = self.app
        fa_dir = app.v("paths.fasta_dir").get().strip()
        if res.get("workflow"):
            app.v("m.workflow").set(res["workflow"])
            if res.get("fasta") and resolve_file(fa_dir, app.v("m.fasta").get()) is None:
                app.v("m.fasta").set(res["fasta"])
        elif res.get("fasta"):
            app.v("m.fasta").set(res["fasta"])
        self._apply_form(method)
        notes = "\n".join(res.get("notes") or [])
        messagebox.showinfo("Use", f"{method} now uses "
                            + " and ".join(x for x in (res.get("workflow") or "", res.get("fasta") or "") if x)
                            + (f".\n\n{notes}" if notes else ".") + "\n\nPress Save to keep it.", parent=parent)

    def use_fasta(self, method: str, path: Path, parent=None) -> None:
        """A FASTA made in the FASTA folder (Download / Add decoys) for the method in the form."""
        if not method or self._method() != method:
            messagebox.showinfo("Use", "Select the method in the Methods tab first.", parent=parent)
            return
        fa_dir = Path(self.app.v("paths.fasta_dir").get().strip() or ".")
        p = Path(path)
        self.app.v("m.fasta").set(p.name if library._key(p.parent) == library._key(fa_dir) else str(p))
        self._apply_form(method)
        messagebox.showinfo("Use", f"{method} now uses {p.name}. Press Save to keep it.", parent=parent)

    def _apply_form(self, method: str) -> None:
        app = self.app
        app._refresh_methods()   # the boxes' lists; apply_method selects the method again (its select reloads the form)
        if app.v("m.workflow").get().strip() and app.v("m.fasta").get().strip():
            app.apply_method()
        else:
            app.set_status(f"{method}: set the other file too, then Apply changes and Save")
        app._describe_method(app.v("m.workflow").get(), app.v("m.fasta").get())
        self.check_banner()

    def refresh_fastas(self) -> None:
        """A new file in the FASTA folder: the FASTA box's list."""
        try:
            self.app._refresh_methods()
        except Exception:  # noqa: BLE001
            pass

    # -- the FASTA line
    def _fasta_changed(self) -> None:
        if self._fa_job is not None:
            try:
                self.app.root.after_cancel(self._fa_job)
            except tk.TclError:
                pass
        self._fa_job = self.app.root.after(400, self.describe_fasta)

    def describe_fasta(self) -> None:
        """describe_fasta of the form's FASTA, off the Tk thread; a newer choice cancels an older reading."""
        self._fa_job = None
        name = self.app.v("m.fasta").get().strip()
        fa_dir = self.app.v("paths.fasta_dir").get().strip()
        if self._fa_stop is not None:
            self._fa_stop.set()
        self._fa_gen += 1
        gen, ev = self._fa_gen, threading.Event()
        self._fa_stop = ev
        if not name:
            self._show_fasta(gen, *fasta_panel_text("", None))
            return
        self.fasta_lbl.configure(text=f"reading {name}…", foreground="#444")

        def work():
            p = resolve_file(fa_dir, name)
            info = None
            if p is not None:
                try:
                    ctx = library.Context(fasta_dir=Path(fa_dir) if fa_dir else None)
                    info = library.describe_fasta(p, ctx, stop=ev, deadline=time.monotonic() + 300)
                except Exception as exc:  # noqa: BLE001
                    text = f"could not read {name}: {exc}"
                    self.app.post(lambda: self._show_fasta(gen, text, "error"))
                    return
            text, sev = fasta_panel_text(name, info)
            self.app.post(lambda: self._show_fasta(gen, text, sev))

        _thread(work)

    def _show_fasta(self, gen: int, text: str, sev: str) -> None:
        if gen == self._fa_gen:
            self.fasta_lbl.configure(text=text, foreground=COLORS.get(sev, "#444"))

