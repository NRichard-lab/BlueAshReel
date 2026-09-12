from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from conftest import TestContext
from fastapi import HTTPException
from test_household_catalog import create_viewer, login
from test_playback import CAPS, begin, playable

from app.models import MediaFile, PlaybackSession, utcnow
from app.services.compatibility import Decision, PlaybackChoice, decide
from app.services.hardware import binary_identity, device_input_options, device_output_options, gpu_hint
from app.services.transcoding import PlaybackManager, ffmpeg_command
from app.services.transcoding_policy import default_policy


def update_policy(context: TestContext, csrf: str, **values: object) -> dict:
    policy = context.client.get("/api/v1/transcoding-policy").json()
    response = context.client.patch(
        "/api/v1/transcoding-policy", headers={"X-CSRF-Token": csrf}, json={**policy, **values}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_policy_default_permissions_and_validation(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    initial = context.client.get("/api/v1/transcoding-policy").json()
    assert initial["mode"] == "automatic" and not initial["allow_4k"]
    assert initial["changes_apply_to"] == "new_streams" and "WebVTT" in initial["subtitle_behavior"]
    assert context.client.patch("/api/v1/transcoding-policy", json=initial).status_code == 403
    for invalid in (
        {"mode": "unknown"},
        {"max_processes": 9},
        {"hardware_device": "0; command"},
        {"mode": "hardware_required", "preferred_hardware": "auto"},
    ):
        response = context.client.patch(
            "/api/v1/transcoding-policy", headers={"X-CSRF-Token": csrf}, json={**initial, **invalid}
        )
        assert response.status_code == 422
    update_policy(context, csrf, mode="software_only", cpu_preset="fast")
    assert context.client.get("/api/v1/transcoding-policy").json()["cpu_preset"] == "fast"
    create_viewer(context, csrf, [])
    login(context, "viewer")
    assert context.client.get("/api/v1/transcoding-policy").status_code == 403
    assert context.client.post("/api/v1/playback-health/detect").status_code == 403


def test_policy_rejects_media_relative_and_drive_scratch(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    policy = context.client.get("/api/v1/transcoding-policy").json()
    for value in (str(context.media_root), str(context.media_root / "scratch"), "relative", context.media_root.anchor):
        response = context.client.patch(
            "/api/v1/transcoding-policy", headers={"X-CSRF-Token": csrf}, json={**policy, "temp_directory": value}
        )
        assert response.status_code == 422
        assert str(context.media_root) not in response.text


def test_settings_snapshot_and_inactivity_only_affect_new_sessions(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    update_policy(context, csrf, mode="software_only", inactive_session_seconds=600)
    first = begin(context, csrf, fid)
    update_policy(context, csrf, mode="direct_only", inactive_session_seconds=30)
    second = begin(context, csrf, fid)
    with context.session_factory() as db:
        one, two = db.get(PlaybackSession, first["id"]), db.get(PlaybackSession, second["id"])
        assert one and two
        assert one.decision["settings"]["mode"] == "software_only"
        assert two.decision["settings"]["mode"] == "direct_only"
        one.last_seen_at = two.last_seen_at = utcnow() - timedelta(seconds=45)
        db.commit()
    assert context.client.get(first["url"]).status_code == 200
    assert context.client.get(second["url"]).status_code == 410


@pytest.mark.parametrize(
    "mode", ["automatic", "hardware_preferred", "software_only", "direct_only", "hardware_required"]
)
def test_all_modes_keep_compatible_direct_play(owner_context: tuple[TestContext, str], mode: str) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    update_policy(context, csrf, mode=mode, preferred_hardware="qsv")
    session = begin(context, csrf, fid)
    assert session["decision"]["method"] == "direct"
    active = context.client.get("/api/v1/streams").json()["items"][0]
    assert active["method_label"] == "Direct Play" and active["selected_mode"] == mode
    assert active["encoder"] == "none" and not active["fallback"]


def test_direct_only_rejects_video_audio_and_precise_remux_seek(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    update_policy(context, csrf, mode="direct_only")
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.container = "matroska"
        db.commit()
        policy = default_policy(context.config).model_copy(update={"mode": "direct_only"})
        choice = PlaybackChoice(file_id=fid, capabilities=CAPS)
        assert decide(file, choice, context.config, policy).method == "remux"
        file.audio_streams[0].codec = "ac3"
        assert decide(file, choice, context.config, policy).method == "unsupported"
        file.audio_streams[0].codec = "aac"
        file.video_streams[0].codec = "hevc"
        assert decide(file, choice, context.config, policy).method == "unsupported"
        db.rollback()
    response = context.client.post(
        "/api/v1/playback/sessions",
        headers={"X-CSRF-Token": csrf},
        json={"file_id": fid, "capabilities": CAPS, "position_seconds": 2},
    )
    assert response.status_code == 422 and "Direct Play and Remux Only" in response.text


def test_4k_conversion_is_opt_in_not_a_direct_play_ban(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.video_streams[0].height = 2160
        choice = PlaybackChoice(file_id=fid, capabilities=CAPS)
        assert decide(file, choice, context.config).method == "direct"
        file.video_streams[0].codec = "hevc"
        assert decide(file, choice, context.config).method == "unsupported"
        allowed = default_policy(context.config).model_copy(update={"allow_4k": True})
        assert decide(file, choice, context.config, allowed).method == "transcode"


def test_native_tv_capabilities_direct_play_matroska_eac3_multichannel(
    owner_context: tuple[TestContext, str],
) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.container = "matroska"
        file.relative_path = "episode.mkv"
        file.audio_streams[0].codec = "eac3"
        file.audio_streams[0].channels = 6
        native = {**CAPS, "matroska": True, "eac3": True, "max_audio_channels": 8, "multi_audio": True}
        decision = decide(file, PlaybackChoice(file_id=fid, capabilities=native), context.config)
        assert decision.method == "direct"
        assert decision.audio_copy and decision.mime == "video/x-matroska"
        legacy = decide(file, PlaybackChoice(file_id=fid, capabilities=CAPS), context.config)
        assert legacy.method == "transcode" and legacy.audio_transcode


def test_encoder_selection_requires_exact_binary_and_device(owner_context: tuple[TestContext, str]) -> None:
    context, _csrf = owner_context
    manager: PlaybackManager = context.client.app.state.playback_manager
    identity = ("exact-ffmpeg", 100, 123)
    manager.hardware["qsv"] = "test encode passed"
    manager.tested_binary = identity
    policy = default_policy(context.config)
    with patch("app.services.transcoding.binary_identity", return_value=identity):
        assert manager.select_encoder(policy)[0] == "h264_qsv"
        assert manager.select_encoder(policy.model_copy(update={"mode": "software_only"})) == ("libx264", False, None)
        assert manager.select_encoder(policy.model_copy(update={"hardware_device": "1"}))[0] == "libx264"
        required = policy.model_copy(update={"mode": "hardware_required", "preferred_hardware": "nvenc"})
        with pytest.raises(HTTPException, match="Hardware Required"):
            manager.select_encoder(required)
    with patch("app.services.transcoding.binary_identity", return_value=("changed-ffmpeg", 100, 123)):
        assert manager.select_encoder(policy)[0] == "libx264"


def test_gpu_inventory_never_counts_as_verified(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    manager: PlaybackManager = context.client.app.state.playback_manager
    manager.gpus = ["NVIDIA detected", "Intel detected"]
    assert manager.select_encoder(default_policy(context.config))[0] == "libx264"
    response = context.client.get("/api/v1/playback-health").json()
    assert response["selected_mode"] == "automatic" and response["software_fallback"]
    assert all(
        test["test_status"] == "not_tested" and not test["available_codecs"] for test in response["hardware_tests"]
    )
    update_policy(context, csrf, mode="hardware_required", preferred_hardware="qsv")
    assert context.client.get("/api/v1/playback-health").json()["selected_encoder"] == "unavailable"


def test_multiple_gpu_inventory_does_not_guess_vendor_device_index_order() -> None:
    inventory = ["Virtual Display", "AMD Discrete Adapter", "AMD Integrated Adapter"]
    assert gpu_hint("amf", inventory, "1") == "Multiple AMD adapters detected; encoder device 1"
    assert gpu_hint("amf", inventory, "auto") == "Multiple AMD adapters detected; automatic device selection"
    assert gpu_hint("qsv", inventory, "0") is None
    assert gpu_hint("amf", ["AMD only adapter"], "auto") == "AMD only adapter"


def test_probe_success_records_only_actual_tested_codec(owner_context: tuple[TestContext, str]) -> None:
    context, _csrf = owner_context
    manager: PlaybackManager = context.client.app.state.playback_manager
    process = Mock()
    process.wait.return_value = process.poll.return_value = 0
    binary = context.config.app_data_dir / "mock-ffmpeg.exe"
    binary.write_bytes(b"mock bundled binary for probe identity")
    identity = (str(binary), binary.stat().st_size, binary.stat().st_mtime_ns)
    def probe(directory: Path, command: list[str], _timeout: float, _quota: int) -> Mock:
        if "-c:v" in command:
            (directory / "probe.h264").write_bytes(b"synthetic encoded output")
        return process
    with (
        patch.object(manager, "_launch", side_effect=probe) as launch,
        patch("app.services.transcoding.binary_identity", return_value=identity),
        patch("app.services.transcoding.detected_gpus", return_value=["Intel test adapter"]),
        patch("app.services.transcoding.advertised_encoders", return_value={"h264_qsv"}),
    ):
        manager.detect_hardware()
    assert launch.call_count == 2
    for call in launch.call_args_list:
        assert call.args[1][0] == context.config.ffmpeg_path
    assert "testsrc2=size=128x128:rate=10" in launch.call_args_list[0].args[1]
    assert "-xerror" in launch.call_args_list[1].args[1]
    tests = manager.health()["hardware_tests"]
    assert tests[0]["test_status"] == "passed" and tests[0]["available_codecs"] == ["h264"]
    assert all(row["test_status"] == "failed" for row in tests[1:])


@pytest.mark.parametrize(
    ("mode", "required", "fallback"),
    [
        ("automatic", False, True),
        ("hardware_preferred", False, True),
        ("hardware_required", True, False),
        ("software_only", False, False),
    ],
)
def test_startup_fallback_is_per_session_and_required_never_uses_cpu(
    owner_context: tuple[TestContext, str],
    mode: str,
    required: bool,
    fallback: bool,
) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    update_policy(context, csrf, mode=mode, preferred_hardware="qsv")
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.video_streams[0].codec = "hevc"
        db.commit()
    manager: PlaybackManager = context.client.app.state.playback_manager
    identity = ("configured-bundled-ffmpeg", 123, 456)
    manager.tested_binary = identity
    manager.hardware["qsv"] = "test encode passed"
    calls = []

    def launch(directory: Path, command: list[str], _timeout: float, _quota: int) -> Mock:
        encoder = command[command.index("-c:v") + 1]
        calls.append(encoder)
        process = Mock()
        process.stdout = None
        process.poll.return_value = process.wait.return_value = 1 if encoder == "h264_qsv" else 0
        if encoder == "libx264":
            (directory / "index.m3u8").write_text("#EXTM3U\nsegment-000000.ts\n")
            (directory / "segment-000000.ts").write_bytes(b"generated test segment")
        return process

    with (
        patch.object(manager, "_launch", side_effect=launch),
        patch("app.services.transcoding.binary_identity", return_value=identity),
        patch("app.services.transcoding.threading.Thread"),
    ):
        response = context.client.post(
            "/api/v1/playback/sessions", headers={"X-CSRF-Token": csrf}, json={"file_id": fid, "capabilities": CAPS}
        )
    if required:
        assert response.status_code == 422 and calls == ["h264_qsv"]
        assert "software fallback is disabled" in response.text
        assert context.client.get("/api/v1/streams").json()["failures"][0]["error"]
    else:
        assert response.status_code == 201, response.text
        assert calls == (["h264_qsv", "libx264"] if fallback else ["libx264"])
        active = context.client.get("/api/v1/streams").json()["items"][0]
        assert active["encoder"] == "libx264" and active["fallback"] == fallback
        assert active["method_label"] == "Software Transcode"


def test_device_and_cpu_preset_are_explicit(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, source = playable(context, csrf)
    session = begin(context, csrf, fid)
    policy = default_policy(context.config).model_copy(update={"cpu_preset": "slow", "hardware_device": "1"})
    with context.session_factory() as db:
        file, row = db.get(MediaFile, fid), db.get(PlaybackSession, session["id"])
        assert file and row
        decision = Decision(method="transcode", reason="test", output_height=240, bitrate_kbps=500)
        command = ffmpeg_command(source, file, row, decision, context.config.temp_dir, context.config, policy=policy)
        assert command[command.index("-preset") + 1] == "slow"
        assert "-gpu" not in command and "-init_hw_device" not in command
        command = ffmpeg_command(
            source, file, row, decision, context.config.temp_dir, context.config, "h264_nvenc", policy=policy
        )
        assert command[command.index("-gpu") + 1] == "1"
    assert device_output_options("h264_nvenc", "2") == ["-gpu", "2"]
    assert device_input_options("libx264", "1") == []
    assert "child_device=" in " ".join(device_input_options("h264_qsv", "1"))
    assert binary_identity("definitely-missing-ffmpeg") is None


def test_temp_root_change_preserves_existing_storage(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    manager: PlaybackManager = context.client.app.state.playback_manager
    old = manager.root
    other = context.config.app_data_dir / "new-scratch"
    update_policy(context, csrf, temp_directory=str(other))
    assert old in manager.storage_locks and other / "playback" in manager.storage_locks
    assert context.client.get("/api/v1/transcoding-policy").json()["temp_directory"] == str(other.resolve())


@pytest.mark.parametrize("mode", ["automatic", "hardware_preferred", "hardware_required", "software_only"])
def test_runtime_hardware_recovery_is_authorized_once_and_preserves_original_policy(
    owner_context: tuple[TestContext, str],
    mode: str,
) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    update_policy(context, csrf, mode=mode, preferred_hardware="qsv")
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        file.video_streams[0].codec = "hevc"
        db.commit()
    manager: PlaybackManager = context.client.app.state.playback_manager
    identity = ("configured-bundled-ffmpeg", 123, 456)
    manager.tested_binary = identity
    manager.hardware["qsv"] = "test encode passed"
    launched = []

    def launch(directory: Path, command: list[str], _timeout: float, _quota: int) -> Mock:
        encoder = command[command.index("-c:v") + 1]
        launched.append(encoder)
        process = Mock()
        process.stdout = None
        process.poll.return_value = process.wait.return_value = 0
        (directory / "index.m3u8").write_text("#EXTM3U\nsegment-000000.ts\n")
        (directory / "segment-000000.ts").write_bytes(b"generated test segment")
        return process

    with (
        patch.object(manager, "_launch", side_effect=launch),
        patch("app.services.transcoding.binary_identity", return_value=identity),
        patch("app.services.transcoding.threading.Thread"),
    ):
        first = begin(context, csrf, fid)
        job = manager.for_session(first["id"])
        job.process.poll.return_value = job.process.wait.return_value = 5
        endpoint = f"/api/v1/playback/{first['id']}/recovery"
        recoverable = context.client.get(endpoint).json()["recoverable"]
        assert recoverable == (mode in ("automatic", "hardware_preferred"))
        if not recoverable:
            return
        # Recovery is continuation of the original mode, not a newly changed Owner policy.
        update_policy(context, csrf, mode="hardware_required", preferred_hardware="nvenc")
        payload = {"file_id": fid, "capabilities": CAPS, "recovery_from": first["id"], "position_seconds": 2.5}
        response = context.client.post("/api/v1/playback/sessions", headers={"X-CSRF-Token": csrf}, json=payload)
        assert response.status_code == 201, response.text
        recovered = response.json()
        assert launched == ["h264_qsv", "libx264"]
        assert recovered["fallback"] and recovered["selected_mode"] == mode
        assert recovered["position_seconds"] == recovered["video_offset"] == 2.5
        assert not job.directory.exists()
        assert not context.client.get(endpoint).json()["recoverable"]
        repeated = context.client.post("/api/v1/playback/sessions", headers={"X-CSRF-Token": csrf}, json=payload)
        assert repeated.status_code == 409
        assert (
            context.client.post(f"/api/v1/playback/{first['id']}/stop", headers={"X-CSRF-Token": csrf}).status_code
            == 204
        )
        failures = context.client.get("/api/v1/streams").json()["failures"]
        assert failures[0]["id"] == first["id"] and "Hardware" in failures[0]["error"]
    create_viewer(context, csrf, [])
    login(context, "viewer")
    assert context.client.get(endpoint).status_code == 404
