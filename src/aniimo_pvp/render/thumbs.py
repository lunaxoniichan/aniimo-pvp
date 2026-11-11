"""Aniimo thumbnails: real PNGs cropped to circles, or an element-coloured initials badge."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from ..domain.roster import Aniimo
from .fonts import Fonts

log = logging.getLogger(__name__)
SS = 4  # supersampling for smooth circle edges


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def circle_mask(size: int) -> Image.Image:
    big = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size * SS - 1, size * SS - 1), fill=255)
    return big.resize((size, size), Image.Resampling.LANCZOS)


class ThumbCache:
    def __init__(self, fonts: Fonts):
        self.fonts = fonts
        self._cache: dict[tuple[str, int, bool, str], Image.Image] = {}

    def clear(self) -> None:
        self._cache.clear()

    def get(self, aniimo: Aniimo, size: int, banned: bool = False) -> Image.Image:
        key = (aniimo.slug, size, banned, str(aniimo.image_path))
        img = self._cache.get(key)
        if img is None:
            img = self._make(aniimo, size, banned)
            self._cache[key] = img
        return img

    def _make(self, a: Aniimo, size: int, banned: bool) -> Image.Image:
        base = self._load(a.image_path, size) if a.image_path else None
        if base is None:
            base = self.placeholder(a, size)
        else:
            ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(ring)
            d.ellipse((0, 0, size - 1, size - 1), outline=(*hex_rgb(a.color), 255), width=max(2, size // 28))
            base = Image.alpha_composite(base, ring)
        if banned:
            gray = ImageOps.grayscale(base.convert("RGB")).convert("RGBA")
            alpha = base.getchannel("A").point(lambda v: int(v * 0.5))
            gray.putalpha(alpha)
            base = gray
        return base

    def _load(self, path: Path, size: int) -> Image.Image | None:
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im).convert("RGBA")
                im = ImageOps.fit(im, (size, size), Image.Resampling.LANCZOS)
        except Exception as e:
            log.warning("Can't read thumbnail %s: %s", path, e)
            return None
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(im, (0, 0), circle_mask(size))
        # keep the image's own transparency inside the circle
        alpha = Image.composite(im.getchannel("A"), Image.new("L", (size, size), 0), circle_mask(size))
        out.putalpha(alpha)
        return out

    def placeholder(self, a: Aniimo, size: int) -> Image.Image:
        big = size * SS
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        r, g, b = hex_rgb(a.color)
        d.ellipse((0, 0, big - 1, big - 1), fill=(r, g, b, 255))
        inset = max(2, big // 30)
        d.ellipse((inset, inset, big - 1 - inset, big - 1 - inset), outline=(14, 17, 32, 60), width=inset)
        font = self.fonts.display(int(big * 0.34))
        d.text((big / 2, big / 2), a.initials, font=font, fill=(14, 17, 32, 255), anchor="mm")
        return img.resize((size, size), Image.Resampling.LANCZOS)
