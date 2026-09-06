; Brand and verified payload supplied by build_native.py.
#ifndef BrandName
  #error Build with build_native.py to load the product identity
#endif
#ifndef PayloadDir
  #error PayloadDir must name the verified native payload
#endif
#ifndef OutputDir
  #define OutputDir "..\..\artifacts\native-dev"
#endif
#ifndef ProductVersion
  #define ProductVersion "0.1.0-development.5"
#endif
#ifndef FileVersion
  #define FileVersion "0.1.0.5"
#endif
#ifndef BuildChannel
  #define BuildChannel "development"
#endif
#if BuildChannel == "stable"
  #define ProductName BrandName
  #define DataName "BlueAshReel"
  #define ServicePrefix "BlueReel"
  #define DefaultPort "8080"
  #define ProductId "{{78EF44D3-F1FC-4F2A-92D2-80921F170DEF}"
  #define OutputName PackageName + "-Setup-x64"
#else
  #define ProductName BrandName + " Development"
  #define DataName "BlueAshReel-Development"
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
DefaultDirName={code:GetDefaultProgramDir}
DefaultGroupName={#ProductName}
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
WizardStyle=modern
SetupLogging=yes
UninstallLogging=yes
VersionInfoVersion={#FileVersion}
VersionInfoProductVersion={#FileVersion}
VersionInfoProductTextVersion={#ProductVersion}
VersionInfoDescription=Unsigned {#BrandName} per-user Windows Agent
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
Compression=lzma2/normal
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AlwaysRestart=no
UsePreviousAppDir=no
InfoBeforeFile=development-notice.txt

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: startup; Description: "Start Blue Ash Reel when I sign in to Windows"; Flags: checkedonce

[Registry]
Root: HKCU; Subkey: "Software\BlueReel\{#BuildChannel}"; ValueType: string; ValueName: "ProgramDir"; ValueData: "{app}"
Root: HKCU; Subkey: "Software\BlueReel\{#BuildChannel}"; ValueType: string; ValueName: "DataDir"; ValueData: "{code:GetDataDir}"
Root: HKCU; Subkey: "Software\BlueReel\{#BuildChannel}"; ValueType: string; ValueName: "Port"; ValueData: "{code:GetPort}"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#ServicePrefix}Tray"; ValueData: """{app}\BlueAshReelAgent.exe"" --data-dir ""{code:GetDataDir}"""; Tasks: startup; Flags: uninsdeletevalue

[Icons]
Name: "{group}\{#ProductName}"; Filename: "{app}\BlueAshReelAgent.exe"; Parameters: "--data-dir ""{code:GetDataDir}"""
Name: "{group}\Open Blue Ash Reel Portal"; Filename: "https://{#ProductDomain}"
Name: "{group}\Open-source notices"; Filename: "{app}\OPEN-SOURCE-NOTICES.txt"
Name: "{group}\Uninstall {#ProductName}"; Filename: "{uninstallexe}"

[Code]
var
  RuntimePage, LocationsPage, CachePage: TInputQueryWizardPage;
  AdvancedPage: TInputOptionWizardPage;
  ExistingUser, LegacyMigration, InstallSuccessful, PreservedReinstall: Boolean;
  DeleteUserData: Boolean;
  ResolvedDataDir, SavedPort, ValidationProgramDir: String;

function HasInstanceMarker(const Directory: String): Boolean;
begin
  Result := FileExists(AddBackslash(Directory) + '.bluereel-native-instance') or
    DirExists(AddBackslash(Directory) + '.bluereel-native-instance');
end;

function Quoted(const Value: String): String;
begin
  if Pos('"', Value) <> 0 then RaiseException('Invalid installation argument.');
  Result := '"' + Value + '"';
end;

function GetDefaultProgramDir(Param: String): String;
begin
  if not RegQueryStringValue(HKCU,'Software\BlueReel\{#BuildChannel}','ProgramDir',Result) then
    Result := ExpandConstant('{localappdata}\Programs\{#DataName}');
end;

function GetDataDir(Param: String): String;
begin
  if ResolvedDataDir = '' then begin
    if not RegQueryStringValue(HKCU, 'Software\BlueReel\{#BuildChannel}', 'DataDir', ResolvedDataDir) then
      if not RegQueryStringValue(HKLM, 'Software\BlueReel\{#BuildChannel}', 'DataDir', ResolvedDataDir) then
        ResolvedDataDir := ExpandConstant('{localappdata}\{#DataName}');
  end;
  Result := ResolvedDataDir;
end;

function GetPort(Param: String): String;
begin
  Result := SavedPort;
  if Result = '' then begin
    if not RegQueryStringValue(HKCU, 'Software\BlueReel\{#BuildChannel}', 'Port', Result) then
      if not RegQueryStringValue(HKLM, 'Software\BlueReel\{#BuildChannel}', 'Port', Result) then Result := '{#DefaultPort}';
  end;
end;

function CommonArguments: String;
begin
  Result := ' -ProgramDir ' + Quoted(ExpandConstant('{app}')) + ' -DataDir ' + Quoted(GetDataDir('')) +
    ' -Instance {#BuildChannel} -UserAccount ' + Quoted(GetEnv('USERDOMAIN') + '\' + GetUserNameString);
end;

function RunMaintenance(const Action, Extra: String; Elevated: Boolean): Boolean;
var Arguments: String; ExitCode: Integer;
begin
  Arguments := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    Quoted(ExpandConstant('{app}\support\user-install.ps1')) + ' -Action ' + Action + CommonArguments + Extra;
  if Elevated then
    Result := ShellExec('runas',ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),Arguments,
      ExpandConstant('{app}'),SW_HIDE,ewWaitUntilTerminated,ExitCode)
  else
    Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),Arguments,
      ExpandConstant('{app}'),SW_HIDE,ewWaitUntilTerminated,ExitCode);
  Result := Result and (ExitCode = 0);
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
    Script.Add('  $extended = if ($Root.StartsWith(''\\'')) { ''\\?\UNC\'' + $Root.Substring(2) } else { ''\\?\'' + $Root }');
    Script.Add('  $pending.Enqueue($extended); $files = 0');
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
    Script.Add('    if (-not ($metadata.PSObject.Properties.Name -contains ''runtime_mode'') -or $metadata.runtime_mode -ne ''per_user'') {');
    Script.Add('    if (Test-Path -LiteralPath (''HKLM:\Software\BlueReel\'' + $Channel)) { throw ''Old product registration remains'' }');
    Script.Add('    foreach ($role in @(''API'',''Worker'',''Web'',''Proxy'')) {');
    Script.Add('      if (Test-Path -LiteralPath (''HKLM:\SYSTEM\CurrentControlSet\Services\'' + $Prefix + $role)) { throw ''Old service registration remains'' }');
    Script.Add('    }');
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
    ' -ProgramDir ' + Quoted(ValidationProgramDir) +
    ' -DataDir ' + Quoted(GetDataDir('')) +
    ' -Prefix {#ServicePrefix} -Channel {#BuildChannel} -PortFile ' + Quoted(PortFile);
  if RequireUninstalled then Arguments := Arguments + ' -Preserved';
  if LegacyMigration then begin
    if not ShellExec('runas',ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Arguments, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then Exit;
  end else if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Arguments, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then Exit;
  if ExitCode <> 0 then Exit;
  if not LoadStringsFromFile(PortFile, PortLines) then Exit;
  if GetArrayLength(PortLines) <> 1 then Exit;
  PortNumber := StrToIntDef(Trim(PortLines[0]), 0);
  if (PortNumber < 1024) or (PortNumber > 65533) then Exit;
  SavedPort := IntToStr(PortNumber);
  Result := True;
end;


procedure InitializeWizard;
var Registered: String;
begin
  ExistingUser := RegQueryStringValue(HKCU,'Software\BlueReel\{#BuildChannel}','ProgramDir',Registered);
  LegacyMigration := (not ExistingUser) and RegQueryStringValue(HKLM,'Software\BlueReel\{#BuildChannel}','ProgramDir',Registered);
  SavedPort := GetPort('');
  RuntimePage := CreateInputQueryPage(wpSelectDir, 'Agent runtime', 'Runs only while you are signed in to Windows.',
    'Sign in to blueashreel.com after installation to pair the Agent. Libraries and access are managed from the authenticated Portal.');
  RuntimePage.Add('Application-data directory:',False);
  RuntimePage.Add('Local Agent port:',False);
  RuntimePage.Add('Maximum concurrent transcodes (1-8):',False);
  RuntimePage.Add('CPU threads per transcode (1-16):',False);
  RuntimePage.Values[0] := ExpandConstant('{param:DATADIR|' + GetDataDir('') + '}');
  RuntimePage.Values[1] := ExpandConstant('{param:PORT|' + SavedPort + '}');
  RuntimePage.Values[2] := '2'; RuntimePage.Values[3] := '2';
  AdvancedPage := CreateInputOptionPage(RuntimePage.ID,'Advanced storage','Safe separate locations are provided.',
    'Choose Advanced to change database, artwork/cache, temporary transcodes, backup and log directories. Existing locations are always preserved during upgrades.',True,False);
  AdvancedPage.Add('Use recommended locations'); AdvancedPage.Add('Advanced: choose individual storage locations'); AdvancedPage.SelectedValueIndex := 0;
  LocationsPage := CreateInputQueryPage(AdvancedPage.ID,'Advanced storage','Database and artwork/cache',
    'Leave blank to use dedicated subdirectories inside the application-data location. Program files and mutable data must remain separate.');
  LocationsPage.Add('SQLite database directory:',False); LocationsPage.Add('Artwork/cache directory:',False);
  CachePage := CreateInputQueryPage(LocationsPage.ID,'Advanced storage','Temporary files, backups and logs',
    'Leave blank to use separate temp, backups and logs subdirectories. Source media is read-only.');
  CachePage.Add('Temporary transcode directory:',False); CachePage.Add('Backup directory:',False); CachePage.Add('Log directory:',False);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := ((ExistingUser or LegacyMigration) and ((PageID = RuntimePage.ID) or (PageID = AdvancedPage.ID) or
    (PageID = LocationsPage.ID) or (PageID = CachePage.ID))) or
    ((AdvancedPage.SelectedValueIndex = 0) and ((PageID = LocationsPage.ID) or (PageID = CachePage.ID)));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var PortNumber: Integer;
begin
  Result := True;
  if CurPageID = RuntimePage.ID then begin
    PortNumber := StrToIntDef(RuntimePage.Values[1],0);
    Result := (PortNumber >= 1024) and (PortNumber <= 65533) and
      (StrToIntDef(RuntimePage.Values[2],0) >= 1) and (StrToIntDef(RuntimePage.Values[2],0) <= 8) and
      (StrToIntDef(RuntimePage.Values[3],0) >= 1) and (StrToIntDef(RuntimePage.Values[3],0) <= 16);
    if not Result then MsgBox('Choose a port from 1024 to 65533 and resource limits within the displayed ranges.',mbError,MB_OK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not (ExistingUser or LegacyMigration) then begin
    ResolvedDataDir := RuntimePage.Values[0]; SavedPort := RuntimePage.Values[1];
  end;
  ValidationProgramDir := ExpandConstant('{app}');
  if LegacyMigration then RegQueryStringValue(HKLM,'Software\BlueReel\{#BuildChannel}','ProgramDir',ValidationProgramDir);
  if ExistingUser or LegacyMigration or HasInstanceMarker(GetDataDir('')) then begin
    PreservedReinstall := not FileExists(AddBackslash(ValidationProgramDir) + 'runtime\python\python.exe');
    if not ValidateExistingData(PreservedReinstall, SavedPort) then begin
      Result := 'Retained data or program files could not be safely identified. No installation files were replaced.';
      Exit;
    end;
    RuntimePage.Values[1] := SavedPort;
    if ExistingUser and not PreservedReinstall then begin
      if not RunMaintenance('PrepareUpgrade','',False) then
        Result := 'The existing Agent could not stop or its backup could not be validated. Existing files were preserved.';
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Extra: String; ExitCode: Integer; Launched: Boolean;
begin
  if CurStep = ssPostInstall then begin
    if PreservedReinstall and not RunMaintenance('Backup','',False) then
      RaiseException('The retained-data backup failed validation. Migrations were not started.');
    Extra := ' -Port ' + GetPort('') + ' -MaxProcesses ' + RuntimePage.Values[2] + ' -Threads ' + RuntimePage.Values[3];
    if not (ExistingUser or LegacyMigration) then Extra := Extra +
      ' -DatabaseDir ' + Quoted(LocationsPage.Values[0]) + ' -ArtworkDir ' + Quoted(LocationsPage.Values[1]) +
      ' -TempDir ' + Quoted(CachePage.Values[0]) + ' -BackupsDir ' + Quoted(CachePage.Values[1]) + ' -LogsDir ' + Quoted(CachePage.Values[2]);
    if not RunMaintenance('Install',Extra,LegacyMigration) then begin
      if LegacyMigration then RunMaintenance('RollbackMigration','',True);
      if ExistingUser and not PreservedReinstall then RunMaintenance('RollbackUser','',False);
      RaiseException('Agent setup did not complete. Existing data and validated recovery backups were preserved.');
    end;
    Launched := ExecAsOriginalUser(ExpandConstant('{app}\BlueAshReelAgent.exe'),'--data-dir ' + Quoted(GetDataDir('')),
      ExpandConstant('{app}'),SW_SHOWNORMAL,ewNoWait,ExitCode);
    if not Launched or not RunMaintenance('Health','',False) then begin
      if LegacyMigration then RunMaintenance('RollbackMigration','',True);
      if ExistingUser and not PreservedReinstall then RunMaintenance('RollbackUser','',False);
      RaiseException('The per-user Agent did not become healthy. Legacy migration was rolled back when available.');
    end;
    if LegacyMigration and not RunMaintenance('FinalizeMigration','',True) then
      RaiseException('The new Agent is healthy but legacy service retirement needs recovery review.');
    if not ExecAsOriginalUser(ExpandConstant('{app}\BlueAshReelAgent.exe'),
      '--data-dir ' + Quoted(GetDataDir('')) + ' --finish-install',
      ExpandConstant('{app}'),SW_SHOWNORMAL,ewNoWait,ExitCode) then
      RaiseException('The Agent is installed and healthy, but its pairing window could not open. Choose Pair Agent from the Windows tray.');
    InstallSuccessful := True;
  end;
end;

function WasSuccessful: Boolean;
begin Result := InstallSuccessful; end;

function GetCustomSetupExitCode: Integer;
begin if InstallSuccessful then Result := 0 else Result := 1; end;

function InitializeUninstall: Boolean;
var Choice: Integer;
begin
  DeleteUserData := False;
  Result := True;
  { Unattended uninstalls always retain identity and data. A visible, explicit
    choice is required for destructive cleanup; No is the safe default. }
  if UninstallSilent then Exit;
  Choice := MsgBox('Do you also want to permanently delete this Agent''s local identity and application data?' + #13#10#13#10 +
    'Yes: delete pairing credentials, settings, database, artwork, temporary files, logs and backups, including configured advanced storage.' + #13#10 +
    'No (recommended): retain them so reinstalling can recover this Agent.' + #13#10#13#10 +
    'Source media files are never deleted. To revoke remote access immediately, use Unpair this Agent before uninstalling.' + #13#10 +
    'Cancel: leave the Agent installed.', mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON2);
  Result := Choice <> IDCANCEL;
  DeleteUserData := Choice = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Registered: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    ValidationProgramDir := ExpandConstant('{app}');
    if not ValidateExistingData(False, SavedPort) then RaiseException('The retained instance could not be safely identified.');
    if not RunMaintenance('Stop','',False) then RaiseException('The Agent could not finish stopping. Uninstall was stopped.');
    if DeleteUserData then begin
      if not RunMaintenance('RemoveData',' -ConfirmDeleteData',False) then
        RaiseException('Local data could not be removed safely. Uninstall was stopped; retained data needs recovery review.');
      if RegQueryStringValue(HKCU,'Software\BlueReel\{#BuildChannel}','DataDir',Registered) and
          (CompareText(Registered, GetDataDir('')) = 0) then begin
        RegDeleteValue(HKCU,'Software\BlueReel\{#BuildChannel}','ProgramDir');
        RegDeleteValue(HKCU,'Software\BlueReel\{#BuildChannel}','DataDir');
        RegDeleteValue(HKCU,'Software\BlueReel\{#BuildChannel}','Port');
        RegDeleteKeyIfEmpty(HKCU,'Software\BlueReel\{#BuildChannel}');
      end;
    end;
  end;
  { Retain every identity and data file unless the user explicitly chose Yes. }
end;
