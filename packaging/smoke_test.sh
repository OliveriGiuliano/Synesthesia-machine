#!/usr/bin/env bash
# Clean-machine smoke runner for the packaged standalone application (Linux).
#
# Deliberately sanitizes PATH, copies the app to a temporary portable-install
# location, runs the compiled self-test, launches/closes/relaunches the Qt UI,
# checks native media/MIDI/audio files and development-file exclusions, deletes
# the portable copy, and verifies that a sentinel user document survives.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# Bundled inside the artifact the script sits in <artifact>/smoke; the app root
# is one level up. From the repository the default is the repository root.
app_dir="${1:-$(cd "$script_dir/.." && pwd -P)}"

headless=0
for argument in "$@"; do
    case "$argument" in
        --headless) headless=1 ;;
    esac
done

# The Linux standalone executable is SynesthesiaMachine.bin (see packaging/README.md).
executable="$app_dir/SynesthesiaMachine.bin"
video="$app_dir/smoke/h264-smoke.mp4"
if [[ ! -f "$executable" ]]; then
    echo "packaged executable not found: $executable" >&2
    exit 1
fi
if [[ ! -f "$video" ]]; then
    echo "bundled H.264 smoke video not found: $video" >&2
    exit 1
fi

# Development files must never ship in the release directory.
development_leaks="$(find "$app_dir" -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.pyo' \) -print -o \
    -path '*/tests/*' -print -o -path '*/.pytest_cache/*' -print -o -path '*/.ruff_cache/*' -print -o \
    -path '*/.venv/*' -print | sort -u || true)"
if [[ -n "$development_leaks" ]]; then
    echo "development-only files leaked into the release: $development_leaks" >&2
    exit 1
fi

# Required native components (Linux shared-library names). PortAudio is a system
# dependency on Linux and is deliberately not part of the artifact.
required_patterns=(*avcodec*.so* cv2*.so *rtmidi*.so)
for pattern in "${required_patterns[@]}"; do
    found="$(find "$app_dir" -type f -name "$pattern" -print -quit)"
    if [[ -z "$found" ]]; then
        echo "required packaged native component was not found: $pattern" >&2
        exit 1
    fi
done

smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/synmachine-smoke-XXXXXX")"
portable_copy="$smoke_root/Synesthesia Machine.dist"
data_home="$smoke_root/data"
documents_dir="${XDG_DOCUMENTS_DIR:-$HOME/Documents}"
mkdir -p "$data_home" "$documents_dir"
document_sentinel="$documents_dir/synmachine-smoke-$$.synmachine.json"
printf '{"release_smoke_sentinel":true}\n' > "$document_sentinel"

report_path="$smoke_root/packaged-smoke.json"

start_packaged_process() {
    # All positional arguments are forwarded to the packaged executable verbatim;
    # PATH is sanitized and per-user state is redirected into the smoke sandbox.
    local code
    env -i PATH="/usr/bin:/bin" HOME="$HOME" XDG_DATA_HOME="$data_home" \
        QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
        "$executable" "$@"
    code=$?
    if [[ "$code" -ne 0 ]]; then
        echo "packaged process exited with code $code" >&2
        exit "$code"
    fi
}

trap 'rm -f "$document_sentinel"; rm -rf "$smoke_root"' EXIT

portable_executable="$portable_copy/SynesthesiaMachine.bin"
portable_video="$portable_copy/smoke/h264-smoke.mp4"
rm -rf "$portable_copy"
cp -R "$app_dir" "$portable_copy"
executable="$portable_executable"
video="$portable_video"

start_packaged_process --packaged-smoke-report "$report_path" --h264-video "$video"
start_packaged_process --smoke-test
start_packaged_process --smoke-test

if ! grep -q '"passed": true' "$report_path"; then
    echo "packaged self-test reported a failure; inspect $report_path" >&2
    exit 1
fi

rm -rf "$portable_copy"
if [[ ! -f "$document_sentinel" ]]; then
    echo "deleting the portable application removed a user document" >&2
    exit 1
fi

echo "packaged smoke test passed. report: $report_path"
