"""
Configuration loading and validation. Nothing else in the package reads YAML.

    cfg = load("C:/Fragpipe_Auto/config.yaml")            # validates paths exist
    cfg = load(path, check_paths=False)                    # for dry-run / tests

Paths with spaces are an error on Windows (FragPipe rule on the real PC) and a
warning elsewhere (so a testbed can live under "~/Code Projects/").
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from ionomos.naming import DEFAULT_METHOD_ALIASES

# users_root subfolders that are never people: FragPipe copies, FASTA stores, Explorer's "New folder"
DEFAULT_USER_IGNORE = ("FragPipe*", "Fasta*", "New folder*", "~*")


class ConfigError(ValueError):
    """config.yaml is missing, malformed, or points at things that don't exist."""


@dataclass(frozen=True)
class MethodConfig:
    key: str
    workflow: str
    fasta: str
    data_type: str  # DDA | DIA
    postprocess: tuple[str, ...]
    aliases: tuple[str, ...]
    extra: dict = field(default_factory=dict)  # method-specific knobs (e.g. isodtb_mod_mass)


@dataclass(frozen=True)
class Config:
    inbox: Path
    users_root: Path
    fragpipe_exe: Path
    workflow_dir: Path
    fasta_dir: Path
    database: Path
    log_dir: Path

    poll_seconds: float
    stable_seconds: float
    min_raw_files: int
    group_loose_files: bool

    auto_run: bool
    threads: int
    ram_gb: int
    timeout_minutes: int
    min_free_gb: float
    config_tools_folder: str
    config_diann: str

    methods: dict[str, MethodConfig]
    user_aliases: dict[str, list[str]]
    default_user: str  # "" => reject folders with no recognisable user
    learned_aliases_file: Path  # aliases the GUI was told to remember
    gui_enabled: bool
    gui_timeout_seconds: float  # 0 => wait forever for an answer
    config_path: Path
    analysis: dict = field(default_factory=dict)  # downstream settings (see downstream/analysis.py); "enabled" too
    user_ignore: tuple[str, ...] = DEFAULT_USER_IGNORE  # users_root subfolders that aren't people (glob patterns)
    warnings: tuple[str, ...] = ()  # non-fatal path problems (FragPipe bits missing, etc.)
    gui_popups: bool = True  # pop-up windows for analysis decisions / failed searches (attention.py)

    @property
    def method_aliases(self) -> dict[str, list[str]]:
        return {k: list(m.aliases) for k, m in self.methods.items()}

    def known_users(self) -> list[str]:
        """Users = subfolders of users_root (a new user is just a new folder), minus not_users()."""
        return [u for u in _subfolders(self.users_root) if not_a_user(u, self.user_ignore) is None]

    def ignored_user_folders(self) -> dict[str, str]:
        """Subfolders of users_root that are not treated as people, with the reason."""
        out = {}
        for u in _subfolders(self.users_root):
            why = not_a_user(u, self.user_ignore)
            if why:
                out[u] = why
        return out


def _subfolders(root: Path) -> list[str]:
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def not_a_user(name: str, patterns: tuple[str, ...] = DEFAULT_USER_IGNORE) -> str | None:
    """Why a users_root subfolder isn't a person, or None if it is one."""
    import fnmatch

    if " " in name:
        return "has a space (FragPipe can't use such a path)"
    for pat in patterns:
        if fnmatch.fnmatch(name.lower(), pat.lower()):
            return f"matches users.ignore '{pat}'"
    return None


_REQUIRED_PATHS = ("inbox", "users_root", "fragpipe_exe", "workflow_dir", "fasta_dir", "database", "log_dir")


def _get(d: dict, section: str, key: str, default=None, required=False):
    sec = d.get(section)
    if not isinstance(sec, dict):
        if required:
            raise ConfigError(f"missing section '{section}:'")
        return default
    if key not in sec:
        if required:
            raise ConfigError(f"missing '{section}.{key}'")
        return default
    return sec[key]


_SPACE_WARNINGS: list[str] = []


def _path(section: str, key: str, value) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{section}.{key}' must be a non-empty path string")
    if " " in value:
        msg = f"'{section}.{key}' contains a space ({value!r}); FragPipe cannot handle that"
        if os.name == "nt":
            raise ConfigError(msg)
        _SPACE_WARNINGS.append(msg + " (tolerated off-Windows for testing)")
    return Path(value)


def load(path: str | Path, check_paths: bool = True) -> Config:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{p} must be a YAML mapping")

    _SPACE_WARNINGS.clear()
    paths = {k: _path("paths", k, _get(raw, "paths", k, required=True)) for k in _REQUIRED_PATHS}
    space_warnings = list(_SPACE_WARNINGS)

    methods_raw = raw.get("methods")
    if not isinstance(methods_raw, dict) or not methods_raw:
        raise ConfigError("'methods:' must list at least one method")
    methods: dict[str, MethodConfig] = {}
    for key, m in methods_raw.items():
        if not isinstance(m, dict):
            raise ConfigError(f"'methods.{key}' must be a mapping")
        for req in ("workflow", "fasta", "data_type"):
            if not m.get(req):
                raise ConfigError(f"'methods.{key}.{req}' is required")
        if m["data_type"] not in ("DDA", "DIA"):
            raise ConfigError(f"'methods.{key}.data_type' must be DDA or DIA")
        aliases = m.get("aliases") or DEFAULT_METHOD_ALIASES.get(key) or [key.lower()]
        extra = {k: v for k, v in m.items() if k not in ("workflow", "fasta", "data_type", "postprocess", "aliases")}
        methods[key] = MethodConfig(
            key=key,
            workflow=str(m["workflow"]),
            fasta=str(m["fasta"]),
            data_type=m["data_type"],
            postprocess=tuple(m.get("postprocess") or ()),
            aliases=tuple(str(a) for a in aliases),
            extra=extra,
        )

    users = raw.get("users") or {}
    user_aliases = {str(k): [str(a) for a in (v or [])] for k, v in (users.get("aliases") or {}).items()}
    learned_file = Path(users.get("learned_aliases_file") or (p.parent / "learned_aliases.yaml"))
    for k, v in _read_learned(learned_file).items():
        user_aliases.setdefault(k, [])
        user_aliases[k] += [a for a in v if a not in user_aliases[k]]
    gui = raw.get("gui") or {}

    cfg = Config(
        **paths,
        poll_seconds=float(_get(raw, "watcher", "poll_seconds", 10)),
        stable_seconds=float(_get(raw, "watcher", "stable_seconds", 60)),
        min_raw_files=int(_get(raw, "watcher", "min_raw_files", 1)),
        group_loose_files=bool(_get(raw, "watcher", "group_loose_files", True)),
        auto_run=bool(_get(raw, "fragpipe", "auto_run", True)),
        threads=int(_get(raw, "fragpipe", "threads", 8)),
        ram_gb=int(_get(raw, "fragpipe", "ram_gb", 16)),
        timeout_minutes=int(_get(raw, "fragpipe", "timeout_minutes", 240)),
        min_free_gb=float(_get(raw, "fragpipe", "min_free_gb", 20) or 0),
        config_tools_folder=str(_get(raw, "fragpipe", "config_tools_folder", "") or ""),
        config_diann=str(_get(raw, "fragpipe", "config_diann", "") or ""),
        methods=methods,
        user_aliases=user_aliases,
        default_user=str(users.get("default") or ""),
        learned_aliases_file=learned_file,
        gui_enabled=bool(gui.get("enabled", True)),
        gui_timeout_seconds=float(gui.get("timeout_minutes", 0) or 0) * 60,
        gui_popups=bool(gui.get("popups", True)),
        config_path=p,
        analysis=_analysis(raw.get("analysis")),
        user_ignore=tuple(str(x) for x in (users.get("ignore") if users.get("ignore") is not None else DEFAULT_USER_IGNORE)),
    )

    warnings = space_warnings
    if check_paths:
        errors, more = _check_paths(cfg)
        if errors:
            raise ConfigError("config problems:\n  - " + "\n  - ".join(errors))
        warnings += more
    return replace(cfg, warnings=tuple(warnings)) if warnings else cfg


def _analysis(raw) -> dict:
    """analysis: section, validated by the same code that uses it (typos fail loudly at load time)."""
    from ionomos.downstream.analysis import AnalysisError, settings_from

    if raw is None:
        return {"enabled": True}
    if not isinstance(raw, dict):
        raise ConfigError("'analysis:' must be a mapping")
    try:
        settings_from(raw)
    except AnalysisError as exc:
        raise ConfigError(f"analysis: {exc}") from exc
    return {"enabled": True, **raw}


def _read_learned(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return {str(k): [str(a) for a in (v or [])] for k, v in data.items()} if isinstance(data, dict) else {}


def remember_alias(cfg: Config, user: str, alias: str) -> None:
    """Persist 'alias means user' (from the GUI) so future drops resolve without asking."""
    data = _read_learned(cfg.learned_aliases_file)
    data.setdefault(user, [])
    if alias not in data[user]:
        data[user].append(alias)
    cfg.learned_aliases_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.learned_aliases_file.write_text(
        "# Aliases learned from the ionomos resolver window. user: [alias, ...]\n" + yaml.safe_dump(data),
        encoding="utf-8",
    )
    cfg.user_aliases.setdefault(user, [])
    if alias not in cfg.user_aliases[user]:
        cfg.user_aliases[user].append(alias)


def _norm(p: Path) -> Path:
    try:
        return Path(os.path.normcase(os.path.abspath(p)))
    except (OSError, ValueError):
        return Path(p)


def _inside(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or p in c.parents


def layout_problems(inbox: Path, users_root: Path, log_dir: Path, database: Path) -> list[str]:
    """Folder layouts that would make ionomos act on its own files. Always errors."""
    out = []
    if _norm(inbox) == _norm(users_root):
        out.append("paths.inbox and paths.users_root are the same folder: filed experiments would be picked up "
                   "again as new drops. Use two different folders.")
    elif _inside(users_root, inbox):
        out.append(f"paths.users_root ({users_root}) is inside the inbox: every user folder would look like a "
                   f"new drop. Put them side by side.")
    elif _inside(inbox, users_root):
        out.append(f"paths.inbox ({inbox}) is inside the users folder: it would look like a user. "
                   f"Put them side by side (e.g. C:/Fragpipe_Auto/inbox and C:/Fragpipe_General).")
    for name, p in (("log_dir", log_dir), ("database", database.parent)):
        if _inside(p, inbox):
            out.append(f"paths.{name} ({p}) is inside the inbox; move it out (the inbox must only hold drops)")
    return out


def _check_paths(cfg: Config) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block Phase 1; warnings only matter for FragPipe runs."""
    problems: list[str] = []
    warnings: list[str] = []
    for name in ("inbox", "users_root"):
        if not getattr(cfg, name).is_dir():
            problems.append(f"paths.{name}: folder does not exist: {getattr(cfg, name)}")
    for name in ("database", "log_dir"):
        parent = getattr(cfg, name) if name == "log_dir" else getattr(cfg, name).parent
        if not parent.is_dir():
            problems.append(f"paths.{name}: parent folder does not exist: {parent}")
    problems += layout_problems(cfg.inbox, cfg.users_root, cfg.log_dir, cfg.database)
    if cfg.default_user and not (cfg.users_root / cfg.default_user).is_dir():
        problems.append(f"users.default: folder does not exist: {cfg.users_root / cfg.default_user}")
    # FragPipe bits are only needed from Phase 2 on.
    if not cfg.fragpipe_exe.is_file():
        warnings.append(f"paths.fragpipe_exe not found: {cfg.fragpipe_exe} (needed to run searches)")
    for m in cfg.methods.values():
        if not (cfg.workflow_dir / m.workflow).is_file():
            warnings.append(f"methods.{m.key}.workflow not found: {cfg.workflow_dir / m.workflow}")
    return problems, warnings
