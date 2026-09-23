"""Resolver: pure logic always; the real tkinter dialog only when a display exists."""
import threading

import pytest

from ionomos.intake import Draft, DraftFile, IntakeError, Kind, draft, plan
from ionomos.manifest import FileOverride
from ionomos.resolve import (
    Answer,
    guess_alias_token,
    gui_available,
    reparse,
    to_overrides,
    validate,
)
from tests.conftest import iso_raws, make_drop


def _draft(folder="XYZ99_isoDTB_run", files=None, kind=Kind.USER, method="isoDTB", user=""):
    files = files or [DraftFile("S_1_1.raw", "S", "1", "1"), DraftFile("S_1_2.raw", "S", "1", "2")]
    return Draft(folder=folder, problem="p", kind=kind, user=user, method=method, date="",
                 known_users=["EJQ", "Isaac"], known_methods=["isoDTB", "TMT", "DIA"], files=files)


def test_guess_alias_token():
    assert guess_alias_token(_draft("XYZ99_isoDTB_run")) == "XYZ"
    assert guess_alias_token(_draft("20260902-isoDTB_IJD05")) == "IJD"
    assert guess_alias_token(_draft("THB10ISODTB")) == ""       # method glued in; can't tell
    assert guess_alias_token(_draft("isoDTB_20260902")) == ""


def test_reparse_switches_method():
    files = [DraftFile("DMSO_1.raw"), DraftFile("Drug_2.raw")]
    out = reparse(files, "DIA")
    assert [(f.experiment, f.bioreplicate, f.fraction) for f in out] == [("DMSO", "1", ""), ("Drug", "2", "")]
    out = reparse([DraftFile("Sample.raw")], "isoDTB")
    assert out[0].error


@pytest.mark.parametrize(
    "patch, msg",
    [
        ({"user": ""}, "User is required"),
        ({"user": "a b"}, "letters, digits"),
        ({"method": "XXX"}, "Method must be"),
        ({"date": "2/9/26"}, "YYYY-MM-DD"),
        ({"files": [DraftFile("a.raw", "", "1", "")]}, "experiment is required"),
        ({"files": [DraftFile("a.raw", "A", "0", "")]}, "replicate must be"),
        ({"files": [DraftFile("a.raw", "A", "1", "x")]}, "fraction must be"),
        ({"files": [DraftFile("a.raw", "A", "1", "1"), DraftFile("b.raw", "A", "1", "1")]}, "duplicates"),
        ({"files": [DraftFile("a.raw", "A", "1", "1"), DraftFile("b.raw", "A", "2", "2")]}, "different fractions"),
    ],
)
def test_validate_errors(patch, msg):
    a = Answer(user="EJQ", method="isoDTB", date="", allow_uneven=False,
               files=[DraftFile("a.raw", "A", "1", "1"), DraftFile("b.raw", "A", "2", "1")])
    for k, v in patch.items():
        setattr(a, k, v)
    assert msg in validate(a, ["isoDTB", "TMT", "DIA"])


def test_validate_ok_and_uneven_flag():
    files = [DraftFile("a.raw", "A", "1", "1"), DraftFile("b.raw", "A", "2", "2")]
    assert validate(Answer("EJQ", "isoDTB", "2026-09-02", True, files), ["isoDTB"]) == ""


def test_to_overrides_only_records_differences():
    d = _draft(files=[DraftFile("S_1_1.raw", "S", "1", "1"), DraftFile("S_1_2.raw", "S", "1", "2")])
    a = Answer("Isaac", "isoDTB", "", False, files=[
        DraftFile("S_1_1.raw", "S", "1", "1"),          # same as parse -> not written
        DraftFile("S_1_2.raw", "S", "2", ""),           # changed -> written, fraction cleared (-1)
    ])
    ov = to_overrides(a, d)
    assert ov.user == "Isaac" and ov.method == "isoDTB"
    assert set(ov.files) == {"S_1_2.raw"}
    assert ov.files["S_1_2.raw"] == FileOverride(experiment="S", bioreplicate=2, fraction=-1)


# ------------------------------------------------------------- real tkinter --

_ok, _why = gui_available()


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_tk_dialog_accept_and_skip(lab):
    import tkinter as tk

    from ionomos.resolve import TkResolver

    d = make_drop(lab["inbox"], "XYZ99_isoDTB_run", iso_raws([1, 2], [1, 2]))
    with pytest.raises(IntakeError) as e:
        plan(d, lab["cfg"])
    dr = draft(d, lab["cfg"], e.value)

    root = tk.Tk()
    root.withdraw()
    remembered = []
    res = TkResolver(root, remember=lambda u, a: remembered.append((u, a)))

    # drive the dialog from the Tk event loop: set the user, press Return
    def drive():
        win = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        combos = [w for w in _all(win) if w.winfo_class() == "TCombobox"]
        combos[0].set("Isaac")
        win.event_generate("<Return>")

    root.after(150, drive)
    ov = res.resolve(dr)
    assert ov is not None and ov.user == "Isaac" and ov.method == "isoDTB"
    assert remembered == [("Isaac", "XYZ")]

    # second dialog: Escape -> None
    root.after(150, lambda: next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel)).event_generate("<Escape>"))
    assert res.resolve(dr) is None
    root.destroy()


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_tk_resolver_from_worker_thread(lab):
    import tkinter as tk

    from ionomos.resolve import TkResolver

    d = make_drop(lab["inbox"], "XYZ99_isoDTB_run", ["S_1_1.raw"])
    dr = draft(d, lab["cfg"], IntakeError("x", Kind.USER))
    root = tk.Tk()
    root.withdraw()
    res = TkResolver(root)
    res.start(every_ms=50)
    out = []

    def worker():
        out.append(res.resolve(dr))
        root.after(0, root.quit)

    def drive():
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        if not tops:
            root.after(50, drive)
            return
        combos = [w for w in _all(tops[0]) if w.winfo_class() == "TCombobox"]
        combos[0].set("EJQ")
        tops[0].event_generate("<Return>")

    threading.Thread(target=worker, daemon=True).start()
    root.after(100, drive)
    root.mainloop()
    root.destroy()
    assert out and out[0].user == "EJQ"


def _all(w):
    yield w
    for c in w.winfo_children():
        yield from _all(c)


@pytest.mark.skipif(not gui_available()[0], reason="no GUI")
def test_tk_variables_can_be_garbage_collected_on_a_worker_thread():
    """With plain tkinter variables this aborts the process on Windows (Tcl called from the wrong thread)."""
    import gc
    import threading
    import tkinter as tk

    from ionomos import tkutil

    root = tk.Tk()
    root.withdraw()
    holder = [[tkutil.StringVar(master=root, value="x"), tkutil.BooleanVar(master=root)] for _ in range(50)]
    for pair in holder:
        pair.append(pair)  # a reference cycle, like a closed dialog's callbacks

    def drop():
        holder.clear()
        gc.collect()

    t = threading.Thread(target=drop)
    t.start()
    t.join()
    root.destroy()


@pytest.mark.skipif(not _ok, reason=f"no GUI: {_why}")
def test_dialog_closes_when_folder_removed(lab):
    import tkinter as tk

    from ionomos.inbox import remove
    from ionomos.resolve import TkResolver

    folder = make_drop(lab['inbox'], 'bad', ['bad.raw'])
    root = tk.Tk()
    root.withdraw()
    root.after(100, lambda: remove(lab['inbox'], folder))
    assert TkResolver(root).resolve(draft(folder, lab['cfg'])) is None
    root.destroy()
