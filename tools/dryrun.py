"""Dry run: real FaceIT API, fake Discord channel. Writes the posts it would send to out/dryrun/.

    python tools/dryrun.py [post_history_limit]

Uses .env for FACEIT_API_KEY / FACEIT_PLAYER_IDS, a scratch state file, no backfill, no Discord.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out" / "dryrun"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # emoji on Windows consoles

os.environ.setdefault("DISCORD_TOKEN", "dry")
os.environ.setdefault("SHAME_CHANNEL_ID", "1")
os.environ["STATE_FILE"] = str(OUT / "state.json")
os.environ["STATS_BACKFILL_ON_STARTUP"] = "false"
os.environ.setdefault("LIABILITY_RETROACTIVE", "true")  # scratch state: show liabilities in the window too
os.environ.setdefault("SHAME_GREY_RETROACTIVE", "true")
os.environ["POST_HISTORY_LIMIT"] = sys.argv[1] if len(sys.argv) > 1 else "10"

from shamebot.app import App  # noqa: E402
from shamebot.config import Settings  # noqa: E402
from shamebot.poller import Poller  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class FakeMessage:
    async def add_reaction(self, emoji: str) -> None:
        print(f"    [reaction {emoji}]")


class FakeChannel:
    name = "dry-run"

    def __init__(self) -> None:
        self.sent = 0

    async def send(self, *args, view=None, files=None, allowed_mentions=None, **kwargs) -> FakeMessage:
        self.sent += 1
        payload = view.to_components() if view else []
        texts = []

        def walk(comps):
            for c in comps:
                if c.get("type") == 10:
                    texts.append(c["content"])
                if "components" in c:
                    walk(c["components"])
                if "accessory" in c:
                    walk([c["accessory"]])

        walk(payload)
        print(f"\n=== POST #{self.sent} ===")
        print("\n".join(texts))
        for f in files or []:
            path = OUT / f"{self.sent:02d}-{f.filename}"
            path.write_bytes(f.fp.read())
            print(f"    [image -> {path.relative_to(ROOT)}]")
        (OUT / f"{self.sent:02d}-payload.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        return FakeMessage()


class FakeClient:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.guilds: list = []

    def get_channel(self, cid: int):
        return self._channel


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    app = App(settings)
    app.state.load()
    await app.resolve_players()
    channel = FakeChannel()
    poller = Poller(FakeClient(channel), app)
    await poller.start()
    # loops are started; stop them again for the dry run
    poller.poll_loop.cancel()
    poller.enrich_loop.cancel()
    poller.digest_loop.cancel()

    print("\n=== state summary ===")
    st = app.state
    print("processed:", len(st.processed_matches), "| outcomes:", len(st.match_outcomes), "| snapshots:", len(st.players))
    for pid, snap in st.players.items():
        print(f"  {snap.nickname:<12} lvl {snap.level} elo {snap.elo} history {len(snap.elo_history)}")
    for a in app.aggregates(scope="recent"):
        print(f"  {a.nickname:<12} games {a.games} shames {a.shame_count} ({a.shame_rate}%) liabilities {a.liability_count} streak {a.current_shame_streak} title {a.title} kd {a.avg_kd} adr {a.avg_adr} wr {a.win_rate}")
    st.flush()
    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
