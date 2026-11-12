"""Platform-independent checks for metadata derived from native dependencies."""
import importlib.util
from pathlib import Path
import struct


def build_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "macos" / "build_bundle.py"
    spec = importlib.util.spec_from_file_location("macos_build_bundle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def macho(path, minimum, *, legacy=False, platform=1):
    version = (minimum[0] << 16) | (minimum[1] << 8) | minimum[2]
    command = (struct.pack("<IIII", 0x24, 16, version, version) if legacy
               else struct.pack("<IIIIII", 0x32, 24, platform, version, version, 0))
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x01000007, 3, 6, 1, len(command), 0, 0)
    path.write_bytes(header + command)


def test_bundle_minimum_tracks_both_native_load_command_formats(tmp_path):
    app = tmp_path / "ImageTools.app"
    app.mkdir()
    macho(app / "python", (10, 13, 0), legacy=True)
    macho(app / "QtCore", (14, 0, 0))
    macho(app / "newer_dependency", (14, 2, 1))
    (app / "readme.txt").write_text("Resource, not a Mach-O binary")
    assert build_module().minimum_macos(app) == "14.2.1"


def test_bundle_minimum_ignores_non_macos_slices_and_keeps_supported_floor(tmp_path):
    macho(tmp_path / "old_python", (10, 13, 0), legacy=True)
    macho(tmp_path / "ios_resource", (19, 0, 0), platform=2)
    assert build_module().minimum_macos(tmp_path) == "14.0"
