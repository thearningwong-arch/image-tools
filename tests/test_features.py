import io
import json

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

from image_tools.features import describe, pixel_digest, similarity


def artwork():
    image = Image.new("RGB", (480, 360), "#e6e2d8")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 55, 220, 270), fill="#346e9d")
    draw.ellipse((180, 80, 420, 310), fill="#dd7756")
    draw.line((20, 325, 450, 35), fill="#243748", width=13)
    draw.rectangle((300, 25, 445, 55), fill="#f7c659")
    return image


def test_resized_compressed_and_recolored_images_remain_similar():
    original = artwork()
    stream = io.BytesIO()
    original.resize((240, 180)).save(stream, "JPEG", quality=30)
    stream.seek(0)
    jpeg = Image.open(stream)
    recolored = ImageEnhance.Color(original).enhance(0.6)
    descriptor = describe(original)
    assert similarity(descriptor, describe(jpeg)) >= 90
    assert similarity(descriptor, describe(recolored)) >= 90
    assert pixel_digest(original) != pixel_digest(jpeg)


def test_modest_center_crop_ranks_above_unrelated_image():
    original = artwork()
    cropped = original.crop((48, 36, 432, 324))
    unrelated = Image.fromarray(np.random.default_rng(42).integers(0, 256, (360, 480, 3), dtype=np.uint8))
    assert similarity(describe(original), describe(cropped)) >= 90
    assert similarity(describe(original), describe(unrelated)) < 70


def test_descriptor_survives_json_and_similarity_is_symmetric():
    a = describe(artwork())
    b = describe(artwork().crop((25, 25, 450, 330)))
    assert json.loads(json.dumps(a)) == a
    assert similarity(a, a) == 100
    assert similarity(a, b) == similarity(b, a)


def test_solid_colors_do_not_match_just_because_hashes_are_flat():
    red = describe(Image.new("RGB", (100, 100), "red"))
    blue = describe(Image.new("RGB", (100, 100), "blue"))
    white = describe(Image.new("RGB", (100, 100), "white"))
    assert similarity(red, blue) < 70
    assert similarity(white, describe(artwork())) < 70


def test_pixel_digest_accounts_for_dimensions_and_transparency():
    assert pixel_digest(Image.new("RGB", (10, 20), "white")) != pixel_digest(Image.new("RGB", (20, 10), "white"))
    transparent = Image.new("RGBA", (10, 10), (200, 0, 100, 0))
    assert pixel_digest(transparent) == pixel_digest(Image.new("RGB", (10, 10), "white"))
