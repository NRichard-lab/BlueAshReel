from __future__ import annotations

import pytest
from conftest import TestContext
from sqlalchemy import select, text

from app.models import Episode, MediaFile, MediaItem, Season, Series, VideoStream, WatchProgress


def library(context: TestContext, csrf: str, name: str = "Movies", kind: str = "movies") -> dict:
    path = context.media_root / name
    path.mkdir(exist_ok=True)
    response = context.client.post(
        "/api/v1/libraries",
        headers={"X-CSRF-Token": csrf},
        json={"name": name, "library_type": kind, "paths": [str(path)]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def movie(context: TestContext, lib: dict, title: str = "Local Movie", available: bool = True) -> str:
    with context.session_factory() as db:
        item = MediaItem(library_id=lib["id"], kind="movie", title=title, sort_title=title.lower(), year=2026)
        db.add(item)
        db.flush()
        file = MediaFile(
            media_item_id=item.id,
            library_path_id=lib["paths"][0]["id"],
            relative_path=f"{title}.mp4",
            size_bytes=32,
            modified_ns=1,
            fingerprint="f" * 64,
            container="mov,mp4",
            duration_seconds=120,
            available=available,
        )
        db.add(file)
        db.flush()
        db.add(VideoStream(media_file_id=file.id, stream_index=0, codec="h264", width=1280, height=720))
        db.commit()
        return item.id


def create_viewer(
    context: TestContext, csrf: str, libs: list[str], username: str = "viewer", role: str = "Viewer"
) -> dict:
    response = context.client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf},
        json={"username": username, "password": "local validation password", "role": role, "library_ids": libs},
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(context: TestContext, username: str, password: str = "local validation password") -> str:  # noqa: S107
    context.client.cookies.clear()
    response = context.client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_catalog_assignments_search_and_redaction(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    first = library(context, csrf)
    second = library(context, csrf, "Private")
    allowed = movie(context, first)
    hidden = movie(context, second, "Secret Movie")
    create_viewer(context, csrf, [first["id"]])
    viewer_csrf = login(context, "viewer")
    for suffix in ("/browse/media", "/browse/media?q=2026", "/browse/media?q=local"):
        response = context.client.get("/api/v1" + suffix)
        assert response.status_code == 200, response.text
        assert [i["id"] for i in response.json()["items"]] == [allowed]
        assert str(context.media_root) not in response.text
    assert context.client.get(f"/api/v1/browse/media/{hidden}").status_code == 404
    assert context.client.get("/api/v1/media").status_code == 403
    assert context.client.get("/api/v1/users").status_code == 403
    assert context.client.get("/api/v1/browse/home").status_code == 200
    assert context.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": viewer_csrf}).status_code == 204


def test_progress_watch_isolation_and_csrf(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    lib = library(context, csrf)
    item = movie(context, lib)
    create_viewer(context, csrf, [lib["id"]])
    assert context.client.put(f"/api/v1/browse/media/{item}/watched", json={"watched": True}).status_code == 403
    response = context.client.put(
        f"/api/v1/browse/media/{item}/watched", headers={"X-CSRF-Token": csrf}, json={"watched": True}
    )
    assert response.status_code == 200, response.text
    assert context.client.get("/api/v1/browse/home").json()["recent_watched"][0]["id"] == item
    login(context, "viewer")
    assert not context.client.get(f"/api/v1/browse/media/{item}").json()["watched"]
    assert context.client.get("/api/v1/browse/home").json()["recent_watched"] == []


def test_admin_boundaries_disable_revoke_and_last_owner(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    owner = context.client.get("/api/v1/auth/me").json()
    lib = library(context, csrf)
    movie(context, lib)
    admin = create_viewer(context, csrf, [], "administrator", "Administrator")
    assert (
        context.client.patch(
            f"/api/v1/users/{owner['id']}", headers={"X-CSRF-Token": csrf}, json={"role": "Viewer"}
        ).status_code
        == 409
    )
    assert (
        context.client.delete(
            f"/api/v1/users/{owner['id']}?delete_history=true", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 409
    )
    admin_csrf = login(context, "administrator")
    assert context.client.get("/api/v1/media").json()["items"] == []
    assert context.client.get("/api/v1/libraries").status_code == 200
    assert (
        context.client.patch(
            "/api/v1/privacy", headers={"X-CSRF-Token": admin_csrf}, json={"local_only": False}
        ).status_code
        == 403
    )
    assert (
        context.client.patch(
            f"/api/v1/users/{owner['id']}", headers={"X-CSRF-Token": admin_csrf}, json={"is_active": False}
        ).status_code
        == 403
    )
    cookies = dict(context.client.cookies)
    csrf = login(context, "owner", "correct horse battery staple")
    assert (
        context.client.patch(
            f"/api/v1/users/{admin['id']}", headers={"X-CSRF-Token": csrf}, json={"is_active": False}
        ).status_code
        == 200
    )
    context.client.cookies.clear()
    context.client.cookies.update(cookies)
    assert context.client.get("/api/v1/auth/me").status_code == 401


def test_tv_hierarchy_combined_search_and_availability(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    lib = library(context, csrf, "Television", "tv")
    episode_id = movie(context, lib, "A local episode")
    with context.session_factory() as db:
        show = MediaItem(library_id=lib["id"], kind="series", title="Test Show", sort_title="test show")
        db.add(show)
        db.flush()
        series = Series(media_item_id=show.id)
        db.add(series)
        db.flush()
        season = Season(series_id=series.id, season_number=1)
        db.add(season)
        db.flush()
        item = db.get(MediaItem, episode_id)
        assert item
        item.kind = "episode"
        db.add(Episode(media_item_id=episode_id, season_id=season.id, episode_number=2))
        db.commit()
        show_id, season_id = show.id, season.id
    assert context.client.get("/api/v1/browse/media?kind=series&available=true").json()["items"][0]["id"] == show_id
    for query in ("S01E02", "Test Show S01E02", "Test Show"):
        response = context.client.get("/api/v1/browse/media", params={"q": query})
        assert response.status_code == 200, response.text
        assert episode_id in [i["id"] for i in response.json()["items"]]
    assert context.client.get(f"/api/v1/browse/shows/{show_id}/seasons").json()["items"][0]["id"] == season_id
    assert context.client.get(f"/api/v1/browse/seasons/{season_id}/episodes").json()["items"][0]["id"] == episode_id
    assert context.client.get(f"/api/v1/browse/media/{show_id}/next").json()["item"]["id"] == episode_id


@pytest.mark.parametrize("delete_history", [True, False])
def test_delete_account_history_choice(owner_context: tuple[TestContext, str], delete_history: bool) -> None:
    context, csrf = owner_context
    lib = library(context, csrf)
    item = movie(context, lib)
    user = create_viewer(context, csrf, [lib["id"]])
    with context.session_factory() as db:
        db.add(WatchProgress(user_id=user["id"], media_item_id=item))
        db.commit()
    assert context.client.delete(f"/api/v1/users/{user['id']}", headers={"X-CSRF-Token": csrf}).status_code == 422
    assert (
        context.client.delete(
            f"/api/v1/users/{user['id']}?delete_history={str(delete_history).lower()}", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 204
    )
    with context.session_factory() as db:
        state = db.scalar(select(WatchProgress))
        assert (state is None) == delete_history
        if state:
            assert state.user_id is None


def test_disabled_library_and_manager_paths(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    lib = library(context, csrf)
    item = movie(context, lib)
    for url in ("/libraries", "/settings", "/dashboard", f"/browse/media/{item}"):
        response = context.client.get("/api/v1" + url)
        assert response.status_code == 200
        for path in [context.media_root, context.config.app_data_dir, context.config.temp_dir]:
            assert str(path).replace("\\", "\\\\") not in response.text
    context.client.patch(f"/api/v1/libraries/{lib['id']}", headers={"X-CSRF-Token": csrf}, json={"enabled": False})
    assert context.client.get(f"/api/v1/browse/media/{item}").status_code == 404
    assert context.client.get("/api/v1/browse/media").json()["total"] == 0


def test_search_index_and_bounded_large_catalog(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    lib = library(context, csrf)
    with context.session_factory() as db:
        db.add_all(
            MediaItem(library_id=lib["id"], kind="movie", title=f"Fixture {n}", sort_title=f"fixture {n}")
            for n in range(1000)
        )
        db.commit()
        plan = db.execute(
            text("EXPLAIN QUERY PLAN SELECT rowid FROM media_search WHERE media_search MATCH 'Fixture'")
        ).all()
        assert any("VIRTUAL TABLE INDEX" in str(row) for row in plan)
    response = context.client.get("/api/v1/browse/media?q=Fixture&page=2&page_size=24")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1000
    assert len(response.json()["items"]) == 24
