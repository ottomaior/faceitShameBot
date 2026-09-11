"""Low-level drawing helpers used by all cards (Pillow only)."""
from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import theme as T

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


# ------------------------------------------------------------------ text helpers


def text_w(draw: ImageDraw.ImageDraw, text: str, font: Font) -> float:
    return draw.textlength(text, font=font)


def fit_text(draw: ImageDraw.ImageDraw, text: str, font: Font, max_w: float) -> str:
    if text_w(draw, text, font) <= max_w:
        return text
    ell = "…"
    while text and text_w(draw, text + ell, font) > max_w:
        text = text[:-1]
    return text + ell


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: Font, max_w: float, max_lines: int = 2) -> list[str]:
    """Greedy word wrap; the last line gets an ellipsis when text is cut."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    i = 0
    while i < len(words):
        trial = f"{cur} {words[i]}".strip()
        if not cur or text_w(draw, trial, font) <= max_w:
            cur = trial
            i += 1
        else:
            lines.append(cur)
            cur = ""
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines and cur:
        lines.append(cur)
        cur = ""
    if lines and (i < len(words) or cur):
        lines[-1] = fit_text(draw, lines[-1] + " …", font, max_w)
    return lines


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: Font,
    fill: tuple,
    *,
    anchor: str = "la",
    shadow: bool = False,
) -> None:
    if shadow:
        draw.text((xy[0] + 2, xy[1] + 2), text, font=font, fill=(0, 0, 0, 170), anchor=anchor)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


# ------------------------------------------------------------------ shapes


def rounded(draw: ImageDraw.ImageDraw, box: tuple, radius: int, fill: tuple | None, outline: tuple | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def pill(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font: Font,
    *,
    fill: tuple,
    color: tuple,
    pad_x: int = 12,
    height: int = 28,
    outline: tuple | None = None,
) -> float:
    """Draw a pill and return its width."""
    w = text_w(draw, text, font) + pad_x * 2
    rounded(draw, (x, y, x + w, y + height), height // 2, fill, outline)
    draw.text((x + pad_x, y + height / 2), text, font=font, fill=color, anchor="lm")
    return w


def gradient(size: tuple[int, int], start: tuple, end: tuple, *, horizontal: bool = True) -> Image.Image:
    """RGBA gradient image from ``start`` to ``end`` (both RGBA). Built as a 1-px strip then scaled."""
    w, h = size
    steps = max(2, min(w if horizontal else h, 512))
    strip = Image.new("RGBA", (steps, 1) if horizontal else (1, steps))
    px = strip.load()
    for i in range(steps):
        t = i / (steps - 1)
        c = tuple(int(start[k] + (end[k] - start[k]) * t) for k in range(4))
        if horizontal:
            px[i, 0] = c
        else:
            px[0, i] = c
    return strip.resize(size, Image.BILINEAR)


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale + centre-crop ``img`` to fill ``size``."""
    w, h = size
    scale = max(w / img.width, h / img.height)
    resized = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left = (resized.width - w) // 2
    top = (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def background(size: tuple[int, int], map_bytes: bytes | None, tint: tuple[int, int, int]) -> Image.Image:
    """Blurred, darkened map image (or a plain gradient) with an accent tint on the left."""
    w, h = size
    if map_bytes:
        try:
            img = Image.open(io.BytesIO(map_bytes)).convert("RGB")
            img = cover(img, size).filter(ImageFilter.GaussianBlur(7))
            base = img.convert("RGBA")
        except OSError:
            base = Image.new("RGBA", size, T.PANEL + (255,))
    else:
        base = gradient(size, T.PANEL_ALT + (255,), T.BG + (255,), horizontal=False)
    base.alpha_composite(Image.new("RGBA", size, (0, 0, 0, 165)))
    base.alpha_composite(gradient(size, tint + (150,), tint + (0,)))
    base.alpha_composite(gradient(size, (0, 0, 0, 0), (0, 0, 0, 120), horizontal=False))
    return base


def circle_image(img_bytes: bytes | None, size: int, *, ring: tuple | None = None, ring_w: int = 5) -> Image.Image:
    """Circular avatar (fallback silhouette) with optional ring. Returns RGBA ``size``×``size``."""
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner = size - ring_w * 2 if ring else size
    avatar: Image.Image | None = None
    if img_bytes:
        try:
            avatar = cover(Image.open(io.BytesIO(img_bytes)).convert("RGBA"), (inner, inner))
        except OSError:
            avatar = None
    if avatar is None:
        avatar = Image.new("RGBA", (inner, inner), (52, 55, 68, 255))
        d = ImageDraw.Draw(avatar)
        # silhouette: head + shoulders
        d.ellipse((inner * 0.32, inner * 0.18, inner * 0.68, inner * 0.54), fill=(120, 124, 140, 255))
        d.ellipse((inner * 0.12, inner * 0.58, inner * 0.88, inner * 1.25), fill=(120, 124, 140, 255))
    mask = Image.new("L", (inner * 4, inner * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, inner * 4 - 1, inner * 4 - 1), fill=255)
    mask = mask.resize((inner, inner), Image.LANCZOS)
    if ring:
        d = ImageDraw.Draw(out)
        d.ellipse((0, 0, size - 1, size - 1), fill=ring)
        out.paste(avatar, (ring_w, ring_w), mask)
    else:
        out.paste(avatar, (0, 0), mask)
    return out


def level_badge(level: int | None, size: int = 44) -> Image.Image:
    """FaceIT-style level badge: dark disc, coloured ring, level number."""
    scale = 3
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = T.level_color(level)
    d.ellipse((0, 0, big - 1, big - 1), fill=(20, 21, 26, 255))
    ring = max(2, size // 9) * scale
    d.ellipse((0, 0, big - 1, big - 1), outline=color, width=ring)
    img = img.resize((size, size), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    f = T.font("bold", int(size * 0.46))
    d.text((size / 2, size / 2 + 1), str(level if level is not None else "?"), font=f, fill=color, anchor="mm")
    return img


def stamp(text: str, *, color: tuple, angle: float = -12, size: int = 38, alpha: int = 200) -> Image.Image:
    """Rubber-stamp: double border rounded rectangle with bold uppercase text, rotated."""
    f = T.font("display", size)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = tmp.textlength(text, font=f)
    pad_x, pad_y = int(size * 0.5), int(size * 0.28)
    w, h = int(tw + pad_x * 2), int(size + pad_y * 2)
    img = Image.new("RGBA", (w + 12, h + 12), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = color + (alpha,)
    d.rounded_rectangle((6, 6, w + 6, h + 6), radius=8, outline=c, width=4)
    d.rounded_rectangle((12, 12, w, h), radius=5, outline=c, width=2)
    d.text(((w + 12) / 2, (h + 12) / 2 + 1), text, font=f, fill=c, anchor="mm")
    return img.rotate(angle, resample=Image.BICUBIC, expand=True)


def stat_tile(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    value: str,
    label: str,
    *,
    value_color: tuple = T.TEXT,
    fill: tuple = (0, 0, 0, 110),
    value_size: int = 30,
    label_size: int = 13,
) -> None:
    x0, y0, x1, y1 = box
    rounded(draw, box, 10, fill, outline=(255, 255, 255, 22))
    cx = (x0 + x1) / 2
    draw.text((cx, y0 + (y1 - y0) * 0.42), value, font=T.font("display", value_size), fill=value_color, anchor="mm")
    draw.text((cx, y1 - 13), label.upper(), font=T.font("semibold", label_size), fill=T.MUTED, anchor="mm")


def form_strip(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    form: list[str],
    *,
    cell: int = 16,
    gap: int = 5,
    oldest_first: bool = True,
) -> float:
    """Squares for the last N games: S=shamed (red), G=glory (gold), C=clean (grey). Returns width."""
    seq = list(reversed(form)) if oldest_first else list(form)
    colors = {"S": T.LOSS, "G": T.GLORY.accent, "C": (110, 114, 130), "W": T.WIN, "L": T.LOSS, "?": (70, 72, 84)}
    for i, code in enumerate(seq):
        cx = x + i * (cell + gap)
        rounded(draw, (cx, y, cx + cell, y + cell), 4, colors.get(code, T.DIM))
    return len(seq) * (cell + gap) - gap


def sparkline(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    values: list[int],
    *,
    color: tuple,
    width: int = 3,
) -> None:
    if len(values) < 2:
        return
    x0, y0, x1, y1 = box
    lo, hi = min(values), max(values)
    span = max(hi - lo, 1)
    pts = []
    for i, v in enumerate(values):
        px = x0 + (x1 - x0) * i / (len(values) - 1)
        py = y1 - (y1 - y0) * (v - lo) / span
        pts.append((px, py))
    draw.line(pts, fill=color, width=width, joint="curve")
    draw.ellipse((pts[-1][0] - 4, pts[-1][1] - 4, pts[-1][0] + 4, pts[-1][1] + 4), fill=color)


def blit(img: Image.Image, overlay: Image.Image, pos: tuple[int, int]) -> None:
    """Paste an RGBA overlay onto an RGB canvas using its own alpha as mask."""
    img.paste(overlay, pos, overlay)


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
