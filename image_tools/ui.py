"""Chinese desktop interface for the local image catalogue."""
from __future__ import annotations

import csv
from pathlib import Path
import threading
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QSplitter, QTextEdit, QVBoxLayout, QWidget, QMainWindow,
)

from .library import Library
from .models import ScanProgress, ScanReport, SearchResult
from .platform_utils import open_folder, reveal_file
from .rendering import SUPPORTED_EXTENSIONS


IMAGE_SUFFIXES = SUPPORTED_EXTENSIONS
FILE_FILTER = "图片与设计文件 (" + " ".join(f"*{suffix}" for suffix in sorted(IMAGE_SUFFIXES)) + ");;所有文件 (*)"
MATCH_LABELS = {"identical": "文件完全相同", "preview": "预览一致", "similar": "外观相似"}

STYLE = """
QMainWindow, QWidget#root { background: #f3f6f7; color: #142d35; }
QWidget { font-family: 'Microsoft YaHei UI', 'Noto Sans CJK SC', sans-serif; font-size: 13px; }
QLabel { color: #142d35; background: transparent; }
QLabel#brand { font-size: 25px; font-weight: 700; }
QLabel#brandMark { background: #087f78; color: white; border-radius: 13px; font-size: 26px; font-weight: 700; }
QLabel#subtle { color: #677c82; }
QLabel#eyebrow { color: #087f78; font-weight: 700; font-size: 11px; }
QLabel#heading { font-size: 17px; font-weight: 700; }
QLabel#stat { color: #087f78; background: #e0f1ed; border-radius: 12px; padding: 7px 12px; }
QFrame#panel, QFrame#resultCard { background: white; border: 1px solid #dce5e7; border-radius: 12px; }
QFrame#queryPanel { background: #f0f8f6; border: 1px dashed #8cb9af; border-radius: 10px; }
QLabel#thumbnail { background: #edf1f2; border: none; border-radius: 8px; color: #7c8d91; }
QLabel#score { color: #087f78; font-size: 22px; font-weight: 700; }
QLabel#tag { color: #526a70; background: #eef3f4; border-radius: 4px; padding: 3px 7px; font-size: 11px; }
QPushButton { background: #fff; color: #234851; border: 1px solid #ccdadd; border-radius: 7px; padding: 8px 12px; }
QPushButton:hover { background: #edf6f4; border-color: #7caaa1; }
QPushButton:pressed { background: #dfeee9; }
QPushButton:disabled { background: #f1f4f5; border-color: #e2e8ea; color: #a2b0b3; }
QPushButton#primary { background: #087f78; color: white; border: 1px solid #087f78; font-weight: 700; padding: 11px 14px; }
QPushButton#primary:hover { background: #056b65; }
QPushButton#primary:disabled { background: #aecbc5; border-color: #aecbc5; color: #f5faf8; }
QPushButton#quiet { background: transparent; border: none; color: #557780; padding: 4px 6px; }
QPushButton#quiet:hover { background: #e9f1f2; }
QListWidget { background: #f7f9f9; border: 1px solid #e3eaec; border-radius: 7px; padding: 4px; outline: 0; }
QListWidget::item { padding: 8px 4px; border-radius: 4px; }
QListWidget::item:selected { background: #d8eee7; color: #14594e; }
QListWidget::item:hover { background: #eaf3f0; }
QLineEdit { border: none; background: transparent; color: #60787f; padding: 0; selection-background-color: #b7e2d8; }
QScrollArea { border: none; background: transparent; }
QWidget#resultsContent, QWidget#sidebarContent { background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 3px 0; }
QScrollBar::handle:vertical { background: #c5d3d7; border-radius: 4px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSlider::groove:horizontal { height: 5px; background: #dce7e5; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #087f78; border-radius: 2px; }
QSlider::handle:horizontal { background: #087f78; border: 3px solid white; width: 15px; height: 15px; margin: -7px 0; border-radius: 10px; }
QProgressBar { background: #e0eae7; border: none; height: 5px; border-radius: 2px; }
QProgressBar::chunk { background: #087f78; border-radius: 2px; }
QTextEdit { background: #f8fafb; border: 1px solid #dae4e7; border-radius: 6px; padding: 8px; }
QToolTip { background: #203c43; color: #fff; border: 0; padding: 6px; }
"""


class _Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(object)

    def __init__(self, operation: Callable, parent: QWidget):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            self.succeeded.emit(self.operation(self.cancel_event, self.progress.emit))
        except Exception as exc:
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc) or type(exc).__name__)


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _panel() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(9)
    return frame, layout


def _scaled_pixmap(path: Path, size: QSize) -> QPixmap:
    """Ask Qt's decoder for a bounded preview instead of loading a full photograph."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    source_size = reader.size()
    if source_size.isValid():
        reader.setScaledSize(source_size.scaled(size, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image).scaled(
        size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
    )


def _human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    return str(value)


class MainWindow(QMainWindow):
    """One local catalogue, one cancellable background operation at a time."""

    def __init__(self, library: Library):
        super().__init__()
        self.library = library
        self._worker: _Worker | None = None
        self._job_kind: str | None = None
        self._close_requested = False
        self._query: Path | None = None
        self._results: list[SearchResult] = []
        self._last_scan_errors: list[tuple[str, str]] = []
        self._folders: list[str] = list(self.library.roots())
        self.setWindowTitle("图迹 · 本地图片与源文件检索")
        self.resize(1240, 870)
        self.setMinimumSize(1000, 700)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._refresh_folders()
        self._refresh_stats()
        self._empty_results("从一张图片，找到本机里的文件", "添加素材文件夹并建立索引，再拖入一张图片开始查找。")
        self._set_busy(False)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(20)

        header = QHBoxLayout()
        mark = _label("图", "brandMark")
        mark.setFixedSize(52, 52)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("图迹", "brand"))
        titles.addWidget(_label("本地图片与源文件检索", "subtle"))
        header.addLayout(titles)
        header.addStretch()
        header.addWidget(_label("离线运行 · 文件留在本机", "stat"))
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(18)
        sidebar_container = QWidget()
        sidebar_container.setObjectName("sidebarContent")
        sidebar_container.setMinimumWidth(298)
        sidebar_container.setMaximumWidth(380)
        sidebar_outer = QVBoxLayout(sidebar_container)
        sidebar_outer.setContentsMargins(0, 0, 0, 0)
        sidebar_outer.setSpacing(10)
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar = QWidget()
        sidebar.setObjectName("sidebarContent")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 2, 0)
        sidebar_layout.setSpacing(14)
        sidebar_scroll.setWidget(sidebar)
        sidebar_outer.addWidget(sidebar_scroll, 1)
        splitter.addWidget(sidebar_container)

        folder_panel, folder_layout = _panel()
        folder_layout.addWidget(_label("01 / 建立素材库", "eyebrow"))
        folder_layout.addWidget(_label("素材文件夹", "heading"))
        folder_layout.addWidget(_label("选择图片和设计源文件所在的文件夹，包含其子文件夹。", "subtle", True))
        self.folder_list = QListWidget()
        self.folder_list.setMinimumHeight(66)
        self.folder_list.setMaximumHeight(90)
        self.folder_list.currentRowChanged.connect(lambda _: self._update_buttons())
        folder_layout.addWidget(self.folder_list)
        folder_actions = QHBoxLayout()
        self.add_folder_button = QPushButton("＋ 添加文件夹")
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.remove_folder_button = QPushButton("移除")
        self.remove_folder_button.setToolTip("仅移除该目录的检索索引，保留原文件。")
        self.remove_folder_button.clicked.connect(self._remove_folder)
        folder_actions.addWidget(self.add_folder_button, 1)
        folder_actions.addWidget(self.remove_folder_button)
        folder_layout.addLayout(folder_actions)
        self.scan_button = QPushButton("建立 / 更新索引")
        self.scan_button.clicked.connect(self._scan)
        folder_layout.addWidget(self.scan_button)
        self.library_summary = _label("", "subtle", True)
        folder_layout.addWidget(self.library_summary)
        self.failures_button = QPushButton("查看无法读取的文件")
        self.failures_button.setObjectName("quiet")
        self.failures_button.clicked.connect(self._show_failures)
        folder_layout.addWidget(self.failures_button)
        sidebar_layout.addWidget(folder_panel)

        query_panel, query_layout = _panel()
        query_layout.addWidget(_label("02 / 以图找文件", "eyebrow"))
        query_header = QHBoxLayout()
        query_header.addWidget(_label("查询图片", "heading"), 1)
        self.query_button = QPushButton("选择图片")
        self.query_button.clicked.connect(self._choose_query)
        query_header.addWidget(self.query_button)
        query_layout.addLayout(query_header)
        drop_frame = QFrame()
        drop_frame.setObjectName("queryPanel")
        drop_layout = QVBoxLayout(drop_frame)
        drop_layout.setContentsMargins(12, 12, 12, 12)
        self.query_preview = _label("拖入一张图片\n或点击下方选择", "subtle", True)
        self.query_preview.setMinimumHeight(80)
        self.query_preview.setMaximumHeight(96)
        self.query_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(self.query_preview)
        self.query_name = _label("支持图片、PSD、PSB、AI、EPS、PDF", "subtle", True)
        self.query_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(self.query_name)
        query_layout.addWidget(drop_frame)
        sidebar_layout.addWidget(query_panel)
        controls_panel, controls_layout = _panel()
        similarity_row = QHBoxLayout()
        similarity_row.addWidget(_label("最低外观相似度"))
        similarity_row.addStretch()
        self.threshold_label = _label("70", "eyebrow")
        similarity_row.addWidget(self.threshold_label)
        controls_layout.addLayout(similarity_row)
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(0, 100)
        self.threshold.setValue(70)
        self.threshold.valueChanged.connect(lambda value: self.threshold_label.setText(str(value)))
        controls_layout.addWidget(self.threshold)
        controls_layout.addWidget(_label("分数并非概率；降低门槛可找到更多候选。", "subtle", True))
        self.search_button = QPushButton("开始检索")
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self._search)
        controls_layout.addWidget(self.search_button)
        sidebar_outer.addWidget(controls_panel)
        page_limit = getattr(self.library, "max_pages", 20)
        sidebar_layout.addWidget(_label(
            f"每份文档最多检索前 {page_limit} 页，可用 --max-pages 调整。源文件通过预览匹配，外观接近不代表已确认原始来源。",
            "subtle", True,
        ))
        sidebar_layout.addStretch()

        results_panel = QWidget()
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(12)
        results_heading = QHBoxLayout()
        heading_copy = QVBoxLayout()
        heading_copy.setSpacing(4)
        heading_copy.addWidget(_label("检索结果", "heading"))
        self.results_summary = _label("先建立索引，再选择查询图片", "subtle", True)
        heading_copy.addWidget(self.results_summary)
        results_heading.addLayout(heading_copy, 1)
        self.export_button = QPushButton("导出名单")
        self.export_button.clicked.connect(self._export_results)
        results_heading.addWidget(self.export_button)
        results_layout.addLayout(results_heading)
        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_content = QWidget()
        self.results_content.setObjectName("resultsContent")
        self.results_stack = QVBoxLayout(self.results_content)
        self.results_stack.setContentsMargins(0, 0, 4, 0)
        self.results_stack.setSpacing(10)
        self.results_scroll.setWidget(self.results_content)
        results_layout.addWidget(self.results_scroll, 1)
        results_layout.addWidget(_label("优先展示完全相同的文件，然后按外观相似度排序 · 每次最多 100 项", "subtle", True))
        splitter.addWidget(results_panel)
        splitter.setSizes([310, 860])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.status_label = _label("就绪 · 首次使用请添加素材文件夹", "subtle")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        footer.addWidget(self.status_label, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setFixedHeight(6)
        footer.addWidget(self.progress_bar)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("quiet")
        self.cancel_button.clicked.connect(self._cancel)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)

    def _status(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setToolTip(message)

    def _refresh_folders(self) -> None:
        self.folder_list.clear()
        for folder in self._folders:
            self.folder_list.addItem(folder)
            self.folder_list.item(self.folder_list.count() - 1).setToolTip(folder)
        self._update_buttons()

    def _refresh_stats(self) -> None:
        stats = self.library.stats()
        self.library_summary.setText(f"{stats.files:,} 个文件 · {stats.pages:,} 张预览 · {stats.failed:,} 个读取问题")
        self.failures_button.setVisible(bool(stats.failed or self._last_scan_errors))

    def _update_buttons(self) -> None:
        if not hasattr(self, "scan_button"):
            return
        busy = self._worker is not None
        self.scan_button.setEnabled(bool(self._folders) and not busy)
        self.remove_folder_button.setEnabled(self.folder_list.currentRow() >= 0 and not busy)
        self.search_button.setEnabled(self._query is not None and not busy)
        self.export_button.setEnabled(bool(self._results) and not busy)

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.add_folder_button, self.query_button, self.folder_list, self.threshold, self.failures_button):
            widget.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        self.progress_bar.setVisible(busy)
        self._update_buttons()

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择要检索的素材文件夹")
        if folder:
            self._add_folder(Path(folder))

    def _add_folder(self, folder: Path) -> None:
        if self._worker is not None:
            return
        value = str(folder.resolve())
        if value not in self._folders:
            self._folders.append(value)
            self._refresh_folders()
            self._status("文件夹已添加，请点击“建立 / 更新索引”。")

    def _remove_folder(self) -> None:
        if self._worker is not None:
            return
        row = self.folder_list.currentRow()
        if row < 0:
            return
        folder = self._folders[row]
        try:
            self.library.remove_root(Path(folder))
        except Exception as exc:
            self._show_error("无法移除索引", str(exc))
            return
        self._folders.pop(row)
        self._refresh_folders()
        self._refresh_stats()
        self._results = []
        self._empty_results("素材库已更新", "选择查询图片，重新检索当前素材库。")
        self._update_buttons()
        self._status("已移除该文件夹的索引，原文件保留。")

    def _choose_query(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "选择一张要查找的图片", "", FILE_FILTER)
        if filename:
            self._set_query(Path(filename))

    def _set_query(self, path: Path) -> None:
        if self._worker is not None:
            return
        if not path.is_file():
            self._show_error("无法选择图片", "文件不存在或无法读取。")
            return
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            self._show_error("暂不支持此文件", "请选择常见图片或 PSD、PSB、AI、EPS、PDF 文件。")
            return
        self._query = path.resolve()
        self._results = []
        self._empty_results("查询图片已更新", "点击“开始检索”，查找与当前图片匹配的文件。")
        self.query_name.setText(path.name)
        self.query_name.setToolTip(str(self._query))
        self.query_preview.clear()
        if path.suffix.lower() in {".psd", ".psb", ".ai", ".eps", ".pdf"}:
            self.query_preview.setText(f"{path.suffix[1:].upper()} 源文件\n检索时读取第 1 页预览")
        else:
            pixmap = _scaled_pixmap(path, QSize(230, 94))
            if pixmap.isNull():
                self.query_preview.setText("图片已选择\n将在检索时读取")
            else:
                self.query_preview.setPixmap(pixmap)
        self._status(f"已选择 {path.name}，点击“开始检索”。")
        self._update_buttons()

    def _scan(self) -> None:
        if self._worker is not None or not self._folders:
            return
        folders = [Path(folder) for folder in self._folders]
        self.progress_bar.setRange(0, 0)
        self._status("正在发现文件并建立索引…")
        self._start_job("scan", lambda cancel, progress: self.library.scan(folders, progress=progress, cancel=cancel))

    def _search(self) -> None:
        if self._query is None or self._worker is not None:
            return
        query, minimum = self._query, self.threshold.value()
        self._results = []
        self._empty_results("正在检索本地素材库", "正在比较图片预览，请等待结果。")
        self.progress_bar.setRange(0, 0)
        self._status("正在比较图片与本地索引…")
        self._start_job("search", lambda cancel, progress: self.library.search(query, min_score=minimum, limit=100, cancel=cancel))

    def _start_job(self, kind: str, operation: Callable) -> None:
        if self._worker is not None:
            return
        worker = _Worker(operation, self)
        self._worker = worker
        self._job_kind = kind
        worker.progress.connect(self._scan_progress)
        worker.succeeded.connect(self._job_succeeded)
        worker.failed.connect(self._job_failed)
        worker.cancelled.connect(self._job_cancelled)
        worker.finished.connect(self._job_finished)
        self._set_busy(True)
        worker.start()

    def _scan_progress(self, progress: ScanProgress) -> None:
        if progress.total > 0:
            self.progress_bar.setRange(0, progress.total)
            self.progress_bar.setValue(progress.current)
        self._status(f"{progress.current:,} / {progress.total:,} · {progress.status} · {Path(progress.path).name}")
        self.status_label.setToolTip(progress.path)

    def _job_succeeded(self, result: object) -> None:
        if self._job_kind == "scan":
            report: ScanReport = result  # type: ignore[assignment]
            self._last_scan_errors = report.errors
            self._refresh_stats()
            self._results = []
            self._empty_results("素材库已更新", "选择查询图片，检索最新的本地索引。")
            outcome = "索引已暂停" if report.cancelled else "索引已更新"
            self._status(
                f"{outcome} · 新建/更新 {report.indexed:,} · 未变化 {report.unchanged:,}"
                f" · 失败 {report.failed:,} · 清理失效记录 {report.removed:,}"
            )
        elif self._job_kind == "search":
            if self._worker is not None and self._worker.cancel_event.is_set():
                self._job_cancelled()
                return
            self._results = list(result)  # type: ignore[arg-type]
            self._show_results()
            if self._results and all(not item.thumbnail_path for item in self._results):
                self._status("无法读取预览，仅完成文件内容比对，未进行相似图片检索。")
            else:
                self._status(f"检索完成 · 找到 {len(self._results):,} 个候选文件")

    def _job_failed(self, message: str) -> None:
        if self._worker is not None and self._worker.cancel_event.is_set():
            self._job_cancelled()
            return
        self._status("本次操作未完成，可查看提示后重试。")
        self._show_error("索引或检索失败", message)

    def _job_cancelled(self) -> None:
        if self._job_kind == "search":
            self._results = []
            self._empty_results("检索已取消", "可重新点击“开始检索”。")
        self._status("检索已取消。" if self._job_kind == "search" else "扫描已取消，已完成的索引保留。")

    def _job_finished(self) -> None:
        worker = self._worker
        self._worker = None
        self._job_kind = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)
        if self._close_requested:
            QTimer.singleShot(0, self.close)

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self._status("正在停止，请等待当前文件处理结束…")

    def _clear_result_widgets(self) -> None:
        while self.results_stack.count():
            item = self.results_stack.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _empty_results(self, title: str, description: str) -> None:
        self._clear_result_widgets()
        self.results_stack.addStretch()
        illustration = _label("▧", "score")
        illustration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.results_stack.addWidget(illustration)
        heading = _label(title, "heading", True)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.results_stack.addWidget(heading)
        copy = _label(description, "subtle", True)
        copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.results_stack.addWidget(copy)
        steps = _label("01  添加文件夹    →    02  建立索引    →    03  选择图片", "subtle", True)
        steps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        steps.setContentsMargins(0, 16, 0, 0)
        self.results_stack.addWidget(steps)
        self.results_stack.addStretch()
        self.results_summary.setText("未显示检索结果")

    def _show_results(self) -> None:
        self._clear_result_widgets()
        if not self._results:
            self._empty_results("暂无符合条件的文件", "可降低相似度门槛，或添加更多素材文件夹并更新索引。")
            self.results_summary.setText("0 个匹配 · 请查看素材库读取问题，确认源文件预览可用")
            return
        exact = sum(result.match_type == "identical" for result in self._results)
        if all(not result.thumbnail_path for result in self._results):
            self.results_summary.setText(
                f"{len(self._results):,} 个文件内容相同 · 无法读取预览，仅完成文件内容比对，未进行相似图片检索"
            )
        else:
            self.results_summary.setText(f"{len(self._results):,} 个候选文件 · {exact:,} 个文件完全相同 · 每个文件显示最匹配的页面")
        for result in self._results:
            self.results_stack.addWidget(self._result_card(result))
        self.results_stack.addStretch()
        self.results_scroll.verticalScrollBar().setValue(0)

    def _result_card(self, result: SearchResult) -> QFrame:
        card = QFrame()
        card.setObjectName("resultCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 14, 14, 14)
        row.setSpacing(14)
        thumbnail = _label("预览", "thumbnail")
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail.setFixedSize(100, 100)
        pixmap = _scaled_pixmap(Path(result.thumbnail_path), QSize(96, 96)) if result.thumbnail_path else QPixmap()
        if not pixmap.isNull():
            thumbnail.setPixmap(pixmap)
        row.addWidget(thumbnail, 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(7)
        title_row = QHBoxLayout()
        title = _label(Path(result.path).name, None, True)
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_row.addWidget(title, 1)
        suffix = Path(result.path).suffix.lstrip(".").upper() or "FILE"
        title_row.addWidget(_label(suffix, "tag"))
        body.addLayout(title_row)
        metadata = (
            f"{result.width:,} × {result.height:,}  ·  {_human_size(result.file_size)}  ·  第 {result.page} 页"
            if result.thumbnail_path
            else f"无可用预览  ·  {_human_size(result.file_size)}"
        )
        body.addWidget(_label(metadata, "subtle"))
        path_field = QLineEdit(result.path)
        path_field.setReadOnly(True)
        path_field.setToolTip(result.path)
        path_field.setCursorPosition(0)
        body.addWidget(path_field)
        actions = QHBoxLayout()
        reveal = QPushButton("资源管理器中定位")
        reveal.clicked.connect(lambda checked=False, path=result.path: self._reveal(path))
        actions.addWidget(reveal)
        copy = QPushButton("复制路径")
        copy.setObjectName("quiet")
        copy.clicked.connect(lambda checked=False, path=result.path: self._copy_path(path))
        actions.addWidget(copy)
        folder = QPushButton("打开文件夹")
        folder.setObjectName("quiet")
        folder.clicked.connect(lambda checked=False, path=result.path: self._open_parent(path))
        actions.addWidget(folder)
        actions.addStretch()
        body.addLayout(actions)
        row.addLayout(body, 1)
        score_column = QVBoxLayout()
        score = _label(f"{result.score:.0f}", "score")
        score.setAlignment(Qt.AlignmentFlag.AlignRight)
        score_column.addWidget(score)
        match_label = _label(MATCH_LABELS.get(result.match_type, "外观相似"), "subtle")
        match_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        score_column.addWidget(match_label)
        score_column.addStretch()
        row.addLayout(score_column)
        return card

    def _reveal(self, filename: str) -> None:
        try:
            reveal_file(Path(filename))
        except OSError as exc:
            self._show_error("无法定位文件", str(exc))

    def _open_parent(self, filename: str) -> None:
        try:
            open_folder(Path(filename).parent)
        except OSError as exc:
            self._show_error("无法打开文件夹", str(exc))

    def _copy_path(self, filename: str) -> None:
        QApplication.clipboard().setText(filename)
        self._status("文件路径已复制。")

    def _show_failures(self) -> None:
        issues = list(dict.fromkeys([*self.library.failures(), *self._last_scan_errors]))
        dialog = QDialog(self)
        dialog.setWindowTitle("素材库读取问题")
        dialog.resize(760, 490)
        layout = QVBoxLayout(dialog)
        layout.addWidget(_label("无法读取预览的文件不会参与外观检索。", "heading", True))
        layout.addWidget(_label(
            "EPS 和部分旧版 AI 需要安装 Ghostscript；AI 建议保存为兼容 PDF，"
            "PSD/PSB 建议开启“最大兼容性”。受密码保护或损坏的文件需要先处理。",
            "subtle", True,
        ))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n\n".join(f"{path}\n{message}" for path, message in issues) or "当前没有读取问题。")
        layout.addWidget(text, 1)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _export_results(self) -> None:
        if not self._results:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "导出检索结果名单", "图片检索结果.csv", "CSV 表格 (*.csv)")
        if not filename:
            return
        if not filename.lower().endswith(".csv"):
            filename += ".csv"

        def safe_cell(value: str) -> str:
            # Spreadsheet programs must not interpret a malicious filename as a formula.
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value

        try:
            with open(filename, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["文件名", "完整路径", "匹配类型", "外观分数（非概率）", "页码", "文件字节数"])
                for result in self._results:
                    writer.writerow([
                        safe_cell(Path(result.path).name), safe_cell(result.path),
                        MATCH_LABELS.get(result.match_type, result.match_type), f"{result.score:.1f}",
                        result.page, result.file_size,
                    ])
        except OSError as exc:
            self._show_error("导出失败", str(exc))
            return
        self._status(f"已导出 {len(self._results):,} 条结果：{filename}")

    def _show_error(self, title: str, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message)
        box.exec()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._worker is None and event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._worker is not None:
            return
        selected_query = False
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                self._add_folder(path)
            elif path.is_file() and not selected_query:
                self._set_query(path)
                selected_query = True
        event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None:
            self._close_requested = True
            self._cancel()
            event.ignore()
            return
        event.accept()
