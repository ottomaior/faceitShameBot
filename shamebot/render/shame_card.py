"""Match card: hero section (1–2 players) + full scoreboard, one PNG. Palette-swappable."""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from ..models import MatchRecord, TrackedResult
from ..rules import award_label
from . import primitives as P
from . import theme as T

W = 1200
MARGIN = 40


@dataclass
class HeroPlayer:
    pid: str
    result: TrackedResult
    avatar: bytes | None = None
    level: int | None = None
    elo: int | None = None
    elo_delta: int | None = None
    line: str = ""  # roast / praise line
    form: list[str] = field(default_factory=list)  # newest first
    record_line: str = ""  # "8/30 on the wall (27%) · streak 1"
    avg_kd: float | None = None
    avg_adr: float | None = None


def _outcome(r: TrackedResult) -> tuple[str, tuple]:
    if r.result == 1:
        return "WIN", T.WIN
    if r.result == 0:
        return "LOSS", T.LOSS
    return "", T.MUTED


def _score(r: TrackedResult) -> str:
    if r.team_score is None or r.enemy_score is None:
        return ""
    return f"{r.team_score}–{r.enemy_score}"


def _fmt_elo(elo: int | None) -> str:
    return "—" if elo is None else f"{elo:,}".replace(",", " ")


def _subline(record: MatchRecord, r: TrackedResult) -> str:
    parts = [record.map_label]
    if _score(r):
        parts.append(_score(r))
    outcome, _ = _outcome(r)
    if outcome:
        parts.append(outcome)
    if record.competition:
        parts.append(record.competition)
    return "  ·  ".join(parts)


def _draw_elo(draw: ImageDraw.ImageDraw, x: float, y: float, hero: HeroPlayer, *, size: int = 16) -> float:
    f = T.font("semibold", size)
    w = P.pill(draw, x, y, f"{_fmt_elo(hero.elo)} ELO", f, fill=(0, 0, 0, 130), color=T.TEXT, height=size + 12)
    if hero.elo_delta is not None:
        col = T.GOOD if hero.elo_delta >= 0 else T.BAD
        w += 8 + P.pill(
            draw,
            x + w + 8,
            y,
            f"{hero.elo_delta:+d}",
            f,
            fill=(0, 0, 0, 130),
            color=col,
            height=size + 12,
        )
    return w


def _tile_color(value: float | None, avg: float | None, *, higher_is_better: bool = True) -> tuple:
    if value is None or avg is None:
        return T.TEXT
    if higher_is_better:
        return T.BAD if value < avg * 0.85 else (T.GOOD if value > avg * 1.15 else T.TEXT)
    return T.BAD if value > avg * 1.15 else (T.GOOD if value < avg * 0.85 else T.TEXT)


def _awards_row(draw: ImageDraw.ImageDraw, x: float, y: float, max_x: float, awards: list[str], pal: T.Palette) -> None:
    f = T.font("semibold", 13)
    for key in awards:
        label = award_label(key).upper()
        w = P.text_w(draw, label, f) + 22
        if x + w > max_x:
            break
        P.pill(draw, x, y, label, f, fill=pal.accent_soft + (230,), color=pal.accent, height=24, pad_x=11, outline=pal.accent + (140,))
        x += w + 8


def _hero_single(img: Image.Image, record: MatchRecord, hero: HeroPlayer, pal: T.Palette, *, headline: str) -> None:
    draw = ImageDraw.Draw(img, "RGBA")
    r = hero.result

    P.draw_text(draw, (MARGIN, 26), headline, T.font("display", 42), pal.accent, shadow=True)
    P.draw_text(draw, (MARGIN, 74), _subline(record, r), T.font("semibold", 17), T.MUTED)

    # avatar + level
    av = P.circle_image(hero.avatar, 150, ring=pal.accent + (255,), ring_w=5)
    P.blit(img, av, (MARGIN, 108))
    P.blit(img, P.level_badge(hero.level, 48), (MARGIN + 150 - 46, 108 + 150 - 46))

    # name + elo
    name_x = 222
    draw = ImageDraw.Draw(img, "RGBA")
    name = P.fit_text(draw, r.nickname, T.font("bold", 46), 520)
    P.draw_text(draw, (name_x, 112), name, T.font("bold", 46), T.TEXT, shadow=True)
    _draw_elo(draw, name_x, 176, hero, size=16)

    # giant kills
    kills_font = T.font("display", 210)
    P.draw_text(draw, (W - MARGIN, 232), str(r.kills), kills_font, pal.accent, anchor="rs", shadow=True)
    P.draw_text(draw, (W - MARGIN, 240), pal.kills_label, T.font("display", 34), T.MUTED, anchor="ra")

    if pal.stamp:
        st = P.stamp(pal.stamp, color=pal.accent, size=40, angle=-12)
        P.blit(img, st, (W - MARGIN - 420 - st.width // 2 + 120, 96))
        draw = ImageDraw.Draw(img, "RGBA")

    # stat tiles
    tiles = [
        (f"{r.kd:.2f}" if r.kd is not None else "—", "K/D", _tile_color(r.kd, hero.avg_kd)),
        (f"{r.adr:.0f}" if r.adr is not None else "—", "ADR", _tile_color(r.adr, hero.avg_adr)),
        (f"{r.hs_pct}%" if r.hs_pct is not None else "—", "HS%", T.TEXT),
        (str(r.mvps) if r.mvps is not None else "—", "MVPs", T.BAD if r.mvps == 0 else T.TEXT),
        (str(r.deaths) if r.deaths is not None else "—", "Deaths", T.BAD if (r.deaths or 0) >= 20 else T.TEXT),
    ]
    tx0, tx1, ty = name_x, W - MARGIN, 300
    gap = 12
    tw = (tx1 - tx0 - gap * (len(tiles) - 1)) / len(tiles)
    for i, (val, label, col) in enumerate(tiles):
        x = tx0 + i * (tw + gap)
        P.stat_tile(draw, (int(x), ty, int(x + tw), ty + 80), val, label, value_color=col)

    # awards
    if r.awards:
        _awards_row(draw, name_x, 396, W - MARGIN, r.awards, pal)

    # form + record (under avatar)
    P.draw_text(draw, (MARGIN, 286), "LAST 10", T.font("semibold", 12), T.MUTED)
    P.form_strip(draw, MARGIN, 304, hero.form[:10], cell=13, gap=4)
    if hero.record_line:
        for j, ln in enumerate(P.wrap_text(draw, hero.record_line, T.font("regular", 13), 170, max_lines=3)):
            P.draw_text(draw, (MARGIN, 330 + j * 17), ln, T.font("regular", 13), T.MUTED)

    # roast line
    if hero.line:
        f = T.font("italic", 21)
        lines = P.wrap_text(draw, f"“{hero.line}”", f, W - MARGIN * 2, max_lines=2)
        base_y = 438 if r.awards else 416
        for j, ln in enumerate(lines):
            P.draw_text(draw, (MARGIN, base_y + j * 28), ln, f, (255, 232, 232) if pal is T.SHAME else T.TEXT, shadow=True)


def _hero_panel(img: Image.Image, record: MatchRecord, hero: HeroPlayer, pal: T.Palette, x0: int, y0: int, w: int, h: int) -> None:
    draw = ImageDraw.Draw(img, "RGBA")
    r = hero.result
    P.rounded(draw, (x0, y0, x0 + w, y0 + h), 16, (0, 0, 0, 95), outline=(255, 255, 255, 18))
    pad = 20

    av = P.circle_image(hero.avatar, 108, ring=pal.accent + (255,), ring_w=4)
    P.blit(img, av, (x0 + pad, y0 + pad))
    P.blit(img, P.level_badge(hero.level, 38), (x0 + pad + 108 - 36, y0 + pad + 108 - 36))
    draw = ImageDraw.Draw(img, "RGBA")

    name_x = x0 + pad + 124
    name = P.fit_text(draw, r.nickname, T.font("bold", 32), 250)
    P.draw_text(draw, (name_x, y0 + pad + 4), name, T.font("bold", 32), T.TEXT, shadow=True)
    _draw_elo(draw, name_x, y0 + pad + 52, hero, size=14)

    P.draw_text(draw, (x0 + w - pad, y0 + 150), str(r.kills), T.font("display", 132), pal.accent, anchor="rs", shadow=True)
    P.draw_text(draw, (x0 + w - pad, y0 + 154), pal.kills_label, T.font("display", 24), T.MUTED, anchor="ra")
    if pal.stamp:
        st = P.stamp(pal.stamp, color=pal.accent, size=26, angle=-12, alpha=190)
        P.blit(img, st, (x0 + w - pad - 250 - st.width // 2 + 40, y0 + 104))
        draw = ImageDraw.Draw(img, "RGBA")

    tiles = [
        (f"{r.kd:.2f}" if r.kd is not None else "—", "K/D", _tile_color(r.kd, hero.avg_kd)),
        (f"{r.adr:.0f}" if r.adr is not None else "—", "ADR", _tile_color(r.adr, hero.avg_adr)),
        (f"{r.hs_pct}%" if r.hs_pct is not None else "—", "HS%", T.TEXT),
        (str(r.deaths) if r.deaths is not None else "—", "Deaths", T.BAD if (r.deaths or 0) >= 20 else T.TEXT),
    ]
    ty = y0 + 196
    gap = 10
    tw = (w - pad * 2 - gap * (len(tiles) - 1)) / len(tiles)
    for i, (val, label, col) in enumerate(tiles):
        x = x0 + pad + i * (tw + gap)
        P.stat_tile(draw, (int(x), ty, int(x + tw), ty + 68), val, label, value_color=col, value_size=26)

    if r.awards:
        _awards_row(draw, x0 + pad, ty + 80, x0 + w - pad, r.awards, pal)

    fy = ty + 118
    strip_w = P.form_strip(draw, x0 + pad, fy, hero.form[:10], cell=12, gap=4)
    if hero.record_line:
        txt = P.fit_text(draw, hero.record_line, T.font("regular", 13), w - pad * 2 - strip_w - 14)
        P.draw_text(draw, (x0 + pad + strip_w + 14, fy - 1), txt, T.font("regular", 13), T.MUTED)

    if hero.line:
        f = T.font("italic", 16)
        for j, ln in enumerate(P.wrap_text(draw, f"“{hero.line}”", f, w - pad * 2, max_lines=3)):
            P.draw_text(draw, (x0 + pad, fy + 26 + j * 22), ln, f, (255, 232, 232) if pal is T.SHAME else T.TEXT, shadow=True)


def _scoreboard(img: Image.Image, y: int, record: MatchRecord, *, highlight: dict[str, T.Palette], tracked: set[str]) -> int:
    """Draw both teams; returns the y after the section."""
    draw = ImageDraw.Draw(img, "RGBA")
    cols = [("K", 700), ("D", 770), ("A", 840), ("K/D", 930), ("ADR", 1020), ("HS%", 1095), ("MVP", 1160)]
    hf = T.font("semibold", 12)
    P.draw_text(draw, (MARGIN + 24, y), "PLAYER", hf, T.DIM)
    for label, cx in cols:
        P.draw_text(draw, (cx, y), label, hf, T.DIM, anchor="ra")
    y += 22

    rf = T.font("regular", 16)
    nf = T.font("semibold", 16)
    for team in record.teams:
        rows = team.players
        panel_h = 44 + len(rows) * 32 + 10
        P.rounded(draw, (MARGIN, y, W - MARGIN, y + panel_h), 12, T.PANEL + (255,))
        # header
        outcome_col = T.WIN if team.win else T.LOSS
        P.draw_text(draw, (MARGIN + 18, y + 22), P.fit_text(draw, team.name, T.font("bold", 18), 520), T.font("bold", 18), T.TEXT, anchor="lm")
        P.draw_text(draw, (W - MARGIN - 18, y + 24), str(team.score), T.font("display", 32), outcome_col, anchor="rm")
        chip = "WIN" if team.win else "LOSS"
        P.pill(draw, W - MARGIN - 18 - 56 - P.text_w(draw, chip, T.font("bold", 12)) - 22, y + 11, chip, T.font("bold", 12), fill=outcome_col + (40,), color=outcome_col, height=22, pad_x=11)
        ry = y + 44
        for i, row in enumerate(sorted(rows, key=lambda p: p.k, reverse=True)):
            box = (MARGIN + 10, ry, W - MARGIN - 10, ry + 32)
            pal = highlight.get(row.pid)
            if pal is not None:
                P.rounded(draw, box, 8, pal.accent_soft + (255,))
                draw.rounded_rectangle((box[0], ry + 4, box[0] + 4, ry + 28), radius=2, fill=pal.accent)
                name_col = T.TEXT
            elif row.pid in tracked:
                P.rounded(draw, box, 8, (34, 40, 60, 255))
                name_col = T.TEXT
            elif i % 2 == 1:
                P.rounded(draw, box, 8, T.PANEL_ALT + (255,))
                name_col = (210, 212, 220)
            else:
                name_col = (210, 212, 220)
            P.blit(img, P.level_badge(row.level, 22), (MARGIN + 22, ry + 5))
            draw = ImageDraw.Draw(img, "RGBA")
            P.draw_text(draw, (MARGIN + 52, ry + 16), P.fit_text(draw, row.nick, nf if row.pid in tracked else rf, 560), nf if row.pid in tracked else rf, name_col, anchor="lm")
            kd_col = T.GOOD if row.kd >= 1.2 else (T.BAD if row.kd < 0.7 else T.TEXT)
            adr_col = T.GOOD if row.adr >= 90 else (T.BAD if row.adr < 50 else T.TEXT)
            values = [
                (str(row.k), 700, T.TEXT),
                (str(row.d), 770, T.TEXT),
                (str(row.a), 840, T.TEXT),
                (f"{row.kd:.2f}", 930, kd_col),
                (f"{row.adr:.0f}", 1020, adr_col),
                (f"{row.hs}%", 1095, T.TEXT),
                (str(row.mvp), 1160, T.TEXT),
            ]
            for val, cx, col in values:
                P.draw_text(draw, (cx, ry + 16), val, rf, col, anchor="rm")
            ry += 32
        y += panel_h + 12
    return y


def render_match_card(
    record: MatchRecord,
    heroes: list[HeroPlayer],
    palette: T.Palette,
    *,
    map_bytes: bytes | None,
    tracked: set[str],
    headline: str | None = None,
    footer: str = "",
) -> bytes:
    multi = len(heroes) > 1
    hero_h = 540 if multi else 500
    board_h = 22 + sum(44 + len(t.players) * 32 + 10 + 12 for t in record.teams) + 18
    total_h = hero_h + 20 + board_h + 26

    # RGB canvas: ImageDraw.Draw(img, "RGBA") then *blends* translucent fills instead of overwriting.
    img = Image.new("RGB", (W, total_h), T.BG)
    img.paste(P.background((W, hero_h), map_bytes, palette.accent_dark).convert("RGB"), (0, 0))
    # accent bar on the very top
    ImageDraw.Draw(img).rectangle((0, 0, W, 6), fill=palette.accent)

    head = headline or palette.headline
    if multi:
        draw = ImageDraw.Draw(img, "RGBA")
        P.draw_text(draw, (MARGIN, 26), head, T.font("display", 42), palette.accent, shadow=True)
        P.draw_text(draw, (MARGIN, 74), _subline(record, heroes[0].result), T.font("semibold", 17), T.MUTED)
        panel_w = (W - MARGIN * 2 - 24) // 2 if len(heroes) == 2 else (W - MARGIN * 2 - 48) // 3
        for i, hero in enumerate(heroes[:3]):
            x0 = MARGIN + i * (panel_w + 24)
            _hero_panel(img, record, hero, palette, x0, 108, panel_w, hero_h - 108 - 18)
    else:
        _hero_single(img, record, heroes[0], palette, headline=head)

    _scoreboard(img, hero_h + 20, record, highlight={h.pid: palette for h in heroes}, tracked=tracked)

    draw = ImageDraw.Draw(img, "RGBA")
    if footer:
        P.draw_text(draw, (W - MARGIN, total_h - 14), footer, T.font("semibold", 12), T.DIM, anchor="rm")
    url = (record.faceit_url or "").replace("https://", "")
    if url:
        P.draw_text(draw, (MARGIN, total_h - 14), P.fit_text(draw, url, T.font("regular", 12), 700), T.font("regular", 12), T.DIM, anchor="lm")
    return P.to_png(img)
