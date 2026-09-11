"""Post (or update) the pinned feature guide in the shame channel via the Discord REST API.

    python tools/post_help.py            # post a new guide message, prints its link
    python tools/post_help.py <msg_id>   # edit an existing guide message in place

Uses DISCORD_TOKEN, SHAME_CHANNEL_ID, FACEIT_API_KEY, FACEIT_PLAYER_IDS from .env. No gateway
session is opened, so it is safe to run while the bot is deployed elsewhere.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shamebot.app import App  # noqa: E402
from shamebot.config import settings  # noqa: E402
from shamebot.views import help_view, load_example_card  # noqa: E402

API = "https://discord.com/api/v10"
IS_COMPONENTS_V2 = 1 << 15


async def main() -> None:
    settings.validate_or_exit()
    app = App(settings)
    await app.resolve_players()  # only for the nicknames in the intro line
    view, files = help_view(app, example=load_example_card())
    payload = {
        "flags": IS_COMPONENTS_V2,
        "components": view.to_components(),
        "allowed_mentions": {"parse": []},
    }
    if files:
        payload["attachments"] = [{"id": i, "filename": f.filename} for i, f in enumerate(files)]

    form = aiohttp.FormData()
    form.add_field("payload_json", json.dumps(payload), content_type="application/json")
    for i, f in enumerate(files):
        form.add_field(f"files[{i}]", f.fp.read(), filename=f.filename, content_type="image/png")

    headers = {"Authorization": f"Bot {settings.discord_token}"}
    url = f"{API}/channels/{settings.shame_channel_id}/messages"
    method = "POST"
    if len(sys.argv) > 1:
        url += f"/{sys.argv[1]}"
        method = "PATCH"

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API}/channels/{settings.shame_channel_id}", headers=headers) as resp:
            channel = await resp.json()
            if resp.status != 200:
                print("Cannot read channel:", resp.status, channel)
                sys.exit(1)
            print(f"Channel: #{channel.get('name')} (guild {channel.get('guild_id')})")
        async with session.request(method, url, data=form, headers=headers) as resp:
            body = await resp.json()
            if resp.status not in (200, 201):
                print("Discord returned", resp.status, json.dumps(body, indent=1)[:2000])
                sys.exit(1)
            guild = channel.get("guild_id") or "@me"
            print(f"{'Updated' if method == 'PATCH' else 'Posted'} message {body['id']}")
            print(f"https://discord.com/channels/{guild}/{body['channel_id']}/{body['id']}")
    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
