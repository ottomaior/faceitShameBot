"""Refresh the committed state.json seed: cache the current post window and mark it processed.

    python tools/seed_state.py

A fresh deploy then only posts matches finished *after* the seed was made (no re-posting of the
last N games). The full-history backfill still runs once on the server.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["STATE_FILE"] = str(ROOT / "state.json")
os.environ.setdefault("DISCORD_TOKEN", "seed")
os.environ.setdefault("SHAME_CHANNEL_ID", "1")

from shamebot.app import App  # noqa: E402
from shamebot.config import Settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def main() -> None:
    app = App(Settings())
    st = app.state
    st.load()
    await app.resolve_players()
    await app.refresh_all_snapshots()
    window: dict[str, int] = {}
    for pid in app.tracked_ids:
        for it in await app.faceit.get_history(pid, limit=app.settings.post_history_limit):
            if it.get("match_id"):
                window[it["match_id"]] = int(it.get("finished_at") or 0)
    new = 0
    for mid in sorted(window, key=window.get):
        if mid not in st.match_outcomes or not st.match_outcomes[mid].enriched:
            rec = await app.fetch_record(mid, finished_at=window[mid], with_details=True)
            if rec:
                app.detect(rec)
                st.put_match(rec)
                new += 1
            await asyncio.sleep(0.5)
        st.processed_matches.add(mid)
    st.save()
    print(f"seed: {len(st.processed_matches)} processed, {len(st.match_outcomes)} outcomes ({new} fetched), {len(st.players)} snapshots -> {st.path}")
    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
