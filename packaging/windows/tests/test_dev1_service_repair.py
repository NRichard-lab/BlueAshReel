from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "repair-dev1-service-arguments.ps1"
POWERSHELL = shutil.which("powershell.exe")


@pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell required")
def test_exact_xml_repair_parses_and_refuses_without_explicit_optin() -> None:
    parsed = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command",
         "$t=$null;$e=$null;[void][Management.Automation.Language.Parser]::ParseFile('"
         + str(SOURCE).replace("'", "''") + "',[ref]$t,[ref]$e);if($e.Count){$e|ForEach-Object Message;exit 1}"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert parsed.returncode == 0, parsed.stdout + parsed.stderr
    refused = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SOURCE)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert refused.returncode != 0 and "Explicit prototype repair opt-in" in refused.stderr


def test_exact_xml_repair_has_no_process_control_and_pins_all_four_originals() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for forbidden in ("Stop-Service", "Start-Service", "Stop-Process", ".Kill(", "sc.exe", "render_services"):
        assert forbidden not in source
    for digest in (
        "b14ce45468057196bb01b07cf774df5f2f72982c3a295bcd5c9e9539c1b0bb7f",
        "33739d7e9d251e899e14d2578a1f0ec83c886a9092449c594d4fb113b367c2e0",
        "37d8cb12698d10a517d5e875e13760bcd835a06f9a00ec242d23de7322d184c0",
        "9c34aad3481a45d7bd895d46079b71237fac43edca70ee36b8d6bc21431176ca",
    ):
        assert digest in source
    assert source.count("Assert-NoNativeRuntime") >= 4
    assert "<startarguments>-I -B -m app.native_runtime</startarguments>" in source
    assert "<stoparguments>-I -B -m app.native_runtime --stop</stoparguments>" in source
    assert "SetAccessRuleProtection($true,$false)" in source
    assert "GetSecurityDescriptorSddlForm" in source
