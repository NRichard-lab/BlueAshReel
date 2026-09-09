from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Timer
from types import SimpleNamespace

import pytest

from app import native_install
from app.remote.storage import state_write_lock
from app.remote.storage import write_json as remote_write_json
from app.services.paths import UnsafeMediaPath


def windows_error(number: int) -> PermissionError:
    error = PermissionError("Test Windows file operation failure")
    error.winerror = number
    return error


@pytest.fixture
def windows_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    clock = [0.0]
    monkeypatch.setattr(native_install, "os", SimpleNamespace(name="nt", fdopen=os.fdopen))
    monkeypatch.setattr(
        native_install,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)),
    )
    return clock


@pytest.mark.parametrize("error_number", [32, 33])
def test_guard_sharing_conflict_retries_without_losing_prior_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_clock: list[float], error_number: int,
) -> None:
    target = tmp_path / "status.json"
    target.write_text('{"old": true}', encoding="utf-8")
    original_guard = native_install.native_directory_guard
    attempts = 0

    @contextmanager
    def contested_guard(path: Path) -> Iterator[None]:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            assert json.loads(target.read_text()) == {"old": True}
            raise windows_error(error_number)
        with original_guard(path):
            yield

    monkeypatch.setattr(native_install, "native_directory_guard", contested_guard)
    native_install.write_json(target, {"complete": "\N{SNOWMAN}"})
    assert json.loads(target.read_text()) == {"complete": "\N{SNOWMAN}"}
    assert attempts == 3
    assert windows_clock[0] == pytest.approx(0.05)
    assert not list(tmp_path.glob(".bluereel-*"))


def test_persistent_guard_conflict_is_bounded_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_clock: list[float],
) -> None:
    target = tmp_path / "status.json"
    target.write_text('{"old": true}', encoding="utf-8")
    original_guard = native_install.native_directory_guard

    @contextmanager
    def unavailable_guard(path: Path) -> Iterator[None]:
        raise windows_error(32)
        yield

    monkeypatch.setattr(native_install, "native_directory_guard", unavailable_guard)
    with pytest.raises(PermissionError) as failure:
        native_install.write_json(target, {"new": True})
    assert failure.value.winerror == 32
    assert windows_clock[0] == pytest.approx(0.5)
    assert json.loads(target.read_text()) == {"old": True}
    assert not list(tmp_path.glob(".bluereel-*"))
    monkeypatch.setattr(native_install, "native_directory_guard", original_guard)
    native_install.write_json(target, {"recovered": True})
    assert json.loads(target.read_text()) == {"recovered": True}


@pytest.mark.parametrize(
    "failure", [windows_error(5), PermissionError("Access denied"), UnsafeMediaPath("Unsafe link")],
)
def test_nonsharing_guard_failures_are_immediate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_clock: list[float], failure: Exception,
) -> None:
    target = tmp_path / "status.json"
    target.write_text('{"old": true}', encoding="utf-8")
    attempts = 0

    @contextmanager
    def denied_guard(path: Path) -> Iterator[None]:
        nonlocal attempts
        attempts += 1
        raise failure
        yield

    monkeypatch.setattr(native_install, "native_directory_guard", denied_guard)
    with pytest.raises(type(failure)) as result:
        native_install.write_json(target, {"new": True})
    assert result.value is failure
    assert attempts == 1
    assert windows_clock[0] == 0
    assert json.loads(target.read_text()) == {"old": True}
    assert not list(tmp_path.glob(".bluereel-*"))


def test_replacement_denial_cleans_temporary_file_despite_transient_cleanup_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_clock: list[float],
) -> None:
    target = tmp_path / "status.json"
    target.write_text('{"old": true}', encoding="utf-8")
    original_unlink = Path.unlink
    removals = 0
    replacements = 0

    def denied_replace(candidate: Path, destination: Path) -> Path:
        nonlocal replacements
        replacements += 1
        raise windows_error(5)

    def contested_cleanup(candidate: Path, missing_ok: bool = False) -> None:
        nonlocal removals
        removals += 1
        if removals <= 2:
            raise windows_error(32)
        original_unlink(candidate, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "replace", denied_replace)
    monkeypatch.setattr(Path, "unlink", contested_cleanup)
    with pytest.raises(PermissionError) as result:
        native_install.write_json(target, {"new": True})
    assert result.value.winerror == 5
    assert replacements > 1
    assert removals == 3
    assert windows_clock[0] == pytest.approx(0.55)
    assert json.loads(target.read_text()) == {"old": True}
    assert not list(tmp_path.glob(".bluereel-*"))


def test_temporary_file_creation_conflict_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_clock: list[float],
) -> None:
    target = tmp_path / "status.json"
    original_mkstemp = native_install.tempfile.mkstemp
    attempts = 0

    def contested_create(*, prefix: str, dir: Path) -> tuple[int, str]:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise windows_error(32)
        return original_mkstemp(prefix=prefix, dir=dir)

    monkeypatch.setattr(native_install.tempfile, "mkstemp", contested_create)
    native_install.write_json(target, {"complete": True})
    assert attempts == 3
    assert windows_clock[0] == pytest.approx(0.05)
    assert json.loads(target.read_text()) == {"complete": True}
    assert not list(tmp_path.glob(".bluereel-*"))


def test_concurrent_writers_share_guard_ancestors_without_partial_or_orphaned_state(tmp_path: Path) -> None:
    start = Barrier(4)

    def writer(index: int) -> None:
        start.wait(timeout=10)
        write = native_install.write_json if index % 2 else remote_write_json
        for sequence in range(100):
            write(tmp_path / f"writer-{index}.json", {"writer": index, "sequence": sequence})

    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(writer, range(4)):
            assert result is None
    for index in range(4):
        assert json.loads((tmp_path / f"writer-{index}.json").read_text()) == {"writer": index, "sequence": 99}
    assert not list(tmp_path.glob(".bluereel-*"))
    assert not list(tmp_path.glob(".pending-*"))


@pytest.mark.skipif(os.name != "nt", reason="Exercises real interprocess Windows replacement coordination")
@pytest.mark.parametrize("shared_destination", [True, False])
def test_native_and_remote_processes_coordinate_state(tmp_path: Path, shared_destination: bool) -> None:
    script = """
import sys
from pathlib import Path
from app.native_install import write_json as native_write
from app.remote.storage import write_json as remote_write
target, number = Path(sys.argv[1]), int(sys.argv[2])
write = native_write if number % 2 else remote_write
print('ready', flush=True)
sys.stdin.buffer.read(1)
for sequence in range(200):
    write(target, {'writer': number, 'sequence': sequence, 'enabled': bool(number % 2), 'padding': 'x' * 4096})
"""
    target = tmp_path / "desired.json"
    children: list[subprocess.Popen[str]] = []
    try:
        for index in range(4):
            # Different casing must resolve to the same mutex on Windows.
            destination = target if shared_destination else tmp_path / f"writer-{index}.json"
            alias = str(destination).upper() if index % 2 else str(destination)
            child = subprocess.Popen(
                [sys.executable, "-c", script, alias, str(index)], text=True,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            children.append(child)
        for child in children:
            assert child.stdout and child.stdin
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            assert child.stdin
            child.stdin.write("x")
            child.stdin.flush()
        for child in children:
            _, error = child.communicate(timeout=30)
            assert child.returncode == 0, error
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream:
                    stream.close()
    for destination in [target] if shared_destination else [tmp_path / f"writer-{index}.json" for index in range(4)]:
        value = json.loads(destination.read_text())
        assert value["writer"] in range(4) and value["sequence"] == 199
        assert value["padding"] == "x" * 4096
    assert not list(tmp_path.glob(".bluereel-*"))
    assert not list(tmp_path.glob(".pending-*"))


@pytest.mark.skipif(os.name != "nt", reason="Exercises real Windows mutex timeout and abandoned-owner recovery")
def test_state_mutex_wait_is_bounded_and_abandoned_owner_recovers(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path
from app.remote.storage import state_write_lock
with state_write_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.buffer.read(1)
"""
    target = tmp_path / "desired.json"
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(target)], text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    timer: Timer | None = None
    try:
        assert child.stdout and child.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with pytest.raises(TimeoutError), state_write_lock(target):
            pytest.fail("A foreign process still owns this destination")
        assert 0.4 <= time.monotonic() - started < 2
        # A different state file can progress while this destination is busy.
        native_install.write_json(tmp_path / "other.json", {"ready": True})
        timer = Timer(0.1, child.kill)
        timer.start()
        with state_write_lock(target):
            native_install.write_json(target, {"recovered": True})
        assert child.wait(timeout=5) != 0
        assert json.loads(target.read_text()) == {"recovered": True}
        # Successful and abandoned acquisitions both release ownership.
        native_install.write_json(target, {"after_release": True})
    finally:
        if timer:
            timer.cancel()
            timer.join(timeout=5)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream:
                stream.close()


@pytest.mark.skipif(os.name != "nt", reason="Exercises actual MoveFileEx denial from a shared-delete reader")
@pytest.mark.parametrize("writer", [native_install.write_json, remote_write_json], ids=["native", "remote"])
@pytest.mark.parametrize("release_reader", [True, False], ids=["temporary-reader", "persistent-reader"])
def test_state_replacement_waits_for_reader_without_truncating_prior_state(
    tmp_path: Path, writer: Callable[[Path, dict[str, object]], None], release_reader: bool,
) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    target = tmp_path / "status.json"
    writer(target, {"original": True})
    # Same sharing flags as the .NET tray reader. MoveFileEx replacing an open
    # destination reports WinError 5 despite FILE_SHARE_DELETE on this reader.
    handle = kernel.CreateFileW(str(target), 0x80000000, 7, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    released = False
    prior_intact: list[bool] = []

    def release() -> None:
        nonlocal released
        prior_intact.append(json.loads(target.read_text()) == {"original": True})
        assert kernel.CloseHandle(handle)
        released = True

    timer = Timer(0.1, release) if release_reader else None
    try:
        started = time.monotonic()
        if timer:
            timer.start()
            writer(target, {"complete": "\N{SNOWMAN}"})
            timer.join(timeout=5)
            assert released and prior_intact == [True]
            assert 0.075 <= time.monotonic() - started < 2
            assert json.loads(target.read_text()) == {"complete": "\N{SNOWMAN}"}
        else:
            with pytest.raises(PermissionError) as failure:
                writer(target, {"complete": True})
            assert failure.value.winerror == 5
            assert 0.4 <= time.monotonic() - started < 2
            assert json.loads(target.read_text()) == {"original": True}
            release()
            writer(target, {"recovered": True})
            assert json.loads(target.read_text()) == {"recovered": True}
        assert not list(tmp_path.glob(".bluereel-*"))
        assert not list(tmp_path.glob(".pending-*"))
    finally:
        if timer:
            timer.join(timeout=5)
        if not released:
            kernel.CloseHandle(handle)


def test_remote_state_creation_access_denied_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def denied_creation(*_args: object, **_kwargs: object) -> tuple[int, str]:
        nonlocal attempts
        attempts += 1
        raise windows_error(5)

    monkeypatch.setattr("app.remote.storage.tempfile.mkstemp", denied_creation)
    with pytest.raises(PermissionError) as failure:
        remote_write_json(tmp_path / "status.json", {"new": True})
    assert failure.value.winerror == 5 and attempts == 1
    assert not list(tmp_path.iterdir())
