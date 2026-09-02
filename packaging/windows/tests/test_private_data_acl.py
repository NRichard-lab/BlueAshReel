"""Installer preflight plus handle-based ACL tests on disposable NTFS fixtures."""

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

INSTALLER = Path(__file__).resolve().parents[1] / "install.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL, reason="Windows PowerShell required")

HARNESS = r"""
$ErrorActionPreference = 'Stop'
$taskParseErrors = $null
$taskTokens = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_TEST_INSTALLER, [ref]$taskTokens, [ref]$taskParseErrors)
if ($taskParseErrors.Count) { throw 'Installer parse failed' }
$taskFunction = $taskAst.Find({ param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq 'Set-PrivateDataAcl' }, $true)
if ($null -eq $taskFunction) { throw 'ACL function missing' }
. ([scriptblock]::Create($taskFunction.Extent.Text))
$DataDir = $env:BLUEREEL_TEST_DATA
$TaskPython = $env:BLUEREEL_TEST_PYTHON
$TaskPrefix = $env:BLUEREEL_TEST_PREFIX
$TaskRoles = if ($env:BLUEREEL_TEST_SERVICE -eq 'true') { @('') } else { @('API','Worker','Web','Proxy') }
$script:taskAclCalls = 0
$script:taskCommands = [Collections.Generic.List[object]]::new()
$script:taskRules = @()
$script:taskOwner = $null
$script:taskProtected = $false
function Get-Service {
    [CmdletBinding()] param([string]$Name)
    if ($env:BLUEREEL_TEST_SERVICE -eq 'true') { [pscustomobject]@{Name=$Name} }
}
function Set-Acl {
    param([string]$LiteralPath, [Security.AccessControl.DirectorySecurity]$AclObject)
    $script:taskAclCalls++
    $script:taskOwner = $AclObject.GetOwner([Security.Principal.SecurityIdentifier]).Value
    $script:taskProtected = $AclObject.AreAccessRulesProtected
    $script:taskRules = @($AclObject.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object {
        [ordered]@{sid=$_.IdentityReference.Value; rights=$_.FileSystemRights.ToString(); type=$_.AccessControlType.ToString(); inheritance=$_.InheritanceFlags.ToString()}
    })
}
function Invoke-Checked {
    param([string]$File,[string[]]$Arguments)
    $script:taskCommands.Add([ordered]@{file=$File; arguments=$Arguments})
}
function Set-PrivateDataEntryAcl {
    param([string]$Root,[string]$Path,[bool]$Directory,[string]$OwnerSid,[Collections.Generic.Dictionary[string,int]]$Allowed)
    $script:taskCommands.Add([ordered]@{file='NativePrivateAcl'; arguments=@($Path); root=$Root; directory=$Directory; owner=$OwnerSid; allowed=$Allowed})
}
$taskSucceeded = $true
$taskMessage = $null
try { Set-PrivateDataAcl } catch { $taskSucceeded = $false; $taskMessage = $_.Exception.Message }
[ordered]@{success=$taskSucceeded; error=$taskMessage; acl_calls=$taskAclCalls; owner=$taskOwner; protected=$taskProtected; rules=@($taskRules); commands=@($taskCommands.ToArray())} | ConvertTo-Json -Depth 8 -Compress
"""


def run_preflight(data: Path, *, prefix: str = "BlueReelDevelopment", service: bool = False) -> dict[str, Any]:
    assert POWERSHELL
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(HARNESS.encode("utf-16-le")).decode()],
        env={**os.environ, "BLUEREEL_TEST_INSTALLER": str(INSTALLER), "BLUEREEL_TEST_DATA": str(data),
             "BLUEREEL_TEST_PREFIX": prefix, "BLUEREEL_TEST_SERVICE": str(service).lower(),
             "BLUEREEL_TEST_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=30, check=True,
    )
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def assert_preserved(result: dict[str, Any]) -> None:
    assert result["success"] is False
    assert result["acl_calls"] == 0
    assert result["commands"] == []


@pytest.mark.parametrize("state", ["unrelated_file", "unknown_directory", "nonempty_known_directory"])
def test_unmarked_foreign_contents_rejected_before_permission_changes(tmp_path: Path, state: str) -> None:
    data = tmp_path / "data"
    data.mkdir()
    if state == "unrelated_file":
        (data / "family-notes.txt").write_text("preserve")
    elif state == "unknown_directory":
        (data / "unrelated").mkdir()
    else:
        (data / "database").mkdir()
        (data / "database" / "unrelated.txt").write_text("preserve")
    before = sorted(path.relative_to(data).as_posix() for path in data.rglob("*"))
    result = run_preflight(data)
    assert_preserved(result)
    assert "unmarked" in result["error"]
    assert sorted(path.relative_to(data).as_posix() for path in data.rglob("*")) == before


@pytest.mark.parametrize("marker", ["BlueReel", "BlueReelDevelopmentOther", "bluereeldevelopment", " BlueReelDevelopment", "BlueReelDevelopment ", "x" * 129])
def test_foreign_or_malformed_instance_marker_rejected_before_acl_writes(tmp_path: Path, marker: str) -> None:
    (tmp_path / ".bluereel-native-instance").write_text(marker, encoding="utf-8")
    (tmp_path / "keep.txt").write_text("untouched")
    assert_preserved(run_preflight(tmp_path))
    assert (tmp_path / "keep.txt").read_text() == "untouched"


def test_marker_directory_is_not_an_instance_marker(tmp_path: Path) -> None:
    (tmp_path / ".bluereel-native-instance").mkdir()
    assert_preserved(run_preflight(tmp_path))


def test_empty_state_directories_get_exact_inheritable_allowlist(tmp_path: Path) -> None:
    for name in ("configuration", "database", "data", "artwork", "logs", "temp", "backups", "state", "upgrade"):
        (tmp_path / name).mkdir()
    result = run_preflight(tmp_path)
    assert result["success"], result["error"]
    assert result["acl_calls"] == 1
    assert result["owner"] == "S-1-5-32-544"
    assert result["protected"] is True
    assert {row["sid"] for row in result["rules"]} == {"S-1-5-18", "S-1-5-32-544"}
    assert all(row["rights"] == "FullControl" and row["type"] == "Allow" for row in result["rules"])
    assert all(row["inheritance"] == "ContainerInherit, ObjectInherit" for row in result["rules"])
    assert len(result["commands"]) == 9
    assert all(row["file"] == "NativePrivateAcl" for row in result["commands"])
    assert all(row["owner"] == "S-1-5-32-544" for row in result["commands"])
    assert all(set(row["allowed"]) == {"S-1-5-18", "S-1-5-32-544"} for row in result["commands"])


def test_matching_marked_instance_repair_accepts_existing_state(tmp_path: Path) -> None:
    (tmp_path / ".bluereel-native-instance").write_text("BlueReelDevelopment\n", encoding="utf-8")
    (tmp_path / "database").mkdir()
    (tmp_path / "database" / "app.db").write_bytes(b"synthetic database")
    result = run_preflight(tmp_path)
    assert result["success"], result["error"]
    assert result["acl_calls"] == 1
    assert (tmp_path / "database" / "app.db").read_bytes() == b"synthetic database"
    paths = [row["arguments"][0] for row in result["commands"]]
    assert paths.index(str(tmp_path / "database")) < paths.index(str(tmp_path / "database" / "app.db"))


def test_registered_service_sid_is_preserved_without_broad_localservice_grant(tmp_path: Path) -> None:
    # Resolve the real Windows Update service SID but mock service enumeration
    # and every mutation. No Windows service is queried or changed.
    (tmp_path / ".bluereel-native-instance").write_text("wuauserv\n", encoding="utf-8")
    result = run_preflight(tmp_path, prefix="wuauserv", service=True)
    assert result["success"], result["error"]
    rules = result["rules"]
    assert len(rules) == 3
    service_rules = [row for row in rules if row["sid"].startswith("S-1-5-80-")]
    assert len(service_rules) == 1 and service_rules[0]["rights"] == "Modify, Synchronize"
    assert "S-1-5-19" not in {row["sid"] for row in rules}


def test_junction_rejected_before_any_acl_changes(tmp_path: Path) -> None:
    assert POWERSHELL
    data, external = tmp_path / "data", tmp_path / "external"
    data.mkdir()
    external.mkdir()
    (data / ".bluereel-native-instance").write_text("BlueReelDevelopment\n", encoding="utf-8")
    (external / "keep.txt").write_text("unrelated")
    subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
         "New-Item -ItemType Junction -Path $env:BLUEREEL_TEST_LINK -Target $env:BLUEREEL_TEST_EXTERNAL | Out-Null"],
        env={**os.environ, "BLUEREEL_TEST_LINK": str(data / "junction"), "BLUEREEL_TEST_EXTERNAL": str(external)},
        capture_output=True, check=True, timeout=30,
    )
    result = run_preflight(data)
    assert_preserved(result)
    assert "reparse" in result["error"]
    assert (external / "keep.txt").read_text() == "unrelated"


def test_hardlink_rejected_before_any_acl_changes(tmp_path: Path) -> None:
    data, external = tmp_path / "data", tmp_path / "external.txt"
    data.mkdir()
    external.write_text("unrelated source")
    (data / ".bluereel-native-instance").write_text("BlueReelDevelopment\n", encoding="utf-8")
    os.link(external, data / "linked-file.txt")
    assert external.stat().st_nlink == 2
    result = run_preflight(data)
    assert_preserved(result)
    assert "hard link" in result["error"]
    assert external.read_text() == "unrelated source"
