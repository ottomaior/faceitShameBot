"""Startup sequence and background loops: polling, backfill, v1→v2 enrichment, weekly digest."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import discord
from discord.ext import tasks

from .app import App
from .models import SCHEMA_VERSION
from .rules import Detection, PostKind
from .stats import HallEntry
from .views import MENTION_USERS, leaderboard_view, match_view, postmortem_view

log = logging.getLogger(__name__)


class Poller:
    def __init__(self, client: discord.Client, app: App) -> None:
        self.client = client
        self.app = app
        self.channel: discord.abc.Messageable | None = None  # shame channel (also the fallback)
        self.channels: dict[str, discord.abc.Messageable] = {}  # "fame" / "postmortem" when configured
        self._failed_fetches: dict[str, int] = {}
        self._started = False
        self._poll_lock = asyncio.Lock()
        self.poll_loop.change_interval(seconds=app.settings.poll_interval_seconds)

    # ------------------------------------------------------------- startup

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.channel = await self.get_shame_channel()
        if self.channel is None:
            return
        for key, cid in (("fame", self.app.settings.fame_channel_id), ("postmortem", self.app.settings.postmortem_channel_id)):
            if cid and cid != self.app.settings.shame_channel_id:
                ch = await self.fetch_channel(cid)
                if ch is not None:
                    self.channels[key] = ch
                    log.info("%s posts go to #%s", key.capitalize(), getattr(ch, "name", cid))
        self.app.arm_grey_zone()
        await self.app.refresh_all_snapshots()
        await self.backfill()
        await self.startup_scan()
        self.poll_loop.start()
        self.enrich_loop.start()
        self.digest_loop.start()
        log.info("Loops started (poll every %ss).", self.app.settings.poll_interval_seconds)

    async def fetch_channel(self, cid: int) -> discord.abc.Messageable | None:
        channel = self.client.get_channel(cid)
        if channel is not None:
            return channel  # type: ignore[return-value]
        try:
            return await self.client.fetch_channel(cid)  # type: ignore[return-value]
        except discord.HTTPException as exc:
            log.error("Cannot access channel %s (%s); falling back to the shame channel.", cid, exc)
            return None

    def channel_for(self, kind: PostKind) -> discord.abc.Messageable:
        assert self.channel is not None
        if kind in (PostKind.GLORY, PostKind.REDEMPTION):
            return self.channels.get("fame", self.channel)
        return self.channel

    async def get_shame_channel(self) -> discord.abc.Messageable | None:
        cid = self.app.settings.shame_channel_id
        channel = self.client.get_channel(cid)
        if channel is not None:
            return channel  # type: ignore[return-value]
        try:
            channel = await self.client.fetch_channel(cid)
            log.info("Fetched shame channel: #%s", getattr(channel, "name", cid))
            return channel  # type: ignore[return-value]
        except discord.NotFound:
            log.error("Channel %s does not exist or the bot cannot access it.", cid)
        except discord.Forbidden:
            log.error("Bot lacks permission to access channel %s.", cid)
        except discord.HTTPException as exc:
            log.error("Failed to fetch channel %s: %s", cid, exc)
        guilds = ", ".join(g.name for g in self.client.guilds) or "(none — invite the bot first)"
        log.error("Bot is in these server(s): %s. Fix SHAME_CHANNEL_ID or invite the bot to the right server.", guilds)
        return None

    # ------------------------------------------------------------ backfill

    async def _post_window(self) -> tuple[dict[str, int], dict[str, list[str]]]:
        """{match_id: finished_at} for the post window of every tracked player + per-player unseen ids."""
        window: dict[str, int] = {}
        per_player_new: dict[str, list[str]] = {}
        for pid in self.app.tracked_ids:
            items = await self.app.faceit.get_history(pid, limit=self.app.settings.post_history_limit)
            for it in items:
                mid = it.get("match_id")
                if not mid:
                    continue
                window[mid] = int(it.get("finished_at") or 0)
                if mid not in self.app.state.processed_matches:
                    per_player_new.setdefault(pid, []).append(mid)
        return window, per_player_new

    async def backfill(self) -> None:
        s = self.app.settings
        st = self.app.state
        if not s.stats_backfill_on_startup:
            log.info("STATS_BACKFILL_ON_STARTUP=false; skipping full history stats backfill.")
            return
        if st.stats_backfill_completed_at:
            log.info("Full stats backfill already completed at %s; skipping.", st.stats_backfill_completed_at)
            return

        self.app.backfill_in_progress = True
        try:
            window, _ = await self._post_window()
            finished: dict[str, int] = {}
            for pid in self.app.tracked_ids:
                for it in await self.app.faceit.get_all_history(pid):
                    if it.get("match_id"):
                        finished[it["match_id"]] = int(it.get("finished_at") or 0)
            all_ids = sorted(finished, key=finished.get)  # oldest first so streaks/awards are right
            total = len(all_ids)
            log.info("Full stats backfill: %d unique match(es) across tracked players...", total)
            cached = marked = 0
            for idx, mid in enumerate(all_ids, 1):
                if mid not in st.match_outcomes:
                    rec = await self.app.fetch_record(mid, finished_at=finished[mid], with_details=False)
                    if rec:
                        self.app.detect(rec)
                        st.put_match(rec)
                        cached += 1
                    await asyncio.sleep(s.stats_backfill_sleep_seconds)
                if mid not in window and mid not in st.processed_matches:
                    st.processed_matches.add(mid)
                    marked += 1
                if idx % 50 == 0 or idx == total:
                    log.info("Stats backfill progress: %d/%d", idx, total)
                    st.mark_dirty()
            st.stats_backfill_completed_at = datetime.now(timezone.utc).isoformat()
            st.save()
            log.info("Full stats backfill done: %d new cached, %d marked processed (no post), %d total outcomes.", cached, marked, len(st.match_outcomes))
        finally:
            self.app.backfill_in_progress = False

    # ----------------------------------------------------------- polling

    async def startup_scan(self) -> None:
        allow = self.app.settings.post_history_on_startup
        log.info("Retroactive scan of the post window (posting=%s)...", allow)
        posted = await self.poll_once(allow_post=allow)
        log.info("Retroactive scan done: %d post(s).", posted)

    async def poll_once(self, *, allow_post: bool = True) -> int:
        """Process unseen post-window matches (oldest first). Returns the number of posts sent."""
        async with self._poll_lock:
            st = self.app.state
            window, per_player_new = await self._post_window()
            unseen = sorted((m for m in window if m not in st.processed_matches), key=window.get)
            if not unseen:
                return 0

            # ELO before/after: snapshot values before refreshing.
            prev = {pid: (snap.elo, snap.elo_ts or 0) for pid in self.app.tracked_ids if (snap := st.snapshot(pid))}
            await self.app.refresh_all_snapshots()

            posted = 0
            for mid in unseen:
                record = await self.app.fetch_record(mid, finished_at=window[mid], with_details=True)
                if record is None:
                    self._failed_fetches[mid] = self._failed_fetches.get(mid, 0) + 1
                    if self._failed_fetches[mid] >= 3:
                        log.warning("Giving up on match %s after 3 failed stats fetches.", mid)
                        st.mark_processed(mid)
                    continue

                for pid, r in record.players.items():
                    snap = st.snapshot(pid)
                    if not snap or snap.elo is None:
                        continue
                    prev_elo, prev_ts = prev.get(pid, (None, 0))
                    if record.finished_at > prev_ts:
                        r.elo_after = snap.elo
                        if prev_elo is not None and len(per_player_new.get(pid, [])) == 1:
                            r.elo_delta = snap.elo - prev_elo
                        snap.push_elo(record.finished_at or int(time.time()), snap.elo)

                detections = self.app.detect(record)
                st.put_match(record)
                if allow_post and self.postmortem_wanted(record):
                    st.pending_postmortems[mid] = record.finished_at or int(time.time())
                    st.mark_dirty()
                if allow_post:
                    for det in detections:
                        if det.kind is PostKind.LIABILITY and not self._liability_live(record.finished_at):
                            continue
                        try:
                            await self.post(det)
                            posted += 1
                        except discord.HTTPException as exc:
                            log.error("Failed to post %s for match %s: %s", det.kind.value, mid, exc)
                st.mark_processed(mid)
                await asyncio.sleep(1)
            st.flush()
            return posted

    def _liability_live(self, finished_at: int | None) -> bool:
        """Liability posts start from the moment the feature is first enabled, never retroactively."""
        st = self.app.state
        if self.app.settings.liability_retroactive:
            return True
        if st.liability_since is None:
            st.liability_since = int(time.time())
            st.mark_dirty()
            log.info("Liability posts enabled from now on (liability_since=%d).", st.liability_since)
        return (finished_at or 0) >= st.liability_since

    async def post(self, det: Detection) -> None:
        channel = self.channel_for(det.kind)
        post = await self.app.render_post(det.match, det.player_ids, det.kind)
        s = self.app.settings
        mention_ids: list[int] = []
        if s.mention_shamed_on_post:
            for pid in det.player_ids:
                uid = s.mentions.get(self.app.nick(pid))
                if uid:
                    mention_ids.append(uid)
        view, files = match_view(self.app, post, mention_ids=mention_ids)
        msg = await channel.send(view=view, files=files, allowed_mentions=MENTION_USERS)
        log.info("Posted %s for match %s: %s", det.kind.value, det.match.match_id, [self.app.nick(p) for p in det.player_ids])
        if det.kind is PostKind.SHAME:
            for emoji in s.shame_reactions:
                try:
                    await msg.add_reaction(emoji)
                except discord.HTTPException:
                    log.debug("Could not add reaction %r", emoji)

    @tasks.loop(seconds=300)
    async def poll_loop(self) -> None:
        try:
            await self.poll_once(allow_post=True)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("Poll cycle failed")
        try:
            await self.flush_postmortems()
        except Exception:  # noqa: BLE001
            log.exception("Post-mortem flush failed")

    # -------------------------------------------------------- post-mortem

    def postmortem_wanted(self, record) -> bool:
        """Auto post-mortem for matches with >= POSTMORTEM_MIN_TRACKED tracked players on one team."""
        s = self.app.settings
        if not s.postmortem_auto or not record.teams:
            return False
        tracked = set(self.app.tracked)
        return max(sum(1 for row in t.players if row.pid in tracked) for t in record.teams) >= s.postmortem_min_tracked

    async def flush_postmortems(self) -> int:
        """Post pending post-mortems once Leetify has the match or the wait deadline passed. Returns posts sent."""
        st = self.app.state
        s = self.app.settings
        if not st.pending_postmortems or self.channel is None:
            return 0
        posted = 0
        now = int(time.time())
        for mid, finished_at in list(st.pending_postmortems.items()):
            deadline = finished_at + s.postmortem_leetify_wait_minutes * 60
            if s.leetify_enabled and now < deadline and not await self.app.leetify.match_ratings(mid):
                continue  # Leetify hasn't processed it yet; retry next cycle
            fresh = await self.app.fetch_record(mid, finished_at=finished_at, with_details=True)
            if fresh is None:
                if now > deadline + 6 * 3600:
                    log.warning("Dropping post-mortem for %s: stats never became available.", mid)
                    del st.pending_postmortems[mid]
                    st.mark_dirty()
                continue
            cached = st.match_outcomes.get(mid)
            if cached:
                for pid, r in fresh.players.items():
                    if pid in cached.players:
                        r.elo_after, r.elo_delta, r.awards = cached.players[pid].elo_after, cached.players[pid].elo_delta, cached.players[pid].awards
            self.app.detect(fresh)
            st.put_match(fresh)
            try:
                res, png = await self.app.postmortem(fresh)
                links = [("Open on FaceIT", fresh.faceit_url)]
                view, files = postmortem_view(res, png, links=links)
                await self.channels.get("postmortem", self.channel).send(view=view, files=files)
                posted += 1
                log.info("Posted post-mortem for match %s: leetify=%s, %d min after the match finished.", mid, res.leetify_used, (now - finished_at) // 60)
            except discord.HTTPException as exc:
                log.error("Failed to post post-mortem for %s: %s", mid, exc)
            del st.pending_postmortems[mid]
            st.mark_dirty()
        st.flush()
        return posted

    # -------------------------------------------------------- enrichment

    @tasks.loop(seconds=2)
    async def enrich_loop(self) -> None:
        """Upgrade one v1 (kills-only) record per tick, newest first, until none are left."""
        if self.app.backfill_in_progress:
            return
        pending = self.app.state.pending_enrichment()
        if not pending:
            self.enrich_loop.stop()
            log.info("All cached matches are on schema v%d.", SCHEMA_VERSION)
            return
        mid = pending[0]
        old = self.app.state.match_outcomes[mid]
        rec = await self.app.fetch_record(mid, finished_at=old.finished_at, with_details=False)
        if rec is None:
            # keep the old record but stop retrying it
            old.v = SCHEMA_VERSION
            self.app.state.put_match(old)
            return
        for pid, r in rec.players.items():
            if pid in old.players:
                r.elo_after, r.elo_delta = old.players[pid].elo_after, old.players[pid].elo_delta
        self.app.detect(rec)
        self.app.state.put_match(rec)
        if len(pending) % 50 == 0:
            log.info("Enrichment: %d match(es) left.", len(pending) - 1)

    # ------------------------------------------------------------ digest

    def digest_is_due(self) -> bool:
        s = self.app.settings
        if not s.weekly_digest_enabled:
            return False
        now = datetime.now(timezone.utc)
        if now.weekday() != s.weekly_digest_cron_day or now.hour != s.weekly_digest_hour_utc:
            return False
        last = self.app.state.last_digest_at
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if (now - last_dt).total_seconds() < 6 * 24 * 3600:
                    return False
            except ValueError:
                pass
        return True

    def digest_lines(self) -> list[str]:
        aggs = [a for a in self.app.aggregates(scope="recent") if a.games]
        lines: list[str] = []
        if aggs:
            bot = max(aggs, key=lambda a: (a.shame_count, a.shame_rate))
            lines.append(f"Bot of the week: {bot.nickname} ({bot.shame_count} shames in the last {self.app.settings.post_history_limit})")
        for e in self.app.hall():
            if isinstance(e, HallEntry) and e.label in ("Biggest ELO loss", "Biggest ELO gain", "Longest shame streak", "Worst game ever"):
                lines.append(f"{e.label}: {e.nickname} ({e.value})")
        return lines[:6]

    async def post_digest(self) -> None:
        assert self.channel is not None
        png = await self.app.render_leaderboard(scope="all", extra_lines=self.digest_lines())
        view, files = leaderboard_view(self.app, png, scope="all", glory=False, summary="📅 **Weekly Wall of Shame digest**\n" + "\n".join(self.digest_lines()))
        await self.channel.send(view=view, files=files)
        self.app.state.last_digest_at = datetime.now(timezone.utc).isoformat()
        self.app.state.save()
        log.info("Posted weekly shame digest.")

    @tasks.loop(minutes=30)
    async def digest_loop(self) -> None:
        try:
            if self.digest_is_due() and any(a.games for a in self.app.aggregates(scope="all")):
                await self.post_digest()
        except Exception:  # noqa: BLE001
            log.exception("Digest failed")
