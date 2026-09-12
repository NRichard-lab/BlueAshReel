"""Native installer operations. Never reads the repository's Docker environment.

The elevated installer creates the protected data ACL before calling this module.
All operations address one explicitly identified instance; there is no discovery
of household media, Docker databases, credentials, or other installations.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.config import AppConfig, get_product_config
from app.remote.storage import state_write_lock
from app.services.paths import (
    assert_no_link_components,
    is_link_or_reparse,
    native_directory_guard,
    validate_media_directory,
)

ROLES = {"api": "API", "worker": "Worker", "web": "Web", "proxy": "Proxy"}
INSTANCES = {
    "development": (get_product_config().name + " Development", "BlueReelDevelopment", "BlueReel-Development", 18080),
    "stable": (get_product_config().name, "BlueReel", "BlueReel", 8080),
}
STATE_DIRECTORIES = ("configuration", "database", "data", "artwork", "logs", "temp", "backups", "state", "upgrade")
MARKER = ".bluereel-native-instance"


class NativeInstallError(RuntimeError):
    """Redacted installer diagnostic; never includes environment or media paths."""


def write_json(path: Path, value: object) -> None:
    _atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _wait_for_windows_sharing(error: OSError, deadline: float, *, replacing: bool = False) -> bool:
    # MoveFileEx can report ACCESS_DENIED while an existing destination has an
    # open reader, even one sharing delete. Only that operation may retry it;
    # guard acquisition, creation, validation and cleanup still fail closed.
    retryable = {5, 32, 33} if replacing else {32, 33}
    if os.name != "nt" or getattr(error, "winerror", None) not in retryable:
        return False
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(0.025, remaining))
    return True


def _atomic_text(path: Path, value: str) -> None:
    with state_write_lock(path):
        _atomic_text_locked(path, value)


def _atomic_text_locked(path: Path, value: str) -> None:
    # Mutable state can be written by service identities. Never truncate an
    # existing path (or predictable .new sibling) with elevated privileges.
    # Pin ancestors and create a unique file exclusively; replacement changes
    # the directory entry, not the target of a pre-existing hard link.
    candidate: Path | None = None
    prepared = False
    deadline = time.monotonic() + 0.5
    try:
        while True:
            try:
                if not prepared:
                    if candidate is not None:
                        candidate.unlink(missing_ok=True)
                        candidate = None
                    # Other writers' replacements can also briefly conflict
                    # with acquiring the guard or creating the temporary file.
                    with native_directory_guard(path.parent):
                        assert_no_link_components(path.parent)
                        descriptor, name = tempfile.mkstemp(prefix=".bluereel-", dir=path.parent)
                        candidate = Path(name)
                        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                            output.write(value)
                    _private_file(candidate)
                    prepared = True
                # MoveFileEx needs the guard released to open the directory
                # for writing. Replacement never truncates a link's target.
                assert candidate is not None
                try:
                    candidate.replace(path)
                except OSError as error:
                    if _wait_for_windows_sharing(error, deadline, replacing=True):
                        continue
                    raise
                candidate = None
                return
            except OSError as error:
                if not _wait_for_windows_sharing(error, deadline):
                    raise
    finally:
        if candidate is not None:
            cleanup_deadline = time.monotonic() + 0.5
            while True:
                try:
                    candidate.unlink(missing_ok=True)
                    break
                except OSError as error:
                    if not _wait_for_windows_sharing(error, cleanup_deadline):
                        raise


def validate_layout(program: Path, data: Path) -> tuple[Path, Path]:
    for path in (program, data):
        if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
            raise NativeInstallError("Program and data locations must be dedicated absolute directories")
        for component in (*path.parents, path):
            if component.exists() and is_link_or_reparse(component):
                raise NativeInstallError("Program and data locations cannot contain links or junctions")
    program, data = program.resolve(), data.resolve()
    if program == data or program in data.parents or data in program.parents:
        raise NativeInstallError("Program files and persistent data must not overlap")
    return program, data


def validate_bind(address: str) -> str:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError as error:
        raise NativeInstallError("Choose 127.0.0.1 or one specific private IPv4 LAN address") from error
    if parsed == ipaddress.IPv4Address("127.0.0.1"):
        return str(parsed)
    if not any(parsed in ipaddress.IPv4Network(block) for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise NativeInstallError("Public addresses, wildcard binds and router exposure are not supported")
    return str(parsed)


def validate_ports(port: int, bind: str = "127.0.0.1") -> None:
    if not 1024 <= port <= 65533:
        raise NativeInstallError("Select a port between 1024 and 65533; two adjacent internal ports are also reserved")
    listeners: list[socket.socket] = []
    try:
        targets = [("127.0.0.1", port + offset) for offset in range(3)]
        if bind != "127.0.0.1":
            targets.append((bind, port))
        for address, number in targets:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listeners.append(listener)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((address, number))
    except OSError as error:
        raise NativeInstallError(
            "The selected port, an internal adjacent port, or the LAN address is unavailable"
        ) from error
    finally:
        for listener in listeners:
            listener.close()


def _executing_program_dir() -> Path:
    # This module is loaded from the protected package by embedded Python's
    # isolated ._pth. Service-writable metadata is never executable authority.
    source = Path(__file__).absolute()
    assert_no_link_components(source)
    return source.parents[2].resolve()


def _private_file(path: Path) -> None:
    assert_no_link_components(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise NativeInstallError("Native maintenance cannot use linked or non-regular configuration files")


def read_installation(data: Path) -> dict[str, Any]:
    try:
        assert_no_link_components(data)
        _private_file(data / MARKER)
        _private_file(data / "configuration" / "installation.json")
        marker = (data / MARKER).read_text(encoding="utf-8").rstrip("\r\n")
        metadata: dict[str, Any] = json.loads(
            (data / "configuration" / "installation.json").read_text(encoding="utf-8")
        )
        expected = INSTANCES[metadata["instance"]]
        if marker != expected[1] or metadata["service_prefix"] != expected[1]:
            raise ValueError("Instance identity mismatch")
        program, actual_data = validate_layout(Path(metadata["program_dir"]), Path(metadata["data_dir"]))
        if program != _executing_program_dir():
            raise ValueError("Program identity differs from the executing protected installation")
        if actual_data != data.resolve():
            raise ValueError("Data identity mismatch")
        if not program.is_dir():
            raise ValueError("Program directory missing")
        validate_bind(metadata["bind_address"])
        port = int(metadata["port"])
        if not 1024 <= port <= 65533 or metadata["api_port"] != port + 1 or metadata["web_port"] != port + 2:
            raise ValueError("Native port identity is invalid")
        return metadata
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise NativeInstallError("The native installation identity or protected configuration is invalid") from error


def _privileged_configuration(data: Path, metadata: dict[str, Any]) -> AppConfig:
    from app.native_runtime import Installation, load_configuration

    try:
        program = _executing_program_dir()
        environment = data / "configuration" / ".env"
        _private_file(environment)
        values = dotenv_values(environment, interpolate=False)
        if Path(values.get("PRODUCT_CONFIG_FILE") or "") != program / "config/product.json":
            raise ValueError("Product configuration must remain inside the protected package")
        for name in STATE_DIRECTORIES:
            assert_no_link_components(data / name)
            if not (data / name).is_dir():
                raise ValueError("Required private storage is not a directory")
        for path in (program / "runtime/python/python.exe", program / "config/product.json"):
            _private_file(path)
        installation = Installation(
            program_dir=program, data_dir=data, service_prefix=metadata["service_prefix"],
            port=metadata["port"], api_port=metadata["api_port"], web_port=metadata["web_port"],
            bind_address=metadata["bind_address"],
        )
        config = load_configuration(installation)
        # Maintenance has a fixed schema/storage layout; an edited .env cannot
        # redirect elevated database creation or recursive backup operations.
        if (
            config.app_data_dir != data / "data" or config.temp_dir != data / "temp"
            or config.artwork_dir != data / "artwork"
            or Path(config.database_url.removeprefix("sqlite:///")) != data / "database/app.db"
            or config.outbound_integrations_enabled
        ):
            raise ValueError("Private maintenance configuration differs from its installed layout")
        for name in ("app.db", "app.db-wal", "app.db-shm", "app.db-journal"):
            database = data / "database" / name
            if database.exists():
                _private_file(database)
        return config
    except Exception as error:
        raise NativeInstallError("Private native configuration is unsafe for elevated maintenance") from error


def _root_definitions(roots: list[str]) -> str:
    if any(not Path(root).is_absolute() or ".." in Path(root).parts for root in roots):
        raise NativeInstallError("Approved media roots must be absolute paths without parent-directory traversal")
    return json.dumps(
        [
            {
                "id": "root_" + hashlib.sha256(str(Path(root).absolute()).casefold().encode()).hexdigest()[:12],
                "display_name": f"Media {index + 1}",
                "path": str(Path(root).absolute()),
            }
            for index, root in enumerate(roots)
        ]
    )


def _explicit_configuration(values: dict[str, str | None]) -> AppConfig:
    # Every field is supplied explicitly, including defaults. Inherited Docker
    # or developer settings must never bleed into installation maintenance.
    explicit: dict[str, Any] = {}
    for name, field in AppConfig.model_fields.items():
        value = values.get(name.upper())
        if value is not None:
            explicit[name] = value
        elif field.is_required():
            raise NativeInstallError("A required private native setting is missing")
        else:
            explicit[name] = field.get_default(call_default_factory=True)
    return AppConfig(_env_file=None, **explicit)


def _validate_roots(config: AppConfig) -> None:
    try:
        for root in config.approved_media_roots:
            assert_no_link_components(root.path)
            validate_media_directory(str(root.path), config)
    except (OSError, ValueError) as error:
        raise NativeInstallError(
            "An approved root is missing, inaccessible, unsafe, overlapping, or contains a junction"
        ) from error


def change_media(data: Path, roots: list[str]) -> None:
    read_installation(data)
    environment = data / "configuration" / ".env"
    values = dict(dotenv_values(environment, interpolate=False))
    values["MEDIA_ROOT_DEFINITIONS"] = _root_definitions(roots)
    values["MEDIA_ROOTS"] = ""
    _validate_roots(_explicit_configuration(values))
    _atomic_text(environment, "".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}\n" for key, value in values.items() if value is not None
    ))


def change_network(data: Path, bind: str) -> None:
    metadata = read_installation(data)
    address = validate_bind(bind)
    validate_ports(int(metadata["port"]), address)
    metadata["bind_address"] = address
    render_proxy(metadata)
    write_json(data / "configuration" / "installation.json", metadata)


def configure(program: Path, data: Path, instance: str, port: int, bind: str, roots: list[str],
              *, runtime_mode: str = "legacy_service") -> dict[str, Any]:
    program, data = validate_layout(program, data)
    if program != _executing_program_dir():
        raise NativeInstallError("The requested program location differs from the executing protected installation")
    bind = validate_bind(bind)
    if (data / MARKER).exists():
        previous = read_installation(data)
        if previous["instance"] != instance or Path(previous["program_dir"]) != program:
            raise NativeInstallError("Existing data belongs to a different installation; it will not be reused")
        # Repair/upgrade must never generate a new secret or replace the approved roots.
        validate_ports(int(previous["port"]), str(previous["bind_address"]))
        if runtime_mode == "legacy_service":
            render_services(previous)
            render_proxy(previous)
        return previous
    # The elevated wrapper may precreate empty, ACL-protected state folders.
    if data.exists() and any(
        child.name not in STATE_DIRECTORIES or not child.is_dir() or any(child.iterdir()) for child in data.iterdir()
    ):
        raise NativeInstallError(
            f"The data location is not an empty {get_product_config().name} directory; nothing was replaced"
        )
    validate_ports(port, bind)
    product, prefix, _directory, _port = INSTANCES[instance]
    for name in STATE_DIRECTORIES:
        (data / name).mkdir(parents=True, exist_ok=True)
    values: dict[str, str] = {
        "APP_SECRET_KEY": secrets.token_hex(48),
        "APP_DATA_DIR": (data / "data").as_posix(),
        "ARTWORK_DIR": (data / "artwork").as_posix(),
        "TMDB_TOKEN_FILE": (data / "configuration" / "tmdb-access-token.txt").as_posix(),
        "TEMP_DIR": (data / "temp").as_posix(),
        "DATABASE_URL": "sqlite:///" + (data / "database" / "app.db").as_posix(),
        "MEDIA_ROOT_DEFINITIONS": _root_definitions(roots),
        "MEDIA_ROOTS": "",
        "OUTBOUND_INTEGRATIONS_ENABLED": "false",
        "LOCAL_TRANSPORT_ENABLED": "false",
        "LOCAL_TRANSPORT_ADDRESS": "127.0.0.1",
        "LOCAL_TRANSPORT_PORT": "18443",
        "DEPLOYMENT_MODE": "native_windows",
        "WINDOWS_SERVICE_PREFIX": prefix,
        "NATIVE_PROGRAM_DIR": program.as_posix(),
        "NATIVE_DATA_DIR": data.as_posix(),
        "FFMPEG_PATH": (program / "runtime" / "ffmpeg" / "ffmpeg.exe").as_posix(),
        "FFPROBE_PATH": (program / "runtime" / "ffmpeg" / "ffprobe.exe").as_posix(),
        "PRODUCT_CONFIG_FILE": (program / "config" / "product.json").as_posix(),
        "SESSION_COOKIE_NAME": f"{prefix.lower()}_session_{port}",
        "CSRF_COOKIE_NAME": f"csrf_token_{port}",
        "SETUP_COOKIE_NAME": f"{prefix.lower()}_setup_{port}",
        "TRANSCODE_MODE": "automatic",
        "TRANSCODE_MAX_PROCESSES": "2",
        "TRANSCODE_MAX_HEIGHT": "1080",
        "TRANSCODE_MAX_BITRATE_KBPS": "8000",
        "TRANSCODE_MAX_STORAGE_MB": "4096",
        "TRANSCODE_ALLOW_4K": "false",
        "TRANSCODE_CPU_PRESET": "veryfast",
        "TRANSCODE_THREADS": "2",
        "LOG_LEVEL": "INFO",
    }
    config = _explicit_configuration(dict(values))
    _validate_roots(config)
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "instance": instance,
        "product_name": product,
        "service_prefix": prefix,
        "program_dir": str(program),
        "data_dir": str(data),
        "port": port,
        "api_port": port + 1,
        "web_port": port + 2,
        "bind_address": bind,
        "created_at": datetime.now(UTC).isoformat(),
        "strict_local": True,
        "runtime_mode": runtime_mode,
    }
    environment = data / "configuration" / ".env"
    with environment.open("x", encoding="utf-8", newline="\n") as output:
        for key, value in values.items():
            output.write(f"{key}={json.dumps(value, ensure_ascii=False)}\n")
    # This empty, private file is configured only on a fresh installation. A
    # repair returns above, preserving both existing credentials and overrides.
    (data / "configuration" / "tmdb-access-token.txt").touch(exist_ok=False)
    write_json(data / "configuration" / "installation.json", metadata)
    (data / MARKER).write_text(prefix + "\n", encoding="utf-8")
    if runtime_mode == "legacy_service":
        render_services(metadata)
        render_proxy(metadata)
    return metadata


def render_services(metadata: dict[str, Any]) -> None:
    program, data = Path(metadata["program_dir"]), Path(metadata["data_dir"])
    service_dir = program / "services"
    service_dir.mkdir(exist_ok=True)
    for role, suffix in ROLES.items():
        name = str(metadata["service_prefix"]) + suffix
        destination = service_dir / f"{name}.exe"
        shutil.copy2(service_dir / "WinSW.exe", destination)
        root = ET.Element("service")
        fields = {
            "id": name,
            "name": f"{get_product_config().server_name} - {suffix}",
            "description": (
                f"Local-first {get_product_config().name} component; no external runtime is required."
            ),
            "executable": str(program / "runtime" / "python" / "python.exe"),
            # WinSW 2.12 appends common arguments to BOTH startarguments and
            # stoparguments. Duplicating -m in common arguments breaks stop.
            "arguments": f'--role {role} --data-dir "{data}"',
            "startarguments": "-I -B -m app.native_runtime",
            "stoparguments": "-I -B -m app.native_runtime --stop",
            "workingdirectory": str(data),
            "startmode": "Automatic",
            "delayedAutoStart": "true",
            "stoptimeout": "110 sec",
            "stopparentprocessfirst": "true",
            "resetfailure": "1 day",
            "logpath": str(data / "logs"),
        }
        for key, value in fields.items():
            ET.SubElement(root, key).text = value
        account = ET.SubElement(root, "serviceaccount")
        ET.SubElement(account, "domain").text = "NT AUTHORITY"
        ET.SubElement(account, "user").text = "LocalService"
        for delay in ("10 sec", "30 sec"):
            ET.SubElement(root, "onfailure", action="restart", delay=delay)
        ET.SubElement(root, "onfailure", action="none")
        if role in {"worker", "proxy"}:
            ET.SubElement(root, "depend").text = str(metadata["service_prefix"]) + "API"
        if role == "proxy":
            ET.SubElement(root, "depend").text = str(metadata["service_prefix"]) + "Web"
        log = ET.SubElement(root, "log", mode="roll-by-size")
        ET.SubElement(log, "sizeThreshold").text = "1024"
        ET.SubElement(log, "keepFiles").text = "4"
        ET.SubElement(root, "env", name="PYTHONDONTWRITEBYTECODE", value="1")
        ET.SubElement(root, "env", name="PYTHONUNBUFFERED", value="1")
        ET.indent(root)
        ET.ElementTree(root).write(service_dir / f"{name}.xml", encoding="utf-8", xml_declaration=True)


def render_proxy(metadata: dict[str, Any]) -> None:
    addresses = [f"http://127.0.0.1:{metadata['port']}"]
    if metadata["bind_address"] != "127.0.0.1":
        addresses.append(f"http://{validate_bind(metadata['bind_address'])}:{metadata['port']}")
    content = """{
    admin off
    auto_https off
    persist_config off
    log default {
        level ERROR
    }
}
"""
    content += ", ".join(addresses) + " {\n"
    bindings = "127.0.0.1"
    if metadata["bind_address"] != "127.0.0.1":
        bindings += " " + validate_bind(metadata["bind_address"])
    content += f"    bind {bindings}\n"
    csp = (
        "default-src 'self'; base-uri 'self'; connect-src 'self'; font-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; img-src 'self' data: blob:; media-src 'self' blob:; object-src 'none'; "
        "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'"
    )
    content += f'    header {{\n        Content-Security-Policy "{csp}"\n'
    content += """
        -Server
        Cross-Origin-Opener-Policy same-origin
        Referrer-Policy no-referrer
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Permissions-Policy "camera=(), geolocation=(), microphone=()"
    }
    @backend path /api/*
    handle @backend {
"""
    content += f"        reverse_proxy 127.0.0.1:{metadata['api_port']}\n    }}\n"
    content += f"    handle {{\n        reverse_proxy 127.0.0.1:{metadata['web_port']}\n    }}\n}}\n"
    _atomic_text(Path(metadata["data_dir"]) / "configuration" / "Caddyfile", content)


def migrate(data: Path) -> None:
    from alembic.config import Config

    from alembic import command

    metadata = read_installation(data)
    config = _privileged_configuration(data, metadata)
    url = config.database_url
    # env.py uses AppConfig/get_config. Limit it to this installation, not cwd .env.
    known = {name.upper() for name in AppConfig.model_fields}
    for key in list(os.environ):
        if key.upper() in known:
            del os.environ[key]
    for key, value in config.model_dump().items():
        if value is not None:
            os.environ[key.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
    from app.config import get_config

    get_config.cache_clear()
    program = Path(metadata["program_dir"])
    os.environ["PRODUCT_CONFIG_FILE"] = str(program / "config/product.json")
    configuration = Config(str(program / "backend" / "alembic.ini"))
    configuration.set_main_option("script_location", str(program / "backend" / "alembic"))
    configuration.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(configuration, "head")


def backup(data: Path) -> Path:
    metadata = read_installation(data)
    _privileged_configuration(data, metadata)
    program = Path(metadata["program_dir"])
    output = data / "backups"
    before = set(output.glob("*.zip"))
    result = subprocess.run(
        [
            str(program / "runtime" / "python" / "python.exe"),
            "-I",
            "-B",
            "-m",
            "scripts.backup",
            "--env-file",
            str(data / "configuration" / ".env"),
            "--database",
            str(data / "database" / "app.db"),
            "--data",
            str(data / "data"),
            "--artwork",
            str(data / "artwork"),
            "--temporary",
            str(data / "temp"),
            "--config",
            str(program / "config"),
            "--native-config",
            str(data / "configuration"),
            "--output",
            str(output),
            "--retention-days",
            "3650",
        ],
        cwd=data,
        capture_output=True,
        timeout=300,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise NativeInstallError("Backup creation or validation failed; the installation update was not started")
    created = set(output.glob("*.zip")) - before
    if len(created) != 1:
        raise NativeInstallError("The new validated backup could not be identified")
    archive = created.pop()
    from scripts.restore_validate import validate

    validate(archive)
    write_json(data / "state" / "last-backup.json", {"filename": archive.name, "validated": True})
    return archive


def prepare_upgrade(data: Path) -> None:
    metadata = read_installation(data)
    _privileged_configuration(data, metadata)
    maintenance = data / "state" / "maintenance"
    _atomic_text(maintenance, "Native installation maintenance\n")
    try:
        if (data / "database" / "app.db").exists():
            backup(data)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        recovery = data / "upgrade" / f"before-{stamp}"
        # A pristine, separate program copy enables explicit offline recovery.
        # We deliberately do not advertise automatic rollback support.
        shutil.copytree(Path(metadata["program_dir"]), recovery / "program")
        shutil.copytree(data / "configuration", recovery / "configuration")
        write_json(data / "state" / "upgrade.json", {"recovery_directory": recovery.name, "automatic_rollback": False})
    except Exception:
        maintenance.unlink(missing_ok=True)
        raise


def health(data: Path, seconds: int = 60) -> bool:
    metadata = read_installation(data)
    deadline = time.monotonic() + seconds
    url = f"http://127.0.0.1:{int(metadata['port'])}/api/v1/health/ready"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=15) as response:  # noqa: S310 - fixed loopback origin
                payload = json.load(response)
                if response.status == 200 and payload.get("status") in {"ready", "ok"}:
                    (data / "state" / "maintenance").unlink(missing_ok=True)
                    return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"{get_product_config().name} native installation maintenance (Windows administrator)"
    )
    parser.add_argument(
        "command",
        choices=(
            "configure",
            "migrate",
            "backup",
            "prepare-upgrade",
            "health",
            "validate-backup",
            "change-media",
            "change-network",
        ),
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--program-dir", type=Path)
    parser.add_argument("--instance", choices=tuple(INSTANCES), default="development")
    parser.add_argument("--port", type=int)
    parser.add_argument("--bind-address", default="127.0.0.1")
    parser.add_argument("--roots-file", type=Path)
    parser.add_argument("--archive", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command in {"configure", "change-media"}:
            roots = (
                []
                if arguments.roots_file is None
                else [
                    line.strip()
                    for line in arguments.roots_file.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip()
                ]
            )
            if arguments.command == "change-media":
                if arguments.roots_file is None:
                    raise NativeInstallError("An explicit approved-root list is required")
                change_media(arguments.data_dir, roots)
            else:
                if arguments.program_dir is None:
                    raise NativeInstallError("The program location is required")
                configure(
                    arguments.program_dir,
                    arguments.data_dir,
                    arguments.instance,
                    arguments.port or INSTANCES[arguments.instance][3],
                    arguments.bind_address,
                    roots,
                )
        elif arguments.command == "change-network":
            change_network(arguments.data_dir, arguments.bind_address)
        elif arguments.command == "migrate":
            migrate(arguments.data_dir)
        elif arguments.command == "backup":
            backup(arguments.data_dir)
        elif arguments.command == "prepare-upgrade":
            prepare_upgrade(arguments.data_dir)
        elif arguments.command == "health":
            if not health(arguments.data_dir):
                raise NativeInstallError(
                    "Services did not become healthy; user data and recovery backups were preserved"
                )
        elif arguments.command == "validate-backup":
            from scripts.restore_validate import validate

            if arguments.archive is None:
                raise NativeInstallError("A backup archive is required; validation never restores live files")
            validate(arguments.archive)
        print(f"{get_product_config().name} native {arguments.command}: successful")
        return 0
    except Exception as error:
        detail = str(error) if isinstance(error, NativeInstallError) else type(error).__name__
        print(f"{get_product_config().name} native {arguments.command} failed: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
