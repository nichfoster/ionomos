"""
Read/write config.yaml as a plain dict, with comments preserved on write.

The GUI edits a nested dict (`ConfigData`) and calls `dump_config` to produce
a commented YAML file; `ionomos.config.load` is then used to validate it.
Nothing here validates — that stays in config.py.

    data = read_config(path)          # dict (defaults merged in)
    text = dump_config(data)          # commented YAML text
    write_config(path, data)
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULT_ROOT_WIN = "C:/Fragpipe_Auto"
DEFAULT_USERS_WIN = "C:/Fragpipe_General"


def defaults(root: str | None = None, users_root: str | None = None) -> dict:
    """A complete config dict with sensible defaults for the given install root."""
    if root is None:
        root = DEFAULT_ROOT_WIN if os.name == "nt" else str(Path.home() / "ionomos")
    if users_root is None:
        users_root = DEFAULT_USERS_WIN if os.name == "nt" else str(Path.home() / "ionomos" / "Fragpipe_General")
    root = root.replace("\\", "/").rstrip("/")
    users_root = users_root.replace("\\", "/").rstrip("/")
    return {
        "paths": {
            "inbox": f"{root}/inbox",
            "users_root": users_root,
            "fragpipe_exe": "C:/FragPipe/FragPipe-24.0/fragpipe/bin/fragpipe.bat",
            "workflow_dir": f"{root}/workflows",
            "fasta_dir": f"{root}/fasta",
            "database": f"{root}/ionomos.db",
            "log_dir": f"{root}/logs",
        },
        "watcher": {"poll_seconds": 10, "stable_seconds": 60, "min_raw_files": 1},
        "fragpipe": {"auto_run": True, "threads": 28, "ram_gb": 48, "timeout_minutes": 240, "min_free_gb": 20, "config_tools_folder": "",
                     "config_diann": ""},
        "gui": {"enabled": True, "timeout_minutes": 0},
        "analysis": {"enabled": True, "test": "moderated", "log2fc": 1.0, "alpha": 0.05, "use_adjusted": True,
                     "min_valid": 2, "normalize": "median", "top_labels": 15,
                     "control_keywords": ["DMSO", "vehicle", "veh", "ctrl", "control", "mock", "untreated", "NT",
                                          "WT", "EV", "scr", "scramble", "siNT", "PBS"]},
        "users": {"aliases": {}, "default": "", "learned_aliases_file": f"{root}/learned_aliases.yaml"},
        "methods": {
            "isoDTB": {"aliases": ["isodtb", "iso-dtb"], "workflow": "isoDTB.workflow",
                       "fasta": "human_reviewed_decoys.fas", "data_type": "DDA",
                       "postprocess": ["isodtb_sites"], "isodtb_mod_mass": "561.3387"},
            "TMT": {"aliases": ["tmt"], "workflow": "TMT10-MS3.workflow", "fasta": "human_reviewed_decoys.fas",
                    "data_type": "DDA", "postprocess": ["tmt_annotation"]},
            "DIA": {"aliases": ["dia", "diann", "dia-nn"], "workflow": "DIA.workflow",
                    "fasta": "human_reviewed_decoys.fas", "data_type": "DIA", "postprocess": []},
        },
    }


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "methods" and k != "aliases":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def read_config(path: str | Path) -> dict:
    """Raw dict from disk with defaults filled in for missing keys. Missing file -> defaults."""
    p = Path(path)
    if not p.is_file():
        return defaults()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    return _normalise(_merge(defaults(), raw))


def _normalise(d: dict) -> dict:
    """Empty YAML entries ('aliases:' with nothing under it) load as None; give every section its type back."""
    base = defaults()
    for sec, default in base.items():
        if not isinstance(d.get(sec), dict):
            d[sec] = default
    users = d["users"]
    if not isinstance(users.get("aliases"), dict):
        users["aliases"] = {}
    users["aliases"] = {str(k): [str(a) for a in (v or [])] if isinstance(v, (list, tuple)) else ([str(v)] if v else [])
                        for k, v in users["aliases"].items()}
    for k in ("default", "learned_aliases_file"):
        if users.get(k) is None:
            users[k] = ""
    if not d["methods"]:
        d["methods"] = base["methods"]
    for key, m in list(d["methods"].items()):
        if not isinstance(m, dict):
            d["methods"][key] = {}
            m = d["methods"][key]
        for list_key in ("aliases", "postprocess"):
            v = m.get(list_key)
            m[list_key] = [str(x) for x in v] if isinstance(v, (list, tuple)) else ([str(v)] if v else [])
    for sec in ("paths", "fragpipe"):
        for k, v in d[sec].items():
            if v is None:
                d[sec][k] = ""
    return d


def _y(v) -> str:
    """One YAML scalar/flow value."""
    return yaml.safe_dump(v, default_flow_style=True, allow_unicode=True).strip().removesuffix("\n...")


def dump_config(d: dict) -> str:
    p, w, f, g, u = d["paths"], d["watcher"], d["fragpipe"], d["gui"], d["users"]
    L: list[str] = []
    a = L.append
    a("# ionomos configuration. Edit here or with the Ionomos app (ionomos setup).")
    a("# Forward slashes are fine on Windows. NO SPACES in any path (FragPipe rule).")
    a("")
    a("paths:")
    a(f"  inbox:        {_y(p['inbox'])}   # THE drop folder users drag into")
    a(f"  users_root:   {_y(p['users_root'])}   # experiments land in users_root/<user>/<name>")
    a(f"  fragpipe_exe: {_y(p['fragpipe_exe'])}   # headless launcher fragpipe.bat (needed for searches)")
    a(f"  workflow_dir: {_y(p['workflow_dir'])}   # pinned .workflow files, one per method")
    a(f"  fasta_dir:    {_y(p['fasta_dir'])}")
    a(f"  database:     {_y(p['database'])}   # SQLite job ledger")
    a(f"  log_dir:      {_y(p['log_dir'])}")
    a("")
    a("watcher:")
    a(f"  poll_seconds: {_y(w['poll_seconds'])}   # how often the inbox is scanned")
    a(f"  stable_seconds: {_y(w['stable_seconds'])}   # tree must be unchanged this long before intake")
    a(f"  min_raw_files: {_y(w['min_raw_files'])}   # folders with fewer .raw files are left alone")
    a("")
    a("fragpipe:")
    a(f"  auto_run: {_y(bool(f.get('auto_run', True)))}   # run FragPipe on queued jobs automatically")
    a(f"  threads: {_y(f['threads'])}")
    a(f"  ram_gb: {_y(f['ram_gb'])}")
    a(f"  timeout_minutes: {_y(f['timeout_minutes'])}   # 0 = no limit")
    a(f"  min_free_gb: {_y(f.get('min_free_gb', 20))}   # jobs wait while the data drive has less free than this + the raws")
    a(f"  config_tools_folder: {_y(f.get('config_tools_folder', '') or '')}   # only if FragPipe can't find its tools")
    a(f"  config_diann: {_y(f.get('config_diann', '') or '')}   # DIA only, path to DiaNN.exe if needed")
    a("")
    a("gui:")
    a(f"  enabled: {_y(bool(g.get('enabled', True)))}   # resolver window on naming problems")
    a(f"  timeout_minutes: {_y(g.get('timeout_minutes', 0))}   # 0 = wait for a person")
    a("")
    an = d.get("analysis") or {}
    a("analysis:   # statistics + volcano plots + report after each search (per experiment: experiment.yaml analysis:)")
    a(f"  enabled: {_y(bool(an.get('enabled', True)))}")
    a(f"  test: {_y(an.get('test', 'moderated'))}   # moderated (limma-style, recommended) | welch | student")
    a(f"  log2fc: {_y(an.get('log2fc', 1.0))}   # |log2 fold change| needed to call a hit")
    a(f"  alpha: {_y(an.get('alpha', 0.05))}   # significance cut-off")
    a(f"  use_adjusted: {_y(bool(an.get('use_adjusted', True)))}   # true: alpha applies to BH q-values; false: raw p")
    a(f"  min_valid: {_y(an.get('min_valid', 2))}   # values needed per group to test a protein/site")
    a(f"  normalize: {_y(an.get('normalize', 'median'))}   # median | none (intensities only)")
    a(f"  top_labels: {_y(an.get('top_labels', 15))}   # hit names written on each volcano")
    a(f"  control_keywords: {_y(list(an.get('control_keywords') or []))}   # how the control condition is recognised")
    a("")
    a("users:   # users are the subfolders of users_root; aliases map initials -> folder")
    a("  aliases:")
    for user, als in (u.get("aliases") or {}).items():
        a(f"    {_y(user)}: {_y(list(als))}")
    a(f"  default: {_y(u.get('default', '') or '')}   # e.g. \"_unsorted\" to accept unknown users")
    a(f"  learned_aliases_file: {_y(u.get('learned_aliases_file', ''))}")
    a("")
    a("methods:   # key = canonical name; aliases are matched in folder/file names")
    for key, m in d["methods"].items():
        a(f"  {_y(key)}:")
        a(f"    aliases: {_y(list(m.get('aliases') or [key.lower()]))}")
        a(f"    workflow: {_y(m.get('workflow', ''))}")
        a(f"    fasta: {_y(m.get('fasta', ''))}")
        a(f"    data_type: {_y(m.get('data_type', 'DDA'))}")
        a(f"    postprocess: {_y(list(m.get('postprocess') or []))}")
        for k, v in m.items():
            if k not in ("aliases", "workflow", "fasta", "data_type", "postprocess"):
                a(f"    {_y(k)}: {_y(v)}")
    a("")
    return "\n".join(L)


BACKUP_DIR = "config-backups"


def backup_config(path: str | Path, keep: int = 30) -> Path | None:
    """Copy an existing config to <dir>/config-backups/config-<ts>.yaml (newest `keep` kept)."""
    import datetime

    p = Path(path)
    if not p.is_file():
        return None
    d = p.parent / BACKUP_DIR
    d.mkdir(exist_ok=True)
    dest = d / f"config-{datetime.datetime.now():%Y%m%d-%H%M%S}.yaml"
    dest.write_bytes(p.read_bytes())
    for old in sorted(d.glob("config-*.yaml"))[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def write_config(path: str | Path, d: dict, backup: bool = True) -> Path:
    """Atomically write config.yaml; the previous version goes to config-backups/ first."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        try:
            backup_config(p)
        except OSError:
            pass  # a failed backup must not block saving
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(dump_config(d), encoding="utf-8")
    os.replace(tmp, p)
    return p
