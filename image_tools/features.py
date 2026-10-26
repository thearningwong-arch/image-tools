"""Small, offline perceptual descriptors.

The score measures visual resemblance, not a probability or semantic similarity.
A few centered crops help match modest crop changes; they do not promise arbitrary
object detection or matching tiny details in a larger design.
"""
from __future__ import annotations

import hashlib
import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageOps


@lru_cache(maxsize=1)
def _dct_basis() -> np.ndarray:
    indices = np.arange(32, dtype=np.float64)
    frequencies = np.arange(8, dtype=np.float64)[:, None]
    basis = np.cos(math.pi * (2 * indices + 1) * frequencies / 64)
    basis[0] *= 1 / math.sqrt(2)
    return basis


def _rgb(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        base = Image.new("RGBA", rgba.size, "white")
        base.alpha_composite(rgba)
        return base.convert("RGB")
    return image.convert("RGB")


def pixel_digest(image: Image.Image) -> str:
    """Digest preview pixels and dimensions, distinct from an original-file hash."""
    rgb = _rgb(image)
    digest = hashlib.sha256()
    digest.update(f"{rgb.width}x{rgb.height}:RGB:".encode("ascii"))
    digest.update(rgb.tobytes())
    return digest.hexdigest()


def _bits(values: np.ndarray) -> str:
    result = 0
    for value in values.ravel():
        result = (result << 1) | int(bool(value))
    return f"{result:016x}"


def _variant(image: Image.Image, fraction: float) -> dict:
    if fraction < 1:
        dx = int(image.width * (1 - fraction) / 2)
        dy = int(image.height * (1 - fraction) / 2)
        image = image.crop((dx, dy, image.width - dx, image.height - dy))
    gray = image.convert("L")
    pixels = np.asarray(gray.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    coefficients = _dct_basis() @ pixels @ _dct_basis().T
    ac = coefficients.ravel()[1:]
    # Remove insignificant roundoff for flat areas and simple vector shapes.
    ac[np.abs(ac) < 1e-6] = 0
    small = np.asarray(gray.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    colors = np.asarray(image.resize((16, 16), Image.Resampling.BOX), dtype=np.float64)
    return {
        "p": _bits(ac > np.median(ac)),
        "d": _bits(small[:, 1:] > small[:, :-1]),
        "color": np.round(colors.mean(axis=(0, 1)), 3).tolist(),
        "contrast": round(float(pixels.std()), 3),
        "crop": fraction,
    }


def describe(image: Image.Image) -> dict:
    rgb = _rgb(image)
    if rgb.width < 1 or rgb.height < 1:
        raise ValueError("图片尺寸无效")
    return {
        "version": 1,
        "aspect": rgb.width / rgb.height,
        "variants": [_variant(rgb, fraction) for fraction in (1.0, 0.9, 0.8, 0.7)],
    }


def _pair(a: dict, b: dict) -> float:
    color_distance = math.sqrt(sum((x - y) ** 2 for x, y in zip(a["color"], b["color"])) / 3)
    color = max(0.0, 100 - color_distance / 255 * 100)
    if a["contrast"] < 2 and b["contrast"] < 2:
        # Hashes of uniformly colored images carry no useful shape information.
        return max(0.0, 100 - color_distance / 255 * 150)
    if min(a["contrast"], b["contrast"]) < 2:
        return min(55.0, color)
    phash = 100 * (1 - (int(a["p"], 16) ^ int(b["p"], 16)).bit_count() / 63)
    dhash = 100 * (1 - (int(a["d"], 16) ^ int(b["d"], 16)).bit_count() / 64)
    return 0.68 * phash + 0.25 * dhash + 0.07 * color


def similarity(a: dict, b: dict) -> float:
    """Compare descriptors with a symmetric, bounded visual resemblance score."""
    if a.get("version") != 1 or b.get("version") != 1:
        raise ValueError("图片索引版本不兼容，请重新建立索引")
    best = 0.0
    for av in a["variants"]:
        for bv in b["variants"]:
            score = _pair(av, bv)
            score -= (2 - av["crop"] - bv["crop"]) * 8
            best = max(best, score)
    aspect_penalty = min(8.0, abs(math.log(a["aspect"] / b["aspect"])) * 5)
    return round(max(0.0, min(100.0, best - aspect_penalty)), 2)
