[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outputRoot = Join-Path $repository 'packaging\out'
$workRoot = Join-Path $repository 'packaging\work'
$deploymentRoot = Join-Path $repository 'deployment'
$artifact = Join-Path $outputRoot 'Synesthesia Machine.dist'

function Assert-RepositoryChild {
    param([Parameter(Mandatory = $true)][string]$Path)
    $resolvedRepository = [System.IO.Path]::GetFullPath($repository).TrimEnd('\') + '\'
    $resolvedTarget = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolvedTarget.StartsWith($resolvedRepository, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the repository: $resolvedTarget"
    }
}

function Assert-NativeSuccess {
    param([Parameter(Mandatory = $true)][string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

foreach ($target in @($outputRoot, $workRoot, $deploymentRoot)) {
    Assert-RepositoryChild -Path $target
}

if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
    throw 'Phase 9 release builds require a 64-bit Windows process on Windows x64'
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Phase 9 release builds are supported only on Windows'
}

Push-Location $repository
try {
    $sourceStatus = git status --porcelain --untracked-files=all
    Assert-NativeSuccess -Operation 'git status'
    if ($sourceStatus -and -not $AllowDirty) {
        throw 'Release builds require a clean Git worktree. Commit changes or pass -AllowDirty for a non-release verification build.'
    }

    uv sync --locked --group packaging
    Assert-NativeSuccess -Operation 'uv sync'
    uv run python -m tools.phase9_release check
    Assert-NativeSuccess -Operation 'release configuration check'
    if (-not $SkipTests) {
        uv run check
        Assert-NativeSuccess -Operation 'static checks'
        uv run pytest -q
        Assert-NativeSuccess -Operation 'test suite'
    }

    foreach ($target in @($outputRoot, $workRoot, $deploymentRoot)) {
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }
    New-Item -ItemType Directory -Force $outputRoot, $workRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $repository 'pysidedeploy.spec') `
        -Destination (Join-Path $workRoot 'pysidedeploy.spec')

    $env:PYTHONHASHSEED = '0'
    $env:SOURCE_DATE_EPOCH = (git show -s --format=%ct HEAD).Trim()
    Assert-NativeSuccess -Operation 'git source timestamp'
    uv run pyside6-deploy -c (Join-Path $workRoot 'pysidedeploy.spec') -f -v
    $executable = Join-Path $artifact 'SynesthesiaMachine.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Standalone executable was not created: $executable"
    }

    $portAudioSource = Join-Path $repository `
        '.venv\Lib\site-packages\_sounddevice_data\portaudio-binaries\libportaudio64bit.dll'
    if (-not (Test-Path -LiteralPath $portAudioSource -PathType Leaf)) {
        throw "Locked PortAudio runtime was not found: $portAudioSource"
    }
    $portAudioDirectory = Join-Path $artifact '_sounddevice_data\portaudio-binaries'
    New-Item -ItemType Directory -Force $portAudioDirectory | Out-Null
    Copy-Item -LiteralPath $portAudioSource -Destination $portAudioDirectory
    if (Get-ChildItem -LiteralPath $artifact -Recurse -File -Filter '*asio*.dll') {
        throw 'The release unexpectedly contains an ASIO-enabled PortAudio binary'
    }

    $smokeDirectory = Join-Path $artifact 'smoke'
    New-Item -ItemType Directory -Force $smokeDirectory | Out-Null
    Copy-Item -LiteralPath (Join-Path $repository 'packaging\smoke_test.ps1') `
        -Destination (Join-Path $smokeDirectory 'smoke_test.ps1')
    Copy-Item -LiteralPath (Join-Path $repository 'examples\phase9\media\h264-smoke.mp4') `
        -Destination (Join-Path $smokeDirectory 'h264-smoke.mp4')
    Copy-Item -LiteralPath (Join-Path $repository 'LICENSE-or-NOTICE.md') -Destination $artifact
    Copy-Item -LiteralPath (Join-Path $repository 'packaging\RUNNING.md') -Destination $artifact
    Copy-Item -LiteralPath (Join-Path $repository 'packaging\licensing-review.md') -Destination $artifact

    uv run python -m tools.phase9_release inventory `
        --output (Join-Path $artifact 'dependency-inventory.json') `
        --notices (Join-Path $artifact 'THIRD_PARTY_NOTICES.md') `
        --licenses (Join-Path $artifact 'licenses')
    Assert-NativeSuccess -Operation 'dependency inventory generation'
    uv run python -m tools.phase9_release provenance `
        --artifact $artifact --output (Join-Path $artifact 'build-provenance.json')
    Assert-NativeSuccess -Operation 'build provenance generation'

    $version = uv run python -c 'from synesthesia_machine import __version__; print(__version__)'
    Assert-NativeSuccess -Operation 'application version query'
    $archive = Join-Path $outputRoot "Synesthesia-Machine-$($version.Trim())-windows-x64.zip"
    uv run python -m tools.phase9_release archive --artifact $artifact --output $archive
    Assert-NativeSuccess -Operation 'deterministic archive creation'
    $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $outputRoot 'SHA256SUMS.txt') `
        -Value "$archiveHash  $([System.IO.Path]::GetFileName($archive))" -Encoding ascii

    Write-Host "Standalone artifact: $artifact"
    Write-Host "Deterministic archive: $archive"
}
finally {
    Pop-Location
}
