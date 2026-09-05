[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Backup','ValidateBackup','ConfigureMedia','ConfigureNetwork')][string]$Action,
    [Parameter(Mandatory)][string]$ProgramDir,
    [Parameter(Mandatory)][string]$DataDir,
    [ValidateSet('development','stable')][string]$Instance = 'development',
    [switch]$Elevated
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskDisplayName = 'Media server'
$taskProductFile = Join-Path $ProgramDir 'config\product.json'
if (Test-Path -LiteralPath $taskProductFile) {
    $taskDisplayName = (Get-Content -LiteralPath $taskProductFile -Raw | ConvertFrom-Json).name
}
function Quote-TaskArgument([string]$Value) {
    if ($Value.Contains('"')) { throw 'Invalid maintenance argument.' }
    return '"' + $Value.TrimEnd('\') + '"'
}
if (-not $Elevated) {
    $taskArguments = @('-NoLogo','-NoProfile','-STA','-ExecutionPolicy','Bypass','-File',
        (Quote-TaskArgument $PSCommandPath),'-Action',$Action,'-ProgramDir',(Quote-TaskArgument $ProgramDir),
        '-DataDir',(Quote-TaskArgument $DataDir),'-Instance',$Instance,'-Elevated')
    try {
        Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -WindowStyle Hidden -ArgumentList $taskArguments | Out-Null
    } catch {
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show('Administrator elevation was not granted. No maintenance was performed.', "$taskDisplayName maintenance") | Out-Null
    }
    exit
}
Add-Type -AssemblyName System.Windows.Forms
try {
    & (Join-Path $PSScriptRoot 'install.ps1') -Action $Action -ProgramDir $ProgramDir -DataDir $DataDir -Instance $Instance -Interactive
    if ($LASTEXITCODE -ne 0) { throw 'The operation failed. See the protected instance logs.' }
    [Windows.Forms.MessageBox]::Show('Maintenance completed. Backup validation, if selected, did not restore or replace any live files.', "$taskDisplayName maintenance") | Out-Null
} catch {
    [Windows.Forms.MessageBox]::Show('The maintenance operation did not complete. Check service status and the protected instance logs. User data and source media were preserved.', "$taskDisplayName maintenance") | Out-Null
    exit 1
}
