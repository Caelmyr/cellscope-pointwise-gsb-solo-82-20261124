"""Unmodified 8/16-bit source pixels and normalized inspection coordinates."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid

from .model import MAX_PIXELS, MAX_UPLOAD


def decode(blob):
    if not blob or len(blob) > MAX_UPLOAD:
        raise Invalid("选择 22 MB 以内的 PNG、TIFF 或 JPEG 照片。")
    try:
        with Image.open(BytesIO(blob)) as image:
            if image.format not in {"PNG", "TIFF", "JPEG"}:
                raise Invalid("选择 PNG、TIFF 或 JPEG 照片。")
            if min(image.size) < 32 or image.width * image.height > MAX_PIXELS:
                raise Invalid("照片每边至少 32 像素，总像素不能超过 1400 万。")
            oriented = ImageOps.exif_transpose(image)
            if oriented.mode in {"I", "I;16", "I;16B", "I;16L"}:
                values = np.asarray(oriented)
                if np.min(values) < 0 or np.max(values) > 65535:
                    raise Invalid("16 位灰度值应在 0 到 65535。")
                return Image.fromarray(values.astype(np.uint16))
            return oriented.convert("L")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise Invalid("无法读取这张照片。") from None


def png(image):
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def gray(image):
    values = np.asarray(image)
    divisor = 255.0 if image.mode == "L" else 65535.0
    return values.astype(np.float32) / divisor


def display(path: Path):
    with Image.open(path) as image:
        if image.mode == "L":
            return image.copy()
        return Image.fromarray(np.rint(gray(image) * 255).astype(np.uint8))


def box(values, width, height):
    x0, y0, x1, y1 = values
    left, top = min(round(x0 * width), width - 1), min(round(y0 * height), height - 1)
    return left, top, max(left + 1, round(x1 * width)), max(top + 1, round(y1 * height))


def point(values, width, height):
    x, y = values
    return round(x * (width - 1)), round(y * (height - 1))


@dataclass(frozen=True)
class View:
    values: np.ndarray
    region: tuple
    mm: float

    @property
    def roi(self):
        x0, y0, x1, y1 = self.region
        return self.values[y0:y1, x0:x1]

    @property
    def size(self):
        return self.values.shape[1], self.values.shape[0]


def observe(path, settings):
    with Image.open(path) as image:
        return View(gray(image), box(settings["roi"], image.width, image.height), settings["mm_per_pixel"])
