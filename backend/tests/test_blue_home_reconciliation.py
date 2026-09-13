"""Cross-feature reconciliation: transport, policies, caches and viewing state."""

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_blue_home import profile
from test_playback import CAPS
from test_remote_media import call, setup_remote
from test_transcoding import conversion_file, ffmpeg  # noqa: F401

from app.models import PlaybackSession, WatchProgress


def checkpoint(media, auth, started):
    call(
        media,
        auth,
        "playback.progress",
        session_id=started["id"],
        position_seconds=0,
        playing=True,
        reason="playing",
        sequence=1,
    )
    with media.factory() as db:
        row = db.get(PlaybackSession, media.resolve(db, "playback", started["id"]))
        row.last_seen_at -= timedelta(seconds=2)
        db.commit()
    call(
        media,
        auth,
        "playback.progress",
        session_id=started["id"],
        position_seconds=2,
        playing=True,
        reason="periodic",
        sequence=2,
    )


@pytest.mark.parametrize("transport", ["local", "relay"])
def test_profile_direct_cache_and_remote_policy(owner_context, transport):
    media, auth, _ = setup_remote(owner_context)
    selected = profile({**auth, "transport": transport})
    card = call(media, selected, "catalog.list")["items"][0]
    started = call(media, selected, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    checkpoint(media, selected, started)
    payload = dict(session_id=started["id"], resource="file", offset=0, length=64)
    assert call(media, selected, "playback.bytes", **payload)
    call(media, auth, "settings.remote_streaming.update", enabled=False)
    if transport == "relay":
        with pytest.raises(HTTPException):
            call(media, selected, "playback.bytes", **payload)
    else:
        assert call(media, selected, "playback.bytes", **payload)
    call(media, auth, "settings.remote_streaming.update", enabled=True)
    # Reuse the same authorization object's caches, as a renewed context can do.
    selected["blue_home"] = {**selected["blue_home"], "profile_id": str(uuid.uuid4())}
    with pytest.raises(HTTPException):
        call(media, selected, "playback.bytes", **payload)
    assert call(media, selected, "catalog.home")["continue"] == []


@pytest.mark.parametrize("transport", ["local", "relay"])
def test_profile_software_fallback_remote_ceiling_and_failure(owner_context, ffmpeg, transport):  # noqa: F811
    context, csrf = owner_context
    media, auth, _ = setup_remote(owner_context)
    fid, source, _ = conversion_file(context, csrf, ffmpeg, "mpeg4", "ac3", "640x360")
    original = Path(source).read_bytes()
    selected = profile({**auth, "transport": transport})
    call(media, auth, "transcoding.update", mode="automatic", max_height=None, allow_software_fallback=True)
    call(media, auth, "settings.remote_streaming.update", max_quality="480p", bitrate_limit_bps=1000000)
    with media.factory() as db:
        opaque = media.alias(db, "file", fid)
        db.commit()
    started = call(media, selected, "playback.start", file_id=opaque, capabilities=CAPS)
    with media.factory() as db:
        row = db.get(PlaybackSession, media.resolve(db, "playback", started["id"]))
        sid = row.id
        assert row.decision["remote_playback"] == (transport == "relay")
        if transport == "relay":
            assert row.decision["bitrate_kbps"] <= 790
    job = media.manager.for_session(sid)
    assert job.encoder == "libx264" and job.process.wait(timeout=25) == 0
    checkpoint(media, selected, started)
    other = profile({**auth, "transport": transport})
    assert call(media, other, "catalog.home")["continue"] == []
    assert call(
        media, selected, "playback.bytes", session_id=started["id"], resource="manifest", offset=0, length=10000
    )
    call(media, selected, "playback.stop", session_id=started["id"])
    assert not media.manager.jobs and Path(source).read_bytes() == original
    # A denied fallback must not create progress in the other profile.
    call(media, auth, "transcoding.update", allow_software_fallback=False)
    with pytest.raises(HTTPException):
        call(media, other, "playback.start", file_id=opaque, capabilities=CAPS)
    assert call(media, other, "catalog.home")["continue"] == []
    with media.factory() as db:
        rows = list(db.scalars(select(WatchProgress)))
        assert len(rows) == 1 and rows[0].position_seconds == 2
