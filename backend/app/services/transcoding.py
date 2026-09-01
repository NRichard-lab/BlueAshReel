"""Bounded API-owned local conversions. No shell, external URLs, or public cache."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.dependencies import Principal
from app.models import MediaFile, PlaybackSession, UserSession, utcnow
from app.security import _as_utc
from app.services.compatibility import Decision
from app.services.playback import load_playback
from app.services.playback_lifecycle import retain_history
from app.services.process_supervisor import lock_file, owned_size, unlock_file

OWNER_MARKER = "bluereel-playback-v1\n"
OWNED_NAME = re.compile(r"representation-[0-9a-f]{32}$")
SEGMENT_NAME = re.compile(r"segment-\d{6}\.ts$")
ENCODERS = {"software": "libx264", "qsv": "h264_qsv", "nvenc": "h264_nvenc", "amf": "h264_amf"}


def no_links(path: Path) -> bool:
    return not any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in (path, *path.parents))


def remove_owned(root: Path, candidate: Path) -> bool:
    """Never delete arbitrary temp content or a directory with a live process lock."""
    if not OWNED_NAME.fullmatch(candidate.name) or not no_links(candidate):
        return False
    try:
        if candidate.resolve().parent != root.resolve():
            return False
        entries = list(candidate.iterdir())
        if any(not no_links(entry) or not entry.is_file() for entry in entries):
            return False
        if (candidate / ".owner").read_text(encoding="ascii") != OWNER_MARKER:
            return False
        handle = lock_file(candidate / ".lock")
    except (OSError, ValueError):
        return False
    # FFmpeg has exited before its supervisor releases this lock. This namespace
    # is private to the sole API process, so no new owner can acquire a stale job.
    unlock_file(handle)
    try:
        shutil.rmtree(candidate)
        return True
    except OSError:
        return False


def ffmpeg_command(
    source: Path,
    file: MediaFile,
    playback: PlaybackSession,
    decision: Decision,
    directory: Path,
    config: AppConfig,
    encoder: str = "libx264",
    offset: float = 0,
) -> list[str]:
    video = min(file.video_streams, key=lambda stream: stream.stream_index)
    command = [
        config.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        "-threads",
        str(config.transcode_threads),
        "-filter_threads",
        "1",
        "-filter_complex_threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        "mov,matroska,webm,avi,asf,mpegts,mpeg",
    ]
    if offset:
        command += ["-ss", f"{offset:.3f}"]
    command += ["-i", str(source), "-map", f"0:{video.stream_index}"]
    if playback.audio_index is not None:
        command += ["-map", f"0:{playback.audio_index}"]
    command += ["-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn"]
    if decision.video_copy:
        command += ["-c:v", "copy"]
    else:
        command += [
            "-c:v",
            encoder,
            "-threads",
            str(config.transcode_threads),
            "-pix_fmt",
            "yuv420p",
            "-vf",
            f"scale=-2:{decision.output_height}",
            "-b:v",
            f"{decision.bitrate_kbps}k",
            "-maxrate",
            f"{decision.bitrate_kbps}k",
            "-bufsize",
            f"{decision.bitrate_kbps * 2}k",
            "-force_key_frames",
            "expr:gte(t,n_forced*4)",
            "-bf",
            "0",
        ]
        if encoder == "libx264":
            command += ["-preset", "veryfast", "-profile:v", "main"]
    command += ["-c:a", "copy"] if decision.audio_copy else ["-c:a", "aac", "-ac", "2", "-b:a", "160k"]
    command += [
        "-max_muxing_queue_size",
        "1024",
        "-f",
        "hls",
        "-hls_time",
        "4",
        "-hls_list_size",
        "0",
        "-hls_playlist_type",
        "event",
        "-hls_flags",
        "temp_file",
        "-hls_segment_filename",
        str(directory / "segment-%06d.ts"),
        str(directory / "index.m3u8"),
    ]
    return command


@dataclass
class Conversion:
    key: str
    directory: Path
    process: subprocess.Popen[bytes]
    encoder: str
    offset: float
    sessions: set[str] = field(default_factory=set)
    metrics: dict[str, float] = field(default_factory=dict)
    created: float = field(default_factory=time.monotonic)


class PlaybackManager:
    def __init__(self, config: AppConfig, factory: sessionmaker[Session]) -> None:
        self.config, self.factory = config, factory
        self.root = config.temp_dir.absolute() / "playback"
        self.lock = threading.RLock()
        self.jobs: dict[str, Conversion] = {}
        self.pending: set[Path] = set()
        self.auxiliary: dict[Path, subprocess.Popen[bytes] | None] = {}
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.owner_lock: BinaryIO | None = None
        self.hardware: dict[str, str] = {key: "not tested" for key in ("qsv", "nvenc", "amf")}
        self.hardware["software"] = "available when FFmpeg supports libx264"
        self.encoder = "libx264"
        self.maintenance_error: str | None = None
        self.hardware_testing = False

    def start(self) -> None:
        if not no_links(self.root):
            raise RuntimeError("Playback temporary storage must not contain symlinks or junctions")
        self.root.mkdir(parents=True, exist_ok=True)
        self.owner_lock = lock_file(self.root / ".manager.lock")
        with self.factory() as db:
            db.execute(
                update(PlaybackSession)
                .where(PlaybackSession.state == "active")
                .values(state="expired", ended_at=utcnow(), was_playing=False)
            )
            retain_history(db, self.config)
            db.commit()
        self.cleanup_orphans()
        self.thread = threading.Thread(target=self._watch, daemon=True, name="local-playback")
        self.thread.start()

    def cleanup_orphans(self) -> int:
        live = {job.directory for job in self.jobs.values()} | self.pending | set(self.auxiliary)
        return sum(
            remove_owned(self.root, directory)
            for directory in self.root.iterdir()
            if directory not in live and directory.is_dir()
        )

    def _launch(self, directory: Path, command: list[str], timeout: float, max_bytes: int) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.services.process_supervisor"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert process.stdin
        try:
            process.stdin.write(
                (
                    json.dumps(
                        {"directory": str(directory), "command": command, "timeout": timeout, "max_bytes": max_bytes}
                    )
                    + "\n"
                ).encode()
            )
            process.stdin.flush()
        except Exception:
            process.stdin.close()
            process.wait(timeout=10)
            raise
        return process

    def _directory(self) -> Path:
        with self.lock:
            if self.stop_event.is_set():
                raise HTTPException(503, "Local playback is shutting down")
            directory = self.root / f"representation-{uuid.uuid4().hex}"
            directory.mkdir()
            (directory / ".owner").write_text(OWNER_MARKER, encoding="ascii")
            self.pending.add(directory)
            return directory

    def subtitle_output(self, command: list[str]) -> bytes:
        with self.lock:
            if len(self.auxiliary) >= 2 or self.hardware_testing:
                raise HTTPException(429, "Local subtitle conversion is busy or awaiting cleanup")
            directory = self._directory()
            # Reserve before launch so concurrent requests cannot over-admit.
            self.auxiliary[directory] = None
        process: subprocess.Popen[bytes] | None = None
        try:
            output = directory / "captions.vtt"
            with self.lock:
                if self.stop_event.is_set():
                    raise HTTPException(503, "Local playback is shutting down")
                process = self._launch(directory, [*command[:-1], str(output)], 15, 2 * 1048576)
                self.auxiliary[directory] = process
                self.pending.discard(directory)
            code = process.wait(timeout=20)
            if code != 0 or not output.is_file() or output.stat().st_size > 2 * 1048576:
                raise HTTPException(422, "Subtitle conversion failed or exceeded local safety limits")
            return output.read_bytes()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(422, "Local subtitle conversion could not complete") from exc
        finally:
            if process:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.maintenance_error = "Subtitle process termination is not confirmed"
                if process.poll() is not None and process.stdout:
                    process.stdout.close()
            with self.lock:
                self.pending.discard(directory)
                if process is None or process.poll() is not None:
                    self.auxiliary.pop(directory, None)
                remove_owned(self.root, directory)

    def total_temp_bytes(self) -> int:
        return sum(
            owned_size(path)
            for path in self.root.iterdir()
            if OWNED_NAME.fullmatch(path.name) and no_links(path) and path.is_dir()
        )

    def detect_hardware(self) -> dict[str, str]:
        # Explicit Owner operation. No guessed GPU support and no source media.
        with self.lock:
            if self.stop_event.is_set():
                raise HTTPException(503, "Local playback is shutting down")
            if self.jobs or self.hardware_testing or self.auxiliary:
                raise HTTPException(409, "Stop active conversions before testing hardware")
            self.hardware_testing = True
        try:
            for key in ("qsv", "nvenc", "amf"):
                directory = self._directory()
                process: subprocess.Popen[bytes] | None = None
                command = [
                    self.config.ffmpeg_path,
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=128x128:rate=10",
                    "-frames:v",
                    "3",
                    "-an",
                    "-c:v",
                    ENCODERS[key],
                    "-threads",
                    "1",
                    "-f",
                    "null",
                    "-",
                ]
                try:
                    with self.lock:
                        if self.stop_event.is_set():
                            raise HTTPException(503, "Local playback is shutting down")
                        process = self._launch(directory, command, 8, 1048576)
                        self.auxiliary[directory] = process
                        self.pending.discard(directory)
                    code = process.wait(timeout=12)
                    self.hardware[key] = "test encode passed" if code == 0 else "test encode unavailable"
                except (OSError, subprocess.TimeoutExpired):
                    self.hardware[key] = "test encode unavailable"
                finally:
                    if process:
                        if process.stdin and not process.stdin.closed:
                            process.stdin.close()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            self.maintenance_error = "Hardware probe termination is not confirmed"
                        if process.poll() is not None and process.stdout:
                            process.stdout.close()
                    with self.lock:
                        self.pending.discard(directory)
                        if process is None or process.poll() is not None:
                            self.auxiliary.pop(directory, None)
                        remove_owned(self.root, directory)
                if process is not None and process.poll() is None:
                    break  # Do not stack probes while termination is uncertain.
            requested = self.config.transcode_hardware
            self.encoder = ENCODERS[requested] if self.hardware.get(requested) == "test encode passed" else "libx264"
            return dict(self.hardware)
        finally:
            self.hardware_testing = False

    def create(
        self, file: MediaFile, source: Path, playback: PlaybackSession, decision: Decision, offset: float
    ) -> Conversion:
        parameters = {
            "file": file.id,
            "fingerprint": file.fingerprint,
            "decision": decision.model_dump(),
            "audio": playback.audio_index,
            "subtitle": playback.subtitle_index,
            "offset": offset,
            "encoder": self.encoder,
        }
        key = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if self.stop_event.is_set():
                raise HTTPException(503, "Local playback is shutting down")
            if self.hardware_testing:
                raise HTTPException(409, "Hardware self-test is running. Retry playback shortly.")
            for job in self.jobs.values():
                if job.key == key and job.process.poll() == 0 and (job.directory / "index.m3u8").exists():
                    job.sessions.add(playback.id)
                    return job
            # Completed outputs still consume one storage reservation until the
            # last referencing stream stops; active playback always has priority.
            if len(self.jobs) >= self.config.transcode_max_processes:
                raise HTTPException(429, "Local conversion capacity reached. Stop another converted stream first.")
            quota = self.config.transcode_max_storage_mb * 1048576 // self.config.transcode_max_processes
            job_directories = {job.directory for job in self.jobs.values()}
            # Charge each directory once. Subtracting separately sampled sizes
            # from a total races with FFmpeg publishing/growing segments.
            reserved_bytes = sum(max(quota, owned_size(directory)) for directory in job_directories) + sum(
                owned_size(directory)
                for directory in self.root.iterdir()
                if directory not in job_directories
                and OWNED_NAME.fullmatch(directory.name)
                and no_links(directory)
                and directory.is_dir()
            )
            if reserved_bytes + quota > self.config.transcode_max_storage_mb * 1048576:
                raise HTTPException(
                    507, "Retained local output consumes the temporary-storage budget; check cleanup health"
                )
            if shutil.disk_usage(self.root).free < min(quota, 256 * 1048576):
                raise HTTPException(507, "Insufficient free temporary storage for local playback")
            directory = self._directory()
            try:
                command = ffmpeg_command(source, file, playback, decision, directory, self.config, self.encoder, offset)
                process = self._launch(directory, command, max(60, playback.duration_seconds * 3), quota)
            except OSError as exc:
                self.pending.discard(directory)
                remove_owned(self.root, directory)
                raise HTTPException(503, "Local FFmpeg could not start. Check System Health.") from exc
            job = Conversion(
                key, directory, process, "copy" if decision.video_copy else self.encoder, offset, {playback.id}
            )
            self.jobs[directory.name] = job
            self.pending.discard(directory)
            threading.Thread(target=self._metrics, args=(job,), daemon=True).start()
        deadline = time.monotonic() + self.config.transcode_startup_timeout_seconds
        while time.monotonic() < deadline:
            if (directory / "index.m3u8").is_file() and any(directory.glob("segment-*.ts")):
                return job
            code = process.poll()
            if code is not None:
                self.stop(playback.id)
                if not decision.video_copy and self.encoder != "libx264":
                    self.encoder = "libx264"
                    return self.create(file, source, playback, decision, offset)
                raise HTTPException(
                    422, "Local conversion failed. Rescan the file, check FFmpeg support, or choose another file."
                )
            time.sleep(0.05)
        self.stop(playback.id)
        raise HTTPException(
            504, "Local conversion startup timed out. Try a lower quality or check CPU/storage capacity."
        )

    @staticmethod
    def _metrics(job: Conversion) -> None:
        assert job.process.stdout
        output = job.process.stdout
        for raw in iter(lambda: output.readline(256), b""):
            try:
                payload = json.loads(raw)
                for key, value in payload.items():
                    if key in {"frame", "out_time_us", "speed", "total_size"}:
                        number = float(str(value).removesuffix("x"))
                        if 0 <= number < 1e15:
                            job.metrics[key] = number
                            if key == "out_time_us" and number > 0:
                                # HLS does not consistently report total_size.
                                # Use owned output bytes / produced media time,
                                # clearly exposed as an average, not link speed.
                                job.metrics["output_bitrate_kbps"] = owned_size(job.directory) * 8000 / number
            except (ValueError, TypeError):
                continue

    def for_session(self, session_id: str) -> Conversion:
        with self.lock:
            for job in self.jobs.values():
                if session_id in job.sessions:
                    return job
        raise HTTPException(410, "Local representation is no longer available. Start playback again.")

    def stop(self, session_id: str) -> None:
        with self.lock:
            job = next((job for job in self.jobs.values() if session_id in job.sessions), None)
            if job is None:
                return
            job.sessions.discard(session_id)
            if job.sessions:
                return
            if job.process.stdin and not job.process.stdin.closed:
                job.process.stdin.close()
            try:
                job.process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                # Never report stopped while a supervisor could still own FFmpeg.
                job.sessions.add(session_id)
                raise HTTPException(503, "Local process termination is not confirmed; check System Health") from exc
            if job.process.stdout:
                job.process.stdout.close()
            if not remove_owned(self.root, job.directory) and job.directory.exists():
                job.sessions.add(session_id)
                raise HTTPException(503, "Playback stopped, but temporary cleanup needs attention")
            self.jobs.pop(job.directory.name, None)

    def _watch(self) -> None:
        last_retention = 0.0
        while not self.stop_event.wait(1):
            try:
                with self.factory() as db:
                    active = db.scalars(select(PlaybackSession).where(PlaybackSession.state == "active")).all()
                    for row in active:
                        auth = db.get(UserSession, row.auth_session_id) if row.auth_session_id else None
                        valid = bool(
                            auth
                            and auth.revoked_at is None
                            and auth.user.is_active
                            and _as_utc(auth.expires_at) > utcnow()
                        )
                        if valid and auth:
                            try:
                                load_playback(db, Principal(auth.user, auth), row.id, self.config)
                            except HTTPException:
                                valid = False
                        if not valid:
                            row.state, row.ended_at, row.was_playing = "expired", utcnow(), False
                    if time.monotonic() - last_retention > 60:
                        retain_history(db, self.config)
                        last_retention = time.monotonic()
                    db.commit()
                with self.lock:
                    sessions = [(sid, job.process.poll()) for job in self.jobs.values() for sid in job.sessions]
                stop_failed = False
                for sid, code in sessions:
                    if code not in (None, 0):
                        with self.factory() as db:
                            failed_row = db.get(PlaybackSession, sid)
                            if failed_row:
                                failed_row.state, failed_row.ended_at = "failed", utcnow()
                                failed_row.error = (
                                    f"Local conversion failed or exceeded its processing/storage limit (status {code})"
                                )
                                db.commit()
                    with self.factory() as db:
                        state = db.scalar(select(PlaybackSession.state).where(PlaybackSession.id == sid))
                    if state != "active" or code not in (None, 0):
                        try:
                            self.stop(sid)
                        except HTTPException:
                            stop_failed = True
                            continue  # Still reap other sessions and auxiliary jobs.
                        with self.factory() as db:
                            db.execute(
                                update(PlaybackSession)
                                .where(
                                    PlaybackSession.id == sid, PlaybackSession.state.in_(["stopping", "stop_failed"])
                                )
                                .values(state="stopped", error=None)
                            )
                            db.commit()
                with self.lock:
                    for directory, process in list(self.auxiliary.items()):
                        if (
                            process is not None
                            and process.stdin
                            and process.stdin.closed
                            and process.poll() is not None
                        ):
                            if process.stdout:
                                process.stdout.close()
                            self.auxiliary.pop(directory)
                            remove_owned(self.root, directory)
                    self.cleanup_orphans()
                    unconfirmed = any(
                        process is not None and process.stdin and process.stdin.closed and process.poll() is None
                        for process in self.auxiliary.values()
                    )
                self.maintenance_error = (
                    "Local process termination is not confirmed" if unconfirmed or stop_failed else None
                )
            except Exception:
                # Fail closed on the next request; no paths/titles/arguments logged.
                self.maintenance_error = "Playback maintenance needs attention; check local storage and database health"

    def close(self) -> None:
        with self.lock:
            self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=12)
        failures = False
        try:
            with self.lock:
                sessions = [sid for job in self.jobs.values() for sid in job.sessions]
            for sid in sessions:
                try:
                    self.stop(sid)
                except HTTPException:
                    failures = True
        finally:
            # Always close every control pipe, including jobs whose stop timed
            # out. Each supervisor retains its own lock until its child exits.
            with self.lock:
                for job in self.jobs.values():
                    if job.process.stdin and not job.process.stdin.closed:
                        job.process.stdin.close()
                for process in list(self.auxiliary.values()):
                    if process is not None and process.stdin and not process.stdin.closed:
                        process.stdin.close()
                if self.owner_lock:
                    unlock_file(self.owner_lock)
                    self.owner_lock = None
        if failures:
            self.maintenance_error = "Some process stops remain unconfirmed; supervisors are still cleaning up"

    def health(self) -> dict[str, Any]:
        with self.lock:
            return {
                "hardware": dict(self.hardware),
                "configured": self.config.transcode_hardware,
                "selected_encoder": self.encoder,
                "active_encoder": ", ".join(
                    sorted({job.encoder for job in self.jobs.values() if job.process.poll() is None})
                )
                or "none",
                "conversions": len(self.jobs),
                "max_conversions": self.config.transcode_max_processes,
                "temp_bytes": self.total_temp_bytes(),
                "max_temp_bytes": self.config.transcode_max_storage_mb * 1048576,
                "maintenance_error": self.maintenance_error,
                "auxiliary_processes": len(self.auxiliary),
            }
