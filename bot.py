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
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "30"))
BOT_STATS_TITLE = os.getenv("BOT_STATS_TITLE", "BRWNr Bot Stats:")
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
tracked_nicknames: set[str] = set()
nick_to_player_id: dict[str, str] = {}
player_id_to_nick: dict[str, str] = {}

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


def get_recent_matches(player_id: str, limit: int | None = None) -> list[dict]:
    """Return up to HISTORY_LIMIT recent CS2 matches (newest first)."""
    n = limit if limit is not None else HISTORY_LIMIT
    data = faceit_get(
        f"/players/{player_id}/history",
        {"game": "cs2", "limit": n},
    )
    if not data:
        return []
    return data.get("items") or []


def load_state() -> None:
    global seen_matches, match_outcomes, last_digest_at
    if not os.path.isfile(STATE_FILE):
        seen_matches = set()
        match_outcomes = {}
        last_digest_at = None
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        seen_matches = set(data.get("processed_matches", []))
        match_outcomes = data.get("match_outcomes") or {}
        last_digest_at = data.get("last_digest_at")
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


def save_state() -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "processed_matches": sorted(seen_matches),
                    "match_outcomes": match_outcomes,
                    "last_digest_at": last_digest_at,
                },
                f,
                indent=2,
            )
    except OSError as exc:
        log.error("Could not save %s: %s", STATE_FILE, exc)


def mark_match_processed(match_id: str) -> None:
    seen_matches.add(match_id)
    save_state()


def collect_history_finished_at() -> dict[str, int]:
    """Map match_id -> finished_at from recent player histories."""
    finished: dict[str, int] = {}
    for player_id in PLAYER_IDS:
        for match in get_recent_matches(player_id):
            match_id = match.get("match_id")
            if match_id:
                finished[match_id] = int(match.get("finished_at") or 0)
    return finished


def get_active_window_match_ids() -> set[str]:
    """Union of recent history match IDs across all tracked players."""
    window: set[str] = set()
    for player_id in PLAYER_IDS:
        for match in get_recent_matches(player_id):
            match_id = match.get("match_id")
            if match_id:
                window.add(match_id)
    return window


def prune_match_outcomes() -> None:
    """Drop cached outcomes outside the rolling history window."""
    global match_outcomes
    window = get_active_window_match_ids()
    if not window:
        return
    before = len(match_outcomes)
    match_outcomes = {mid: data for mid, data in match_outcomes.items() if mid in window}
    if len(match_outcomes) < before:
        save_state()
        log.info("Pruned match_outcomes: %d -> %d", before, len(match_outcomes))


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


def get_player_window_match_ids(player_id: str) -> list[str]:
    """Recent match IDs for a player, newest first."""
    return [
        m["match_id"]
        for m in get_recent_matches(player_id)
        if m.get("match_id")
    ]


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


def compute_player_rolling(player_id: str) -> dict:
    """Aggregate shame stats over the last HISTORY_LIMIT games."""
    window_ids = get_player_window_match_ids(player_id)
    nickname = player_id_to_nick.get(player_id, player_id)

    shame_count = 0
    games_in_window = 0
    shame_kills: list[int] = []
    worst_k: int | None = None
    current_shame_streak = 0
    current_clean_streak = 0
    longest_shame_streak = 0
    run = 0

    for match_id in window_ids:
        mo = match_outcomes.get(match_id)
        if not mo:
            continue
        pdata = mo.get("players", {}).get(player_id)
        if not pdata:
            continue
        games_in_window += 1
        if pdata["shamed"]:
            shame_count += 1
            shame_kills.append(pdata["kills"])
            if worst_k is None or pdata["kills"] < worst_k:
                worst_k = pdata["kills"]

    for match_id in window_ids:
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

    for match_id in window_ids:
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

    for match_id in reversed(window_ids):
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

    return {
        "player_id": player_id,
        "nickname": nickname,
        "shame_count": shame_count,
        "games_in_window": games_in_window,
        "shame_rate": shame_rate,
        "current_shame_streak": current_shame_streak,
        "current_clean_streak": current_clean_streak,
        "longest_shame_streak": longest_shame_streak,
        "avg_kills_when_shamed": avg_kills_when_shamed,
        "worst_kills": worst_k,
        "title": get_shame_title(shame_count, current_shame_streak),
    }


def compute_all_tracked_rolling() -> list[dict]:
    return [compute_player_rolling(pid) for pid in PLAYER_IDS]


def rolling_by_nickname() -> dict[str, dict]:
    return {r["nickname"]: r for r in compute_all_tracked_rolling()}


def format_rolling_line(rolling: dict) -> str:
    sc = rolling["shame_count"]
    gw = rolling["games_in_window"]
    rate = rolling["shame_rate"]
    streak = rolling["current_shame_streak"]
    parts = [f"Wall record (last {HISTORY_LIMIT}): **{sc}/{gw}** games ({rate}%)"]
    if streak > 0:
        parts.append(f"streak **{streak}**")
    if rolling["worst_kills"] is not None:
        parts.append(f"worst **{rolling['worst_kills']} K**")
    parts.append(f"Title: **{rolling['title']}**")
    return " · ".join(parts)


def format_leaderboard_message(rolling_list: list[dict]) -> str:
    sorted_rows = sorted(
        rolling_list,
        key=lambda r: (r["shame_count"], r["shame_rate"]),
        reverse=True,
    )
    lines = [f"**{BOT_STATS_TITLE} Shame Leaderboard** (last {HISTORY_LIMIT} games)"]
    for i, r in enumerate(sorted_rows, 1):
        lines.append(
            f"{i}. {r['nickname']} — {r['shame_count']} shames / "
            f"{r['games_in_window']} games ({r['shame_rate']}%) · {r['title']}"
        )
    if not sorted_rows:
        lines.append("_No tracked players._")
    return "\n".join(lines)


def format_weekly_digest_message(rolling_list: list[dict]) -> str:
    if not rolling_list:
        return f"**Weekly Wall of Shame digest** — no data yet."

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
        f"**Weekly Wall of Shame digest** (last {HISTORY_LIMIT} games)",
        "",
        format_leaderboard_message(rolling_list),
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


async def backfill_match_outcomes() -> None:
    """Fetch and cache stats for window matches missing from match_outcomes."""
    window_ids = collect_history_match_ids()
    finished_map = collect_history_finished_at()
    missing = [mid for mid in window_ids if mid not in match_outcomes]
    if not missing:
        log.info("Outcome backfill: all %d window match(es) cached.", len(window_ids))
        prune_match_outcomes()
        return

    log.info(
        "Outcome backfill: fetching %d/%d window match(es)...",
        len(missing),
        len(window_ids),
    )
    cached = 0
    for match_id in missing:
        stats = get_match_stats(match_id)
        if stats:
            record_match_outcomes(
                match_id,
                stats,
                finished_at=finished_map.get(match_id),
            )
            cached += 1
        await asyncio.sleep(1)

    prune_match_outcomes()
    log.info(
        "Outcome backfill done: cached %d new, %d total in window.",
        cached,
        len([m for m in window_ids if m in match_outcomes]),
    )


def collect_history_match_ids() -> list[str]:
    """Recent history match IDs, oldest finished first."""
    pending: dict[str, int] = {}
    for player_id in PLAYER_IDS:
        for match in get_recent_matches(player_id):
            match_id = match.get("match_id")
            if match_id:
                pending[match_id] = int(match.get("finished_at") or 0)
    return [match_id for match_id, _ in sorted(pending.items(), key=lambda x: x[1])]


def get_unseen_match_ids() -> list[str]:
    """Match IDs from recent history not yet processed, oldest finished first."""
    return [
        match_id
        for match_id in collect_history_match_ids()
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
    await channel.send(
        format_shame_message(shame_players, rolling=rolling),
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

    finished_map = collect_history_finished_at()
    record_match_outcomes(
        match_id,
        stats,
        finished_at=finished_map.get(match_id),
    )
    prune_match_outcomes()

    shame_players = find_shamed_tracked(stats)
    posted = False
    if shame_players and allow_post:
        await post_shame_for_match(channel, match_id, stats, shame_players)
        posted = True

    mark_match_processed(match_id)
    return posted


async def scan_history_on_startup(channel: discord.abc.Messageable) -> None:
    """Post retroactive shame for unprocessed games in recent history."""
    match_ids = collect_history_match_ids()
    unprocessed = [m for m in match_ids if m not in seen_matches]

    if not POST_HISTORY_ON_STARTUP:
        finished_map = collect_history_finished_at()
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
        prune_match_outcomes()
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
    rolling = compute_all_tracked_rolling()
    if not any(r["games_in_window"] for r in rolling):
        return
    await channel.send(format_weekly_digest_message(rolling))
    last_digest_at = datetime.now(timezone.utc).isoformat()
    save_state()
    log.info("Posted weekly shame digest.")


@tree.command(
    name="shamestats",
    description=f"Shame leaderboard for tracked players (last {HISTORY_LIMIT} games)",
)
async def shamestats_command(interaction: discord.Interaction) -> None:
    if (
        not STATS_COMMAND_ANY_CHANNEL
        and SHAME_CHANNEL_ID
        and interaction.channel_id != SHAME_CHANNEL_ID
    ):
        await interaction.response.send_message(
            "Use this command in the shame channel.",
            ephemeral=True,
        )
        return

    rolling = compute_all_tracked_rolling()
    await interaction.response.send_message(format_leaderboard_message(rolling))


async def run_bot_loop() -> None:
    await client.wait_until_ready()
    load_state()

    channel = await get_shame_channel()
    if channel is None:
        return

    await scan_history_on_startup(channel)
    await backfill_match_outcomes()

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
