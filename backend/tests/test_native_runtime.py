from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import TestContext
from sqlalchemy import func, select

from app import native_runtime
from app.models import Library, LibraryPath, MediaFile, ScanLock
from app.services.ffprobe import ProbeResult
from app.services.jobs import claim_next_job, enqueue_scan
from app.services.scanner import ScanInterrupted, run_scan
from app.worker import requeue_interrupted


@pytest.fixture
def installation(tmp_path: Path) -> native_runtime.Installation:
    program = tmp_path / "program"
    data = tmp_path / "state"
    for path in (program / "runtime/ffmpeg", program / "config", data / "configuration", data / "state",
                 data / "data", data / "temp", data / "artwork", data / "database"):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (program / "runtime/ffmpeg" / name).write_bytes(b"test executable placeholder; never run")
    (program / "config/product.json").write_text('{"name":"BlueReel Development"}', encoding="utf-8")
    metadata = {
        "program_dir": str(program), "data_dir": str(data), "service_prefix": "BlueReelDevelopment",
        "port": 18080, "api_port": 18081, "web_port": 18082, "bind_address": "127.0.0.1",
    }
    (data / "configuration/installation.json").write_text(json.dumps(metadata), encoding="utf-8")
    environment = {
        "DEPLOYMENT_MODE": "native_windows", "NATIVE_PROGRAM_DIR": program, "NATIVE_DATA_DIR": data,
        "WINDOWS_SERVICE_PREFIX": "BlueReelDevelopment", "DATABASE_URL": f"sqlite:///{data / 'database/app.db'}",
        "APP_SECRET_KEY": "private-native-unit-test-secret-029478-and-more",
        "APP_DATA_DIR": data / "data", "TEMP_DIR": data / "temp", "ARTWORK_DIR": data / "artwork",
        "FFMPEG_PATH": program / "runtime/ffmpeg/ffmpeg.exe", "FFPROBE_PATH": program / "runtime/ffmpeg/ffprobe.exe",
        "MEDIA_ROOTS": "", "MEDIA_ROOT_DEFINITIONS": "",
    }
    (data / "configuration/.env").write_text(
        "\n".join(f"{name}={value}" for name, value in environment.items()), encoding="utf-8",
    )
    return native_runtime.load_installation(data)


def test_native_configuration_does_not_inherit_development_or_docker_environment(
    installation: native_runtime.Installation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///development-private.db")
    monkeypatch.setenv("APP_SECRET_KEY", "wrong-env-secret-not-from-native-installer")
    monkeypatch.setenv("OUTBOUND_INTEGRATIONS_ENABLED", "true")
    monkeypatch.setenv("MEDIA_ROOTS", "/media")
    config = native_runtime.load_configuration(installation)
    assert config.database_url == f"sqlite:///{installation.data_dir / 'database/app.db'}"
    assert config.app_secret_key == "private-native-unit-test-secret-029478-and-more"  # noqa: S105
    assert config.outbound_integrations_enabled is False
    assert config.media_roots == ""
    assert not (installation.data_dir / "database/app.db").exists()


def test_native_configuration_does_not_fall_back_when_private_env_is_missing(
    installation: native_runtime.Installation,
) -> None:
    (installation.data_dir / "configuration/.env").unlink()
    with pytest.raises(OSError):
        native_runtime.load_configuration(installation)


def test_native_record_rejects_overlapping_program_and_data(installation: native_runtime.Installation) -> None:
    source = installation.data_dir / "configuration/installation.json"
    metadata = json.loads(source.read_text(encoding="utf-8"))
    metadata["program_dir"] = str(installation.data_dir)
    source.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(native_runtime.NativeRuntimeError, match="separate"):
        native_runtime.load_installation(installation.data_dir)


def test_native_stop_and_heartbeat_are_instance_local_and_sanitized(installation: native_runtime.Installation) -> None:
    assert native_runtime.main(["--data-dir", str(installation.data_dir), "--role", "worker", "--stop"]) == 0
    assert installation.stop_file("worker").read_text(encoding="ascii") == "stop\n"
    assert not installation.stop_file("api").exists()
    native_runtime.heartbeat(installation, "worker", "ready")
    record = json.loads((installation.state_dir / "heartbeat-worker.json").read_text(encoding="utf-8"))
    assert set(record) == {"role", "status", "checked_at"}
    assert record["role"] == "worker" and record["status"] == "ready"
    assert "private-native" not in json.dumps(record)


def test_native_watchdog_has_bounded_start_and_failure_behavior(installation: native_runtime.Installation) -> None:
    stop = threading.Event()
    requested: list[bool] = []
    assert not native_runtime.watch_service(
        installation, "api", stop, lambda: False, lambda: requested.append(True), startup_seconds=0, interval=0,
    )
    assert stop.is_set() and requested == [True]
    checks = iter((True, False, False, False))
    stop.clear()
    assert not native_runtime.watch_service(
        installation, "api", stop, lambda: next(checks), lambda: requested.append(True), interval=0,
    )
    assert requested == [True, True]
    assert json.loads((installation.state_dir / "heartbeat-api.json").read_text())["status"] == "failed"


def test_native_watchdog_honors_stop_marker_before_health(installation: native_runtime.Installation) -> None:
    installation.stop_file("worker").write_text("stop\n", encoding="ascii")
    requested: list[bool] = []

    def should_not_run() -> bool:
        pytest.fail("A stopping service should not make another health probe")

    assert native_runtime.watch_service(
        installation, "worker", threading.Event(), should_not_run, lambda: requested.append(True), interval=0,
    )
    assert requested == [True]


def test_native_watchdog_does_not_overwrite_final_heartbeat_after_slow_probe(
    installation: native_runtime.Installation,
) -> None:
    stop = threading.Event()
    requested: list[bool] = []

    def finishing_probe() -> bool:
        # The API finished and wrote its final state while this probe was busy.
        stop.set()
        native_runtime.heartbeat(installation, "api", "stopped")
        return True

    assert native_runtime.watch_service(
        installation, "api", stop, finishing_probe, lambda: requested.append(True), interval=0,
    )
    assert not requested
    assert json.loads((installation.state_dir / "heartbeat-api.json").read_text())["status"] == "stopped"


def test_child_commands_use_bundled_tools_without_secret_arguments_or_inherited_node_options(
    installation: native_runtime.Installation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "must-not-go-to-child")
    monkeypatch.setenv("NODE_OPTIONS", "--require unwanted-development-hook.js")
    for role in ("web", "proxy"):
        command, environment = native_runtime.child_command(installation, role)
        assert str(installation.program_dir) in command[0]
        assert "must-not-go-to-child" not in " ".join(command)
        assert "APP_SECRET_KEY" not in environment and "NODE_OPTIONS" not in environment
        assert environment["BLUEREEL_STOP_FILE"] == str(installation.stop_file(role))
    command, environment = native_runtime.child_command(installation, "web")
    assert command[1] == "--require"
    assert environment["HOST"] == "127.0.0.1" and environment["PORT"] == "18082"


def test_cooperative_worker_stop_requeues_checkpoint_without_consuming_retry(
    context: TestContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = context.media_root / "Native Scan"
    source.mkdir()
    for number in range(3):
        (source / f"fixture-{number}.mp4").write_bytes(b"synthetic fixture")
    with context.session_factory() as db:
        library = Library(name="Native Scan", library_type="movies")
        db.add(library)
        db.flush()
        db.add(LibraryPath(library_id=library.id, canonical_path=str(source)))
        db.commit()
        enqueue_scan(db, library.id, "full")
        db.commit()
        job = claim_next_job(db, context.config, "native-old-worker")
        assert job is not None and job.attempts == 1
        probe = ProbeResult(container="mp4", duration_seconds=1, bitrate=100, embedded_title=None,
                            video=(), audio=(), subtitles=())
        monkeypatch.setattr("app.services.scanner.run_ffprobe", lambda *_args: (probe, 1))
        count = 0

        def stop_after_one_committed_batch() -> bool:
            nonlocal count
            count += 1
            return count >= 4

        with pytest.raises(ScanInterrupted):
            run_scan(db, job, context.config, stop_requested=stop_after_one_committed_batch)
        assert db.scalar(select(func.count()).select_from(MediaFile)) == 2
        assert job.scan is not None and job.scan.checkpoint
        requeue_interrupted(db, job)
        assert job.status == "queued" and job.attempts == 0
        assert db.scalar(select(func.count()).select_from(ScanLock)) == 1
        replacement = claim_next_job(db, context.config, "native-replacement-worker")
        assert replacement is not None and replacement.id == job.id and replacement.attempts == 1
        run_scan(db, replacement, context.config)
        assert replacement.status == "succeeded"
        assert db.scalar(select(func.count()).select_from(MediaFile)) == 3
        assert db.scalar(select(func.count()).select_from(ScanLock)) == 0


@pytest.mark.skipif(os.name != "nt", reason="Exercises real Windows Job Object cleanup")
def test_windows_job_reaps_child_when_service_process_exits() -> None:
    import ctypes
    from ctypes import wintypes

    program = """
import os, subprocess, sys
from app.native_runtime import protect_child_processes
protect_child_processes()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)
print(child.pid, flush=True)
sys.stdin.buffer.read(1)
os._exit(0)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", program], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = None
    try:
        assert process.stdout and process.stdin
        line = process.stdout.readline()
        assert line, "The isolated job-protection subprocess did not start"
        handle = kernel.OpenProcess(0x100000, False, int(line))  # SYNCHRONIZE only
        assert handle
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if handle:
            kernel.CloseHandle(handle)
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()
