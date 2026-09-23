"""
Loose raw files dropped straight into the inbox (no folder) become experiment folders.

People drag a handful of .raw files instead of a folder. Once the loose files
have stopped changing (the same stability rule as folders), they are grouped
by shared name and each group is MOVED into a new folder in the inbox; from
there the normal folder path takes over (user, method, resolver window, ...).

    CS_22rv1_FLAG-AR_MA25-10uM_DMSO_1.raw  ┐
    CS_22rv1_FLAG-AR_MA25-10uM_DMSO_2.raw  ├─▶ inbox/CS_22rv1_FLAG-AR_MA25-10uM/
    CS_22rv1_FLAG-AR_MA25-10uM_MA25_1.raw  ┘
    EJQ_PK_A_1_1.raw, EJQ_PK_A_1_2.raw     ──▶ inbox/EJQ_PK_A/

Grouping: files sharing at least MIN_SHARED_TOKENS leading name tokens (split
on _ - . and spaces) go together; the folder is named after that shared part,
without trailing replicate/fraction tokens and without an Xcalibur timestamp.
A file that shares nothing with the others gets a folder of its own.

Rules: files are only ever moved (same volume: a rename), never overwritten or
deleted. A folder made here carries a marker (.ionomos-grouped); files that
arrive later with the same name prefix join it instead of starting "…_2".
Non-raw loose files are left where they are.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ionomos.naming import RAW_SUFFIX, sanitize, strip_acq_stamp

log = logging.getLogger("ionomos.loose")

MARKER = ".ionomos-grouped"
MIN_SHARED_TOKENS = 2
_SPLIT = re.compile(r"[_\-.\s]+")
_TRAILING_POSITION = re.compile(r"^(?:\d{1,3}|[Ff]\d{1,3}|[Rr](?:ep)?\d{1,3}|rep|frac|[Ff]raction\d*)$", re.IGNORECASE)  # never method keywords (TMT stays)


def loose_raws(inbox: Path) -> list[Path]:
    try:
        return sorted(p for p in Path(inbox).iterdir()
                      if p.is_file() and p.name.lower().endswith(RAW_SUFFIX) and not p.name.startswith((".", "~$")))
    except OSError:
        return []


def _tokens(stem: str) -> list[str]:
    return [t for t in _SPLIT.split(strip_acq_stamp(stem)) if t]


def _shared_prefix_text(stems: list[str]) -> str:
    """The longest leading part (whole tokens, original separators) that every stem shares."""
    first = strip_acq_stamp(stems[0])
    toks = [_tokens(s) for s in stems]
    n = 0
    while all(len(t) > n for t in toks) and len({t[n].lower() for t in toks}) == 1:
        n += 1
    if n == 0:
        return ""
    # cut the first stem after its n-th token, keeping its own separators
    count, i = 0, 0
    for m in re.finditer(r"[^_\-.\s]+", first):
        count += 1
        if count == n:
            i = m.end()
            break
    return first[:i]


def folder_name_for(stems: list[str]) -> str:
    """Folder name for a group of raw stems: their shared part minus trailing rep/fraction tokens."""
    if len(stems) == 1:
        base = strip_acq_stamp(stems[0])
    else:
        base = _shared_prefix_text(stems) or strip_acq_stamp(stems[0])
    parts = re.split(r"([_\-.\s]+)", base)
    # drop trailing position-like tokens (and their separators) when a group spans several files
    while len(stems) > 1 and len(parts) > 2 and _TRAILING_POSITION.match(parts[-1] or ""):
        parts = parts[:-2]
    name = "".join(parts).strip("_-. ")
    try:
        return sanitize(name) if name else "loose_files"
    except Exception:  # noqa: BLE001 - symbols-only names: let intake explain it later
        return "loose_files"


def group(names: list[str]) -> dict[str, list[str]]:
    """{folder name: [file names]} for loose raw file names."""
    stems = sorted((n[: -len(RAW_SUFFIX)], n) for n in names)
    clusters: list[list[tuple[str, str]]] = []
    for stem, name in stems:
        if clusters:
            head = clusters[-1][0][0]
            shared = 0
            for a, b in zip(_tokens(head), _tokens(stem), strict=False):
                if a.lower() != b.lower():
                    break
                shared += 1
            if shared >= MIN_SHARED_TOKENS:
                clusters[-1].append((stem, name))
                continue
        clusters.append([(stem, name)])
    out: dict[str, list[str]] = {}
    for c in clusters:
        fname = folder_name_for([s for s, _ in c])
        out.setdefault(fname, []).extend(n for _, n in c)
    return out


def _target_folder(inbox: Path, name: str) -> Path:
    """inbox/name, or the grouping folder we made earlier under that name, else name_2, name_3 ..."""
    cand = inbox / name
    k = 2
    while cand.exists():
        if cand.is_dir() and (cand / MARKER).exists():
            return cand  # our own folder from a moment ago: late files join it
        cand = inbox / f"{name}_{k}"
        k += 1
    return cand


def group_loose_files(inbox: Path, files: list[Path] | None = None) -> list[Path]:
    """Move loose raw files into folders. Returns the folders that received files."""
    inbox = Path(inbox)
    files = files if files is not None else loose_raws(inbox)
    if not files:
        return []
    touched: list[Path] = []
    # files belonging to a group made earlier (one was locked, or copied later) join that folder
    ours = [d for d in inbox.iterdir() if d.is_dir() and (d / MARKER).exists()]
    plan: dict[Path, list[str]] = {}
    rest = []
    for f in files:
        ftoks = [t.lower() for t in _tokens(f.name[: -len(RAW_SUFFIX)])]
        home = None
        for d in ours:
            dtoks = [t.lower() for t in _tokens(d.name)]
            if len(dtoks) >= MIN_SHARED_TOKENS and ftoks[: len(dtoks)] == dtoks:
                if home is None or len(d.name) > len(home.name):
                    home = d
        if home is not None:
            plan.setdefault(home, []).append(f.name)
        else:
            rest.append(f.name)
    for folder_name, names in group(rest).items():
        plan.setdefault(_target_folder(inbox, folder_name), []).extend(names)
    for target, names in plan.items():
        target.mkdir(exist_ok=True)
        (target / MARKER).write_text("made by Ionomos from loose files dropped in the inbox\n", encoding="utf-8")
        moved = 0
        for n in names:
            src, dst = inbox / n, target / n
            if dst.exists():
                log.warning("not moving %s: %s already has a file of that name", n, target.name)
                continue
            try:
                os.rename(src, dst)
                moved += 1
            except PermissionError:
                log.info("%s is still open in another program; it joins %s on a later pass", n, target.name)
            except FileNotFoundError:
                pass
        if moved:
            log.info("grouped %d loose raw file(s) into %s", moved, target.name)
            touched.append(target)
    return touched
