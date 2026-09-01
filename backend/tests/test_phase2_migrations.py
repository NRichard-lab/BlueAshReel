from __future__ import annotations

# Synthetic hashes and the fixed table allowlist below are deliberate test fixtures.
# ruff: noqa: S106, S608
from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from alembic import command
from app.config import AppConfig
from app.database import create_database_engine
from app.models import ApplicationSetting, AuditEvent, BackgroundJob, Library, MediaItem, Role, User


def test_populated_phase1_upgrade_and_safe_downgrade(tmp_path: Path) -> None:
    config = AppConfig(
        database_url=f"sqlite:///{tmp_path / 'upgrade.db'}",
        app_secret_key="migration-test-only-ABC123!long-enough-secret",
    )  # noqa: S106
    migration = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    migration.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    with patch("app.config.get_config", return_value=config):
        command.upgrade(migration, "773863f5a6aa")
    engine = create_database_engine(config.database_url)
    with Session(engine) as db:
        owner = User(
            username="fixture owner",
            normalized_username="fixture owner",
            password_hash="test-only-hash",
            roles=[Role(name="Owner")],
        )
        lib = Library(name="Preserved library", library_type="movies")
        db.add_all([owner, lib])
        db.flush()
        item = MediaItem(library_id=lib.id, kind="movie", title="Preserved movie", sort_title="preserved movie")
        db.add_all(
            [
                item,
                ApplicationSetting(key="privacy.local_only", value=True),
                AuditEvent(actor_user_id=owner.id, event_type="fixture"),
                BackgroundJob(job_type="fixture"),
            ]
        )
        db.commit()
        owner_id, library_id, item_id = owner.id, lib.id, item.id
        before = {
            table: db.execute(text(f"SELECT * FROM {table}")).all()
            for table in (
                "users",
                "roles",
                "user_roles",
                "libraries",
                "media_items",
                "application_settings",
                "audit_events",
                "background_jobs",
            )
        }
    with patch("app.config.get_config", return_value=config):
        command.upgrade(migration, "head")
    with engine.connect() as connection:
        for table, rows in before.items():
            assert connection.execute(text(f"SELECT * FROM {table}")).all() == rows
        assert connection.execute(text("SELECT user_id,library_id FROM user_libraries")).one() == (owner_id, library_id)
        assert (
            connection.execute(text("SELECT title FROM media_search WHERE media_search MATCH 'Preserved'")).scalar_one()
            == "Preserved movie"
        )
        connection.execute(text("UPDATE media_items SET title='Changed movie' WHERE id=:id"), {"id": item_id})
        assert (
            connection.execute(text("SELECT title FROM media_search WHERE media_search MATCH 'Changed'")).scalar_one()
            == "Changed movie"
        )
        connection.rollback()
    with patch("app.config.get_config", return_value=config):
        command.downgrade(migration, "773863f5a6aa")
    with Session(engine) as db:
        assert db.scalar(select(User.id)) == owner_id
        assert db.scalar(select(MediaItem.id)) == item_id
        for table, rows in before.items():
            assert db.execute(text(f"SELECT * FROM {table}")).all() == rows
    engine.dispose()
