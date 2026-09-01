[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Archive,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvironmentFile = Join-Path $RepositoryRoot ".env"
if (-not (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf)) {
    [Console]::Error.WriteLine("Error: Run scripts/bootstrap.ps1 first; .env does not exist.")
    exit 1
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine("Error: Docker is required. Start Docker Desktop and rerun.")
    exit 1
}

function Read-BackupPath([string]$Path) {
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*BACKUP_PATH\s*=\s*(.+?)\s*$') {
            return $Matches[1].Trim('"', "'")
        }
    }
    return "./backups"
}

$backupSetting = Read-BackupPath $EnvironmentFile
$backupDirectory = if ([System.IO.Path]::IsPathRooted($backupSetting)) {
    [System.IO.Path]::GetFullPath($backupSetting)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $backupSetting))
}
$archivePath = [System.IO.Path]::GetFullPath($Archive)
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    [Console]::Error.WriteLine("Error: Backup archive does not exist: $archivePath")
    exit 1
}
$backupPrefix = $backupDirectory.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
if (-not $archivePath.StartsWith($backupPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    [Console]::Error.WriteLine("Error: The archive must be inside configured BACKUP_PATH: $backupDirectory")
    exit 1
}
$relative = $archivePath.Substring($backupPrefix.Length)

$containerArchive = "/backups/" + $relative.Replace('\', '/')
$arguments = @(
    "compose", "--env-file", ".env", "--profile", "tools", "run", "--rm", "--no-deps",
    "--entrypoint", "python", "backup", "/tools/restore_validate.py", $containerArchive
)
if ($Json) { $arguments += "--json" }

Push-Location $RepositoryRoot
try {
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
