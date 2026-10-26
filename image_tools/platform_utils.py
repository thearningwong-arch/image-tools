"""Native file-manager integration. Never pass filenames through a shell."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def data_directory() -> Path:
    override = os.environ.get("IMAGE_TOOLS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "ImageTools"


def reveal_file(path: Path | str) -> None:
    """Open the containing directory and select the file where supported."""
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file():
        raise OSError("文件不存在或不是普通文件，请刷新索引。")
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "/select,", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)])
    else:
        # xdg-open has no portable select-file option.
        subprocess.Popen(["xdg-open", str(target.parent)])


def open_folder(path: Path | str) -> None:
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_dir():
        raise OSError("文件夹不存在，请确认磁盘或网络共享已连接。")
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
