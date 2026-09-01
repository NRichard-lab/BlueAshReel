[CmdletBinding()]
param(
    [ValidateRange(1, 36500)]
    [int]$RetentionDays,
    [switch]$SkipArtwork
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not (Test-Path -LiteralPath (Join-Path $RepositoryRoot ".env") -PathType Leaf)) {
    [Console]::Error.WriteLine("Error: Run scripts/bootstrap.ps1 first; .env does not exist.")
    exit 1
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine("Error: Docker is required. Start Docker Desktop and rerun.")
    exit 1
}

$arguments = @("compose", "--env-file", ".env", "--profile", "tools", "run", "--rm", "--no-deps", "backup")
if ($PSBoundParameters.ContainsKey("RetentionDays")) { $arguments += @("--retention-days", $RetentionDays.ToString()) }
if ($SkipArtwork) { $arguments += "--skip-artwork" }

Push-Location $RepositoryRoot
try {
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
