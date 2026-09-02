"""LAN acceptance tests never invoke the installed helper or mutate host policy."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "test_private_lan.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell required")
LOAD = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Import-Module Microsoft.PowerShell.Utility
$taskTokens=$null; $taskErrors=$null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_TEST_SOURCE,[ref]$taskTokens,[ref]$taskErrors)
if ($taskErrors.Count) { throw 'Source did not parse' }
foreach ($taskFunction in $taskAst.FindAll({param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst]},$true)) {
    . ([scriptblock]::Create($taskFunction.Extent.Text))
}
$LocalIpv4 = '192.168.50.228'
$TaskPrefix = 'BlueReelDevelopment'
$TaskRuleName = 'BlueReelDevelopment-Inbound-PrivateLAN'
$TaskInstaller = 'C:\fixed-program\support\install.ps1'
$TaskCaddy = 'C:\fixed-program\runtime\caddy\caddy.exe'
$TaskReport = [ordered]@{passed=$false;restored_loopback=$false;remote_device_verified=$false}
$taskCase = $env:BLUEREEL_TEST_CASE
"""


def run(script: str, case: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode((LOAD + script).encode("utf-16-le")).decode()],
        env={**{key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"},
             "BLUEREEL_TEST_SOURCE": str(SOURCE), "BLUEREEL_TEST_CASE": case},
        capture_output=True, text=True, timeout=25, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def test_entrypoint_requires_optin_before_privilege_or_host_access(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SOURCE),
         "-LocalIpv4", "192.168.50.228", "-ReportDirectory", str(tmp_path)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode != 0
    assert "Disposable private-LAN acceptance did not pass" in " ".join(result.stderr.split())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("case,passed", [
    ("private", True), ("public", False), ("unassigned", False), ("duplicate", False),
    ("not_preferred", False), ("wildcard", False), ("loopback", False), ("internet", False),
    ("linklocal", False), ("hostname", False), ("ipv6", False), ("shorthand", False),
])
def test_interface_preflight_rejects_unsafe_or_unassigned_addresses(case: str, passed: bool) -> None:
    result = run(r"""
$script:taskQueries = 0
function Get-NetIPAddress {
    param($AddressFamily,$IPAddress)
    $script:taskQueries++
    if ($taskCase -eq 'unassigned') { return }
    $taskRecord = [pscustomobject]@{InterfaceIndex=10;AddressState=$(if($taskCase -eq 'not_preferred'){'Tentative'}else{'Preferred'})}
    $taskRecord
    if ($taskCase -eq 'duplicate') { $taskRecord }
}
function Get-NetConnectionProfile { param($InterfaceIndex); [pscustomobject]@{NetworkCategory=$(if($taskCase -eq 'public'){'Public'}else{'Private'})} }
$taskAddress = switch ($taskCase) {
    'wildcard' {'0.0.0.0'} 'loopback' {'127.0.0.1'} 'internet' {'8.8.8.8'} 'linklocal' {'169.254.1.2'}
    'hostname' {'private-host'} 'ipv6' {'::1'} 'shorthand' {'192.168.01.2'} default {$LocalIpv4}
}
$taskPassed=$true
try { $null=Assert-PrivateInterface $taskAddress } catch { $taskPassed=$false }
@{passed=$taskPassed;queries=$script:taskQueries} | ConvertTo-Json -Compress
""", case)
    assert result["passed"] is passed
    if case in {"wildcard", "loopback", "internet", "linklocal", "hostname", "ipv6", "shorthand"}:
        assert result["queries"] == 0


@pytest.mark.parametrize("case", ["valid", "profile", "program", "remote", "local", "port", "protocol", "group", "disabled"])
def test_lan_rule_requires_every_narrow_policy_field(case: str) -> None:
    result = run(r"""
function Get-NetFirewallRule { param($Name,$PolicyStore)
    [pscustomobject]@{Group=$(if($taskCase -eq 'group'){'foreign'}else{$TaskPrefix});Enabled=$(if($taskCase -eq 'disabled'){'False'}else{'True'});Direction='Inbound';Action='Allow';Profile=$(if($taskCase -eq 'profile'){'Any'}else{'Private'})}
}
function Get-NetFirewallAddressFilter {
    [pscustomobject]@{LocalAddress=$(if($taskCase -eq 'local'){'Any'}else{$LocalIpv4});RemoteAddress=$(if($taskCase -eq 'remote'){'Any'}else{'LocalSubnet'})}
}
function Get-NetFirewallPortFilter {
    [pscustomobject]@{Protocol=$(if($taskCase -eq 'protocol'){'UDP'}else{'TCP'});LocalPort=$(if($taskCase -eq 'port'){'Any'}else{'18080'});RemotePort='Any'}
}
function Get-NetFirewallApplicationFilter { [pscustomobject]@{Program=$(if($taskCase -eq 'program'){'Any'}else{$TaskCaddy})} }
$taskPassed=$true
try { Assert-LanRule } catch { $taskPassed=$false }
@{passed=$taskPassed} | ConvertTo-Json -Compress
""", case)
    assert result["passed"] is (case == "valid")


@pytest.mark.parametrize("case", ["success", "enable_failure", "rule_failure", "health_failure", "restore_failure", "foreign_change", "env_change"])
def test_lifecycle_always_attempts_loopback_restore_and_checks_original_state(case: str) -> None:
    result = run(r"""
$script:taskCalls = [Collections.Generic.List[string]]::new()
$script:taskSnapshots = 0
$script:taskConfigReads = 0
function Get-SafetySnapshot {
    $script:taskSnapshots++
    @{profiles='same';unrelated_rules=$(if($taskCase -eq 'foreign_change' -and $script:taskSnapshots -gt 1){'changed'}else{'same'})}
}
function Get-ConfigurationHashes {
    $script:taskConfigReads++
    @{env=$(if($taskCase -eq 'env_change' -and $script:taskConfigReads -gt 1){'changed'}else{'same'});metadata='same'}
}
function Get-FileHash { param($LiteralPath,$Algorithm); [pscustomobject]@{Hash='same-protected-source'} }
function Invoke-ConfigureNetwork { param($Address)
    $script:taskCalls.Add($Address)
    if (($taskCase -eq 'enable_failure' -and $Address -eq $LocalIpv4) -or ($taskCase -eq 'restore_failure' -and $Address -eq '127.0.0.1')) { throw 'synthetic private failure details must not appear' }
}
function Assert-LanRule { if($taskCase -eq 'rule_failure'){throw 'rule failure'} }
function Assert-Listeners { param($Enabled) }
function Assert-Health { param($Address); if($taskCase -eq 'health_failure' -and $Address -eq $LocalIpv4){throw 'health failure'} }
function Get-NetFirewallRule { param($Name) }
$taskFailed=$false
try { Invoke-LanLifecycle } catch { if($taskCase -eq 'success'){throw}; $taskFailed=$true }
@{failed=$taskFailed;report=$TaskReport;calls=@($script:taskCalls.ToArray());snapshots=$script:taskSnapshots} | ConvertTo-Json -Depth 8 -Compress
""", case)
    assert result["calls"] == ["192.168.50.228", "127.0.0.1"]
    assert result["failed"] is (case != "success")
    assert result["report"]["passed"] is (case == "success")
    assert result["report"]["restored_loopback"] is (case not in {"restore_failure", "foreign_change", "env_change"})
    assert result["report"]["remote_device_verified"] is False
    assert "synthetic private failure" not in json.dumps(result)


def test_helper_has_only_fixed_installed_network_workflow_as_mutation_path() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for forbidden in ("New-NetFirewallRule", "Set-NetFirewallProfile", "Set-NetConnectionProfile", "Remove-NetFirewallRule",
                      "Start-Service", "Stop-Service", "Set-ItemProperty", "0.0.0.0", "http://8.8.8.8"):
        assert forbidden not in source
    assert "'ConfigureNetwork'" in source and "'support\\install.ps1'" in source
    assert "Assert-ProtectedInstaller" in source and "Get-SafetySnapshot" in source
    assert "FileMode]::CreateNew" in source and "$taskStart.CreateNoWindow = $true" in source
