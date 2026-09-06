"""Exercise privileged migration decisions using disposable service doubles only."""
from __future__ import annotations

import base64
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
    return [pscustomobject]@{ExecutablePath=$taskPath}
}
try { Stop-UserRuntime; Write-Output 'stopped' } catch { Write-Output 'rejected' }
Write-Output ('queries=' + $script:queries)
""", powershell=str(powershell), environment={"BLUEREEL_STATE": str(tmp_path), "BLUEREEL_IDENTITY": identity})
    assert result.returncode == 0, result.stderr
    expected = "stopped" if identity in {"matching", "gone"} else "rejected"
    count = 2 if identity == "matching" else 0 if identity == "invalid_pid" else 1
    assert result.stdout.splitlines() == [expected, f"queries={count}"]
