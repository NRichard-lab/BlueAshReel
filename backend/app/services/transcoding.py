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
from app.services.hardware import (
    ENCODERS,
    advertised_encoders,
    binary_identity,
    detected_gpus,
    device_input_options,
    device_output_options,
    gpu_hint,
    output_pixel_format,
    video_filter,
)
from app.services.playback import load_playback
from app.services.playback_lifecycle import retain_history
from app.services.process_supervisor import lock_file, owned_size, unlock_file
from app.services.transcoding_policy import (
    TranscodingPolicy,
    read_policy,
    safe_scratch_path,
    session_policy,
    validate_temp_directory,
)

OWNER_MARKER = "bluereel-playback-v1\n"
OWNED_NAME = re.compile(r"representation-[0-9a-f]{32}$")
SEGMENT_NAME = re.compile(r"segment-\d{6}\.ts$")
HARDWARE_RECHECK_SECONDS = 6 * 60 * 60


def no_links(path: Path) -> bool:
    return safe_scratch_path(path)


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
    policy: TranscodingPolicy | None = None,
) -> list[str]:
    policy = policy or session_policy(playback, config)
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
    if not decision.video_copy:
        command += device_input_options(encoder, policy.hardware_device)
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
            output_pixel_format(encoder, policy.hardware_device),
            "-vf",
            video_filter(decision.output_height, encoder, policy.hardware_device),
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
            command += ["-preset", policy.cpu_preset, "-profile:v", "main"]
        command += device_output_options(encoder, policy.hardware_device)
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
    fallback: bool = False
    fallback_reason: str | None = None
    reservation_bytes: int = 0
    policy: TranscodingPolicy | None = None
    starting: bool = False
    audio_encoder: str = "copy"


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
        self.storage_locks: dict[Path, BinaryIO] = {}
        self.hardware: dict[str, str] = {key: "not tested" for key in ("qsv", "nvenc", "amf")}
        self.hardware["software"] = "available when FFmpeg supports libx264"
        self.encoder = "libx264"
        self.maintenance_error: str | None = None
        self.storage_maintenance_error: str | None = None
        self.hardware_testing = False
        self.hardware_checked_at = time.monotonic()
        self.hardware_tests: dict[str, dict[str, Any]] = {
            key: {
                "encoder": key,
                "gpu": None,
                "test_status": "not_tested",
                "last_test_at": None,
                "available_codecs": [],
                "device": "auto",
                "failure": "Not tested with this FFmpeg binary",
            }
            for key in ("qsv", "nvenc", "amf")
        }
        self.tested_binary: tuple[str, int, int] | None = None
        self.tested_device = "auto"
        self.gpus: list[str] = []
        self.tested_ffmpeg_sha256: str | None = None

    def start(self) -> None:
        if not no_links(self.root / ".manager.lock"):
            raise RuntimeError("Playback temporary storage must not contain symlinks or junctions")
        self.root.mkdir(parents=True, exist_ok=True)
        self.owner_lock = lock_file(self.root / ".manager.lock")
        self.storage_locks[self.root] = self.owner_lock
        with self.factory() as db:
            policy = read_policy(db, self.config)
            previous = list(db.scalars(select(PlaybackSession).where(PlaybackSession.state == "active").limit(32)))
            previous_policies = [session_policy(row, self.config) for row in previous if row.method != "direct"]
        if Path(policy.temp_directory).absolute() / "playback" != self.root:
            try:
                self.storage_root(policy)
            except (HTTPException, OSError):
                self.storage_maintenance_error = (
                    "Configured temporary storage is unavailable. Choose a writable local directory in Settings."
                )
        for previous_policy in previous_policies:
            try:
                self.storage_root(previous_policy)
            except (HTTPException, OSError):
                self.storage_maintenance_error = (
                    "An earlier temporary directory is unavailable; its cleanup needs attention"
                )
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
            remove_owned(root, directory)
            for root in self.storage_locks
            for directory in root.iterdir()
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

    def storage_root(self, policy: TranscodingPolicy) -> Path:
        root = Path(policy.temp_directory).absolute() / "playback"
        with self.lock:
            if root not in self.storage_locks:
                base = validate_temp_directory(policy.temp_directory, self.config)
                root = base / "playback"
                if not no_links(root / ".manager.lock"):
                    raise HTTPException(422, "Temporary playback directory cannot be a link or junction")
                root.mkdir(parents=True, exist_ok=True)
                try:
                    self.storage_locks[root] = lock_file(root / ".manager.lock")
                except OSError as exc:
                    raise HTTPException(409, "Temporary storage is already owned by another server") from exc
            return root

    def _directory(self, root: Path | None = None) -> Path:
        with self.lock:
            if self.stop_event.is_set():
                raise HTTPException(503, "Local playback is shutting down")
            directory = (root or self.root) / f"representation-{uuid.uuid4().hex}"
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
            for root in self.storage_locks
            for path in root.iterdir()
            if OWNED_NAME.fullmatch(path.name) and no_links(path) and path.is_dir()
        )

    def detect_hardware(self, policy: TranscodingPolicy | None = None) -> dict[str, str]:
        # Explicit Owner operation. No guessed GPU support and no source media.
        if policy is None:
            with self.factory() as db:
                policy = read_policy(db, self.config)
        if policy.mode == "software_only":
            raise HTTPException(409, "Software Only does not initialize hardware encoders")
        with self.lock:
            if self.stop_event.is_set():
                raise HTTPException(503, "Local playback is shutting down")
            if self.jobs or self.hardware_testing or self.auxiliary:
                raise HTTPException(409, "Stop active conversions before testing hardware")
            self.hardware_testing = True
        try:
            self.gpus = detected_gpus()
            advertised = advertised_encoders(self.config.ffmpeg_path)
            identity = binary_identity(self.config.ffmpeg_path)
            self.tested_binary = None
            self.tested_ffmpeg_sha256 = None
            if identity:
                try:
                    with Path(identity[0]).open("rb") as binary:
                        self.tested_ffmpeg_sha256 = hashlib.file_digest(binary, "sha256").hexdigest()
                except OSError:
                    identity = None
            self.tested_device = policy.hardware_device
            for key in ("qsv", "nvenc", "amf"):
                directory = self._directory()
                process: subprocess.Popen[bytes] | None = None
                command = [
                    self.config.ffmpeg_path,
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    *device_input_options(ENCODERS[key], policy.hardware_device),
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=128x128:rate=10",
                    "-frames:v",
                    "3",
                    "-an",
                    "-c:v",
                    ENCODERS[key],
                    "-vf",
                    video_filter(128, ENCODERS[key], policy.hardware_device),
                    "-pix_fmt",
                    output_pixel_format(ENCODERS[key], policy.hardware_device),
                    *device_output_options(ENCODERS[key], policy.hardware_device),
                    "-threads",
                    "1",
                    "-f",
                    "h264",
                    str(directory / "probe.h264"),
                ]
                try:
                    if ENCODERS[key] not in advertised or not gpu_hint(key, self.gpus, policy.hardware_device):
                        raise OSError("Hardware prerequisites unavailable")
                    with self.lock:
                        if self.stop_event.is_set():
                            raise HTTPException(503, "Local playback is shutting down")
                        process = self._launch(directory, command, 8, 1048576)
                        self.auxiliary[directory] = process
                        self.pending.discard(directory)
                    code = process.wait(timeout=12)
                    output = directory / "probe.h264"
                    passed = code == 0 and output.is_file() and 0 < output.stat().st_size <= 1048576
                    if passed:
                        if process.stdin and not process.stdin.closed:
                            process.stdin.close()
                        if process.stdout:
                            process.stdout.close()
                        # A successful encode exit alone does not prove usable output.
                        command = [self.config.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
                                   "-xerror", "-threads", "1", "-protocol_whitelist", "file,pipe",
                                   "-i", str(output), "-f", "null", "-"]
                        with self.lock:
                            process = self._launch(directory, command, 8, 1048576)
                            self.auxiliary[directory] = process
                        passed = process.wait(timeout=12) == 0
                    self.hardware[key] = "test encode passed" if passed else "test encode unavailable"
                except (OSError, subprocess.TimeoutExpired):
                    self.hardware[key] = "test encode unavailable"
                finally:
                    passed = self.hardware[key] == "test encode passed" and identity is not None
                    if not passed:
                        self.hardware[key] = "test encode unavailable"
                    self.hardware_tests[key] = {
                        "encoder": key,
                        "gpu": gpu_hint(key, self.gpus, policy.hardware_device),
                        "test_status": "passed" if passed else "failed",
                        "last_test_at": utcnow().isoformat(),
                        "available_codecs": ["h264"] if passed else [],
                        "device": policy.hardware_device,
                        "failure": None
                        if passed
                        else "The configured FFmpeg could not encode with this adapter. "
                        "Check the device choice, driver and FFmpeg hardware support, then retest.",
                    }
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
            if identity == binary_identity(self.config.ffmpeg_path):
                self.tested_binary = identity
            self.hardware_checked_at = time.monotonic()
            try:
                self.encoder, _fallback, _reason = self.select_encoder(policy)
            except HTTPException:
                self.encoder = "unavailable"
            return dict(self.hardware)
        finally:
            self.hardware_testing = False

    def select_encoder(self, policy: TranscodingPolicy) -> tuple[str, bool, str | None]:
        if policy.mode in ("software_only", "direct_only"):
            return "libx264", False, None
        candidates = [policy.preferred_hardware] if policy.preferred_hardware != "auto" else ["qsv", "nvenc", "amf"]
        verified = (
            self.tested_binary is not None and self.tested_binary == binary_identity(self.config.ffmpeg_path)
            and time.monotonic() - self.hardware_checked_at < HARDWARE_RECHECK_SECONDS
        )
        for key in candidates:
            if (
                verified
                and self.tested_device == policy.hardware_device
                and self.hardware.get(key) == "test encode passed"
            ):
                return ENCODERS[key], False, None
        reason = "No selected hardware encoder has passed a test with this FFmpeg binary and device; using software."
        if policy.mode == "hardware_required":
            raise HTTPException(422, "Hardware Required: the selected encoder/device is not verified. Retest hardware.")
        return "libx264", True, reason

    def create(
        self,
        file: MediaFile,
        source: Path,
        playback: PlaybackSession,
        decision: Decision,
        offset: float,
        *,
        software_fallback: str | None = None,
    ) -> Conversion:
        policy = session_policy(playback, self.config)
        if policy.mode == "direct_only" and decision.method == "transcode":
            raise HTTPException(422, "Direct Play and Remux Only does not permit video or audio transcoding")
        if decision.video_copy:
            encoder, fallback, fallback_reason = "libx264", False, None
            if decision.method == "transcode" and policy.mode == "hardware_required":
                raise HTTPException(422, "Hardware Required cannot perform audio-only software conversion")
        elif software_fallback:
            if policy.mode not in ("automatic", "hardware_preferred"):
                raise HTTPException(422, "This playback policy does not allow software fallback")
            encoder, fallback, fallback_reason = "libx264", True, software_fallback
        else:
            if (
                policy.mode in ("automatic", "hardware_preferred", "hardware_required")
                and not self.jobs
                and not self.auxiliary
                and not self.hardware_testing
                and (
                    self.tested_binary != binary_identity(self.config.ffmpeg_path)
                    or self.tested_device != policy.hardware_device
                    or time.monotonic() - self.hardware_checked_at >= HARDWARE_RECHECK_SECONDS
                )
            ):
                self.detect_hardware(policy)
            encoder, fallback, fallback_reason = self.select_encoder(policy)
        root = self.storage_root(policy)
        parameters = {
            "file": file.id,
            "fingerprint": file.fingerprint,
            "decision": decision.model_dump(),
            "audio": playback.audio_index,
            "subtitle": playback.subtitle_index,
            "offset": offset,
            "encoder": encoder,
            "settings": policy.model_dump(),
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
                    self.record_conversion(playback, job)
                    return job
            # Completed outputs still consume one storage reservation until the
            # last referencing stream stops; active playback always has priority.
            if len(self.jobs) >= policy.max_processes:
                raise HTTPException(429, "Local conversion capacity reached. Stop another converted stream first.")
            quota = policy.max_storage_mb * 1048576 // policy.max_processes
            job_directories = {job.directory for job in self.jobs.values()}
            # Charge each directory once. Subtracting separately sampled sizes
            # from a total races with FFmpeg publishing/growing segments.
            reserved_bytes = sum(
                max(job.reservation_bytes or quota, owned_size(job.directory)) for job in self.jobs.values()
            ) + sum(
                owned_size(directory)
                for storage in self.storage_locks
                for directory in storage.iterdir()
                if directory not in job_directories
                and OWNED_NAME.fullmatch(directory.name)
                and no_links(directory)
                and directory.is_dir()
            )
            if reserved_bytes + quota > policy.max_storage_mb * 1048576:
                raise HTTPException(
                    507, "Retained local output consumes the temporary-storage budget; check cleanup health"
                )
            if shutil.disk_usage(root).free < min(quota, 256 * 1048576):
                raise HTTPException(507, "Insufficient free temporary storage for local playback")
            directory = self._directory(root)
            try:
                command = ffmpeg_command(
                    source, file, playback, decision, directory, self.config, encoder, offset, policy
                )
                process = self._launch(directory, command, max(60, playback.duration_seconds * 3), quota)
            except OSError as exc:
                self.pending.discard(directory)
                remove_owned(root, directory)
                raise HTTPException(503, "Local FFmpeg could not start. Check System Health.") from exc
            job = Conversion(
                key,
                directory,
                process,
                "copy" if decision.video_copy else encoder,
                offset,
                {playback.id},
                fallback=fallback,
                fallback_reason=fallback_reason,
                reservation_bytes=quota,
                policy=policy,
                starting=True,
                audio_encoder="copy" if decision.audio_copy else "aac (CPU)",
            )
            self.jobs[directory.name] = job
            self.pending.discard(directory)
            self.record_conversion(playback, job)
            threading.Thread(target=self._metrics, args=(job,), daemon=True).start()
        deadline = time.monotonic() + self.config.transcode_startup_timeout_seconds
        while time.monotonic() < deadline:
            if (
                (directory / "index.m3u8").is_file()
                and any(directory.glob("segment-*.ts"))
                and process.poll() in (None, 0)
            ):
                job.starting = False
                self.record_conversion(playback, job)
                return job
            code = process.poll()
            if code is not None:
                self.stop(playback.id)
                if not decision.video_copy and encoder != "libx264":
                    self.invalidate_encoder(encoder, "Hardware video encoding failed during startup. Retest hardware.")
                    reason = "Hardware video encoding failed during startup. Software fallback is active."
                    if policy.mode in ("automatic", "hardware_preferred"):
                        return self.create(file, source, playback, decision, offset, software_fallback=reason)
                    raise HTTPException(
                        422, "Hardware Required: the selected encoder failed; software fallback is disabled"
                    )
                raise HTTPException(
                    422, "Local conversion failed. Rescan the file, check FFmpeg support, or choose another file."
                )
            time.sleep(0.05)
        self.stop(playback.id)
        if not decision.video_copy and encoder != "libx264":
            self.invalidate_encoder(encoder, "Hardware video encoding did not become ready in time. Retest hardware.")
            reason = "Hardware video encoding did not become ready in time. Software fallback is active."
            if policy.mode in ("automatic", "hardware_preferred"):
                return self.create(file, source, playback, decision, offset, software_fallback=reason)
            raise HTTPException(504, "Hardware Required: the selected encoder timed out; software fallback is disabled")
        raise HTTPException(
            504, "Local conversion startup timed out. Try a lower quality or check CPU/storage capacity."
        )

    def invalidate_encoder(self, encoder: str, reason: str) -> None:
        key = next((key for key, value in ENCODERS.items() if value == encoder), None)
        if key and key in self.hardware_tests:
            self.hardware[key] = "test encode unavailable"
            self.hardware_tests[key] = {
                **self.hardware_tests[key],
                "test_status": "failed",
                "failure": reason,
                "available_codecs": [],
            }

    def record_conversion(self, playback: PlaybackSession, job: Conversion) -> None:
        # The API commits this snapshot after process admission. Never take a
        # SQLite writer lock while holding the manager lock: recovery admission
        # obtains them in the opposite order.
        playback.decision = {
            **playback.decision,
            "active_encoder": job.encoder,
            "fallback": job.fallback,
            "fallback_reason": job.fallback_reason,
            "audio_encoder": job.audio_encoder,
        }
        playback.last_seen_at = utcnow()

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

    def can_recover(self, playback: PlaybackSession) -> bool:
        policy = session_policy(playback, self.config)
        if (
            policy.mode not in ("automatic", "hardware_preferred")
            or playback.decision.get("recovery_used")
            or playback.decision.get("active_encoder") not in {"h264_qsv", "h264_nvenc", "h264_amf"}
        ):
            return False
        if playback.state == "failed":
            return True
        if playback.state != "active":
            return False
        try:
            return self.for_session(playback.id).process.poll() not in (None, 0)
        except HTTPException:
            return False

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
            if not remove_owned(job.directory.parent, job.directory) and job.directory.exists():
                job.sessions.add(session_id)
                raise HTTPException(503, "Playback stopped, but temporary cleanup needs attention")
            self.jobs.pop(job.directory.name, None)

    def _watch(self) -> None:
        last_retention = 0.0
        while not self.stop_event.wait(1):
            try:
                with self.factory() as db:
                    active = db.scalars(select(PlaybackSession).where(PlaybackSession.state == "active")).all()
                    with self.lock:
                        preparing = {sid for job in self.jobs.values() if job.starting for sid in job.sessions}
                    for row in active:
                        if row.id in preparing:
                            continue
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
                    sessions = [
                        (sid, job.process.poll())
                        for job in self.jobs.values()
                        if not job.starting
                        for sid in job.sessions
                    ]
                stop_failed = False
                for sid, code in sessions:
                    if code not in (None, 0):
                        try:
                            failed_job = self.for_session(sid)
                            if failed_job.encoder not in ("copy", "libx264"):
                                self.invalidate_encoder(
                                    failed_job.encoder, "Hardware failed after playback began. Retest hardware."
                                )
                        except HTTPException:
                            failed_job = None
                        with self.factory() as db:
                            failed_row = db.get(PlaybackSession, sid)
                            if failed_row:
                                failed_row.state, failed_row.ended_at = "failed", utcnow()
                                failed_row.error = (
                                    f"Local conversion failed or exceeded its processing/storage limit (status {code})"
                                )
                                if failed_job and failed_job.encoder not in ("copy", "libx264"):
                                    failed_row.error = (
                                        "Hardware conversion failed during playback. Restart playback to recover."
                                    )
                                    if failed_job.policy and failed_job.policy.mode == "hardware_required":
                                        failed_row.error += " Hardware Required prevents software fallback."
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
                for handle in self.storage_locks.values():
                    unlock_file(handle)
                self.storage_locks.clear()
                self.owner_lock = None
        if failures:
            self.maintenance_error = "Some process stops remain unconfirmed; supervisors are still cleaning up"

    def health(self) -> dict[str, Any]:
        with self.factory() as db:
            policy = read_policy(db, self.config)
        try:
            selected, fallback, failure = self.select_encoder(policy)
        except HTTPException as exc:
            selected, fallback, failure = "unavailable", False, str(exc.detail)
        with self.lock:
            return {
                "hardware": dict(self.hardware),
                "configured": policy.preferred_hardware,
                "selected_encoder": selected,
                "selected_mode": policy.mode,
                "software_fallback": fallback,
                "failure": failure,
                "hardware_tests": list(self.hardware_tests.values()),
                "detected_gpus": list(self.gpus),
                "hardware_testing": self.hardware_testing,
                "tested_ffmpeg_sha256": self.tested_ffmpeg_sha256,
                "decoding": "software (hardware decoding is not enabled in this build)",
                "active_encoder": ", ".join(
                    sorted(
                        {
                            f"{job.encoder} / {job.audio_encoder} audio"
                            for job in self.jobs.values()
                            if job.process.poll() is None
                        }
                    )
                )
                or "none",
                "conversions": len(self.jobs),
                "max_conversions": policy.max_processes,
                "temp_bytes": self.total_temp_bytes(),
                "max_temp_bytes": policy.max_storage_mb * 1048576,
                "maintenance_error": self.maintenance_error or self.storage_maintenance_error,
                "auxiliary_processes": len(self.auxiliary),
            }
