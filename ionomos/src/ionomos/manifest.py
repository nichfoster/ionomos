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
      KL6159A_1_1.raw: {experiment: plex1, bioreplicate: 1, fraction: 1}
    tmt:
      tag: TMT-10
      channels: {126: DMSO_126, 127N: DMSO_127N, ...}     # one plex
      # or, several plexes:  plexes: {plex1: {channels: {...}}, plex2: {...}}
    notes: free text
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ionomos.naming import NamingError, RawName, RawSet

EXPERIMENT_YAML = "experiment.yaml"

_TOP_KEYS = {"method", "user", "date", "workflow", "fasta", "allow_uneven_fractions", "files", "tmt", "notes",
             "resolved_by", "analysis"}
_FILE_KEYS = {"experiment", "bioreplicate", "fraction"}


class OverridesError(ValueError):
    """experiment.yaml is malformed. User-facing message."""


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


def parse_overrides(data: dict | None) -> Overrides:
    data = data or {}
    if not isinstance(data, dict):
        raise OverridesError("experiment.yaml must be a mapping")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise OverridesError(f"unknown key(s) in experiment.yaml: {', '.join(sorted(unknown))}")

    ov = Overrides()
    ov.method = str(data["method"]) if data.get("method") else None
    ov.user = _require_path_safe(str(data["user"]), "user") if data.get("user") else None
    ov.workflow = str(data["workflow"]) if data.get("workflow") else None
    ov.fasta = str(data["fasta"]) if data.get("fasta") else None
    ov.notes = str(data["notes"]) if data.get("notes") else None
    ov.resolved_by = str(data["resolved_by"]) if data.get("resolved_by") else None
    ov.allow_uneven_fractions = bool(data.get("allow_uneven_fractions", False))

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
            raise OverridesError(f"files.{name}: unknown key(s) {', '.join(sorted(bad))}")
        experiment = str(spec["experiment"]) if spec.get("experiment") else None
        ov.files[str(name)] = FileOverride(
            experiment=_require_path_safe(experiment, f"files.{name}.experiment") if experiment else None,
            bioreplicate=_int_or_none(spec.get("bioreplicate"), f"files.{name}.bioreplicate"),
            fraction=_int_or_none(spec.get("fraction"), f"files.{name}.fraction"),
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
        if not isinstance(tmt, dict):
            raise OverridesError("tmt: must be a mapping")
        plexes = tmt.get("plexes") or ({"default": tmt} if tmt.get("channels") else {})
        if not plexes:
            raise OverridesError("tmt: needs either channels: {...} or plexes: {name: {channels: {...}}}")
        for plex, spec in plexes.items():
            ch = (spec or {}).get("channels")
            if not isinstance(ch, dict) or not ch:
                raise OverridesError(f"tmt: plex {plex!r} needs a non-empty channels mapping")
        ov.tmt = tmt
    return ov


def load_overrides(folder: Path) -> Overrides:
    """Read <folder>/experiment.yaml; empty Overrides if absent."""
    p = Path(folder) / EXPERIMENT_YAML
    if not p.is_file():
        return Overrides()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise OverridesError(f"{EXPERIMENT_YAML} is not valid YAML: {exc}") from exc
    return parse_overrides(data)


def save_overrides(folder: Path, ov: Overrides) -> Path:
    """Write experiment.yaml (merging over an existing one, new values win)."""
    p = Path(folder) / EXPERIMENT_YAML
    existing: dict = {}
    if p.is_file():
        try:
            existing = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            existing = {}
    merged = {**existing, **ov.to_dict()}
    if "files" in existing and ov.files:
        merged["files"] = {**existing.get("files", {}), **ov.to_dict()["files"]}
    header = "# Written by ionomos. Edit freely; keys are documented in docs/NAMING_CONVENTION.md\n"
    p.write_text(header + yaml.safe_dump(merged, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


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
    tmt = ov.tmt or {}
    if not tmt:
        return {}
    if "plexes" in tmt:
        plexes = tmt["plexes"]
        missing = set(experiments) - set(plexes)
        if missing:
            raise OverridesError(f"tmt.plexes has no entry for experiment(s): {', '.join(sorted(missing))}")
        return {exp: _annot(plexes[exp]["channels"]) for exp in experiments}
    return {exp: _annot(tmt["channels"]) for exp in experiments}


def _annot(channels: dict) -> str:
    return "".join(f"{ch}\t{name}\n" for ch, name in channels.items())
