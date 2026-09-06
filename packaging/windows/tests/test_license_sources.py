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


def test_mit_reference_pin_and_package_scope_are_explicit() -> None:
    reference = licensing.MIT_LICENSE_REFERENCE
    assert reference["url"] == "https://raw.githubusercontent.com/spdx/license-list-data/v3.27.0/text/MIT.txt"
    assert reference["sha256"] == "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5"
    assert reference["packages"] == ["css-box-shadow@1.0.0-3", "unpic@4.2.2", "@unpic/core@1.0.3"]
    assert reference["reference_only"] is True
    assert reference["upstream_package_notice"] is False
    assert reference["role"] == "license_text_reference"


def test_mit_reference_preserves_source_bytes_and_existing_upstream_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"MIT License\n\nCopyright (c) <year> <copyright holders>\n\nReference fixture.\n"
    reference = {**licensing.MIT_LICENSE_REFERENCE, "sha256": hashlib.sha256(payload).hexdigest()}
    monkeypatch.setattr(licensing, "MIT_LICENSE_REFERENCE", reference)
    cache, stage = tmp_path / "cache", tmp_path / "stage"
    cache.mkdir()
    (cache / reference["filename"]).write_bytes(payload)
    originals = {}
    for identity in reference["packages"]:
        metadata = stage / "licenses/npm" / identity.replace("/", "__") / "package.json"
        metadata.parent.mkdir(parents=True)
        originals[metadata] = json.dumps({"license": "MIT", "name": identity}).encode()
        metadata.write_bytes(originals[metadata])
    rows = licensing.stage_license_references(cache, stage, offline=True)
    assert rows == licensing.stage_license_references(cache, stage, offline=True)
    assert len(rows) == 1 and rows[0]["packages"] == reference["packages"]
    assert (stage / rows[0]["notices"][0]).read_bytes() == payload
    explanation = (stage / rows[0]["notices"][1]).read_text()
    assert "not a recovered original package copyright notice" in explanation
    assert "upstream_full_license_text_missing:true" in explanation
    assert "<year>" in explanation and "<copyright holders>" in explanation
    for path, original in originals.items():
        assert path.read_bytes() == original
        assert not (path.parent / "LICENSE").exists()


def test_mit_reference_requires_verified_offline_cache(tmp_path: Path) -> None:
    cache, stage = tmp_path / "cache", tmp_path / "stage"
    with pytest.raises(licensing.LicenseSourceError, match="Missing offline"):
        licensing.stage_license_references(cache, stage, offline=True)
    (cache / licensing.MIT_LICENSE_REFERENCE["filename"]).write_bytes(b"unverified replacement")
    with pytest.raises(licensing.LicenseSourceError, match="checksum mismatch"):
        licensing.stage_license_references(cache, stage, offline=True)
    assert not stage.exists()


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
