"""Entry point for both Python and packaged desktop builds."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from image_tools import __version__
from image_tools.platform_utils import data_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="图迹 · 本地图片与设计源文件检索")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=None, help="索引与缩略图目录（不存放原文件）")
    parser.add_argument("--max-pages", type=int, default=20, help="每份 PDF/AI/TIFF 最多索引页数，默认 20")
    args = parser.parse_args(argv)
    if args.max_pages < 1:
        parser.error("--max-pages 必须大于 0")

    from PySide6.QtWidgets import QApplication, QMessageBox
    from image_tools.library import Library
    from image_tools.ui import MainWindow

    app = QApplication([sys.argv[0]])
    app.setApplicationName("图迹 Image Tools")
    app.setOrganizationName("ImageTools")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    storage = (args.data_dir or data_directory()).expanduser().resolve()
    try:
        storage.mkdir(parents=True, exist_ok=True)
        library = Library(storage / "library.sqlite3", storage / "thumbnails", max_pages=args.max_pages)
    except Exception as exc:
        QMessageBox.critical(None, "无法打开本地索引", f"{storage}\n\n{exc}\n\n可通过 --data-dir 指定可写目录。")
        return 1
    window = MainWindow(library)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
