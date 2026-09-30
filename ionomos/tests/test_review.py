"""The review window before filing, condition codes in DIA names (X_D1 = DMSO rep 1), and a watcher that
picks up users / aliases added in the app without a restart.

The scenario these came from (2026-09-28): Kosuke dropped a DIA folder named with his initials, KC, and raws
ending _D1.._D3 (DMSO) and _C1.._C3 (compound). Ionomos didn't know KC, read every file as its own condition,
and after Kosuke was added in the app the open window still didn't know him — the watcher had to be restarted.
"""
import time

import pytest
import yaml

from ionomos.config import ConfigError, LiveConfig, load
from ionomos.intake import DraftFile, IntakeError, IntakeResult, Kind, draft, intake, plan
from ionomos.ledger import Ledger
from ionomos.manifest import load_overrides, save_overrides
from ionomos.naming import DEFAULT_CONDITION_CODES, NamingError, parse_raw_name
from ionomos.resolve import Answer, guess_control, summarize, to_overrides, validate
from tests.conftest import make_drop, make_tk_root

KC_RAWS = [f"KC_DIA_{c}{r}.raw" for c in "DC" for r in (1, 2, 3)]


# ------------------------------------------------------------------ naming --


@pytest.mark.parametrize(("name", "sample", "rep"), [
    ("KC_DIA_D1.raw", "KC_DIA_DMSO", 1),
    ("KC_DIA_C3.raw", "KC_DIA_Compound", 3),
    ("D2.raw", "DMSO", 2),
    ("X-C1.raw", "X-Compound", 1),
    ("X_DM4.raw", "X_DM", 4),                         # an unknown 1-2 letter code is kept as it is
    ("X_D1_20260508180610.raw", "X_DMSO", 1),         # Xcalibur's duplicate-name stamp is still ignored
    ("DMSO_1.raw", "DMSO", 1),                        # the usual forms are unchanged
    ("Drug_R3.raw", "Drug", 3),
    ("CS_22rv1_175_DIA_2.raw", "CS_22rv1_175_DIA", 2),
    # real inventory name: HCD33 is a collision energy, never "condition HCD, replicate 33"
    ("CS_isoDTB_ELK_3_1-7_DIA_HCD33.raw", "CS_isoDTB_ELK_3_1-7_DIA_HCD33", 1),
    ("X_HeLa1.raw", "X_HeLa1", 1),
])
def test_dia_condition_codes(name, sample, rep):
    r = parse_raw_name(name, "DIA")
    assert (r.sample, r.rep) == (sample, rep)


def test_condition_codes_are_configurable():
    assert parse_raw_name("X_V2.raw", "DIA", {"V": "Vehicle", "T": "Treated"}).sample == "X_Vehicle"
    assert parse_raw_name("X_Veh2.raw", "DIA", {"Veh": "Vehicle"}).sample == "X_Vehicle"  # longer codes if listed
    with pytest.raises(NamingError):  # isoDTB tails are unaffected: X_D1 still isn't _<rep>_<fraction>
        parse_raw_name("X_D1.raw", "isoDTB")


def test_config_condition_codes(lab):
    assert load(lab["cfg_path"]).condition_codes == DEFAULT_CONDITION_CODES
    d = dict(lab["cfg_dict"], naming={"condition_codes": {"V": "Vehicle"}})
    lab["cfg_path"].write_text(yaml.safe_dump(d))
    assert load(lab["cfg_path"]).condition_codes == {"V": "Vehicle"}
    d["naming"] = {"condition_codes": {"V1": "Vehicle"}}
    lab["cfg_path"].write_text(yaml.safe_dump(d))
    with pytest.raises(ConfigError, match="condition_codes"):
        load(lab["cfg_path"])


def test_coded_dia_drop_plans_two_conditions_with_replicates(lab):
    (lab["general"] / "Kosuke").mkdir()
    folder = make_drop(lab["inbox"], "20260927_Kosuke_DIA_pulldown", KC_RAWS)
    p = plan(folder, lab["cfg"])
    got = sorted((m.experiment, m.bioreplicate) for m in p.manifest)
    assert got == sorted([("KC_DIA_DMSO", r) for r in (1, 2, 3)] + [("KC_DIA_Compound", r) for r in (1, 2, 3)])


# -------------------------------------------------------- live config --


def _add_user_with_alias(lab, user, alias):
    (lab["general"] / user).mkdir(exist_ok=True)
    d = lab["cfg_dict"]
    d.setdefault("users", {}).setdefault("aliases", {})[user] = [alias]
    time.sleep(0.01)  # a distinct mtime on coarse file systems
    lab["cfg_path"].write_text(yaml.safe_dump(d))


def test_live_config_picks_up_a_user_added_in_the_app(lab):
    live = LiveConfig(lab["cfg"])
    folder = make_drop(lab["inbox"], "20260927_KC_DIA_pulldown", KC_RAWS)
    with pytest.raises(IntakeError) as e:
        plan(folder, live.get())
    assert e.value.kind == Kind.USER
    _add_user_with_alias(lab, "Kosuke", "KC")
    assert plan(folder, live.get()).folder.user == "Kosuke"  # no restart
    assert draft(folder, live.get()).user == "Kosuke"        # what the open window's refresh sees


def test_live_config_keeps_the_last_good_config_and_the_open_paths(lab):
    live = LiveConfig(lab["cfg"])
    time.sleep(0.01)
    lab["cfg_path"].write_text("paths: [broken")
    assert live.get() is lab["cfg"]  # a file caught mid-save doesn't take the watcher down
    d = dict(lab["cfg_dict"])
    d["paths"] = dict(d["paths"], inbox=str(lab["root"] / "elsewhere"))
    (lab["root"] / "elsewhere").mkdir()
    time.sleep(0.01)
    lab["cfg_path"].write_text(yaml.safe_dump(d))
    assert live.get().inbox == lab["cfg"].inbox  # the running watcher keeps watching the folder it opened


# ------------------------------------------------------------- review --


class Reviewer:
    """A resolver with a review window: records drafts, answers with a function of the draft."""

    def __init__(self, answer):
        self.answer, self.seen = answer, []

    def resolve(self, d):
        self.seen.append(("resolve", d))
        return self.answer(d)

    def review(self, d):
        self.seen.append(("review", d))
        return self.answer(d)


def _accept_as_read(d, control=""):
    a = Answer(user=d.user, method=d.method, date=d.date, allow_uneven=d.allow_uneven, files=d.files,
               control=control)
    return to_overrides(a, d)


@pytest.fixture
def ledger(lab):
    led = Ledger(lab["cfg"].database)
    yield led
    led.close()


def test_every_clean_drop_is_reviewed_before_filing(lab, ledger):
    (lab["general"] / "Kosuke").mkdir()
    folder = make_drop(lab["inbox"], "20260927_Kosuke_DIA_pulldown", KC_RAWS)
    rv = Reviewer(_accept_as_read)
    assert intake(folder, lab["cfg"], ledger, rv) == IntakeResult.QUEUED
    (kind, d), = rv.seen
    assert kind == "review" and d.review and d.user == "Kosuke" and d.method == "DIA"
    assert {f.experiment for f in d.files} == {"KC_DIA_DMSO", "KC_DIA_Compound"}
    assert d.condition_codes == DEFAULT_CONDITION_CODES and "DMSO" in d.control_keywords
    filed = lab["general"] / "Kosuke" / "20260927_Kosuke_DIA_pulldown"
    assert load_overrides(filed).resolved_by == "gui"  # the person's confirmation travels with the data


def test_skipping_the_review_leaves_the_drop_in_the_inbox_with_a_note(lab, ledger):
    folder = make_drop(lab["inbox"], "20260927_EJQ_DIA_x", ["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"])
    assert intake(folder, lab["cfg"], ledger, Reviewer(lambda d: None)) == IntakeResult.REJECTED
    assert folder.is_dir()
    note = folder.parent / (folder.name + ".REJECTED.txt")
    assert "review window" in note.read_text(encoding="utf-8")


def test_a_chosen_control_is_saved_and_keeps_saved_comparisons(lab, ledger):
    # "Water" has no control keyword, so the analysis alone would pick "Drug" (alphabetically first)
    folder = make_drop(lab["inbox"], "20260927_EJQ_DIA_y", ["Water_1.raw", "Water_2.raw", "Drug_1.raw", "Drug_2.raw"])
    save_overrides(folder, load_overrides(folder).__class__(analysis={"log2fc": 0.5}))
    rv = Reviewer(lambda d: _accept_as_read(d, control="Water"))
    assert intake(folder, lab["cfg"], ledger, rv) == IntakeResult.QUEUED
    assert guess_control(["Water", "Drug"], rv.seen[0][1].control_keywords) == "Drug"
    an = load_overrides(lab["general"] / "EJQ" / "20260927_EJQ_DIA_y").analysis
    assert an == {"log2fc": 0.5, "control": "Water"}


def test_accepting_the_automatic_control_pins_nothing(lab):
    folder = make_drop(lab["inbox"], "20260927_EJQ_DIA_z", KC_RAWS)
    d = draft(folder, lab["cfg"], review=True)
    assert _accept_as_read(d, control="KC_DIA_DMSO").analysis == {}
    assert _accept_as_read(d, control="KC_DIA_Compound").analysis == {"control": "KC_DIA_Compound"}


def test_answered_folders_and_old_resolvers_are_not_asked_again(lab, ledger):
    # a folder a person already answered for (experiment.yaml resolved_by: gui) is filed without asking twice
    folder = make_drop(lab["inbox"], "20260927_EJQ_DIA_w", ["DMSO_1.raw", "DMSO_2.raw", "Drug_1.raw", "Drug_2.raw"])
    save_overrides(folder, load_overrides(folder).__class__(resolved_by="gui"))
    rv = Reviewer(_accept_as_read)
    assert intake(folder, lab["cfg"], ledger, rv) == IntakeResult.QUEUED and rv.seen == []

    class ProblemsOnly:  # a resolver without review(): unchanged behaviour
        def resolve(self, d):
            raise AssertionError("should not be asked about a clean drop")

    folder = make_drop(lab["inbox"], "20260927_EJQ_DIA_v", ["DMSO_1.raw", "Drug_1.raw"])
    assert intake(folder, lab["cfg"], ledger, ProblemsOnly()) == IntakeResult.QUEUED


def test_review_can_be_switched_off(lab, ledger):
    d = dict(lab["cfg_dict"], gui={"review_drops": False})
    lab["cfg_path"].write_text(yaml.safe_dump(d))
    folder = make_drop(lab["inbox"], "20260927_EJQ_DIA_u", ["DMSO_1.raw", "Drug_1.raw"])
    rv = Reviewer(_accept_as_read)
    assert intake(folder, load(lab["cfg_path"]), ledger, rv) == IntakeResult.QUEUED and rv.seen == []


# ------------------------------------------------------ what the window shows --


def _files(spec):
    return [DraftFile(f"{e}_{r}.raw", e, str(r), str(fr) if fr else "") for e, r, fr in spec]


def test_summary_names_roles_replicates_and_fractions():
    files = _files([("KC_DMSO", r, None) for r in (1, 2, 3)] + [("KC_Compound", r, None) for r in (1, 2, 3)])
    ctl = guess_control(["KC_DMSO", "KC_Compound"], ["DMSO", "vehicle"])
    assert ctl == "KC_DMSO"
    lines = summarize(files, "DIA", ctl)
    files.reverse()  # whatever order the files come in, the control is listed first
    lines = summarize(files, "DIA", ctl)
    assert lines[0] == "KC_DMSO — CONTROL · 3 replicates (1–3)"
    assert lines[1] == "KC_Compound — treated · 3 replicates (1–3)"
    one = summarize(_files([("A", 1, None), ("B", 1, None), ("B", 2, None)]), "DIA", "A")
    assert "one replicate" in one[0]
    iso = summarize(_files([("S", r, f) for r in (1, 2) for f in (1, 2, 3)]), "isoDTB", "")
    assert iso == ["S — sample (heavy/light ratio vs 0) · 2 replicates (1–2) · fractions 1–3"]
    assert "only one condition" in summarize(_files([("A", 1, None), ("A", 2, None)]), "DIA", "A")[-1]


def test_control_must_be_a_condition():
    a = Answer(user="EJQ", method="DIA", date="", allow_uneven=False,
               files=_files([("A", 1, None), ("B", 1, None)]), control="C")
    assert "not one of the conditions" in validate(a, ["DIA"])


# ------------------------------------------------------------ real tkinter --

from tests.conftest import gui_tests  # noqa: E402

_ok, _why = gui_tests()


def _widgets(w):
    yield w
    for c in w.winfo_children():
        yield from _widgets(c)


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_review_window_shows_the_reading_and_saves_a_changed_control(lab):
    from ionomos.resolve import TkResolver
    from tests.test_resolve import _drive_dialog

    (lab["general"] / "Kosuke").mkdir()
    folder = make_drop(lab["inbox"], "20260927_Kosuke_DIA_pulldown", KC_RAWS)
    d = draft(folder, lab["cfg"], review=True)
    root = make_tk_root()
    root.withdraw()
    seen = {}

    def act(win):
        texts = [str(w.cget("text")) for w in _widgets(win) if w.winfo_class() == "TLabel"]
        seen["title"] = win.title()
        seen["summary"] = next((t for t in texts if "CONTROL" in t), "")
        seen["codes"] = next((t for t in texts if "Short codes" in t), "")
        combos = [w for w in _widgets(win) if w.winfo_class() == "TCombobox"]
        ctl = next(c for c in combos if "KC_DIA_DMSO" in c.cget("values"))
        seen["control"] = ctl.get()
        ctl.set("KC_DIA_Compound")
        ctl.event_generate("<<ComboboxSelected>>")
        win.event_generate("<Return>")

    assert not _drive_dialog(root, act)
    ov = TkResolver(root).review(d)
    root.destroy()
    assert seen["title"] == "ionomos — check before filing"
    assert seen["control"] == "KC_DIA_DMSO" and "KC_DIA_DMSO — CONTROL" in seen["summary"]
    assert "D1 = DMSO rep 1" in seen["codes"]
    assert ov is not None and ov.user == "Kosuke" and ov.analysis == {"control": "KC_DIA_Compound"}


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_open_window_picks_up_a_user_added_meanwhile(lab):
    """KC isn't known when the window opens; Kosuke + alias KC are added in the app; the window fills him in."""
    from ionomos.resolve import TkResolver
    from tests.test_resolve import _drive_dialog

    live = LiveConfig(lab["cfg"])
    folder = make_drop(lab["inbox"], "20260927_KC_DIA_pulldown", KC_RAWS)
    with pytest.raises(IntakeError) as e:
        plan(folder, live.get())
    d = draft(folder, live.get(), e.value)
    assert d.user == ""
    root = make_tk_root()
    root.withdraw()
    filled = []

    def act(win):
        _add_user_with_alias(lab, "Kosuke", "KC")
        user_cb = next(w for w in _widgets(win) if w.winfo_class() == "TCombobox")

        def wait_for_user():
            if not win.winfo_exists():
                return
            if user_cb.get() == "Kosuke":
                filled.append("Kosuke" in user_cb.cget("values"))
                # Press Accept directly: a synthetic <Return> only reaches the window while it holds keyboard
                # focus, and on a desktop in use it often doesn't, so the key was dropped and the test flaked.
                next(w for w in _widgets(win) if w.winfo_class() == "TButton"
                     and str(w.cget("text")).startswith("Accept")).invoke()
            else:
                win.after(100, wait_for_user)

        wait_for_user()

    hung = _drive_dialog(root, act)
    ov = TkResolver(root, refresh=lambda dd: draft(folder, live.get(), review=dd.review)).resolve(d)
    root.destroy()
    assert not hung  # checked after the window closed; before it opens the list is always empty
    assert filled == [True]
    assert ov is not None and ov.user == "Kosuke"
