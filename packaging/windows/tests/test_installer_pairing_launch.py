"""Compile the actual native launch selector and enforce the Inno completion contract."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_successful_installer_dispatches_native_handoff_after_health():
    source = (ROOT / 'installer.iss').read_text()
    completion = source[source.index('procedure CurStepChanged'):source.index('function WasSuccessful')]
    assert completion.index("RunMaintenance('Health'") < completion.index("' --finish-install'")
    assert completion.index("' --finish-install'") < completion.index('InstallSuccessful := True')
    assert 'ExecAsOriginalUser' in completion
    assert '[Run]' not in source  # No independent home/download launch after native completion.


@pytest.mark.skipif(os.name != 'nt', reason='Windows inbox C# compiler required')
def test_compiled_install_launch_waits_and_selects_exact_agent_or_loopback(tmp_path):
    harness = tmp_path / 'LaunchTests.cs'
    harness.write_text('''using System;
static class LaunchTests {
  static int Main() {
    string id = "a731e941-2324-412b-9922-73f936d62072";
    if (AgentTray.InstallationDestination(false,false,null,18080) != null) return 1;
    if (AgentTray.InstallationDestination(true,false,null,18080) != "http://127.0.0.1:18080/portal/start?purpose=pair") return 2;
    if (AgentTray.InstallationDestination(true,true,id,18080) != "https://blueashreel.com/portal/agents/"+id) return 3;
    if (AgentTray.InstallationDestination(true,true,"../download",18080) != null) return 4;
    if (AgentTray.InstallationDestination(true,true,null,18080) != null) return 5;
    if (AgentTray.InstallationDestination(true,false,null,80) != null) return 6;
    if (AgentTray.InstallationDestination(true,false,null,65536) != null) return 7;
    return 0;
  }
}''')
    compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    output = tmp_path/'launch.exe'
    result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:x64', '/main:LaunchTests',
        '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll', '/reference:System.Web.Extensions.dll',
        '/out:'+str(output), str(ROOT/'BlueAshReelAgent.cs'), str(harness)],
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    assert subprocess.run([str(output)], timeout=10, creationflags=subprocess.CREATE_NO_WINDOW).returncode == 0
