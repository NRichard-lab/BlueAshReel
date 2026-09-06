"""Opt-in uninstall cleanup exercises disposable directories only."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from app import native_install, native_user_install
from app.services.process_supervisor import lock_file, unlock_file

DIRECTORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bluereel_remove_user_data", DIRECTORY / "remove_user_data.py")
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


@pytest.fixture
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    program, data, artwork = tmp_path / "Programs/Agent", tmp_path / "User Data", tmp_path / "Artwork"
    for directory in (program / "runtime/python", program / "runtime/ffmpeg", program / "config"):
        directory.mkdir(parents=True)
    for name in ("runtime/python/python.exe", "runtime/ffmpeg/ffmpeg.exe", "runtime/ffmpeg/ffprobe.exe"):
        (program / name).write_bytes(b"synthetic runtime fixture")
    (program / "config/product.json").write_text('{"name":"Blue Ash Reel"}')
    monkeypatch.setattr(native_install, "_executing_program_dir", lambda: program)
    monkeypatch.setattr(native_install, "validate_ports", lambda *args: None)
    native_user_install.configure(program, data, "development", 19080, {"artwork": str(artwork)})
    (data / "remote-identity/identity.json").write_text('{"fixture":"not a real identity"}')
    (artwork / "fixture.txt").write_text("synthetic artwork")
    with closing(sqlite3.connect(data / "database/app.db")) as connection, connection:
        connection.execute("CREATE TABLE library_paths(canonical_path TEXT)")
    return program, data, artwork


def test_confirmed_removal_deletes_owned_identity_and_advanced_storage_only(
    installation: tuple[Path, Path, Path],
) -> None:
    program, data, artwork = installation
    media = data.parent / "Source media"
    media.mkdir()
    (media / "fixture.mp4").write_bytes(b"synthetic source")
    with closing(sqlite3.connect(data / "database/app.db")) as connection, connection:
        connection.execute("INSERT INTO library_paths VALUES (?)", (str(media),))
    cleanup.remove_user_data(program, data, "development", confirmed=True)
    assert not data.exists() and not artwork.exists()
    assert program.is_dir() and (media / "fixture.mp4").read_bytes() == b"synthetic source"


def test_missing_confirmation_never_reads_or_removes_data(installation: tuple[Path, Path, Path]) -> None:
    program, data, artwork = installation
    with pytest.raises(ValueError, match="confirmation"):
        cleanup.remove_user_data(program, data, "development", confirmed=False)
    assert data.exists() and (artwork / "fixture.txt").exists()


def test_running_supervisor_lock_prevents_any_deletion(installation: tuple[Path, Path, Path]) -> None:
    program, data, artwork = installation
    ownership = lock_file(data / "state/tray-runtime.lock")
    try:
        with pytest.raises(OSError):
            cleanup.remove_user_data(program, data, "development", confirmed=True)
    finally:
        unlock_file(ownership)
    assert (data / "remote-identity/identity.json").exists() and (artwork / "fixture.txt").exists()


@pytest.mark.parametrize("fault", ["channel", "legacy", "program", "storage_overlap", "configuration", "marker"])
def test_invalid_installation_preflights_all_targets_before_deleting(
    installation: tuple[Path, Path, Path], fault: str,
) -> None:
    program, data, artwork = installation
    path = data / "configuration/installation.json"
    record = json.loads(path.read_text())
    if fault == "legacy":
        record["runtime_mode"] = "legacy_service"
    elif fault == "program":
        record["program_dir"] = str(data.parent / "Other program")
    elif fault == "storage_overlap":
        record["storage"]["logs"] = str(artwork)
    elif fault == "configuration":
        record["storage"]["artwork"] = str(data.parent / "Other artwork")
    elif fault == "marker":
        (data / ".bluereel-native-instance").write_text("BlueReel")
    path.write_text(json.dumps(record))
    with pytest.raises((ValueError, RuntimeError)):
        cleanup.remove_user_data(program, data, "stable" if fault == "channel" else "development", confirmed=True)
    assert (data / "remote-identity/identity.json").exists() and (artwork / "fixture.txt").exists()


@pytest.mark.parametrize("target", ["data", "inside_artwork", "parent"])
def test_library_source_overlap_refuses_automatic_data_removal(
    installation: tuple[Path, Path, Path], target: str,
) -> None:
    program, data, artwork = installation
    source = {"data": data, "inside_artwork": artwork / "Source", "parent": data.parent}[target]
    with closing(sqlite3.connect(data / "database/app.db")) as connection, connection:
        connection.execute("INSERT INTO library_paths VALUES (?)", (str(source),))
    with pytest.raises(ValueError, match="media folder"):
        cleanup.remove_user_data(program, data, "development", confirmed=True)
    assert (data / "remote-identity/identity.json").exists() and (artwork / "fixture.txt").exists()


def test_shared_profile_ancestor_is_never_a_cleanup_target(
    installation: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    program, data, artwork = installation
    monkeypatch.setenv("APPDATA", str(artwork / "Shared user settings"))
    with pytest.raises(ValueError, match="shared"):
        cleanup.remove_user_data(program, data, "development", confirmed=True)
    assert (data / "remote-identity/identity.json").exists() and (artwork / "fixture.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows junction")
def test_junction_fails_before_removing_any_valid_root(installation: tuple[Path, Path, Path]) -> None:
    program, data, artwork = installation
    outside = data.parent / "Unrelated"
    outside.mkdir()
    (outside / "keep.txt").write_text("untouched")
    shell = shutil.which("powershell.exe")
    assert shell
    subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                    "New-Item -ItemType Junction -Path $env:BLUEREEL_LINK -Target $env:BLUEREEL_OUTSIDE | Out-Null"],
                   env={**os.environ, "BLUEREEL_LINK": str(data / "foreign-link"), "BLUEREEL_OUTSIDE": str(outside)},
                   check=True, capture_output=True, timeout=15)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        cleanup.remove_user_data(program, data, "development", confirmed=True)
    assert (outside / "keep.txt").read_text() == "untouched" and (artwork / "fixture.txt").exists()


def test_native_tray_builds_with_required_pairing_controls(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Inbox Windows C# compiler")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    result = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64",
                             "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
                             "/reference:System.Web.Extensions.dll", "/out:" + str(tmp_path / "tray.exe"),
                             str(DIRECTORY / "BlueAshReelAgent.cs")], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    source = (DIRECTORY / "BlueAshReelAgent.cs").read_text()
    for label in ("Open Portal", "Open local management", "Pair Agent", "Reconnect", "Unpair this Agent",
                  "fingerprint_short", "Connection status: ", "Start with Windows"):
        assert label in source
    assert "label == \"Unpaired\"" not in source
    assert "MessageBoxDefaultButton.Button2) == DialogResult.Yes) Command(\"unpair\")" in source
    assert "String.Equals(Convert.ToString(key.GetValue(runName)), StartupCommand(), StringComparison.OrdinalIgnoreCase)" in source
    assert "else if (IsStartup()) key.DeleteValue(runName,false)" in source
