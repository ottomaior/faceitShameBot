"""Render smoke tests (real PNG output) and Components V2 view serialisation."""
from __future__ import annotations

import io

import discord
from PIL import Image

from shamebot.app import App, RenderedPost
from shamebot.config import Settings
from shamebot.models import parse_match
from shamebot.render import theme as T
from shamebot.render.compare_card import CompareSide, render_compare
from shamebot.render.leaderboard_card import LeaderRow, render_leaderboard
from shamebot.render.profile_card import ProfileData, render_profile
from shamebot.render.shame_card import HeroPlayer, render_match_card
from shamebot.rules import PostKind, Thresholds, detect
from shamebot.stats import aggregate
from shamebot.views import ExcuseButton, WallButton, image_view, leaderboard_view, match_view, text_view


def _png_size(png: bytes) -> tuple[int, int]:
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    return img.size


def _record(fixtures, tracked, suffix):
    rec = parse_match(fixtures["details" + suffix]["match_id"], fixtures["stats" + suffix], fixtures["details" + suffix], tracked, kill_threshold=10)
    detect(rec, streaks_before={}, thresholds=Thresholds())
    return rec


def test_fonts_bundled():
    assert T.fonts_available(), "assets/fonts must ship with the repo"


def test_render_single_and_double(fixtures, tracked):
    rec = _record(fixtures, tracked, "_shame")
    pids = list(rec.players)
    heroes = [HeroPlayer(pid=p, result=rec.players[p], level=6, elo=1300, elo_delta=-20, line="roast", form=["S", "C"], record_line="1/2 on the wall") for p in pids]
    w, h = _png_size(render_match_card(rec, heroes, T.SHAME, map_bytes=None, tracked=set(tracked), headline="WALL OF SHAME — DOUBLE FEATURE"))
    assert w == 1200 and h > 900
    w, h = _png_size(render_match_card(rec, heroes[:1], T.SHAME, map_bytes=None, tracked=set(tracked)))
    assert w == 1200 and h > 900
    for pal in (T.REDEMPTION, T.GLORY, T.NEUTRAL):
        assert _png_size(render_match_card(rec, heroes[:1], pal, map_bytes=None, tracked=set(tracked)))[0] == 1200


def test_render_with_broken_images(fixtures, tracked):
    rec = _record(fixtures, tracked, "_onekill")
    pid = next(iter(rec.players))
    hero = HeroPlayer(pid=pid, result=rec.players[pid], avatar=b"not-an-image")
    assert _png_size(render_match_card(rec, [hero], T.SHAME, map_bytes=b"garbage", tracked=set()))[0] == 1200


def test_render_leaderboard_profile_compare():
    rows = [LeaderRow(f"P{i}", None, i + 1, f"{i}/10", "on the wall", i / 10, f"{i * 10}%", "Regular", ["worst 3 K"]) for i in range(5)]
    assert _png_size(render_leaderboard(rows, T.SHAME, headline="H", subtitle="s", extra_lines=["x"]))[0] == 1200
    assert _png_size(render_leaderboard(rows[:1], T.GLORY, headline="H", subtitle="s"))[0] == 1200
    assert _png_size(render_leaderboard([], T.SHAME, headline="H", subtitle="s"))[0] == 1200
    a = aggregate("p", [], nickname="P")
    data = ProfileData("P", None, 6, 1300, "hu", -5, 20, [1200, 1250, 1300], a, a, [])
    assert _png_size(render_profile(data))[0] == 1200
    from shamebot.compare import build_categories, score, write_verdict

    res = write_verdict(score("A", "B", build_categories(a, a, elo_a=1300, elo_b=1400, leet_a=None, leet_b=None)), seed="t")
    assert _png_size(render_compare(CompareSide("A", None, 6, 1300, "Saint", False), CompareSide("B", None, 7, 1400, "Slump", False), res, subtitle="s"))[0] == 1200


def _app(tracked) -> App:
    app = App(Settings())
    app.tracked = dict(tracked)
    return app


def test_match_view_serialises(fixtures, tracked):
    app = _app(tracked)
    rec = _record(fixtures, tracked, "_shame")
    pids = list(rec.players)
    heroes = [HeroPlayer(pid=p, result=rec.players[p], elo_delta=-24, record_line="8/30") for p in pids]
    post = RenderedPost(PostKind.SHAME, rec, heroes, b"png", "WALL OF SHAME — DOUBLE FEATURE", {p: "roast" for p in pids})
    view, files = match_view(app, post, mention_ids=[123])
    assert len(files) == 1 and files[0].filename.endswith(".png")
    payload = view.to_components()
    assert payload[0]["type"] == discord.ComponentType.container.value
    children = payload[0]["components"]
    types = [c["type"] for c in children]
    assert discord.ComponentType.media_gallery.value in types
    assert discord.ComponentType.action_row.value in types
    row = next(c for c in children if c["type"] == discord.ComponentType.action_row.value)
    ids = [b.get("custom_id") or b.get("url") for b in row["components"]]
    assert ids[0] == rec.faceit_url
    assert sum(1 for i in ids if i and i.startswith("shame:excuse:")) == 2
    assert "shame:wall:recent" in ids
    text = "\n".join(c.get("content", "") for c in children if c["type"] == discord.ComponentType.text_display.value)
    assert "<@123>" in text and "Double feature" in text
    assert view.is_persistent()


def test_dynamic_item_templates():
    assert ExcuseButton.__discord_ui_compiled_template__.fullmatch("shame:excuse:1-fc7575a5-e458-4a76-98d5-c54489cb6cb5:64499644-1111-2222-3333-444444444444")
    assert WallButton.__discord_ui_compiled_template__.fullmatch("shame:wall:all")


def test_other_views(fixtures, tracked):
    app = _app(tracked)
    view, files = leaderboard_view(app, b"png", scope="recent", glory=False, summary="s")
    assert view.to_components()[0]["type"] == discord.ComponentType.container.value and files
    view, _ = image_view("t", b"png", filename="x.png", thumbnail="https://example.com/a.png", link=("L", "https://x"))
    assert view.to_components()[0]["components"][0]["type"] == discord.ComponentType.section.value
    assert text_view("x" * 5000).to_components()[0]["components"][0]["content"] == "x" * 3900


def test_help_view(tracked):
    from shamebot.views import help_text, help_view, load_example_card

    app = _app(tracked)
    chunks = help_text(app)
    assert sum(len(c) for c in chunks) < 4000
    assert "/wall" in chunks[2] and "BRNWr" in chunks[0]
    png = load_example_card()
    assert png and png[:4] == b"\x89PNG"
    view, files = help_view(app, example=png)
    comps = view.to_components()[0]["components"]
    assert [c["type"] for c in comps].count(discord.ComponentType.text_display.value) == 3
    assert files[0].filename == "example_card.png"
    assert view.is_persistent()
