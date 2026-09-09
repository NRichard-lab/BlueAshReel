"""Upgrade a populated Agent, preserving library/files/history and opaque identity."""

from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from alembic import command
from app.config import AppConfig
from app.database import create_database_engine
from app.models import LocalArtwork, MetadataRecord


def test_metadata_upgrade_preserves_catalog_identity_and_history(tmp_path: Path) -> None:
    config = AppConfig(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'migration.db'}",
        app_secret_key="metadata-migration-only-9d23Abc456!unique-key",  # noqa: S106
    )
    migration = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    migration.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    with patch("app.config.get_config", return_value=config):
        command.upgrade(migration, "2c0100000001")
    engine = create_database_engine(config.database_url)
    with engine.begin() as db:
        db.execute(
            text("""INSERT INTO libraries(id,name,library_type,enabled,created_at,updated_at)
                        VALUES('library','Keep me','movies',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
        db.execute(
            text("""INSERT INTO library_paths(id,library_id,canonical_path,enabled,created_at,updated_at)
                        VALUES('root','library','/private/media',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
        db.execute(
            text("""INSERT INTO media_items(id,library_id,kind,title,sort_title,match_confidence,available,
                        created_at,updated_at) VALUES('media','library','movie','Local title','local title',.7,1,
                        CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
        db.execute(
            text("""INSERT INTO media_files(id,media_item_id,library_path_id,relative_path,size_bytes,modified_ns,
                        fingerprint,available,created_at,updated_at)
                        VALUES('file','media','root','Local title.mkv',100,1,
                        'digest',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
        db.execute(
            text("""INSERT INTO users(id,username,normalized_username,password_hash,is_active,created_at,updated_at)
                        VALUES('owner','Owner','owner','!fixture',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
        db.execute(
            text("""INSERT INTO watch_progress(id,user_id,media_item_id,position_seconds,duration_seconds,
                        watched_seconds,watched,started_at,last_played_at)
                        VALUES('progress','owner','media',42,120,42,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        )
        db.execute(
            text("""INSERT INTO portal_grants(portal_user_id,agent_id,local_user_id,role,enabled,access_version)
                        VALUES('portal-owner','paired-agent','owner','owner',1,1)""")
        )
        db.execute(
            text("""INSERT INTO remote_objects(id,agent_id,kind,local_id,revoked)
                        VALUES('opaque','paired-agent','media','media',0)""")
        )
        preserved = (
            "libraries",
            "library_paths",
            "media_items",
            "media_files",
            "users",
            "watch_progress",
            "portal_grants",
            "remote_objects",
        )
        baseline = {table: db.execute(text(f"SELECT * FROM {table}")).all() for table in preserved}  # noqa: S608
    with patch("app.config.get_config", return_value=config):
        command.upgrade(migration, "head")
    with Session(engine) as db:
        assert db.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
        for table, rows in baseline.items():
            assert db.execute(text(f"SELECT * FROM {table}")).all() == rows  # noqa: S608
        db.add(
            MetadataRecord(
                media_item_id="media",
                kind="movie",
                status="complete",
                provider="tmdb",
                provider_id="603",
                title="The Matrix",
                genres=["Science Fiction"],
                studios=["Warner Bros."],
                external_ids={"tmdb": "603", "imdb": "tt0133093"},
                overview="Fixture metadata",
                manually_confirmed=True,
                match_method="manual",
                match_confidence=1,
            )
        )
        db.add(
            LocalArtwork(
                media_item_id="media",
                library_path_id="root",
                artwork_type="poster",
                source_path="tmdb:/fixture.jpg",
                cached_path="metadata/aa/" + "a" * 64 + ".jpg",
                fingerprint="a" * 64,
                provider="tmdb",
                provider_path="/fixture.jpg",
                content_type="image/jpeg",
            )
        )
        db.commit()
        db.expire_all()
        assert db.query(MetadataRecord).one().external_ids["imdb"] == "tt0133093"
        assert db.query(LocalArtwork).one().provider == "tmdb"
    with patch("app.config.get_config", return_value=config):
        command.downgrade(migration, "2c0100000001")
    with engine.connect() as db:
        for table, rows in baseline.items():
            assert db.execute(text(f"SELECT * FROM {table}")).all() == rows  # noqa: S608
        assert db.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    engine.dispose()


def test_secret_configuration_is_private_and_missing_token_is_safe(tmp_path: Path) -> None:
    token = "fixture_private_token_1234567890"  # noqa: S105
    config = AppConfig(_env_file=None, tmdb_access_token=token)
    assert config.tmdb_token == token
    assert token not in repr(config) and "tmdb_access_token" not in config.model_dump()
    assert AppConfig(_env_file=None, tmdb_access_token="invalid\nheader").tmdb_token == ""  # noqa: S106
    token_file = tmp_path / "token.txt"
    assert AppConfig(_env_file=None, tmdb_token_file=token_file).tmdb_token == ""
    token_file.write_text(token)
    assert AppConfig(_env_file=None, tmdb_token_file=token_file).tmdb_token == token
