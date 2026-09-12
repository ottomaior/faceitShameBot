"""Async FaceIT Data API v4 client (aiohttp). Never blocks the event loop."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://open.faceit.com/data/v4"
HISTORY_PAGE_SIZE = 100
HISTORY_MAX_OFFSET = 1000  # FaceIT caps pagination around ~1100 matches
IMAGE_USER_AGENT = "Mozilla/5.0 (compatible; faceit-shame-bot/2.0)"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
STEAM_ID_RE = re.compile(r"^\d{17}$")


class FaceitClient:
    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._image_cache: dict[str, tuple[float, bytes]] = {}

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ core

    async def get(self, path: str, params: dict[str, Any] | None = None, *, retries: int = 2) -> dict | None:
        url = f"{BASE_URL}{path}"
        session = await self.session()
        for attempt in range(retries + 1):
            try:
                async with session.get(url, headers=self._headers, params=params) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", "60"))
                        log.warning("FaceIT rate limited on %s; sleeping %ss", path, retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status == 404:
                        return None
                    if resp.status >= 500 and attempt < retries:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        log.error("FaceIT %s returned %s: %s", path, resp.status, text[:200])
                        return None
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.error("FaceIT request failed %s: %s", path, exc)
                if attempt < retries:
                    await asyncio.sleep(2 * (attempt + 1))
        return None

    # --------------------------------------------------------------- players

    async def resolve_entry(self, entry: str) -> dict | None:
        """Resolve a FaceIT UUID, 17-digit Steam ID, or nickname to a player profile."""
        if UUID_RE.match(entry):
            return await self.get(f"/players/{entry}")
        if STEAM_ID_RE.match(entry):
            data = await self.get("/players", {"game": "cs2", "game_player_id": entry})
            if not data:
                log.error(
                    "Steam ID %s not linked to a FaceIT CS2 profile. Use the FaceIT UUID instead.", entry
                )
            return data
        return await self.get("/players", {"nickname": entry})

    async def get_player(self, player_id: str) -> dict | None:
        return await self.get(f"/players/{player_id}")

    async def get_player_stats(self, player_id: str) -> dict | None:
        """Lifetime + per-map CS2 stats (``lifetime`` dict and ``segments`` list)."""
        return await self.get(f"/players/{player_id}/stats/cs2")

    async def get_history(self, player_id: str, *, limit: int, offset: int = 0) -> list[dict]:
        data = await self.get(
            f"/players/{player_id}/history", {"game": "cs2", "limit": limit, "offset": offset}
        )
        return (data or {}).get("items") or []

    async def get_all_history(self, player_id: str) -> list[dict]:
        """Paginate all fetchable CS2 history (newest first)."""
        items: list[dict] = []
        offset = 0
        while offset <= HISTORY_MAX_OFFSET:
            page = await self.get_history(player_id, limit=HISTORY_PAGE_SIZE, offset=offset)
            if not page:
                break
            items.extend(page)
            if len(page) < HISTORY_PAGE_SIZE:
                break
            offset += len(page)
        return items

    # --------------------------------------------------------------- matches

    async def get_match(self, match_id: str) -> dict | None:
        return await self.get(f"/matches/{match_id}")

    async def get_match_stats(self, match_id: str) -> dict | None:
        return await self.get(f"/matches/{match_id}/stats")

    # ---------------------------------------------------------------- images

    async def fetch_image(self, url: str, *, ttl: float = 6 * 3600) -> bytes | None:
        """Download an avatar/map image with a small in-memory TTL cache."""
        if not url:
            return None
        now = time.monotonic()
        cached = self._image_cache.get(url)
        if cached and now - cached[0] < ttl:
            return cached[1]
        try:
            session = await self.session()
            # FaceIT's CDN rejects requests without a browser-like User-Agent.
            async with session.get(url, headers={"User-Agent": IMAGE_USER_AGENT}) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Image fetch failed %s: %s", url, exc)
            return None
        if len(self._image_cache) > 200:
            self._image_cache.clear()
        self._image_cache[url] = (now, data)
        return data
