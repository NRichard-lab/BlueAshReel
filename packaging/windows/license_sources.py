"""Stage checksum-pinned Caddy/Go/WinSW dependency notices and source evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

LOCK = Path(__file__).with_name("additional-components.json")
NOTICE_PREFIXES = ("license", "licence", "copying", "notice", "copyright", "patents", "authors")


class LicenseSourceError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def no_links(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
            raise LicenseSourceError("License-source paths cannot contain links or junctions")


def read_lock(path: Path = LOCK) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise LicenseSourceError("Unsupported additional-component lock version")
    identities: set[str] = set()
    for artifact in document["artifacts"]:
        if artifact["id"] in identities or not re.fullmatch(r"[a-z0-9_]+", artifact["id"]):
            raise LicenseSourceError("Duplicate or invalid additional component identifier")
        identities.add(artifact["id"])
        if (
            not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
            or not artifact["url"].startswith("https://") or "/latest/" in artifact["url"]
            or Path(artifact["filename"]).name != artifact["filename"]
        ):
            raise LicenseSourceError("Every license-source artifact requires a pinned HTTPS source and checksum")
    return document


def fetch(artifact: dict[str, Any], directory: Path, offline: bool) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / str(artifact["filename"])
    no_links(target)
    if not target.exists():
        if offline:
            raise LicenseSourceError(f"Missing offline license source: {target.name}")
        descriptor, name = tempfile.mkstemp(prefix=".license-download-", suffix=".partial", dir=directory)
        os.close(descriptor)
        partial = Path(name)
        try:
            request = urllib.request.Request(artifact["url"], headers={"User-Agent": "BlueReel-native-licensing/1"})
            with urllib.request.urlopen(request, timeout=90) as source, partial.open("wb") as output:
                shutil.copyfileobj(source, output)
            if sha256(partial) != artifact["sha256"]:
                raise LicenseSourceError(f"License-source download checksum mismatch: {target.name}")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    if sha256(target) != artifact["sha256"]:
        raise LicenseSourceError(f"License-source checksum mismatch: {target.name}")
    if expected := artifact.get("upstream_sha512"):
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha512").hexdigest() != expected:
                raise LicenseSourceError("Published source SHA-512 checksum mismatch")
    return target


def safe_member(name: str) -> PurePosixPath:
    relative = PurePosixPath(name)
    if (
        not relative.parts or relative.is_absolute() or ".." in relative.parts
        or "\\" in name or ":" in name or "\x00" in name
    ):
        raise LicenseSourceError("Unsafe license archive member")
    return relative


def copy_file(source: Path, destination: Path) -> None:
    no_links(source)
    no_links(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(source) != sha256(destination):
            raise LicenseSourceError("A different license-source payload already exists")
        return
    shutil.copy2(source, destination)


def _write_notice(destination: Path, name: str, payload: bytes) -> str:
    target = destination.joinpath(*safe_member(name).parts)
    no_links(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise LicenseSourceError("A different extracted license notice already exists")
    else:
        target.write_bytes(payload)
    return target.as_posix()


def extract_notices(archive: Path, destination: Path, *, metadata: bool = False) -> dict[str, bytes]:
    """Read selected regular files directly; never extract a whole untrusted tree."""
    notices: dict[str, bytes] = {}

    def wanted(name: str) -> bool:
        base = PurePosixPath(name).name.lower()
        return base.startswith(NOTICE_PREFIXES) or metadata and base.endswith(".nuspec")

    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as source:
            for member in source:
                safe_member(member.name)
                if member.isfile() and wanted(member.name):
                    if member.size > 2 * 1024 * 1024:
                        raise LicenseSourceError("An upstream notice exceeds the safe extraction limit")
                    stream = source.extractfile(member)
                    if stream is None:
                        raise LicenseSourceError("A required source notice could not be read")
                    payload = stream.read()
                    _write_notice(destination, member.name, payload)
                    notices[member.name] = payload
    else:
        with zipfile.ZipFile(archive) as zipped:
            for entry in zipped.infolist():
                safe_member(entry.filename)
                if entry.is_dir() or not wanted(entry.filename):
                    continue
                if entry.file_size > 2 * 1024 * 1024 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise LicenseSourceError("An upstream notice is unsafe")
                payload = zipped.read(entry)
                _write_notice(destination, entry.filename, payload)
                notices[entry.filename] = payload
    return notices


def identify_license(contents: bytes) -> str:
    text = contents.decode("utf-8-sig", errors="replace").lower()
    if "cc0 1.0 universal" in text:
        return "CC0-1.0"
    if "mozilla public license" in text:
        return "MPL-2.0"
    if "apache license" in text and "version 2.0" in text:
        return "Apache-2.0"
    if "permission is hereby granted" in text:
        return "MIT"
    if "redistribution and use in source and binary forms" in text:
        return "BSD-3-Clause" if "neither the name" in text or "may not be used to endorse" in text else "BSD-2-Clause"
    if "permission to use, copy, modify" in text and "for any purpose" in text:
        return "ISC"
    raise LicenseSourceError("A component license requires explicit review")


def caddy_components(
    sbom: dict[str, Any], notices: dict[str, bytes], stage: Path, artifacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    binary = stage / "runtime/caddy/caddy.exe"
    expected = sbom["metadata"]["component"]["version"].removeprefix("sha256:")
    if not binary.is_file() or sha256(binary) != expected:
        raise LicenseSourceError("Caddy binary does not match the exact upstream Windows SBOM")
    components: list[dict[str, Any]] = []
    for component in sbom["components"]:
        if not component.get("purl", "").startswith("pkg:golang/"):
            continue
        name = component["name"]
        if name == "stdlib":
            source = artifacts["go_source"]
            license_name = "BSD-3-Clause and included third-party notices"
            paths = ["licenses/native/go_source/go/LICENSE", "licenses/native/go_source/go/PATENTS"]
        else:
            source = artifacts["caddy_buildable_source"]
            prefix = "" if name == "caddy" else f"vendor/{name}/"
            own = [
                path for path in notices
                if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
                and PurePosixPath(path).name.lower().startswith(("license", "copying"))
            ]
            if not own:
                raise LicenseSourceError(f"Missing root license for compiled Caddy module: {name}")
            try:
                licenses = sorted({identify_license(notices[path]) for path in own})
            except LicenseSourceError as exc:
                raise LicenseSourceError(f"Unreviewed Caddy module license: {name}") from exc
            license_name = " AND ".join(licenses)
            paths = [f"licenses/native/caddy_buildable_source/{path}" for path in notices if path.startswith(prefix)]
        properties = {item["name"]: item["value"] for item in component.get("properties", [])}
        components.append({
            "name": name, "version": component["version"], "purl": component["purl"],
            "role": "embedded_caddy_runtime", "license": license_name, "notices": sorted(paths),
            "source_artifact": source, "source_archive": f"source/native/{source['filename']}",
            "upstream_module_digest": properties.get("syft:metadata:h1Digest"),
        })
    if len(components) != 147:
        raise LicenseSourceError("Pinned Caddy SBOM component count changed")
    return components


def stage_additional_components(downloads: Path, stage: Path, *, offline: bool = False) -> list[dict[str, Any]]:
    """Builder contract: called after Caddy staging; returns manifest component rows."""
    no_links(stage)
    lock = read_lock()
    artifacts = {item["id"]: item for item in lock["artifacts"]}
    cached: dict[str, Path] = {}
    notices: dict[str, dict[str, bytes]] = {}
    rows: list[dict[str, Any]] = []
    for identity, artifact in artifacts.items():
        cached[identity] = fetch(artifact, downloads, offline)
        row = dict(artifact)
        target = stage / "licenses/native" / identity
        if artifact["distribution"] == "notice":
            copy_file(cached[identity], target / artifact["filename"])
            row["notices"] = [f"licenses/native/{identity}/{artifact['filename']}"]
        else:
            notices[identity] = extract_notices(cached[identity], target, metadata=True)
            if not notices[identity]:
                raise LicenseSourceError(f"No source notices or package evidence found for {identity}")
            row["notices"] = [f"licenses/native/{identity}/{name}" for name in sorted(notices[identity])]
            if artifact["distribution"] == "source_and_notices":
                copy_file(cached[identity], stage / "source/native" / artifact["filename"])
                row["source_archive"] = f"source/native/{artifact['filename']}"
        rows.append(row)
    sbom = json.loads(cached["caddy_windows_sbom"].read_text(encoding="utf-8"))
    rows.extend(caddy_components(sbom, notices["caddy_buildable_source"], stage, artifacts))
    for component in lock["winsw_merged_components"]:
        source = artifacts[component["source_artifact"]]
        rows.append({
            **component, "role": "embedded_winsw_runtime", "artifact": source,
            "notices": [f"licenses/native/{source['id']}/{name}" for name in sorted(notices[source["id"]])],
        })
    for source in (LOCK, Path(__file__)):
        copy_file(source, stage / "source/packaging" / source.name)
    return rows
