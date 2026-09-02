#!/usr/bin/env python3
"""Build the unsigned, offline-capable Windows development installer.

This is a build-time tool. The installed product never downloads or invokes pip,
pnpm, a compiler, Docker, or the development checkout. Existing staging trees are
never replaced; a repeat build uses a newly allocated sibling directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
PACKAGING = Path(__file__).resolve().parent
ARTIFACTS = REPOSITORY / "artifacts" / "native-dev"
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".vite", ".cache"}
NOTICE_PREFIXES = ("license", "licence", "copying", "notice", "copyright")
EMBEDDED_PTH = "python313.zip\n.\nLib/site-packages\n../../backend\n../../.\nimport site\n"


class BuildError(RuntimeError):
    """A failed integrity, isolation, or completeness check."""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def configure_embedded_paths(python_root: Path) -> None:
    # CPython 3.13.15's embedded Windows path normalizer misresolves a terminal
    # ../.. to the runtime directory. The explicit trailing /. reaches the
    # package root, including sibling scripts, without a machine-specific path.
    (python_root / "python313._pth").write_text(EMBEDDED_PTH, encoding="ascii", newline="\n")


def inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved == root.resolve() or not resolved.is_relative_to(root.resolve()):
        raise BuildError(f"Target must be strictly below the build artifact directory: {path}")
    return resolved


def ensure_no_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise BuildError(f"Build payload cannot contain links or junctions: {candidate}")


def load_lock(path: Path = PACKAGING / "components.lock.json") -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise BuildError("Unsupported component lock schema")
    identities: set[str] = set()
    for component in [
        *document["artifacts"], *document["ffmpeg_sources"],
        *document.get("npm_notices", []), *document.get("npm_sources", []),
    ]:
        if component["id"] in identities:
            raise BuildError("Duplicate pinned component identity")
        identities.add(component["id"])
        if not SHA256.fullmatch(component["sha256"]):
            raise BuildError("Every component requires an exact SHA-256 checksum")
        if not component["url"].startswith("https://") or "/latest/" in component["url"]:
            raise BuildError("Component sources must use pinned HTTPS URLs")
        if Path(component["filename"]).name != component["filename"]:
            raise BuildError("Component download names must not contain directories")
    return document


def verify_file(path: Path, expected: str) -> None:
    if not path.is_file() or digest(path) != expected:
        raise BuildError(f"Cryptographic checksum mismatch or missing file: {path.name}")


def download(component: dict[str, Any], destination: Path, *, offline: bool) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target: Path = destination / str(component["filename"])
    ensure_no_links(target)
    if target.exists():
        verify_file(target, component["sha256"])
        return target
    if offline:
        raise BuildError(f"Offline build is missing pinned artifact: {target.name}")
    # A unique partial file is safe to remove on failure; cached files are never overwritten.
    handle, temporary = tempfile.mkstemp(prefix=".download-", suffix=".partial", dir=destination)
    os.close(handle)
    partial = Path(temporary)
    try:
        request = urllib.request.Request(component["url"], headers={"User-Agent": "BlueReel-development-builder/1"})
        with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        verify_file(partial, component["sha256"])
        if target.exists():
            verify_file(target, component["sha256"])
        else:
            partial.rename(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def fresh_stage(preferred: Path, artifact_root: Path = ARTIFACTS) -> Path:
    preferred = inside(preferred, artifact_root)
    ensure_no_links(preferred)
    preferred.parent.mkdir(parents=True, exist_ok=True)
    if preferred.exists():
        return Path(tempfile.mkdtemp(prefix=preferred.name + ".", dir=preferred.parent))
    preferred.mkdir()
    return preferred


def extract_zip(archive: Path, destination: Path, members: dict[str, str] | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            name = entry.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or not path.parts:
                raise BuildError(f"Unsafe archive member in {archive.name}")
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise BuildError(f"Archive links are not allowed: {archive.name}")
            if members is not None and name not in members:
                continue
            relative = members[name] if members is not None else name
            target = inside(destination / relative, destination)
            ensure_no_links(target)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        if members is not None:
            missing = set(members) - set(zipped.namelist())
            if missing:
                raise BuildError(f"Required archive files are absent: {sorted(missing)}")


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise BuildError(f"Required build output directory is missing: {source}")
    ensure_no_links(source)
    destination.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        if entry.name in SKIP_DIRS or entry.name.startswith(".env") or entry.suffix in {".pyc", ".pyo"}:
            continue
        ensure_no_links(entry)
        target = destination / entry.name
        if entry.is_dir():
            copy_tree(entry, target)
        elif entry.is_file():
            shutil.copy2(entry, target)
        else:
            raise BuildError(f"Unsupported payload object: {entry}")


def copy_required(source: Path, target: Path) -> None:
    if not source.is_file():
        raise BuildError(f"Required build input is missing: {source}")
    ensure_no_links(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def run(command: list[str], *, cwd: Path = REPOSITORY, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, check=False)
    if result.returncode:
        raise BuildError(f"Build command failed ({Path(command[0]).name}, exit {result.returncode})")


def normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def requirements_pins(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", line)
        if match:
            current = {"version": match[2], "hashes": set()}
            result[normalized_name(match[1])] = current
        for checksum in re.findall(r"--hash=sha256:([a-f0-9]{64})", line):
            if current is None:
                raise BuildError("Hash without a pinned Python requirement")
            current["hashes"].add(checksum)
    if not result or any(not value["hashes"] for value in result.values()):
        raise BuildError("Python requirements must be exact version pins with hashes")
    return result


def wheel_metadata(wheel: Path) -> tuple[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        matches = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(matches) != 1:
            raise BuildError(f"Wheel must contain one metadata record: {wheel.name}")
        metadata = email.message_from_bytes(archive.read(matches[0]))
        return str(metadata["Name"]), str(metadata["Version"])


def prepare_wheels(lock: Path, wheelhouse: Path, python: str, *, offline: bool) -> dict[str, dict[str, Any]]:
    wheelhouse.mkdir(parents=True, exist_ok=True)
    platform = ["--only-binary=:all:", "--platform", "win_amd64", "--python-version", "313",
                "--implementation", "cp", "--abi", "cp313"]
    arguments = [python, "-m", "pip", "--disable-pip-version-check", "download", "--require-hashes",
                 *platform, "--dest", str(wheelhouse), "-r", str(lock)]
    if offline:
        arguments += ["--no-index", "--find-links", str(wheelhouse)]
    run(arguments)
    pins = requirements_pins(lock)
    artifacts: dict[str, dict[str, Any]] = {}
    for wheel in sorted(wheelhouse.glob("*.whl")):
        name, version = wheel_metadata(wheel)
        pin = pins.get(normalized_name(name))
        checksum = digest(wheel)
        if pin is None or pin["version"] != version or checksum not in pin["hashes"]:
            raise BuildError(f"Wheel is not approved by requirements.lock: {wheel.name}")
        artifacts[normalized_name(name)] = {
            "filename": wheel.name, "sha256": checksum, "version": version,
            "source": "https://pypi.org/project/" + name + "/" + version + "/",
        }
    return artifacts


def install_wheels(lock: Path, wheelhouse: Path, target: Path, python: str) -> None:
    run([python, "-m", "pip", "--disable-pip-version-check", "install", "--no-index",
         "--find-links", str(wheelhouse), "--require-hashes", "--only-binary=:all:",
         "--platform", "win_amd64", "--python-version", "313", "--implementation", "cp", "--abi", "cp313",
         "--no-compile", "--no-warn-script-location", "--target", str(target), "-r", str(lock)])
    # Installed console entry-point launchers embed the builder's Python path.
    # Native services use python -m instead; remove only these newly staged stubs.
    for name in ("bin", "Scripts"):
        candidate = target / name
        if candidate.exists():
            inside(candidate, target)
            ensure_no_links(candidate)
            shutil.rmtree(candidate)


def notice_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name not in SKIP_DIRS and name != "node_modules"]
        base = Path(directory)
        for name in files:
            if name.lower().startswith(NOTICE_PREFIXES):
                found.append(base / name)
    if not found:
        for readme in root.glob("README*"):
            if readme.is_file():
                text = readme.read_text(encoding="utf-8", errors="replace").lower()
                if "permission is hereby granted" in text or "redistribution and use in source and binary" in text:
                    found.append(readme)
    return sorted(found)


def save_notices(files: list[Path], source_root: Path, destination: Path, stage: Path) -> list[str]:
    if not files:
        raise BuildError(f"Required third-party license notice is missing: {source_root.name}")
    copied: list[str] = []
    for source in files:
        target = destination / source.relative_to(source_root)
        copy_required(source, target)
        copied.append(target.relative_to(stage).as_posix())
    return copied


def python_components(site_packages: Path, wheel_artifacts: dict[str, dict[str, Any]], stage: Path) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        metadata = email.message_from_bytes((dist_info / "METADATA").read_bytes())
        name, version = str(metadata["Name"]), str(metadata["Version"])
        pinned = wheel_artifacts.get(normalized_name(name))
        if pinned is None or pinned["version"] != version:
            raise BuildError(f"Installed Python component has no verified wheel: {name}")
        notices = save_notices(
            notice_files(dist_info), dist_info, stage / "licenses" / "python" / (name + "-" + version), stage,
        )
        components.append({
            "ecosystem": "pypi", "name": name, "version": version, "role": "runtime",
            "license": metadata.get("License-Expression") or metadata.get("License") or "See included notices",
            "artifact": pinned, "notices": notices,
        })
    if not components:
        raise BuildError("No installed Python package metadata found")
    return components


def python_upstream_components(sbom: Path, python_archive_sha256: str) -> list[dict[str, Any]]:
    document = json.loads(sbom.read_text(encoding="utf-8"))
    packages = document.get("packages", [])
    cpython = next((package for package in packages if package.get("name") == "CPython"), None)
    if not cpython or not any(
        checksum.get("algorithm") == "SHA256" and checksum.get("checksumValue") == python_archive_sha256
        for checksum in cpython.get("checksums", [])
    ):
        raise BuildError("Upstream CPython SBOM does not describe the pinned embedded distribution")
    return [{
        "ecosystem": "upstream_spdx", "name": package["name"],
        "version": package.get("versionInfo"), "role": "upstream_python_sbom_entry",
        "license": package.get("licenseConcluded", "NOASSERTION"),
        "source": package.get("downloadLocation"), "checksums": package.get("checksums", []),
        "sbom": "licenses/CPython-embedded.spdx.json",
        "note": "Preserved upstream SBOM entry; the upstream document can include source/build dependencies.",
    } for package in packages if package.get("name") != "CPython"]


def pnpm_integrities(lock: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    in_packages, current = False, ""
    for line in lock.read_text(encoding="utf-8").splitlines():
        if line == "packages:":
            in_packages = True
        elif in_packages and line and not line.startswith(" "):
            break
        if not in_packages:
            continue
        match = re.match(r"^  (?:'([^']+)'|([^:\s]+)):$", line)
        if match:
            current = match[1] or match[2]
        integrity = re.search(r"resolution: \{integrity: (sha(?:256|512)-[^,} ]+)", line)
        if integrity and current:
            result[current] = integrity[1]
    if not result:
        raise BuildError("No npm source integrity records found in pnpm-lock.yaml")
    return result


def resolve_npm_dependency(start: Path, name: str) -> Path | None:
    for directory in (start, *start.parents):
        candidate = directory / "node_modules" / name
        if (candidate / "package.json").is_file():
            return candidate.resolve()
    return None


def frontend_package_roots(frontend: Path) -> dict[Path, str]:
    """Exact standalone packages plus conservative production build inputs.

    Browser bundles inline dependencies, so retaining notices for the production
    dependency closure avoids omitting attribution after tree-shaking. Components
    that are merely build inputs are labeled as such, not falsely called runtime.
    """
    standalone = frontend / "dist" / "standalone"
    roots: dict[Path, str] = {}
    for metadata in (standalone / "node_modules").rglob("package.json"):
        data = json.loads(metadata.read_text(encoding="utf-8"))
        if data.get("name") and data.get("version"):
            roots[metadata.parent] = "frontend_runtime"
    project = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    pending: list[Path] = []
    for name in project.get("dependencies", {}):
        found = resolve_npm_dependency(frontend, name)
        if found is None:
            raise BuildError(f"Frontend build dependency is not installed: {name}")
        pending.append(found)
    visited: set[Path] = set()
    while pending:
        root = pending.pop()
        if root in visited:
            continue
        visited.add(root)
        roots[root] = roots.get(root, "frontend_build_input")
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
        optional = data.get("optionalDependencies", {})
        for name in set(data.get("dependencies", {})) | set(optional):
            found = resolve_npm_dependency(root, name)
            if found is None:
                if name in optional:
                    continue
                raise BuildError(f"Frontend dependency graph is incomplete: {data.get('name')} -> {name}")
            pending.append(found)
    return roots


def frontend_components(
    frontend: Path, stage: Path, lock: dict[str, Any], downloads: dict[str, Path],
) -> list[dict[str, Any]]:
    integrities = pnpm_integrities(frontend / "pnpm-lock.yaml")
    components: dict[str, dict[str, Any]] = {}
    for root, role in frontend_package_roots(frontend).items():
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        name, version = str(package["name"]), str(package["version"])
        identity = name + "@" + version
        if identity in components:
            if role == "frontend_runtime":
                components[identity]["role"] = role
            continue
        if identity not in integrities:
            raise BuildError(f"Frontend component lacks a locked source integrity: {identity}")
        destination = stage / "licenses" / "npm" / identity.replace("/", "__")
        files = notice_files(root)
        notices: list[str] = []
        declarations_only = False
        original_sources = [item for item in lock.get("npm_sources", []) if identity in item.get("packages", [])]
        supplements = [item for item in lock.get("npm_notices", []) if identity in item.get("packages", [])]
        if files:
            notices = save_notices(files, root, destination, stage)
        elif supplements:
            for supplement in supplements:
                target = destination / supplement["filename"]
                copy_required(downloads[supplement["id"]], target)
                notices.append(target.relative_to(stage).as_posix())
        elif any(item.get("published_declaration_only") for item in original_sources):
            # Explicit, version-specific exception: preserve the publisher's actual
            # MIT declaration/author data and complete published source, not a
            # fabricated copyright line or a guessed license from another project.
            if package.get("license") != "MIT":
                raise BuildError(f"Published license declaration changed: {identity}")
            declarations_only = True
            originals = [root / "package.json", *root.glob("README*")]
            notices = save_notices([path for path in originals if path.is_file()], root, destination, stage)
        else:
            raise BuildError(f"Required third-party license notice is missing: {identity}")
        components[identity] = {
            "ecosystem": "npm", "name": name, "version": version, "role": role,
            "license": package.get("license") or package.get("licenses") or "See included notices",
            "source_integrity": integrities[identity],
            "source": "https://registry.npmjs.org/" + name + "/-/" + name.rsplit("/", 1)[-1] + "-" + version + ".tgz",
            "notices": notices,
            "upstream_full_license_text_missing": declarations_only,
            "source_archives": ["source/npm/" + item["filename"] for item in original_sources],
        }
    return [components[key] for key in sorted(components)]


def inventory(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        ensure_no_links(path)
        if path.is_file():
            result.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": digest(path)})
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def stage_ffmpeg(source: Path, stage: Path, lock: dict[str, Any]) -> None:
    for component in lock["ffmpeg_sources"]:
        verify_file(source / "source" / component["filename"], component["sha256"])
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        copy_required(source / "bin" / name, stage / "runtime" / "ffmpeg" / name)
    binary_hashes = (source / "source" / "binaries.sha256").read_text(encoding="ascii").splitlines()
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        match = next((line.split()[0] for line in binary_hashes if line.split()[-1].endswith("/" + name)), None)
        if match is None:
            raise BuildError("FFmpeg build lacks recorded binary checksums")
        verify_file(stage / "runtime" / "ffmpeg" / name, match)
    copy_tree(source / "licenses", stage / "licenses" / "ffmpeg")
    copy_tree(source / "source", stage / "source" / "ffmpeg")
    for name in ("build-ffmpeg.sh", "ffmpeg.Dockerfile", "mingw-toolchain.cmake"):
        copy_required(PACKAGING / name, stage / "source" / "ffmpeg" / name)
    if not (stage / "licenses" / "ffmpeg" / "FFmpeg-GPL-3.0.txt").is_file():
        raise BuildError("FFmpeg GPL redistribution notice is missing")


def assert_payload(stage: Path) -> None:
    required = [
        "runtime/python/python.exe", "runtime/python/python313.dll", "runtime/python/python313._pth",
        "runtime/python/vcruntime140.dll", "runtime/python/vcruntime140_1.dll",
        "runtime/python/sqlite3.dll", "runtime/node/node.exe",
        "runtime/ffmpeg/ffmpeg.exe", "runtime/ffmpeg/ffprobe.exe", "runtime/caddy/caddy.exe",
        "services/WinSW.exe", "backend/app/main.py", "backend/alembic.ini",
        "frontend/server.js", "frontend/node_modules/vinext/package.json",
        "scripts/backup.py", "scripts/backup_format.py", "scripts/restore_validate.py",
        "config/product.json", "support/install.ps1", "support/native-guard.cjs",
        "support/maintenance.ps1", "support/development-notice.txt",
    ]
    for name in required:
        if not (stage / name).is_file():
            raise BuildError(f"Native payload is incomplete: {name}")
    if not any((stage / "backend" / "alembic" / "versions").glob("*.py")):
        raise BuildError("Alembic migrations are missing")
    if not (stage / "frontend" / "dist").is_dir():
        raise BuildError("Compiled frontend output is missing")
    for path in stage.rglob("*"):
        ensure_no_links(path)
        if path.name.startswith(".env") or path.suffix in {".sqlite", ".sqlite3", ".db", ".pyc", ".log"}:
            raise BuildError(f"Runtime state or secret configuration must not enter the payload: {path.name}")


def stage_payload(args: argparse.Namespace) -> Path:
    lock = load_lock(args.component_lock)
    if os.name != "nt":
        raise BuildError("Native payload assembly runs on Windows; unit tests are platform-independent")
    downloadable = [*lock["artifacts"], *lock.get("npm_notices", []), *lock.get("npm_sources", [])]
    downloads = {item["id"]: download(item, args.downloads, offline=args.offline) for item in downloadable}
    wheel_artifacts = prepare_wheels(PACKAGING / "requirements.lock", args.wheelhouse, args.python, offline=args.offline)
    if not args.skip_frontend_build:
        environment = dict(os.environ, BLUEREEL_NATIVE_BUILD="1", NEXT_TELEMETRY_DISABLED="1",
                           WRANGLER_SEND_METRICS="false")
        run([args.pnpm, "run", "build"], cwd=REPOSITORY / "frontend", env=environment)
    stage = fresh_stage(args.stage_dir)
    print(f"Staging native payload: {stage}", flush=True)
    python_root = stage / "runtime" / "python"
    extract_zip(downloads["python"], python_root)
    configure_embedded_paths(python_root)
    site_packages = python_root / "Lib" / "site-packages"
    install_wheels(PACKAGING / "requirements.lock", args.wheelhouse, site_packages, args.python)
    node_version = next(item["version"] for item in lock["artifacts"] if item["id"] == "node")
    prefix = "node-v" + node_version + "-win-x64/"
    extract_zip(downloads["node"], stage / "runtime" / "node",
                {prefix + "node.exe": "node.exe", prefix + "LICENSE": "LICENSE"})
    extract_zip(downloads["caddy"], stage / "runtime" / "caddy", {"caddy.exe": "caddy.exe", "LICENSE": "LICENSE"})
    copy_required(downloads["winsw"], stage / "services" / "WinSW.exe")
    copy_required(downloads["winsw_license"], stage / "licenses" / "WinSW-MIT.txt")
    copy_required(python_root / "LICENSE.txt", stage / "licenses" / "CPython-LICENSE.txt")
    copy_required(python_root / "__install__.json", stage / "licenses" / "CPython-install-metadata.json")
    copy_required(downloads["python_sbom"], stage / "licenses" / "CPython-embedded.spdx.json")
    copy_required(stage / "runtime" / "node" / "LICENSE", stage / "licenses" / "Node.js-LICENSE.txt")
    copy_required(stage / "runtime" / "caddy" / "LICENSE", stage / "licenses" / "Caddy-LICENSE.txt")
    for sbom in python_root.glob("*sbom*"):
        if sbom.is_file():
            copy_required(sbom, stage / "licenses" / sbom.name)
    inno_notice = next((path for path in args.iscc.parent.glob("*") if path.name.lower() in {"license.txt", "license.rtf"}), None)
    if inno_notice:
        copy_required(inno_notice, stage / "licenses" / ("Inno-Setup-" + inno_notice.name))
    stage_ffmpeg(args.ffmpeg_dir, stage, lock)
    copy_tree(REPOSITORY / "backend" / "app", stage / "backend" / "app")
    copy_tree(REPOSITORY / "backend" / "alembic", stage / "backend" / "alembic")
    copy_required(REPOSITORY / "backend" / "alembic.ini", stage / "backend" / "alembic.ini")
    for name in ("backup.py", "backup_format.py", "restore_validate.py", "__init__.py"):
        copy_required(REPOSITORY / "scripts" / name, stage / "scripts" / name)
    copy_required(REPOSITORY / "config" / "product.json", stage / "config" / "product.json")
    copy_tree(REPOSITORY / "frontend" / "dist" / "standalone", stage / "frontend")
    for name in ("install.ps1", "native-guard.cjs", "maintenance.ps1", "development-notice.txt"):
        copy_required(PACKAGING / name, stage / "support" / name)
    if (PACKAGING / "service-template.xml").is_file():
        copy_required(PACKAGING / "service-template.xml", stage / "support" / "service-template.xml")
    for source in lock.get("npm_sources", []):
        copy_required(downloads[source["id"]], stage / "source" / "npm" / source["filename"])
    python_pin = next(item["sha256"] for item in lock["artifacts"] if item["id"] == "python")
    components = [*downloadable, *lock["ffmpeg_sources"],
                  *python_upstream_components(downloads["python_sbom"], python_pin),
                  *python_components(site_packages, wheel_artifacts, stage),
                  *frontend_components(REPOSITORY / "frontend", stage, lock, downloads)]
    try:
        from license_sources import stage_additional_components
    except ImportError as error:
        raise BuildError("Caddy/WinSW transitive license helper is required before packaging") from error
    components.extend(stage_additional_components(args.downloads, stage, offline=args.offline))
    for name in ("components.lock.json", "requirements.lock", "build_native.py", "build.ps1",
                 "additional-components.json", "license_sources.py", "installer.iss",
                 "install.ps1", "native-guard.cjs", "maintenance.ps1", "development-notice.txt"):
        copy_required(PACKAGING / name, stage / "source" / "packaging" / name)
    (stage / "OPEN-SOURCE-NOTICES.txt").write_text(
        "BlueReel Development — unsigned local development installer\n\n"
        "Third-party component versions, original source pins, and notice paths are recorded in "
        "included-components.json. Preserve the licenses directory when redistributing this package.\n\n"
        "This FFmpeg build enables GPL and version 3 plus x264. It is distributed under GPL-3.0-or-later. "
        "Complete corresponding source archives and the build controls are included in source/ffmpeg. "
        "The bundled binaries are not presented as LGPL-only. Source media and transcodes stay local.\n\n"
        "Node.js LICENSE and CPython LICENSE.txt contain their upstream third-party notices. "
        "Python wheel and npm notices are preserved individually. Frontend build-input entries "
        "are a conservative attribution set; they are not all independent installed runtimes.\n\n"
        "A small set of upstream MIT packages publishes an SPDX license declaration but no full license text. "
        "Their authentic package metadata, README, and complete published sources are preserved; "
        "included-components.json explicitly marks upstream_full_license_text_missing. "
        "This is a documented upstream packaging limitation, not an assertion of legal certification. "
        "The MPL-2.0 resvg-js source and original notice are included under source/npm and licenses/npm.\n\n"
        "Windows 10 22H2 / Windows 11 provide the .NET Framework runtime used by WinSW. "
        "No additional runtime download is performed by this installer.\n",
        encoding="utf-8",
    )
    assert_payload(stage)
    smoke_environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "TEMP", "TMP"}
    }
    smoke_environment.update({
        "APP_SECRET_KEY": secrets.token_urlsafe(48),
        "DATABASE_URL": "sqlite:///:memory:",
        "LOG_LEVEL": "ERROR",
    })
    run([str(python_root / "python.exe"), "-I", "-B", "-c", (
        "import sqlite3,ssl,fastapi,uvicorn,alembic,sqlalchemy,pydantic,argon2;"
        "import app.main,scripts.backup,scripts.restore_validate;"
        "from pathlib import Path; import sys;"
        "root=Path(sys.executable).resolve().parents[2];"
        "assert Path(scripts.backup.__file__).resolve().is_relative_to(root);"
        "print('Embedded backend, backup and validator imports verified')"
    )], cwd=stage, env=smoke_environment)
    assert_payload(stage)
    run([str(stage / "runtime" / "node" / "node.exe"), "--version"], cwd=stage)
    run([str(stage / "runtime" / "ffmpeg" / "ffmpeg.exe"), "-version"], cwd=stage)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, capture_output=True, text=True, check=False)
    worktree = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=REPOSITORY, capture_output=True, text=True, check=False,
    )
    write_json(stage / "included-components.json", {
        "schema_version": 1, "product": lock["product"], "version": lock["version"],
        "windows_file_version": lock["windows_file_version"], "architecture": "x64",
        "built_at": dt.datetime.now(dt.UTC).isoformat(), "source_revision": revision.stdout.strip(),
        "source_revision_dirty": bool(worktree.stdout.strip()) if worktree.returncode == 0 else None,
        "unsigned": True, "external_prerequisites": [],
        "supported_os": lock["minimum_windows"], "components": components,
        "files": inventory(stage),
        "inventory_note": (
            "This embedded inventory excludes itself to avoid self-hashing. "
            "The sibling .files.json contains hashes of every final payload file including this manifest."
        ),
    })
    write_json(stage.with_name(stage.name + ".files.json"), {"files": inventory(stage)})
    return stage


def compile_installer(stage: Path, args: argparse.Namespace) -> Path:
    if not args.iscc.is_file():
        raise BuildError("Pinned Inno Setup compiler is not installed at the configured build-only location")
    output: Path = Path(args.output_dir).resolve()
    inside(output, REPOSITORY / "artifacts")
    output.mkdir(parents=True, exist_ok=True)
    lock = load_lock(args.component_lock)
    run([
        str(args.iscc), "/DPayloadDir=" + str(stage), "/DOutputDir=" + str(output),
        "/DProductVersion=" + lock["version"], "/DFileVersion=" + lock["windows_file_version"],
        str(PACKAGING / "installer.iss"),
    ])
    installer = output / "BlueReel-Setup-Development-x64.exe"
    if not installer.is_file():
        raise BuildError("Inno Setup did not produce the expected development installer")
    write_json(output / "installer-artifact.json", {
        "path": str(installer), "size": installer.stat().st_size, "sha256": digest(installer),
        "unsigned": True, "payload": str(stage), "version": lock["version"],
        "windows_file_version": lock["windows_file_version"],
    })
    (output / (installer.name + ".sha256")).write_text(digest(installer) + "  " + installer.name + "\n", encoding="ascii")
    return installer


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--python", default=sys.executable, help="Build-only Python with pip; never used by the installer")
    result.add_argument("--pnpm", default="pnpm")
    result.add_argument("--component-lock", type=Path, default=PACKAGING / "components.lock.json")
    result.add_argument("--downloads", type=Path, default=ARTIFACTS / "downloads")
    result.add_argument("--wheelhouse", type=Path, default=ARTIFACTS / "wheels")
    result.add_argument("--ffmpeg-dir", type=Path, default=ARTIFACTS / "ffmpeg")
    result.add_argument("--stage-dir", type=Path, default=ARTIFACTS / "package")
    result.add_argument("--output-dir", type=Path, default=ARTIFACTS)
    result.add_argument("--iscc", type=Path, default=ARTIFACTS / "inno" / "ISCC.exe")
    result.add_argument("--skip-frontend-build", action="store_true")
    result.add_argument("--skip-installer", action="store_true")
    result.add_argument("--offline", action="store_true", help="Require all build inputs already cached and hash-verified")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        stage = stage_payload(args)
        installer = None if args.skip_installer else compile_installer(stage, args)
        print(json.dumps({"payload": str(stage), "installer": str(installer) if installer else None}, indent=2))
    except (BuildError, OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"Native development build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
