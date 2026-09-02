#requires -Version 5.1
<#
Exact, explicitly authorized repair of the four original dev.1 WinSW XML files.
This does not stop, kill, start, reconfigure or uninstall any Windows service.
All native runtime children must already have exited; stale WinSW wrappers may
remain for the operator's separately reviewed, targeted process recovery.
#>
[CmdletBinding()]
param([switch]$AllowDisposablePrototypeRepair)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $AllowDisposablePrototypeRepair) { throw 'Explicit prototype repair opt-in is required; nothing changed.' }
if ($PSVersionTable.PSEdition -ne 'Desktop') { throw 'Use inbox Windows PowerShell 5.1.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'This exact prototype repair requires elevation.' }

$program = 'C:\Program Files\BlueReel Development'
$data = 'C:\ProgramData\BlueReel-Development'
$manifest = Join-Path $program 'included-components.json'
$repairDirectory = Join-Path $data 'state\dev1-service-arguments-repair'
$evidenceDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\artifacts\native-dev'))
$evidencePath = Join-Path $evidenceDirectory 'dev1-service-arguments-repair.json'
$expected = [ordered]@{
    API = @{ role = 'api'; hash = 'b14ce45468057196bb01b07cf774df5f2f72982c3a295bcd5c9e9539c1b0bb7f' }
    Web = @{ role = 'web'; hash = '33739d7e9d251e899e14d2578a1f0ec83c886a9092449c594d4fb113b367c2e0' }
    Worker = @{ role = 'worker'; hash = '37d8cb12698d10a517d5e875e13760bcd835a06f9a00ec242d23de7322d184c0' }
    Proxy = @{ role = 'proxy'; hash = '9c34aad3481a45d7bd895d46079b71237fac43edca70ee36b8d6bc21431176ca' }
}

function Assert-Ordinary([string]$Path, [switch]$AllowMissing) {
    $candidate = [IO.Path]::GetFullPath($Path)
    while ($candidate) {
        if (Test-Path -LiteralPath $candidate) {
            if (((Get-Item -LiteralPath $candidate -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Exact prototype repair refuses reparse points.'
            }
        } elseif (-not $AllowMissing -and $candidate -eq [IO.Path]::GetFullPath($Path)) {
            throw 'An expected prototype path is missing.'
        }
        $parent = [IO.Directory]::GetParent($candidate)
        if ($null -eq $parent) { break }
        $candidate = $parent.FullName
    }
}

function Get-RepairHash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-NoNativeRuntime {
    $runtimeNames = @('python.exe','pythonw.exe','node.exe','caddy.exe','ffmpeg.exe','ffprobe.exe')
    $processes = @(Get-CimInstance Win32_Process)
    foreach ($process in $processes) {
        if ($runtimeNames -contains ([string]$process.Name).ToLowerInvariant()) {
            if (-not $process.ExecutablePath) { throw 'Runtime process provenance unavailable; refusing repair.' }
        }
        if ($process.ExecutablePath -and ([string]$process.ExecutablePath).StartsWith(
            $program + '\runtime\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Native runtime children are still present. Stop them cooperatively before this explicit repair.'
        }
    }
}

foreach ($path in @($program,$data,$manifest,$evidenceDirectory,(Join-Path $data '.bluereel-native-instance'))) {
    Assert-Ordinary $path
}
Assert-Ordinary $repairDirectory -AllowMissing
Assert-Ordinary $evidencePath -AllowMissing
if ((Get-Content -LiteralPath (Join-Path $data '.bluereel-native-instance') -Raw).Trim() -ne 'BlueReelDevelopment') {
    throw 'Development instance marker mismatch.'
}
if ((Get-RepairHash $manifest) -ne 'c1382266a0447ca6dd007eddcae19046e713a82559cb9239684cdca512e7e858') {
    throw 'This is not the exact tested dev.1 payload.'
}
if ((Get-RepairHash (Join-Path $program 'runtime\python\python313._pth')) -ne
    '6d27e5b48c5cc144c9bff1a008244a07cebfc970e921ee5d4cac275e2b2ac5e6') {
    throw 'The separately verified dev.1 Python path repair must be completed first.'
}
if ((Test-Path -LiteralPath $repairDirectory) -or (Test-Path -LiteralPath $evidencePath)) {
    throw 'Prior repair evidence exists and will not be overwritten.'
}
Assert-NoNativeRuntime
$changes = @()
$aclSections = [Security.AccessControl.AccessControlSections]'Access,Owner,Group'
foreach ($suffix in $expected.Keys) {
    $name = 'BlueReelDevelopment' + $suffix
    $path = Join-Path $program ('services\' + $name + '.xml')
    $wrapper = Join-Path $program ('services\' + $name + '.exe')
    Assert-Ordinary $path
    Assert-Ordinary $wrapper
    if ((Get-RepairHash $path) -ne $expected[$suffix].hash -or
        (Get-RepairHash $wrapper) -ne 'b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f') {
        throw 'An original XML or wrapper differs from the exact dev.1 prototype.'
    }
    $service = Get-CimInstance Win32_Service -Filter ("Name='" + $name + "'")
    if (-not $service -or ([string]$service.PathName).Trim('"') -ne $wrapper -or
        $service.StartName -ne 'NT AUTHORITY\LocalService') { throw 'Registered service provenance mismatch.' }
    $text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    $xml = [Xml.XmlDocument]::new()
    $xml.XmlResolver = $null
    $xml.LoadXml($text)
    $role = $expected[$suffix].role
    $oldArguments = '-m app.native_runtime --role ' + $role + ' --data-dir "' + $data + '"'
    $commonArguments = '--role ' + $role + ' --data-dir "' + $data + '"'
    if ($xml.service.id -ne $name -or $xml.service.executable -ne (Join-Path $program 'runtime\python\python.exe') -or
        $xml.service.arguments -ne $oldArguments -or $xml.service.stoparguments -ne ($oldArguments + ' --stop') -or
        $xml.SelectNodes('/service/startarguments').Count -ne 0) { throw 'Original WinSW argument structure mismatch.' }
    $oldStart = '<arguments>' + $oldArguments + '</arguments>'
    $oldStop = '<stoparguments>' + $oldArguments + ' --stop</stoparguments>'
    $newline = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $newStart = '<arguments>' + $commonArguments + '</arguments>' + $newline + '  <startarguments>-I -B -m app.native_runtime</startarguments>'
    $newStop = '<stoparguments>-I -B -m app.native_runtime --stop</stoparguments>'
    $corrected = $text.Replace($oldStart,$newStart).Replace($oldStop,$newStop)
    if ($corrected -eq $text -or $corrected.Replace($newStart,$oldStart).Replace($newStop,$oldStop) -cne $text) {
        throw 'Only the exact three argument elements may change.'
    }
    $changes += [pscustomobject]@{
        name=$name; path=$path; original=$text; corrected=$corrected; oldHash=$expected[$suffix].hash
        sddl=(Get-Acl -LiteralPath $path).GetSecurityDescriptorSddlForm($aclSections)
    }
}

$privateAcl = [Security.AccessControl.DirectorySecurity]::new()
$privateAcl.SetAccessRuleProtection($true,$false)
$administrators = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$privateAcl.SetOwner($administrators)
foreach ($sid in @($administrators,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $privateAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
}
[void][IO.Directory]::CreateDirectory($repairDirectory,$privateAcl)
foreach ($change in $changes) {
    $copy = Join-Path $repairDirectory ($change.name + '.xml.original')
    [IO.File]::Copy($change.path,$copy,$false)
    [IO.File]::WriteAllText((Join-Path $repairDirectory ($change.name + '.acl.sddl')),$change.sddl,[Text.Encoding]::ASCII)
    if ((Get-RepairHash $copy) -ne $change.oldHash) { throw 'Protected original XML copy failed verification.' }
}
$records = @()
foreach ($change in $changes) {
    Assert-NoNativeRuntime
    if ((Get-RepairHash $change.path) -ne $change.oldHash) { throw 'XML changed since preflight; stopping partial repair.' }
    $stream = [IO.File]::Open($change.path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($change.corrected)
        $stream.SetLength(0)
        $stream.Write($bytes,0,$bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
    if (([IO.File]::ReadAllText($change.path,[Text.Encoding]::UTF8)) -cne $change.corrected -or
        (Get-Acl -LiteralPath $change.path).GetSecurityDescriptorSddlForm($aclSections) -ne $change.sddl) {
        throw 'Corrected XML or original ACL verification failed; protected originals retained.'
    }
    $records += [ordered]@{service=$change.name;old_sha256=$change.oldHash;new_sha256=(Get-RepairHash $change.path);acl_preserved=$true}
}
Assert-NoNativeRuntime
$evidence = [ordered]@{
    schema_version=1;product_version='0.1.0-dev.1';action='explicit four-XML disposable prototype repair'
    files=$records;protected_originals_retained=$true;native_runtime_children_absent=$true
    services_or_processes_changed=$false;database_or_media_changed=$false;timestamp_utc=[DateTime]::UtcNow.ToString('o')
}
[IO.File]::WriteAllText($evidencePath,($evidence | ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
Write-Output 'Exact dev.1 WinSW arguments repaired. No service or process control was performed.'
Write-Output ('Non-sensitive evidence: ' + $evidencePath)
