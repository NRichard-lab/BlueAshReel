from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import TestContext
from sqlalchemy import select

from app.config import AppConfig
from app.models import LibraryPath
from app.services import media_roots as media_root_service
from app.services.media_roots import (
    FolderUnavailable,
    InvalidFolderSelection,
    friendly_path,
    list_folders,
    make_selection_id,
    resolve_selection_id,
    root_state,
    selection_from_path,
)
from app.services.paths import UnsafeMediaPath, is_link_or_reparse, validate_media_directory


def configured(tmp_path: Path, *roots: tuple[str, str, Path]) -> AppConfig:
    return AppConfig(
        _env_file=None,
        app_secret_key="media-root-tests-have-a-unique-long-secret-value",  # noqa: S106
        app_data_dir=tmp_path / "data",
        temp_dir=tmp_path / "temp",
        artwork_dir=tmp_path / "artwork",
        media_root_definitions=json.dumps(
            [{"id": root_id, "display_name": name, "path": str(path)} for root_id, name, path in roots]
        ),
    )


@pytest.fixture(autouse=True)
def native_test_mount_state_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unit-test temporary directories are not Docker media bind mounts. Production
    # rejects an explicit rw mount; these tests exercise the portable unknown state.
    monkeypatch.setattr(media_root_service, "read_only_enforced", lambda _path: None)


def test_multiple_roots_signed_selections_natural_sort_and_directory_only(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    for name in ("Folder 10", "Folder 2", "Folder 1"):
        (primary / name).mkdir()
    (primary / "not-a-folder.mkv").write_bytes(b"fixture")
    config = configured(tmp_path, ("primary", "Media", primary), ("archive", "Archive", secondary))

    assert [root.id for root in config.approved_media_roots] == ["primary", "archive"]
    selection_id = make_selection_id(config.approved_media_roots[0], (), config)
    selection = resolve_selection_id(selection_id, config)
    assert [item.relative_parts[-1] for item in list_folders(selection)] == [
        "Folder 1",
        "Folder 2",
        "Folder 10",
    ]
    assert selection_from_path(str(primary / "Folder 2"), config).relative_parts == ("Folder 2",)

    with pytest.raises(InvalidFolderSelection):
        resolve_selection_id(selection_id[:-1] + ("A" if selection_id[-1] != "A" else "B"), config)
    traversal = make_selection_id(config.approved_media_roots[0], ("..",), config)
    with pytest.raises(InvalidFolderSelection):
        resolve_selection_id(traversal, config)


def test_natural_sort_accepts_unicode_non_decimal_digits(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    for name in ("10", "2", "\u0662", "\u00b2", "\u2460"):
        (root / name).mkdir()
    config = configured(tmp_path, ("primary", "Media", root))
    selection = resolve_selection_id(make_selection_id(config.approved_media_roots[0], (), config), config)

    assert [item.relative_parts[-1] for item in list_folders(selection)] == [
        "2",
        "\u0662",
        "10",
        "\u00b2",
        "\u2460",
    ]


def test_missing_permission_large_and_similar_prefix_states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "media-other"
    outside.mkdir()
    similar_prefix = tmp_path / "media-other-child"
    similar_prefix.mkdir()
    config = configured(tmp_path, ("primary", "Media", root), ("outside", "Other", outside))
    with pytest.raises(UnsafeMediaPath, match="outside the configured"):
        validate_media_directory(str(similar_prefix), config)

    missing = configured(tmp_path, ("missing", "Disconnected", tmp_path / "missing"))
    assert root_state(missing.approved_media_roots[0])[0] == "unavailable"

    real_scandir = media_root_service.os.scandir

    def denied(path: object) -> object:
        if Path(path) == root:
            raise PermissionError("private path must not escape")
        return real_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(media_root_service.os, "scandir", denied)
    assert root_state(config.approved_media_roots[0])[0] == "permission_denied"
    monkeypatch.setattr(media_root_service.os, "scandir", real_scandir)

    for name in ("A", "B", "C"):
        (root / name).mkdir()
    selection = resolve_selection_id(make_selection_id(config.approved_media_roots[0], (), config), config)
    monkeypatch.setattr(media_root_service, "MAX_LISTED_FOLDERS", 2)
    with pytest.raises(FolderUnavailable, match="too many"):
        list_folders(selection)


def test_symlink_and_reparse_entries_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "media"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    config = configured(tmp_path, ("primary", "Media", root))
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation is unavailable on this host")

    root_selection = resolve_selection_id(make_selection_id(config.approved_media_roots[0], (), config), config)
    assert list_folders(root_selection) == []
    with pytest.raises(InvalidFolderSelection, match="Linked"):
        resolve_selection_id(make_selection_id(config.approved_media_roots[0], ("escape",), config), config)

    reparse = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400))
    assert is_link_or_reparse(reparse) is True  # type: ignore[arg-type]


def test_windows_host_path_gets_actionable_container_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "media"
    root.mkdir()
    config = configured(tmp_path, ("primary", "Media", root))
    monkeypatch.setattr("app.services.paths.NATIVE_WINDOWS", False)
    with pytest.raises(UnsafeMediaPath, match="configured as approved media roots"):
        validate_media_directory(r"D:\Media", config)


def test_absolute_unc_drive_and_path_component_attacks(
    owner_context: tuple[TestContext, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    context, csrf = owner_context
    outside = context.media_root.parent / "outside-absolute"
    similar = context.media_root.parent / f"{context.media_root.name}-other"
    outside.mkdir()
    similar.mkdir()
    headers = {"X-CSRF-Token": csrf}

    for raw_path in (str(outside), str(similar), "/etc"):
        response = context.client.post(
            "/api/v1/media-folders/validate", headers=headers, json={"path": raw_path}
        )
        assert response.status_code == 422

    monkeypatch.setattr("app.services.paths.NATIVE_WINDOWS", False)
    for raw_path in (r"D:\Media", r"\\server\private-media"):
        response = context.client.post(
            "/api/v1/media-folders/validate", headers=headers, json={"path": raw_path}
        )
        assert response.status_code == 422
        assert "configured as approved media roots" in response.text

    root = context.config.approved_media_roots[0]
    for parts in (("..",), ("/etc",), (r"\\server\share",), ("C:",), ("bad/name",)):
        forged = make_selection_id(root, parts, context.config)
        response = context.client.post(
            "/api/v1/media-folders/browse",
            headers=headers,
            json={"selection_id": forged},
        )
        assert response.status_code == 422
        assert forged not in response.text

    assert str(outside) not in caplog.text
    assert str(similar) not in caplog.text


def test_empty_root_setup_session_browse_and_atomic_library_completion(context: TestContext) -> None:
    csrf = context.begin_setup()
    assert context.client.get("/api/v1/auth/me").status_code == 401
    roots = context.client.get("/api/v1/media-roots")
    assert roots.status_code == 200
    root = roots.json()["items"][0]
    assert root["internal_path"] is None
    browsed = context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": csrf},
        json={"selection_id": root["selection_id"]},
    )
    assert browsed.status_code == 200, browsed.text
    page = browsed.json()
    assert page["items"] == []
    assert page["total"] == 0
    assert page["current"]["status"] == "available"
    assert page["current"]["readable"] is True

    created_owner = context.client.post(
        "/api/v1/setup/owner",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "owner",
            "password": "correct horse battery staple",
            "initial_library": {
                "name": "Empty first library",
                "library_type": "other",
                "folder_ids": [page["current"]["selection_id"]],
            },
        },
    )
    assert created_owner.status_code == 201, created_owner.text
    assert created_owner.cookies["media_session"]
    assert context.client.get("/api/v1/auth/me").json()["roles"] == ["Owner"]
    libraries = context.client.get("/api/v1/libraries").json()
    assert libraries["total"] == 1
    assert libraries["items"][0]["paths"][0]["path"] == "Media"
    assert context.client.get("/api/v1/setup/status").json()["setup_required"] is False


def test_cursor_is_bound_to_selection_and_rejects_tampering(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    for name in ("Alpha", "Bravo"):
        (context.media_root / name).mkdir()
    headers = {"X-CSRF-Token": csrf}
    root = context.client.get("/api/v1/media-roots").json()["items"][0]
    first = context.client.post(
        "/api/v1/media-folders/browse",
        headers=headers,
        json={"selection_id": root["selection_id"], "page_size": 1},
    ).json()
    cursor = first["next_cursor"]
    child_selection = first["items"][0]["selection_id"]
    assert cursor

    wrong_selection = context.client.post(
        "/api/v1/media-folders/browse",
        headers=headers,
        json={"selection_id": child_selection, "cursor": cursor, "page_size": 1},
    )
    assert wrong_selection.status_code == 422

    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    for invalid in (tampered, "not-a-valid-cursor"):
        response = context.client.post(
            "/api/v1/media-folders/browse",
            headers=headers,
            json={"selection_id": root["selection_id"], "cursor": invalid, "page_size": 1},
        )
        assert response.status_code == 422
        assert invalid not in response.text


def _create_household_user(context: TestContext, csrf: str, username: str, role: str) -> None:
    response = context.client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": username,
            "password": "local validation password",
            "role": role,
            "library_ids": [],
        },
    )
    assert response.status_code == 201, response.text


def _login(context: TestContext, username: str) -> str:
    context.client.cookies.clear()
    response = context.client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "local validation password"},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_folder_api_pagination_selection_library_and_storage(owner_context: tuple[TestContext, str], caplog) -> None:
    context, csrf = owner_context
    for number in range(1, 106):
        (context.media_root / f"Folder {number}").mkdir()
    (context.media_root / "visible-file.mkv").write_bytes(b"not listed")

    roots = context.client.get("/api/v1/media-roots")
    assert roots.status_code == 200, roots.text
    root = roots.json()["items"][0]
    assert root["id"] == "primary"
    assert root["status"] == "available"
    assert root["read_only"] is True
    assert root["internal_path"] == str(context.media_root)

    assert context.client.post(
        "/api/v1/media-folders/browse", json={"selection_id": root["selection_id"]}
    ).status_code == 403
    first = context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": csrf},
        json={"selection_id": root["selection_id"], "page_size": 100},
    )
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["total"] == 105
    assert len(page["items"]) == 100
    assert [item["name"] for item in page["items"][:3]] == ["Folder 1", "Folder 2", "Folder 3"]
    assert all(item["name"] != "visible-file.mkv" for item in page["items"])
    assert page["next_cursor"]
    second = context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": csrf},
        json={
            "selection_id": root["selection_id"],
            "cursor": page["next_cursor"],
            "page_size": 100,
        },
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 5

    selected = page["items"][0]
    created = context.client.post(
        "/api/v1/libraries",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Chosen", "library_type": "movies", "folder_ids": [selected["selection_id"]]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["paths"][0]["path"] == "Media / Folder 1"
    added = context.client.post(
        f"/api/v1/libraries/{created.json()['id']}/paths",
        headers={"X-CSRF-Token": csrf},
        json={"folder_id": page["items"][1]["selection_id"]},
    )
    assert added.status_code == 201, added.text
    assert added.json()["path"] == "Media / Folder 2"
    with context.session_factory() as db:
        stored = db.scalars(select(LibraryPath).order_by(LibraryPath.canonical_path)).all()
        assert {Path(item.canonical_path) for item in stored} == {
            (context.media_root / "Folder 1").resolve(),
            (context.media_root / "Folder 2").resolve(),
        }

    manual = context.client.post(
        "/api/v1/media-folders/validate",
        headers={"X-CSRF-Token": csrf},
        json={"path": str(context.media_root / "Folder 2")},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["display_path"] == "Media / Folder 2"

    storage = context.client.get("/api/v1/media-storage")
    assert storage.status_code == 200
    assert storage.json()["items"][0]["libraries"] == [{"id": created.json()["id"], "name": "Chosen"}]
    for sensitive in (
        str(context.media_root),
        "Folder 1",
        root["selection_id"],
        page["next_cursor"],
        selected["selection_id"],
    ):
        assert sensitive not in caplog.text


def test_secondary_root_library_paths_use_friendly_labels_and_outside_fallback(
    owner_context: tuple[TestContext, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    context, csrf = owner_context
    secondary = context.media_root.parent / "secondary-media"
    selected = secondary / "Movies 2"
    selected.mkdir(parents=True)
    context.config.media_root_definitions = json.dumps(
        [
            {"id": "primary", "display_name": "Media", "path": str(context.media_root)},
            {"id": "secondary", "display_name": "Validation folders", "path": str(secondary)},
        ]
    )
    monkeypatch.setattr(media_root_service, "read_only_enforced", lambda _path: None)
    root = context.config.approved_media_roots[1]
    selection_id = make_selection_id(root, ("Movies 2",), context.config)
    created = context.client.post(
        "/api/v1/libraries",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Secondary collection", "library_type": "movies", "folder_ids": [selection_id]},
    )
    assert created.status_code == 201, created.text
    library_id = created.json()["id"]
    expected = "Validation folders / Movies 2"
    assert created.json()["paths"][0]["path"] == expected
    detail = context.client.get(f"/api/v1/libraries/{library_id}")
    assert detail.status_code == 200
    assert detail.json()["paths"][0]["path"] == expected
    listed = context.client.get("/api/v1/libraries")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["paths"][0]["path"] == expected
    assert friendly_path(context.media_root.parent / "outside-approved-roots", context.config) == (
        "Approved media folder"
    )


def test_root_removal_reports_unavailable_and_recovers(
    owner_context: tuple[TestContext, str], caplog: pytest.LogCaptureFixture
) -> None:
    context, csrf = owner_context
    root = context.client.get("/api/v1/media-roots").json()["items"][0]
    disconnected = context.media_root.with_name(f"{context.media_root.name}-disconnected")
    context.media_root.rename(disconnected)
    try:
        unavailable = context.client.get("/api/v1/media-roots")
        assert unavailable.status_code == 200
        missing_root = unavailable.json()["items"][0]
        assert missing_root["status"] == "unavailable"
        assert missing_root["available"] is False
        assert missing_root["readable"] is False

        failed = context.client.post(
            "/api/v1/media-folders/browse",
            headers={"X-CSRF-Token": csrf},
            json={"selection_id": root["selection_id"]},
        )
        assert failed.status_code == 409
        assert failed.json()["error"]["fields"]["state"] == "unavailable"
        assert str(context.media_root) not in failed.text
        assert root["selection_id"] not in failed.text
    finally:
        disconnected.rename(context.media_root)

    recovered = context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": csrf},
        json={"selection_id": root["selection_id"]},
    )
    assert recovered.status_code == 200, recovered.text
    assert str(context.media_root) not in caplog.text
    assert root["selection_id"] not in caplog.text


def test_child_permission_denied_is_truthful_and_path_private(
    owner_context: tuple[TestContext, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    context, csrf = owner_context
    restricted = context.media_root / "Restricted Collection Secret"
    restricted.mkdir()
    root = context.client.get("/api/v1/media-roots").json()["items"][0]
    real_scandir = media_root_service.os.scandir

    def denied(path: object) -> object:
        if Path(path).resolve(strict=False) == restricted.resolve():
            raise PermissionError("sensitive directory must not be logged")
        return real_scandir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(media_root_service, "_open_relative_directory", lambda _root, _parts: None)
    monkeypatch.setattr(media_root_service.os, "scandir", denied)
    page_response = context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": csrf},
        json={"selection_id": root["selection_id"]},
    )
    assert page_response.status_code == 200, page_response.text
    child = page_response.json()["items"][0]
    assert child["status"] == "permission_denied"
    assert child["available"] is False
    assert child["readable"] is False

    denied_response = context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": csrf},
        json={"selection_id": child["selection_id"]},
    )
    assert denied_response.status_code == 409
    assert denied_response.json()["error"]["fields"]["state"] == "permission_denied"
    assert str(restricted) not in denied_response.text
    assert child["selection_id"] not in denied_response.text
    assert str(restricted) not in caplog.text
    assert "Restricted Collection Secret" not in caplog.text
    assert child["selection_id"] not in caplog.text


def test_explicit_rw_mount_is_reported_and_rejected(
    owner_context: tuple[TestContext, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    context, csrf = owner_context
    root = context.client.get("/api/v1/media-roots").json()["items"][0]
    monkeypatch.setattr("app.api.media_roots.read_only_enforced", lambda _path: False)
    reported = context.client.get("/api/v1/media-storage")
    assert reported.status_code == 200
    assert reported.json()["items"][0]["read_only_enforced"] is False

    monkeypatch.setattr(media_root_service, "read_only_enforced", lambda _path: False)
    rejected = context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": csrf},
        json={"selection_id": root["selection_id"]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["fields"]["state"] == "unavailable"
    assert "not mounted read-only" in rejected.text


def test_explicit_rw_mount_cannot_bypass_policy_via_legacy_library_paths(
    owner_context: tuple[TestContext, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    context, csrf = owner_context
    headers = {"X-CSRF-Token": csrf}
    root = context.client.get("/api/v1/media-roots").json()["items"][0]
    existing = context.client.post(
        "/api/v1/libraries",
        headers=headers,
        json={"name": "Initially empty", "library_type": "other"},
    )
    assert existing.status_code == 201, existing.text
    library_id = existing.json()["id"]
    monkeypatch.setattr(media_root_service, "read_only_enforced", lambda _path: False)

    for reference in (
        {"paths": [str(context.media_root)]},
        {"folder_ids": [root["selection_id"]]},
    ):
        rejected = context.client.post(
            "/api/v1/libraries",
            headers=headers,
            json={"name": "Must not be created", "library_type": "other", **reference},
        )
        assert rejected.status_code == 422, rejected.text
        assert str(context.media_root) not in rejected.text

    for reference in ({"path": str(context.media_root)}, {"folder_id": root["selection_id"]}):
        rejected = context.client.post(
            f"/api/v1/libraries/{library_id}/paths", headers=headers, json=reference
        )
        assert rejected.status_code == 422, rejected.text
        assert str(context.media_root) not in rejected.text

    libraries = context.client.get("/api/v1/libraries").json()["items"]
    assert len(libraries) == 1
    assert libraries[0]["id"] == library_id
    assert libraries[0]["paths"] == []
    with context.session_factory() as db:
        assert db.scalars(select(LibraryPath)).all() == []


def test_folder_api_anonymous_viewer_and_administrator_boundaries(context: TestContext) -> None:
    assert context.client.get("/api/v1/media-roots").status_code == 401
    owner_csrf, _cookies = context.setup_owner()
    _create_household_user(context, owner_csrf, "viewer", "Viewer")
    _create_household_user(context, owner_csrf, "administrator", "Administrator")

    _login(context, "viewer")
    assert context.client.get("/api/v1/media-roots").status_code == 403
    assert context.client.get("/api/v1/media-storage").status_code == 403

    admin_csrf = _login(context, "administrator")
    roots = context.client.get("/api/v1/media-roots")
    assert roots.status_code == 200
    root = roots.json()["items"][0]
    assert root["internal_path"] is None
    browsed = context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": admin_csrf},
        json={"selection_id": root["selection_id"]},
    )
    assert browsed.status_code == 200, browsed.text
    assert browsed.json()["current"]["internal_path"] is None
    assert context.client.get("/api/v1/media-storage").status_code == 403
