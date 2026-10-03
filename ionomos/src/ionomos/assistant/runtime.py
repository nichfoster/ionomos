"""
Sharing the PC with a search: how long the runtime keeps the model loaded, and what changes while FragPipe (or
another engine) is running (ROADMAP Phase 6, D72).

    busy, why = search_running(cfg.log_dir)          # from the worker's heartbeat, no network
    s = effective(settings, busy)                    # assistant: settings with while_searching: applied
    extra = request_fields(s)                        # what client.request_body adds ({"keep_alive": "2m"})

Only fields the runtimes' OpenAI-compatible endpoint accepts are sent. Which runtime honours what (checked
against their source and documentation, 2026-10-03; docs/ASSISTANT.md has the table):

    keep_alive   Ollama's /v1/chat/completions reads it (how long the model stays loaded after the request;
                 "0" unloads at once, "-1" keeps it). An older Ollama ignores it, and then OLLAMA_KEEP_ALIVE
                 sets it for every model. llama-server does not use it: it keeps its model loaded, or sleeps
                 after --sleep-idle-seconds.
    model        every runtime. While a search runs, a different model name can be a smaller model, or (Ollama)
                 the same weights created with fewer threads: a Modelfile with `PARAMETER num_thread 4`.
    base_url     every runtime. For llama-server, whose threads are fixed at start (-t / -tb), a second server
                 started with fewer threads on another port.

Threads are never sent with a request: Ollama's OpenAI-compatible endpoint has no num_thread (only its native
API's options do, and a request with other options reloads the model), and llama-server takes -t at start.
Ionomos does not change another program's priority either: it only reads and asks. Starting the runtime at a
lower priority is the lab's choice (docs/ASSISTANT.md).

`pause: true` in while_searching: does not ask the model at all while a search runs; the answer is Ionomos's
own text, as when the assistant is not set up.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

WHILE_SEARCHING_KEYS = ("model", "base_url", "keep_alive", "timeout_seconds", "pause")
# Go's time.ParseDuration, which Ollama uses for a keep_alive given as text: "30s", "5m", "1h30m", "-1", "0"
_DURATION = re.compile(r"^-?(?:0|(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+)$")
_RUNNING = "running job"


def keep_alive_value(v, where: str = "keep_alive"):
    """A keep_alive setting as it goes into the request: "" (not sent), a duration text, or seconds (a number).
    Raises ValueError with what is wrong."""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        raise ValueError(f"{where} must be a duration like 5m or 30s, or seconds")
    if isinstance(v, int | float):
        return int(v) if float(v).is_integer() else float(v)
    text = str(v).strip()
    if text.lstrip("-").isdigit():
        return int(text)  # Ollama reads a bare number as seconds, but not the text "300"
    if not _DURATION.match(text):
        raise ValueError(f"{where} must be a duration like 5m or 30s, or seconds (got {text!r})")
    return text


def while_searching_from(raw) -> dict:
    """assistant.while_searching: -> the overrides that are set. Raises ValueError."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"while_searching must be a mapping ({', '.join(WHILE_SEARCHING_KEYS)})")
    bad = sorted(set(map(str, raw)) - set(WHILE_SEARCHING_KEYS))
    if bad:
        raise ValueError(f"while_searching.{bad[0]}: unknown setting (known: {', '.join(WHILE_SEARCHING_KEYS)})")
    out: dict = {}
    for k, v in raw.items():
        if v is None or v == "":
            continue
        if k in ("model", "base_url"):
            if not isinstance(v, str):
                raise ValueError(f"while_searching.{k} must be a text")
            out[k] = v.strip()
        elif k == "keep_alive":
            out[k] = keep_alive_value(v, "while_searching.keep_alive")
        elif k == "pause":
            if not isinstance(v, bool):
                raise ValueError("while_searching.pause must be true or false")
            out[k] = v
        else:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                raise ValueError("while_searching.timeout_seconds must be a number") from None
            if not 1 <= out[k] <= 3600:
                raise ValueError("while_searching.timeout_seconds must be between 1 and 3600")
    return out


def search_running(log_dir: Path | str | None, now: float | None = None) -> tuple[bool, str]:
    """(True, "job 3: MSFragger") while the worker runs a search, from <log_dir>/heartbeat.json. The worker
    beats every few seconds during a search; a heartbeat older than health.STALE_AFTER means no watcher is
    running, so nothing is searching. Never raises; no file means idle."""
    from ionomos import health

    if log_dir is None:
        return False, ""
    hb = health.read_heartbeat(Path(log_dir))
    part = ((hb or {}).get("parts") or {}).get("worker") or {}
    state = str(part.get("state") or "")
    try:
        age = (time.time() if now is None else now) - float(part.get("at", 0))
    except (TypeError, ValueError):
        return False, ""
    if state.startswith(_RUNNING) and age <= health.STALE_AFTER:
        return True, state[len("running "):]
    return False, ""


def effective(settings: dict, searching: bool) -> dict:
    """The settings to ask with: while a search runs, while_searching: replaces model, base_url, keep_alive and
    timeout_seconds where it sets them. The localhost gate applies to whatever base_url results (client.py)."""
    s = {k: v for k, v in settings.items() if k != "while_searching"}
    s["mode"] = "searching" if searching else "idle"
    if searching:
        s.update(settings.get("while_searching") or {})
    s.setdefault("pause", False)
    return s


def request_fields(settings: dict) -> dict:
    """Fields beyond the standard chat request: only keep_alive, and only when it is set."""
    ka = settings.get("keep_alive", "")
    return {} if ka == "" or ka is None else {"keep_alive": ka}
