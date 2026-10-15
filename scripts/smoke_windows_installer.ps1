param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$Report = "release-assets/Windows-installer-smoke.json"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw "NSIS installation smoke requires Windows" }

$repository = Split-Path -Parent $PSScriptRoot
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$reportPath = [System.IO.Path]::GetFullPath($Report)
$destination = Join-Path $env:RUNNER_TEMP ("SongCut installed smoke " + [guid]::NewGuid().ToString("N"))
$smokeScript = Join-Path $repository "scripts/smoke_desktop_backend.py"
$python = (Get-Command python -CommandType Application).Source

# NSIS requires /D to be last and unquoted, even if the destination has spaces.
# Start-Process joins ArgumentList without adding quotation marks around /D.
$process = Start-Process -FilePath $installerPath -ArgumentList "/S /D=$destination" -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "NSIS installer exited with $($process.ExitCode)" }

$backend = Join-Path $destination "backend/songcut-backend.exe"
$ffmpeg = Join-Path $destination "backend/_internal/resources/bin/ffmpeg.exe"
$ffprobe = Join-Path $destination "backend/_internal/resources/bin/ffprobe.exe"
foreach ($path in @($backend, $ffmpeg, $ffprobe)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "NSIS installed runtime is incomplete: $path"
    }
}

# The common smoke launches the installed executable from a fresh directory,
# removes Python and FFmpeg from PATH, and exercises real ML/media/server work.
Push-Location $env:RUNNER_TEMP
try {
    & $python $smokeScript $backend
    if ($LASTEXITCODE -ne 0) { throw "Installed runtime smoke exited with $LASTEXITCODE" }
} finally {
    Pop-Location
}

$files = [ordered]@{}
foreach ($entry in @(
    @{name = "installer"; path = $installerPath},
    @{name = "backend"; path = $backend},
    @{name = "ffmpeg"; path = $ffmpeg},
    @{name = "ffprobe"; path = $ffprobe}
)) {
    $files[$entry.name] = [ordered]@{
        name = [System.IO.Path]::GetFileName($entry.path)
        sha256 = (Get-FileHash -LiteralPath $entry.path -Algorithm SHA256).Hash.ToLowerInvariant()
        bytes = (Get-Item -LiteralPath $entry.path).Length
    }
}
$result = [ordered]@{
    platform = "Windows-x64"
    passed = $true
    commit = $env:GITHUB_SHA
    run_id = $env:GITHUB_RUN_ID
    checked_at_utc = [DateTime]::UtcNow.ToString("o")
    installed_path = $destination
    checks = @("NSIS silent installation into a path with spaces", "installed backend/media resource layout", "standalone runtime smoke from clean temporary directory with restricted PATH", "real feature extraction and two-step training", "checkpoint validation and inference", "bundled media tools", "web/help pages and graceful backend shutdown")
    files = $files
}
New-Item -ItemType Directory -Path (Split-Path -Parent $reportPath) -Force | Out-Null
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Host "Installed Windows runtime verified; report: $reportPath"
