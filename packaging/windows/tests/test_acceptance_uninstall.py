"""Uninstall acceptance regression tests; only disposable pytest files mutate."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "test_instance.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell and sharing semantics")
REPORT_NAME = "UninstallPreserve-20260902T025658-94c732c3ef334300952d2af557a4615c.json"


def invoke(script: str, functions: list[str], directory: Path, **environment: str) -> dict[str, object]:
    shell = shutil.which("powershell.exe")
    assert shell
    setup = r"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$taskTokens=$null; $taskErrors=$null
$taskAst=[Management.Automation.Language.Parser]::ParseFile($env:BLUEREEL_TEST_HELPER,[ref]$taskTokens,[ref]$taskErrors)
if ($taskErrors.Count) { throw 'Parse failed' }
foreach ($taskName in ($env:BLUEREEL_TEST_FUNCTIONS -split ',')) {
    $taskFunction=$taskAst.Find({param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq $taskName},$true)
    . ([scriptblock]::Create($taskFunction.Extent.Text))
}
$ReportDirectory=$env:BLUEREEL_TEST_DIRECTORY
"""
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode((setup + script).encode("utf-16-le")).decode()],
        env={**os.environ, "BLUEREEL_TEST_HELPER": str(HELPER), "BLUEREEL_TEST_DIRECTORY": str(directory),
             "BLUEREEL_TEST_FUNCTIONS": ",".join(functions), **environment},
        capture_output=True, text=True, timeout=25, check=True,
    )
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def test_private_log_cleanup_retries_actual_sharing_violation(tmp_path: Path) -> None:
    private = tmp_path / ("private-" + "a" * 32)
    private.mkdir()
    log = private / "uninstall.log"
    log.write_bytes(b"synthetic private log never emitted")
    stream = log.open("rb")  # CPython's ordinary Windows open denies delete sharing.
    release = threading.Timer(2, stream.close)
    release.start()
    try:
        result = invoke("Remove-PrivateUninstallLog $env:BLUEREEL_TEST_PRIVATE | ConvertTo-Json -Compress",
                        ["Assert-NoReparse", "Remove-PrivateUninstallLog"], tmp_path, BLUEREEL_TEST_PRIVATE=str(private))
    finally:
        release.cancel()
        release.join()
        stream.close()
    assert result["removed"] is True and int(str(result["attempts"])) > 1
    assert not private.exists()


@pytest.mark.parametrize("scenario", ["unknown_child", "foreign_directory", "junction"])
def test_private_log_cleanup_never_recurses_or_deletes_foreign_targets(tmp_path: Path, scenario: str) -> None:
    private = tmp_path / ("private-" + "b" * 32)
    private.mkdir()
    log = private / "uninstall.log"
    log.write_bytes(b"preserve this fixture")
    if scenario == "unknown_child":
        (private / "foreign.txt").write_bytes(b"also preserved")
    elif scenario == "foreign_directory":
        renamed = tmp_path / "unrelated"
        private.rename(renamed)
        private = renamed
        log = private / "uninstall.log"
    script = "Remove-PrivateUninstallLog $env:BLUEREEL_TEST_PRIVATE | ConvertTo-Json -Compress"
    if scenario == "junction":
        script = r"""
$taskLink = Join-Path $ReportDirectory ('private-' + ('c' * 32))
$null=New-Item -ItemType Junction -Path $taskLink -Target $env:BLUEREEL_TEST_PRIVATE
Remove-PrivateUninstallLog $taskLink | ConvertTo-Json -Compress
"""
    result = invoke(script, ["Assert-NoReparse", "Remove-PrivateUninstallLog"], tmp_path, BLUEREEL_TEST_PRIVATE=str(private))
    assert result["removed"] is False
    assert log.read_bytes() == b"preserve this fixture"


@pytest.mark.parametrize("scenario", ["valid", "wrong_phase", "failed_uninstaller", "invalid_digest", "wrong_scope", "foreign_name"])
def test_prior_evidence_requires_exact_successful_preserve_uninstall(tmp_path: Path, scenario: str) -> None:
    document = {
        "schema_version": 1, "instance": "development", "phase": "UninstallPreserve", "uninstaller_exit_code": 0,
        "preserved_before": {"file_count": 10087, "sha256": "e" * 64, "excludes_volatile_logs_state_temp": True},
    }
    if scenario == "wrong_phase":
        document["phase"] = "UninstallPurge"
    if scenario == "failed_uninstaller":
        document["uninstaller_exit_code"] = 1
    if scenario == "invalid_digest":
        document["preserved_before"] = {"file_count": 1, "sha256": "invalid", "excludes_volatile_logs_state_temp": True}
    path = tmp_path / (REPORT_NAME if scenario != "foreign_name" else "foreign.json")
    if scenario == "wrong_scope":
        (tmp_path / "elsewhere").mkdir()
        path = tmp_path / "elsewhere" / REPORT_NAME
    path.write_text(json.dumps(document), encoding="utf-8")
    result = invoke(r"""
$taskAccepted=$false
try { $null=Read-PriorUninstallEvidence $env:BLUEREEL_TEST_PRIOR; $taskAccepted=$true } catch {}
@{accepted=$taskAccepted} | ConvertTo-Json -Compress
""", ["Assert-NoReparse", "Read-PriorUninstallEvidence"], tmp_path, BLUEREEL_TEST_PRIOR=str(path))
    assert result["accepted"] is (scenario == "valid")


def test_read_only_followup_has_no_runtime_execution_or_cleanup_calls() -> None:
    source = HELPER.read_text(encoding="utf-8")
    body = source.split("'VerifyUninstallPreserve' {", 1)[1].split("        'Diagnostics'", 1)[0]
    assert "Get-PreservedDataDigest" in body and "Set-UninstalledStateEvidence" in body
    assert "application_uninstall_verified = $true" in body
    assert "Invoke-Probe" not in body and "Remove-" not in body and "Stop-" not in body
    assert "finally {\n                $TaskReport.transient_log_cleanup = Remove-PrivateUninstallLog" in source
    assert source.index("$TaskReport.application_uninstall_verified = $true", source.index("'Backup' {")) < source.index("if (-not $TaskReport.transient_raw_uninstall_log_removed)")
