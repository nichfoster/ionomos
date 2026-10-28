"""
Read/write config.yaml as a plain dict, with comments preserved on write.

The GUI edits a nested dict (`ConfigData`) and calls `dump_config` to produce
a commented YAML file; `ionomos.config.load` is then used to validate it.
Nothing here validates — that stays in config.py.

    data = read_config(path)          # dict (defaults merged in)
    text = dump_config(data)          # commented YAML text
    write_config(path, data)
    lines = diff_words(old, new)      # what a Save changes, in words ("FragPipe → Threads: 24 → 28")
    backups(path)                     # config-backups/config-*.yaml, newest first (Undo last save)
"""
from __future__ import annotations

import copy
import datetime
import os
import re
from pathlib import Path

import yaml

from ionomos.config import ConfigError, parse_error, read_yaml_text

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
            "fragpipe_exe": "C:/FragPipe/FragPipe-24.0/bin/fragpipe.bat",
            "workflow_dir": f"{root}/workflows",
            "fasta_dir": f"{root}/fasta",
            "database": f"{root}/ionomos.db",
            "log_dir": f"{root}/logs",
        },
        "watcher": {"poll_seconds": 10, "stable_seconds": 60, "min_raw_files": 1, "group_loose_files": True},
        "fragpipe": {"auto_run": True, "threads": 28, "ram_gb": 48, "timeout_minutes": 240, "min_free_gb": 20, "config_tools_folder": "",
                     "config_diann": "", "config_python": ""},
        "gui": {"enabled": True, "review_drops": True, "timeout_minutes": 0, "popups": True},
        "naming": {"condition_codes": {"D": "DMSO", "C": "Compound"}},
        "analysis": {"enabled": True, "test": "limma", "de_type": "control", "log2fc": 1.0, "alpha": 0.05,
                     "use_adjusted": True, "remove_contaminants": True, "filter_global_pct": 0,
                     "filter_condition_pct": 50, "normalize": "auto", "imputation": "auto", "min_valid": 2,
                     "enrichment": True, "enrichment_libraries": ["Hallmark", "GO Biological Process", "Reactome"],
                     "enrichment_gmt": "", "top_labels": 15,
                     "export": {"size": "slide169", "font_pt": 14, "font_family": "Arial", "palette": "default",
                                "background": "light", "figures": []},
                     "control_keywords": ["DMSO", "vehicle", "veh", "ctrl", "control", "mock", "untreated", "NT",
                                          "WT", "EV", "scr", "scramble", "siNT", "PBS"],
                     "competition_keywords": ["comp", "competition", "competitor", "competed", "compete",
                                              "competing", "excess"]},
        "qc_trend": {"enabled": True, "match": ["hela", "k562", "qc_std", "qcstd", "_qc_"], "exclude": [],
                     "methods": [], "instrument": "", "baseline_runs": 10, "baseline_from": "", "baseline_to": "",
                     "popup": False},
        "notify": copy.deepcopy(_notify_defaults()),
        "library": {"roots": [], "exclude": [], "max_depth": 6, "time_limit_s": 120},
        # no default folder: the lab's Dropbox path has spaces (CLAUDE.md); the app offers the one it finds (D120)
        "atlas": {"enabled": False, "handoff_dir": "", "methods": ["isoDTB"], "pin_max_mb": 100, "max_tries": 10,
                  "retry_minutes": 15, "sweep": False, "sweep_roots": [], "sweep_exclude": [], "sweep_hours": 24,
                  "sweep_min_free_gb": 40},
        "users": {"aliases": {}, "default": "", "learned_aliases_file": f"{root}/learned_aliases.yaml",
                  "ignore": ["FragPipe*", "Fasta*", "New folder*", "~*"]},
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


def _notify_defaults() -> dict:
    from ionomos.notify import DEFAULTS

    return DEFAULTS


def _notify_keys(d: dict) -> dict:
    from ionomos.notify import fix_keys

    return fix_keys(d)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("methods", "aliases", "condition_codes"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def read_config(path: str | Path) -> dict:
    """Raw dict from disk with defaults filled in for missing keys. Missing file -> defaults. A file that does
    not parse raises ConfigError naming the line (#136), the same message `ionomos check` prints."""
    p = Path(path)
    if not p.is_file():
        d = defaults()
        d["users"]["learned_aliases_file"] = (p.parent / "learned_aliases.yaml").as_posix()
        return d
    try:
        raw = yaml.safe_load(read_yaml_text(p)) or {}  # cp1252 (Notepad "ANSI") read with a warning, D86
    except yaml.YAMLError as exc:
        raise ConfigError(parse_error(p, exc)) from exc
    if not isinstance(raw, dict):
        raw = {}
    from ionomos import retired

    # a renamed key is read under its new name, so a Save writes it there with its value; a removed one is kept
    # where it is (a Save never drops a value) and the loader goes on saying so (retired.py, D133)
    raw, _said = retired.apply(raw, retired.CONFIG, keep_removed=True)
    if isinstance(raw.get("notify"), dict):
        raw["notify"] = _notify_keys(raw["notify"])  # a bare `on:` key loads as True
    d = _normalise(_merge(defaults(), raw))
    users_raw = raw.get("users") if isinstance(raw.get("users"), dict) else {}
    if not users_raw.get("learned_aliases_file"):
        # not set: the loader's own default, the file beside this config.yaml (config.py), never the default root's,
        # so that saving a config kept elsewhere (a testbed, D:) does not move where learned aliases live (#149)
        d["users"]["learned_aliases_file"] = (p.parent / "learned_aliases.yaml").as_posix()
    ex =(raw.get("analysis") or {}).get("export") if isinstance(raw.get("analysis"), dict) else None
    if isinstance(ex, dict) and "font_pt" not in ex and isinstance(d["analysis"].get("export"), dict):
        d["analysis"]["export"].pop("font_pt", None)  # not set: the size's own text size (7 pt for col1), not 14
    return d


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
    if d["analysis"].get("sdrf", {}) is None:  # the writer's 'sdrf:' heading with nothing under it
        del d["analysis"]["sdrf"]
    return d


def _y(v) -> str:
    """One YAML scalar/flow value, on one line: PyYAML's default width of 80 folded a long value with a space past
    column 80 onto the next line, where the writer's `# comment` after it made config.yaml unreadable (a Dropbox
    folder in atlas.handoff_dir, D120)."""
    return yaml.safe_dump(v, default_flow_style=True, allow_unicode=True, width=float("inf")).strip() \
        .removesuffix("\n...")


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
    _rest(a, p, _PATHS)
    a("")
    a("watcher:")
    a(f"  poll_seconds: {_y(w['poll_seconds'])}   # how often the inbox is scanned")
    a(f"  stable_seconds: {_y(w['stable_seconds'])}   # tree must be unchanged this long before intake")
    a(f"  min_raw_files: {_y(w['min_raw_files'])}   # folders with fewer .raw files are left alone")
    a(f"  group_loose_files: {_y(bool(w.get('group_loose_files', True)))}   # .raw files dropped without a folder get one")
    _rest(a, w, ("poll_seconds", "stable_seconds", "min_raw_files", "group_loose_files"))
    a("")
    a("fragpipe:")
    a(f"  auto_run: {_y(bool(f.get('auto_run', True)))}   # run FragPipe on queued jobs automatically")
    a(f"  threads: {_y(f['threads'])}")
    a(f"  ram_gb: {_y(f['ram_gb'])}")
    a(f"  timeout_minutes: {_y(f['timeout_minutes'])}   # 0 = no limit")
    a(f"  min_free_gb: {_y(f.get('min_free_gb', 20))}   # jobs wait while the data drive has less free than this + the raws")
    a(f"  config_tools_folder: {_y(f.get('config_tools_folder', '') or '')}   # only if FragPipe can't find its tools")
    a(f"  config_diann: {_y(f.get('config_diann', '') or '')}   # DIA only, path to DiaNN.exe if needed")
    a(f"  config_python: {_y(f.get('config_python', '') or '')}   # only if FragPipe can't find its Python (spectral library)")
    _rest(a, f, ("auto_run", "threads", "ram_gb", "timeout_minutes", "min_free_gb", "config_tools_folder", "config_diann",
                 "config_python"))
    a("")
    a("gui:")
    a(f"  enabled: {_y(bool(g.get('enabled', True)))}   # the naming window (problems, and reviews below)")
    a(f"  review_drops: {_y(bool(g.get('review_drops', True)))}   # show every drop's reading (conditions, replicates, "
      "control) before filing")
    a(f"  timeout_minutes: {_y(g.get('timeout_minutes', 0))}   # 0 = wait for a person; N = a review files as read")
    a(f"  popups: {_y(bool(g.get('popups', True)))}   # windows when an analysis needs a decision or a search fails")
    _rest(a, g, ("enabled", "review_drops", "timeout_minutes", "popups"))
    a("")
    codes = (d.get("naming") or {}).get("condition_codes")
    codes = codes if isinstance(codes, dict) else {"D": "DMSO", "C": "Compound"}
    a("naming:")
    a("  condition_codes:   # DIA short forms: X_D1 = DMSO rep 1; 1-2 letter codes, longer ones only if listed")
    for k, v in codes.items():
        a(f"    {_y(str(k))}: {_y(str(v))}")
    nm = d.get("naming") or {}
    if nm.get("date_formats") is not None:  # another lab's convention (D37); the app doesn't edit these
        a(f"  date_formats: {_y(list(nm['date_formats']))}   # tried in order")
    if isinstance(nm.get("methods"), dict) and nm["methods"]:
        a("  methods:   # how each method's raw file names are read (docs/NAMING_CONVENTION.md)")
        for key, spec in nm["methods"].items():
            if isinstance(spec, dict):
                a(f"    {_y(str(key))}:")
                for k, v in spec.items():
                    a(f"      {_y(str(k))}: {_y(v)}")
            else:
                a(f"    {_y(str(key))}: {_y(spec)}")
    _rest(a, nm, ("condition_codes", "date_formats", "methods"))
    a("")
    an = d.get("analysis") or {}
    a("analysis:   # statistics + volcano plots + report after each search (per experiment: experiment.yaml analysis:)")
    a(f"  enabled: {_y(bool(an.get('enabled', True)))}")
    a("  # statistics follow FragPipe-Analyst / FragPipeAnalystR (filter -> normalise -> impute -> limma)")
    a(f"  test: {_y(an.get('test', 'limma'))}   # limma (moderated t, recommended) | welch | student")
    a(f"  de_type: {_y(an.get('de_type', 'control'))}   # control (each vs the control) | all (every pair) | others")
    a(f"  log2fc: {_y(an.get('log2fc', 1.0))}   # |log2 fold change| needed to call a hit")
    a(f"  alpha: {_y(an.get('alpha', 0.05))}   # significance cut-off")
    a(f"  use_adjusted: {_y(bool(an.get('use_adjusted', True)))}   # true: alpha applies to BH-adjusted p; false: raw p")
    a(f"  remove_contaminants: {_y(bool(an.get('remove_contaminants', True)))}")
    a(f"  filter_global_pct: {_y(an.get('filter_global_pct', 0))}   # keep features measured in >= this % of samples")
    a(f"  filter_condition_pct: {_y(an.get('filter_condition_pct', 50))}   # ... and in >= this % of one condition")
    a(f"  normalize: {_y(an.get('normalize', 'auto'))}   # auto (median; ratio when many features change one way) | "
      "median | gn (median + MAD) | ratio | none")
    a(f"  imputation: {_y(an.get('imputation', 'auto'))}   # auto | none | perseus | min | zero | mindet | minprob | knn")
    a(f"  min_valid: {_y(an.get('min_valid', 2))}   # measured values per group needed when nothing is imputed")
    a(f"  enrichment: {_y(bool(an.get('enrichment', True)))}   # gene-set enrichment of the hits (downloads libraries once)")
    a(f"  enrichment_libraries: {_y(list(an.get('enrichment_libraries') or []))}")
    a(f"  enrichment_gmt: {_y(an.get('enrichment_gmt', '') or '')}   # optional extra gene sets (.gmt file)")
    a(f"  top_labels: {_y(an.get('top_labels', 15))}   # hit names written on each volcano")
    a(f"  control_keywords: {_y(list(an.get('control_keywords') or []))}   # how the control condition is recognised")
    a(f"  competition_keywords: {_y(list(an.get('competition_keywords') or []))}   # a word of a condition name that "
      "means 'plus a competitor' (Probe_Comp): its comparisons follow the design")
    for k in _AN_LISTS:
        if an.get(k) not in (None, ""):
            a(f"  {k}: {_y(list(an[k]) if isinstance(an[k], (list, tuple)) else an[k])}")
    if isinstance(an.get("roles"), dict) and an["roles"]:
        a("  roles:   # condition -> control | compound | competition of <compound> | reference | qc | background "
          "(usually per experiment, in experiment.yaml)")
        for k, v in an["roles"].items():
            a(f"    {_y(str(k))}: {_y(v)}")
    for k in _AN_SCALARS:
        if an.get(k) not in (None, ""):
            a(f"  {k}: {_y(an[k])}")
    if isinstance(an.get("times"), dict) and an["times"]:
        a("  times:   # condition -> time (usually per experiment, in experiment.yaml)")
        for k, v in an["times"].items():
            a(f"    {_y(str(k))}: {_y(v)}")
    if isinstance(an.get("doses"), dict) and an["doses"]:
        a("  doses:   # condition -> dose (usually per experiment, in experiment.yaml)")
        for k, v in an["doses"].items():
            a(f"    {_y(str(k))}: {_y(v)}")
    _rest(a, an, (*_AN_WRITTEN, *_AN_LISTS, *_AN_SCALARS, "sdrf", "export",
                  *(k for k in ("roles", "times", "doses") if isinstance(an.get(k), dict))))
    meta = an.get("sdrf") if isinstance(an.get("sdrf"), dict) else {}
    a("  sdrf:   # results/sdrf.tsv sample metadata: instrument, organism (default: from the FASTA), organism_part, "
      "cell_type, disease, cleavage_agent (default: from the workflow)")
    for k, v in meta.items():
        a(f"    {_y(str(k))}: {_y(str(v))}")
    ex = an.get("export") if isinstance(an.get("export"), dict) else {}
    a("  export:   # exported figures: the lab's style. The report's Export starts from it, `ionomos export` uses it")
    for k, dflt, what in _EXPORT_KEYS:
        if k == "font_pt":  # not set: the text size that suits the size (7 pt for a journal column)
            dflt = _EXPORT_FONT.get(str(ex.get("size", "slide169")), dflt)
        a(f"    {k}: {_y(ex.get(k, dflt))}   # {what}")
    for k, v in ex.items():  # the rest of the style (width, height, unit, up, down, neutral, line_scale, title ...)
        if k not in [x[0] for x in _EXPORT_KEYS]:
            a(f"    {_y(str(k))}: {_y(v)}")
    a("")
    q = d.get("qc_trend") if isinstance(d.get("qc_trend"), dict) else {}
    a("qc_trend:   # instrument QC: runs of the lab's QC standard (HeLa, K562 ...) trended in logs/qc_trend.html")
    a(f"  enabled: {_y(bool(q.get('enabled', True)))}   # does nothing until a QC-standard run is searched")
    a(f"  match: {_y(list(q.get('match') or []))}   # words in a folder or .raw name that mean 'QC standard'")
    a(f"  exclude: {_y(list(q.get('exclude') or []))}   # names with these words are never QC runs")
    a(f"  methods: {_y(list(q.get('methods') or []))}   # every run of these methods is a QC run (a dedicated QC method)")
    a(f"  instrument: {_y(q.get('instrument', '') or '')}   # a label for the page, e.g. Eclipse")
    a(f"  baseline_runs: {_y(q.get('baseline_runs', 10))}   # the first N runs of a series set its mean and SD")
    a(f"  baseline_from: {_y(str(q.get('baseline_from', '') or ''))}   # or pin the baseline to runs acquired "
      "between two dates (YYYY-MM-DD)")
    a(f"  baseline_to: {_y(str(q.get('baseline_to', '') or ''))}")
    a(f"  popup: {_y(bool(q.get('popup', False)))}   # true: a broken rule opens a window (default: the attention list only)")
    if q.get("max_file_mb") not in (None, ""):
        a(f"  max_file_mb: {_y(q['max_file_mb'])}   # larger tables are skipped")
    _rest(a, q, ("enabled", "match", "exclude", "methods", "instrument", "baseline_runs", "baseline_from", "baseline_to",
                 "popup", "max_file_mb"))
    a("")
    lib = d.get("library") if isinstance(d.get("library"), dict) else {}
    a("library:   # where `ionomos library scan` looks for FragPipe workflows and FASTAs (docs/LIBRARY.md)")
    a(f"  roots: {_y(list(lib.get('roots') or []))}   # more folders besides the pinned ones, FragPipe installations "
      "and users_root, e.g. [D:/] (UNC paths work)")
    a(f"  exclude: {_y(list(lib.get('exclude') or []))}   # folder names or path patterns to leave out, e.g. ['*/raw']")
    a(f"  max_depth: {_y(lib.get('max_depth', 6))}   # folders deep below each root")
    a(f"  time_limit_s: {_y(lib.get('time_limit_s', 120))}   # a scan stops after this many seconds and says how far "
      "it got")
    for k, v in lib.items():  # a typo is written back as it was, so the loader can name it
        if k not in ("roots", "exclude", "max_depth", "time_limit_s"):
            a(f"  {_y(str(k))}: {_y(v)}")
    a("")
    n = d.get("notify") if isinstance(d.get("notify"), dict) else {}
    n = _merge(_notify_defaults(), {k: ({"url": v} if isinstance(v, str) and k in ("webhook", "teams", "slack") else v)
                                    for k, v in _notify_keys(n).items() if v is not None})
    a("notify:   # a message when a search is done, failed or waiting. Off by default: nothing leaves this PC")
    a("          # unless you turn it on. What is sent: docs/ARCHITECTURE.md \"Notifications\". Test: ionomos notify-test")
    a("          # or the app's Notifications tab (8), which edits this block")
    a(f"  enabled: {_y(n['enabled'])}")
    a(f"  on: {_y(n['on'])}   # which of done, failed, held (= waiting) send a message")
    a(f"  include_names: {_y(n['include_names'])}   # false: only the job number and status are sent")
    a(f"  timeout_seconds: {_y(n['timeout_seconds'])}   # a channel that doesn't answer is given up on; jobs never wait")
    for name, what in (("webhook", "any service that takes a JSON POST"),
                       ("teams", "a Microsoft Teams webhook (Workflows) address"),
                       ("slack", "a Slack incoming-webhook address")):
        c = n[name] if isinstance(n[name], dict) else {}
        a(f"  {name}:   # {what}")
        a(f"    url: {_y(c.get('url') or '')}   # a secret: left out of diagnostics and logs")
        a(f"    url_env: {_y(c.get('url_env') or '')}   # or the NAME of an environment variable that holds it")
        for k, v in c.items():
            if k not in ("url", "url_env"):
                a(f"    {_y(str(k))}: {_y(v)}")
    e = n["email"] if isinstance(n["email"], dict) else {}
    a("  email:   # SMTP; no host = no email")
    a(f"    host: {_y(e.get('host') or '')}")
    a(f"    port: {_y(e.get('port', 587))}")
    a(f"    security: {_y(e.get('security') or 'starttls')}   # starttls | ssl | none (none: no password allowed)")
    a(f"    username: {_y(e.get('username') or '')}")
    a(f"    password: {_y(e.get('password') or '')}   # a secret: left out of diagnostics and logs")
    a(f"    password_env: {_y(e.get('password_env') or '')}   # or the NAME of an environment variable that holds it")
    a(f"    from: {_y(e.get('from') or '')}")
    a(f"    to: {_y(e.get('to') if e.get('to') is not None else [])}")
    for k, v in e.items():
        if k not in ("host", "port", "security", "username", "password", "password_env", "from", "to"):
            a(f"    {_y(str(k))}: {_y(v)}")
    for k, v in n.items():  # a typo is written back as it was, so the loader can name it
        if k not in _notify_defaults():
            a(f"  {_y(str(k))}: {_y(v)}")
    a("")
    at = d.get("atlas") if isinstance(d.get("atlas"), dict) else {}
    a("atlas:   # hand finished isoDTB (and TMT) searches to your lab's cysteine atlas (docs/ATLAS.md). Off by default")
    a(f"  enabled: {_y(at.get('enabled', False))}")
    a(f"  handoff_dir: {_y(at.get('handoff_dir', '') or '')}   # the Dropbox folder the atlas side reads, e.g. "
      "...\\<team folder>\\fragpipe-runs (spaces are fine here: FragPipe never sees it)")
    a(f"  methods: {_y(list(at.get('methods') or []) if at.get('methods') is not None else ['isoDTB'])}   "
      "# FragPipe isoDTB or TMT methods whose searches are handed off (the atlas counts isoDTB only)")
    a(f"  pin_max_mb: {_y(at.get('pin_max_mb', 100))}   # each .pin (the decoys) goes gzip-compressed if no larger than "
      "this many MB before compression; 0 = none")
    a(f"  max_tries: {_y(at.get('max_tries', 10))}   # a hand-off that keeps failing (Dropbox offline) stops after this "
      "many tries")
    a(f"  retry_minutes: {_y(at.get('retry_minutes', 15))}   # the first wait before trying again; it doubles, up to "
      "12 hours")
    a(f"  sweep: {_y(at.get('sweep', False))}   # also search this PC's drives for FragPipe runs Ionomos did not search "
      "(harvest's rule) and hand off the isoDTB (and TMT) ones, with their decoys and sample design")
    a(f"  sweep_roots: {_y(list(at.get('sweep_roots') or []))}   # the folders searched; [] = every fixed drive")
    a(f"  sweep_exclude: {_y(list(at.get('sweep_exclude') or []))}   # folders never searched (the atlas and Dropbox "
      "folders never are)")
    a(f"  sweep_hours: {_y(at.get('sweep_hours', 24))}   # a search every this many hours, never while a search runs")
    a(f"  sweep_min_free_gb: {_y(at.get('sweep_min_free_gb', 40))}   # the sweep's copying stops before the atlas "
      "folder's drive has less free than this (a job's hand-off keeps fragpipe.min_free_gb)")
    for k, v in at.items():  # a typo is written back as it was, so the loader can name it
        if k not in ("enabled", "handoff_dir", "methods", "pin_max_mb", "max_tries", "retry_minutes", "sweep",
                     "sweep_roots", "sweep_exclude", "sweep_hours", "sweep_min_free_gb"):
            a(f"  {_y(str(k))}: {_y(v)}")
    a("")

    if isinstance(d.get("assistant"), dict) and d["assistant"]:  # the app doesn't edit these; keep them (D57)
        a("assistant:   # the local assistant, `ionomos ask` (docs/ASSISTANT.md); off unless enabled with a model")
        for k, v in d["assistant"].items():
            a(f"  {_y(str(k))}: {_y(v)}")
        a("")
    a("users:   # users are the subfolders of users_root; aliases map initials -> folder")
    a("  aliases:")
    for user, als in (u.get("aliases") or {}).items():
        a(f"    {_y(user)}: {_y(list(als))}")
    a(f"  default: {_y(u.get('default', '') or '')}   # e.g. \"_unsorted\" to accept unknown users")
    a(f"  learned_aliases_file: {_y(u.get('learned_aliases_file', ''))}")
    a(f"  ignore: {_y(list(u.get('ignore') or []))}   # users_root subfolders that aren't people (FragPipe copies, FASTA folders)")
    _rest(a, u, ("aliases", "default", "learned_aliases_file", "ignore"))
    a("")
    a("methods:   # key = canonical name; aliases are matched in folder/file names")
    for key, m in d["methods"].items():
        a(f"  {_y(key)}:")
        from ionomos.naming import DEFAULT_METHOD_ALIASES

        # no aliases of its own: the ones the loader gives it (iso-dtb, iso_dtb for isoDTB), not just the key, so a
        # Save never narrows what folder names mean this method
        a(f"    aliases: {_y(list(m.get('aliases') or DEFAULT_METHOD_ALIASES.get(key) or [key.lower()]))}")
        a(f"    workflow: {_y(m.get('workflow', ''))}")
        a(f"    fasta: {_y(m.get('fasta', ''))}")
        a(f"    data_type: {_y(m.get('data_type', 'DDA'))}")
        a(f"    postprocess: {_y(list(m.get('postprocess') or []))}")
        for k, v in m.items():
            if k not in ("aliases", "workflow", "fasta", "data_type", "postprocess"):
                a(f"    {_y(k)}: {_y(v)}")
    a("")
    other = {k: v for k, v in d.items() if k not in _SECTIONS}
    if other:  # a section Ionomos doesn't know (a typo such as notifyy:) is kept, so the loader can name it
        a("# not a section Ionomos knows; kept as it was so that `ionomos check` can name it")
        for k, v in other.items():
            a(f"{_y(str(k))}: {_y(v)}")
        a("")
    return "\n".join(L)


_PATHS = ("inbox", "users_root", "fragpipe_exe", "workflow_dir", "fasta_dir", "database", "log_dir")
_SECTIONS = ("paths", "watcher", "fragpipe", "gui", "naming", "analysis", "qc_trend", "library", "notify", "assistant",
             "atlas", "users", "methods")
# analysis keys the writer always writes; then those written when set (lists, then single values). Anything else
# a lab has in analysis: is written back as it was (_rest), so a Save never drops a setting the loader takes.
_AN_WRITTEN = ("enabled", "test", "de_type", "log2fc", "alpha", "use_adjusted", "remove_contaminants",
               "filter_global_pct", "filter_condition_pct", "normalize", "imputation", "min_valid", "enrichment",
               "enrichment_libraries", "enrichment_gmt", "top_labels", "control_keywords", "competition_keywords")
_AN_LISTS = ("competition_keywords_weak", "background_keywords", "role_comparisons",
             "small_group_min_valid")  # roles: D61, D88
_AN_SCALARS = ("impute_shift", "impute_scale", "seed", "pca_features", "heatmap_max", "control", "variance_prior",
               "block", "block_from", "covariates",  # DEqMS and the design (downstream/design.py)
               "dose_response", "dose_min_doses", "dose_alpha", "dose_fc_lim", "dose_unit",  # dose-response: D44
               "liganded", "liganded_ratio", "liganded_min_replicates", "liganded_direction",  # cys.py: D52
               "site_annotation",
               "time_course", "time_min_points", "time_unit",  # time courses: D53
               "time_model", "time_spline_df",  # spline time courses: D77
               "psm_qc",  # search quality per run: D55
               "phospho", "phospho_min_localization", "phospho_localization_per_sample", "phospho_table",  # D79
               "kinase_substrates", "ksea_min_substrates", "ksea_networkin", "ksea_networkin_score", "ksea_organism",
               "ksea_match", "string_network", "string_min_score",
               "f_test", "rollup",  # the F-test switch and the protein roll-up: D76
               "irs", "tmt_reference",  # several TMT plexes on one scale (plex.py): D48, D71
               "tmt_min_labelling", "tmt_min_purity")  # the TMT QC's limits (tmtqc.py): D91


def _rest(a, sec, done) -> None:
    """The keys of a section the writer has no line of its own for (a setting set by hand, a typo), written back as
    they were (D67): a Save never drops a value, and a typo stays where the loader names it."""
    if not isinstance(sec, dict):
        return
    for k, v in sec.items():
        if k not in done:
            a(f"  {_y(str(k))}: {_y(v)}")


# analysis.export keys the writer always shows, with their default and what they take (downstream/charts.py)
_EXPORT_KEYS = (
    ("size", "slide169", "slide169 (16:9, 1280 x 720 px) | slide43 | half | col1 (85 mm) | col2 (180 mm) | custom "
                         "(+ width, height, unit: px | mm)"),
    ("font_pt", 14, "text size in points in the finished figure (14 suits a slide, 7 a journal column)"),
    ("font_family", "Arial", "the font's name; it must be installed where the figure is opened"),
    ("palette", "default", "default | colorblind | grey | custom (+ up, down, neutral: \"#rrggbb\")"),
    ("background", "light", "light | dark | transparent"),
    ("figures", [], "static SVG written to results/figures after each analysis: any of volcano, pca, heatmap, "
                    "correlation, dose, time, liganded ([] = none)"),
)
_EXPORT_FONT = {"slide169": 14, "slide43": 14, "half": 12, "col1": 7, "col2": 7}  # charts.SIZES (a test compares)

BACKUP_DIR = "config-backups"


def backup_config(path: str | Path, keep: int = 30) -> Path | None:
    """Copy an existing config to <dir>/config-backups/config-<ts>.yaml (newest `keep` kept)."""
    p = Path(path)
    if not p.is_file():
        return None
    d = p.parent / BACKUP_DIR
    d.mkdir(exist_ok=True)
    stamp = f"config-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    dest = d / f"{stamp}.yaml"
    n = 1
    while dest.exists():  # two Saves in one second (Undo last save must find the one before, never a copy)
        n += 1
        dest = d / f"{stamp}-{n}.yaml"
    data = p.read_bytes()
    with open(dest, "xb") as fh:
        fh.write(data)
    for old in sorted(d.glob("config-*.yaml"), key=_backup_order)[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


_BACKUP_NAME = re.compile(r"config-(\d{8}-\d{6})(?:-(\d+))?\.yaml$")


def backup_time(backup_path: str | Path) -> datetime.datetime | None:
    """When a backup was made, from its name config-YYYYmmdd-HHMMSS.yaml (or -2, -3 … for a second Save in the same
    second); None for any other name."""
    m = _BACKUP_NAME.search(Path(backup_path).name)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def _backup_order(p: Path) -> tuple:
    m = _BACKUP_NAME.search(p.name)
    return (backup_time(p) or datetime.datetime.min, int(m.group(2) or 1) if m else 0, p.name)


def backups(path: str | Path) -> list[Path]:
    """The config-backups/config-*.yaml beside the config at `path`, newest first (by the time in the name)."""
    d = Path(path).parent / BACKUP_DIR
    try:
        found = [p for p in d.glob("config-*.yaml") if p.is_file()]
    except OSError:
        return []
    return sorted(found, key=_backup_order, reverse=True)


def latest_backup(path: str | Path) -> Path | None:
    """The newest backup of the config at `path` (what "Undo last save" restores), or None."""
    found = backups(path)
    return found[0] if found else None


# ------------------------------------------------------------ the word diff --

SECTION_WORDS = {"paths": "Folders", "watcher": "Watcher", "fragpipe": "FragPipe", "gui": "Windows",
                 "analysis": "Analysis", "notify": "Notifications", "users": "Users", "methods": "Methods",
                 "library": "Library", "qc_trend": "Instrument QC", "naming": "Naming", "assistant": "Assistant",
                 "atlas": "Atlas"}
# blocks whose keys are names a person chose (a method, a user, a condition): shown exactly as written
_NAMED = {("methods",), ("users", "aliases"), ("naming", "condition_codes"), ("naming", "methods"),
          ("analysis", "roles"), ("analysis", "times"), ("analysis", "doses")}
_SECRETS = ("url", "password")
_MASK = "•••"
_MISSING = object()


def _key_words(path: tuple) -> str:
    k = str(path[-1])
    if len(path) == 1:
        return SECTION_WORDS.get(k, k.replace("_", " "))
    if path[:-1] in _NAMED:
        return k
    k = k.replace("_", " ")
    return k[:1].upper() + k[1:] if len(path) == 2 else k


def _value_words(v) -> str:
    if v is _MISSING:
        return "(not set)"
    if isinstance(v, bool):
        return "on" if v else "off"
    if v is None or v == "":
        return "(empty)"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (list, tuple)):
        return ", ".join(_value_words(x) for x in v) if v else "(none)"
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_value_words(x)}" for k, x in v.items()) if v else "(empty)"
    return str(v)


def _secret(path: tuple) -> bool:
    return path[0] == "notify" and str(path[-1]) in _SECRETS


def _blank(v) -> bool:
    return v is _MISSING or v is None or (not isinstance(v, bool) and v in ("", [], {}, ()))


def _same(a, b) -> bool:
    if _blank(a) and _blank(b):
        return True  # 'sdrf:' with nothing under it and no sdrf: at all say the same
    if a is _MISSING or b is _MISSING:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b  # on is not 1
    return a == b  # 24 and 24.0 are the same setting


def _walk(path: tuple, old: dict, new: dict, out: list[str]) -> None:
    for k in [*new, *(k for k in old if k not in new)]:
        a, b = old.get(k, _MISSING), new.get(k, _MISSING)
        p = (*path, k)
        if (isinstance(a, dict) or isinstance(b, dict)) and all(isinstance(x, dict) or x is _MISSING for x in (a, b)):
            _walk(p, a if isinstance(a, dict) else {}, b if isinstance(b, dict) else {}, out)
            continue
        if _same(a, b):
            continue
        before_w = _value_words(a)
        after_w = "(removed)" if b is _MISSING else _value_words(b)
        if _secret(p):
            before_w = _MASK if a not in (_MISSING, None, "") else before_w
            after_w = _MASK if b not in (_MISSING, None, "") else after_w
        out.append(" → ".join(_key_words(p[:i]) for i in range(1, len(p) + 1)) + f": {before_w} → {after_w}")


def diff_words(old: dict, new: dict) -> list[str]:
    """What a Save changes, one line per setting in words: 'FragPipe → Threads: 24 → 28',
    'Users → Aliases → Isaac: IJ → IJ, IJD', 'Methods → TMT: (not set) → …', 'X: 5 → (removed)'.
    Lines follow the order of `new`, then the removed keys. Secrets (notify url / password) show as •••."""
    out: list[str] = []
    _walk((), old if isinstance(old, dict) else {}, new if isinstance(new, dict) else {}, out)
    return out


def diff_summary(lines: list[str]) -> str:
    """'1 setting changes' / '3 settings change' / 'nothing changes'."""
    n = len(lines)
    return "nothing changes" if not n else ("1 setting changes" if n == 1 else f"{n} settings change")


def write_config(path: str | Path, d: dict, backup: bool = True) -> Path:
    """Atomically write config.yaml; the previous version goes to config-backups/ first. The text is made before
    anything is touched, and a crash or a full disk while writing leaves the previous config.yaml as it was
    (config.write_text_atomic)."""
    from ionomos.config import write_text_atomic

    p = Path(path)
    text = dump_config(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        try:
            backup_config(p)
        except OSError:
            pass  # a failed backup must not block saving
    write_text_atomic(p, text)
    return p
