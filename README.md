# 图迹 · Image Tools

在自己的 Windows 10/11 电脑上，以图片查找本地的相同图片、外观相近图片，以及画面匹配的 PSD、PSB、AI、EPS、PDF 源文件候选。结果包含缩略图、文件名、完整路径和匹配页，可直接在资源管理器中定位文件。

图片、缩略图和索引均留在本机。首次安装下载软件依赖；使用时不需要账号、联网服务或 API 密钥。

## 在 Windows 上使用

1. 安装 **64 位 Python 3.12–3.15**（建议 3.12/3.13），安装时勾选 **Add Python to PATH**。
2. 把本项目解压到本机文件夹，双击 **`start-windows.bat`**。首次启动自动建立独立环境并下载依赖，需要联网。
3. 添加存放图片/设计文件的文件夹，点击建立或更新索引，等待扫描完成。
4. 选择或拖入一张参考图片，点击搜索。可调整相似度门槛，或导出结果 CSV。
5. 点击结果的“定位文件”按钮，即可在资源管理器中打开所在目录并选中文件。也可以复制路径或打开文件夹。

以后双击同一启动文件即可使用。文件有新增、修改、移动或删除时，需要重新扫描；没有变化的文件会跳过。第一次扫描大型素材库可能需要较长时间，可取消后再继续。取消会等待当前预览处理步骤结束，复杂源文件可能需要稍等。

若使用命令行：

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m image_tools
```

## 支持范围

| 文件类型 | 检索方式与条件 |
| --- | --- |
| JPG/JPEG、PNG、WebP、BMP、GIF、TIFF 等 | 读取图像预览；按 EXIF 纠正方向，透明背景按白色处理 |
| PSD、PSB | 优先读取已保存的合成预览；必要时尝试图层合成。建议在 Photoshop 保存时开启“最大兼容性” |
| PDF | 渲染页面预览，不需要安装 Acrobat |
| AI | 含 PDF 兼容数据的 AI 直接渲染；建议 Illustrator 保存时勾选“创建 PDF 兼容文件” |
| EPS、旧版 PostScript AI | 需要额外安装 Ghostscript；未安装时会列出明确失败原因 |

PDF、PDF 兼容 AI 和 TIFF 默认最多索引前 **20 页**，普通动图取第一帧。可用以下方式增加页数，随后重新扫描：

```powershell
.\start-windows.bat --max-pages 100
```

无法读取、加密、损坏或超过预览资源限制的文件会进入扫描问题列表，不能参加外观相似检索；仍可通过完全相同文件的 SHA-256 查找副本。预览器会限制约 1 亿像素以上的超大图片/PSD 解码，Pillow 的内存保护还可能提前拒绝处理；可单独导出较小预览后检索。PSB 文件体积本身不代表一定可读取，仍受图像尺寸与文档特性限制。部分 Photoshop 效果、颜色配置、链接资源和复杂 AI/EPS 的预览可能与原软件不同。

**安装 Ghostscript（可选）**：从 [Ghostscript 官网](https://ghostscript.com/releases/gsdnld.html) 安装 Windows 64 位版本。程序会尝试从 PATH 或标准安装目录发现 `gswin64c.exe`。自定义安装位置可在启动前设置：

```powershell
$env:IMAGE_TOOLS_GHOSTSCRIPT = 'C:\Program Files\gs\gs10.xx.x\bin\gswin64c.exe'
.venv\Scripts\python.exe -m image_tools
```

安装后重新扫描失败文件即可，不需要重建整个素材库。

## 如何理解结果

- **文件完全相同**：文件的 SHA-256 相同，即字节内容一致；文件名可以不同。
- **预览一致**：本工具渲染出的可见画面像素一致，文件格式、图层或元数据可能不同。
- **外观相似**：使用感知哈希、多尺度裁剪和颜色信息进行近似匹配。可找到缩放、压缩、轻微改色及部分轻度裁剪版本。分数是排序指标，**不是匹配概率**。

默认门槛为 70，可降低门槛寻找更多候选。程序按文件列出最佳匹配页，最多显示 100 个结果。与 PSD/AI 等源文件的画面相似，只能说明它是候选，不能证明该图片一定由此源文件导出。这里没有“同类物体/同种风格”的语义模型；严重裁剪、旋转、大面积改版或嵌在复杂版面里的小图可能无法匹配，纯色和简单图形也可能出现误报。

## 索引与原文件

- Windows 默认索引位置：`%LOCALAPPDATA%\ImageTools`，包含 SQLite 数据库及 JPEG 缩略图。
- 添加和移除索引文件夹均不会移动、修改或删除原文件。
- 支持多个文件夹和重叠目录，结果不会因重复扫描同一路径而重复列出。
- 磁盘或网络共享离线时保留已有索引；扫描问题列表会提示不可访问。失效路径无法在资源管理器中打开，磁盘恢复后可重新扫描。
- 索引含文件路径和缩略图，需要清除时先关闭程序，再删除索引目录。无需删除素材。
- 可使用 `--data-dir D:\ImageToolsIndex`，或环境变量 `IMAGE_TOOLS_DATA_DIR` 更改索引位置。

扫描基于文件大小和纳秒修改时间判断变更，未做文件系统实时监听。搜索会对已索引预览做本地比较；当前版本适合个人素材库，超大规模库还需要向量索引和性能优化。

## 打包为 EXE

在 **Windows 本机**双击 `build-windows.bat`，构建成功后运行 `dist\ImageTools\ImageTools.exe`。分发时需要复制整个 `dist\ImageTools` 文件夹。该文件夹内含 Python 运行时，使用者无需另装 Python；Ghostscript 仍是独立的可选组件。

Windows EXE 必须在 Windows 上构建。云端 Linux 的验证不等于已验证 Windows 安装、原生资源管理器行为或 EXE 打包；这些仍需在目标 PC 上验收。

## 开发与验证

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m image_tools
```

Linux 无显示服务器时使用 `QT_QPA_PLATFORM=offscreen` 运行 UI 自动化检查。运行桌面界面需本地显示会话。macOS 使用 Finder 定位，Linux 打开所在目录；主要目标平台为 Windows。

代码分为 `rendering.py`（文档预览）、`features.py`（视觉描述与比较）、`library.py`（SQLite 增量索引与检索）、`ui.py`（桌面交互）和 `platform_utils.py`（文件管理器操作）。测试包括真正的文档渲染、索引更新/删除/取消、相似检索和桌面工作流。

本次验证为 **54 项测试通过**。详细覆盖范围、已修复问题与 Windows 待验收事项见 [开发与验收记录](docs/VALIDATION.md)。
