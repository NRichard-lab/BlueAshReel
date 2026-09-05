<#
Opt-in privileged acceptance for the disposable DEVELOPMENT instance only.
Never invoke this script against a production installation. No rollback is
automatic. Reports contain allowlisted facts/hashes, never raw logs or .env.
Run with Windows PowerShell x64 through an explicit RunAs elevation.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Inventory','Snapshot','Diagnostics','StopState','SignalStop','Restart','Backup','VerifyFirewall','UninstallPreserve','UninstallPurge','VerifyUninstallPreserve')][string]$Phase,
    [Parameter(Mandatory)][string]$ReportDirectory,
    [switch]$AllowDisposableInstance,
    [ValidateSet('BlueAshReel-Development')][string]$ConfirmPurge,
    [string]$PriorReport
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$TaskPrefix = 'BlueReelDevelopment'
$TaskProgram = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'BlueAshReel Development'
$TaskData = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'BlueAshReel-Development'
$TaskPython = Join-Path $TaskProgram 'runtime\python\python.exe'
$TaskNode = Join-Path $TaskProgram 'runtime\node\node.exe'
$TaskRoles = @('API','Worker','Web','Proxy')
$TaskReport = [ordered]@{schema_version=1; instance='development'; phase=$Phase; started_utc=[DateTime]::UtcNow.ToString('o'); passed=$false}
$TaskReportPath = $null

function Assert-NoReparse([string]$Path) {
    $taskCandidate = [IO.Path]::GetFullPath($Path)
    while ($taskCandidate) {
        if (Test-Path -LiteralPath $taskCandidate) {
            if ((Get-Item -LiteralPath $taskCandidate -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Acceptance target contains a reparse point.'
            }
        }
        $taskParent = [IO.Directory]::GetParent($taskCandidate)
        $taskCandidate = if ($null -eq $taskParent) { $null } else { $taskParent.FullName }
    }
}

function Assert-Instance {
    Assert-NoReparse $TaskProgram
    Assert-NoReparse $TaskData
    $taskMarker = Join-Path $TaskData '.bluereel-native-instance'
    Assert-NoReparse $taskMarker
    if (-not (Test-Path -LiteralPath $taskMarker -PathType Leaf) -or (Get-Item -LiteralPath $taskMarker).Length -gt 128 -or
        [IO.File]::ReadAllText($taskMarker).TrimEnd([char[]]"`r`n") -cne $TaskPrefix) {
        throw 'Disposable development instance marker is absent or foreign.'
    }
    $taskMetadataPath = Join-Path $TaskData 'configuration\installation.json'
    Assert-NoReparse $taskMetadataPath
    $taskMetadata = Get-Content -LiteralPath $taskMetadataPath -Raw | ConvertFrom-Json
    if ($taskMetadata.instance -cne 'development' -or $taskMetadata.service_prefix -cne $TaskPrefix -or
        [IO.Path]::GetFullPath($taskMetadata.program_dir).TrimEnd('\') -ine $TaskProgram -or
        [IO.Path]::GetFullPath($taskMetadata.data_dir).TrimEnd('\') -ine $TaskData) {
        throw 'Disposable installation metadata has an unexpected identity.'
    }
    foreach ($taskRole in $TaskRoles) {
        $taskName = $TaskPrefix + $taskRole
        $taskService = Get-CimInstance Win32_Service -Filter "Name='$taskName'"
        if ($null -ne $taskService -and ($taskService.PathName.Trim('"') -ine (Join-Path $TaskProgram "services\$taskName.exe") -or
            $taskService.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\LOCAL SERVICE'))) {
            throw 'A development service has a foreign binary or account identity.'
        }
    }
    $null = Get-TestRemoteService
}

function Get-TestRemoteService {
    $taskName = $TaskPrefix + 'Remote'
    $taskService = Get-CimInstance Win32_Service -Filter "Name='$taskName'"
    if ($null -ne $taskService -and
        ($taskService.PathName.Trim('"') -ine (Join-Path $TaskProgram "services\$taskName.exe") -or
         $taskService.StartName -ine "NT SERVICE\$taskName")) {
        throw 'The disposable remote service has a foreign binary or account identity.'
    }
    return $taskService
}

function Quote-Argument([string]$Value) {
    # CommandLineToArgvW-compatible quoting, including a trailing backslash.
    return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Invoke-PrivateProcess([string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds = 90) {
    $taskAllowed = @($TaskPython, $TaskNode, (Join-Path $TaskProgram 'unins000.exe'))
    if ($Executable -notin $taskAllowed) { throw 'Acceptance subprocess is outside the fixed instance allowlist.' }
    Assert-NoReparse $Executable
    $taskStart = [Diagnostics.ProcessStartInfo]::new()
    $taskStart.FileName = $Executable
    $taskStart.Arguments = (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
    # The uninstaller must not hold ProgramData (or Program Files) open as its
    # current directory while removing that exact tree.
    $taskStart.WorkingDirectory = if ($Executable -ieq (Join-Path $TaskProgram 'unins000.exe')) { $ReportDirectory } else { $TaskData }
    $taskStart.UseShellExecute = $false
    $taskStart.CreateNoWindow = $true
    $taskStart.RedirectStandardOutput = $true
    $taskStart.RedirectStandardError = $true
    foreach ($taskVariable in @('NODE_OPTIONS','NODE_PATH','BLUEREEL_STOP_FILE','PYTHONPATH','PYTHONHOME')) {
        $taskStart.EnvironmentVariables.Remove($taskVariable)
    }
    $taskProcess = [Diagnostics.Process]::new()
    $taskProcess.StartInfo = $taskStart
    try {
        $null = $taskProcess.Start()
        $taskOutput = $taskProcess.StandardOutput.ReadToEndAsync()
        $taskError = $taskProcess.StandardError.ReadToEndAsync()
        if (-not $taskProcess.WaitForExit($TimeoutSeconds * 1000)) {
            $taskProcess.Kill()
            throw 'Acceptance subprocess exceeded its bounded deadline.'
        }
        $taskProcess.WaitForExit()
        return [pscustomobject]@{exit_code=$taskProcess.ExitCode; stdout=$taskOutput.Result; stderr_bytes=$taskError.Result.Length}
    } finally { $taskProcess.Dispose() }
}

function Invoke-Probe([string]$Name, [int]$TimeoutSeconds = 90) {
    $taskSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'test_instance_probe.py'))
    $taskEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($taskSource))
    $taskCode = "exec(__import__('base64').b64decode('$taskEncoded'))"
    $taskResult = Invoke-PrivateProcess $TaskPython @('-I','-B','-c',$taskCode,$Name,$TaskProgram,$TaskData) $TimeoutSeconds
    if ($taskResult.exit_code -ne 0) {
        try {
            $taskFailure = $taskResult.stdout | ConvertFrom-Json
            if ($taskFailure.probe_failed -and $taskFailure.error_type -match '^[A-Za-z0-9_]{1,80}$' -and $taskFailure.operation -match '^[a-z_]{1,80}$') {
                $TaskReport.probe_failure = [ordered]@{probe=$Name; error_type=$taskFailure.error_type; operation=$taskFailure.operation}
            }
        } catch { }
        throw 'Private native acceptance probe failed; raw output was withheld.'
    }
    return ($taskResult.stdout | ConvertFrom-Json)
}

function Get-ServiceEvidence {
    $taskEvidence = @()
    foreach ($taskRole in $TaskRoles) {
        $taskName = $TaskPrefix + $taskRole
        $taskService = Get-CimInstance Win32_Service -Filter "Name='$taskName'"
        if ($null -eq $taskService) { throw 'A required disposable service is missing.' }
        $taskRegistry = Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\$taskName"
        $taskEvidence += [ordered]@{
            role=$taskRole; state=$taskService.State; pid=[int]$taskService.ProcessId
            local_service=($taskService.StartName -in @('NT AUTHORITY\LocalService','NT AUTHORITY\LOCAL SERVICE'))
            automatic=($taskService.StartMode -eq 'Auto'); delayed_auto_start=($taskRegistry.DelayedAutoStart -eq 1)
            service_sid_enabled=($taskRegistry.ServiceSidType -eq 1)
        }
    }
    return $taskEvidence
}

function Test-ConfigurationAcl($Acl,[string[]]$Allowed,[bool]$IsDirectory,[bool]$IsRoot) {
    if ($Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')) { return $false }
    if ($IsRoot -and -not $Acl.AreAccessRulesProtected) { return $false }
    $taskRules = @($Acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    $taskSids = @($taskRules | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
    if ($taskRules.Count -ne $Allowed.Count -or @(Compare-Object ($Allowed | Sort-Object) $taskSids).Count) { return $false }
    foreach ($taskRule in $taskRules) {
        $taskExpected = if ($taskRule.IdentityReference.Value -in @('S-1-5-18','S-1-5-32-544')) {
            [Security.AccessControl.FileSystemRights]::FullControl
        } else { [Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [Security.AccessControl.FileSystemRights]::Synchronize }
        $taskInheritance = if ($IsDirectory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
        if ($taskRule.AccessControlType -ne 'Allow' -or $taskRule.FileSystemRights -ne $taskExpected -or
            $taskRule.InheritanceFlags -ne $taskInheritance -or $taskRule.PropagationFlags -ne 'None') { return $false }
    }
    return $true
}

function Get-AclEvidence {
    $taskAllowed = @('S-1-5-18','S-1-5-32-544')
    foreach ($taskRole in $TaskRoles) {
        $taskAllowed += ([Security.Principal.NTAccount]::new('NT SERVICE',($TaskPrefix + $taskRole))).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $taskPending = [Collections.Generic.Queue[string]]::new()
    $taskPending.Enqueue($TaskData)
    $taskCount = 0; $taskUnexpected = 0; $taskOwnerMismatch = 0; $taskLocalServiceOwners = 0
    $taskConfigurationRoot = Join-Path $TaskData 'configuration'
    $taskConfigCount = 0; $taskConfigInvalid = 0; $taskConfigProtected = $false
    while ($taskPending.Count) {
        $taskEntry = $taskPending.Dequeue()
        $taskItem = Get-Item -LiteralPath $taskEntry -Force
        if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Data ACL inspection encountered a reparse point.' }
        $taskAcl = Get-Acl -LiteralPath $taskEntry
        $taskCount++
        $taskOwner = $taskAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($taskOwner -eq 'S-1-5-19') { $taskLocalServiceOwners++ }
        elseif ($taskOwner -notin $taskAllowed) { $taskOwnerMismatch++ }
        foreach ($taskRule in $taskAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($taskRule.IdentityReference.Value -notin $taskAllowed -or $taskRule.AccessControlType -ne 'Allow') { $taskUnexpected++ }
        }
        $taskIsConfigurationRoot = $taskEntry -ieq $taskConfigurationRoot
        if ($taskIsConfigurationRoot -or $taskEntry.StartsWith($taskConfigurationRoot + '\',[StringComparison]::OrdinalIgnoreCase)) {
            $taskConfigCount++
            if ($taskIsConfigurationRoot) { $taskConfigProtected = $taskAcl.AreAccessRulesProtected }
            if (-not (Test-ConfigurationAcl $taskAcl $taskAllowed ([bool]$taskItem.PSIsContainer) $taskIsConfigurationRoot)) { $taskConfigInvalid++ }
        }
        if ($taskItem.PSIsContainer) {
            foreach ($taskChild in @(Get-ChildItem -LiteralPath $taskEntry -Force)) { $taskPending.Enqueue($taskChild.FullName) }
        }
    }
    $taskRootAcl = Get-Acl -LiteralPath $TaskData
    $taskRootRules = @($taskRootAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    $taskRootSids = @($taskRootRules | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
    $taskExact = (@(Compare-Object ($taskAllowed | Sort-Object) $taskRootSids).Count -eq 0)
    $taskRightsValid = $true
    foreach ($taskRule in $taskRootRules) {
        $taskExpectedRights = if ($taskRule.IdentityReference.Value -in @('S-1-5-18','S-1-5-32-544')) {
            [Security.AccessControl.FileSystemRights]::FullControl
        } else { [Security.AccessControl.FileSystemRights]::Modify -bor [Security.AccessControl.FileSystemRights]::Synchronize }
        if ($taskRule.FileSystemRights -ne $taskExpectedRights -or
            $taskRule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit') { $taskRightsValid = $false }
    }
    $taskConfigOwnersValid = $true
    foreach ($taskConfiguration in @('.bluereel-native-instance','configuration\.env','configuration\installation.json')) {
        $taskConfigurationAcl = Get-Acl -LiteralPath (Join-Path $TaskData $taskConfiguration)
        if ($taskConfigurationAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')) { $taskConfigOwnersValid = $false }
    }
    return [ordered]@{
        inspected_entries=$taskCount; unexpected_aces=$taskUnexpected; unexpected_owners=$taskOwnerMismatch
        expected_localservice_runtime_owners=$taskLocalServiceOwners; private_configuration_owner_valid=$taskConfigOwnersValid
        configuration_entries=$taskConfigCount; configuration_invalid_acl_entries=$taskConfigInvalid
        configuration_protected=$taskConfigProtected; configuration_service_read_only=($taskConfigCount -gt 0 -and $taskConfigInvalid -eq 0 -and $taskConfigProtected)
        protected_root=$taskRootAcl.AreAccessRulesProtected; root_owner_administrators=($taskRootAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-5-32-544')
        exact_root_sid_allowlist=$taskExact; exact_root_rights=$taskRightsValid
        passed=($taskUnexpected -eq 0 -and $taskOwnerMismatch -eq 0 -and $taskRootAcl.AreAccessRulesProtected -and
            $taskRootAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-5-32-544' -and
            $taskExact -and $taskRightsValid -and $taskConfigOwnersValid -and
            $taskConfigCount -gt 0 -and $taskConfigInvalid -eq 0 -and $taskConfigProtected)
    }
}

function Get-LogEvidence {
    $taskLogs = Join-Path $TaskData 'logs'
    Assert-NoReparse $taskLogs
    $taskResult = @()
    foreach ($taskLog in @(Get-ChildItem -LiteralPath $taskLogs -File -Force)) {
        if ($taskLog.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'A private log is a reparse point.' }
        $taskLines = @(Get-Content -LiteralPath $taskLog.FullName -Tail 120 -ErrorAction Stop)
        $taskRole = 'native'
        foreach ($taskCandidate in $TaskRoles) { if ($taskLog.Name -match [regex]::Escape($TaskPrefix + $taskCandidate)) { $taskRole = $taskCandidate } }
        $taskResult += [ordered]@{
            role=$taskRole; bytes=$taskLog.Length; recent_lines=$taskLines.Count
            failure_markers=@($taskLines | Where-Object { $_ -match '(?i)traceback|fatal|failed|error' }).Count
            permission_markers=@($taskLines | Where-Object { $_ -match '(?i)permission|access.denied' }).Count
            warning_markers=@($taskLines | Where-Object { $_ -match '(?i)warn' }).Count
            raw_content_withheld=$true
        }
    }
    return $taskResult
}

function Get-InstanceProcesses {
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($TaskProgram + '\',[StringComparison]::OrdinalIgnoreCase)
    } | ForEach-Object { [ordered]@{pid=[int]$_.ProcessId; parent_pid=[int]$_.ParentProcessId; image=[IO.Path]::GetFileName($_.ExecutablePath)} })
}

function Stop-TestInstance {
    $taskRemote = Get-TestRemoteService
    if ($taskRemote -and $taskRemote.State -ne 'Stopped') {
        Stop-Service -Name $taskRemote.Name -NoWait
        (Get-Service -Name $taskRemote.Name).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(120))
    }
    foreach ($taskRole in @('Proxy','Worker','API','Web')) {
        $taskService = Get-Service -Name ($TaskPrefix + $taskRole) -ErrorAction SilentlyContinue
        if ($taskService -and $taskService.Status -ne 'Stopped') {
            Stop-Service -Name ($TaskPrefix + $taskRole) -NoWait
            (Get-Service -Name ($TaskPrefix + $taskRole)).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(120))
        }
    }
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        $taskChildren = @(Get-InstanceProcesses)
        if ($taskChildren.Count -eq 0) { return }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    throw 'An instance process survived orderly service shutdown; no force-kill fallback was used.'
}

function Get-PreservedDataDigest {
    $taskRows = [Collections.Generic.List[string]]::new()
    foreach ($taskName in @('.bluereel-native-instance','configuration','database','data','artwork','backups','upgrade')) {
        $taskPath = Join-Path $TaskData $taskName
        Assert-NoReparse $taskPath
        if (-not (Test-Path -LiteralPath $taskPath)) { continue }
        $taskPending = [Collections.Generic.Queue[string]]::new()
        $taskPending.Enqueue($taskPath)
        $taskFiles = [Collections.Generic.List[object]]::new()
        while ($taskPending.Count) {
            $taskEntry = Get-Item -LiteralPath $taskPending.Dequeue() -Force
            if ($taskEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Persistent data contains a reparse point.' }
            if ($taskEntry.PSIsContainer) {
                foreach ($taskChild in @(Get-ChildItem -LiteralPath $taskEntry.FullName -Force)) { $taskPending.Enqueue($taskChild.FullName) }
            } else { $taskFiles.Add($taskEntry) }
        }
        foreach ($taskFile in $taskFiles) {
            Assert-NoReparse $taskFile.FullName
            $taskRelative = $taskFile.FullName.Substring($TaskData.Length + 1)
            $taskRows.Add($taskRelative + ':' + (Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash)
        }
    }
    $taskHash = [Security.Cryptography.SHA256]::Create()
    try { $taskDigest = [BitConverter]::ToString($taskHash.ComputeHash([Text.Encoding]::UTF8.GetBytes((($taskRows | Sort-Object) -join "`n")))).Replace('-','').ToLowerInvariant() }
    finally { $taskHash.Dispose() }
    return [ordered]@{file_count=$taskRows.Count; sha256=$taskDigest; excludes_volatile_logs_state_temp=$true}
}

function Read-PriorUninstallEvidence([string]$Path) {
    if (-not $Path -or -not [IO.Path]::IsPathRooted($Path) -or $Path -match '(^|[\\/])\.\.([\\/]|$)') { throw 'An absolute prior uninstall report is required.' }
    $taskPath = [IO.Path]::GetFullPath($Path)
    if ([IO.Path]::GetDirectoryName($taskPath) -ine $ReportDirectory -or
        [IO.Path]::GetFileName($taskPath) -cnotmatch '^UninstallPreserve-[0-9]{8}T[0-9]{6}-[a-f0-9]{32}\.json$') { throw 'Prior report must be an exact preserve-uninstall report in this evidence directory.' }
    Assert-NoReparse $taskPath
    $taskItem = Get-Item -LiteralPath $taskPath -Force
    if ($taskItem.PSIsContainer -or $taskItem.Length -gt 1048576) { throw 'Invalid prior report file.' }
    $taskPrior = [IO.File]::ReadAllText($taskPath) | ConvertFrom-Json
    if ($taskPrior.schema_version -ne 1 -or $taskPrior.instance -cne 'development' -or $taskPrior.phase -cne 'UninstallPreserve' -or
        $taskPrior.uninstaller_exit_code -ne 0 -or $taskPrior.preserved_before.file_count -lt 1 -or
        $taskPrior.preserved_before.sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        $taskPrior.preserved_before.excludes_volatile_logs_state_temp -isnot [bool] -or
        -not $taskPrior.preserved_before.excludes_volatile_logs_state_temp) { throw 'Prior report does not prove a completed development preserve-uninstall with a valid before digest.' }
    return $taskPrior
}

function Remove-PrivateUninstallLog([string]$Directory) {
    $taskAttempts = 0
    try {
        $taskPrivate = [IO.Path]::GetFullPath($Directory).TrimEnd('\')
        if ([IO.Path]::GetDirectoryName($taskPrivate) -ine $ReportDirectory -or
            [IO.Path]::GetFileName($taskPrivate) -cnotmatch '^private-[a-f0-9]{32}$') { throw 'Unexpected private log target.' }
        Assert-NoReparse $taskPrivate
        $taskLog = Join-Path $taskPrivate 'uninstall.log'
        $taskDeadline = [DateTime]::UtcNow.AddSeconds(15)
        do {
            $taskAttempts++
            if (-not (Test-Path -LiteralPath $taskPrivate)) { return [ordered]@{removed=$true;attempts=$taskAttempts} }
            $taskEntries = @(Get-ChildItem -LiteralPath $taskPrivate -Force)
            if (@($taskEntries | Where-Object { $_.Name -cne 'uninstall.log' -or $_.PSIsContainer -or ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }).Count) {
                return [ordered]@{removed=$false;attempts=$taskAttempts;unexpected_entries=$true}
            }
            try {
                # Inno's temporary uninstaller can retain the log briefly after
                # its launcher returns. Delete only this exact log, then an
                # empty directory; never recurse or delete unknown children.
                if (Test-Path -LiteralPath $taskLog) { [IO.File]::Delete($taskLog) }
                [IO.Directory]::Delete($taskPrivate,$false)
                return [ordered]@{removed=$true;attempts=$taskAttempts}
            } catch {
                $taskCause = $_.Exception
                while ($null -ne $taskCause.InnerException) { $taskCause = $taskCause.InnerException }
                $taskNativeCode = $taskCause.HResult -band 65535
                if ($taskNativeCode -notin @(32,33,145) -or [DateTime]::UtcNow -ge $taskDeadline) {
                    return [ordered]@{removed=$false;attempts=$taskAttempts;error_type=$taskCause.GetType().Name;native_code=$taskNativeCode}
                }
                Start-Sleep -Milliseconds 200
            }
        } while ($true)
    } catch { return [ordered]@{removed=$false;attempts=$taskAttempts;error_type=$_.Exception.GetType().Name} }
}

function Set-UninstalledStateEvidence {
    $TaskReport.remaining_services = @(@($TaskRoles + 'Remote') | ForEach-Object { Get-Service -Name ($TaskPrefix + $_) -ErrorAction SilentlyContinue }).Count
    $TaskReport.remaining_firewall_rules = @(Get-NetFirewallRule -PolicyStore ActiveStore -Group $TaskPrefix,($TaskPrefix + 'Remote') -ErrorAction SilentlyContinue).Count
    $TaskReport.remaining_processes = @(Get-InstanceProcesses).Count
    $TaskReport.program_directory_exists = Test-Path -LiteralPath $TaskProgram
    $TaskReport.data_directory_exists = Test-Path -LiteralPath $TaskData
    if ($TaskReport.remaining_services -or $TaskReport.remaining_firewall_rules -or $TaskReport.remaining_processes) { throw 'Disposable service, process, or firewall cleanup is incomplete.' }
}

function Invoke-NodeChecks {
    $taskSource = @'
const assert = require('node:assert/strict');
const http = require('node:http');
const dns = require('node:dns/promises');
(async () => {
  await assert.rejects(() => dns.lookup('bluereel-acceptance.invalid'), {code:'ERR_BLUEREEL_LOCAL_ONLY'});
  const server = http.createServer((_request, response) => response.end('local'));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  for (const host of ['127.0.0.1', 'localhost']) {
    await new Promise((resolve, reject) => http.get({host, port:server.address().port, agent:false}, response => {
      response.resume(); response.on('end', resolve);
    }).on('error', reject));
  }
  await new Promise(resolve => server.close(resolve));
  console.log(JSON.stringify({loopback:true, localhost:true, hostname_blocked_before_dns:true}));
})().catch(() => process.exit(1));
'@
    $taskEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($taskSource))
    $taskResult = Invoke-PrivateProcess $TaskNode @('--require',(Join-Path $TaskProgram 'support\native-guard.cjs'),'-e',"eval(Buffer.from('$taskEncoded','base64').toString())") 15
    if ($taskResult.exit_code -ne 0) { throw 'Bundled Node local-only acceptance failed.' }
    return ($taskResult.stdout | ConvertFrom-Json)
}

try {
    if (-not $AllowDisposableInstance) { throw 'Explicit -AllowDisposableInstance is required.' }
    if (-not [Environment]::Is64BitProcess) { throw 'Use x64 Windows PowerShell.' }
    $taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run acceptance with explicit Windows administrator elevation.' }
    if ($Phase -eq 'UninstallPurge' -and $ConfirmPurge -cne 'BlueAshReel-Development') { throw 'Purge additionally requires -ConfirmPurge BlueAshReel-Development.' }
    $taskRepository = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $taskIgnoredRoots = @('native-dev','development') | ForEach-Object { [IO.Path]::GetFullPath((Join-Path $taskRepository ('artifacts\' + $_))).TrimEnd('\') }
    if (-not [IO.Path]::IsPathRooted($ReportDirectory) -or $ReportDirectory -match '(^|[\\/])\.\.([\\/]|$)') { throw 'Report directory must be an absolute ignored artifact location.' }
    $ReportDirectory = [IO.Path]::GetFullPath($ReportDirectory).TrimEnd('\')
    if (-not @($taskIgnoredRoots | Where-Object { $ReportDirectory.StartsWith($_ + '\',[StringComparison]::OrdinalIgnoreCase) }).Count) { throw 'Use a report subdirectory beneath an ignored native artifact directory.' }
    Assert-NoReparse $ReportDirectory
    if (-not (Test-Path -LiteralPath $ReportDirectory)) { New-Item -ItemType Directory -Path $ReportDirectory | Out-Null }
    $TaskReportPath = Join-Path $ReportDirectory ($Phase + '-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N') + '.json')
    Assert-Instance
    switch ($Phase) {
        'Inventory' {
            $TaskReport.services = @(Get-ServiceEvidence)
            $TaskReport.acl = Get-AclEvidence
            $TaskReport.native = Invoke-Probe 'inventory'
            if (-not $TaskReport.acl.passed -or @($TaskReport.services | Where-Object { -not $_.local_service -or -not $_.automatic -or -not $_.delayed_auto_start -or -not $_.service_sid_enabled }).Count) { throw 'Service or ACL acceptance mismatch.' }
            foreach ($taskRole in $TaskRoles) { if (-not $TaskReport.native.recovery.$taskRole.bounded_two_restarts -or -not $TaskReport.native.recovery.$taskRole.no_reboot_or_command) { throw 'Service recovery policy is not bounded.' } }
        }
        'Snapshot' {
            $TaskReport.snapshot = Invoke-Probe 'snapshot'
        }
        'VerifyUninstallPreserve' {
            $taskPrior = Read-PriorUninstallEvidence $PriorReport
            $TaskReport.prior_report = [IO.Path]::GetFileName($PriorReport)
            $TaskReport.uninstaller_exit_code = $taskPrior.uninstaller_exit_code
            $TaskReport.preserved_before = $taskPrior.preserved_before
            $TaskReport.read_only_followup = $true
            Set-UninstalledStateEvidence
            $TaskReport.preserved_after = Get-PreservedDataDigest
            if (-not $TaskReport.data_directory_exists -or $TaskReport.preserved_before.sha256 -cne $TaskReport.preserved_after.sha256 -or
                $TaskReport.preserved_before.file_count -ne $TaskReport.preserved_after.file_count) { throw 'Persistent data no longer matches the pre-uninstall digest.' }
            $TaskReport.application_uninstall_verified = $true
            $TaskReport.prior_transient_log_cleanup_not_retried = $true
        }
        'Diagnostics' {
            $TaskReport.services = @(Get-ServiceEvidence)
            $TaskReport.logs = @(Get-LogEvidence)
            $TaskReport.processes = @(Get-InstanceProcesses)
            $TaskReport.health = Invoke-Probe 'health' 75
            if (-not $TaskReport.health.healthy) { throw 'Disposable instance is not healthy.' }
        }
        'SignalStop' {
            # Explicit cooperative recovery for the first prototype's malformed
            # WinSW stop arguments. Does not kill processes or claim rollback.
            $TaskReport.manual_cooperative_recovery = $true
            foreach ($taskRole in @('Proxy','Worker','API','Web')) {
                $taskService = Get-Service -Name ($TaskPrefix + $taskRole)
                if ($taskService.Status -eq 'Stopped') { continue }
                if ($taskService.Status -ne 'StopPending') { Stop-Service -Name ($TaskPrefix + $taskRole) -NoWait }
                $taskResult = Invoke-PrivateProcess $TaskPython @('-I','-B','-m','app.native_runtime','--data-dir',$TaskData,'--role',$taskRole.ToLowerInvariant(),'--stop') 15
                if ($taskResult.exit_code -ne 0) { throw 'Cooperative stop marker failed.' }
                (Get-Service -Name ($TaskPrefix + $taskRole)).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(120))
            }
            $TaskReport.stop_markers_requested = $true
        }
        'StopState' {
            $TaskReport.services = @(Get-ServiceEvidence)
            $TaskReport.processes = @(Get-InstanceProcesses)
            $TaskReport.logs = @(Get-LogEvidence)
            $TaskReport.stop_markers = @($TaskRoles | ForEach-Object {
                $taskMarker = Join-Path $TaskData ('state\stop-' + $_.ToLowerInvariant())
                [ordered]@{role=$_; present=(Test-Path -LiteralPath $taskMarker)}
            })
            # Only fixed diagnostic keywords, never command lines or raw paths.
            $TaskReport.wrapper_events = @(Get-ChildItem -LiteralPath (Join-Path $TaskData 'logs') -Filter '*.wrapper.log' | ForEach-Object {
                $taskRole = 'unknown'
                foreach ($taskCandidate in $TaskRoles) { if ($_.Name.Contains($taskCandidate)) { $taskRole = $taskCandidate } }
                [ordered]@{role=$taskRole; keywords=@(Get-Content -LiteralPath $_.FullName -Tail 12 | ForEach-Object {
                    [ordered]@{
                        events=(@([regex]::Matches($_,'(?i)started|starting|stopping|stopped|finished|killed|kill|timeout|failed|failure|exit code:?-? ?[0-9]+') | ForEach-Object { $_.Value }) -join ',')
                        native_runtime=$_.Contains('app.native_runtime')
                        stop_switch=$_.Contains('--stop')
                        quoted_data_argument=$_.Contains('--data-dir "')
                    }
                })}
            })
        }
        'VerifyFirewall' {
            $TaskReport.firewall = Invoke-Probe 'firewall' 35
            $TaskReport.node = Invoke-NodeChecks
            if (-not $TaskReport.firewall.passed) { throw 'OS firewall or Python DNS enforcement was not proven.' }
        }
        'Restart' {
            $TaskReport.before = Invoke-Probe 'snapshot'
            $TaskReport.processes_before = @(Get-InstanceProcesses)
            $taskRemoteBefore = Get-TestRemoteService
            Stop-TestInstance
            $TaskReport.child_cleanup = $true
            foreach ($taskRole in $TaskRoles) { Start-Service -Name ($TaskPrefix + $taskRole) }
            if ($taskRemoteBefore) { Start-Service -Name $taskRemoteBefore.Name }
            $TaskReport.health = Invoke-Probe 'health' 75
            $TaskReport.after = Invoke-Probe 'snapshot'
            $TaskReport.services_after = @(Get-ServiceEvidence)
            if (-not $TaskReport.health.healthy -or $TaskReport.before.secret_sha256 -cne $TaskReport.after.secret_sha256 -or $TaskReport.before.environment_sha256 -cne $TaskReport.after.environment_sha256) { throw 'Restart failed health or persistent configuration checks.' }
        }
        'Backup' { $TaskReport.backup = Invoke-Probe 'backup' 360 }
        { $_ -in @('UninstallPreserve','UninstallPurge') } {
            $taskUninstaller = Join-Path $TaskProgram 'unins000.exe'
            if (-not (Test-Path -LiteralPath $taskUninstaller -PathType Leaf)) { throw 'The actual development uninstaller is unavailable.' }
            Stop-TestInstance
            $TaskReport.before = Invoke-Probe 'snapshot'
            $TaskReport.preserved_before = Get-PreservedDataDigest
            # A fresh administrator-only directory holds the required raw Inno
            # log transiently. Never emit its contents; delete only that exact
            # helper-created log after recording its exit status.
            $taskPrivate = Join-Path $ReportDirectory ('private-' + [guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $taskPrivate | Out-Null
            $taskAcl = [Security.AccessControl.DirectorySecurity]::new()
            $taskAcl.SetAccessRuleProtection($true,$false)
            $taskAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
            foreach ($taskSid in @('S-1-5-32-544','S-1-5-18')) { $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($taskSid),'FullControl','ContainerInherit,ObjectInherit','None','Allow')) }
            Set-Acl -LiteralPath $taskPrivate -AclObject $taskAcl
            $taskRawLog = Join-Path $taskPrivate 'uninstall.log'
            $taskArguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG=' + $taskRawLog))
            if ($Phase -eq 'UninstallPurge') { $taskArguments += '/PURGEDATA=BlueAshReel-Development' }
            try {
                $taskResult = Invoke-PrivateProcess $taskUninstaller $taskArguments 600
                $TaskReport.uninstaller_exit_code = $taskResult.exit_code
                if ($taskResult.exit_code -ne 0) { throw 'The native uninstaller reported failure.' }
            } finally {
                $TaskReport.transient_log_cleanup = Remove-PrivateUninstallLog $taskPrivate
                $TaskReport.transient_raw_uninstall_log_removed = $TaskReport.transient_log_cleanup.removed
            }
            Set-UninstalledStateEvidence
            if ($Phase -eq 'UninstallPreserve') {
                $TaskReport.preserved_after = Get-PreservedDataDigest
                if (-not $TaskReport.data_directory_exists -or $TaskReport.preserved_before.sha256 -cne $TaskReport.preserved_after.sha256) { throw 'Default uninstall did not preserve persistent data exactly.' }
            } elseif ($TaskReport.data_directory_exists) { throw 'Explicit purge did not remove the exact disposable data directory.' }
            $TaskReport.application_uninstall_verified = $true
            if (-not $TaskReport.transient_raw_uninstall_log_removed) { throw 'Application uninstall verified, but helper transient-log cleanup is incomplete.' }
        }
    }
    $TaskReport.passed = $true
} catch {
    # No exception message, stack, process stderr, file path, or environment
    # value enters evidence. Known phase + error type identify the failed step.
    $TaskReport.error_type = $_.Exception.GetType().Name
    if ($_.FullyQualifiedErrorId -match '^[A-Za-z0-9_.,-]{1,256}$') { $TaskReport.error_id = $_.FullyQualifiedErrorId }
    $taskErrorCause = $_.Exception.InnerException
    $TaskReport.native_error_codes = @()
    while ($null -ne $taskErrorCause) {
        if ($taskErrorCause -is [ComponentModel.Win32Exception]) { $TaskReport.native_error_codes += $taskErrorCause.NativeErrorCode }
        $taskErrorCause = $taskErrorCause.InnerException
    }
    $TaskReport.failed_operation = $Phase
} finally {
    $TaskReport.finished_utc = [DateTime]::UtcNow.ToString('o')
    if ($TaskReportPath) { [IO.File]::WriteAllText($TaskReportPath, ($TaskReport | ConvertTo-Json -Depth 14), [Text.UTF8Encoding]::new($false)) }
    [ordered]@{phase=$Phase; passed=$TaskReport.passed; report_written=($null -ne $TaskReportPath)} | ConvertTo-Json -Compress
}
if (-not $TaskReport.passed) { exit 1 }
