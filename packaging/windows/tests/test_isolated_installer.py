"""Run actual Inno/C# scope guards without installing or launching an Agent."""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1]
REPOSITORY = PACKAGING.parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Actual Windows Inno and inbox C# integration")


def invoke(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=30, check=False,
                          creationflags=subprocess.CREATE_NO_WINDOW)


@pytest.fixture(scope="module")
def inno_scope_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = REPOSITORY / "artifacts/native-dev/inno/ISCC.exe"
    if not compiler.is_file():
        pytest.skip("Pinned local Inno compiler is required")
    root = tmp_path_factory.mktemp("inno-scope")
    source = (PACKAGING / "installer.iss").read_text()
    # Compile the implementation verbatim. InitializeSetup deliberately returns
    # false before files, shortcuts, startup or uninstall registration can run.
    functions = source[source.index("function GetFileAttributesW"):source.index("function CommonArguments")]
    script = root / "scope.iss"
    script.write_text(
        '#define ProductId "{{5E41781A-6BE6-4505-B5D9-177F592ED611}"\n'
        '#define BuildChannel "development"\n#define DataName "BlueAshReel-Development"\n'
        '#define DefaultPort "18080"\n'
        '[Setup]\nAppId=BlueReelScopeHarness\nAppName=Scope Harness\nAppVersion=1\n'
        'DefaultDirName={tmp}\\scope-harness-unused\nPrivilegesRequired=lowest\n'
        'Uninstallable=no\nCreateUninstallRegKey=no\nSetupLogging=no\n'
        'OutputDir=.\nOutputBaseFilename=scope\n[Code]\n'
        'var ScopeResolved: Boolean; IsolationId, IsolationRoot, ResolvedDataDir, SavedPort: String;\n'
        + functions + '\nfunction InitializeSetup: Boolean;\nvar Outcome: String;\nbegin\n'
        "  try\n    ResolveScope;\n"
        "    Outcome := 'accepted' + #13#10 + GetAppId('') + #13#10 + GetRegistrationKey('') + #13#10 +\n"
        "      IntToStr(Ord(AllowDesktopIntegration)) + #13#10 + GetDefaultProgramDir('') + #13#10 + GetDataDir('');\n"
        "  except Outcome := 'rejected'; end;\n"
        "  SaveStringToFile(ExpandConstant('{param:RESULT}'),Outcome,False);\n"
        "  Result := False;\nend;\n", encoding="utf-8")
    result = invoke([str(compiler), str(script)])
    assert result.returncode == 0, result.stdout + result.stderr
    return root / "scope.exe"


def scope(harness: Path, output: Path, identifier: str | None) -> list[str]:
    arguments = [str(harness), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/RESULT={output}"]
    if identifier is not None:
        arguments.append(f"/ISOLATEDTEST={identifier}")
    result = invoke(arguments)
    assert result.returncode == 1  # Our InitializeSetup always refuses installation.
    return output.read_text(encoding="utf-8-sig").splitlines()


@pytest.fixture
def isolated_root() -> Path:
    base = Path(os.environ["USERPROFILE"]) / "BlueAshReel-Installer-Tests"
    base.mkdir(exist_ok=True)
    identifier = uuid.uuid4().hex
    root = base / identifier
    root.mkdir()  # Exclusive ownership of a new, unpredictable disposable fixture.
    try:
        yield root
    finally:
        resolved = root.resolve()
        assert resolved.parent == base.resolve() and resolved.name == identifier
        assert not root.is_symlink() and not root.is_junction()
        shutil.rmtree(root)


@pytest.mark.parametrize("identifier", ["", "../escape", "Uppercase", "with-hyphen", "a" * 33, "a/b", "a b"])
def test_compiled_inno_rejects_invalid_isolation_ids(inno_scope_harness: Path, tmp_path: Path, identifier: str) -> None:
    assert scope(inno_scope_harness, tmp_path / "result.txt", identifier) == ["rejected"]


def test_compiled_inno_preserves_normal_upgrade_identity_when_switch_absent(
    inno_scope_harness: Path, tmp_path: Path,
) -> None:
    result = scope(inno_scope_harness, tmp_path / "result.txt", None)
    assert result[:4] == ["accepted", "{5E41781A-6BE6-4505-B5D9-177F592ED611}",
                          "Software\\BlueReel\\development", "1"]


@pytest.mark.parametrize("arguments", [["/ISOLATEDTEST"], ["/ISOLATEDTEST=first", "/ISOLATEDTEST=second"]])
def test_compiled_inno_rejects_bare_or_duplicate_test_switches(
    inno_scope_harness: Path, tmp_path: Path, arguments: list[str],
) -> None:
    output = tmp_path / "result.txt"
    result = invoke([str(inno_scope_harness), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                     f"/RESULT={output}", *arguments])
    assert result.returncode == 1
    assert output.read_text(encoding="utf-8-sig") == "rejected"


def test_compiled_inno_scope_is_distinct_and_disables_desktop_integration(inno_scope_harness: Path, tmp_path: Path) -> None:
    identifier = uuid.uuid4().hex
    result = scope(inno_scope_harness, tmp_path / "result.txt", identifier)
    root = Path(os.environ["USERPROFILE"]) / "BlueAshReel-Installer-Tests" / identifier
    assert result == ["accepted", "BlueAshReel-IsolatedTest-" + identifier,
                      "Software\\BlueReel\\InstallerTests\\" + identifier, "0",
                      str(root / "program"), str(root / "data")]
    assert not root.exists()  # Resolving scope does not create or overwrite data.


@pytest.mark.parametrize("marker", [None, "foreign", "extra\nlines"])
def test_compiled_inno_rejects_unowned_existing_test_root(
    inno_scope_harness: Path, tmp_path: Path, isolated_root: Path, marker: str | None,
) -> None:
    if marker is not None:
        (isolated_root / ".bluereel-installer-test").write_text(marker)
    assert scope(inno_scope_harness, tmp_path / "result.txt", isolated_root.name) == ["rejected"]


def test_compiled_inno_accepts_only_matching_existing_marker(
    inno_scope_harness: Path, tmp_path: Path, isolated_root: Path,
) -> None:
    marker = isolated_root / ".bluereel-installer-test"
    marker.write_text(isolated_root.name + "\n")
    before = marker.read_bytes()
    assert scope(inno_scope_harness, tmp_path / "result.txt", isolated_root.name)[0] == "accepted"
    assert marker.read_bytes() == before


def test_compiled_tray_guards_test_identity_and_both_startup_writes(tmp_path: Path, isolated_root: Path) -> None:
    marker = isolated_root / ".bluereel-installer-test"
    marker.write_text(isolated_root.name + "\n")
    harness = tmp_path / "GuardTests.cs"
    harness.write_text(r'''using System;
using System.IO;
using System.Reflection;
using System.Runtime.Serialization;
using Microsoft.Win32;
static class GuardTests {
  static bool Rejected(string program, string data) {
    try { AgentTray.IsIsolatedInstallation(program,data); return false; }
    catch(IOException) { return true; }
  }
  static int Main(string[] args) {
    string root=args[0], program=Path.Combine(root,"program"), data=Path.Combine(root,"data");
    string marker=Path.Combine(root,".bluereel-installer-test");
    if (!AgentTray.IsIsolatedInstallation(program,data)) return 1;
    if (!Rejected(program,Path.Combine(root,"elsewhere"))) return 2;
    if (!Rejected(Path.Combine(root,"nested","program"),data)) return 3;
    File.WriteAllText(marker,"foreign");
    if (!Rejected(program,data)) return 4;
    File.WriteAllText(marker,Path.GetFileName(root));
    string ordinary=Path.Combine(Path.GetTempPath(),"ordinary-program");
    if (AgentTray.IsIsolatedInstallation(ordinary,data)) return 5;
    // Bypass the constructor: no UI, process, Agent state, or identity is created.
    object tray=FormatterServices.GetUninitializedObject(typeof(AgentTray));
    var flags=BindingFlags.Instance|BindingFlags.NonPublic;
    typeof(AgentTray).GetField("isolatedTest",flags).SetValue(tray,true);
    string key="Software\\BlueReel\\InstallerTests\\Guard"+Path.GetFileName(root);
    typeof(AgentTray).GetField("runKey",flags).SetValue(tray,key);
    typeof(AgentTray).GetField("runName",flags).SetValue(tray,"must-not-write");
    foreach(bool enabled in new bool[]{false,true}) {
      try { typeof(AgentTray).GetMethod("SetStartup",flags).Invoke(tray,new object[]{enabled}); return 6; }
      catch(TargetInvocationException error) { if (!(error.InnerException is IOException)) return 7; }
    }
    using(var value=Registry.CurrentUser.OpenSubKey(key)) { if(value!=null) return 8; }
    return 0;
  }
}''', encoding="utf-8")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    output = tmp_path / "guard.exe"
    result = invoke([str(compiler), "/nologo", "/target:exe", "/platform:x64", "/main:GuardTests",
                     "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
                     "/reference:System.Web.Extensions.dll", "/out:" + str(output),
                     str(PACKAGING / "BlueAshReelAgent.cs"), str(harness)])
    assert result.returncode == 0, result.stdout + result.stderr
    result = invoke([str(output), str(isolated_root)])
    assert result.returncode == 0, result.stdout + result.stderr
