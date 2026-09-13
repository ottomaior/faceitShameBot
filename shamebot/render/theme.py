"""Colours, fonts and palettes shared by every rendered card."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache

from PIL import ImageFont

log = logging.getLogger(__name__)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "fonts")

FONT_FILES = {
    "regular": "Inter-Regular.ttf",
    "semibold": "Inter-SemiBold.ttf",
    "bold": "Inter-Bold.ttf",
    "italic": "Inter-Italic.ttf",
    "display": "BebasNeue-Regular.ttf",
}

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


@lru_cache(maxsize=64)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = os.path.join(FONT_DIR, FONT_FILES.get(kind, FONT_FILES["regular"]))
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        log.warning("Font %s missing at %s; falling back to Pillow default", kind, path)
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            return ImageFont.load_default()


def fonts_available() -> bool:
    return all(os.path.isfile(os.path.join(FONT_DIR, f)) for f in FONT_FILES.values())


# --- FaceIT level colours -------------------------------------------------------------
def level_color(level: int | None) -> RGB:
    if level is None or level <= 1:
        return (200, 200, 205)
    if level <= 3:
        return (28, 228, 0)
    if level <= 7:
        return (255, 200, 0)
    if level <= 9:
        return (255, 99, 9)
    return (254, 31, 0)


# --- palettes -------------------------------------------------------------------------
@dataclass(frozen=True)
class Palette:
    name: str
    accent: RGB
    accent_dark: RGB
    accent_soft: RGB  # for tinted panels
    stamp: str
    headline: str
    kills_label: str = "KILLS"


SHAME = Palette(
    name="shame",
    accent=(224, 20, 60),
    accent_dark=(120, 8, 30),
    accent_soft=(70, 18, 30),
    stamp="CERTIFIED BOT",
    headline="WALL OF SHAME",
)
REDEMPTION = Palette(
    name="redemption",
    accent=(46, 204, 113),
    accent_dark=(18, 92, 50),
    accent_soft=(18, 60, 38),
    stamp="REDEEMED",
    headline="REDEMPTION ARC",
)
GLORY = Palette(
    name="glory",
    accent=(245, 179, 1),
    accent_dark=(120, 85, 0),
    accent_soft=(70, 52, 10),
    stamp="CERTIFIED PROBLEM",
    headline="WALL OF FAME",
)
LIABILITY = Palette(
    name="liability",
    accent=(168, 176, 196),
    accent_dark=(58, 62, 78),
    accent_soft=(44, 47, 60),
    stamp="LIABILITY",
    headline="THE LIABILITY",
)
NEUTRAL = Palette(
    name="neutral",
    accent=(88, 140, 255),
    accent_dark=(30, 55, 120),
    accent_soft=(22, 34, 64),
    stamp="",
    headline="LAST MATCH",
)

PALETTES = {p.name: p for p in (SHAME, REDEMPTION, GLORY, LIABILITY, NEUTRAL)}

BG: RGB = (13, 14, 18)
PANEL: RGB = (24, 26, 33)
PANEL_ALT: RGB = (29, 31, 40)
LINE: RGB = (48, 50, 62)
TEXT: RGB = (236, 237, 241)
MUTED: RGB = (150, 154, 168)
DIM: RGB = (96, 100, 115)
BAD: RGB = (255, 96, 96)
GOOD: RGB = (86, 226, 140)
WIN: RGB = (46, 204, 113)
LOSS: RGB = (224, 20, 60)
