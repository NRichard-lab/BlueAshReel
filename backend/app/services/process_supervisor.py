"""Private subprocess entry point: parent-pipe EOF stops and reaps local FFmpeg.

No API accepts commands for this helper. Its parent constructs an argument array.
The helper owns the directory lock until the child has actually exited.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO


def lock_file(path: Path) -> BinaryIO:
    handle = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        return handle
    except OSError:
        handle.close()
        raise


def unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def owned_size(directory: Path) -> int:
    total = 0
    try:
        for entry in directory.iterdir():
            try:
                info = entry.lstat()
                if stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                    total += info.st_size
            except FileNotFoundError:
                pass  # Atomic HLS publication can rename .tmp files mid-scan.
    except FileNotFoundError:
        pass
    return total


def terminate(process: subprocess.Popen[bytes]) -> None:
    while process.poll() is None:
        try:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            # Keep ownership and retry; never release the lock while the child
            # might still be alive, even if the parent reports a stop timeout.
            time.sleep(0.25)


def main() -> int:
    line = sys.stdin.buffer.readline(65537)
    if len(line) > 65536 or not line.endswith(b"\n"):
        return 2
    launch = json.loads(line)
    directory = Path(launch["directory"])
    command = launch["command"]
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        return 2
    owner_lock = lock_file(directory / ".lock")
    stop = threading.Event()

    def parent_watch() -> None:
        sys.stdin.buffer.read(1)  # EOF or an explicit stop byte, never credentials.
        stop.set()

    threading.Thread(target=parent_watch, daemon=True).start()
    child: subprocess.Popen[bytes] | None = None
    try:
        if stop.is_set():
            return 0
        child = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def progress() -> None:
            assert child and child.stdout
            output = child.stdout
            for raw in iter(lambda: output.readline(1024), b""):
                key, _, value = raw.decode("ascii", errors="ignore").strip().partition("=")
                if key in {"frame", "out_time_us", "speed", "total_size", "progress"}:
                    try:
                        print(json.dumps({key: value[:40]}), flush=True)
                    except (BrokenPipeError, OSError):
                        stop.set()
                        return

        reader = threading.Thread(target=progress, daemon=True)
        reader.start()
        started = time.monotonic()
        result = 0
        while child.poll() is None:
            if stop.wait(0.25):
                break
            if time.monotonic() - started > float(launch["timeout"]):
                result = 3
                break
            if owned_size(directory) > int(launch["max_bytes"]):
                result = 4
                break
        natural = child.poll()
        terminate(child)
        reader.join(timeout=2)
        if owned_size(directory) > int(launch["max_bytes"]):
            result = 4
        if natural not in (None, 0):
            result = 5
        return result
    finally:
        if child is not None:
            terminate(child)
            if child.stdout:
                child.stdout.close()
        unlock_file(owner_lock)


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        # Never expose command arguments, source names, or stderr to routine logs.
        exit_code = 6
    # main() explicitly reaps FFmpeg and releases ownership. The parent-pipe
    # watcher may still be blocked in a daemon read after natural completion;
    # avoid CPython finalization of that live buffered-I/O thread on Windows.
    os._exit(exit_code)
