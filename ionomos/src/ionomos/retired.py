"""
Keys of config.yaml and experiment.yaml that were renamed or taken away (D133).

docs/VISION.md, "Versioning and support": a key or option that goes away warns for at least one minor release
first. While a key is in RETIRED, a file that still has it loads as before: a renamed key's value is used under its
new name, a removed key is ignored, and each says so in words, with the version that retired it:

    config.yaml      config.load: Config.warnings and Config.retired_keys, so `ionomos check` prints a ! line and
                     the app's checklist marks Settings saved; the watcher logs it when it starts (cli._load).
                     configio.read_config renames too, so the app's Save writes the new key with the old value.
    experiment.yaml  manifest.parse_overrides: Overrides.warnings, which the analysis adds to its notes
                     (postprocess.prepare), and once in the log. manifest.overrides_text renames too (its Save).
    analysis:        downstream.analysis.settings_from, for settings from anywhere else (a job record written
                     before the rename, the command line): once in the log.

    data, said = apply(raw, CONFIG)        # a copy: renamed keys moved, removed ones left out; said: the warnings
    layer, said = analysis(layer)          # the analysis: entries on one settings dict (no "analysis." prefix)

An entry stays at least until the next minor release after `since` has shipped; then it may go, and the key is
unknown from then on (a warning in config.yaml's checked sections, an error in analysis:, naming:, methods: and
experiment.yaml). Values that are accepted under an older spelling (imputation: man, normalize: md) are not keys
and are not here. Nor are the LabWatch-era names (labwatch.json, LABWATCH_CONFIG, ...): they are not keys, and
names.py keeps them working for good (D21).
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass

log = logging.getLogger("ionomos.config")

CONFIG, EXPERIMENT = "config.yaml", "experiment.yaml"


@dataclass(frozen=True)
class Retired:
    key: str                # where it was, dotted, as written in the file: "watcher.poll_secs", "analysis.normalise";
                            # "*" stands for every name of a mapping ("files.*.rep", "methods.*.workflow_file")
    new: str | None         # the key that took its place, in the same file and section; None: nothing did
    since: str              # the version that retired it ("0.21.0")
    message: str = ""       # what to do instead, in plain words (said after the rest)
    files: tuple[str, ...] = (CONFIG,)   # the files it is read from; an "analysis." key: both

    def __post_init__(self):
        if self.new is not None and self.new.split(".")[:-1] != self.key.split(".")[:-1]:
            raise ValueError(f"{self.key} -> {self.new}: a key is renamed within its own section")


# Nothing has been retired yet (2026-10-26, 0.20.0). An entry, for example:
#   Retired("watcher.poll_secs", "watcher.poll_seconds", "0.21.0"),
#   Retired("analysis.normalise", "analysis.normalize", "0.21.0", files=(CONFIG, EXPERIMENT)),
#   Retired("gui.sound", None, "0.21.0", "the app no longer plays a sound"),
RETIRED: tuple[Retired, ...] = ()

_said: set[str] = set()


def _walk(d: dict, parts: list[str], where: list[str]):
    """(the mapping that holds the last part, the dotted path to it) for every match of parts in d."""
    if not isinstance(d, dict):
        return
    head, rest = parts[0], parts[1:]
    names = [k for k in d if isinstance(k, str)] if head == "*" else ([head] if head in d else [])
    for name in names:
        if not rest:
            yield d, where + [name]
        else:
            yield from _walk(d[name], rest, where + [name])


def _apply_one(data: dict, r: Retired, file: str, parts: list[str], prefix: list[str], keep_removed: bool
               ) -> list[str]:
    said = []
    for holder, path in list(_walk(data, parts, [])):
        old = ".".join(prefix + path)
        if path[-1] not in holder:
            continue
        if r.new is None:
            if not keep_removed:
                holder.pop(path[-1])
            said.append(f"{old} was retired in Ionomos {r.since} and is ignored"
                        + (f": {r.message}" if r.message else "") + f". Delete it from {file}.")
            continue
        value = holder.pop(path[-1])
        new_name = r.new.split(".")[-1]
        new = ".".join(prefix + path[:-1] + [new_name])
        if new_name in holder:
            said.append(f"{old} was renamed {new} in Ionomos {r.since}, and both are set: {new} is used, {old} is "
                        f"ignored. Delete {old} from {file}." + (f" {r.message}" if r.message else ""))
        else:
            holder[new_name] = value
            said.append(f"{old} was renamed {new} in Ionomos {r.since}: its value is used as {new} for now. Rename "
                        f"it in {file}; the old name will stop working in a later release."
                        + (f" {r.message}" if r.message else ""))
    return said


def apply(data, file: str, table: tuple[Retired, ...] | None = None, keep_removed: bool = False) -> tuple:
    """(a copy of data with the retired keys of `file` renamed or left out, the warnings: one per key found).
    data that is not a mapping, or a file with nothing retired, is returned as it is, with no warnings.
    keep_removed: a removed key stays where it is (the app's Save never drops a value, D67), and is still said."""
    rows = [r for r in (RETIRED if table is None else table) if file in r.files]
    if not isinstance(data, dict) or not rows:
        return data, []
    out, said = copy.deepcopy(data), []
    for r in rows:
        said += _apply_one(out, r, file, r.key.split("."), [], keep_removed)
    return out, said


def analysis(layer, table: tuple[Retired, ...] | None = None) -> tuple:
    """apply() for one analysis settings dict (the analysis: section's keys, without "analysis."), from anywhere."""
    rows = [r for r in (RETIRED if table is None else table) if r.key.startswith("analysis.")]
    if not isinstance(layer, dict) or not rows:
        return layer, []
    out, said = copy.deepcopy(layer), []
    for r in rows:
        said += _apply_one(out, r, " or ".join(r.files), r.key.split(".")[1:], ["analysis"], False)
    return out, said


def log_once(said: list[str]) -> None:
    """Each warning in the log once per process: experiment.yaml is read again for every analysis."""
    for s in said:
        if s not in _said:
            _said.add(s)
            log.warning("%s", s)
