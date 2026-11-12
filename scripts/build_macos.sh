#!/bin/bash
# Build native macOS artifacts; PyInstaller does not cross-compile from Linux.
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
    echo 'Mac 构建必须在 macOS 上运行。' >&2
    exit 1
fi
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
python_exe="${IMAGE_TOOLS_PYTHON:-}"
skip_install=0
skip_tests=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python) python_exe="$2"; shift 2 ;;
        --skip-install) skip_install=1; shift ;;
        --skip-tests) skip_tests=1; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
source "$repo/scripts/macos/python_env.sh"
python_exe="$(image_tools_find_python "$python_exe")"
"$python_exe" -c 'import sys, struct; assert (3,12) <= sys.version_info[:2] < (3,14), "Python 3.12–3.13 required by the verified macOS Qt runtime"; assert struct.calcsize("P") == 8, "64-bit Python required"'
if [[ ! -x .venv-macos/bin/python ]]; then
    "$python_exe" -m venv .venv-macos
fi
python_exe="$repo/.venv-macos/bin/python"
"$python_exe" -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,14), "Remove an incompatible .venv-macos and retry with Python 3.12–3.13"'
if [[ "$skip_install" == 0 ]]; then
    "$python_exe" -m pip install -r requirements-dev.txt
fi
"$python_exe" -m pip check
mkdir -p build/macos
if [[ "$skip_tests" == 0 ]]; then
    QT_QPA_PLATFORM=offscreen "$python_exe" -m pytest -q -ra --junitxml=build/macos/pytest.xml
fi
"$python_exe" scripts/macos/build_bundle.py
# Exercise the actual disk image, with the native Cocoa plugin and a clean cwd.
mkdir -p build/macos/validation
validation_dir="$(mktemp -d "$repo/build/macos/validation/run-XXXXXX")"
echo "验收记录：$validation_dir"
"$python_exe" scripts/macos/test_bundle.py --build-info build/macos/build-info.json --output-dir "$validation_dir"
