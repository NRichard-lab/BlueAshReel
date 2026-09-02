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
    assert compose["x-common-environment"]["MEDIA_ROOTS"] == "${MEDIA_ROOTS:-/media}"
    assert compose["x-common-environment"]["MEDIA_ROOT_DEFINITIONS"] == "${MEDIA_ROOT_DEFINITIONS:-}"
    for name, default in {
        "TRANSCODE_MODE": "automatic",
        "TRANSCODE_CPU_PRESET": "veryfast",
        "TRANSCODE_ALLOW_4K": "false",
        "TRANSCODE_DEVICE": "auto",
    }.items():
        assert compose["x-common-environment"][name] == f"${{{name}:-{default}}}"
    assert "OUTBOUND_INTEGRATIONS_ENABLED: ${OUTBOUND_INTEGRATIONS_ENABLED:-false}" in raw
    assert "PRODUCT_CONFIG_FILE: /app/config/product.json" in raw
    assert "WORKER_POLL_INTERVAL:" in raw
    assert "read_only: true" in raw and "target: /media" in raw
    for service_name in ("backend", "worker", "frontend", "proxy"):
        service = services[service_name]
        assert service["restart"] == "unless-stopped"
        assert "healthcheck" in service
        assert service["logging"]["options"] == {"max-file": "3", "max-size": "10m"}
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cpus"] and service["mem_limit"]
        assert 0 < service["pids_limit"] <= 256
    assert services["proxy"]["init"] is False
    assert services["proxy"]["healthcheck"]["test"] == ["CMD", "/usr/local/bin/proxy-health"]
    assert services["frontend"]["environment"]["CLOUDFLARE_CF_FETCH_ENABLED"] == "false"
    assert any(mount.startswith("/app/dist/server/.wrangler:") for mount in services["frontend"]["tmpfs"])
    assert all(services[name]["networks"] == ["private"] for name in ("backend", "worker", "frontend"))
    assert services["proxy"]["networks"] == ["private", "ingress"]
    assert "network_mode" not in services["proxy"]
    assert "ingress" not in services  # No split namespace/port-owner lifecycle.
    assert services["proxy"]["dns"] == ["127.0.0.1"]
    assert services["proxy"]["cap_add"] == ["NET_ADMIN", "SETUID", "SETGID", "SETPCAP"]
    assert not services["proxy"].get("privileged", False)


def test_docker_context_excludes_native_state_and_nested_secrets() -> None:
    root = Path(__file__).parents[2]
    patterns = set((root / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {
        "backend/data", "runtime", "media", "backups", ".bluereel", "compose.override.yml",
        "compose.override.yaml", "**/.env", "**/.env.*", "**/*.env",
        "**/*.db", "**/*.db-*", "**/*.sqlite", "**/*.sqlite-*", "**/*.sqlite3", "**/*.sqlite3-*",
        "**/*.log", "**/.coverage", "**/node_modules", "**/dist", "**/ffmpeg.exe",
    } <= patterns


def test_frontend_image_supports_workerd_without_runtime_metadata_fetch() -> None:
    dockerfile = (Path(__file__).parents[2] / "docker/frontend.Dockerfile").read_text(encoding="utf-8")
    assert "FROM node:22-bookworm-slim AS build" in dockerfile
    assert "FROM node:22-bookworm-slim AS runtime" in dockerfile
    assert "CLOUDFLARE_CF_FETCH_ENABLED=false" in dockerfile


def test_proxy_removes_unneeded_privileged_port_capability_at_build_time() -> None:
    dockerfile = (Path(__file__).parents[2] / "docker/proxy.Dockerfile").read_text(encoding="utf-8")
    assert "RUN setcap -r /usr/bin/caddy" in dockerfile
    assert '"/usr/local/bin/proxy-entrypoint"' in dockerfile


def test_proxy_runtime_logs_remove_request_and_path_fields() -> None:
    config = (Path(__file__).parents[2] / "docker/Caddyfile").read_text(encoding="utf-8")
    for field in ("request", "file", "storage", "err_trace"):
        assert f"{field} delete" in config


def test_ingress_fails_closed_and_drops_setup_privileges() -> None:
    entrypoint = (Path(__file__).parents[2] / "docker/proxy-entrypoint.sh").read_text(encoding="utf-8")
    assert entrypoint.index("iptables -P OUTPUT DROP") < entrypoint.index("getent hosts backend")
    assert "ip6tables -P OUTPUT DROP" in entrypoint
    assert '--dport 8000 -j ACCEPT' in entrypoint and '--dport 3000 -j ACCEPT' in entrypoint
    assert "--bounding-set=-all" in entrypoint and "--reuid=1000" in entrypoint
    assert "iptables -F OUTPUT" in entrypoint and "iptables -D OUTPUT -o lo -j ACCEPT" in entrypoint
    assert '/sbin/tini -- "$@"' in entrypoint
    health = (Path(__file__).parents[2] / "docker/proxy-health.sh").read_text(encoding="utf-8")
    assert "/proc/1/status" in health
    assert all(field in health for field in ("Uid", "CapEff", "CapBnd", "CapPrm", "CapAmb", "NoNewPrivs"))
    assert "--bounding-set=-all" in health
