"""
Configuration loading and validation. Nothing else in the package reads YAML.

    cfg = load("C:/Fragpipe_Auto/config.yaml")            # validates paths exist
    cfg = load(path, check_paths=False)                    # for dry-run / tests

Paths with spaces are an error on Windows (FragPipe rule on the real PC) and a
warning elsewhere (so a testbed can live under "~/Code Projects/").
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from ionomos.naming import (
    DEFAULT_CONDITION_CODES,
    DEFAULT_DATE_FORMATS,
    DEFAULT_FILE_RULES,
    DEFAULT_FILE_TEMPLATES,
    DEFAULT_METHOD_ALIASES,
    FileRule,
    NamingError,
    check_date_formats,
    check_method_aliases,
    file_rule,
)

log = logging.getLogger("ionomos.config")

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
    review_drops: bool = True  # show every drop in the review window before it is filed (when a display exists)
    condition_codes: dict = field(default_factory=lambda: dict(DEFAULT_CONDITION_CODES))  # DIA X_D1 -> DMSO rep 1
    file_rules: dict[str, FileRule] = field(default_factory=lambda: dict(DEFAULT_FILE_RULES))  # naming.methods
    date_formats: tuple[str, ...] = DEFAULT_DATE_FORMATS  # naming.date_formats
    qc_trend: dict = field(default_factory=dict)  # instrument QC trending (qctrend.py, D45); {} = defaults

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
        engine = str(m.get("engine") or "fragpipe").lower()
        if engine not in ("fragpipe", "diann", "maxquant"):
            raise ConfigError(f"'methods.{key}.engine' must be fragpipe, diann or maxquant")
        for req in (("workflow", "fasta", "data_type") if engine == "fragpipe" else ("fasta", "data_type")):
            if not m.get(req):
                raise ConfigError(f"'methods.{key}.{req}' is required")
        if m["data_type"] not in ("DDA", "DIA"):
            raise ConfigError(f"'methods.{key}.data_type' must be DDA or DIA")
        if engine == "diann" and m["data_type"] != "DIA":
            raise ConfigError(f"'methods.{key}': DIA-NN (engine: diann) needs data_type DIA")
        if engine == "maxquant" and m["data_type"] != "DDA":
            raise ConfigError(f"'methods.{key}': MaxQuant (engine: maxquant) needs data_type DDA")
        aliases = m.get("aliases") or DEFAULT_METHOD_ALIASES.get(key) or [key.lower()]
        extra = {k: v for k, v in m.items() if k not in ("workflow", "fasta", "data_type", "postprocess", "aliases")}
        methods[key] = MethodConfig(
            key=key,
            workflow=str(m.get("workflow") or ""),
            fasta=str(m["fasta"]),
            data_type=m["data_type"],
            postprocess=tuple(m.get("postprocess") or ()),
            aliases=tuple(str(a) for a in aliases),
            extra=extra,
        )
    try:
        check_method_aliases({k: list(m.aliases) for k, m in methods.items()})
    except NamingError as exc:
        raise ConfigError(f"methods: {exc}") from None

    users = raw.get("users") or {}
    user_aliases = {str(k): [str(a) for a in (v or [])] for k, v in (users.get("aliases") or {}).items()}
    learned_file = Path(users.get("learned_aliases_file") or (p.parent / "learned_aliases.yaml"))
    for k, v in _read_learned(learned_file).items():
        user_aliases.setdefault(k, [])
        user_aliases[k] += [a for a in v if a not in user_aliases[k]]
    gui = raw.get("gui") or {}
    naming = raw.get("naming") or {}
    if not isinstance(naming, dict):
        raise ConfigError("'naming:' must be a mapping (condition_codes, date_formats, methods)")
    unknown = sorted(set(naming) - {"condition_codes", "date_formats", "methods"})
    if unknown:
        raise ConfigError(f"naming.{unknown[0]}: unknown setting (known: condition_codes, date_formats, methods)")
    file_rules = _file_rules(naming.get("methods"), methods)
    try:
        date_formats = (DEFAULT_DATE_FORMATS if naming.get("date_formats") is None
                        else check_date_formats(naming["date_formats"]))
    except NamingError as exc:
        raise ConfigError(f"naming.date_formats: {exc}") from None
    codes = naming.get("condition_codes")
    if codes is None:
        codes = dict(DEFAULT_CONDITION_CODES)
    elif not isinstance(codes, dict) or not all(
            re.fullmatch(r"[A-Za-z]{1,8}", str(k)) and str(v).strip() for k, v in codes.items()):
        raise ConfigError("naming.condition_codes must map letter codes to condition names, e.g. {D: DMSO, C: Compound}")
    else:
        codes = {str(k): str(v).strip() for k, v in codes.items()}

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
        review_drops=bool(gui.get("review_drops", True)),
        condition_codes=codes,
        file_rules=file_rules,
        date_formats=date_formats,
        config_path=p,
        analysis=_analysis(raw.get("analysis")),
        qc_trend=_qc_trend(raw.get("qc_trend")),
        user_ignore=tuple(str(x) for x in (users.get("ignore") if users.get("ignore") is not None else DEFAULT_USER_IGNORE)),
    )

    warnings = space_warnings
    if check_paths:
        errors, more = _check_paths(cfg)
        if errors:
            raise ConfigError("config problems:\n  - " + "\n  - ".join(errors))
        warnings += more
    return replace(cfg, warnings=tuple(warnings)) if warnings else cfg


_RULE_KEYS = ("like", "files", "pattern", "condition_codes")


def _file_rules(raw, methods: dict[str, MethodConfig]) -> dict[str, FileRule]:
    """naming.methods: how each method's raw file names are read (D37). Absent = the built-in rules.

    A method's entry is a template string (shorthand for files:), or a mapping of
    like (isoDTB | TMT | DIA), files (template), pattern (regex), condition_codes (true/false)."""
    rules = dict(DEFAULT_FILE_RULES)
    if raw is None:
        return rules
    if not isinstance(raw, dict):
        raise ConfigError("naming.methods must map method names to file rules, e.g. {DIA: '{sample}_{rep}'}")
    for key, spec in raw.items():
        key = str(key)
        where = f"naming.methods.{key}"
        if key not in methods:
            raise ConfigError(f"{where}: there is no methods.{key} (add it with workflow, fasta and data_type, "
                              f"or use one of {', '.join(methods)})")
        if isinstance(spec, str):
            spec = {"files": spec}
        if not isinstance(spec, dict) or not spec:
            raise ConfigError(f"{where}: give files: (a template), pattern: (a regex) or like: "
                              f"{' | '.join(DEFAULT_FILE_TEMPLATES)}")
        bad = sorted(set(map(str, spec)) - set(_RULE_KEYS))
        if bad:
            raise ConfigError(f"{where}.{bad[0]}: unknown setting (known: {', '.join(_RULE_KEYS)})")
        like = spec.get("like")
        if like is not None and str(like) not in DEFAULT_FILE_TEMPLATES:
            raise ConfigError(f"{where}.like: {like!r} is not a built-in method; use "
                              f"{' | '.join(DEFAULT_FILE_TEMPLATES)}")
        use_codes = spec.get("condition_codes")
        if use_codes is not None and not isinstance(use_codes, bool):
            raise ConfigError(f"{where}.condition_codes must be true or false")
        for k in ("files", "pattern"):
            if spec.get(k) is not None and not isinstance(spec[k], str):
                raise ConfigError(f"{where}.{k} must be a text (quote it in YAML: '{{sample}}_{{rep}}')")
        try:
            rules[key] = file_rule(key, like=str(like) if like is not None else None, files=spec.get("files"),
                                   pattern=spec.get("pattern"), codes=use_codes)
        except NamingError as exc:
            raise ConfigError(f"{where}: {exc}") from None
    return rules


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


def _qc_trend(raw) -> dict:
    """qc_trend: section (instrument QC trending, D45), validated by the module that uses it."""
    from ionomos.qctrend import QCTrendError, settings_from

    try:
        return settings_from(raw)
    except QCTrendError as exc:
        raise ConfigError(f"qc_trend.{exc}" if not str(exc).startswith("must") else f"qc_trend: {exc}") from None


def _read_learned(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return {str(k): [str(a) for a in (v or [])] for k, v in data.items()} if isinstance(data, dict) else {}


class LiveConfig:
    """The running watcher's view of config.yaml: re-read whenever it (or the learned-aliases file) changes,
    so a user or alias added in the app counts for the next drop without restarting. A broken file mid-save
    keeps the last good config. Folder paths are read at start (the watcher and ledger are already open)."""

    def __init__(self, cfg: Config, check_paths: bool = True):
        self._cfg = cfg
        self._check = check_paths
        self._stamp = self._mtimes(cfg)
        self._bad: tuple | None = None

    @staticmethod
    def _mtimes(cfg: Config) -> tuple:
        out = []
        for p in (cfg.config_path, cfg.learned_aliases_file):
            try:
                out.append(p.stat().st_mtime_ns)
            except OSError:
                out.append(None)
        return tuple(out)

    def get(self) -> Config:
        stamp = self._mtimes(self._cfg)
        if stamp == self._stamp or stamp == self._bad:
            return self._cfg
        try:
            new = load(self._cfg.config_path, check_paths=self._check)
        except (ConfigError, OSError) as exc:
            if stamp != self._bad:
                log.warning("config changed but can't be read (%s); keeping the previous settings", exc)
            self._bad = stamp
            return self._cfg
        if (new.inbox, new.database, new.log_dir) != (self._cfg.inbox, self._cfg.database, self._cfg.log_dir):
            log.warning("inbox / database / log folder changed in config.yaml: restart the watcher to use them")
            new = replace(new, inbox=self._cfg.inbox, database=self._cfg.database, log_dir=self._cfg.log_dir)
        log.info("config.yaml changed: reloaded (users, aliases, methods, naming, analysis)")
        self._cfg, self._stamp, self._bad = new, stamp, None
        return new


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
