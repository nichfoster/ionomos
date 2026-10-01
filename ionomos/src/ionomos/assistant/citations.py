"""
Grounded or silent: every paragraph of an answer must cite something a tool returned in this conversation.

    reg = Registry()                        # filled by the tools (tools.py) as they return data
    reg.add("log", "3#41", "MSFragger: Java heap space")
    v = check("FragPipe ran out of memory [log:3#41].", reg)
    v.ok, v.valid, v.invalid, v.uncited

The citation forms (ROADMAP Phase 6, D49; `job` added in D57):

    [issue:CODE]        a doctor issue code            [help:ID]          a help entry
    [log:JOB#LINE]      a line of a job's engine log   [analysis:FIELD]   a field of results/analysis.json
    [job:ID]            a job in the ledger            (also [analysis:JOB#FIELD], [log:JOB#A-B], and
                                                        several in one bracket: [log:3#41, help:search.failed])

Ionomos, not the model, decides what exists: a citation is valid only when a tool call of this conversation
returned that code, line, entry, field or job. An answer passes when it has no invalid citation and every
paragraph has at least one valid one. Anything else is not shown (assistant.fallback takes over).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = ("issue", "log", "help", "analysis", "job")
_BRACKET = re.compile(r"\[\s*((?:issue|log|help|analysis|job)\s*:[^\[\]]{1,200})\]", re.IGNORECASE)
_PART = re.compile(r"^(?:(issue|log|help|analysis|job)\s*:\s*)?(\S{1,120})$", re.IGNORECASE)
_RANGE = re.compile(r"^(\d+)#(\d+)-(\d+)$")
MAX_RANGE = 20  # [log:3#40-45]: every line of the range must have been returned


@dataclass
class Registry:
    """What the tools returned in this conversation: (kind, ref) -> a short label for the Sources list."""
    items: dict[tuple[str, str], str] = field(default_factory=dict)

    def add(self, kind: str, ref, label: str = "") -> None:
        key = (kind, _norm(kind, str(ref)))
        if key not in self.items or (label and not self.items[key]):
            self.items[key] = label

    def has(self, kind: str, ref: str) -> bool:
        return (kind, _norm(kind, ref)) in self.items

    def label(self, kind: str, ref: str) -> str:
        return self.items.get((kind, _norm(kind, ref)), "")


def _norm(kind: str, ref: str) -> str:
    ref = ref.strip().strip(".,;")
    if kind == "issue":
        return ref.upper()
    if kind == "job":
        return ref.lstrip("#")
    return ref


@dataclass
class Verdict:
    valid: list[str] = field(default_factory=list)      # "kind:ref", in order, no repeats
    invalid: list[str] = field(default_factory=list)
    uncited: list[str] = field(default_factory=list)    # the start of each paragraph without a valid citation

    @property
    def ok(self) -> bool:
        return bool(self.valid) and not self.invalid and not self.uncited

    def why(self) -> str:
        if self.invalid:
            return "cited something no tool returned: " + ", ".join(f"[{c}]" for c in self.invalid[:5])
        if not self.valid:
            return "no citation"
        if self.uncited:
            return f"{len(self.uncited)} paragraph(s) without a citation"
        return ""


def found(text: str) -> list[tuple[str, str]]:
    """Every (kind, ref) cited in a text, in order. A malformed part of a bracket comes back as kind ''."""
    out = []
    for m in _BRACKET.finditer(text or ""):
        kind = ""
        for part in re.split(r"[,;]", m.group(1)):
            pm = _PART.match(part.strip())
            if not pm:
                out.append(("", part.strip()))
                continue
            kind = (pm.group(1) or kind).lower()
            out.append((kind, _norm(kind, pm.group(2))))
    return out


def _exists(kind: str, ref: str, reg: Registry) -> bool:
    if kind == "log":
        m = _RANGE.match(ref)
        if m:
            job, a, b = m.group(1), int(m.group(2)), int(m.group(3))
            return a <= b and b - a < MAX_RANGE and all(reg.has("log", f"{job}#{n}") for n in range(a, b + 1))
    return bool(kind) and reg.has(kind, ref)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def check(text: str, reg: Registry) -> Verdict:
    v = Verdict()
    for para in paragraphs(text):
        good = False
        for kind, ref in found(para):
            cite = f"{kind}:{ref}" if kind else ref
            if _exists(kind, ref, reg):
                good = True
                if cite not in v.valid:
                    v.valid.append(cite)
            elif cite not in v.invalid:
                v.invalid.append(cite)
        if not good:
            v.uncited.append(para[:60])
    return v


def sources(valid: list[str], reg: Registry) -> list[str]:
    """One line per citation of a shown answer, from what the tools returned (never from the model)."""
    out = []
    for cite in valid:
        kind, ref = cite.split(":", 1)
        label = reg.label(kind, ref)
        out.append(f"[{cite}] {label}".rstrip())
    return out
