"""The docs are the spec (CLAUDE.md): these tests fail when a doc and the code drift apart.

Each test reads the code for what exists (the CLI's parser, what `configio.dump_config` writes, the help ids the
code opens, `names.py`, the testbed's scenarios) and the Markdown for what is said, and names the file and the
thing to fix. Nothing here is about wording; only about facts a reader would act on.

    commands     every subcommand in cli.py's docstring, ARCHITECTURE's cli.py row and README;
                 every `ionomos X` a doc shows is a real command
    config       every key dump_config writes is documented (ARCHITECTURE's config section or a doc it names)
    help         every help id the code or a doc points at exists
    links        every relative link in docs/*.md and the top-level *.md resolves, #anchors included
    decisions    every Dnn cited exists; DECISIONS headings unique and in order
    changelog    no "not built yet" / "wave 2" sentence names what CHANGELOG's Unreleased says was added
    testbed      every scenario is in TESTING.md's table, and its counts are right
    on disk      every file / folder name in names.py appears in ARCHITECTURE's on-disk layout

The allow-lists below are short and say why; empty them when the reason goes away.
"""
from __future__ import annotations

import argparse
import ast
import inspect
import re
from functools import cache, lru_cache
from pathlib import Path
from unittest import mock

import yaml

from ionomos import cli, configio, names, testbed
from ionomos import help as helpdoc

SRC = Path(cli.__file__).resolve().parent      # src/ionomos
PKG = SRC.parents[1]                           # the ionomos/ project folder
REPO = PKG.parent
DOCS = REPO / "docs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The text under a `## heading` (to the next heading of the same or a higher level)."""
    m = re.search(rf"^(#+) {re.escape(heading)}[^\n]*\n", text, re.M)
    assert m, f"no heading {heading!r}"
    level = len(m.group(1))
    end = re.compile(rf"^#{{1,{level}}} ", re.M).search(text, m.end())
    return text[m.end():end.start() if end else len(text)]


def _without_code(text: str) -> str:
    """Markdown with fenced code blocks and inline code spans blanked (links and headings in code don't count)."""
    text = re.sub(r"^(```|~~~).*?^\1[^\n]*$", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S | re.M)
    return re.sub(r"`[^`\n]*`", "``", text)


def _md_files() -> list[Path]:
    return sorted([*DOCS.glob("*.md"), *REPO.glob("*.md")])


# ----------------------------------------------------------------- commands --

# Hidden commands main() handles before argparse (testbed stand-ins, the Sage job, the display probe).
HIDDEN = {"probe-gui", "sage-job", "fake-sage", "fake-rawparser", "fake-maxquant", "fake-diann", "fake-fragpipe"}
# Commands a doc plans but that are not built: {command: where}. Remove an entry when the command exists.
PLANNED: dict[str, str] = {}
# README lists every command once the coordinator adds the line from round4/notes-docs-consistency.md (README is
# edited only through notes this round). Empty this set when that line is in README.md.
README_PENDING: set[str] = set()


class _Parser(Exception):
    pass


@lru_cache(maxsize=1)
def _parser() -> argparse.ArgumentParser:
    """The real parser main() builds (cli.build_parser when there is one; else caught at parse_args)."""
    build = getattr(cli, "build_parser", None)
    if build is not None:
        return build()

    def grab(self, *_a, **_k):
        raise _Parser(self)

    with mock.patch.object(argparse.ArgumentParser, "parse_args", grab):
        try:
            cli.main(["status"])
        except _Parser as got:
            return got.args[0]
    raise AssertionError("cli.main never built its parser")


def _commands() -> set[str]:
    sub = next(a for a in _parser()._actions if isinstance(a, argparse._SubParsersAction))
    return set(sub.choices)


def _docstring_commands() -> set[str]:
    out = set()
    for first, alt in re.findall(r"^ {4}ionomos ([a-z][\w-]*)(?: *\| *([a-z][\w-]*))?", cli.__doc__, re.M):
        out |= {first, alt} - {""}
    return out


def _architecture_cli_row() -> set[str]:
    row = next(line for line in _read(DOCS / "ARCHITECTURE.md").splitlines() if line.startswith("| `cli.py` |"))
    listed = re.match(r"\| `cli\.py` \| `ionomos ([^`]+)`", row).group(1)
    return {part.split()[0] for part in listed.split(" / ")}


def test_every_command_is_in_the_cli_docstring():
    missing = sorted(_commands() - _docstring_commands())
    assert not missing, f"cli.py's docstring (the top of `ionomos --help`) lacks: {missing}"
    stale = sorted(_docstring_commands() - _commands())
    assert not stale, f"cli.py's docstring lists commands that don't exist: {stale}"


def test_every_command_is_in_architectures_cli_row():
    missing = sorted(_commands() - _architecture_cli_row())
    assert not missing, f"docs/ARCHITECTURE.md's `cli.py` row lacks: {missing}"
    stale = sorted(_architecture_cli_row() - _commands())
    assert not stale, f"docs/ARCHITECTURE.md's `cli.py` row lists commands that don't exist: {stale}"


def test_every_command_is_in_the_readme():
    readme = _read(REPO / "README.md")
    said = set(re.findall(r"\bionomos ([a-z][\w-]*)", readme))
    if re.search(r"^## Commands", readme, re.M):  # a list of `name`s under its own heading counts too
        said |= set(re.findall(r"`([a-z][\w-]*)(?: [a-z]+)?`", _section(readme, "Commands")))
    missing = sorted(_commands() - said - README_PENDING)
    assert not missing, f"README.md doesn't mention: {missing}"
    done = sorted(README_PENDING & said)
    assert not done, f"README.md now mentions {done}: take them out of README_PENDING"


def _shown_commands(text: str) -> set[str]:
    """`ionomos X` in inline code, and lines of fenced code that start with it (a command someone would type)."""
    out = set(re.findall(r"`ionomos ([a-z][\w-]*)", text))
    for block in re.findall(r"^```[^\n]*\n(.*?)^```", text, re.S | re.M):
        out |= set(re.findall(r"^ {0,4}(?:\$ |> )?ionomos +([a-z][\w-]*)", block, re.M))
    return out


def test_every_command_a_doc_shows_exists():
    real = _commands() | HIDDEN | set(PLANNED)
    texts = {p.relative_to(REPO).as_posix(): _read(p) for p in [*_md_files(), *(SRC / "help").glob("*.md")]}
    wrong = sorted(f"{name}: ionomos {c}" for name, text in texts.items() for c in _shown_commands(text) - real)
    assert not wrong, f"docs show commands that don't exist: {wrong}"
    shown = {c for t in texts.values() for c in _shown_commands(t)}
    unused = sorted(set(PLANNED) - shown)
    assert not unused, f"no doc shows {unused} any more: take them out of PLANNED"
    built = sorted(set(PLANNED) & _commands())
    assert not built, f"{built} exist now: take them out of PLANNED"


# ------------------------------------------------------------------- config --

# User data under a key, not settings: their children are names the lab chose.
USER_MAPS = {("naming", "condition_codes"), ("naming", "methods"), ("users", "aliases"), ("analysis", "roles"),
             ("analysis", "times"), ("analysis", "doses"), ("analysis", "sdrf")}


def _optional_analysis_keys() -> list[str]:
    """The analysis keys dump_config writes only when set: configio's own tuples (`_AN_LISTS`, `_AN_SCALARS`),
    plus any `for k in (...)` key tuple still inside dump_config."""
    keys = [*getattr(configio, "_AN_LISTS", ()), *getattr(configio, "_AN_SCALARS", ())]
    tree = ast.parse(inspect.getsource(configio.dump_config))
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            vals = [e.value for e in node.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if vals and len(vals) == len(node.iter.elts) and all(re.fullmatch(r"[a-z_]+", v) for v in vals):
                keys += vals
    assert "tmt_min_purity" in keys, "configio's optional analysis keys were not found: update this helper"
    return keys


def _full_config() -> dict:
    """defaults() with every key dump_config can write set, so the written file shows them all."""
    from ionomos.assistant import DEFAULTS as ASSISTANT

    d = configio.defaults("C:/Fragpipe_Auto", "C:/Fragpipe_General")
    for k in _optional_analysis_keys():
        d["analysis"].setdefault(k, 1)
    d["analysis"].update(roles={"Pool": "reference"}, times={"T0": 0}, doses={"C1": 1}, sdrf={"instrument": "x"})
    d["naming"].update(date_formats=["%Y%m%d"], methods={"DIA": {"template": "{condition}_{biorep}"}})
    d["qc_trend"]["max_file_mb"] = 4096
    d["assistant"] = dict(ASSISTANT)
    return d


def _written_keys() -> set[tuple[str, ...]]:
    data = yaml.safe_load(configio.dump_config(_full_config()))
    out: set[tuple[str, ...]] = set()

    def walk(node: dict, path: tuple[str, ...]):
        for k, v in node.items():
            p = (*path, "on" if k is True else str(k))  # YAML 1.1 reads notify's bare `on:` as True
            if p[:1] == ("methods",):
                if len(p) == 3:
                    out.add(("methods", "<key>", p[2]))
                elif isinstance(v, dict):
                    walk(v, p)
                continue
            out.add(p)
            if isinstance(v, dict) and p not in USER_MAPS and len(p) < 3:
                walk(v, p)

    walk(data, ())
    return out


def _config_docs() -> tuple[str, list[str]]:
    """ARCHITECTURE's config section, and every doc ARCHITECTURE names (where it sends the reader for the rest)."""
    arch = _read(DOCS / "ARCHITECTURE.md")
    named = sorted({m for m in re.findall(r"\b([A-Z][A-Z_]+\.md)\b", arch) if (DOCS / m).is_file()})
    corpus = [_section(arch, "Configuration (`config.yaml`)"), _section(arch, "Notifications (D58)")]
    corpus += [_read(DOCS / m) for m in named]
    return "\n".join(corpus), named


def _documented(key: tuple[str, ...], corpus: str) -> bool:
    k = re.escape(key[-1])
    parent = re.escape(key[-2]) if len(key) > 1 and key[-2] != "<key>" else None
    pats = [rf"(?<![\w-]){k}:", rf"`{k}`", rf"`[^`\s]*\.{k}`", rf"`{k}[:= ]"]
    if parent:
        pats.append(rf"\b{parent}\.{k}\b")
    return any(re.search(p, corpus) for p in pats)


def test_every_config_key_the_writer_writes_is_documented():
    corpus, named = _config_docs()
    missing = sorted(".".join(k) for k in _written_keys() if not _documented(k, corpus))
    assert not missing, (f"config keys written by configio.dump_config that neither ARCHITECTURE's "
                         f"'Configuration' section nor a doc it names ({', '.join(named)}) explains: {missing}")


def test_the_config_check_sees_the_optional_keys():
    keys = _written_keys()
    assert ("analysis", "tmt_min_purity") in keys and ("analysis", "irs") in keys
    assert ("notify", "email", "password_env") in keys and ("methods", "<key>", "postprocess") in keys
    assert ("assistant", "while_searching") in keys and ("library", "time_limit_s") in keys


# --------------------------------------------------------------------- help --

HELP_NS = ("faq", "issue", "attention", "intake", "search", "glossary", "qc", "report", "start", "safety", "trouble",
           "library")
FILE_EXT = {"html", "tsv", "css", "js", "txt", "json", "jsonl", "parquet", "csv", "xlsx", "md", "yaml", "py", "gmt",
            "fas", "fasta", "log", "zip", "raw", "workflow", "pdf"}
_ID = re.compile(rf"^(?:{'|'.join(HELP_NS)})\.([A-Za-z0-9_][A-Za-z0-9_-]*)$")


def _help_ids() -> set[str]:
    return set(helpdoc.entries()) | {s.id for s in helpdoc.sections()}


def _code_help_ids() -> dict[str, set[str]]:
    """Every string literal in the package that is shaped like a help id (`faq.app`, `trouble.watcher` ...)."""
    out: dict[str, set[str]] = {}
    for py in SRC.rglob("*.py"):
        if py.parent.name == "help":
            continue
        for node in ast.walk(ast.parse(_read(py))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                m = _ID.match(node.value)
                if m and m.group(1).lower() not in FILE_EXT:
                    out.setdefault(node.value, set()).add(py.relative_to(SRC).as_posix())
    return out


def test_every_help_id_the_code_names_exists():
    from ionomos import uinav
    from ionomos.assistant import askui

    ids = _help_ids()
    found = _code_help_ids()
    assert {"faq.app", "faq.check-accuracy", "faq.figure-style"} <= set(found), "the scan found the app's Help buttons"
    named = dict(found)
    named |= {uinav.tab_help(t[0]): {"uinav.TABS"} for t in uinav.TABS}
    named |= {h: {"assistant/askui.HELP"} for h in askui.HELP.values()}
    named |= {h: {"help.HOLD_TOPICS"} for _rx, h in helpdoc.HOLD_TOPICS}
    missing = sorted(f"{h} ({', '.join(sorted(where))})" for h, where in named.items() if h not in ids)
    assert not missing, f"help ids the code opens that no help/*.md entry has: {missing}"


def test_every_help_id_a_doc_names_exists():
    """`faq.library`, `issue.NO_TABLE`, `ionomos help pca` in the docs and the changelog point at real help."""
    ids = _help_ids()
    # attention.* / intake.* are left out: in the docs they are mostly the modules' names (`attention.KINDS`).
    pat = re.compile(r"`((?:faq|glossary|trouble|safety|start)\.[A-Za-z0-9_-]+|search\.hold-[a-z0-9-]+|"
                     r"issue\.[A-Z][A-Z0-9_]+)`")
    placeholders = {"issue.CODE", "TOPIC"}  # HELP.md's and the docstrings' stand-ins
    wrong = []
    for p in [*_md_files(), *(SRC / "help").glob("*.md")]:
        text = _read(p)
        for hid in pat.findall(text):
            if hid not in ids and hid not in placeholders and hid.rsplit(".", 1)[-1] not in FILE_EXT:
                wrong.append(f"{p.name}: {hid}")
        for topic in re.findall(r"`ionomos help ([A-Za-z0-9_.-]+)`", text):
            if not topic.startswith("-") and topic not in placeholders and helpdoc.topic(topic) is None:
                wrong.append(f"{p.name}: ionomos help {topic}")
    assert not wrong, f"help ids named in docs that don't exist: {sorted(set(wrong))}"


# -------------------------------------------------------------------- links --


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped (not - or _), spaces to -."""
    h = re.sub(r"`([^`]*)`", r"\1", heading.strip())
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)
    h = re.sub(r"[^\w\- ]", "", h.lower())
    return h.replace(" ", "-")


@cache
def _anchors(path: Path) -> frozenset[str]:
    text = _read(path)
    seen: dict[str, int] = {}
    out = set()
    for line in re.sub(r"^```.*?^```", "", text, flags=re.S | re.M).splitlines():
        m = re.match(r"^#{1,6} +(.+?) *#*$", line)
        if m:
            s = _slug(m.group(1))
            n = seen.get(s, 0)
            out.add(s if n == 0 else f"{s}-{n}")
            seen[s] = n + 1
    out |= set(re.findall(r"<a (?:name|id)=\"([^\"]+)\"", text))
    return frozenset(out)


def _links(text: str) -> list[str]:
    return re.findall(r"\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)", _without_code(text))


def test_every_relative_link_in_the_docs_resolves():
    broken = []
    for p in _md_files():
        for target in _links(_read(p)):
            if re.match(r"[a-z][a-z0-9+.-]*:", target):  # https:, mailto:
                continue
            path, _, anchor = target.partition("#")
            dest = (p.parent / path).resolve() if path else p
            if not dest.exists():
                broken.append(f"{p.relative_to(REPO).as_posix()}: {target} (no such file)")
            elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
                broken.append(f"{p.relative_to(REPO).as_posix()}: {target} (no heading #{anchor})")
    assert not broken, f"links that lead nowhere: {broken}"


def test_the_slugs_are_githubs():
    assert _slug("The road to publishing") == "the-road-to-publishing"
    assert _slug("Wave 2: the app") == "wave-2-the-app"
    assert _slug("What the app does (`library_tab.py`)") == "what-the-app-does-library_tabpy"
    assert _slug("D59 — FragPipe is run") == "d59--fragpipe-is-run"


# ---------------------------------------------------------------- decisions --

# Decisions cited before their entry is written: {number: where}. The coordinator writes D96 from the round's
# notes (docs/EXAMPLE.md, example.py: `ionomos example`); empty this when DECISIONS.md has it.
DECISION_PENDING: dict[int, str] = {}
# a number taken by a decision in a pull request that is not merged into this branch yet
DECISION_GAPS: dict[int, str] = {105: "PR #186", 106: "PR #186", 107: "PR #187"}
_DREF = re.compile(r"(?<![\w-])D(\d{1,3})(?![\w])")


def _decision_headings() -> list[int]:
    return [int(n) for n in re.findall(r"^#{2,4} D(\d+)\b", _read(DOCS / "DECISIONS.md"), re.M)]


def test_decision_headings_are_unique_and_in_order():
    nums = _decision_headings()
    assert len(nums) > 90
    dupes = sorted({n for n in nums if nums.count(n) > 1})
    assert not dupes, f"docs/DECISIONS.md has these decision numbers twice: {dupes}"
    out_of_order = [f"D{a} before D{b}" for a, b in zip(nums, nums[1:], strict=False) if b <= a]
    assert not out_of_order, f"docs/DECISIONS.md headings out of order: {out_of_order}"
    gaps = sorted(set(range(1, max(nums) + 1)) - set(nums) - set(DECISION_GAPS))
    assert not gaps, f"docs/DECISIONS.md skips: {gaps}"
    closed = sorted(n for n in DECISION_GAPS if n in nums)
    assert not closed, f"DECISIONS.md now has {closed}: take them out of DECISION_GAPS"


def _cited() -> dict[int, set[str]]:
    files = [REPO / "CHANGELOG.md", *_md_files(), *(SRC / "help").glob("*.md"), *SRC.rglob("*.py"),
             *(PKG / "tests").glob("*.py")]
    out: dict[int, set[str]] = {}
    for p in dict.fromkeys(files):
        if p.name == "DECISIONS.md" or p == Path(__file__).resolve():
            continue
        for n in _DREF.findall(_read(p)):
            out.setdefault(int(n), set()).add(p.relative_to(REPO).as_posix())
    return out


def test_every_cited_decision_exists():
    have = set(_decision_headings())
    cited = _cited()
    assert {24, 46, 91} <= set(cited)
    missing = sorted(f"D{n} ({', '.join(sorted(cited[n]))})" for n in cited if n not in have and n not in
                     DECISION_PENDING)
    assert not missing, f"decisions cited but not in docs/DECISIONS.md: {missing}"
    written = sorted(n for n in DECISION_PENDING if n in have)
    assert not written, f"DECISIONS.md now has {written}: take them out of DECISION_PENDING"


def test_every_decision_cited_in_docs_decisions_itself_exists():
    text = _read(DOCS / "DECISIONS.md")
    have = set(_decision_headings())
    wrong = sorted({int(n) for n in _DREF.findall(text)} - have - set(DECISION_PENDING))
    assert not wrong, f"docs/DECISIONS.md cites decisions it doesn't have: {wrong}"


# ---------------------------------------------------------------- changelog --

# Words that say something is still to come. DECISIONS.md and CHANGELOG.md are logs (true when written) and
# ROADMAP.md is the plan, so only the docs that describe today are read.
FUTURE = re.compile(r"\bwave \d\b|\bnot (?:yet )?built\b|\bbuilt yet\b|\bawaits?\b|\bcoming (?:in|soon)\b|"
                    r"\bwill be (?:added|built)\b|\bnot (?:yet )?implemented\b", re.I)
LOGS = {"DECISIONS.md", "CHANGELOG.md", "ROADMAP.md"}
# Names every paragraph uses; naming one says nothing about what was added.
GENERIC = {"config.yaml", "experiment.yaml", "ionomos.json", "analysis.json", "report.html", "psm.tsv"}


def _unreleased() -> str:
    text = _read(REPO / "CHANGELOG.md")
    start = text.index("## [Unreleased]")
    return text[start:text.index("\n## [", start + 1)]


def _paragraphs(text: str) -> list[str]:
    """Paragraphs, with a heading kept together with the paragraph under it; each table row is one."""
    out, cur = [], []
    for line in text.splitlines():
        if line.startswith("|"):
            out.append(line)
            continue
        if not line.strip():
            if cur and not (len(cur) == 1 and cur[0].startswith("#")):
                out.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return out


def test_no_doc_says_something_the_changelog_added_is_still_to_come():
    """A paragraph that says "not built yet" / "wave 2" / "awaits" must not name a file, command or setting that
    CHANGELOG's Unreleased says was added or changed."""
    added = {t for t in re.findall(r"`([^`\n]+)`", _unreleased()) if re.search(r"[A-Za-z]{3}", t)} - GENERIC
    added |= {f"ionomos {c}" for c in re.findall(r"`ionomos ([a-z][\w-]*)", _unreleased())}
    found = []
    for p in [*_md_files(), *(SRC / "help").glob("*.md")]:
        if p.name in LOGS:
            continue
        text = re.sub(r"^```.*?^```", "", _read(p), flags=re.S | re.M)
        for para in _paragraphs(text):
            m = FUTURE.search(para)
            if not m:
                continue
            named = set(re.findall(r"`([^`\n]{3,})`", para))
            named |= {f"ionomos {c}" for c in re.findall(r"`ionomos ([a-z][\w-]*)", para)}
            clash = sorted(named & added)
            if clash:
                found.append(f"{p.name}: '{m.group(0)}' in \"{para.splitlines()[0][:70]}…\" names {clash}")
    assert not found, f"docs that call shipped work future (CHANGELOG [Unreleased] has it): {found}"


# CHANGELOG.md is edited only through notes this round; the coordinator merges its two "### Added" sections.
# Set this to [] when [Unreleased] has one of each.
CHANGELOG_PENDING: list[str] = []


def test_the_changelog_unreleased_has_one_heading_per_kind():
    kinds = re.findall(r"^### (\w+)", _unreleased(), re.M)
    dupes = sorted({k for k in kinds if kinds.count(k) > 1})
    assert dupes == CHANGELOG_PENDING, (f"CHANGELOG.md [Unreleased] has two '### {dupes}' sections"
                                        if dupes else "CHANGELOG_PENDING can be emptied")


# ------------------------------------------------------------------ testbed --


def test_every_testbed_scenario_is_in_testing_md():
    text = _read(DOCS / "TESTING.md")
    table = _section(text, "What each sample proves")
    rows = re.findall(r"^\| `([a-z0-9_]+)` \|", table, re.M)
    missing = sorted(set(testbed.SAMPLES) - set(rows))
    assert not missing, f"testbed scenarios missing from TESTING.md's 'What each sample proves': {missing}"
    stale = sorted(set(rows) - set(testbed.SAMPLES))
    assert not stale, f"TESTING.md's 'What each sample proves' lists scenarios testbed.py doesn't have: {stale}"
    n = len(testbed.SAMPLES)
    counts = re.findall(r"\b(\d+) sample drops\b|\bthe (\d+) samples and what each proves", text)
    counts = [int(a or b) for a, b in counts]
    assert counts and all(c == n for c in counts), f"TESTING.md says {counts} sample drops; testbed.py has {n}"


# ------------------------------------------------------------------ on disk --

# names.py constants that are not file or folder names on the lab's disk, with where they are instead.
NOT_ON_DISK = {
    "APP": "the product name", "SLUG": "the package name", "TASK": "the Task Scheduler task's name",
    "ENV_CONFIG": "an environment variable", "REPO_URL": "a web address", "RELEASES_URL": "a web address",
    "APPDATA_DIR": "%APPDATA%'s folder, in the layout as %APPDATA%\\Ionomos",
    "BUNDLE_MANIFEST": "inside the bundle zip (ARCHITECTURE 'The bundle')",
    "BUNDLE_README": "inside the bundle zip (ARCHITECTURE 'The bundle')",
    "FAKE_FP_MODE_FILE": "the testbed's sample drops only (testbed.py)",
    "REPORT_PREFIX": "the zip name Report a problem used before the bundle; only recognised",
    "RUN_FINGERPRINT_GLOB": "a glob over FINGERPRINT_FILE's name",
}


def _disk_names() -> dict[str, str]:
    out = {}
    for k, v in vars(names).items():
        if k.isupper() and isinstance(v, str) and not k.startswith("LEGACY") and k not in NOT_ON_DISK:
            out[k] = v.replace("{plex}", "<plex>")
    return out


def test_every_names_py_file_name_is_in_the_on_disk_layout():
    layout = _section(_read(DOCS / "ARCHITECTURE.md"), "On-disk layout (proteomics PC)")
    assert {"STATUS_FILE", "RUN_DIR", "LIBRARY_FILE", "UI_STATE_FILE"} <= set(_disk_names())
    missing = sorted(f"{k} = {v!r}" for k, v in _disk_names().items() if v not in layout)
    assert not missing, f"names.py names missing from ARCHITECTURE.md's 'On-disk layout': {missing}"
    gone = sorted(k for k in NOT_ON_DISK if not hasattr(names, k))
    assert not gone, f"NOT_ON_DISK names constants names.py no longer has: {gone}"
