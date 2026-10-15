"""
The TMT channel editor (D91, wave 2): which sample is on which channel of each plex, as a person sees it.

    cm = ChannelMap.from_block(ov.tmt, ["plexA", "plexB"], workflow_label="TMT-10")   # experiment.yaml tmt:
    cm = ChannelMap.from_folder(folder, plexes)        # a person's own *annotation.txt (read only, never changed)
    cm.suggest(["DMSO", "Drug"], 3, "126")            # Suggest…: tmtkit.suggest_channels for every plex
    cm.set_condition("plexA", "127N", "DMSO"); cm.set_role("plexA", "126", "reference"); cm.set_label("TMT-16")
    cm.flags(); cm.summary(); cm.block()               # tmtkit.validate, plain words, the experiment.yaml tmt: block
    roles_with_references(given_roles, cm.block())    # analysis.roles with the bridge / pool channels as reference

    TmtEditorFrame(parent, cm, folder=lambda: path, on_change=...)    # the ttk frame (review window, Analysis tab)

All the logic lives in ChannelMap, without Tk: state -> block, block -> state, the lab's name convention
<condition>_<plex>_<channel> (the plex is its position, 1, 2 ...), the label type and FragPipe's rules
(tmtkit.validate: every text the window shows under the grid is tmtkit's own). The frame only copies it in and out
of its widgets. Nothing is written here: the review window (resolve.py) and the experiment editor
(experiment_editor.py) write the block through manifest.Overrides / save_overrides on their own Accept / Save.

A channel's role is sample, reference (a bridge / pooled channel: it joins the plexes, IRS, and is never compared:
roles.kept_apart) or NA (unused). Replicates are counted, not typed: the k-th channel of a condition across the
plexes is replicate k (TMT replicates are channels, bioreplicate stays 1, the SOP).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ionomos import tmtkit

SAMPLE, REFERENCE, NA = "sample", "reference", "NA"
ROLES = (SAMPLE, REFERENCE, NA)
ROLE_WORDS = {SAMPLE: "sample", REFERENCE: "reference (bridge, pooled)", NA: "NA (unused)"}
REFERENCE_NAME = "Pool"      # a reference channel without a condition of its own (tmtkit.suggest_channels)
DEFAULT_LABEL = "TMT-10"     # neither the workflow nor experiment.yaml says


@dataclass
class Channel:
    channel: str
    condition: str = ""
    role: str = NA
    name: str = ""           # a name typed by hand; "" = the convention <condition>_<plex>_<channel>


def convention(condition: str, plex_index: int, channel: str) -> str:
    """The lab's name: DMSO_1_126 (the R script's condition_plex_channel)."""
    return f"{condition}_{plex_index}_{channel}" if condition else ""


def _split(text: str) -> list[str]:
    return [x for x in re.split(r"[,;\s]+", str(text or "")) if x]


@dataclass
class ChannelMap:
    label: str = DEFAULT_LABEL
    plexes: dict[str, list[Channel]] = field(default_factory=dict)
    workflow_label: str = ""        # the method's workflow (tmtintegrator.channel_num), "" = unknown
    workflow: str = ""              # its name, for the words
    ref_tag: str = ""               # tmtintegrator.ref_tag
    add_ref: str = ""               # tmtintegrator.add_Ref (-1: a reference sample, which must carry the tag)
    sources: dict[str, str] = field(default_factory=dict)   # plex -> where its map came from (words)
    initial: dict = field(default_factory=dict)              # the block it was loaded from (changed())

    # ------------------------------------------------------------- building --

    @classmethod
    def empty(cls, plexes=(), label: str = "", workflow_label: str = "", **kw) -> ChannelMap:
        cm = cls(label=_label_name(label or workflow_label) or DEFAULT_LABEL, workflow_label=workflow_label, **kw)
        cm.set_plexes(list(plexes) or ["plex"])
        return cm

    @classmethod
    def from_block(cls, tmt: dict | None, plexes=(), label: str = "", workflow_label: str = "", **kw) -> ChannelMap:
        """An experiment.yaml `tmt:` block. plexes: the drop's plexes (FragPipe experiments); a block for one plex
        (`channels:`) applies to every plex, as tmtkit.maps_from_block does; a `plexes:` entry for a plex that is
        not here is not shown (and not written back)."""
        tmt = tmt or {}
        cm = cls.empty(plexes or (list(map(str, tmt["plexes"])) if isinstance(tmt.get("plexes"), dict) else ()),
                       label or str(tmt.get("tag") or ""), workflow_label, **kw)
        maps: dict[str, dict[str, str]] = {}
        if isinstance(tmt.get("plexes"), dict):
            by = {str(k): v for k, v in tmt["plexes"].items()}
            for p in cm.plexes:
                spec = by.get(p)
                if isinstance(spec, dict) and isinstance(spec.get("channels"), dict):
                    maps[p] = {str(k): "" if v is None else str(v) for k, v in spec["channels"].items()}
        elif isinstance(tmt.get("channels"), dict):
            ch = {str(k): "" if v is None else str(v) for k, v in tmt["channels"].items()}
            maps = {p: dict(ch) for p in cm.plexes}
        for p, m in maps.items():
            cm._load(p, m, tmtkit.references_from_block(tmt, p))
            cm.sources[p] = "experiment.yaml"
        cm.initial = cm.block()
        return cm

    @classmethod
    def from_folder(cls, folder: Path, plexes=(), label: str = "", workflow_label: str = "", **kw) -> ChannelMap:
        cm = cls.empty(plexes, label, workflow_label, **kw)
        cm.read_folder(folder)
        cm.initial = {}
        return cm

    def read_folder(self, folder: Path | None) -> str:
        """Read button: every *annotation.txt a person put in the folder (tmtkit.channel_map_from_folder: the top,
        raw/ and each plex folder; read as FragPipe reads them, never changed). Returns what was read, in words."""
        if folder is None or not Path(folder).is_dir():
            return "no folder to read from"
        found = tmtkit.channel_map_from_folder(Path(folder))
        if not found:
            return f"no file ending in annotation.txt in {Path(folder).name} or its plex folders"
        said, used = [], set()
        for rel, fm in found.items():
            where = f"{rel}/{fm.file or fm.files[0]}" if rel else (fm.file or fm.files[0])
            if fm.problem or not fm.file:
                said.append(f"{where}: " + (fm.problem or f"{len(fm.files)} annotation files there; FragPipe "
                                                           f"uses one only when there is exactly one"))
                continue
            plex = self._plex_for(rel, len(found))
            if plex is None or plex in used:
                said.append(f"{where}: no plex {rel or '(top level)'} here, not used")
                continue
            used.add(plex)
            if not self._fits(fm.channels):
                lt = _label_for_count(len(fm.channels))
                if lt and {tmtkit.channel_key(c) for c in fm.channels} <= set(tmtkit.LABEL_TYPES[lt].channels):
                    self.set_label(lt)  # the file says which kit (FragPipe needs every channel listed)
            self._load(plex, fm.channels, [])
            self.sources[plex] = where + " (your file: FragPipe uses it, it is never changed)"
            said.append(f"read {where} for plex {plex}")
        return "; ".join(said)

    def _plex_for(self, rel: str, n_files: int) -> str | None:
        names = list(self.plexes)
        if rel in names:
            return rel
        safe = {re.sub(r"[^A-Za-z0-9._-]+", "_", p).lower(): p for p in names}
        hit = safe.get(re.sub(r"[^A-Za-z0-9._-]+", "_", rel).lower())
        if hit:
            return hit
        if rel in ("", "raw") and len(names) == 1:
            return names[0]
        if n_files == 1 and len(names) == 1:
            return names[0]
        return None

    def _fits(self, channels: dict[str, str]) -> bool:
        """The file's channels are the label type's (the same count, none it does not have)."""
        lt = tmtkit.label_type(self.label)
        keys = {tmtkit.channel_key(c) for c in channels}
        return lt is not None and keys <= set(lt.channels) and len(keys) == lt.n

    def _load(self, plex: str, channels: dict[str, str], refs: list[str]) -> None:
        """{channel: name} into a plex's rows: NA, a reference (the plex's reference channels or names, a name with
        the ref tag or a pool / bridge word), else a sample; a name that is not the convention is kept as typed."""
        k = self.index(plex)
        ref_keys = {tmtkit.channel_key(r) for r in refs if tmtkit.channel_key(r)}
        rows = {c.channel: c for c in self.plexes[plex]}
        for ch, name in channels.items():
            key = tmtkit.channel_key(ch) or str(ch)
            row = rows.get(key)
            if row is None:  # a channel the label type does not have: shown, so validate can name it
                row = Channel(key)
                self.plexes[plex].append(row)
                rows[key] = row
            name = str(name or "").strip()
            if not name or tmtkit.is_na(name):
                row.condition, row.role, row.name = "", NA, ""
                continue
            row.condition = tmtkit.condition_of(name)
            row.role = (REFERENCE if key in ref_keys or name in refs or tmtkit.is_reference_name(name, self.ref_tag)
                        else SAMPLE)
            row.name = "" if name == convention(row.condition, k, key) else name

    # ---------------------------------------------------------------- edits --

    def index(self, plex: str) -> int:
        return list(self.plexes).index(plex) + 1

    def channels(self) -> tuple[str, ...]:
        lt = tmtkit.label_type(self.label)
        return lt.channels if lt else ()

    def set_plexes(self, names: list[str]) -> None:
        """The drop's plexes (its FragPipe experiments), in order; a plex kept keeps its rows, and a renamed one (a
        name typed in the file grid) takes the rows of a plex that is gone, in order. Names made by the convention
        follow the plex's new position."""
        old = self.plexes
        new = list(dict.fromkeys(str(x) for x in names if str(x).strip())) or ["plex"]
        spare = [p for p in old if p not in new]
        self.plexes = {}
        for n in new:
            self.plexes[n] = old.get(n) or (old[spare.pop(0)] if spare else [Channel(c) for c in self.channels()])
        for p in list(self.sources):
            if p not in self.plexes:
                del self.sources[p]

    def set_label(self, label: str) -> None:
        """Another label type: its channels, in its order; what was on a channel it also has is kept."""
        lt = tmtkit.label_type(label)
        if lt is None:
            return
        self.label = lt.name
        for p, rows in self.plexes.items():
            by = {c.channel: c for c in rows}
            self.plexes[p] = [by.get(ch) or Channel(ch) for ch in lt.channels]

    def row(self, plex: str, channel: str) -> Channel:
        key = tmtkit.channel_key(channel) or str(channel)
        return next(c for c in self.plexes[plex] if c.channel == key)

    def set_condition(self, plex: str, channel: str, condition: str) -> None:
        r = self.row(plex, channel)
        r.condition, r.name = str(condition or "").strip(), ""
        if r.condition and r.role == NA:
            r.role = SAMPLE
        elif not r.condition and r.role == SAMPLE:
            r.role = NA

    def set_name(self, plex: str, channel: str, name: str) -> None:
        """A name typed by hand: its condition is read from it (the text before the first '_')."""
        r = self.row(plex, channel)
        name = str(name or "").strip()
        if not name or tmtkit.is_na(name):
            r.role, r.name = NA, ""
            return
        r.condition = tmtkit.condition_of(name)
        r.name = "" if name == convention(r.condition, self.index(plex), r.channel) else name
        if r.role == NA:
            r.role = REFERENCE if tmtkit.is_reference_name(name, self.ref_tag) else SAMPLE

    def set_role(self, plex: str, channel: str, role: str) -> None:
        role = next((k for k, w in ROLE_WORDS.items() if role in (k, w)), role)
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        r = self.row(plex, channel)
        r.role = role
        if role == REFERENCE and not r.condition:
            r.condition = REFERENCE_NAME

    def clear(self) -> None:
        for p in self.plexes:
            self.plexes[p] = [Channel(c) for c in self.channels()]
        self.sources.clear()

    def suggest(self, conditions, replicates, reference=None, reference_name: str = REFERENCE_NAME) -> None:
        """Suggest…: tmtkit.suggest_channels for every plex (the plex's position in the names). Raises
        tmtkit.TmtError when the answers do not fit the label type."""
        maps = {p: tmtkit.suggest_channels(self.label, list(conditions), replicates, reference, plex=self.index(p),
                                           reference_name=reference_name or REFERENCE_NAME)
                for p in self.plexes}
        refs = [tmtkit.channel_key(r) for r in tmtkit._refs(reference)]
        for p, m in maps.items():
            self.plexes[p] = [Channel(c) for c in self.channels()]
            self._load(p, m, refs)
            self.sources[p] = "suggested"

    def suggest_from_text(self, conditions: str, replicates: str, reference: str = "",
                          reference_name: str = "") -> None:
        """The Suggest… dialog's three boxes: 'DMSO, Drug', '3' (or '3, 4' one per condition), '126' (or blank)."""
        conds = _split(conditions)
        if not conds:
            raise tmtkit.TmtError("name at least one condition (e.g. DMSO, Drug)")
        try:
            reps = [int(x) for x in _split(replicates or "1")]
        except ValueError:
            raise tmtkit.TmtError("replicates: a whole number, or one per condition (3, 4)") from None
        if not reps or any(r < 1 for r in reps):
            raise tmtkit.TmtError("replicates: at least 1 per condition")
        self.suggest(conds, reps if len(reps) > 1 else reps[0], _split(reference) or None,
                     (reference_name or "").strip() or REFERENCE_NAME)

    # -------------------------------------------------------------- reading --

    def name(self, plex: str, row: Channel) -> str:
        if row.role == NA:
            return "NA"
        return row.name or convention(row.condition, self.index(plex), row.channel)

    def replicates(self) -> dict[tuple[str, str], int]:
        """{(plex, channel): replicate}: the k-th sample channel of a condition across the plexes."""
        n: dict[str, int] = {}
        out = {}
        for p, rows in self.plexes.items():
            for r in rows:
                if r.role == SAMPLE and r.condition:
                    n[r.condition] = n.get(r.condition, 0) + 1
                    out[(p, r.channel)] = n[r.condition]
        return out

    def maps(self) -> dict[str, dict[str, str]]:
        return {p: {r.channel: self.name(p, r) for r in rows} for p, rows in self.plexes.items()}

    def references(self) -> dict[str, list[str]]:
        return {p: [r.channel for r in rows if r.role == REFERENCE] for p, rows in self.plexes.items()}

    def is_empty(self) -> bool:
        return not any(r.role != NA for rows in self.plexes.values() for r in rows)

    def block(self) -> dict:
        """The experiment.yaml `tmt:` block (tmtkit.to_overrides_block); {} when no channel is named. The reference
        channels: reference_channel when every plex has the same, else each plex's own reference:."""
        if self.is_empty():
            return {}
        refs = self.references()
        same = len({tuple(v) for v in refs.values()}) == 1
        block = tmtkit.to_overrides_block(self.maps(), self.label, next(iter(refs.values())) if same else None)
        if not same:
            for p, r in refs.items():
                if r:
                    block["plexes"][p]["reference"] = r[0] if len(r) == 1 else r
        return block

    def changed(self) -> bool:
        return self.block() != (self.initial or {})

    def flags(self) -> list[tmtkit.Flag]:
        """FragPipe's rules and Ionomos' own checks (tmtkit.validate), with the workflow's ref tag."""
        if self.is_empty():
            return []
        return tmtkit.validate(self.maps(), self.label, self.ref_tag, self.add_ref, self.references(),
                               source="the channel editor")

    def label_warning(self) -> str:
        wf, lt = tmtkit.label_type(self.workflow_label), tmtkit.label_type(self.label)
        if not wf or not lt or wf.name == lt.name:
            return ""
        what = f"workflow {self.workflow}" if self.workflow else "the method's workflow"
        return (f"{what} is set to {wf.name} (tmtintegrator.channel_num): a {lt.name} map fails the job before "
                f"the search. Pick {wf.name} again, or name a {lt.name} workflow with workflow: in experiment.yaml")

    def errors(self) -> list[str]:
        """What stops the search (tmtkit's error flags, their own words) and a label type that is not the
        workflow's."""
        out = [f.line() for f in self.flags() if f.severity == "error"]
        return out + ([self.label_warning()] if self.label_warning() else [])

    def warnings(self) -> list[str]:
        return [f.line() for f in self.flags() if f.severity != "error"]

    def reference_conditions(self) -> list[str]:
        return tmtkit.reference_conditions(self.block(), self.ref_tag)

    def summary(self) -> list[str]:
        """What the map says, in plain words (TmtPlan.summary's style)."""
        lt = tmtkit.label_type(self.label)
        head = f"{lt.name} ({lt.reagent}, {lt.n} channels)" if lt else f"label type {self.label}"
        if self.workflow_label:
            head += (", as the workflow" if not self.label_warning() else f"; the workflow says {self.workflow_label}")
        out = [head]
        if self.is_empty():
            out.append("no channel named yet: FragPipe would name them <plex>_<channel>, and the report would not "
                       "know your conditions. Suggest… fills the grid from a few answers")
            return out
        reps = self.replicates()
        for p, rows in self.plexes.items():
            conds: dict[str, int] = {}
            for r in rows:
                if r.role == SAMPLE and r.condition:
                    conds[r.condition] = conds.get(r.condition, 0) + 1
            named = sum(1 for r in rows if r.role != NA)
            ref = [f"{r.channel} ({self.name(p, r)})" for r in rows if r.role == REFERENCE]
            line = (f"plex {p}: {named} channel{'s' if named != 1 else ''} named ("
                    + (", ".join(f"{c} {n}" for c, n in conds.items()) or "no conditions") + f"), "
                    f"{sum(1 for r in rows if r.role == NA)} NA" + (f", reference {', '.join(ref)}" if ref else ""))
            src = self.sources.get(p, "")
            if src and src not in ("experiment.yaml", "suggested"):
                line += f"; read from {src}"
            out.append(line)
        if any(self.references().values()):
            out.append("A reference channel joins the plexes (IRS) and is left out of the comparisons")
        top = max(reps.values(), default=0)
        if top:
            out.append(f"replicates are the channels of a condition (up to {top}); bioreplicate stays 1")
        return out


def _label_name(text: str) -> str:
    """FragPipe's name for the label type (#154: an iTRAQ-4 map stays iTRAQ-4, never the default TMT-10)."""
    return tmtkit.fragpipe_label(text)


def _label_for_count(n: int) -> str:
    return next((lt.name for lt in tmtkit.LABEL_TYPES.values() if lt.n == n), "")


def roles_with_references(given: dict | None, block: dict | None) -> dict[str, str]:
    """analysis.roles after the channel editor: each reference channel's condition is a reference (roles.py, so it
    is used for IRS only and never compared, D91); a condition the map now holds on sample channels only loses a
    reference role it had; every other entry is kept as written."""
    from ionomos.downstream import roles

    try:
        out = roles.normalise(given or {})
    except roles.RoleError:
        out = dict(given or {})
    block = block or {}
    refs = tmtkit.reference_conditions(block)
    named: set[str] = set()
    try:
        plexes = list(map(str, block["plexes"])) if isinstance(block.get("plexes"), dict) else ["plex"]
        for m in tmtkit.maps_from_block(block, plexes).values():
            named |= {tmtkit.condition_of(n) for n in m.values() if not tmtkit.is_na(n) and str(n).strip()}
    except (tmtkit.TmtError, AttributeError, TypeError):
        pass
    low = {c.lower() for c in refs}
    for c in list(out):
        if c.lower() in {x.lower() for x in named} - low and out[c] == "reference":
            del out[c]
    for c in refs:
        if not any(k.lower() == c.lower() for k in out):
            out[c] = "reference"
    return out


def workflow_info(cfg, method: str, workflow: str | None = None) -> dict:
    """{label, ref_tag, add_ref, workflow} of a TMT method's workflow (tmtintegrator.*), {} when it can't be read.
    Small files only: safe on a watcher thread; never called from a drop handler (D82)."""
    try:
        from ionomos.fragpipe import _find_file, read_properties

        name = workflow or cfg.methods[method].workflow
        wf = _find_file(name, cfg.workflow_dir, ".workflow") if name else None
        if wf is None:
            return {"workflow": name or ""}
        props = read_properties(wf.read_text(encoding="utf-8", errors="replace"))
    except (OSError, KeyError, AttributeError, ValueError):
        return {}
    return {"label": props.get("tmtintegrator.channel_num", "").strip(), "workflow": Path(str(name)).stem,
            "ref_tag": props.get("tmtintegrator.ref_tag", "").strip(),
            "add_ref": props.get("tmtintegrator.add_Ref", "").strip()}


def for_folder(dest: Path, cfg=None, method: str | None = None) -> ChannelMap | None:
    """The editor's state for a filed experiment (the Analysis tab): tmtkit.plan_for_folder (its plexes, label
    type, the workflow's ref tag), the map from experiment.yaml's tmt: or else the person's annotation.txt.
    None when it is not a TMT experiment. Reads, writes nothing."""
    import contextlib
    import io

    from ionomos.manifest import OverridesError, load_overrides

    try:
        with contextlib.redirect_stdout(io.StringIO()):  # plan_for_folder says its fallbacks for the command line
            tp = tmtkit.plan_for_folder(Path(dest), cfg, method)
        tmt = load_overrides(dest).tmt or {}
    except (OSError, ValueError, OverridesError, tmtkit.TmtError):
        return None
    plexes = [p.name for p in tp.plexes]
    kw = {"workflow_label": tp.label if tp.label_from == "workflow" else "", "ref_tag": tp.ref_tag,
          "add_ref": tp.add_ref}
    if tmt.get("channels") or tmt.get("plexes"):
        cm = ChannelMap.from_block(tmt, plexes, tp.label, **kw)
    else:
        cm = ChannelMap.empty(plexes, tp.label, **kw)
        cm.read_folder(dest)
        for pp in tp.plexes:  # the map the search used, if intake recorded one (plan.tmt)
            if pp.channels and pp.name in cm.plexes and pp.name not in cm.sources:
                cm._load(pp.name, pp.channels, [r.split(" ")[0] for r in pp.reference])
                cm.sources[pp.name] = pp.source or "the search"
        cm.initial = {}
    return cm


# ------------------------------------------------------------------ tk frame --


class TmtEditorFrame:
    """The ttk frame: label type, a grid per plex (a tab each), Read / Suggest… / Clear, validation and a summary.
    on_change() runs after every edit (the windows refresh their own summary). folder() gives the folder the Read
    button reads (None: the button is off)."""

    def __init__(self, parent, model: ChannelMap, folder: Callable[[], Path | None] | None = None,
                 on_change: Callable[[], None] | None = None, title: str = "TMT channels: which sample is on "
                                                                           "which channel"):
        from tkinter import ttk

        from ionomos import tkutil

        self.model = model
        self.folder = folder
        self.on_change = on_change
        self.frame = ttk.LabelFrame(parent, text=title, padding=6)
        self.frame.editor = self  # the windows' tests reach the editor from its widget
        self.frame.columnconfigure(0, weight=1)
        top = ttk.Frame(self.frame)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Label type").pack(side="left")
        self.label_v = tkutil.StringVar(value=model.label)
        self.label_cb = ttk.Combobox(top, textvariable=self.label_v, values=list(tmtkit.LABEL_TYPES),
                                     state="readonly", width=10)
        self.label_cb.pack(side="left", padx=4)
        self.label_cb.bind("<<ComboboxSelected>>", lambda _e: self._label_chosen())
        self.read_btn = ttk.Button(top, text="Read annotation.txt from the folder", command=self.read)
        self.read_btn.pack(side="left", padx=2)
        self.suggest_btn = ttk.Button(top, text="Suggest…", command=self.suggest_dialog)
        self.suggest_btn.pack(side="left", padx=2)
        ttk.Button(top, text="Clear", command=self.clear).pack(side="left", padx=2)
        if folder is None:
            self.read_btn.state(["disabled"])
        self.label_note = ttk.Label(self.frame, foreground="#b26a00", wraplength=640, justify="left")
        self.label_note.grid(row=1, column=0, sticky="w")
        self.book = ttk.Notebook(self.frame)
        self.book.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.errors_l = ttk.Label(self.frame, foreground="#b00020", wraplength=640, justify="left")
        self.errors_l.grid(row=3, column=0, sticky="w")
        self.warnings_l = ttk.Label(self.frame, wraplength=640, justify="left")
        self.warnings_l.grid(row=4, column=0, sticky="w")
        self.summary_l = ttk.Label(self.frame, foreground="#333", wraplength=640, justify="left")
        self.summary_l.grid(row=5, column=0, sticky="w", pady=(2, 0))
        self.status_l = ttk.Label(self.frame, foreground="#666", wraplength=640, justify="left")
        self.status_l.grid(row=6, column=0, sticky="w")
        self._cells: dict[tuple[str, str], tuple] = {}   # (plex, channel) -> (name var, cond var, rep label, role cb)
        self._busy = False
        self.build()

    # ------------------------------------------------------------ the grid --

    def build(self) -> None:
        """(Re)draw every plex's grid from the model."""
        import tkinter as tk
        from tkinter import ttk

        from ionomos import tkutil

        for tab in self.book.tabs():
            self.book.forget(tab)
        self._cells.clear()
        for plex, rows in self.model.plexes.items():
            outer = ttk.Frame(self.book)
            canvas = tk.Canvas(outer, highlightthickness=0, height=min(24 * (len(rows) + 1) + 6, 240))
            vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            grid = ttk.Frame(canvas)
            grid.bind("<Configure>", lambda _e, c=canvas, g=grid: c.configure(scrollregion=c.bbox("all"),
                                                                              width=g.winfo_reqwidth()))
            canvas.create_window((0, 0), window=grid, anchor="nw")
            canvas.configure(yscrollcommand=vsb.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            for c, h in enumerate(("channel", "sample name", "condition", "rep", "role")):
                ttk.Label(grid, text=h, foreground="#666").grid(row=0, column=c, sticky="w", padx=3)
            for i, r in enumerate(rows, start=1):
                name_v = tkutil.StringVar(value="" if r.role == NA else self.model.name(plex, r))
                cond_v = tkutil.StringVar(value=r.condition if r.role != NA else "")
                ttk.Label(grid, text=r.channel, width=5).grid(row=i, column=0, sticky="w", padx=3)
                ttk.Entry(grid, textvariable=name_v, width=24).grid(row=i, column=1, sticky="w", padx=3, pady=1)
                ttk.Entry(grid, textvariable=cond_v, width=14).grid(row=i, column=2, sticky="w", padx=3)
                rep = ttk.Label(grid, width=3)
                rep.grid(row=i, column=3, sticky="w", padx=3)
                role = ttk.Combobox(grid, values=[ROLE_WORDS[k] for k in ROLES], state="readonly", width=24)
                role.set(ROLE_WORDS[r.role])
                role.grid(row=i, column=4, sticky="w", padx=3)
                name_v.trace_add("write", lambda *_, p=plex, ch=r.channel: self._typed(p, ch, "name"))
                cond_v.trace_add("write", lambda *_, p=plex, ch=r.channel: self._typed(p, ch, "condition"))
                role.bind("<<ComboboxSelected>>", lambda _e, p=plex, ch=r.channel: self._typed(p, ch, "role"))
                self._cells[(plex, r.channel)] = (name_v, cond_v, rep, role)
            self.book.add(outer, text=plex)
        self.refresh()

    def _typed(self, plex: str, channel: str, what: str) -> None:
        if self._busy:
            return
        name_v, cond_v, _rep, role = self._cells[(plex, channel)]
        if what == "name":
            self.model.set_name(plex, channel, name_v.get())
        elif what == "condition":
            self.model.set_condition(plex, channel, cond_v.get())
        else:
            self.model.set_role(plex, channel, role.get())
        self.refresh(skip=(plex, channel, what))

    def refresh(self, skip: tuple = ()) -> None:
        """The cells, replicates, validation and summary from the model (the cell being typed in is left alone)."""
        self._busy = True
        try:
            reps = self.model.replicates()
            for (plex, ch), (name_v, cond_v, rep, role) in self._cells.items():
                r = self.model.row(plex, ch)
                here = skip[:2] == (plex, ch)
                if not (here and skip[2] == "name"):
                    want = "" if r.role == NA else self.model.name(plex, r)
                    if name_v.get() != want:
                        name_v.set(want)
                if not (here and skip[2] == "condition") and cond_v.get() != (r.condition if r.role != NA else ""):
                    cond_v.set(r.condition if r.role != NA else "")
                role.set(ROLE_WORDS[r.role])
                rep.configure(text=str(reps.get((plex, ch), "")))
        finally:
            self._busy = False
        self.label_v.set(self.model.label)
        self.label_note.configure(text=self.model.label_warning())
        errors, warnings = self.model.errors(), self.model.warnings()
        self.errors_l.configure(text="\n".join(errors[:6]) + (f"\n… and {len(errors) - 6} more" if len(errors) > 6
                                                               else ""))
        self.warnings_l.configure(text="\n".join(warnings[:6]))
        self.summary_l.configure(text="\n".join(self.model.summary()))
        self.suggest_btn.configure(text="Suggest…  (start here)" if self.model.is_empty() else "Suggest…")
        if self.on_change is not None:
            self.on_change()

    # ------------------------------------------------------------- buttons --

    def _label_chosen(self) -> None:
        self.model.set_label(self.label_v.get())
        self.build()

    def set_plexes(self, names: list[str]) -> None:
        if list(self.model.plexes) != [n for n in names if n.strip()]:
            self.model.set_plexes(names)
            self.build()

    def read(self) -> None:
        said = self.model.read_folder(self.folder() if self.folder else None)
        self.status_l.configure(text=said)
        self.build()

    def clear(self) -> None:
        self.model.clear()
        self.status_l.configure(text="cleared: nothing is written until you Accept / Save")
        self.build()

    def suggest(self, conditions: str, replicates: str, reference: str = "", reference_name: str = "") -> str:
        """What the dialog's OK does; returns the problem in words ("" = done)."""
        try:
            self.model.suggest_from_text(conditions, replicates, reference, reference_name)
        except tmtkit.TmtError as exc:
            return str(exc)
        self.status_l.configure(text="suggested: check the names, then Accept / Save")
        self.build()
        return ""

    def suggest_dialog(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        from ionomos import tkutil

        win = tk.Toplevel(self.frame)
        win.title("Suggest a channel map")
        win.transient(self.frame.winfo_toplevel())
        win.attributes("-topmost", True)
        f = ttk.Frame(win, padding=10)
        f.grid(sticky="nsew")
        lt = tmtkit.label_type(self.model.label)
        ttk.Label(f, text=f"{self.model.label}: {lt.n if lt else '?'} channels per plex, "
                          f"{len(self.model.plexes)} plex{'es' if len(self.model.plexes) != 1 else ''}",
                  foreground="#666").grid(row=0, column=0, columnspan=2, sticky="w")
        cv, rv, refv, refn = (tkutil.StringVar(value=v) for v in ("", "3", "", REFERENCE_NAME))
        for i, (text, var, hint) in enumerate((("Conditions", cv, "e.g. DMSO, Drug (letters and digits)"),
                                               ("Replicates per condition", rv, "3, or one per condition: 3, 4"),
                                               ("Reference channel", refv, "the bridge / pool channel, e.g. 126; "
                                                                           "blank = none"),
                                               ("Reference name", refn, "its condition in the names")), start=1):
            ttk.Label(f, text=text).grid(row=i * 2 - 1, column=0, sticky="e", padx=4, pady=2)
            if var is refv:
                ttk.Combobox(f, textvariable=var, values=["", *self.model.channels()], width=10).grid(
                    row=i * 2 - 1, column=1, sticky="w", padx=4)
            else:
                ttk.Entry(f, textvariable=var, width=28).grid(row=i * 2 - 1, column=1, sticky="w", padx=4)
            ttk.Label(f, text=hint, foreground="#666").grid(row=i * 2, column=1, sticky="w", padx=4)
        err = ttk.Label(f, foreground="#b00020", wraplength=360, justify="left")
        err.grid(row=9, column=0, columnspan=2, sticky="w")

        def ok(*_):
            problem = self.suggest(cv.get(), rv.get(), refv.get(), refn.get())
            if problem:
                err.configure(text=problem)
            else:
                win.destroy()

        btns = ttk.Frame(f)
        btns.grid(row=10, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=4)
        ttk.Button(btns, text="Fill the grid", command=ok).pack(side="left", padx=4)
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda *_: win.destroy())
        win.focus_force()
