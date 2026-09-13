"""Post-mortem card: match header, headline banner, the five teammates ranked, kill-feed callouts."""
from __future__ import annotations

from datetime import datetime, timezone

from PIL import Image, ImageDraw

from ..leetify import ATTRIBUTION, fmt_rating
from ..postmortem import PostmortemResult, RankedRow
from . import primitives as P
from . import theme as T

W = 1200
MARGIN = 40
HEADER_H = 150
BANNER_H = 76
CAPTION_H = 22
ROW_H = 96
ROW_GAP = 8
FOOTER_H = 40

BAR_X, BAR_W = 700, 280
PIP_X, PIP_W, PIP_STEP = 700, 40, 48
CAT_SHORT = {"Impact": "IMP", "Fragging": "FRAG", "Opening": "OPEN", "Clutch": "CLU", "Utility": "UTIL", "Discipline": "DISC"}


def _rank_color(row: RankedRow, n: int) -> tuple:
    if row.rank == 1:
        return T.GLORY.accent
    if row.rank == n and n > 1:
        return T.BAD
    return T.MUTED


def _strip_md(text: str) -> str:
    return text.replace("**", "").replace("_", "")


def _row(img: Image.Image, draw: ImageDraw.ImageDraw, y: int, row: RankedRow, res: PostmortemResult, pal: T.Palette, avatar: bytes | None, alt: bool) -> None:
    ln = row.line
    n = len(res.rows)
    box = (MARGIN, y, W - MARGIN, y + ROW_H)
    if ln.tracked:
        P.rounded(draw, box, 12, pal.accent_soft + (255,))
        draw.rounded_rectangle((MARGIN, y + 8, MARGIN + 4, y + ROW_H - 8), radius=2, fill=pal.accent)
        name_col = T.TEXT
    else:
        P.rounded(draw, box, 12, (T.PANEL_ALT if alt else T.PANEL) + (255,))
        name_col = (210, 212, 220)

    rc = _rank_color(row, n)
    P.draw_text(draw, (MARGIN + 30, y + ROW_H // 2), str(row.rank), T.font("display", 40), rc, anchor="mm", shadow=True)

    av = P.circle_image(avatar, 56, ring=T.level_color(ln.level) + (255,), ring_w=3)
    P.blit(img, av, (MARGIN + 56, y + 20))
    P.blit(img, P.level_badge(ln.level, 22), (MARGIN + 56 + 56 - 20, y + 20 + 56 - 20))
    draw = ImageDraw.Draw(img, "RGBA")

    tx = MARGIN + 130
    nf = T.font("bold", 22)
    P.draw_text(draw, (tx, y + 14), P.fit_text(draw, ln.nick, nf, 380), nf, name_col)
    sf = T.font("regular", 13)
    line1 = f"{ln.k} K · {ln.d} D · {ln.a} A · {ln.adr:.0f} ADR · {ln.kd:.2f} K/D · {ln.mvp} MVP · {ln.hs}% HS"
    P.draw_text(draw, (tx, y + 46), P.fit_text(draw, line1, sf, 500), sf, T.MUTED)
    if ln.extended:
        line2 = f"Entries {ln.entry_wins or 0}/{ln.entry_count or 0} · Clutches {ln.clutch_wins or 0}/{ln.clutches or 0} · Util {ln.util_dmg or 0} · Flashed {ln.enemies_flashed or 0}"
    else:
        line2 = "Entries — · Clutches — · Util — · Flashed —"
    if ln.leetify is not None:
        line2 += f" · Leetify {fmt_rating(ln.leetify, fraction=True)}"
    P.draw_text(draw, (tx, y + 66), P.fit_text(draw, line2, sf, 500), sf, T.MUTED)

    # score bar + number
    P.rounded(draw, (BAR_X, y + 22, BAR_X + BAR_W, y + 34), 6, (0, 0, 0, 120))
    fill = max(8, int(BAR_W * max(0.0, min(1.0, row.score))))
    P.rounded(draw, (BAR_X, y + 22, BAR_X + fill, y + 34), 6, rc + (255,))
    P.draw_text(draw, (BAR_X + BAR_W + 12, y + 28), f"{round(row.score * 100)}", T.font("display", 28), T.TEXT, anchor="lm")

    # category pips
    for i, c in enumerate(res.categories):
        px = PIP_X + i * PIP_STEP
        s = row.cat_scores.get(c.name) if c.usable else None
        if s is None:
            P.rounded(draw, (px, y + 52, px + PIP_W, y + 60), 4, None, outline=T.DIM + (160,), width=1)
            continue
        col = T.GOOD if s >= 0.66 else (T.BAD if s <= 0.33 else T.DIM)
        P.rounded(draw, (px, y + 52, px + PIP_W, y + 60), 4, col + (255,))

    # best / worst chips
    cf = T.font("semibold", 12)
    if row.best_at:
        txt = f"BEST · {row.best_at.upper()}"
        cw = P.text_w(draw, txt, cf) + 20
        P.pill(draw, W - MARGIN - 18 - cw, y + 20, txt, cf, fill=T.GOOD + (40,), color=T.GOOD, height=24, pad_x=10)
    if row.worst_at:
        txt = f"WORST · {row.worst_at.upper()}"
        cw = P.text_w(draw, txt, cf) + 20
        P.pill(draw, W - MARGIN - 18 - cw, y + 54, txt, cf, fill=T.BAD + (40,), color=T.BAD, height=24, pad_x=10)


def render_postmortem(
    res: PostmortemResult,
    *,
    avatars: dict[str, bytes | None],
    map_bytes: bytes | None,
    footer: str = "",
) -> bytes:
    pal = T.REDEMPTION if res.won else T.SHAME
    n = len(res.rows)
    rows_h = n * ROW_H + max(0, n - 1) * ROW_GAP
    callout_h = (30 + len(res.callouts) * 22 + 14 + ROW_GAP) if res.callouts else 0
    other_h = 22 if res.other_team else 0
    total_h = HEADER_H + BANNER_H + CAPTION_H + rows_h + ROW_GAP + callout_h + other_h + FOOTER_H

    img = Image.new("RGB", (W, total_h), T.BG)
    img.paste(P.background((W, HEADER_H), map_bytes, pal.accent_dark).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle((0, 0, W, 6), fill=pal.accent)

    # header
    P.draw_text(draw, (MARGIN, 26), "POST-MORTEM", T.font("display", 42), pal.accent, shadow=True)
    date = datetime.fromtimestamp(res.finished_at, tz=timezone.utc).strftime("%Y-%m-%d") if res.finished_at else ""
    sub = "  ·  ".join(x for x in (res.map_label, res.team_name, f"{res.rounds} rounds", date) if x)
    P.draw_text(draw, (MARGIN, 76), sub, T.font("semibold", 17), T.MUTED)
    outcome_col = T.WIN if res.won else T.LOSS
    P.draw_text(draw, (W - MARGIN, 22), res.score, T.font("display", 64), outcome_col, anchor="ra", shadow=True)
    chip = "WIN" if res.won else "LOSS"
    cf = T.font("bold", 12)
    cw = P.text_w(draw, chip, cf) + 22
    P.pill(draw, W - MARGIN - cw, 104, chip, cf, fill=outcome_col + (40,), color=outcome_col, height=22, pad_x=11)
    mvp_txt = f"MVP  {res.mvp.nick.upper()}" if res.rows else ""
    if mvp_txt:
        mw = P.text_w(draw, mvp_txt, cf) + 22
        P.pill(draw, W - MARGIN - cw - 10 - mw, 104, mvp_txt, cf, fill=T.GLORY.accent + (40,), color=T.GLORY.accent, height=22, pad_x=11)

    # headline banner
    by = HEADER_H + 8
    P.rounded(draw, (MARGIN, by, W - MARGIN, by + BANNER_H - 16), 14, (0, 0, 0, 120), outline=pal.accent + (160,), width=2)
    hf = T.font("italic", 19)
    lines = P.wrap_text(draw, _strip_md(res.headline), hf, W - MARGIN * 2 - 48, max_lines=2)
    for i, text in enumerate(lines):
        P.draw_text(draw, (MARGIN + 24, by + 12 + i * 24), text, hf, T.TEXT)

    # column captions
    cy = HEADER_H + BANNER_H
    capf = T.font("semibold", 12)
    P.draw_text(draw, (MARGIN + 30, cy + 6), "#", capf, T.DIM, anchor="ma")
    P.draw_text(draw, (MARGIN + 130, cy + 6), "PLAYER", capf, T.DIM)
    P.draw_text(draw, (BAR_X + BAR_W + 12, cy + 6), "SCORE", capf, T.DIM)
    for i, c in enumerate(res.categories):
        P.draw_text(draw, (PIP_X + i * PIP_STEP + PIP_W // 2, cy + 6), CAT_SHORT.get(c.name, c.name[:4].upper()), T.font("semibold", 10), T.DIM if c.usable else (60, 62, 72), anchor="ma")
    P.draw_text(draw, (W - MARGIN - 18, cy + 6), "BEST / WORST", capf, T.DIM, anchor="ra")

    # rows
    y = cy + CAPTION_H
    for i, row in enumerate(res.rows):
        _row(img, draw, y, row, res, pal, avatars.get(row.pid), alt=i % 2 == 1)
        draw = ImageDraw.Draw(img, "RGBA")
        y += ROW_H + ROW_GAP

    # callouts
    if res.callouts:
        h = 30 + len(res.callouts) * 22 + 14
        P.rounded(draw, (MARGIN, y, W - MARGIN, y + h), 12, T.PANEL + (255,))
        P.draw_text(draw, (MARGIN + 18, y + 10), "KILL-FEED ILLUSIONS", T.font("semibold", 12), T.DIM)
        lf = T.font("regular", 15)
        for i, c in enumerate(res.callouts):
            P.draw_text(draw, (MARGIN + 18, y + 32 + i * 22), P.fit_text(draw, "• " + c.text, lf, W - MARGIN * 2 - 36), lf, T.TEXT)
        y += h + ROW_GAP

    if res.other_team:
        names = ", ".join(f"{nick} ({rank}{_suffix(rank)} on their team)" for nick, rank in res.other_team)
        P.draw_text(draw, (MARGIN + 18, y + 2), P.fit_text(draw, "On the other side: " + names, T.font("regular", 13), W - MARGIN * 2 - 36), T.font("regular", 13), T.MUTED)
        y += other_h

    attribution = f"{ATTRIBUTION}  ·  FaceIT Data API" if res.leetify_used else "FaceIT Data API"
    P.draw_text(draw, (MARGIN, total_h - 16), attribution, T.font("semibold", 12), T.DIM, anchor="lm")
    if footer:
        P.draw_text(draw, (W - MARGIN, total_h - 16), footer, T.font("semibold", 12), T.DIM, anchor="rm")
    return P.to_png(img)


def _suffix(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
