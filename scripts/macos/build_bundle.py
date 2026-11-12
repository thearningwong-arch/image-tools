"""Build and sign a native .app and drag-to-install DMG on macOS.

The optional identity/profile refer to credentials already installed in the
builder's keychain. No certificates or credentials are stored in this project.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
APP_NAME = "ImageTools"
BUNDLE_ID = "io.github.thearningwong-arch.image-tools"


def run(*command: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, **kwargs)


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def minimum_macos(app: Path, baseline: str = "14.0") -> str:
    """Never advertise a lower OS floor than any bundled Mach-O requires."""
    from macholib.MachO import MachO
    from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX

    required = version_tuple(baseline) + (0,)
    # Inspect magic bytes first; trying MachO on every Qt resource is expensive.
    magic = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
             b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
             b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
    for binary in app.rglob("*"):
        if not binary.is_file() or binary.is_symlink():
            continue
        with binary.open("rb") as stream:
            if stream.read(4) not in magic:
                continue
        for header in MachO(str(binary)).headers:
            for command, payload, _data in header.commands:
                if command.cmd == LC_BUILD_VERSION:
                    # macOS platform value. Catalyst/iOS slices are not macOS.
                    if payload.platform != 1:
                        continue
                    packed = payload.minos
                elif command.cmd == LC_VERSION_MIN_MACOSX:
                    packed = payload.version
                else:
                    continue
                required = max(required, (packed >> 16, (packed >> 8) & 255, packed & 255))
    return ".".join(map(str, required[:2] if required[2] == 0 else required))


def make_icon(path: Path) -> None:
    """A bundled high-resolution icon, independent of host system artwork."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((72, 72, 952, 952), radius=190, fill="#19354D")
    draw.rounded_rectangle((228, 250, 764, 712), radius=48, outline="#DAF4F5", width=38)
    draw.ellipse((558, 326, 654, 422), fill="#63D9C6")
    draw.line([(270, 650), (416, 460), (540, 612), (620, 520), (726, 650)],
              fill="#63D9C6", width=40, joint="curve")
    draw.ellipse((590, 590, 806, 806), fill="#19354D", outline="#DAF4F5", width=38)
    draw.line((780, 780, 866, 866), fill="#DAF4F5", width=40)
    image.save(path, format="ICNS")


def notarize(artifact: Path, profile: str, report: Path) -> None:
    # Keep Apple's diagnostics even when notarytool itself returns nonzero.
    result = subprocess.run(
        ["xcrun", "notarytool", "submit", str(artifact), "--keychain-profile",
         profile, "--wait", "--output-format", "json"], capture_output=True, text=True)
    report.write_text(result.stdout, encoding="utf-8")
    report.with_suffix(".log").write_text(result.stderr, encoding="utf-8")
    result.check_returncode()
    if json.loads(result.stdout).get("status") != "Accepted":
        raise RuntimeError(f"Apple did not accept {artifact.name}; see {report}")
    run("xcrun", "stapler", "staple", str(artifact))
    run("xcrun", "stapler", "validate", str(artifact))


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Build macOS bundles on a Mac; cross-compilation is unsupported.")
    if not (3, 12) <= sys.version_info[:2] < (3, 16):
        raise SystemExit("Python 3.12–3.15 is required.")
    arch = platform.machine()
    if arch not in {"arm64", "x86_64"}:
        raise SystemExit(f"Unsupported Python architecture: {arch}")
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    from image_tools import __version__

    if not re.fullmatch(r"\d+\.\d+\.\d+", __version__):
        raise SystemExit("The bundle version must use major.minor.patch.")
    identity = os.environ.get("MACOS_SIGNING_IDENTITY", "").strip() or None
    profile = os.environ.get("MACOS_NOTARY_PROFILE", "").strip() or None
    if profile and (not identity or not identity.startswith("Developer ID Application:")):
        raise SystemExit("Notarization requires a Developer ID Application signing identity.")
    build = REPO / "build" / "macos"
    build.mkdir(parents=True, exist_ok=True)
    dist = REPO / "dist"
    dist.mkdir(exist_ok=True)
    icon = build / "ImageTools.icns"
    make_icon(icon)
    # The generated spec lives under ignored build/, not the checkout root.
    spec = build / "ImageTools.spec"
    spec.write_text(f'''from PyInstaller.utils.hooks import collect_all
datas, binaries, hiddenimports = [], [], []
for package in ("psd_tools", "pypdfium2", "pypdfium2_raw"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
a = Analysis([{str(REPO / "scripts" / "desktop_entry.py")!r}], pathex=[{str(REPO)!r}],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ImageTools",
          console=False, strip=False, upx=False, target_arch={arch!r},
          codesign_identity={identity!r})
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ImageTools")
app = BUNDLE(coll, name="ImageTools.app", icon={str(icon)!r},
             bundle_identifier={BUNDLE_ID!r}, version={__version__!r},
             info_plist={{"CFBundleDisplayName": "图迹", "CFBundleName": "图迹",
                          "CFBundleShortVersionString": {__version__!r},
                          "CFBundleVersion": {__version__!r},
                          "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True}})
''', encoding="utf-8")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(dist), "--workpath", str(build / "pyinstaller"), str(spec))
    app = dist / "ImageTools.app"
    plist_path = app / "Contents" / "Info.plist"
    info = plistlib.loads(plist_path.read_bytes())
    minimum = minimum_macos(app)
    if version_tuple(minimum) > version_tuple(platform.mac_ver()[0]):
        raise RuntimeError(f"Bundled dependencies require macOS {minimum}; this builder cannot validate them.")
    info["LSMinimumSystemVersion"] = minimum
    plist_path.write_bytes(plistlib.dumps(info))
    sign = ["codesign", "--force", "--deep", "--sign", identity or "-"]
    if identity:
        sign += ["--options", "runtime", "--timestamp"]
    run(*sign, str(app))
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app))
    if profile:
        archive = build / "ImageTools-notarization.zip"
        run("ditto", "-c", "-k", "--keepParent", str(app), str(archive))
        # ZIP submissions attach tickets to the contained app, not the ZIP.
        result = subprocess.run(
            ["xcrun", "notarytool", "submit", str(archive), "--keychain-profile",
             profile, "--wait", "--output-format", "json"], capture_output=True, text=True)
        (build / "app-notarization.json").write_text(result.stdout, encoding="utf-8")
        (build / "app-notarization.log").write_text(result.stderr, encoding="utf-8")
        result.check_returncode()
        if json.loads(result.stdout).get("status") != "Accepted":
            raise RuntimeError("Apple did not accept the app; see app-notarization.json")
        run("xcrun", "stapler", "staple", str(app))
        run("xcrun", "stapler", "validate", str(app))
        run("spctl", "--assess", "--type", "execute", "--verbose=2", str(app))

    stage = build / "dmg-root"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    run("ditto", str(app), str(stage / app.name))
    (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
    dmg = dist / f"ImageTools-{__version__}-macos-{arch}.dmg"
    run("hdiutil", "create", "-volname", "图迹 Image Tools", "-srcfolder", str(stage),
        "-format", "UDZO", "-ov", str(dmg))
    if identity:
        run("codesign", "--force", "--sign", identity, "--timestamp", str(dmg))
    if profile:
        notarize(dmg, profile, build / "notarization.json")
        run("spctl", "--assess", "--type", "open", "--context", "context:primary-signature",
            "--verbose=2", str(dmg))
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    Path(str(dmg) + ".sha256").write_text(f"{digest}  {dmg.name}\n", encoding="ascii")
    metadata = {"version": __version__, "architecture": arch, "minimum_macos": minimum,
                "app": str(app), "dmg": str(dmg), "sha256": digest,
                "signing": "developer-id" if identity else "ad-hoc", "notarized": bool(profile)}
    (build / "build-info.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
