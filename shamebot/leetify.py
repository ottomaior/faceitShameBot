"""Leetify public API client (https://api-public.cs-prod.leetify.com).

Developer guidelines (https://leetify.com/blog/leetify-api-developer-guidelines/):
- data is fetched live and only held in a short in-memory cache — never written to state.json;
- metrics are shown the way Leetify shows them; every surface carries "Data provided by Leetify".
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://api-public.cs-prod.leetify.com"
USER_AGENT = "faceit-shame-bot/2.1 (+https://github.com/ottomaior/faceitShameBot)"
CACHE_TTL = 15 * 60
ATTRIBUTION = "Data provided by Leetify"


def profile_url(steam64: str) -> str:
    return f"https://leetify.com/app/profile/{steam64}"


def fmt_rating(value: float | None, *, fraction: bool = False) -> str:
    """Leetify rating as shown on leetify.com: signed, two decimals. Per-match values come as fractions."""
    if value is None:
        return "—"
    if fraction:
        value = value * 100
    return f"{value:+.2f}"


@dataclass
class LeetifyProfile:
    steam_id: str
    name: str
    rating: float | None = None  # ranks.leetify (already in displayed units)
    aim: float | None = None
    positioning: float | None = None
    utility: float | None = None
    clutch: float | None = None
    opening: float | None = None
    ct_rating: float | None = None
    t_rating: float | None = None
    preaim: float | None = None
    reaction_time_ms: float | None = None
    spray_accuracy: float | None = None
    counter_strafe_pct: float | None = None
    opening_duel_ct_pct: float | None = None
    opening_duel_t_pct: float | None = None
    trade_kill_pct: float | None = None
    traded_death_pct: float | None = None
    flash_leading_to_kill: float | None = None
    he_foe_damage: float | None = None
    winrate: float | None = None
    total_matches: int | None = None
    recent_ratings: list[float] = field(default_factory=list)  # fractions, newest first

    @property
    def url(self) -> str:
        return profile_url(self.steam_id)

    @property
    def opening_duel_pct(self) -> float | None:
        vals = [v for v in (self.opening_duel_ct_pct, self.opening_duel_t_pct) if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def parse_profile(data: dict) -> LeetifyProfile:
    ranks = data.get("ranks") or {}
    rating = data.get("rating") or {}
    stats = data.get("stats") or {}
    return LeetifyProfile(
        steam_id=str(data.get("steam64_id") or ""),
        name=data.get("name") or "",
        rating=_f(ranks.get("leetify")),
        aim=_f(rating.get("aim")),
        positioning=_f(rating.get("positioning")),
        utility=_f(rating.get("utility")),
        clutch=_f(rating.get("clutch")),
        opening=_f(rating.get("opening")),
        ct_rating=_f(rating.get("ct_leetify")),
        t_rating=_f(rating.get("t_leetify")),
        preaim=_f(stats.get("preaim")),
        reaction_time_ms=_f(stats.get("reaction_time_ms")),
        spray_accuracy=_f(stats.get("spray_accuracy")),
        counter_strafe_pct=_f(stats.get("counter_strafing_good_shots_ratio")),
        opening_duel_ct_pct=_f(stats.get("ct_opening_duel_success_percentage")),
        opening_duel_t_pct=_f(stats.get("t_opening_duel_success_percentage")),
        trade_kill_pct=_f(stats.get("trade_kills_success_percentage")),
        traded_death_pct=_f(stats.get("traded_deaths_success_percentage")),
        flash_leading_to_kill=_f(stats.get("flashbang_leading_to_kill")),
        he_foe_damage=_f(stats.get("he_foes_damage_avg")),
        winrate=_f(data.get("winrate")),
        total_matches=data.get("total_matches"),
        recent_ratings=[r for m in (data.get("recent_matches") or []) if (r := _f(m.get("leetify_rating"))) is not None],
    )


def parse_match_ratings(data: dict) -> dict[str, float]:
    """steam64 -> per-match leetify_rating (fraction) for every player Leetify parsed."""
    out: dict[str, float] = {}
    for s in data.get("stats") or []:
        sid = str(s.get("steam64_id") or "")
        r = _f(s.get("leetify_rating"))
        if sid and r is not None:
            out[sid] = r
    return out


class LeetifyClient:
    def __init__(self, api_key: str | None = None, *, enabled: bool = True, timeout: float = 20.0) -> None:
        self.enabled = enabled
        self.has_key = bool(api_key)
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if api_key:
            self._headers["_leetify_key"] = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._blocked_until = 0.0
        self._sem = asyncio.Semaphore(3)

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @property
    def rate_limited(self) -> bool:
        return time.monotonic() < self._blocked_until

    async def _get(self, path: str, params: dict | None = None) -> Any | None:
        """GET with TTL cache. Returns None on 404/500/429/errors (negative results are cached too)."""
        if not self.enabled:
            return None
        key = path + ("?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())) if params else "")
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
        if self.rate_limited:
            return None
        data: Any | None = None
        try:
            async with self._sem:
                session = await self._session_get()
                async with session.get(BASE_URL + path, params=params, headers=self._headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                    elif resp.status == 429:
                        retry = int(resp.headers.get("Retry-After", "60"))
                        if not self.rate_limited:
                            log.warning("Leetify rate limited; pausing Leetify lookups for %ss%s", retry, "" if self.has_key else " (no LEETIFY_API_KEY set)")
                        self._blocked_until = time.monotonic() + retry
                        return None
                    elif resp.status not in (404, 500):
                        log.warning("Leetify %s returned %s", path, resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Leetify request failed %s: %s", path, exc)
            return None
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = (now, data)
        return data

    async def profile(self, steam64: str | None) -> LeetifyProfile | None:
        if not steam64:
            return None
        data = await self._get("/v3/profile", {"steam64_id": steam64})
        if not data or data.get("privacy_mode") not in (None, "public"):
            return None
        return parse_profile(data)

    async def match_ratings(self, faceit_match_id: str) -> dict[str, float] | None:
        data = await self._get(f"/v2/matches/faceit/{faceit_match_id}")
        return parse_match_ratings(data) if data else None
