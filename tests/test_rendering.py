from pathlib import Path
import struct

from PIL import Image, ImageDraw
import pytest
from psd_tools import PSDImage
from psd_tools.constants import Compression

from image_tools import rendering
from image_tools.features import describe, similarity
from image_tools.rendering import RenderError, render_pages


def sample_image():
    image = Image.new("RGB", (240, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 110, 140), fill="navy")
    draw.ellipse((90, 40, 220, 130), fill="orange")
    return image


def test_raster_exif_transparency_and_bounded_preview(tmp_path):
    oriented = tmp_path / "orientation.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (80, 40), "green").save(oriented, exif=exif)
    assert list(render_pages(oriented))[0].image.size == (40, 80)
    transparent = tmp_path / "transparent.png"
    Image.new("RGBA", (30, 20), (255, 0, 0, 0)).save(transparent)
    assert list(render_pages(transparent))[0].image.getpixel((0, 0)) == (255, 255, 255)
    large = tmp_path / "large.png"
    Image.new("RGB", (2400, 1800), "red").save(large)
    assert list(render_pages(large))[0].image.size == (1600, 1200)


@pytest.mark.parametrize("extension,version", [("psd", 1), ("psb", 2)])
def test_real_photoshop_composites(tmp_path, extension, version):
    source = sample_image()
    document = PSDImage.frompil(source, compression=Compression.RAW)
    # RAW data is valid for both header versions; PSB RLE uses different lengths.
    document._record.header.version = version
    path = tmp_path / f"design.{extension}"
    document.save(path)
    assert path.read_bytes()[4:6] == version.to_bytes(2, "big")
    result = list(render_pages(path))
    assert len(result) == 1
    assert result[0].image.size == source.size
    assert similarity(describe(source), describe(result[0].image)) >= 99


def test_pdf_ai_and_tiff_read_pages_and_honor_limit(tmp_path):
    first, second = sample_image(), sample_image().rotate(180)
    pdf = tmp_path / "multipage.pdf"
    first.save(pdf, "PDF", save_all=True, append_images=[second])
    pages = list(render_pages(pdf))
    assert [page.page for page in pages] == [1, 2]
    assert similarity(describe(first), describe(pages[0].image)) >= 90
    assert similarity(describe(second), describe(pages[1].image)) >= 90
    assert len(list(render_pages(pdf, max_pages=1))) == 1
    ai = tmp_path / "pdf-compatible.ai"
    ai.write_bytes(pdf.read_bytes())
    assert len(list(render_pages(ai))) == 2
    tiff = tmp_path / "multipage.tiff"
    first.copy().save(tiff, save_all=True, append_images=[second.copy()])
    assert len(list(render_pages(tiff))) == 2
    assert len(list(render_pages(tiff, max_pages=1))) == 1


@pytest.mark.skipif(rendering.find_ghostscript() is None, reason="optional Ghostscript is not installed")
@pytest.mark.parametrize("extension", ["eps", "ai"])
def test_real_postscript_renderer_with_unicode_path(tmp_path, extension):
    path = tmp_path / f"设计 源文件.{extension}"
    sample_image().save(path, "EPS")
    result = list(render_pages(path))
    assert len(result) == 1
    assert max(result[0].image.size) <= 1600
    assert similarity(describe(sample_image()), describe(result[0].image)) >= 90


@pytest.mark.skipif(rendering.find_ghostscript() is None, reason="optional Ghostscript is not installed")
def test_postscript_cannot_override_bounded_preview_page_size(tmp_path):
    path = tmp_path / "page-size.eps"
    path.write_text(
        "%!PS-Adobe-3.0 EPSF-3.0\n"
        "%%BoundingBox: 0 0 240 160\n"
        "<< /PageSize [9000 9000] >> setpagedevice\n"
        "0 0 1 setrgbcolor 10 10 200 120 rectfill\nshowpage\n",
        encoding="ascii",
    )
    preview = list(render_pages(path))[0].image
    assert preview.size == (480, 320)
    assert preview.getpixel((100, 100)) == (0, 0, 255)


def test_missing_ghostscript_has_actionable_error(tmp_path, monkeypatch):
    path = tmp_path / "vector.eps"
    path.write_text("%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 200 100\nshowpage\n")
    monkeypatch.setattr(rendering, "find_ghostscript", lambda: None)
    with pytest.raises(RenderError, match="需要 Ghostscript"):
        list(render_pages(path))


def test_corrupt_unsupported_and_missing_files_are_not_previews(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")
    with pytest.raises(RenderError, match="无法识别"):
        list(render_pages(path))
    with pytest.raises(RenderError, match="不存在"):
        list(render_pages(tmp_path / "missing.jpg"))
    unsupported = tmp_path / "source.txt"
    unsupported.write_text("source")
    with pytest.raises(RenderError, match="不支持"):
        list(render_pages(unsupported))
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"%PDF-broken")
    with pytest.raises(RenderError, match="PDF / AI 预览失败"):
        list(render_pages(pdf))


def test_large_photoshop_document_has_actionable_limit(tmp_path, monkeypatch):
    source = sample_image()
    path = tmp_path / "large.psd"
    PSDImage.frompil(source).save(path)
    monkeypatch.setattr(rendering, "MAX_SOURCE_PIXELS", 100)
    with pytest.raises(RenderError, match="1 亿像素"):
        list(render_pages(path))


@pytest.mark.parametrize("version,extension", [(1, "psd"), (2, "psb")])
def test_oversized_photoshop_header_is_rejected_before_loading_layers(tmp_path, monkeypatch, version, extension):
    path = tmp_path / f"large.{extension}"
    path.write_bytes(struct.pack(
        ">4sH6sHIIHH", b"8BPS", version, b"\0" * 6, 3, 20_000, 20_000, 8, 3,
    ))

    def unexpected_load(*args, **kwargs):
        pytest.fail("Oversized document must be rejected before PSDImage.open")

    monkeypatch.setattr(PSDImage, "open", unexpected_load)
    with pytest.raises(RenderError, match="1 亿像素"):
        list(render_pages(path))


@pytest.mark.parametrize("header", [
    b"8BPS\x00\x01",
    struct.pack(
        ">4sH6sHIIHH", b"NOPE", 1, b"\0" * 6, 3, 100, 100, 8, 3,
    ),
    struct.pack(
        ">4sH6sHIIHH", b"8BPS", 3, b"\0" * 6, 3, 100, 100, 8, 3,
    ),
])
def test_invalid_photoshop_header_has_actionable_error(tmp_path, monkeypatch, header):
    path = tmp_path / "broken.psd"
    path.write_bytes(header)

    def unexpected_load(*args, **kwargs):
        pytest.fail("Invalid document must be rejected before PSDImage.open")

    monkeypatch.setattr(PSDImage, "open", unexpected_load)
    with pytest.raises(RenderError, match="文件头"):
        list(render_pages(path))


def test_photoshop_without_embedded_preview_composites_layers(tmp_path):
    from psd_tools.api.layers import PixelLayer
    from psd_tools.constants import Resource
    from psd_tools.psd.image_resources import ImageResource, VersionInfo

    original = sample_image()
    document = PSDImage.new("RGB", original.size, color=(255, 255, 255))
    PixelLayer.frompil(original, document, name="Artwork")
    document.image_resources[Resource.VERSION_INFO] = ImageResource(
        key=Resource.VERSION_INFO,
        data=VersionInfo(has_composite=False),
    )
    path = tmp_path / "layers-without-preview.psd"
    document.save(path)
    assert not PSDImage.open(path).has_preview()
    preview = list(render_pages(path))[0].image
    assert similarity(describe(original), describe(preview)) >= 99
