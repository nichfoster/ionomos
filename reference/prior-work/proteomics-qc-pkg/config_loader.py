"""
Loads config.yaml. Every module imports load_config() from here so there is
exactly one place that knows how settings are read.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# config.yaml lives next to this file, at the project root.
_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Read and return the config as a plain dict.

    Pass an explicit path to override the default (useful in tests).
    """
    cfg_path = Path(path) if path is not None else _CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError(f"Config at {cfg_path} did not parse to a mapping.")
    return cfg
