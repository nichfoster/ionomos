"""
The roles of an experiment's conditions, and the comparisons that follow from them (D61).

    plan = roles.plan(m, settings)            # roles, the control, the default comparisons, what was skipped
    res  = roles.specific_targets(plan, diffs) # per compound: enriched against the control AND competed off

A chemoproteomics experiment is not a flat list of conditions. Each condition has a role:

    control       the vehicle (DMSO, vehicle, mock, untreated ...): analysis.control_keywords, as before
    compound      a probe or a drug alone (every condition that is nothing else)
    competition   the probe plus an excess of a competitor (Probe_Comp, Probe+Comp, ProbeComp, Comp); it is
                  linked to the compound it competes ("competition of Probe")
    reference     a pooled / bridge channel (plex.py's words: pool, bridge, reference, norm)
    qc            a QC standard run with the samples (QC, HeLa, K562, standard, blank)

Where a role comes from, highest first:
    analysis.roles          {DMSO: control, Probe: compound, Probe_Comp: competition of Probe} in experiment.yaml
                            or config.yaml
    analysis.control        names the control
    an SDRF column          characteristics[role] (or [sample role], comment[role]) of an SDRF in the folder
    the condition's name    control_keywords, then reference and QC words, then competition_keywords
                            (a whole token of the name: `_ - . + space` and a lower-to-upper step split it);
                            competition_keywords_weak (pre, block, 10x ...) count only when the rest of the
                            name is another condition, and are asked about

The competition keywords are NOT confirmed by the lab. `Comp` is the one form seen on the lab PC (the folder
KL6283A_TMTPD_Comp_KL6159A in reference/pc-inventory/); the others are the usual words.

Comparisons (analysis.choose_comparisons, de_type: control, no explicit `comparisons:`). With no competition
condition nothing changes: every condition against the control. With one, the comparisons follow the design:

    compound vs control           enrichment / engagement                     kind "enrichment"
    competition vs its compound   what the competitor displaces (down)        kind "competition"
    competition vs control        what is left with the competitor            kind "remaining"

and nothing else by default: no control against a second control, no pool or QC standard against the control, no
competition of one compound against another compound. `comparisons:`, `de_type: all | others` and
`role_comparisons: false` give the earlier behaviour. With `control:` set, every condition is still compared
with that control (nothing is left out); the competition-against-compound comparisons are added.

Specific targets of a compound: features enriched against the control AND competed off by the competitor, each
at the report's cut-offs (|log2FC| >= log2fc and adjusted p <= alpha; up in compound vs control, down in
competition vs compound). The rule is the field's usual reading of a competition experiment; the lab has not
confirmed it.

Site ratio data (isoDTB) is a competition experiment by construction: every condition is a compound competing
with the probe, and the vehicle is the other isotopic tag of the same run. Its roles say so; cys.py makes the calls.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

ROLES = ("control", "compound", "competition", "reference", "qc")
ROLE_WORDS = {"control": "control", "vehicle": "control", "ctrl": "control",
              "compound": "compound", "treatment": "compound", "treated": "compound", "probe": "compound",
              "drug": "compound",
              "competition": "competition", "competitor": "competition", "competed": "competition",
              "comp": "competition",
              "reference": "reference", "pool": "reference", "pooled": "reference", "bridge": "reference",
              "qc": "qc", "standard": "qc", "qc standard": "qc"}
# a whole token of the condition name. "Comp" is the lab's own (reference/pc-inventory: KL6283A_TMTPD_Comp_KL6159A)
DEFAULT_COMPETITION_KEYWORDS = ("comp", "competition", "competitor", "competed", "compete", "competing", "excess")
# words that also mean other things (pre / post, a blocking step, a 10x dose): they make a competition only when
# the rest of the name is another condition (Probe_pre next to Probe), and the doctor asks. A number followed by x
# (10x, 50x) is treated as one of these.
DEFAULT_COMPETITION_KEYWORDS_WEAK = ("pre", "pretreat", "pretreated", "pretreatment", "block", "blocked", "blocking",
                                     "cold")
QC_WORDS = ("qc", "hela", "k562", "standard", "std", "blank")
KINDS = {"enrichment": "compound vs control: enrichment / engagement",
         "competition": "competition vs its compound: what the competitor displaces",
         "remaining": "competition vs control: what is left with the competitor"}
SPECIFIC_COLUMNS = ["compound", "competition", "control", "id", "label", "description", "call",
                    "enrichment_log2fc", "enrichment_p", "enrichment_padj",
                    "competition_log2fc", "competition_p", "competition_padj", "competed_pct",
                    "remaining_log2fc", "remaining_padj", "n_control", "n_compound", "n_competition"]
CALLS = ("specific", "enriched, not competed", "competed, not enriched", "")
_FOLD = re.compile(r"^\d+(?:\.\d+)?x$")
_SPLIT = re.compile(r"[_\-\s.+/|,()\[\]]+")


class RoleError(ValueError):
    pass


def parse_role(v) -> tuple[str, str]:
    """'competition of Probe' / {role: competition, of: Probe} / 'vehicle' -> (role, linked compound or '')."""
    of = ""
    if isinstance(v, dict):
        of = str(v.get("of") or v.get("competes") or "").strip()
        v = v.get("role", "")
    t = str(v or "").strip()
    m = re.match(r"^(.+?)\s+(?:of|for|with|vs\.?)\s+(.+)$", t, re.I) or re.match(r"^(.+?)\s*[:(]\s*(.+?)\)?$", t)
    if m:
        t, of = m.group(1).strip(), of or m.group(2).strip()
    role = ROLE_WORDS.get(t.lower())
    if role is None:
        raise RoleError(f"role {t!r} is not one of {', '.join(ROLES)}")
    if of and role != "competition":
        raise RoleError(f"only a competition names its compound ('competition of Probe'), not a {role}")
    return role, of


def format_role(role: str, of: str = "") -> str:
    return f"{role} of {of}" if role == "competition" and of else role


def normalise(v) -> dict[str, str]:
    """analysis.roles as written -> {condition: 'control' | 'compound' | 'competition of X' | ...}."""
    if not isinstance(v, dict):
        raise RoleError("roles must map a condition to its role, e.g. {DMSO: control, Probe: compound, "
                        "Probe_Comp: competition of Probe}")
    return {str(c).strip(): format_role(*parse_role(r)) for c, r in v.items() if str(c).strip()}


def tokens(name: str, whole=()) -> list[str]:
    """'Probe_Comp' / 'Probe+Comp' / 'ProbeComp' -> ['probe', 'comp'] (lower case). A token that is one of
    `whole` (the keywords: HeLa, siNT) is kept as it is; any other is also split at a lower-to-upper step."""
    out = []
    for t in _SPLIT.split(str(name)):
        if t.lower() in whole:
            out.append(t.lower())
        else:
            out += [x.lower() for x in re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t).split() if x]
    return out


@dataclass
class Role:
    condition: str
    role: str
    source: str               # where it came from: "analysis.roles", "analysis.control", "SDRF", "name (comp)", ...
    of: str = ""              # competition: the compound it competes
    link: str = ""            # how `of` was found: "name", "the only compound", "analysis.roles"
    competitor: str = ""      # competition: what is left of the name (the competitor), when it says
    sure: bool = True         # False: a weak keyword, or a competition that couldn't be linked
    n: int = 0                # samples

    def text(self) -> str:
        return format_role(self.role, self.of)

    def as_dict(self) -> dict:
        out = {"role": self.role, "source": self.source, "samples": self.n}
        if self.of:
            out["of"], out["linked_by"] = self.of, self.link
        if self.competitor:
            out["competitor"] = self.competitor
        if not self.sure:
            out["sure"] = False
        return out


@dataclass
class Plan:
    roles: dict[str, Role] = field(default_factory=dict)
    control: str | None = None
    active: bool = False                                  # the default comparisons follow the roles
    comparisons: list[tuple[str, str]] = field(default_factory=list)
    kinds: dict[tuple[str, str], str] = field(default_factory=dict)
    triples: list[tuple[str, str, str | None]] = field(default_factory=list)  # (compound, competition, control)
    skipped: list[tuple[str, str]] = field(default_factory=list)              # (what, why)
    notes: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)    # genuinely ambiguous: the doctor asks (ROLES_UNSURE)
    inactive: str = ""                                    # why the roles don't set the comparisons
    by_construction: bool = False                         # site ratio data: every condition competes with the probe

    @property
    def competitions(self) -> list[str]:
        return [c for c, r in self.roles.items() if r.role == "competition"]

    def describe(self) -> str:
        """'DMSO: control (2 samples); Probe: compound (4); Probe_Comp: competition of Probe (4)'."""
        return "; ".join(f"{c}: {r.text()} ({r.n} sample{'s' if r.n != 1 else ''})" if k == 0 else
                         f"{c}: {r.text()} ({r.n})" for k, (c, r) in enumerate(self.roles.items()))

    def as_dict(self) -> dict:
        out = {"conditions": {c: r.as_dict() for c, r in self.roles.items()}, "control": self.control,
               "comparisons_follow_roles": self.active}
        if self.active:
            out["comparisons"] = [{"name": f"{t} vs {c}", "kind": self.kinds.get((t, c), "")}
                                  for t, c in self.comparisons]
            out["not_compared"] = [{"what": w, "why": y} for w, y in self.skipped]
        elif self.inactive:
            out["why_not"] = self.inactive
        if self.by_construction:
            out["by_construction"] = True
        if self.questions:
            out["questions"] = list(self.questions)
        return out


def _find(name: str, conditions: list[str]) -> str | None:
    return next((c for c in conditions if c.lower() == str(name).strip().lower()), None)


def _keyword(toks: list[str], words) -> str | None:
    low = {str(w).strip().lower() for w in words}
    return next((t for t in toks if t in low), None)


def infer(conditions: list[str], s, sizes: dict[str, int] | None = None, sources: dict[str, str] | None = None,
          kind: str = "intensity") -> Plan:
    """Roles for these conditions. s: analysis.Settings. sources: {condition: where its analysis.roles entry came
    from} (analyze() merges an SDRF's roles into the settings and says so here)."""
    from ionomos.downstream import analysis
    from ionomos.downstream.plex import REFERENCE_WORDS

    plan = Plan()
    sizes = sizes or {}
    sources = sources or {}
    if kind == "ratio":  # isoDTB: each condition is a compound competing with the probe; the vehicle is the other tag
        plan.by_construction = True
        plan.inactive = "site ratio data: every condition is tested against 0 (the vehicle is the other isotopic tag)"
        for c in conditions:
            plan.roles[c] = Role(c, "competition", "site ratio data (competitive by construction)", of="the probe",
                                 link="by construction", n=sizes.get(c, 0))
        return plan
    given: dict[str, tuple[str, str]] = {}
    for name, text in (getattr(s, "roles", None) or {}).items():
        c = _find(name, conditions)
        if c is None:
            plan.notes.append(f"analysis.roles names {name!r}, which is not a condition here "
                              f"(conditions: {', '.join(conditions)})")
            continue
        given[c] = parse_role(text)
    try:
        control = analysis.find_control(conditions, s)
    except analysis.AnalysisError:
        control = None  # choose_comparisons reports a control that isn't a condition
    strong = tuple(getattr(s, "competition_keywords", DEFAULT_COMPETITION_KEYWORDS))
    weak = tuple(getattr(s, "competition_keywords_weak", DEFAULT_COMPETITION_KEYWORDS_WEAK))
    control_words = {str(k).lower() for k in s.control_keywords}
    drop = {str(w).lower() for w in (*strong, *weak)}
    whole = control_words | drop | set(QC_WORDS)

    def rest_of(c: str) -> list[str]:
        """The name without its competition words: what is left names the compound (and the competitor)."""
        return [t for t in tokens(c, whole) if t not in drop and not _FOLD.match(t)]

    found: dict[str, tuple[str, bool]] = {}   # competition by name: condition -> (keyword, weak)
    for c in conditions:
        toks = tokens(c, whole)
        if c in given:
            role, of = given[c]
            plan.roles[c] = Role(c, role, sources.get(c, "analysis.roles"), n=sizes.get(c, 0))
            if of:
                plan.roles[c].of = of
        elif c == control:
            plan.roles[c] = Role(c, "control", "analysis.control" if s.control else "name (control_keywords)",
                                 n=sizes.get(c, 0))
        elif c.lower() in control_words:  # a second vehicle (DMSO and Mock): the whole name is a control keyword
            plan.roles[c] = Role(c, "control", "name (control_keywords)", n=sizes.get(c, 0))
        elif REFERENCE_WORDS.search(c):
            plan.roles[c] = Role(c, "reference", "name (pool / bridge / reference)", n=sizes.get(c, 0))
        elif _keyword(toks, QC_WORDS):
            plan.roles[c] = Role(c, "qc", f"name ({_keyword(toks, QC_WORDS)})", n=sizes.get(c, 0))
        else:
            kw = _keyword(toks, strong)
            wk = None if kw else (_keyword(toks, weak) or next((t for t in toks if _FOLD.match(t)), None))
            if kw or wk:
                found[c] = (kw or wk, kw is None)
            plan.roles[c] = Role(c, "compound", "default (not a control)", n=sizes.get(c, 0))
    if control is None:
        control = next((c for c, r in plan.roles.items() if r.role == "control"), None)
    plan.control = control
    # competitions named by a keyword, linked to the compound they compete
    for c, (kw, is_weak) in found.items():
        compounds = [x for x, r in plan.roles.items() if r.role == "compound" and x not in found]
        rest = rest_of(c)
        of, link = _link(rest, compounds, whole)
        if is_weak and link != "name":
            continue  # 'pre' / 'block' / '10x' with nothing to hang on: an ordinary condition
        r = plan.roles[c]
        r.role, r.source, r.sure = "competition", f"name ({kw})", not is_weak
        if of:
            r.of, r.link = of, link
            r.competitor = "_".join(t for t in rest if link != "name" or t not in tokens(of, whole))
        if is_weak:
            plan.questions.append(f"{c} was read as {of} plus a competitor because of {kw!r} in its name, which "
                                  f"can also mean something else (a dose, a time). If it is not a competition, set "
                                  f"analysis.roles {{{c}: compound}}")
    # competitions given by the settings / an SDRF: check or find the link
    for c, r in plan.roles.items():
        if r.role != "competition":
            continue
        compounds = [x for x, q in plan.roles.items() if q.role == "compound"]
        if r.of and r.link:
            continue
        if r.of:  # named in analysis.roles
            hit = _find(r.of, list(plan.roles))
            if hit is None or plan.roles[hit].role != "compound":
                plan.questions.append(f"{c} is a competition of {r.of!r}, which is not a compound condition here "
                                      f"(compounds: {', '.join(compounds) or 'none'})")
                r.of, r.sure = "", False
            else:
                r.of, r.link = hit, r.source
            continue
        r.of, r.link = _link(rest_of(c), compounds, whole)
        if not r.of:
            r.sure = False
            if compounds:
                plan.questions.append(f"{c} is a competition, but which compound it competes can't be read from "
                                      f"the names ({', '.join(compounds)}). Set analysis.roles "
                                      f"{{{c}: competition of {compounds[0]}}}")
    return plan


def _link(rest: list[str], compounds: list[str], whole=()) -> tuple[str, str]:
    """The compound a competition competes: the one its name contains (the longest such), else the only one."""
    best, score, tie = "", 0, False
    for x in compounds:
        tx = tokens(x, whole)
        if tx and all(t in rest for t in tx):
            if len(tx) > score:
                best, score, tie = x, len(tx), False
            elif len(tx) == score:
                tie = True
    if best and not tie:
        return best, "name"
    if len(compounds) == 1 and not tie:
        return compounds[0], "the only compound"
    return "", ""


def plan(m, s) -> Plan:
    """infer() for a QuantMatrix, plus the default comparisons when they follow the roles."""
    conds = m.conditions
    out = infer(conds, s, {c: len(m.samples_of(c)) for c in conds}, (m.meta or {}).get("role_sources"), m.kind)
    if out.by_construction:
        return out
    comps = [c for c in out.competitions]
    if s.comparisons:
        out.inactive = "explicit analysis.comparisons are used"
    elif s.de_type != "control":
        out.inactive = f"de_type: {s.de_type} is used"
    elif not getattr(s, "role_comparisons", True):
        out.inactive = "role_comparisons: false"
    elif not comps:
        out.inactive = "no competition condition: every condition is compared with the control"
    elif len(conds) < 2:
        out.inactive = "only one condition"
    if out.inactive:
        return out
    ctrl = out.control
    done: set[str] = set()

    def add(t: str, c: str, kind: str) -> None:
        if (t, c) not in out.kinds:
            out.comparisons.append((t, c))
            out.kinds[(t, c)] = kind

    for c in conds:
        r = out.roles[c]
        if r.role != "compound":
            continue
        if ctrl:
            add(c, ctrl, "enrichment")
        for k in comps:
            if out.roles[k].of == c:
                add(k, c, "competition")
                if ctrl:
                    add(k, ctrl, "remaining")
                out.triples.append((c, k, ctrl))
                done.add(k)
    for k in comps:
        if k not in done:
            if ctrl:
                add(k, ctrl, "remaining")
            out.skipped.append((k, "no compound to compare it with: only compared with the control" if ctrl
                                else "no compound and no control to compare it with"))
    explicit = bool(s.control) and ctrl is not None  # analysis.control: every condition is still compared with it
    for c in conds:
        r = out.roles[c]
        if explicit and c != ctrl:
            add(c, ctrl, "")
        elif r.role == "control" and c != ctrl:
            out.skipped.append((f"{c} vs {ctrl}", "both are controls"))
        elif r.role in ("reference", "qc"):
            out.skipped.append((c, f"a {'pooled reference' if r.role == 'reference' else 'QC standard'}, not a "
                                   "treatment"))
        elif r.role == "compound" and not ctrl and not any(t == c or k == c for t, k in out.comparisons):
            out.skipped.append((c, "no control and no competition to compare it with"))
    if not out.comparisons:
        out.active = False
        out.inactive = "the roles give no comparison: every condition is compared with the control"
        out.kinds, out.triples, out.skipped = {}, [], []
        return out
    out.active = True
    out.notes.append("competition experiment: " + out.describe() + ". Comparisons follow the design: "
                     + ", ".join(f"{t} vs {c}" for t, c in out.comparisons)
                     + ". Change the roles with analysis.roles, or list comparisons: yourself.")
    for what, why in out.skipped:
        out.notes.append(f"not compared by default: {what} ({why})")
    return out


# --------------------------------------------------------- specific targets --


@dataclass
class Specific:
    compound: str
    competition: str
    control: str
    enrichment: str            # comparison names
    displaced: str
    remaining: str
    rows: list[dict]           # every feature tested in both comparisons, specific ones first
    counts: dict[str, int]
    confidence: str = ""       # "" | "low" | "none": the weaker of the two comparisons
    note: str = ""


def _num(v) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def specific_targets(plan_: Plan, diffs: list) -> list[Specific]:
    """Per (compound, competition, control): the features enriched in compound vs control AND competed off
    (down in competition vs compound), each by the comparison's own significance call (so the report's cut-offs,
    adjusted or raw p as set, and fold change only where there are no replicates)."""
    by = {(d.treatment, d.control): d for d in diffs}
    out = []
    for compound, comp, ctrl in plan_.triples:
        e, k = by.get((compound, ctrl)), by.get((comp, compound))
        if e is None or k is None:
            continue
        rem = by.get((comp, ctrl))
        ke = {r["index"]: r for r in k.rows}
        kr = {r["index"]: r for r in rem.rows} if rem is not None else {}
        rows, counts = [], {c: 0 for c in CALLS[:3]}
        counts["tested"] = 0
        for r in e.rows:
            q = ke.get(r["index"])
            if q is None or r["log2fc"] is None or q["log2fc"] is None:
                continue
            counts["tested"] += 1
            up, off = r["significant"] == "up", q["significant"] == "down"
            call = CALLS[0] if up and off else CALLS[1] if up else CALLS[2] if off else ""
            if call:
                counts[call] += 1
            z = kr.get(r["index"]) or {}
            fc = q["log2fc"]
            rows.append({"compound": compound, "competition": comp, "control": ctrl, "index": r["index"],
                         "id": r["id"], "label": r["label"], "description": r["description"], "call": call,
                         "enrichment_log2fc": r["log2fc"], "enrichment_p": _num(r["pvalue"]),
                         "enrichment_padj": _num(r["qvalue"]),
                         "competition_log2fc": fc, "competition_p": _num(q["pvalue"]),
                         "competition_padj": _num(q["qvalue"]),
                         "competed_pct": 100 * (1 - 2 ** fc) if fc < 0 else 0.0,
                         "remaining_log2fc": _num(z.get("log2fc")), "remaining_padj": _num(z.get("qvalue")),
                         "n_control": r["n_control"], "n_compound": r["n_treatment"],
                         "n_competition": q["n_treatment"]})
        rows.sort(key=lambda x: (CALLS.index(x["call"]), -(x["enrichment_log2fc"] - x["competition_log2fc"])))
        conf = "none" if "none" in (e.confidence, k.confidence) else "low" if "low" in (e.confidence, k.confidence) \
            else ""
        note = ""
        if conf == "none":
            note = ("Fold change only: a comparison behind these calls has no replicates, so they rest on "
                    "|log2FC| alone. Treat them as leads to confirm.")
        elif conf == "low":
            note = ("Low confidence: a group behind these calls has fewer samples than the analysis asks for; "
                    "its p-values borrow the replicate spread from the rest of the experiment.")
        out.append(Specific(compound, comp, ctrl, e.name, k.name, rem.name if rem is not None else "", rows, counts,
                            conf, note))
    return out


def specific_table_rows(results: list[Specific]) -> list[dict]:
    return [r for res in results for r in res.rows]


def specific_summary(results: list[Specific], table: str | None = None) -> list[dict]:
    """analysis.json "specific_targets"."""
    return [{"compound": r.compound, "competition": r.competition, "control": r.control,
             "enrichment": r.enrichment, "displaced": r.displaced, "remaining": r.remaining,
             "rule": "significant up in the enrichment comparison and significant down in the competition "
                     "comparison, each at the analysis cut-offs",
             "tested": r.counts["tested"], "specific": r.counts[CALLS[0]],
             "enriched_not_competed": r.counts[CALLS[1]], "competed_not_enriched": r.counts[CALLS[2]],
             "confidence": r.confidence or "normal", "note": r.note,
             "top": [x["label"] or x["id"] for x in r.rows if x["call"] == CALLS[0]][:15], "table": table}
            for r in results]


def report_payload(plan_: Plan | None, results: list[Specific], diffs: list) -> dict:
    """The report's "roles" data (report.js renderSpecific): the roles, and per compound which comparisons make
    its specific-targets view. The page recomputes the calls with its live cut-offs from the comparisons."""
    if plan_ is None:
        return {}
    names = [d.name for d in diffs]
    out = {"conditions": [{"name": c, "role": r.role, "of": r.of, "n": r.n, "source": r.source}
                          for c, r in plan_.roles.items()],
           "active": plan_.active, "control": plan_.control, "specific": []}
    for r in results:
        out["specific"].append({"compound": r.compound, "competition": r.competition, "control": r.control,
                                "e": names.index(r.enrichment), "k": names.index(r.displaced),
                                "r": names.index(r.remaining) if r.remaining in names else None,
                                "conf": r.confidence, "note": r.note})
    return out
