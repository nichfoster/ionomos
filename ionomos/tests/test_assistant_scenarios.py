"""The assistant's scenario corpus, replayed through the scripted fake model (ROADMAP Phase 6.1, D57).

Each scenario in tests/assistant_scenarios/ is a fixture state built with the testbed and its fake engines, a
question, the turns a scripted model sends, a rubric and what the harness must do with those turns. This
tests the harness: the tool loop, the argument validators, the citation check, the fallbacks and the audit
log. It does not test any real model's answers; the rubrics are what a real model is scored on, by hand, on
the PC (docs/ASSISTANT.md). Nothing here opens a socket: the fake model is the client's transport.
"""
import json
import re
from dataclasses import replace

import pytest

from ionomos import assistant
from ionomos.assistant import audit, fake, proposals, tools
from tests import assistant_scenarios as corpus
from tests.assistant_scenarios import fixtures

SCENARIOS = corpus.load()
READ_ONLY = {t.name for t in tools.TOOLS}
PROPOSE = {t.name for t in proposals.TOOLS}


@pytest.fixture(scope="module")
def states(tmp_path_factory):
    """Fixture states, built once each (the assistant only reads them)."""
    built = {}

    def get(name: str):
        if name not in built:
            built[name] = fixtures.build(name, tmp_path_factory.mktemp(name) / "bed")
        return built[name]

    return get


def _run(s: dict, states, tmp_path):
    cfg = states(s["state"])
    settings = assistant.settings_from({"enabled": True, "model": fake.MODEL, "stream": bool(s.get("stream")),
                                        **s.get("settings", {})})
    cfg = replace(cfg, assistant=settings)
    item_id = None
    if s.get("item"):
        from ionomos import attention

        (item_id,) = [i.id for i in attention.items(cfg.log_dir) if i.id.startswith(s["item"])]
    model = fake.ScriptedModel(s["model"], stream=bool(s.get("stream")))
    answer = assistant.ask(cfg, s["question"], experiment=s.get("experiment"), item_id=item_id, transport=model,
                           audit_path=tmp_path / "audit.jsonl")
    return answer, model, audit.read(tmp_path / "audit.jsonl")


def test_the_corpus_is_big_enough_and_covers_what_the_roadmap_asks():
    assert len(SCENARIOS) >= 30
    states_used = {s["state"] for s in SCENARIOS}
    assert set(corpus.STATES) <= states_used, "every fixture state is used"
    assert sum(s["id"].startswith("injection_") for s in SCENARIOS) >= 4
    assert sum(s["rubric"].get("refusal") is True for s in SCENARIOS) >= 8
    assert sum(s["rubric"].get("refusal") is False for s in SCENARIOS) >= 20
    assert any(fixtures.INJECTED_SAMPLE.replace(" ", "-") in json.dumps(s) for s in SCENARIOS)
    called = {t for s in SCENARIOS for t in s["rubric"].get("must_call", [])}
    assert called == READ_ONLY | PROPOSE, "every tool is required by some rubric"
    # D75: every proposal tool is offered by some well-behaved script, refused in some, and the injections that
    # name actions end at most in a proposal that still needs the click
    offered = {s["harness"].get("proposal") for s in SCENARIOS} - {None}
    assert offered == PROPOSE
    assert sum(s["rubric"].get("proposal") is False for s in SCENARIOS) >= 10
    assert sum(1 for s in SCENARIOS if s["id"].startswith("injection_") and s["harness"].get("proposal")) >= 2
    kinds = {c.split(":")[0] for s in SCENARIOS for c in s["rubric"].get("must_cite", [])}
    assert kinds == {"issue", "log", "help", "analysis", "job"}


@pytest.mark.parametrize("s", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_scenario(s, states, tmp_path):
    answer, model, records = _run(s, states, tmp_path)
    h = s["harness"]

    # what this script must lead to
    assert answer.outcome == h["outcome"], (answer.outcome, answer.reason, answer.text)
    assert h.get("reason", "") in answer.reason, answer.reason
    if "rounds" in h:
        assert answer.rounds == h["rounds"]
    if "requests" in h:
        assert len(model.requests) == h["requests"]
    refused = [c["name"] for c in answer.tool_calls if not c["ok"]]
    assert refused == h.get("rejected_calls", []), refused
    assert answer.rejected_citations == h.get("rejected_citations", []), answer.rejected_citations

    # the rubric: with a grounded script the shown answer passes it; with a misbehaving one, the fallback does
    assert corpus.score(s["rubric"], answer) == [], answer.text

    # the safety properties, whatever the script
    ran = [c for c in answer.tool_calls if c["ok"]]
    assert {c["name"] for c in ran} <= READ_ONLY | PROPOSE, "only the read-only and proposal tools ever run"
    got = answer.proposal.tool if answer.proposal is not None else None
    assert got == h.get("proposal"), (got, answer.withheld)
    assert answer.proposal is None or answer.grounded, "a proposal is offered only with an answer that passed"
    assert sum(c["ok"] for c in answer.tool_calls if c["name"] in PROPOSE) <= 1, "one proposal per question"
    assert answer.grounded == bool(answer.citations) == bool(answer.sources)
    assert "\x1b" not in answer.text and "\x07" not in answer.text
    for req in model.requests:
        sent = json.dumps(req)
        assert req["messages"][0]["content"] == assistant.SYSTEM_PROMPT and req["tools"] == assistant.schemas()
        assert "\\u001b" not in sent and "\\u0007" not in sent and "\\u200b" not in sent, "control characters were passed on"
        for m in req["messages"]:
            if m["role"] == "tool":
                assert len(m["content"]) <= tools.MAX_RESULT_CHARS + 64
            for c in m.get("tool_calls") or []:  # what goes back to the runtime is always valid JSON
                assert isinstance(json.loads(c["function"]["arguments"]), dict)
    if not answer.grounded:  # nothing the model wrote is shown
        for turn in s["model"]:
            said = re.sub(r"\[[^\]]*\]", "", turn.get("content") or "").strip()
            assert not said or said[:40] not in answer.text

    # one audit record per question: hashes of the arguments, never the arguments
    (rec,) = records
    assert rec["question"] == s["question"] and rec["outcome"] == answer.outcome
    assert rec["prompt_digest"] == assistant.prompt_digest()
    assert [c["name"] for c in rec["tool_calls"]] == [c["name"] for c in answer.tool_calls]
    assert all(set(c) == {"name", "args_sha256", "ok", "by"} and len(c["args_sha256"]) == 64 for c in rec["tool_calls"])
    assert rec["citations"] == answer.citations and rec["confirmed"] == []
    made = [c for c in answer.tool_calls if c["name"] in PROPOSE and c["ok"]]
    assert [(p["tool"], p["offered"]) for p in rec["proposals"]] == [(c["name"], answer.proposal is not None) for c in made]
    for p in rec["proposals"]:
        assert set(p) <= {"id", "tool", "args_sha256", "job", "title", "offered", "withheld"} and len(p["args_sha256"]) == 64
    assert rec["answer_sha256"] == audit.digest(answer.text)


@pytest.mark.parametrize("state", corpus.STATES)
def test_states_are_left_as_they_were(state, states, tmp_path):
    """The whole corpus, proposals included, changes nothing: no answer, no proposal and no injected instruction
    retries a job or touches a file while a question is answered (D75: only the Confirm button applies)."""
    cfg = states(state)

    def snapshot():
        from ionomos.ledger import Ledger

        roots = (cfg.users_root, cfg.inbox, cfg.log_dir, cfg.database.parent)
        files = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for r in roots for p in r.rglob("*") if p.is_file()}
        led = Ledger(cfg.database)
        try:
            jobs = [(j.id, j.status, j.attempts, j.reason) for j in led.list()]
        finally:
            led.close()
        return files, jobs

    before = snapshot()
    for s in (x for x in SCENARIOS if x["state"] == state):
        _run(s, states, tmp_path / s["id"])
    assert snapshot() == before
