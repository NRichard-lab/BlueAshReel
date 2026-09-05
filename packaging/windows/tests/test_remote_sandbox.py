"""Execute a real native sandbox with synthetic files when the pinned runtime exists."""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(os.name != "nt", reason="Windows interactive desktop safety boundary")
def test_service_desktop_grant_refuses_interactive_desktop() -> None:
    from app.remote.native_desktop import allow_service_desktop_read

    with pytest.raises(RuntimeError, match="noninteractive and invisible"):
        allow_service_desktop_read(None, "bluereelremote.diagnostics")


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer acceptance")
def test_appcontainer_denies_private_file_and_preserves_dpapi_across_restart(tmp_path: Path) -> None:
    source = ROOT / "artifacts/native-dev/package/runtime/python"
    if not (source / "python.exe").is_file():
        pytest.skip("Pinned embedded runtime has not been built on this host")
    spec = importlib.util.spec_from_file_location("native_connector_launcher", ROOT / "backend/app/remote/native_launcher.py")
    assert spec and spec.loader
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    # This synthetic test intentionally uses an interactive developer token;
    # service desktop handling is accepted separately under the real SCM token.
    launcher.allow_service_desktop_read = lambda *_: None
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for entry in source.iterdir():
        if entry.is_file() and entry.suffix in {".exe", ".dll", ".pyd", ".zip"}:
            shutil.copy2(entry, runtime / entry.name)
    archive = next(runtime.glob("python3*.zip"))
    (runtime / (archive.stem + "._pth")).write_text(archive.name + "\n.\n")
    module = runtime / "app/remote"
    module.mkdir(parents=True)
    shutil.copy2(ROOT / "backend/app/remote/storage.py", module / "storage.py")
    (module / "native_policy.py").write_text('''
import ctypes,json,sys
from ctypes import wintypes as w
from pathlib import Path
from app.remote.storage import protect_secret,unprotect_secret
policy=json.loads(Path(sys.argv[-1]).read_text())
k=ctypes.WinDLL('kernel32',use_last_error=True);a=ctypes.WinDLL('advapi32',use_last_error=True)
k.GetCurrentProcess.restype=w.HANDLE
a.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
a.GetTokenInformation.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
t=w.HANDLE();assert a.OpenProcessToken(k.GetCurrentProcess(),8,ctypes.byref(t))
v=w.DWORD();n=w.DWORD();assert a.GetTokenInformation(t,29,ctypes.byref(v),4,ctypes.byref(n))
try:Path(policy['denied']).read_text();denied=False
except PermissionError:denied=True
state=Path(policy['result']);secret=state.parent/'identity.json'
restarted=secret.exists()
if restarted:assert unprotect_secret(json.loads(secret.read_text())['secret'])==b'public synthetic fixture'
else:secret.write_text(json.dumps({'secret':protect_secret(b'public synthetic fixture')}))
state.write_text(json.dumps({'appcontainer':v.value,'media_denied':denied,'dpapi_restart':restarted}))
''', encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "synthetic.txt").write_text("synthetic private media placeholder")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"denied": str(private / "synthetic.txt"), "result": str(state / "result.json")}))
    name = "blueashreel.synthetic." + tmp_path.name[-24:].replace("_", ".")
    _, sid = launcher.appcontainer_sid(name)
    for path, permission in [(tmp_path, "RX"), (runtime, "(OI)(CI)RX"), (state, "(OI)(CI)M"), (policy, "R")]:
        subprocess.run(["icacls.exe", str(path), "/grant", f"*{sid}:{permission}"], check=True, capture_output=True)
    subprocess.run(["icacls.exe", str(state), "/setintegritylevel", "(OI)(CI)L"], check=True, capture_output=True)
    try:
        for restarted in (False, True):
            assert launcher.launch(name, runtime / "python.exe", policy) == 0
            assert json.loads((state / "result.json").read_text()) == {
                "appcontainer": 1, "media_denied": True, "dpapi_restart": restarted
            }
    finally:
        userenv = ctypes.WinDLL("userenv")
        userenv.DeleteAppContainerProfile.argtypes = [ctypes.c_wchar_p]
        userenv.DeleteAppContainerProfile(name)


def test_native_provisioning_preserves_media_runtime_firewall_and_uses_appcontainer() -> None:
    script = (ROOT / "packaging/windows/install-remote.ps1").read_text()
    assert 'runtime\\remote-python' in script and 'NT SERVICE' in script
    assert 'app.remote.native_launcher' in script and '--derive-sid' in script
    assert '-Direction Inbound -Action Block' in script
    assert '-RemotePort 443 -RemoteAddress $taskDestinations' in script
    assert "'::/1'" in script and "'8000::/1'" in script and "'0-442','444-65535'" in script
    assert 'Set-NetFirewallProfile' not in script
    assert "if ($Action -eq 'PurgeState')" in script
    assert '$TaskRemoteData -ine ($DataDir + \'-Remote\')' in script
    assert 'REMOTE_CONTROL_DIR=' in script and '.env.before-remote-' in script
    lifecycle = (ROOT / "packaging/windows/install.ps1").read_text()
    assert "function Get-OptionalRemoteService" in lifecycle
    assert "Invoke-RemoteMaintenance 'PrepareUpgrade'" in lifecycle
    assert "Invoke-RemoteMaintenance 'Remove'" in lifecycle
    assert "Invoke-RemoteMaintenance 'Install'" in lifecycle
    assert "Invoke-RemoteMaintenance 'PurgeState'" in lifecycle
    assert "Assert-RemoteUpgradeReady" not in lifecycle
    assert "$TaskRoles = @('API','Worker','Web','Proxy')" in lifecycle


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell native argument regression")
@pytest.mark.parametrize("addresses", [[], ["192.0.2.55", "192.0.2.56"]])
def test_windows_powershell_policy_transfer_uses_file_for_json_quotes(tmp_path: Path, addresses: list[str]) -> None:
    runtime = ROOT / "artifacts/native-dev/package/runtime/python/python.exe"
    if not runtime.is_file():
        pytest.skip("Pinned embedded runtime has not been built on this host")
    script = (ROOT / "packaging/windows/install-remote.ps1").read_text()
    policy_code = script.split("$taskPolicyScript = @'\n", 1)[1].split("\n'@", 1)[0]
    data = tmp_path / "synthetic data"
    remote = tmp_path / "synthetic remote"
    (data / "configuration").mkdir(parents=True)
    (remote / "configuration").mkdir(parents=True)
    (data / "configuration/.env").write_text("MEDIA_ROOT_DEFINITIONS=\"[]\"\n", encoding="utf-8")
    pins = remote / "configuration/endpoint-pins.json"
    pins.write_text(json.dumps(addresses), encoding="utf-8")
    runner = tmp_path / "invoke policy.ps1"
    runner.write_text(
        "param([string]$Interpreter,[string]$Data,[string]$Remote,[string]$Pins)\n"
        "$ErrorActionPreference='Stop'\n$taskPolicyScript = @'\n" + policy_code
        + "\n'@\n& $Interpreter -I -B -c $taskPolicyScript $Data $Remote $Pins\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
                    "-Interpreter", str(runtime), "-Data", str(data), "-Remote", str(remote), "-Pins", str(pins)],
                   check=True, capture_output=True, text=True)
    policy = json.loads((remote / "configuration/policy.json").read_text())
    assert policy["allowed_ips"] == addresses
    assert policy["control_dir"] == str(remote / "control")
    assert "REMOTE_CONTROL_DIR=" in (data / "configuration/.env").read_text()


def test_native_existing_state_rejects_hardlinks_before_acl_mutations(tmp_path: Path) -> None:
    script = (ROOT / "packaging/windows/install-remote.ps1").read_text()
    check_code = script.split("$taskCheckTree = @'\n", 1)[1].split("\n'@", 1)[0]
    state = tmp_path / "state"
    state.mkdir()
    (state / "normal.json").write_text("synthetic")
    subprocess.run([sys.executable, "-c", check_code, str(state)], check=True, capture_output=True)
    outside = tmp_path / "protected-synthetic.json"
    outside.write_text("unchanged synthetic content")
    os.link(outside, state / "hardlink.json")
    result = subprocess.run([sys.executable, "-c", check_code, str(state)], capture_output=True, text=True, check=False)
    assert result.returncode != 0 and "filesystem link" in result.stderr
    assert outside.read_text() == "unchanged synthetic content"


@pytest.mark.skipif(os.name != "nt", reason="Windows effective firewall policy validation")
@pytest.mark.parametrize("mode", ["approved", "disabled_profile", "ignored_local_rules", "missing_rule", "wrong_program", "unenforced"])
def test_native_firewall_requires_effective_enforcement(tmp_path: Path, mode: str) -> None:
    source = (ROOT / "packaging/windows/install-remote.ps1").read_text()
    function = "function Assert-ConnectorFirewall" + source.split("function Assert-ConnectorFirewall", 1)[1].split("function Assert-ConnectorState", 1)[0]
    runner = tmp_path / "effective firewall.ps1"
    runner.write_text("param([string]$Mode)\n$ErrorActionPreference='Stop'\n" + function + r'''
$TaskName='SyntheticRemote';$TaskRemotePython='C:\synthetic\python.exe';$TaskPins=@('192.0.2.55')
function Get-Service { [pscustomobject]@{Status='Running'} }
function Get-NetFirewallProfile {
 foreach ($name in @('Domain','Private','Public')) {
  [pscustomobject]@{Name=$name;Enabled=($Mode -ne 'disabled_profile');AllowLocalFirewallRules=($Mode -ne 'ignored_local_rules')}
 }
}
function Get-NetFirewallRule {
 param($PolicyStore,$Name,$ErrorAction)
 if ($Mode -eq 'missing_rule') { return }
 $action=if($Name.EndsWith('-TLS')){'Allow'}else{'Block'}
 $enforcement=if($Mode -eq 'unenforced'){'LocalFirewallRulesDisallowed'}else{'Enforced'}
 [pscustomobject]@{Enabled='True';Action=$action;Group=$TaskName;EnforcementStatus=@('ProfileInactive',$enforcement)}
}
function Get-NetFirewallApplicationFilter {
 process { [pscustomobject]@{Program=$(if($Mode -eq 'wrong_program'){'C:\unrelated\python.exe'}else{$TaskRemotePython})} }
}
try { Assert-ConnectorFirewall; exit 0 } catch { exit 1 }
''', encoding="utf-8")
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner), "-Mode", mode], capture_output=True, check=False)
    assert (result.returncode == 0) is (mode == "approved")


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell firewall generation")
@pytest.mark.parametrize("offline", [False, True])
def test_native_firewall_empty_pins_deny_all_destinations(tmp_path: Path, offline: bool) -> None:
    from app.remote.network import pin_resolution
    source = (ROOT / "packaging/windows/install-remote.ps1").read_text()
    function = "function Set-ConnectorFirewall" + source.split("function Set-ConnectorFirewall", 1)[1].split("$taskIdentity =", 1)[0]
    runner = tmp_path / "firewall generation.ps1"
    runner.write_text("$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n" + function + r'''
$TaskName='Synthetic';$TaskDisplayName='Synthetic';$TaskRemotePython='C:\synthetic\python.exe'
$script:Rules=@()
function Assert-ConnectorFirewall {}
function Get-NetFirewallRule { param($Group,$ErrorAction) }
function New-NetFirewallRule {
 param($Program,$Group,$Profile,$Enabled,$Name,$DisplayName,$Direction,$Action,$Protocol,$RemoteAddress,$RemotePort)
 $script:Rules += [pscustomobject]@{Name=$Name;Enabled=$Enabled;Action=$Action;Direction=$Direction;Addresses=@($RemoteAddress);Ports=@($RemotePort)}
}
$TaskPins=''' + ("@()" if offline else "@('192.0.2.55')") + "\nSet-ConnectorFirewall $TaskPins\nConvertTo-Json -InputObject @($script:Rules) -Depth 5\n", encoding="utf-8")
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)], capture_output=True, text=True, check=True)
    rules = {r["Name"].removeprefix("Synthetic-"): r for r in json.loads(result.stdout)}
    assert len(rules) == 6
    assert rules["TLS"]["Enabled"] == ("False" if offline else "True")
    addresses = rules["OtherDestinations"]["Addresses"]
    assert addresses[-2:] == ["::/1", "8000::/1"]
    if offline:
        assert addresses[0] == "0.0.0.0-255.255.255.255"
        import socket
        original = socket.getaddrinfo
        try:
            pin_resolution([])
            with pytest.raises(OSError, match="Canonical destination unavailable"):
                socket.getaddrinfo("blueashreel.com", 443)
        finally:
            socket.getaddrinfo = original
    else:
        assert addresses[:2] == ["0.0.0.0-192.0.2.54", "192.0.2.56-255.255.255.255"]


@pytest.mark.skipif(os.name != "nt", reason="Windows pre-upgrade helper dispatch")
def test_new_installer_helper_upgrades_without_invoking_legacy_remote_rejection(tmp_path: Path) -> None:
    source = (ROOT / "packaging/windows/install.ps1").read_text()
    helper = "function Invoke-RemoteMaintenance" + source.split("function Invoke-RemoteMaintenance", 1)[1].split("function Stop-Instance", 1)[0]
    old = tmp_path / "old program/support"
    old.mkdir(parents=True)
    (old / "install-remote.ps1").write_text("throw 'Legacy remote upgrade unsupported'")
    (tmp_path / "install-remote.ps1").write_text("param($Action,$ProgramDir,$DataDir)\nif ($Action -ne 'PrepareUpgrade') { throw 'Unexpected action' }; Write-Output 'new helper preserved identity'")
    runner = tmp_path / "upgrade-install.ps1"
    runner.write_text("param($ProgramDir,$DataDir)\n$ErrorActionPreference='Stop'\n" + helper + "\nInvoke-RemoteMaintenance 'PrepareUpgrade'\n")
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner), "-ProgramDir", str(old.parent), "-DataDir", str(tmp_path / "state")], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "new helper preserved identity"
    installer = (ROOT / "packaging/windows/installer.iss").read_text()
    dispatch = installer.split("function RunMaintenance", 1)[1].split("function WriteExistingDataValidator", 1)[0]
    assert "ExtractTemporaryFile('upgrade-install.ps1')" in dispatch
    assert "ExtractTemporaryFile('install-remote.ps1')" in dispatch
    assert "Helper := ExpandConstant('{tmp}\\upgrade-install.ps1')" in dispatch
