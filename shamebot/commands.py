"""Slash commands. All replies use Components V2 views."""
from __future__ import annotations

import logging
from typing import Literal

import discord
from discord import app_commands

from .app import App
from .render import theme as T
from .rules import PostKind, compute_awards
from .stats import elo_change
from .leetify import ATTRIBUTION, profile_url as leetify_profile_url
from .views import MENTION_USERS, compare_view, help_view, image_view, leaderboard_view, load_example_card, match_view, postmortem_view, text_view

log = logging.getLogger(__name__)

Scope = Literal["recent", "all"]


def _level_emoji(level: int | None) -> str:
    if level is None:
        return "⬜"
    if level <= 1:
        return "⬜"
    if level <= 3:
        return "🟩"
    if level <= 7:
        return "🟨"
    if level <= 9:
        return "🟧"
    return "🟥"


def register(tree: app_commands.CommandTree, app: App) -> None:
    s = app.settings

    # ------------------------------------------------------------ guards

    async def allowed(interaction: discord.Interaction) -> bool:
        if not s.stats_command_any_channel and s.command_channel_ids and interaction.channel_id not in s.command_channel_ids:
            await interaction.response.send_message("Use this command in the bot's channels.", ephemeral=True)
            return False
        if app.backfill_in_progress:
            await interaction.response.send_message("Stats backfill is still running. Try again in a few minutes.", ephemeral=True)
            return False
        return True

    async def resolve(interaction: discord.Interaction, player: str | None) -> str | None:
        """Nickname (or omitted -> caller's mapped nick) -> player_id, or reply with an error."""
        if not player:
            nick = s.discord_id_to_nick.get(interaction.user.id)
            if not nick:
                await interaction.response.send_message(
                    "Pick a player — your Discord account isn't mapped to a FaceIT nick (FACEIT_DISCORD_MENTIONS).",
                    ephemeral=True,
                )
                return None
            player = nick
        pid = app.pid_for_nick(player)
        if not pid:
            await interaction.response.send_message(
                f"`{player}` is not a tracked player. Tracked: {', '.join(app.tracked.values())}", ephemeral=True
            )
            return None
        return pid

    async def nick_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        cur = current.lower()
        return [
            app_commands.Choice(name=nick, value=nick)
            for nick in app.tracked.values()
            if cur in nick.lower()
        ][:25]

    # ---------------------------------------------------------- commands

    @tree.command(name="wall", description="Wall of Shame leaderboard (image)")
    @app_commands.describe(scope="recent = last N games per player (default), all = every cached match")
    async def wall(interaction: discord.Interaction, scope: Scope = "recent") -> None:
        if not await allowed(interaction):
            return
        await interaction.response.defer()
        png = await app.render_leaderboard(scope=scope)
        aggs = sorted(app.aggregates(scope=scope), key=lambda a: (a.shame_count, a.shame_rate), reverse=True)
        summary = "\n".join(
            f"**{i}.** {a.nickname} — **{a.shame_count}**/{a.games} ({a.shame_rate}%)"
            + (f" · streak **{a.current_shame_streak}**" if a.current_shame_streak else "")
            + f" · *{a.title}*"
            for i, a in enumerate(aggs, 1)
        ) or "_No tracked players._"
        view, files = leaderboard_view(app, png, scope=scope, glory=False, summary=summary)
        await interaction.followup.send(view=view, files=files)

    @tree.command(name="glory", description="Glory board: best games, clean rate, averages (image)")
    async def glory(interaction: discord.Interaction) -> None:
        if not await allowed(interaction):
            return
        await interaction.response.defer()
        png = await app.render_leaderboard(scope="all", glory=True)
        aggs = sorted(app.aggregates(scope="all"), key=lambda a: (a.best_kills or 0, a.clean_rate, a.avg_kills or 0), reverse=True)
        summary = "\n".join(
            f"**{i}.** {a.nickname} — best **{a.best_kills} K** · avg **{a.avg_kills}** · {a.clean_games}/{a.games} clean ({a.clean_rate}%) · *{a.glory_title}*"
            for i, a in enumerate(aggs, 1)
        ) or "_No tracked players._"
        view, files = leaderboard_view(app, png, scope="all", glory=True, summary=summary)
        await interaction.followup.send(view=view, files=files)

    @tree.command(name="profile", description="Player card: level, ELO trend, averages, form, maps")
    @app_commands.describe(player="Tracked FaceIT nickname (defaults to you, if mapped)")
    @app_commands.autocomplete(player=nick_autocomplete)
    async def profile(interaction: discord.Interaction, player: str | None = None) -> None:
        if not await allowed(interaction):
            return
        pid = await resolve(interaction, player)
        if not pid:
            return
        await interaction.response.defer()
        snap = await app.refresh_snapshot(pid)
        png = await app.render_profile(pid)
        recent = app.aggregate(pid, scope="recent")
        d7 = elo_change(snap, days=7)
        text = (
            f"## {_level_emoji(snap.level if snap else None)} {app.nick(pid)}\n"
            f"Level **{snap.level if snap else '—'}** · **{snap.elo if snap else '—'}** ELO"
            + (f" ({d7:+d} / 7d)" if d7 is not None else "")
            + f" · last {s.post_history_limit}: **{recent.shame_count}** shames ({recent.shame_rate}%) · *{recent.title}*"
        )
        leet = await app.leetify_profile(pid)
        links = [("FaceIT profile", snap.faceit_url)] if snap and snap.faceit_url else []
        if leet:
            links.append(("View on Leetify", leet.url))
            text += f"\n-# {ATTRIBUTION}"
        view, files = image_view(
            text,
            png,
            filename=f"profile-{pid[:8]}.png",
            palette=T.SHAME if recent.shame_rate >= 25 else T.NEUTRAL,
            thumbnail=snap.avatar if snap else None,
            links=links,
        )
        await interaction.followup.send(view=view, files=files)

    @tree.command(name="last", description="A player's most recent match as a card")
    @app_commands.describe(player="Tracked FaceIT nickname (defaults to you, if mapped)")
    @app_commands.autocomplete(player=nick_autocomplete)
    async def last(interaction: discord.Interaction, player: str | None = None) -> None:
        if not await allowed(interaction):
            return
        pid = await resolve(interaction, player)
        if not pid:
            return
        await interaction.response.defer()
        records = app.state.matches_for_player(pid)
        if not records:
            await interaction.followup.send("No cached matches for that player yet.")
            return
        record = records[0]
        if not record.enriched or not record.map_image:
            fresh = await app.fetch_record(record.match_id, finished_at=record.finished_at, with_details=True)
            if fresh:
                for p, r in fresh.players.items():
                    if p in record.players:
                        r.elo_after, r.elo_delta, r.awards = record.players[p].elo_after, record.players[p].elo_delta, record.players[p].awards
                app.detect(fresh)
                app.state.put_match(fresh)
                record = fresh
        r = record.players[pid]
        kind = PostKind.SHAME if r.shamed else (PostKind.GLORY if r.fame or r.kills >= s.glory_kills or (r.penta or 0) else (PostKind.LIABILITY if r.liability else None))
        post = await app.render_post(record, [pid], kind)
        post.headline = post.headline if kind else "LAST MATCH"
        view, files = match_view(app, post, mention_ids=None, buttons=True)
        await interaction.followup.send(view=view, files=files, allowed_mentions=MENTION_USERS)

    @tree.command(name="compare", description="Head-to-head verdict: who is the better player (FaceIT + Leetify)")
    @app_commands.describe(a="First player", b="Second player", scope="recent (default) or all")
    @app_commands.autocomplete(a=nick_autocomplete, b=nick_autocomplete)
    async def compare(interaction: discord.Interaction, a: str, b: str, scope: Scope = "recent") -> None:
        if not await allowed(interaction):
            return
        pa, pb = app.pid_for_nick(a), app.pid_for_nick(b)
        if not pa or not pb:
            await interaction.response.send_message(f"Both players must be tracked. Tracked: {', '.join(app.tracked.values())}", ephemeral=True)
            return
        if pa == pb:
            await interaction.response.send_message("Comparing a player with themselves. Bold. Pick two different players.", ephemeral=True)
            return
        await interaction.response.defer()
        await app.refresh_snapshot(pa)
        await app.refresh_snapshot(pb)
        res, png = await app.compare(pa, pb, scope=scope)
        links: list[tuple[str, str]] = []
        for pid in (pa, pb):
            snap = app.state.snapshot(pid)
            if snap and snap.faceit_url:
                links.append((f"{app.nick(pid)} on FaceIT", snap.faceit_url))
        for pid in (pa, pb):
            snap = app.state.snapshot(pid)
            if snap and snap.steam_id and await app.leetify_profile(pid):
                links.append((f"{app.nick(pid)} on Leetify", leetify_profile_url(snap.steam_id)))
        view, files = compare_view(res, png, scope_label=app.scope_label(scope), links=links)
        await interaction.followup.send(view=view, files=files)

    @tree.command(name="postmortem", description="Who actually played best in the last game: all 5 teammates ranked, friends roasted")
    @app_commands.describe(player="Tracked nickname → their last match (default: newest match with 2+ of you)", match_id="Any FaceIT match id (1-…)")
    @app_commands.autocomplete(player=nick_autocomplete)
    async def postmortem(interaction: discord.Interaction, player: str | None = None, match_id: str | None = None) -> None:
        if not await allowed(interaction):
            return
        focus: str | None = None
        record = None
        if match_id:
            mid = match_id.strip()
        else:
            if player:
                focus = app.pid_for_nick(player)
                if not focus:
                    await interaction.response.send_message(f"Unknown player. Tracked: {', '.join(app.tracked.values())}", ephemeral=True)
                    return
            else:
                focus = app.pid_for_nick(s.discord_id_to_nick.get(interaction.user.id, ""))
            record = app.pick_postmortem_match(focus if player else None)
            if record is None:
                await interaction.response.send_message("No cached matches yet.", ephemeral=True)
                return
            mid = record.match_id
        await interaction.response.defer()
        fresh = await app.fetch_record(mid, finished_at=record.finished_at if record else None, with_details=True)
        if fresh is None:
            await interaction.followup.send("Match not found on FaceIT (or it has no stats yet).")
            return
        if not fresh.players and interaction.user.id not in s.admin_discord_ids:
            await interaction.followup.send("No tracked player in that match.")
            return
        if record:
            for p, r in fresh.players.items():
                if p in record.players:
                    r.elo_after, r.elo_delta, r.awards = record.players[p].elo_after, record.players[p].elo_delta, record.players[p].awards
        if fresh.players:
            app.detect(fresh)
            app.state.put_match(fresh)
        res, png = await app.postmortem(fresh, focus_pid=focus)
        links: list[tuple[str, str]] = [("Open on FaceIT", fresh.faceit_url)]
        for row in res.rows:
            if row.line.tracked and row.line.steam_id and len(links) < 5:
                links.append((f"{row.nick} on Leetify", leetify_profile_url(row.line.steam_id)))
        view, files = postmortem_view(res, png, links=links)
        await interaction.followup.send(view=view, files=files)

    @tree.command(name="maps", description="Per-map record for a tracked player")
    @app_commands.describe(player="Tracked FaceIT nickname (defaults to you, if mapped)")
    @app_commands.autocomplete(player=nick_autocomplete)
    async def maps(interaction: discord.Interaction, player: str | None = None) -> None:
        if not await allowed(interaction):
            return
        pid = await resolve(interaction, player)
        if not pid:
            return
        from .stats import per_map

        stats = per_map(pid, app.state.matches_for_player(pid))
        if not stats:
            await interaction.response.send_message("No map data cached yet.", ephemeral=True)
            return
        lines = [f"{'Map':<10}{'Games':>6}{'Win%':>7}{'Avg K':>7}{'Wall%':>7}"]
        for m in stats[:12]:
            wr = f"{m.win_rate}%" if m.win_rate is not None else "—"
            ak = f"{m.avg_kills:.1f}" if m.avg_kills is not None else "—"
            lines.append(f"{m.map.replace('de_', '')[:10]:<10}{m.games:>6}{wr:>7}{ak:>7}{m.shame_rate:>6}%")
        text = f"## 🗺️ {app.nick(pid)} — maps (all cached matches)\n```\n" + "\n".join(lines) + "\n```"
        await interaction.response.send_message(view=text_view(text))

    @tree.command(name="elo", description="Tracked players ranked by FaceIT ELO")
    async def elo(interaction: discord.Interaction) -> None:
        if not await allowed(interaction):
            return
        await interaction.response.defer()
        await app.refresh_all_snapshots()
        snaps = [app.state.snapshot(pid) for pid in app.tracked_ids]
        snaps = [sn for sn in snaps if sn]
        snaps.sort(key=lambda sn: sn.elo or 0, reverse=True)
        lines = ["## 📈 ELO board"]
        for i, sn in enumerate(snaps, 1):
            d7, d30 = elo_change(sn, days=7), elo_change(sn, days=30)
            deltas = " · ".join(x for x in (f"7d **{d7:+d}**" if d7 is not None else "", f"30d **{d30:+d}**" if d30 is not None else "") if x)
            lines.append(f"**{i}.** {_level_emoji(sn.level)} **{sn.nickname}** — **{sn.elo if sn.elo is not None else '—'}** (lvl {sn.level if sn.level is not None else '—'})" + (f" · {deltas}" if deltas else ""))
        await interaction.followup.send(view=text_view("\n".join(lines), palette=T.NEUTRAL))

    @tree.command(name="awards", description="Hall of shame: records nobody wanted")
    async def awards(interaction: discord.Interaction) -> None:
        if not await allowed(interaction):
            return
        entries = app.hall()
        if not entries:
            await interaction.response.send_message("Nothing on record yet.", ephemeral=True)
            return
        lines = ["## 🏛️ Hall of Shame"]
        for e in entries:
            link = ""
            rec = app.state.match_outcomes.get(e.match_id or "")
            if rec and rec.faceit_url:
                link = f" · [match]({rec.faceit_url})"
            lines.append(f"**{e.label}:** {e.nickname} — {e.value}{link}")
        await interaction.response.send_message(view=text_view("\n".join(lines), palette=T.SHAME))

    @tree.command(name="help", description="How the Wall of Shame bot works: posts, buttons, awards, commands")
    @app_commands.describe(public="Post it for everyone (default: only you see it)")
    async def help_command(interaction: discord.Interaction, public: bool = False) -> None:
        view, files = help_view(app, example=load_example_card())
        await interaction.response.send_message(view=view, files=files, ephemeral=not public)

    @tree.command(name="shametest", description="(admin) Render the full post for any match id, ignoring the threshold")
    @app_commands.describe(match_id="FaceIT match id (1-....)", kind="Which post style to render")
    async def shametest(interaction: discord.Interaction, match_id: str, kind: Literal["shame", "redemption", "glory", "liability", "last"] = "shame") -> None:
        if interaction.user.id not in s.admin_discord_ids:
            await interaction.response.send_message("Admins only (ADMIN_DISCORD_IDS).", ephemeral=True)
            return
        await interaction.response.defer()
        record = await app.fetch_record(match_id.strip(), finished_at=None, with_details=True)
        if not record or not record.players:
            await interaction.followup.send("Match not found, or no tracked player in it.")
            return
        post_kind = {"shame": PostKind.SHAME, "redemption": PostKind.REDEMPTION, "glory": PostKind.GLORY, "liability": PostKind.LIABILITY}.get(kind)
        # Prefer the real detection for that kind (exact heroes + awards); fall back to forcing it on everyone.
        detected = next((d for d in app.detect(record) if d.kind is post_kind), None)
        pids = list(detected.player_ids) if detected else list(record.players)
        if post_kind is PostKind.SHAME and not detected:
            for pid in pids:
                record.players[pid].shamed = True
                record.players[pid].awards = compute_awards(record, pid, streak_before=app.streak_before(pid, record.finished_at), shamed_count=len(pids), kill_threshold=app.thresholds.kill_threshold)
        post = await app.render_post(record, pids, post_kind)
        view, files = match_view(app, post, mention_ids=None, buttons=True)
        await interaction.followup.send(view=view, files=files, allowed_mentions=MENTION_USERS)
