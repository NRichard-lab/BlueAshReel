"""Acceptance tooling tests: no elevation, services, or host-firewall writes."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Self

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bluereel_acceptance_probe", DIRECTORY / "test_instance_probe.py")
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_snapshot_never_discloses_secret_configuration_or_row_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "configuration").mkdir()
    (tmp_path / "database").mkdir()
    (tmp_path / "configuration/.env").write_text("APP_SECRET_KEY=synthetic-secret-not-for-output")
    (tmp_path / "configuration/installation.json").write_text('{"private_path":"X:/Private Media"}')
    with sqlite3.connect(tmp_path / "database/app.db") as connection:
        connection.execute("CREATE TABLE users (name TEXT)")
        connection.execute("INSERT INTO users VALUES ('private-user-name')")
    monkeypatch.setattr(probe, "configuration", lambda *_args: (
        SimpleNamespace(app_secret_key="synthetic-secret-not-for-output", approved_media_roots=["X:/Private Media"]),
        {"bind_address": "127.0.0.1", "port": 18080},
    ))
    result = probe.inventory(tmp_path / "program", tmp_path)
    encoded = json.dumps(result)
    assert "synthetic-secret" not in encoded and "private-user-name" not in encoded and "Private Media" not in encoded
    assert result["database_counts"]["users"] == 1
    assert len(result["secret_sha256"]) == 64


@pytest.mark.parametrize("windows_error,expected", [(10013, True), (10060, False), (10061, False), (None, False)])
def test_firewall_requires_real_os_access_denied_not_timeout_or_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_error: int | None, expected: bool,
) -> None:
    events: list[str] = []

    class GuardDenied(PermissionError):
        pass

    class Connection:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _timeout: float) -> None:
            pass

        def connect(self, target: tuple[str, int]) -> None:
            assert target == ("192.0.2.1", 443)
            assert "guard" not in events
            events.append("direct_without_hook")
            if windows_error is not None:
                error = OSError("private error must not be emitted")
                error.winerror = windows_error  # type: ignore[attr-defined]
                raise error

    module = ModuleType("app.services.outbound")
    module.OutboundConnectionDisabled = GuardDenied  # type: ignore[attr-defined]
    module._query_windows_firewall = lambda _prefix: {  # type: ignore[attr-defined]
        "profiles": ["True"] * 3,
        "rules": [{"name": "BlueReelDevelopment-Outbound-" + name} for name in ("python", "node", "ffmpeg", "ffprobe", "caddy")],
    }
    module._verified_firewall = lambda *_args: True  # type: ignore[attr-defined]
    module.install_native_network_guard = lambda _config: events.append("guard")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.services.outbound", module)
    monkeypatch.setattr(probe, "configuration", lambda *_args: (object(), {}))
    monkeypatch.setattr(socket, "socket", lambda *_args: Connection())

    def deny_name(*_args: object) -> None:
        assert events == ["direct_without_hook", "guard"]
        raise GuardDenied()

    monkeypatch.setattr(socket, "getaddrinfo", deny_name)
    result = probe.verify_firewall(tmp_path, tmp_path)
    assert result["passed"] is expected
    assert result["direct_ip_without_hook"]["os_access_denied"] is expected
    assert "private error" not in json.dumps(result)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell command quoting")
def test_windows_argument_quoting_with_spaces_quotes_and_backslashes(tmp_path: Path) -> None:
    shell = shutil.which("powershell.exe")
    assert shell
    values = ["plain", "with spaces", 'embedded"quote', "ends-with-backslash\\", 'C:\\Program Files\\test "quoted"\\']
    script = r"""
$ErrorActionPreference = 'Stop'
$taskTokens = $null; $taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_TEST_HELPER,[ref]$taskTokens,[ref]$taskErrors)
if ($taskErrors.Count) { throw 'Parse failed' }
foreach ($taskName in @('Quote-Argument','Assert-NoReparse','Invoke-PrivateProcess')) {
  $taskFunction = $taskAst.Find({ param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq $taskName },$true)
  . ([scriptblock]::Create($taskFunction.Extent.Text))
}
$TaskPython = $env:BLUEREEL_TEST_PYTHON
$TaskNode = 'not-a-runtime'
$TaskProgram = $env:BLUEREEL_TEST_DATA
$TaskData = $env:BLUEREEL_TEST_DATA
$taskValues = $env:BLUEREEL_TEST_VALUES | ConvertFrom-Json
$taskResult = Invoke-PrivateProcess $TaskPython (@('-I','-B','-c','import json,sys; print(json.dumps(sys.argv[1:]))') + $taskValues) 15
if ($taskResult.exit_code -ne 0) { throw 'Argument probe failed' }
$taskResult.stdout
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
        env={**os.environ, "BLUEREEL_TEST_HELPER": str(DIRECTORY / "test_instance.ps1"),
             "BLUEREEL_TEST_PYTHON": sys.executable, "BLUEREEL_TEST_DATA": str(tmp_path), "BLUEREEL_TEST_VALUES": json.dumps(values)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    assert json.loads(result.stdout) == values


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only acceptance entry point")
@pytest.mark.parametrize("phase", ["UninstallPurge", "Snapshot"])
def test_helper_refuses_without_disposable_opt_in_and_creates_no_report(tmp_path: Path, phase: str) -> None:
    shell = shutil.which("powershell.exe")
    assert shell
    report = tmp_path / "must-not-be-created"
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(DIRECTORY / "test_instance.ps1"),
         "-Phase", phase, "-ReportDirectory", str(report)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 1
    output: dict[str, Any] = json.loads(result.stdout)
    assert output == {"phase": phase, "passed": False, "report_written": False}
    assert not report.exists()


def test_snapshot_phase_only_invokes_existing_read_only_snapshot_probe() -> None:
    source = (DIRECTORY / "test_instance.ps1").read_text(encoding="utf-8")
    assert "'Snapshot' {\n            $TaskReport.snapshot = Invoke-Probe 'snapshot'\n        }" in source


@pytest.mark.skipif(sys.platform != "win32", reason="Windows service identity guard")
@pytest.mark.parametrize("scenario", ["absent", "valid", "foreign_path", "foreign_account"])
def test_remote_acceptance_refuses_foreign_service_before_lifecycle_changes(scenario: str) -> None:
    shell = shutil.which("powershell.exe")
    assert shell
    script = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskTokens = $null; $taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_TEST_HELPER,[ref]$taskTokens,[ref]$taskErrors)
if ($taskErrors.Count) { throw 'Parse failed' }
$taskFunction = $taskAst.Find({param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq 'Get-TestRemoteService'},$true)
. ([scriptblock]::Create($taskFunction.Extent.Text))
$TaskPrefix = 'BlueReelDevelopment'
$TaskProgram = 'C:\Program Files\BlueAshReel Development'
function Get-CimInstance {
    param([string]$ClassName,[string]$Filter)
    if ($ClassName -cne 'Win32_Service' -or $Filter -cne "Name='BlueReelDevelopmentRemote'") { throw 'Unexpected service lookup' }
    if ($env:BLUEREEL_TEST_SCENARIO -eq 'absent') { return $null }
    $taskPath = Join-Path $TaskProgram 'services\BlueReelDevelopmentRemote.exe'
    $taskAccount = 'NT SERVICE\BlueReelDevelopmentRemote'
    if ($env:BLUEREEL_TEST_SCENARIO -eq 'foreign_path') { $taskPath = 'C:\Unrelated\service.exe' }
    if ($env:BLUEREEL_TEST_SCENARIO -eq 'foreign_account') { $taskAccount = 'LocalSystem' }
    return [pscustomobject]@{PathName=('"' + $taskPath + '"');StartName=$taskAccount}
}
try { $taskResult = Get-TestRemoteService; @{accepted=$true;exists=($null -ne $taskResult)} | ConvertTo-Json -Compress }
catch { @{accepted=$false;exists=$false} | ConvertTo-Json -Compress }
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
        env={**os.environ, "BLUEREEL_TEST_HELPER": str(DIRECTORY / "test_instance.ps1"),
             "BLUEREEL_TEST_SCENARIO": scenario},
        capture_output=True, text=True, timeout=15, check=True,
    )
    assert json.loads(result.stdout) == {
        "accepted": scenario in {"absent", "valid"}, "exists": scenario == "valid",
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL objects, no filesystem ACL mutation")
@pytest.mark.parametrize("scenario", ["directory", "file", "service_modify", "service_full", "service_incomplete", "runtime_owner", "broad_sid", "missing_sid", "unprotected", "inherit_only"])
def test_configuration_acl_requires_exact_service_read_only_permissions(scenario: str) -> None:
    shell = shutil.which("powershell.exe")
    assert shell
    script = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskTokens = $null; $taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_TEST_HELPER,[ref]$taskTokens,[ref]$taskErrors)
if ($taskErrors.Count) { throw 'Parse failed' }
$taskFunction = $taskAst.Find({ param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq 'Test-ConfigurationAcl' },$true)
. ([scriptblock]::Create($taskFunction.Extent.Text))
$taskScenario = $env:BLUEREEL_TEST_SCENARIO
$taskIsDirectory = $taskScenario -ne 'file'
$taskAllowed = @('S-1-5-18','S-1-5-32-544','S-1-5-80-1-2-3-4-5','S-1-5-80-1-2-3-4-6','S-1-5-80-1-2-3-4-7','S-1-5-80-1-2-3-4-8')
$taskAcl = if ($taskIsDirectory) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
$taskAcl.SetAccessRuleProtection(($taskScenario -ne 'unprotected'),$false)
$taskOwner = if ($taskScenario -eq 'runtime_owner') { 'S-1-5-19' } else { 'S-1-5-32-544' }
$taskAcl.SetOwner([Security.Principal.SecurityIdentifier]::new($taskOwner))
$taskSids = if ($taskScenario -eq 'broad_sid') { $taskAllowed + @('S-1-1-0') } elseif ($taskScenario -eq 'missing_sid') { $taskAllowed[0..4] } else { $taskAllowed }
foreach ($taskSid in $taskSids) {
    $taskRights = if ($taskSid -in @('S-1-5-18','S-1-5-32-544')) { 'FullControl' }
        elseif ($taskScenario -eq 'service_modify') { 'Modify' }
        elseif ($taskScenario -eq 'service_full') { 'FullControl' }
        elseif ($taskScenario -eq 'service_incomplete') { 'ReadData' }
        else { 'ReadAndExecute' }
    $taskFlags = if ($taskIsDirectory) { 'ContainerInherit,ObjectInherit' } else { 'None' }
    $taskPropagation = if ($taskScenario -eq 'inherit_only') { 'InheritOnly' } else { 'None' }
    $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($taskSid),$taskRights,$taskFlags,$taskPropagation,'Allow'))
}
@{accepted=(Test-ConfigurationAcl $taskAcl $taskAllowed $taskIsDirectory $taskIsDirectory)} | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
        env={**os.environ, "BLUEREEL_TEST_HELPER": str(DIRECTORY / "test_instance.ps1"), "BLUEREEL_TEST_SCENARIO": scenario},
        capture_output=True, text=True, timeout=15, check=True,
    )
    assert json.loads(result.stdout) == {"accepted": scenario in {"directory", "file"}}
