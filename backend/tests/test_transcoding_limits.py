from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import TestContext
from fastapi import HTTPException
from pydantic import ValidationError
from test_playback import CAPS, begin, playable
from test_remote_media import call, setup_remote
from test_transcoding import conversion_file, ffmpeg  # noqa: F401
from test_transcoding_policy import update_policy

from app.models import MediaFile
from app.services.compatibility import PlaybackChoice, decide
from app.services.ffprobe import run_ffprobe
from app.services.transcoding_policy import TranscodingPolicy, default_policy


def test_legacy_defaults_and_strict_limits() -> None:
    policy = TranscodingPolicy.model_validate({"max_processes": 5})
    assert policy.max_video_transcodes == policy.max_audio_transcodes == 5
    assert policy.allow_software_fallback and policy.cpu_preset == "veryfast"
    for changes in (
        {"max_video_transcodes": True},
        {"max_audio_transcodes": 1.5},
        {"max_video_transcodes": 0},
        {"max_audio_transcodes": 9},
        {"allow_software_fallback": "false"},
        {"max_bitrate_kbps": 0},
        {"max_bitrate_kbps": True},
    ):
        with pytest.raises(ValidationError):
            TranscodingPolicy.model_validate(changes)


def test_remote_settings_merge_validate_and_preserve_private_storage(owner_context: tuple[TestContext, str]) -> None:
    media, auth, _ = setup_remote(owner_context)
    original = call(media, auth, "transcoding.get")
    assert original["settings_schema"] == 1 and "temp_directory" not in original
    saved = call(
        media,
        auth,
        "transcoding.update",
        max_bitrate_kbps=12500,
        max_height=None,
        max_video_transcodes=1,
        allow_software_fallback=False,
    )
    assert saved["max_bitrate_kbps"] == 12500 and saved["max_height"] is None
    assert saved["mode"] == original["mode"] and saved["max_audio_transcodes"] == original["max_audio_transcodes"]
    for changes in ({"max_audio_transcodes": 0}, {"temp_directory": "private"}, {"cpu_preset": "injected"}):
        with pytest.raises((ValueError, ValidationError)):
            call(media, auth, "transcoding.update", **changes)
    assert call(media, auth, "transcoding.get")["max_bitrate_kbps"] == 12500
    with pytest.raises(HTTPException):
        call(media, {**auth, "role": "viewer"}, "transcoding.update", max_video_transcodes=2)


@pytest.mark.parametrize(
    ("source", "ceiling", "expected"),
    [
        (2160, 1080, 1080),
        (1080, 720, 720),
        (720, 1080, 720),
        (1080, None, 1080),
    ],
)
def test_resolution_ceiling(
    owner_context: tuple[TestContext, str], source: int, ceiling: int | None, expected: int
) -> None:
    context, csrf = owner_context
    _, _, fid, _ = playable(context, csrf)
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.video_streams[0].height = source
        file.video_streams[0].codec = "mpeg4"
        policy = default_policy(context.config).model_copy(update={"max_height": ceiling, "allow_4k": True})
        result = decide(file, PlaybackChoice(file_id=fid, capabilities=CAPS), context.config, policy)
        assert result.output_height == expected and result.method == "transcode"


def test_fallback_switch_blocks_selection_and_recovery(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    manager = context.client.app.state.playback_manager
    policy = default_policy(context.config).model_copy(update={"allow_software_fallback": False})
    with pytest.raises(HTTPException, match="fallback is disabled"):
        manager.select_encoder(policy)
    assert manager.select_encoder(policy.model_copy(update={"mode": "software_only"}))[0] == "libx264"
    update_policy(context, csrf, allow_software_fallback=False, max_video_transcodes=1, max_audio_transcodes=3)
    saved = context.client.get("/api/v1/transcoding-policy").json()
    assert saved["max_video_transcodes"] == 1 and saved["max_audio_transcodes"] == 3
    assert saved["allow_software_fallback"] is False


@pytest.mark.parametrize("preset", ["ultrafast", "veryfast", "medium"])
def test_real_software_quality_scaling_and_cleanup(
    owner_context: tuple[TestContext, str],
    ffmpeg: str,  # noqa: F811
    preset: str,
) -> None:
    context, csrf = owner_context
    fid, source, _ = conversion_file(context, csrf, ffmpeg, "mpeg4", "ac3", "640x360")
    update_policy(context, csrf, mode="software_only", cpu_preset=preset, max_height=240, max_bitrate_kbps=750)
    session = begin(context, csrf, fid)
    manager = context.client.app.state.playback_manager
    job = manager.for_session(session["id"])
    assert job.process.wait(timeout=25) == 0
    from pathlib import Path

    probe, _ = run_ffprobe(str(Path(ffmpeg).with_name("ffprobe.exe")), next(job.directory.glob("segment-*.ts")), 10)
    assert probe.video[0].codec == "h264"
    assert probe.video[0].details["height"] == 240
    assert abs(probe.video[0].details["width"] / 240 - 640 / 360) < 0.01
    status = context.client.get("/api/v1/streams").json()["items"][0]
    assert status["playback_mode"] == "full_transcode"
    assert status["source_video_codec"] == "mpeg4" and status["source_audio_codec"] == "ac3"
    assert status["bitrate_kbps"] == 750 and status["encoder"] == "libx264"
    assert "RESOLUTION_LIMIT" in status["reason_codes"]
    assert (
        context.client.post(f"/api/v1/streams/{session['id']}/stop", headers={"X-CSRF-Token": csrf}).status_code == 204
    )
    assert not job.directory.exists() and not manager.jobs
    assert Path(source).is_file()


def test_independent_audio_video_admission(owner_context: tuple[TestContext, str], ffmpeg: str) -> None:  # noqa: F811
    context, csrf = owner_context
    context.config.playback_streams_per_user = 8
    fid, _, _ = conversion_file(context, csrf, ffmpeg, "h264", "ac3")
    update_policy(context, csrf, mode="software_only", max_processes=4, max_video_transcodes=1, max_audio_transcodes=1)
    first = begin(context, csrf, fid)
    manager = context.client.app.state.playback_manager
    assert manager.for_session(first["id"]).encoder == "copy"
    rejected_audio = context.client.post(
        "/api/v1/playback/sessions", headers={"X-CSRF-Token": csrf},
        json={"file_id": fid, "capabilities": CAPS, "quality": "480p"},
    )
    assert rejected_audio.status_code == 429 and "audio transcode limit" in rejected_audio.text
    assert len(manager.jobs) == 1
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.audio_streams[0].codec = "aac"
        file.video_streams[0].codec = "mpeg4"
        db.commit()
    second = begin(context, csrf, fid)
    assert manager.for_session(second["id"]).encoder == "libx264"
    # A distinct offset prevents completed-output reuse.
    response = context.client.post(
        "/api/v1/playback/sessions",
        headers={"X-CSRF-Token": csrf},
        json={"file_id": fid, "capabilities": CAPS, "position_seconds": 1},
    )
    assert response.status_code == 429 and "capacity reached" in response.text
    assert manager.for_session(first["id"]) and manager.for_session(second["id"])
    for session in (first, second):
        assert (
            context.client.post(f"/api/v1/streams/{session['id']}/stop", headers={"X-CSRF-Token": csrf}).status_code
            == 204
        )
    assert not manager.jobs


def test_real_capability_operation_never_guesses(owner_context: tuple[TestContext, str], ffmpeg: str) -> None:  # noqa: F811
    context, _ = owner_context
    context.config.ffmpeg_path = ffmpeg
    manager = context.client.app.state.playback_manager
    # Inventory absence is not proof an encoder cannot initialize.
    with patch("app.services.transcoding.detected_gpus", return_value=[]):
        manager.detect_hardware()
    for result in manager.hardware_tests.values():
        assert result["test_status"] in {"passed", "failed", "unavailable"}
        assert bool(result["available_codecs"]) == (result["test_status"] == "passed")
    assert not manager.auxiliary and not manager.pending and manager.total_temp_bytes() == 0
