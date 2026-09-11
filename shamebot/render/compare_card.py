"""Side-by-side comparison card for /compare."""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from ..stats import PlayerAggregate
from . import primitives as P
from . import theme as T

W = 1200
MARGIN = 40


@dataclass
class CompareSide:
    agg: PlayerAggregate
    avatar: bytes | None
    level: int | None
    elo: int | None


def _metric_rows(a: PlayerAggregate, b: PlayerAggregate) -> list[tuple[str, str, str, int]]:
    """(label, left, right, winner) where winner is -1 left, 1 right, 0 tie."""

    def cmp(x, y, *, higher: bool = True) -> int:
        if x is None or y is None or x == y:
            return 0
        return (-1 if x > y else 1) if higher else (-1 if x < y else 1)

    def f(v, fmt="{}"):
        return "—" if v is None else fmt.format(v)

    return [
        ("Games", str(a.games), str(b.games), 0),
        ("Win rate", f(a.win_rate, "{}%"), f(b.win_rate, "{}%"), cmp(a.win_rate, b.win_rate)),
        ("Avg kills", f(a.avg_kills), f(b.avg_kills), cmp(a.avg_kills, b.avg_kills)),
        ("Avg K/D", f(a.avg_kd, "{:.2f}"), f(b.avg_kd, "{:.2f}"), cmp(a.avg_kd, b.avg_kd)),
        ("Avg ADR", f(a.avg_adr, "{:.0f}"), f(b.avg_adr, "{:.0f}"), cmp(a.avg_adr, b.avg_adr)),
        ("HS%", f(a.avg_hs, "{:.0f}%"), f(b.avg_hs, "{:.0f}%"), cmp(a.avg_hs, b.avg_hs)),
        ("Best game", f(a.best_kills, "{} K"), f(b.best_kills, "{} K"), cmp(a.best_kills, b.best_kills)),
        ("Worst game", f(a.worst_kills, "{} K"), f(b.worst_kills, "{} K"), cmp(a.worst_kills, b.worst_kills)),
        ("On the wall", f"{a.shame_count}/{a.games}", f"{b.shame_count}/{b.games}", cmp(a.shame_rate, b.shame_rate, higher=False)),
        ("Shame rate", f"{a.shame_rate}%", f"{b.shame_rate}%", cmp(a.shame_rate, b.shame_rate, higher=False)),
        ("Longest shame streak", str(a.longest_shame_streak), str(b.longest_shame_streak), cmp(a.longest_shame_streak, b.longest_shame_streak, higher=False)),
        ("Aces", str(a.aces), str(b.aces), cmp(a.aces, b.aces)),
    ]


def render_compare(left: CompareSide, right: CompareSide, *, subtitle: str, footer: str = "") -> bytes:
    rows = _metric_rows(left.agg, right.agg)
    header_h = 230
    row_h = 40
    total_h = header_h + 30 + len(rows) * row_h + 60
    img = Image.new("RGB", (W, total_h), T.BG)
    img.paste(P.background((W, header_h), None, T.NEUTRAL.accent_dark).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle((0, 0, W, 6), fill=T.NEUTRAL.accent)
    P.draw_text(draw, (W / 2, 26), "HEAD TO HEAD", T.font("display", 42), T.NEUTRAL.accent, anchor="ma", shadow=True)
    P.draw_text(draw, (W / 2, 74), subtitle, T.font("semibold", 16), T.MUTED, anchor="ma")

    for side, x, anchor in ((left, MARGIN, "l"), (right, W - MARGIN, "r")):
        av = P.circle_image(side.avatar, 110, ring=T.level_color(side.level) + (255,), ring_w=4)
        ax = x if anchor == "l" else x - 110
        P.blit(img, av, (ax, 100))
        P.blit(img, P.level_badge(side.level, 36), (ax + 110 - 34, 100 + 110 - 34))
        draw = ImageDraw.Draw(img, "RGBA")
        tx = x + 126 if anchor == "l" else x - 126
        P.draw_text(draw, (tx, 112), P.fit_text(draw, side.agg.nickname, T.font("bold", 34), 380), T.font("bold", 34), T.TEXT, anchor=f"{anchor}a", shadow=True)
        elo = "—" if side.elo is None else f"{side.elo:,} ELO".replace(",", " ")
        P.draw_text(draw, (tx, 160), f"{elo}  ·  {side.agg.title}", T.font("semibold", 16), T.MUTED, anchor=f"{anchor}a")

    y = header_h + 20
    wins = [0, 0]
    for i, (label, lv, rv, winner) in enumerate(rows):
        box = (MARGIN, y, W - MARGIN, y + row_h - 6)
        P.rounded(draw, box, 8, (T.PANEL_ALT if i % 2 else T.PANEL) + (255,))
        P.draw_text(draw, (W / 2, y + 17), label.upper(), T.font("semibold", 13), T.MUTED, anchor="mm")
        lc = T.GOOD if winner == -1 else T.TEXT
        rc = T.GOOD if winner == 1 else T.TEXT
        if winner == -1:
            wins[0] += 1
        elif winner == 1:
            wins[1] += 1
        P.draw_text(draw, (MARGIN + 24, y + 17), lv, T.font("display", 26), lc, anchor="lm")
        P.draw_text(draw, (W - MARGIN - 24, y + 17), rv, T.font("display", 26), rc, anchor="rm")
        y += row_h

    verdict = f"{left.agg.nickname} takes {wins[0]} · {right.agg.nickname} takes {wins[1]}"
    P.draw_text(draw, (W / 2, y + 12), verdict, T.font("semibold", 16), T.TEXT, anchor="ma")
    if footer:
        P.draw_text(draw, (W - MARGIN, total_h - 14), footer, T.font("semibold", 12), T.DIM, anchor="rm")
    return P.to_png(img)
