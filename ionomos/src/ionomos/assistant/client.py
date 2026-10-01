"""
The one protocol the assistant speaks: POST <base_url>/chat/completions, OpenAI-compatible, with tool calls.

    reply = chat(settings, messages, tools)        # -> Reply(content, tool_calls, model, fingerprint, ttft)

Ollama, llama.cpp's llama-server and others serve this API on the PC; Ionomos bundles no runtime and no
weights (D49). Standard library only (urllib). The request goes straight to the address in config: no proxy
from the environment and no redirects, so a local address can't be turned into a remote one on the way.

local_problem(base_url) is the gate: anything but localhost / a loopback address is refused before a byte
is sent. Cloud models are ROADMAP Phase 6.4 (an admin flag, a banner, a preview of what is sent); until
that exists a non-local address is refused whatever `assistant.allow_cloud` says.

A reply is read whole (JSON) or as a stream (server-sent events); a stream only serves to time the first
token, because nothing is shown before the answer's citations are checked. `transport` replaces the HTTP
call: tests and the scenario corpus pass assistant.fake.ScriptedModel, so no test opens a socket.
"""
from __future__ import annotations

import ipaddress
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from urllib.parse import urlsplit

MAX_REPLY_BYTES = 2_000_000
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)  # reasoning some local models put in content

Transport = Callable[[str, bytes, dict, float], Iterable[bytes]]


class ChatError(Exception):
    """The runtime did not answer, or answered something that is not a chat completion."""


class NotLocal(ChatError):
    """assistant.base_url points off this PC: refused before anything is sent."""


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{"id", "name", "arguments" (JSON text)}]
    model: str = ""
    fingerprint: str = ""
    ttft: float | None = None      # seconds to the first streamed token (None: not streamed)


def local_problem(base_url: str) -> str | None:
    """None when base_url is on this PC (localhost or a loopback address); otherwise why it is not."""
    try:
        u = urlsplit(str(base_url or ""))
        host = u.hostname or ""
        u.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return f"assistant.base_url is not a URL: {base_url!r}"
    if u.scheme not in ("http", "https") or not host:
        return f"assistant.base_url must look like http://127.0.0.1:11434/v1 (got {base_url!r})"
    if host == "localhost":
        return None
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError:
        pass
    return (f"assistant.base_url points at {host}, which is not this PC. The assistant only talks to a model "
            f"running on this PC (localhost / 127.0.0.1); nothing was sent")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a, **_k):  # a redirect becomes an HTTP error instead of a second request
        return None


def _http(url: str, data: bytes, headers: dict, timeout: float) -> Iterator[bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            yield from resp
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(300).decode("utf-8", errors="replace")
        except OSError:
            pass
        raise ChatError(f"the model runtime answered HTTP {exc.code} {body}".strip()) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ChatError(f"no answer from the model runtime at {url}: {getattr(exc, 'reason', exc)}") from None


def request_body(settings: dict, messages: list[dict], tools: list[dict]) -> bytes:
    """The request, with a fixed key order: model, the messages (system prompt first), then the tools."""
    body = {"model": settings["model"], "messages": messages, "tools": tools, "tool_choice": "auto",
            "temperature": 0, "stream": bool(settings.get("stream", True))}
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def chat(settings: dict, messages: list[dict], tools: list[dict], transport: Transport | None = None) -> Reply:
    problem = local_problem(settings.get("base_url", ""))
    if problem:
        raise NotLocal(problem)
    url = str(settings["base_url"]).rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    started = time.monotonic()
    lines, size = [], 0
    reply = Reply()
    try:
        for raw in (transport or _http)(url, request_body(settings, messages, tools), headers,
                                        float(settings.get("timeout_seconds", 120))):
            size += len(raw)
            if size > MAX_REPLY_BYTES:
                raise ChatError("the model's reply is too large")
            lines.append(raw)
            if reply.ttft is None and raw.lstrip().startswith(b"data:") and b'"delta"' in raw:
                reply.ttft = round(time.monotonic() - started, 3)
    except ChatError:
        raise
    except (OSError, ValueError) as exc:
        raise ChatError(f"no answer from the model runtime: {exc}") from None
    text = b"".join(lines).decode("utf-8", errors="replace")
    try:
        if text.lstrip().startswith("data:"):
            _read_stream(text, reply)
        else:
            _read_json(json.loads(text), reply)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ChatError(f"the model runtime's reply is not a chat completion ({type(exc).__name__})") from None
    reply.content = _THINK.sub("", reply.content).strip()
    return reply


def _read_json(d: dict, reply: Reply) -> None:
    if "error" in d and "choices" not in d:
        err = d["error"]
        raise ChatError(f"the model runtime reported: {str(err.get('message', err) if isinstance(err, dict) else err)[:300]}")
    msg = d["choices"][0]["message"]
    reply.content = msg.get("content") or ""
    reply.model, reply.fingerprint = str(d.get("model") or ""), str(d.get("system_fingerprint") or "")
    for n, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function") or {}
        args = fn.get("arguments")
        reply.tool_calls.append({"id": str(c.get("id") or f"call_{n}"), "name": str(fn.get("name") or ""),
                                 "arguments": args if isinstance(args, str) else json.dumps(args or {})})


def _read_stream(text: str, reply: Reply) -> None:
    calls: dict[int, dict] = {}
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        d = json.loads(data)
        if "error" in d and "choices" not in d:
            _read_json(d, reply)
        reply.model = reply.model or str(d.get("model") or "")
        reply.fingerprint = reply.fingerprint or str(d.get("system_fingerprint") or "")
        for choice in d.get("choices") or []:
            delta = choice.get("delta") or {}
            reply.content += delta.get("content") or ""
            for c in delta.get("tool_calls") or []:
                slot = calls.setdefault(int(c.get("index", len(calls))), {"id": "", "name": "", "arguments": ""})
                fn = c.get("function") or {}
                slot["id"] = slot["id"] or str(c.get("id") or "")
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
    for n in sorted(calls):
        calls[n]["id"] = calls[n]["id"] or f"call_{n}"
        reply.tool_calls.append(calls[n])
