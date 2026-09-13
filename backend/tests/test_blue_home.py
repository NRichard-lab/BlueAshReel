"""Profile identity is separate from the real account's library grants."""

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_playback import CAPS
from test_remote_media import call, setup_remote

from app.models import BlueHomeState, PlaybackSession, PortalGrant, WatchProgress


def profile(auth, profile_id=None, owner=False):
    return {
        **auth,
        "blue_home": {
            "profile_id": profile_id or str(uuid.uuid4()),
            "home_id": str(uuid.uuid5(uuid.NAMESPACE_URL, auth["agent_id"])),
            "owner_account_id": auth["user_id"],
            "owner_profile": owner,
        },
    }


def test_owner_history_mapping_is_lossless_and_idempotent(owner_context):
    media, auth, (_, item_id, _, _) = setup_remote(owner_context)
    card = call(media, auth, "catalog.list")["items"][0]
    call(media, auth, "catalog.watched", media_id=card["id"], watched=True)
    with media.factory() as db:
        before = db.scalar(select(WatchProgress).where(WatchProgress.media_item_id == item_id))
        identity, state_user = before.id, before.user_id
    owner = profile(auth, owner=True)
    for _ in range(2):
        assert call(media, owner, "catalog.detail", media_id=card["id"])["watched"] is True
    with media.factory() as db:
        after = db.get(WatchProgress, identity)
        mapping = db.get(BlueHomeState, owner["blue_home"]["profile_id"])
        assert after.user_id == mapping.local_user_id == state_user
        assert after.watched


def test_local_profile_and_linked_account_share_progress_not_permissions(owner_context):
    media, auth, (_, item_id, _, _) = setup_remote(owner_context)
    selected = profile(auth)
    card = call(media, selected, "catalog.list")["items"][0]
    started = call(media, selected, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    call(
        media,
        selected,
        "playback.progress",
        session_id=started["id"],
        position_seconds=0,
        playing=True,
        reason="playing",
        sequence=1,
    )
    with media.factory() as db:
        row = db.get(PlaybackSession, media.resolve(db, "playback", started["id"]))
        row.last_seen_at -= timedelta(seconds=25)
        db.commit()
    call(
        media,
        selected,
        "playback.progress",
        session_id=started["id"],
        position_seconds=25,
        playing=True,
        reason="periodic",
        sequence=2,
    )
    other = profile(auth)
    assert call(media, other, "catalog.home")["continue"] == []
    assert call(media, auth, "catalog.home")["continue"] == []
    with pytest.raises(HTTPException):
        call(
            media,
            other,
            "playback.progress",
            session_id=started["id"],
            position_seconds=50,
            playing=True,
            reason="periodic",
            sequence=3,
        )
    call(media, selected, "playback.stop", session_id=started["id"])
    external = {**selected, "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "role": "viewer"}
    with pytest.raises(HTTPException):
        call(media, external, "catalog.home")
    call(media, auth, "grants.set", user_id=external["user_id"], library_ids=[])
    assert call(media, external, "catalog.home")["continue"] == []
    with pytest.raises(HTTPException):
        call(media, external, "catalog.detail", media_id=card["id"])
    call(media, auth, "grants.set", user_id=external["user_id"], library_ids=[card["library_id"]])
    assert call(media, external, "catalog.home")["continue"][0]["position_seconds"] == 25
    resumed = call(media, external, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    assert resumed["position_seconds"] == 25
    call(media, external, "playback.stop", session_id=resumed["id"])
    call(media, external, "catalog.watched", media_id=card["id"], watched=True)
    assert call(media, selected, "catalog.detail", media_id=card["id"])["watched"] is True
    assert call(media, other, "catalog.detail", media_id=card["id"])["watched"] is False
    with pytest.raises(HTTPException):
        call(media, external, "libraries.list")
    with media.factory() as db:
        state = db.get(BlueHomeState, selected["blue_home"]["profile_id"])
        grant = db.get(PortalGrant, external["user_id"])
        assert state.local_user_id != grant.local_user_id
        assert db.scalar(
            select(WatchProgress).where(
                WatchProgress.user_id == state.local_user_id, WatchProgress.media_item_id == item_id
            )
        ).watched


@pytest.mark.parametrize("change", ["foreign-home-owner", "invalid-profile", "foreign-owner-profile"])
def test_invalid_trusted_context_is_rejected(owner_context, change):
    media, auth, _ = setup_remote(owner_context)
    selected = profile(auth)
    if change == "foreign-home-owner":
        selected["blue_home"]["owner_account_id"] = str(uuid.uuid4())
    elif change == "invalid-profile":
        selected["blue_home"]["profile_id"] = "../bad"
    else:
        selected["blue_home"]["owner_profile"] = True
        selected["user_id"] = str(uuid.uuid4())
        selected["role"] = "viewer"
        call(media, auth, "grants.set", user_id=selected["user_id"], library_ids=[])
    with pytest.raises(HTTPException):
        call(media, selected, "catalog.home")


def test_retiring_old_profile_does_not_stop_new_profile_playback(owner_context):
    media, auth, _ = setup_remote(owner_context)
    old = profile(auth)
    new = profile(auth)
    card = call(media, old, "catalog.list")["items"][0]
    first = call(media, old, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    second = call(media, new, "playback.start", file_id=card["file_id"], capabilities=CAPS)
    media.release(old)
    binding = (media.agent_id, new["user_id"], new["session_id"])
    assert media._authorizations[binding] is new
    with media.factory() as db:
        assert db.get(PlaybackSession, media.resolve(db,"playback",first["id"])).state == "stopped"
        assert db.get(PlaybackSession, media.resolve(db,"playback",second["id"])).state == "active"
    call(media, new, "playback.stop", session_id=second["id"])
