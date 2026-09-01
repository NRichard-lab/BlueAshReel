[CmdletBinding()]
param(
    [ValidateRange(1, 36500)]
    [int]$RetentionDays,
    [switch]$NoPull
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvironmentFile = Join-Path $RepositoryRoot ".env"
$ProductConfigFile = Join-Path $RepositoryRoot "config\product.json"

function Stop-WithMessage([string]$Message) {
    [Console]::Error.WriteLine("Error: $Message")
    exit 1
}

function Read-DotEnv([string]$Path) {
    $values = @{}
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
        $key, $value = $trimmed.Split("=", 2)
        $values[$key.Trim()] = $value.Trim().Trim('"', "'")
    }
    return $values
}

if (-not (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf)) {
    Stop-WithMessage "Upgrade requires an existing .env. Run bootstrap for a new installation instead."
}
if (-not (Test-Path -LiteralPath $ProductConfigFile -PathType Leaf)) {
    Stop-WithMessage "Central product configuration is missing: $ProductConfigFile"
}
try {
    $productConfiguration = Get-Content -LiteralPath $ProductConfigFile -Raw | ConvertFrom-Json
    $ProductName = [string]$productConfiguration.name
} catch {
    Stop-WithMessage "Central product configuration is not valid JSON."
}
if ([string]::IsNullOrWhiteSpace($ProductName)) { Stop-WithMessage "Central product name is empty." }

# Reuse bootstrap's non-mutating validation path. It preserves the existing
# .env, validates prerequisites and mounts, and renders Compose without start.
& (Join-Path $PSScriptRoot "bootstrap.ps1") -NoStart
if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Pre-upgrade validation failed; no upgrade action was taken." }

$backupArguments = @{}
if ($PSBoundParameters.ContainsKey("RetentionDays")) { $backupArguments.RetentionDays = $RetentionDays }
& (Join-Path $PSScriptRoot "backup.ps1") @backupArguments
if ($LASTEXITCODE -ne 0) { Stop-WithMessage "The pre-upgrade online backup failed; images and services were not changed." }

Push-Location $RepositoryRoot
try {
    if (-not $NoPull) {
        & docker compose --env-file .env pull proxy
        if ($LASTEXITCODE -ne 0) { Stop-WithMessage "The proxy image could not be retrieved; running services were left unchanged." }
        & docker compose --env-file .env build --pull backend worker frontend
    } else {
        & docker compose --env-file .env build backend worker frontend
    }
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Image build failed; running services were left unchanged." }

    & docker compose --env-file .env stop worker backend
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Could not stop the worker/backend cleanly. Inspect service state before continuing." }

    & docker compose --env-file .env run --rm --no-deps backend migrate
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "Database migration failed. Backend/worker remain stopped; preserve state and follow docs/backup-and-restore.md."
    }

    & docker compose --env-file .env up --detach
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Migration succeeded, but the upgraded services did not start. Inspect docker compose logs." }
} finally {
    Pop-Location
}

$settings = Read-DotEnv $EnvironmentFile
$address = $settings["BIND_ADDRESS"]
$port = $settings["HTTP_PORT"]
$displayHost = if ($address -eq "127.0.0.1" -or $address -eq "::1") { "localhost" } else { $address }
$localUrl = "http://${displayHost}:$port"
$healthUrl = "$localUrl/api/v1/health/ready"
$healthy = $false
for ($attempt = 1; $attempt -le 60; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -Method Get -TimeoutSec 3 -UseBasicParsing
        if ($response.StatusCode -eq 200) { $healthy = $true; break }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $healthy) {
    Stop-WithMessage "Upgrade commands completed, but readiness failed within 120 seconds. Inspect docker compose ps and logs; do not delete state."
}

Write-Host "$ProductName upgrade completed and is ready at $localUrl"
Write-Host "A validated backup was created before migration. No Git remote, firewall, router, volume, or unrelated workload was changed."
