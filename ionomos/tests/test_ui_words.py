"""Every error box in the app says what to do next (docs/UI.md S2.1, D46). No display needed: the Tk modules are
read as source and every messagebox.showerror call's literal text is checked against uinav.ACTION_WORDS."""
import ast
from pathlib import Path

import pytest

from ionomos import uinav

SRC = Path(uinav.__file__).parent
# The modules with message boxes; the test fails when a new module with a showerror is not listed here.
TK_MODULES = ("app.py", "analysis_tab.py", "notify_tab.py", "experiment_editor.py", "library_tab.py",
              "accuracy_page.py", "bundle_dialog.py", "popups.py", "resolve.py", "folder_check.py", "tmt_editor.py",
              "__main__.py")


def _literal(node) -> str:
    """Every string literal inside an expression (f-strings, concatenations, conditionals), joined."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_literal(v) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return _literal(node.value) if isinstance(node.value, (ast.IfExp, ast.JoinedStr)) else ""
    if isinstance(node, ast.BinOp):
        return _literal(node.left) + " " + _literal(node.right)
    if isinstance(node, ast.IfExp):
        return _literal(node.body) + " " + _literal(node.orelse)
    if isinstance(node, ast.Call):
        return " ".join(_literal(a) for a in node.args)
    return ""


def showerror_calls(path: Path) -> list[tuple[int, str]]:
    """(line, the literal text of the message) for every messagebox.showerror(...) in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "showerror"):
            continue
        msg = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "message"),
                                                            None)
        out.append((node.lineno, _literal(msg) if msg is not None else ""))
    return out


def test_every_module_with_an_error_box_is_checked():
    for p in sorted(SRC.rglob("*.py")):
        if ".showerror(" in p.read_text(encoding="utf-8"):
            rel = str(p.relative_to(SRC)).replace("\\", "/")
            assert rel in TK_MODULES, f"{rel} shows errors: add it to TK_MODULES"


@pytest.mark.parametrize("module", TK_MODULES)
def test_every_error_box_says_what_to_do(module):
    calls = showerror_calls(SRC / module)
    bad = [(line, text) for line, text in calls if not uinav.says_what_to_do(text)]
    assert not bad, f"{module}: these error boxes say what happened but not what to do: {bad}"


@pytest.mark.parametrize("text, ok", [
    ("Could not write config.yaml", False),
    ("Could not write config.yaml.\n\n→ Pick a folder you can write to, then Save again.", True),
    ("No spaces in user folder names (FragPipe paths). Type the name without a space.", True),
    ("", False),
])
def test_says_what_to_do(text, ok):
    assert uinav.says_what_to_do(text) is ok
