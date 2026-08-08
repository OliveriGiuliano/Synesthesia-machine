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
    if ($sourceStatus -and -not $AllowDirty) {
        throw 'Release builds require a clean Git worktree. Commit changes or pass -AllowDirty for a non-release verification build.'
    }

    uv sync --locked --group packaging
    uv run python -m tools.phase9_release check
    if (-not $SkipTests) {
        uv run check
        uv run pytest -q
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
    uv run pyside6-deploy -c (Join-Path $workRoot 'pysidedeploy.spec') -f -v
    if (-not (Test-Path -LiteralPath $artifact -PathType Container)) {
        throw "Standalone artifact was not created: $artifact"
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
    uv run python -m tools.phase9_release provenance `
        --artifact $artifact --output (Join-Path $artifact 'build-provenance.json')

    $version = uv run python -c 'from synesthesia_machine import __version__; print(__version__)'
    $archive = Join-Path $outputRoot "Synesthesia-Machine-$($version.Trim())-windows-x64.zip"
    uv run python -m tools.phase9_release archive --artifact $artifact --output $archive
    $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $outputRoot 'SHA256SUMS.txt') `
        -Value "$archiveHash  $([System.IO.Path]::GetFileName($archive))" -Encoding ascii

    Write-Host "Standalone artifact: $artifact"
    Write-Host "Deterministic archive: $archive"
}
finally {
    Pop-Location
}
