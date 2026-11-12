#!/bin/bash
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
if ! /bin/bash "$repo/scripts/build_macos.sh" "$@"; then
    echo '构建失败，请查看上面的错误。'
    if [[ -t 0 ]]; then read -r -p '按回车键关闭…' _answer; fi
    exit 1
fi
echo 'Mac 安装镜像与校验文件已生成在 dist 文件夹。'
if [[ -t 0 ]]; then read -r -p '按回车键关闭…' _answer; fi
