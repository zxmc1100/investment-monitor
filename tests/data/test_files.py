"""monitor.data.files: atomic writers and the exclusive lock — portable (fcntl on POSIX, msvcrt on Windows).
A local add-on imports this module, so it must import on every OS."""
import errno
import os
import sys
import threading
import types

import pytest

from monitor.data import files


def test_imports_without_fcntl():
    """Importing the module must not need a POSIX-only module (Windows has no fcntl)."""
    import ast
    tree = ast.parse(open(files.__file__, encoding="utf-8").read())
    top = [a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names]
    top += [n.module for n in tree.body if isinstance(n, ast.ImportFrom)]
    assert "fcntl" not in top and "msvcrt" not in top


@pytest.mark.skipif(os.name == "nt", reason="the POSIX branch")
def test_locked_excludes_another_holder_until_released(tmp_path):
    target = tmp_path / "log.csv"
    order, inside = [], threading.Event()

    def other():
        with files.locked(target):
            order.append("other")

    with files.locked(target):
        t = threading.Thread(target=other)
        t.start()
        t.join(0.3)
        assert t.is_alive() and order == []            # blocked while we hold it
        order.append("first")
    t.join(5)
    assert order == ["first", "other"]
    assert (tmp_path / "log.csv.lock").exists()


class FakeMsvcrt(types.ModuleType):
    """msvcrt.locking as Windows documents it: LK_LOCK retries for ~10 s, then raises OSError(EDEADLOCK)."""
    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self, busy: int = 0):
        super().__init__("msvcrt")
        self.calls, self.busy = [], busy

    def locking(self, fd, mode, nbytes):
        self.calls.append((mode, nbytes, os.lseek(fd, 0, os.SEEK_CUR)))
        if mode == self.LK_LOCK and self.busy:
            self.busy -= 1
            raise OSError(getattr(errno, "EDEADLOCK", errno.EDEADLK), "Resource deadlock avoided")


class _NtOs:
    """`os` as files.py sees it on Windows: only the name differs."""
    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


def test_locked_on_windows_locks_one_byte_at_the_start_and_unlocks(tmp_path, monkeypatch):
    fake = FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(files, "os", _NtOs())
    target = tmp_path / "log.csv"
    (tmp_path / "log.csv.lock").write_bytes(b"older content")      # the lock region stays at byte 0
    with files.locked(target):
        assert fake.calls == [(fake.LK_LOCK, 1, 0)]
    assert fake.calls == [(fake.LK_LOCK, 1, 0), (fake.LK_UNLCK, 1, 0)]


def test_locked_on_windows_keeps_waiting_while_another_process_holds_it(tmp_path, monkeypatch):
    """LK_LOCK gives up after ~10 s with EDEADLOCK; the lock is blocking, so it tries again."""
    fake = FakeMsvcrt(busy=2)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(files, "os", _NtOs())
    with files.locked(tmp_path / "x"):
        pass
    assert [m for m, _, _ in fake.calls] == [fake.LK_LOCK] * 3 + [fake.LK_UNLCK]


def test_locked_on_windows_releases_when_the_body_raises(tmp_path, monkeypatch):
    fake = FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(files, "os", _NtOs())
    with pytest.raises(KeyError):
        with files.locked(tmp_path / "x"):
            raise KeyError("boom")
    assert fake.calls[-1][0] == fake.LK_UNLCK


def test_locked_on_windows_does_not_spin_on_other_errors(tmp_path, monkeypatch):
    fake = FakeMsvcrt()
    fake.locking = lambda fd, mode, n: (_ for _ in ()).throw(OSError(errno.EBADF, "bad file"))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(files, "os", _NtOs())
    with pytest.raises(OSError):
        with files.locked(tmp_path / "x"):
            pass


def test_write_text_atomic_writes_utf8_whatever_the_locale(tmp_path):
    p = files.write_text_atomic(tmp_path / "n.txt", "Société Générale · 3,95 €\n")
    assert p.read_bytes().decode("utf-8").replace("\r\n", "\n") == "Société Générale · 3,95 €\n"


def test_write_text_atomic_can_keep_line_ends_exactly(tmp_path, monkeypatch):
    """newline="\\n": the bytes written are the text's, on Windows too (where text mode writes \\r\\n)."""
    monkeypatch.setattr(files.os, "linesep", "\r\n")
    p = files.write_text_atomic(tmp_path / "t.csv", "a,b\n1,2\n", newline="\n")
    assert p.read_bytes() == b"a,b\n1,2\n"
