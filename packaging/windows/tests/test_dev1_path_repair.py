from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "repair-dev1-python-path.ps1"
POWERSHELL = shutil.which("powershell.exe")


@pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell required for prototype helper syntax checks")
def test_repair_helper_parses_and_refuses_unrequested_action() -> None:
    parsed = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command",
         "$tokens=$null;$errors=$null;[void][Management.Automation.Language.Parser]::ParseFile("
         + "'" + str(SOURCE).replace("'", "''") + "',[ref]$tokens,[ref]$errors);"
         + "if($errors.Count){$errors | ForEach-Object Message;exit 1}"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert parsed.returncode == 0, parsed.stdout + parsed.stderr
    refused = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SOURCE)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert refused.returncode != 0
    assert "Explicit -AllowDisposablePrototypeRepair is required" in refused.stderr


def test_repair_helper_is_exactly_pinned_and_not_a_metadata_selected_runtime() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "param([switch]$AllowDisposablePrototypeRepair)" in source
    assert "$program = 'C:\\Program Files\\BlueReel Development'" in source
    assert "$data = 'C:\\ProgramData\\BlueReel-Development'" in source
    assert "read_installation" not in source
    assert "$start.FileName = $python" in source and "$start.Arguments = '-I -B -c" in source
    assert "$start.EnvironmentVariables.Clear()" in source
    assert "scripts.backup,scripts.restore_validate" in source
    assert "[IO.File]::Copy($target, $originalPath, $false)" in source
    assert "SetAccessRuleProtection($true, $false)" in source
    assert "GetSecurityDescriptorSddlForm" in source
    assert "original_manifest_retained_with_documented_path_file_deviation" in source
    corrected = b"python313.zip\n.\nLib/site-packages\n../../backend\n../../.\nimport site\n"
    assert hashlib.sha256(corrected).hexdigest() in source
    assert "4f47cd1bb3a89139a6cbae0ecb24905afc16fa2fd4596ada0a63279c2929bcd6" in source
    assert "c1382266a0447ca6dd007eddcae19046e713a82559cb9239684cdca512e7e858" in source
