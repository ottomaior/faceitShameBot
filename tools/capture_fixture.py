"""Capture real FaceIT API responses into tests/fixtures for offline rendering and tests.

Usage (needs FACEIT_API_KEY and FACEIT_PLAYER_IDS in .env):
    python tools/capture_fixture.py            # newest match of the first tracked player
    python tools/capture_fixture.py <match_id> [suffix]  # a specific match -> match_*_<suffix>.json

Writes tests/fixtures/{player,history,match_details,match_stats}.json.
Secrets are never printed.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures"
BASE = "https://open.faceit.com/data/v4"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
STEAM_RE = re.compile(r"^\d{17}$")


def get(key: str, path: str, params: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def dump(name: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote tests/fixtures/{name}")


def main() -> None:
    env = dotenv_values(ROOT / ".env")
    key = env.get("FACEIT_API_KEY") or ""
    entries = [e.strip() for e in (env.get("FACEIT_PLAYER_IDS") or "").split(",") if e.strip()]
    if not key or not entries:
        sys.exit("FACEIT_API_KEY / FACEIT_PLAYER_IDS missing in .env")

    first = entries[0]
    if UUID_RE.match(first):
        player = get(key, f"/players/{first}")
    elif STEAM_RE.match(first):
        player = get(key, "/players", {"game": "cs2", "game_player_id": first})
    else:
        player = get(key, "/players", {"nickname": first})
    dump("player.json", player)
    pid = player["player_id"]
    print("player:", player.get("nickname"), "level", player.get("games", {}).get("cs2", {}).get("skill_level"))

    history = get(key, f"/players/{pid}/history", {"game": "cs2", "limit": 30})
    dump("history.json", history)

    match_id = sys.argv[1] if len(sys.argv) > 1 else history["items"][0]["match_id"]
    suffix = f"_{sys.argv[2]}" if len(sys.argv) > 2 else ""
    dump(f"match_details{suffix}.json", get(key, f"/matches/{match_id}"))
    stats = get(key, f"/matches/{match_id}/stats")
    dump(f"match_stats{suffix}.json", stats)

    # Print the CS2 player_stats keys so we know exactly what the API gives us.
    keys = sorted(stats["rounds"][0]["teams"][0]["players"][0]["player_stats"].keys())
    print("player_stats keys:", ", ".join(keys))
    print("round_stats keys:", ", ".join(sorted(stats["rounds"][0]["round_stats"].keys())))
    print("team_stats keys:", ", ".join(sorted(stats["rounds"][0]["teams"][0]["team_stats"].keys())))


if __name__ == "__main__":
    main()
