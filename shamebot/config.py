"""Environment configuration. Every variable from the original bot.py keeps its name and default."""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def _int(name: str, default: str) -> int:
    return int(os.getenv(name) or default)


def _list(name: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, "").split(",") if p.strip()]


def _mentions(raw: str) -> dict[str, int]:
    """Parse ``Nick:discord_id,Nick2:discord_id`` into {nick: id}."""
    result: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        nick, _, discord_id = part.partition(":")
        nick, discord_id = nick.strip(), discord_id.strip()
        if not nick or not discord_id.isdigit():
            log.warning("Skipping invalid FACEIT_DISCORD_MENTIONS entry: %r", part)
            continue
        result[nick] = int(discord_id)
    return result


@dataclass
class Settings:
    # --- required -----------------------------------------------------------
    discord_token: str = os.getenv("DISCORD_TOKEN", "")
    faceit_api_key: str = os.getenv("FACEIT_API_KEY", "")
    shame_channel_id: int = _int("SHAME_CHANNEL_ID", "0")
    # Optional extra channels; 0 falls back to the shame channel
    fame_channel_id: int = _int("FAME_CHANNEL_ID", "0")  # Wall of Fame + Redemption Arc
    postmortem_channel_id: int = _int("POSTMORTEM_CHANNEL_ID", "0")  # automatic post-mortems
    player_entries: list[str] = field(default_factory=lambda: _list("FACEIT_PLAYER_IDS"))

    # --- detection ----------------------------------------------------------
    kill_threshold: int = _int("KILL_THRESHOLD", "10")
    redemption_kills: int = _int("REDEMPTION_KILLS", "25")
    glory_posts_enabled: bool = _bool("GLORY_POSTS_ENABLED", "true")
    glory_kills: int = _int("GLORY_KILLS", "30")
    # Wall of Fame carry rule: top-fragger of the lobby + best on the team + K/D or ADR floor, from this many kills
    fame_carry_kills: int = _int("FAME_CARRY_KILLS", "20")
    fame_carry_kd: float = float(os.getenv("FAME_CARRY_KD") or "1.5")
    fame_carry_adr: float = float(os.getenv("FAME_CARRY_ADR") or "90")
    fame_carry_share: int = _int("FAME_CARRY_SHARE", "28")
    # "Liability" posts: >= KILL_THRESHOLD kills but the team's worst player by a wide margin on a loss / close win
    liability_posts_enabled: bool = _bool("LIABILITY_POSTS_ENABLED", "true")
    liability_blame_share: int = _int("LIABILITY_BLAME_SHARE", "35")
    liability_close_win_margin: int = _int("LIABILITY_CLOSE_WIN_MARGIN", "3")
    liability_max_kd: float = float(os.getenv("LIABILITY_MAX_KD") or "0.9")
    # Grey zone: KILL_THRESHOLD..KILL_THRESHOLD+SHAME_GREY_ZONE-1 kills still shame when K/D, ADR or blame say so
    shame_grey_zone: int = _int("SHAME_GREY_ZONE", "3")
    shame_grey_kd: float = float(os.getenv("SHAME_GREY_KD") or "0.65")
    shame_grey_adr: float = float(os.getenv("SHAME_GREY_ADR") or "55")
    shame_grey_blame_share: int = _int("SHAME_GREY_BLAME_SHARE", "35")
    shame_grey_min_signals: int = _int("SHAME_GREY_MIN_SIGNALS", "2")
    shame_grey_kd_hard: float = float(os.getenv("SHAME_GREY_KD_HARD") or "0.5")
    shame_grey_adr_hard: float = float(os.getenv("SHAME_GREY_ADR_HARD") or "45")
    shame_grey_retroactive: bool = _bool("SHAME_GREY_RETROACTIVE", "false")
    # Post liabilities for matches that finished before the feature was first enabled (dry runs / tests)
    liability_retroactive: bool = _bool("LIABILITY_RETROACTIVE", "false")

    # --- polling / history --------------------------------------------------
    poll_interval_seconds: int = _int("POLL_INTERVAL_SECONDS", "300")
    post_history_limit: int = int(
        os.getenv("POST_HISTORY_LIMIT") or os.getenv("HISTORY_LIMIT") or "30"
    )
    post_history_on_startup: bool = _bool("POST_HISTORY_ON_STARTUP", "true")
    stats_backfill_on_startup: bool = _bool("STATS_BACKFILL_ON_STARTUP", "true")
    stats_backfill_sleep_seconds: float = float(os.getenv("STATS_BACKFILL_SLEEP_SECONDS") or "1")
    state_file: str = os.getenv("STATE_FILE", "state.json")

    # --- presentation -------------------------------------------------------
    bot_stats_title: str = os.getenv("BOT_STATS_TITLE", "BRWNr Bot Stats:")
    shame_reactions: list[str] = field(default_factory=lambda: _list("SHAME_REACTIONS") or ["🤡"])
    roast_level: str = os.getenv("ROAST_LEVEL", "brutal").strip().lower()
    custom_roasts_file: str = os.getenv("CUSTOM_ROASTS_FILE", "roasts_custom.txt")

    # --- mentions / permissions --------------------------------------------
    mentions: dict[str, int] = field(
        default_factory=lambda: _mentions(os.getenv("FACEIT_DISCORD_MENTIONS", ""))
    )
    mention_shamed_on_post: bool = _bool("MENTION_SHAMED_ON_POST", "true")
    stats_command_any_channel: bool = _bool("STATS_COMMAND_ANY_CHANNEL", "false")
    admin_discord_ids: set[int] = field(
        default_factory=lambda: {int(x) for x in _list("ADMIN_DISCORD_IDS") if x.isdigit()}
    )

    # --- ELO ----------------------------------------------------------------
    elo_tracking_enabled: bool = _bool("ELO_TRACKING_ENABLED", "true")

    # --- Leetify (external analytics, optional) -----------------------------
    leetify_enabled: bool = _bool("LEETIFY_ENABLED", "true")
    leetify_api_key: str = os.getenv("LEETIFY_API_KEY", "").strip()

    # --- automatic post-mortem ---------------------------------------------
    postmortem_auto: bool = _bool("POSTMORTEM_AUTO", "true")
    postmortem_min_tracked: int = _int("POSTMORTEM_MIN_TRACKED", "2")  # tracked players on the same team
    postmortem_leetify_wait_minutes: int = _int("POSTMORTEM_LEETIFY_WAIT_MINUTES", "120")

    # --- weekly digest ------------------------------------------------------
    weekly_digest_enabled: bool = _bool("WEEKLY_DIGEST_ENABLED", "false")
    weekly_digest_cron_day: int = _int("WEEKLY_DIGEST_CRON_DAY", "0")
    weekly_digest_hour_utc: int = _int("WEEKLY_DIGEST_HOUR_UTC", "18")

    def validate_or_exit(self) -> None:
        missing = []
        if not self.discord_token:
            missing.append("DISCORD_TOKEN")
        if not self.faceit_api_key:
            missing.append("FACEIT_API_KEY")
        if not self.shame_channel_id:
            missing.append("SHAME_CHANNEL_ID")
        if not self.player_entries:
            missing.append("FACEIT_PLAYER_IDS")
        if missing:
            log.error("Missing required env vars: %s", ", ".join(missing))
            sys.exit(1)

    @property
    def command_channel_ids(self) -> set[int]:
        """Channels where slash commands are accepted (all configured post channels)."""
        return {c for c in (self.shame_channel_id, self.fame_channel_id, self.postmortem_channel_id) if c}

    @property
    def discord_id_to_nick(self) -> dict[int, str]:
        return {uid: nick for nick, uid in self.mentions.items()}


settings = Settings()
