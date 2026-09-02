from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).parents[1] / "acceptance_api.py"
SPEC = importlib.util.spec_from_file_location("native_acceptance_api", SOURCE)
assert SPEC and SPEC.loader
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080", "http://localhost:28080", "http://example.org:18080", "https://localhost:18080",
    "http://user:secret@localhost:18080", "http://localhost:18080/api", "http://localhost:18080/?token=secret",
])
def test_target_rejects_production_remote_and_credential_urls(url: str) -> None:
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.target_url(url, True)


def test_explicit_disposable_authorization_required() -> None:
    with pytest.raises(acceptance.AcceptanceError, match="Explicit"):
        acceptance.target_url("http://127.0.0.1:18080", False)
    assert acceptance.target_url("http://localhost:18080/", True) == "http://localhost:18080"


def test_private_credentials_never_appear_in_invalid_credential_error(tmp_path: Path) -> None:
    secret = "secret"
    credential_file = tmp_path / "private.json"
    credential_file.write_text(json.dumps({"username": "owner", "password": secret}))
    with pytest.raises(acceptance.AcceptanceError) as caught:
        acceptance.credentials(credential_file)
    assert secret not in str(caught.value)


def test_credentials_use_only_dedicated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLUEREEL_ACCEPTANCE_USERNAME", "test-owner")
    monkeypatch.setenv("BLUEREEL_ACCEPTANCE_PASSWORD", "private-long-test-password")
    assert acceptance.credentials(None) == ("test-owner", "private-long-test-password")


def test_source_loss_restores_generated_fixture_when_request_fails(tmp_path: Path) -> None:
    source = tmp_path / "Source.Loss.2026.mp4"
    source.write_bytes(b"synthetic source only")
    fingerprint = acceptance.sha256(source)
    with (
        pytest.raises(RuntimeError, match="request failed"),
        acceptance.temporarily_missing(source, tmp_path, fingerprint),
    ):
        assert not source.exists()
        raise RuntimeError("request failed")
    assert source.read_bytes() == b"synthetic source only"
    assert not source.with_name(source.name + ".acceptance-held").exists()


@pytest.mark.parametrize("name", ["Family.Movie.mp4", "Source.Loss.2026.mkv"])
def test_source_loss_cannot_mutate_another_filename(tmp_path: Path, name: str) -> None:
    source = tmp_path / name
    source.write_bytes(b"untouched")
    with (
        pytest.raises(acceptance.AcceptanceError, match="exact generated"),
        acceptance.temporarily_missing(source, tmp_path, acceptance.sha256(source)),
    ):
        pytest.fail("Must not enter mutation context")
    assert source.read_bytes() == b"untouched"


def test_source_loss_rejects_changed_fixture_or_existing_backup(tmp_path: Path) -> None:
    source = tmp_path / "Source.Loss.2026.mp4"
    source.write_bytes(b"untouched")
    with (
        pytest.raises(acceptance.AcceptanceError, match="identity changed"),
        acceptance.temporarily_missing(source, tmp_path, "0" * 64),
    ):
        pytest.fail("Must not enter mutation context")
    source.with_name(source.name + ".acceptance-held").write_bytes(b"older source")
    with (
        pytest.raises(acceptance.AcceptanceError, match="already exists"),
        acceptance.temporarily_missing(source, tmp_path, acceptance.sha256(source)),
    ):
        pytest.fail("Must not enter mutation context")


def test_source_loss_rejects_hardlink(tmp_path: Path) -> None:
    source = tmp_path / "Source.Loss.2026.mp4"
    source.write_bytes(b"shared source")
    os.link(source, tmp_path / "another.mp4")
    with (
        pytest.raises(acceptance.AcceptanceError, match="identity changed"),
        acceptance.temporarily_missing(source, tmp_path, acceptance.sha256(source)),
    ):
        pytest.fail("Must not mutate a shared file")


def test_api_refuses_non_api_or_absolute_routes() -> None:
    api = acceptance.Api("http://127.0.0.1:18080")
    for path in ("https://example.com/api/v1/login", "//example.com/api/v1/login", "/", "/api/v1/\r\nInjected"):
        with pytest.raises(acceptance.AcceptanceError):
            api.raw("POST", path, {"password": "never send"})


def test_redirect_handler_never_forwards_credentials() -> None:
    assert acceptance.NoRedirect().redirect_request(None, None, 307, "", {}, "http://example.com") is None


def test_case_evidence_withholds_unexpected_exception_details(tmp_path: Path) -> None:
    harness = object.__new__(acceptance.Harness)
    harness.run_id = "abcdef012345"
    harness.api = acceptance.Api("http://127.0.0.1:18080")
    harness.run_root = tmp_path / "fixtures"
    harness.output = tmp_path
    harness.cases = []

    def failure() -> Any:
        raise ValueError("password=super-private-do-not-log")

    harness.case("safe_error", failure)
    evidence = (tmp_path / "evidence.json").read_text()
    assert "super-private" not in evidence
    assert json.loads(evidence)["cases"][0]["status"] == "failed"


def test_catalog_identifies_scanned_names_and_episode_numbers() -> None:
    assert acceptance.Harness.identify({"title": "Direct Test", "kind": "movie"}) == "direct"
    assert acceptance.Harness.identify({"title": "Episode 2", "kind": "episode", "episode_number": 2}) == "episode2"
    assert acceptance.Harness.identify({"title": "Existing private family library", "kind": "movie"}) is None


def test_fixture_generation_refuses_non_dedicated_root(tmp_path: Path) -> None:
    with pytest.raises(acceptance.AcceptanceError, match="dedicated"):
        acceptance.fixture_tree(tmp_path, "abcdef012345", tmp_path / "ffmpeg.exe")


def test_fixture_generation_never_reuses_existing_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "MEDIA_ROOT", tmp_path)
    run = tmp_path / "Acceptance-abcdef012345"
    run.mkdir()
    marker = run / "keep.txt"
    marker.write_text("original")
    with pytest.raises(FileExistsError):
        acceptance.fixture_tree(tmp_path, "abcdef012345", tmp_path / "ffmpeg.exe")
    assert marker.read_text() == "original"


def test_resume_checkpoint_credits_elapsed_playback_before_seek(monkeypatch: pytest.MonkeyPatch) -> None:
    actions: list[Any] = []

    class FakeApi:
        def json(self, method: str, endpoint: str, data: dict[str, Any]) -> None:
            assert method == "POST" and endpoint == "/api/v1/playback/owned-session/progress"
            actions.append(data)

    monkeypatch.setattr(acceptance.time, "sleep", lambda seconds: actions.append({"wait": seconds}))
    acceptance.record_resume_checkpoint(FakeApi(), "owned-session")
    assert actions == [
        {"position_seconds": 0, "playing": True, "reason": "playing", "sequence": 1},
        {"wait": 2.25},
        {"position_seconds": 2.1, "playing": False, "reason": "pause", "sequence": 2},
        {"position_seconds": 8, "playing": False, "reason": "seek", "sequence": 3},
    ]
