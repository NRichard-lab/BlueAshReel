from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from conftest import TestContext
from fastapi import HTTPException
from sqlalchemy import delete
from test_household_catalog import create_viewer, login
from test_playback import CAPS, begin, playable

from app.models import MediaFile, SubtitleStream
from app.services.compatibility import Decision
from app.services.ffprobe import run_ffprobe
from app.services.process_supervisor import lock_file, owned_size, terminate, unlock_file
from app.services.process_supervisor import main as supervisor_main
from app.services.subtitles import retime_vtt
from app.services.transcoding import OWNER_MARKER, Conversion, PlaybackManager, ffmpeg_command, remove_owned


@pytest.fixture
def ffmpeg() -> str:
    executable = os.getenv("TEST_FFMPEG_PATH") or shutil.which("ffmpeg")
    if not executable:
        pytest.skip("FFmpeg not installed; native integration requires TEST_FFMPEG_PATH")
    return executable


def conversion_file(
    context: TestContext, csrf: str, executable: str, codec: str = "h264", audio: str = "aac", size: str = "320x240"
) -> tuple[str, str, dict]:
    lib, _item, fid, old_source = playable(context, csrf)
    source = old_source.with_suffix(".mkv")
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate=24",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-t",
        "6",
        "-c:v",
        "libx264" if codec == "h264" else codec,
        "-threads",
        "1",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        audio,
        "-ac",
        "2",
        "-g",
        "24",
        str(source),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=15)
    probe_executable = str(Path(executable).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe"))
    probe, _elapsed = run_ffprobe(probe_executable, source, 10)
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.relative_path = source.name
        file.container = probe.container
        file.size_bytes, file.modified_ns = source.stat().st_size, source.stat().st_mtime_ns
        file.duration_seconds = probe.duration_seconds
        file.bitrate = probe.bitrate
        db.execute(delete(SubtitleStream).where(SubtitleStream.media_file_id == fid))
        video = file.video_streams[0]
        video.codec = probe.video[0].codec
        for key in ("width", "height", "profile", "level", "pixel_format", "bit_depth"):
            setattr(video, key, probe.video[0].details[key])
        file.audio_streams[0].codec = probe.audio[0].codec
        db.commit()
    context.config.ffmpeg_path = executable
    return fid, str(source), lib


@pytest.mark.parametrize(
    ("video_codec", "audio_codec", "method"),
    [("h264", "aac", "remux"), ("mpeg4", "aac", "transcode"), ("h264", "ac3", "transcode")],
)
def test_local_hls_outputs_and_stop(
    owner_context: tuple[TestContext, str], ffmpeg: str, video_codec: str, audio_codec: str, method: str
) -> None:
    context, csrf = owner_context
    fid, source, _lib = conversion_file(context, csrf, ffmpeg, video_codec, audio_codec)
    manager: PlaybackManager = context.client.app.state.playback_manager
    with patch("socket.create_connection", side_effect=AssertionError("No outbound calls allowed")):
        session = begin(context, csrf, fid)
        assert session["decision"]["method"] == method
        manifest = context.client.get(session["url"])
        assert manifest.status_code == 200, manifest.text
        assert "#EXTM3U" in manifest.text and "http" not in manifest.text and source not in manifest.text
        segment_name = next(line for line in manifest.text.splitlines() if line and not line.startswith("#"))
        segment_url = session["url"].replace("index.m3u8", segment_name)
        assert context.client.get(segment_url).status_code == 200
        assert context.client.get(session["url"].replace("index.m3u8", "segment-000000.ts.tmp")).status_code == 404
        assert context.client.get("/api/v1/streams").json()["items"][0]["method"] == method
        job = manager.for_session(session["id"])
        process = job.process
        assert process.wait(timeout=15) == 0
        completed = context.client.get(session["url"]).text
        durations = [
            float(line.split(":")[1].rstrip(",")) for line in completed.splitlines() if line.startswith("#EXTINF:")
        ]
        assert "#EXT-X-ENDLIST" in completed and 5.8 <= sum(durations) <= 6.3
        assert (
            context.client.post(f"/api/v1/streams/{session['id']}/stop", headers={"X-CSRF-Token": csrf}).status_code
            == 204
        )
        assert process.poll() is not None
        assert not job.directory.exists()
        assert context.client.get(segment_url).status_code == 410
        assert manager.health()["temp_bytes"] == 0


def test_hls_cross_user_and_precise_offset(owner_context: tuple[TestContext, str], ffmpeg: str) -> None:
    context, csrf = owner_context
    fid, _source, lib = conversion_file(context, csrf, ffmpeg)
    create_viewer(context, csrf, [lib["id"]])
    session = begin(context, csrf, fid, position_seconds=2.5)
    assert session["decision"]["method"] == "transcode"
    assert session["video_offset"] == 2.5
    assert not session["decision"]["audio_copy"] and not session["decision"]["video_copy"]
    job = context.client.app.state.playback_manager.for_session(session["id"])
    assert job.process.wait(timeout=15) == 0
    manifest = context.client.get(session["url"]).text
    durations = [float(line.split(":")[1].rstrip(",")) for line in manifest.splitlines() if line.startswith("#EXTINF:")]
    assert 3.3 <= sum(durations) <= 3.8
    login(context, "viewer")
    assert context.client.get(session["url"]).status_code == 404
    assert context.client.get("/api/v1/streams").status_code == 403
    assert context.client.get("/api/v1/playback-health").status_code == 403


def test_failed_conversion_is_clean_and_releases_capacity(owner_context: tuple[TestContext, str], ffmpeg: str) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.container = "matroska"
        db.commit()
    context.config.ffmpeg_path = ffmpeg
    response = context.client.post(
        "/api/v1/playback/sessions", headers={"X-CSRF-Token": csrf}, json={"file_id": fid, "capabilities": CAPS}
    )
    assert response.status_code == 422
    assert str(context.media_root) not in response.text
    assert context.client.get("/api/v1/streams").json()["total"] == 0
    assert context.client.app.state.playback_manager.health()["conversions"] == 0


def test_cleanup_only_unlocked_owned_directories(tmp_path: Path) -> None:
    root = tmp_path / "playback"
    root.mkdir()
    other = root / "family-backup"
    other.mkdir()
    assert not remove_owned(root, other)
    candidate = root / ("representation-" + "a" * 32)
    candidate.mkdir()
    (candidate / ".owner").write_text(OWNER_MARKER)
    held = lock_file(candidate / ".lock")
    assert not remove_owned(root, candidate)
    unlock_file(held)
    assert remove_owned(root, candidate)
    assert other.exists()


def test_subtitles_retime_for_offset_stream() -> None:
    output = retime_vtt("WEBVTT\n\n00:01.000 --> 00:03.000\nfirst\n\n00:05.000 --> 00:08.000\nsecond\n", 2)
    assert "00:00:00.000 --> 00:00:01.000" in output
    assert "00:00:03.000 --> 00:00:06.000" in output
    assert "first" not in retime_vtt(output, 2)


def test_ffmpeg_command_is_local_bounded_and_explicit(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, source = playable(context, csrf)
    session = begin(context, csrf, fid)
    from app.models import PlaybackSession

    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        row = db.get(PlaybackSession, session["id"])
        assert file and row
        row.audio_index = 7
        command = ffmpeg_command(
            source,
            file,
            row,
            Decision(method="transcode", reason="test", output_height=240, bitrate_kbps=500),
            context.config.temp_dir,
            context.config,
        )
        assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
        assert "0:7" in command and "-map_metadata" in command
        assert "libx264" in command and "aac" in command and "-filter_threads" in command
        assert all("http:" not in value and "https:" not in value for value in command)


def test_supervisor_reaps_on_parent_crash(tmp_path: Path, ffmpeg: str) -> None:
    directory = tmp_path / ("representation-" + "b" * 32)
    directory.mkdir()
    (directory / ".owner").write_text(OWNER_MARKER)
    # The short parent is deliberately killed, without a graceful close(). The
    # inherited control pipe closes and the supervisor must release its lock.
    code = """
import json,subprocess,sys,time
from pathlib import Path
p=subprocess.Popen([sys.executable,'-m','app.services.process_supervisor'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL)
command=[sys.argv[1],'-hide_banner','-nostdin','-loglevel','error','-re','-f','lavfi','-i','testsrc2=size=128x128:rate=10','-t','60','-c:v','libx264','-threads','1','-f','mpegts',str(Path(sys.argv[2])/'output.ts')]
p.stdin.write((json.dumps({'directory':sys.argv[2],'command':command,'timeout':70,'max_bytes':16777216})+'\\n').encode());p.stdin.flush()
print(p.pid,flush=True)
time.sleep(90)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", code, ffmpeg, str(directory)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert parent.stdout and parent.stdout.readline().strip().isdigit()
        deadline = time.monotonic() + 10
        while not (directory / "output.ts").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (directory / "output.ts").exists()
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 10
        cleaned = False
        while time.monotonic() < deadline:
            if remove_owned(tmp_path, directory):
                cleaned = True
                break
            time.sleep(0.1)
        assert cleaned, "Supervisor did not confirm child termination and release ownership"
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        if parent.stdout:
            parent.stdout.close()


def test_termination_does_not_release_ownership_on_timeout() -> None:
    process = Mock()
    process.poll.side_effect = [None, None, 0]
    process.wait.side_effect = [
        subprocess.TimeoutExpired("local-child", 3),
        subprocess.TimeoutExpired("local-child", 5),
        0,
    ]
    with patch("app.services.process_supervisor.time.sleep"):
        terminate(process)
    assert process.terminate.call_count == 2
    assert process.kill.call_count == 1


def test_size_accounting_tolerates_atomic_rename(tmp_path: Path) -> None:
    entry = tmp_path / "segment-000000.ts.tmp"
    entry.write_bytes(b"local")
    with patch.object(Path, "lstat", side_effect=FileNotFoundError):
        assert owned_size(tmp_path) == 0


def test_unconfirmed_stop_remains_visible(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    session = begin(context, csrf, fid)
    manager = context.client.app.state.playback_manager
    with patch.object(manager, "stop", side_effect=HTTPException(503, "Stop not confirmed")):
        response = context.client.post(f"/api/v1/streams/{session['id']}/stop", headers={"X-CSRF-Token": csrf})
        assert response.status_code == 503
        row = context.client.get("/api/v1/streams").json()["items"][0]
        assert row["state"] == "stop_failed" and row["error"]
        assert context.client.get(session["url"]).status_code == 410


def test_locked_orphan_bytes_are_reported(owner_context: tuple[TestContext, str]) -> None:
    context, _csrf = owner_context
    manager = context.client.app.state.playback_manager
    directory = manager.root / ("representation-" + "c" * 32)
    directory.mkdir()
    (directory / ".owner").write_text(OWNER_MARKER)
    (directory / "segment-000000.ts").write_bytes(b"x" * 128)
    held = lock_file(directory / ".lock")
    try:
        manager.cleanup_orphans()
        assert directory.exists()
        assert manager.health()["temp_bytes"] >= 128
    finally:
        unlock_file(held)


def test_completed_outputs_reuse_until_last_reference_stops(
    owner_context: tuple[TestContext, str], ffmpeg: str
) -> None:
    context, csrf = owner_context
    fid, _source, _lib = conversion_file(context, csrf, ffmpeg)
    first = begin(context, csrf, fid)
    manager = context.client.app.state.playback_manager
    job = manager.for_session(first["id"])
    assert job.process.wait(timeout=15) == 0
    second = begin(context, csrf, fid)
    assert manager.for_session(second["id"]) is job
    context.client.post(f"/api/v1/streams/{first['id']}/stop", headers={"X-CSRF-Token": csrf})
    assert job.directory.exists()
    context.client.post(f"/api/v1/streams/{second['id']}/stop", headers={"X-CSRF-Token": csrf})
    assert not job.directory.exists()


def test_conversion_admission_and_cancel_running_process(owner_context: tuple[TestContext, str], ffmpeg: str) -> None:
    context, csrf = owner_context
    fid, _source, _lib = conversion_file(context, csrf, ffmpeg)
    context.config.transcode_max_processes = 1

    def realtime(*args, **kwargs):
        command = ffmpeg_command(*args, **kwargs)
        command.insert(command.index("-i"), "-re")
        return command

    with patch("app.services.transcoding.ffmpeg_command", side_effect=realtime):
        session = begin(context, csrf, fid)
    manager = context.client.app.state.playback_manager
    job = manager.for_session(session["id"])
    assert job.process.poll() is None
    started = time.perf_counter()
    assert context.client.get("/api/v1/browse/home").status_code == 200
    assert context.client.get("/api/v1/dashboard").status_code == 200
    print(f"Home plus administration during running FFmpeg: {(time.perf_counter() - started) * 1000:.1f} ms")
    rejected = context.client.post(
        "/api/v1/playback/sessions",
        headers={"X-CSRF-Token": csrf},
        json={"file_id": fid, "capabilities": CAPS, "position_seconds": 1},
    )
    assert rejected.status_code == 429
    stopped = context.client.post(f"/api/v1/streams/{session['id']}/stop", headers={"X-CSRF-Token": csrf})
    assert stopped.status_code == 204 and job.process.poll() is not None
    assert not job.directory.exists() and manager.health()["temp_bytes"] == 0


def test_hardware_probe_failure_is_software_and_cleanup(owner_context: tuple[TestContext, str]) -> None:
    context, _csrf = owner_context
    manager = context.client.app.state.playback_manager
    process = Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("probe", 12), 1] * 3
    process.poll.return_value = 1
    with patch.object(manager, "_launch", return_value=process):
        result = manager.detect_hardware()
    assert all(result[key] == "test encode unavailable" for key in ("qsv", "nvenc", "amf"))
    assert manager.encoder == "libx264" and not manager.hardware_testing
    assert not manager.auxiliary and not manager.pending and manager.health()["temp_bytes"] == 0


@pytest.mark.parametrize("checkpoint_failure", ["rate", "source"])
def test_end_reaps_even_when_final_checkpoint_is_rejected(
    owner_context: tuple[TestContext, str], ffmpeg: str, checkpoint_failure: str
) -> None:
    context, csrf = owner_context
    fid, source, _lib = conversion_file(context, csrf, ffmpeg)
    session = begin(context, csrf, fid)
    manager = context.client.app.state.playback_manager
    job = manager.for_session(session["id"])
    if checkpoint_failure == "source":
        Path(source).rename(Path(source).with_suffix(".missing"))
    with patch(
        "app.api.playback.playback_budget.check",
        side_effect=HTTPException(429, "Rate limit") if checkpoint_failure == "rate" else None,
    ):
        response = context.client.post(
            f"/api/v1/playback/{session['id']}/end",
            headers={"X-CSRF-Token": csrf},
            json={"position_seconds": 1, "sequence": 1, "reason": "exit", "playing": False},
        )
    assert response.status_code in (404, 410, 429)
    assert job.process.poll() is not None and not job.directory.exists()


def test_supervisor_checks_completed_output_quota(tmp_path: Path) -> None:
    launch = {"directory": str(tmp_path), "command": ["local-child"], "max_bytes": 1, "timeout": 5}
    stdin = Mock(buffer=BytesIO((json.dumps(launch) + "\n").encode()))
    process = Mock()
    process.poll.return_value = 0
    with (
        patch("app.services.process_supervisor.sys.stdin", stdin),
        patch("app.services.process_supervisor.threading.Thread"),
        patch("app.services.process_supervisor.subprocess.Popen", return_value=process),
        patch("app.services.process_supervisor.owned_size", return_value=100),
    ):
        assert supervisor_main() == 4


def test_storage_reserves_growth_of_running_conversions(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, source = playable(context, csrf)
    session = begin(context, csrf, fid)
    manager = context.client.app.state.playback_manager
    context.config.transcode_max_processes = 2
    context.config.transcode_max_storage_mb = 512
    process = Mock()
    process.poll.return_value = None
    directory = manager._directory()
    orphan = manager._directory()
    job = Conversion("unrelated", directory, process, "libx264", 0, {"reserved"})
    manager.jobs[directory.name] = job
    from app.models import PlaybackSession

    try:
        with (
            context.session_factory() as db,
            patch(
                "app.services.transcoding.owned_size", side_effect=lambda path: 200 * 1048576 if path == orphan else 0
            ),
        ):
            with pytest.raises(HTTPException) as failure:
                manager.create(
                    db.get(MediaFile, fid),
                    source,
                    db.get(PlaybackSession, session["id"]),
                    Decision(method="transcode", reason="test"),
                    0,
                )
            assert failure.value.status_code == 507
    finally:
        manager.jobs.pop(directory.name)
        manager.pending.discard(directory)
        manager.pending.discard(orphan)
        remove_owned(manager.root, directory)
        remove_owned(manager.root, orphan)


def test_unconfirmed_auxiliary_remains_visible_and_blocks_probes(owner_context: tuple[TestContext, str]) -> None:
    context, _csrf = owner_context
    manager = context.client.app.state.playback_manager
    process = Mock()
    process.stdin.closed = False
    process.stdin.close.side_effect = lambda: setattr(process.stdin, "closed", True)
    process.wait.side_effect = subprocess.TimeoutExpired("probe", 12)
    process.poll.return_value = None
    with patch.object(manager, "_launch", return_value=process):
        manager.detect_hardware()
    assert len(manager.auxiliary) == 1 and manager.health()["maintenance_error"]
    with pytest.raises(HTTPException) as failure:
        manager.detect_hardware()
    assert failure.value.status_code == 409
    process.poll.return_value = 0


def test_quality_reduction_outputs_h264_at_requested_height(
    owner_context: tuple[TestContext, str], ffmpeg: str
) -> None:
    context, csrf = owner_context
    fid, _source, _lib = conversion_file(context, csrf, ffmpeg, size="1280x720")
    session = begin(context, csrf, fid, quality="480p")
    assert session["decision"]["method"] == "transcode"
    assert session["decision"]["output_height"] == 480 and session["decision"]["bitrate_kbps"] == 1200
    job = context.client.app.state.playback_manager.for_session(session["id"])
    assert job.process.wait(timeout=15) == 0
    probe, _ = run_ffprobe(
        str(Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")),
        job.directory / "segment-000000.ts",
        10,
    )
    assert probe.video[0].codec == "h264" and probe.video[0].details["height"] == 480


def test_real_local_subtitle_extraction_is_supervised_and_private(
    owner_context: tuple[TestContext, str], ffmpeg: str
) -> None:
    context, csrf = owner_context
    fid, source, _lib = conversion_file(context, csrf, ffmpeg)
    captions = context.media_root / "generated.srt"
    captions.write_text("1\n00:00:01,000 --> 00:00:03,000\n<b>Local</b> cue.\n", encoding="utf-8")
    muxed = Path(source).with_name("captions.mkv")
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            source,
            "-i",
            str(captions),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-map",
            "1:s:0",
            "-c",
            "copy",
            str(muxed),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        file.relative_path = muxed.name
        file.size_bytes, file.modified_ns = muxed.stat().st_size, muxed.stat().st_mtime_ns
        db.add(SubtitleStream(media_file_id=fid, stream_index=2, codec="subrip", language="eng"))
        db.commit()
    session = begin(context, csrf, fid, subtitle_index=2)
    with patch("socket.create_connection", side_effect=AssertionError("No outbound subtitle calls")):
        response = context.client.get(f"/api/v1/playback/{session['id']}/subtitles/2.vtt")
    assert response.status_code == 200 and "Local cue." in response.text and "<b>" not in response.text
    assert "00:01.000 --> 00:03.000" in response.text
    assert str(context.media_root) not in response.text
    assert not context.client.app.state.playback_manager.auxiliary


def test_shutdown_rejects_pending_and_new_work(owner_context: tuple[TestContext, str]) -> None:
    context, _csrf = owner_context
    manager = context.client.app.state.playback_manager
    original = manager._directory

    def close_before_launch():
        directory = original()
        manager.stop_event.set()
        return directory

    with (
        patch.object(manager, "_directory", side_effect=close_before_launch),
        patch.object(manager, "_launch") as launch,
    ):
        with pytest.raises(HTTPException) as failure:
            manager.subtitle_output(["local-child", "pipe:1"])
        assert failure.value.status_code == 503
        launch.assert_not_called()
    assert not manager.auxiliary and not manager.pending
    with pytest.raises(HTTPException):
        manager.detect_hardware()


def test_maintenance_continues_after_one_stop_failure(owner_context: tuple[TestContext, str]) -> None:
    context, _csrf = owner_context
    manager = context.client.app.state.playback_manager
    manager.stop_event.set()
    manager.thread.join(timeout=5)
    process = Mock()
    process.poll.return_value = None
    manager.jobs = {str(n): Conversion(str(n), manager.root / str(n), process, "copy", 0, {str(n)}) for n in range(2)}
    try:
        with (
            patch.object(manager.stop_event, "wait", side_effect=[False, True]),
            patch.object(manager, "stop", side_effect=[HTTPException(503, "Not confirmed"), None]) as stop,
        ):
            manager._watch()
        assert [call.args[0] for call in stop.call_args_list] == ["0", "1"]
        assert manager.maintenance_error
    finally:
        manager.jobs.clear()
