from pathlib import Path
from unittest.mock import Mock

import pytest

from image_tools import platform_utils


def test_windows_reveal_preserves_unicode_and_shell_characters(tmp_path, monkeypatch):
    file = tmp_path / "设计 图 & draft.png"
    file.write_bytes(b"sample")
    process = Mock()
    monkeypatch.setattr(platform_utils.sys, "platform", "win32")
    monkeypatch.setattr(platform_utils.subprocess, "Popen", process)
    platform_utils.reveal_file(file)
    process.assert_called_once_with(["explorer.exe", "/select,", str(file.resolve())])


def test_missing_file_does_not_launch_explorer(tmp_path, monkeypatch):
    process = Mock()
    monkeypatch.setattr(platform_utils.subprocess, "Popen", process)
    with pytest.raises(OSError):
        platform_utils.reveal_file(tmp_path / "missing.psd")
    process.assert_not_called()


def test_open_folder_uses_separate_argument(tmp_path, monkeypatch):
    process = Mock()
    monkeypatch.setattr(platform_utils.sys, "platform", "win32")
    monkeypatch.setattr(platform_utils.subprocess, "Popen", process)
    platform_utils.open_folder(tmp_path)
    process.assert_called_once_with(["explorer.exe", str(tmp_path.resolve())])


def test_data_directory_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TOOLS_DATA_DIR", str(tmp_path / "index"))
    assert platform_utils.data_directory() == tmp_path / "index"


def test_macos_data_is_stored_outside_the_application_bundle(tmp_path, monkeypatch):
    monkeypatch.delenv("IMAGE_TOOLS_DATA_DIR", raising=False)
    monkeypatch.setattr(platform_utils.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert platform_utils.data_directory() == tmp_path / "Library" / "Application Support" / "ImageTools"


def test_macos_reveal_preserves_unicode_and_shell_characters(tmp_path, monkeypatch):
    file = tmp_path / "设计 图 & 'draft'.png"
    file.write_bytes(b"sample")
    process = Mock()
    monkeypatch.setattr(platform_utils.sys, "platform", "darwin")
    monkeypatch.setattr(platform_utils.subprocess, "Popen", process)
    monkeypatch.setenv("PATH", "")  # Finder integration does not depend on shell setup.
    platform_utils.reveal_file(file)
    process.assert_called_once_with(["/usr/bin/open", "-R", str(file.resolve())])


def test_macos_opens_folder_as_one_argument(tmp_path, monkeypatch):
    folder = tmp_path / "素材 & 参考"
    folder.mkdir()
    process = Mock()
    monkeypatch.setattr(platform_utils.sys, "platform", "darwin")
    monkeypatch.setattr(platform_utils.subprocess, "Popen", process)
    platform_utils.open_folder(folder)
    process.assert_called_once_with(["/usr/bin/open", str(folder.resolve())])
