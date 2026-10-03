"""The app's settings forms without Tk (forms.py, D67): analysis.export and notify: <-> the window's fields.

Every value is checked by the module that uses it (charts.style_layer, notify.settings_from), so what the
window accepts is what config.yaml and the command line accept. These run everywhere (no display needed)."""
from __future__ import annotations

import pytest
import yaml

from ionomos import configio, forms, notify
from ionomos.config import ConfigError, load
from ionomos.downstream import charts

SLACK = "https://hooks.slack.com/services/T000/B000/secret-key-123"


# ------------------------------------------------------------- export style --


def test_export_fields_show_the_defaults_when_nothing_is_set():
    f = forms.export_fields(None)
    assert f["size"] == "slide169" and f["font_pt"] == "14" and f["font_family"] == "Arial"
    assert f["palette"] == "default" and f["background"] == "light" and f["zip_format"] == "both"
    assert not any(f[f"figure.{n}"] for n in charts.STATIC_FIGURES)
    assert f == forms.export_defaults()


@pytest.mark.parametrize("size, pt", [("slide169", "14"), ("slide43", "14"), ("half", "12"), ("col1", "7"),
                                      ("col2", "7")])
def test_a_size_without_a_text_size_shows_the_size_s_own(size, pt):
    assert forms.export_fields({"size": size})["font_pt"] == pt
    assert forms.default_font_pt(size) == int(pt) == charts.SIZES[size][4]


def test_the_text_size_follows_the_preset_unless_typed():
    assert forms.font_after_size_change("slide169", "col1", "14") == "7"
    assert forms.font_after_size_change("slide169", "col1", "") == "7"
    assert forms.font_after_size_change("slide169", "col1", "11") == "11"
    assert forms.font_after_size_change("col1", "custom", "7") == "7"


def test_size_labels_round_trip():
    for key in charts.SIZES:
        assert forms.size_key(forms.size_label(key)) == key == forms.size_key(key)
    assert forms.size_label("slide169") == "16:9 slide (1280 x 720 px, 14 pt)"


def test_export_block_round_trips_and_keeps_keys_the_window_does_not_show():
    existing = {"size": "slide169", "font_pt": 14, "line_scale": 1.5, "title": False, "label_count": 8}
    f = forms.export_fields(existing)
    f.update(size="col2", font_pt="7,5", font_family="Segoe UI", palette="custom", up="D55E00", down="#0072b2",
             background="transparent", zip_format="svg")
    f["figure.volcano"] = f["figure.pca"] = True
    block = forms.export_block(f, existing)
    assert block == {"size": "col2", "font_pt": 7.5, "font_family": "Segoe UI", "palette": "custom", "up": "#d55e00",
                     "down": "#0072b2", "background": "transparent", "zip_format": "svg",
                     "figures": ["volcano", "pca"], "line_scale": 1.5, "title": False, "label_count": 8}
    assert existing["size"] == "slide169", "the existing block is not changed in place"
    assert charts.style_layer(block)  # the loader's own check accepts it
    assert forms.export_fields(block)["up"] == "#d55e00"


def test_an_empty_field_leaves_its_key_out():
    f = forms.export_fields({"size": "slide169", "font_pt": 20, "font_family": "Calibri"})
    f.update(font_pt="", font_family="")
    block = forms.export_block(f, {"size": "slide169", "font_pt": 20, "font_family": "Calibri"})
    assert "font_pt" not in block and "font_family" not in block and "unit" not in block
    assert charts.style_from(block)["font_pt"] == 14


@pytest.mark.parametrize("field, value, says", [
    ("font_pt", "99", "Text size (pt): must be a number from 4 to 48"),
    ("font_pt", "big", "'big' is not a number"),
    ("font_family", "Arial; DROP", "Font: must be a font's name"),
    ("up", "red", "Up colour: must be a colour like"),
    ("size", "poster", "Size: must be one of slide169"),
    ("width", "5", "Width: must be a number from 20 to 8000"),
])
def test_bad_values_name_the_field(field, value, says):
    f = forms.export_fields(None)
    f[field] = value
    with pytest.raises(forms.FormError, match="Figure style → ") as e:
        forms.export_block(f)
    assert says in str(e.value) and isinstance(e.value, ConfigError)


def test_a_custom_size_needs_width_and_height():
    f = forms.export_fields(None)
    f.update(size="custom", width="900")
    with pytest.raises(forms.FormError, match="custom size needs height"):
        forms.export_block(f)
    f["height"] = "600"
    block = forms.export_block(f)
    assert (block["size"], block["width"], block["height"], block["unit"]) == ("custom", 900, 600, "px")
    assert charts.size_px(charts.style_from(block))[:2] == (900, 600)


def test_the_config_writer_keeps_what_the_window_wrote(tmp_path):
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    f = forms.export_fields(d["analysis"]["export"])
    f.update(size="col1", font_pt="", palette="grey")
    f["figure.heatmap"] = True
    d["analysis"]["export"] = forms.export_block(f, d["analysis"]["export"])
    p = configio.write_config(tmp_path / "config.yaml", d)
    cfg = load(p, check_paths=False)
    assert cfg.analysis["export"]["size"] == "col1" and cfg.analysis["export"]["font_pt"] == 7
    assert cfg.analysis["export"]["figures"] == ["heatmap"] and cfg.analysis["export"]["palette"] == "grey"


def test_read_config_does_not_give_a_journal_size_the_slide_text_size(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"analysis": {"export": {"size": "col1"}}}), encoding="utf-8")
    ex = configio.read_config(p)["analysis"]["export"]
    assert "font_pt" not in ex and forms.export_fields(ex)["font_pt"] == "7"
    assert "font_pt: 7   #" in configio.dump_config(configio.read_config(p))
    p.write_text(yaml.safe_dump({"analysis": {"export": {"size": "col1", "font_pt": 9}}}), encoding="utf-8")
    assert configio.read_config(p)["analysis"]["export"]["font_pt"] == 9


def test_export_summary_says_what_the_figures_look_like():
    s = forms.export_summary({"size": "col1", "palette": "colorblind", "figures": ["volcano", "pca"]})
    assert s == ("Journal figure, one column, 7 pt Arial, colorblind colours on light; written after each "
                 "analysis: volcano, pca")
    assert forms.export_summary({"size": "custom", "width": 900, "height": 600}).startswith("900 x 600 px")
    assert forms.export_summary({"palette": "pink"}).startswith("not valid")


# ------------------------------------------------------------ notifications --


def _fields(**over) -> dict:
    f = forms.notify_fields(configio.defaults()["notify"])
    f.update(over)
    return f


def test_notify_fields_from_the_defaults_and_from_shorthand():
    f = forms.notify_fields(configio.defaults()["notify"])
    assert f["enabled"] is False and f["include_names"] is True and f["timeout_seconds"] == "10"
    assert all(f[f"on.{e}"] for e in notify.EVENTS)
    assert f["email.port"] == "587" and f["email.security"] == "starttls" and f["email.to"] == ""
    g = forms.notify_fields({True: ["done"], "slack": SLACK, "email": {"to": "a@x.org"}})  # bare `on:` = True
    assert g["on.done"] and not g["on.failed"] and g["slack.url"] == SLACK and g["email.to"] == "a@x.org"
    assert forms.notify_fields(None)["enabled"] is False


def test_notify_block_round_trips_through_the_loader(tmp_path):
    f = _fields(enabled=True, **{"on.held": False, "slack.url": SLACK, "teams.url_env": "TEAMS_HOOK",
                                 "email.host": "smtp.example.org", "email.port": "465", "email.security": "ssl",
                                 "email.username": "pc", "email.password": " p w ", "email.from": "pc@example.org",
                                 "email.to": "a@example.org; b@example.org", "timeout_seconds": "20"})
    block = forms.notify_block(f, configio.defaults()["notify"])
    assert block["on"] == ["done", "failed"] and block["slack"] == {"url": SLACK, "url_env": ""}
    assert block["email"]["password"] == " p w " and block["email"]["port"] == 465
    assert block["email"]["to"] == ["a@example.org", "b@example.org"] and block["timeout_seconds"] == 20
    d = configio.defaults(str(tmp_path / "Auto"), str(tmp_path / "General"))
    d["notify"] = block
    p = configio.write_config(tmp_path / "config.yaml", d)
    cfg = load(p, check_paths=False)
    assert cfg.notify["enabled"] and notify.channels(cfg.notify) == ["teams", "slack", "email"]
    assert forms.notify_fields(configio.read_config(p)["notify"]) == {**f, "email.to": "a@example.org, b@example.org",
                                                                      "email.port": "465"}


@pytest.mark.parametrize("over, says", [
    ({"enabled": True}, "enabled is true but no channel is set"),
    ({"slack.url": "http://hooks.example.org/x"}, "slack.url must start with https://"),
    ({"on.done": False, "on.failed": False, "on.held": False}, "tick at least one of done, failed, waiting"),
    ({"timeout_seconds": "ten"}, "'ten' is not a number"),
    ({"timeout_seconds": "90"}, "timeout_seconds must be a number from 1 to 60"),
    ({"email.host": "smtp.x.org", "email.to": "a@x.org"}, "email.from: give the address"),
    ({"email.host": "smtp.x.org", "email.to": "a@x.org", "email.from": "p@x.org", "email.security": "none",
      "email.username": "u", "email.password": "pw"}, "would send the password unencrypted"),
    ({"teams.url_env": "https://not-a-name"}, "must be the NAME of an environment variable"),
])
def test_bad_notify_settings_say_why_and_never_echo_a_secret(over, says):
    with pytest.raises(forms.FormError) as e:
        forms.notify_block(_fields(**over))
    assert says in str(e.value) and str(e.value).startswith("Notifications → ")
    assert "pw" not in str(e.value).replace("password", "")


def test_a_key_the_window_does_not_know_is_kept_so_the_loader_names_it():
    with pytest.raises(forms.FormError, match="colour: unknown setting"):
        forms.notify_block(_fields(), {"colour": "blue"})
    with pytest.raises(forms.FormError, match=r"slack\.channel: unknown setting"):
        forms.notify_block(_fields(**{"slack.url": SLACK}), {"slack": {"url": "", "channel": "#lab"}})


def test_notify_settings_is_what_send_test_takes():
    s = forms.notify_settings(_fields(**{"slack.url": SLACK}))
    assert s == notify.settings_from(forms.notify_block(_fields(**{"slack.url": SLACK})))
    assert notify.channels(s) == ["slack"] and s["enabled"] is False


def test_secret_fields_are_the_ones_notify_treats_as_secrets():
    s = notify.settings_from({"slack": {"url": SLACK}, "email": {"host": "h", "username": "u", "password": "pw!",
                                                                  "from": "a@b.c", "to": ["d@e.f"]}})
    secret_values = set(notify.secrets(s))
    f = forms.notify_fields(s)
    assert {f[k] for k in forms.SECRET_FIELDS if f[k]} == secret_values


# --------------------------------------------------------------------- help --


def test_every_help_button_in_the_app_opens_an_entry_that_exists():
    import re
    from pathlib import Path

    from ionomos import help as helpdoc

    src = Path(forms.__file__).parent
    ids = {m for f in ("app.py", "analysis_tab.py", "accuracy_page.py", "notify_tab.py")
           for m in re.findall(r"open_help\(\"([\w.-]+)\"\)", (src / f).read_text(encoding="utf-8"))}
    assert {"faq.figure-style", "faq.check-accuracy", "faq.notify"} <= ids
    assert ids <= set(helpdoc.entries()), ids - set(helpdoc.entries())


# ------------------------------------------------------------- notify-test --


def test_run_test_is_what_notify_test_prints(monkeypatch):
    lines = []
    s = notify.settings_from({})
    assert notify.run_test(s, lines.append) == 1 and "notifications are not set up" in lines[0]
    sent = []
    monkeypatch.setattr(notify, "send_test", lambda s, env=None: sent.append(s) or [
        notify.Result("slack", True, "HTTP 200"), notify.Result("email", False, "could not connect: refused")])
    lines.clear()
    s = notify.settings_from({"slack": {"url": SLACK}, "email": {"host": "h", "from": "a@b.c", "to": ["d@e.f"]}})
    assert notify.run_test(s, lines.append) == 1 and sent == [s]
    text = "\n".join(lines)
    assert "notify.enabled is false" in text and "sending a test message by slack, email" in text
    assert " ✓ slack    sent: HTTP 200" in text and " ✗ email    NOT sent: could not connect" in text
    assert "1 of 2 could not be sent" in text and "secret-key" not in text
