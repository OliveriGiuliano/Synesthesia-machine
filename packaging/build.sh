#!/usr/bin/env bash
# Linux x86_64 mirror of packaging/build.ps1.
#
# Builds the Nuitka standalone directory application, bundles notices and licence
# texts, records per-file provenance, and creates a fixed-order/fixed-timestamp ZIP
# plus SHA256SUMS.txt. -SkipTests/--skip-tests and --allow-dirty exist only for
# local diagnosis; artifacts produced with either flag are not release candidates.
set -euo pipefail

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
output_root="$repository/packaging/out"
work_root="$repository/packaging/work"
deployment_root="$repository/deployment"
artifact="$output_root/Synesthesia Machine.dist"

skip_tests=0
allow_dirty=0
for argument in "$@"; do
    case "$argument" in
        --skip-tests) skip_tests=1 ;;
        --allow-dirty) allow_dirty=1 ;;
        *) echo "unknown option: $argument" >&2; exit 2 ;;
    esac
done

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "release builds require Linux x86_64, found $(uname -s) $(uname -m)" >&2
    exit 1
fi

cd "$repository"

source_status="$(git status --porcelain --untracked-files=all)"
if [[ -n "$source_status" && "$allow_dirty" -ne 1 ]]; then
    echo "release builds require a clean Git worktree. Commit changes or pass --allow-dirty for a non-release verification build." >&2
    exit 1
fi

uv sync --locked --group packaging
uv run python -m tools.release check
if [[ "$skip_tests" -ne 1 ]]; then
    QT_QPA_PLATFORM=offscreen uv run check
    QT_QPA_PLATFORM=offscreen uv run pytest -q
fi

rm -rf "$output_root" "$work_root" "$deployment_root"
mkdir -p "$output_root" "$work_root"
cp "$repository/pysidedeploy.linux.spec" "$work_root/pysidedeploy.spec"

export PYTHONHASHSEED=0
export SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"

uv run pyside6-deploy -c "$work_root/pysidedeploy.spec" -f -v
# pyside6-deploy names the Linux standalone executable with a .bin suffix
# (Windows produces SynesthesiaMachine.exe).
executable="$artifact/SynesthesiaMachine.bin"
if [[ ! -f "$executable" ]]; then
    echo "standalone executable was not created: $executable" >&2
    exit 1
fi

# Linux resolves the PortAudio runtime from the system (libportaudio2); nothing
# is bundled. Machines without it pass the smoke with the audio check skipped.

smoke_dir="$artifact/smoke"
mkdir -p "$smoke_dir"
cp "$repository/packaging/smoke_test.sh" "$smoke_dir/smoke_test.sh"
cp "$repository/packaging/fixtures/h264-smoke.mp4" "$smoke_dir/h264-smoke.mp4"
cp "$repository/LICENSE-or-NOTICE.md" "$artifact/"
cp "$repository/packaging/RUNNING.md" "$artifact/"
cp "$repository/packaging/licensing-review.md" "$artifact/"

uv run python -m tools.release inventory \
    --output "$artifact/dependency-inventory.json" \
    --notices "$artifact/THIRD_PARTY_NOTICES.md" \
    --licenses "$artifact/licenses"
uv run python -m tools.release provenance \
    --artifact "$artifact" --output "$artifact/build-provenance.json"

version="$(uv run python -c 'from synesthesia_machine import __version__; print(__version__)')"
archive="$output_root/Synesthesia-Machine-${version}-linux-x64.zip"
uv run python -m tools.release archive --artifact "$artifact" --output "$archive"
archive_hash="$(sha256sum "$archive" | awk '{print $1}')"
printf '%s  %s\n' "$archive_hash" "$(basename "$archive")" > "$output_root/SHA256SUMS.txt"

echo "Standalone artifact: $artifact"
echo "Deterministic archive: $archive"
