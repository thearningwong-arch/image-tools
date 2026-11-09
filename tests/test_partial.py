import io
import numpy as np
from PIL import Image, ImageDraw
import pytest

from image_tools import partial


@pytest.mark.parametrize('box,scale', [
    ((502, 317, 736, 505), 0.55),
    ((16, 352, 249, 583), 1.8),
    ((0, 0, 220, 180), 1.35),
    ((625, 454, 800, 600), 2.3),
])
def test_offcenter_scaled_compressed_screenshot_locates_whole_image(detailed_picture, box, scale):
    crop = detailed_picture.crop(box)
    crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    crop.save(stream, 'JPEG', quality=65)
    stream.seek(0)
    query = partial.prepare(Image.open(stream))
    target = partial.unpack(partial.pack(partial.prepare(detailed_picture)))
    match = partial.locate(query, target)
    assert match is not None and match.score > 85
    expected = np.array(box) / (800, 600, 800, 600)
    assert np.max(np.abs(np.array(match.region) - expected)) < 0.015


def test_low_keypoint_drawing_uses_multiscale_position_search():
    image = Image.new('RGB', (640, 480), '#eee9dc')
    draw = ImageDraw.Draw(image)
    draw.rectangle((400, 230, 450, 390), fill='#226c87')
    draw.ellipse((430, 260, 575, 400), fill='#df9b3c')
    query = partial.prepare(image.crop((390, 220, 590, 415)).resize((310, 302)))
    target = partial.unpack(partial.pack(partial.prepare(image)))
    match = partial.locate(query, target)
    assert match is not None and match.score > 95
    assert np.max(np.abs(np.array(match.region) - np.array((390/640, 220/480, 590/640, 415/480)))) < 0.02


def test_unrelated_and_solid_queries_do_not_get_spurious_containment(detailed_picture):
    target = partial.prepare(detailed_picture)
    unrelated = Image.fromarray(np.random.default_rng(810).integers(0, 256, (160, 200, 3), dtype=np.uint8))
    assert partial.locate(partial.prepare(unrelated), target) is None
    assert partial.locate(partial.prepare(Image.new('RGB', (90, 80), 'white')), target) is None
    assert not partial.searchable(partial.prepare(detailed_picture.resize((8, 8))))


def test_same_geometry_with_different_colors_is_rejected():
    image = Image.new('RGB', (400, 300), '#333333')
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 90, 220, 230), fill='red')
    draw.ellipse((210, 110, 310, 245), fill='yellow')
    query = image.crop((160, 80, 330, 260))
    rgb = np.asarray(query).copy()
    rgb = rgb[:, :, ::-1].copy()
    assert partial.locate(partial.prepare(Image.fromarray(rgb)), partial.prepare(image)) is None


def test_cancellation_during_multiscale_search_is_observed(detailed_picture):
    query = partial.prepare(detailed_picture.crop((420, 310, 620, 460)))
    target = partial.prepare(Image.new('RGB', (800, 600), 'white'))
    count = 0

    def check():
        nonlocal count
        count += 1
        if count >= 5:
            raise InterruptedError('cancelled')

    with pytest.raises(InterruptedError, match='cancelled'):
        partial.locate(query, target, check)
    assert count == 5
