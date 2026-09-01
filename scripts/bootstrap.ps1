[CmdletBinding()]
param(
    [string]$MediaPath,
    [switch]$NoBuild,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvironmentFile = Join-Path $RepositoryRoot ".env"
$EnvironmentExample = Join-Path $RepositoryRoot ".env.example"
$ComposeFile = Join-Path $RepositoryRoot "compose.yml"
$ProductConfigFile = Join-Path $RepositoryRoot "config\product.json"

function Stop-WithMessage([string]$Message) {
    [Console]::Error.WriteLine("Error: $Message")
    exit 1
}

function New-SecureHexSecret {
    $bytes = New-Object byte[] 48
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [System.BitConverter]::ToString($bytes).Replace("-", "").ToLowerInvariant()
}

function Read-DotEnv([string]$Path) {
    $values = @{}
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) { continue }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) { continue }
        $key = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$key] = $value
    }
    return $values
}

function Resolve-ConfiguredPath([string]$Value) {
    if ([System.IO.Path]::IsPathRooted($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $Value))
}

function Test-PrivateBindAddress([string]$Address) {
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$parsed)) { return $false }
    if ([System.Net.IPAddress]::IsLoopback($parsed)) { return $true }
    if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { return $false }
    $octets = $parsed.GetAddressBytes()
    return ($octets[0] -eq 10) -or
        ($octets[0] -eq 172 -and $octets[1] -ge 16 -and $octets[1] -le 31) -or
        ($octets[0] -eq 192 -and $octets[1] -eq 168)
}

if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    Stop-WithMessage "compose.yml was not found at $ComposeFile. Run this script from a complete application checkout."
}
if (-not (Test-Path -LiteralPath $ProductConfigFile -PathType Leaf)) {
    Stop-WithMessage "Central product configuration is missing: $ProductConfigFile"
}
try {
    $productConfiguration = Get-Content -LiteralPath $ProductConfigFile -Raw | ConvertFrom-Json
    $ProductName = [string]$productConfiguration.name
} catch {
    Stop-WithMessage "Central product configuration is not valid JSON: $ProductConfigFile"
}
if ([string]::IsNullOrWhiteSpace($ProductName)) {
    Stop-WithMessage "Central product configuration must define a non-empty name."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-WithMessage @"
Docker is not installed or is not on PATH. Install Docker Desktop, enable its WSL 2 engine when supported, start Docker Desktop, and rerun this script.
One installation option is: winget install --exact --id Docker.DockerDesktop
No packages or system settings were changed.
"@
}

& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Docker Compose v2 is unavailable. Update Docker Desktop so 'docker compose' works, then rerun this script."
}

& docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "The Docker engine is not reachable. Start Docker Desktop and wait until it reports that the engine is running."
}

if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    & wsl.exe --status | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "WSL 2 status could not be read. Docker Desktop can explain whether its WSL 2 backend needs attention."
    }
} else {
    Write-Warning "WSL is not installed. Docker Desktop may use another supported backend; install WSL 2 if Docker Desktop requests it."
}

$createdEnvironment = $false
if (Test-Path -LiteralPath $EnvironmentFile) {
    Write-Host "Keeping existing .env; it was not overwritten."
    if ($MediaPath) {
        Write-Warning "-MediaPath was ignored because .env already exists. Edit MEDIA_PATH deliberately, then rerun for validation."
    }
} else {
    if (-not (Test-Path -LiteralPath $EnvironmentExample -PathType Leaf)) {
        Stop-WithMessage ".env.example is missing; refusing to invent an incomplete configuration."
    }
    $contents = [System.IO.File]::ReadAllText($EnvironmentExample)
    $secret = New-SecureHexSecret
    $contents = $contents -replace 'APP_SECRET_KEY=GENERATE_WITH_BOOTSTRAP_DO_NOT_USE', "APP_SECRET_KEY=$secret"
    if ($MediaPath) {
        if (-not (Test-Path -LiteralPath $MediaPath -PathType Container)) {
            Stop-WithMessage "The selected media directory does not exist or is not a directory: $MediaPath"
        }
        $resolvedMedia = [System.IO.Path]::GetFullPath($MediaPath).Replace('\', '/')
        if ($resolvedMedia.Contains("`n") -or $resolvedMedia.Contains("`r")) {
            Stop-WithMessage "The selected media path contains an unsupported newline."
        }
        $contents = [regex]::Replace($contents, '(?m)^MEDIA_PATH=.*$', "MEDIA_PATH=$resolvedMedia")
    }
    [System.IO.File]::WriteAllText($EnvironmentFile, $contents, [System.Text.UTF8Encoding]::new($false))
    $createdEnvironment = $true
    Write-Host "Created .env with a cryptographically secure application secret."
}

$settings = Read-DotEnv $EnvironmentFile
$requiredKeys = @(
    "APP_SECRET_KEY", "BIND_ADDRESS", "HTTP_PORT", "DATA_PATH", "DATABASE_PATH",
    "ARTWORK_PATH", "TEMP_PATH", "MEDIA_PATH", "BACKUP_PATH", "PUID", "PGID"
)
foreach ($key in $requiredKeys) {
    if (-not $settings.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($settings[$key])) {
        Stop-WithMessage ".env is missing required setting $key. Compare it with .env.example; the script did not overwrite it."
    }
}

if ($settings["APP_SECRET_KEY"] -eq "GENERATE_WITH_BOOTSTRAP_DO_NOT_USE" -or $settings["APP_SECRET_KEY"].Length -lt 64) {
    Stop-WithMessage "APP_SECRET_KEY is still a placeholder or is too short. Move the existing .env aside and rerun bootstrap, or set a cryptographically random value of at least 64 characters."
}
if (-not (Test-PrivateBindAddress $settings["BIND_ADDRESS"])) {
    Stop-WithMessage "BIND_ADDRESS must be a loopback or RFC1918 private IPv4 address. Public and wildcard binds are intentionally rejected."
}
$port = 0
if (-not [int]::TryParse($settings["HTTP_PORT"], [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
    Stop-WithMessage "HTTP_PORT must be an integer from 1 through 65535."
}
foreach ($identityKey in @("PUID", "PGID")) {
    $identity = 0
    if (-not [int]::TryParse($settings[$identityKey], [ref]$identity) -or $identity -lt 1) {
        Stop-WithMessage "$identityKey must be a positive numeric container identity; running application containers as root is rejected."
    }
}

$stateKeys = @("DATA_PATH", "DATABASE_PATH", "ARTWORK_PATH", "TEMP_PATH", "BACKUP_PATH")
$resolvedState = @{}
foreach ($key in $stateKeys) {
    $resolved = Resolve-ConfiguredPath $settings[$key]
    if ($resolved -eq [System.IO.Path]::GetPathRoot($resolved) -or $resolved -eq $RepositoryRoot) {
        Stop-WithMessage "$key resolves to an unsafe broad directory: $resolved"
    }
    [System.IO.Directory]::CreateDirectory($resolved) | Out-Null
    $probe = Join-Path $resolved (".application-write-test-" + [guid]::NewGuid().ToString("N"))
    try {
        [System.IO.File]::WriteAllText($probe, "test")
    } finally {
        if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force }
    }
    $resolvedState[$key] = $resolved.TrimEnd('\', '/')
}

for ($leftIndex = 0; $leftIndex -lt $stateKeys.Count; $leftIndex++) {
    $leftKey = $stateKeys[$leftIndex]
    $leftPath = $resolvedState[$leftKey]
    $leftPrefix = $leftPath + [System.IO.Path]::DirectorySeparatorChar
    for ($rightIndex = $leftIndex + 1; $rightIndex -lt $stateKeys.Count; $rightIndex++) {
        $rightKey = $stateKeys[$rightIndex]
        $rightPath = $resolvedState[$rightKey]
        $rightPrefix = $rightPath + [System.IO.Path]::DirectorySeparatorChar
        if ($leftPath -eq $rightPath -or
            $leftPath.StartsWith($rightPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
            $rightPath.StartsWith($leftPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Stop-WithMessage "$leftKey and $rightKey overlap. Application state paths must be isolated, not equal or nested."
        }
    }
}

$resolvedMediaPath = Resolve-ConfiguredPath $settings["MEDIA_PATH"]
if (-not (Test-Path -LiteralPath $resolvedMediaPath -PathType Container)) {
    if ($createdEnvironment -and $settings["MEDIA_PATH"] -eq "./media") {
        [System.IO.Directory]::CreateDirectory($resolvedMediaPath) | Out-Null
        Write-Host "Created the default empty media directory at $resolvedMediaPath."
    } else {
        Stop-WithMessage "MEDIA_PATH does not exist or is not a directory: $resolvedMediaPath"
    }
}
try { Get-ChildItem -LiteralPath $resolvedMediaPath -Force -ErrorAction Stop | Select-Object -First 1 | Out-Null } catch {
    Stop-WithMessage "MEDIA_PATH is not readable by the current user: $resolvedMediaPath"
}

$mediaPrefix = $resolvedMediaPath.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
foreach ($entry in $resolvedState.GetEnumerator()) {
    $statePrefix = $entry.Value + [System.IO.Path]::DirectorySeparatorChar
    if ($entry.Value -eq $resolvedMediaPath -or $entry.Value.StartsWith($mediaPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        $resolvedMediaPath.StartsWith($statePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        Stop-WithMessage "$($entry.Key) and MEDIA_PATH overlap. Runtime writes must be isolated from read-only source media."
    }
}

Push-Location $RepositoryRoot
try {
    docker compose --env-file $EnvironmentFile config --quiet
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Docker Compose rejected the generated configuration; no services were started." }
    if ($NoStart) {
        Write-Host "Configuration and paths are valid. Services were not started because -NoStart was supplied."
        return
    }

    $upArguments = @("compose", "--env-file", $EnvironmentFile, "up", "--detach")
    if (-not $NoBuild) { $upArguments += "--build" }
    & docker @upArguments
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Docker Compose could not start $ProductName. Run 'docker compose logs' for details." }
} finally {
    Pop-Location
}

$address = $settings["BIND_ADDRESS"]
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
    Stop-WithMessage "Services started but readiness did not succeed within 120 seconds. Inspect with: docker compose logs"
}

Write-Host "$ProductName is ready at $localUrl"
Write-Host "Complete Owner setup in the browser. No public firewall or router settings were changed."
