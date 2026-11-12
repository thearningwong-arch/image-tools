"""Exercise the frozen app copied from its read-only DMG, including reinstall.

All files are confined to an explicit validation directory. The user's real
Application Support directory and /Applications are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback


def run(*command: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, **kwargs)


def fingerprint(directory: Path) -> dict[str, str]:
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file() and not path.is_symlink()}


def exercise(app: Path, output: Path, expected_version: str | None) -> dict:
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    version = plist["CFBundleShortVersionString"]
    if expected_version and version != expected_version:
        raise RuntimeError(f"Bundle version {version} != {expected_version}")
    executable_name = plist["CFBundleExecutable"]
    destination = output / "安装路径 含空格" / app.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    storage = output / "独立测试索引"
    if storage.exists():
        raise RuntimeError("Use a new validation directory; the index already exists.")
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM"):
        environment.pop(key, None)
    reports = []
    before = None
    for step in ("installed", "reinstalled"):
        # ditto preserves the framework links and native bundle permissions.
        run("ditto", str(app), str(destination))
        binary = destination / "Contents" / "MacOS" / executable_name
        run("codesign", "--verify", "--deep", "--strict", str(destination))
        before = fingerprint(destination)
        report_path = output / f"{step}-app.json"
        with (output / f"{step}-app.log").open("w", encoding="utf-8") as log:
            # open uses the same LaunchServices path as Finder. Also exercise
            # the executable directly after reinstall, from outside the repo.
            command = (["/usr/bin/open", "-W", "-n", str(destination), "--args"]
                       if step == "installed" else [str(binary)])
            run(*command, "--data-dir", str(storage), "--self-test-report", str(report_path),
                cwd="/", env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        if not report_path.is_file():
            raise RuntimeError(f"The {step} app did not write its report; see {step}-app.log")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not (report.get("status") == "passed" and report.get("frozen") is True
                and report.get("qt_window") is True and report.get("version") == version
                and report.get("platform") == "darwin" and report.get("architecture") == platform.machine()
                and report.get("qt_platform") == "cocoa"
                and Path(report.get("executable", "")).resolve() == binary.resolve()
                and report.get("first_run") is (step == "installed")):
            raise RuntimeError(f"Frozen application acceptance failed: {report_path}")
        if step == "reinstalled":
            if not report.get("index_existed") or report.get("scan_unchanged") != 5:
                raise RuntimeError("Reinstall did not preserve the existing index.")
            if report.get("source_sha256") != reports[0].get("source_sha256"):
                raise RuntimeError("Reinstall changed source files.")
        if fingerprint(destination) != before:
            raise RuntimeError("The application wrote inside its signed bundle.")
        reports.append(report)
        shutil.rmtree(destination)
        if not (storage / "library.sqlite3").is_file():
            raise RuntimeError("Removing the application removed the persistent index.")
    return {"version": version, "minimum_macos": plist["LSMinimumSystemVersion"],
            "installed_app": True, "native_cocoa_window": True, "reinstalled_app": True,
            "index_retained": True, "source_files_unchanged": True, "bundle_unchanged": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--build-info", type=Path)
    source.add_argument("--app", type=Path)
    source.add_argument("--dmg", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Bundle validation requires macOS.")
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {"status": "failed"}
    mounted = False
    mountpoint = None
    try:
        expected_version = None
        dmg = args.dmg
        app = args.app
        if args.build_info:
            metadata = json.loads(args.build_info.read_text(encoding="utf-8"))
            dmg = Path(metadata["dmg"])
            expected_version = metadata["version"]
        if dmg:
            mountpoint = Path(tempfile.mkdtemp(prefix="image-tools-dmg-"))
            run("hdiutil", "verify", str(dmg.resolve()))
            run("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mountpoint),
                str(dmg.resolve()))
            mounted = True
            app = mountpoint / "ImageTools.app"
            applications = mountpoint / "Applications"
            if not applications.is_symlink() or applications.readlink() != Path("/Applications"):
                raise RuntimeError("The disk image is missing its Applications shortcut.")
            result["readonly_dmg"] = True
        result.update(exercise(app.expanduser().resolve(), output, expected_version))
        result["status"] = "passed"
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        if mounted:
            try:
                run("hdiutil", "detach", str(mountpoint))
            except Exception:
                result["status"] = "failed"
                result["detach_error"] = traceback.format_exc()
        if mountpoint:
            try:
                mountpoint.rmdir()
            except OSError:
                result["status"] = "failed"
                result["mount_cleanup_error"] = traceback.format_exc()
        (output / "bundle-regression.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
