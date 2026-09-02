"""Offline checks for exact-source notice staging and archive containment."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "license_sources.py"
SPEC = importlib.util.spec_from_file_location("bluereel_license_sources", SOURCE)
assert SPEC and SPEC.loader
licensing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(licensing)


def test_additional_lock_has_exact_compiled_components() -> None:
    lock = licensing.read_lock()
    assert {row["id"] for row in lock["artifacts"]} == {
        "caddy_windows_sbom", "caddy_buildable_source", "go_source", "winsw_source",
        "log4net_nuget", "log4net_source", "yamldotnet_nuget",
    }
    assert len(lock["winsw_merged_components"]) == 4


def test_offline_source_cache_rejects_corruption(tmp_path: Path) -> None:
    content = b"original archive"
    item = {"filename": "fixture.zip", "sha256": hashlib.sha256(content).hexdigest()}
    with pytest.raises(licensing.LicenseSourceError, match="Missing offline"):
        licensing.fetch(item, tmp_path, offline=True)
    source = tmp_path / "fixture.zip"
    source.write_bytes(content)
    assert licensing.fetch(item, tmp_path, offline=True) == source
    source.write_bytes(b"tampered")
    with pytest.raises(licensing.LicenseSourceError, match="checksum mismatch"):
        licensing.fetch(item, tmp_path, offline=True)
    assert source.read_bytes() == b"tampered"


@pytest.mark.parametrize("path", ["../LICENSE", "/LICENSE", "C:/LICENSE", r"..\LICENSE", "notice\x00"])
def test_archive_member_paths_must_remain_relative(path: str) -> None:
    with pytest.raises(licensing.LicenseSourceError):
        licensing.safe_member(path)


def test_selected_notices_preserve_bytes_and_do_not_extract_code(tmp_path: Path) -> None:
    source = tmp_path / "fixture.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("source/LICENSE", b"exact original notice\r\n")
        archive.writestr("source/main.go", b"source code stays in the source archive")
    target = tmp_path / "notices"
    notices = licensing.extract_notices(source, target)
    assert notices == {"source/LICENSE": b"exact original notice\r\n"}
    assert (target / "source" / "LICENSE").read_bytes() == notices["source/LICENSE"]
    assert not (target / "source" / "main.go").exists()


def test_notice_symlink_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "fixture.zip"
    entry = zipfile.ZipInfo("LICENSE")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(entry, "../other")
    with pytest.raises(licensing.LicenseSourceError, match="unsafe"):
        licensing.extract_notices(source, tmp_path / "notices")


def test_tar_notice_path_traversal_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "fixture.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo("../LICENSE")
        member.size = 7
        archive.addfile(member, io.BytesIO(b"outside"))
    with pytest.raises(licensing.LicenseSourceError, match="Unsafe"):
        licensing.extract_notices(source, tmp_path / "notices")
    assert not (tmp_path / "LICENSE").exists()


def test_caddy_binary_must_match_upstream_sbom(tmp_path: Path) -> None:
    binary = tmp_path / "runtime" / "caddy" / "caddy.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"different binary")
    sbom = {"metadata": {"component": {"version": "sha256:" + "a" * 64}}, "components": []}
    with pytest.raises(licensing.LicenseSourceError, match="does not match"):
        licensing.caddy_components(sbom, {}, tmp_path, {})


def test_unknown_license_requires_review() -> None:
    with pytest.raises(licensing.LicenseSourceError, match="explicit review"):
        licensing.identify_license(b"All rights reserved, proprietary terms")


def test_lock_rejects_unpinned_source_urls(tmp_path: Path) -> None:
    source = tmp_path / "lock.json"
    source.write_text(json.dumps({"schema_version": 1, "artifacts": [{
        "id": "fixture", "filename": "fixture.zip", "url": "https://example.invalid/latest/fixture.zip",
        "sha256": "a" * 64,
    }]}))
    with pytest.raises(licensing.LicenseSourceError, match="pinned HTTPS"):
        licensing.read_lock(source)
