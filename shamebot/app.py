"""Application core shared by the poller and the slash commands (no Discord message building here)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Settings
from .compare import CompareResult, build_categories, score, write_verdict
from .faceit import FaceitClient
from .leetify import LeetifyClient, LeetifyProfile
from .blame import compute_blame, compute_carry
from .models import MatchRecord, PlayerSnapshot, parse_match
from .postmortem import PostmortemResult, analyze, choose_team, write_prose
from .render import theme as T
from .render.compare_card import CompareSide, render_compare
from .render.leaderboard_card import LeaderRow, render_leaderboard
from .render.postmortem_card import render_postmortem
from .render.profile_card import ProfileData, render_profile
from .render.shame_card import HeroPlayer, render_match_card
from .roasts import RoastContext, RoastEngine
from .rules import Detection, PostKind, Thresholds, detect, grey_zone_reasons
from .state import State
from .stats import PlayerAggregate, aggregate, elo_change, group_by_player, hall_of_shame, per_map

log = logging.getLogger(__name__)


@dataclass
class RenderedPost:
    kind: PostKind
    record: MatchRecord
    heroes: list[HeroPlayer]
    png: bytes
    headline: str
    lines: dict[str, str] = field(default_factory=dict)  # pid -> roast / praise line


class App:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = State(settings.state_file)
        self.faceit = FaceitClient(settings.faceit_api_key)
        self.roasts = RoastEngine(level=settings.roast_level, custom_file=settings.custom_roasts_file, recent=self.state.recent_roasts)
        self.leetify = LeetifyClient(settings.leetify_api_key or None, enabled=settings.leetify_enabled)
        self.tracked: dict[str, str] = {}  # player_id -> nickname
        self.backfill_in_progress = False
        self.thresholds = Thresholds(
            kill_threshold=settings.kill_threshold,
            redemption_kills=settings.redemption_kills,
            glory_kills=settings.glory_kills,
            glory_enabled=settings.glory_posts_enabled,
            fame_carry_kills=settings.fame_carry_kills,
            fame_carry_kd=settings.fame_carry_kd,
            fame_carry_adr=settings.fame_carry_adr,
            fame_carry_share=settings.fame_carry_share,
            liability_enabled=settings.liability_posts_enabled,
            liability_blame_share=settings.liability_blame_share,
            liability_close_win_margin=settings.liability_close_win_margin,
            liability_max_kd=settings.liability_max_kd,
            grey_zone=settings.shame_grey_zone,
            grey_kd=settings.shame_grey_kd,
            grey_adr=settings.shame_grey_adr,
            grey_blame_share=settings.shame_grey_blame_share,
            grey_min_signals=settings.shame_grey_min_signals,
            grey_kd_hard=settings.shame_grey_kd_hard,
            grey_adr_hard=settings.shame_grey_adr_hard,
        )

    def arm_grey_zone(self) -> None:
        """Grey-zone shames apply from the first run with the feature, never to older cached games."""
        if self.settings.shame_grey_retroactive:
            self.thresholds.grey_since = None
            return
        if self.state.grey_zone_since is None:
            self.state.grey_zone_since = int(time.time())
            self.state.mark_dirty()
            log.info("Grey-zone shame rule enabled from now on (grey_zone_since=%d).", self.state.grey_zone_since)
        self.thresholds.grey_since = self.state.grey_zone_since

    # ------------------------------------------------------------ players

    @property
    def tracked_ids(self) -> list[str]:
        return list(self.tracked)

    def nick(self, pid: str) -> str:
        snap = self.state.snapshot(pid)
        return snap.nickname if snap else self.tracked.get(pid, pid)

    def pid_for_nick(self, nickname: str) -> str | None:
        wanted = nickname.strip().lower()
        for pid, nick in self.tracked.items():
            if nick.lower() == wanted:
                return pid
        return None

    async def resolve_players(self) -> None:
        self.tracked = {}
        for entry in self.settings.player_entries:
            profile = await self.faceit.resolve_entry(entry)
            if not profile or not profile.get("player_id"):
                log.error("Could not resolve FaceIT player for entry=%r", entry)
                continue
            pid = profile["player_id"]
            self.tracked[pid] = profile.get("nickname") or pid
            self._store_profile(profile)
            log.info("Tracking %s (%s)", self.tracked[pid], pid)
        if not self.tracked:
            log.error(
                "No FaceIT players resolved. FACEIT_PLAYER_IDS accepts FaceIT UUIDs, Steam IDs (17 digits), "
                "or exact FaceIT nicknames — not Discord IDs."
            )

    def _store_profile(self, profile: dict) -> PlayerSnapshot:
        pid = profile["player_id"]
        snap = PlayerSnapshot.from_profile(profile, previous=self.state.snapshot(pid))
        now = int(time.time())
        if snap.elo is not None:
            snap.elo_ts = now
            snap.elo_updated_at = datetime.now(timezone.utc).isoformat()
            if not snap.elo_history or snap.elo_history[-1][1] != snap.elo:
                snap.push_elo(now, snap.elo)
        self.state.put_snapshot(snap)
        return snap

    async def refresh_snapshot(self, pid: str) -> PlayerSnapshot | None:
        profile = await self.faceit.get_player(pid)
        if not profile:
            return self.state.snapshot(pid)
        return self._store_profile(profile)

    async def refresh_all_snapshots(self) -> None:
        if not self.settings.elo_tracking_enabled:
            return
        for pid in self.tracked_ids:
            await self.refresh_snapshot(pid)
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------- matches

    async def fetch_record(self, match_id: str, *, finished_at: int | None, with_details: bool = True) -> MatchRecord | None:
        stats = await self.faceit.get_match_stats(match_id)
        if not stats:
            return None
        details = await self.faceit.get_match(match_id) if with_details else None
        return parse_match(
            match_id,
            stats,
            details,
            self.tracked,
            kill_threshold=self.settings.kill_threshold,
            finished_at=finished_at,
        )

    def streak_before(self, pid: str, finished_at: int) -> int:
        """Shame streak going *into* a match that finished at ``finished_at``."""
        streak = 0
        for m in self.state.matches_for_player(pid):
            if m.finished_at >= finished_at:
                continue
            if m.players[pid].shamed:
                streak += 1
            else:
                break
        return streak

    def detect(self, record: MatchRecord) -> list[Detection]:
        streaks = {pid: self.streak_before(pid, record.finished_at) for pid in record.players}
        return detect(record, streaks_before=streaks, thresholds=self.thresholds)

    # ---------------------------------------------------------- aggregates

    def recent_records(self, pid: str) -> list[MatchRecord]:
        return self.state.matches_for_player(pid)[: self.settings.post_history_limit]

    def aggregate(self, pid: str, *, scope: str) -> PlayerAggregate:
        records = self.recent_records(pid) if scope == "recent" else self.state.matches_for_player(pid)
        return aggregate(pid, records, nickname=self.nick(pid), glory_kills=self.settings.glory_kills)

    def aggregates(self, *, scope: str) -> list[PlayerAggregate]:
        return [self.aggregate(pid, scope=scope) for pid in self.tracked_ids]

    def record_line(self, agg: PlayerAggregate) -> str:
        parts = [f"{agg.shame_count}/{agg.games} on the wall ({agg.shame_rate}%)"]
        if agg.current_shame_streak:
            parts.append(f"streak {agg.current_shame_streak}")
        if agg.liability_count:
            parts.append(f"{agg.liability_count} liabilit{'y' if agg.liability_count == 1 else 'ies'}")
        if agg.worst_kills is not None:
            parts.append(f"worst {agg.worst_kills} K")
        parts.append(agg.title)
        return " · ".join(parts)

    # ------------------------------------------------------------ rendering

    async def _avatar_bytes(self, pid: str) -> bytes | None:
        snap = self.state.snapshot(pid)
        return await self.faceit.fetch_image(snap.avatar) if snap and snap.avatar else None

    async def build_hero(self, record: MatchRecord, pid: str, kind: PostKind) -> HeroPlayer:
        r = record.players[pid]
        snap = self.state.snapshot(pid)
        agg = self.aggregate(pid, scope="recent")
        level = next((row.level for t in record.teams for row in t.players if row.pid == pid), None)
        streak = self.streak_before(pid, record.finished_at) + (1 if r.shamed else 0)
        ctx = RoastContext.from_match(record, r, streak=streak)
        if "bait_job" in r.awards:
            ctx.grey_reasons = ", ".join(grey_zone_reasons(record, pid, self.thresholds))
        if kind is PostKind.SHAME:
            line = self.roasts.roast(ctx, seed=record.match_id)
        elif kind is PostKind.REDEMPTION:
            ctx.streak = self.streak_before(pid, record.finished_at)
            line = self.roasts.redemption(ctx, seed=record.match_id)
        elif kind is PostKind.GLORY:
            line = self.roasts.glory(ctx, seed=record.match_id, ace=(r.penta or 0) >= 1)
        elif kind is PostKind.LIABILITY:
            line = self.roasts.liability(ctx, seed=record.match_id, won=r.result == 1, kill_threshold=self.thresholds.kill_threshold)
        else:
            line = ""
        return HeroPlayer(
            pid=pid,
            result=r,
            avatar=await self._avatar_bytes(pid),
            level=level if level is not None else (snap.level if snap else None),
            elo=r.elo_after if r.elo_after is not None else (snap.elo if snap else None),
            elo_delta=r.elo_delta,
            line=line,
            form=agg.form,
            record_line=self.record_line(agg),
            avg_kd=agg.avg_kd,
            avg_adr=agg.avg_adr,
            blame=(compute_carry(record, pid) if kind is PostKind.GLORY else compute_blame(record, pid) if kind in (PostKind.SHAME, PostKind.LIABILITY) else None),
        )

    def headline_for(self, kind: PostKind, heroes: list[HeroPlayer]) -> str:
        pal = self.palette_for(kind)
        if kind is PostKind.SHAME and all("bait_job" in h.result.awards for h in heroes):
            return f"{pal.headline} — GREY ZONE"
        if kind is PostKind.SHAME and len(heroes) == 2:
            return f"{pal.headline} — DOUBLE FEATURE"
        if kind is PostKind.SHAME and len(heroes) >= 3:
            return f"{pal.headline} — TRIPLE THREAT"
        if kind is PostKind.GLORY and any((h.result.penta or 0) >= 1 for h in heroes):
            return f"{pal.headline} — ACE"
        if kind is PostKind.GLORY and all(h.result.kills >= self.thresholds.glory_kills for h in heroes):
            return f"{pal.headline} — {max(h.result.kills for h in heroes)} BOMB"
        if kind is PostKind.GLORY and all("hard_carry" in h.result.awards for h in heroes):
            return f"{pal.headline} — HARD CARRY"
        if kind is PostKind.GLORY and len(heroes) >= 2 and all(("duo_carry" in h.result.awards or "hard_carry" in h.result.awards) for h in heroes):
            return f"{pal.headline} — DUO CARRY"
        if kind is PostKind.GLORY and all("wasted" in h.result.awards for h in heroes):
            return f"{pal.headline} — WASTED"
        if kind is PostKind.LIABILITY and all(h.result.result == 1 for h in heroes):
            return f"{pal.headline} — CARRIED"
        return pal.headline

    @staticmethod
    def palette_for(kind: PostKind | None) -> T.Palette:
        return {
            PostKind.SHAME: T.SHAME,
            PostKind.REDEMPTION: T.REDEMPTION,
            PostKind.GLORY: T.GLORY,
            PostKind.LIABILITY: T.LIABILITY,
        }.get(kind, T.NEUTRAL)

    async def render_post(self, record: MatchRecord, pids: list[str], kind: PostKind | None) -> RenderedPost:
        heroes = [await self.build_hero(record, pid, kind or PostKind.SHAME) for pid in pids]
        if kind is None:
            for h in heroes:
                h.line = ""
        map_bytes = await self.faceit.fetch_image(record.map_image) if record.map_image else None
        pal = self.palette_for(kind)
        headline = self.headline_for(kind, heroes) if kind else pal.headline
        png = await asyncio.to_thread(
            render_match_card,
            record,
            heroes,
            pal,
            map_bytes=map_bytes,
            tracked=set(self.tracked),
            headline=headline,
            footer=self.settings.bot_stats_title.rstrip(":"),
        )
        return RenderedPost(kind or PostKind.SHAME, record, heroes, png, headline, {h.pid: h.line for h in heroes})

    async def leaderboard_rows(self, *, scope: str, glory: bool = False) -> list[LeaderRow]:
        aggs = self.aggregates(scope=scope)
        if glory:
            aggs.sort(key=lambda a: (a.best_kills or 0, a.clean_rate, a.avg_kills or 0), reverse=True)
        else:
            aggs.sort(key=lambda a: (a.shame_count, a.shame_rate), reverse=True)
        rows: list[LeaderRow] = []
        for a in aggs:
            snap = self.state.snapshot(a.player_id)
            avatar = await self._avatar_bytes(a.player_id)
            if glory:
                details = [f"best {a.best_kills} K" if a.best_kills is not None else "", f"avg {a.avg_kills}" if a.avg_kills is not None else ""]
                rows.append(LeaderRow(a.nickname, avatar, snap.level if snap else None, f"{a.clean_games}/{a.games}", "clean games", (a.clean_rate / 100) if a.games else 0.0, f"{a.clean_rate}%", a.glory_title, [d for d in details if d]))
            else:
                details = [f"worst {a.worst_kills} K" if a.worst_kills is not None else ""]
                if a.current_shame_streak:
                    details.append(f"streak {a.current_shame_streak}")
                rows.append(LeaderRow(a.nickname, avatar, snap.level if snap else None, f"{a.shame_count}/{a.games}", "on the wall", (a.shame_rate / 100) if a.games else 0.0, f"{a.shame_rate}%", a.title, [d for d in details if d]))
        return rows

    def scope_label(self, scope: str) -> str:
        return f"Last {self.settings.post_history_limit} games per player" if scope == "recent" else "All CS2 matches on record"

    async def render_leaderboard(self, *, scope: str, glory: bool = False, extra_lines: list[str] | None = None) -> bytes:
        rows = await self.leaderboard_rows(scope=scope, glory=glory)
        pal = T.GLORY if glory else T.SHAME
        headline = "GLORY BOARD" if glory else "WALL OF SHAME — LEADERBOARD"
        return await asyncio.to_thread(
            render_leaderboard,
            rows,
            pal,
            headline=headline,
            subtitle=self.scope_label(scope),
            footer=self.settings.bot_stats_title.rstrip(":"),
            extra_lines=extra_lines,
        )

    async def render_profile(self, pid: str) -> bytes:
        snap = self.state.snapshot(pid)
        recent = self.aggregate(pid, scope="recent")
        alltime = self.aggregate(pid, scope="all")
        data = ProfileData(
            nickname=self.nick(pid),
            avatar=await self._avatar_bytes(pid),
            level=snap.level if snap else None,
            elo=snap.elo if snap else None,
            country=snap.country if snap else None,
            elo_7d=elo_change(snap, days=7),
            elo_30d=elo_change(snap, days=30),
            elo_history=[e for _, e in (snap.elo_history if snap else [])],
            recent=recent,
            alltime=alltime,
            maps=per_map(pid, self.state.matches_for_player(pid)),
            window=self.settings.post_history_limit,
            leetify=await self.leetify_profile(pid),
        )
        return await asyncio.to_thread(render_profile, data, footer=self.settings.bot_stats_title.rstrip(":"))

    def hall(self) -> list:
        aggs = self.aggregates(scope="all")
        by_player = group_by_player(list(self.state.match_outcomes.values()))
        return hall_of_shame(aggs, {pid: by_player.get(pid, []) for pid in self.tracked_ids})

    # -------------------------------------------------------------- leetify

    async def leetify_profile(self, pid: str) -> LeetifyProfile | None:
        snap = self.state.snapshot(pid)
        if not snap or not snap.steam_id:
            return None
        return await self.leetify.profile(snap.steam_id)

    async def leetify_window_ratings(self, pids: list[str], *, scope: str, cap: int | None = None) -> dict[str, list[float]]:
        """Per-match Leetify ratings (fractions, newest first) for each player's window, all 10 players per match."""
        if cap is None:
            cap = 20 if self.leetify.has_key else 8  # unauthenticated Leetify access is rate limited tightly
        steam = {pid: (self.state.snapshot(pid).steam_id if self.state.snapshot(pid) else None) for pid in pids}
        windows = {pid: [m.match_id for m in (self.recent_records(pid) if scope == "recent" else self.state.matches_for_player(pid))[:cap]] for pid in pids}
        ids = list(dict.fromkeys(mid for w in windows.values() for mid in w))
        fetched = await asyncio.gather(*(self.leetify.match_ratings(mid) for mid in ids))
        by_match = dict(zip(ids, fetched))
        out: dict[str, list[float]] = {}
        for pid in pids:
            sid = steam.get(pid)
            out[pid] = [by_match[mid][sid] for mid in windows[pid] if sid and by_match.get(mid) and sid in by_match[mid]]  # type: ignore[index]
        return out

    # -------------------------------------------------------------- compare

    async def compare(self, pa: str, pb: str, *, scope: str) -> tuple[CompareResult, bytes]:
        agg_a, agg_b = self.aggregate(pa, scope=scope), self.aggregate(pb, scope=scope)
        snap_a, snap_b = self.state.snapshot(pa), self.state.snapshot(pb)
        leet_a, leet_b = await self.leetify_profile(pa), await self.leetify_profile(pb)
        window = await self.leetify_window_ratings([pa, pb], scope=scope) if self.settings.leetify_enabled else {}
        notes: list[str] = []
        for name, leet in ((agg_a.nickname, leet_a), (agg_b.nickname, leet_b)):
            if leet is None and self.settings.leetify_enabled:
                notes.append(f"No Leetify profile for **{name}** — Leetify metrics skipped on that side (sign up at leetify.com for full coverage).")
        if self.leetify.rate_limited:
            notes.append("Leetify is rate-limiting right now — some Leetify metrics may be missing.")
        for name, agg in ((agg_a.nickname, agg_a), (agg_b.nickname, agg_b)):
            if agg.games and not agg.extended_games:
                notes.append(f"Extended FaceIT stats for **{name}** are still being cached — entry/clutch/utility categories will fill in shortly.")
        cats = build_categories(
            agg_a, agg_b,
            elo_a=snap_a.elo if snap_a else None, elo_b=snap_b.elo if snap_b else None,
            leet_a=leet_a, leet_b=leet_b,
            window_leet_a=window.get(pa), window_leet_b=window.get(pb),
        )
        res = score(agg_a.nickname, agg_b.nickname, cats, notes=notes)
        loser = agg_b if res.winner == -1 else agg_a
        write_verdict(res, seed=f"{pa}|{pb}|{scope}", shame_rate_loser=loser.shame_rate)
        sides = []
        for pid, agg, snap, leet in ((pa, agg_a, snap_a, leet_a), (pb, agg_b, snap_b, leet_b)):
            sides.append(CompareSide(agg.nickname, await self._avatar_bytes(pid), snap.level if snap else None, snap.elo if snap else None, agg.title, leet is not None))
        png = await asyncio.to_thread(render_compare, sides[0], sides[1], res, subtitle=self.scope_label(scope), footer=self.settings.bot_stats_title.rstrip(":"))
        return res, png

    # ------------------------------------------------------------ postmortem

    def pick_postmortem_match(self, pid: str | None = None) -> MatchRecord | None:
        """``pid`` → their newest match; else the newest match with 2+ tracked players, else the newest of anyone."""
        if pid:
            recs = self.state.matches_for_player(pid)
            return recs[0] if recs else None
        recs = sorted(self.state.match_outcomes.values(), key=lambda m: m.finished_at, reverse=True)
        return next((m for m in recs if len(m.players) >= 2), recs[0] if recs else None)

    async def postmortem(self, record: MatchRecord, *, focus_pid: str | None = None) -> tuple[PostmortemResult, bytes]:
        """Rank the friends' team for one match and render the card. ``record`` should be freshly fetched with details."""
        ratings: dict[str, float] | None = None
        notes: list[str] = []
        if self.settings.leetify_enabled:
            ratings = await self.leetify.match_ratings(record.match_id) or {}
            if not ratings and self.leetify.rate_limited:
                notes.append("Leetify is rate-limiting right now — the rating column is missing.")
        tracked = set(self.tracked)
        team_index = choose_team(record, tracked, focus_pid=focus_pid)
        res = analyze(record, team_index=team_index, tracked=tracked, leetify=ratings, notes=notes)
        write_prose(res, seed=record.match_id, recent=self.roasts.recent)
        self.state.mark_dirty()
        avatars: dict[str, bytes | None] = {}
        for row in res.rows:
            if row.line.tracked:
                avatars[row.pid] = await self._avatar_bytes(row.pid)
            elif row.line.avatar:
                avatars[row.pid] = await self.faceit.fetch_image(row.line.avatar)
        map_bytes = await self.faceit.fetch_image(record.map_image) if record.map_image else None
        png = await asyncio.to_thread(render_postmortem, res, avatars=avatars, map_bytes=map_bytes, footer=self.settings.bot_stats_title.rstrip(":"))
        return res, png

    async def close(self) -> None:
        self.state.flush()
        await self.faceit.close()
        await self.leetify.close()
