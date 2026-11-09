"""Real multi-artboard Photoshop files shared by rendering/index/UI tests."""
import pytest
from PIL import Image, ImageDraw
from psd_tools import PSDImage
from psd_tools.api.layers import Artboard, Group, PixelLayer
from psd_tools.constants import Compression, Resource, Tag
from psd_tools.psd.descriptor import Descriptor, Double, Integer
from psd_tools.psd.image_resources import ImageResource, VersionInfo
import numpy as np


@pytest.fixture
def detailed_picture():
    """A deterministic, non-periodic poster with fine details across its canvas."""
    rng = np.random.default_rng(304)
    pixels = rng.integers(205, 241, (600, 800, 3), dtype=np.uint8)
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)
    for number in range(100):
        x, y = rng.integers((0, 0), (735, 550))
        color = tuple(int(value) for value in rng.integers(20, 190, 3))
        width, height = rng.integers(12, 65, 2)
        if number % 2:
            draw.ellipse((x, y, x + width, y + height), fill=color)
        else:
            draw.rectangle((x, y, x + width, y + height), fill=color)
        draw.text((x, y + height), f'Photo {number:03}', fill='#19273c')
    return image


@pytest.fixture
def artboard_factory(tmp_path):
    def create(*, version=1, preview=True, count=3, outside=False, hidden_last=False, overlap=False):
        width, height, gap = 160, 120, 24
        canvas_width = count * (width + gap)
        if outside:
            canvas_width = width + gap  # Later boards extend outside the header canvas.
        document = PSDImage.new("RGB", (canvas_width, height + 40), color=(255, 255, 255))
        document._record.header.version = version
        document._record.image_data.compression = Compression.RAW
        parent = Group.new(document, "Designs")
        exports = []
        for index in range(count):
            left, top = index * (width + gap), 20
            if outside == "negative":
                left = -(index + 1) * (width + gap)
            if overlap and index == count - 1:
                left = 0
            child = Image.new("RGB", (width - 20, height - 20), "#eeeeee")
            draw = ImageDraw.Draw(child)
            # Different silhouettes and colors; leave a real artboard margin.
            palette = ("#165b88", "#bc5726", "#587844")
            draw.rectangle((8, 10, 55, 82), fill=palette[index % 3])
            draw.ellipse((40, 20, 125, 90), fill=palette[(index + 1) % 3])
            draw.line((5, 95, 135, 5 + index % 30), fill="#29213e", width=6)
            child = child.transpose(Image.Transpose.ROTATE_180) if index % 2 else child
            group = Group.new(parent, "Board")
            group.tagged_blocks.set_data(Tag.ARTBOARD_DATA1, {
                b"artboardRect": Descriptor({
                    b"Left": Double(left), b"Top ": Double(top),
                    b"Rght": Double(left + width), b"Btom": Double(top + height),
                }),
                b"artboardBackgroundType": Integer(2),  # White board background.
            })
            board = Artboard._move(group)
            board.name = f"设计画板 {index + 1}"
            PixelLayer.frompil(child, board, top=top + 10, left=left + 10, compression=Compression.RAW)
            board.visible = not (hidden_last and index == count - 1)
            exported = Image.new("RGB", (width, height), "white")
            exported.paste(child, (10, 10))
            exports.append(exported)
        if not preview:
            document.image_resources[Resource.VERSION_INFO] = ImageResource(
                key=Resource.VERSION_INFO, data=VersionInfo(has_composite=False),
            )
        path = tmp_path / ("design.psb" if version == 2 else "design.psd")
        document.save(path)
        return path, exports

    return create
