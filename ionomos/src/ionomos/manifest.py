"""
experiment.yaml (overrides) + FragPipe manifest / TMT annotation writers.

experiment.yaml lives inside the dropped folder. It is optional, and is also
what the GUI resolver writes when a human fixes a naming problem, so a
decision made once is remembered if the folder is re-dropped.

    method: TMT                      # override keyword match
    user: Isaac                      # override initials match (must be a users_root folder)
    date: 2026-09-02
    workflow: TMT10-MS3-phospho      # file under workflow_dir (extension optional)
    fasta: human_2025-01_decoys.fas
    allow_uneven_fractions: false    # accept reps with different fraction sets
    files:                           # per-file overrides; keys are file names as dropped
      KL6159A_1_1.raw: {experiment: plex1, bioreplicate: 1, fraction: 1}   # numbers 1-999; fraction -1 = none
    tmt:
      tag: TMT-10
      channels: {126: DMSO_126, 127N: DMSO_127N, ...}     # one plex
      # or, several plexes:  plexes: {plex1: {channels: {...}}, plex2: {...}}
      reference_channel: 126         # the pooled / bridge channel for IRS across plexes (= analysis.tmt_reference)
      # tag: FragPipe's label type, TMT-10 | TMT-16 | iTRAQ-4 ... (tmtkit.LABEL_TYPES); a plex may set its reference:
    notes: free text
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ionomos.config import read_yaml_text
from ionomos.naming import NUMBER_WHY, NamingError, RawName, RawSet, number_ok

EXPERIMENT_YAML = "experiment.yaml"

_TOP_KEYS = {"method", "user", "date", "workflow", "fasta", "allow_uneven_fractions", "files", "tmt", "notes",
             "resolved_by", "analysis"}
_FILE_KEYS = {"experiment", "bioreplicate", "fraction"}


class OverridesError(ValueError):
    """experiment.yaml is malformed. User-facing message."""


class NumberOutOfRange(OverridesError):
    """A files: replicate or fraction number outside 1-999 (D85). The naming window can correct it, so intake opens
    the window for it instead of only leaving a note."""


@dataclass
class FileOverride:
    experiment: str | None = None
    bioreplicate: int | None = None
    fraction: int | None = None  # -1 => explicitly single-shot (clear fraction)


@dataclass
class Overrides:
    method: str | None = None
    user: str | None = None
    date: date | None = None
    workflow: str | None = None
    fasta: str | None = None
    allow_uneven_fractions: bool = False
    files: dict[str, FileOverride] = field(default_factory=dict)
    tmt: dict = field(default_factory=dict)
    notes: str | None = None
    resolved_by: str | None = None  # "gui" when a human set these
    analysis: dict = field(default_factory=dict)  # per-experiment analysis settings (comparisons, control, ...)
    # a window's TMT channel editor showed the whole map (tmt_editor.py): save_overrides writes ov.tmt as the whole
    # tmt: block, and {} removes it. Not written to the file; False = an existing tmt: is kept unless ov.tmt is set
    tmt_set: bool = field(default=False, compare=False)

    def is_empty(self) -> bool:
        return not any([self.method, self.user, self.date, self.workflow, self.fasta,
                        self.allow_uneven_fractions, self.files, self.tmt])

    def to_dict(self) -> dict:
        d: dict = {}
        for k in ("method", "user", "workflow", "fasta", "notes", "resolved_by"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.date:
            d["date"] = self.date.isoformat()
        if self.allow_uneven_fractions:
            d["allow_uneven_fractions"] = True
        if self.files:
            d["files"] = {
                name: {k: v for k, v in (("experiment", f.experiment), ("bioreplicate", f.bioreplicate),
                                         ("fraction", f.fraction)) if v is not None}
                for name, f in self.files.items()
            }
        if self.tmt:
            d["tmt"] = self.tmt
        if self.analysis:
            d["analysis"] = self.analysis
        return d


def _int_or_none(v, what: str) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise OverridesError(f"{what} must be an integer, got {v!r}") from None


def _number_or_none(v, what: str, single_shot: bool = False) -> int | None:
    """A replicate or fraction number: 1-999 like a file name's (D30, D85), or -1 for "no fraction" when
    single_shot. Anything else is refused with why: the value would reach FragPipe's manifest."""
    n = _int_or_none(v, what)
    if n is None or number_ok(n) or (single_shot and n == -1):
        return n
    allowed = "1-999, or -1 for a single-shot file" if single_shot else "1-999"
    raise NumberOutOfRange(f"{what} is {n}, but it must be {allowed}: {NUMBER_WHY}. Set it to the file's "
                         f"{'fraction' if single_shot else 'replicate'} number (1, 2, 3 ...)")


def _require_path_safe(value: str, what: str) -> str:
    """Reject path-like values and sanitize the rest — nothing read from
    experiment.yaml ever reaches a path join raw (audit L1). Path-like values
    are refused, not rewritten: a silently altered user: could file an
    experiment under a different user's folder."""
    from ionomos.naming import sanitize  # local import avoids cycle at module load

    if "/" in value or "\\" in value or not value.isprintable():
        raise OverridesError(f"{what} must be a name, not a path: {value!r}")
    try:
        return sanitize(value)
    except NamingError as exc:
        raise OverridesError(f"{what}: {exc}") from exc


def _unknown_keys(unknown: set, known: set, where: str) -> str:
    """'unknown key(s) in <where>: methd (did you mean method?) — the keys it can have: ...': what is wrong and
    what to write instead, for a hand-edited file."""
    import difflib

    def one(k) -> str:
        close = difflib.get_close_matches(str(k).lower(), sorted(known), n=1, cutoff=0.6)
        return f"{k} (did you mean {close[0]}?)" if close else str(k)

    return (f"unknown key(s) in {where}: {', '.join(one(k) for k in sorted(unknown, key=str))} — the keys it can "
            f"have: {', '.join(sorted(known))} (docs/NAMING_CONVENTION.md)")


def _name_value(data: dict, key: str) -> str | None:
    """A key that holds one name (method, user, workflow, fasta): a list or a mapping is refused, not turned into
    text that happens to sanitise to a real user's folder."""
    v = data.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, (list, tuple, dict, set)):
        raise OverridesError(f"{key} must be one name, not a {'list' if isinstance(v, (list, tuple, set)) else 'mapping'}"
                             f" ({v!r}) — e.g. {key}: {_EXAMPLE[key]}")
    return str(v) if v else None


_EXAMPLE = {"method": "isoDTB", "user": "EJQ", "workflow": "isoDTB.workflow", "fasta": "human_reviewed_decoys.fas"}


_TRUE = {"true", "yes", "y", "on", "1"}
_FALSE = {"false", "no", "n", "off", "0", ""}


def _flag(v, key: str) -> bool:
    """A yes/no key. bool('false') is True, so a quoted 'false' or 'no' would have switched the check off."""
    if v is None or isinstance(v, bool):
        return bool(v)
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise OverridesError(f"{key} must be true or false, got {v!r}")


def parse_overrides(data: dict | None) -> Overrides:
    data = data or {}
    if not isinstance(data, dict):
        raise OverridesError("experiment.yaml must be a mapping")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise OverridesError(_unknown_keys(unknown, _TOP_KEYS, "experiment.yaml"))

    ov = Overrides()
    ov.method = _name_value(data, "method")
    user = _name_value(data, "user")
    ov.user = _require_path_safe(user, "user") if user else None
    ov.workflow = _name_value(data, "workflow")
    ov.fasta = _name_value(data, "fasta")
    ov.notes = str(data["notes"]) if data.get("notes") else None
    ov.resolved_by = str(data["resolved_by"]) if data.get("resolved_by") else None
    ov.allow_uneven_fractions = _flag(data.get("allow_uneven_fractions", False), "allow_uneven_fractions")

    d = data.get("date")
    if d:
        if isinstance(d, date):
            ov.date = d
        else:
            try:
                ov.date = date.fromisoformat(str(d))
            except ValueError:
                raise OverridesError(f"date must be YYYY-MM-DD, got {d!r}") from None

    files = data.get("files") or {}
    if not isinstance(files, dict):
        raise OverridesError("files: must be a mapping of filename -> {experiment, bioreplicate, fraction}")
    for name, spec in files.items():
        if not isinstance(spec, dict):
            raise OverridesError(f"files.{name} must be a mapping")
        bad = set(spec) - _FILE_KEYS
        if bad:
            raise OverridesError(_unknown_keys(bad, _FILE_KEYS, f"files.{name}"))
        experiment = str(spec["experiment"]) if spec.get("experiment") else None
        ov.files[str(name)] = FileOverride(
            experiment=_require_path_safe(experiment, f"files.{name}.experiment") if experiment else None,
            bioreplicate=_number_or_none(spec.get("bioreplicate"), f"files.{name}.bioreplicate"),
            fraction=_number_or_none(spec.get("fraction"), f"files.{name}.fraction", single_shot=True),
        )

    an = data.get("analysis") or {}
    if an:
        if not isinstance(an, dict):
            raise OverridesError("analysis: must be a mapping (e.g. comparisons: [\"Drug vs DMSO\"])")
        from ionomos.downstream.analysis import AnalysisError, settings_from

        try:
            settings_from(an)
        except AnalysisError as exc:
            raise OverridesError(f"analysis: {exc}") from exc
        ov.analysis = an

    tmt = data.get("tmt") or {}
    if tmt:
        from ionomos.tmtkit import check_block  # the label types and the map rules live there (D91)

        problems = check_block(tmt)
        if problems:
            raise OverridesError("; ".join(problems))
        ov.tmt = tmt
    return ov


def load_overrides(folder: Path) -> Overrides:
    """Read <folder>/experiment.yaml; empty Overrides if absent."""
    p = Path(folder) / EXPERIMENT_YAML
    if not p.is_file():
        return Overrides()
    text = read_yaml_text(p, error=OverridesError)
    data = _safe_load(text)
    if isinstance(data, dict) and data.get("tmt"):
        from ionomos.tmtkit import repeated_keys

        twice = repeated_keys(text)   # YAML keeps the last of two equal keys: the first name would vanish (#156)
        if twice:
            raise OverridesError(f"tmt: {', '.join(twice)} is written twice in {EXPERIMENT_YAML} (YAML keeps only the "
                                 f"last one); list every channel once")
    return parse_overrides(data)


def _safe_load(text: str):
    """yaml.safe_load, with its two ways of failing as OverridesError: not YAML, and a date YAML reads as one but
    that does not exist (date: 2026-02-30 raises a bare ValueError inside PyYAML, which used to reject the folder
    as 'an unexpected problem — send diagnostics' and crash the naming window's draft)."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OverridesError(f"{EXPERIMENT_YAML} is not valid YAML: {exc}") from exc
    except (ValueError, OverflowError) as exc:
        raise OverridesError(f"{EXPERIMENT_YAML} has a date that does not exist ({exc}); write it as YYYY-MM-DD, "
                             f"e.g. date: 2026-09-02") from exc


def save_overrides(folder: Path, ov: Overrides, replace_analysis: bool = False, replace_files: bool = False) -> Path:
    """Write experiment.yaml (merging over an existing one, new values win). The analysis: block is merged key by
    key too (the review window's control keeps saved comparisons), unless replace_analysis: then ov.analysis is
    the whole block, so a choice taken back in the experiment editor (a role back to automatic, a sample used
    again) is gone from the file too. replace_files: ov.files is the whole files: block (the naming window shows
    every raw, so its answer is the full picture; an old entry it was opened for, such as a replicate outside 1-999,
    is not merged back in, D85)."""
    p = Path(folder) / EXPERIMENT_YAML
    p.write_text(overrides_text(folder, ov, replace_analysis, replace_files), encoding="utf-8")
    return p


def overrides_text(folder: Path, ov: Overrides, replace_analysis: bool = False, replace_files: bool = False) -> str:
    """What save_overrides would write to <folder>/experiment.yaml, without writing it (the assistant's proposals
    show the difference before anything is saved, D75)."""
    p = Path(folder) / EXPERIMENT_YAML
    existing: dict = {}
    if p.is_file():
        try:
            existing = yaml.safe_load(read_yaml_text(p, error=OverridesError)) or {}  # unreadable: raise, keep it
        except OverridesError:
            raise
        except (yaml.YAMLError, ValueError, OverflowError):  # ValueError: a date that does not exist (2026-02-30)
            existing = {}
        if not isinstance(existing, dict):  # a list or a bare word: nothing to merge over
            existing = {}
    merged = {**existing, **ov.to_dict()}
    if replace_files:
        if ov.files:
            merged["files"] = ov.to_dict()["files"]
        else:
            merged.pop("files", None)
    elif isinstance(existing.get("files"), dict) and ov.files:
        merged["files"] = {**existing["files"], **ov.to_dict()["files"]}
    if replace_analysis:
        if ov.analysis:
            merged["analysis"] = dict(ov.analysis)
        else:
            merged.pop("analysis", None)
    elif isinstance(existing.get("analysis"), dict) and ov.analysis:  # a new control keeps saved comparisons
        merged["analysis"] = {**existing["analysis"], **ov.analysis}
    if ov.tmt_set and not ov.tmt:  # the channel editor was cleared: its answer is "no map"
        merged.pop("tmt", None)
    header = "# Written by ionomos. Edit freely; keys are documented in docs/NAMING_CONVENTION.md\n"
    return header + yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)


def backup_experiment_yaml(folder: Path) -> Path | None:
    """Copy <folder>/experiment.yaml to <folder>/experiment-backups/experiment-<time>.yaml (names.py) before it is
    rewritten. A backup is created exclusively: never replaced, never removed. None when there is no file yet."""
    from datetime import datetime

    from ionomos import names

    p = Path(folder) / EXPERIMENT_YAML
    if not p.is_file():
        return None
    data = p.read_bytes()
    d = Path(folder) / names.EXPERIMENT_BACKUP_DIR
    d.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for n in range(1000):
        dest = d / (f"experiment-{stamp}.yaml" if n == 0 else f"experiment-{stamp}-{n}.yaml")
        try:
            with open(dest, "xb") as fh:
                fh.write(data)
            return dest
        except FileExistsError:
            continue
    raise OSError(f"no free backup name in {d}")


def save_analysis(folder: Path, analysis: dict, lab: dict | None = None) -> tuple[Path | None, Path | None]:
    """Save an experiment's whole analysis: block: the experiment editor's Save, and a change the assistant proposed
    once a person pressed Confirm (D75), so both are checked and backed up the same way. The block is checked
    against the lab's settings (raises AnalysisError) and the file must read (raises OverridesError); the version it
    replaces is kept in experiment-backups/ first. Returns (experiment.yaml, the backup), or (None, None) when the
    file already says this."""
    from ionomos.downstream.analysis import settings_from

    settings_from(lab, analysis)
    ov = load_overrides(folder)
    if ov.analysis == analysis:
        return None, None
    ov.analysis = dict(analysis)
    backup = backup_experiment_yaml(folder)
    return save_overrides(folder, ov, replace_analysis=True), backup


# ------------------------------------------------------------ apply overrides --


def apply_file_overrides(raws: RawSet, ov: Overrides) -> RawSet:
    """Return a new RawSet with per-file overrides applied and layout rebuilt."""
    if not ov.files:
        return raws
    from ionomos.naming import group_from_parsed  # local import avoids cycle at module load

    files: list[RawName] = []
    unknown = set(ov.files) - {r.filename for r in raws.files} - {r.safe_filename for r in raws.files}
    if unknown:
        raise OverridesError(f"files: not in folder: {', '.join(sorted(unknown))}")
    for r in raws.files:
        fo = ov.files.get(r.filename) or ov.files.get(r.safe_filename)
        if not fo:
            files.append(r)
            continue
        frac = r.fraction if fo.fraction is None else (None if fo.fraction < 0 else fo.fraction)
        files.append(RawName(
            filename=r.filename, safe_filename=r.safe_filename,
            sample=fo.experiment or r.sample,
            rep=fo.bioreplicate if fo.bioreplicate is not None else r.rep,
            fraction=frac,
        ))
    try:
        return group_from_parsed(files, raws.method, allow_uneven=ov.allow_uneven_fractions)
    except NamingError as exc:
        raise OverridesError(str(exc)) from exc


# ------------------------------------------------------------- FragPipe files --


def fp_manifest_text(lines: list[tuple[str, str, int, str]]) -> str:
    """lines = [(absolute raw path, experiment, bioreplicate, DDA|DIA)] -> .fp-manifest content.

    Tab-separated, forward slashes (FragPipe is happiest that way even on Windows).
    """
    out = []
    for path, exp, rep, dtype in lines:
        out.append(f"{str(path).replace(chr(92), '/')}\t{exp}\t{rep}\t{dtype}\n")
    return "".join(out)


def tmt_annotation_files(ov: Overrides, experiments: list[str]) -> dict[str, str]:
    """{experiment (plex) name -> annotation.txt content}.

    Single-plex `tmt.channels` applies to every experiment; multi-plex
    `tmt.plexes` must name each experiment. Content is FragPipe's
    '<channel>\\t<sample>' per line.
    """
    from ionomos.tmtkit import TmtError, annotation_text, maps_from_block

    try:
        maps = maps_from_block(ov.tmt or {}, experiments)  # e.g. only reference_channel: no annotation
    except TmtError as exc:
        raise OverridesError(str(exc)) from exc
    return {exp: annotation_text(ch) for exp, ch in maps.items()}
