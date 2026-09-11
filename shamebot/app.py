"""Application core shared by the poller and the slash commands (no Discord message building here)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Settings
from .faceit import FaceitClient
from .models import MatchRecord, PlayerSnapshot, parse_match
from .render import theme as T
from .render.leaderboard_card import LeaderRow, render_leaderboard
from .render.profile_card import ProfileData, render_profile
from .render.shame_card import HeroPlayer, render_match_card
from .roasts import RoastContext, RoastEngine
from .rules import Detection, PostKind, Thresholds, detect
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
        self.roasts = RoastEngine(level=settings.roast_level, custom_file=settings.custom_roasts_file)
        self.tracked: dict[str, str] = {}  # player_id -> nickname
        self.backfill_in_progress = False
        self.thresholds = Thresholds(
            kill_threshold=settings.kill_threshold,
            redemption_kills=settings.redemption_kills,
            glory_kills=settings.glory_kills,
            glory_enabled=settings.glory_posts_enabled,
        )

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
        if kind is PostKind.SHAME:
            line = self.roasts.roast(ctx, seed=record.match_id)
        elif kind is PostKind.REDEMPTION:
            ctx.streak = self.streak_before(pid, record.finished_at)
            line = self.roasts.redemption(ctx, seed=record.match_id)
        elif kind is PostKind.GLORY:
            line = self.roasts.glory(ctx, seed=record.match_id, ace=(r.penta or 0) >= 1)
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
        )

    def headline_for(self, kind: PostKind, heroes: list[HeroPlayer]) -> str:
        pal = self.palette_for(kind)
        if kind is PostKind.SHAME and len(heroes) == 2:
            return f"{pal.headline} — DOUBLE FEATURE"
        if kind is PostKind.SHAME and len(heroes) >= 3:
            return f"{pal.headline} — TRIPLE THREAT"
        if kind is PostKind.GLORY and any((h.result.penta or 0) >= 1 for h in heroes):
            return f"{pal.headline} — ACE"
        return pal.headline

    @staticmethod
    def palette_for(kind: PostKind | None) -> T.Palette:
        return {
            PostKind.SHAME: T.SHAME,
            PostKind.REDEMPTION: T.REDEMPTION,
            PostKind.GLORY: T.GLORY,
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
        )
        return await asyncio.to_thread(render_profile, data, footer=self.settings.bot_stats_title.rstrip(":"))

    def hall(self) -> list:
        aggs = self.aggregates(scope="all")
        by_player = group_by_player(list(self.state.match_outcomes.values()))
        return hall_of_shame(aggs, {pid: by_player.get(pid, []) for pid in self.tracked_ids})

    async def close(self) -> None:
        self.state.flush()
        await self.faceit.close()
