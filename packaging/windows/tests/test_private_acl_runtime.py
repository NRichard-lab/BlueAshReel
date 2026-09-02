"""Real ACL mutations are restricted to newly generated pytest temporary trees."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "install.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL, reason="Windows PowerShell required")

HARNESS = r"""
$ErrorActionPreference = 'Stop'
$errors = $null; $tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_TEST_INSTALLER,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Installer parse failed' }
$function = $ast.Find({param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq 'Initialize-PrivateAclRuntime'},$true)
. ([scriptblock]::Create($function.Extent.Text))
Initialize-PrivateAclRuntime
$taskBase = [IO.Path]::GetFullPath($env:BLUEREEL_TEST_BASE)
$taskRoot = Join-Path $taskBase 'private'
if (-not (Test-Path -LiteralPath (Join-Path $taskBase 'pytest-disposable-fixture'))) { throw 'Disposable test marker required' }
if (-not $taskRoot.StartsWith($taskBase + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Test escaped fixture' }
$taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$taskAcl = [Security.AccessControl.DirectorySecurity]::new()
$taskAcl.SetOwner($taskIdentity)
$taskAcl.SetAccessRuleProtection($true,$false)
$taskInheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
$taskService = ([Security.Principal.NTAccount]::new('NT SERVICE','wuauserv')).Translate([Security.Principal.SecurityIdentifier])
$taskAllowed = [Collections.Generic.Dictionary[string,int]]::new([StringComparer]::Ordinal)
foreach ($taskSid in @($taskIdentity,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'),$taskService)) {
    $taskRights = if ($taskSid.Value -eq $taskService.Value) { [Security.AccessControl.FileSystemRights]::Modify } else { [Security.AccessControl.FileSystemRights]::FullControl }
    $taskRule = [Security.AccessControl.FileSystemAccessRule]::new($taskSid,$taskRights,$taskInheritance,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
    $taskAcl.AddAccessRule($taskRule)
    $taskAllowed.Add($taskSid.Value,[int]$taskRule.FileSystemRights)
}
$taskMode = $env:BLUEREEL_TEST_MODE
if ($taskMode -eq 'unsafe_parent') {
    $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),[Security.AccessControl.FileSystemRights]::ReadAndExecute,$taskInheritance,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))
}
Set-Acl -LiteralPath $taskRoot -AclObject $taskAcl
$taskTarget = Join-Path $taskRoot 'child.txt'
if ($taskMode -eq 'acl') {
    # Simulate hostile retained explicit ACEs on an otherwise owned child.
    $taskChildAcl = Get-Acl -LiteralPath $taskTarget
    $taskChildAcl.SetAccessRuleProtection($true,$true)
    $taskChildAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow))
    Set-Acl -LiteralPath $taskTarget -AclObject $taskChildAcl
}
$taskOutside = Join-Path $taskBase 'external.txt'
$taskOutsideBefore = (Get-Acl -LiteralPath $taskOutside).Sddl
if ($taskMode -eq 'hardlink') {
    # The link exists only after the root DACL was established, simulating a
    # late replacement between bulk preflight and this same-handle check.
    Remove-Item -LiteralPath $taskTarget
    New-Item -ItemType HardLink -Path $taskTarget -Target $taskOutside | Out-Null
}
if ($taskMode -eq 'outside') { $taskTarget = $taskOutside }
if ($taskMode -eq 'wrong_type') { $taskTarget = Join-Path $taskRoot 'nested' }
if ($taskMode -eq 'junction') {
    $taskJunction = Join-Path $taskRoot 'late-junction'
    New-Item -ItemType Junction -Path $taskJunction -Target (Join-Path $taskBase 'external') | Out-Null
    $taskTarget = Join-Path $taskJunction 'outside.txt'
}
$taskBefore = (Get-Acl -LiteralPath $taskTarget).Sddl
$taskFileLock = $null
if ($taskMode -eq 'locked') { $taskFileLock = [IO.File]::Open($taskTarget,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None) }
$taskSuccess = $true; $taskFailure = $null
$taskWatch = [Diagnostics.Stopwatch]::StartNew()
try {
    if ($taskMode -eq 'configuration') {
        $taskConfig = Join-Path $taskRoot 'nested'
        $taskReadOnly = [Collections.Generic.Dictionary[string,int]]::new($taskAllowed,[StringComparer]::Ordinal)
        $taskReadOnly[$taskService.Value] = [int]([Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [Security.AccessControl.FileSystemRights]::Synchronize)
        [BlueReel.NativePrivateAcl]::ProtectEntry($taskRoot,$taskConfig,$taskIdentity.Value,$taskAllowed,$taskReadOnly)
        $taskTarget = Join-Path $taskConfig 'file-0000.txt'
        [BlueReel.NativePrivateAcl]::ResetEntry($taskConfig,$taskTarget,$false,$taskIdentity.Value,$taskReadOnly)
        $taskAllowed = $taskReadOnly
    } elseif ($taskMode -eq 'bulk') {
        $taskNested = Join-Path $taskRoot 'nested'
        [BlueReel.NativePrivateAcl]::ResetEntry($taskRoot,$taskNested,$true,$taskIdentity.Value,$taskAllowed)
        foreach ($taskFile in @(Get-ChildItem -LiteralPath $taskNested -File)) {
            [BlueReel.NativePrivateAcl]::ResetEntry($taskRoot,$taskFile.FullName,$false,$taskIdentity.Value,$taskAllowed)
        }
    } else {
        [BlueReel.NativePrivateAcl]::ResetEntry($taskRoot,$taskTarget,$false,$taskIdentity.Value,$taskAllowed)
    }
} catch { $taskSuccess = $false; $taskFailure = $_.Exception.Message }
finally { if ($taskFileLock) { $taskFileLock.Dispose() } }
$taskWatch.Stop()
$taskAfter = Get-Acl -LiteralPath $taskTarget
$taskRules = @($taskAfter.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object { @{sid=$_.IdentityReference.Value; inherited=$_.IsInherited; mask=[int]$_.FileSystemRights} })
$taskResult = [ordered]@{success=$taskSuccess; error=$taskFailure; milliseconds=$taskWatch.Elapsed.TotalMilliseconds; unchanged=($taskBefore -eq $taskAfter.Sddl); external_unchanged=($taskOutsideBefore -eq (Get-Acl -LiteralPath $taskOutside).Sddl); owner=$taskAfter.GetOwner([Security.Principal.SecurityIdentifier]).Value; expected_owner=$taskIdentity.Value; protected=$taskAfter.AreAccessRulesProtected; rules=$taskRules; allowed=$taskAllowed}
# Remove only the generated links, retaining both external fixture targets.
if ($taskMode -eq 'junction') { [IO.Directory]::Delete($taskJunction) }
if ($taskMode -eq 'hardlink') { [IO.File]::Delete($taskTarget) }
$taskResult | ConvertTo-Json -Depth 8 -Compress
"""


def run_runtime(tmp_path: Path, mode: str, *, bulk_files: int = 0) -> dict[str, Any]:
    assert POWERSHELL
    (tmp_path / "pytest-disposable-fixture").write_text("disposable test data")
    root = tmp_path / "private"
    root.mkdir()
    (root / "child.txt").write_text("synthetic application data")
    (root / "nested").mkdir()
    (tmp_path / "external.txt").write_text("untouched synthetic source")
    (tmp_path / "external").mkdir()
    (tmp_path / "external/outside.txt").write_text("untouched synthetic source")
    for index in range(bulk_files):
        (root / "nested" / f"file-{index:04}.txt").write_text("synthetic upgrade file")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(HARNESS.encode("utf-16-le")).decode()],
        env={**{key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"},
             "BLUEREEL_TEST_INSTALLER": str(SOURCE), "BLUEREEL_TEST_BASE": str(tmp_path), "BLUEREEL_TEST_MODE": mode},
        capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stderr
    value: dict[str, Any] = json.loads(result.stdout)
    assert (tmp_path / "external.txt").read_text() == "untouched synthetic source"
    assert (tmp_path / "external/outside.txt").read_text() == "untouched synthetic source"
    return value


def test_real_child_reset_removes_explicit_everyone_and_inherits_only_private_parent(tmp_path: Path) -> None:
    result = run_runtime(tmp_path, "acl")
    assert result["success"], result["error"]
    assert result["protected"] is False and result["owner"] == result["expected_owner"]
    assert {row["sid"]: row["mask"] for row in result["rules"]} == result["allowed"]
    assert all(row["inherited"] for row in result["rules"])
    assert result["external_unchanged"]


@pytest.mark.parametrize("mode", ["hardlink", "junction", "outside", "unsafe_parent", "wrong_type", "locked"])
def test_late_unsafe_target_or_parent_fails_before_child_acl_mutation(tmp_path: Path, mode: str) -> None:
    result = run_runtime(tmp_path, mode)
    assert result["success"] is False, result
    assert result["unchanged"] and result["external_unchanged"], result


def test_thousand_upgrade_files_use_in_process_checked_acl_resets(tmp_path: Path) -> None:
    result = run_runtime(tmp_path, "bulk", bulk_files=1000)
    assert result["success"], result["error"]
    assert result["milliseconds"] < 20000, result
    assert result["external_unchanged"]
    print(f"1001 verified native ACL updates: {result['milliseconds']:.1f} ms")


def test_configuration_boundary_removes_service_write_and_delete_permissions(tmp_path: Path) -> None:
    result = run_runtime(tmp_path, "configuration", bulk_files=1)
    assert result["success"], result["error"]
    assert result["protected"] is False and result["owner"] == result["expected_owner"]
    assert {row["sid"]: row["mask"] for row in result["rules"]} == result["allowed"]
    services = [row for row in result["rules"] if row["sid"].startswith("S-1-5-80-")]
    assert len(services) == 1 and services[0]["mask"] == 0x1200A9  # read/execute + synchronize
    assert services[0]["mask"] & 0x000D0156 == 0  # no write data/append/delete/DACL/owner
    assert all(row["inherited"] for row in result["rules"])
    assert result["external_unchanged"]
