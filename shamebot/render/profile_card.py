"""Profile card for /profile: identity, ELO trend, averages, form and map summary."""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from ..stats import MapStat, PlayerAggregate
from . import primitives as P
from . import theme as T

W = 1200
H = 560
MARGIN = 40


@dataclass
class ProfileData:
    nickname: str
    avatar: bytes | None
    level: int | None
    elo: int | None
    country: str | None
    elo_7d: int | None
    elo_30d: int | None
    elo_history: list[int]  # oldest -> newest
    recent: PlayerAggregate  # last N games
    alltime: PlayerAggregate
    maps: list[MapStat] = field(default_factory=list)
    window: int = 30


def _delta(v: int | None) -> tuple[str, tuple]:
    if v is None:
        return "—", T.MUTED
    return f"{v:+d}", (T.GOOD if v >= 0 else T.BAD)


def render_profile(data: ProfileData, *, footer: str = "") -> bytes:
    pal = T.NEUTRAL if data.recent.shame_rate < 25 else T.SHAME
    img = Image.new("RGB", (W, H), T.BG)
    img.paste(P.background((W, 250), None, pal.accent_dark).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle((0, 0, W, 6), fill=pal.accent)

    # identity
    av = P.circle_image(data.avatar, 150, ring=T.level_color(data.level) + (255,), ring_w=5)
    P.blit(img, av, (MARGIN, 40))
    P.blit(img, P.level_badge(data.level, 48), (MARGIN + 150 - 46, 40 + 150 - 46))
    draw = ImageDraw.Draw(img, "RGBA")
    x = 222
    P.draw_text(draw, (x, 44), P.fit_text(draw, data.nickname, T.font("bold", 46), 480), T.font("bold", 46), T.TEXT, shadow=True)
    sub = [f"Level {data.level}" if data.level is not None else "Level —"]
    if data.country:
        sub.append(data.country.upper())
    sub.append(f"{data.alltime.games} matches on record")
    P.draw_text(draw, (x, 104), "  ·  ".join(sub), T.font("semibold", 16), T.MUTED)

    # titles
    tf = T.font("semibold", 13)
    px = x
    for label, color, soft in (
        (data.recent.title, pal.accent, pal.accent_soft),
        (data.alltime.glory_title, T.GLORY.accent, T.GLORY.accent_soft),
    ):
        px += P.pill(draw, px, 138, label.upper(), tf, fill=soft + (255,), color=color, height=26, pad_x=12, outline=color + (140,)) + 10

    # ELO block (right)
    ex = W - MARGIN
    P.draw_text(draw, (ex, 48), "—" if data.elo is None else f"{data.elo:,}".replace(",", " "), T.font("display", 96), pal.accent, anchor="ra", shadow=True)
    P.draw_text(draw, (ex, 150), "FACEIT ELO", T.font("display", 22), T.MUTED, anchor="ra")
    d7, c7 = _delta(data.elo_7d)
    d30, c30 = _delta(data.elo_30d)
    P.draw_text(draw, (ex - 150, 186), d7, T.font("bold", 20), c7, anchor="ra")
    P.draw_text(draw, (ex - 150, 210), "7 DAYS", T.font("semibold", 11), T.MUTED, anchor="ra")
    P.draw_text(draw, (ex, 186), d30, T.font("bold", 20), c30, anchor="ra")
    P.draw_text(draw, (ex, 210), "30 DAYS", T.font("semibold", 11), T.MUTED, anchor="ra")
    if len(data.elo_history) >= 2:
        P.sparkline(draw, (ex - 560, 60, ex - 300, 150), data.elo_history[-40:], color=pal.accent + (255,))
        P.draw_text(draw, (ex - 300, 160), "ELO TREND", T.font("semibold", 11), T.MUTED, anchor="ra")

    # tiles
    r, a = data.recent, data.alltime
    tiles = [
        (str(r.games), f"last {data.window}"),
        (f"{r.win_rate}%" if r.win_rate is not None else "—", "win rate"),
        (f"{r.avg_kills:.1f}" if r.avg_kills is not None else "—", "avg kills"),
        (f"{r.avg_kd:.2f}" if r.avg_kd is not None else "—", "avg k/d"),
        (f"{r.avg_adr:.0f}" if r.avg_adr is not None else "—", "avg adr"),
        (f"{r.avg_hs:.0f}%" if r.avg_hs is not None else "—", "hs%"),
        (f"{r.shame_count}/{r.games}", "on the wall"),
        (f"{r.shame_rate}%", "shame rate"),
    ]
    ty = 270
    gap = 12
    tw = (W - MARGIN * 2 - gap * (len(tiles) - 1)) / len(tiles)
    for i, (val, label) in enumerate(tiles):
        tx = MARGIN + i * (tw + gap)
        col = T.TEXT
        if label == "shame rate":
            col = T.BAD if r.shame_rate >= 30 else (T.GOOD if r.shame_rate <= 10 else T.TEXT)
        P.stat_tile(draw, (int(tx), ty, int(tx + tw), ty + 82), val, label, value_color=col, fill=T.PANEL + (255,))

    # form strips
    fy = 384
    P.draw_text(draw, (MARGIN, fy), "LAST 10 · WALL", T.font("semibold", 12), T.MUTED)
    P.form_strip(draw, MARGIN, fy + 20, r.form[:10], cell=22, gap=6)
    P.draw_text(draw, (MARGIN + 300, fy), "LAST 10 · RESULT", T.font("semibold", 12), T.MUTED)
    P.form_strip(draw, MARGIN + 300, fy + 20, r.form_wl[:10], cell=22, gap=6)

    # streak/best/worst summary
    sx = MARGIN + 600
    lines = [
        f"Current streak: {r.current_shame_streak} shame" if r.current_shame_streak else f"Clean streak: {r.current_clean_streak}",
        f"Longest shame streak: {a.longest_shame_streak}",
        f"Best game: {a.best_kills} K   ·   Worst game: {a.worst_kills if a.worst_kills is not None else '—'} K",
        f"All-time: {a.shame_count}/{a.games} on the wall ({a.shame_rate}%)",
    ]
    for j, ln in enumerate(lines):
        P.draw_text(draw, (sx, fy + j * 22), ln, T.font("regular", 14), T.TEXT if j == 0 else T.MUTED)

    # maps
    my = 470
    P.draw_text(draw, (MARGIN, my), "MAPS (ALL TIME)", T.font("semibold", 12), T.MUTED)
    my += 20
    mx = MARGIN
    for m in data.maps[:6]:
        label = m.map.replace("de_", "").capitalize()
        wr = f"{m.win_rate}%" if m.win_rate is not None else "—"
        txt = f"{label}  {m.games}g · {wr} W · {m.avg_kills:.0f} K · {m.shame_rate}% wall" if m.avg_kills is not None else f"{label}  {m.games}g"
        w = P.pill(draw, mx, my, txt, T.font("semibold", 13), fill=T.PANEL + (255,), color=T.TEXT, height=30, pad_x=12)
        mx += w + 10
        if mx > W - MARGIN - 200:
            break

    if footer:
        P.draw_text(draw, (W - MARGIN, H - 14), footer, T.font("semibold", 12), T.DIM, anchor="rm")
    return P.to_png(img)
