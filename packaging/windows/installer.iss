; Brand values are supplied from config/product.json by build_native.py.
#ifndef BrandName
  #error Build with build_native.py to load the central product identity
#endif
; Build input is a verified onedir payload, never the developer checkout.
#ifndef PayloadDir
  #error PayloadDir must name the verified native payload directory
#endif
#ifndef OutputDir
  #define OutputDir "..\..\artifacts\native-dev"
#endif
#ifndef ProductVersion
  #define ProductVersion "0.1.0-dev.2"
#endif
#ifndef FileVersion
  #define FileVersion "0.1.0.2"
#endif
#ifndef BuildChannel
  #define BuildChannel "development"
#endif
#if BuildChannel == "stable"
  #define ProductName BrandName
  #define DataName "BlueReel"
  #define InstallDirectoryName "BlueReel"
  #define ServicePrefix "BlueReel"
  #define DefaultPort "8080"
  #define ProductId "{{78EF44D3-F1FC-4F2A-92D2-80921F170DEF}"
  #define OutputName PackageName + "-Setup-x64"
#else
  #define ProductName BrandName + " Development"
  #define DataName "BlueReel-Development"
  #define InstallDirectoryName "BlueReel Development"
  #define ServicePrefix "BlueReelDevelopment"
  #define DefaultPort "18080"
  #define ProductId "{{5E41781A-6BE6-4505-B5D9-177F592ED611}"
  #define OutputName PackageName + "-Setup-Development-x64"
#endif

[Setup]
AppId={#ProductId}
AppName={#ProductName}
AppVersion={#ProductVersion}
AppVerName={#ProductName} {#ProductVersion} (unsigned)
AppPublisher={#BrandName}
AppPublisherURL=https://{#ProductDomain}
AppSupportURL=https://{#ProductDomain}/security
; Keep established install locations, service IDs, registry keys and AppId.
DefaultDirName={autopf}\{#InstallDirectoryName}
DefaultGroupName={#ProductName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
WizardStyle=modern
SetupLogging=yes
UninstallLogging=yes
UninstallDisplayName={#ProductName}
VersionInfoVersion={#FileVersion}
VersionInfoProductVersion={#FileVersion}
VersionInfoProductTextVersion={#ProductVersion}
VersionInfoDescription=Unsigned {#BrandName} native Windows development installer
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
Compression=lzma2/normal
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AlwaysRestart=no
UsePreviousAppDir=yes
UsePreviousGroup=yes
InfoBeforeFile=development-notice.txt

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKLM; Subkey: "Software\BlueReel\{#BuildChannel}"; ValueType: string; ValueName: "ProgramDir"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\BlueReel\{#BuildChannel}"; ValueType: string; ValueName: "DataDir"; ValueData: "{commonappdata}\{#DataName}"
Root: HKLM; Subkey: "Software\BlueReel\{#BuildChannel}"; ValueType: string; ValueName: "Port"; ValueData: "{code:GetPort}"

[Icons]
Name: "{group}\Open {#ProductName}"; Filename: "{code:GetApplicationURL}"
Name: "{group}\Create backup"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "{code:GetBackupShortcut}"; Flags: runminimized
Name: "{group}\Validate backup (does not restore)"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "{code:GetValidateShortcut}"; Flags: runminimized
Name: "{group}\Configure approved media folders"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "{code:GetMediaShortcut}"; Flags: runminimized
Name: "{group}\Configure private-LAN access"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "{code:GetNetworkShortcut}"; Flags: runminimized
Name: "{group}\Open-source notices"; Filename: "{app}\OPEN-SOURCE-NOTICES.txt"
Name: "{group}\Uninstall {#ProductName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{code:GetApplicationURL}"; Description: "Open {#ProductName} and set up the Owner account"; Flags: shellexec nowait postinstall skipifsilent runasoriginaluser; Check: WasSuccessful

[UninstallDelete]
; Only installer-generated program files. Never ProgramData or source media.
Type: files; Name: "{app}\services\{#ServicePrefix}API.exe"
Type: files; Name: "{app}\services\{#ServicePrefix}API.xml"
Type: files; Name: "{app}\services\{#ServicePrefix}Worker.exe"
Type: files; Name: "{app}\services\{#ServicePrefix}Worker.xml"
Type: files; Name: "{app}\services\{#ServicePrefix}Web.exe"
Type: files; Name: "{app}\services\{#ServicePrefix}Web.xml"
Type: files; Name: "{app}\services\{#ServicePrefix}Proxy.exe"
Type: files; Name: "{app}\services\{#ServicePrefix}Proxy.xml"

[Code]
var
  NetworkPage: TInputQueryWizardPage;
  MediaPage: TWizardPage;
  MediaMemo: TNewMemo;
  BrowseButton: TNewButton;
  MediaCaption: TNewStaticText;
  RootsFile: String;
  ExistingInstallation: Boolean;
  InstallSuccessful: Boolean;
  PreparedUpgrade: Boolean;
  PreservedReinstall: Boolean;

function Quoted(const Value: String): String;
begin
  { Windows path names cannot contain a quote; never pass shell command text. }
  if Pos('"', Value) <> 0 then
    RaiseException('An invalid quote was found in an installation argument.');
  Result := '"' + Value + '"';
end;

function GetPort(Param: String): String;
begin
  Result := NetworkPage.Values[0];
end;

function GetApplicationURL(Param: String): String;
begin
  Result := 'http://127.0.0.1:' + GetPort('') + '/';
end;

function CommonArguments: String;
begin
  Result := ' -ProgramDir ' + Quoted(ExpandConstant('{app}')) +
    ' -DataDir ' + Quoted(ExpandConstant('{commonappdata}\{#DataName}')) +
    ' -Instance {#BuildChannel}';
end;

function MaintenanceShortcut(const Action: String): String;
begin
  Result := '-NoProfile -ExecutionPolicy Bypass -File ' +
    Quoted(ExpandConstant('{app}\support\maintenance.ps1')) +
    ' -Action ' + Action + CommonArguments;
end;

function GetBackupShortcut(Param: String): String;
begin
  Result := MaintenanceShortcut('Backup');
end;

function GetValidateShortcut(Param: String): String;
begin
  Result := MaintenanceShortcut('ValidateBackup');
end;

function GetMediaShortcut(Param: String): String;
begin
  Result := MaintenanceShortcut('ConfigureMedia');
end;

function GetNetworkShortcut(Param: String): String;
begin
  Result := MaintenanceShortcut('ConfigureNetwork');
end;

function RunMaintenance(const Action, Extra: String): Boolean;
var
  ExitCode: Integer;
  Arguments: String;
begin
  Arguments := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    Quoted(ExpandConstant('{app}\support\install.ps1')) + ' -Action ' + Action +
    CommonArguments + Extra;
  Log('Running {#BrandName} maintenance action: ' + Action);
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Arguments, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode);
  Result := Result and (ExitCode = 0);
  if not Result then
    Log('{#BrandName} maintenance action failed with exit code ' + IntToStr(ExitCode));
end;

function WriteExistingDataValidator(const ScriptPath: String): Boolean;
var
  Script: TStringList;
begin
  { Embedded read-only validation works even after the old runtime was cleanly
    uninstalled. Never execute code from the retained ProgramData directory. }
  Script := TStringList.Create;
  try
    Script.Add('param([string]$ProgramDir,[string]$DataDir,[string]$Prefix,[string]$Channel,[string]$PortFile,[switch]$Preserved)');
    Script.Add('$ErrorActionPreference = ''Stop''');
    Script.Add('$ProgressPreference = ''SilentlyContinue''');
    Script.Add('Set-StrictMode -Version Latest');
    Script.Add('function Canonical([string]$Path) {');
    Script.Add('  if (-not [IO.Path]::IsPathRooted($Path) -or $Path -match ''(^|[\\/])\.\.([\\/]|$)'') { throw ''Unsafe identity path'' }');
    Script.Add('  return [IO.Path]::GetFullPath($Path).TrimEnd(''\'')');
    Script.Add('}');
    Script.Add('function NoReparse([string]$Path) {');
    Script.Add('  $item = Canonical $Path');
    Script.Add('  while ($item) {');
    Script.Add('    if (Test-Path -LiteralPath $item) { if ((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ''Unsafe reparse point'' } }');
    Script.Add('    $parent = [IO.Directory]::GetParent($item)');
    Script.Add('    $item = if ($null -eq $parent) { $null } else { $parent.FullName }');
    Script.Add('  }');
    Script.Add('}');
    Script.Add('function InspectTree([string]$Root) {');
    Script.Add('  NoReparse $Root');
    Script.Add('  if (-not (Test-Path -LiteralPath $Root)) { return 0 }');
    Script.Add('  $pending = [Collections.Generic.Queue[string]]::new()');
    Script.Add('  $pending.Enqueue($Root); $files = 0');
    Script.Add('  while ($pending.Count) {');
    Script.Add('    $item = Get-Item -LiteralPath $pending.Dequeue() -Force');
    Script.Add('    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ''Unsafe data tree'' }');
    Script.Add('    if ($item.PSIsContainer) {');
    Script.Add('      foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) { $pending.Enqueue($child.FullName) }');
    Script.Add('    } else { $files++ }');
    Script.Add('  }');
    Script.Add('  return $files');
    Script.Add('}');
    Script.Add('try {');
    Script.Add('  $ProgramDir = Canonical $ProgramDir; $DataDir = Canonical $DataDir');
    Script.Add('  $null = InspectTree $DataDir');
    Script.Add('  $programFiles = InspectTree $ProgramDir');
    Script.Add('  $marker = Join-Path $DataDir ''.bluereel-native-instance''');
    Script.Add('  $metadataPath = Join-Path $DataDir ''configuration\installation.json''');
    Script.Add('  if ((Get-Item -LiteralPath $marker -Force).PSIsContainer -or (Get-Item -LiteralPath $marker -Force).Length -gt 128) { throw ''Invalid marker'' }');
    Script.Add('  if ([IO.File]::ReadAllText($marker).TrimEnd([char[]]"`r`n") -cne $Prefix) { throw ''Foreign marker'' }');
    Script.Add('  if ((Get-Item -LiteralPath $metadataPath -Force).Length -gt 1048576) { throw ''Invalid metadata'' }');
    Script.Add('  $metadata = [IO.File]::ReadAllText($metadataPath) | ConvertFrom-Json');
    Script.Add('  if ($metadata.instance -cne $Channel -or $metadata.service_prefix -cne $Prefix -or');
    Script.Add('      (Canonical $metadata.program_dir) -ine $ProgramDir -or (Canonical $metadata.data_dir) -ine $DataDir) { throw ''Foreign instance identity'' }');
    Script.Add('  $port = [int]$metadata.port');
    Script.Add('  if ($port -lt 1024 -or $port -gt 65533 -or [int]$metadata.api_port -ne ($port + 1) -or [int]$metadata.web_port -ne ($port + 2)) { throw ''Invalid saved port'' }');
    Script.Add('  if ($Preserved) {');
    Script.Add('    if ($programFiles -ne 0) { throw ''Partial old program payload remains'' }');
    Script.Add('    if (Test-Path -LiteralPath (''HKLM:\Software\BlueReel\'' + $Channel)) { throw ''Old product registration remains'' }');
    Script.Add('    foreach ($role in @(''API'',''Worker'',''Web'',''Proxy'')) {');
    Script.Add('      if (Test-Path -LiteralPath (''HKLM:\SYSTEM\CurrentControlSet\Services\'' + $Prefix + $role)) { throw ''Old service registration remains'' }');
    Script.Add('    }');
    Script.Add('  }');
    Script.Add('  NoReparse $PortFile');
    Script.Add('  [IO.File]::WriteAllText($PortFile,[string]$port,[Text.Encoding]::ASCII)');
    Script.Add('  exit 0');
    Script.Add('} catch { exit 1 }');
    Result := SaveStringToFile(ScriptPath, Script.Text, False);
  finally
    Script.Free;
  end;
end;

function ValidateExistingData(const RequireUninstalled: Boolean; var SavedPort: String): Boolean;
var
  ScriptPath, PortFile, Arguments: String;
  PortLines: TArrayOfString;
  ExitCode, PortNumber: Integer;
begin
  Result := False;
  ScriptPath := ExpandConstant('{tmp}\validate-existing-instance.ps1');
  PortFile := ExpandConstant('{tmp}\validated-instance-port.txt');
  if not WriteExistingDataValidator(ScriptPath) then Exit;
  Arguments := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + Quoted(ScriptPath) +
    ' -ProgramDir ' + Quoted(ExpandConstant('{app}')) +
    ' -DataDir ' + Quoted(ExpandConstant('{commonappdata}\{#DataName}')) +
    ' -Prefix {#ServicePrefix} -Channel {#BuildChannel} -PortFile ' + Quoted(PortFile);
  if RequireUninstalled then Arguments := Arguments + ' -Preserved';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Arguments, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then Exit;
  if ExitCode <> 0 then Exit;
  if not LoadStringsFromFile(PortFile, PortLines) then Exit;
  if GetArrayLength(PortLines) <> 1 then Exit;
  PortNumber := StrToIntDef(Trim(PortLines[0]), 0);
  if (PortNumber < 1024) or (PortNumber > 65533) then Exit;
  SavedPort := IntToStr(PortNumber);
  Result := True;
end;

procedure BrowseMedia(Sender: TObject);
var
  Directory: String;
begin
  Directory := '';
  if BrowseForFolder('Choose one approved media root (source files are never changed)', Directory, False) then begin
    if MediaMemo.Text <> '' then
      MediaMemo.Text := MediaMemo.Text + #13#10;
    MediaMemo.Text := MediaMemo.Text + Directory;
  end;
end;

procedure InitializeWizard;
var
  PreviousPort: String;
  MediaListFile: String;
  Lines: TArrayOfString;
  Index: Integer;
begin
  InstallSuccessful := False;
  PreparedUpgrade := False;
  PreservedReinstall := False;
  ExistingInstallation := FileExists(ExpandConstant('{commonappdata}\{#DataName}\.bluereel-native-instance')) or
    DirExists(ExpandConstant('{commonappdata}\{#DataName}\.bluereel-native-instance'));
  PreviousPort := '{#DefaultPort}';
  RegQueryStringValue(HKLM, 'Software\BlueReel\{#BuildChannel}', 'Port', PreviousPort);
  NetworkPage := CreateInputQueryPage(wpSelectDir, 'Local access',
    'Localhost only is the recommended default.',
    'No router, UPnP, public access, telemetry or metadata-provider configuration is performed.' + #13#10 +
    'Private-LAN access is an explicit Owner/administrator choice. Enter only the exact private IPv4 address of this PC.');
  NetworkPage.Add('Web port (also reserves the next two ports for internal services):', False);
  NetworkPage.Add('Bind address (127.0.0.1 for local-only):', False);
  NetworkPage.Values[0] := ExpandConstant('{param:PORT|' + PreviousPort + '}');
  NetworkPage.Values[1] := ExpandConstant('{param:BINDADDRESS|127.0.0.1}');
  MediaPage := CreateCustomPage(NetworkPage.ID, 'Approved media folders',
    'Add existing Windows folders, or type one absolute path per line.');
  MediaCaption := TNewStaticText.Create(MediaPage);
  MediaCaption.Parent := MediaPage.Surface;
  MediaCaption.SetBounds(0, 0, MediaPage.SurfaceWidth, ScaleY(48));
  MediaCaption.AutoSize := False;
  MediaCaption.WordWrap := True;
  MediaCaption.Caption := 'Only these roots are visible in {#BrandName}. Source media is never moved, modified, or deleted. ' +
    'Services use LocalService: ensure it can read the selected folders. Network shares need advanced service-account configuration.';
  MediaMemo := TNewMemo.Create(MediaPage);
  MediaMemo.Parent := MediaPage.Surface;
  MediaMemo.SetBounds(0, ScaleY(58), MediaPage.SurfaceWidth, ScaleY(120));
  MediaMemo.ScrollBars := ssVertical;
  BrowseButton := TNewButton.Create(MediaPage);
  BrowseButton.Parent := MediaPage.Surface;
  BrowseButton.SetBounds(0, ScaleY(190), ScaleX(145), ScaleY(25));
  BrowseButton.Caption := 'Add folder...';
  BrowseButton.OnClick := @BrowseMedia;
  MediaListFile := ExpandConstant('{param:MEDIAROOTSLIST|}');
  if MediaListFile <> '' then begin
    if not LoadStringsFromFile(MediaListFile, Lines) then
      RaiseException('The approved media-root list could not be read.');
    for Index := 0 to GetArrayLength(Lines) - 1 do
      MediaMemo.Lines.Add(Lines[Index]);
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Repair/upgrade preserve the saved port, LAN setting, roots and secrets. }
  Result := ExistingInstallation and ((PageID = NetworkPage.ID) or (PageID = MediaPage.ID));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  PortNumber: Integer;
begin
  Result := True;
  if CurPageID = NetworkPage.ID then begin
    PortNumber := StrToIntDef(NetworkPage.Values[0], 0);
    if (PortNumber < 1024) or (PortNumber > 65533) then begin
      MsgBox('Choose a port between 1024 and 65533. Two adjacent internal ports are also required.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ProgramFiles, SavedPort: String;
  Lines: TArrayOfString;
  Index: Integer;
begin
  Result := '';
  ProgramFiles := AddBackslash(ExpandConstant('{autopf}'));
  if CompareText(Copy(AddBackslash(ExpandFileName(WizardDirValue)), 1, Length(ProgramFiles)), ProgramFiles) <> 0 then begin
    Result := 'Service executables must be installed in a protected Program Files directory.';
    Exit;
  end;
  if ExistingInstallation and not PreparedUpgrade then begin
    PreservedReinstall := not FileExists(ExpandConstant('{app}\support\install.ps1'));
    if not ValidateExistingData(PreservedReinstall, SavedPort) then begin
      Result := 'Existing data could not be safely identified as this instance, or an incomplete old installation remains. Restore the prior application before retrying; existing data was not changed.';
      Exit;
    end;
    NetworkPage.Values[0] := SavedPort;
    if not PreservedReinstall then begin
      if not RunMaintenance('PrepareUpgrade', '') then begin
        Result := 'The validated backup or graceful service shutdown failed. No program files were replaced. Check the protected {#BrandName} logs before retrying.';
        Exit;
      end;
    end else begin
      Log('Validated clean uninstall with preserved same-instance data. Backup will run with the new runtime before migration.');
    end;
    PreparedUpgrade := True;
  end;
  RootsFile := ExpandConstant('{tmp}\approved-media-roots.txt');
  SetArrayLength(Lines, MediaMemo.Lines.Count);
  for Index := 0 to MediaMemo.Lines.Count - 1 do
    Lines[Index] := MediaMemo.Lines[Index];
  if not SaveStringsToUTF8File(RootsFile, Lines, False) then
    Result := 'Unable to stage the approved media-root list.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Extra: String;
begin
  if CurStep = ssPostInstall then begin
    if PreservedReinstall and not RunMaintenance('Backup', '') then
      RaiseException('The preserved-data backup could not be validated. Migration and service installation were not started; retained data was not replaced. Automatic rollback is not provided.');
    Extra := ' -Port ' + GetPort('') + ' -BindAddress ' + Quoted(NetworkPage.Values[1]) +
      ' -RootsFile ' + Quoted(RootsFile);
    if not RunMaintenance('Install', Extra) then
      RaiseException('{#BrandName} services did not install or become healthy. Persistent data and upgrade backups were preserved. Automatic rollback is not provided; see the native Windows recovery instructions.');
    InstallSuccessful := True;
  end;
end;

function WasSuccessful: Boolean;
begin
  Result := InstallSuccessful;
end;

function GetCustomSetupExitCode: Integer;
begin
  if InstallSuccessful then Result := 0 else Result := 1;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RemoveData: Boolean;
  Extra, SavedPort: String;
begin
  if CurUninstallStep = usUninstall then begin
    if FileExists(ExpandConstant('{commonappdata}\{#DataName}\.bluereel-native-instance')) or
      DirExists(ExpandConstant('{commonappdata}\{#DataName}\.bluereel-native-instance')) then begin
      if not ValidateExistingData(False, SavedPort) then
        RaiseException('The retained instance identity is invalid or unsafe. No service, firewall, or data removal was attempted.');
    end;
    RemoveData := ExpandConstant('{param:PURGEDATA|}') = '{#DataName}';
    if not UninstallSilent then
      RemoveData := MsgBox('Permanently delete this {#ProductName} instance''s database, accounts, configuration, artwork, history and backups?' + #13#10 +
        'Choose No to preserve all data (recommended). Source media is never removed.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    Extra := '';
    if RemoveData then
      Extra := ' -RemoveData -ConfirmDataRemoval {#DataName}';
    if not RunMaintenance('Remove', Extra) then
      RaiseException('Services or firewall rules could not be removed safely. Uninstallation was stopped; inspect the protected instance logs.');
  end;
end;
