"""Element icons: assets/elements/<element>.png when present, otherwise a generated badge."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from ..domain.roster import DEFAULT_COLOR, ELEMENT_COLORS
from .fonts import Fonts
from .thumbs import SS, hex_rgb

log = logging.getLogger(__name__)


class ElementIcons:
    def __init__(self, elements_dir: str | Path, fonts: Fonts | None = None):
        self.dir = Path(elements_dir)
        self.fonts = fonts or Fonts()
        self._cache: dict[tuple[str, int], Image.Image] = {}

    def clear(self) -> None:
        self._cache.clear()

    def path(self, element: str) -> Path | None:
        p = self.dir / f"{element.lower()}.png"
        return p if p.is_file() else None

    def has_file(self, element: str) -> bool:
        return self.path(element) is not None

    def get(self, element: str, size: int) -> Image.Image:
        key = (element, size)
        img = self._cache.get(key)
        if img is None:
            img = self._load(element, size) or self._generate(element, size)
            self._cache[key] = img
        return img

    def _load(self, element: str, size: int) -> Image.Image | None:
        p = self.path(element)
        if p is None:
            return None
        try:
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im).convert("RGBA")
                return ImageOps.contain(im, (size, size), Image.Resampling.LANCZOS)
        except Exception as e:
            log.warning("Can't read element icon %s: %s", p, e)
            return None

    def _generate(self, element: str, size: int) -> Image.Image:
        """A coloured disc with the element's first letter (used until real icons are downloaded)."""
        big = size * SS
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((0, 0, big - 1, big - 1), fill=(*hex_rgb(ELEMENT_COLORS.get(element, DEFAULT_COLOR)), 255))
        d.text(
            (big / 2, big / 2),
            element[:1].upper(),
            font=self.fonts.display(int(big * 0.5)),
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        return img.resize((size, size), Image.Resampling.LANCZOS)
