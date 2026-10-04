"""Every text file the code (and the suite, which CI runs on Windows too) reads or writes names its encoding.
Without one, Python uses the system's: cp1252 on a German Windows, where a "·" or "€" written on a Mac
reads back as garbage or fails. Binary modes need none. User CSVs use utf-8-sig (Excel writes a BOM)."""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEXT_CALLS = {"read_text": 0, "write_text": 1}       # method -> index of a positional encoding
RUNNERS = {"run", "Popen", "check_output", "call", "check_call"}


def _const(node):
    return node.value if isinstance(node, ast.Constant) else None


def _kw(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _mode(call: ast.Call, index: int):
    """The mode argument (positional `index` or mode=): its constant value, "r" when absent, else None."""
    node = call.args[index] if len(call.args) > index else _kw(call, "mode")
    return "r" if node is None else _const(node)


def missing_encoding(source: str) -> list[int]:
    """Line numbers of text-mode I/O calls in `source` without an explicit encoding."""
    bad = []
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call) or _kw(n, "encoding") is not None:
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id == "open" or \
                isinstance(f, ast.Attribute) and f.attr == "open" and isinstance(f.value, ast.Name) and f.value.id == "io":
            if len(n.args) < 4 and "b" not in (_mode(n, 1) or ""):
                bad.append(n.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "fdopen":
            if "b" not in (_mode(n, 1) or ""):
                bad.append(n.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "open" and not (
                isinstance(f.value, ast.Name) and f.value.id in ("webbrowser", "os")):       # Path.open(mode)
            if "b" not in (_mode(n, 0) or ""):
                bad.append(n.lineno)
        elif isinstance(f, ast.Attribute) and f.attr in TEXT_CALLS:
            if len(n.args) <= TEXT_CALLS[f.attr]:
                bad.append(n.lineno)
        elif isinstance(f, ast.Attribute) and f.attr in RUNNERS and isinstance(f.value, ast.Name) \
                and f.value.id == "subprocess":
            if any(_const(_kw(n, k)) is True for k in ("text", "universal_newlines")):
                bad.append(n.lineno)
    return sorted(bad)


def test_the_scanner_knows_text_from_binary():
    src = '''
open(p)
open(p, "w")
open(p, "rb")
open(p, mode="ab")
open(p, encoding="utf-8")
os.fdopen(fd, "w")
os.fdopen(fd, "wb")
path.open()
path.open("rb")
path.read_text()
path.read_text(encoding="utf-8")
path.write_text(s)
path.write_text(s, "utf-8")
path.read_bytes()
webbrowser.open(url)
subprocess.run(cmd, text=True)
subprocess.run(cmd, text=True, encoding="utf-8")
subprocess.run(cmd, capture_output=True)
'''
    assert missing_encoding(src) == [2, 3, 7, 9, 11, 13, 17]


def test_every_text_io_call_names_its_encoding():
    bad = []
    for root in ("monitor", "tests"):
        for f in sorted((REPO / root).rglob("*.py")):
            for line in missing_encoding(f.read_text(encoding="utf-8")):
                bad.append(f"{f.relative_to(REPO).as_posix()}:{line}")
    assert not bad, "text I/O without encoding= (add encoding=\"utf-8\"):\n" + "\n".join(bad)
