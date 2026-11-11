"""Bracket image for 2-128 players."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from ..domain import formats
from ..services.views import BracketBox
from .fonts import Fonts

BG = "#0E1120"
BOX = "#171B33"
BOX_LINE = "#2A3058"
WIRE = "#3A3F66"
TEXT = "#EEF0F7"
MUTED = "#9AA1C0"
TBD = "#8C93B5"
WIN = "#7EE0A0"
HIGHLIGHT = "#8B7CF0"
FINAL = "#E5B53A"


class BracketRenderer:
    def __init__(self, fonts: Fonts | None = None):
        self.fonts = fonts or Fonts()

    def _fit(self, text: str, font, max_w: int) -> str:
        if font.getlength(text) <= max_w:
            return text
        while text and font.getlength(text + "…") > max_w:
            text = text[:-1]
        return text + "…"

    def render(self, title: str, format_key: str, rounds: list[list[BracketBox]],
               highlight_match_id: int | None = None) -> bytes:
        size = len(rounds[0]) * 2 if rounds else 2
        r1 = len(rounds[0]) if rounds else 1
        compact = r1 > 16
        unit = 44 if compact else 64
        box_h = 36 if compact else 44
        box_w, gap = 190, 30
        pad, header = 24, 78
        width = pad * 2 + len(rounds) * box_w + (len(rounds) - 1) * gap
        height = header + r1 * unit + pad
        img = Image.new("RGB", (max(width, 560), height), BG)
        d = ImageDraw.Draw(img)
        d.text((pad, 22), self._fit(title, self.fonts.display(22), width - 2 * pad), font=self.fonts.display(22),
               fill=TEXT, anchor="lm")
        fmt = formats.FORMATS.get(format_key)
        sub = f"Single elimination · {fmt.label if fmt else format_key} · {size}-player bracket"
        d.text((pad, 46), sub, font=self.fonts.inter(13), fill=MUTED, anchor="lm")
        f_name = self.fonts.inter(12 if compact else 13)
        f_seed = self.fonts.inter(10 if compact else 11)
        f_score = self.fonts.inter(11, "bold")
        centers: dict[tuple[int, int], float] = {}
        for r_idx, matches in enumerate(rounds):
            players = size >> r_idx
            x = pad + r_idx * (box_w + gap)
            label = f"{formats.round_name(players).upper()} · BO{formats.bo_for_round(format_key, size, r_idx + 1)}"
            d.text((x, header - 12), label, font=self.fonts.inter(11, "bold"), fill=MUTED, anchor="lm")
            span = unit * (2 ** r_idx)
            for m in matches:
                cy = header + span * (m.slot + 0.5)
                centers[(r_idx, m.slot)] = cy
                y0 = int(cy - box_h / 2)
                outline = BOX_LINE
                if m.match_id == highlight_match_id:
                    outline = HIGHLIGHT
                elif r_idx == len(rounds) - 1:
                    outline = FINAL
                d.rounded_rectangle((x, y0, x + box_w, y0 + box_h), radius=6, fill=BOX, outline=outline)
                d.line((x + 1, y0 + box_h // 2, x + box_w - 1, y0 + box_h // 2), fill=BOX_LINE)
                for i, (name, seed, feeder) in enumerate(((m.p1, m.seed1, m.feeder1), (m.p2, m.seed2, m.feeder2))):
                    ry = y0 + box_h * (0.25 + 0.5 * i)
                    tx = x + 8
                    if seed is not None:
                        d.text((tx, ry), str(seed), font=f_seed, fill=MUTED, anchor="lm")
                        tx += 24 if size >= 100 else 18
                    won = m.winner == i + 1
                    if name:
                        col = WIN if won else (TBD if m.winner and not won else TEXT)
                        txt = name
                    elif m.is_bye:
                        col, txt = TBD, "bye"
                    else:
                        col, txt = TBD, feeder or "TBD"
                    font = self.fonts.inter(12 if compact else 13, "semibold") if won else f_name
                    d.text((tx, ry), self._fit(txt, font, box_w - (tx - x) - 34), font=font, fill=col, anchor="lm")
                if m.score:
                    d.text((x + box_w - 8, y0 + box_h / 2), m.score, font=f_score, fill=MUTED, anchor="rm")
                if r_idx > 0:
                    for feeder_slot in (m.slot * 2, m.slot * 2 + 1):
                        fy = centers.get((r_idx - 1, feeder_slot))
                        if fy is None:
                            continue
                        fx = x - gap
                        mid = fx + gap // 2
                        d.line((fx, fy, mid, fy), fill=WIRE, width=2)
                        d.line((mid, fy, mid, cy), fill=WIRE, width=2)
                    d.line((x - gap // 2, cy, x, cy), fill=WIRE, width=2)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
