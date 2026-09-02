"""Execute the embedded read-only preflight against synthetic instance trees."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
INSTALLER = DIRECTORY / "installer.iss"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL, reason="Windows installer preflight")


def embedded_validator() -> str:
    source = INSTALLER.read_text(encoding="utf-8")
    block = source.split("function WriteExistingDataValidator", 1)[1].split("function ValidateExistingData", 1)[0]
    lines = re.findall(r"Script\.Add\('((?:[^']|'')*)'\);", block)
    assert len(lines) >= 45
    return "\n".join(line.replace("''", "'") for line in lines)


def instance(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    program, data = tmp_path / "program", tmp_path / "data"
    program.mkdir()
    (data / "configuration").mkdir(parents=True)
    (data / "database").mkdir()
    (data / ".bluereel-native-instance").write_text("BlueReelDevelopment\n", encoding="utf-8")
    (data / "configuration/.env").write_text("APP_SECRET_KEY=private-do-not-output")
    (data / "database/app.db").write_bytes(b"synthetic private database")
    metadata: dict[str, Any] = {
        "instance": "development", "service_prefix": "BlueReelDevelopment", "program_dir": str(program),
        "data_dir": str(data), "port": 23450, "api_port": 23451, "web_port": 23452,
    }
    (data / "configuration/installation.json").write_text(json.dumps(metadata), encoding="utf-8")
    return program, data, metadata


def validate(
    tmp_path: Path, program: Path, data: Path, *, preserved: bool = True,
    service_registered: bool = False, product_registered: bool = False,
) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL
    wrapper = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Test-Path {
    [CmdletBinding()] param([string]$LiteralPath)
    if ($LiteralPath.StartsWith('HKLM:\SYSTEM\CurrentControlSet\Services\')) { return $env:BLUEREEL_TEST_SERVICE -eq 'true' }
    if ($LiteralPath.StartsWith('HKLM:\Software\BlueReel\')) { return $env:BLUEREEL_TEST_PRODUCT -eq 'true' }
    return (Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath)
}
$taskSource = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:BLUEREEL_TEST_SOURCE))
$taskBlock = [scriptblock]::Create($taskSource)
& $taskBlock -ProgramDir $env:BLUEREEL_TEST_PROGRAM -DataDir $env:BLUEREEL_TEST_DATA -Prefix BlueReelDevelopment -Channel development -PortFile $env:BLUEREEL_TEST_PORT -Preserved:($env:BLUEREEL_TEST_PRESERVED -eq 'true')
"""
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(wrapper.encode("utf-16-le")).decode()],
        env={**os.environ, "BLUEREEL_TEST_SOURCE": base64.b64encode(embedded_validator().encode()).decode(),
             "BLUEREEL_TEST_PROGRAM": str(program), "BLUEREEL_TEST_DATA": str(data),
             "BLUEREEL_TEST_PORT": str(tmp_path / "validated-port.txt"), "BLUEREEL_TEST_PRESERVED": str(preserved).lower(),
             "BLUEREEL_TEST_SERVICE": str(service_registered).lower(), "BLUEREEL_TEST_PRODUCT": str(product_registered).lower()},
        capture_output=True, text=True, timeout=30, check=False,
    )


def test_clean_preserved_reinstall_accepts_empty_leftover_directories_and_saved_port(tmp_path: Path) -> None:
    program, data, _metadata = instance(tmp_path)
    (program / "services").mkdir()
    (program / "old-empty" / "nested").mkdir(parents=True)
    before = {path.relative_to(data): path.read_bytes() for path in data.rglob("*") if path.is_file()}
    result = validate(tmp_path, program, data)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "validated-port.txt").read_text() == "23450"
    assert result.stdout == "" and result.stderr == ""
    assert {path.relative_to(data): path.read_bytes() for path in data.rglob("*") if path.is_file()} == before


def test_preserved_reinstall_accepts_absent_old_program_directory(tmp_path: Path) -> None:
    program, data, _metadata = instance(tmp_path)
    program.rmdir()
    assert validate(tmp_path, program, data).returncode == 0
    assert not program.exists()


@pytest.mark.parametrize("kind", ["runtime", "maintenance", "uninstaller", "unexpected"])
def test_preserved_reinstall_rejects_any_remaining_program_file(tmp_path: Path, kind: str) -> None:
    program, data, _metadata = instance(tmp_path)
    names = {"runtime": "runtime/python.exe", "maintenance": "support/install.ps1", "uninstaller": "unins000.exe", "unexpected": "keep.txt"}
    remaining = program / names[kind]
    remaining.parent.mkdir(parents=True, exist_ok=True)
    remaining.write_bytes(b"old program data")
    result = validate(tmp_path, program, data)
    assert result.returncode == 1
    assert remaining.read_bytes() == b"old program data"
    assert not (tmp_path / "validated-port.txt").exists()


@pytest.mark.parametrize("registration", ["service", "product"])
def test_preserved_reinstall_rejects_remaining_registration(tmp_path: Path, registration: str) -> None:
    program, data, _metadata = instance(tmp_path)
    result = validate(tmp_path, program, data, service_registered=registration == "service", product_registered=registration == "product")
    assert result.returncode == 1
    assert not (tmp_path / "validated-port.txt").exists()


def test_normal_upgrade_allows_existing_payload_and_registrations_for_prepare_upgrade(tmp_path: Path) -> None:
    program, data, _metadata = instance(tmp_path)
    (program / "support").mkdir()
    (program / "support/install.ps1").write_text("old maintenance placeholder")
    result = validate(tmp_path, program, data, preserved=False, service_registered=True, product_registered=True)
    assert result.returncode == 0
    assert (program / "support/install.ps1").read_text() == "old maintenance placeholder"


@pytest.mark.parametrize("field,value", [
    ("instance", "stable"), ("service_prefix", "BlueReel"), ("program_dir", "C:/foreign-program"),
    ("data_dir", "C:/foreign-data"), ("program_dir", "relative/program"), ("port", 100), ("api_port", 23490),
])
def test_foreign_metadata_or_invalid_ports_rejected_without_disclosure(tmp_path: Path, field: str, value: object) -> None:
    program, data, metadata = instance(tmp_path)
    metadata[field] = value
    (data / "configuration/installation.json").write_text(json.dumps(metadata), encoding="utf-8")
    result = validate(tmp_path, program, data)
    assert result.returncode == 1
    assert result.stdout == "" and result.stderr == ""
    assert not (tmp_path / "validated-port.txt").exists()


@pytest.mark.parametrize("marker", ["BlueReel", "bluereeldevelopment", " BlueReelDevelopment", "BlueReelDevelopmentOther"])
def test_foreign_marker_rejected_before_reinstall_or_uninstall(tmp_path: Path, marker: str) -> None:
    program, data, _metadata = instance(tmp_path)
    (data / ".bluereel-native-instance").write_text(marker)
    assert validate(tmp_path, program, data, preserved=True).returncode == 1
    assert validate(tmp_path, program, data, preserved=False).returncode == 1


@pytest.mark.parametrize("location", ["program", "data"])
def test_reparse_tree_rejected_without_following_external_directory(tmp_path: Path, location: str) -> None:
    assert POWERSHELL
    program, data, _metadata = instance(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.txt").write_text("external must stay unchanged")
    junction = (program if location == "program" else data) / "junction"
    subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", "New-Item -ItemType Junction -Path $env:BLUEREEL_TEST_JUNCTION -Target $env:BLUEREEL_TEST_EXTERNAL | Out-Null"],
        env={**os.environ, "BLUEREEL_TEST_JUNCTION": str(junction), "BLUEREEL_TEST_EXTERNAL": str(external)},
        capture_output=True, timeout=15, check=True,
    )
    result = validate(tmp_path, program, data)
    assert result.returncode == 1
    assert (external / "private.txt").read_text() == "external must stay unchanged"


def test_installer_routes_backup_before_migrate_and_never_silently_purges() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    prepare = source.split("function PrepareToInstall", 1)[1].split("procedure CurStepChanged", 1)[0]
    assert prepare.index("ValidateExistingData(PreservedReinstall, SavedPort)") < prepare.index("RunMaintenance('PrepareUpgrade'")
    assert "NetworkPage.Values[0] := SavedPort" in prepare
    assert "if not PreservedReinstall then begin" in prepare
    post_install = source.split("procedure CurStepChanged", 1)[1].split("function WasSuccessful", 1)[0]
    assert post_install.index("RunMaintenance('Backup'") < post_install.index("RunMaintenance('Install'")
    uninstall = source.split("procedure CurUninstallStepChanged", 1)[1]
    assert uninstall.index("ValidateExistingData(False, SavedPort)") < uninstall.index("RunMaintenance('Remove'")
    assert "ExpandConstant('{param:PURGEDATA|}') = '{#DataName}'" in uninstall
    assert "MB_DEFBUTTON2" in uninstall
    assert "if RemoveData then" in uninstall


def test_current_inno_compiler_accepts_full_installer_source(tmp_path: Path) -> None:
    compiler = DIRECTORY.parents[1] / "artifacts/native-dev/inno/ISCC.exe"
    if not compiler.is_file():
        pytest.skip("Pinned Inno compiler not cached")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "synthetic.txt").write_text("Compilation fixture, never installed")
    result = subprocess.run(
        [str(compiler), f"/DPayloadDir={payload}", f"/DOutputDir={tmp_path / 'output'}", str(INSTALLER)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
