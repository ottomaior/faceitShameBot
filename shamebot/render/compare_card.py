"""Head-to-head card: identity header, verdict banner, one block per category with a split bar."""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from ..compare import Category, CompareResult
from ..leetify import ATTRIBUTION
from . import primitives as P
from . import theme as T

W = 1200
MARGIN = 40
CENTER = W // 2


@dataclass
class CompareSide:
    name: str
    avatar: bytes | None
    level: int | None
    elo: int | None
    title: str = ""
    has_leetify: bool = True


def _side_color(side: int, winner: int) -> tuple:
    if winner == 0:
        return T.MUTED
    return T.GOOD if side == winner else T.MUTED


def _category_block(draw: ImageDraw.ImageDraw, y: int, c: Category, res: CompareResult) -> int:
    metrics = [m for m in c.metrics if m.a is not None or m.b is not None]
    h = 46 + len(metrics) * 24 + 12
    P.rounded(draw, (MARGIN, y, W - MARGIN, y + h), 12, T.PANEL + (255,))

    # header: name + weight pips (left), split bar (centre), winner tag (right)
    name_f = T.font("bold", 17)
    P.draw_text(draw, (MARGIN + 18, y + 22), c.name, name_f, T.TEXT, anchor="lm")  # no emoji: Pillow has no colour-emoji font
    px = MARGIN + 18 + P.text_w(draw, c.name, name_f) + 12
    for i in range(c.weight):
        draw.ellipse((px + i * 12, y + 17, px + i * 12 + 8, y + 25), fill=T.MUTED)

    bar_w, bar_h = 320, 12
    bx0, by0 = CENTER - bar_w // 2, y + 16
    P.rounded(draw, (bx0, by0, bx0 + bar_w, by0 + bar_h), 6, (0, 0, 0, 120))
    s = c.score or 0.0
    fill = int(min(1.0, abs(s)) * (bar_w // 2))
    if c.winner == -1:
        P.rounded(draw, (CENTER - max(fill, 8), by0, CENTER, by0 + bar_h), 6, T.GOOD + (255,))
    elif c.winner == 1:
        P.rounded(draw, (CENTER, by0, CENTER + max(fill, 8), by0 + bar_h), 6, T.GOOD + (255,))
    else:
        P.rounded(draw, (CENTER - 6, by0, CENTER + 6, by0 + bar_h), 6, T.DIM + (255,))
    draw.rectangle((CENTER - 1, by0 - 3, CENTER + 1, by0 + bar_h + 3), fill=T.TEXT)

    tag = "TIE" if c.winner == 0 else (f"+{c.weight} {res.a_name if c.winner == -1 else res.b_name}")
    tf = T.font("semibold", 12)
    tw = P.text_w(draw, tag.upper(), tf) + 20
    P.pill(draw, W - MARGIN - 18 - tw, y + 10, tag.upper(), tf, fill=(T.GOOD if c.winner else T.DIM) + (40,), color=T.GOOD if c.winner else T.MUTED, height=24, pad_x=10)

    # metric rows
    my = y + 50
    lf = T.font("regular", 13)
    vf = T.font("semibold", 15)
    for m in metrics:
        label = m.label + ("  ↓" if not m.higher_better else "")
        P.draw_text(draw, (CENTER, my + 10), label, lf, T.MUTED, anchor="mm")
        ca = T.GOOD if m.better == -1 else (T.DIM if m.a is None else T.TEXT)
        cb = T.GOOD if m.better == 1 else (T.DIM if m.b is None else T.TEXT)
        P.draw_text(draw, (CENTER - 190, my + 10), m.text("a"), vf, ca, anchor="rm")
        P.draw_text(draw, (CENTER + 190, my + 10), m.text("b"), vf, cb, anchor="lm")
        if m.source == "leetify":
            P.draw_text(draw, (MARGIN + 18, my + 10), "leetify", T.font("semibold", 10), T.DIM, anchor="lm")
        my += 24
    return y + h + 10


def render_compare(left: CompareSide, right: CompareSide, res: CompareResult, *, subtitle: str, footer: str = "") -> bytes:
    header_h = 232
    banner_h = 96
    body_h = sum(46 + len([m for m in c.metrics if m.a is not None or m.b is not None]) * 24 + 22 for c in res.categories)
    total_h = header_h + banner_h + 24 + body_h + 40

    img = Image.new("RGB", (W, total_h), T.BG)
    win_pal = T.REDEMPTION if res.winner else T.NEUTRAL
    img.paste(P.background((W, header_h + banner_h), None, T.NEUTRAL.accent_dark).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle((0, 0, W, 6), fill=T.NEUTRAL.accent)
    P.draw_text(draw, (CENTER, 24), "HEAD TO HEAD", T.font("display", 40), T.NEUTRAL.accent, anchor="ma", shadow=True)
    P.draw_text(draw, (CENTER, 70), subtitle, T.font("semibold", 15), T.MUTED, anchor="ma")

    for side, x, anchor, idx in ((left, MARGIN, "l", -1), (right, W - MARGIN, "r", 1)):
        ring = T.GOOD if res.winner == idx else T.level_color(side.level)
        av = P.circle_image(side.avatar, 120, ring=ring + (255,), ring_w=5)
        ax = x if anchor == "l" else x - 120
        P.blit(img, av, (ax, 96))
        P.blit(img, P.level_badge(side.level, 38), (ax + 120 - 36, 96 + 120 - 36))
        draw = ImageDraw.Draw(img, "RGBA")
        tx = x + 138 if anchor == "l" else x - 138
        nf = T.font("bold", 36)
        P.draw_text(draw, (tx, 104), P.fit_text(draw, side.name, nf, 330), nf, T.TEXT, anchor=f"{anchor}a", shadow=True)
        elo = "—" if side.elo is None else f"{side.elo:,} ELO".replace(",", " ")
        P.draw_text(draw, (tx, 152), elo, T.font("semibold", 17), T.MUTED, anchor=f"{anchor}a")
        line3 = side.title + ("" if side.has_leetify else "  ·  no Leetify profile")
        P.draw_text(draw, (tx, 178), line3, T.font("regular", 14), T.DIM, anchor=f"{anchor}a")

    # verdict banner
    by = header_h
    P.rounded(draw, (MARGIN, by, W - MARGIN, by + banner_h - 12), 14, (0, 0, 0, 120), outline=win_pal.accent + (160,), width=2)
    if res.winner:
        P.draw_text(draw, (MARGIN + 24, by + 30), res.winner_name.upper(), T.font("display", 40), win_pal.accent, anchor="lm", shadow=True)
        P.draw_text(draw, (W - MARGIN - 24, by + 30), f"{res.points_a if res.winner == -1 else res.points_b} – {res.points_b if res.winner == -1 else res.points_a}", T.font("display", 40), T.TEXT, anchor="rm")
        P.draw_text(draw, (CENTER, by + 30), f"{res.margin_label.upper()} WIN", T.font("display", 26), T.MUTED, anchor="mm")
    else:
        P.draw_text(draw, (MARGIN + 24, by + 30), "DEAD EVEN", T.font("display", 40), T.MUTED, anchor="lm")
        P.draw_text(draw, (W - MARGIN - 24, by + 30), f"{res.points_a} – {res.points_b}", T.font("display", 40), T.TEXT, anchor="rm")
    if res.reasons:
        P.draw_text(draw, (MARGIN + 24, by + 64), P.fit_text(draw, "   ·   ".join(res.reasons), T.font("regular", 14), W - MARGIN * 2 - 48), T.font("regular", 14), T.TEXT, anchor="lm")

    y = header_h + banner_h + 12
    for c in res.categories:
        y = _category_block(draw, y, c, res)

    P.draw_text(draw, (MARGIN, total_h - 16), f"{ATTRIBUTION}  ·  FaceIT Data API", T.font("semibold", 12), T.DIM, anchor="lm")
    if footer:
        P.draw_text(draw, (W - MARGIN, total_h - 16), footer, T.font("semibold", 12), T.DIM, anchor="rm")
    return P.to_png(img)
