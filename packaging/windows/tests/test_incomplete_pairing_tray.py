"""Compile the real tray and exercise incomplete-identity discard eligibility."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def test_native_discard_requires_an_incomplete_identity_and_no_pending_approval(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Requires the Windows inbox C# compiler")
    source = Path(__file__).resolve().parents[1] / "BlueAshReelAgent.cs"
    harness = tmp_path / "IncompletePairingTests.cs"
    harness.write_text('''using System;
static class IncompletePairingTests {
    static int Main() {
        string fingerprint = new String('a', 64);
        string[] states = {"Not paired", "Error", "Pairing expired", "Pairing cancelled", "Revoked",
            "Waiting for Portal approval", "Waiting for local confirmation", "Connecting", "Connected", "Running"};
        for (int index = 0; index < states.Length; index++) {
            if (AgentTray.CanDiscardIncomplete(false, false, states[index], fingerprint) != (index < 5)) return 1;
            if (AgentTray.CanDiscardIncomplete(true, false, states[index], fingerprint)) return 2;
            if (AgentTray.CanDiscardIncomplete(false, true, states[index], fingerprint)) return 3;
            if (AgentTray.CanDiscardIncomplete(false, false, states[index], null)) return 4;
            if (AgentTray.CanDiscardIncomplete(false, false, states[index], "")) return 5;
        }
        return 0;
    }
}''', encoding="utf-8")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    executable = tmp_path / "eligibility.exe"
    result = subprocess.run([
        str(compiler), "/nologo", "/target:exe", "/platform:x64", "/main:IncompletePairingTests",
        "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
        "/reference:System.Web.Extensions.dll", "/out:" + str(executable), str(source), str(harness),
    ], capture_output=True, text=True, timeout=30, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    outcome = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10, check=False,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    assert outcome.returncode == 0
