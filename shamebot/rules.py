"""Detection rules: which post (if any) a finished match triggers, plus per-player award badges."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .models import MatchRecord, TrackedResult


class PostKind(str, Enum):
    SHAME = "shame"
    REDEMPTION = "redemption"
    GLORY = "glory"


AWARD_LABELS: dict[str, str] = {
    "bottom_of_lobby": "Bottom of the lobby",
    "double_feature": "Double feature",
    "triple_threat": "Triple threat",
    "hat_trick": "Hat-trick",
    "streak": "Streak {n}",
    "carried": "Carried",
    "anchor": "Anchor",
    "spectator": "Spectator",
    "fed": "Fed",
    "zero_mvp": "Zero MVP club",
    "headless": "Headless",
}

AWARD_EMOJI: dict[str, str] = {
    "bottom_of_lobby": "🪦",
    "double_feature": "🎬",
    "triple_threat": "🎪",
    "hat_trick": "🎩",
    "streak": "🔥",
    "carried": "🎒",
    "anchor": "⚓",
    "spectator": "👀",
    "fed": "🍽️",
    "zero_mvp": "🫥",
    "headless": "🎯",
}


def award_label(key: str) -> str:
    if key.startswith("streak:"):
        return AWARD_LABELS["streak"].format(n=key.split(":", 1)[1])
    return AWARD_LABELS.get(key, key)


def award_emoji(key: str) -> str:
    return AWARD_EMOJI.get(key.split(":", 1)[0], "🏷️")


@dataclass
class Thresholds:
    kill_threshold: int = 10
    redemption_kills: int = 25
    glory_kills: int = 30
    glory_enabled: bool = True
    ace_min_kills: int = 15  # an ace in a 10-kill game is not a highlight


@dataclass
class Detection:
    kind: PostKind
    match: MatchRecord
    player_ids: list[str] = field(default_factory=list)


def compute_awards(
    record: MatchRecord,
    pid: str,
    *,
    streak_before: int,
    shamed_count: int,
) -> list[str]:
    """Badges for a shamed tracked player in ``record``. ``streak_before`` excludes this match."""
    r: TrackedResult = record.players[pid]
    awards: list[str] = []
    lowest = record.lowest_kills()
    if lowest is not None and r.kills <= lowest:
        awards.append("bottom_of_lobby")
    if shamed_count == 2:
        awards.append("double_feature")
    elif shamed_count >= 3:
        awards.append("triple_threat")
    streak_now = streak_before + 1
    if streak_now == 3:
        awards.append("hat_trick")
    elif streak_now > 3:
        awards.append(f"streak:{streak_now}")
    if r.result == 1:
        awards.append("carried")
    elif r.team_score is not None and r.enemy_score is not None and r.enemy_score - r.team_score >= 8:
        awards.append("anchor")
    if r.kills <= 3:
        awards.append("spectator")
    if (r.deaths or 0) >= 20:
        awards.append("fed")
    if r.mvps == 0:
        awards.append("zero_mvp")
    if r.hs_pct is not None and r.hs_pct < 20 and r.kills >= 5:
        awards.append("headless")
    return awards


def detect(
    record: MatchRecord,
    *,
    streaks_before: dict[str, int],
    thresholds: Thresholds,
) -> list[Detection]:
    """Return the posts a match should trigger. Also fills ``awards`` on shamed players."""
    shamed = [pid for pid, r in record.players.items() if r.kills < thresholds.kill_threshold]
    redeemed: list[str] = []
    glorious: list[str] = []
    for pid, r in record.players.items():
        if pid in shamed:
            continue
        streak = streaks_before.get(pid, 0)
        if streak >= 2 and r.kills >= thresholds.redemption_kills:
            redeemed.append(pid)
        elif thresholds.glory_enabled and (
            r.kills >= thresholds.glory_kills
            or ((r.penta or 0) >= 1 and r.kills >= thresholds.ace_min_kills)
        ):
            glorious.append(pid)

    for pid in shamed:
        record.players[pid].shamed = True
        record.players[pid].awards = compute_awards(
            record, pid, streak_before=streaks_before.get(pid, 0), shamed_count=len(shamed)
        )

    out: list[Detection] = []
    if shamed:
        out.append(Detection(PostKind.SHAME, record, shamed))
    if redeemed:
        out.append(Detection(PostKind.REDEMPTION, record, redeemed))
    if glorious:
        out.append(Detection(PostKind.GLORY, record, glorious))
    return out
