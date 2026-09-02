"""No live process changes: exact prototype recovery guards and PS syntax."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "recover-dev1-stale-wrappers.ps1"


def test_recovery_targets_only_prevalidated_orphan_pids() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "-Id $taskRow.pid" in source
    assert "Stop-Process -Name" not in source
    assert source.index("$taskInstanceProcesses.Count -ne 2") < source.index("Stop-Process -Id")
    assert source.index("Existing recovery report will not be overwritten") < source.index("Stop-Process -Id")
    assert "corrected_xml_verified=$true" in source
    assert "application_children_already_exited_cooperatively=$true" in source


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell prototype helper")
def test_no_opt_in_refuses_before_reading_installation() -> None:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         "-ExpectedApiProcessId", "1", "-ExpectedWebProcessId", "2"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode != 0
    assert "Explicit disposable prototype recovery is required" in result.stderr
