"""
TMT, before the search: label types, channel maps, FragPipe's rules and Ionomos' own checks (D91). No Tk.

The single source of truth for what a TMT label type is and what a channel map must look like. Used by
manifest.py (experiment.yaml `tmt:`), fragpipe.prepare (the annotation files and every check before a search),
intake.py (what a drop's own annotation.txt says), the fake FragPipe and the testbed, `ionomos tmt ...`, and
(wave 2) the Tk channel editor.

    LABEL_TYPES                                  {"TMT-10": LabelType(...), ...}: FragPipe's names (QuantLabel.java)
    label_type("TMTpro-16") -> LabelType | None  any spelling: TMT10, tmt-10, Tmt16 (Sage), TMTpro, 16, iTRAQ4plex
    fragpipe_label("dileu12") -> "DiLeu-12"     FragPipe's name for any label type it knows ("" = none)
    read_annotation(path) -> [(channel, name)]   a FragPipe annotation file, as FragPipe reads it (raises TmtError)
    annotation_text(channels) -> str             "<channel>\\t<name>\\n" per channel, in the order given
    parse_sample_name("DMSO_1_126")              -> SampleName(condition="DMSO", plex=1, channel="126")
    validate(channels_by_plex, label, ref_tag, add_ref=None, reference=None) -> [Flag]
    plan(dest, overrides, workflow_props, raws, raw_dir=None, workflow_name="") -> TmtPlan
    channel_map_from_folder(folder) -> {subfolder: FolderMap}   the user's own *annotation.txt, read only
    suggest_channels(label, conditions, replicates, reference=None, plex=1) -> {channel: name}
    to_overrides_block(maps, label, reference=None) -> dict      ready for experiment.yaml `tmt:`
    reference_conditions(tmt, ref_tag="") -> [condition]          the bridge / pool channels' conditions (the
                                                                 analysis gives them the reference role)
    require_tmt(folder, method, source, cfg, given)  raises NotTmt unless the job is TMT (Config.kind, D54), or
                                                 when nothing says what it is: `ionomos tmt` reads TMT jobs only
    add_parser(sub)                              `ionomos tmt plan | annotation | suggest | qc`

FragPipe's rules (FragPipe 24.0, CmdTmtIntegrator.configure and TmtiPanel.parseTmtAnnotationFile; checked against the
source on 2026-10-14): a line is "<channel> <sample>" split on whitespace, two fields or the run stops ("Invalid line
in annotation file"); a file lists exactly as many lines as the workflow's label type has channels
(`tmtintegrator.channel_num`); a sample name other than NA is used once across all plexes (case-sensitive); with
"Define reference: Reference sample" (`tmtintegrator.add_Ref=-1`) the ref tag must be set and every file must have a
name containing it (`String.contains`, case-sensitive); no name is empty. FragPipe's window also refuses a file whose
channels are not exactly the label type's (TmtiPanel.validateAnnotations); headless runs do not check that, so
Ionomos does, as an error: names on channels the label does not have cannot be right.

Every Flag has a stable code, a severity and a fix. "error" = FragPipe would stop, or the map cannot be right: the job
fails before the search with the rule. "warn" = filed and searched, said in the log, ionomos.json and DONE.txt.
"info" = recorded only (a note). Small groups are tested and labelled, never refused (D32).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

TMT_MASS = 229.1629      # TMT 6/10/11-plex reagent, monoisotopic (FragPipe's workflows: 229.16293)
TMTPRO_MASS = 304.2071   # TMTpro 16/18/35-plex (FragPipe's workflows: 304.20715)
MASS_TOL = 0.01


class TmtError(ValueError):
    """A channel map or annotation file that can't be read or used. User-facing message."""


@dataclass(frozen=True)
class LabelType:
    name: str                    # what FragPipe calls it: tmtintegrator.channel_num
    reagent: str                 # TMT | TMTpro
    mass: float                  # the label's mass on K and the peptide N-terminus
    channels: tuple[str, ...]    # in FragPipe's order (QuantLabel.java), which is the reporter-ion order
    sage: str = ""               # Sage's quant.tmt kit ("" = Sage has none)

    @property
    def n(self) -> int:
        return len(self.channels)


_TMT10 = ("126", "127N", "127C", "128N", "128C", "129N", "129C", "130N", "130C", "131N")
_PRO = (*_TMT10, "131C", "132N", "132C", "133N", "133C", "134N", "134C", "135N")
_PRO_D = ("127D", "128ND", "128CD", "129ND", "129CD", "130ND", "130CD", "131ND", "131CD", "132ND", "132CD", "133ND",
          "133CD", "134ND", "134CD", "135ND", "135CD")   # TMTpro-35's deuterated channels
# FragPipe 24's QuantLabel.java (LABELS and labelModMap), checked against the 24.0 source on 2026-10-15: the name
# (tmtintegrator.channel_num), the channels in its order and the label's mass. The lab's kits come first (a count of
# channels picks them before the others); TESTED are searched end to end with the fake FragPipe (#154).
LABEL_TYPES: dict[str, LabelType] = {
    "TMT-6": LabelType("TMT-6", "TMT", TMT_MASS, ("126", "127N", "128C", "129N", "130C", "131N"), "Tmt6"),
    "TMT-10": LabelType("TMT-10", "TMT", TMT_MASS, _TMT10, "Tmt10"),
    "TMT-11": LabelType("TMT-11", "TMT", TMT_MASS, (*_TMT10, "131C"), "Tmt11"),
    "TMT-16": LabelType("TMT-16", "TMTpro", TMTPRO_MASS, _PRO[:16], "Tmt16"),
    "TMT-18": LabelType("TMT-18", "TMTpro", TMTPRO_MASS, _PRO, "Tmt18"),
    "TMT-35": LabelType("TMT-35", "TMTpro", TMTPRO_MASS, (*_PRO, *_PRO_D)),
    "TMT-2": LabelType("TMT-2", "TMT", 225.1558, ("126", "127C")),
    "TMT-0": LabelType("TMT-0", "TMT", 224.1525, ("126",)),
    "iTRAQ-4": LabelType("iTRAQ-4", "iTRAQ", 144.1021, ("114", "115", "116", "117")),
    "iTRAQ-8": LabelType("iTRAQ-8", "iTRAQ", 304.2054, ("113", "114", "115", "116", "117", "118", "119", "121")),
    "iodoTMT-6": LabelType("iodoTMT-6", "iodoTMT", 329.2266, ("126", "127", "128", "129", "130", "131")),
}
TESTED = ("TMT-6", "TMT-10", "TMT-11", "TMT-16", "TMT-18", "iTRAQ-4")   # searched end to end with the fake FragPipe
# FragPipe knows these too (QuantLabel.java) but their channels are not TMT-like (115a, 302O, 597): a tag: or a
# workflow naming one is accepted and the map passed on, checked for FragPipe's own rules only (TMT_LABEL_UNKNOWN)
OTHER_FRAGPIPE_LABELS = ("sCLIP-6", "IBT-16", "DiLeu-12", "DiLeu-1", "DeAla-13", "Trp-2")
CHANNEL_RE = re.compile(r"^(\d{3})(ND|CD|[NCD]?)$", re.I)
_CHANNEL_TOKEN = re.compile(r"^\d{3}(?:ND|CD|[NCD])?$", re.I)
LAB_NAME = re.compile(r"^[A-Za-z0-9]+_1_\d{3}[A-Z]?$")   # the lab's R script keeps only these (downstream/tmt.py)
REFERENCE_WORDS = re.compile(r"(?i)(?:^|[_\-\s.])(?:pool(?:ed)?|bridge|ref(?:erence)?|norm|irs)(?:$|[_\-\s.\d])")
TMT_KEYS = ("tag", "channels", "plexes", "reference_channel")
PLEX_KEYS = ("channels", "reference")
NO_MAP = ("TMT job without a tmt: channel map in experiment.yaml; FragPipe will use its default channel names "
          "(<plex>_<channel>), so the report will not know your conditions")


def known_tags() -> str:
    return (", ".join(LABEL_TYPES) + " (TMTpro-16 = TMT-16, TMTpro-18 = TMT-18); FragPipe's "
            + ", ".join(OTHER_FRAGPIPE_LABELS) + " are passed on unchecked")


def _squash(text) -> str:
    return re.sub(r"[\s_\-]+", "", str(text or "")).lower().replace("plex", "")


def label_type(text) -> LabelType | None:
    """FragPipe's label type for any common spelling: 'TMT-10', 'TMT10', 'tmt10plex', 'Tmt16' (Sage), 'TMTpro'
    (16), 'TMTpro-18', '18', 'iTRAQ4plex', 'itraq-8', 'TMT-35', 'iodoTMT-6'. None when Ionomos does not know its
    channels (FragPipe's sCLIP, IBT, DiLeu ...: fragpipe_label)."""
    s = _squash(text)
    if not s:
        return None
    for prefix, name in (("itraq", "iTRAQ-{}"), ("iodotmt", "iodoTMT-{}")):
        if s.startswith(prefix):
            return LABEL_TYPES.get(name.format(s[len(prefix):]))
    if s.startswith("tmtpro"):
        s = "tmt" + (s[6:] or "16")
    m = re.fullmatch(r"(?:tmt)?(\d+)", s)
    return LABEL_TYPES.get(f"TMT-{m.group(1)}") if m else None


def fragpipe_label(text) -> str:
    """FragPipe's own name for any label type it knows (QuantLabel.java), in any spelling label_type takes; '' when
    FragPipe has no such label type."""
    lt = label_type(text)
    if lt:
        return lt.name
    return next((n for n in OTHER_FRAGPIPE_LABELS if _squash(n) == _squash(text)), "") if _squash(text) else ""


def channel_key(ch) -> str:
    """126 / '127n' / 'TMT127N' / '127N ' -> '127N'; TMT-35's '128nd' -> '128ND'; '' when it is no channel."""
    m = re.search(r"(\d{3})\s*(ND|CD|[NCD]?)\s*$", str(ch if ch is not None else "").strip(), re.I)
    return (m.group(1) + m.group(2).upper()) if m else ""


def is_na(name) -> bool:
    return str(name or "").strip().upper() == "NA"


# ------------------------------------------------------------------ flags --


@dataclass
class Flag:
    code: str
    severity: str        # error | warn | info
    text: str
    fix: str = ""
    plex: str = ""
    source: str = ""     # "experiment.yaml tmt" or the annotation file the rule is about

    def as_dict(self) -> dict:
        return asdict(self)

    def line(self) -> str:
        return self.text + (f" — {self.fix}" if self.fix else "")


# --------------------------------------------------------------- names --


@dataclass(frozen=True)
class SampleName:
    condition: str           # as the report reads it: the text before the first "_"
    plex: int | None         # the lab's middle number (DMSO_1_126 -> 1)
    channel: str             # the channel token in the name ("" when none)
    lab_pattern: bool        # condition_1_channel, which the lab's R script keeps


def parse_sample_name(name: str) -> SampleName | None:
    """The lab's condition_plex_channel names ('DMSO_1_126'), tolerating 'DMSO_126', 'DMSO-1-126' and '126_DMSO'.
    The condition is what the report uses: the text before the first '_' (or '-' when there is no '_'), with a
    leading channel token skipped. None for NA or an empty name."""
    s = str(name or "").strip()
    if not s or is_na(s):
        return None
    tokens = [t for t in re.split(r"[_\-]", s) if t]
    if not tokens:
        return None
    channel, plex = "", None
    if _CHANNEL_TOKEN.match(tokens[-1]) and len(tokens) > 1:
        channel = channel_key(tokens[-1])
        body = tokens[:-1]
    elif _CHANNEL_TOKEN.match(tokens[0]) and len(tokens) > 1:
        channel = channel_key(tokens[0])
        body = tokens[1:]
    else:
        body = tokens
    if len(body) > 1 and body[-1].isdigit() and channel:
        plex = int(body[-1])
    if channel and _CHANNEL_TOKEN.match(tokens[0]) and not _CHANNEL_TOKEN.match(tokens[-1]):
        condition = body[0]
    else:
        first = s.split("_")[0] if "_" in s else (tokens[0] if channel else s)
        condition = first if not _CHANNEL_TOKEN.match(first) else (body[0] if body else first)
    return SampleName(condition, plex, channel, bool(LAB_NAME.match(s)))


def condition_of(name: str) -> str:
    p = parse_sample_name(name)
    return p.condition if p else ""


def is_reference_name(name: str, ref_tag: str = "") -> bool:
    s = str(name or "")
    return bool(REFERENCE_WORDS.search("_" + s + "_")) or (bool(ref_tag) and ref_tag in s)


# ------------------------------------------------------ annotation files --


def annotation_text(channels) -> str:
    """FragPipe's annotation file: '<channel>\\t<name>' per line, in the order given ({channel: name} or pairs)."""
    pairs = channels.items() if isinstance(channels, dict) else channels
    return "".join(f"{ch}\t{'' if name is None else name}\n" for ch, name in pairs)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")


def annotation_rows(text: str) -> list[list[str]]:
    """The lines of an annotation file as FragPipe splits them (blank lines skipped, whitespace-separated)."""
    return [ln.split() for ln in text.splitlines() if ln.strip()]


def read_annotation(path: Path) -> list[tuple[str, str]]:
    """[(channel, sample name)] as FragPipe reads the file (TmtiPanel.parseTmtAnnotationFile). Raises TmtError with
    FragPipe's own words for a line that is not two fields. Read only: the file is never changed."""
    p = Path(path)
    try:
        text = _decode(p.read_bytes())
    except OSError as exc:
        raise TmtError(f"cannot read {p.name}: {exc}") from exc
    out = []
    for row in annotation_rows(text):
        if len(row) != 2:
            raise TmtError(f"Invalid line in annotation file {p.name}: {' '.join(row)} (a line is a channel and one "
                           f"sample name without spaces; NA for an unused channel)")
        out.append((row[0], row[1]))
    return out


# ------------------------------------------------------- experiment.yaml --


def _norm_channels(ch) -> dict[str, str]:
    if not isinstance(ch, dict):
        raise TmtError("channels must map a channel to a sample name (126: DMSO_1_126)")
    return {str(k).strip(): ("" if v is None else str(v)) for k, v in ch.items()}


def _refs(v) -> list[str]:
    if v is None or v == "":
        return []
    items = v if isinstance(v, (list, tuple)) else [v]
    return [str(x).strip() for x in items if str(x).strip()]


def check_block(tmt) -> list[str]:
    """What is wrong with an experiment.yaml `tmt:` block as written ([] when nothing). Used by
    manifest.parse_overrides: an unknown key or tag, a plex without channels, channels that are not a mapping."""
    if not isinstance(tmt, dict):
        return ["tmt: must be a mapping"]
    out = []
    bad = sorted(set(map(str, tmt)) - set(TMT_KEYS))
    if bad:
        out.append(f"tmt: unknown key(s) {', '.join(bad)} (known: {', '.join(TMT_KEYS)})")
    if tmt.get("tag") not in (None, "") and not fragpipe_label(tmt["tag"]):
        out.append(f"tmt: tag {tmt['tag']!r} is not a label type FragPipe knows; use one of {known_tags()} "
                   f"(the workflow's Quant (Isobaric) tab, 'label type')")
    if "channels" in tmt and "plexes" in tmt:
        out.append("tmt: give either channels: (one plex, or the same map for every plex) or plexes:, not both")
    if tmt.get("plexes") is not None and not isinstance(tmt.get("plexes"), dict):
        out.append("tmt: plexes must map a plex name to {channels: {...}}")
        return out
    plexes = tmt.get("plexes") or ({"default": tmt} if tmt.get("channels") is not None else {})
    if not plexes and tmt.get("reference_channel") in (None, ""):
        out.append("tmt: needs either channels: {...} or plexes: {name: {channels: {...}}}")
    for plex, spec in plexes.items():
        if not isinstance(spec, dict):
            out.append(f"tmt: plex {plex!r} needs a non-empty channels mapping")
            continue
        if plex != "default":
            extra = sorted(set(map(str, spec)) - set(PLEX_KEYS))
            if extra:
                out.append(f"tmt: plex {plex!r}: unknown key(s) {', '.join(extra)} (known: {', '.join(PLEX_KEYS)})")
        ch = spec.get("channels")
        if not isinstance(ch, dict) or not ch:
            out.append(f"tmt: plex {plex!r} needs a non-empty channels mapping")
    return out


def maps_from_block(tmt: dict, experiments: list[str]) -> dict[str, dict[str, str]]:
    """{experiment (plex): {channel: name}} from an experiment.yaml `tmt:` block. One `channels:` applies to every
    experiment; `plexes:` must name each one (TmtError otherwise). {} without a channel map."""
    tmt = tmt or {}
    if not (tmt.get("plexes") or tmt.get("channels")):
        return {}
    if tmt.get("plexes"):
        plexes = tmt["plexes"]
        missing = sorted(set(experiments) - set(map(str, plexes)))
        if missing:
            raise TmtError(f"tmt.plexes has no entry for experiment(s): {', '.join(missing)}")
        by = {str(k): v for k, v in plexes.items()}
        return {exp: _norm_channels((by[exp] or {}).get("channels")) for exp in experiments}
    ch = _norm_channels(tmt["channels"])
    return {exp: dict(ch) for exp in experiments}


def references_from_block(tmt: dict, plex: str) -> list[str]:
    """The reference (bridge / pool) channels or names for a plex: its own `reference:`, else `reference_channel`."""
    tmt = tmt or {}
    spec = (tmt.get("plexes") or {}).get(plex) if isinstance(tmt.get("plexes"), dict) else None
    own = _refs((spec or {}).get("reference")) if isinstance(spec, dict) else []
    return own or _refs(tmt.get("reference_channel"))


def reference_conditions(tmt: dict, ref_tag: str = "") -> list[str]:
    """The conditions (the text before the first '_', as the report reads them) of the reference (bridge / pool)
    channels of an experiment.yaml `tmt:` block: each plex's `reference:` / `reference_channel` (a channel or a
    name), and every name carrying the workflow's ref tag or a pool / bridge word. The analysis gives them the
    reference role (roles.py), so they join the plexes and are not compared (D91)."""
    tmt = tmt or {}
    try:
        plexes = (list(map(str, tmt["plexes"])) if isinstance(tmt.get("plexes"), dict) else ["plex"])
        maps = maps_from_block(tmt, plexes)
    except (TmtError, AttributeError, TypeError):
        return []
    out: list[str] = []
    other: set[str] = set()   # a condition also on a sample channel is a condition, not a reference (DMSO on 126)
    for plex, ch in maps.items():
        want = references_from_block(tmt, plex)
        keys = {channel_key(w) for w in want if channel_key(w)}
        for c, name in ch.items():
            if is_na(name) or not str(name).strip():
                continue
            cond = condition_of(name)
            if channel_key(c) in keys or name in want or is_reference_name(name, ref_tag):
                if cond and cond not in out:
                    out.append(cond)
            elif cond:
                other.add(cond)
    return [c for c in out if c not in other]


# ------------------------------------------------------------- validate --


def _rows_of(channels) -> list[list[str]]:
    return annotation_rows(annotation_text(channels))


def fragpipe_problems(rows_by_plex: dict[str, list[list[str]]], label: str = "", ref_tag: str = "",
                      add_ref=None, source: str = "experiment.yaml tmt") -> list[Flag]:
    """What FragPipe 24 would stop on (CmdTmtIntegrator.configure). rows: each file's lines as FragPipe splits them."""
    out: list[Flag] = []
    lt = label_type(label)
    m = re.search(r"-(\d+)$", str(label or "").strip())
    want = lt.n if lt else (int(m.group(1)) if m else 0)
    real_ref = str(add_ref).strip() == "-1"
    if real_ref and not str(ref_tag or "").strip():
        out.append(Flag("TMT_REF_TAG_EMPTY", "error", "the workflow uses a reference sample (Define reference: "
                        "Reference sample, tmtintegrator.add_Ref=-1) but its Ref sample tag is empty; FragPipe stops "
                        "on that", "set the tag in FragPipe's Quant (Isobaric) tab and save the workflow, or use "
                        "Define reference: Virtual", source=source))
    seen: dict[str, str] = {}
    for plex, rows in rows_by_plex.items():
        odd = [" ".join(r) for r in rows if len(r) != 2]
        if odd:
            out.append(Flag("TMT_BAD_LINE", "error", f"plex {plex}: a channel needs one sample name without spaces "
                            f"(NA to leave it out), got {odd[0]!r}", "write each channel as '126 DMSO_1_126'",
                            plex, source))
            continue
        if want and len(rows) != want:
            out.append(Flag("TMT_CHANNEL_COUNT", "error", f"plex {plex} lists {len(rows)} channel(s) but the "
                            f"workflow's label type is {label} ({want}); list every channel, with NA for the unused "
                            f"ones", f"list all {want} channels of {label}", plex, source))
        for _ch, sample in rows:
            if is_na(sample):
                continue
            if sample in seen:
                out.append(Flag("TMT_DUPLICATE_NAME", "error", f"sample name {sample} is used twice (plex "
                                f"{seen[sample]} and {plex}); FragPipe needs every name once across all plexes",
                                "give each plex's channels their own names (DMSO_1_126 in plex 1, DMSO_2_126 in "
                                "plex 2)", plex, source))
            seen.setdefault(sample, plex)
        if real_ref and ref_tag and rows and not any(ref_tag in r[1] for r in rows):
            out.append(Flag("TMT_NO_REFERENCE", "error", f"plex {plex} has no sample name containing the workflow's "
                            f"Ref sample tag {ref_tag!r} (tmtintegrator.ref_tag, with Define reference: Reference "
                            f"sample); FragPipe stops ('Found annotation files without reference channel')",
                            f"name the bridge / pool channel with {ref_tag!r} in it (e.g. {ref_tag}_1_126), or use "
                            f"Define reference: Virtual", plex, source))
    return out


def validate(channels_by_plex: dict[str, dict[str, str]], label_type_name: str = "", ref_tag: str = "",
             add_ref=None, reference=None, source: str = "experiment.yaml tmt") -> list[Flag]:
    """FragPipe's rules, then Ionomos' own checks of the maps themselves ({plex: {channel: name}}). reference: the
    bridge / pool channels or names ({plex: [..]} or one list for every plex), for the across-plexes check."""
    rows = {p: _rows_of(ch) for p, ch in channels_by_plex.items()}
    out = fragpipe_problems(rows, label_type_name, ref_tag, add_ref, source)
    lt = label_type(label_type_name)
    conds: dict[str, int] = {}
    unlike: list[str] = []
    for plex, ch in channels_by_plex.items():
        keys = [channel_key(c) or str(c) for c in ch]
        if lt:
            unknown = [k for k in keys if k not in lt.channels]
            if unknown:
                out.append(Flag("TMT_UNKNOWN_CHANNEL", "error", f"plex {plex}: {', '.join(unknown[:4])} "
                                f"{'is not a channel' if len(unknown) == 1 else 'are not channels'} of {lt.name} "
                                f"({', '.join(lt.channels)})", f"use {lt.name}'s channels, or set tag: to the kit "
                                f"you used", plex, source))
            elif len(set(keys)) == len(keys) and [k for k in lt.channels if k in keys] != keys:
                out.append(Flag("TMT_CHANNEL_ORDER", "warn", f"plex {plex}: the channels are not in {lt.name}'s "
                                f"order ({', '.join(lt.channels[:4])}, ...)", "list them in the kit's order, as "
                                "FragPipe's window writes them", plex, source))
        twice = sorted({k for k in keys if keys.count(k) > 1})
        if twice:
            out.append(Flag("TMT_CHANNEL_TWICE", "error", f"plex {plex}: channel {', '.join(twice)} is listed more "
                            f"than once", "list every channel once", plex, source))
        named = {channel_key(c) or str(c): n for c, n in ch.items() if not is_na(n) and str(n).strip()
                 and len(str(n).split()) == 1}  # a name FragPipe would refuse is said once, above
        if ch and not named:
            out.append(Flag("TMT_ALL_NA", "warn", f"plex {plex}: every channel is NA, so it has no samples",
                            "name the channels that hold samples", plex, source))
        for c, name in named.items():
            p = parse_sample_name(name)
            if p and p.channel and p.channel != c:
                out.append(Flag("TMT_NAME_CHANNEL", "warn", f"plex {plex}: {name} is on channel {c} but its name "
                                f"says {p.channel}", "check the map: keep each channel's own token in its name "
                                "(the SOP: 'Keep the 126, 127N, etc. description for each channel')", plex, source))
            if p and not is_reference_name(name, ref_tag):
                conds[p.condition] = conds.get(p.condition, 0) + 1
            if not LAB_NAME.match(name):
                unlike.append(name)
    single = sorted(c for c, n in conds.items() if n == 1)
    if single and len(conds) > 1:
        out.append(Flag("TMT_ONE_CHANNEL", "warn", f"condition {', '.join(single[:5])} "
                        f"{'has' if len(single) == 1 else 'have'} one channel only: it is compared anyway, labelled "
                        f"low confidence (D32)", "add replicate channels if the experiment has them", source=source))
    if unlike:
        out.append(Flag("TMT_NAME_PATTERN", "info", f"{len(unlike)} name(s) do not follow condition_1_channel "
                        f"(e.g. {unlike[0]}): the lab's annotation script leaves them out, and the report takes "
                        f"the condition from the text before the first '_'", "name channels like DMSO_1_126",
                        source=source))
    if len(channels_by_plex) > 1:
        refs = reference if isinstance(reference, dict) else {p: list(reference or []) for p in channels_by_plex}
        anyref = any(refs.values())
        at: dict[str, list[str]] = {}   # plex -> the names on its reference channel(s)
        for plex, ch in channels_by_plex.items():
            by_ch = {channel_key(c) or str(c): n for c, n in ch.items()}
            at[plex] = [n for r in refs.get(plex) or [] for n in ([by_ch.get(channel_key(r))] if channel_key(r)
                        else [r if r in ch.values() else None]) if n and not is_na(n)]
        pooled = any(is_reference_name(n, ref_tag) for ns in at.values() for n in ns)
        for plex in channels_by_plex:
            want = refs.get(plex) or []
            if not want:
                continue
            if not at[plex]:
                out.append(Flag("TMT_REFERENCE_MISSING", "warn", f"plex {plex} has no sample on the reference "
                                f"{', '.join(want)}: the plexes can't be joined on it (IRS, D48)",
                                "put the bridge / pool sample on the same channel in every plex, or change "
                                "reference_channel", plex, source))
            elif pooled and not any(is_reference_name(n, ref_tag) for n in at[plex]):
                out.append(Flag("TMT_REFERENCE_MISSING", "warn", f"plex {plex} has {', '.join(at[plex])} on the "
                                f"reference {', '.join(want)}, where the other plexes have their pool",
                                "put the bridge / pool sample on the same channel in every plex, or give this "
                                "plex its own reference:", plex, source))
        if not anyref and not all(any(is_reference_name(n, ref_tag) for n in ch.values())
                                  for ch in channels_by_plex.values()):
            out.append(Flag("TMT_NO_BRIDGE", "info", f"{len(channels_by_plex)} plexes and no reference (bridge / pool) "
                            f"channel: the analysis joins them on each plex's mean only when every plex holds the "
                            f"same mix of conditions (D48)", "set reference_channel: if a pool is in every plex",
                            source=source))
    return out


# ------------------------------------------------------------- the folder --


@dataclass
class FolderMap:
    """The annotation file(s) a user put in one folder of a drop, read only."""
    folder: str                  # relative to the drop ("" = the top, "raw", or a plex folder)
    files: list[str]             # every file ending in annotation.txt there
    file: str = ""               # the one FragPipe would use (exactly one), else ""
    channels: dict[str, str] = field(default_factory=dict)
    problem: str = ""            # it could not be read as FragPipe reads it

    def as_dict(self) -> dict:
        return asdict(self)


def _annotation_files(d: Path) -> list[Path]:
    try:
        return sorted(p for p in d.iterdir() if p.is_file() and p.name.endswith("annotation.txt")
                      and not p.name.startswith((".", "~$")))
    except OSError:
        return []


def channel_map_from_folder(folder: Path) -> dict[str, FolderMap]:
    """Every *annotation.txt a person put in a drop or an experiment folder: at the top, in raw/ and in each folder
    one level down (a plex). Read as FragPipe reads them; nothing is written (help/safety.md)."""
    folder = Path(folder)
    out: dict[str, FolderMap] = {}
    try:
        subs = [folder] + sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith((".", "~$"))
                                 and p.name not in ("fragpipe", "results", "ionomos_run", "labwatch_run")
                                 and not p.name.startswith("fragpipe_previous"))
    except OSError:
        return out
    for d in subs:
        files = _annotation_files(d)
        if not files:
            continue
        rel = "" if d == folder else d.name
        fm = FolderMap(rel, [f.name for f in files])
        if len(files) == 1:
            fm.file = files[0].name
            try:
                fm.channels = dict(read_annotation(files[0]))
            except TmtError as exc:
                fm.problem = str(exc)
        out[rel] = fm
    return out


# ----------------------------------------------------------------- the plan --


@dataclass
class PlexPlan:
    name: str                                  # the FragPipe experiment
    folder: str                                # where its raw files are, relative to the experiment folder
    files: int                                 # raw files (fractions)
    channels: dict[str, str] = field(default_factory=dict)   # the map FragPipe will use ({} = its own names)
    source: str = ""                           # experiment.yaml | <folder>/<file> (the user's) | "" (none)
    target: str = ""                           # where Ionomos writes annotation.txt ("" = nothing is written)
    na: list[str] = field(default_factory=list)
    reference: list[str] = field(default_factory=list)
    conditions: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TmtPlan:
    label: str = ""                    # FragPipe's label type the search uses ("" = unknown)
    label_from: str = ""               # workflow | experiment.yaml | ""
    ref_tag: str = ""
    add_ref: str = ""                  # tmtintegrator.add_Ref: -1 = a reference sample, 1 = virtual
    quant_level: str = ""              # tmtintegrator.quant_level: 2 = MS2, 3 = MS3
    plexes: list[PlexPlan] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    annotations: dict[str, str] = field(default_factory=dict)   # RunSpec.annotations: target -> text
    user_files: dict[str, dict] = field(default_factory=dict)   # channel_map_from_folder, as dicts

    def errors(self) -> list[Flag]:
        return [f for f in self.flags if f.severity == "error"]

    def warnings(self) -> list[Flag]:
        return [f for f in self.flags if f.severity == "warn"]

    def infos(self) -> list[Flag]:
        return [f for f in self.flags if f.severity == "info"]

    def error_text(self) -> str:
        """One line for a JobError: grouped by where the rule applies, the experiment.yaml map first."""
        groups: dict[str, list[str]] = {}
        for f in self.errors():
            groups.setdefault(f.source or "TMT", []).append(f.line())
        return "; ".join(f"{src}: " + "; ".join(texts) for src, texts in groups.items())

    def as_dict(self) -> dict:
        d = asdict(self)
        d["summary"] = self.summary()
        return d

    def summary(self) -> list[str]:
        """What Ionomos will do, in plain words, a line per plex and then the flags."""
        lt = label_type(self.label)
        head = (f"{self.label} ({lt.reagent}, {lt.n} channels)" if lt else "label type unknown (the workflow sets "
                "none)") + (f", from the {self.label_from}" if self.label_from else "")
        if self.quant_level:
            head += f"; reporter ions from {'MS3' if self.quant_level == '3' else 'MS2'}"
        if self.add_ref:
            head += ("; TMT-Integrator's reference: a reference sample named with " + repr(self.ref_tag)
                     if self.add_ref == "-1" else "; TMT-Integrator's reference: virtual (the mean of the channels)")
        out = [head]
        for p in self.plexes:
            named = len(p.channels) - len(p.na)
            conds = ", ".join(f"{c} {n}" for c, n in p.conditions.items())
            where = (f"Ionomos writes {p.target}" if p.target else
                     ("FragPipe uses " + p.source if p.source and p.source != "experiment.yaml" else
                      "no annotation file: FragPipe names the channels <plex>_<channel>"))
            out.append(f"plex {p.name} ({p.folder or 'top level'}, {p.files} file{'s' if p.files != 1 else ''}): "
                       + (f"{named} channel{'s' if named != 1 else ''} named ({conds or 'no conditions'}), "
                          f"{len(p.na)} NA" if p.channels else "no channel map")
                       + (f", reference {', '.join(p.reference)}" if p.reference else "")
                       + (f"; map from {p.source}" if p.source else "") + f"; {where}")
        for f in self.flags:
            out.append(f"{f.severity.upper()} {f.code}: {f.line()}")
        return out


def _lines(raws, dest: Path) -> list[tuple[Path, str, int]]:
    out = []
    for r in raws or []:
        if isinstance(r, dict):
            p = Path(r["file"])
            out.append((p if p.is_absolute() else Path(dest) / p, str(r["experiment"]), int(r.get("bioreplicate") or 1)))
        else:
            p = Path(r[0])
            out.append((p if p.is_absolute() else Path(dest) / p, str(r[1]), int(r[2] or 1)))
    return out


def current_block(dest: Path, filed: dict | None = None) -> tuple[dict, str]:
    """The `tmt:` block a search of a filed experiment uses, and where it came from (#147): the experiment folder's
    experiment.yaml as it is now (read as intake reads it, manifest.load_overrides), so a map fixed after a failed
    job, in the file or on the Analysis tab, is the one the Retry uses; the block saved at intake (ionomos.json
    plan.overrides.tmt, `filed`) only when experiment.yaml is gone. Raises TmtError when experiment.yaml can't be
    read: the job fails with that, rather than searching with a map the person has since changed."""
    from ionomos.manifest import EXPERIMENT_YAML, OverridesError, load_overrides

    filed = dict(filed or {})
    if not (Path(dest) / EXPERIMENT_YAML).is_file():
        return filed, ("the map saved at intake (experiment.yaml is gone)" if filed else "")
    try:
        now = dict(load_overrides(dest).tmt or {})
    except (OverridesError, OSError) as exc:
        said = " ".join(str(exc).split())  # one line: it becomes the job's reason (FAILED.txt, the job list)
        raise TmtError(f"{EXPERIMENT_YAML} can't be read, so its tmt: map can't be used: {said}; fix the file, "
                       f"then Retry") from None
    if not now:
        return {}, ("experiment.yaml (its tmt: block was removed after intake)" if filed else "")
    return now, "experiment.yaml" + (" (changed since intake)" if filed and _as_json(now) != _as_json(filed) else "")


def _as_json(block) -> object:
    """A block as ionomos.json keeps it (YAML's 126: key is "126" there)."""
    import json

    return json.loads(json.dumps(block, default=str))


def _overrides_tmt(overrides) -> dict:
    if overrides is None:
        return {}
    if isinstance(overrides, dict):
        return overrides.get("tmt") or {}
    return getattr(overrides, "tmt", None) or {}


def _rel(p: Path, dest: Path) -> str:
    try:
        return p.relative_to(dest).as_posix() or "."
    except ValueError:
        return str(p)


def plan(dest: Path, overrides, workflow_props: dict | None, raws, raw_dir: Path | None = None,
         workflow_name: str = "") -> TmtPlan:
    """What a TMT search will use, and every check before it. dest: the experiment folder; overrides: experiment.yaml
    (a dict or manifest.Overrides) or the plan's overrides dict; workflow_props: fragpipe.read_properties of the
    workflow; raws: the manifest ((path, experiment, bioreplicate[, type]) or {file, experiment, bioreplicate});
    raw_dir: the folder RunSpec.annotations keys are relative to (default dest). Reads, writes nothing."""
    dest = Path(dest)
    raw_dir = Path(raw_dir) if raw_dir is not None else dest
    props = workflow_props or {}
    tmt = _overrides_tmt(overrides)
    tp = TmtPlan(ref_tag=props.get("tmtintegrator.ref_tag", "").strip(),
                 add_ref=props.get("tmtintegrator.add_Ref", "").strip(),
                 quant_level=props.get("tmtintegrator.quant_level", "").strip())
    yaml_src = "experiment.yaml tmt"
    for problem in check_block(tmt) if tmt else []:
        tp.flags.append(Flag("TMT_BLOCK", "error", problem, "fix tmt: in experiment.yaml", source="experiment.yaml"))

    wf = props.get("tmtintegrator.channel_num", "").strip()
    wf_lt, tag_lt = label_type(wf), label_type(tmt.get("tag"))
    wf_name, tag_name = fragpipe_label(wf) or wf, fragpipe_label(tmt.get("tag"))
    wfname = f"workflow {workflow_name}" if workflow_name else "the workflow"
    if wf and tag_name and _squash(wf_name) != _squash(tag_name):   # by FragPipe's name, any label type (#154)
        tp.flags.append(Flag("TMT_TAG_MISMATCH", "error", f"experiment.yaml says tag: {tmt.get('tag')} but {wfname} "
                             f"is set to {wf} (tmtintegrator.channel_num)",
                             f"if the samples are {wf_name}, change tag: to {wf_name}; if they are "
                             f"{tag_name}, name a {tag_name} workflow with workflow: in experiment.yaml "
                             f"(FragPipe ships TMT10-MS3, TMT16, TMT18-Astral, iTRAQ4, TMT35 ...)", source="TMT"))
    if not wf and props:
        tp.flags.append(Flag("TMT_LABEL_UNSET", "warn", f"{wfname} sets no label type (tmtintegrator.channel_num)",
                             "set it in FragPipe's Quant (Isobaric) tab and save the workflow"))
    lt = wf_lt or (None if wf else tag_lt)
    tp.label = lt.name if lt else (wf_name or tag_name or "")
    tp.label_from = "workflow" if wf else ("experiment.yaml" if tag_name else "")
    if tp.label and not lt:
        where = wfname if wf else "experiment.yaml's tag:"
        tp.flags.append(Flag("TMT_LABEL_UNKNOWN", "warn", f"{where} is set to label type {tp.label}, whose channels "
                             f"Ionomos does not know (it checks {', '.join(LABEL_TYPES)})",
                             "the map is passed on as it is, checked only for FragPipe's own rules"))

    lines = _lines(raws, dest)
    experiments = sorted({e for _p, e, _r in lines})
    folders = {e: sorted({str(p.parent) for p, ee, _r in lines if ee == e}) for e in experiments}
    own_folder = (all(len(fs) == 1 for fs in folders.values())
                  and len({f for fs in folders.values() for f in fs}) == len(experiments))
    try:
        yaml_maps = maps_from_block(tmt, experiments) if not any(f.code == "TMT_BLOCK" for f in tp.flags) else {}
    except TmtError as exc:
        tp.flags.append(Flag("TMT_BLOCK", "error", str(exc), "add a plexes: entry for every plex",
                             source="experiment.yaml"))
        yaml_maps = {}

    # what is in each folder already: FragPipe takes the one file ending in annotation.txt there
    in_folder: dict[str, list[Path]] = {}
    for fs in folders.values():
        for f in fs:
            in_folder.setdefault(f, _annotation_files(Path(f)))
    tp.user_files = {_rel(Path(f), dest): {"files": [p.name for p in files]} for f, files in in_folder.items() if files}

    effective: dict[str, dict[str, str]] = {}
    sources: dict[str, str] = {}
    user_rows: dict[str, tuple[str, list[list[str]]]] = {}
    for exp in experiments:
        fs = folders[exp]
        rel = _rel(Path(fs[0]), dest) if len(fs) == 1 else ", ".join(_rel(Path(f), dest) for f in fs)
        pp = PlexPlan(exp, "" if rel == "." else rel, sum(1 for _p, e, _r in lines if e == exp))
        tp.plexes.append(pp)
        here = in_folder.get(fs[0], []) if len(fs) == 1 else []
        shared = not own_folder and sum(1 for e in experiments if folders[e] == fs) > 1
        if len(here) > 1:
            tp.flags.append(Flag("TMT_SEVERAL_FILES", "warn", f"kept {', '.join(p.name for p in here)} and wrote no "
                                 f"annotation.txt beside them" + (" (experiment.yaml's tmt: map was not applied)"
                                                                  if exp in yaml_maps else "")
                                 + "; FragPipe uses a folder's annotation file only when there is exactly one, so it "
                                   "will name the channels <plex>_<channel>",
                                 "keep one file ending in annotation.txt per plex folder", exp))
            continue
        if len(here) == 1:
            user = here[0]
            src = _rel(user, dest)
            try:
                text = _decode(user.read_bytes())
            except OSError as exc:
                tp.flags.append(Flag("TMT_UNREADABLE", "error", f"{src} can't be read: {exc}",
                                     "close it in other programs, then Retry", exp, src))
                continue
            rows = annotation_rows(text)
            if shared:
                if any(f.code == "TMT_SHARED_FILE" and f.source == src for f in tp.flags):
                    continue
                tp.flags.append(Flag("TMT_SHARED_FILE", "error", f"{src} sits in a folder that holds "
                                     f"{sum(1 for e in experiments if folders[e] == fs)} plexes: FragPipe uses it for "
                                     f"every one of them and then stops on the repeated sample names",
                                     "drop each plex's raw files in a folder of its own (<plex>\\*.raw) with its own "
                                     "annotation.txt", exp, src))
                continue
            user_rows[exp] = (src, rows)
            pp.source = src
            if all(len(r) == 2 for r in rows):
                effective[exp] = {r[0]: r[1] for r in rows}
            ours = _rows_of(yaml_maps[exp]) if exp in yaml_maps else None
            if ours is not None and sorted(map(tuple, ours)) != sorted(map(tuple, rows)):  # order: Ionomos writes the kit's
                if user.name == "annotation.txt":
                    tp.flags.append(Flag("TMT_ANNOTATION_DIFFERS", "warn", f"kept the existing {user.name} (differs "
                                         f"from experiment.yaml's tmt: map)", "FragPipe uses the file; delete or fix "
                                         "it if experiment.yaml is right, then Retry", exp, src))
                else:
                    tp.flags.append(Flag("TMT_ANNOTATION_DIFFERS", "warn", f"kept {user.name} and wrote no "
                                         f"annotation.txt beside it (experiment.yaml's tmt: map was not applied)",
                                         "FragPipe uses your file; remove it if experiment.yaml is right", exp, src))
            continue
        if exp in yaml_maps:
            pp.source = "experiment.yaml"
            if own_folder or len(yaml_maps) == 1:
                folder = Path(fs[0])
                target = "annotation.txt" if folder == raw_dir else str(folder / "annotation.txt")
                pp.target = _rel(folder / "annotation.txt", dest)
                # in the kit's order, as FragPipe's window writes it (YAML may have sorted the keys)
                tp.annotations[target] = annotation_text(in_kit_order(yaml_maps[exp], lt))
                effective[exp] = in_kit_order(yaml_maps[exp], lt)
            sources[exp] = yaml_src

    if not yaml_maps and not user_rows:
        tp.flags.append(Flag("TMT_NO_MAP", "warn", NO_MAP, "write the channels into experiment.yaml (tmt:), or put "
                             "an annotation.txt beside each plex's raw files; `ionomos tmt suggest` makes one"))
        if len(experiments) > 1 and not own_folder:
            tp.flags.append(Flag("TMT_SHARED_FOLDER", "warn", shared_plex_warning(len(experiments)), ""))
    elif yaml_maps and not tp.annotations and len(yaml_maps) > 1 and not own_folder:
        tp.flags.append(Flag("TMT_SHARED_FOLDER", "warn", shared_plex_warning(len(yaml_maps)), ""))

    # FragPipe's rules on what it will read: the files Ionomos writes and the user's own
    written = {e: effective[e] for e in effective if sources.get(e) == yaml_src}
    if written:
        tp.flags += fragpipe_problems({e: _rows_of(m) for e, m in written.items()}, tp.label, tp.ref_tag,
                                      tp.add_ref, yaml_src)
    if user_rows:
        for exp, (src, rows) in user_rows.items():
            tp.flags += [f for f in fragpipe_problems({exp: rows}, tp.label, tp.ref_tag, tp.add_ref, src)
                         if f.code != "TMT_REF_TAG_EMPTY" or not written]
        names: dict[str, str] = {}
        for exp, rows in [(e, _rows_of(m)) for e, m in written.items()] + [(e, r) for e, (_s, r) in user_rows.items()]:
            for r in rows:
                if len(r) == 2 and not is_na(r[1]):
                    if r[1] in names and names[r[1]] != exp and (exp in user_rows or names[r[1]] in user_rows):
                        tp.flags.append(Flag("TMT_DUPLICATE_NAME", "error", f"sample name {r[1]} is used twice (plex "
                                             f"{names[r[1]]} and {exp}); FragPipe needs every name once across all "
                                             f"plexes", "give each plex's channels their own names", exp,
                                             user_rows.get(exp, (yaml_src,))[0]))
                    names.setdefault(r[1], exp)

    # Ionomos' own checks: the maps FragPipe will use, else experiment.yaml's
    maps = effective or yaml_maps
    refs = {e: references_from_block(tmt, e) for e in experiments}
    if maps:
        own = [f for f in validate(maps, tp.label, tp.ref_tag, None, refs, yaml_src if not user_rows else "TMT")
               if f.code not in ("TMT_CHANNEL_COUNT", "TMT_DUPLICATE_NAME", "TMT_BAD_LINE", "TMT_NO_REFERENCE",
                                 "TMT_REF_TAG_EMPTY")
               and (f.code != "TMT_CHANNEL_ORDER" or f.plex in user_rows)]  # Ionomos writes its files in order
        for f in own:
            if not effective and f.severity == "error":  # FragPipe won't read this map: worth knowing, not a stop
                f.severity = "warn"
            if f.plex in user_rows:
                f.source = user_rows[f.plex][0]
            elif f.source == "TMT":
                f.source = yaml_src
        tp.flags += own
    counts = {p.name: p.files for p in tp.plexes}
    if len(set(counts.values())) > 1:
        tp.flags.append(Flag("TMT_FRACTIONS_DIFFER", "warn", "the plexes have different numbers of raw files ("
                             + ", ".join(f"{k} {v}" for k, v in counts.items()) + "): a fraction may be missing",
                             "check the copy; fractions of a plex are combined, so a missing one costs depth, not "
                             "the search"))
    reps = sorted({r for _p, _e, r in lines if r != 1})
    if reps:
        tp.flags.append(Flag("TMT_BIOREPLICATE", "warn", f"bioreplicate {', '.join(map(str, reps))} is set for TMT "
                             f"files; the SOP sets 1 when the replicates are in the channels",
                             "set bioreplicate: 1 in experiment.yaml files: unless each file really is another "
                             "biological replicate"))
    for pp in tp.plexes:
        ch = effective.get(pp.name) or yaml_maps.get(pp.name) or {}
        pp.channels = dict(ch)
        pp.na = [c for c, n in ch.items() if is_na(n) or not str(n).strip()]
        want = refs.get(pp.name) or []
        pp.reference = [f"{c} ({n})" for c, n in ch.items() if (channel_key(c) in {channel_key(r) for r in want}
                                                              or n in want) and not is_na(n)]
        if not pp.reference:
            pp.reference = [f"{c} ({n})" for c, n in ch.items() if not is_na(n) and is_reference_name(n, tp.ref_tag)]
        for n in ch.values():
            p = parse_sample_name(n)
            if p and not is_reference_name(n, tp.ref_tag):
                pp.conditions[p.condition] = pp.conditions.get(p.condition, 0) + 1
    return tp


def in_kit_order(channels: dict[str, str], lt: LabelType | None) -> dict[str, str]:
    """{channel: name} in the label type's order when every channel is one of its own; else as given."""
    if lt is None:
        return dict(channels)
    keyed = {channel_key(c) or str(c): (c, n) for c, n in channels.items()}
    if len(keyed) != len(channels) or not set(keyed) <= set(lt.channels):
        return dict(channels)
    return {keyed[k][0]: keyed[k][1] for k in lt.channels if k in keyed}


def shared_plex_warning(n: int) -> str:
    """Several TMT plexes whose raw files sit in one folder (D69). The layout is the lab's choice: Ionomos never
    moves the files, it says what FragPipe will do."""
    return (f"{n} TMT plexes share one folder: FragPipe reads one annotation file per folder, so Ionomos writes "
            f"none and FragPipe will name the channels <plex>_<channel> (an annotation.txt of your own there would "
            f"be used for every plex, and FragPipe stops on the repeated sample names). To get the sample names, "
            f"drop the experiment with each plex's raw files in a folder of its own (<plex>\\*.raw)")


# --------------------------------------------------------------- suggest --


def suggest_channels(label: str, conditions: list[str], replicates: int | list[int] | dict, reference=None,
                     plex: int = 1, reference_name: str = "Pool") -> dict[str, str]:
    """A channel map from a few answers, in the kit's order: the reference channel(s) first named
    <reference_name>_<plex>_<channel>, then each condition's replicates (<condition>_<plex>_<channel>), the rest NA.
    replicates: one number for every condition, a list in the conditions' order (one number each), or {condition: n}.
    Raises TmtError, one sentence, when they do not fit the kit: no condition, a condition named twice, fewer than 1
    replicate, a reference channel the kit does not have or named twice, more samples than channels."""
    lt = label_type(label)
    if lt is None and fragpipe_label(label):
        raise TmtError(f"Ionomos does not know the channels of {fragpipe_label(label)}, so it can't suggest a map; "
                       f"write channels: by hand with FragPipe's channel names")
    if lt is None:
        raise TmtError(f"{label!r} is not a label type FragPipe knows; use one of {known_tags()}")
    conds = [str(c).strip() for c in conditions if str(c).strip()]
    if not conds:
        raise TmtError("name at least one condition (e.g. DMSO, Drug)")
    bad = [c for c in conds if not re.fullmatch(r"[A-Za-z0-9]+", c)]
    if bad:
        raise TmtError(f"condition {bad[0]!r}: use letters and digits only (the report takes the text before the "
                       f"first '_' as the condition)")
    seen: dict[str, str] = {}
    for c in conds:
        if c.casefold() in seen:
            raise TmtError(f"condition {c} is named twice" + (f" (as {seen[c.casefold()]} and {c})"
                                                               if seen[c.casefold()] != c else "")
                           + "; name each condition once and give its channels with replicates")
        seen[c.casefold()] = c
    if isinstance(replicates, (list, tuple)) and len(replicates) not in (1, len(conds)):
        raise TmtError(f"replicates: {len(replicates)} numbers for {len(conds)} condition"
                       f"{'s' if len(conds) != 1 else ''}; give one number, or one per condition")
    try:
        if isinstance(replicates, dict):
            n = [int(replicates.get(c, 0)) for c in conds]
        elif isinstance(replicates, (list, tuple)):
            n = [int(x) for x in replicates] * (len(conds) if len(replicates) == 1 else 1)
        else:
            n = [int(replicates)] * len(conds)
    except (TypeError, ValueError):
        raise TmtError(f"replicates: {replicates!r} is not a whole number (give one, or one per condition: 3, 4)")             from None
    few = [(c, k) for c, k in zip(conds, n, strict=True) if k < 1]
    if few:
        raise TmtError(f"replicates: at least 1 per condition ({few[0][0]} has {few[0][1]})")
    raw_refs = [x for r in _refs(reference) for x in re.split(r"[,;\s]+", r) if x]   # "126,127N" is two
    unknown = [r for r in raw_refs if channel_key(r) not in lt.channels
               or not re.fullmatch(r"(?:tmt|itraq)?\d{3}(?:nd|cd|[ncd])?", r, re.I)]
    if unknown:
        raise TmtError(f"reference {', '.join(unknown)} is not a channel of {lt.name} ({' '.join(lt.channels)})")
    refs = [channel_key(r) for r in raw_refs]
    twice = sorted({r for r in refs if refs.count(r) > 1})
    if twice:
        raise TmtError(f"reference {', '.join(twice)} is named twice; a channel holds one sample")
    need = sum(n) + len(refs)
    if need > lt.n:
        fit = lt.n - len(refs)
        raise TmtError(f"{lt.name} has {lt.n} channels; {' + '.join(map(str, n))} replicates"
                       + (f" + {len(refs)} reference" if refs else "") + f" need {need}, and {fit} samples fit"
                       + (" beside the reference" if refs else "")
                       + (f" ({fit // len(conds)} per condition for {len(conds)} conditions)" if len(conds) > 1 else ""))
    out = {ch: "NA" for ch in lt.channels}
    for r in refs:
        out[r] = f"{reference_name}_{plex}_{r}"
    free = [ch for ch in lt.channels if ch not in refs]
    k = 0
    for c, reps in zip(conds, n, strict=True):
        for _ in range(reps):
            out[free[k]] = f"{c}_{plex}_{free[k]}"
            k += 1
    return out


def to_overrides_block(maps: dict[str, dict[str, str]] | dict[str, str], label: str = "", reference=None) -> dict:
    """{plex: {channel: name}} (or one {channel: name}) -> the dict for experiment.yaml `tmt:`."""
    block: dict = {"tag": fragpipe_label(label)} if fragpipe_label(label) else {}
    if maps and all(isinstance(v, dict) for v in maps.values()):
        if len(maps) == 1:
            block["channels"] = dict(next(iter(maps.values())))
        else:
            block["plexes"] = {p: {"channels": dict(m)} for p, m in maps.items()}
    else:
        block["channels"] = dict(maps)
    refs = _refs(reference)
    if refs:
        block["reference_channel"] = refs[0] if len(refs) == 1 else refs
    return block


# -------------------------------------------------------------------- cli --


def _load_cfg(path):
    try:
        from ionomos.config import load

        return load(path, check_paths=False)
    except Exception as exc:  # noqa: BLE001 - the commands work without a lab config
        print(f"(no lab config: {exc}; reading the folder only)")
        return None


class NotTmt(TmtError):
    """The folder is not a TMT job, or Ionomos cannot tell what it is (`ionomos tmt` exits 2 with it)."""


def _record(folder: Path) -> dict:
    import json

    from ionomos import names

    try:
        rec = json.loads(names.status_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _filed_method(rec: dict) -> str:
    return str(((rec.get("plan") or {}).get("folder") or {}).get("method") or "")


def method_kind_of(method: str, cfg=None) -> str:
    """What a method behaves as (Config.kind, D54); the built-in rules without a lab config."""
    if cfg is not None:
        return cfg.kind(method)
    from ionomos.naming import method_kind

    return method_kind(method)


def require_tmt(folder: Path, method: str = "", source: str = "", cfg=None, given: str | None = None,
                unknown_ok: bool = False) -> None:
    """Raises NotTmt unless the job is TMT: `method` is what the folder says (ionomos.json, or intake's reading of
    the folder, named by `source`); `given` is --method, which wins. A --method the lab config does not have is
    refused too, rather than another method being used in its place. unknown_ok: nothing says what the job is ->
    read it as TMT (the editor, opened on a TMT experiment) instead of refusing (the command line)."""
    from ionomos import names

    if given and cfg is not None and cfg.methods and given not in cfg.methods:
        raise TmtError(f"--method {given} is not one of the lab's methods ({', '.join(cfg.methods)})")
    m = given or method
    if not m and unknown_ok:
        return
    if not m:
        raise NotTmt(f"can't tell whether {Path(folder).name} is a TMT job: it has no {names.STATUS_FILE} saying "
                     f"what it was filed as" + ("" if cfg is not None else " and there is no lab config to read "
                                                                           "its name with")
                     + "; if it is TMT, say so with --method TMT")
    kind = method_kind_of(m, cfg)
    if kind != "TMT":
        raise NotTmt(f"this is a {kind} job, not TMT (method {m}, from {'--method' if given else source}); "
                     f"`ionomos tmt` reads TMT jobs only")


def plan_for_folder(folder: Path, cfg=None, method: str | None = None, unknown_ok: bool = True) -> TmtPlan:
    """tmtkit.plan for a folder on disk: a filed experiment (its ionomos.json manifest) or a drop (read as intake
    would read it, without moving anything). The workflow comes from the lab config when there is one. Raises
    NotTmt (require_tmt) when the job is not TMT (ionomos.json's method, intake's reading, or `method`); when nothing
    says what it is, too, unless unknown_ok (`ionomos tmt` passes False)."""
    from ionomos import names
    from ionomos.manifest import load_overrides

    folder = Path(folder)
    given = method
    rec = _record(folder)
    lines = list((rec.get("plan") or {}).get("manifest") or [])
    filed, source = _filed_method(rec), names.STATUS_FILE
    intake_said = ""
    if not lines and cfg is not None:
        try:
            from ionomos.intake import plan as intake_plan

            p = intake_plan(folder, cfg)
            lines = [{"file": m.file, "experiment": m.experiment, "bioreplicate": m.bioreplicate} for m in p.manifest]
            if not filed and p.folder.method:
                filed, source = p.folder.method, "how intake reads the folder"
        except Exception as exc:  # noqa: BLE001 - say why and fall back to the file names
            intake_said = f"(intake would not take this folder as it is: {exc})"
    try:
        require_tmt(folder, filed, source, cfg, given, unknown_ok)
    except NotTmt as exc:
        raise NotTmt(f"{exc} {intake_said}".strip()) from None
    if intake_said:
        print(intake_said)
    method = given or filed
    ov = load_overrides(folder)
    if not lines:
        from ionomos.intake import _raw_places
        from ionomos.naming import NamingError, parse_raw_name

        raw_dir, raw_names, _o, subdirs = _raw_places(folder)
        for n in raw_names:
            try:
                sample = parse_raw_name(n, "TMT").sample
            except NamingError:
                sample = Path(n).stem
            sub = subdirs.get(n) or raw_dir
            lines.append({"file": f"{sub}/{n}" if sub else n, "experiment": subdirs.get(n) or sample,
                          "bioreplicate": 1})
    props, wf_name = {}, ""
    if cfg is not None:
        from ionomos.fragpipe import _find_file, read_properties

        method = method if method in cfg.methods else next((k for k in cfg.methods if cfg.kind(k) == "TMT"), None)
        if method:
            wf_name = ov.workflow or cfg.methods[method].workflow
            wf = _find_file(wf_name, cfg.workflow_dir, ".workflow") if wf_name else None
            if wf is not None:
                props = read_properties(wf.read_text(encoding="utf-8", errors="replace"))
            else:
                print(f"(workflow {wf_name} not found in {cfg.workflow_dir}: the label type is not checked)")
    raw_dir = folder / (rec.get("plan") or {}).get("raw_dir", "") if (rec.get("plan") or {}).get("raw_dir") else folder
    return plan(folder, ov, props, lines, raw_dir, wf_name)


def add_parser(sub):
    """`ionomos tmt ...`: the TMT channel map before a search, and the TMT QC after it (D91)."""
    p = sub.add_parser("tmt", help="TMT channel maps: what Ionomos will write and check, a map from a few "
                                   "answers, and the TMT QC of a finished search",
                       description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="tmt_cmd", required=True)
    pl = s.add_parser("plan", help="what Ionomos will write for a TMT folder, and every check (read only)")
    method_help = ("the lab's method key, e.g. TMT (default: what the folder was filed as, or what its name says); "
                   "a job that is not TMT is refused")
    pl.add_argument("folder")
    pl.add_argument("--method", default=None, help=method_help)
    pl.add_argument("--json", action="store_true")
    an = s.add_parser("annotation", help="print the annotation file(s) from experiment.yaml (write with --write, "
                                         "never over an existing file)")
    an.add_argument("folder")
    an.add_argument("--method", default=None, help=method_help)
    an.add_argument("--write", action="store_true", help="write each annotation.txt that is not there yet")
    sg = s.add_parser("suggest", help="a tmt: block for experiment.yaml from a few answers")
    sg.add_argument("--tag", required=True, help="the label type: TMT-6, TMT-10, TMT-11, TMT-16, TMT-18, TMT-35, "
                                                 "iTRAQ-4, iTRAQ-8 ... (FragPipe's names)")
    sg.add_argument("--conditions", required=True, help="comma-separated, e.g. DMSO,Drug")
    sg.add_argument("--replicates", default="3", help="channels per condition: one number or one per condition (3,4)")
    sg.add_argument("--reference", default="", help="the bridge / pool channel(s), e.g. 126")
    sg.add_argument("--plexes", default="", help="several plexes: their names, comma-separated (plexA,plexB)")
    qc = s.add_parser("qc", help="the TMT QC of a finished search: channels, labelling, purity (read only)")
    qc.add_argument("folder")
    qc.add_argument("--method", default=None, help=method_help)
    qc.add_argument("--json", action="store_true")
    p.set_defaults(fn=main)
    return p


def _replicates_arg(text) -> int | list[int]:
    """--replicates: '3' or '3,4' (one per condition). Raises TmtError."""
    parts = [x.strip() for x in str(text).split(",") if x.strip()]
    try:
        reps = [int(x) for x in parts]
    except ValueError:
        raise TmtError(f"--replicates {text}: give a whole number, or one per condition (3,4)") from None
    if not reps:
        raise TmtError("--replicates is empty: give a whole number, or one per condition (3,4)")
    return reps if len(reps) > 1 else reps[0]


def main(args) -> int:
    import json

    import yaml

    if args.tmt_cmd == "suggest":
        conds = [c for c in args.conditions.split(",") if c.strip()]
        refs = [x.strip() for x in args.reference.split(",") if x.strip()]
        plexes = [x.strip() for x in args.plexes.split(",") if x.strip()]
        try:
            reps = _replicates_arg(args.replicates)
            twice = sorted({px for px in plexes if plexes.count(px) > 1})
            if twice:
                raise TmtError(f"plex {', '.join(twice)} is named twice in --plexes; each plex is its own TMT set")
            maps = {px: suggest_channels(args.tag, conds, reps, refs, plex=k + 1)
                    for k, px in enumerate(plexes or ["plex"])}
        except TmtError as exc:
            print(f"error: {exc}")
            return 2
        block = to_overrides_block(maps, args.tag, refs or None)
        print("# paste into experiment.yaml (docs/NAMING_CONVENTION.md, the tmt: block)")
        print(yaml.safe_dump({"tmt": block}, sort_keys=False, allow_unicode=True), end="")
        return 0
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"error: not a folder: {folder}")
        return 2
    if args.tmt_cmd == "qc":
        from ionomos import names
        from ionomos.downstream import tmtqc

        try:
            with contextlib.redirect_stdout(io.StringIO()):  # --json prints JSON only; the kind needs no lab config
                cfg = _load_cfg(args.config)
            require_tmt(folder, _filed_method(_record(folder)), names.STATUS_FILE, cfg, args.method)
        except TmtError as exc:
            print(f"error: {exc}")
            return 2
        res = tmtqc.run_folder(folder)
        if args.json:
            print(json.dumps(tmtqc.summary(res), indent=2, default=str))
        else:
            print("\n".join(tmtqc.text(res)))
        return 0
    cfg = _load_cfg(args.config)
    try:
        tp = plan_for_folder(folder, cfg, args.method, unknown_ok=False)
    except Exception as exc:  # noqa: BLE001 - a broken experiment.yaml is said, not a traceback
        print(f"error: {exc}")
        return 2
    if args.tmt_cmd == "plan":
        if args.json:
            print(json.dumps(tp.as_dict(), indent=2, default=str))
        else:
            print("\n".join(tp.summary()))
        return 1 if tp.errors() else 0
    # annotation
    if not tp.annotations:
        print("nothing to write: " + ("; ".join(f.text for f in tp.flags if f.code in ("TMT_NO_MAP",
                                                                                      "TMT_SHARED_FOLDER"))
                                      or "every plex already has its annotation file"))
    base = folder
    for target, text in tp.annotations.items():
        path = Path(target) if Path(target).is_absolute() else base / target
        print(f"--- {_rel(path, folder)}")
        print(text, end="")
        if args.write:
            try:
                with open(path, "x", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                print(f"(written: {path})")
            except FileExistsError:
                print(f"(not written: {path} exists and is never replaced)")
    for f in tp.errors():
        print(f"ERROR {f.code}: {f.line()}")
    return 1 if tp.errors() else 0
