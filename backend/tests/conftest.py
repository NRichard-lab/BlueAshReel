from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault(
    "APP_SECRET_KEY",
    "test-suite-only-9f8a7b6c5d4e3f2a1B0C!unique-secret",
)

from app.config import AppConfig, get_config
from app.database import Base, create_database_engine, get_db
from app.main import app
from app.services.rate_limit import login_rate_limiter


@dataclass
class TestContext:
    client: TestClient
    session_factory: sessionmaker[Session]
    config: AppConfig
    media_root: Path

    def setup_owner(self) -> tuple[str, dict[str, str]]:
        response = self.client.post(
            "/api/v1/setup/owner",
            json={"username": "owner", "password": "correct horse battery staple"},
        )
        assert response.status_code == 201, response.text
        return response.json()["csrf_token"], dict(response.cookies)


@pytest.fixture
def context(tmp_path: Path) -> Generator[TestContext, None, None]:
    login_rate_limiter.clear()
    media_root = tmp_path / "media"
    media_root.mkdir()
    config = AppConfig(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        app_secret_key="test-secret-key-is-at-least-thirty-two-characters",  # noqa: S106
        app_data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        artwork_dir=tmp_path / "artwork",
        media_roots=str(media_root),
        ffprobe_path="definitely-not-installed-ffprobe",
        ffmpeg_path="definitely-not-installed-ffmpeg",
        scan_batch_size=2,
        job_stale_minutes=1,
        ffprobe_timeout_seconds=1,
    )
    for storage_directory in (config.app_data_dir, config.temp_dir, config.artwork_dir):
        storage_directory.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(config.database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    def override_db() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_config] = lambda: config
    with TestClient(app) as client:
        yield TestContext(client=client, session_factory=factory, config=config, media_root=media_root)
    app.dependency_overrides.clear()
    login_rate_limiter.clear()
    engine.dispose()


@pytest.fixture
def owner_context(context: TestContext) -> tuple[TestContext, str]:
    csrf, _cookies = context.setup_owner()
    return context, csrf
