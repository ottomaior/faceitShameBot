"""Discord Components V2 message builders and persistent buttons."""
from __future__ import annotations

import io
import logging
import os
import re
from typing import TYPE_CHECKING

import discord
from discord import ui

from .render import theme as T
from .rules import PostKind, award_emoji, award_label

if TYPE_CHECKING:
    from .app import App, RenderedPost

log = logging.getLogger(__name__)

KIND_EMOJI = {PostKind.SHAME: "🧱", PostKind.REDEMPTION: "🌅", PostKind.GLORY: "🏆"}
MENTION_USERS = discord.AllowedMentions(users=True, roles=False, everyone=False)
NO_MENTIONS = discord.AllowedMentions.none()


def _colour(pal: T.Palette) -> discord.Colour:
    return discord.Colour.from_rgb(*pal.accent)


def _app(interaction: discord.Interaction) -> "App":
    return interaction.client.app  # type: ignore[attr-defined]


# ------------------------------------------------------------- persistent buttons


class ExcuseButton(ui.DynamicItem[ui.Button], template=r"shame:excuse:(?P<match>[0-9A-Za-z-]+):(?P<pid>[0-9a-f-]+)"):
    def __init__(self, match_id: str, pid: str, *, label: str = "Excuse me") -> None:
        super().__init__(
            ui.Button(label=label, emoji="🎤", style=discord.ButtonStyle.secondary, custom_id=f"shame:excuse:{match_id}:{pid}")
        )
        self.match_id = match_id
        self.pid = pid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["match"], match["pid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        app = _app(interaction)
        nick = app.nick(self.pid)
        excuse = app.roasts.excuse()
        await interaction.response.send_message(
            f"🎤 {interaction.user.mention} on behalf of **{nick}**: “{excuse}”",
            allowed_mentions=NO_MENTIONS,
        )


class WallButton(ui.DynamicItem[ui.Button], template=r"shame:wall:(?P<scope>recent|all)"):
    def __init__(self, scope: str = "recent", *, label: str | None = None) -> None:
        super().__init__(
            ui.Button(
                label=label or ("Wall (recent)" if scope == "recent" else "Wall (all time)"),
                emoji="📊",
                style=discord.ButtonStyle.secondary,
                custom_id=f"shame:wall:{scope}",
            )
        )
        self.scope = scope

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["scope"])

    async def callback(self, interaction: discord.Interaction) -> None:
        app = _app(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        png = await app.render_leaderboard(scope=self.scope)
        view, files = leaderboard_view(app, png, scope=self.scope, glory=False, buttons=False)
        await interaction.followup.send(view=view, files=files, ephemeral=True)


DYNAMIC_ITEMS = (ExcuseButton, WallButton)


# ---------------------------------------------------------------------- views


def _file(png: bytes, name: str) -> discord.File:
    return discord.File(io.BytesIO(png), filename=name)


def _hero_line(app: "App", post: "RenderedPost", hero) -> str:
    r = hero.result
    rec = post.record
    bits = [f"**{r.nickname}** — **{r.kills} kill{'s' if r.kills != 1 else ''}** on {rec.map_label}"]
    if r.team_score is not None and r.enemy_score is not None:
        outcome = "W" if r.result == 1 else "L"
        bits.append(f"{r.team_score}–{r.enemy_score} **{outcome}**")
    if hero.elo_delta is not None:
        bits.append(f"**{hero.elo_delta:+d} ELO**")
    elif hero.elo is not None:
        bits.append(f"{hero.elo} ELO")
    return " · ".join(bits)


def match_view(
    app: "App",
    post: "RenderedPost",
    *,
    mention_ids: list[int] | None = None,
    buttons: bool = True,
) -> tuple[ui.LayoutView, list[discord.File]]:
    pal = app.palette_for(post.kind)
    rec = post.record
    filename = f"{post.kind.value}-{rec.match_id[-8:]}.png"
    emoji = KIND_EMOJI.get(post.kind, "🎮")

    head = [f"# {emoji} {post.headline}"]
    for hero in post.heroes:
        head.append(_hero_line(app, post, hero))
        line = post.lines.get(hero.pid)
        if line:
            head.append(f"_“{line}”_")
    head_text = "\n".join(head)

    avatar_url = None
    snap = app.state.snapshot(post.heroes[0].pid) if post.heroes else None
    if snap and snap.avatar:
        avatar_url = snap.avatar

    items: list[ui.Item] = []
    if avatar_url:
        items.append(ui.Section(ui.TextDisplay(head_text), accessory=ui.Thumbnail(avatar_url)))
    else:
        items.append(ui.TextDisplay(head_text))
    items.append(ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}")))
    items.append(ui.Separator(spacing=discord.SeparatorSpacing.small))

    footer_lines: list[str] = []
    for hero in post.heroes:
        r = hero.result
        if r.awards:
            badges = " · ".join(f"{award_emoji(a)} {award_label(a)}" for a in r.awards)
            footer_lines.append(f"{r.nickname}: {badges}" if len(post.heroes) > 1 else badges)
    for hero in post.heroes:
        if hero.record_line:
            prefix = f"{hero.result.nickname}: " if len(post.heroes) > 1 else ""
            footer_lines.append(f"📉 {prefix}Wall record (last {app.settings.post_history_limit}): {hero.record_line}")
    if mention_ids:
        footer_lines.append(" ".join(f"<@{uid}>" for uid in mention_ids))
    if footer_lines:
        items.append(ui.TextDisplay("\n".join(footer_lines)))

    if buttons:
        row = ui.ActionRow()
        if rec.faceit_url:
            row.add_item(ui.Button(label="Open on FaceIT", url=rec.faceit_url, emoji="🔗"))
        if post.kind is PostKind.SHAME:
            for hero in post.heroes[:3]:
                label = "Excuse me" if len(post.heroes) == 1 else f"Excuse {hero.result.nickname}"[:80]
                row.add_item(ExcuseButton(rec.match_id, hero.pid, label=label))
        row.add_item(WallButton("recent", label="Wall"))
        items.append(row)

    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=_colour(pal)))
    return view, [_file(post.png, filename)]


def leaderboard_view(
    app: "App",
    png: bytes,
    *,
    scope: str,
    glory: bool,
    summary: str | None = None,
    buttons: bool = True,
) -> tuple[ui.LayoutView, list[discord.File]]:
    pal = T.GLORY if glory else T.SHAME
    filename = f"{'glory' if glory else 'wall'}-{scope}.png"
    title = "🏆 Glory board" if glory else "🧱 Wall of Shame — leaderboard"
    items: list[ui.Item] = [
        ui.TextDisplay(f"## {title}\n{app.scope_label(scope)}"),
        ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}")),
    ]
    if summary:
        items.append(ui.Separator())
        items.append(ui.TextDisplay(summary))
    if buttons and not glory:
        other = "all" if scope == "recent" else "recent"
        items.append(ui.ActionRow(WallButton(other)))
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=_colour(pal)))
    return view, [_file(png, filename)]


def image_view(
    title: str,
    png: bytes,
    *,
    filename: str,
    palette: T.Palette = T.NEUTRAL,
    text: str | None = None,
    thumbnail: str | None = None,
    link: tuple[str, str] | None = None,
) -> tuple[ui.LayoutView, list[discord.File]]:
    head = ui.TextDisplay(title)
    items: list[ui.Item] = [ui.Section(head, accessory=ui.Thumbnail(thumbnail)) if thumbnail else head]
    items.append(ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}")))
    if text:
        items.append(ui.Separator())
        items.append(ui.TextDisplay(text))
    if link:
        items.append(ui.ActionRow(ui.Button(label=link[0], url=link[1], emoji="🔗")))
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=_colour(palette)))
    return view, [_file(png, filename)]


def text_view(text: str, *, palette: T.Palette = T.NEUTRAL) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(ui.TextDisplay(text[:3900]), accent_colour=_colour(palette)))
    return view


# ----------------------------------------------------------------------- help

EXAMPLE_CARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "example_card.png")


def help_text(app: "App") -> list[str]:
    """Guide text as a few TextDisplay chunks (pinnable; also served by /help)."""
    s = app.settings
    nicks = ", ".join(f"**{n}**" for n in app.tracked.values()) or "the configured players"
    minutes = max(1, round(s.poll_interval_seconds / 60))
    ace = " or an ace"
    intro = (
        "# 🧱 Wall of Shame Bot — how it works\n"
        f"Watches {nicks} on FaceIT CS2 and checks every finished match within ~{minutes} min.\n\n"
        "## What gets posted\n"
        f"🧱 **Wall of Shame** — fewer than **{s.kill_threshold} kills** → a card with the full scoreboard, "
        "your stats, a roast, award badges and your ELO change. Two of you in the same game = **Double Feature**.\n"
        f"🌅 **Redemption Arc** — 2+ shames in a row, then **{s.redemption_kills}+ kills**.\n"
        + (f"🏆 **Highlight** — **{s.glory_kills}+ kills**{ace} (15+ kills).\n" if s.glory_posts_enabled else "")
        + ("📅 **Weekly digest** — leaderboard, bot of the week, biggest ELO loss.\n" if s.weekly_digest_enabled else "")
    )
    buttons = (
        "## Buttons on every post\n"
        "🔗 **Open on FaceIT** — the match room\n"
        "🎤 **Excuse me** — posts a random excuse on the shamed player's behalf. Anyone can press it. Repeatedly.\n"
        "📊 **Wall** — the leaderboard, visible only to you\n\n"
        "## Awards\n"
        "🪦 Bottom of the lobby · 🎬 Double feature · 🎩 Hat-trick (3 in a row, then 🔥 Streak N) · "
        "🎒 Carried (shamed but won) · ⚓ Anchor (lost by 8+) · 👀 Spectator (≤ 3 kills) · "
        "🍽️ Fed (20+ deaths) · 🫥 Zero MVP club · 🎯 Headless (< 20% HS)\n\n"
        "## Titles (last {n} games)\n"
        "**Saint** 0 shames · **Slump** 1–2 · **Regular** 3–4 · **Permanent resident** 5+ · **Heater (reverse)** 3 in a row"
    ).replace("{n}", str(s.post_history_limit))
    commands = (
        "## Commands (use them in this channel)\n"
        f"`/wall` — shame leaderboard, last {s.post_history_limit} games · `scope: all` for all-time\n"
        "`/glory` — best games, clean rate, averages\n"
        "`/profile [player]` — level, ELO trend, averages, last-10 form, maps\n"
        "`/last [player]` — most recent match as a card\n"
        "`/compare a b` — head to head\n"
        "`/maps [player]` — per-map record\n"
        "`/elo` — ELO ranking with 7-day / 30-day change\n"
        "`/awards` — hall of shame records\n"
        "`/help` — this guide\n"
        "Player names autocomplete. Leave `player` empty for your own stats if your Discord is linked to your FaceIT nick."
    )
    return [intro.rstrip(), buttons, commands]


def help_view(app: "App", *, example: bytes | None = None) -> tuple[ui.LayoutView, list[discord.File]]:
    chunks = help_text(app)
    items: list[ui.Item] = [ui.TextDisplay(chunks[0])]
    files: list[discord.File] = []
    if example:
        items.append(ui.MediaGallery(discord.MediaGalleryItem("attachment://example_card.png", description="Example shame card")))
        files.append(_file(example, "example_card.png"))
    items.append(ui.Separator())
    items.append(ui.TextDisplay(chunks[1]))
    items.append(ui.Separator())
    items.append(ui.TextDisplay(chunks[2]))
    items.append(ui.ActionRow(WallButton("recent", label="Wall (last games)"), WallButton("all", label="Wall (all time)")))
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=_colour(T.SHAME)))
    return view, files


def load_example_card() -> bytes | None:
    try:
        with open(EXAMPLE_CARD, "rb") as f:
            return f.read()
    except OSError:
        return None
