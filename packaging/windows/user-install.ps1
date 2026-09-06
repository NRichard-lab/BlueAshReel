[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Install','PrepareUpgrade','Backup','RollbackUser','FinalizeMigration','RollbackMigration','Stop','Health')][string]$Action,
    [Parameter(Mandatory)][string]$ProgramDir,
    [Parameter(Mandatory)][string]$DataDir,
    [ValidateSet('development','stable')][string]$Instance = 'development',
    [int]$Port = 18080,
    [string]$DatabaseDir, [string]$ArtworkDir, [string]$TempDir, [string]$BackupsDir, [string]$LogsDir,
    [int]$MaxProcesses = 2, [int]$Threads = 2,
    [string]$UserSid, [string]$UserAccount
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Dedicated([string]$Value) {
    if (-not [IO.Path]::IsPathRooted($Value) -or $Value -match '(^|[\\/])\.\.([\\/]|$)') { throw 'Dedicated absolute path required.' }
    $taskFull = [IO.Path]::GetFullPath($Value).TrimEnd('\')
    if ($taskFull -eq [IO.Path]::GetPathRoot($taskFull).TrimEnd('\')) { throw 'Drive-root storage is unsupported.' }
    for ($taskProbe = $taskFull; $taskProbe; $taskProbe = [IO.Path]::GetDirectoryName($taskProbe)) {
        if ((Test-Path -LiteralPath $taskProbe) -and ((Get-Item -LiteralPath $taskProbe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Linked storage is unsupported.' }
    }
    return $taskFull
}
$ProgramDir = Dedicated $ProgramDir
$DataDir = Dedicated $DataDir
$taskPython = Join-Path $ProgramDir 'runtime\python\python.exe'
$taskState = Join-Path $DataDir 'state'
$taskRecord = Join-Path $DataDir 'configuration\installation.json'
$taskMigration = Join-Path $taskState 'user-migration.json'
function Native([string]$Command, [string[]]$Extra = @()) {
    & $taskPython -I -B -m app.native_user_install $Command --data-dir $DataDir @Extra
    if ($LASTEXITCODE -ne 0) { throw 'Per-user runtime configuration failed; retained data was preserved.' }
}
function Assert-NoLinkedTree([string]$Root) {
    $taskQueue = [Collections.Generic.Queue[string]]::new()
    $taskQueue.Enqueue((Dedicated $Root))
    while ($taskQueue.Count) {
        $taskItem = Get-Item -LiteralPath $taskQueue.Dequeue() -Force
        if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked recovery or program data is unsupported.' }
        if ($taskItem.PSIsContainer) { foreach ($taskChild in @(Get-ChildItem -LiteralPath $taskItem.FullName -Force)) { $taskQueue.Enqueue($taskChild.FullName) } }
    }
}
function Stop-UserRuntime {
    if (-not (Test-Path -LiteralPath $taskState)) { return }
    $taskCommand = Join-Path $taskState 'tray-command.json'
    $taskMessage = @{action='exit'; created_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()} | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($taskCommand,$taskMessage)
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(100)
    do {
        $taskStatusFile = Join-Path $taskState 'tray-status.json'
        if (-not (Test-Path -LiteralPath $taskStatusFile)) { return }
        $taskStatus = Get-Content -LiteralPath $taskStatusFile -Raw | ConvertFrom-Json
        $taskRuntimePid = [uint32]$taskStatus.pid
        if ($taskRuntimePid -eq 0) { throw 'Invalid runtime process identity.' }
        # Inno can invoke 32-bit PowerShell. Get-Process.Path cannot inspect a
        # 64-bit pythonw module from that process, but CIM reports its image path.
        $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskRuntimePid"
        if (-not $taskProcess) { return }
        $taskProcessPath = $taskProcess.ExecutablePath
        if ($taskProcessPath -ine (Join-Path $ProgramDir 'runtime\python\pythonw.exe')) { throw 'Unexpected runtime process identity.' }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    throw 'The prior Agent did not finish stopping. Program files and data were preserved.'
}
function Validate-LegacyServices($Record) {
    $taskExpectedPrefix = if ($Instance -eq 'development') { 'BlueReelDevelopment' } else { 'BlueReel' }
    if ($Record.instance -cne $Instance -or $Record.service_prefix -cne $taskExpectedPrefix -or (Dedicated $Record.data_dir) -ine $DataDir) { throw 'Legacy instance identity differs.' }
    $taskProtectedProgram = Dedicated $Record.program_dir
    if (-not $taskProtectedProgram.StartsWith($env:ProgramFiles + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Legacy program files are not in protected Program Files.' }
    $taskServices = @()
    foreach ($taskSuffix in @('Remote','Proxy','Worker','API','Web')) {
        $taskName = $Record.service_prefix + $taskSuffix
        $taskService = Get-CimInstance Win32_Service -Filter "Name='$taskName'"
        if ($taskService) {
            $taskExpected = Join-Path $Record.program_dir "services\$taskName.exe"
            if ($taskService.PathName.Trim('"') -ine $taskExpected) { throw 'Legacy service path differs from the recorded instance.' }
            $taskExpectedAccount = if ($taskSuffix -eq 'Remote') { 'NT SERVICE\' + $taskName } else { 'NT AUTHORITY\LocalService' }
            if ($taskService.StartName -ine $taskExpectedAccount) { throw 'Legacy service account differs from the installed instance.' }
            $taskServices += @{name=$taskName; running=($taskService.State -eq 'Running'); start_mode=$taskService.StartMode}
        }
    }
    return $taskServices
}
function Read-Migration {
    $taskSaved = Get-Content -LiteralPath (Dedicated $taskMigration) -Raw | ConvertFrom-Json
    $taskRecovery = Dedicated $taskSaved.recovery
    if (-not $taskRecovery.StartsWith($DataDir + '\upgrade\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected recovery directory.' }
    Assert-NoLinkedTree $taskRecovery
    $taskOldRecord = Get-Content -LiteralPath (Join-Path $taskRecovery 'installation.json') -Raw | ConvertFrom-Json
    if ($taskOldRecord.program_dir -ine $taskSaved.old_program) { throw 'Recovery program identity changed.' }
    $null = Validate-LegacyServices $taskOldRecord
    $taskAllowed = @('Remote','Proxy','Worker','API','Web') | ForEach-Object { $taskOldRecord.service_prefix + $_ }
    foreach ($taskService in $taskSaved.services) {
        if ($taskService.name -cnotin $taskAllowed -or $taskService.start_mode -notin @('Auto','Manual','Disabled')) { throw 'Unexpected recovery service identity.' }
        $taskWrapper = Dedicated (Join-Path $taskSaved.old_program ('services\' + $taskService.name + '.exe'))
        if (-not (Test-Path -LiteralPath $taskWrapper)) { throw 'The protected legacy recovery executable is missing.' }
    }
    return $taskSaved
}
function Restore-LegacyServices($Saved) {
    # Restore the complete registration graph before starting any service.
    # Proxy/Web wrappers can depend on API/Worker registrations removed earlier.
    foreach ($taskService in $Saved.services) {
        if (-not (Get-Service -Name $taskService.name -ErrorAction SilentlyContinue)) {
            $taskWrapper = Join-Path $Saved.old_program ('services\' + $taskService.name + '.exe')
            & $taskWrapper install
            if ($LASTEXITCODE -ne 0) { throw 'Legacy service registration could not be restored.' }
        }
        $taskMode = switch ($taskService.start_mode) { 'Auto' {'Automatic'} 'Disabled' {'Disabled'} default {'Manual'} }
        Set-Service -Name $taskService.name -StartupType $taskMode
    }
    foreach ($taskSuffix in @('API','Worker','Web','Proxy','Remote')) {
        foreach ($taskService in $Saved.services) {
            if ($taskService.running -and $taskService.name.EndsWith($taskSuffix,[StringComparison]::Ordinal)) {
                if ($taskService.start_mode -eq 'Disabled') { Set-Service -Name $taskService.name -StartupType Manual }
                Start-Service -Name $taskService.name
                if ($taskService.start_mode -eq 'Disabled') { Set-Service -Name $taskService.name -StartupType Disabled }
            }
        }
    }
}
function Begin-LegacyMigration($Record) {
    if (-not $UserSid -and $UserAccount) { $script:UserSid = ([Security.Principal.NTAccount]::new($UserAccount)).Translate([Security.Principal.SecurityIdentifier]).Value }
    if (-not $UserSid -or $UserSid -notmatch '^S-1-5-21-[0-9-]+$') { throw 'The installing Windows user must be identified before elevated migration.' }
    $taskServices = @(Validate-LegacyServices $Record)
    $taskOldPython = Join-Path $Record.program_dir 'runtime\python\python.exe'
    $taskDatabaseValue = & $taskOldPython -I -B -c 'import sys; from pathlib import Path; from app.native_runtime import load_installation,load_configuration; print(load_configuration(load_installation(Path(sys.argv[1]))).database_url.removeprefix("sqlite:///"))' $DataDir
    if ($LASTEXITCODE -ne 0 -or @($taskDatabaseValue).Count -ne 1) { throw 'Legacy database location could not be validated.' }
    $taskDatabase = Dedicated ([string]$taskDatabaseValue)
    if (-not $taskDatabase.StartsWith($DataDir + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Legacy database is outside its protected installation.' }
    # Existing trusted installation validates its backup before any stop or edit.
    & $taskOldPython -I -B -m app.native_install backup --data-dir $DataDir
    if ($LASTEXITCODE -ne 0) { throw 'The validated legacy backup failed. Existing services were not changed.' }
    $taskRecovery = Join-Path $DataDir ('upgrade\user-migration-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
    New-Item -ItemType Directory -Path $taskRecovery | Out-Null
    Copy-Item -LiteralPath (Join-Path $DataDir 'configuration\.env') -Destination (Join-Path $taskRecovery '.env')
    Copy-Item -LiteralPath $taskRecord -Destination (Join-Path $taskRecovery 'installation.json')
    $taskAclFile = Join-Path $taskRecovery 'data-acls.txt'
    & icacls.exe $DataDir /save $taskAclFile /T /Q
    if ($LASTEXITCODE -ne 0) { throw 'Legacy permission snapshot failed.' }
    $taskLedger = @{recovery=$taskRecovery; services=$taskServices; old_program=$Record.program_dir; user_sid=$UserSid; database_path=$taskDatabase; validated=$false; backup_validated=$true; drained_snapshot=$false}
    $taskLedger |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $taskMigration -Encoding UTF8
    foreach ($taskService in $taskServices) {
        if ($taskService.running) { Stop-Service -Name $taskService.name; (Get-Service -Name $taskService.name).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(120)) }
    }
    # Capture the final drained database for automatic rollback. Retain the
    # independently validated full archive above as a second recovery source.
    & $taskOldPython -I -B -c 'import sqlite3,sys; a=sqlite3.connect(sys.argv[1]); b=sqlite3.connect(sys.argv[2]); a.backup(b); assert b.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; b.close(); a.close()' $taskDatabase (Join-Path $taskRecovery 'app.db')
    if ($LASTEXITCODE -ne 0) { throw 'Drained database recovery snapshot failed.' }
    $taskLedger.drained_snapshot = $true
    $taskLedger | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $taskMigration -Encoding UTF8
    # Retain existing service/SYSTEM rights until validation and rollback window.
    & icacls.exe $DataDir /grant ('*' + $UserSid + ':(OI)(CI)F') /T /Q
    if ($LASTEXITCODE -ne 0) { throw 'Logged-in user access could not be granted safely.' }
    Native 'adopt-legacy' @('--program-dir',$ProgramDir,'--instance',$Instance)
}
try {
    switch ($Action) {
        'PrepareUpgrade' { Stop-UserRuntime; Native 'prepare-upgrade' }
        'Backup' { Native 'backup' }
        'RollbackUser' {
            Stop-UserRuntime
            Native 'restore'
            $taskUserSaved = Get-Content -LiteralPath (Join-Path $taskState 'user-upgrade.json') -Raw | ConvertFrom-Json
            if (-not $taskUserSaved.program_retained) { throw 'Previous program files were not retained in this snapshot.' }
            $taskUserRecovery = Dedicated $taskUserSaved.backup
            $taskUserRecord = Get-Content -LiteralPath $taskRecord -Raw | ConvertFrom-Json
            $taskBackupRoot = Dedicated $taskUserRecord.storage.backups
            if ([IO.Path]::GetDirectoryName($taskUserRecovery) -ine $taskBackupRoot) { throw 'Unexpected recovery location.' }
            $taskOldFiles = Dedicated (Join-Path $taskUserRecovery 'program')
            # Native restore already checked both complete trees without following
            # links. Inbox Robocopy handles bundled dependency paths over MAX_PATH.
            & robocopy.exe $taskOldFiles $ProgramDir /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /XJ /NFL /NDL /NJH /NJS /NP
            if ($LASTEXITCODE -ge 8) { throw 'Previous program files could not be fully restored.' }
        }
        'Stop' { Stop-UserRuntime }
        'Health' { Native 'health' }
        'Install' {
            if (Test-Path -LiteralPath $taskRecord) {
                $taskExisting = Get-Content -LiteralPath $taskRecord -Raw | ConvertFrom-Json
                if (-not ($taskExisting.PSObject.Properties.Name -contains 'runtime_mode') -or $taskExisting.runtime_mode -ne 'per_user') {
                    Begin-LegacyMigration $taskExisting
                }
            }
            $taskArguments = @('--program-dir',$ProgramDir,'--instance',$Instance,'--port',[string]$Port,'--max-processes',[string]$MaxProcesses,'--threads',[string]$Threads)
            foreach ($taskOption in @(@('database',$DatabaseDir),@('artwork',$ArtworkDir),@('temp',$TempDir),@('backups',$BackupsDir),@('logs',$LogsDir))) {
                if ($taskOption[1]) { $taskArguments += @('--' + $taskOption[0] + '-dir',$taskOption[1]) }
            }
            Native 'configure' $taskArguments
            Native 'migrate'
        }
        'FinalizeMigration' {
            if (Test-Path -LiteralPath $taskMigration) {
                Native 'health'
                $taskSaved = Read-Migration
                $taskLive = Get-Content -LiteralPath (Join-Path $taskState 'tray-status.json') -Raw | ConvertFrom-Json
                if (-not $taskLive.healthy -or ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $taskLive.updated_at) -gt 20) { throw 'Tray health validation expired.' }
                foreach ($taskService in $taskSaved.services) {
                    $taskInstalled = Get-CimInstance Win32_Service -Filter "Name='$($taskService.name)'"
                    $taskWrapper = Join-Path $taskSaved.old_program ('services\' + $taskService.name + '.exe')
                    if ($taskInstalled -and ($taskInstalled.State -ne 'Stopped' -or $taskInstalled.PathName.Trim('"') -ine $taskWrapper)) { throw 'Legacy service changed during validation.' }
                    if ($taskInstalled) {
                        & $taskWrapper uninstall
                        if ($LASTEXITCODE -ne 0) { throw 'A validated legacy service could not be removed.' }
                    }
                }
                $taskSaved.validated = $true
                $taskSaved | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $taskMigration -Encoding UTF8
            }
        }
        'RollbackMigration' {
            Stop-UserRuntime
            $taskSaved = Read-Migration
            $taskRecovery = Dedicated $taskSaved.recovery
            if (-not $taskRecovery.StartsWith($DataDir + '\upgrade\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected recovery directory.' }
            Copy-Item -LiteralPath (Join-Path $taskRecovery '.env') -Destination (Join-Path $DataDir 'configuration\.env') -Force
            Copy-Item -LiteralPath (Join-Path $taskRecovery 'installation.json') -Destination $taskRecord -Force
            if (Test-Path -LiteralPath (Join-Path $taskRecovery 'app.db')) {
                $taskDatabase = Dedicated $taskSaved.database_path
                if (-not $taskDatabase.StartsWith($DataDir + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected retained database location.' }
                if (Test-Path -LiteralPath $taskDatabase) { Copy-Item -LiteralPath $taskDatabase -Destination (Join-Path $taskRecovery 'failed-user-app.db') -Force }
                Copy-Item -LiteralPath (Join-Path $taskRecovery 'app.db') -Destination $taskDatabase -Force
                foreach ($taskSuffix in @('-wal','-shm','-journal')) {
                    $taskSidecar = $taskDatabase + $taskSuffix
                    if (Test-Path -LiteralPath $taskSidecar) { Move-Item -LiteralPath $taskSidecar -Destination (Join-Path $taskRecovery ('failed-user-app.db' + $taskSuffix)) -Force }
                }
            }
            & icacls.exe ([IO.Path]::GetDirectoryName($DataDir)) /restore (Join-Path $taskRecovery 'data-acls.txt') /Q
            if ($LASTEXITCODE -ne 0) { throw 'Legacy permission restoration requires recovery review.' }
            Restore-LegacyServices $taskSaved
        }
    }
    exit 0
} catch {
    Write-Error ('Agent setup could not complete (' + $_.Exception.GetType().Name + ', helper line ' + $_.InvocationInfo.ScriptLineNumber + '). Original data and validated recovery material were retained.') -ErrorAction Continue
    exit 1
}
