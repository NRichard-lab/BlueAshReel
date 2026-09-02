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
        [Windows.Forms.MessageBox]::Show('Administrator elevation was not granted. No maintenance was performed.', 'BlueReel maintenance') | Out-Null
    }
    exit
}
Add-Type -AssemblyName System.Windows.Forms
try {
    & (Join-Path $PSScriptRoot 'install.ps1') -Action $Action -ProgramDir $ProgramDir -DataDir $DataDir -Instance $Instance -Interactive
    if ($LASTEXITCODE -ne 0) { throw 'The operation failed. See the protected BlueReel instance logs.' }
    [Windows.Forms.MessageBox]::Show('BlueReel maintenance completed. Backup validation, if selected, did not restore or replace any live files.', 'BlueReel maintenance') | Out-Null
} catch {
    [Windows.Forms.MessageBox]::Show('The maintenance operation did not complete. Check service status and the protected instance logs. User data and source media were preserved.', 'BlueReel maintenance') | Out-Null
    exit 1
}
