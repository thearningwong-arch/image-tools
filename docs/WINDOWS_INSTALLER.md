# Windows 安装包

## 用户安装、升级、卸载

从仓库 Releases 下载 `ImageTools-版本号-win-x64-setup.exe`，双击按中文向导安装。“图迹”会出现在桌面、开始菜单及 Windows 系统卸载列表中。安装程序仅安装到当前用户的 `%LOCALAPPDATA%\Programs\ImageTools`，不要求管理员权限，不下载运行依赖。

直接运行新版安装包即可升级。安装器使用固定 AppId 和同一安装目录覆盖程序。索引保持现有位置 `%LOCALAPPDATA%\ImageTools`，与安装目录分开；卸载仅删除安装器管理的程序和快捷方式，不删除索引、缩略图、自定义索引目录或原素材。重新安装即可继续使用默认索引。若要清空个人索引，请退出图迹后自行删除索引目录。

Python、Qt、OpenCV、Pillow、NumPy、PSD/PSB 合成和 PDF 渲染依赖均随程序分发。EPS / 旧版 PostScript AI 的 Ghostscript 按 README 可选安装；安装包不捆绑该独立程序。安装包尚未代码签名。

## 构建

构建必须在 64 位 Windows 机器上进行：

1. 安装 64 位 Python 3.12（或项目支持的 3.13–3.15）。
2. 安装 Inno Setup 6.5 或更新版本。CI 固定使用 6.7.4；仓库附带来自 Inno Setup 官方固定提交的简体中文语言资源及许可，无需单独下载语言文件。
3. 若要覆盖 EPS/旧 AI 回归，安装 Ghostscript；CI 会安装。
4. 在仓库根目录运行 `build-windows.bat`；自动化运行用 `build-windows.bat --ci` 或 `./scripts/build_windows.ps1`。

输出 `dist\ImageTools-版本号-win-x64-setup.exe`，同时保留 `dist\ImageTools` 供调试。仅分发安装器即可，不需要用户复制程序文件夹。构建时需要下载依赖，安装和正常使用无需联网。

## 自动验收和发布

`.github/workflows/windows-installer.yml` 使用 Windows Server 2022 x64 和 Python 3.12，运行现有完整 pytest 套件并生成独立 EXE。另从固定提交 `c940d732868c11cf15cf264d8cc9a28c5f2045ea` 构建真实的 0.2.0 程序，以同一安装规则生成旧版本基线包。

`scripts/windows/test-installer.ps1` 在专用的 Windows 测试账号中安装基线包和候选包，检查升级、快捷方式和系统卸载信息、已安装 EXE 的图像检索，以及卸载和重装前后的索引保留。该脚本会安装和卸载图迹，不能在已有个人安装及索引的账号上运行。安装器和应用测试的日志由 Actions 附件保留；失败不会发布安装文件。

分支构建通过后在 Actions 下载 `ImageTools-windows-x64-installer` 附件。对与 `image_tools.__version__` 一致的 `v版本号` 标签，工作流通过全部验收后把安装器和 SHA-256 发布到 GitHub Releases，作为普通用户下载入口。首次启用 Actions 时，仓库必须允许执行工作流和 Release job 的 `contents: write` 权限。

CI 验证 Windows 构建机上的行为，并不能代替 Windows 10/11 用户电脑的原生资源管理器、公司网盘、特殊权限及大型素材库验收。请在实际目标电脑上检查安装向导显示、首次打开、扫描/检索、定位文件和导出名单。
