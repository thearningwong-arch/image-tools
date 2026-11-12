# Mac 版安装与构建

图迹在 Mac 上使用同一套本地图片检索与 SQLite 索引，不需要账号或在线识图服务。安装镜像内包含 Python、Qt、图片检索、PSD/PSB 和 PDF 兼容 AI 的运行依赖。EPS 与旧版 PostScript AI 需要另外安装可选的 Ghostscript。

## 选择与安装

在苹果菜单“关于本机”中查看芯片类型：Apple M 系列选择 `arm64`，Intel 处理器选择 `x86_64`。两个包分别在对应架构的 Mac 上构建，不是通用二进制。当前构建以 **macOS 14** 为起点，Intel OpenCV 运行包也要求 14.0。Mac 专用固定 Qt/PySide6 6.9.3，避免新版安装轮包中实际二进制要求更高系统的问题。构建还会检查随包二进制依赖；若依赖要求更新系统，会提高应用声明的最低版本。安装前查看构建报告 `build-info.json` 中的最低版本，以实际应用 `Info.plist` 为准。较旧 macOS 需要另行开发并验证兼容依赖版本，当前安装包不承诺支持。

1. 从本仓库的 Releases 或已通过验证的 Actions 运行下载对应架构的 DMG 与 `.sha256` 文件。Actions 产物需要先解压外层 ZIP。
2. 在终端进入下载目录，运行 `shasum -a 256 -c ImageTools-版本号-macos-架构.dmg.sha256` 核对文件。
3. 打开 DMG，将 `ImageTools.app` 拖到窗口中的“应用程序”快捷方式。若当前账号不能写入系统“应用程序”，可以复制到自己的 `~/Applications`。
4. 推出磁盘镜像，从安装目录打开应用。添加素材文件夹并建立索引，再选择或拖入查询图片。结果可在 Finder 中定位，或复制路径、打开文件夹和导出 CSV。

当前开发分支提供构建与验证流程；实际可下载版本与验收结果以仓库 Actions 和 Releases 为准。未经过原生构建的源码不等于已经发布 Mac 安装包。

默认构建使用临时签名（ad hoc），没有开发者身份，也没有经过 Apple 公证。macOS 可能阻止打开互联网下载的应用。先确认来源与校验值，再按系统“隐私与安全性”中的具体提示决定是否允许打开；不要关闭系统安全检查。正式分发可以使用 Developer ID 签名并提交公证，构建方法见下文。校验值用于检查下载完整性，不能替代开发者身份验证。

## 索引、升级与卸载

默认索引目录为 `~/Library/Application Support/ImageTools`，包含 `library.sqlite3`、缩略图及局部检索缓存。此目录位于应用包之外，不应放到只读 DMG 内。

升级时关闭图迹，复制新版应用替换旧版。使用同一 Mac 账号和默认目录即可沿用索引。卸载时关闭图迹并把应用移到废纸篓；这会保留索引，也不会删除素材。仅在确实需要清除本机索引时，关闭应用后删除上述索引目录。

自定义索引位置可以使用命令行参数，路径含空格时需加引号：

```bash
"/Applications/ImageTools.app/Contents/MacOS/ImageTools" --data-dir "$HOME/Library/Application Support/ImageTools-Custom"
```

也可设置 `IMAGE_TOOLS_DATA_DIR`。在终端中设置的环境变量通常不会传给从 Finder 双击启动的应用；需要使用自定义位置时，保持相同的启动方式与参数。该目录只存索引，不会接收或移动原素材。

## 文件访问权限与共享盘

首次选择桌面、文稿、下载、外置磁盘或网络卷时，macOS 可能询问访问权限。按实际需要授权；已拒绝时，可在系统设置“隐私与安全性”的相应权限项中检查图迹，源码运行时也可能显示为终端或 Python。通常无需为普通素材文件夹授予完全磁盘访问权限。

公司共享盘应先用同一 Mac 账号在 Finder“前往 → 连接服务器”中挂载，并完成所需的公司 VPN 与共享盘登录；再在图迹中选择 `/Volumes` 下已挂载、可读取的文件夹。图迹复用系统访问权限，不收集公司密码。网页型网盘不能作为本地目录直接扫描。

共享卷断开时，已有索引保留，但无法读取的源文件不会出现在检索结果中。重新挂载后可再次查询；路径变化时需添加新路径并更新索引。建议让索引保持在本机默认目录。网络超时仍可能延迟取消，实际公司共享盘与大素材库需在目标网络验收。

## Ghostscript（可选）

普通图片、PSD/PSB、PDF 和 PDF 兼容 AI 不需要 Ghostscript。需要 EPS 或旧版 PostScript AI 时，可使用已安装的 Homebrew：

```bash
brew install ghostscript
```

程序除搜索 `PATH` 外，也会检查 Apple 芯片 Homebrew 的 `/opt/homebrew/bin/gs`、Intel Homebrew 的 `/usr/local/bin/gs` 和 MacPorts 的 `/opt/local/bin/gs`，适配从 Finder 启动时较短的 `PATH`。非标准安装位置可明确指定可执行文件：

```bash
IMAGE_TOOLS_GHOSTSCRIPT="/自定义路径/bin/gs" \
  "/Applications/ImageTools.app/Contents/MacOS/ImageTools"
```

安装后重新扫描失败文件即可。Ghostscript 不随安装镜像分发。

## 从源码运行

在 macOS 14 或更新版本上安装与芯片架构一致的 64 位 Python 3.12 或 3.13，双击项目根目录的 `start-macos.command` 即可。Mac 固定 Qt 版本要求 Python 低于 3.14。启动器会寻找受支持的 Python，在项目内创建 `.venv-macos`，首次启动或固定依赖清单变化时联网安装依赖。以后双击同一文件即可使用；启动失败时终端会保留错误供检查。安装包用户无需此步骤。

命令行也可运行 `./start-macos.command --max-pages 100`，参数直接传给图迹；如需选择 Python，可设置 `IMAGE_TOOLS_PYTHON=/完整路径/python3`。手动建立环境运行的方式如下：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m image_tools
```

自定义页面上限与索引位置：

```bash
.venv/bin/python -m image_tools --max-pages 100 --data-dir "$HOME/Library/Application Support/ImageTools"
```

## 在 Mac 上构建

必须在对应架构的 Mac 上构建，不能在 Linux 或 Windows 上生成可运行的 Mac 应用。固定依赖来自 `requirements-dev.txt`；构建脚本运行回归测试、PyInstaller 打包和冻结应用自测，再生成 DMG 与校验文件。

```bash
bash scripts/build_macos.sh
```

也可双击项目根目录的 `build-macos.command`。首次构建需要联网下载依赖，使用生成的安装包不需要 Python。Apple 芯片和 Intel 两个架构应分别构建；不要用重命名文件的方式改变架构。

产物为 `dist/ImageTools.app`、`dist/ImageTools-版本号-macos-arm64.dmg`（或 `x86_64`）及对应 `.sha256`；构建信息和验证报告位于 `build/macos/`。Mac 工作流针对两种架构执行构建并上传安装镜像及报告，不自动把未验收产物当作正式发布。

脚本可以用 `--python /完整路径/python3` 选择已有 Python；`--skip-install` 适用于已安装固定依赖的构建环境，`--skip-tests` 会跳过源码回归，正式验收不应使用。构建脚本使用 `.venv-macos` 环境。独立执行包含只读挂载 DMG 的安装包验证：

```bash
validation_dir="$(mktemp -d "$PWD/build/macos/validation/manual-XXXXXX")"
.venv-macos/bin/python scripts/macos/test_bundle.py --build-info build/macos/build-info.json --output-dir "$validation_dir"
```

默认 ad hoc 签名用于本机构建与测试。已有可用 Developer ID Application 证书并配置 `notarytool` 钥匙串配置后，可执行：

```bash
MACOS_SIGNING_IDENTITY='Developer ID Application: 你的名称 (TEAMID)' \
MACOS_NOTARY_PROFILE='预先配置的钥匙串配置名' \
  bash scripts/build_macos.sh
```

不要把证书密码、Apple ID 密码或公证凭证写进仓库。`MACOS_NOTARY_PROFILE` 使用构建机已有的钥匙串配置；未提供真实 Developer ID 时不能公证。构建报告应明确记录实际签名与公证状态。

## 原生验收

自动化检查应验证源码回归、应用包架构与元数据、冻结程序依赖、真实 Qt 窗口、PNG/PSD/PSB/PDF/AI 的整图及局部检索，以及索引重开与原素材哈希。DMG 应能只读挂载、复制应用并再次运行自测。

发布前仍应在目标 Mac 检查：Finder 双击启动与权限提示；中文、空格及特殊字符路径；拖入查询图片；扫描与检索期间取消/退出；Finder 定位；CSV 导出；替换应用后索引保留；卸载应用后索引及素材保留；有需要时检查真实 EPS/旧版 AI、外置盘和公司共享盘。无显示服务器的 Linux 测试不能替代上述 macOS 原生行为。
