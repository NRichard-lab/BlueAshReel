from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import TestContext
from fastapi import HTTPException
from sqlalchemy import select
from test_household_catalog import create_viewer, library, login

from app.models import (
    AudioStream,
    Library,
    MediaFile,
    MediaItem,
    PlaybackSession,
    SubtitleStream,
    VideoStream,
    WatchProgress,
    utcnow,
)
from app.services.compatibility import PlaybackChoice, decide
from app.services.playback import byte_range, file_chunks
from app.services.playback_lifecycle import retain_history
from app.services.subtitles import sanitize_vtt

CAPS = {"h264": True, "aac": True, "hls": True, "webm_vp9": True, "opus": True}


def playable(context: TestContext, csrf: str) -> tuple[dict, str, str, Path]:
    lib = library(context, csrf)
    source = context.media_root / "Movies" / "local.mp4"
    source.write_bytes(bytes(range(256)) * 4096)
    info = source.stat()
    with context.session_factory() as db:
        item = MediaItem(library_id=lib["id"], kind="movie", title="Local", sort_title="local")
        db.add(item)
        db.flush()
        file = MediaFile(
            media_item_id=item.id,
            library_path_id=lib["paths"][0]["id"],
            relative_path="local.mp4",
            size_bytes=info.st_size,
            modified_ns=info.st_mtime_ns,
            fingerprint="a" * 64,
            container="mov,mp4",
            duration_seconds=120,
            available=True,
        )
        db.add(file)
        db.flush()
        db.add(
            VideoStream(
                media_file_id=file.id,
                stream_index=0,
                codec="h264",
                width=640,
                height=360,
                profile="Main",
                level=30,
                pixel_format="yuv420p",
                bit_depth=8,
            )
        )
        db.add(AudioStream(media_file_id=file.id, stream_index=1, codec="aac", channels=2))
        db.add(SubtitleStream(media_file_id=file.id, stream_index=2, codec="subrip"))
        db.commit()
        return lib, item.id, file.id, source


def begin(context: TestContext, csrf: str, file_id: str, **options: object) -> dict:
    response = context.client.post(
        "/api/v1/playback/sessions",
        headers={"X-CSRF-Token": csrf},
        json={"file_id": file_id, "capabilities": CAPS, **options},
    )
    assert response.status_code == 201, response.text
    return response.json()


def checkpoint(
    context: TestContext,
    csrf: str,
    sid: str,
    position: float,
    sequence: int,
    reason: str = "periodic",
    playing: bool = True,
    elapsed: int = 5,
) -> dict:
    with context.session_factory() as db:
        row = db.get(PlaybackSession, sid)
        assert row
        row.last_seen_at = utcnow() - timedelta(seconds=elapsed)
        db.commit()
    response = context.client.post(
        f"/api/v1/playback/{sid}/progress",
        headers={"X-CSRF-Token": csrf},
        json={"position_seconds": position, "sequence": sequence, "reason": reason, "playing": playing},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, (0, 99, 200)),
        ("bytes=0-9", (0, 9, 206)),
        ("bytes=50-", (50, 99, 206)),
        ("bytes=-20", (80, 99, 206)),
        ("bytes=0-900", (0, 99, 206)),
    ],
)
def test_byte_ranges(header: str | None, expected: tuple[int, int, int]) -> None:
    assert byte_range(header, 100) == expected


@pytest.mark.parametrize(
    "header",
    ["bytes=0-1,4-5", "bytes=-0", "bytes=100-", "bytes=9-2", "bytes=-", "items=0-9", "bytes=" + "9" * 200 + "-"],
)
def test_invalid_ranges(header: str) -> None:
    with pytest.raises(HTTPException) as error:
        byte_range(header, 100)
    assert error.value.status_code == 416
    assert error.value.headers == {"Content-Range": "bytes */100"}


def test_direct_head_seek_and_disconnect(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, source = playable(context, csrf)
    session = begin(context, csrf, fid)
    url = session["url"]
    response = context.client.get(url, headers={"Range": "bytes=256-511"})
    assert response.status_code == 206
    assert response.content == bytes(range(256))
    assert response.headers["content-range"] == f"bytes 256-511/{source.stat().st_size}"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"
    assert str(source) not in str(response.headers)
    assert context.client.head(url, headers={"Range": "bytes=-12"}).headers["content-length"] == "12"
    assert context.client.head(url).content == b""
    assert context.client.get(url, headers={"Range": "bytes=1-2,4-5"}).status_code == 416
    with context.session_factory() as db:
        row, file = db.get(PlaybackSession, session["id"]), db.get(MediaFile, fid)
        assert row and file

        async def cancel() -> None:
            chunks = file_chunks(db, row, file, source, 0, file.size_bytes - 1, context.config)
            assert len(await anext(chunks)) == 256 * 1024
            await chunks.aclose()  # type: ignore[attr-defined]

        with patch("app.services.playback.os.close", wraps=__import__("os").close) as close:
            asyncio.run(cancel())
            assert close.call_count == 1
    assert (
        context.client.post(f"/api/v1/playback/{session['id']}/stop", headers={"X-CSRF-Token": csrf}).status_code == 204
    )
    assert context.client.get(url).status_code == 410


def test_playback_authorization_revocation_source_and_subtitles(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    lib, _item, fid, source = playable(context, csrf)
    create_viewer(context, csrf, [lib["id"]])
    own = begin(context, csrf, fid)
    viewer_csrf = login(context, "viewer")
    assert context.client.get(own["url"]).status_code == 404
    assert context.client.get(f"/api/v1/playback/{own['id']}/subtitles/2.vtt").status_code == 404
    session = begin(context, viewer_csrf, fid)
    assert context.client.post(f"/api/v1/playback/{session['id']}/stop").status_code == 403
    with patch("app.api.playback.extract_subtitles", return_value="WEBVTT\n\n00:01.000 --> 00:03.000\nLocal\n"):
        assert context.client.get(f"/api/v1/playback/{session['id']}/subtitles/2.vtt").status_code == 200
    with context.session_factory() as db:
        library_row = db.get(Library, lib["id"])
        assert library_row
        library_row.enabled = False
        db.commit()
    assert context.client.get(session["url"]).status_code == 404
    with context.session_factory() as db:
        library_row = db.get(Library, lib["id"])
        assert library_row
        library_row.enabled = True
        db.commit()
    source.unlink()
    assert context.client.get(session["url"]).status_code in (404, 409)
    context.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": viewer_csrf})
    assert context.client.get(session["url"]).status_code == 401
    with context.session_factory() as db:
        assert db.get(PlaybackSession, session["id"]).state == "stopped"


def test_progress_pause_seek_restart_threshold_history_and_resume(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    lib, item, fid, _source = playable(context, csrf)
    create_viewer(context, csrf, [lib["id"]])
    session = begin(context, csrf, fid)
    sid = session["id"]
    checkpoint(context, csrf, sid, 0, 1, "playing")
    checkpoint(context, csrf, sid, 5, 2, "pause", False)
    assert context.client.get(f"/api/v1/browse/media/{item}").json()["position_seconds"] == 5
    checkpoint(context, csrf, sid, 118, 3, "seek", False)
    assert not context.client.get(f"/api/v1/browse/media/{item}").json()["watched"]
    assert checkpoint(context, csrf, sid, 1, 2)["saved"] is False
    context.client.post(f"/api/v1/playback/{sid}/stop", headers={"X-CSRF-Token": csrf})
    restarted = begin(context, csrf, fid, restart=True)
    assert restarted["position_seconds"] == 0
    assert context.client.get(f"/api/v1/browse/media/{item}").json()["position_seconds"] == 118
    checkpoint(context, csrf, restarted["id"], 0, 1, "playing")
    for seq, pos in enumerate([15, 30, 45, 60, 75, 90, 108], 2):
        checkpoint(context, csrf, restarted["id"], pos, seq, elapsed=18)
    assert context.client.get(f"/api/v1/browse/media/{item}").json()["watched"]
    assert context.client.get("/api/v1/browse/home").json()["continue"] == []
    context.client.delete("/api/v1/profile/history", headers={"X-CSRF-Token": csrf})
    assert context.client.get(f"/api/v1/playback/{restarted['id']}").status_code == 404
    with context.session_factory() as db:
        assert db.scalar(select(PlaybackSession.id)) is None
        assert db.scalar(select(WatchProgress.id)) is None
    viewer_csrf = login(context, "viewer")
    assert begin(context, viewer_csrf, fid)["position_seconds"] == 0


def test_limits_and_expiry(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    one = begin(context, csrf, fid)
    begin(context, csrf, fid)
    assert (
        context.client.post(
            "/api/v1/playback/sessions", headers={"X-CSRF-Token": csrf}, json={"file_id": fid, "capabilities": CAPS}
        ).status_code
        == 429
    )
    with context.session_factory() as db:
        row = db.get(PlaybackSession, one["id"])
        assert row
        row.last_seen_at = utcnow() - timedelta(minutes=10)
        db.commit()
    assert context.client.get(one["url"]).status_code == 410
    begin(context, csrf, fid)


def test_compatibility_is_conservative(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    choice = PlaybackChoice(file_id=fid, capabilities=CAPS)
    with context.session_factory() as db:
        file = db.get(MediaFile, fid)
        assert file
        assert decide(file, choice, context.config).method == "direct"
        file.container = "matroska,webm"
        assert decide(file, choice, context.config).method == "remux"
        file.audio_streams[0].codec = "dts"
        result = decide(file, choice, context.config)
        assert result.method == "transcode" and result.video_copy and not result.audio_copy
        file.video_streams[0].codec = "hevc"
        assert not decide(file, choice, context.config).video_copy
        file.video_streams[0].codec = "vp9"
        file.video_streams[0].profile = "Profile 2"
        file.video_streams[0].pixel_format = "yuv420p10le"
        file.video_streams[0].bit_depth = None
        file.audio_streams[0].codec = "opus"
        assert decide(file, choice, context.config).method == "transcode"
        choice.subtitle_index = 999
        assert decide(file, choice, context.config).method == "unsupported"
        choice.subtitle_index = 2
        file.subtitle_streams[0].codec = "hdmv_pgs_subtitle"
        assert decide(file, choice, context.config).method == "unsupported"


def test_subtitle_sanitizing_preserves_short_timing() -> None:
    value = sanitize_vtt(
        "WEBVTT\n\n00:01.000 --> 00:04.200\n<b>Local</b> & text\n\n"
        "00:99:99.000 --> 00:99:99.500\nbad\n\n00:05.000 --> 00:02.000\nreversed\n"
    )
    assert "00:01.000 --> 00:04.200\nLocal &amp; text" in value
    assert "bad" not in value and "reversed" not in value and "<b>" not in value


def test_retention_clears_both_histories(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, item, fid, _source = playable(context, csrf)
    session = begin(context, csrf, fid)
    checkpoint(context, csrf, session["id"], 0, 1, "playing")
    checkpoint(context, csrf, session["id"], 5, 2, "pause", False)
    with context.session_factory() as db:
        row = db.get(PlaybackSession, session["id"])
        progress = db.scalar(select(WatchProgress).where(WatchProgress.media_item_id == item))
        assert row and progress
        row.state = "stopped"
        row.last_seen_at = progress.last_played_at = utcnow() - timedelta(days=400)
        db.commit()
        retain_history(db, context.config)
        db.commit()
        assert db.scalar(select(WatchProgress.id)) is None
        assert db.scalar(select(PlaybackSession.id)) is None


@pytest.mark.parametrize("relative", ["../outside.mp4", "/outside.mp4", "C:/outside.mp4", "folder/../../outside.mp4"])
def test_playback_rejects_path_escape(owner_context: tuple[TestContext, str], relative: str) -> None:
    context, csrf = owner_context
    _lib, _item, fid, _source = playable(context, csrf)
    with context.session_factory() as db:
        db.get(MediaFile, fid).relative_path = relative
        db.commit()
    response = context.client.post(
        "/api/v1/playback/sessions", headers={"X-CSRF-Token": csrf}, json={"file_id": fid, "capabilities": CAPS}
    )
    assert response.status_code == 404 and relative not in response.text


def test_playback_rejects_symlink_and_malformed_id(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, _item, fid, source = playable(context, csrf)
    # Mock the platform lstat result: this runs even on Windows without the
    # privilege needed to create a real symlink. The source is never replaced.
    import stat
    from types import SimpleNamespace

    original = Path.lstat

    def linked(path, *args, **kwargs):
        if path == source:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path, *args, **kwargs)

    with patch.object(Path, "lstat", linked):
        response = context.client.post(
            "/api/v1/playback/sessions", headers={"X-CSRF-Token": csrf}, json={"file_id": fid, "capabilities": CAPS}
        )
    assert response.status_code == 404


def test_direct_and_catalog_privacy_fail_closed(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    _lib, item, fid, source = playable(context, csrf)
    with patch("socket.create_connection", side_effect=AssertionError("No runtime outbound calls")):
        session = begin(context, csrf, fid)
        response = context.client.get(session["url"], headers={"Range": "bytes=0-127"})
        assert response.status_code == 206 and len(response.content) == 128
        for url in (f"/api/v1/browse/media/{item}", "/api/v1/streams", "/api/v1/playback-health"):
            result = context.client.get(url)
            assert result.status_code == 200
            assert str(source).replace("\\", "\\\\") not in result.text
            assert context.config.app_secret_key not in result.text
        assert context.client.get("/api/v1/privacy").json()["runtime_outbound_allowed"] is False
    response = context.client.post(
        "/api/v1/playback/sessions",
        headers={"X-CSRF-Token": csrf},
        json={"file_id": "../../local.mp4", "capabilities": CAPS},
    )
    assert response.status_code == 404
