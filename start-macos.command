#!/bin/bash
# Finder-friendly source launcher. Packaged users do not need this file/Python.
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
cd "$repo"
failed() {
    status=$?
    if [[ "$status" != 0 ]]; then
        echo "启动失败（退出码 $status），请查看上面的错误。"
        if [[ -t 0 ]]; then read -r -p '按回车键关闭…' _answer; fi
    fi
}
trap failed EXIT
if [[ "$(uname -s)" != Darwin ]]; then
    echo '此启动文件需要 macOS。' >&2
    exit 1
fi
python_exe="${IMAGE_TOOLS_PYTHON:-}"
source "$repo/scripts/macos/python_env.sh"
python_exe="$(image_tools_find_python "$python_exe")"
"$python_exe" -c 'import sys, struct; assert (3,12) <= sys.version_info[:2] < (3,14) and struct.calcsize("P") == 8, "Python 3.12–3.13 (64-bit) required by the verified macOS Qt runtime"'
if [[ ! -x .venv-macos/bin/python ]]; then
    "$python_exe" -m venv .venv-macos
fi
python_exe="$repo/.venv-macos/bin/python"
"$python_exe" -c 'import sys; assert sys.platform == "darwin" and (3,12) <= sys.version_info[:2] < (3,14), "Remove an incompatible .venv-macos and retry with Python 3.12–3.13"'
requirements_hash="$(shasum -a 256 requirements.txt | cut -d ' ' -f 1)"
hash_file="$repo/.venv-macos/.image-tools-requirements.sha256"
if [[ ! -f "$hash_file" ]] || [[ "$(cat "$hash_file")" != "$requirements_hash" ]]; then
    "$python_exe" -m pip install -r requirements.txt
    "$python_exe" -m pip check
    printf '%s\n' "$requirements_hash" > "$hash_file"
fi
"$python_exe" -m image_tools "$@"
