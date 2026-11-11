"""Explicit, isolated acceptance test for the frozen Windows executable.

This is invoked only by the hidden --self-test-report developer switch, with an
explicit test data directory. It exercises the dependencies from inside the
packaged executable, rather than importing them from a development Python.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import traceback

from image_tools import __version__


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exercise(storage: Path) -> dict:
    import numpy as np
    import cv2
    import pypdfium2
    from PIL import Image, ImageDraw
    from psd_tools import PSDImage
    from psd_tools.constants import Compression
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from image_tools.library import Library
    from image_tools.ui import MainWindow

    storage.mkdir(parents=True, exist_ok=True)
    assets = storage / "self-test-assets"
    queries = storage / "self-test-queries"
    first_run = not assets.exists()
    if first_run:
        assets.mkdir()
        queries.mkdir()
        rng = np.random.default_rng(304)
        image = Image.fromarray(rng.integers(205, 241, (600, 800, 3), dtype=np.uint8))
        draw = ImageDraw.Draw(image)
        for number in range(100):
            x, y = (int(v) for v in rng.integers((0, 0), (735, 550)))
            color = tuple(int(v) for v in rng.integers(20, 190, 3))
            width, height = (int(v) for v in rng.integers(12, 65, 2))
            draw.ellipse((x, y, x + width, y + height), fill=color)
            draw.text((x, y + height), f"Photo {number:03}", fill="#19273c")
        image.save(assets / "完整图片.png")
        image.save(queries / "整图.png")
        image.crop((453, 299, 697, 503)).resize((383, 320)).save(queries / "局部截图.jpg", quality=75)
        for suffix, version in (("psd", 1), ("psb", 2)):
            document = PSDImage.frompil(image, compression=Compression.RAW)
            document._record.header.version = version
            document.save(assets / f"设计源文件.{suffix}")
        Image.new("RGB", image.size, "white").save(
            assets / "画册.pdf", save_all=True, append_images=[image], resolution=72)
        (assets / "兼容设计.ai").write_bytes((assets / "画册.pdf").read_bytes())

    expected = {"完整图片.png", "设计源文件.psd", "设计源文件.psb", "画册.pdf", "兼容设计.ai"}
    _require({p.name for p in assets.iterdir()} == expected, "Persistent source fixtures are missing or changed")
    originals = {p.name: _hash(p) for p in assets.iterdir()}
    database = storage / "library.sqlite3"
    index_existed = database.is_file()
    library = Library(database, storage / "thumbnails")
    report = library.scan([assets])
    _require(report.failed == 0, f"Document scan failed: {report}")
    _require(library.stats().files == 5, "Expected five indexed files")
    _require(library.stats().pages == 7, "Expected PDF/AI second-page previews")
    if not first_run:
        _require(report.unchanged == 5, "Existing index was rebuilt rather than retained")
    whole = {Path(result.path).name: result for result in library.search(queries / "整图.png")}
    _require(set(whole) == expected, f"Whole-image search mismatch: {set(whole)}")
    _require(whole["完整图片.png"].match_type == "identical", "Exact-file matching failed")
    _require(whole["画册.pdf"].page == whole["兼容设计.ai"].page == 2, "Wrong PDF/AI page")
    partial = {Path(result.path).name: result for result in library.search(
        queries / "局部截图.jpg", mode="partial", min_score=85)}
    _require(set(partial) == expected, f"Partial-image search mismatch: {set(partial)}")
    region = np.array((453 / 800, 299 / 600, 697 / 800, 503 / 600))
    for result in partial.values():
        _require(result.match_region is not None, "Partial match has no location")
        _require(float(np.max(np.abs(np.array(result.match_region) - region))) < 0.025,
                 f"Incorrect partial match location: {result.match_region}")
        preview = library.match_preview(result)
        _require(preview.width >= 800 and preview.height >= 600, "Match preview could not be loaded")
    library.close()
    reopened = Library(database, storage / "thumbnails")
    _require(reopened.stats().files == 5, "Index did not persist after reopening")
    _require(len(reopened.search(queries / "整图.png")) == 5, "Reopened index cannot search")
    _require({p.name: _hash(p) for p in assets.iterdir()} == originals, "Source files were modified")

    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    window = MainWindow(reopened)
    window.show()
    observed = []

    def inspect() -> None:
        observed.append(window.isVisible() and window.library.stats().files == 5)
        window.close()
        application.quit()

    QTimer.singleShot(150, inspect)
    _require(application.exec() == 0, "Qt event loop failed")
    _require(observed == [True], "Real application window did not become visible")
    reopened.close()
    return {
        "index_existed": index_existed,
        "first_run": first_run,
        "scan_indexed": report.indexed,
        "scan_unchanged": report.unchanged,
        "files": 5,
        "pages": 7,
        "whole_matches": len(whole),
        "partial_matches": len(partial),
        "qt_window": True,
        "dependencies": {"numpy": np.__version__, "opencv": cv2.__version__,
                         "pdfium": str(pypdfium2.PYPDFIUM_INFO.version)},
        "source_sha256": originals,
    }


def run(storage: Path, report_path: Path) -> int:
    report = {
        "status": "failed", "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": str(Path(sys.executable).resolve()),
        "storage": str(storage.expanduser().resolve()),
    }
    try:
        report.update(_exercise(storage.expanduser().resolve()))
        report["status"] = "passed"
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "passed" else 1
