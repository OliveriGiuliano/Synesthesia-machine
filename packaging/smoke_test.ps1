[CmdletBinding()]
param(
    [string]$AppDirectory = (Join-Path $PSScriptRoot '..'),
    [string]$ReportPath = (Join-Path ([System.IO.Path]::GetTempPath()) 'synesthesia-machine-packaged-smoke.json'),
    [switch]$Headless
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Start-PackagedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$LocalAppData,
        [int]$TimeoutSeconds = 45
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.Arguments = $Arguments
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.EnvironmentVariables['LOCALAPPDATA'] = $LocalAppData
    $startInfo.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
    if ($Headless) {
        $startInfo.EnvironmentVariables['QT_QPA_PLATFORM'] = 'offscreen'
    }
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw "Could not start $Executable"
    }
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill()
        throw "Packaged process timed out after $TimeoutSeconds seconds"
    }
    if ($process.ExitCode -ne 0) {
        throw "Packaged process exited with code $($process.ExitCode)"
    }
}

$resolvedApp = (Resolve-Path -LiteralPath $AppDirectory).Path
$executable = Join-Path $resolvedApp 'SynesthesiaMachine.exe'
$video = Join-Path $resolvedApp 'smoke\h264-smoke.mp4'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Packaged executable not found: $executable"
}
if (-not (Test-Path -LiteralPath $video -PathType Leaf)) {
    throw "Bundled H.264 smoke video not found: $video"
}

$developmentLeaks = Get-ChildItem -LiteralPath $resolvedApp -Recurse -File | Where-Object {
    $_.Extension -in '.py', '.pyc', '.pyo' -or
    $_.FullName -match '[\\/](tests|\.pytest_cache|\.ruff_cache|\.venv)[\\/]'
}
if ($developmentLeaks) {
    throw "Development-only files leaked into the release: $($developmentLeaks.FullName -join ', ')"
}

$requiredNativePatterns = @(
    'avcodec*.dll',
    'cv2*.pyd',
    'rtmidi*.pyd',
    'libportaudio*.dll'
)
foreach ($pattern in $requiredNativePatterns) {
    if (-not (Get-ChildItem -LiteralPath $resolvedApp -Recurse -File -Filter $pattern)) {
        throw "Required packaged native component was not found: $pattern"
    }
}

$smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) "synmachine-smoke-$([guid]::NewGuid())"
$portableCopy = Join-Path $smokeRoot 'Synesthesia Machine.dist'
$localAppData = Join-Path $smokeRoot 'LocalAppData'
New-Item -ItemType Directory -Force $smokeRoot, $localAppData | Out-Null
Copy-Item -LiteralPath $resolvedApp -Destination $portableCopy -Recurse

$documents = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)
if (-not $documents -or -not (Test-Path -LiteralPath $documents -PathType Container)) {
    throw 'The current account has no accessible Documents directory'
}
$documentSentinel = Join-Path $documents "synmachine-smoke-$([guid]::NewGuid()).synmachine.json"
Set-Content -LiteralPath $documentSentinel -Value '{"release_smoke_sentinel":true}' -Encoding UTF8

try {
    $portableExe = Join-Path $portableCopy 'SynesthesiaMachine.exe'
    $portableVideo = Join-Path $portableCopy 'smoke\h264-smoke.mp4'
    $quotedReport = '"' + $ReportPath.Replace('"', '\"') + '"'
    $quotedVideo = '"' + $portableVideo.Replace('"', '\"') + '"'
    Start-PackagedProcess -Executable $portableExe `
        -Arguments "--packaged-smoke-report $quotedReport --h264-video $quotedVideo" `
        -LocalAppData $localAppData -TimeoutSeconds 90
    Start-PackagedProcess -Executable $portableExe -Arguments '--smoke-test' `
        -LocalAppData $localAppData
    Start-PackagedProcess -Executable $portableExe -Arguments '--smoke-test' `
        -LocalAppData $localAppData

    $report = Get-Content -Raw -LiteralPath $ReportPath | ConvertFrom-Json
    if (-not $report.passed) {
        throw "Packaged self-test reported a failure; inspect $ReportPath"
    }

    Remove-Item -LiteralPath $portableCopy -Recurse -Force
    if (-not (Test-Path -LiteralPath $documentSentinel -PathType Leaf)) {
        throw 'Deleting the portable application removed a user document'
    }
}
finally {
    if (Test-Path -LiteralPath $documentSentinel) {
        Remove-Item -LiteralPath $documentSentinel -Force
    }
    if (Test-Path -LiteralPath $smokeRoot) {
        Remove-Item -LiteralPath $smokeRoot -Recurse -Force
    }
}

Write-Host "Packaged smoke test passed. Report: $ReportPath"
