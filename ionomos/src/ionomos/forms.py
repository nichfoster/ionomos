"""
The app's settings forms without Tk (D67): config.yaml blocks <-> the text and tick boxes a window shows.

    fields = export_fields(cfg["analysis"].get("export"))      # analysis.export -> {field: str | bool}
    block = export_block(fields, existing)                      # back; raises FormError, keeps unknown keys
    fields = notify_fields(cfg.get("notify"))                   # notify: -> {field: str | bool}
    block = notify_block(fields, existing)                      # back; checked by notify.settings_from

Everything that decides what is valid lives in the module that uses the setting (downstream/charts.py
style_layer, notify.settings_from), so the window, config.yaml and the command line accept the same things.
The Tk code (analysis_tab.py, notify_tab.py) only copies these dicts in and out of its variables.

A field left empty means "not set": the key is left out and its default applies. A key the window has no
field for (line_scale, title, png_scale ... ; a typo) is kept as it was, so the loader can still name it.
"""
from __future__ import annotations

import copy
import re

from ionomos.config import ConfigError


class FormError(ConfigError):
    """A value typed into the window is not valid. The text names the field and never holds a secret."""


# ------------------------------------------------------------- export style --

# The fields the Analysis tab's "Figure style" page shows, in analysis.export's own key names (charts.py).
EXPORT_TEXT = ("size", "width", "height", "unit", "font_pt", "font_family", "palette", "up", "down", "neutral",
               "background", "zip_format")
EXPORT_NUMBERS = ("width", "height", "font_pt")
COLOURS = ("up", "down", "neutral")


def _charts():
    from ionomos.downstream import charts

    return charts


def export_choices() -> dict[str, list[str]]:
    """The values each drop-down offers (from charts.py, so the window and the loader never disagree)."""
    c = _charts()
    return {"size": list(c.SIZES), "unit": ["px", "mm"], "palette": ["default", "colorblind", "grey", "custom"],
            "background": ["light", "dark", "transparent"], "zip_format": ["both", "svg", "png"]}


def size_label(size: str) -> str:
    """'16:9 slide (1280 x 720 px, 14 pt)' for a size preset; custom: 'Custom size'."""
    c = _charts()
    z = c.SIZES.get(size)
    if z is None:
        return size
    if size == "custom":
        return z[0]
    return f"{z[0]} ({z[1]} x {z[2]} {z[3]}, {z[4]} pt)"


def size_key(text: str) -> str:
    """A size preset's key from what the drop-down shows (its label) or the key itself; unknown: as typed."""
    c = _charts()
    t = str(text or "").strip()
    if t in c.SIZES:
        return t
    return next((k for k in c.SIZES if size_label(k) == t), t)


def default_font_pt(size: str) -> int:
    """The text size a preset takes when none is given (14 pt for a slide, 7 pt for a journal column)."""
    c = _charts()
    z = c.SIZES.get(size)
    return z[4] if z and z[4] else c.STYLE_DEFAULTS["font_pt"]


def export_defaults() -> dict:
    """What the fields hold when nothing is set: the defaults, as text (empty = the default applies)."""
    c = _charts()
    d = c.STYLE_DEFAULTS
    out: dict = {k: "" for k in EXPORT_TEXT}
    out.update(size=d["size"], unit=d["unit"], palette=d["palette"], background=d["background"],
               zip_format=d["zip_format"], font_pt=str(default_font_pt(d["size"])), font_family=d["font_family"])
    for name in c.STATIC_FIGURES:
        out[f"figure.{name}"] = False
    return out


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def export_fields(block) -> dict:
    """analysis.export -> the window's fields (text, and figure.<name> ticks). A missing key shows its default."""
    c = _charts()
    block = block if isinstance(block, dict) else {}
    out = export_defaults()
    for k in EXPORT_TEXT:
        if block.get(k) not in (None, ""):
            out[k] = _text(block[k])
    if "font_pt" not in block and out["size"] in c.SIZES:
        out["font_pt"] = str(default_font_pt(out["size"]))
    figs = block.get("figures")
    try:
        chosen = c.style_layer({"figures": figs})["figures"] if figs is not None else []
    except c.StyleError:
        chosen = []
    for name in c.STATIC_FIGURES:
        out[f"figure.{name}"] = name in chosen
    return out


def font_after_size_change(old_size: str, new_size: str, font_pt: str) -> str:
    """The text-size field when the size preset changes: it follows the preset if it held the old one's default
    (14 pt on a slide -> 7 pt for a journal column), and stays as typed otherwise."""
    cur = font_pt.strip()
    if new_size == "custom":
        return cur
    if not cur or _number(cur) == default_font_pt(old_size):
        return str(default_font_pt(new_size))
    return cur


def _number(raw: str):
    s = raw.strip().replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return int(v) if v.is_integer() else v


_LABELS = {"size": "Size", "width": "Width", "height": "Height", "unit": "Unit", "font_pt": "Text size (pt)",
           "font_family": "Font", "palette": "Colours", "up": "Up colour", "down": "Down colour",
           "neutral": "Not-significant colour", "background": "Background", "zip_format": "Formats in the zip"}


def _check_export(k: str, v):
    """One value through charts.style_layer (the loader's own check). Raises FormError naming the field."""
    c = _charts()
    try:
        return c.style_layer({k: v})[k]
    except c.StyleError as exc:
        why = str(exc).removeprefix(f"export.{k} ")
        raise FormError(f"Figure style → {_LABELS.get(k, k)}: {why} (analysis.export.{k})") from None


def export_block(fields: dict, existing=None) -> dict:
    """The window's fields -> analysis.export. Each value is checked by charts.style_layer; an empty field leaves
    its key out; keys the window doesn't show (line_scale, title, ...) are kept from `existing`."""
    c = _charts()
    out = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    for k in EXPORT_TEXT:
        raw = str(fields.get(k, "") or "").strip()
        if not raw:
            out.pop(k, None)
            continue
        if k in EXPORT_NUMBERS:
            v = _number(raw)
            if v is None:
                raise FormError(f"Figure style → {_LABELS[k]}: {raw!r} is not a number (analysis.export.{k})")
        elif k in COLOURS:
            v = raw if raw.startswith("#") else "#" + raw
        else:
            v = raw
        out[k] = _check_export(k, v)
    size = out.get("size", c.STYLE_DEFAULTS["size"])
    if size == "custom":
        missing = [_LABELS[k] for k in ("width", "height") if k not in out]
        if missing:
            raise FormError(f"Figure style → a custom size needs {' and '.join(missing).lower()}")
        out.setdefault("unit", "px")
    elif not (isinstance(existing, dict) and "unit" in existing):
        out.pop("unit", None)  # only a custom size reads it; don't add a key the file didn't have
    out["figures"] = [n for n in c.STATIC_FIGURES if fields.get(f"figure.{n}")]
    return out


def export_summary(block) -> str:
    """One line for the window: what the lab's figures look like with these settings."""
    c = _charts()
    try:
        s = c.style_from(block if isinstance(block, dict) else {})
    except c.StyleError as exc:
        return f"not valid: {exc}"
    w, h, mm = c.size_px(s)
    size = (f"{s['width']:g} x {s['height']:g} {s['unit']}" if s["size"] == "custom"
            else size_label(s["size"]).split(" (")[0])
    figs = ", ".join(s["figures"]) or "none"
    return (f"{size}, {s['font_pt']:g} pt {s['font_family']}, {s['palette']} colours on {s['background']}; "
            f"written after each analysis: {figs}")


# ------------------------------------------------------------ notifications --

HOOK_LABELS = {"webhook": "Webhook (JSON)", "teams": "Microsoft Teams", "slack": "Slack"}
SECRET_FIELDS = ("webhook.url", "teams.url", "slack.url", "email.password")


def _notify():
    from ionomos import notify

    return notify


def notify_fields(block) -> dict:
    """config.yaml notify: -> the window's fields. Secrets are copied as they are; the window masks them."""
    n = _notify()
    raw = n.fix_keys(block) if isinstance(block, dict) else {}
    on = raw.get("on", n.DEFAULTS["on"])
    on = [on] if isinstance(on, str) else (on if isinstance(on, list) else [])
    out: dict = {"enabled": raw.get("enabled") is True, "include_names": raw.get("include_names", True) is not False,
                 "timeout_seconds": _text(raw.get("timeout_seconds", n.DEFAULTS["timeout_seconds"]))}
    for e in n.EVENTS:
        out[f"on.{e}"] = e in on
    for name in n.HOOKS:
        c = raw.get(name)
        c = {"url": c} if isinstance(c, str) else (c if isinstance(c, dict) else {})
        out[f"{name}.url"] = _text(c.get("url"))
        out[f"{name}.url_env"] = _text(c.get("url_env"))
    e = raw.get("email") if isinstance(raw.get("email"), dict) else {}
    d = n.DEFAULTS["email"]
    for k in ("host", "username", "password", "password_env", "from"):
        out[f"email.{k}"] = _text(e.get(k))
    out["email.port"] = _text(e.get("port", d["port"]))
    out["email.security"] = _text(e.get("security") or d["security"])
    to = e.get("to")
    out["email.to"] = ", ".join(str(a) for a in (to if isinstance(to, list) else [to] if to else []))
    return out


def _int(raw: str, where: str):
    v = _number(raw)
    if v is None:
        raise FormError(f"Notifications → {where}: {raw.strip()!r} is not a number")
    return v


def notify_block(fields: dict, existing=None) -> dict:
    """The window's fields -> config.yaml notify:, checked by notify.settings_from (as `ionomos check` does).
    Keys the window doesn't show are kept from `existing` (a typo then still fails the loader, by name)."""
    n = _notify()
    out = n.fix_keys(copy.deepcopy(existing)) if isinstance(existing, dict) else {}
    out["enabled"] = bool(fields.get("enabled"))
    out["on"] = [e for e in n.EVENTS if fields.get(f"on.{e}")]
    out["include_names"] = bool(fields.get("include_names", True))
    t = str(fields.get("timeout_seconds", "") or "").strip()
    out["timeout_seconds"] = _int(t, "give up after (s)") if t else n.DEFAULTS["timeout_seconds"]
    for name in n.HOOKS:
        c = out.get(name)
        c = {"url": c} if isinstance(c, str) else (dict(c) if isinstance(c, dict) else {})
        c["url"] = str(fields.get(f"{name}.url", "") or "").strip()
        c["url_env"] = str(fields.get(f"{name}.url_env", "") or "").strip()
        out[name] = c
    e = dict(out["email"]) if isinstance(out.get("email"), dict) else {}
    for k in ("host", "username", "password_env", "from"):
        e[k] = str(fields.get(f"email.{k}", "") or "").strip()
    e["password"] = str(fields.get("email.password", "") or "")  # a password may start or end with a space
    port = str(fields.get("email.port", "") or "").strip()
    e["port"] = _int(port, "email port") if port else n.DEFAULTS["email"]["port"]
    e["security"] = str(fields.get("email.security", "") or "").strip().lower() or n.DEFAULTS["email"]["security"]
    e["to"] = [a.strip() for a in re.split(r"[,;\n]", str(fields.get("email.to", "") or "")) if a.strip()]
    out["email"] = e
    if not out["on"]:
        raise FormError("Notifications → tick at least one of done, failed, waiting (notify.on)")
    try:
        n.settings_from(out)
    except n.NotifyError as exc:
        raise FormError(f"Notifications → {exc} (notify:)") from None
    return out


def notify_settings(fields: dict, existing=None) -> dict:
    """The complete settings notify.send_test takes, from the window's fields. Raises FormError."""
    return _notify().settings_from(notify_block(fields, existing))
