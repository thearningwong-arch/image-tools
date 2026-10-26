"""Exercise the same application entry point used by Python and packaged builds."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import pytest

from image_tools import __main__
from image_tools.ui import MainWindow


def test_main_starts_real_window_with_configured_storage(tmp_path, monkeypatch):
    # Reuse Qt's singleton if other desktop tests have already created it.
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    storage = tmp_path / "配置 索引"
    seen = []
    original_exec = QApplication.exec

    def run_briefly():
        def inspect_and_close():
            windows = [widget for widget in application.topLevelWidgets()
                       if isinstance(widget, MainWindow) and widget.isVisible()]
            for window in windows:
                seen.append((window.library.database_path.parent, window.library.max_pages))
                window.close()
            application.quit()

        QTimer.singleShot(50, inspect_and_close)
        return original_exec()

    # Keep the actual event loop and constructor path, replacing only ownership
    # of the single QApplication and adding an automated close timer.
    monkeypatch.setattr("PySide6.QtWidgets.QApplication", lambda args: application)
    monkeypatch.setattr(application, "exec", run_briefly)
    assert __main__.main(["--data-dir", str(storage), "--max-pages", "7"]) == 0
    assert seen == [(storage.resolve(), 7)]
    assert (storage / "library.sqlite3").is_file()


def test_invalid_page_limit_fails_before_starting_desktop():
    with pytest.raises(SystemExit) as error:
        __main__.main(["--max-pages", "0"])
    assert error.value.code == 2
