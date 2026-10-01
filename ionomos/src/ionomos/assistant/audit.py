"""
The assistant's audit log: one JSON line per question, appended to <app-data>/assistant-audit.jsonl.

    append(record, path=None)        # never raises; path defaults to default_path()
    read(path=None)                  # the records, oldest first (a damaged line is skipped)

A record says what was asked, which model answered (and the digest of the prompt it was given), every tool
call with a hash of its arguments, which citations were accepted or refused, and what was shown. `proposals`
and `confirmed` are always empty until ROADMAP Phase 6.2 adds actions. The file is only ever appended to;
Ionomos never rewrites or trims it. Its name lives in names.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from ionomos import names

log = logging.getLogger("ionomos.assistant")


def default_path() -> Path:
    from ionomos.service import appdata_dir

    return appdata_dir() / names.ASSISTANT_AUDIT_FILE


def digest(value) -> str:
    """sha256 of a string, bytes, or the canonical JSON of anything else."""
    if isinstance(value, str):
        value = value.encode("utf-8")
    elif not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def append(record: dict, path: Path | str | None = None) -> Path | None:
    try:
        p = Path(path) if path is not None else default_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"), **record},
                          ensure_ascii=False, default=str)
        with open(p, "a", encoding="utf-8") as fh:  # one write per record: lines from two processes don't mix
            fh.write(line + "\n")
        return p
    except OSError as exc:
        log.warning("could not write the assistant's audit log: %s", exc)
        return None


def read(path: Path | str | None = None) -> list[dict]:
    try:
        text = (Path(path) if path is not None else default_path()).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out
