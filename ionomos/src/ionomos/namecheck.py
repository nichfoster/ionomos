"""
"Test your names": how the live config reads folder and raw-file names (D37). Read-only.

    readings = check_names(["2026-09-30__jdoe__DIA__liver", "WT-a_rep1.raw"], cfg)
    print(format_readings(readings))

Each argument is a folder name, a .raw file name, or a folder on disk (its name and its .raw
files). File names that follow a folder name are read as that folder's files, with its method.
Used by `ionomos names test` and the app's Methods tab; nothing is moved or written.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ionomos.config import Config
from ionomos.naming import (
    RAW_SUFFIX,
    NamingError,
    build_user_lookup,
    find_date,
    find_method,
    find_user,
    group_raws,
    parse_raw_name,
    sanitize,
)


@dataclass
class FileReading:
    name: str
    method: str = ""  # the method it was read as ("" = none could be chosen)
    sample: str = ""
    rep: int | None = None
    fraction: int | None = None
    error: str = ""
    alternatives: list[FileReading] = field(default_factory=list)  # no method known: one reading per method

    @property
    def ok(self) -> bool:
        return not self.error and (bool(self.method) or any(a.ok for a in self.alternatives))


@dataclass
class FolderReading:
    name: str
    safe: str = ""
    user: str = ""
    user_note: str = ""
    user_error: str = ""
    method: str = ""
    method_error: str = ""
    rule: str = ""  # the method's file rule as configured (template or regex)
    kind: str = ""  # what the method is searched and analysed as (Config.kind), when not its own name
    date: date | None = None
    files: list[FileReading] = field(default_factory=list)
    layout: list[str] = field(default_factory=list)
    layout_error: str = ""
    on_disk: bool = False
    has_overrides: bool = False  # experiment.yaml present: its values win over the names

    @property
    def ok(self) -> bool:
        return (not self.user_error and not self.method_error and not self.layout_error
                and bool(self.safe) and all(f.ok for f in self.files))


def _read_file(name: str, method: str, cfg: Config) -> FileReading:
    try:
        r = parse_raw_name(name, method, cfg.condition_codes, cfg.file_rules)
    except NamingError as exc:
        return FileReading(name, method, error=str(exc))
    return FileReading(name, method, r.sample, r.rep, r.fraction)


def _file(name: str, method: str | None, cfg: Config) -> FileReading:
    if method:
        return _read_file(name, method, cfg)
    try:
        return _read_file(name, find_method(name[: -len(RAW_SUFFIX)], cfg.method_aliases), cfg)
    except NamingError:
        pass
    alts = [_read_file(name, m, cfg) for m in cfg.methods if m in cfg.file_rules]
    return FileReading(name, alternatives=alts,
                       error="" if alts else "no method has a file rule (naming.methods)")


def _raws_on_disk(folder: Path) -> list[str]:
    names = []
    for base in (folder, folder / "raw"):
        if base.is_dir():
            names += [p.name for p in base.iterdir() if p.is_file() and p.name.lower().endswith(RAW_SUFFIX)]
    return sorted(names)


def _folder(name: str, raws: list[str], method: str | None, cfg: Config) -> FolderReading:
    fr = FolderReading(name)
    try:
        fr.safe = sanitize(name)
    except NamingError as exc:
        fr.method_error = str(exc)
        return fr
    if method:
        fr.method = method
    else:
        try:
            fr.method = find_method(name, cfg.method_aliases, fallback_names=raws)
        except NamingError as exc:
            fr.method_error = str(exc)
    rule = cfg.file_rules.get(fr.method) if fr.method else None
    fr.rule = rule.source if rule else ""
    if fr.method and cfg.kind(fr.method) != fr.method:
        fr.kind = cfg.kind(fr.method)
    users = build_user_lookup(cfg.known_users(), cfg.user_aliases)
    try:
        fr.user = find_user(name, users, cfg.method_aliases)
    except NamingError as exc:
        if cfg.default_user and "no known user" in str(exc):
            fr.user, fr.user_note = cfg.default_user, "no user recognised: filed under users.default"
        else:
            fr.user_error = str(exc)
    fr.date = find_date(name, formats=cfg.date_formats)
    return fr


def _layout(fr: FolderReading, cfg: Config) -> None:
    if not fr.files or not fr.method or any(f.error for f in fr.files):
        return
    try:
        rs = group_raws([f.name for f in fr.files], fr.method, codes=cfg.condition_codes, rules=cfg.file_rules)
    except NamingError as exc:
        fr.layout_error = str(exc)
        return
    for sample, reps in sorted(rs.layout.items()):
        fracs = next(iter(reps.values()))
        shape = f"{len(fracs)} fraction(s) each" if fracs else "single-shot"
        fr.layout.append(f"{sample}: {len(reps)} replicate(s) ({', '.join(map(str, reps))}), {shape}")


def check_names(names: list[str], cfg: Config, method: str | None = None) -> list[FolderReading | FileReading]:
    """Read each name with cfg's rules. method: read .raw names as this method (a key of cfg.methods)."""
    out: list[FolderReading | FileReading] = []
    current: FolderReading | None = None
    for arg in names:
        arg = arg.strip()
        if not arg:
            continue
        p = Path(arg)
        if p.is_dir():
            if current is not None:
                _layout(current, cfg)
            raws = _raws_on_disk(p)
            current = _folder(p.name, raws, method, cfg)
            current.on_disk = True
            current.has_overrides = (p / "experiment.yaml").is_file()
            current.files = [_file(r, current.method or method, cfg) for r in raws]
            out.append(current)
        elif arg.lower().endswith(RAW_SUFFIX):
            name = p.name or arg
            if current is not None:
                current.files.append(_file(name, current.method or method, cfg))
            else:
                out.append(_file(name, method, cfg))
        else:
            if current is not None:
                _layout(current, cfg)
            current = _folder(p.name or arg, [], method, cfg)
            out.append(current)
    if current is not None:
        _layout(current, cfg)
    return out


def names_from_text(text: str) -> list[str]:
    """The app's box: one name per line (folder names may contain spaces)."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def report_from_data(data: dict, text: str, config_path: Path) -> str:
    """The app's Test names button: read the names with the settings being edited (saved or not).

    The candidate config is written next to config_path, as the app's Save does, so relative
    settings resolve the same way; it is removed again."""
    from ionomos import configio
    from ionomos.config import ConfigError, load

    probe = Path(config_path).with_name(f".{Path(config_path).stem}.names.yaml")
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(configio.dump_config(data), encoding="utf-8")
        cfg = load(probe, check_paths=False)
    except (ConfigError, OSError) as exc:
        return f"config problem: {exc}\n"
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    names = names_from_text(text)
    if not names:
        return "type a folder name and/or .raw file names, one per line\n"
    return format_readings(check_names(names, cfg))


def _reading(f: FileReading) -> str:
    if f.error:
        return f"✗ {f.error}"
    frac = f"fraction {f.fraction}" if f.fraction is not None else "no fraction"
    return f"sample {f.sample} · replicate {f.rep} · {frac}"


def _file_lines(f: FileReading, indent: str) -> list[str]:
    if not f.alternatives:
        return [f"{indent}{f.name}   {_reading(f)}" + (f"   [{f.method}]" if f.method and not f.error else "")]
    lines = [f"{indent}{f.name}   (no method keyword in it: pass --method, or put the folder name first)"]
    lines += [f"{indent}  as {a.method:<8} {_reading(a)}" for a in f.alternatives]
    return lines


def format_readings(readings: list[FolderReading | FileReading]) -> str:
    """Plain text, one block per folder / loose file, in the style of `ionomos dry-run`."""
    lines: list[str] = []
    for r in readings:
        if isinstance(r, FileReading):
            lines.append(("OK      " if r.ok else "REJECT  ") + "file")
            lines += _file_lines(r, "  ")
            lines.append("")
            continue
        lines.append(("OK      " if r.ok else "REJECT  ") + f"folder {r.name}")
        if r.safe and r.safe != r.name:
            lines.append(f"  renamed to : {r.safe}")
        lines.append(f"  user       : {r.user or '✗ ' + r.user_error}" + (f"  ({r.user_note})" if r.user_note else ""))
        lines.append(f"  method     : {r.method or '✗ ' + r.method_error}"
                     + (f"  (searched and analysed as {r.kind})" if r.kind else ""))
        if r.rule:
            lines.append(f"  file rule  : {r.rule}")
        lines.append(f"  date       : {r.date.isoformat() if r.date else '(none in the name: the drop date is used)'}")
        if r.has_overrides:
            lines.append("  note       : experiment.yaml is present; its values win (see `ionomos dry-run`)")
        if r.on_disk and not r.files:
            lines.append("  files      : ✗ no .raw files (top level or raw/)")
        if r.files:
            lines.append("  files      :")
            for f in r.files:
                lines += _file_lines(f, "    ")
        if r.layout_error:
            lines.append(f"  layout     : ✗ {r.layout_error}")
        elif r.layout:
            lines.append("  layout     :")
            lines += [f"    {x}" for x in r.layout]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
