from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from test_playback import CAPS
from test_remote_media import call, setup_remote

from app.models import MediaFile
from app.services.compatibility import PlaybackChoice, decide
from app.services.transcoding import HARDWARE_RECHECK_SECONDS
from app.services.transcoding_policy import default_policy


def test_owner_remote_settings_persist_without_exposing_storage(owner_context):
    media, auth, _ = setup_remote(owner_context)
    for mode in ("software_only", "hardware_preferred", "automatic"):
        result = call(media, auth, "transcoding.update", mode=mode, preferred_hardware="amf")
        assert result["mode"] == mode and "temp_directory" not in result
        assert call(media, auth, "transcoding.get")["mode"] == mode
    with pytest.raises(ValueError):
        call(media, auth, "transcoding.update", mode="automatic", temp_directory="untrusted")
    with pytest.raises(HTTPException):
        call(media, {**auth, "role": "viewer"}, "transcoding.get")


def test_remote_status_and_stop_use_opaque_sessions_and_bounded_health(owner_context):
    media, auth, _ = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    stream = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    call(media, auth, "playback.progress", session_id=stream["id"], position_seconds=5,
         playing=True, sequence=1, telemetry={"buffer_seconds": 12, "dropped_frames": 2})
    result = call(media, auth, "streams.list")
    item = result["items"][0]
    assert item["id"] == stream["id"] and item["method_label"] == "Direct Play"
    assert item["client_health"]["buffer_seconds"] == 12
    assert "health" not in result and "path" not in item and "title" not in item
    call(media, auth, "streams.stop", session_id=item["id"])
    assert call(media, auth, "streams.list")["items"] == []


@pytest.mark.parametrize("output,decode_status", [(b"", 0), (b"not decodable", 1)])
def test_hardware_requires_nonempty_decodable_output(owner_context, output, decode_status):
    context, _ = owner_context
    manager = context.client.app.state.playback_manager
    process = Mock()
    process.poll.return_value = 0
    process.wait.return_value = 0

    def launch(directory: Path, command: list[str], _timeout: float, _quota: int):
        if "-c:v" in command:
            (directory / "probe.h264").write_bytes(output)
            process.wait.return_value = 0
        else:
            process.wait.return_value = decode_status
        return process

    with (
        patch.object(manager, "_launch", side_effect=launch),
        patch("app.services.transcoding.detected_gpus", return_value=["AMD test adapter"]),
        patch("app.services.transcoding.advertised_encoders", return_value={"h264_amf"}),
    ):
        manager.detect_hardware()
    assert manager.hardware["amf"] == "test encode unavailable"
    assert not manager.auxiliary and not manager.pending


def test_expired_hardware_is_ineligible_and_software_only_never_probes(owner_context):
    context, _ = owner_context
    manager = context.client.app.state.playback_manager
    identity = ("synthetic-binary", 1, 1)
    manager.tested_binary = identity
    manager.hardware["amf"] = "test encode passed"
    manager.hardware_checked_at = time.monotonic() - HARDWARE_RECHECK_SECONDS - 1
    policy = default_policy(context.config)
    with patch("app.services.transcoding.binary_identity", return_value=identity):
        assert manager.select_encoder(policy)[0] == "libx264"
    with patch.object(manager, "_launch") as launch:
        with pytest.raises(HTTPException):
            manager.detect_hardware(policy.model_copy(update={"mode": "software_only"}))
        launch.assert_not_called()


def test_forced_delivery_preserves_compatibility_and_safe_defaults(owner_context):
    media, auth, (_, _, file_id, _) = setup_remote(owner_context)
    with media.factory() as db:
        file = db.get(MediaFile, file_id)
        for delivery, method in [("auto", "direct"), ("remux", "remux"), ("transcode", "transcode")]:
            result = decide(file, PlaybackChoice(file_id=file_id, capabilities=CAPS, delivery=delivery), media.config)
            assert result.method == method
        file.video_streams[0].codec = "hevc"
        result = decide(file, PlaybackChoice(file_id=file_id, capabilities=CAPS, delivery="remux"), media.config)
        assert result.method == "unsupported"
