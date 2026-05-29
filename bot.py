import asyncio
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import discord
from discord import app_commands
import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

FACEIT_API_KEY = os.getenv("FACEIT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SHAME_CHANNEL_ID = int(os.getenv("SHAME_CHANNEL_ID", "0"))
KILL_THRESHOLD = int(os.getenv("KILL_THRESHOLD", "10"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
POST_HISTORY_LIMIT = int(
    os.getenv("POST_HISTORY_LIMIT") or os.getenv("HISTORY_LIMIT", "30")
)
BOT_STATS_TITLE = os.getenv("BOT_STATS_TITLE", "BRWNr Bot Stats:")
STATS_BACKFILL_ON_STARTUP = os.getenv("STATS_BACKFILL_ON_STARTUP", "true").lower() in (
    "1",
    "true",
    "yes",
)
STATS_BACKFILL_SLEEP_SECONDS = float(os.getenv("STATS_BACKFILL_SLEEP_SECONDS", "1"))
MENTION_SHAMED_ON_POST = os.getenv("MENTION_SHAMED_ON_POST", "true").lower() in (
    "1",
    "true",
    "yes",
)
FACEIT_HISTORY_PAGE_SIZE = 100
FACEIT_HISTORY_MAX_OFFSET = 1000
POST_HISTORY_ON_STARTUP = os.getenv(
    "POST_HISTORY_ON_STARTUP", "true"
).lower() in ("1", "true", "yes")
STATE_FILE = os.getenv("STATE_FILE", "state.json")
WEEKLY_DIGEST_ENABLED = os.getenv("WEEKLY_DIGEST_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
WEEKLY_DIGEST_CRON_DAY = int(os.getenv("WEEKLY_DIGEST_CRON_DAY", "0"))
WEEKLY_DIGEST_HOUR_UTC = int(os.getenv("WEEKLY_DIGEST_HOUR_UTC", "18"))
STATS_COMMAND_ANY_CHANNEL = os.getenv("STATS_COMMAND_ANY_CHANNEL", "false").lower() in (
    "1",
    "true",
    "yes",
)

_raw_ids = os.getenv("FACEIT_PLAYER_IDS", "")
PLAYER_ENTRIES = [entry.strip() for entry in _raw_ids.split(",") if entry.strip()]
# FaceIT UUIDs resolved from entries (UUID, Steam ID, or nickname).
PLAYER_IDS: list[str] = []

HEADERS = {"Authorization": f"Bearer {FACEIT_API_KEY}"}
BASE_URL = "https://open.faceit.com/data/v4"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
STEAM_ID_RE = re.compile(r"^\d{17}$")

seen_matches: set[str] = set()
match_outcomes: dict[str, dict] = {}
last_digest_at: str | None = None
stats_backfill_completed_at: str | None = None
stats_backfill_in_progress = False
tracked_nicknames: set[str] = set()
nick_to_player_id: dict[str, str] = {}
player_id_to_nick: dict[str, str] = {}
nick_to_discord_id: dict[str, int] = {}

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


IMG_WIDTH = 1100

# Column x-offsets for monospace scoreboard table
COL_PLAYER = 24
COL_K = 300
COL_D = 350
COL_A = 400
COL_KD = 450
COL_HS = 530
COL_MVP = 610
COL_ADR = 680


def _load_mono_font(size: int = 15) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
    ):
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _stat_value(stats: dict, key: str, default: str = "-") -> str:
    val = stats.get(key)
    if val is None or val == "":
        return default
    return str(val)


def _truncate_nick(nick: str, max_len: int = 22) -> str:
    return nick if len(nick) <= max_len else nick[: max_len - 1] + "…"


def format_match_header(round_data: dict) -> str:
    """Build map + final score line from FaceIT round payload."""
    round_stats = round_data.get("round_stats") or {}
    map_name = round_stats.get("Map", "")
    overall = round_stats.get("Score", "")

    team_parts = []
    for team in round_data.get("teams", []):
        ts = team.get("team_stats") or {}
        name = ts.get("Team", "Team")
        score = ts.get("Final Score", "?")
        team_parts.append(f"{name} {score}")

    if team_parts:
        teams_str = "  vs  ".join(team_parts)
        if map_name and overall:
            return f"{map_name}  |  {overall}  ({teams_str})"
        if map_name:
            return f"{map_name}  |  {teams_str}"
        return teams_str
    if map_name and overall:
        return f"{map_name}  |  {overall}"
    return overall or map_name or ""


def faceit_get(path: str, params: dict | None = None) -> dict | None:
    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    except requests.RequestException as exc:
        log.error("FaceIT request failed %s: %s", path, exc)
        return None

    if r.status_code == 429:
        retry_after = int(r.headers.get("Retry-After", 60))
        log.warning("FaceIT rate limited; sleeping %ss", retry_after)
        import time

        time.sleep(retry_after)
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        except requests.RequestException as exc:
            log.error("FaceIT retry failed %s: %s", path, exc)
            return None

    if r.status_code != 200:
        log.error("FaceIT %s returned %s: %s", path, r.status_code, r.text[:200])
        return None

    return r.json()


def resolve_player_entry(entry: str) -> dict | None:
    """Resolve FaceIT UUID, Steam ID, or nickname to a player profile dict."""
    if UUID_RE.match(entry):
        return faceit_get(f"/players/{entry}")

    if STEAM_ID_RE.match(entry):
        log.info(
            "Entry %s looks like a Steam ID; resolving via FaceIT game_player_id.",
            entry,
        )
        data = faceit_get("/players", {"game": "cs2", "game_player_id": entry})
        if data:
            return data
        log.error(
            "Steam ID %s not linked to a FaceIT CS2 profile. "
            "Use the FaceIT UUID from the profile URL instead.",
            entry,
        )
        return None

    log.info("Entry %s treated as FaceIT nickname.", entry)
    return faceit_get("/players", {"nickname": entry})


def get_player_profile(player_id: str) -> dict | None:
    return faceit_get(f"/players/{player_id}")


def get_player_nickname(player_id: str) -> str | None:
    data = get_player_profile(player_id)
    if not data:
        return None
    return data.get("nickname")


def get_all_cs2_matches(player_id: str) -> list[dict]:
    """Paginate FaceIT CS2 history (newest first). Capped by API offset limit."""
    all_items: list[dict] = []
    offset = 0
    while offset <= FACEIT_HISTORY_MAX_OFFSET:
        data = faceit_get(
            f"/players/{player_id}/history",
            {
                "game": "cs2",
                "limit": FACEIT_HISTORY_PAGE_SIZE,
                "offset": offset,
            },
        )
        if not data:
            break
        items = data.get("items") or []
        if not items:
            break
        all_items.extend(items)
        if len(items) < FACEIT_HISTORY_PAGE_SIZE:
            break
        offset += len(items)
    return all_items


def get_post_window_matches(player_id: str) -> list[dict]:
    """Recent CS2 matches used for posting and /shamestats_recent (single API call)."""
    data = faceit_get(
        f"/players/{player_id}/history",
        {"game": "cs2", "limit": POST_HISTORY_LIMIT},
    )
    if not data:
        return []
    return data.get("items") or []


def load_state() -> None:
    global seen_matches, match_outcomes, last_digest_at, stats_backfill_completed_at
    if not os.path.isfile(STATE_FILE):
        seen_matches = set()
        match_outcomes = {}
        last_digest_at = None
        stats_backfill_completed_at = None
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        seen_matches = set(data.get("processed_matches", []))
        match_outcomes = data.get("match_outcomes") or {}
        last_digest_at = data.get("last_digest_at")
        stats_backfill_completed_at = data.get("stats_backfill_completed_at")
        log.info(
            "Loaded %d processed match(es), %d outcome(s) from %s",
            len(seen_matches),
            len(match_outcomes),
            STATE_FILE,
        )
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Could not load %s: %s", STATE_FILE, exc)
        seen_matches = set()
        match_outcomes = {}
        last_digest_at = None
        stats_backfill_completed_at = None


def save_state() -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "processed_matches": sorted(seen_matches),
                    "match_outcomes": match_outcomes,
                    "last_digest_at": last_digest_at,
                    "stats_backfill_completed_at": stats_backfill_completed_at,
                },
                f,
                indent=2,
            )
    except OSError as exc:
        log.error("Could not save %s: %s", STATE_FILE, exc)


def mark_match_processed(match_id: str, *, persist: bool = True) -> None:
    seen_matches.add(match_id)
    if persist:
        save_state()


def collect_post_window_finished_at() -> dict[str, int]:
    """Map match_id -> finished_at from post-window player histories."""
    finished: dict[str, int] = {}
    for player_id in PLAYER_IDS:
        for match in get_post_window_matches(player_id):
            match_id = match.get("match_id")
            if match_id:
                finished[match_id] = int(match.get("finished_at") or 0)
    return finished


def get_post_window_match_id_set() -> set[str]:
    """Union of post-window match IDs across all tracked players."""
    window: set[str] = set()
    for player_id in PLAYER_IDS:
        for match in get_post_window_matches(player_id):
            match_id = match.get("match_id")
            if match_id:
                window.add(match_id)
    return window


def _player_kills_from_row(player: dict) -> int:
    try:
        return int(player.get("player_stats", {}).get("Kills", KILL_THRESHOLD))
    except (TypeError, ValueError):
        return KILL_THRESHOLD


def _resolve_player_id_from_row(player: dict) -> str | None:
    pid = player.get("player_id")
    if pid:
        return str(pid)
    nick = player.get("nickname")
    if nick and nick in nick_to_player_id:
        return nick_to_player_id[nick]
    return None


def record_match_outcomes(
    match_id: str,
    stats: dict,
    *,
    finished_at: int | None = None,
) -> None:
    """Persist per-tracked-player kills and shame flag for a match."""
    global match_outcomes
    rounds = stats.get("rounds") or []
    if not rounds:
        return

    players: dict[str, dict] = {}
    for team in rounds[0].get("teams", []):
        for player in team.get("players", []):
            nick = player.get("nickname")
            if nick not in tracked_nicknames:
                continue
            player_id = _resolve_player_id_from_row(player)
            if not player_id:
                continue
            kills = _player_kills_from_row(player)
            players[player_id] = {
                "nickname": nick,
                "kills": kills,
                "shamed": kills < KILL_THRESHOLD,
            }

    if not players:
        return

    entry: dict = {"players": players}
    if finished_at is not None:
        entry["finished_at"] = finished_at

    match_outcomes[match_id] = entry
    save_state()


def get_player_post_window_match_ids(player_id: str) -> list[str]:
    """Post-window match IDs for a player, newest first."""
    return [
        m["match_id"]
        for m in get_post_window_matches(player_id)
        if m.get("match_id")
    ]


def get_player_alltime_match_ids(player_id: str) -> list[str]:
    """All cached match IDs for a player, newest first by finished_at."""
    dated: list[tuple[str, int]] = []
    for match_id, mo in match_outcomes.items():
        pdata = mo.get("players", {}).get(player_id)
        if not pdata:
            continue
        dated.append((match_id, int(mo.get("finished_at") or 0)))
    dated.sort(key=lambda x: x[1], reverse=True)
    return [match_id for match_id, _ in dated]


def get_shame_title(shame_count: int, current_shame_streak: int) -> str:
    if current_shame_streak >= 3:
        return "Heater (reverse)"
    if shame_count == 0:
        return "Saint"
    if shame_count <= 2:
        return "Slump"
    if shame_count >= 5:
        return "Permanent resident"
    return "Regular"


def _compute_player_shame_from_match_ids(
    player_id: str, match_ids: list[str]
) -> dict:
    """Aggregate shame stats over the given match IDs (newest first)."""
    nickname = player_id_to_nick.get(player_id, player_id)

    shame_count = 0
    games_in_window = 0
    shame_kills: list[int] = []
    all_kills: list[int] = []
    worst_k: int | None = None
    best_k: int | None = None
    current_shame_streak = 0
    current_clean_streak = 0
    longest_shame_streak = 0
    run = 0

    for match_id in match_ids:
        mo = match_outcomes.get(match_id)
        if not mo:
            continue
        pdata = mo.get("players", {}).get(player_id)
        if not pdata:
            continue
        games_in_window += 1
        kills = pdata["kills"]
        all_kills.append(kills)
        if best_k is None or kills > best_k:
            best_k = kills
        if pdata["shamed"]:
            shame_count += 1
            shame_kills.append(kills)
            if worst_k is None or kills < worst_k:
                worst_k = kills

    for match_id in match_ids:
        mo = match_outcomes.get(match_id)
        if not mo:
            break
        pdata = mo.get("players", {}).get(player_id)
        if not pdata:
            break
        if pdata["shamed"]:
            current_shame_streak += 1
        else:
            break

    for match_id in match_ids:
        mo = match_outcomes.get(match_id)
        if not mo:
            break
        pdata = mo.get("players", {}).get(player_id)
        if not pdata:
            break
        if not pdata["shamed"]:
            current_clean_streak += 1
        else:
            break

    for match_id in reversed(match_ids):
        mo = match_outcomes.get(match_id)
        if not mo:
            continue
        pdata = mo.get("players", {}).get(player_id)
        if not pdata:
            continue
        if pdata["shamed"]:
            run += 1
            longest_shame_streak = max(longest_shame_streak, run)
        else:
            run = 0

    shame_rate = (
        round(100 * shame_count / games_in_window) if games_in_window else 0
    )
    avg_kills_when_shamed = (
        round(sum(shame_kills) / len(shame_kills), 1) if shame_kills else None
    )
    avg_kills = round(sum(all_kills) / len(all_kills), 1) if all_kills else None
    clean_games = games_in_window - shame_count
    clean_rate = (
        round(100 * clean_games / games_in_window) if games_in_window else 0
    )

    return {
        "player_id": player_id,
        "nickname": nickname,
        "shame_count": shame_count,
        "games_in_window": games_in_window,
        "shame_rate": shame_rate,
        "clean_games": clean_games,
        "clean_rate": clean_rate,
        "current_shame_streak": current_shame_streak,
        "current_clean_streak": current_clean_streak,
        "longest_shame_streak": longest_shame_streak,
        "avg_kills_when_shamed": avg_kills_when_shamed,
        "avg_kills": avg_kills,
        "worst_kills": worst_k,
        "best_kills": best_k,
        "title": get_shame_title(shame_count, current_shame_streak),
    }


def compute_player_shame_recent(player_id: str) -> dict:
    return _compute_player_shame_from_match_ids(
        player_id, get_player_post_window_match_ids(player_id)
    )


def compute_player_shame_alltime(player_id: str) -> dict:
    return _compute_player_shame_from_match_ids(
        player_id, get_player_alltime_match_ids(player_id)
    )


def compute_all_tracked_shame_recent() -> list[dict]:
    return [compute_player_shame_recent(pid) for pid in PLAYER_IDS]


def compute_all_tracked_shame_alltime() -> list[dict]:
    return [compute_player_shame_alltime(pid) for pid in PLAYER_IDS]


def rolling_by_nickname() -> dict[str, dict]:
    return {r["nickname"]: r for r in compute_all_tracked_shame_recent()}


def get_glory_title(clean_rate: int, best_kills: int | None) -> str:
    if best_kills is not None and best_kills >= 30:
        return "Fragger"
    if clean_rate >= 90:
        return "Reliable"
    if clean_rate >= 70:
        return "Solid"
    if clean_rate >= 50:
        return "Mixed"
    return "Work in progress"


def compute_player_glory_alltime(player_id: str) -> dict:
    stats = compute_player_shame_alltime(player_id)
    return {
        **stats,
        "glory_title": get_glory_title(stats["clean_rate"], stats["best_kills"]),
    }


def compute_all_tracked_glory_alltime() -> list[dict]:
    return [compute_player_glory_alltime(pid) for pid in PLAYER_IDS]


def format_rolling_line(rolling: dict) -> str:
    sc = rolling["shame_count"]
    gw = rolling["games_in_window"]
    rate = rolling["shame_rate"]
    streak = rolling["current_shame_streak"]
    parts = [
        f"Wall record (last {POST_HISTORY_LIMIT}): **{sc}/{gw}** games ({rate}%)"
    ]
    if streak > 0:
        parts.append(f"streak **{streak}**")
    if rolling["worst_kills"] is not None:
        parts.append(f"worst **{rolling['worst_kills']} K**")
    parts.append(f"Title: **{rolling['title']}**")
    return " · ".join(parts)


def _format_shame_row(r: dict) -> str:
    line = (
        f"{r['nickname']} — **{r['shame_count']}** shames / "
        f"**{r['games_in_window']}** games ({r['shame_rate']}%)"
    )
    if r["worst_kills"] is not None:
        line += f" · worst **{r['worst_kills']} K**"
    if r["current_shame_streak"] > 0:
        line += f" · streak **{r['current_shame_streak']}**"
    line += f" · **{r['title']}**"
    return line


def format_shame_leaderboard_message(
    rows: list[dict], *, scope: str
) -> str:
    sorted_rows = sorted(
        rows,
        key=lambda r: (r["shame_count"], r["shame_rate"]),
        reverse=True,
    )
    if scope == "recent":
        header = (
            f"**{BOT_STATS_TITLE} Shame Leaderboard** "
            f"(last {POST_HISTORY_LIMIT} games)"
        )
    else:
        header = (
            f"**{BOT_STATS_TITLE} Shame Leaderboard** "
            f"(all CS2 matches on record)"
        )
    lines = [header]
    for i, r in enumerate(sorted_rows, 1):
        lines.append(f"{i}. {_format_shame_row(r)}")
    if not sorted_rows:
        lines.append("_No tracked players._")
    return "\n".join(lines)


def format_glory_leaderboard_message(rows: list[dict]) -> str:
    sorted_rows = sorted(
        rows,
        key=lambda r: (r.get("best_kills") or 0, r["clean_rate"], r["avg_kills"] or 0),
        reverse=True,
    )
    lines = [
        f"**{BOT_STATS_TITLE} Glory Leaderboard** (all CS2 matches on record)"
    ]
    for i, r in enumerate(sorted_rows, 1):
        best = r.get("best_kills")
        avg = r.get("avg_kills")
        best_s = f"**{best} K**" if best is not None else "—"
        avg_s = f"**{avg}**" if avg is not None else "—"
        lines.append(
            f"{i}. {r['nickname']} — best {best_s} · avg {avg_s} · "
            f"**{r['clean_games']}/{r['games_in_window']}** clean ({r['clean_rate']}%) · "
            f"**{r['glory_title']}**"
        )
    if not sorted_rows:
        lines.append("_No tracked players._")
    return "\n".join(lines)


def format_weekly_digest_message(rolling_list: list[dict]) -> str:
    if not rolling_list:
        return "**Weekly Wall of Shame digest** — no data yet."

    sorted_rows = sorted(
        rolling_list,
        key=lambda r: (r["shame_count"], r["shame_rate"]),
        reverse=True,
    )
    top = sorted_rows[0]
    clean = min(rolling_list, key=lambda r: (r["shame_count"], r["shame_rate"]))
    worst_game = min(
        (r for r in rolling_list if r["worst_kills"] is not None),
        key=lambda r: r["worst_kills"],
        default=None,
    )
    longest = max(rolling_list, key=lambda r: r["longest_shame_streak"])

    lines = [
        "**Weekly Wall of Shame digest** (all CS2 matches on record)",
        "",
        format_shame_leaderboard_message(rolling_list, scope="all"),
        "",
        f"Most shames: **{top['nickname']}** ({top['shame_count']})",
        f"Cleanest: **{clean['nickname']}** ({clean['shame_count']} shames)",
        f"Longest shame streak: **{longest['nickname']}** ({longest['longest_shame_streak']} games)",
    ]
    if worst_game:
        lines.append(
            f"Worst single game: **{worst_game['nickname']}** ({worst_game['worst_kills']} K)"
        )
    return "\n".join(lines)


async def backfill_all_match_outcomes() -> None:
    """Cache stats for all fetchable CS2 history; mark old matches processed without posting."""
    global stats_backfill_in_progress, stats_backfill_completed_at

    if not STATS_BACKFILL_ON_STARTUP:
        log.info("STATS_BACKFILL_ON_STARTUP=false; skipping full history stats backfill.")
        return

    stats_backfill_in_progress = True
    post_window_ids = get_post_window_match_id_set()
    finished_map: dict[str, int] = {}

    for player_id in PLAYER_IDS:
        for match in get_all_cs2_matches(player_id):
            match_id = match.get("match_id")
            if match_id:
                finished_map[match_id] = int(match.get("finished_at") or 0)

    all_ids = sorted(finished_map.keys(), key=lambda m: finished_map[m])
    total = len(all_ids)
    log.info(
        "Full stats backfill: %d unique match(es) across tracked players...",
        total,
    )

    cached = 0
    marked = 0
    for idx, match_id in enumerate(all_ids, 1):
        if match_id not in match_outcomes:
            stats = get_match_stats(match_id)
            if stats:
                record_match_outcomes(
                    match_id,
                    stats,
                    finished_at=finished_map.get(match_id),
                )
                cached += 1

        if match_id not in post_window_ids and match_id not in seen_matches:
            mark_match_processed(match_id, persist=False)
            marked += 1

        if idx % 50 == 0 or idx == total:
            log.info("Stats backfill progress: %d/%d", idx, total)
        await asyncio.sleep(STATS_BACKFILL_SLEEP_SECONDS)

    save_state()
    stats_backfill_in_progress = False
    stats_backfill_completed_at = datetime.now(timezone.utc).isoformat()
    save_state()
    log.info(
        "Full stats backfill done: %d new cached, %d marked processed (no post), "
        "%d total outcomes.",
        cached,
        marked,
        len(match_outcomes),
    )


async def backfill_post_window_match_outcomes() -> None:
    """Cache stats for post-window matches when full history backfill is off."""
    finished_map = collect_post_window_finished_at()
    match_ids = list(finished_map.keys())
    missing = [mid for mid in match_ids if mid not in match_outcomes]
    if not missing:
        return
    log.info(
        "Post-window outcome backfill: fetching %d/%d match(es)...",
        len(missing),
        len(match_ids),
    )
    for match_id in missing:
        stats = get_match_stats(match_id)
        if stats:
            record_match_outcomes(
                match_id,
                stats,
                finished_at=finished_map.get(match_id),
            )
        await asyncio.sleep(STATS_BACKFILL_SLEEP_SECONDS)


def collect_post_window_match_ids() -> list[str]:
    """Post-window match IDs, oldest finished first."""
    pending: dict[str, int] = {}
    for player_id in PLAYER_IDS:
        for match in get_post_window_matches(player_id):
            match_id = match.get("match_id")
            if match_id:
                pending[match_id] = int(match.get("finished_at") or 0)
    return [match_id for match_id, _ in sorted(pending.items(), key=lambda x: x[1])]


def get_unseen_match_ids() -> list[str]:
    """Post-window match IDs not yet processed, oldest finished first."""
    return [
        match_id
        for match_id in collect_post_window_match_ids()
        if match_id not in seen_matches
    ]


def get_match_stats(match_id: str) -> dict | None:
    return faceit_get(f"/matches/{match_id}/stats")


def find_shamed_tracked(stats: dict) -> dict[str, int]:
    """Return {nickname: kills} for tracked players under the kill threshold."""
    shamed: dict[str, int] = {}
    rounds = stats.get("rounds") or []
    if not rounds:
        return shamed

    for team in rounds[0].get("teams", []):
        for player in team.get("players", []):
            nick = player.get("nickname")
            if nick not in tracked_nicknames:
                continue
            try:
                kills = int(player.get("player_stats", {}).get("Kills", KILL_THRESHOLD))
            except (TypeError, ValueError):
                kills = KILL_THRESHOLD
            if kills < KILL_THRESHOLD:
                shamed[nick] = kills
    return shamed


def _draw_table_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    nick: str,
    stats: dict,
    *,
    color: tuple[int, int, int],
) -> None:
    draw.text((COL_PLAYER, y), _truncate_nick(nick), fill=color, font=font)
    draw.text((COL_K, y), _stat_value(stats, "Kills"), fill=color, font=font)
    draw.text((COL_D, y), _stat_value(stats, "Deaths"), fill=color, font=font)
    draw.text((COL_A, y), _stat_value(stats, "Assists"), fill=color, font=font)
    draw.text((COL_KD, y), _stat_value(stats, "K/D Ratio"), fill=color, font=font)
    hs = _stat_value(stats, "Headshots %", "")
    draw.text((COL_HS, y), f"{hs}%" if hs not in ("", "-") else "-", fill=color, font=font)
    draw.text((COL_MVP, y), _stat_value(stats, "MVPs"), fill=color, font=font)
    adr = stats.get("ADR") or stats.get("Average Damage per Round")
    draw.text(
        (COL_ADR, y),
        str(adr) if adr is not None and adr != "" else "-",
        fill=color,
        font=font,
    )


def build_scoreboard_image(
    match_stats: dict,
    shame_players: dict[str, int],
    rolling: dict[str, dict] | None = None,
) -> io.BytesIO:
    rounds = match_stats.get("rounds") or []
    round_data = rounds[0] if rounds else {}
    teams = round_data.get("teams", [])

    row_count = sum(len(t.get("players", [])) for t in teams)
    header_lines = 3 if format_match_header(round_data) else 2
    rolling_lines = 0
    if rolling and shame_players:
        rolling_lines = sum(1 for nick in shame_players if nick in rolling)
    height = max(
        420,
        50
        + header_lines * 26
        + 36  # column headers
        + row_count * 22
        + len(teams) * 32
        + 50
        + rolling_lines * 22,
    )

    img = Image.new("RGB", (IMG_WIDTH, height), color=(24, 24, 28))
    draw = ImageDraw.Draw(img)
    title_font = _load_mono_font(20)
    font = _load_mono_font(15)
    header_font = _load_mono_font(14)
    muted = (140, 140, 150)
    text = (210, 210, 215)
    shame_color = (255, 90, 90)
    accent = (255, 160, 60)

    y = 18
    draw.text((COL_PLAYER, y), BOT_STATS_TITLE, fill=shame_color, font=title_font)
    y += 32

    score_line = format_match_header(round_data)
    if score_line:
        draw.text((COL_PLAYER, y), score_line, fill=(120, 200, 255), font=header_font)
        y += 26

    draw.line([(COL_PLAYER, y), (IMG_WIDTH - 24, y)], fill=(60, 60, 70), width=1)
    y += 12

    draw.text((COL_PLAYER, y), "Player", fill=muted, font=header_font)
    draw.text((COL_K, y), "K", fill=muted, font=header_font)
    draw.text((COL_D, y), "D", fill=muted, font=header_font)
    draw.text((COL_A, y), "A", fill=muted, font=header_font)
    draw.text((COL_KD, y), "K/D", fill=muted, font=header_font)
    draw.text((COL_HS, y), "HS%", fill=muted, font=header_font)
    draw.text((COL_MVP, y), "MVP", fill=muted, font=header_font)
    draw.text((COL_ADR, y), "ADR", fill=muted, font=header_font)
    y += 24

    for team in teams:
        ts = team.get("team_stats") or {}
        team_name = ts.get("Team", "Team")
        team_score = ts.get("Final Score", "?")
        draw.text(
            (COL_PLAYER, y),
            f"[{team_name}]  {team_score} rounds",
            fill=(180, 180, 190),
            font=header_font,
        )
        y += 26

        for player in team.get("players", []):
            nick = player["nickname"]
            stats = player.get("player_stats") or {}
            row_color = shame_color if nick in shame_players else text
            _draw_table_row(draw, y, font, nick, stats, color=row_color)
            y += 22
        y += 6

    footer_parts = [f"{nick} ({k} K)" for nick, k in shame_players.items()]
    footer = f"Shamed: {', '.join(footer_parts)} — under {KILL_THRESHOLD} kills"
    draw.text((COL_PLAYER, y + 8), footer, fill=accent, font=header_font)
    y += 26

    if rolling:
        for nick in shame_players:
            r = rolling.get(nick)
            if not r:
                continue
            line = (
                f"{nick}: {r['shame_count']}/{r['games_in_window']} on the wall "
                f"({r['shame_rate']}%)"
            )
            if r["worst_kills"] is not None:
                line += f" · worst {r['worst_kills']} K"
            if r["current_shame_streak"] > 0:
                line += f" · streak {r['current_shame_streak']}"
            draw.text((COL_PLAYER, y), line, fill=(200, 140, 255), font=header_font)
            y += 22

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def load_faceit_discord_mentions() -> None:
    global nick_to_discord_id
    nick_to_discord_id = {}
    raw = os.getenv("FACEIT_DISCORD_MENTIONS", "").strip()
    if not raw:
        return
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        nick, _, discord_id = part.partition(":")
        nick = nick.strip()
        discord_id = discord_id.strip()
        if not nick or not discord_id.isdigit():
            log.warning("Skipping invalid FACEIT_DISCORD_MENTIONS entry: %r", part)
            continue
        nick_to_discord_id[nick] = int(discord_id)
        log.info("Discord mention map: %s -> %s", nick, discord_id)


def format_shame_mentions(shame_players: dict[str, int]) -> str:
    if not MENTION_SHAMED_ON_POST or not nick_to_discord_id:
        return ""
    parts = []
    for nick in shame_players:
        uid = nick_to_discord_id.get(nick)
        if uid:
            parts.append(f"<@{uid}>")
    return " ".join(parts) + " " if parts else ""


def format_shame_message(
    shame_players: dict[str, int],
    rolling: dict[str, dict] | None = None,
) -> str:
    lines = [f"**{BOT_STATS_TITLE}**"]
    for nick, kills in shame_players.items():
        lines.append(f"{nick} — **{kills} kills** last game (below {KILL_THRESHOLD})")
        if rolling and nick in rolling:
            lines.append(format_rolling_line(rolling[nick]))
    return "\n".join(lines)


def resolve_player_ids() -> None:
    """Populate PLAYER_IDS from FACEIT_PLAYER_IDS entries (UUID, Steam ID, or nickname)."""
    global PLAYER_IDS
    PLAYER_IDS = []
    for entry in PLAYER_ENTRIES:
        profile = resolve_player_entry(entry)
        if not profile:
            log.error("Could not resolve FaceIT player for entry=%r", entry)
            continue
        player_id = profile.get("player_id")
        nick = profile.get("nickname")
        if not player_id:
            log.error("FaceIT profile for %r has no player_id", entry)
            continue
        PLAYER_IDS.append(player_id)
        if nick:
            log.info("Resolved %r -> %s (%s)", entry, nick, player_id)
        else:
            log.info("Resolved %r -> %s", entry, player_id)

    if not PLAYER_IDS:
        log.error(
            "No FaceIT players resolved. FACEIT_PLAYER_IDS accepts FaceIT UUIDs, "
            "Steam IDs (17 digits), or exact FaceIT nicknames — not Discord IDs."
        )


def resolve_tracked_nicknames() -> None:
    global tracked_nicknames, nick_to_player_id, player_id_to_nick
    tracked_nicknames = set()
    nick_to_player_id = {}
    player_id_to_nick = {}
    for player_id in PLAYER_IDS:
        nick = get_player_nickname(player_id)
        if nick:
            tracked_nicknames.add(nick)
            nick_to_player_id[nick] = player_id
            player_id_to_nick[player_id] = nick
            log.info("Tracking %s (%s)", nick, player_id)
        else:
            log.error("Could not resolve nickname for player_id=%s", player_id)

    if not tracked_nicknames:
        log.error("No tracked nicknames resolved; shame posts will not trigger.")


async def post_shame_for_match(
    channel: discord.abc.Messageable,
    match_id: str,
    stats: dict,
    shame_players: dict[str, int],
) -> None:
    rolling = rolling_by_nickname()
    img_buf = build_scoreboard_image(stats, shame_players, rolling=rolling)
    content = format_shame_mentions(shame_players) + format_shame_message(
        shame_players, rolling=rolling
    )
    await channel.send(
        content,
        file=discord.File(fp=img_buf, filename="shame.png"),
    )
    log.info("Posted shame for match %s: %s", match_id, list(shame_players))


async def process_match(
    channel: discord.abc.Messageable,
    match_id: str,
    *,
    allow_post: bool,
) -> bool:
    """
    Fetch stats and optionally post shame. Always marks match processed on success.
    Returns True if a shame post was sent.
    """
    if match_id in seen_matches:
        return False

    stats = get_match_stats(match_id)
    if not stats:
        return False

    finished_map = collect_post_window_finished_at()
    record_match_outcomes(
        match_id,
        stats,
        finished_at=finished_map.get(match_id),
    )

    shame_players = find_shamed_tracked(stats)
    posted = False
    if shame_players and allow_post:
        await post_shame_for_match(channel, match_id, stats, shame_players)
        posted = True

    mark_match_processed(match_id)
    return posted


async def scan_history_on_startup(channel: discord.abc.Messageable) -> None:
    """Post retroactive shame for unprocessed games in post-window history."""
    match_ids = collect_post_window_match_ids()
    unprocessed = [m for m in match_ids if m not in seen_matches]

    if not POST_HISTORY_ON_STARTUP:
        finished_map = collect_post_window_finished_at()
        for match_id in unprocessed:
            stats = get_match_stats(match_id)
            if stats:
                record_match_outcomes(
                    match_id,
                    stats,
                    finished_at=finished_map.get(match_id),
                )
            mark_match_processed(match_id)
            await asyncio.sleep(1)
        log.info(
            "POST_HISTORY_ON_STARTUP=false: marked %d match(es) seen without posting.",
            len(unprocessed),
        )
        return

    log.info(
        "Retroactive scan: checking last %d games (%d not yet processed)...",
        len(match_ids),
        len(unprocessed),
    )
    posted = 0
    for match_id in unprocessed:
        if await process_match(channel, match_id, allow_post=True):
            posted += 1
        await asyncio.sleep(1)  # avoid Discord / FaceIT burst limits

    log.info(
        "Retroactive scan done: %d shame post(s), %d match(es) marked processed.",
        posted,
        len(unprocessed),
    )


async def get_shame_channel() -> discord.abc.Messageable | None:
    channel = client.get_channel(SHAME_CHANNEL_ID)
    if channel is not None:
        return channel

    try:
        channel = await client.fetch_channel(SHAME_CHANNEL_ID)
        log.info("Fetched shame channel: #%s", getattr(channel, "name", SHAME_CHANNEL_ID))
        return channel
    except discord.NotFound:
        log.error(
            "Channel %s does not exist or the bot cannot access it.",
            SHAME_CHANNEL_ID,
        )
    except discord.Forbidden:
        log.error(
            "Bot lacks permission to access channel %s.",
            SHAME_CHANNEL_ID,
        )
    except discord.HTTPException as exc:
        log.error("Failed to fetch channel %s: %s", SHAME_CHANNEL_ID, exc)

    guild_names = [g.name for g in client.guilds]
    log.error(
        "Bot is in %d server(s): %s. Invite the bot to the server that owns "
        "SHAME_CHANNEL_ID, or fix the channel ID (Developer Mode -> Copy Channel ID).",
        len(guild_names),
        ", ".join(guild_names) if guild_names else "(none — invite the bot first)",
    )
    return None


def digest_is_due() -> bool:
    if not WEEKLY_DIGEST_ENABLED:
        return False
    now = datetime.now(timezone.utc)
    if now.weekday() != WEEKLY_DIGEST_CRON_DAY or now.hour != WEEKLY_DIGEST_HOUR_UTC:
        return False
    if last_digest_at:
        try:
            last = datetime.fromisoformat(last_digest_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < 6 * 24 * 3600:
                return False
        except ValueError:
            pass
    return True


async def maybe_post_weekly_digest(channel: discord.abc.Messageable) -> None:
    global last_digest_at
    if not digest_is_due():
        return
    rolling = compute_all_tracked_shame_alltime()
    if not any(r["games_in_window"] for r in rolling):
        return
    await channel.send(format_weekly_digest_message(rolling))
    last_digest_at = datetime.now(timezone.utc).isoformat()
    save_state()
    log.info("Posted weekly shame digest.")


def _stats_command_allowed(interaction: discord.Interaction) -> bool:
    if (
        not STATS_COMMAND_ANY_CHANNEL
        and SHAME_CHANNEL_ID
        and interaction.channel_id != SHAME_CHANNEL_ID
    ):
        return False
    return True


async def _reply_stats_blocked(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Use this command in the shame channel.",
        ephemeral=True,
    )


async def _reply_backfill_in_progress(interaction: discord.Interaction) -> bool:
    if stats_backfill_in_progress:
        await interaction.response.send_message(
            "Stats backfill is still running. Try again in a few minutes.",
            ephemeral=True,
        )
        return True
    return False


@tree.command(
    name="shamestats_recent",
    description=f"Shame leaderboard for tracked players (last {POST_HISTORY_LIMIT} games)",
)
async def shamestats_recent_command(interaction: discord.Interaction) -> None:
    if not _stats_command_allowed(interaction):
        await _reply_stats_blocked(interaction)
        return
    if stats_backfill_in_progress:
        await interaction.response.send_message(
            "Stats backfill is still running. Try again in a few minutes.",
            ephemeral=True,
        )
        return
    await interaction.response.defer()
    rows = compute_all_tracked_shame_recent()
    await interaction.followup.send(
        format_shame_leaderboard_message(rows, scope="recent")
    )


@tree.command(
    name="shamestats_all",
    description="Shame leaderboard for tracked players (all cached CS2 matches)",
)
async def shamestats_all_command(interaction: discord.Interaction) -> None:
    if not _stats_command_allowed(interaction):
        await _reply_stats_blocked(interaction)
        return
    if stats_backfill_in_progress:
        await interaction.response.send_message(
            "Stats backfill is still running. Try again in a few minutes.",
            ephemeral=True,
        )
        return
    await interaction.response.defer()
    rows = compute_all_tracked_shame_alltime()
    await interaction.followup.send(
        format_shame_leaderboard_message(rows, scope="all")
    )


@tree.command(
    name="glorystats",
    description="Glory leaderboard for tracked players (all cached CS2 matches)",
)
async def glorystats_command(interaction: discord.Interaction) -> None:
    if not _stats_command_allowed(interaction):
        await _reply_stats_blocked(interaction)
        return
    if stats_backfill_in_progress:
        await interaction.response.send_message(
            "Stats backfill is still running. Try again in a few minutes.",
            ephemeral=True,
        )
        return
    await interaction.response.defer()
    rows = compute_all_tracked_glory_alltime()
    await interaction.followup.send(format_glory_leaderboard_message(rows))


async def run_bot_loop() -> None:
    await client.wait_until_ready()
    load_state()

    channel = await get_shame_channel()
    if channel is None:
        return

    await backfill_all_match_outcomes()
    if not STATS_BACKFILL_ON_STARTUP:
        await backfill_post_window_match_outcomes()
    await scan_history_on_startup(channel)

    while not client.is_closed():
        for match_id in get_unseen_match_ids():
            await process_match(channel, match_id, allow_post=True)
            await asyncio.sleep(1)

        await maybe_post_weekly_digest(channel)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def validate_config() -> None:
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not FACEIT_API_KEY:
        missing.append("FACEIT_API_KEY")
    if not SHAME_CHANNEL_ID:
        missing.append("SHAME_CHANNEL_ID")
    if not PLAYER_ENTRIES:
        missing.append("FACEIT_PLAYER_IDS")
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)


@client.event
async def on_ready() -> None:
    log.info("Logged in as %s", client.user)
    resolve_player_ids()
    resolve_tracked_nicknames()
    load_faceit_discord_mentions()
    try:
        synced = await tree.sync()
        log.info("Synced %d slash command(s).", len(synced))
    except discord.HTTPException as exc:
        log.warning(
            "Slash command sync failed (shame posts still work): %s. "
            "Re-invite the bot with applications.commands scope if needed.",
            exc,
        )
    client.loop.create_task(run_bot_loop())


if __name__ == "__main__":
    validate_config()
    client.run(DISCORD_TOKEN)
