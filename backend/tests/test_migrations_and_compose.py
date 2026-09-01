from __future__ import annotations

from pathlib import Path

import yaml
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.config import get_config


def test_clean_database_migrates_to_head(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_config.cache_clear()
    alembic = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    alembic.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    command.upgrade(alembic, "head")
    inspector = inspect(create_engine(f"sqlite:///{database}"))
    tables = set(inspector.get_table_names())
    assert {
        "alembic_version",
        "users",
        "libraries",
        "media_items",
        "media_files",
        "background_jobs",
        "audit_events",
    } <= tables
    artwork_columns = {column["name"] for column in inspector.get_columns("local_artwork")}
    assert "library_path_id" in artwork_columns
    command.downgrade(alembic, "base")
    command.upgrade(alembic, "head")
    get_config.cache_clear()


def test_compose_has_private_read_only_runtime_foundation() -> None:
    compose_path = Path(__file__).parents[2] / "compose.yml"
    raw = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(raw)
    services = compose["services"]
    assert {"backend", "worker", "frontend", "proxy"} <= set(services)
    assert compose["networks"]["private"]["internal"] is True
    assert "${BIND_ADDRESS:-127.0.0.1}" in raw
    assert "${MEDIA_PATH:?" in raw
    assert "OUTBOUND_INTEGRATIONS_ENABLED: ${OUTBOUND_INTEGRATIONS_ENABLED:-false}" in raw
    assert "PRODUCT_CONFIG_FILE: /app/config/product.json" in raw
    assert "WORKER_POLL_INTERVAL:" in raw
    assert "read_only: true" in raw and "target: /media" in raw
    for service_name in ("backend", "worker", "frontend", "proxy"):
        service = services[service_name]
        assert service["restart"] == "unless-stopped"
        assert "healthcheck" in service
        assert service["logging"]["options"] == {"max-file": "3", "max-size": "10m"}
