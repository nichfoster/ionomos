"""Persistent, user/method scoped examples confirmed in the resolver.

Learn sample-label corrections across replicates; retain exact corrections for
unusual tails. Conflicting examples are never silently applied.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict

from ionomos import names
from ionomos.manifest import FileOverride
from ionomos.naming import NamingError, parse_raw_name, strip_acq_stamp

log = logging.getLogger(__name__)


def history_path(cfg):
    return cfg.log_dir / names.NAMING_HISTORY_FILE


def remember(cfg, folder, user, method, files):
    path = history_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"folder": folder, "user": user, "method": method,
                                 "files": [asdict(f) for f in files]}) + "\n")
    log.info("Learned naming from %s: %d confirmed files (%s, %s)", folder, len(files), user, method)


def suggestions(cfg, user, method, filenames):
    path = history_path(cfg)
    if not path.is_file():
        return {}
    exact, samples = {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if record["user"] != user or record["method"] != method:
                continue
            for f in record["files"]:
                value = (f["experiment"], int(f["bioreplicate"]), int(f["fraction"]) if f["fraction"] else -1)
                key = strip_acq_stamp(f["filename"][:-4])
                exact.setdefault(key, set()).add(value)
                try:
                    parsed = parse_raw_name(f["filename"], method)
                except NamingError:
                    continue
                if (parsed.rep, parsed.fraction or -1) == value[1:]:
                    samples.setdefault(parsed.sample, set()).add(value[0])
        except (ValueError, KeyError, TypeError):
            log.warning("Ignoring invalid naming history entry in %s", path)
    result = {}
    for filename in filenames:
        choices = exact.get(strip_acq_stamp(filename[:-4]), set())
        if len(choices) == 1:
            result[filename] = FileOverride(*next(iter(choices)))
        elif not choices:
            try:
                parsed = parse_raw_name(filename, method)
            except NamingError:
                continue
            labels = samples.get(parsed.sample, set())
            if len(labels) == 1:
                result[filename] = FileOverride(next(iter(labels)), parsed.rep, parsed.fraction or -1)
    return result
