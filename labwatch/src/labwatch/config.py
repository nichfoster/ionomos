"""
Configuration loading and validation.

Contract (Phase 1):
    load(path) -> Config
        - Parses config.yaml (see ../../config.example.yaml).
        - Validates every `paths.*` entry: exists (or parent exists for
          database/log_dir), and contains NO SPACES (FragPipe rule).
        - Validates each `methods.<KEY>` has workflow/fasta/data_type/postprocess,
          and that workflow_dir/<workflow> and fasta_dir/<fasta> exist.
        - Builds `Config.method_lookup`: {key.lower(): key} for naming.parse_folder_name.
        - Raises ConfigError with a message that names the offending key.

    Config is a frozen dataclass; nothing else in the package reads YAML.

Reuse: reference/prior-work/proteomics-qc-pkg/config_loader.py (path checks).
"""
from __future__ import annotations


class ConfigError(ValueError):
    """config.yaml is missing, malformed, or points at things that don't exist."""


def load(path: str):  # -> Config
    raise NotImplementedError("Phase 1")
