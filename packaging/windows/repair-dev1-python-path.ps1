#requires -Version 5.1
<#
One-time, explicit repair for the exact locally tested 0.1.0-dev.1 prototype.
Run elevated with Windows PowerShell -NoProfile -File and the opt-in switch.
This is not an installer rollback or a general repair mechanism. Dev.2 must pass
a clean install without this helper. The original manifest is retained unchanged;
the separate evidence file records this deliberate one-file prototype deviation.
#>
[CmdletBinding()]
param([switch]$AllowDisposablePrototypeRepair)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $AllowDisposablePrototypeRepair) {
    throw 'Explicit -AllowDisposablePrototypeRepair is required; nothing was changed.'
}
if ($PSVersionTable.PSEdition -ne 'Desktop') {
    throw 'Use inbox Windows PowerShell 5.1 for this exact prototype repair.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This explicit prototype repair must run elevated.'
}

$program = 'C:\Program Files\BlueReel Development'
$data = 'C:\ProgramData\BlueReel-Development'
$target = Join-Path $program 'runtime\python\python313._pth'
$python = Join-Path $program 'runtime\python\python.exe'
$manifestPath = Join-Path $program 'included-components.json'
$marker = Join-Path $data '.bluereel-native-instance'
$repairDirectory = Join-Path $data 'state\dev1-python-path-repair'
$originalPath = Join-Path $repairDirectory 'python313._pth.original'
$evidenceDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\artifacts\native-dev'))
$evidencePath = Join-Path $evidenceDirectory 'dev1-python-path-repair.json'
$oldHash = '4f47cd1bb3a89139a6cbae0ecb24905afc16fa2fd4596ada0a63279c2929bcd6'
$newHash = '6d27e5b48c5cc144c9bff1a008244a07cebfc970e921ee5d4cac275e2b2ac5e6'
$manifestHash = 'c1382266a0447ca6dd007eddcae19046e713a82559cb9239684cdca512e7e858'
$content = "python313.zip`n.`nLib/site-packages`n../../backend`n../../.`nimport site`n"

function Assert-OrdinaryPath([string]$Path, [switch]$AllowMissing) {
    $candidate = [IO.Path]::GetFullPath($Path)
    while ($candidate) {
        if (Test-Path -LiteralPath $candidate) {
            $entry = Get-Item -LiteralPath $candidate -Force
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Prototype repair refuses reparse points in its exact paths.'
            }
        } elseif (-not $AllowMissing -and $candidate -eq [IO.Path]::GetFullPath($Path)) {
            throw 'An expected prototype path is missing.'
        }
        $parent = [IO.Directory]::GetParent($candidate)
        if ($null -eq $parent) { break }
        $candidate = $parent.FullName
    }
}

function Get-ExactHash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

foreach ($path in @($program, $data, $target, $python, $manifestPath, $marker, $evidenceDirectory)) {
    Assert-OrdinaryPath $path
}
Assert-OrdinaryPath $repairDirectory -AllowMissing
Assert-OrdinaryPath $evidencePath -AllowMissing
if ((Get-Content -LiteralPath $marker -Raw).Trim() -ne 'BlueReelDevelopment') {
    throw 'Only the identified Development instance may receive this prototype repair.'
}
if ((Get-ExactHash $manifestPath) -ne $manifestHash) {
    throw 'The installed payload is not the exact tested dev.1 artifact.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.version -ne '0.1.0-dev.1' -or $manifest.windows_file_version -ne '0.1.0.1') {
    throw 'This one-time helper is only for the original dev.1 prototype.'
}
if ((Get-ExactHash $target) -ne $oldHash) {
    throw 'The embedded path file differs from the exact original; no modification was made.'
}
if (Test-Path -LiteralPath $repairDirectory) {
    throw 'A prior repair record exists; inspect it explicitly instead of overwriting recovery evidence.'
}
if (Test-Path -LiteralPath $evidencePath) {
    throw 'Public repair evidence already exists; it will not be overwritten.'
}

# Verify every immutable payload file before invoking an elevated installed
# interpreter. Runtime and script selection never comes from installation.json.
foreach ($file in $manifest.files) {
    $relative = [string]$file.path
    if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains(':') -or
        @($relative.Replace('\','/').Split('/')) -contains '..') {
        throw 'The pinned manifest unexpectedly contains an unsafe relative path.'
    }
    $payloadFile = Join-Path $program $relative
    Assert-OrdinaryPath $payloadFile
    if ((Get-Item -LiteralPath $payloadFile).Length -ne $file.size -or
        (Get-ExactHash $payloadFile) -ne $file.sha256) {
        throw 'Installed immutable payload verification failed; no modification was made.'
    }
}

$oldAcl = Get-Acl -LiteralPath $target
$aclSections = [Security.AccessControl.AccessControlSections]'Access,Owner,Group'
$oldSddl = $oldAcl.GetSecurityDescriptorSddlForm($aclSections)
$privateAcl = [Security.AccessControl.DirectorySecurity]::new()
$privateAcl.SetAccessRuleProtection($true, $false)
$administrators = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$privateAcl.SetOwner($administrators)
foreach ($sid in @($administrators, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
    )
    $privateAcl.AddAccessRule($rule)
}
# Windows PowerShell/.NET Framework applies this private DACL at creation.
[void][IO.Directory]::CreateDirectory($repairDirectory, $privateAcl)
[IO.File]::Copy($target, $originalPath, $false)
[IO.File]::WriteAllText((Join-Path $repairDirectory 'original-acl.sddl'), $oldSddl, [Text.Encoding]::ASCII)
if ((Get-ExactHash $originalPath) -ne $oldHash) { throw 'Protected original copy failed verification.' }

# In-place write preserves the existing file DACL, owner, inheritance and path.
# This tiny configuration file is not held by running Python processes; their
# already-loaded paths do not change. Newly launched maintenance reads the fix.
$targetStream = [IO.File]::Open($target, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $bytes = [Text.Encoding]::ASCII.GetBytes($content)
    $targetStream.SetLength(0)
    $targetStream.Write($bytes, 0, $bytes.Length)
    $targetStream.Flush($true)
} finally {
    $targetStream.Dispose()
}
if ((Get-ExactHash $target) -ne $newHash) { throw 'Corrected path file checksum failed; protected original retained.' }
$currentSddl = (Get-Acl -LiteralPath $target).GetSecurityDescriptorSddlForm($aclSections)
if ($currentSddl -ne $oldSddl) { throw 'Original file ACL changed unexpectedly; protected original retained.' }

$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = $python
$start.Arguments = '-I -B -c "import scripts.backup,scripts.restore_validate,sys;from pathlib import Path;root=Path(sys.executable).resolve().parents[2];assert Path(scripts.backup.__file__).resolve().parent==root/''scripts'';assert Path(scripts.restore_validate.__file__).resolve().parent==root/''scripts'';print(''isolated shared backup imports verified'')"'
$start.WorkingDirectory = $program
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.EnvironmentVariables.Clear()
foreach ($key in @('SystemRoot','WINDIR','SystemDrive','TEMP','TMP')) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if ($value) { $start.EnvironmentVariables[$key] = $value }
}
$start.EnvironmentVariables['PATH'] = (Join-Path $program 'runtime\python') + ';' + (Join-Path $env:SystemRoot 'System32')
$process = [Diagnostics.Process]::Start($start)
if (-not $process.WaitForExit(15000)) {
    $process.Kill()
    $process.Dispose()
    throw 'Isolated import check exceeded its time limit. Protected original retained.'
}
$standardOutput = $process.StandardOutput.ReadToEnd()
$standardError = $process.StandardError.ReadToEnd()
$importPassed = $process.ExitCode -eq 0 -and $standardOutput.Trim() -eq 'isolated shared backup imports verified'
$process.Dispose()
$evidence = [ordered]@{
    schema_version = 1
    product_version = '0.1.0-dev.1'
    action = 'explicit one-file disposable prototype repair'
    target = 'runtime/python/python313._pth'
    old_sha256 = $oldHash
    new_sha256 = $newHash
    original_manifest_sha256 = $manifestHash
    original_acl_preserved = $true
    protected_original_retained = $true
    isolated_backup_and_validator_imports = $importPassed
    services_restarted = $false
    runtime_binaries_or_database_files_modified = $false
    original_manifest_retained_with_documented_path_file_deviation = $true
    timestamp_utc = [DateTime]::UtcNow.ToString('o')
}
[IO.File]::WriteAllText($evidencePath, ($evidence | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
if (-not $importPassed) {
    throw 'Path file repaired but isolated import verification failed. Protected original retained; inspect explicitly.'
}
Write-Output 'Exact dev.1 prototype path repaired; backup and validator isolated imports passed. No services restarted.'
Write-Output ('Non-sensitive evidence: ' + $evidencePath)
