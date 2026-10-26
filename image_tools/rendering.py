"""Read bounded visual previews without changing the source document."""
from __future__ import annotations

import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import threading
from typing import Iterator
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .models import RenderedPage

SUPPORTED_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".bmp", ".gif", ".webp",
    ".tif", ".tiff", ".ico", ".avif", ".psd", ".psb", ".ai", ".eps", ".pdf",
})
MAX_PREVIEW_EDGE = 1600
MAX_SOURCE_PIXELS = 100_000_000
GHOSTSCRIPT_TIMEOUT = 90
_PDFIUM_LOCK = threading.RLock()  # PDFium is not safe across concurrent threads.


class RenderError(RuntimeError):
    """A source cannot provide a usable preview in the current environment."""


def _check_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise RenderError("图片尺寸无效")
    if width * height > MAX_SOURCE_PIXELS:
        raise RenderError("图像超过 1 亿像素，当前预览器无法安全解码；请导出较小的预览图")


def _normalize(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    image.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE), Image.Resampling.LANCZOS)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        base = Image.new("RGBA", rgba.size, "white")
        base.alpha_composite(rgba)
        return base.convert("RGB")
    return image.convert("RGB")


def _raster(path: Path, max_pages: int) -> Iterator[RenderedPage]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            count = min(getattr(image, "n_frames", 1), max_pages) if path.suffix.lower() in (".tif", ".tiff") else 1
            for number in range(count):
                image.seek(number)
                _check_size(*image.size)
                # JPEG draft avoids decoding unnecessarily large camera originals.
                if image.format == "JPEG":
                    image.draft("RGB", (MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE))
                yield RenderedPage(_normalize(image.copy()), number + 1)


def _pdf(path: Path, max_pages: int) -> Iterator[RenderedPage]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RenderError("缺少 PDF 预览组件 pypdfium2，请重新安装项目依赖") from exc
    try:
        with _PDFIUM_LOCK, pdfium.PdfDocument(str(path)) as document:
            if len(document) == 0:
                raise RenderError("PDF 不包含可预览页面")
            for number in range(min(len(document), max_pages)):
                page = document[number]
                try:
                    width, height = page.get_size()
                    if width <= 0 or height <= 0 or not all(math.isfinite(v) for v in (width, height)):
                        raise RenderError("PDF 页面尺寸无效")
                    scale = min(2.0, MAX_PREVIEW_EDGE / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        image = _normalize(bitmap.to_pil().copy())
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                yield RenderedPage(image, number + 1)
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"PDF / AI 预览失败（可能已加密、损坏或未保存 PDF 兼容内容）：{exc}") from exc


def _check_photoshop_header(path: Path) -> None:
    """Reject unsafe dimensions before psd-tools reads the layer data.

    PSD and PSB share the same fixed, 26-byte header. Merely checking the parsed
    document's dimensions is too late: PSDImage.open also loads channel data.
    """
    with path.open("rb") as source:
        header = source.read(26)
    if len(header) != 26:
        raise RenderError("PSD / PSB 文件头不完整，文件可能损坏")
    signature, version, reserved, channels, height, width, depth, _ = struct.unpack(
        ">4sH6sHIIHH", header,
    )
    if signature != b"8BPS" or version not in (1, 2) or reserved != b"\0" * 6:
        raise RenderError("PSD / PSB 文件头无效，文件可能损坏或格式不受支持")
    if not 1 <= channels <= 56 or depth not in (1, 8, 16, 32):
        raise RenderError("PSD / PSB 通道数或位深无效，文件可能损坏或格式不受支持")
    _check_size(width, height)
    max_edge = 30_000 if version == 1 else 300_000
    if max(width, height) > max_edge:
        raise RenderError("PSD / PSB 尺寸超出该格式的范围，文件可能损坏")


def _photoshop(path: Path) -> Iterator[RenderedPage]:
    _check_photoshop_header(path)
    try:
        from psd_tools import PSDImage
    except ImportError as exc:
        raise RenderError("缺少 PSD / PSB 预览组件 psd-tools，请重新安装项目依赖") from exc
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        psd = PSDImage.open(path)
        _check_size(psd.width, psd.height)
        image = psd.topil()
        if image is None:
            image = psd.composite()
        if image is None:
            raise RenderError("PSD / PSB 没有可用的合成预览；请在 Photoshop 中启用“最大兼容性”后保存")
        yield RenderedPage(_normalize(image), 1)


def find_ghostscript() -> str | None:
    """Find Ghostscript without assuming a fixed installed version on Windows."""
    override = os.environ.get("IMAGE_TOOLS_GHOSTSCRIPT")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return str(candidate)
        return shutil.which(override)
    for command in ("gswin64c", "gswin32c", "gs"):
        found = shutil.which(command)
        if found:
            return found
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                root = Path(base) / "gs"
                for candidate in sorted(root.glob("gs*/bin/gswin*c.exe"), reverse=True):
                    if candidate.is_file():
                        return str(candidate)
    return None


def _bounding_box(path: Path) -> tuple[float, float, float, float]:
    # DSC may declare its final bounding box at the end of the document.
    with path.open("rb") as source:
        head = source.read(65536)
        source.seek(max(0, path.stat().st_size - 65536))
        tail = source.read(65536)
    data = head + b"\n" + tail
    number = rb"([-+]?\d+(?:\.\d+)?)"
    for field in (b"HiResBoundingBox", b"BoundingBox"):
        matches = re.findall(rb"%%" + field + rb":\s*" + rb"\s+".join([number] * 4), data)
        for match in reversed(matches):
            bbox = tuple(float(value) for value in match)
            if all(math.isfinite(value) for value in bbox) and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                return bbox
    raise RenderError("EPS / 旧版 AI 缺少有效页面边界；请另存为 PDF 或启用 PDF 兼容的 AI")


def _postscript(path: Path) -> Iterator[RenderedPage]:
    executable = find_ghostscript()
    if not executable:
        raise RenderError("EPS / 旧版 AI 需要 Ghostscript；请安装 Windows 64 位 Ghostscript，或设置 IMAGE_TOOLS_GHOSTSCRIPT 为 gswin64c.exe 路径")
    left, bottom, right, top = _bounding_box(path)
    width, height = right - left, top - bottom
    scale = min(2.0, MAX_PREVIEW_EDGE / max(width, height))
    pixels_w, pixels_h = max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))
    with tempfile.TemporaryDirectory(prefix="image-tools-eps-") as temporary:
        output = Path(temporary) / "preview.png"
        command = [
            executable, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dNOPROMPT", "-dFIXEDMEDIA",
            "-sDEVICE=pngalpha", f"-g{pixels_w}x{pixels_h}", f"-r{72 * scale:.8f}",
            "-dFirstPage=1", "-dLastPage=1", f"-sOutputFile={output}",
            "-c", f"{-left:.8f} {-bottom:.8f} translate", "-f", str(path.resolve()),
            "-c", "showpage",
        ]
        options = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GHOSTSCRIPT_TIMEOUT, check=False, **options)
        except subprocess.TimeoutExpired as exc:
            raise RenderError("Ghostscript 预览超时（90 秒），请导出 PDF 兼容版本或普通图片") from exc
        if completed.returncode != 0 or not output.is_file():
            details = (completed.stderr or completed.stdout).strip()[-500:]
            raise RenderError(f"Ghostscript 无法预览此 EPS / AI：{details or '没有生成预览'}")
        with Image.open(output) as image:
            yield RenderedPage(_normalize(image.copy()), 1)


def render_pages(path: Path, max_pages: int = 20) -> Iterator[RenderedPage]:
    """Yield previews, including at most max_pages PDF/TIFF pages.

    GIF uses its first frame. PSD/PSB use the document composite, not individual
    layers. AI must contain PDF data or a Ghostscript-readable PostScript body.
    """
    path = Path(path)
    if max_pages < 1:
        raise ValueError("max_pages 必须大于零")
    if not path.is_file():
        raise RenderError("文件不存在或不可读取")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise RenderError(f"不支持的文件类型：{suffix or '无扩展名'}")
    try:
        if suffix == ".pdf":
            yield from _pdf(path, max_pages)
        elif suffix == ".ai":
            with path.open("rb") as source:
                is_pdf = b"%PDF-" in source.read(1024)
            if is_pdf:
                yield from _pdf(path, max_pages)
            else:
                yield from _postscript(path)
        elif suffix in (".psd", ".psb"):
            yield from _photoshop(path)
        elif suffix == ".eps":
            yield from _postscript(path)
        else:
            yield from _raster(path, max_pages)
    except RenderError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise RenderError("图片尺寸过大，已停止预览以避免内存耗尽；请导出较小的预览图") from exc
    except UnidentifiedImageError as exc:
        raise RenderError("无法识别图片内容，文件可能损坏或格式不受支持") from exc
    except Exception as exc:
        raise RenderError(f"无法生成预览：{exc}") from exc
