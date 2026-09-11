from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Keep config import side-effect free in tests.
os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("FACEIT_API_KEY", "x")
os.environ.setdefault("SHAME_CHANNEL_ID", "1")
os.environ.setdefault("FACEIT_PLAYER_IDS", "BRNWr")

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def tracked() -> dict[str, str]:
    return {p["player_id"]: p["nickname"] for p in load("tracked_players.json")}


@pytest.fixture(scope="session")
def fixtures() -> dict[str, dict]:
    return {
        "player": load("player.json"),
        "tracked": load("tracked_players.json"),
        "stats": load("match_stats.json"),
        "details": load("match_details.json"),
        "stats_shame": load("match_stats_shame.json"),
        "details_shame": load("match_details_shame.json"),
        "stats_onekill": load("match_stats_onekill.json"),
        "details_onekill": load("match_details_onekill.json"),
    }
