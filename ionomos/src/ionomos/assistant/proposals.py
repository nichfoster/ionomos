"""
The assistant's proposal tools (ROADMAP Phase 6.2, D75): the model can suggest one change per question, and
nothing happens until a person presses Confirm in a window Ionomos draws from the checked arguments.

    p = build(ctx, "propose_leave_out", {"job_id": 3, "sample": "DMSO_2", "leave_out": True})  # or raises Refused
    p.title, p.changes, p.diff          # what the window (popups.ProposalDialog) and `ionomos ask` show
    call(ctx, name, raw_args)           # the model's call: checked, built, kept as ctx.proposal; a dict for the model

    propose_retry       re-run a failed search                        -> worker.request_retry, after Confirm
    propose_condition   give a sample another condition               -> analysis.sample_conditions
    propose_leave_out   leave a sample out, or use it again           -> analysis.exclude_samples
    propose_setting     one whitelisted analysis setting (SETTINGS)   -> analysis.<key>
    propose_role        a condition's role                            -> analysis.roles

This module only reads. Applying is actions.apply(), which only the Confirm button of the proposal window calls
(tests/test_assistant_proposals.py reads the source to check that). A proposal says what it would do in
Ionomos's own words, built from arguments that passed the schema and the checks below; nothing the model wrote
as prose is shown in the window.

What is checked before a proposal exists (a refusal is an {"error": ...} the model reads; no window opens):
  - the schema: types, lengths, no unknown argument, no path (tools.validate)
  - the job is in the ledger and its folder is the one the ledger names; a retry needs a failed job
  - a sample is one of the experiment's samples (its last analysis, results/analysis.json, plus the samples
    experiment.yaml leaves out); a control, comparison or role names one of its conditions; a new condition
    is a plain name
  - the analysis: block after the change passes the analysis' own validator with the lab's settings
    (downstream.analysis.settings_from, as the experiment editor's Save does) and differs from what is there
  - one proposal per question: a second one is refused
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ionomos.assistant import tools
from ionomos.assistant.tools import Tool, clean

SETTINGS = ("imputation", "normalize", "alpha", "log2fc", "control", "de_type", "comparisons")  # the whitelist
NEW_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+\-]{0,63}")   # a condition the experiment does not have yet
DEFAULT_WORDS = ("", "default", "lab default", "(lab default)")
AUTOMATIC = ("automatic", "auto", "default")
SAFE_ARG = re.compile(r"[A-Za-z0-9_.+\-:]+")                   # printed in a command without quotes
SETTING_LABEL = {"imputation": "Imputation", "normalize": "Normalisation", "alpha": "p ≤", "log2fc": "|log2FC| ≥",
                 "control": "the control", "de_type": "what is compared", "comparisons": "the comparisons"}
DE_TYPE_WORDS = {"control": "each condition vs control", "all": "all pairs", "others": "each condition vs all others"}


class Refused(ValueError):
    """A proposal that cannot be made. The message is for the model (and, in tests, for a person)."""


@dataclass
class Proposal:
    """One change the assistant proposes, in Ionomos's words. Plain data: it is shown, audited and, after Confirm,
    rebuilt from tool + arguments and applied (actions.py)."""
    tool: str
    arguments: dict                 # as the schema and the checks left them
    job_id: int
    job_name: str
    action: str                     # "retry" | "analysis"
    title: str                      # one line, e.g. "Retry job 12 (EJQ_...)"
    changes: list[str] = field(default_factory=list)   # what Confirm does, in words
    diff: str = ""                  # experiment.yaml, unified diff ("" for a retry)
    analysis: dict | None = None    # the whole analysis: block after the change (what is saved)
    file_sha256: str = ""           # experiment.yaml as it was ("" = no file): a change since then stops Confirm
    commands: list[str] = field(default_factory=list)  # `ionomos` commands a person can run instead
    app_steps: str = ""             # the same in the app
    note: str = ""                  # set by ask(): e.g. the job is not the one the question was about
    id: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def args_sha256(self) -> str:
        from ionomos.assistant import audit

        return audit.digest({"tool": self.tool, "arguments": self.arguments})


def _id(tool: str, arguments: dict, job_id: int, file_sha256: str, analysis) -> str:
    blob = json.dumps([tool, arguments, job_id, file_sha256, analysis], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ reading --


def _job(ctx: tools.Context, job_id: int):
    job = ctx.ledger.get(job_id) if ctx.ledger is not None else None
    if job is None:
        raise Refused(f"there is no job {job_id} (list_experiments shows the job ids)")
    ctx.registry.add("job", job.id, f"job {job.id}: {clean(job.inbox_name, 80)} ({job.status})")
    return job


def _lab(ctx: tools.Context) -> dict:
    lab = dict(getattr(ctx.cfg, "analysis", None) or {})
    lab.pop("enabled", None)
    return lab


def file_digest(dest: Path) -> str:
    from ionomos.manifest import EXPERIMENT_YAML

    try:
        return hashlib.sha256((Path(dest) / EXPERIMENT_YAML).read_bytes()).hexdigest()
    except OSError:
        return ""


def _overrides(dest: Path):
    from ionomos.manifest import OverridesError, load_overrides

    if not Path(dest).is_dir():
        raise Refused("the experiment's folder is not there any more")
    try:
        return load_overrides(dest)
    except OverridesError as exc:
        raise Refused(f"experiment.yaml cannot be read ({clean(exc, 200)}); fix it in the experiment editor") from None


def design(job, analysis: dict) -> tuple[dict[str, str | None], list[str]]:
    """({sample: condition or None}, the conditions of the samples used) as the next analysis would see them:
    the samples of the last analysis (results/analysis.json) and those experiment.yaml leaves out, with
    experiment.yaml's sample_conditions applied. Raises Refused when the analysis has no sample list."""
    from ionomos.downstream.analysis import _list

    summary = tools._analysis(job) or {}
    seen = summary.get("samples")
    if not isinstance(seen, dict) or not seen:
        raise Refused(f"job {job.id} has no analysed samples yet (results/analysis.json); its analysis must run first")
    given = analysis.get("sample_conditions")
    given = given if isinstance(given, dict) else {}
    excluded = _list(analysis.get("exclude_samples") or [])
    samples: dict[str, str | None] = {str(s): str(given.get(s, c)) for s, c in seen.items()}
    for s in excluded:
        samples.setdefault(s, str(given[s]) if s in given else None)
    conds = list(dict.fromkeys(c for s, c in samples.items() if c is not None and s not in excluded))
    return samples, conds


def _one_of(value: str, known, what: str) -> str:
    if value in known:
        return value
    near = [k for k in known if k.lower() == value.lower()]
    if len(near) == 1:
        return near[0]
    shown = ", ".join(clean(k, 60) for k in list(known)[:12])
    raise Refused(f"{clean(value, 80)!r} is not one of this experiment's {what} ({shown})")


# ----------------------------------------------------------------- building --


def _analysis_change(ctx, job, tool: str, arguments: dict, mutate, title: str, words: list[str], step: str,
                     command: list[str] | None) -> Proposal:
    """A proposal that changes the analysis: block: mutate(block, samples, conditions) changes a copy in place."""
    import copy

    from ionomos.downstream.analysis import AnalysisError, settings_from
    from ionomos.manifest import EXPERIMENT_YAML, overrides_text

    dest = Path(job.dest_dir)
    ov = _overrides(dest)
    before = dict(ov.analysis or {})
    after = copy.deepcopy(before)
    mutate(after)
    if after == before:
        raise Refused("experiment.yaml already says that; there is nothing to change")
    try:
        settings_from(_lab(ctx), after)
    except AnalysisError as exc:
        raise Refused(f"the analysis would not accept that: {clean(exc, 240)}") from None
    sha = file_digest(dest)
    try:
        old = (dest / EXPERIMENT_YAML).read_text(encoding="utf-8")
    except OSError:
        old = ""
    ov.analysis = after
    new = overrides_text(dest, ov, replace_analysis=True)
    diff = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), f"{EXPERIMENT_YAML} (now)",
                                          f"{EXPERIMENT_YAML} (after Confirm)", lineterm="", n=2))
    changes = [*words,
               f"Ionomos saves it in job {job.id}'s experiment.yaml (analysis:), as the experiment editor's Save does; "
               f"the version it replaces is kept in its experiment-backups folder.",
               f"The analysis uses it the next time it runs: Jobs tab → select job {job.id} → Re-run analysis, or "
               f"ionomos analyze {job.id}."]
    cmds = [f"ionomos analyze {job.id} " + " ".join(command)] if command else []
    return Proposal(tool, arguments, job.id, clean(job.inbox_name, 120), "analysis", title, changes,
                    _block(diff), after, sha, cmds,
                    f"App: Jobs tab → select job {job.id} → Analysis options… → {step} → Run analysis.",
                    id=_id(tool, arguments, job.id, sha, after))


def _block(text: str, limit: int = 6000) -> str:
    """A diff made safe to show: as tools.clean, but the indentation that YAML depends on is kept."""
    s = tools._ANSI.sub("", str(text)).replace("\t", " ")
    s = "".join(c for c in s if c == "\n" or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _quoted(values) -> list[str] | None:
    """Arguments for a printed command, or None when one of them cannot be printed safely in a terminal."""
    out = []
    for v in values:
        v = str(v)
        if SAFE_ARG.fullmatch(v):
            out.append(v)
        elif v and not re.search(r"[\"%!^`$\\\n]", v) and v.isprintable():
            out.append(f'"{v}"')
        else:
            return None
    return out


def _flags(pairs: list[tuple[str, str]]) -> list[str] | None:
    vals = _quoted([v for _f, v in pairs])
    return None if vals is None else [f"{f} {v}" for (f, _v), v in zip(pairs, vals, strict=True)]


def propose_retry(ctx: tools.Context, job_id: int) -> Proposal:
    job = _job(ctx, job_id)
    if job.status != "failed":
        raise Refused(f"job {job.id} is {job.status}; only a failed search can be retried")
    name = clean(job.inbox_name, 120)
    args = {"job_id": job.id}
    return Proposal("propose_retry", args, job.id, name, "retry", f"Retry job {job.id} ({name})",
                    [f"Job {job.id} goes back into the queue; the watcher runs its search again when it is free.",
                     "Its earlier output is kept. This is what the Retry button and `ionomos retry` do."],
                    commands=[f"ionomos retry {job.id}"],
                    app_steps=f"App: Jobs tab → select job {job.id} → Retry (or Retry search in its pop-up window).",
                    id=_id("propose_retry", args, job.id, "", None))


def propose_condition(ctx: tools.Context, job_id: int, sample: str, condition: str) -> Proposal:
    job = _job(ctx, job_id)
    before = _overrides(Path(job.dest_dir)).analysis or {}
    samples, conds = design(job, before)
    sample = _one_of(sample, samples, "samples")
    condition = condition.strip()
    if condition in conds or any(c.lower() == condition.lower() for c in conds):
        condition = _one_of(condition, conds, "conditions")
    elif not NEW_NAME.fullmatch(condition):
        raise Refused("a new condition must be a plain name: letters, digits, _ . + - (at most 64)")
    if samples.get(sample) == condition:
        raise Refused(f"{clean(sample, 80)} is already in {clean(condition, 64)}")

    def mutate(an):
        sc = an.get("sample_conditions")
        an["sample_conditions"] = {**(sc if isinstance(sc, dict) else {}), sample: condition}

    was = samples.get(sample)
    return _analysis_change(
        ctx, job, "propose_condition", {"job_id": job.id, "sample": sample, "condition": condition}, mutate,
        f"Give sample {clean(sample, 80)} the condition {clean(condition, 64)} in job {job.id}'s analysis",
        [f"Sample {clean(sample, 80)} is analysed as {clean(condition, 64)}"
         + (f" (now {clean(was, 64)})." if was else ".")],
        f"select sample {clean(sample, 80)}, press Change condition… and type {clean(condition, 64)}", None)


def propose_leave_out(ctx: tools.Context, job_id: int, sample: str, leave_out: bool) -> Proposal:
    from ionomos.downstream.analysis import _list

    job = _job(ctx, job_id)
    before = _overrides(Path(job.dest_dir)).analysis or {}
    samples, _conds = design(job, before)
    sample = _one_of(sample, samples, "samples")
    excluded = _list(before.get("exclude_samples") or [])
    if leave_out and sample in excluded:
        raise Refused(f"{clean(sample, 80)} is already left out")
    if not leave_out and sample not in excluded:
        raise Refused(f"{clean(sample, 80)} is already used")
    after = sorted({*excluded, sample}) if leave_out else [s for s in excluded if s != sample]
    if leave_out and not [s for s in samples if s not in after]:
        raise Refused("that would leave every sample out")

    def mutate(an):
        if after:
            an["exclude_samples"] = after
        else:
            an.pop("exclude_samples", None)

    s = clean(sample, 80)
    return _analysis_change(
        ctx, job, "propose_leave_out", {"job_id": job.id, "sample": sample, "leave_out": leave_out}, mutate,
        f"Leave sample {s} out of job {job.id}'s analysis" if leave_out else f"Use sample {s} again in job {job.id}'s analysis",
        [f"Sample {s} is left out of the statistics; its raw file stays in the folder." if leave_out else
         f"Sample {s} is used in the statistics again."],
        f"select sample {s} and press Leave out / use",
        _flags([("--exclude", x) for x in after]) if leave_out else None)


def propose_setting(ctx: tools.Context, job_id: int, key: str, value: str) -> Proposal:
    from ionomos.downstream.analysis import AnalysisError, parse_comparison, settings_from

    job = _job(ctx, job_id)
    before = _overrides(Path(job.dest_dir)).analysis or {}
    value = value.strip()
    note = ""
    if value.lower() in DEFAULT_WORDS:
        new = None
        shown = "the lab default"
    elif key in ("control", "comparisons"):
        _samples, conds = design(job, before)
        if key == "control":
            new = shown = _one_of(value, conds, "conditions")
            if before.get("comparisons"):
                note = ("This experiment names its comparisons (analysis.comparisons); those are run, and the control "
                        "is used for the roles.")
        else:
            items = [x.strip() for x in value.replace("\n", ";").split(";") if x.strip()]
            if not items or len(items) > 24:
                raise Refused("give 1 to 24 comparisons like 'Drug vs DMSO; Drug2 vs DMSO'")
            new = []
            for it in items:
                try:
                    a, b = parse_comparison(it)
                except AnalysisError as exc:
                    raise Refused(clean(exc, 200)) from None
                a, b = _one_of(a, conds, "conditions"), _one_of(b, conds, "conditions")
                if a == b:
                    raise Refused(f"a comparison needs two different conditions, not {clean(a, 64)} vs itself")
                new.append(f"{a} vs {b}")
            shown = "; ".join(new)
    else:
        try:
            new = getattr(settings_from({key: value}), key)
        except AnalysisError as exc:
            raise Refused(clean(exc, 200)) from None
        shown = DE_TYPE_WORDS.get(new, str(new)) if key == "de_type" else str(new)

    def mutate(an):
        if new is None:
            an.pop(key, None)
        else:
            an[key] = new

    label = SETTING_LABEL[key]
    step = {"control": f"under Comparisons choose each condition vs control: {shown}",
            "comparisons": f"under Comparisons choose just these: {shown}",
            "de_type": f"under Comparisons choose {shown}"}.get(key, f"set {label} to {shown}")
    if new is None:
        step = f"set {label} back to the lab default (blank or (lab default))"
    flag = {"imputation": "--imputation", "alpha": "--alpha", "log2fc": "--log2fc", "control": "--control",
            "de_type": "--de-type"}.get(key)
    if new is None:
        cmd = None
    elif key == "comparisons":
        cmd = _flags([("--compare", c) for c in new])
    elif key == "normalize":
        cmd = _flags([("--normalize", str(new))]) if new in ("median", "gn", "none") else None
    else:
        cmd = _flags([(flag, str(new))])
    p = _analysis_change(
        ctx, job, "propose_setting", {"job_id": job.id, "key": key, "value": value}, mutate,
        f"Set {label} to {clean(shown, 200)} in job {job.id}'s analysis",
        [f"analysis.{key}: {clean(before[key], 200) if key in before else '(the lab default)'} → "
         f"{clean(shown, 200) if new is not None else '(the lab default)'}"] + ([note] if note else []),
        step, cmd)
    return p


def propose_role(ctx: tools.Context, job_id: int, condition: str, role: str) -> Proposal:
    from ionomos.downstream import roles

    job = _job(ctx, job_id)
    before = _overrides(Path(job.dest_dir)).analysis or {}
    _samples, conds = design(job, before)
    given = before.get("roles") if isinstance(before.get("roles"), dict) else {}
    condition = _one_of(condition.strip(), list(dict.fromkeys([*conds, *map(str, given)])), "conditions")
    if role.strip().lower() in AUTOMATIC:
        text = None
    else:
        try:
            kind, of = roles.parse_role(role)
        except roles.RoleError as exc:
            raise Refused(f"{clean(exc, 200)} (or automatic)") from None
        if of:
            of = _one_of(of, conds, "conditions")
        text = roles.format_role(kind, of)

    def mutate(an):
        r = dict(an.get("roles") or {}) if isinstance(an.get("roles"), dict) else {}
        if text is None:
            r.pop(condition, None)
        else:
            r[condition] = text
        if r:
            an["roles"] = r
        else:
            an.pop("roles", None)

    c = clean(condition, 64)
    shown = text or "automatic (what the name says)"
    return _analysis_change(
        ctx, job, "propose_role", {"job_id": job.id, "condition": condition, "role": role}, mutate,
        f"Make {c} a {shown} in job {job.id}'s analysis" if text else f"Give {c} its automatic role in job {job.id}'s analysis",
        [f"The role of {c}: {clean(given.get(condition), 80) if condition in given else 'automatic'} → {shown}."],
        f"under Roles select {c} and pick {text or 'automatic'}", None)


_JOB = {"type": "integer", "minimum": 1, "maximum": 10_000_000}   # read-only tools describe it already
_SAMPLE = {"type": "string", "maxLength": 120}
_COND = {"type": "string", "maxLength": 64}
TOOLS: tuple[Tool, ...] = (
    Tool("propose_retry", "Propose re-running a failed search.", tools._schema({"job_id": _JOB}, ("job_id",)),
         propose_retry),
    Tool("propose_condition", "Propose another condition for one sample of a job's analysis.",
         tools._schema({"job_id": _JOB, "sample": _SAMPLE, "condition": _COND}, ("job_id", "sample", "condition")),
         propose_condition),
    Tool("propose_leave_out", "Propose leaving one sample out of a job's analysis (leave_out false: use it again).",
         tools._schema({"job_id": _JOB, "sample": _SAMPLE, "leave_out": {"type": "boolean"}},
                       ("job_id", "sample", "leave_out")), propose_leave_out),
    Tool("propose_setting", "Propose one analysis setting of a job. value \"\" = lab default; comparisons: "
                            "\"A vs B; C vs B\".",
         tools._schema({"job_id": _JOB, "key": {"type": "string", "enum": list(SETTINGS)},
                        "value": {"type": "string", "maxLength": 200}}, ("job_id", "key", "value")), propose_setting),
    Tool("propose_role", "Propose a condition's role: control, compound, competition of X, reference, qc, automatic.",
         tools._schema({"job_id": _JOB, "condition": _COND, "role": _COND}, ("job_id", "condition", "role")),
         propose_role),
)
BY_NAME = {t.name: t for t in TOOLS}


def schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}} for t in TOOLS]


def build(ctx: tools.Context, name: str, raw_args) -> Proposal:
    """Check a proposal call and build the proposal. Raises Refused. Reads only."""
    tool = BY_NAME.get(name)
    if tool is None:
        raise Refused(f"there is no proposal tool {clean(name, 60)!r}")
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except ValueError:
            raise Refused("the arguments are not valid JSON") from None
    args, problem = tools.validate(tool.parameters, raw_args)
    if problem:
        raise Refused(problem)
    return tool.fn(ctx, **args)


def call(ctx: tools.Context, name: str, raw_args) -> dict:
    """The model's proposal call: at most one proposal per question (ctx.proposal). Never raises. The result
    tells the model that nothing has changed; the proposal itself goes to the window, not through the model."""
    if ctx.proposal is not None:
        return {"error": "one proposal per question, and one is already waiting for the user's Confirm or Cancel"}
    try:
        p = build(ctx, name, raw_args)
    except Refused as exc:
        return {"error": f"not proposed: {clean(exc, 300)}"}
    except Exception as exc:  # noqa: BLE001 - a proposal tool must never take the conversation down
        return {"error": f"not proposed: {clean(exc, 160)}"}
    ctx.proposal = p
    return {"proposed": p.title, "job": p.job_id,
            "status": "shown to the user in a window with Confirm and Cancel; nothing has changed, and a reply in "
                      "the chat confirms nothing"}


# ----------------------------------------------------------------- showing --


def text(p: Proposal) -> str:
    """The proposal as the window shows it: plain text, every line built by Ionomos."""
    out = [p.title, ""]
    if p.note:
        out += [f"Note: {p.note}", ""]
    out += ["What Confirm does:", *(f"  - {c}" for c in p.changes)]
    if p.diff:
        out += ["", "experiment.yaml:", p.diff]
    return "\n".join(out)


def cli_text(p: Proposal) -> str:
    """What `ionomos ask` prints for a proposal: it never applies one, it says how a person would."""
    out = ["Proposed change (nothing has changed; `ionomos ask` never makes a change):", "", text(p), ""]
    if p.action == "retry":
        out += ["To make it, run:", *(f"  {c}" for c in p.commands), f"or in the {p.app_steps[0].lower()}{p.app_steps[1:]}"]
    else:
        out += ["To make it, in the " + p.app_steps[0].lower() + p.app_steps[1:]]
        if p.commands:
            out += ["To try it once without saving it in experiment.yaml:", *(f"  {c}" for c in p.commands)]
    return "\n".join(out)
