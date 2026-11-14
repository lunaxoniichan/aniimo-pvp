"""Draft board image (1280x720), as approved in the design canvas."""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw

from ..domain.formats import round_name
from ..services.views import MatchView, SlotView, StepView
from .elements import ElementIcons
from .fonts import Fonts
from .thumbs import ThumbCache, hex_rgb

W, H = 1280, 720
BG = "#0E1120"
HEADER_BG = "#121530"
LINE = "#232846"
PANEL = "#151931"
CARD = "#1B2040"
CARD_LINE = "#2A3058"
DASH = "#39406B"
TEXT = "#EEF0F7"
SOFT = "#C9CEE6"
MUTED = "#9AA1C0"
FAINT = "#8088AA"
BAN_X = "#FF5C5C"
SIDE = {
    "A": {"c": "#4C8DFF", "lt": "#7FB0FF", "label": "SIDE A · FIRST PICK"},
    "B": {"c": "#FF8A3D", "lt": "#FFAA70", "label": "SIDE B · COUNTER-PICK"},
}


def blend(fg: str, bg: str, alpha: float) -> tuple[int, int, int]:
    f, b = hex_rgb(fg), hex_rgb(bg)
    return tuple(round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3))  # type: ignore[return-value]


class BoardRenderer:
    def __init__(self, fonts: Fonts | None = None, thumbs: ThumbCache | None = None, icons: ElementIcons | None = None):
        self.fonts = fonts or Fonts()
        self.thumbs = thumbs or ThumbCache(self.fonts)
        self.icons = icons or ElementIcons("assets/elements", self.fonts)

    # ---------- helpers ----------
    def fit(self, text: str, font, max_w: int) -> str:
        if font.getlength(text) <= max_w:
            return text
        while text and font.getlength(text + "…") > max_w:
            text = text[:-1]
        return text + "…"

    def dashed_circle(self, d: ImageDraw.ImageDraw, box, color: str, dashes: int = 18, width: int = 2) -> None:
        step = 360 / dashes
        for i in range(dashes):
            start = i * step
            d.arc(box, start, start + step * 0.55, fill=color, width=width)

    def dashed_rect(self, d: ImageDraw.ImageDraw, box, color: str, radius: int = 14, dash: int = 8) -> None:
        x0, y0, x1, y1 = box
        for x in range(x0 + radius, x1 - radius, dash * 2):
            d.line((x, y0, min(x + dash, x1 - radius), y0), fill=color, width=1)
            d.line((x, y1, min(x + dash, x1 - radius), y1), fill=color, width=1)
        for y in range(y0 + radius, y1 - radius, dash * 2):
            d.line((x0, y, x0, min(y + dash, y1 - radius)), fill=color, width=1)
            d.line((x1, y, x1, min(y + dash, y1 - radius)), fill=color, width=1)
        for cx, cy, a0 in (
            (x0 + radius, y0 + radius, 180),
            (x1 - radius, y0 + radius, 270),
            (x1 - radius, y1 - radius, 0),
            (x0 + radius, y1 - radius, 90),
        ):
            d.arc((cx - radius, cy - radius, cx + radius, cy + radius), a0 + 15, a0 + 75, fill=color, width=1)

    def text(self, d, xy, s, font, fill, anchor="la", strike=False):
        d.text(xy, s, font=font, fill=fill, anchor=anchor)
        if strike:
            left, top, right, bottom = d.textbbox(xy, s, font=font, anchor=anchor)
            ym = (top + bottom) / 2 + 1
            d.line((left, ym, right, ym), fill=fill, width=1)

    # ---------- main ----------
    def render(self, v: MatchView) -> bytes:
        img = Image.new("RGBA", (W, H), BG)
        d = ImageDraw.Draw(img)
        self._header(d, v)
        a_player, b_player = v.side_a, v.side_b
        self._panel(img, d, v, "A", a_player, 24)
        self._panel(img, d, v, "B", b_player, W - 24 - 480)
        self._center(d, v, 520, 760)
        self._footer(d, v)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        return buf.getvalue()

    def _header(self, d: ImageDraw.ImageDraw, v: MatchView) -> None:
        d.rectangle((0, 0, W, 64), fill=HEADER_BG)
        d.line((0, 64, W, 64), fill=LINE)
        f_title = self.fonts.display(22)
        title = self.fit("Friendly duel" if v.is_friendly else v.tournament.name, f_title, 330)
        d.text((24, 32), title, font=f_title, fill=TEXT, anchor="lm")
        x = 24 + f_title.getlength(title) + 14
        g = v.game
        if v.is_friendly:  # no round, bracket or series: just which draft this is
            sub = f"Draft {g.number}" if g else ""
            sub += " · Fearless" if v.tournament.fearless else ""
        else:
            game_txt = f"Game {g.number}" + (f" (replay {g.attempt - 1})" if g and g.attempt > 1 else "") if g else ""
            players = (v.tournament.bracket_size or 2) >> (v.match.round - 1)
            sub = f"{round_name(players)} · Match {v.match.slot + 1}" + (f" · {game_txt}" if game_txt else "")
            sub += f" · Bo{v.match.bo}"
        d.text((x, 33), self.fit(sub, self.fonts.inter(15), 470), font=self.fonts.inter(15), fill=MUTED, anchor="lm")
        a, b = v.side_a, v.side_b
        if a and b:
            f_n, f_s = self.fonts.inter(15, "semibold"), self.fonts.display(22)
            score = "vs" if v.is_friendly else f"{v.score_of(a.id)} – {v.score_of(b.id)}"
            b_name = self.fit(b.name, f_n, 130)
            a_name = self.fit(a.name, f_n, 130)
            x = W - 24
            d.text((x, 32), b_name, font=f_n, fill=SIDE["B"]["lt"], anchor="rm")
            x -= f_n.getlength(b_name) + 12
            d.text((x, 32), score, font=f_s, fill=TEXT, anchor="rm")
            x -= f_s.getlength(score) + 12
            d.text((x, 32), a_name, font=f_n, fill=SIDE["A"]["lt"], anchor="rm")
            x -= f_n.getlength(a_name) + 12
            if not v.is_friendly:
                d.text((x, 32), "Series", font=self.fonts.inter(15), fill=MUTED, anchor="rm")

    def _panel(self, img, d, v: MatchView, side: str, player, x: int) -> None:
        y0, y1 = 88, H - 36 - 24
        sc = SIDE[side]
        d.rounded_rectangle((x, y0, x + 480, y1), radius=16, fill=sc["c"])
        d.rounded_rectangle((x, y0 + 4, x + 480, y1), radius=15, fill=PANEL)
        d.rectangle((x + 1, y0 + 4, x + 479, y0 + 18), fill=PANEL)
        inner_l, inner_r = x + 20, x + 460
        right = side == "B"
        # name row
        cy = y0 + 4 + 20 + 22
        name = player.name if player else "TBD"
        av_x = inner_r - 44 if right else inner_l
        d.ellipse((av_x, cy - 22, av_x + 44, cy + 22), fill=sc["c"])
        d.text((av_x + 22, cy), name[:1].upper(), font=self.fonts.inter(18, "bold"), fill=BG, anchor="mm")
        f_name = self.fonts.inter(22, "bold")
        f_lab = self.fonts.inter(12, "bold")
        tx = av_x - 12 if right else av_x + 44 + 12
        anchor_n, anchor_l = ("rs", "ra") if right else ("ls", "la")
        d.text((tx, cy + 3), self.fit(name, f_name, 340), font=f_name, fill=TEXT, anchor=anchor_n)
        d.text((tx, cy + 7), sc["label"], font=f_lab, fill=sc["lt"], anchor=anchor_l)
        # bans
        by = cy + 22 + 18
        d.text((inner_l, by + 26), "BANS", font=f_lab, fill=MUTED, anchor="lm")
        bans = v.bans(side)
        bx0 = inner_l + 58
        each = (inner_r - bx0) // max(1, len(bans))
        for i, slot in enumerate(bans):
            self._ban(img, d, slot, sc, bx0 + i * each, by, each - 10)
        # picks
        py = by + 56 + 18
        d.text((inner_l, py), "PICKS", font=f_lab, fill=MUTED, anchor="la")
        gy = py + 24
        picks = v.picks(side)
        cols = 2
        rows = max(1, math.ceil(len(picks) / cols))
        gap = 12
        cw = (440 - gap * (cols - 1)) // cols
        ch = (y1 - 20 - gy - gap * (rows - 1)) // rows
        for i, slot in enumerate(picks):
            r_, c_ = divmod(i, cols)
            cx0 = inner_l + c_ * (cw + gap)
            cy0 = gy + r_ * (ch + gap)
            self._card(img, d, slot, sc, (cx0, cy0, cx0 + cw, cy0 + ch), v)

    def _ban(self, img, d, slot: SlotView, sc, x: int, y: int, width: int) -> None:
        box = (x, y, x + 52, y + 52)
        f_txt, f_small = self.fonts.inter(15, "semibold"), self.fonts.inter(11, "bold")
        label = f"BAN {slot.index}"
        if slot.state == "done" and slot.aniimo and not slot.skipped:
            img.alpha_composite(self.thumbs.get(slot.aniimo, 52, banned=True), (x, y))
            d.line((x + 15, y + 15, x + 37, y + 37), fill=BAN_X, width=4)
            d.line((x + 37, y + 15, x + 15, y + 37), fill=BAN_X, width=4)
            self.text(
                d,
                (x + 62, y + 24),
                self.fit(slot.aniimo.name, f_txt, width - 64),
                f_txt,
                "#B4BAD6",
                anchor="ls",
                strike=True,
            )
            label += " · AUTO" if slot.auto else ""
        elif slot.state == "done":
            self.dashed_circle(d, box, DASH)
            d.text((x + 62, y + 24), "No ban", font=f_txt, fill=FAINT, anchor="ls")
            label += " · TIME RAN OUT" if slot.auto else ""
        elif slot.state == "active":
            d.ellipse((x - 4, y - 4, x + 56, y + 56), outline=blend(sc["c"], PANEL, 0.25), width=4)
            d.ellipse(box, outline=sc["c"], width=2)
            d.text((x + 62, y + 24), "Banning…", font=self.fonts.inter(15, "bold"), fill=sc["lt"], anchor="ls")
        else:
            self.dashed_circle(d, box, DASH)
            d.text((x + 62, y + 24), "—", font=f_txt, fill=FAINT, anchor="ls")
        d.text((x + 62, y + 30), label, font=f_small, fill=MUTED, anchor="la")

    def _card(self, img, d, slot: SlotView, sc, box, v: MatchView) -> None:
        x0, y0, x1, y1 = box
        if slot.state == "done":
            d.rounded_rectangle(box, radius=14, fill=CARD, outline=CARD_LINE)
        elif slot.state == "active":
            d.rounded_rectangle((x0 - 4, y0 - 4, x1 + 4, y1 + 4), radius=18, fill=blend(sc["c"], PANEL, 0.22))
            d.rounded_rectangle(box, radius=14, fill=blend(sc["c"], PANEL, 0.10), outline=sc["c"], width=2)
        else:
            self.dashed_rect(d, box, DASH)
        f_lab = self.fonts.inter(12, "bold")
        d.text((x0 + 16, y0 + 14), f"PICK {slot.index}", font=f_lab, fill=MUTED, anchor="la")
        if slot.state == "done" and slot.auto:
            pill = (x1 - 16 - 50, y0 + 11, x1 - 16, y0 + 31)
            d.rounded_rectangle(pill, radius=6, fill=SOFT)
            d.text(
                ((pill[0] + pill[2]) / 2, (pill[1] + pill[3]) / 2),
                "AUTO",
                font=self.fonts.inter(11, "bold"),
                fill=BG,
                anchor="mm",
            )
        inner_top = y0 + 38
        avail_h = y1 - inner_top - 12
        if slot.state == "done" and slot.aniimo:
            size = max(48, min(84, avail_h))
            ty = inner_top + (avail_h - size) // 2
            img.alpha_composite(self.thumbs.get(slot.aniimo, size), (x0 + 16, ty))
            tx = x0 + 16 + size + 14
            max_w = x1 - tx - 12
            for fs in (19, 17, 15, 14):
                f_name = self.fonts.inter(fs, "bold")
                if f_name.getlength(slot.aniimo.name) <= max_w:
                    break
            d.text(
                (tx, ty + size / 2 - 4), self.fit(slot.aniimo.name, f_name, max_w), font=f_name, fill=TEXT, anchor="ls"
            )
            icon_y = ty + size / 2 + 14
            ix = tx
            for el in slot.aniimo.elements:  # element signs, e.g. a flame and a moon for Fire / Dark
                icon = self.icons.get(el, 22)
                img.alpha_composite(icon, (round(ix), round(icon_y - icon.height / 2)))
                ix += icon.width + 4
            f_el = self.fonts.inter(13)
            if len(slot.aniimo.elements) == 1:
                label = self.fit(slot.aniimo.element_label, f_el, max(20, x1 - ix - 16))
                d.text((ix + 4, icon_y), label, font=f_el, fill=SOFT, anchor="lm")
            else:  # two signs leave no room beside them: the names go on the next line
                label = self.fit(slot.aniimo.element_label, self.fonts.inter(12), x1 - tx - 12)
                d.text((tx, icon_y + 21), label, font=self.fonts.inter(12), fill=SOFT, anchor="lm")
        elif slot.state == "done":
            d.text((x0 + 16, inner_top + 18), "Skipped", font=self.fonts.inter(14), fill=FAINT, anchor="la")
        elif slot.state == "active":
            who = v.turn_player.name if v.turn_player else ""
            d.text((x0 + 16, inner_top + 18), "Picking…", font=self.fonts.inter(22, "bold"), fill=sc["lt"], anchor="la")
            d.text(
                (x0 + 16, inner_top + 50),
                self.fit(f"{who} is choosing", self.fonts.inter(13), x1 - x0 - 32),
                font=self.fonts.inter(13),
                fill=SOFT,
                anchor="la",
            )
        else:
            d.text((x0 + 16, inner_top + 18), "Waiting", font=self.fonts.inter(14), fill=FAINT, anchor="la")

    def _center(self, d, v: MatchView, x0: int, x1: int) -> None:
        y = 88
        paused = v.match.state == "paused"
        turn = v.turn
        if turn is None:
            border, fill, name_col = "#3A4170", PANEL, TEXT
            n = len(v.order)
            lines = ("DRAFT COMPLETE", "Good luck!", "Play the game, then report", f"{n} of {n} steps done")
        else:
            sc = SIDE[turn[0]]
            border, fill, name_col = sc["c"], blend(sc["c"], BG, 0.14), sc["lt"]
            who = v.turn_player.name.upper() if v.turn_player else "?"
            verb = "is banning" if turn[1] == "ban" else "is picking"
            phase = v.phase.upper()
            if paused:
                phase, verb = "PAUSED BY AN ADMIN", f"{verb} (paused)"
            lines = (phase, who, verb, f"Step {v.draft.step + 1} of {len(v.order)}")
        box = (x0, y, x1, y + 112)
        d.rounded_rectangle(box, radius=14, fill=fill, outline=border)
        d.text((x0 + 16, y + 14), lines[0], font=self.fonts.inter(11, "bold"), fill=SOFT, anchor="la")
        f_name = self.fonts.display(30 if turn else 26)
        d.text((x0 + 16, y + 30), self.fit(lines[1], f_name, x1 - x0 - 32), font=f_name, fill=name_col, anchor="la")
        d.text((x0 + 16, y + 70), lines[2], font=self.fonts.inter(15), fill=TEXT, anchor="la")
        d.text((x0 + 16, y + 90), lines[3], font=self.fonts.inter(12), fill=MUTED, anchor="la")
        # tracker
        steps = v.steps()
        top = y + 112 + 14
        bottom = H - 36 - 24
        phase_gaps = sum(1 for i in range(1, len(steps)) if steps[i].kind != steps[i - 1].kind)
        row_h = min(30, (bottom - top - phase_gaps * 8 - (len(steps) - 1) * 4) // max(1, len(steps)))
        yy = top
        for i, st in enumerate(steps):
            if i and st.kind != steps[i - 1].kind:
                yy += 8
            self._step_row(d, st, x0, x1, yy, row_h)
            yy += row_h + 4

    def _step_row(self, d, st: StepView, x0: int, x1: int, y: int, h: int) -> None:
        sc = SIDE[st.side]
        if st.state == "active":
            d.rounded_rectangle((x0, y, x1, y + h), radius=8, fill=blend(sc["c"], BG, 0.16), outline=sc["c"])
        cy = y + h // 2
        r = min(10, h // 2 - 1)
        dot = (x0 + 10, cy - r, x0 + 10 + 2 * r, cy + r)
        f_num = self.fonts.inter(11, "bold")
        if st.state == "done":
            d.ellipse(dot, fill=sc["c"])
            num_col = BG
        elif st.state == "active":
            d.ellipse(dot, outline=sc["c"], width=2)
            num_col = sc["lt"]
        else:
            d.ellipse(dot, outline="#3A4170", width=1)
            num_col = MUTED
        d.text(((dot[0] + dot[2]) / 2, cy), str(st.num), font=f_num, fill=num_col, anchor="mm")
        tag_col = MUTED if st.state == "empty" else sc["lt"]
        d.text(
            (x0 + 40, cy), f"{st.side} {st.kind.upper()}", font=self.fonts.inter(11, "bold"), fill=tag_col, anchor="lm"
        )
        f = self.fonts.inter(13, "semibold" if st.state != "empty" else "regular")
        max_w = x1 - (x0 + 98) - 8
        if st.state == "done":
            if st.skipped:
                txt = ("no ban" if st.kind == "ban" else "skipped") + (" · auto" if st.auto else "")
                col, strike = FAINT, False
            else:
                txt = (st.aniimo.name if st.aniimo else "?") + (" · auto" if st.auto else "")
                col, strike = (MUTED, True) if st.kind == "ban" else (TEXT, False)
        elif st.state == "active":
            txt, col, strike = "now", sc["lt"], False
        else:
            txt, col, strike = "—", FAINT, False
        self.text(d, (x0 + 98, cy), self.fit(txt, f, max_w), f, col, anchor="lm", strike=strike)

    def _footer(self, d, v: MatchView) -> None:
        d.line((0, H - 36, W, H - 36), fill=LINE)
        bans = sum(1 for s, k in v.order if s == "A" and k == "ban")
        picks = sum(1 for s, k in v.order if s == "A" and k == "pick")
        fear = "Fearless on" if v.tournament.fearless else "Fearless off"
        f = self.fonts.inter(12)
        d.text(
            (24, H - 18),
            f"Exclusive picks · {bans} bans + {picks} picks per player · {fear}",
            font=f,
            fill="#8C93B5",
            anchor="lm",
        )
        d.text((W - 24, H - 18), "AUTO = time ran out, picked at random", font=f, fill="#8C93B5", anchor="rm")
