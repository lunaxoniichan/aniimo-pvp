"""Bundled fonts (Inter + Space Grotesk, SIL OFL) with safe fallbacks."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

log = logging.getLogger(__name__)
DEFAULT_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"


class Fonts:
    def __init__(self, fonts_dir: str | Path | None = None):
        self.dir = Path(fonts_dir) if fonts_dir else DEFAULT_DIR
        self._inter = {
            "regular": self.dir / "Inter-Regular.otf",
            "semibold": self.dir / "Inter-SemiBold.otf",
            "bold": self.dir / "Inter-Bold.otf",
        }
        self._display = self.dir / "SpaceGrotesk-Variable.ttf"

    @lru_cache(maxsize=128)  # noqa: B019 - one Fonts object per process
    def inter(self, size: int, weight: str = "regular") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        path = self._inter.get(weight, self._inter["regular"])
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            log.warning("Font %s missing, using Pillow's default font", path)
            return ImageFont.load_default(size)

    @lru_cache(maxsize=64)  # noqa: B019
    def display(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            f = ImageFont.truetype(str(self._display), size)
            try:
                f.set_variation_by_name("Bold")
            except Exception:
                pass
            return f
        except OSError:
            return self.inter(size, "bold")
