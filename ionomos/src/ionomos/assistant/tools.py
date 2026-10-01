"""
The assistant's tools: read-only wrappers over what Ionomos already knows. Tools, not a shell (D49).

    ctx = Context(cfg, ledger)                       # ledger: an open Ledger, or None when there is none yet
    schemas()                                        # the "tools" of a chat-completions request (byte-stable)
    call(ctx, "log_tail", '{"job_id": 3}')           # -> {"job": 3, "lines": ["41| ...", ...], ...}

    list_experiments   the jobs in the ledger                    ledger.Ledger.list
    get_job            one job: status, reason, likely causes    ledger + the experiment's status file (names.py)
    list_attention     what needs a person                       attention.items, help.topic_for_item
    explain_issue      a doctor issue code, in the help's words  help.issue_entry / help.to_text (verbatim)
    log_tail           the end of a job's engine log, numbered   fragpipe.read_tail_text / fragpipe.explain
    analysis_summary   fields of results/analysis.json           what downstream.analyze wrote
    search_help        the help entries that match some words    assistant/helpsearch.py (BM25)

What a tool can never do, by construction and by test (tests/test_assistant.py):
  - No argument is a file path: an experiment is named by its job id, and its folder comes from the ledger.
    Arguments are checked against the schema (types, ranges, lengths, no unknown keys) before anything runs.
  - Nothing is written, moved, deleted or started, and nothing opens a network connection.
  - call() never raises: a bad name or argument is an {"error": ...} result the model can read.

Everything returned is untrusted text (file names, sample names, log lines, experiment.yaml): clean() strips
control and invisible characters, every string and list is capped, and the whole result is cut to
MAX_RESULT_CHARS. As a tool returns an issue code, log line, help entry, analysis field or job, it records
it in ctx.registry, which is what citations.check() accepts.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ionomos import attention, fragpipe, names
from ionomos import help as helpdoc
from ionomos.assistant import helpsearch
from ionomos.assistant.citations import Registry
from ionomos.ledger import STATUSES

MAX_RESULT_CHARS = 6000     # one tool result as JSON (about 1.5k tokens)
MAX_STRING = 400
MAX_TEXT = 2400             # a help entry (the longest is under 2000 characters)
MAX_LOG_LINE = 240
MAX_LOG_LINES = 60
LOG_SCAN_BYTES = 400_000    # fragpipe.read_tail_text's default: the end of the log is what explains a failure
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ERRORS = re.compile(r"(?i)error|exception|fatal|failed|exit code: -?[1-9]|not found|denied|cannot|could not")


@dataclass
class Context:
    cfg: object                         # config.Config
    ledger: object | None = None        # ledger.Ledger (None: no job ledger yet)
    registry: Registry = field(default_factory=Registry)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., dict]


# ------------------------------------------------------------------ hygiene --


def clean(value, limit: int = MAX_STRING) -> str:
    """Untrusted text made safe to pass on: no ANSI escapes, control or invisible characters; one line unless
    it had line breaks; cut to `limit`."""
    s = _ANSI.sub("", str(value))
    s = re.sub(r"[\t\r]", " ", re.sub(r"\r\n", "\n", s))
    s = "".join(c for c in s if c == "\n" or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    s = re.sub(r"[ ]{2,}", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _clean_all(obj, limit: int = MAX_STRING):
    if isinstance(obj, str):
        return clean(obj, limit)
    if isinstance(obj, dict):  # "text" is a help entry, passed on whole (explain_issue: verbatim)
        return {clean(k, 80): _clean_all(v, MAX_TEXT if k == "text" else limit) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_all(v, limit) for v in obj]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return clean(obj, limit)


def _longest(obj, kind, best=None):
    """The longest list (or string holder) anywhere in obj: (container, key) for shrinking it."""
    items = obj.items() if isinstance(obj, dict) else enumerate(obj) if isinstance(obj, list) else ()
    for k, v in items:
        if isinstance(v, kind) and len(v) > 1 and (best is None or len(v) > len(best[0][best[1]])):
            best = (obj, k)
        if isinstance(v, (dict, list)):
            best = _longest(v, kind, best)
    return best


def fit(obj: dict, budget: int = MAX_RESULT_CHARS) -> dict:
    """Shrink a result until its JSON fits: halve the longest list, then the longest string. Marks it truncated."""
    for _ in range(64):
        if len(json.dumps(obj, ensure_ascii=False)) <= budget:
            return obj
        hit = _longest(obj, list) or _longest(obj, str)
        if hit is None:
            break
        box, k = hit
        v = box[k]
        box[k] = v[: max(len(v) // 2, 1)] if isinstance(v, list) else v[: max(len(v) // 2, 1)] + "…"
        obj["truncated"] = True
    return {"error": "the result was too large to pass on", "truncated": True}


# --------------------------------------------------------------- validation --


def validate(schema: dict, args) -> tuple[dict, str | None]:
    """Check arguments against a tool's schema (the subset of JSON Schema the tools use).
    Returns (arguments with defaults, None) or ({}, what is wrong)."""
    if not isinstance(args, dict):
        return {}, "arguments must be a JSON object"
    props = schema.get("properties", {})
    extra = sorted(set(args) - set(props))
    if extra:
        return {}, f"unknown argument {extra[0]!r} (known: {', '.join(props) or 'none'})"
    out = {}
    for name, spec in props.items():
        if name not in args or args[name] is None:
            if name in schema.get("required", ()):
                return {}, f"missing argument {name!r}"
            continue
        v, t = args[name], spec["type"]
        if t == "integer":
            if isinstance(v, bool) or not isinstance(v, int):
                return {}, f"{name} must be a whole number"
            if v < spec.get("minimum", v) or v > spec.get("maximum", v):
                return {}, f"{name} must be between {spec.get('minimum')} and {spec.get('maximum')}"
        elif t == "boolean":
            if not isinstance(v, bool):
                return {}, f"{name} must be true or false"
        else:
            if not isinstance(v, str):
                return {}, f"{name} must be text"
            if len(v) > spec.get("maxLength", 200):
                return {}, f"{name} is too long (at most {spec.get('maxLength', 200)} characters)"
            if "enum" in spec and v not in spec["enum"]:
                return {}, f"{name} must be one of: {', '.join(spec['enum'])}"
            if "pattern" in spec and not re.fullmatch(spec["pattern"], v):
                return {}, f"{name} has characters that are not allowed"
            v = clean(v, spec.get("maxLength", 200))
        out[name] = v
    return out, None


# ------------------------------------------------------------------ helpers --


def _job(ctx: Context, job_id: int):
    job = ctx.ledger.get(job_id) if ctx.ledger is not None else None
    if job is None:
        return None, {"error": f"there is no job {job_id}", "hint": "list_experiments shows the job ids"}
    ctx.registry.add("job", job.id, f"job {job.id}: {clean(job.inbox_name, 80)} ({job.status})")
    return job, None


def _record(job) -> dict:
    """The experiment's status file (ionomos.json, or its LabWatch-era twin). {} when unreadable."""
    try:
        rec = json.loads(names.status_path(Path(job.dest_dir)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _analysis(job) -> dict | None:
    from ionomos import downstream

    try:
        d = json.loads((Path(job.dest_dir) / downstream.RESULTS / "analysis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _issues(ctx: Context, issues, full: bool = False) -> list[dict]:
    out = []
    for i in issues or []:
        if not isinstance(i, dict) or not i.get("code"):
            continue
        code = clean(i["code"], 64).upper()
        ctx.registry.add("issue", code, clean(i.get("title", ""), 120))
        hid = helpdoc.issue_entry(code)
        if hid:
            ctx.registry.add("help", hid, helpdoc.entries()[hid].title)
        row = {"code": code, "severity": i.get("severity", ""), "title": i.get("title", "")}
        if full:
            row |= {"message": i.get("message", ""), "causes": list(i.get("causes") or [])[:6],
                    "fixes": list(i.get("fixes") or [])[:6]}
        out.append(row | ({"help": hid} if hid else {}))
    return out


def _console_log(job, rec: dict) -> Path | None:
    """The job's engine console log, found inside its own run folder only (never from a path in a file)."""
    from ionomos import diann, maxquant, sage

    run = names.run_dir(Path(job.dest_dir))
    known = (fragpipe.CONSOLE_LOG, diann.CONSOLE_LOG, maxquant.CONSOLE_LOG, sage.CONSOLE_LOG)
    said = Path(str((rec.get("run") or {}).get("console_log") or "")).name
    found = [run / n for n in known if (run / n).is_file()]
    if not found:
        return None
    return next((p for p in found if p.name == said), max(found, key=lambda p: p.stat().st_mtime))


def _numbered_tail(path: Path) -> tuple[list[tuple[int, str]], int]:
    """([(line number in the file, text)], lines in the file) for the end of a log."""
    text = fragpipe.read_tail_text(path, LOG_SCAN_BYTES)
    skipped = 0
    try:
        size = path.stat().st_size
        if size > LOG_SCAN_BYTES:  # count the lines before the part that was read, so numbers are the file's
            with open(path, "rb") as fh:
                left = size - LOG_SCAN_BYTES
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    skipped += chunk.count(b"\n")
                    left -= len(chunk)
    except OSError:
        pass
    lines = text.splitlines()
    return [(skipped + n, ln) for n, ln in enumerate(lines, 1)], skipped + len(lines)


# -------------------------------------------------------------------- tools --


def list_experiments(ctx: Context, status: str | None = None, user: str | None = None, limit: int = 20) -> dict:
    jobs = ctx.ledger.list(status) if ctx.ledger is not None else []
    if user:
        jobs = [j for j in jobs if j.user.lower() == user.lower()]
    rows = []
    for j in reversed(jobs[-limit:]):
        ctx.registry.add("job", j.id, f"job {j.id}: {clean(j.inbox_name, 80)} ({j.status})")
        rows.append({"job": j.id, "name": j.inbox_name, "user": j.user, "method": j.method, "status": j.status,
                     "reason": clean(j.reason or "", 160), "created": j.created_at})
    return {"total": len(jobs), "shown": len(rows), "experiments": rows}


def get_job(ctx: Context, job_id: int) -> dict:
    job, err = _job(ctx, job_id)
    if err:
        return err
    rec = _record(job)
    run = rec.get("run") if isinstance(rec.get("run"), dict) else {}
    out = {"job": job.id, "name": job.inbox_name, "user": job.user, "method": job.method, "status": job.status,
           "reason": job.reason or "", "attempts": job.attempts, "created": job.created_at,
           "started": job.started_at, "finished": job.finished_at}
    for k in ("exit_code", "progress"):
        if run.get(k) is not None:
            out[k] = run[k]
    if run.get("hints"):
        out["likely_causes"] = list(run["hints"])[:6]
    if run.get("warnings"):
        out["warnings"] = list(run["warnings"])[:6]
    items = [i for i in attention.items(ctx.cfg.log_dir) if i.job_id == job.id]
    if items:
        out["needs_attention"] = [{"id": i.id, "kind": i.kind, "title": i.title} for i in items[:5]]
    summary = _analysis(job)
    if summary is not None:
        out["analysis"] = {"state": summary.get("state"), "issues": _issues(ctx, summary.get("issues"))}
    return out


def list_attention(ctx: Context, job_id: int | None = None, limit: int = 5) -> dict:
    items = attention.items(ctx.cfg.log_dir)
    if job_id is not None:
        items = [i for i in items if i.job_id == job_id]
    rows = []
    for it in items[:limit]:
        hid = helpdoc.topic_for_item(it)
        if hid in helpdoc.entries():
            ctx.registry.add("help", hid, helpdoc.entries()[hid].title)
        if it.job_id is not None and ctx.ledger is not None and ctx.ledger.get(it.job_id) is not None:
            ctx.registry.add("job", it.job_id, f"job {it.job_id}")
        row = {"id": it.id, "kind": it.kind, "severity": it.severity, "title": it.title, "message": it.message,
               "job": it.job_id, "causes": it.causes[:6], "fixes": it.fixes[:6], "help": hid, "since": it.created}
        issues = _issues(ctx, (it.data or {}).get("issues"))
        if issues:
            row["issues"] = issues
        rows.append(row)
    return {"total": len(items), "shown": len(rows), "items": rows}


def explain_issue(ctx: Context, code: str, job_id: int | None = None) -> dict:
    hid = helpdoc.issue_entry(code)
    if hid is None:
        return {"error": f"there is no issue code {code.upper()}",
                "hint": "issue codes come from get_job, list_attention or analysis_summary"}
    e = helpdoc.entries()[hid]
    ctx.registry.add("issue", code.upper(), e.title)
    ctx.registry.add("help", hid, e.title)
    out = {"code": code.upper(), "help": hid, "title": e.title, "text": helpdoc.to_text(e.body, width=10_000)}
    if job_id is not None:
        job, err = _job(ctx, job_id)
        if err:
            return err
        mine = [i for i in _issues(ctx, (_analysis(job) or {}).get("issues"), full=True) if i["code"] == code.upper()]
        out["in_this_job"] = mine[0] if mine else None
    return out


def log_tail(ctx: Context, job_id: int, lines: int = 30, contains: str | None = None,
             errors_only: bool = False) -> dict:
    job, err = _job(ctx, job_id)
    if err:
        return err
    path = _console_log(job, _record(job))
    if path is None:
        return {"job": job.id, "log": None, "note": "this job has no engine log (the search never started)"}
    numbered, total = _numbered_tail(path)
    causes = fragpipe.explain("\n".join(t for _n, t in numbered))
    keep = [(n, t) for n, t in numbered if t.strip() and not t.startswith("# ")]  # as fragpipe.tail: no markers
    if contains:
        keep = [(n, t) for n, t in keep if contains.lower() in t.lower()]
    if errors_only:
        keep = [(n, t) for n, t in keep if _ERRORS.search(t)]
    shown = []
    for n, t in keep[-lines:]:
        t = clean(t, MAX_LOG_LINE)
        ctx.registry.add("log", f"{job.id}#{n}", t[:120])
        shown.append(f"{n}| {t}")
    return {"job": job.id, "log": path.name, "lines_in_file": total, "matching": len(keep), "lines": shown,
            "likely_causes": causes[:4]}


ANALYSIS_FIELDS = ("method", "state", "features", "features_loaded", "level", "samples", "comparisons", "processing",
                   "imputation", "issues", "quality", "f_test", "dose_response", "time_course", "cysteines",
                   "enrichment_notes", "notes", "generated_at", "ionomos_version")


def analysis_summary(ctx: Context, job_id: int) -> dict:
    job, err = _job(ctx, job_id)
    if err:
        return err
    d = _analysis(job)
    if d is None:
        return {"job": job.id, "analysis": None,
                "note": "this job has no results/analysis.json (the analysis has not run, or could not write it)"}
    out: dict = {"job": job.id}
    for k in ANALYSIS_FIELDS:
        v = d.get(k)
        if v in (None, "", [], {}) or (isinstance(v, dict) and v.get("ran") is False):  # a step that did not run
            continue
        if k == "issues":
            v = _issues(ctx, v)
        elif k == "comparisons":
            v = [{x: c.get(x) for x in ("name", "up", "down", "tested", "confidence", "confidence_note")
                  if c.get(x) not in (None, "")} for c in v if isinstance(c, dict)][:12]
        elif k == "samples" and isinstance(v, dict):
            v = dict(list(v.items())[:48])
        elif isinstance(v, list):
            v = v[:12]
        out[k] = v
        ctx.registry.add("analysis", k, f"results/analysis.json of job {job.id}: {k}")
        ctx.registry.add("analysis", f"{job.id}#{k}", f"results/analysis.json of job {job.id}: {k}")
    return out


def search_help(ctx: Context, query: str, limit: int = 3) -> dict:
    ents = helpdoc.entries()
    rows = []
    for h in helpsearch.search(query, limit):
        ctx.registry.add("help", h.id, h.title)
        rows.append({"help": h.id, "title": h.title, "text": clean(helpdoc.to_text(ents[h.id].body, width=10_000), 700)})
    return {"query": query, "entries": rows}


_JOB = {"type": "integer", "minimum": 1, "maximum": 10_000_000, "description": "Job id from list_experiments."}


def _schema(props: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


TOOLS: tuple[Tool, ...] = (
    Tool("list_experiments", "List the experiments (jobs) Ionomos has filed, newest first.",
         _schema({"status": {"type": "string", "enum": list(STATUSES)},
                  "user": {"type": "string", "maxLength": 64, "pattern": r"[A-Za-z0-9_.\-]+"},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 30}}), list_experiments),
    Tool("get_job", "One job: status, the reason for a failure or wait, likely causes, open items, analysis issues.",
         _schema({"job_id": _JOB}, ("job_id",)), get_job),
    Tool("list_attention", "What needs a person now: failed or waiting searches, analysis decisions, rejected folders.",
         _schema({"job_id": _JOB, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}), list_attention),
    Tool("explain_issue", "What an analysis issue code (e.g. NO_TABLE) means and what to do: the help's own text.",
         _schema({"code": {"type": "string", "maxLength": 64, "pattern": r"[A-Za-z][A-Za-z0-9_]*"}, "job_id": _JOB},
                 ("code",)), explain_issue),
    Tool("log_tail", "The last lines of a job's search-engine log, with line numbers, and the causes Ionomos "
                     "recognises in it.",
         _schema({"job_id": _JOB, "lines": {"type": "integer", "minimum": 1, "maximum": MAX_LOG_LINES},
                  "contains": {"type": "string", "maxLength": 60}, "errors_only": {"type": "boolean"}}, ("job_id",)),
         log_tail),
    Tool("analysis_summary", "What the analysis of a job did and found: samples and conditions, comparisons and hit "
                             "counts, processing steps, imputation, issues, quality checks.",
         _schema({"job_id": _JOB}, ("job_id",)), analysis_summary),
    Tool("search_help", "Search the Ionomos help by words. Returns the best entries with their text.",
         _schema({"query": {"type": "string", "maxLength": 200},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 5}}, ("query",)), search_help),
)
BY_NAME = {t.name: t for t in TOOLS}


def schemas() -> list[dict]:
    """The request's "tools": the same bytes every time (the runtime's prompt cache reuses the prefix)."""
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}} for t in TOOLS]


def call(ctx: Context, name: str, raw_args) -> dict:
    """Run one tool call from the model. Returns the cleaned, size-capped result; never raises."""
    tool = BY_NAME.get(name if isinstance(name, str) else "")
    if tool is None:
        return {"error": f"there is no tool {clean(name, 60)!r}; the tools only read",
                "tools": [t.name for t in TOOLS]}
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except ValueError:
            return {"error": "the arguments are not valid JSON"}
    args, problem = validate(tool.parameters, raw_args)
    if problem:
        return {"error": problem}
    try:
        return fit(_clean_all(tool.fn(ctx, **args)))
    except Exception as exc:  # noqa: BLE001 - a tool must never take the conversation (or the app) down
        return {"error": f"{tool.name} could not read that: {clean(exc, 160)}"}
