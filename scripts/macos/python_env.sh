#!/bin/bash
# Shared Python discovery for Terminal and Finder's limited login environment.
image_tools_find_python() {
    local candidate
    if [[ -n "${1:-}" ]]; then
        printf '%s\n' "$1"
        return 0
    fi
    for candidate in python3.15 python3.14 python3.13 python3.12 python3 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.15/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys, struct; assert (3,12) <= sys.version_info[:2] < (3,16) and struct.calcsize("P") == 8' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo '请先从 https://www.python.org/downloads/macos/ 安装 64 位 Python 3.12–3.15，再重新双击启动。' >&2
    return 1
}
