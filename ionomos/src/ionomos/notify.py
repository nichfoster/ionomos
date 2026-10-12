"""
Notifications: a message when a search is done, failed or held (D58). Off unless config.yaml asks.

    s = settings_from(raw)                       # config.yaml notify: -> complete dict; raises NotifyError
    announce(cfg, "done", job, summary=summary)  # the worker: builds the message, sends it in a thread
    results = deliver(s, message)                # synchronous: one Result per configured channel
    send_test(s)                                 # `ionomos notify-test`

Channels: a generic JSON webhook, a Microsoft Teams webhook (an Adaptive Card), a Slack incoming
webhook ({"text": ...}) and SMTP email. Standard library only (urllib, smtplib).

What leaves the PC is exactly `Message.fields`: the event, the job id, the time and, unless
`include_names: false`, the experiment name, user, method, the reason, the hit counts per comparison,
the local path of the report and the PC's name. Never a file, never a quantity.

Nothing here may fail or delay a job: `announce` never raises, sending happens in a daemon thread
with a timeout, there are no retries, and a channel's error is logged once until it works again.
Webhook URLs and the SMTP password are secrets: they are never logged (`scrub`), and the
diagnostics report and bundle are passed through `redact_config_text` / `scrub` (service.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from ionomos import __version__, names

log = logging.getLogger("ionomos.notify")

EVENTS = ("done", "failed", "held")
HOOKS = ("webhook", "teams", "slack")
CHANNELS = (*HOOKS, "email")
SECURITY = ("starttls", "ssl", "none")
HIDDEN = "***"
MAX_REASON = 600  # characters of a failure / hold reason that are sent

_HOOK_DEFAULTS = {"url": "", "url_env": ""}
_EMAIL_DEFAULTS = {"host": "", "port": 587, "security": "starttls", "username": "", "password": "",
                   "password_env": "", "from": "", "to": []}
DEFAULTS = {"enabled": False, "on": list(EVENTS), "include_names": True, "timeout_seconds": 10,
            "webhook": dict(_HOOK_DEFAULTS), "teams": dict(_HOOK_DEFAULTS), "slack": dict(_HOOK_DEFAULTS),
            "email": _EMAIL_DEFAULTS}

_WORDS = {"done": "done", "failed": "failed", "held": "waiting", "test": "test message"}
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOOPBACK = ("localhost", "127.0.0.1", "::1")


class NotifyError(ValueError):
    """The notify: block is wrong, or a message could not be sent. The text never holds a secret."""


# ---------------------------------------------------------------- settings --


def _check_url(where: str, url: str) -> None:
    try:
        u = urllib.parse.urlsplit(url)
        host = u.hostname
    except ValueError:
        u, host = None, None
    if u is None or not host or u.scheme not in ("http", "https"):
        raise NotifyError(f"{where} must be an https:// address")
    if u.scheme == "http" and host not in _LOOPBACK:
        raise NotifyError(f"{where} must start with https:// (http:// is only accepted for this computer, localhost)")


def _env_name(where: str, v) -> str:
    v = str(v or "").strip()
    if v and not _ENV_NAME.fullmatch(v):
        raise NotifyError(f"{where} must be the NAME of an environment variable (letters, digits, _), not its value")
    return v


def fix_keys(raw: dict) -> dict:
    """YAML 1.1 (PyYAML) reads a bare `on:` key as the boolean true; give it its name back."""
    return {("on" if k is True else k): v for k, v in raw.items()}


def settings_from(raw) -> dict:
    """config.yaml notify: -> a complete settings dict (DEFAULTS filled in). Raises NotifyError."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise NotifyError("must be a mapping (enabled, on, include_names, webhook, teams, slack, email)")
    raw = fix_keys(raw)
    bad = sorted(set(map(str, raw)) - set(DEFAULTS))
    if bad:
        raise NotifyError(f"{bad[0]}: unknown setting (known: {', '.join(DEFAULTS)})")
    s: dict = {k: raw.get(k, DEFAULTS[k]) for k in ("enabled", "include_names", "timeout_seconds")}
    for k in ("enabled", "include_names"):
        if not isinstance(s[k], bool):
            raise NotifyError(f"{k} must be true or false")
    on = raw.get("on", DEFAULTS["on"])
    if isinstance(on, str):
        on = [on]
    if not isinstance(on, list) or not on or [e for e in on if e not in EVENTS]:
        raise NotifyError(f"on must be a list of {', '.join(EVENTS)}, e.g. [done, failed]")
    s["on"] = [e for e in EVENTS if e in on]
    t = s["timeout_seconds"]
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 1 <= t <= 60:
        raise NotifyError("timeout_seconds must be a number from 1 to 60")

    for name in HOOKS:
        c = raw.get(name)
        if c is None:
            c = {}
        if isinstance(c, str):  # slack: https://...  is shorthand for slack: {url: ...}
            c = {"url": c}
        if not isinstance(c, dict):
            raise NotifyError(f"{name} must be a mapping (url, url_env)")
        bad = sorted(set(map(str, c)) - set(_HOOK_DEFAULTS))
        if bad:
            raise NotifyError(f"{name}.{bad[0]}: unknown setting (known: url, url_env)")
        url = c.get("url") or ""
        if not isinstance(url, str):
            raise NotifyError(f"{name}.url must be a text")
        url = url.strip()
        if url:
            _check_url(f"{name}.url", url)
        s[name] = {"url": url, "url_env": _env_name(f"{name}.url_env", c.get("url_env"))}

    e = raw.get("email")
    if e is None:
        e = {}
    if not isinstance(e, dict):
        raise NotifyError("email must be a mapping (host, port, security, username, password, from, to)")
    bad = sorted(set(map(str, e)) - set(_EMAIL_DEFAULTS))
    if bad:
        raise NotifyError(f"email.{bad[0]}: unknown setting (known: {', '.join(_EMAIL_DEFAULTS)})")
    m = {k: e.get(k) if e.get(k) is not None else v for k, v in _EMAIL_DEFAULTS.items()}
    for k in ("host", "username", "password", "from"):
        if not isinstance(m[k], str):
            raise NotifyError(f"email.{k} must be a text (quote it in YAML)")
        m[k] = m[k].strip() if k != "password" else m[k]
    if isinstance(m["port"], bool) or not isinstance(m["port"], int) or not 1 <= m["port"] <= 65535:
        raise NotifyError("email.port must be a number, e.g. 587")
    m["security"] = str(m["security"]).lower()
    if m["security"] not in SECURITY:
        raise NotifyError(f"email.security must be one of {', '.join(SECURITY)}")
    m["password_env"] = _env_name("email.password_env", m["password_env"])
    to = [m["to"]] if isinstance(m["to"], str) else m["to"]
    if not isinstance(to, list) or not all(isinstance(a, str) for a in to):
        raise NotifyError("email.to must be a list of addresses")
    m["to"] = [a.strip() for a in to if a.strip()]
    if m["host"]:
        if not m["to"]:
            raise NotifyError("email.to: give at least one address to send to")
        if not m["from"]:
            raise NotifyError("email.from: give the address the message is sent from")
        for a in (m["from"], *m["to"]):
            if not re.fullmatch(r"[^@\s]+@[^@\s]+", a) and not re.fullmatch(r"[^<>]*<[^@\s]+@[^@\s]+>", a):
                raise NotifyError(f"email: {a!r} is not an email address")
        if (m["password"] or m["password_env"]) and not m["username"]:
            raise NotifyError("email.username is needed with a password")
        if m["security"] == "none" and (m["password"] or m["password_env"]):
            raise NotifyError("email.security: none would send the password unencrypted; use starttls or ssl")
    s["email"] = m
    if s["enabled"] and not channels(s):
        raise NotifyError("enabled is true but no channel is set: give webhook.url, teams.url, slack.url "
                          "(or their url_env) or email.host")
    return s


def channels(s: dict) -> list[str]:
    """The channels that are set up, in the order they are sent."""
    out = [n for n in HOOKS if s[n]["url"] or s[n]["url_env"]]
    return out + (["email"] if s["email"]["host"] else [])


def describe(s: dict) -> str:
    """One line for `ionomos check` (no secrets)."""
    if not s.get("enabled"):
        return "off"
    return (f"on: {', '.join(channels(s))}; when a search is {', '.join(_WORDS[e] for e in s['on'])}; "
            + ("names included" if s["include_names"] else "job number and status only"))


# ----------------------------------------------------------------- secrets --


def _hook_url(s: dict, name: str, env=None) -> str:
    env = os.environ if env is None else env
    c = s[name]
    return (env.get(c["url_env"], "") if c["url_env"] else "") or c["url"]


def _password(s: dict, env=None) -> str:
    env = os.environ if env is None else env
    m = s["email"]
    return (env.get(m["password_env"], "") if m["password_env"] else "") or m["password"]


def secrets(s: dict, env=None) -> list[str]:
    """Every secret value in use: the webhook URLs and the SMTP password, from the file or the environment."""
    out = [_hook_url(s, n, env) for n in HOOKS] + [s[n]["url"] for n in HOOKS]
    out += [_password(s, env), s["email"]["password"]]
    return sorted({v for v in out if v}, key=len, reverse=True)


# hook addresses carry their key in the path; hide any that turn up in a text we did not write
_HOOK_URLS = re.compile(r"https://(?:hooks\.slack\.com/|[\w.-]*webhook\.office\.com/|[\w.-]*logic\.azure\.com[:/])[^\s\"'<>]+")
_SECRET_LINE = re.compile(r"^(\s*[\"']?(?:url|password)[\"']?\s*:[ \t]*)(\S[^\n]*)$", re.MULTILINE)
_EMPTY_VALUE = re.compile(r"(\"\"|'')(\s+#.*)?\s*")


def scrub(text: str, secret_values=()) -> str:
    """`text` with the given secret values, and anything that looks like a Slack / Teams hook address, hidden."""
    for v in secret_values:
        if v:
            text = text.replace(v, HIDDEN)
    return _HOOK_URLS.sub(HIDDEN, text)


def raw_secrets(raw) -> list[str]:
    """Secret values of a parsed config.yaml, without validating it (a broken config is still redacted)."""
    out: list[str] = []
    block = raw.get("notify") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return out
    for name, c in block.items():
        if name in HOOKS and isinstance(c, str):
            out.append(c)
        elif isinstance(c, dict):
            for k in ("url", "password"):
                if isinstance(c.get(k), str):
                    out.append(c[k])
            for k in ("url_env", "password_env"):
                if isinstance(c.get(k), str) and os.environ.get(c[k]):
                    out.append(os.environ[c[k]])
    return sorted({v for v in out if v.strip()}, key=len, reverse=True)


def file_secrets(config_path: Path | str) -> list[str]:
    """`raw_secrets` of a config file; [] if it can't be read or parsed."""
    try:
        import yaml

        from ionomos.config import read_yaml_text

        return raw_secrets(yaml.safe_load(read_yaml_text(config_path)))
    except Exception:  # noqa: BLE001 - redaction must never crash diagnostics
        return []


def redact_config_text(text: str, secret_values=()) -> str:
    """config.yaml as text with every `url:` / `password:` value and every known secret replaced by ***."""

    def hide(m: re.Match) -> str:
        value = m.group(2)
        if value.startswith("#") or _EMPTY_VALUE.fullmatch(value):
            return m.group(0)
        if value[0] in "\"'":  # quoted: a # inside is not a comment, so the whole rest of the line goes
            return m.group(1) + HIDDEN
        comment = re.search(r"\s+#.*$", value)
        return m.group(1) + HIDDEN + (comment.group(0) if comment else "")

    return _SECRET_LINE.sub(hide, scrub(text, secret_values))


# ----------------------------------------------------------------- message --


@dataclass(frozen=True)
class Message:
    title: str                                   # one line: the email subject, the card's heading
    facts: tuple[tuple[str, str], ...] = ()      # (label, value) lines under it
    fields: dict = field(default_factory=dict)   # the generic webhook's JSON: everything that is sent

    @property
    def text(self) -> str:
        return "\n".join([self.title, *(f"{k}: {v}" for k, v in self.facts)])


def _pc() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""


def build(event: str, job, s: dict, reason: str = "", summary: dict | None = None) -> Message:
    """The message for one event. With include_names false: the job number and the status, nothing else."""
    word = _WORDS[event]
    job_id = getattr(job, "id", None)
    fields: dict = {"app": names.APP, "version": __version__, "event": event, "job_id": job_id,
                    "time": datetime.now().astimezone().isoformat(timespec="seconds")}
    if not s["include_names"]:
        title = f"{names.APP}: job {job_id} {word}"
        return Message(title, (), {**fields, "text": title})
    title = f"{names.APP}: {job.user}/{job.inbox_name} {word}"
    facts = [("Experiment", str(job.inbox_name)), ("User", str(job.user)), ("Method", str(job.method)),
             ("Job", str(job_id))]
    fields.update(experiment=job.inbox_name, user=job.user, method=job.method)
    reason = " ".join(str(reason or "").split())[:MAX_REASON]
    if reason:
        facts.append(("Waiting for" if event == "held" else "Reason" if event == "failed" else "Note", reason))
        fields["reason"] = reason
    hits = []
    for c in (summary or {}).get("comparisons") or []:
        try:
            hits.append({"comparison": str(c["name"]), "up": int(c["up"]), "down": int(c["down"]),
                         "tested": int(c["tested"])})
        except (KeyError, TypeError, ValueError):
            continue
    if hits:
        facts += [(h["comparison"], f"{h['up']} up, {h['down']} down of {h['tested']}") for h in hits]
        fields["hits"] = hits
    if (summary or {}).get("report"):
        report = str(Path(job.dest_dir) / summary["report"])
        facts.append(("Report (on the proteomics PC)", report))
        fields["report"] = report
    pc = _pc()
    if pc:
        facts.append(("PC", pc))
        fields["pc"] = pc
    msg = Message(title, tuple(facts))
    return Message(title, tuple(facts), {**fields, "text": msg.text})


def sample_message(s: dict) -> Message:
    title = f"{names.APP}: test message"
    facts = [("What", "sent by `ionomos notify-test`; nothing is wrong")]
    fields = {"app": names.APP, "version": __version__, "event": "test", "job_id": None,
              "time": datetime.now().astimezone().isoformat(timespec="seconds")}
    if s["include_names"] and _pc():
        facts.append(("PC", _pc()))
        fields["pc"] = _pc()
    msg = Message(title, tuple(facts))
    return Message(title, tuple(facts), {**fields, "text": msg.text})


def slack_payload(msg: Message) -> dict:
    """Slack incoming webhook: {"text": ...} (mrkdwn)."""
    return {"text": "\n".join([f"*{msg.title}*", *(f"{k}: {v}" for k, v in msg.facts)])}


def teams_payload(msg: Message) -> dict:
    """Teams webhook (a Workflows "when a webhook request is received" flow, or an incoming webhook):
    a message with one Adaptive Card attachment."""
    body: list[dict] = [{"type": "TextBlock", "text": msg.title, "weight": "Bolder", "size": "Medium", "wrap": True}]
    if msg.facts:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in msg.facts]})
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                    "version": "1.2", "body": body}}]}


def email_message(msg: Message, s: dict) -> EmailMessage:
    m = s["email"]
    em = EmailMessage()
    em["Subject"] = msg.title
    em["From"] = m["from"]
    em["To"] = ", ".join(m["to"])
    em["Date"] = formatdate(localtime=True)
    em.set_content("\n".join(f"{k}: {v}" for k, v in msg.facts) + f"\n\n-- \n{names.APP} {__version__}\n"
                   if msg.facts else f"{msg.title}\n")
    return em


_PAYLOADS = {"webhook": lambda msg: msg.fields, "teams": teams_payload, "slack": slack_payload}


# ----------------------------------------------------------------- sending --


@dataclass(frozen=True)
class Result:
    channel: str
    ok: bool
    detail: str  # "HTTP 200", "accepted by smtp.example.org", or why not (never a secret)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # a hook that redirects is misconfigured; don't follow it elsewhere
        return None


def _post(url: str, payload: dict, timeout: float) -> str:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": f"{names.SLUG}/{__version__}"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as r:
            r.read(2000)
            return f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        raise NotifyError(f"the server answered HTTP {exc.code} {exc.reason}") from None


def _send_email(s: dict, msg: Message, timeout: float, env=None) -> str:
    m = s["email"]
    if m["security"] == "ssl":
        smtp = smtplib.SMTP_SSL(m["host"], m["port"], timeout=timeout, context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(m["host"], m["port"], timeout=timeout)
    try:
        if m["security"] == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if m["username"]:
            smtp.login(m["username"], _password(s, env))
        refused = smtp.send_message(email_message(msg, s))
    finally:
        try:
            smtp.quit()
        except (OSError, smtplib.SMTPException):
            pass
    if refused:
        raise NotifyError(f"the server refused {len(refused)} of {len(m['to'])} address(es)")
    return f"accepted by {m['host']}"


def _why(exc: BaseException) -> str:
    if isinstance(exc, NotifyError):
        return str(exc)
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(exc):
        return "no answer in time (timed out)"
    if isinstance(exc, urllib.error.URLError):
        return f"could not connect: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


def deliver(s: dict, msg: Message, env=None) -> list[Result]:
    """Send `msg` on every configured channel, one try each. Never raises; a Result's detail holds no secret."""
    out = []
    hide = secrets(s, env)
    for name in channels(s):
        try:
            if name == "email":
                if s["email"]["password_env"] and not _password(s, env):
                    raise NotifyError(f"environment variable {s['email']['password_env']} (email.password_env) is not set")
                detail = _send_email(s, msg, s["timeout_seconds"], env)
            else:
                url = _hook_url(s, name, env)
                if not url:
                    raise NotifyError(f"environment variable {s[name]['url_env']} ({name}.url_env) is not set")
                _check_url(f"{name}.url_env", url)
                detail = _post(url, _PAYLOADS[name](msg), s["timeout_seconds"])
            out.append(Result(name, True, detail))
        except Exception as exc:  # noqa: BLE001 - a message must never break anything
            out.append(Result(name, False, scrub(_why(exc), hide)))
    return out


def send_test(s: dict, env=None) -> list[Result]:
    """`ionomos notify-test`: a test message on every configured channel (also when enabled is false)."""
    return deliver(s, sample_message(s), env)


def run_test(s: dict, say=print, env=None) -> int:
    """`ionomos notify-test` and the app's Send test (D67): a test message on every configured channel, each
    line of what happened passed to `say` as it comes. Returns the exit code (0: all sent). No line holds a
    secret: a Result's detail is scrubbed."""
    chans = channels(s)
    if not chans:
        say("notifications are not set up: config.yaml has no notify: channel (webhook, teams, slack or email).\n"
            "Nothing was sent. See: ionomos help notify")
        return 1
    if not s["enabled"]:
        say("notify.enabled is false: jobs send nothing. Testing the configured channel(s) anyway.")
    say(f"sending a test message by {', '.join(chans)} (waiting up to {s['timeout_seconds']:g} s each) ...")
    results = send_test(s, env)
    for r in results:
        say(f" {'✓' if r.ok else '✗'} {r.channel:<8} {'sent' if r.ok else 'NOT sent'}: {r.detail}")
    bad = [r for r in results if not r.ok]
    say("\nall sent; check that the message arrived" if not bad
        else f"\n{len(bad)} of {len(results)} could not be sent; jobs are not affected by this")
    return 1 if bad else 0


# ---------------------------------------------------------- from the worker --

_lock = threading.Lock()
_failing: dict[str, str] = {}  # channel -> the error already logged (log once until it changes or works)
_sending: list[threading.Thread] = []


def flush(timeout: float = 10.0) -> bool:
    """Wait for messages still being sent (tests). True if none is left."""
    import time

    end = time.monotonic() + timeout
    with _lock:
        threads = list(_sending)
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    with _lock:
        _sending[:] = [t for t in _sending if t.is_alive()]
        return not _sending


def _send_logged(s: dict, msg: Message, what: str) -> None:
    for r in deliver(s, msg):
        with _lock:
            before = _failing.get(r.channel)
            if r.ok:
                _failing.pop(r.channel, None)
            else:
                _failing[r.channel] = r.detail
        if r.ok:
            log.info("notified by %s: %s%s", r.channel, what, " (it works again)" if before else "")
        elif before != r.detail:
            log.warning("could not notify by %s: %s (%s). Not tried again for this message; "
                        "check with: ionomos notify-test", r.channel, r.detail, what)


def _state_path(log_dir: Path) -> Path:
    return Path(log_dir) / names.NOTIFY_STATE


def _read_state(log_dir: Path) -> dict:
    try:
        d = json.loads(_state_path(log_dir).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("held"), dict) else {"held": {}}
    except (OSError, ValueError):
        return {"held": {}}


def _write_state(log_dir: Path, state: dict) -> None:
    p = _state_path(log_dir)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _hold_key(reason: str) -> str:
    """A hold reason without its numbers: "low disk space: 12 GB free" and "... 11 GB free" are one reason."""
    return re.sub(r"\d+(?:\.\d+)?", "#", reason)[:200]


_mem_held: dict[tuple[str, str], set[str]] = {}  # if the state file can't be written, still once per process


def _first_time_held(log_dir: Path, job_id, reason: str) -> bool:
    key = _hold_key(reason)
    with _lock:
        seen = _mem_held.setdefault((str(log_dir), str(job_id)), set())
        state = _read_state(log_dir)
        told = state["held"].setdefault(str(job_id), [])
        if key in seen or key in told:
            return False
        seen.add(key)
        told.append(key)
        _write_state(log_dir, state)
        return True


def _forget_held(log_dir: Path, job_id) -> None:
    with _lock:
        _mem_held.pop((str(log_dir), str(job_id)), None)
        state = _read_state(log_dir)
        if state["held"].pop(str(job_id), None) is not None:
            _write_state(log_dir, state)


def announce(cfg, event: str, job, reason: str = "", summary: dict | None = None) -> threading.Thread | None:
    """Tell the configured channels that `job` is done / failed / held. Returns at once: the sending is
    done by the returned daemon thread (None if nothing is sent). Never raises.

    "held" is sent once per job and reason (remembered in <log_dir>/notify_state.json, so a restart
    doesn't repeat it); the job running to done or failed clears that."""
    try:
        s = getattr(cfg, "notify", None) or {}
        if not s.get("enabled") or not channels(s):
            return None
        log_dir = getattr(cfg, "log_dir", None)
        if log_dir is not None:
            if event == "held":
                if not _first_time_held(log_dir, job.id, reason):
                    return None
            else:
                _forget_held(log_dir, job.id)
        if event not in s["on"]:
            return None
        msg = build(event, job, s, reason, summary)
        t = threading.Thread(target=_send_logged, args=(s, msg, f"job {job.id} {_WORDS[event]}"),
                             name=f"notify-job{job.id}", daemon=True)
        with _lock:
            _sending[:] = [x for x in _sending if x.is_alive()] + [t]
        t.start()
        return t
    except Exception as exc:  # noqa: BLE001 - never let a message stop a search
        log.warning("could not prepare a notification for job %s: %s", getattr(job, "id", "?"), type(exc).__name__)
        return None
