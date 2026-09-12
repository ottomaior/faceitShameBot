"""Post the real Wall of Shame / Redemption / Highlight message for a given match to the shame channel.

    python tools/post_match.py <match_id> [shame|redemption|glory|last]

Same rendering path as the bot (roast, awards, wall record from the cached state), sent through the
Discord REST API with the bot token — the buttons are handled by the deployed bot. The committed
state.json is used read-only (a scratch copy is loaded).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_scratch = Path(tempfile.mkdtemp(prefix="shamebot-post-")) / "state.json"
if (ROOT / "state.json").is_file():
    shutil.copy(ROOT / "state.json", _scratch)
os.environ["STATE_FILE"] = str(_scratch)

from shamebot.app import App  # noqa: E402
from shamebot.config import settings  # noqa: E402
from shamebot.rules import PostKind, compute_awards  # noqa: E402
from shamebot.views import MENTION_USERS, match_view  # noqa: E402
from tools.discord_rest import add_reaction, get_channel, message_link, send_view  # noqa: E402


async def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    match_id = sys.argv[1].strip()
    kind_name = sys.argv[2] if len(sys.argv) > 2 else "shame"
    kind = {"shame": PostKind.SHAME, "redemption": PostKind.REDEMPTION, "glory": PostKind.GLORY}.get(kind_name)

    settings.validate_or_exit()
    app = App(settings)
    app.state.load()
    await app.resolve_players()

    record = await app.fetch_record(match_id, finished_at=None, with_details=True)
    if not record or not record.players:
        sys.exit("Match not found, or no tracked player in it.")
    cached = app.state.match_outcomes.get(match_id)
    if cached:
        record.finished_at = cached.finished_at or record.finished_at
        for pid, r in record.players.items():
            if pid in cached.players:
                r.elo_after, r.elo_delta = cached.players[pid].elo_after, cached.players[pid].elo_delta

    if kind is PostKind.SHAME:
        pids = [pid for pid, r in record.players.items() if r.kills < settings.kill_threshold] or list(record.players)
        for pid in pids:
            record.players[pid].shamed = True
            record.players[pid].awards = compute_awards(
                record, pid, streak_before=app.streak_before(pid, record.finished_at), shamed_count=len(pids)
            )
    else:
        pids = list(record.players)

    post = await app.render_post(record, pids, kind)
    mention_ids = [settings.mentions[app.nick(p)] for p in pids if app.nick(p) in settings.mentions]
    view, files = match_view(app, post, mention_ids=mention_ids or None, buttons=True)

    async with aiohttp.ClientSession() as session:
        channel = await get_channel(session, settings.discord_token, settings.shame_channel_id)
        print(f"Channel: #{channel.get('name')}")
        msg = await send_view(
            session,
            settings.discord_token,
            settings.shame_channel_id,
            view,
            files,
            allowed_mentions=MENTION_USERS.to_dict(),
        )
        print(f"Posted {kind_name} for {[app.nick(p) for p in pids]} on {record.map_label}: {message_link(channel, msg)}")
        if kind is PostKind.SHAME:
            for emoji in settings.shame_reactions:
                await add_reaction(session, settings.discord_token, settings.shame_channel_id, msg["id"], emoji)
    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
