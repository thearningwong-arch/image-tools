"""Offline, scale-invariant containment matching in bounded page previews.

SIFT proposes a scale/translation from local details. Pixel verification rejects
accidental geometric matches. Multi-scale template matching also handles drawings
with too few keypoints. Regions are normalized (left, top, right, bottom) within
the rendered page/artboard, never Photoshop document-canvas coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import math
from typing import Callable
from zipfile import BadZipFile

import cv2
import numpy as np
from PIL import Image

from .features import _rgb

VERSION = 1
MAX_EDGE = 1600
cv2.setNumThreads(2)  # Bound CPU use in the cancellable scan/search worker.


@dataclass
class Preview:
    rgb: np.ndarray
    gray: np.ndarray
    points: np.ndarray
    descriptors: np.ndarray

    @property
    def size(self) -> tuple[int, int]:
        return self.rgb.shape[1], self.rgb.shape[0]


@dataclass
class Match:
    score: float
    region: tuple[float, float, float, float]


def prepare(image: Image.Image) -> Preview:
    image = _rgb(image)
    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    rgb = np.asarray(image).copy()
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    detector = cv2.SIFT_create(nfeatures=2400, contrastThreshold=0.025)
    points, descriptors = detector.detectAndCompute(gray, None)
    return Preview(
        rgb, gray, np.array([point.pt for point in points], dtype=np.float32).reshape(-1, 2),
        descriptors if descriptors is not None else np.empty((0, 128), dtype=np.float32),
    )


def pack(preview: Preview) -> bytes:
    # Non-pickle arrays; JPEG bounds cache size while retaining a useful preview.
    ok, encoded = cv2.imencode('.jpg', cv2.cvtColor(preview.rgb, cv2.COLOR_RGB2BGR),
                               [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise ValueError('无法保存局部检索预览')
    stream = io.BytesIO()
    np.savez_compressed(stream, image=encoded, points=preview.points,
                        descriptors=preview.descriptors.astype(np.uint8))
    return stream.getvalue()


def unpack(data: bytes) -> Preview:
    try:
        with np.load(io.BytesIO(data), allow_pickle=False) as archive:
            rgb = cv2.imdecode(archive['image'], cv2.IMREAD_COLOR)
            if rgb is not None:
                rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)
            points = archive['points'].astype(np.float32)
            descriptors = archive['descriptors'].astype(np.float32)
    except (OSError, ValueError, KeyError, EOFError, BadZipFile, cv2.error) as exc:
        raise ValueError('局部检索缓存损坏，需要重新生成') from exc
    if rgb is None or max(rgb.shape[:2]) > MAX_EDGE or points.ndim != 2 \
            or points.shape[1] != 2 or descriptors.shape != (len(points), 128) \
            or len(points) > 10_000 or not np.isfinite(points).all() or not np.isfinite(descriptors).all():
        raise ValueError('局部检索缓存无效，请重试以重新生成')
    return Preview(rgb, cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), points, descriptors)


def searchable(query: Preview) -> bool:
    if min(query.size) < 16 or float(query.gray.std()) < 3:
        return False
    return bool(np.mean(cv2.Canny(query.gray, 30, 100) > 0) >= 0.003)


def _verify(query: Preview, target: Preview, box: tuple[float, ...]) -> Match | None:
    left, top, right, bottom = box
    tw, th = target.size
    width, height = right - left, bottom - top
    if min(width, height) < 12 or left < -2 or top < -2 or right > tw + 2 or bottom > th + 2:
        return None
    # Verify shape AND color in the entire proposed screenshot, not just inliers.
    size = (min(192, query.size[0]), min(192, query.size[1]))
    transform = np.array([[width / size[0], 0, left], [0, height / size[1], top]], dtype=np.float32)
    candidate = cv2.warpAffine(target.rgb, transform, size,
                               flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_REPLICATE)
    reference = cv2.resize(query.rgb, size, interpolation=cv2.INTER_AREA)
    correlation = float(cv2.matchTemplate(candidate, reference, cv2.TM_CCOEFF_NORMED)[0, 0])
    error = float(np.mean(np.abs(candidate.astype(np.float32) - reference.astype(np.float32))))
    if not math.isfinite(correlation) or correlation < 0.80 or error > 38:
        return None
    score = min(100.0, 100 * (0.8 * correlation + 0.2 * max(0, 1 - error / 100)))
    return Match(round(score, 2), (max(0.0, left / tw), max(0.0, top / th),
                                  min(1.0, right / tw), min(1.0, bottom / th)))


def _keypoint_box(query: Preview, target: Preview) -> tuple[float, ...] | None:
    if min(len(query.points), len(target.points)) < 6:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(query.descriptors, target.descriptors, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.72 * pair[1].distance]
    # Multiple query points mapped to one target point are not independent evidence.
    unique = {match.trainIdx: match for match in sorted(good, key=lambda item: -item.distance)}
    good = list(unique.values())
    if len(good) < 6:
        return None
    source = np.array([query.points[m.queryIdx] for m in good], dtype=np.float32)
    destination = np.array([target.points[m.trainIdx] for m in good], dtype=np.float32)
    matrix, mask = cv2.estimateAffinePartial2D(
        source, destination, method=cv2.RANSAC, ransacReprojThreshold=3,
        maxIters=2000, confidence=0.995,
    )
    if matrix is None or mask is None or not np.isfinite(matrix).all():
        return None
    inliers = source[mask.ravel() != 0]
    if len(inliers) < 6 or len(inliers) / len(good) < 0.5:
        return None
    qw, qh = query.size
    spread = np.ptp(inliers, axis=0) / (qw, qh)
    area = cv2.contourArea(cv2.convexHull(inliers)) / (qw * qh)
    scale = float(matrix[0, 0])
    # Screenshots may be scaled; large rotations/shears need a different model.
    if min(spread) < 0.20 or area < 0.04 or scale <= 0 \
            or abs(matrix[1, 0]) > scale * 0.035:
        return None
    left, top = matrix[:, 2]
    return float(left), float(top), float(left + scale * qw), float(top + scale * qh)


def _refine(query: Preview, target: Preview, box: tuple[float, ...],
            check: Callable[[], None], fraction: float = 0.06) -> Match | None:
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    tw, th = target.size
    margin = max(6, int(max(width, height) * fraction))
    x0, y0 = max(0, int(left) - margin), max(0, int(top) - margin)
    x1, y1 = min(tw, math.ceil(right) + margin), min(th, math.ceil(bottom) + margin)
    roi = target.gray[y0:y1, x0:x1]
    best = None
    for scale in np.linspace(1 - fraction, 1 + fraction, 17):
        check()
        w, h = round(width * scale), round(height * scale)
        if min(w, h) < 12 or w > roi.shape[1] or h > roi.shape[0]:
            continue
        template = cv2.resize(query.gray, (w, h), interpolation=cv2.INTER_AREA)
        if float(template.std()) < 3:
            continue
        correlation, location = cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))[1::2]
        if correlation < 0.70:
            continue
        x, y = location[0] + x0, location[1] + y0
        match = _verify(query, target, (x, y, x + w, y + h))
        if match is not None and (best is None or match.score > best.score):
            best = match
    return best


def locate(query: Preview, target: Preview, check: Callable[[], None] = lambda: None) -> Match | None:
    """Return the best verified containment; cancellation is checked between steps."""
    check()
    if not searchable(query) or min(target.size) < 12:
        return None
    box = _keypoint_box(query, target)
    check()
    if box is not None:
        match = _verify(query, target, box)
        refined = _refine(query, target, box, check)
        if refined is not None and (match is None or refined.score > match.score):
            match = refined
        if match is not None:
            return match

    # Texture-poor logos/illustrations: search every position over a bounded scale
    # pyramid, then refine the best proposals at full preview resolution.
    tw, th = target.size
    working_scale = min(1.0, 800 / max(tw, th))
    working = cv2.resize(target.gray, (round(tw * working_scale), round(th * working_scale)),
                         interpolation=cv2.INTER_AREA)
    qw, qh = query.size
    longest = max(qw, qh)
    maximum = min(working.shape[1] * longest / qw, working.shape[0] * longest / qh)
    if maximum < 24:
        return None
    lengths = list(np.geomspace(24, maximum, max(2, math.ceil(math.log(maximum / 24) / math.log(1.14)) + 1)))
    lengths.append(min(maximum, longest * working_scale))
    proposals = []
    for length in lengths:
        check()
        w, h = round(length * qw / longest), round(length * qh / longest)
        if min(w, h) < 12 or w > working.shape[1] or h > working.shape[0]:
            continue
        template = cv2.resize(query.gray, (w, h), interpolation=cv2.INTER_AREA)
        if float(template.std()) < 3:
            continue
        value, location = cv2.minMaxLoc(cv2.matchTemplate(working, template, cv2.TM_CCOEFF_NORMED))[1::2]
        if value >= 0.60:
            x, y = location
            proposals.append((value, tuple(v / working_scale for v in (x, y, x + w, y + h))))
    best = None
    for _, proposed in sorted(proposals, reverse=True)[:3]:
        match = _refine(query, target, proposed, check, fraction=0.16)
        if match is not None and (best is None or match.score > best.score):
            best = match
    return best
