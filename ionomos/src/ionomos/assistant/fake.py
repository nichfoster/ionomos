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

ScriptedServer serves the same scripts over real HTTP, on 127.0.0.1 and a port the OS picks, for the tests of
`ionomos ask-eval` and for `ionomos ask-eval --scripted` (the runner checked on the PC without a model):

    with ScriptedServer(delay=0.05) as srv:      # srv.base_url == "http://127.0.0.1:<port>/v1"
        srv.use(turns)                           # the script for the next question
"""
from __future__ import annotations

import http.server
import json
import threading
import time
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


class ScriptedServer:
    """A chat-completions endpoint on 127.0.0.1 that answers with a ScriptedModel: whole JSON or server-sent
    events, as each request's "stream" asks, after `delay` seconds (a model reading its prompt). Every request
    body is kept (requests); one the script rejects is answered HTTP 500 and noted (errors)."""

    def __init__(self, turns: list[dict] | None = None, delay: float = 0.0):
        self.delay = delay
        self.model = ScriptedModel(list(turns or []))
        self.requests: list[dict] = []
        self.errors: list[str] = []
        self._lock = threading.Lock()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's name
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                url = f"http://{self.headers.get('Host', '')}{self.path}"
                try:
                    with server._lock:
                        server.requests.append(json.loads(body.decode("utf-8")))
                        server.model.stream = bool(server.requests[-1].get("stream"))
                        chunks = list(server.model(url, body, dict(self.headers), 0))
                except (AssertionError, ValueError, ChatError) as exc:
                    server.errors.append(f"{self.path}: {exc}")
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": str(exc)}}).encode("utf-8"))
                    return
                time.sleep(server.delay)
                self.send_response(200)
                streamed = bool(chunks) and chunks[0].startswith(b"data:")
                self.send_header("Content-Type", "text/event-stream" if streamed else "application/json")
                self.end_headers()
                for c in chunks:
                    self.wfile.write(c)
                    self.wfile.flush()

            def log_message(self, *_a):  # quiet
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="scripted-model", daemon=True)

    def use(self, turns: list[dict]) -> None:
        """The script for the next question (its turns are answered in order, one per request)."""
        with self._lock:
            self.model = ScriptedModel(list(turns))

    def __enter__(self) -> ScriptedServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
