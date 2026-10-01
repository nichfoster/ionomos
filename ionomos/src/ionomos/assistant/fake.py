"""
A scripted fake model: an in-process stand-in for the chat-completions endpoint, for tests.

    model = ScriptedModel([{"tool_calls": [{"name": "get_job", "arguments": {"job_id": 1}}]},
                           {"content": "It ran out of memory [log:1#3]."}])
    answer = assistant.ask(cfg, "why did it fail?", transport=model)
    model.requests            # every request body it was sent, parsed

It is passed as client.chat's transport, so nothing opens a socket. Each request gets the next turn of the
script; when the script runs out it answers with an empty message. A turn is one of:

    {"content": "text"}                                    the answer
    {"tool_calls": [{"name": ..., "arguments": {...}}]}    tool calls ("arguments" may be a raw string, to
                                                           script malformed JSON)
    {"error": "text"}                                      the runtime is not answering (ChatError)
    {"raw": "text"}                                        these bytes, whatever they are

Like the testbed's fake FragPipe it checks what the real thing would: the URL, that the request is a
chat completion with a model, a system prompt first, and a tool message for every tool call. With
stream=True it answers in server-sent events, content and tool-call arguments in pieces.

This tests the harness (the loop, the validators, the citation check, the audit log). It says nothing
about how well any real model answers.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

from ionomos.assistant.client import ChatError

MODEL = "scripted-fake"


class ScriptedModel:
    def __init__(self, turns: list[dict], stream: bool = False):
        self.turns = list(turns)
        self.stream = stream
        self.requests: list[dict] = []

    def __call__(self, url: str, data: bytes, headers: dict, timeout: float) -> Iterator[bytes]:
        body = json.loads(data.decode("utf-8"))
        self._check(url, body)
        self.requests.append(body)
        turn = self.turns[len(self.requests) - 1] if len(self.requests) <= len(self.turns) else {"content": ""}
        if "error" in turn:
            raise ChatError(str(turn["error"]))
        if "raw" in turn:
            return iter([str(turn["raw"]).encode("utf-8")])
        calls = [{"id": c.get("id", f"call_{len(self.requests)}_{n}"), "type": "function",
                  "function": {"name": c["name"], "arguments": c["arguments"] if isinstance(c.get("arguments"), str)
                               else json.dumps(c.get("arguments") or {})}}
                 for n, c in enumerate(turn.get("tool_calls") or [])]
        content = turn.get("content") or ""
        return iter(self._events(content, calls) if self.stream else [self._whole(content, calls)])

    @staticmethod
    def _check(url: str, body: dict) -> None:
        assert url.endswith("/chat/completions"), url
        assert body.get("model") and isinstance(body.get("tools"), list), "a model and the tools are required"
        msgs = body["messages"]
        assert msgs[0]["role"] == "system" and any(m["role"] == "user" for m in msgs), "system first, then a user"
        asked = [c["id"] for m in msgs if m["role"] == "assistant" for c in m.get("tool_calls") or []]
        answered = [m["tool_call_id"] for m in msgs if m["role"] == "tool"]
        assert asked == answered, f"every tool call needs one tool message, in order: {asked} vs {answered}"
        for m in msgs:
            assert isinstance(m.get("content"), str), "message content must be text"

    @staticmethod
    def _whole(content: str, calls: list[dict]) -> bytes:
        msg = {"role": "assistant", "content": content or None}
        if calls:
            msg["tool_calls"] = calls
        return json.dumps({"id": "fake", "object": "chat.completion", "model": MODEL, "system_fingerprint": "fake-0",
                           "choices": [{"index": 0, "message": msg,
                                        "finish_reason": "tool_calls" if calls else "stop"}]}).encode("utf-8")

    @staticmethod
    def _events(content: str, calls: list[dict]) -> list[bytes]:
        def event(delta: dict) -> bytes:
            return b"data: " + json.dumps({"object": "chat.completion.chunk", "model": MODEL,
                                           "choices": [{"index": 0, "delta": delta}]}).encode("utf-8") + b"\n\n"

        out = [event({"role": "assistant"})]
        out += [event({"content": content[i:i + 7]}) for i in range(0, len(content), 7)]
        for n, c in enumerate(calls):
            args = c["function"]["arguments"]
            out.append(event({"tool_calls": [{"index": n, "id": c["id"], "type": "function",
                                              "function": {"name": c["function"]["name"], "arguments": ""}}]}))
            out += [event({"tool_calls": [{"index": n, "function": {"arguments": args[i:i + 5]}}]})
                    for i in range(0, len(args), 5)]
        return out + [b"data: [DONE]\n\n"]
