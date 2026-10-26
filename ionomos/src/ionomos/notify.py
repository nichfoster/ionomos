"""
Notifications: a message when a search is done, failed or held (D58). Off unless config.yaml asks.

    s = settings_from(raw)                       # config.yaml notify: -> complete dict; raises NotifyError
    announce(cfg, "done", job, summary=summary)  # the worker: builds the message, sends it in a thread
    results = deliver(s, message)                # synchronous: one Result per configured channel
    send_test(s)                                 # `ionomos notify-test`
    retry_unsent(cfg)                            # the watcher, every minute: the messages kept for later (D131)

Channels: a generic JSON webhook, a Microsoft Teams webhook (an Adaptive Card), a Slack incoming
webhook ({"text": ...}) and SMTP email. Standard library only (urllib, smtplib).

What leaves the PC is exactly `Message.fields`: the event, the job id, the time and, unless
`include_names: false`, the experiment name, user, method, the reason, the hit counts per comparison,
the local path of the report and the PC's name. Never a file, never a quantity.

Nothing here may fail or delay a job: `announce` never raises, sending happens in a daemon thread
with a timeout, and a channel's error is logged once until it works again. One try per message and
channel, except (D131) a message that never reached the service (no connection, no name lookup, refused;
or the service said "later": HTTP 429 / 5xx, SMTP 4xx): it is kept in <log_dir>/notify_state.json, with
one attention item (`notify_unsent`, a warning), and sent again later (every UNSENT_FIRST_S, doubling, at
most UNSENT_MAX_S apart; at once when the channel works again), the newest about a job only, at most
UNSENT_MAX messages for UNSENT_DAYS days. A request that reached the service and got no answer is not
sent again (it may have arrived). Webhook URLs and the SMTP password are secrets: they are never logged
(`scrub`), never kept with a message, and the diagnostics report and bundle are passed through
`redact_config_text` / `scrub` (service.py).
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import smtplib
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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
# messages that never reached the service (D131): tried again after UNSENT_FIRST_S, doubling, at most UNSENT_MAX_S
# apart; kept UNSENT_DAYS days, at most UNSENT_MAX of them (the oldest go first)
UNSENT_FIRST_S = 300
UNSENT_MAX_S = 1800
UNSENT_DAYS = 7
UNSENT_MAX = 50
ATTENTION_KIND = "notify_unsent"

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
    if "@" in u.netloc:  # urllib would send user:password@ nowhere useful, and its errors would show the password
        raise NotifyError(f"{where} has a user name or password before the host (user:password@): webhook "
                          "addresses carry their key in the path; copy the address the service gave without it")
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


def _url_parts(url: str) -> list[str]:
    """The pieces of a hook address an error message may quote on their own: the user:password before the host,
    the path and the query (where hooks keep their key). Short pieces ("/", "/hook") are not secrets."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return []
    parts = [u.netloc.rpartition("@")[0], u.path, u.query, u.path + ("?" + u.query if u.query else "")]
    return [p for p in parts if len(p) >= 12] + [p for p in (u.password,) if p and len(p) >= 4]


# hook addresses carry their key in the path; hide any that turn up in a text we did not write
_HOOK_URLS = re.compile(r"https://(?:hooks\.slack\.com/|[\w.-]*webhook\.office\.com/|[\w.-]*logic\.azure\.com[:/])[^\s\"'<>]+")
_SECRET_LINE = re.compile(r"^(\s*(?:-\s+)?[\"']?(?:url|password)[\"']?\s*:[ \t]*)(\S[^\n]*)$", re.MULTILINE)
_EMPTY_VALUE = re.compile(r"(\"\"|'')(\s+#.*)?\s*")
# the value on the lines below the key: `url:` / `password: |` / `url: >-` (YAML block and plain multi-line values)
_SECRET_BLOCK = re.compile(r"^(\s*)(?:-\s+)?[\"']?(?:url|password)[\"']?\s*:[ \t]*(?:[|>][-+0-9]*)?[ \t]*(?:#.*)?$")
# inside a flow mapping: {url: https://..., password: x}  (a config that doesn't parse is redacted by its text alone)
_SECRET_FLOW = re.compile(r"([{,][ \t]*)([\"']?)(url|password)\2([ \t]*:[ \t]*)"
                          r"(\"[^\"\n]*\"?|'[^'\n]*'?|[^\s\[{,}\]#][^,}\]\n]*)")


def _hide_below(text: str) -> str:
    """Lines more indented than a secret key whose value starts on the next line, replaced by ***."""
    out, indent = [], None
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if indent is not None:
            if not body.strip():
                out.append(line)
                continue
            lead = len(body) - len(body.lstrip())
            if lead > indent:
                out.append(body[:lead] + HIDDEN + line[len(body):])
                continue
            indent = None
        m = _SECRET_BLOCK.match(body)
        if m:
            indent = len(m.group(1))
        out.append(line)
    return "".join(out)


def scrub(text: str, secret_values=()) -> str:
    """`text` with the given secret values, the pieces of a secret address that an error may quote on their own
    (its user:password, path and query), and anything that looks like a Slack / Teams hook address, hidden."""
    values = [v for v in secret_values if v]
    values += [part for v in values if "://" in v for part in _url_parts(v)]
    for v in sorted(set(values), key=len, reverse=True):
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


BACKUPS_READ = 30  # configio keeps 30 backups


def file_secrets(config_path: Path | str) -> list[str]:
    """`raw_secrets` of a config file and of its backups (config-backups/config-*.yaml: a hook address replaced
    last week is still in last week's log lines); [] for a file that can't be read or parsed."""
    import yaml

    from ionomos.config import read_yaml_text
    from ionomos.configio import BACKUP_DIR

    out: set[str] = set()
    p = Path(config_path)
    try:
        backups = sorted((p.parent / BACKUP_DIR).glob("config-*.yaml"))[-BACKUPS_READ:]
    except OSError:
        backups = []
    for f in (p, *backups):
        try:
            out.update(raw_secrets(yaml.safe_load(read_yaml_text(f))))
        except Exception:  # noqa: BLE001 - redaction must never crash diagnostics
            continue
    return sorted(out, key=len, reverse=True)


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

    text = _SECRET_LINE.sub(hide, _hide_below(scrub(text, secret_values)))
    return _SECRET_FLOW.sub(lambda m: m.group(1) + m.group(2) + m.group(3) + m.group(2) + m.group(4) + HIDDEN, text)


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


def _slack_text(s: str) -> str:
    """Slack reads <...> as a link or a mention (<!channel> pings everyone): &, < and > are escaped, as its
    message-formatting rules ask, so a name or a reason is shown as written."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_payload(msg: Message) -> dict:
    """Slack incoming webhook: {"text": ...} (mrkdwn)."""
    return {"text": "\n".join([f"*{_slack_text(msg.title)}*",
                               *(f"{_slack_text(k)}: {_slack_text(v)}" for k, v in msg.facts)])}


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
    retry: bool = False  # not sent, but it may get through later: it never reached the service (D131)


class _Refused(NotifyError):
    """The webhook's server answered with an HTTP error."""

    def __init__(self, code: int, text: str):
        super().__init__(text)
        self.code = code


class _NoAnswer(NotifyError):
    """The request went out and no answer came (a timeout, the connection closed): it may have arrived."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # a hook that redirects is misconfigured; don't follow it elsewhere
        return None


def _post(url: str, payload: dict, timeout: float) -> str:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": f"{names.SLUG}/{__version__}"})
    try:
        r = urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _Refused(exc.code, f"the server answered HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError:
        raise  # urllib's own: the request was not sent (no connection, no name lookup, refused, TLS)
    except OSError as exc:  # sent; then no answer in time, or the connection closed before one
        raise _NoAnswer(_why(exc)) from None
    with r:
        try:
            r.read(2000)
        except (OSError, http.client.HTTPException):
            pass  # the status line was the answer
        return f"HTTP {r.status}"


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
    if isinstance(exc, urllib.error.URLError):
        return f"could not connect: {exc.reason}"
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(exc):
        return "no answer in time (timed out)"
    return f"{type(exc).__name__}: {exc}"


def worth_retrying(exc: BaseException) -> bool:
    """Whether a message that failed this way may still arrive when it is sent again later (D131): it never reached
    the service (no connection, no name lookup, refused, a TLS handshake that failed), or the service said "later"
    (HTTP 429 or 5xx, an SMTP 4xx; a mail server has accepted nothing until it says 250). Not when the service said
    no (another HTTP error, a wrong password, refused addresses), a setting is wrong, or a webhook took the request
    and gave no answer (it may have arrived)."""
    if isinstance(exc, _Refused):
        return exc.code == 429 or exc.code >= 500
    if isinstance(exc, NotifyError):
        return False
    if isinstance(exc, smtplib.SMTPResponseException):  # login, sender, data, connect, HELO: the code says
        return 400 <= exc.smtp_code < 500
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return True
    if isinstance(exc, smtplib.SMTPException):  # refused recipients, no STARTTLS: an answer, and it is no
        return False
    if isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError):
        return False  # something answered, with a certificate that isn't the service's: a setting, or a proxy
    return isinstance(exc, OSError)  # urllib's URLError, or the mail server could not be reached


def deliver(s: dict, msg: Message, env=None, only: tuple[str, ...] | None = None) -> list[Result]:
    """Send `msg` on every configured channel (or those in `only`), one try each. Never raises; a Result's detail
    holds no secret, and its `retry` says whether the message never reached the service (`worth_retrying`)."""
    out = []
    hide = secrets(s, env)
    for name in channels(s):
        if only is not None and name not in only:
            continue
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
            out.append(Result(name, False, scrub(_why(exc), hide), worth_retrying(exc)))
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
        else f"\n{len(bad)} of {len(results)} could not be sent; jobs are not affected by this"
        + (". A search's message that can't get through for want of a connection waits and is sent when the "
           "connection is back" if any(r.retry for r in bad) else ""))
    return 1 if bad else 0


# ---------------------------------------------------------- from the worker --

_lock = threading.Lock()
_failing: dict[str, str] = {}  # channel -> the error already logged (log once until it changes or works)
_sending: list[threading.Thread] = []
_unsent_lock = threading.Lock()  # one thread at a time sends the kept messages (D131)
_SERVICE = {"webhook": "the webhook", "teams": "Teams", "slack": "Slack", "email": "the mail server"}


def flush(timeout: float = 10.0) -> bool:
    """Wait for messages still being sent (tests). True if none is left."""
    end = time.monotonic() + timeout
    with _lock:
        threads = list(_sending)
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    with _lock:
        _sending[:] = [t for t in _sending if t.is_alive()]
        return not _sending


def _start(target, args: tuple, kwargs: dict, name: str) -> threading.Thread:
    t = threading.Thread(target=target, args=args, kwargs=kwargs, name=name, daemon=True)
    with _lock:
        _sending[:] = [x for x in _sending if x.is_alive()] + [t]
    t.start()
    return t


def _note(r: Result, what: str, kept: bool = False, late: str = "") -> None:
    """Log one channel's outcome; a failure once, until its error changes or the channel works again."""
    with _lock:
        before = _failing.get(r.channel)
        if r.ok:
            _failing.pop(r.channel, None)
        else:
            _failing[r.channel] = r.detail
    if r.ok:
        log.info("notified by %s: %s%s%s", r.channel, what, late, " (it works again)" if before else "")
    elif before == r.detail:
        return
    elif kept:
        log.warning("could not notify by %s: %s (%s). Kept: it is sent when %s can be reached again (tried every "
                    "few minutes, then every %d minutes, for %d days); check with: ionomos notify-test", r.channel,
                    r.detail, what, _SERVICE.get(r.channel, r.channel), UNSENT_MAX_S // 60, UNSENT_DAYS)
    else:
        log.warning("could not notify by %s: %s (%s). Not tried again for this message; "
                    "check with: ionomos notify-test", r.channel, r.detail, what)


def _send_logged(s: dict, msg: Message, what: str, log_dir: Path | None = None, job_id=None, order: int = 0) -> None:
    """The sending thread of one message. order: when it was announced (ns), so a slow thread never keeps an older
    message over a newer one about the same job."""
    worked = []
    for r in deliver(s, msg):
        kept = bool(not r.ok and r.retry and log_dir is not None and _keep(log_dir, r, msg, what, job_id, order))
        _note(r, what, kept)
        if r.ok:
            worked.append(r.channel)
    if log_dir is not None and worked:
        _send_unsent(s, log_dir, only=tuple(worked), force=True)  # it works: what was kept for it goes now


def _state_path(log_dir: Path) -> Path:
    return Path(log_dir) / names.NOTIFY_STATE


def _unsent_entry(e) -> bool:
    return (isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("channel"), str)
            and isinstance(e.get("title"), str) and isinstance(e.get("fields"), dict)
            and isinstance(e.get("kept"), (int, float)))


def _read_state(log_dir: Path) -> dict:
    """{"held": {job id: [reasons sent]}, "unsent": [kept messages, oldest first], "retry": {channel: {"tries",
    "next"}}}; a part that is missing or damaged reads as empty."""
    try:
        d = json.loads(_state_path(log_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    held = d.get("held") if isinstance(d.get("held"), dict) else {}
    unsent = [e for e in d.get("unsent") or [] if _unsent_entry(e)] if isinstance(d.get("unsent"), list) else []
    retry = {k: v for k, v in (d.get("retry") or {}).items() if isinstance(v, dict)} \
        if isinstance(d.get("retry"), dict) else {}
    return {"held": held, "unsent": unsent, "retry": retry}


def _write_state(log_dir: Path, state: dict) -> bool:
    p = _state_path(log_dir)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


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


# ------------------------------------------------- kept for later (D131) --


def _delay(tries: int) -> float:
    """Seconds to the next try of a channel after its `tries`-th failure: UNSENT_FIRST_S, doubling, UNSENT_MAX_S."""
    return float(min(UNSENT_FIRST_S * 2 ** max(tries - 1, 0), UNSENT_MAX_S))


def _trim(entries: list[dict], now: float) -> tuple[list[dict], int]:
    """The kept messages younger than UNSENT_DAYS, the newest UNSENT_MAX of them; and how many were dropped."""
    young = [e for e in entries if now - e["kept"] <= UNSENT_DAYS * 86400]
    keep = young[-UNSENT_MAX:]
    return keep, len(entries) - len(keep)


def _tidy(state: dict) -> None:
    """A channel's retry schedule goes with its last kept message."""
    left = {e["channel"] for e in state["unsent"]}
    state["retry"] = {k: v for k, v in state["retry"].items() if k in left}


def _same_job(e: dict, job_id) -> bool:
    return job_id is not None and e.get("job_id") == job_id


def _keep(log_dir: Path, r: Result, msg: Message, what: str, job_id, order: int = 0) -> bool:
    """Keep a message that never reached `r.channel`, to send it later. The newest about a job only: an older one
    about the same job and channel is replaced, and one older than what is kept already is not kept (it was
    announced first, its thread only finished later). False when it can't be kept (the state file can't be
    written)."""
    now = time.time()
    entry = {"id": uuid.uuid4().hex[:12], "channel": r.channel, "job_id": job_id, "what": what, "order": order,
             "title": msg.title, "facts": [list(f) for f in msg.facts], "fields": msg.fields,
             "error": r.detail, "kept": now}
    with _lock:
        state = _read_state(log_dir)
        if any(_same_job(e, job_id) and int(e.get("order") or 0) > order for e in state["unsent"]):
            return True  # a newer message about this job waits already: this one is stale
        old = [e for e in state["unsent"] if not (e["channel"] == r.channel and _same_job(e, job_id))]
        state["unsent"], dropped = _trim([*old, entry], now)
        sched = state["retry"].setdefault(r.channel, {"tries": 1, "next": now + _delay(1)})
        sched["error"] = r.detail
        _tidy(state)
        ok = _write_state(log_dir, state)
        waiting = list(state["unsent"])
    if not ok:
        return False
    if dropped:
        log.warning("%d kept notification(s) dropped: older than %d days, or more than %d waiting", dropped,
                    UNSENT_DAYS, UNSENT_MAX)
    _tell_unsent(log_dir, waiting, new=True)
    return True


def _drop_unsent(log_dir: Path, job_id, order: int) -> None:
    """A newer message about `job_id` (announced at `order`) replaces whatever was kept about it."""
    with _lock:
        state = _read_state(log_dir)
        left = [e for e in state["unsent"] if not (_same_job(e, job_id) and int(e.get("order") or 0) < order)]
        if len(left) == len(state["unsent"]):
            return
        state["unsent"] = left
        _tidy(state)
        _write_state(log_dir, state)
    _tell_unsent(log_dir, left)


def _when(iso) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "earlier"


def late_message(entry: dict) -> Message:
    """A kept message as it is sent later: the same title, facts and fields, and a line saying when it happened."""
    facts = tuple((str(f[0]), str(f[1])) for f in entry.get("facts") or []
                  if isinstance(f, (list, tuple)) and len(f) == 2)
    when = _when(entry["fields"].get("time"))
    facts += (("Sent late", f"this happened {when}; the PC could not reach "
                            f"{_SERVICE.get(entry['channel'], entry['channel'])} then"),)
    text = Message(entry["title"], facts).text
    return Message(entry["title"], facts, {**entry["fields"], "text": text})


def _send_unsent(s: dict, log_dir: Path, only: tuple[str, ...] | None = None, force: bool = False,
                 now: float | None = None, locked: bool = False) -> int:
    """Send the kept messages of each channel that is due (all of them with `force`), oldest first. A channel that
    still can't be reached waits longer (`_delay`); one that now says no drops that message. Returns how many were
    sent. One thread at a time: a second call while one runs returns 0. locked: the caller took _unsent_lock for
    this call (retry_unsent, before starting the thread); it is released here."""
    if not locked and not _unsent_lock.acquire(blocking=False):
        return 0
    try:
        now = time.time() if now is None else now
        live = set(channels(s))
        with _lock:
            state = _read_state(log_dir)
            before = len(state["unsent"])
            state["unsent"], dropped = _trim(state["unsent"], now)
            gone = [e for e in state["unsent"] if e["channel"] not in live]
            state["unsent"] = [e for e in state["unsent"] if e["channel"] in live]
            _tidy(state)
            if len(state["unsent"]) != before:
                _write_state(log_dir, state)
            due: dict[str, list[dict]] = {}
            for e in state["unsent"]:
                sched = state["retry"].get(e["channel"]) or {}
                if (only is None or e["channel"] in only) and (force or float(sched.get("next") or 0) <= now):
                    due.setdefault(e["channel"], []).append(e)
        if dropped:
            log.warning("%d kept notification(s) dropped: older than %d days", dropped, UNSENT_DAYS)
        if gone:
            log.warning("%d kept notification(s) dropped: %s no longer set up in config.yaml", len(gone),
                        ", ".join(sorted({e["channel"] for e in gone})))
        sent = 0
        for ch, entries in due.items():
            for e in entries:
                try:
                    msg = late_message(e)
                except Exception:  # noqa: BLE001 - a damaged entry is dropped, not retried for a week
                    _forget_unsent(log_dir, e["id"])
                    continue
                (r,) = deliver(s, msg, only=(ch,)) or [Result(ch, False, "not set up", False)]
                if r.ok:
                    _forget_unsent(log_dir, e["id"])
                    sent += 1
                    _note(r, str(e.get("what") or "a message"),
                          late=f" (sent late; it happened {_when(e['fields'].get('time'))})")
                    continue
                if r.retry:  # still out of reach: the channel waits longer, its other messages too
                    _postpone(log_dir, ch, r.detail, now)
                    _note(r, str(e.get("what") or "a message"), kept=True)
                    break
                _forget_unsent(log_dir, e["id"])  # the service answered no: as for a new message, not tried again
                _note(r, str(e.get("what") or "a message"))
        with _lock:
            left = _read_state(log_dir)["unsent"]
        if sent or due or dropped or gone:
            _tell_unsent(log_dir, left)
        return sent
    finally:
        _unsent_lock.release()


def _forget_unsent(log_dir: Path, entry_id: str) -> None:
    with _lock:
        state = _read_state(log_dir)
        state["unsent"] = [e for e in state["unsent"] if e["id"] != entry_id]
        _tidy(state)
        _write_state(log_dir, state)


def _postpone(log_dir: Path, channel: str, detail: str, now: float) -> None:
    with _lock:
        state = _read_state(log_dir)
        sched = state["retry"].setdefault(channel, {"tries": 0})
        sched["tries"] = int(sched.get("tries") or 0) + 1
        sched["next"] = now + _delay(sched["tries"])
        sched["error"] = detail
        _tidy(state)
        _write_state(log_dir, state)


def unsent(log_dir: Path | None) -> list[dict]:
    """The messages kept for later, oldest first (for `ionomos status` and the tests)."""
    if log_dir is None:
        return []
    with _lock:
        return _read_state(log_dir)["unsent"]


def _tell_unsent(log_dir: Path, waiting: list[dict], new: bool = False) -> None:
    """One attention item while messages wait (a warning: no pop-up), closed when none is left. A person who
    dismissed it is not asked again until every message has gone."""
    try:
        from ionomos import attention

        item = next((i for i in attention.items(log_dir, open_only=False) if i.key == ATTENTION_KIND), None)
        if not waiting:
            if item is not None and item.state != "resolved":
                attention.resolve(log_dir, item.id)
            return
        if item is not None and not item.is_open and (not new or item.state == "dismissed"):
            return
        if item is None and not new:
            return
        chans = list(dict.fromkeys(e["channel"] for e in waiting))
        n = len(waiting)
        message = (f"{n} message{'s' if n != 1 else ''} about searches could not be sent by {', '.join(chans)}: "
                   f"{waiting[-1].get('error') or 'no connection'}")
        if item is not None and item.is_open and item.message == message:
            return
        attention.raise_item(
            log_dir, ATTENTION_KIND, "Notifications are waiting to be sent", message, key=ATTENTION_KIND,
            severity="warning",
            causes=["This PC has no internet connection right now, or it can't reach the service (a firewall, a "
                    "proxy, the service is down)"],
            fixes=[f"Nothing to do: they are sent by themselves when the connection is back (tried every few "
                   f"minutes, then every {UNSENT_MAX_S // 60} minutes, for {UNSENT_DAYS} days). Searches, reports "
                   "and the atlas hand-off are not affected",
                   "To check the connection now: Notifications tab → Send test, or run  ionomos notify-test"],
            data={"waiting": n, "channels": chans})
    except Exception:  # noqa: BLE001 - telling a person must not break a message
        log.exception("could not record %s", ATTENTION_KIND)


def retry_unsent(cfg, now: float | None = None, force: bool = False) -> threading.Thread | None:
    """The watcher's maintenance thread calls this every minute (and once at start with force=True, whatever the
    schedule says): the kept messages of each channel that is due are sent by a daemon thread, which is returned
    (None: nothing is due, notifications are off, or one is already sending). Returns at once; never raises."""
    try:
        s = getattr(cfg, "notify", None) or {}
        log_dir = getattr(cfg, "log_dir", None)
        if log_dir is None or not s.get("enabled") or not channels(s) or _unsent_lock.locked():
            return None
        now = time.time() if now is None else now
        with _lock:
            state = _read_state(log_dir)
        if not state["unsent"]:
            return None
        if not force and not any(float((state["retry"].get(e["channel"]) or {}).get("next") or 0) <= now
                                 for e in state["unsent"]):
            return None
        if not _unsent_lock.acquire(blocking=False):
            return None
    except Exception as exc:  # noqa: BLE001 - the maintenance loop must go on
        log.warning("could not send the kept notifications: %s", type(exc).__name__)
        return None
    try:  # the thread releases the lock when it is done
        return _start(_send_unsent, (s, log_dir), {"force": force, "now": now, "locked": True}, "notify-unsent")
    except Exception as exc:  # noqa: BLE001 - e.g. no thread can be started
        _unsent_lock.release()
        log.warning("could not send the kept notifications: %s", type(exc).__name__)
        return None


def announce(cfg, event: str, job, reason: str = "", summary: dict | None = None) -> threading.Thread | None:
    """Tell the configured channels that `job` is done / failed / held. Returns at once: the sending is
    done by the returned daemon thread (None if nothing is sent). Never raises.

    "held" is sent once per job and reason (remembered in <log_dir>/notify_state.json, so a restart
    doesn't repeat it); the job running to done or failed clears that. A message that never reached a
    channel is kept and sent later (D131); a newer message about the same job replaces it."""
    try:
        s = getattr(cfg, "notify", None) or {}
        if not s.get("enabled") or not channels(s):
            return None
        order = time.time_ns()
        log_dir = getattr(cfg, "log_dir", None)
        if log_dir is not None:
            if event == "held":
                if not _first_time_held(log_dir, job.id, reason):
                    return None
            else:
                _forget_held(log_dir, job.id)
            _drop_unsent(log_dir, job.id, order)
        if event not in s["on"]:
            return None
        msg = build(event, job, s, reason, summary)
        return _start(_send_logged, (s, msg, f"job {job.id} {_WORDS[event]}"),
                      {"log_dir": log_dir, "job_id": job.id, "order": order}, f"notify-job{job.id}")
    except Exception as exc:  # noqa: BLE001 - never let a message stop a search
        log.warning("could not prepare a notification for job %s: %s", getattr(job, "id", "?"), type(exc).__name__)
        return None
