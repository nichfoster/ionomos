"""Recoverable inbox removal. Hidden storage is never ingested by the watcher."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from ionomos import names
from ionomos.intake import note_path

REMOVED = names.REMOVED_DIR


def remove(inbox: Path, target: Path) -> Path:
    inbox, target = Path(inbox).resolve(), Path(target).absolute()
    relative = target.relative_to(inbox)
    if not relative.parts or any(p.startswith('.') for p in relative.parts):
        raise ValueError("Select an inbox item")
    if target.is_symlink() or not target.resolve().is_relative_to(inbox):
        raise ValueError("Cannot remove a link outside the inbox")
    if (inbox / REMOVED).is_symlink():
        raise ValueError("Removed-items storage must not be a symbolic link")
    destination = inbox / REMOVED / uuid.uuid4().hex / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    target.rename(destination)
    note = note_path(target)
    if note.is_file():
        note.rename(destination.parent / note.name)
    logging.getLogger(__name__).info("Removed from inbox: %s -> %s", target, destination)
    return destination
