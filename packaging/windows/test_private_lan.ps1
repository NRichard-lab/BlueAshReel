<#
Explicit, elevated acceptance for the disposable DEVELOPMENT instance only.
This is a same-host lifecycle check, not proof of access from another LAN device.
Only the protected installed ConfigureNetwork workflow changes application state.
Global firewall/profile settings and unrelated rules are read and hashed, never written.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$LocalIpv4,
    [Parameter(Mandatory)][string]$ReportDirectory,
    [switch]$AllowDisposableInstance
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$TaskPrefix = 'BlueReelDevelopment'
$TaskProgram = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'BlueReel Development'
$TaskData = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'BlueReel-Development'
$TaskInstaller = Join-Path $TaskProgram 'support\install.ps1'
$TaskCaddy = Join-Path $TaskProgram 'runtime\caddy\caddy.exe'
$TaskRuleName = "$TaskPrefix-Inbound-PrivateLAN"
$TaskOwnRules = @($TaskRuleName) + @('python','node','ffmpeg','ffprobe','caddy' | ForEach-Object { "$TaskPrefix-Outbound-$_" })
$TaskReport = [ordered]@{schema_version=1; instance='development'; passed=$false; restored_loopback=$false; remote_device_verified=$false}

function Assert-NoReparse([string]$Path) {
    $taskPath = [IO.Path]::GetFullPath($Path)
    while ($taskPath) {
        if (Test-Path -LiteralPath $taskPath) {
            if ((Get-Item -LiteralPath $taskPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'LAN acceptance target contains a link or reparse point.'
            }
        }
        $taskParent = [IO.Directory]::GetParent($taskPath)
        $taskPath = if ($null -eq $taskParent) { $null } else { $taskParent.FullName }
    }
}

function Assert-ProtectedInstaller {
    Assert-NoReparse $TaskInstaller
    $taskTrusted = @('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    foreach ($taskPath in @($TaskProgram,(Join-Path $TaskProgram 'support'),$TaskInstaller)) {
        $taskAcl = Get-Acl -LiteralPath $taskPath
        if ($taskAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $taskTrusted) {
            throw 'The installed maintenance source is not owned by a trusted Windows administrator identity.'
        }
        foreach ($taskRule in $taskAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($taskRule.AccessControlType -eq 'Allow' -and $taskRule.IdentityReference.Value -notin $taskTrusted -and
                -not ($taskRule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and
                ([int]$taskRule.FileSystemRights -band 0x000D0156)) {
                throw 'The installed maintenance source is writable outside its protected administrator boundary.'
            }
        }
    }
}

function Assert-PrivateInterface([string]$Address) {
    $taskParsed = $null
    if (-not [Net.IPAddress]::TryParse($Address,[ref]$taskParsed) -or
        $taskParsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $taskParsed.ToString() -cne $Address) {
        throw 'Choose one canonical assigned private IPv4 address.'
    }
    $taskBytes = $taskParsed.GetAddressBytes()
    if (-not ($taskBytes[0] -eq 10 -or ($taskBytes[0] -eq 172 -and $taskBytes[1] -ge 16 -and $taskBytes[1] -le 31) -or
        ($taskBytes[0] -eq 192 -and $taskBytes[1] -eq 168))) { throw 'Only an explicit RFC1918 private IPv4 address is permitted.' }
    $taskAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Address -ErrorAction Stop | Where-Object { $_.AddressState -eq 'Preferred' })
    if ($taskAddresses.Count -ne 1) { throw 'The selected private address is not uniquely assigned and preferred on this host.' }
    $taskProfiles = @(Get-NetConnectionProfile -InterfaceIndex $taskAddresses[0].InterfaceIndex -ErrorAction Stop)
    if ($taskProfiles.Count -ne 1 -or $taskProfiles[0].NetworkCategory -ne 'Private') {
        throw 'The selected interface must already use the Private network profile; acceptance never changes it.'
    }
    return [int]$taskAddresses[0].InterfaceIndex
}

function Get-Digest($Value) {
    $taskHash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($taskHash.ComputeHash([Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 12 -Compress)))).Replace('-','').ToLowerInvariant() }
    finally { $taskHash.Dispose() }
}

function Get-SafetySnapshot {
    $taskProfiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore | Sort-Object Name | Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction,AllowInboundRules,AllowLocalFirewallRules,AllowLocalIPsecRules,NotifyOnListen,LogAllowed,LogBlocked)
    $taskNetwork = @(Get-NetConnectionProfile | Sort-Object InterfaceIndex | Select-Object InterfaceIndex,NetworkCategory)
    $taskRules = @(Get-NetFirewallRule -PolicyStore ActiveStore | Where-Object { $_.Name -notin $TaskOwnRules } | Sort-Object Name | Select-Object Name,Group,Enabled,Profile,Direction,Action,EdgeTraversalPolicy,LooseSourceMapping,LocalOnlyMapping,Owner,PolicyStoreSource,PolicyStoreSourceType)
    # Filter InstanceID is the owning firewall rule Name; include every effective
    # non-instance filter so a rule cannot silently change its program/port/scope.
    $taskFilters = [ordered]@{}
    foreach ($taskCommand in @('Get-NetFirewallAddressFilter','Get-NetFirewallPortFilter','Get-NetFirewallApplicationFilter','Get-NetFirewallServiceFilter','Get-NetFirewallInterfaceFilter','Get-NetFirewallInterfaceTypeFilter','Get-NetFirewallSecurityFilter')) {
        $taskFilters[$taskCommand] = @(& $taskCommand -PolicyStore ActiveStore | Where-Object { $_.InstanceID -notin $TaskOwnRules } | Sort-Object InstanceID | Select-Object * -ExcludeProperty CimClass,CimInstanceProperties,CimSystemProperties,PSComputerName)
    }
    return [ordered]@{profiles=(Get-Digest @($taskProfiles,$taskNetwork)); unrelated_rules=(Get-Digest @($taskRules,$taskFilters))}
}

function Assert-Instance {
    Assert-NoReparse $TaskData
    $taskMarker = Join-Path $TaskData '.bluereel-native-instance'
    Assert-NoReparse $taskMarker
    if (-not (Test-Path -LiteralPath $taskMarker -PathType Leaf) -or (Get-Item -LiteralPath $taskMarker).Length -gt 128 -or
        [IO.File]::ReadAllText($taskMarker).TrimEnd([char[]]"`r`n") -cne $TaskPrefix) { throw 'Disposable development marker is absent or foreign.' }
    $taskPath = Join-Path $TaskData 'configuration\installation.json'
    Assert-NoReparse $taskPath
    $taskMetadata = Get-Content -LiteralPath $taskPath -Raw | ConvertFrom-Json
    if ($taskMetadata.instance -cne 'development' -or $taskMetadata.service_prefix -cne $TaskPrefix -or
        [IO.Path]::GetFullPath($taskMetadata.program_dir).TrimEnd('\') -ine $TaskProgram -or
        [IO.Path]::GetFullPath($taskMetadata.data_dir).TrimEnd('\') -ine $TaskData -or
        $taskMetadata.port -ne 18080 -or $taskMetadata.api_port -ne 18081 -or $taskMetadata.web_port -ne 18082 -or
        $taskMetadata.bind_address -cne '127.0.0.1') { throw 'LAN acceptance requires the fixed development instance initially bound to loopback only.' }
    foreach ($taskRole in @('API','Worker','Web','Proxy')) {
        $taskName = $TaskPrefix + $taskRole
        $taskService = Get-CimInstance Win32_Service -Filter "Name='$taskName'"
        if ($null -eq $taskService -or $taskService.State -ne 'Running' -or
            $taskService.PathName.Trim('"') -ine (Join-Path $TaskProgram "services\$taskName.exe") -or
            $taskService.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\LOCAL SERVICE')) {
            throw 'Every fixed development service must be running with its exact protected binary and LocalService account.'
        }
    }
    if (@(Get-NetFirewallRule -Name $TaskRuleName -ErrorAction SilentlyContinue).Count) { throw 'LAN acceptance requires no pre-existing instance LAN rule.' }
}

function Get-ConfigurationHashes {
    $taskResult = [ordered]@{}
    foreach ($taskName in @('.env','installation.json','Caddyfile')) {
        $taskPath = Join-Path $TaskData ('configuration\' + $taskName)
        Assert-NoReparse $taskPath
        $taskResult[$taskName] = (Get-FileHash -LiteralPath $taskPath -Algorithm SHA256).Hash
    }
    return $taskResult
}

function Quote-Argument([string]$Value) {
    return '"' + [regex]::Replace([regex]::Replace($Value,'(\\*)"','$1$1\"'),'(\\+)$','$1$1') + '"'
}

function Invoke-ConfigureNetwork([string]$Address) {
    if ($Address -cne $LocalIpv4 -and $Address -cne '127.0.0.1') { throw 'LAN lifecycle requested an unexpected binding.' }
    Assert-ProtectedInstaller
    $taskStart = [Diagnostics.ProcessStartInfo]::new()
    $taskStart.FileName = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    $taskArguments = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$TaskInstaller,'-Action','ConfigureNetwork','-ProgramDir',$TaskProgram,'-DataDir',$TaskData,'-Instance','development','-BindAddress',$Address)
    $taskStart.Arguments = (($taskArguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
    $taskStart.UseShellExecute = $false
    $taskStart.CreateNoWindow = $true
    $taskStart.RedirectStandardOutput = $true
    $taskStart.RedirectStandardError = $true
    $taskProcess = [Diagnostics.Process]::new()
    $taskProcess.StartInfo = $taskStart
    try {
        $null = $taskProcess.Start()
        $taskOutput = $taskProcess.StandardOutput.ReadToEndAsync()
        $taskError = $taskProcess.StandardError.ReadToEndAsync()
        if (-not $taskProcess.WaitForExit(720000)) { $taskProcess.Kill(); throw 'The fixed installed network workflow exceeded its bounded deadline.' }
        $taskProcess.WaitForExit()
        $null = $taskOutput.Result; $null = $taskError.Result
        if ($taskProcess.ExitCode -ne 0) { throw 'The installed network workflow failed; raw subprocess output was withheld.' }
    } finally { $taskProcess.Dispose() }
}

function Assert-Health([string]$Address) {
    if ($Address -cne $LocalIpv4 -and $Address -cne '127.0.0.1') { throw 'Health probe origin is outside this exact host.' }
    $taskRequest = [Net.HttpWebRequest]::Create("http://${Address}:18080/api/v1/health/ready")
    $taskRequest.Proxy = $null
    $taskRequest.Timeout = 5000
    $taskRequest.ReadWriteTimeout = 5000
    $taskResponse = $taskRequest.GetResponse()
    try {
        $taskReader = [IO.StreamReader]::new($taskResponse.GetResponseStream())
        try { $taskPayload = $taskReader.ReadToEnd() | ConvertFrom-Json } finally { $taskReader.Dispose() }
        if ([int]$taskResponse.StatusCode -ne 200 -or $taskPayload.status -notin @('ready','ok')) { throw 'Native health is not ready.' }
    } finally { $taskResponse.Dispose() }
}

function Assert-Listeners([bool]$Enabled) {
    $taskExpected = if ($Enabled) { @('127.0.0.1',$LocalIpv4) } else { @('127.0.0.1') }
    $taskListeners = @(Get-NetTCPConnection -State Listen -LocalPort 18080 -ErrorAction Stop)
    if (@(Compare-Object ($taskExpected | Sort-Object) @($taskListeners.LocalAddress | Sort-Object -Unique)).Count) {
        throw 'Public ingress listener addresses differ from the exact requested scope.'
    }
    foreach ($taskListener in $taskListeners) {
        if ((Get-Process -Id $taskListener.OwningProcess).Path -ine $TaskCaddy) { throw 'Ingress listener is not owned by the fixed bundled Caddy.' }
    }
    foreach ($taskPort in @(18081,18082)) {
        $taskInternal = @(Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction Stop)
        if (-not $taskInternal.Count -or @($taskInternal | Where-Object { $_.LocalAddress -cne '127.0.0.1' }).Count) {
            throw 'Internal API/frontend listeners must remain exclusively loopback.'
        }
    }
}

function Assert-LanRule {
    $taskRules = @(Get-NetFirewallRule -Name $TaskRuleName -PolicyStore ActiveStore -ErrorAction Stop)
    if ($taskRules.Count -ne 1) { throw 'Exactly one instance LAN rule is required.' }
    $taskRule = $taskRules[0]
    if ($taskRule.Group -cne $TaskPrefix -or $taskRule.Enabled -ne 'True' -or $taskRule.Direction -ne 'Inbound' -or
        $taskRule.Action -ne 'Allow' -or [string]$taskRule.Profile -cne 'Private') { throw 'The instance LAN rule has an unexpected policy scope.' }
    $taskAddress = $taskRule | Get-NetFirewallAddressFilter
    $taskPort = $taskRule | Get-NetFirewallPortFilter
    $taskApplication = $taskRule | Get-NetFirewallApplicationFilter
    if (@($taskAddress.LocalAddress).Count -ne 1 -or $taskAddress.LocalAddress -cne $LocalIpv4 -or
        @($taskAddress.RemoteAddress).Count -ne 1 -or $taskAddress.RemoteAddress -cne 'LocalSubnet' -or
        $taskPort.Protocol -notin @('TCP','6') -or [string]$taskPort.LocalPort -cne '18080' -or
        [string]$taskPort.RemotePort -cne 'Any' -or $taskApplication.Program -ine $TaskCaddy) {
        throw 'The instance LAN rule must target only bundled Caddy, the chosen address, TCP18080 and LocalSubnet.'
    }
}

function Invoke-LanLifecycle {
    $taskBefore = Get-SafetySnapshot
    $taskConfiguration = Get-ConfigurationHashes
    $taskInstallerHash = (Get-FileHash -LiteralPath $TaskInstaller -Algorithm SHA256).Hash
    try {
        Invoke-ConfigureNetwork $LocalIpv4
        Assert-LanRule
        Assert-Listeners $true
        Assert-Health '127.0.0.1'
        Assert-Health $LocalIpv4
        $TaskReport.enabled_checks_passed = $true
    } finally {
        # Even a partial enable failure invokes the ordinary protected disable
        # workflow. Never broaden rules/profile settings as a recovery shortcut.
        Invoke-ConfigureNetwork '127.0.0.1'
        if (@(Get-NetFirewallRule -Name $TaskRuleName -ErrorAction SilentlyContinue).Count) { throw 'The instance LAN rule remained after restoring loopback.' }
        Assert-Listeners $false
        Assert-Health '127.0.0.1'
        $taskAfter = Get-SafetySnapshot
        $TaskReport.global_profiles_unchanged = $taskBefore.profiles -ceq $taskAfter.profiles
        $TaskReport.unrelated_rules_unchanged = $taskBefore.unrelated_rules -ceq $taskAfter.unrelated_rules
        $TaskReport.configuration_restored = (Get-Digest $taskConfiguration) -ceq (Get-Digest (Get-ConfigurationHashes))
        $TaskReport.installer_unchanged = $taskInstallerHash -ceq (Get-FileHash -LiteralPath $TaskInstaller -Algorithm SHA256).Hash
        if (-not ($TaskReport.global_profiles_unchanged -and $TaskReport.unrelated_rules_unchanged -and
            $TaskReport.configuration_restored -and $TaskReport.installer_unchanged)) { throw 'LAN acceptance detected an unexpected configuration or unrelated policy change.' }
        $TaskReport.restored_loopback = $true
    }
    $TaskReport.passed = $true
}

$TaskReportPath = $null
try {
    if (-not $AllowDisposableInstance) { throw 'Explicit disposable development acceptance opt-in is required.' }
    $taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not ([Security.Principal.WindowsPrincipal]::new($taskIdentity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Explicit administrator elevation is required.' }
    $taskReportRoot = [IO.Path]::GetFullPath($ReportDirectory).TrimEnd('\')
    if (-not [IO.Path]::IsPathRooted($ReportDirectory) -or $taskReportRoot.Length -lt 5 -or
        $taskReportRoot -ieq $TaskProgram -or $taskReportRoot -ieq $TaskData -or
        $taskReportRoot.StartsWith($TaskProgram + '\',[StringComparison]::OrdinalIgnoreCase) -or
        $taskReportRoot.StartsWith($TaskData + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Use a dedicated report directory outside installed program and application data.' }
    Assert-NoReparse $taskReportRoot
    if (-not (Test-Path -LiteralPath $taskReportRoot -PathType Container)) { throw 'Create the explicit report directory before acceptance.' }
    $TaskReportPath = Join-Path $taskReportRoot ('private-lan-' + [Guid]::NewGuid().ToString('N') + '.json')
    Assert-ProtectedInstaller
    Assert-Instance
    $TaskReport.interface_index = Assert-PrivateInterface $LocalIpv4
    $TaskReport.local_ipv4 = $LocalIpv4
    Assert-Health '127.0.0.1'
    Invoke-LanLifecycle
} catch {
    $TaskReport.error = 'Private-LAN acceptance failed. Consult boolean checks; if restored_loopback is false, use the installed ConfigureNetwork workflow to restore 127.0.0.1 before retrying.'
} finally {
    if ($TaskReportPath) {
        $taskFile = [IO.File]::Open($TaskReportPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        $taskWriter = [IO.StreamWriter]::new($taskFile,[Text.UTF8Encoding]::new($false))
        try { $taskWriter.Write(($TaskReport | ConvertTo-Json -Depth 8)) } finally { $taskWriter.Dispose() }
    }
}
if (-not $TaskReport.passed) { Write-Error 'Disposable private-LAN acceptance did not pass; no raw private output was recorded.'; exit 1 }
Write-Output 'Disposable private-LAN lifecycle passed and loopback-only access was restored. Cross-device access was not tested.'
