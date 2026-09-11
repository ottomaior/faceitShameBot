"""Leaderboard card: podium for the top 3 + ranked rows. Used by /wall, /glory and the weekly digest."""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from . import primitives as P
from . import theme as T

W = 1200
MARGIN = 40


@dataclass
class LeaderRow:
    nickname: str
    avatar: bytes | None
    level: int | None
    primary: str  # big number on the podium, e.g. "8/30"
    primary_label: str  # "on the wall"
    ratio: float  # 0..1 for the bar
    ratio_label: str  # "27%"
    title: str  # "Regular"
    details: list[str] = field(default_factory=list)  # small extras: "worst 1 K", "streak 3"


def _podium_slot(img: Image.Image, row: LeaderRow, rank: int, x: int, y: int, w: int, h: int, pal: T.Palette) -> None:
    draw = ImageDraw.Draw(img, "RGBA")
    P.rounded(draw, (x, y, x + w, y + h), 16, T.PANEL + (255,), outline=(255, 255, 255, 14))
    rank_col = {1: pal.accent, 2: (200, 204, 216), 3: (205, 127, 50)}[rank]
    draw.rounded_rectangle((x, y, x + w, y + 6), radius=3, fill=rank_col)

    av_size = 110 if rank == 1 else 88
    av = P.circle_image(row.avatar, av_size, ring=rank_col + (255,), ring_w=4)
    P.blit(img, av, (x + (w - av_size) // 2, y + 24))
    P.blit(img, P.level_badge(row.level, 34), (x + (w + av_size) // 2 - 30, y + 24 + av_size - 30))
    draw = ImageDraw.Draw(img, "RGBA")

    # rank medal
    P.rounded(draw, (x + 14, y + 16, x + 50, y + 52), 18, rank_col + (255,))
    P.draw_text(draw, (x + 32, y + 35), f"#{rank}", T.font("bold", 15), (20, 21, 26), anchor="mm")

    cy = y + 24 + av_size + 16
    name_f = T.font("bold", 26 if rank == 1 else 22)
    P.draw_text(draw, (x + w / 2, cy), P.fit_text(draw, row.nickname, name_f, w - 30), name_f, T.TEXT, anchor="ma")
    cy += 38 if rank == 1 else 32
    P.draw_text(draw, (x + w / 2, cy), row.primary, T.font("display", 54 if rank == 1 else 44), pal.accent, anchor="ma")
    cy += 62 if rank == 1 else 50
    P.draw_text(draw, (x + w / 2, cy), f"{row.primary_label}  ·  {row.ratio_label}", T.font("semibold", 13), T.MUTED, anchor="ma")
    cy += 26
    tf = T.font("semibold", 13)
    tw = P.text_w(draw, row.title.upper(), tf) + 22
    P.pill(draw, x + (w - tw) / 2, cy, row.title.upper(), tf, fill=pal.accent_soft + (255,), color=pal.accent, height=24, pad_x=11, outline=pal.accent + (140,))


def render_leaderboard(
    rows: list[LeaderRow],
    palette: T.Palette,
    *,
    headline: str,
    subtitle: str,
    footer: str = "",
    extra_lines: list[str] | None = None,
) -> bytes:
    podium = rows[:3]
    rest = rows
    header_h = 100
    podium_h = 330 if podium else 0
    row_h = 46
    extra = extra_lines or []
    rows_h = 30 + len(rest) * row_h + 16
    extra_h = (len(extra) * 24 + 20) if extra else 0
    total_h = header_h + podium_h + rows_h + extra_h + 40

    img = Image.new("RGB", (W, total_h), T.BG)
    img.paste(P.background((W, header_h + podium_h + 30), None, palette.accent_dark).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle((0, 0, W, 6), fill=palette.accent)
    P.draw_text(draw, (MARGIN, 26), headline, T.font("display", 42), palette.accent, shadow=True)
    P.draw_text(draw, (MARGIN, 74), subtitle, T.font("semibold", 17), T.MUTED)

    # podium: #2 left, #1 centre (taller), #3 right
    if podium:
        slot_w = 300
        gap = 24
        total_w = slot_w * min(3, len(podium)) + gap * (min(3, len(podium)) - 1)
        x_start = (W - total_w) // 2
        order = [(2, 0), (1, 1), (3, 2)] if len(podium) == 3 else [(1, 0), (2, 1)][: len(podium)]
        if len(podium) == 1:
            order = [(1, 0)]
        for rank, slot in order:
            row = podium[rank - 1]
            h = 318 if rank == 1 else 290
            y = header_h + (podium_h - h) - 10
            _podium_slot(img, row, rank, x_start + slot * (slot_w + gap), y, slot_w, h, palette)

    # ranked rows
    y = header_h + podium_h + 20
    draw = ImageDraw.Draw(img, "RGBA")
    hf = T.font("semibold", 12)
    P.draw_text(draw, (MARGIN + 60, y), "PLAYER", hf, T.DIM)
    P.draw_text(draw, (MARGIN + 430, y), rows[0].primary_label.upper() if rows else "", hf, T.DIM)
    P.draw_text(draw, (W - MARGIN - 250, y), "TITLE", hf, T.DIM, anchor="ra")
    P.draw_text(draw, (W - MARGIN, y), "DETAILS", hf, T.DIM, anchor="ra")
    y += 24
    for i, row in enumerate(rest):
        box = (MARGIN, y, W - MARGIN, y + row_h - 6)
        P.rounded(draw, box, 10, (T.PANEL_ALT if i % 2 else T.PANEL) + (255,))
        rank_col = palette.accent if i == 0 else T.MUTED
        P.draw_text(draw, (MARGIN + 30, y + 20), str(i + 1), T.font("display", 24), rank_col, anchor="mm")
        av = P.circle_image(row.avatar, 28)
        P.blit(img, av, (MARGIN + 52, y + 6))
        draw = ImageDraw.Draw(img, "RGBA")
        P.draw_text(draw, (MARGIN + 90, y + 20), P.fit_text(draw, row.nickname, T.font("semibold", 17), 300), T.font("semibold", 17), T.TEXT, anchor="lm")
        # bar
        bx0, bx1 = MARGIN + 430, MARGIN + 600
        P.rounded(draw, (bx0, y + 14, bx1, y + 26), 6, (0, 0, 0, 120))
        fill_w = max(12, int((bx1 - bx0) * max(0.0, min(1.0, row.ratio))))
        P.rounded(draw, (bx0, y + 14, bx0 + fill_w, y + 26), 6, palette.accent + (255,))
        P.draw_text(draw, (bx1 + 14, y + 20), f"{row.primary}  ({row.ratio_label})", T.font("semibold", 15), T.TEXT, anchor="lm")
        tf = T.font("semibold", 12)
        tw = P.text_w(draw, row.title.upper(), tf) + 20
        P.pill(draw, W - MARGIN - 250 - tw, y + 9, row.title.upper(), tf, fill=palette.accent_soft + (255,), color=palette.accent, height=22, pad_x=10)
        P.draw_text(draw, (W - MARGIN - 8, y + 20), P.fit_text(draw, "  ·  ".join(row.details), T.font("regular", 13), 236), T.font("regular", 13), T.MUTED, anchor="rm")
        y += row_h

    if extra:
        y += 10
        for line in extra:
            P.draw_text(draw, (MARGIN, y), line, T.font("regular", 15), T.TEXT)
            y += 24

    if footer:
        P.draw_text(draw, (W - MARGIN, total_h - 14), footer, T.font("semibold", 12), T.DIM, anchor="rm")
    return P.to_png(img)
