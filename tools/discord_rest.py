"""Minimal Discord REST helper for the tools: send/edit a Components V2 message with files, add reactions.

No gateway session is opened, so these are safe to run while the bot is deployed elsewhere.
"""
from __future__ import annotations

import json
import urllib.parse

import aiohttp
import discord

API = "https://discord.com/api/v10"
IS_COMPONENTS_V2 = 1 << 15


async def send_view(
    session: aiohttp.ClientSession,
    token: str,
    channel_id: int,
    view: discord.ui.LayoutView,
    files: list[discord.File],
    *,
    edit_message_id: str | None = None,
    allowed_mentions: dict | None = None,
) -> dict:
    payload = {
        "flags": IS_COMPONENTS_V2,
        "components": view.to_components(),
        "allowed_mentions": allowed_mentions or {"parse": []},
    }
    if files:
        payload["attachments"] = [{"id": i, "filename": f.filename} for i, f in enumerate(files)]
    form = aiohttp.FormData()
    form.add_field("payload_json", json.dumps(payload), content_type="application/json")
    for i, f in enumerate(files):
        form.add_field(f"files[{i}]", f.fp.read(), filename=f.filename, content_type="image/png")
    url = f"{API}/channels/{channel_id}/messages"
    method = "POST"
    if edit_message_id:
        url += f"/{edit_message_id}"
        method = "PATCH"
    async with session.request(method, url, data=form, headers={"Authorization": f"Bot {token}"}) as resp:
        body = await resp.json()
        if resp.status not in (200, 201):
            raise RuntimeError(f"Discord returned {resp.status}: {json.dumps(body)[:1500]}")
        return body


async def get_channel(session: aiohttp.ClientSession, token: str, channel_id: int) -> dict:
    async with session.get(f"{API}/channels/{channel_id}", headers={"Authorization": f"Bot {token}"}) as resp:
        body = await resp.json()
        if resp.status != 200:
            raise RuntimeError(f"Cannot read channel {channel_id}: {resp.status} {body}")
        return body


async def add_reaction(session: aiohttp.ClientSession, token: str, channel_id: int, message_id: str, emoji: str) -> None:
    url = f"{API}/channels/{channel_id}/messages/{message_id}/reactions/{urllib.parse.quote(emoji)}/@me"
    async with session.put(url, headers={"Authorization": f"Bot {token}"}) as resp:
        if resp.status != 204:
            print(f"  (reaction {emoji} failed: {resp.status})")


def message_link(channel: dict, message: dict) -> str:
    return f"https://discord.com/channels/{channel.get('guild_id', '@me')}/{message['channel_id']}/{message['id']}"
