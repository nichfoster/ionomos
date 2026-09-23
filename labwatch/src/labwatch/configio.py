"""
Read/write config.yaml as a plain dict, with comments preserved on write.

The GUI edits a nested dict (`ConfigData`) and calls `dump_config` to produce
a commented YAML file; `labwatch.config.load` is then used to validate it.
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
        root = DEFAULT_ROOT_WIN if os.name == "nt" else str(Path.home() / "labwatch")
    if users_root is None:
        users_root = DEFAULT_USERS_WIN if os.name == "nt" else str(Path.home() / "labwatch" / "Fragpipe_General")
    root = root.replace("\\", "/").rstrip("/")
    users_root = users_root.replace("\\", "/").rstrip("/")
    return {
        "paths": {
            "inbox": f"{root}/inbox",
            "users_root": users_root,
            "fragpipe_exe": "C:/FragPipe/FragPipe-24.0/fragpipe/bin/fragpipe.bat",
            "workflow_dir": f"{root}/workflows",
            "fasta_dir": f"{root}/fasta",
            "database": f"{root}/labwatch.db",
            "log_dir": f"{root}/logs",
        },
        "watcher": {"poll_seconds": 10, "stable_seconds": 60, "min_raw_files": 1},
        "fragpipe": {"auto_run": True, "threads": 28, "ram_gb": 48, "timeout_minutes": 240, "config_tools_folder": "",
                     "config_diann": ""},
        "gui": {"enabled": True, "timeout_minutes": 0},
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
    return _merge(defaults(), raw)


def _y(v) -> str:
    """One YAML scalar/flow value."""
    return yaml.safe_dump(v, default_flow_style=True, allow_unicode=True).strip().removesuffix("\n...")


def dump_config(d: dict) -> str:
    p, w, f, g, u = d["paths"], d["watcher"], d["fragpipe"], d["gui"], d["users"]
    L: list[str] = []
    a = L.append
    a("# labwatch configuration. Edit here or with the LabWatch app (labwatch setup).")
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
    a(f"  timeout_minutes: {_y(f['timeout_minutes'])}")
    a(f"  config_tools_folder: {_y(f.get('config_tools_folder', '') or '')}   # only if FragPipe can't find its tools")
    a(f"  config_diann: {_y(f.get('config_diann', '') or '')}   # DIA only, path to DiaNN.exe if needed")
    a("")
    a("gui:")
    a(f"  enabled: {_y(bool(g.get('enabled', True)))}   # resolver window on naming problems")
    a(f"  timeout_minutes: {_y(g.get('timeout_minutes', 0))}   # 0 = wait for a person")
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


def write_config(path: str | Path, d: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(dump_config(d), encoding="utf-8")
    os.replace(tmp, p)
    return p
