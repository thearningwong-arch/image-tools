"""Desktop workflows with real Qt signals and the persistent search engine."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import shutil
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QFrame, QLineEdit, QPushButton

from image_tools.library import Library
from image_tools import ui
from image_tools.rendering import SUPPORTED_EXTENSIONS


def wait_until(condition, timeout=15):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert condition(), "Qt background operation did not complete"


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    library = Library(tmp_path / "index" / "library.sqlite3", tmp_path / "index" / "thumbnails")
    desktop = ui.MainWindow(library)
    desktop.errors = []
    monkeypatch.setattr(desktop, "_show_error", lambda *args: desktop.errors.append(args))
    desktop.show()
    QTest.qWait(30)
    yield desktop
    if desktop._worker is not None:
        desktop._worker.cancel_event.set()
        wait_until(lambda: desktop._worker is None)
    desktop.close()
    app.processEvents()


def artwork():
    image = Image.new("RGB", (240, 180), "#e8e0d5")
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 10, 97, 150), fill="#176d87")
    draw.ellipse((80, 40, 220, 165), fill="#e0a44f")
    draw.line((15, 165, 215, 15), fill="#182c49", width=7)
    return image


def click(button):
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


def test_desktop_scan_search_actions_export_and_reopen(window, tmp_path, monkeypatch, app):
    root = tmp_path / "素材库"
    root.mkdir()
    source = root / "原图.png"
    artwork().save(source)
    shutil.copyfile(source, root / "=1+2.png")
    artwork().resize((120, 90)).save(root / "压缩版本.jpg", quality=45)
    query = tmp_path / "查询.png"
    shutil.copyfile(source, query)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(root))
    click(window.add_folder_button)
    assert window.folder_list.count() == 1
    click(window.scan_button)
    wait_until(lambda: window._worker is None)
    assert window.library.stats().files == 3
    assert not window.errors

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(query), ""))
    click(window.query_button)
    click(window.search_button)
    wait_until(lambda: window._worker is None)
    assert {Path(result.path).name for result in window._results} == {
        "原图.png", "=1+2.png", "压缩版本.jpg",
    }
    assert [result.match_type for result in window._results].count("identical") == 2
    assert window.export_button.isEnabled()
    assert not window.errors

    located, opened = [], []
    monkeypatch.setattr(ui, "reveal_file", located.append)
    monkeypatch.setattr(ui, "open_folder", opened.append)
    card = window.results_content.findChildren(QFrame, "resultCard")[0]
    displayed_path = card.findChild(QLineEdit).text()
    buttons = {button.text(): button for button in card.findChildren(QPushButton)}
    click(buttons["复制路径"])
    assert app.clipboard().text() == displayed_path
    click(buttons["资源管理器中定位"])
    click(buttons["打开文件夹"])
    assert located == [Path(displayed_path)]
    assert opened == [Path(displayed_path).parent]

    exported = tmp_path / "检索名单"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(exported), ""))
    click(window.export_button)
    with exported.with_suffix(".csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert "'=1+2.png" in {row["文件名"] for row in rows}
    assert {row["完整路径"] for row in rows} == {result.path for result in window._results}

    reopened = Library(window.library.database_path, window.library.cache_dir)
    assert reopened.roots() == [str(root.resolve())]
    assert len(reopened.search(query)) == 3
    assert reopened.scan([root]).unchanged == 3


def test_new_query_clears_previous_results_and_failed_search_cannot_export_them(window, tmp_path, monkeypatch):
    root = tmp_path / "images"
    root.mkdir()
    first, second = root / "first.png", tmp_path / "second.png"
    artwork().save(first)
    Image.new("RGB", (30, 40), "white").save(second)
    window.library.scan([root])
    window._set_query(first)
    click(window.search_button)
    wait_until(lambda: window._worker is None)
    assert window._results
    window._set_query(second)
    assert not window._results
    assert not window.export_button.isEnabled()
    assert window.results_summary.text() == "未显示检索结果"

    def unavailable(*args, **kwargs):
        raise OSError("读取磁盘失败")

    monkeypatch.setattr(window.library, "search", unavailable)
    click(window.search_button)
    wait_until(lambda: window._worker is None)
    assert window.errors == [("索引或检索失败", "读取磁盘失败")]
    assert not window._results and not window.export_button.isEnabled()


def test_cancel_search_is_not_an_error_and_mutations_are_blocked(window, tmp_path, monkeypatch):
    query = tmp_path / "query.png"
    artwork().save(query)
    window._set_query(query)
    started = threading.Event()
    calls = []

    def cancellable(query, *, cancel, **kwargs):
        calls.append(query)
        started.set()
        if cancel.wait(5):
            raise InterruptedError("检索已取消")
        raise RuntimeError("test operation was not cancelled")

    monkeypatch.setattr(window.library, "search", cancellable)
    click(window.search_button)
    wait_until(started.is_set)
    worker = window._worker
    assert not window.search_button.isEnabled()
    assert not window.add_folder_button.isEnabled()
    assert not window.query_button.isEnabled()
    window._search()
    window._add_folder(tmp_path)
    window._set_query(tmp_path / "missing.png")
    assert window._worker is worker
    assert not window._folders
    assert window._query == query
    assert calls == [query]
    click(window.cancel_button)
    wait_until(lambda: window._worker is None)
    assert not window.errors
    assert "已取消" in window.status_label.text()
    assert window.search_button.isEnabled()


def test_close_waits_for_worker_cancellation_without_destroying_thread(window, tmp_path, monkeypatch):
    query = tmp_path / "query.png"
    artwork().save(query)
    window._set_query(query)
    started = threading.Event()

    def cancellable(query, *, cancel, **kwargs):
        started.set()
        if cancel.wait(5):
            raise InterruptedError("检索已取消")
        return []

    monkeypatch.setattr(window.library, "search", cancellable)
    click(window.search_button)
    wait_until(started.is_set)
    assert not window.close()
    wait_until(lambda: window._worker is None and not window.isVisible())
    assert not window.errors


def test_supported_query_extensions_match_renderer(window, tmp_path):
    assert ui.IMAGE_SUFFIXES == SUPPORTED_EXTENSIONS
    for extension in (".jpe", ".jfif", ".avif"):
        path = tmp_path / f"query{extension}"
        artwork().save(path, format="JPEG")
        window._set_query(path)
        assert window._query == path
        assert f"*{extension}" in ui.FILE_FILTER
    assert not window.errors


def test_result_reveal_reports_missing_file_without_crashing(window, monkeypatch):
    def missing(path):
        raise FileNotFoundError("文件已移动，请更新索引")

    monkeypatch.setattr(ui, "reveal_file", missing)
    window._reveal("missing.png")
    assert window.errors == [("无法定位文件", "文件已移动，请更新索引")]


def test_unrenderable_query_shows_exact_only_limitation(window, tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    source = root / "damaged.png"
    source.write_bytes(b"damaged image content")
    query = tmp_path / "query.png"
    shutil.copyfile(source, query)
    assert window.library.scan([root]).failed == 1
    window._set_query(query)
    click(window.search_button)
    wait_until(lambda: window._worker is None)
    assert len(window._results) == 1
    assert window._results[0].match_type == "identical"
    assert window._results[0].thumbnail_path == ""
    assert "仅完成文件内容比对" in window.results_summary.text()
    assert "未进行相似图片检索" in window.status_label.text()
    assert not window.errors
