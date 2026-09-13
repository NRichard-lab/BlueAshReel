from __future__ import annotations

import asyncio
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_playback import CAPS, begin
from test_remote_media import call, setup_remote

from app.models import ApplicationSetting, MediaFile, PlaybackSession, utcnow
from app.services.compatibility import PlaybackChoice, decide
from app.services.remote_streaming import (
    KEY,
    LIMIT_REACHED,
    RemoteStreamingSettings,
    read_remote_settings,
    update_remote_settings,
)
from app.services.transcoding import ffmpeg_command
from app.services.transcoding_policy import default_policy


def test_default_read_does_not_persist_or_add_a_playback_cap(context):
    with context.session_factory() as db:
        value = read_remote_settings(db, context.config)
        assert value.enabled and value.max_quality == "original"
        assert value.bitrate_limit_bps is None
        assert value.session_limit == context.config.playback_max_streams
        assert db.get(ApplicationSetting, KEY) is None


@pytest.mark.parametrize("changes", [
    {"enabled": "false"}, {"enabled": 0}, {"max_quality": "8k"},
    {"bitrate_limit_bps": 0}, {"bitrate_limit_bps": -1}, {"bitrate_limit_bps": 50_000_001},
    {"bitrate_limit_bps": 999_999}, {"bitrate_limit_bps": True}, {"bitrate_limit_bps": 1_000_000.5},
    {"session_limit": 0}, {"session_limit": 101}, {"session_limit": 1.5}, {"session_limit": True},
    {"schema_version": 2}, {"unrecognized": True},
])
def test_validation_does_not_persist(context, changes):
    with context.session_factory() as db:
        with pytest.raises(ValueError):
            update_remote_settings(db, context.config, changes)
        db.rollback()
        assert db.get(ApplicationSetting, KEY) is None


def test_persists_partial_updates_and_commit_failure(context):
    with context.session_factory() as db:
        update_remote_settings(db, context.config, {"enabled": False, "bitrate_limit_bps": 12_500_000})
        update_remote_settings(db, context.config, {"max_quality": "1080p", "session_limit": 3})
    with context.session_factory() as db:
        value = read_remote_settings(db, context.config)
        assert value == RemoteStreamingSettings(enabled=False, bitrate_limit_bps=12_500_000,
                                                max_quality="1080p", session_limit=3)
        with patch.object(db, "commit", side_effect=OSError("disk unavailable")), pytest.raises(OSError):
            update_remote_settings(db, context.config, {"enabled": True})
        db.rollback()
        assert read_remote_settings(db, context.config) == value


def test_owner_rpc_and_disabled_playback_preserve_browsing_and_local_playback(owner_context):
    media, auth, (_, _, local_file, _) = setup_remote(owner_context)
    before = call(media, auth, "settings.remote_streaming.get")
    assert before["enabled"]
    card = call(media, auth, "catalog.list")["items"][0]
    started = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    call(media, auth, "settings.remote_streaming.update", enabled=False)
    assert call(media, auth, "settings.remote_streaming.get")["enabled"] is False
    for op, args in [
        ("playback.start", {"file_id": card["file_id"], "capabilities": CAPS}),
        ("playback.decision", {"file_id": card["file_id"], "capabilities": CAPS}),
        ("playback.bytes", {"session_id": started["id"], "resource": "file", "offset": 0, "length": 100}),
    ]:
        response = asyncio.run(media.dispatch({"id": str(uuid.uuid4()), "op": op, "payload": args}, auth))
        assert response["error"] == "remote_streaming_disabled"
    assert call(media, auth, "status")["health"] == "ok"
    assert call(media, auth, "catalog.list")["items"][0]["id"] == card["id"]
    context, csrf = owner_context
    assert begin(context, csrf, local_file)["decision"]["method"] == "direct"
    call(media, auth, "playback.stop", session_id=started["id"])
    call(media, auth, "settings.remote_streaming.update", enabled=True)
    assert call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    with pytest.raises(HTTPException):
        call(media, {**auth, "role": "viewer"}, "settings.remote_streaming.update", enabled=False)


def test_remote_session_limit_excludes_local_and_releases_stale_slot(owner_context):
    media, auth, (_, _, local_file, _) = setup_remote(owner_context)
    context, csrf = owner_context
    local = begin(context, csrf, local_file)
    call(media, auth, "settings.remote_streaming.update", session_limit=1)
    card = call(media, auth, "catalog.list")["items"][0]
    first = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    with pytest.raises(HTTPException, match=LIMIT_REACHED):
        call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    with media.factory() as db:
        internal = media.resolve(db, "playback", first["id"])
        row = db.get(PlaybackSession, internal)
        assert row.state == "active" and db.get(PlaybackSession, local["id"]).state == "active"
        row.last_seen_at = utcnow() - timedelta(seconds=601)
        db.commit()
    second = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    assert second["id"] != first["id"]
    with media.factory() as db:
        assert db.get(PlaybackSession, internal).state == "expired"


def test_client_payload_cannot_claim_local_transport(owner_context):
    media, auth, _ = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    call(media, auth, "settings.remote_streaming.update", enabled=False)
    with pytest.raises((HTTPException, ValueError)):
        call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS, remote_playback=False)
    with media.factory() as db:
        assert list(db.scalars(select(PlaybackSession))) == []


def test_concurrent_remote_admission_cannot_overbook(owner_context):
    media, auth, _ = setup_remote(owner_context)
    call(media, auth, "settings.remote_streaming.update", session_limit=1)
    card = call(media, auth, "catalog.list")["items"][0]

    def start():
        try:
            return call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
        except HTTPException as error:
            assert error.detail == LIMIT_REACHED
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert sum(value is not None for value in results) == 1
    with media.factory() as db:
        assert len(list(db.scalars(select(PlaybackSession).where(PlaybackSession.state == "active")))) == 1


@pytest.mark.parametrize("source_height,cap,expected,method", [
    (720, "1080p", 720, "direct"), (1080, "720p", 720, "transcode"),
    (2160, "1080p", 1080, "transcode"), (360, "480p", 360, "direct"),
    (2160, "4k", 2160, "direct"),
])
def test_quality_ceiling_never_upscales(owner_context, source_height, cap, expected, method):
    media, _auth, (_, _, file_id, _) = setup_remote(owner_context)
    with media.factory() as db:
        file = db.get(MediaFile, file_id)
        file.video_streams[0].height = source_height
        value = decide(file, PlaybackChoice(file_id=file_id, capabilities=CAPS), media.config,
                       default_policy(media.config).model_copy(update={"allow_4k": True}),
                       RemoteStreamingSettings(max_quality=cap))
        assert value.output_height == expected and value.method == method


@pytest.mark.parametrize("source_bps,method", [(2_000_000, "direct"), (12_000_000, "transcode"), (None, "transcode")])
def test_bitrate_budget_and_existing_ffmpeg_command(owner_context, source_bps, method, tmp_path):
    media, _auth, (_, _, file_id, source) = setup_remote(owner_context)
    with media.factory() as db:
        file = db.get(MediaFile, file_id)
        file.bitrate = source_bps
        choice = decide(file, PlaybackChoice(file_id=file_id, capabilities=CAPS), media.config,
                        remote=RemoteStreamingSettings(bitrate_limit_bps=4_000_000))
        assert choice.method == method
        if method == "transcode":
            assert not choice.video_copy and not choice.audio_copy
            assert choice.bitrate_kbps == 3640  # 4 Mbps minus mux/audio allowance.
            command = ffmpeg_command(source, file, PlaybackSession(audio_index=1, decision={}),
                                     choice, tmp_path, media.config)
            assert command[command.index("-maxrate") + 1] == "3640k"
            assert command[command.index("-b:a") + 1] == "160k"


def test_unsupported_conversion_is_not_reported_as_enforced(owner_context):
    media, _auth, (_, _, file_id, _) = setup_remote(owner_context)
    with media.factory() as db:
        file = db.get(MediaFile, file_id)
        file.video_streams[0].height = 2160
        policy = default_policy(media.config)
        value = decide(file, PlaybackChoice(file_id=file_id, capabilities=CAPS), media.config, policy,
                       RemoteStreamingSettings(max_quality="720p"))
        assert value.method == "unsupported" and "4K transcoding is disabled" in value.reason


def test_real_remote_conversion_obeys_ceiling_and_serves_existing_hls(owner_context):
    from test_transcoding import conversion_file

    from app.main import app
    from app.remote.media import RemoteMedia
    from app.remote.protocol import decode
    from app.services.ffprobe import run_ffprobe

    executable = os.getenv("TEST_FFMPEG_PATH")
    if not executable:
        pytest.skip("Set TEST_FFMPEG_PATH for synthetic conversion integration")
    context, csrf = owner_context
    file_id, _source, _lib = conversion_file(context, csrf, executable, size="1280x720")
    media = RemoteMedia(context.config, context.session_factory, app.state.playback_manager)
    media.bind(str(uuid.uuid4()), str(uuid.uuid4()))
    auth = {"authorized": True, "agent_id": media.agent_id, "user_id": media.owner_id,
            "session_id": str(uuid.uuid4()), "role": "owner", "expires_at": time.time() + 800}
    call(media, auth, "settings.remote_streaming.update", max_quality="480p", bitrate_limit_bps=1_000_000)
    card = call(media, auth, "catalog.list")["items"][0]
    result = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    assert result["decision"]["method"] == "transcode"
    assert result["decision"]["output_height"] == 480
    assert result["decision"]["bitrate_kbps"] == 790
    manifest = decode(call(media, auth, "playback.bytes", session_id=result["id"], resource="manifest",
                           snapshot_id=str(uuid.uuid4()))["data"]).decode()
    segment = next(line for line in manifest.splitlines() if line and not line.startswith("#"))
    assert decode(call(media, auth, "playback.bytes", session_id=result["id"], resource="segment",
                       segment=segment)["data"])[0] == 0x47
    with media.factory() as db:
        internal = media.resolve(db, "playback", result["id"])
        stored = db.get(PlaybackSession, internal)
        assert stored.media_file_id == file_id and stored.decision["remote_playback"] is True
        assert stored.decision["remote_settings"]["bitrate_limit_bps"] == 1_000_000
    job = media.manager.for_session(internal)
    segment_file = next(job.directory.glob("segment-*.ts"))
    probe, _ = run_ffprobe(str(Path(executable).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")),
                           segment_file, 10)
    assert probe.video[0].details["height"] == 480
    call(media, auth, "playback.stop", session_id=result["id"])
    assert job.process.poll() is not None
