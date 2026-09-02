[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Pnpm = "pnpm",
    [string]$StageDir,
    [string]$FfmpegDir,
    [string]$Iscc,
    [switch]$SkipFrontendBuild,
    [switch]$SkipInstaller,
    [switch]$Offline
)
$ErrorActionPreference = "Stop"
$builder = Join-Path $PSScriptRoot "build_native.py"
$arguments = @($builder, "--python", $Python, "--pnpm", $Pnpm)
if ($StageDir) { $arguments += @("--stage-dir", $StageDir) }
if ($FfmpegDir) { $arguments += @("--ffmpeg-dir", $FfmpegDir) }
if ($Iscc) { $arguments += @("--iscc", $Iscc) }
if ($SkipFrontendBuild) { $arguments += "--skip-frontend-build" }
if ($SkipInstaller) { $arguments += "--skip-installer" }
if ($Offline) { $arguments += "--offline" }
& $Python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "The BlueReel native development build failed (exit $LASTEXITCODE). Existing artifacts were preserved."
}
