"""Native GUI helper guards; never launch an installer or inspect desktop UI."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
HELPER = DIRECTORY / "test_installer_ui.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only native UI helper")

EXTRACT = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskTokens = $null; $taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_TEST_HELPER,[ref]$taskTokens,[ref]$taskErrors)
if ($taskErrors.Count) { throw 'Parse failed' }
foreach ($taskName in ($env:BLUEREEL_TEST_FUNCTIONS -split ',' | Where-Object { $_ })) {
    $taskFunction = $taskAst.Find({ param($item)
        $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq $taskName
    },$true)
    if ($null -eq $taskFunction) { throw 'Missing test function' }
    . ([scriptblock]::Create($taskFunction.Extent.Text))
}
"""


def powershell(script: str, functions: list[str] | None = None, **environment: str) -> subprocess.CompletedProcess[str]:
    shell = shutil.which("powershell.exe")
    assert shell
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode((EXTRACT + script).encode("utf-16-le")).decode()],
        env={**os.environ, "BLUEREEL_TEST_HELPER": str(HELPER),
             "BLUEREEL_TEST_FUNCTIONS": ",".join(functions or []), **environment},
        capture_output=True, text=True, timeout=30, check=True,
    )


@pytest.mark.parametrize("switches", [[], ["-AllowDisposableInstallerUI"], ["-ConfirmOwnedTestMedia"]])
def test_gui_requires_both_explicit_opt_ins_before_any_process_or_report(tmp_path: Path, switches: list[str]) -> None:
    shell = shutil.which("powershell.exe")
    assert shell
    report = tmp_path / "must-not-be-created"
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(HELPER),
         "-InstallerPath", str(tmp_path / "not-an-installer.exe"), "-InstallerSha256", "0" * 64,
         "-MediaRoot", str(tmp_path), "-ReportDirectory", str(report), *switches],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "native_gui_passed": False, "report_written": False, "browser_load_verified": False,
    }
    assert not report.exists()


def test_gui_powershell_parses_and_csharp_compiles_without_ui_calls() -> None:
    result = powershell(r"""
$taskSources = @($taskAst.FindAll({ param($item)
    $item -is [Management.Automation.Language.StringConstantExpressionAst] -and $item.Value.StartsWith('using System;')
},$true))
if ($taskSources.Count -ne 1) { throw 'Unexpected native helper source' }
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition $taskSources[0].Value -ReferencedAssemblies @('UIAutomationClient','UIAutomationTypes','WindowsBase','System.Core','System')
if (-not ('BlueReelGuiAutomation' -as [type])) { throw 'Native helper did not compile' }
'compiled_without_ui_calls'
""")
    assert result.stdout.strip() == "compiled_without_ui_calls"


@pytest.mark.parametrize("scenario", ["missing", "multiple", "foreign", "wrong_wizard", "disabled", "offscreen", "owned"])
def test_unique_control_gate_refuses_ambiguous_foreign_or_unavailable_elements(scenario: str) -> None:
    result = powershell(r"""
$TaskKnownProcesses = @{42=1; 43=1}
$TaskWizardProcessId = 42
$taskCurrent = [pscustomobject]@{ProcessId=42; IsEnabled=$true; IsOffscreen=$false}
$taskElement = [pscustomobject]@{Current=$taskCurrent}
$taskElements = @($taskElement)
switch ($env:BLUEREEL_TEST_SCENARIO) {
    'missing' { $taskElements = @() }
    'multiple' { $taskElements = @($taskElement,$taskElement) }
    'foreign' { $taskCurrent.ProcessId = 99 }
    'wrong_wizard' { $taskCurrent.ProcessId = 43 }
    'disabled' { $taskCurrent.IsEnabled = $false }
    'offscreen' { $taskCurrent.IsOffscreen = $true }
}
$taskAccepted = $false
try { $null = Select-UniqueElement $taskElements; $taskAccepted = $true } catch {}
@{accepted=$taskAccepted} | ConvertTo-Json -Compress
""", ["Assert-OwnedElement", "Select-UniqueElement"], BLUEREEL_TEST_SCENARIO=scenario)
    assert json.loads(result.stdout) == {"accepted": scenario == "owned"}


def test_lineage_adopts_only_descendants_and_refuses_reused_process_ids() -> None:
    result = powershell(r"""
$TaskStarted = [DateTime]::UtcNow.AddMinutes(-1)
$taskOldDate = $TaskStarted.AddSeconds(1)
$taskNewDate = $TaskStarted.AddSeconds(2)
$TaskKnownProcesses = @{400=$taskOldDate.Ticks; 600=$taskOldDate.Ticks}
function Get-CimInstance {
    [pscustomobject]@{ProcessId=400;ParentProcessId=1;CreationDate=$taskOldDate}
    [pscustomobject]@{ProcessId=501;ParentProcessId=500;CreationDate=$taskNewDate}
    [pscustomobject]@{ProcessId=500;ParentProcessId=400;CreationDate=$taskNewDate}
    [pscustomobject]@{ProcessId=600;ParentProcessId=99;CreationDate=$taskNewDate}
    [pscustomobject]@{ProcessId=700;ParentProcessId=99;CreationDate=$taskNewDate}
}
Update-InstallerLineage
@($TaskKnownProcesses.Keys | Sort-Object) | ConvertTo-Json -Compress
""", ["Update-InstallerLineage"])
    assert json.loads(result.stdout) == [400, 500, 501]


def test_safe_tree_refuses_actual_junction_and_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "not-for-enumeration.txt").write_text("fixture remains unchanged")
    result = powershell(r"""
$taskRoot = $env:BLUEREEL_TEST_ROOT
$taskOutside = $env:BLUEREEL_TEST_OUTSIDE
$null = New-Item -ItemType Junction -Path (Join-Path $taskRoot 'junction') -Target $taskOutside
$taskRejected = @()
foreach ($taskPath in @('relative-path',(Join-Path $taskRoot '..\outside'),$taskRoot)) {
    $taskFailed = $false
    try { $null = @(Get-SafeFiles $taskPath) } catch { $taskFailed = $true }
    $taskRejected += $taskFailed
}
$taskRejected | ConvertTo-Json -Compress
""", ["Assert-SafePath", "Get-SafeFiles"], BLUEREEL_TEST_ROOT=str(root), BLUEREEL_TEST_OUTSIDE=str(outside))
    assert json.loads(result.stdout) == [True, True, True]
    assert (outside / "not-for-enumeration.txt").read_text() == "fixture remains unchanged"


def test_failure_diagnostics_redact_folder_names_and_ignore_foreign_process_controls() -> None:
    result = powershell(r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$TaskWizardProcessId = 42
$script:taskFakeControls = @(
    [pscustomobject]@{Current=[pscustomobject]@{ProcessId=42; Name='private-folder-name'; AutomationId='private_identifier';
        ControlType=[pscustomobject]@{ProgrammaticName='ControlType.Edit'}; IsEnabled=$true; IsOffscreen=$false}},
    [pscustomobject]@{Current=[pscustomobject]@{ProcessId=42; Name='Add folder...'; AutomationId='1001';
        ControlType=[pscustomobject]@{ProgrammaticName='ControlType.Button'}; IsEnabled=$true; IsOffscreen=$false}},
    [pscustomobject]@{Current=[pscustomobject]@{ProcessId=99; Name='foreign-window-text'; AutomationId='9999'}}
)
$script:taskFakeWindow = [pscustomobject]@{Current=[pscustomobject]@{ProcessId=42}}
$script:taskFakeWindow | Add-Member -MemberType ScriptMethod -Name FindAll -Value { return $script:taskFakeControls }
function Get-OwnedNativeWindows { return @($script:taskFakeWindow) }
@(Get-NativeFailureDiagnostics) | ConvertTo-Json -Depth 5 -Compress
""", ["Get-NativeFailureDiagnostics"])
    rows = json.loads(result.stdout)
    assert len(rows) == 2
    assert rows[0]["name"] == "[redacted]" and rows[0]["automation_id"] == "[redacted]"
    assert rows[1]["name"] == "Add folder..." and rows[1]["automation_id"] == "1001"
    assert "private" not in result.stdout and "foreign" not in result.stdout


def test_source_scopes_every_ui_action_and_keeps_browser_verification_separate() -> None:
    source = HELPER.read_text(encoding="utf-8")
    assert "-WindowStyle Hidden -PassThru" in source
    assert "-WindowStyle Normal" not in source
    assert "SetForegroundWindow" not in source and "SendWait(" not in source
    assert "::ProcessIdProperty,[int]$taskKey" in source
    assert "Current.ProcessId -eq $TaskWizardProcessId" in source
    assert source.index("$TaskWizardProcessId = $null") < source.index("function Assert-OwnedElement")
    assert "if ($Elements.Count -ne 1)" in source
    assert "browser_verification='requires_browser_skill'" in source
    assert "$TaskEvidence.browser_load_verified = $true" not in source
    assert "-cne 'BlueReel-Setup-Development-x64.exe'" in source
    assert "Get-FileHash -LiteralPath $taskInstaller -Algorithm SHA256" in source
    assert ".Status -ne 'NotSigned'" in source
    assert "if (Test-Path -LiteralPath $TaskData)" in source
    assert "HKLM:\\Software\\BlueReel\\development" in source
    assert "'BlueReel-Development-TestMedia'" in source
    assert "$taskDialogHandle = [int]$taskDialog.Current.NativeWindowHandle" in source
    assert "$_.Current.NativeWindowHandle -eq $taskDialog.Current.NativeWindowHandle" not in source
    assert "Environment.Exit(124)" in source  # Deadline terminates only this helper, never the installer.
    assert "Stop-Process" not in source and "taskkill" not in source.lower()
