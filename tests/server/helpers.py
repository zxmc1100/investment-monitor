"""Fakes for server tests: a recording broker and a disk-backed fake screen."""
import uuid

from monitor.screens.base import Screen


class Recorder:
    """Broker stand-in: records published events (thread-safe enough for list.append)."""

    def __init__(self):
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)


def make_screen(tmp_path, monkeypatch, *, sid="FAKE", fail=None, panels=None, params=None):
    """A two-tier screen whose code version is a real temp module (edit it to 'change code').

    fail: dict tier -> bool, consulted on every compute (mutate it between runs).
    Returns (screen, calls, dep_file); calls records (tier, ctx.force)."""
    name = f"fake_dep_{uuid.uuid4().hex[:8]}"
    dep = tmp_path / f"{name}.py"
    dep.write_text("X = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    calls: list[tuple[str, bool]] = []
    fail = fail if fail is not None else {}

    def compute(tier, ctx, param=None):
        calls.append((tier, ctx.force) if params is None else (tier, ctx.force, param))
        if fail.get(tier):
            raise RuntimeError(f"{tier} broke")
        part = {"tier": tier, "n": len(calls)}
        if params is not None:
            part["param"] = param
        return part

    def assemble(parts, meta, param=None):
        return {"screen": sid, "title": "Fake", "meta": meta, "help": [],
                "panels": panels if panels is not None else [], "parts": parts}

    if params is None:
        scr = Screen(sid, "Fake", 9, public=True, tiers=("quote", "daily"), deps=(name,),
                     compute=lambda t, c: compute(t, c), assemble=lambda p, m: assemble(p, m))
    else:
        scr = Screen(sid, "Fake", None, public=True, tiers=("quote", "daily"), deps=(name,),
                     compute=compute, assemble=assemble, params=lambda ctx: list(params))
    return scr, calls, dep
