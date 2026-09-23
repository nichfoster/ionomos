"""Shared fixture: a fake C:\\Fragpipe_Auto + C:\\Fragpipe_General layout in a temp dir."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("LABWATCH_OFFLINE", "1")  # the app must not git-fetch during tests
import yaml

from labwatch.config import load


@pytest.fixture
def lab(tmp_path: Path):
    auto = tmp_path / "Fragpipe_Auto"
    general = tmp_path / "Fragpipe_General"
    for d in (auto / "inbox", auto / "workflows", auto / "fasta", auto / "logs", general):
        d.mkdir(parents=True)
    for u in ("EJQ", "Isaac", "Chris", "Aman", "Taylor_Elements", "_unsorted"):
        (general / u).mkdir()
    (auto / "workflows" / "isoDTB.workflow").write_text("x")
    cfg = {
        "paths": {
            "inbox": str(auto / "inbox"),
            "users_root": str(general),
            "fragpipe_exe": str(auto / "fragpipe.exe"),  # absent -> warning only
            "workflow_dir": str(auto / "workflows"),
            "fasta_dir": str(auto / "fasta"),
            "database": str(auto / "labwatch.db"),
            "log_dir": str(auto / "logs"),
        },
        "watcher": {"poll_seconds": 0.01, "stable_seconds": 0.05, "min_raw_files": 1},
        "fragpipe": {"threads": 4, "ram_gb": 8, "timeout_minutes": 10, "min_free_gb": 0},
        "methods": {
            "isoDTB": {"workflow": "isoDTB.workflow", "fasta": "h.fas", "data_type": "DDA",
                       "postprocess": ["isodtb_sites"], "isodtb_mod_mass": "561.3387"},
            "TMT": {"workflow": "TMT10-MS3.workflow", "fasta": "h.fas", "data_type": "DDA"},
            "DIA": {"workflow": "DIA.workflow", "fasta": "h.fas", "data_type": "DIA"},
        },
        "users": {"aliases": {"Isaac": ["IJ", "IJD"], "EJQ": ["EJQ_2"]}},
    }
    cfg_path = auto / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    return {"root": tmp_path, "auto": auto, "general": general, "inbox": auto / "inbox",
            "cfg_path": cfg_path, "cfg_dict": cfg, "cfg": load(cfg_path)}


def make_drop(inbox: Path, name: str, raws: list[str], others: list[str] = (), raw_sub: bool = False):
    d = inbox / name
    d.mkdir()
    base = d / "raw" if raw_sub else d
    base.mkdir(exist_ok=True)
    for r in raws:
        (base / r).write_bytes(b"\0" * 64)
    for o in others:
        (d / o).write_text("note")
    return d


def iso_raws(reps=(1, 2, 3), fracs=range(1, 8), prefix="EJQ_PK_EJQ-2-027_isoDTB_1uM_3h"):
    return [f"{prefix}_{r}_{f}.raw" for r in reps for f in fracs]
