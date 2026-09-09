from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from test_household_catalog import create_viewer, library, login, movie

from app.models import Episode, Library, LibraryPath, MediaFile, MediaItem, Season, Series, WatchProgress, utcnow

HOME_KEYS = {"continue", "recent_movies", "recent_episodes", "libraries", "movies", "shows", "recent_watched"}


def home(context):
    response = context.client.get("/api/v1/browse/home")
    assert response.status_code == 200, response.text
    assert set(response.json()) == HOME_KEYS
    assert str(context.media_root) not in response.text
    assert "canonical_path" not in response.text and "relative_path" not in response.text
    return response.json()


def test_home_empty_and_authenticated(owner_context):
    context, _ = owner_context
    assert home(context) == {key: [] for key in HOME_KEYS}
    context.client.cookies.clear()
    assert context.client.get("/api/v1/browse/home").status_code == 401


def test_home_addition_order_uses_ingestion_not_file_modification(owner_context):
    context, csrf = owner_context
    movies = library(context, csrf)
    television = library(context, csrf, "Television", "tv")
    oldest, newest = [movie(context, movies, name) for name in ("Old arrival", "New arrival")]
    ep_old, ep_new = [movie(context, television, name) for name in ("Earlier episode", "Latest episode")]
    with context.session_factory() as db:
        now = utcnow()
        show = MediaItem(library_id=television["id"], kind="series", title="Local series", sort_title="local series")
        db.add(show)
        db.flush()
        series = Series(media_item_id=show.id)
        db.add(series)
        db.flush()
        season = Season(series_id=series.id, season_number=2)
        db.add(season)
        db.flush()
        for index, media_id in enumerate((ep_old, ep_new), 1):
            db.get(MediaItem, media_id).kind = "episode"
            db.add(Episode(media_item_id=media_id, season_id=season.id, episode_number=index))
        for index, media_id in enumerate((oldest, newest, ep_old, ep_new)):
            db.get(MediaItem, media_id).created_at = now + timedelta(seconds=index)
            db.scalar(select(MediaFile).where(MediaFile.media_item_id == media_id)).modified_ns = 100 - index
        db.commit()
    data = home(context)
    assert [item["id"] for item in data["recent_movies"]] == [newest, oldest]
    assert [item["id"] for item in data["recent_episodes"]] == [ep_new, ep_old]
    latest = data["recent_episodes"][0]
    assert latest["show_title"] == "Local series" and latest["season_number"] == 2 and latest["episode_number"] == 2
    assert latest["added_at"] > data["recent_episodes"][1]["added_at"]


@pytest.mark.parametrize("unavailable", ["file", "item", "analysis", "path", "library", "deleted"])
def test_home_excludes_unavailable_titles_from_every_rail(owner_context, unavailable):
    context, csrf = owner_context
    lib = library(context, csrf)
    media_id = movie(context, lib)
    user_id = context.client.get("/api/v1/auth/me").json()["id"]
    with context.session_factory() as db:
        db.add(WatchProgress(user_id=user_id, media_item_id=media_id, position_seconds=20, duration_seconds=120))
        file = db.scalar(select(MediaFile).where(MediaFile.media_item_id == media_id))
        if unavailable == "file":
            file.available = False
        elif unavailable == "item":
            db.get(MediaItem, media_id).available = False
        elif unavailable == "analysis":
            file.analysis_error = "unreadable"
        elif unavailable == "path":
            db.get(LibraryPath, lib["paths"][0]["id"]).enabled = False
        elif unavailable == "library":
            db.get(Library, lib["id"]).enabled = False
        else:
            db.delete(file)
        db.commit()
    data = home(context)
    assert all(not value for key, value in data.items() if key != "libraries")
    assert len(data["libraries"]) == (0 if unavailable == "library" else 1)


def test_home_continue_uses_incomplete_current_user_progress_and_recent_play_order(owner_context):
    context, csrf = owner_context
    lib = library(context, csrf)
    items = [
        movie(context, lib, name) for name in ("First", "Second", "Completed", "Never started", "At end", "Cleared")
    ]
    owner = context.client.get("/api/v1/auth/me").json()["id"]
    with context.session_factory() as db:
        for index, media_id in enumerate(items):
            db.add(
                WatchProgress(
                    user_id=owner,
                    media_item_id=media_id,
                    position_seconds=[25, 30, 119, 0, 120, 110][index],
                    duration_seconds=120,
                    watched=index == 2,
                    completed_at=utcnow() if index == 5 else None,
                    last_played_at=utcnow() + timedelta(seconds=index),
                )
            )
        db.commit()
    result = home(context)["continue"]
    assert [item["id"] for item in result] == [items[1], items[0]]
    assert result[0]["position_seconds"] == 30 and result[0]["completion"] == 25
    assert result[0]["file_id"] and result[0]["available"]
    create_viewer(context, csrf, [lib["id"]])
    login(context, "viewer")
    assert home(context)["continue"] == []


def test_home_libraries_are_viewer_authorized_unpaginated_and_path_free(owner_context):
    context, csrf = owner_context
    permitted = [library(context, csrf, f"Collection {index:02}") for index in range(27)]
    hidden = library(context, csrf, "Private")
    movie(context, permitted[0])
    private_title = movie(context, hidden, "Private title")
    create_viewer(context, csrf, [entry["id"] for entry in permitted])
    with context.session_factory() as db:
        db.get(Library, permitted[-1]["id"]).enabled = False
        db.get(Library, permitted[1]["id"]).name = "C:\\private\\secret"
        db.commit()
    login(context, "viewer")
    data = home(context)
    assert len(data["libraries"]) == 26
    assert {item["id"] for item in data["libraries"]} == {item["id"] for item in permitted[:-1]}
    assert all(set(item) == {"id", "name", "library_type", "enabled"} for item in data["libraries"])
    assert private_title not in json.dumps(data) and "private\\\\secret" not in json.dumps(data)


def test_home_is_bounded_and_batch_queries_do_not_grow_per_card(owner_context):
    context, csrf = owner_context
    lib = library(context, csrf)
    user_id = context.client.get("/api/v1/auth/me").json()["id"]
    engine = context.session_factory.kw["bind"]

    def measure():
        queries = []

        def capture(_conn, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            return home(context), queries
        finally:
            event.remove(engine, "before_cursor_execute", capture)

    movie(context, lib)
    _, small = measure()
    with context.session_factory() as db:
        for index in range(2000):
            item = MediaItem(
                library_id=lib["id"], kind="movie", title=f"Real database row {index}", sort_title=str(index)
            )
            db.add(item)
            db.flush()
            db.add(
                MediaFile(
                    media_item_id=item.id,
                    library_path_id=lib["paths"][0]["id"],
                    relative_path=f"item-{index}.mp4",
                    size_bytes=32,
                    modified_ns=index + 1,
                    fingerprint="f" * 64,
                    available=True,
                    duration_seconds=120,
                )
            )
            if index < 30:
                db.add(WatchProgress(user_id=user_id, media_item_id=item.id, position_seconds=25, duration_seconds=120))
        db.commit()
    result, large = measure()
    assert len(result["continue"]) == 20 and len(result["recent_movies"]) == 20
    assert len(result["movies"]) == 8
    assert len(large) <= len(small) + 1 and len(large) <= 22
