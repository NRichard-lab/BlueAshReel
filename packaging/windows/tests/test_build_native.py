"""Build-only packaging checks; no administrator, network, or installer required."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "build_native.py"
SPEC = importlib.util.spec_from_file_location("bluereel_native_builder", SOURCE)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def locked_artifact(content: bytes = b"pinned dependency") -> dict[str, str]:
    import hashlib

    return {
        "id": "fixture", "filename": "fixture.zip", "url": "https://example.invalid/releases/1.0/fixture.zip",
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_repository_component_pins_are_complete() -> None:
    lock = builder.load_lock()
    assert lock["version"] == "0.1.0-dev.2"
    assert {item["id"] for item in lock["artifacts"]} >= {"python", "node", "winsw", "caddy", "inno"}
    assert {item["id"] for item in lock["ffmpeg_sources"]} == {
        "ffmpeg", "x264", "nv-codec-headers", "amf", "libvpl",
    }
    assert "Windows 10 22H2" in lock["minimum_windows"]


@pytest.mark.parametrize("change", [{"sha256": "not-a-hash"}, {"url": "http://example.invalid/v1/a"},
                                    {"url": "https://example.invalid/latest/a"}, {"filename": "../escape.zip"}])
def test_rejects_unpinned_or_unsafe_component_lock(tmp_path: Path, change: dict[str, str]) -> None:
    record = {**locked_artifact(), **change}
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"schema_version": 1, "artifacts": [record], "ffmpeg_sources": []}))
    with pytest.raises(builder.BuildError):
        builder.load_lock(lock)


def test_download_reuses_only_hash_verified_cache(tmp_path: Path) -> None:
    item = locked_artifact()
    (tmp_path / item["filename"]).write_bytes(b"pinned dependency")
    assert builder.download(item, tmp_path, offline=True).name == item["filename"]
    (tmp_path / item["filename"]).write_bytes(b"modified")
    with pytest.raises(builder.BuildError, match="checksum mismatch"):
        builder.download(item, tmp_path, offline=True)
    assert (tmp_path / item["filename"]).read_bytes() == b"modified"


def test_offline_build_cannot_fetch_missing_dependency(tmp_path: Path) -> None:
    with pytest.raises(builder.BuildError, match="Offline build"):
        builder.download(locked_artifact(), tmp_path, offline=True)


def test_fresh_staging_never_overwrites_existing_data(tmp_path: Path) -> None:
    existing = tmp_path / "package"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("owned by an earlier build")
    generated = builder.fresh_stage(existing, tmp_path)
    assert generated != existing
    assert generated.parent == tmp_path
    assert marker.read_text() == "owned by an earlier build"


def test_staging_cannot_target_workspace_root_or_parent(tmp_path: Path) -> None:
    with pytest.raises(builder.BuildError):
        builder.fresh_stage(tmp_path, tmp_path)
    with pytest.raises(builder.BuildError):
        builder.fresh_stage(tmp_path.parent / "outside", tmp_path)


@pytest.mark.parametrize("member", ["../escape.exe", "/root.exe", "C:/Windows/escape.exe", r"..\escape.exe"])
def test_zip_extraction_rejects_traversal(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(member, b"not allowed")
    with pytest.raises(builder.BuildError, match="Unsafe archive"):
        builder.extract_zip(archive, tmp_path / "payload")
    assert not (tmp_path / "escape.exe").exists()


def test_zip_extraction_rejects_archive_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    entry = zipfile.ZipInfo("shortcut")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(entry, "../elsewhere")
    with pytest.raises(builder.BuildError, match="links"):
        builder.extract_zip(archive, tmp_path / "payload")


def test_selected_zip_members_exclude_bundled_build_tools(tmp_path: Path) -> None:
    archive = tmp_path / "node.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("node-v1/node.exe", b"runtime")
        zipped.writestr("node-v1/LICENSE", b"upstream notice")
        zipped.writestr("node-v1/npm.cmd", b"not required")
    target = tmp_path / "node"
    builder.extract_zip(archive, target, {"node-v1/node.exe": "node.exe", "node-v1/LICENSE": "LICENSE"})
    assert sorted(path.name for path in target.iterdir()) == ["LICENSE", "node.exe"]


def test_missing_archive_member_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("LICENSE", "notice")
    with pytest.raises(builder.BuildError, match="absent"):
        builder.extract_zip(archive, tmp_path / "runtime", {"python.exe": "python.exe"})


def test_source_copy_excludes_secrets_caches_and_bytecode(tmp_path: Path) -> None:
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "main.py").write_text("pass")
    (source / ".env").write_text("DO_NOT_PACKAGE")
    (source / "main.pyc").write_bytes(b"local paths")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "cached.pyc").write_bytes(b"cache")
    builder.copy_tree(source, target)
    assert [path.name for path in target.iterdir()] == ["main.py"]


def test_python_requirements_require_hashes_and_versions(tmp_path: Path) -> None:
    source = tmp_path / "requirements.lock"
    source.write_text("example==1.2.3 \\\n  --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    assert builder.requirements_pins(source)["example"] == {"version": "1.2.3", "hashes": {"a" * 64}}
    source.write_text("example==1.2.3\n", encoding="utf-8")
    with pytest.raises(builder.BuildError, match="pins with hashes"):
        builder.requirements_pins(source)


def test_wheel_metadata_has_exact_component_identity(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example-1.0.dist-info/METADATA", "Name: Example\nVersion: 1.0\n\n")
    assert builder.wheel_metadata(wheel) == ("Example", "1.0")


def test_python_upstream_sbom_must_match_exact_archive(tmp_path: Path) -> None:
    sbom = tmp_path / "python.spdx.json"
    sbom.write_text(json.dumps({"packages": [
        {"name": "CPython", "checksums": [{"algorithm": "SHA256", "checksumValue": "a" * 64}]},
        {"name": "sqlite", "versionInfo": "3.50.4.0", "licenseConcluded": "NOASSERTION"},
    ]}))
    rows = builder.python_upstream_components(sbom, "a" * 64)
    assert rows[0]["name"] == "sqlite"
    assert rows[0]["version"] == "3.50.4.0"
    assert rows[0]["role"] == "upstream_python_sbom_entry"
    with pytest.raises(builder.BuildError, match="does not describe"):
        builder.python_upstream_components(sbom, "b" * 64)


def test_pnpm_integrity_parser_excludes_snapshot_duplicates(tmp_path: Path) -> None:
    lock = tmp_path / "pnpm-lock.yaml"
    lock.write_text(
        "lockfileVersion: '9.0'\npackages:\n  '@scope/name@1.2.3':\n"
        "    resolution: {integrity: sha512-verified==}\n  package@4.5.6:\n"
        "    resolution: {integrity: sha512-second==}\nsnapshots:\n  '@scope/name@1.2.3': {}\n",
        encoding="utf-8",
    )
    assert builder.pnpm_integrities(lock) == {
        "@scope/name@1.2.3": "sha512-verified==", "package@4.5.6": "sha512-second==",
    }


def test_missing_license_notice_does_not_generate_fictional_notice(tmp_path: Path) -> None:
    source, stage = tmp_path / "dependency", tmp_path / "stage"
    source.mkdir()
    stage.mkdir()
    (source / "package.json").write_text('{"license":"MIT"}')
    with pytest.raises(builder.BuildError, match="notice is missing"):
        builder.save_notices(builder.notice_files(source), source, stage / "licenses", stage)
    assert not (stage / "licenses").exists()


def test_exact_upstream_metadata_exception_preserves_declaration_without_inventing_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend, stage = tmp_path / "frontend", tmp_path / "stage"
    package = frontend / "dependency"
    package.mkdir(parents=True)
    original = '{"name":"declared-only","version":"1.0","license":"MIT","author":"Original Publisher"}'
    (package / "package.json").write_text(original)
    (package / "README.md").write_text("Original upstream documentation. MIT.")
    monkeypatch.setattr(builder, "frontend_package_roots", lambda _frontend: {package: "frontend_runtime"})
    monkeypatch.setattr(builder, "pnpm_integrities", lambda _lock: {"declared-only@1.0": "sha512-pinned"})
    lock = {"npm_sources": [{
        "packages": ["declared-only@1.0"], "filename": "original.tgz", "published_declaration_only": True,
    }]}
    result = builder.frontend_components(frontend, stage, lock, {})
    assert result[0]["upstream_full_license_text_missing"] is True
    assert result[0]["source_archives"] == ["source/npm/original.tgz"]
    assert (stage / "licenses/npm/declared-only@1.0/package.json").read_text() == original
    assert not (stage / "licenses/npm/declared-only@1.0/LICENSE").exists()


def test_metadata_only_license_exception_does_not_expand_to_unpinned_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "dependency"
    package.mkdir()
    (package / "package.json").write_text('{"name":"declared-only","version":"2.0","license":"MIT"}')
    monkeypatch.setattr(builder, "frontend_package_roots", lambda _frontend: {package: "frontend_runtime"})
    monkeypatch.setattr(builder, "pnpm_integrities", lambda _lock: {"declared-only@2.0": "sha512-pinned"})
    lock = {"npm_sources": [{
        "packages": ["declared-only@1.0"], "filename": "original.tgz", "published_declaration_only": True,
    }]}
    with pytest.raises(builder.BuildError, match="notice is missing"):
        builder.frontend_components(tmp_path, tmp_path / "stage", lock, {})


def test_inventory_records_size_and_hash_for_every_file(tmp_path: Path) -> None:
    (tmp_path / "first.exe").write_bytes(b"runtime")
    (tmp_path / "licenses").mkdir()
    (tmp_path / "licenses" / "LICENSE").write_bytes(b"notice")
    records = builder.inventory(tmp_path)
    assert [item["path"] for item in records] == ["first.exe", "licenses/LICENSE"]
    assert records[0]["size"] == 7
    assert records[0]["sha256"] == builder.digest(tmp_path / "first.exe")


def test_embedded_runtime_paths_are_relative_and_site_enabled() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert r"python313.zip\n.\nLib/site-packages\n../../backend\n../../.\nimport site\n" in source
    assert "scripts.backup,scripts.restore_validate" in source
    assert "--require-hashes" in source
    assert "--no-compile" in source
    assert builder.parser().get_default("skip_frontend_build") is False


def test_installer_compile_uses_payload_and_output_defines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    args: Any = builder.parser().parse_args([])
    args.iscc = tmp_path / "ISCC.exe"
    args.iscc.write_bytes(b"fixture")
    args.output_dir = tmp_path / "artifacts" / "native-dev"
    args.output_dir.mkdir(parents=True)
    installer = args.output_dir / "BlueReel-Setup-Development-x64.exe"
    installer.write_bytes(b"fixture installer")
    monkeypatch.setattr(builder, "REPOSITORY", tmp_path)
    monkeypatch.setattr(builder, "run", lambda command: seen.append(command))
    stage = tmp_path / "artifacts" / "package"
    assert builder.compile_installer(stage, args) == installer
    assert "/DPayloadDir=" + str(stage) in seen[0]
    assert "/DOutputDir=" + str(args.output_dir) in seen[0]
    lock = builder.load_lock(args.component_lock)
    assert "/DProductVersion=" + lock["version"] in seen[0]
    assert "/DFileVersion=" + lock["windows_file_version"] in seen[0]
    artifact = json.loads((args.output_dir / "installer-artifact.json").read_text())
    assert artifact["unsigned"] is True
    assert artifact["version"] == lock["version"]
    assert artifact["windows_file_version"] == lock["windows_file_version"]
    assert artifact["size"] == len(b"fixture installer")


@pytest.mark.skipif(os.name != "nt", reason="Real embedded Windows runtime path regression")
def test_real_embedded_runtime_imports_shared_backup_from_relocated_payload(tmp_path: Path) -> None:
    pin = next(row for row in builder.load_lock()["artifacts"] if row["id"] == "python")
    archive = builder.ARTIFACTS / "downloads" / pin["filename"]
    if not archive.is_file():
        pytest.skip("Pinned embedded Python cache required for native packaging integration")
    builder.verify_file(archive, pin["sha256"])
    payload = tmp_path / "Relocated Program Files" / "BlueReel Development"
    runtime = payload / "runtime" / "python"
    builder.extract_zip(archive, runtime)
    builder.configure_embedded_paths(runtime)
    scripts = payload / "scripts"
    scripts.mkdir()
    for name in ("__init__.py", "backup.py", "backup_format.py", "restore_validate.py"):
        shutil.copy2(builder.REPOSITORY / "scripts" / name, scripts / name)
    unrelated = tmp_path / "Unrelated working directory"
    unrelated.mkdir()
    environment = dict(os.environ, PYTHONPATH=str(unrelated), PYTHONDONTWRITEBYTECODE="1")
    probe = subprocess.run(
        [str(runtime / "python.exe"), "-I", "-B", "-c", (
            "import scripts.backup,scripts.restore_validate,json,sys;from pathlib import Path;"
            "root=Path(sys.executable).resolve().parents[2];"
            "assert Path(scripts.backup.__file__).resolve().parent == root/'scripts';"
            "assert Path(scripts.restore_validate.__file__).resolve().parent == root/'scripts';"
            "assert str(root) in sys.path;print('isolated relocated backup imports verified')"
        )], cwd=unrelated, env=environment, capture_output=True, text=True, timeout=15, check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert "isolated relocated backup imports verified" in probe.stdout
    for module in ("scripts.backup", "scripts.restore_validate"):
        result = subprocess.run(
            [str(runtime / "python.exe"), "-I", "-B", "-m", module, "--help"],
            cwd=unrelated, env=environment, capture_output=True, text=True, timeout=15, check=False,
        )
        assert result.returncode == 0, result.stderr
    assert not list(payload.rglob("*.pyc"))
