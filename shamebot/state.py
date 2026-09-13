"""Persistent state (state.json). Schema v2 with transparent loading of v1 files."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from .models import SCHEMA_VERSION, MatchRecord, PlayerSnapshot

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str) -> None:
        self.path = path
        self.processed_matches: set[str] = set()
        self.match_outcomes: dict[str, MatchRecord] = {}
        self.players: dict[str, PlayerSnapshot] = {}
        self.last_digest_at: str | None = None
        self.stats_backfill_completed_at: str | None = None
        self.liability_since: int | None = None  # unix ts; liability posts only for matches finished after this
        self.grey_zone_since: int | None = None  # unix ts; grey-zone shames only for matches finished after this
        self.recent_roasts: list[str] = []  # last roast templates used, so posts don't repeat (shared with RoastEngine)
        self._dirty = False
        self._save_handle: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------ io

    SEED_FILE = "state.json"  # committed seed; copied to STATE_FILE (e.g. a volume) on first boot

    def load(self) -> None:
        source = self.path
        if not os.path.isfile(source):
            seed = os.path.abspath(self.SEED_FILE)
            if os.path.abspath(self.path) != seed and os.path.isfile(seed):
                log.info("No state file at %s; seeding it from %s.", self.path, seed)
                source = seed
            else:
                log.info("No state file at %s; starting fresh.", self.path)
                return
        try:
            with open(source, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not load %s: %s", source, exc)
            return

        self.processed_matches = set(data.get("processed_matches") or [])
        self.match_outcomes = {
            mid: MatchRecord.from_dict(mid, rec) for mid, rec in (data.get("match_outcomes") or {}).items()
        }
        self.players = {
            pid: PlayerSnapshot.from_dict(pid, rec) for pid, rec in (data.get("players") or {}).items()
        }
        self.last_digest_at = data.get("last_digest_at")
        self.stats_backfill_completed_at = data.get("stats_backfill_completed_at")
        self.liability_since = data.get("liability_since")
        self.grey_zone_since = data.get("grey_zone_since")
        self.recent_roasts[:] = [x for x in (data.get("recent_roasts") or []) if isinstance(x, str)]
        v1 = sum(1 for m in self.match_outcomes.values() if m.v < SCHEMA_VERSION)
        log.info(
            "Loaded %d processed match(es), %d outcome(s) (%d need enrichment), %d player snapshot(s) from %s",
            len(self.processed_matches),
            len(self.match_outcomes),
            v1,
            len(self.players),
            source,
        )
        if source != self.path:
            self.save()

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "processed_matches": sorted(self.processed_matches),
            "match_outcomes": {mid: rec.to_dict() for mid, rec in self.match_outcomes.items()},
            "players": {pid: snap.to_dict() for pid, snap in self.players.items()},
            "last_digest_at": self.last_digest_at,
            "stats_backfill_completed_at": self.stats_backfill_completed_at,
            "liability_since": self.liability_since,
            "grey_zone_since": self.grey_zone_since,
            "recent_roasts": self.recent_roasts[-60:],
        }

    def save(self) -> None:
        """Atomic write: temp file in the same directory, then os.replace."""
        if self._save_handle:
            self._save_handle.cancel()
            self._save_handle = None
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=1)
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError as exc:
            log.error("Could not save %s: %s", self.path, exc)

    def mark_dirty(self, *, delay: float = 2.0) -> None:
        """Batch writes: save at most once per ``delay`` seconds while the loop is running."""
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save()
            return
        if self._save_handle is None:
            self._save_handle = loop.call_later(delay, self.save)

    def flush(self) -> None:
        if self._dirty:
            self.save()

    # ------------------------------------------------------------ accessors

    def mark_processed(self, match_id: str) -> None:
        if match_id not in self.processed_matches:
            self.processed_matches.add(match_id)
            self.mark_dirty()

    def put_match(self, record: MatchRecord) -> None:
        self.match_outcomes[record.match_id] = record
        self.mark_dirty()

    def matches_for_player(self, player_id: str) -> list[MatchRecord]:
        """All cached matches for a player, newest first."""
        rows = [m for m in self.match_outcomes.values() if player_id in m.players]
        rows.sort(key=lambda m: m.finished_at, reverse=True)
        return rows

    def pending_enrichment(self) -> list[str]:
        """Match ids still on schema v1 (kills only), newest first."""
        rows = [m for m in self.match_outcomes.values() if m.v < SCHEMA_VERSION]
        rows.sort(key=lambda m: m.finished_at, reverse=True)
        return [m.match_id for m in rows]

    def snapshot(self, player_id: str) -> PlayerSnapshot | None:
        return self.players.get(player_id)

    def put_snapshot(self, snap: PlayerSnapshot) -> None:
        self.players[snap.player_id] = snap
        self.mark_dirty()
