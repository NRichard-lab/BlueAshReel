#requires -Version 5.1
<# Explicit recovery of this disposable prototype's two orphaned WinSW wrappers.
Application children must already have exited cooperatively. This is not an
installer feature, general process killer, or automatic rollback mechanism. #>
[CmdletBinding()]
param(
    [switch]$AllowDisposablePrototypeRecovery,
    [Parameter(Mandatory)][int]$ExpectedApiProcessId,
    [Parameter(Mandatory)][int]$ExpectedWebProcessId
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $AllowDisposablePrototypeRecovery) { throw 'Explicit disposable prototype recovery is required.' }
$taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Elevation required.' }
$taskProgram = 'C:\Program Files\BlueReel Development'
$taskData = 'C:\ProgramData\BlueReel-Development'
$taskPrefix = 'BlueReelDevelopment'
$taskReport = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\artifacts\native-dev\dev1-stale-wrapper-recovery.json'))
if (Test-Path -LiteralPath $taskReport) { throw 'Existing recovery report will not be overwritten.' }
$taskManifest = Join-Path $taskProgram 'included-components.json'
if ((Get-FileHash -LiteralPath $taskManifest -Algorithm SHA256).Hash -ine 'c1382266a0447ca6dd007eddcae19046e713a82559cb9239684cdca512e7e858') { throw 'Only the exact original prototype is allowed.' }
if ((Get-Content -LiteralPath (Join-Path $taskData '.bluereel-native-instance') -Raw).Trim() -cne $taskPrefix) { throw 'Wrong instance.' }
foreach ($taskPath in @($taskProgram,$taskData)) {
    $taskCurrent = $taskPath
    while ($taskCurrent) {
        if ((Get-Item -LiteralPath $taskCurrent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked target refused.' }
        $taskParent = [IO.Directory]::GetParent($taskCurrent)
        $taskCurrent = if ($null -eq $taskParent) { $null } else { $taskParent.FullName }
    }
}
$taskExpected = @{API=$ExpectedApiProcessId; Web=$ExpectedWebProcessId}
$taskRows = @()
foreach ($taskRole in @('API','Web')) {
    $taskName = $taskPrefix + $taskRole
    $taskService = Get-CimInstance Win32_Service -Filter "Name='$taskName'"
    $taskBinary = Join-Path $taskProgram ('services\' + $taskName + '.exe')
    $taskXmlPath = Join-Path $taskProgram ('services\' + $taskName + '.xml')
    if ($taskService.ProcessId -ne $taskExpected[$taskRole] -or $taskService.ProcessId -le 0 -or
        $taskService.PathName.Trim('"') -ine $taskBinary -or $taskService.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\LOCAL SERVICE')) { throw 'Stale process identity changed; no recovery attempted.' }
    if ((Get-FileHash -LiteralPath $taskBinary -Algorithm SHA256).Hash -ine 'b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f') { throw 'Unexpected wrapper binary.' }
    [xml]$taskXml = Get-Content -LiteralPath $taskXmlPath -Raw
    if ($taskXml.service.startarguments -cne '-I -B -m app.native_runtime' -or
        $taskXml.service.stoparguments -cne '-I -B -m app.native_runtime --stop' -or
        $taskXml.service.arguments -cne ('--role ' + $taskRole.ToLowerInvariant() + ' --data-dir "' + $taskData + '"')) { throw 'Corrected prototype XML is required before recovery.' }
    if (-not (Test-Path -LiteralPath (Join-Path $taskData ('state\stop-' + $taskRole.ToLowerInvariant())))) { throw 'Prior cooperative stop evidence is missing.' }
    $taskRows += [ordered]@{role=$taskRole; pid=[int]$taskService.ProcessId; image=[IO.Path]::GetFileName($taskBinary)}
}
$taskProcesses = @(Get-CimInstance Win32_Process)
$taskInstanceProcesses = @($taskProcesses | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskProgram + '\',[StringComparison]::OrdinalIgnoreCase) })
if ($taskInstanceProcesses.Count -ne 2 -or @($taskInstanceProcesses | Where-Object { $_.ProcessId -notin @($ExpectedApiProcessId,$ExpectedWebProcessId) }).Count -or
    @($taskProcesses | Where-Object { $_.ParentProcessId -in @($ExpectedApiProcessId,$ExpectedWebProcessId) }).Count) { throw 'Application children or unexpected processes remain; no termination attempted.' }
# No wildcard process names, forceful application termination, or user-session
# selection. Both exact orphaned wrapper PIDs were verified above.
foreach ($taskRow in $taskRows) { Stop-Process -Id $taskRow.pid -Force -ErrorAction Stop }
$taskEvidence = [ordered]@{
    schema_version=1; prototype='0.1.0-dev.1'; manual_recovery=$true
    application_children_already_exited_cooperatively=$true
    corrected_xml_verified=$true; only_stale_wrapper_processes_terminated=$taskRows
    timestamp_utc=[DateTime]::UtcNow.ToString('o')
}
[IO.File]::WriteAllText($taskReport,($taskEvidence | ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
Write-Output 'Two exact orphaned prototype wrappers were removed after all application children had exited. No database or media files were changed.'
