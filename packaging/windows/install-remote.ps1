[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Install','RefreshEndpoint','Stop','Remove')][string]$Action,
    [Parameter(Mandatory)][string]$ProgramDir,
    [Parameter(Mandatory)][string]$DataDir
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Path([string]$Path) {
    $taskFull = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not [IO.Path]::IsPathRooted($Path) -or $taskFull.Length -lt 5 -or $taskFull.StartsWith('\\')) {
        throw 'A dedicated absolute local installation path is required.'
    }
    for ($taskProbe = $taskFull; $taskProbe; $taskProbe = [IO.Path]::GetDirectoryName($taskProbe)) {
        if ((Test-Path -LiteralPath $taskProbe) -and
            ((Get-Item -LiteralPath $taskProbe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Remote installation paths cannot contain junctions or reparse points.'
        }
    }
    return $taskFull
}

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'A native connector provisioning step failed.' }
}

function Set-ScopedAcl([string]$Path, [hashtable]$Rules, [switch]$LowIntegrity) {
    $taskAcl = [Security.AccessControl.DirectorySecurity]::new()
    $taskAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    $taskAcl.SetAccessRuleProtection($true,$false)
    $taskInheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    $taskAllowed = @{'S-1-5-18'='FullControl';'S-1-5-32-544'='FullControl'}
    foreach ($taskKey in $Rules.Keys) { $taskAllowed[$taskKey] = $Rules[$taskKey] }
    foreach ($taskKey in $taskAllowed.Keys) {
        $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($taskKey),
            [Security.AccessControl.FileSystemRights]$taskAllowed[$taskKey],$taskInheritance,
            [Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))
    }
    Set-Acl -LiteralPath $Path -AclObject $taskAcl
    if ($LowIntegrity) { Invoke-Checked 'icacls.exe' @($Path,'/setintegritylevel','(OI)(CI)L') }
}

function Read-Pins {
    $taskPins = @([Net.Dns]::GetHostAddresses('blueashreel.com') |
        Where-Object AddressFamily -eq ([Net.Sockets.AddressFamily]::InterNetwork) |
        ForEach-Object IPAddressToString | Sort-Object -Unique)
    if ($taskPins.Count -lt 1 -or $taskPins.Count -gt 16) { throw 'Canonical portal IPv4 destinations could not be verified.' }
    return $taskPins
}

function Assert-ConnectorFirewall {
    foreach ($taskServiceName in @('BFE','MpsSvc')) {
        if ((Get-Service -Name $taskServiceName).Status -ne 'Running') { throw 'Windows firewall enforcement is unavailable; connector remains stopped.' }
    }
    $taskProfiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
    if ($taskProfiles.Count -ne 3) { throw 'Cannot verify all effective firewall profiles.' }
    foreach ($taskProfileState in $taskProfiles) {
        if ([string]$taskProfileState.Enabled -ne 'True' -or [string]$taskProfileState.AllowLocalFirewallRules -eq 'False') {
            throw 'Effective Windows policy does not enforce local firewall rules; connector remains stopped.'
        }
    }
    foreach ($taskSuffix in @('OtherDestinations','OtherTCP','UDP','ICMP','TLS','NoInbound')) {
        $taskEffective = @(Get-NetFirewallRule -PolicyStore ActiveStore -Name "$TaskName-$taskSuffix" -ErrorAction Stop)
        $taskExpectedAction = if ($taskSuffix -eq 'TLS') { 'Allow' } else { 'Block' }
        if ($taskEffective.Count -ne 1 -or [string]$taskEffective[0].Enabled -ne 'True' -or
            [string]$taskEffective[0].Action -ne $taskExpectedAction -or $taskEffective[0].Group -cne $TaskName) {
            throw 'Connector firewall rules are absent or overridden in effective Windows policy.'
        }
        $taskApplication = $taskEffective[0] | Get-NetFirewallApplicationFilter
        if ($taskApplication.Program -ine $TaskRemotePython) { throw 'Effective connector firewall executable mismatch.' }
        if ($taskExpectedAction -eq 'Block' -and 'Enforced' -notin @($taskEffective[0].EnforcementStatus)) {
            throw 'Windows is not enforcing a connector firewall block; connector remains stopped.'
        }
    }
}

function Assert-ConnectorState {
    # lstat checks hardlinks as well as reparse points before any inherited ACL
    # or state-file mutation, including endpoint refreshes and reinstalls.
    $taskCheckTree = @'
import os,pathlib,stat,sys
root=pathlib.Path(sys.argv[1])
def reject_unreadable(error):
    raise error
for parent,dirs,files in os.walk(root,followlinks=False,onerror=reject_unreadable):
    for name in dirs+files:
        info=(pathlib.Path(parent)/name).lstat()
        if getattr(info,'st_file_attributes',0)&1024 or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1):
            raise SystemExit('Connector state contains a filesystem link; it was preserved.')
'@
    Invoke-Checked $TaskPython @('-I','-B','-c',$taskCheckTree,$TaskRemoteData)
}

function Assert-ConnectorReady([double]$StartedAt) {
    $taskReadyDeadline = (Get-Date).AddSeconds(15)
    $taskStatusPath = Join-Path $TaskRemoteData 'control\status.json'
    do {
        if ((Get-Service -Name $TaskName).Status -ne 'Running') {
            throw 'The isolated connector exited during startup; remote access remains unavailable.'
        }
        if (Test-Path -LiteralPath $taskStatusPath) {
            $taskStatus = Get-Content -LiteralPath $taskStatusPath -Raw | ConvertFrom-Json
            if ([double]$taskStatus.updated_at -ge $StartedAt) { return }
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $taskReadyDeadline)
    Stop-Service -Name $TaskName
    throw 'The isolated connector did not publish a fresh status after its privacy preflight; it was stopped.'
}

function Set-ConnectorFirewall([string[]]$Pins) {
    # Windows Block overrides Allow. Therefore block the exact complement of
    # pinned destination addresses, and separately block all non-443 TCP/UDP.
    # Existing media Python rules and global firewall defaults stay untouched.
    $taskNumbers = @($Pins | ForEach-Object {
        $taskBytes = [Net.IPAddress]::Parse($_).GetAddressBytes()
        [Array]::Reverse($taskBytes)
        [BitConverter]::ToUInt32($taskBytes,0)
    } | Sort-Object -Unique)
    function IPv4([uint64]$Value) {
        $taskBytes = [BitConverter]::GetBytes([uint32]$Value)
        [Array]::Reverse($taskBytes)
        return [Net.IPAddress]::new($taskBytes).IPAddressToString
    }
    $taskComplement = [Collections.Generic.List[string]]::new()
    [uint64]$taskStart = 0
    foreach ($taskNumber in $taskNumbers) {
        if ($taskNumber -gt $taskStart) { $taskComplement.Add("$(IPv4 $taskStart)-$(IPv4 ($taskNumber-1))") }
        $taskStart = [uint64]$taskNumber + 1
    }
    if ($taskStart -le 4294967295) { $taskComplement.Add("$(IPv4 $taskStart)-255.255.255.255") }
    # Windows Firewall rejects the IPv6 /0 form; these two accepted /1 prefixes
    # cover precisely the same complete IPv6 address space.
    $taskComplement.Add('::/1')
    $taskComplement.Add('8000::/1')
    foreach ($taskRule in @(Get-NetFirewallRule -Group $TaskName -ErrorAction SilentlyContinue)) {
        if (-not $taskRule.Name.StartsWith($TaskName + '-')) { throw 'Connector firewall identity conflict.' }
        $taskRule | Remove-NetFirewallRule
    }
    $taskCommon = @{Program=$TaskRemotePython;Group=$TaskName;Profile='Any';Enabled='True'}
    New-NetFirewallRule @taskCommon -Name "$TaskName-OtherDestinations" -DisplayName "$TaskDisplayName destination restriction" `
        -Direction Outbound -Action Block -Protocol Any -RemoteAddress $taskComplement.ToArray() | Out-Null
    New-NetFirewallRule @taskCommon -Name "$TaskName-OtherTCP" -DisplayName "$TaskDisplayName port restriction" `
        -Direction Outbound -Action Block -Protocol TCP -RemotePort @('0-442','444-65535') | Out-Null
    New-NetFirewallRule @taskCommon -Name "$TaskName-UDP" -DisplayName "$TaskDisplayName no UDP" `
        -Direction Outbound -Action Block -Protocol UDP | Out-Null
    New-NetFirewallRule @taskCommon -Name "$TaskName-ICMP" -DisplayName "$TaskDisplayName no ICMP" `
        -Direction Outbound -Action Block -Protocol ICMPv4 | Out-Null
    New-NetFirewallRule @taskCommon -Name "$TaskName-TLS" -DisplayName "$TaskDisplayName canonical TLS" `
        -Direction Outbound -Action Allow -Protocol TCP -RemotePort 443 -RemoteAddress $Pins | Out-Null
    New-NetFirewallRule @taskCommon -Name "$TaskName-NoInbound" -DisplayName "$TaskDisplayName no inbound" `
        -Direction Inbound -Action Block -Protocol Any | Out-Null
    Assert-ConnectorFirewall
}

$taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($taskIdentity)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator elevation is required for connector isolation.' }
$ProgramDir = Assert-Path $ProgramDir
$DataDir = Assert-Path $DataDir
$TaskMetadata = Get-Content -LiteralPath (Join-Path $DataDir 'configuration\installation.json') -Raw | ConvertFrom-Json
if ($TaskMetadata.program_dir -ine $ProgramDir -or $TaskMetadata.data_dir -ine $DataDir -or
    $TaskMetadata.service_prefix -notin @('BlueReel','BlueReelDevelopment')) { throw 'Native installation identity mismatch.' }
$TaskName = $TaskMetadata.service_prefix + 'Remote'
$TaskProduct = Get-Content -LiteralPath (Join-Path $ProgramDir 'config\product.json') -Raw | ConvertFrom-Json
$TaskDisplayName = [string]$TaskProduct.agent_name + ' Remote Diagnostics'
if ($TaskMetadata.service_prefix -eq 'BlueReelDevelopment') { $TaskDisplayName += ' (Development)' }
$TaskProfile = $TaskName.ToLowerInvariant() + '.diagnostics'
$TaskRemoteData = Assert-Path ($DataDir + '-Remote')
$TaskRemoteRuntime = Assert-Path (Join-Path $ProgramDir 'runtime\remote-python')
$TaskRemotePython = Join-Path $TaskRemoteRuntime 'python.exe'
$TaskPython = Join-Path $ProgramDir 'runtime\python\python.exe'
$TaskWrapper = Join-Path $ProgramDir "services\$TaskName.exe"
$TaskXml = Join-Path $ProgramDir "services\$TaskName.xml"
$TaskPolicy = Join-Path $TaskRemoteData 'configuration\policy.json'
$TaskService = Get-CimInstance Win32_Service -Filter "Name='$TaskName'" -ErrorAction SilentlyContinue
if ($TaskService -and ($TaskService.PathName.Trim('"') -ine $TaskWrapper -or
    $TaskService.StartName -ine "NT SERVICE\$TaskName")) { throw 'Existing connector service identity conflicts.' }
if ($TaskService -and $TaskService.State -ne 'Stopped') {
    Stop-Service -Name $TaskName
    (Get-Service -Name $TaskName).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30))
}
if ($Action -eq 'Stop') { return }
if ($Action -eq 'Remove') {
    if (Test-Path -LiteralPath $TaskRemoteRuntime) {
        $taskMarker = Join-Path $TaskRemoteData '.connector-instance'
        if (-not (Test-Path -LiteralPath $taskMarker) -or
            [IO.File]::ReadAllText($taskMarker).Trim() -cne $TaskName) { throw 'Connector marker missing; files preserved.' }
    }
    if ($TaskService) { Invoke-Checked $TaskWrapper @('uninstall') }
    Get-NetFirewallRule -Group $TaskName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if (Test-Path -LiteralPath $TaskRemoteRuntime) {
        $taskRetired = Assert-Path (Join-Path $TaskRemoteData ('retired-runtime-' + (Get-Date -Format 'yyyyMMddHHmmssfff')))
        if ([IO.Path]::GetDirectoryName($TaskRemoteRuntime) -ine (Join-Path $ProgramDir 'runtime') -or
            [IO.Path]::GetDirectoryName($taskRetired) -ine $TaskRemoteData) { throw 'Runtime retirement escaped its scope.' }
        Move-Item -LiteralPath $TaskRemoteRuntime -Destination $taskRetired
    }
    # Retain protected state and the old code for rollback. No media/database
    # targets are moved. Owner should Unpair first to finish central revocation.
    return
}
$TaskPins = @(Read-Pins)
if (Test-Path -LiteralPath $TaskRemoteData) { Assert-ConnectorState }
if ($Action -eq 'RefreshEndpoint') {
    if (-not $TaskService) { throw 'Install the isolated connector before refreshing its endpoint.' }
    $taskPolicyData = Get-Content -LiteralPath $TaskPolicy -Raw | ConvertFrom-Json
    $taskPolicyData.allowed_ips = $TaskPins
    Set-ConnectorFirewall $TaskPins
    [IO.File]::WriteAllText($TaskPolicy,($taskPolicyData | ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
    $taskStartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    Start-Service -Name $TaskName
    Assert-ConnectorReady $taskStartedAt
    return
}

if (Test-Path -LiteralPath $TaskRemoteData) {
    $taskMarker = Join-Path $TaskRemoteData '.connector-instance'
    if (-not (Test-Path -LiteralPath $taskMarker) -or
        [IO.File]::ReadAllText($taskMarker).Trim() -cne $TaskName) { throw 'Unrecognized connector data directory; preserved.' }
    $taskExistingAcl = Get-Acl -LiteralPath $TaskRemoteData
    $taskExistingOwner = $taskExistingAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($taskExistingOwner -ne 'S-1-5-32-544' -or -not $taskExistingAcl.AreAccessRulesProtected) {
        throw 'Existing connector state is not administrator-owned and protected; it was preserved.'
    }
    # No links or hardlinks may redirect provisioning ACLs outside this directory.
    foreach ($taskEntry in @(Get-ChildItem -LiteralPath $TaskRemoteData -Recurse -Force)) {
        if ($taskEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Connector state contains a link.' }
    }
} else {
    New-Item -ItemType Directory -Path $TaskRemoteData | Out-Null
    Set-ScopedAcl $TaskRemoteData @{}
}
if (Test-Path -LiteralPath $TaskRemoteRuntime) { throw 'Remote runtime already exists. Use a reviewed package upgrade or RefreshEndpoint; files were preserved.' }
foreach ($taskDirectory in @('control','identity','configuration','logs')) {
    New-Item -ItemType Directory -Path (Join-Path $TaskRemoteData $taskDirectory) -Force | Out-Null
}
New-Item -ItemType Directory -Path $TaskRemoteRuntime | Out-Null
[IO.File]::WriteAllText((Join-Path $TaskRemoteData '.connector-instance'),$TaskName,[Text.UTF8Encoding]::new($false))
# Copy only the pinned interpreter, remote modules, and their crypto/socket dependencies.
$taskSourceRuntime = Join-Path $ProgramDir 'runtime\python'
Get-ChildItem -LiteralPath $taskSourceRuntime -File | Where-Object {
    $_.Extension -in @('.exe','.dll','.pyd','.zip') -and $_.Name -ne 'pythonw.exe'
} | Copy-Item -Destination $TaskRemoteRuntime
$taskSite = Join-Path $TaskRemoteRuntime 'Lib\site-packages'
New-Item -ItemType Directory -Path $taskSite -Force | Out-Null
foreach ($taskPackage in @('cryptography','websockets','cffi','pycparser')) {
    $taskSource = Join-Path $taskSourceRuntime "Lib\site-packages\$taskPackage"
    if (-not (Test-Path -LiteralPath $taskSource)) { throw 'The packaged remote dependency lock is incomplete.' }
    Copy-Item -LiteralPath $taskSource -Destination $taskSite -Recurse
}
Get-ChildItem -LiteralPath (Join-Path $taskSourceRuntime 'Lib\site-packages') -Filter '_cffi_backend*.pyd' |
    Copy-Item -Destination $taskSite
$taskRemoteApp = Join-Path $TaskRemoteRuntime 'app'
New-Item -ItemType Directory -Path $taskRemoteApp | Out-Null
Copy-Item -LiteralPath (Join-Path $ProgramDir 'backend\app\remote') -Destination $taskRemoteApp -Recurse
$taskZip = @(Get-ChildItem -LiteralPath $TaskRemoteRuntime -Filter 'python3*.zip')
if ($taskZip.Count -ne 1) { throw 'Expected one pinned Python standard-library archive.' }
[IO.File]::WriteAllText((Join-Path $TaskRemoteRuntime ($taskZip[0].BaseName + '._pth')),
    "$($taskZip[0].Name)`n.`nLib\site-packages`n",[Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath (Join-Path $ProgramDir 'config\product.json') -Destination (Join-Path $TaskRemoteData 'configuration\product.json')
Copy-Item -LiteralPath (Join-Path $ProgramDir 'services\WinSW.exe') -Destination $TaskWrapper

$taskDocument = [xml]'<service />'
$taskFields = [ordered]@{
    id=$TaskName;name=$TaskDisplayName;
    description='Isolated outbound encrypted diagnostics; media is inaccessible inside AppContainer.';
    executable=$TaskPython;
    arguments="-I -B -m app.remote.native_launcher --name $TaskProfile --executable `"$TaskRemotePython`" --policy `"$TaskPolicy`"";
    workingdirectory=$TaskRemoteRuntime;startmode='Automatic';delayedAutoStart='true';stoptimeout='10 sec';
    stopparentprocessfirst='true';logpath=(Join-Path $TaskRemoteData 'logs')
}
foreach ($taskKey in $taskFields.Keys) {
    $taskNode = $taskDocument.CreateElement($taskKey); $taskNode.InnerText = $taskFields[$taskKey]
    $null = $taskDocument.DocumentElement.AppendChild($taskNode)
}
$taskAccount = $taskDocument.CreateElement('serviceaccount')
foreach ($taskValue in @(@('domain','NT SERVICE'),@('user',$TaskName))) {
    $taskNode = $taskDocument.CreateElement($taskValue[0]);$taskNode.InnerText=$taskValue[1]
    $null=$taskAccount.AppendChild($taskNode)
}
$null=$taskDocument.DocumentElement.AppendChild($taskAccount)
$taskLog=$taskDocument.CreateElement('log');$taskLog.SetAttribute('mode','none')
$null=$taskDocument.DocumentElement.AppendChild($taskLog)
$taskDocument.Save($TaskXml)
Invoke-Checked $TaskWrapper @('install')
Invoke-Checked 'sc.exe' @('sidtype',$TaskName,'unrestricted')
$TaskSid=([Security.Principal.NTAccount]::new('NT SERVICE',$TaskName)).Translate([Security.Principal.SecurityIdentifier]).Value
$TaskApiSid=([Security.Principal.NTAccount]::new('NT SERVICE',($TaskMetadata.service_prefix+'API'))).Translate([Security.Principal.SecurityIdentifier]).Value
$TaskAppSid = (& $TaskPython -I -B -m app.remote.native_launcher --name $TaskProfile --derive-sid).Trim()
if ($LASTEXITCODE -ne 0 -or $TaskAppSid -notmatch '^S-1-15-2-[0-9-]+$') { throw 'Cannot derive sandbox identity.' }
Set-ScopedAcl $TaskRemoteRuntime @{$TaskSid='ReadAndExecute';$TaskAppSid='ReadAndExecute'}
Set-ScopedAcl $TaskRemoteData @{$TaskSid='ReadAndExecute';$TaskAppSid='ReadAndExecute';$TaskApiSid='ReadAndExecute'}
Set-ScopedAcl (Join-Path $TaskRemoteData 'configuration') @{$TaskSid='ReadAndExecute';$TaskAppSid='ReadAndExecute'}
Set-ScopedAcl (Join-Path $TaskRemoteData 'control') @{$TaskSid='FullControl';$TaskAppSid='FullControl';$TaskApiSid='Modify'} -LowIntegrity
Set-ScopedAcl (Join-Path $TaskRemoteData 'identity') @{$TaskSid='FullControl';$TaskAppSid='FullControl'} -LowIntegrity
Set-ScopedAcl (Join-Path $TaskRemoteData 'logs') @{$TaskSid='Modify'}
$taskPolicyScript = @'
import json,pathlib,sys
from dotenv import dotenv_values
data,remote=map(pathlib.Path,sys.argv[1:3])
values=dotenv_values(data/'configuration'/'.env')
roots=json.loads(values.get('MEDIA_ROOT_DEFINITIONS') or '[]')
policy={'control_dir':str(remote/'control'),'identity_dir':str(remote/'identity'),'product_config':str(remote/'configuration'/'product.json'),'denied_paths':[str(data/'database'),str(data/'data'),str(data/'artwork'),str(data/'configuration'),*[root['path'] for root in roots]],'allowed_ips':json.loads(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8'))}
(remote/'configuration'/'policy.json').write_text(json.dumps(policy,indent=2),encoding='utf-8')
env=data/'configuration'/'.env'
lines=[line for line in env.read_text(encoding='utf-8').splitlines() if not line.startswith('REMOTE_CONTROL_DIR=')]
lines.append('REMOTE_CONTROL_DIR='+json.dumps(str(remote/'control')))
env.write_text('\n'.join(lines)+'\n',encoding='utf-8')
'@
# Save an ACL-protected exact local configuration backup before adding the spool path.
$taskEnvironment=Join-Path $DataDir 'configuration\.env'
Copy-Item -LiteralPath $taskEnvironment -Destination (Join-Path $DataDir ('configuration\.env.before-remote-'+(Get-Date -Format 'yyyyMMddHHmmss')))
$taskPinsFile = Join-Path $TaskRemoteData 'configuration\endpoint-pins.json'
[IO.File]::WriteAllText($taskPinsFile,(ConvertTo-Json -InputObject @($TaskPins) -Compress),[Text.UTF8Encoding]::new($false))
Invoke-Checked $TaskPython @('-I','-B','-c',$taskPolicyScript,$DataDir,$TaskRemoteData,$taskPinsFile)
Set-ConnectorFirewall $TaskPins
$taskStartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
Start-Service -Name $TaskName
# Reload configuration and restore only the dependent services that were running.
$taskRestart = @()
foreach ($taskRole in @('Proxy','Worker','API')) {
    $taskLocalService = Get-Service -Name ($TaskMetadata.service_prefix+$taskRole)
    if ($taskLocalService.Status -eq 'Running') {
        $taskRestart += $taskLocalService.Name
        Stop-Service -Name $taskLocalService.Name
        $taskLocalService.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(120))
    }
}
[Array]::Reverse($taskRestart)
foreach ($taskLocalName in $taskRestart) { Start-Service -Name $taskLocalName }
Assert-ConnectorReady $taskStartedAt
Write-Output 'Isolated connector installed, remote access disabled until the local Owner pairs it. Verify the connector status and AppContainer privacy preflight before enabling.'
