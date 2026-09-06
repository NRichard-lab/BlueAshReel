"""Exercise interactive launcher arguments without elevation or opening a window."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "maintenance.ps1"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell interactive launcher")
@pytest.mark.parametrize("action", ["ConfigureMedia", "ConfigureNetwork", "Backup", "ValidateBackup"])
def test_interactive_maintenance_launches_visible_sta_child_with_exact_arguments(tmp_path: Path, action: str) -> None:
    result = tmp_path / "launch.json"
    wrapper = tmp_path / "capture-launch.ps1"
    wrapper.write_text(r'''
param($Source,$Action,$ProgramDir,$DataDir,$Result)
$ErrorActionPreference='Stop'
function Start-Process {
    param($FilePath,$Verb,$WindowStyle,$ArgumentList)
    [pscustomobject]@{Executable=$FilePath;Verb=$Verb;WindowStyle=$WindowStyle;Arguments=$ArgumentList} |
        ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $Result -Encoding UTF8
}
function Add-Type { throw 'No dialog or alternate elevation path should run in this test.' }
& $Source -Action $Action -ProgramDir $ProgramDir -DataDir $DataDir -Instance development
''', encoding="utf-8")
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    program, data = tmp_path / "Program Files" / "Blue Ash Reel", tmp_path / "Program Data" / "Blue Ash Reel"
    run = subprocess.run(
        [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
         "-Source", str(SOURCE), "-Action", action, "-ProgramDir", str(program), "-DataDir", str(data),
         "-Result", str(result)], capture_output=True, text=True, timeout=20, check=False,
    )
    assert run.returncode == 0, run.stderr
    launch = json.loads(result.read_text(encoding="utf-8-sig"))
    assert launch["Verb"] == "RunAs"
    assert launch["WindowStyle"] == "Normal"
    assert Path(launch["Executable"]).resolve() == powershell.resolve()
    assert launch["Arguments"] == [
        "-NoLogo", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", f'"{SOURCE}"',
        "-Action", action, "-ProgramDir", f'"{program}"', "-DataDir", f'"{data}"',
        "-Instance", "development", "-Elevated",
    ]
