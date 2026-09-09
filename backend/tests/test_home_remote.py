"""Home contract through the existing authorized encrypted-media adapter."""

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from test_household_catalog import movie
from test_playback import CAPS
from test_remote_access import browser_session, encrypted_request
from test_remote_media import call, setup_remote

from app.models import LocalArtwork, PlaybackSession
from app.remote.protocol import decode


def test_home_opaque_mapping_artwork_and_real_playback_lifecycle(owner_context):
    context, _ = owner_context
    media, auth, (library, local_id, _, source) = setup_remote(owner_context)
    poster = b"\xff\xd8\xfflocal-test-poster"
    source.with_name("poster.jpg").write_bytes(poster)
    with media.factory() as db:
        db.add(
            LocalArtwork(
                media_item_id=local_id,
                library_path_id=library["paths"][0]["id"],
                source_path="poster.jpg",
                artwork_type="poster",
                fingerprint="b" * 64,
            )
        )
        db.commit()
    initial = call(media, auth, "catalog.home")
    card = initial["recent_movies"][0]
    assert initial["continue"] == [] and card["id"] != local_id
    assert initial["libraries"][0]["id"] == card["library_id"] != library["id"]
    assert card["added_at"] and card["artwork_id"] and "poster_url" not in card
    assert str(source.parent) not in json.dumps(initial)
    assert call(media, auth, "catalog.detail", media_id=card["id"])["id"] == card["id"]
    assert decode(call(media, auth, "artwork.bytes", artwork_id=card["artwork_id"])["data"]) == poster
    started = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
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
        db.get(PlaybackSession, media.resolve(db, "playback", started["id"])).last_seen_at -= timedelta(seconds=25)
        db.commit()
    call(
        media,
        auth,
        "playback.progress",
        session_id=started["id"],
        position_seconds=25,
        playing=True,
        reason="periodic",
        sequence=2,
    )
    call(media, auth, "playback.stop", session_id=started["id"])
    refreshed = call(media, auth, "catalog.home")["continue"][0]
    assert refreshed["id"] == card["id"] and refreshed["position_seconds"] == 25
    assert refreshed["completion"] == pytest.approx(100 * 25 / 120)
    assert call(media, auth, "catalog.detail", media_id=card["id"])["position_seconds"] == 25
    resumed = call(media, auth, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    assert resumed["position_seconds"] == 25
    call(media, auth, "playback.stop", session_id=resumed["id"])
    for guessed in (local_id, str(uuid.uuid4()), "../../private", str(source)):
        with pytest.raises((HTTPException, ValueError)):
            call(media, auth, "artwork.bytes", artwork_id=guessed)
    viewer = {**auth, "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "role": "viewer"}
    call(media, auth, "grants.set", user_id=viewer["user_id"], library_ids=[])
    assert all(not values for values in call(media, viewer, "catalog.home").values())
    with pytest.raises(HTTPException):
        call(media, viewer, "artwork.bytes", artwork_id=card["artwork_id"])
    call(media, auth, "grants.set", user_id=viewer["user_id"], library_ids=[card["library_id"]])
    allowed = call(media, viewer, "catalog.home")
    assert allowed["libraries"] == initial["libraries"] and allowed["continue"] == []
    assert allowed["recent_movies"][0]["id"] == card["id"]
    with pytest.raises(HTTPException):
        call(media, viewer, "libraries.list")
    for invalid in (
        {**auth, "authorized": False},
        {**auth, "expires_at": 0},
        {**auth, "agent_id": str(uuid.uuid4())},
        {**auth, "user_id": str(uuid.uuid4()), "role": "viewer"},
    ):
        for op, payload in (("catalog.home", {}), ("artwork.bytes", {"artwork_id": card["artwork_id"]})):
            with pytest.raises(HTTPException):
                call(media, invalid, op, **payload)
    with pytest.raises(ValueError):
        call(media, auth, "catalog.home", library_id=card["library_id"])
    session, incoming, _, _, _, _ = browser_session()
    request = {"id": str(uuid.uuid4()), "op": "catalog.home", "payload": {}}
    result = asyncio.run(media.dispatch(session.open(encrypted_request(session, incoming, request)), auth))
    assert result["ok"] and result["result"]["recent_movies"][0]["id"] == card["id"]
    assert card["id"] not in json.dumps(session.seal(result))


def test_home_alias_queries_are_batched_and_stable(owner_context):
    context, _ = owner_context
    media, auth, (library, _, _, _) = setup_remote(owner_context)
    engine = media.factory.kw["bind"]

    def measure():
        selects = []

        def capture(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            return call(media, auth, "catalog.home"), selects
        finally:
            event.remove(engine, "before_cursor_execute", capture)

    baseline = call(media, auth, "catalog.home")
    _, small = measure()
    for index in range(30):
        movie(context, library, f"Additional {index}")
    large, queries = measure()
    assert len(large["recent_movies"]) == 20
    assert len(queries) <= len(small) + 1 and len(queries) < 30
    assert large == call(media, auth, "catalog.home")
    assert large["libraries"] == baseline["libraries"]
