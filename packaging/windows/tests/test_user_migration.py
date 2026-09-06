"""Exercise privileged migration decisions using disposable service doubles only."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

POWERSHELL = shutil.which("powershell.exe")
SOURCE = Path(__file__).resolve().parents[1] / "user-install.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL, reason="Windows migration helper")


def evaluate(body: str, *, powershell: str | None = POWERSHELL,
             environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert powershell
    script = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskTokens = $null; $taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_HELPER,[ref]$taskTokens,[ref]$taskErrors)
if ($taskErrors.Count) { throw 'Helper syntax failed' }
$taskDefinitions = $taskAst.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)
foreach ($taskDefinition in $taskDefinitions) { . ([scriptblock]::Create($taskDefinition.Extent.Text)) }
""" + body
    return subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
        env={**os.environ, "BLUEREEL_HELPER": str(SOURCE), **(environment or {})},
        text=True, capture_output=True, timeout=30, check=False,
    )


@pytest.mark.parametrize("mismatch", ["none", "path", "account", "channel", "prefix", "data", "unprotected"])
def test_legacy_validation_rejects_foreign_services_without_mutation(mismatch: str) -> None:
    script = r"""
$Instance = 'development'; $DataDir = 'C:\MigrationFixture\data'; $env:ProgramFiles = 'C:\ProtectedFixture'
$taskRecord = [pscustomobject]@{instance='development';service_prefix='BlueReelDevelopment';data_dir=$DataDir;program_dir='C:\ProtectedFixture\Agent'}
$script:mismatch = '__MISMATCH__'
if ($script:mismatch -eq 'channel') { $taskRecord.instance = 'stable' }
if ($script:mismatch -eq 'prefix') { $taskRecord.service_prefix = 'BlueReel' }
if ($script:mismatch -eq 'data') { $taskRecord.data_dir = 'C:\Foreign\data' }
if ($script:mismatch -eq 'unprotected') { $taskRecord.program_dir = 'C:\UserWritable\program' }
function Dedicated([string]$Value) { return $Value }
function Get-CimInstance {
    param($ClassName,$Filter)
    $taskName = $Filter.Substring(6).TrimEnd("'")
    $taskAccount = if ($taskName.EndsWith('Remote')) { 'NT SERVICE\' + $taskName } else { 'NT AUTHORITY\LocalService' }
    if ($script:mismatch -eq 'account') { $taskAccount = 'LocalSystem' }
    $taskPath = 'C:\ProtectedFixture\Agent\services\' + $taskName + '.exe'
    if ($script:mismatch -eq 'path') { $taskPath = 'C:\Foreign\service.exe' }
    return [pscustomobject]@{PathName='"' + $taskPath + '"';StartName=$taskAccount;State='Running';StartMode='Auto'}
}
function Stop-Service { throw 'Unexpected mutation' }
function Set-Service { throw 'Unexpected mutation' }
function Start-Service { throw 'Unexpected mutation' }
try { $taskResult = @(Validate-LegacyServices $taskRecord); if ($taskResult.Count -ne 5) { throw 'Wrong service count' }; Write-Output 'accepted' }
catch { Write-Output 'rejected' }
""".replace("__MISMATCH__", mismatch)
    result = evaluate(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("accepted" if mismatch == "none" else "rejected")


def test_rollback_restores_graph_before_dependency_ordered_start() -> None:
    result = evaluate(r"""
$script:events = [Collections.Generic.List[string]]::new()
$taskServices = @('Remote','Proxy','Worker','API','Web') | ForEach-Object { [pscustomobject]@{name='BlueReelDevelopment'+$_;running=($_ -ne 'Remote');start_mode='Auto'} }
function Get-Service { param($Name,$ErrorAction) return [pscustomobject]@{Name=$Name} }
function Set-Service { param($Name,$StartupType) $script:events.Add('configure:'+$Name) }
function Start-Service { param($Name) $script:events.Add('start:'+$Name) }
Restore-LegacyServices ([pscustomobject]@{services=$taskServices;old_program='C:\ProtectedFixture\Agent'})
if ($script:events.Count -ne 9) { throw 'Unexpected operation count' }
if (@($script:events.GetRange(0,5) | Where-Object { -not $_.StartsWith('configure:') }).Count) { throw 'Started before complete registration' }
if (($script:events.GetRange(5,4) -join ',') -ne 'start:BlueReelDevelopmentAPI,start:BlueReelDevelopmentWorker,start:BlueReelDevelopmentWeb,start:BlueReelDevelopmentProxy') { throw 'Wrong dependency order or restarted an originally stopped service' }
Write-Output 'restored'
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "restored"


@pytest.mark.parametrize("architecture", ["System32", "SysWOW64"])
@pytest.mark.parametrize("identity", ["matching", "gone", "foreign", "unavailable", "invalid_pid"])
def test_stop_uses_cross_bitness_identity_and_handles_process_exit(
    tmp_path: Path, architecture: str, identity: str,
) -> None:
    powershell = Path(os.environ["WINDIR"]) / architecture / "WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.exists():
        pytest.skip("Requested Windows PowerShell architecture is unavailable")
    result = evaluate(r"""
$taskState = $env:BLUEREEL_STATE
$ProgramDir = 'C:\DisposableFixture\program'
$taskPid = if ($env:BLUEREEL_IDENTITY -eq 'invalid_pid') { '123 OR 1=1' } else { 123 }
[IO.File]::WriteAllText((Join-Path $taskState 'tray-status.json'),(@{pid=$taskPid}|ConvertTo-Json -Compress))
$script:queries = 0
function Get-Process { throw 'Module paths cannot establish cross-bitness identity' }
function Stop-Process { throw 'Must never kill a process from a status-file PID' }
function Start-Sleep { param($Milliseconds) }
function Write-StopCommand { param($Path,$Message) [IO.File]::WriteAllText($Path,$Message) }
function Get-CimInstance {
    param($ClassName,$Filter)
    if ($ClassName -ne 'Win32_Process' -or $Filter -ne 'ProcessId=123') { throw 'Unbounded process query' }
    $script:queries++
    if ($env:BLUEREEL_IDENTITY -eq 'gone' -or $script:queries -gt 1) { return }
    $taskPath = switch ($env:BLUEREEL_IDENTITY) {
        'matching' { 'C:\DisposableFixture\program\runtime\python\pythonw.exe' }
        'foreign' { 'C:\Foreign\pythonw.exe' }
        'unavailable' { $null }
    }
    return [pscustomobject]@{ExecutablePath=$taskPath;CreationDate=[datetime]'2026-09-06T00:00:00Z'}
}
try { Stop-UserRuntime; Write-Output 'stopped' } catch { Write-Output 'rejected' }
Write-Output ('queries=' + $script:queries)
""", powershell=str(powershell), environment={"BLUEREEL_STATE": str(tmp_path), "BLUEREEL_IDENTITY": identity})
    assert result.returncode == 0, result.stderr
    expected = "stopped" if identity in {"matching", "gone"} else "rejected"
    count = 2 if identity == "matching" else 0 if identity == "invalid_pid" else 1
    assert result.stdout.splitlines() == [expected, f"queries={count}"]
    command = tmp_path / "tray-command.json"
    assert command.exists() == (identity == "matching")
    if command.exists():
        message = json.loads(command.read_text())
        assert message["target_pid"] == 123 and message["runtime_id"] is None
        assert len(message["maintenance_id"]) == 32


@pytest.mark.parametrize("scenario", [
    "missing_status", "gone", "exited_before_consume", "consumed", "newer_command", "pid_reused", "changed_status",
    "foreign_after_publish", "missing_creation", "invalid_generation", "creation_unavailable_after_publish",
])
def test_stop_command_ownership_and_runtime_generation(tmp_path: Path, scenario: str) -> None:
    result = evaluate(r"""
$taskState = $env:BLUEREEL_STATE
$ProgramDir = 'C:\DisposableFixture\program'
$taskCommand = Join-Path $taskState 'tray-command.json'
$taskStatusFile = Join-Path $taskState 'tray-status.json'
$taskNewer = '{"action":"restart","created_at":99}'
[IO.File]::WriteAllText($taskCommand,$taskNewer)
$taskGeneration = if ($env:BLUEREEL_SCENARIO -eq 'invalid_generation') { 'not-a-generation' } else { 'a'*32 }
if ($env:BLUEREEL_SCENARIO -ne 'missing_status') {
    [IO.File]::WriteAllText($taskStatusFile,(@{pid=123;runtime_id=$taskGeneration}|ConvertTo-Json -Compress))
}
$script:queries = 0
$script:publications = 0
function Start-Sleep { param($Milliseconds) }
function Write-StopCommand {
    param($Path,$Message)
    $script:publications++
    [IO.File]::WriteAllText($Path,$Message)
}
function Get-CimInstance {
    param($ClassName,$Filter)
    if ($ClassName -ne 'Win32_Process' -or $Filter -ne 'ProcessId=123') { throw 'Followed a replacement runtime PID' }
    $script:queries++
    if ($env:BLUEREEL_SCENARIO -eq 'gone') { return }
    $taskCreation = [datetime]'2026-09-06T00:00:00Z'
    if ($env:BLUEREEL_SCENARIO -eq 'missing_creation') { $taskCreation = $null }
    $taskPath = 'C:\DisposableFixture\program\runtime\python\pythonw.exe'
    if ($script:queries -gt 1) {
        switch ($env:BLUEREEL_SCENARIO) {
            'consumed' { [IO.File]::Delete($taskCommand); return }
            'newer_command' { [IO.File]::WriteAllText($taskCommand,$taskNewer); return }
            'pid_reused' { return [pscustomobject]@{ExecutablePath='C:\Foreign\new-process.exe';CreationDate=$taskCreation.AddSeconds(1)} }
            'changed_status' {
                if ($script:queries -gt 2) { return }
                [IO.File]::WriteAllText($taskStatusFile,'{"pid":456,"runtime_id":"replacement"}')
            }
            'foreign_after_publish' { $taskPath = 'C:\Foreign\pythonw.exe' }
            'creation_unavailable_after_publish' { $taskCreation = $null }
            default { return }
        }
    }
    return [pscustomobject]@{ExecutablePath=$taskPath;CreationDate=$taskCreation}
}
try { Stop-UserRuntime; Write-Output 'stopped' } catch { Write-Output 'rejected' }
Write-Output ('publications=' + $script:publications)
Write-Output ('queries=' + $script:queries)
""", environment={"BLUEREEL_STATE": str(tmp_path), "BLUEREEL_SCENARIO": scenario})
    assert result.returncode == 0, result.stderr
    rejected = scenario in {
        "foreign_after_publish", "missing_creation", "invalid_generation", "creation_unavailable_after_publish",
    }
    unpublished = scenario in {"missing_status", "gone", "missing_creation", "invalid_generation"}
    expected_queries = 0 if scenario == "missing_status" else 1 if unpublished else 3 if scenario == "changed_status" else 2
    assert result.stdout.splitlines() == [
        "rejected" if rejected else "stopped", f"publications={0 if unpublished else 1}", f"queries={expected_queries}",
    ]
    command = tmp_path / "tray-command.json"
    if scenario == "consumed":
        assert not command.exists()
    elif unpublished or scenario == "newer_command":
        assert json.loads(command.read_text()) == {"action": "restart", "created_at": 99}
    else:
        value = json.loads(command.read_text())
        assert value["runtime_id"] == "a" * 32 and value["target_pid"] == 123
        assert value["action"] == "exit" and len(value["maintenance_id"]) == 32


@pytest.mark.parametrize("architecture", ["System32", "SysWOW64"])
def test_stop_publication_uses_real_guarded_atomic_writer(tmp_path: Path, architecture: str) -> None:
    powershell = Path(os.environ["WINDIR"]) / architecture / "WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.exists():
        pytest.skip("Requested PowerShell architecture is unavailable")
    result = evaluate(r"""
$taskPython = $env:BLUEREEL_PYTHON
$taskCommand = Join-Path $env:BLUEREEL_STATE 'tray-command.json'
[IO.File]::WriteAllText($taskCommand,'{"old":true}')
$taskMessage = @{action='exit';created_at=99;maintenance_id=('a'*32);target_pid=123;runtime_id=('b'*32)} | ConvertTo-Json -Compress
Write-StopCommand $taskCommand $taskMessage
Write-Output 'published'
""", powershell=str(powershell), environment={"BLUEREEL_STATE": str(tmp_path), "BLUEREEL_PYTHON": sys.executable})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "published"
    assert json.loads((tmp_path / "tray-command.json").read_text()) == {
        "action": "exit", "created_at": 99, "maintenance_id": "a" * 32, "target_pid": 123, "runtime_id": "b" * 32,
    }
    assert not list(tmp_path.glob(".bluereel-*"))
