"""Acceptance checks: a raster query must find real design-source candidates."""
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance
from psd_tools import PSDImage
from psd_tools.constants import Compression
import pytest

from image_tools.library import Library
from image_tools.rendering import find_ghostscript


def design():
    image = Image.new("RGB", (400, 300), "#f3eee1")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 40, 190, 245), fill="#176f72")
    draw.ellipse((160, 70, 365, 270), fill="#e29854")
    draw.polygon([(45, 22), (345, 30), (290, 60)], fill="#393d5a")
    draw.line((20, 270, 375, 40), width=9, fill="#6c4b91")
    return image


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_query_finds_design_sources_and_variants_after_index_reopens(tmp_path):
    root = tmp_path / "设计素材库"
    root.mkdir()
    source = design()
    query = tmp_path / "待查找图片.png"
    source.save(query)
    (root / "同图 副本.png").write_bytes(query.read_bytes())
    source.resize((200, 150)).save(root / "压缩版本.jpg", quality=35)
    ImageEnhance.Color(source).enhance(0.7).save(root / "轻微改色.png")
    source.crop((40, 30, 360, 270)).save(root / "裁剪版本.png")
    for extension, version in [("psd", 1), ("psb", 2)]:
        document = PSDImage.frompil(source, compression=Compression.RAW)
        document._record.header.version = version
        document.save(root / f"源文件.{extension}")
    first_page = Image.new("RGB", source.size, "white")
    first_page.save(root / "画册.pdf", save_all=True, append_images=[source])
    (root / "设计稿.ai").write_bytes((root / "画册.pdf").read_bytes())
    noise = np.random.default_rng(7).integers(0, 256, (300, 400, 3), dtype=np.uint8)
    Image.fromarray(noise).save(root / "不相关图片.png")

    originals = {path: checksum(path) for path in root.iterdir()}
    database = tmp_path / "index" / "library.sqlite3"
    thumbnails = tmp_path / "index" / "thumbnails"
    library = Library(database, thumbnails)
    report = library.scan([root])
    assert report.failed == 0
    assert report.indexed == 9
    assert library.stats().pages == 11
    library.close()

    reopened = Library(database, thumbnails)
    results = {Path(item.path).name: item for item in reopened.search(query)}
    assert set(results) == {path.name for path in originals if path.name != "不相关图片.png"}
    assert results["同图 副本.png"].match_type == "identical"
    assert results["源文件.psd"].match_type == "preview"
    assert results["源文件.psb"].match_type == "preview"
    assert results["画册.pdf"].page == results["设计稿.ai"].page == 2
    assert all(Path(result.thumbnail_path).is_file() for result in results.values())
    assert all(Path(result.path).is_absolute() for result in results.values())
    assert reopened.scan([root]).unchanged == 9
    assert {path: checksum(path) for path in originals} == originals


@pytest.mark.skipif(find_ghostscript() is None, reason="EPS / legacy AI requires optional Ghostscript")
def test_query_finds_real_postscript_sources(tmp_path):
    root = tmp_path / "postscript"
    root.mkdir()
    query = tmp_path / "query.png"
    source = design()
    source.save(query)
    for extension in ("eps", "ai"):
        source.save(root / f"设计 源文件.{extension}", "EPS")
    library = Library(tmp_path / "library.sqlite3", tmp_path / "thumbs")
    report = library.scan([root])
    assert report.failed == 0
    assert report.indexed == 2
    results = library.search(query)
    assert {Path(result.path).suffix for result in results} == {".ai", ".eps"}
    assert all(result.score >= 85 for result in results)
