"""Detection rules: which post (if any) a finished match triggers, plus per-player award badges."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .blame import BlameReport, CarryReport, compute_blame, compute_carry
from .models import MatchRecord, TrackedResult


class PostKind(str, Enum):
    SHAME = "shame"
    REDEMPTION = "redemption"
    GLORY = "glory"
    LIABILITY = "liability"  # enough kills to dodge the wall, still the reason the team lost


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
    # --- moment awards (need extended per-match stats, schema v3) ---
    "clutch_donor": "Clutch donor",
    "entry_fodder": "Entry fodder",
    "nade_hoarder": "Nade hoarder",
    "blank_nades": "Blank nades",
    "flash_artist": "Flash artist",
    "bait_job": "Bait job",  # made the kill threshold, stats say otherwise (grey zone)
    # --- fame awards (glory posts) ---
    "top_of_lobby": "Top of the lobby",
    "hard_carry": "Hard carry",
    "wasted": "Wasted",
    "ace": "Ace",
    "clutch_king": "Clutch king",
    "entry_king": "Entry king",
    "utility_master": "Utility master",
    "headhunter": "Headhunter",
    "untouchable": "Untouchable",
    "mvp_machine": "MVP machine",
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
    "clutch_donor": "🎁",
    "entry_fodder": "🚪",
    "nade_hoarder": "🧳",
    "blank_nades": "💨",
    "flash_artist": "🔦",
    "bait_job": "🎣",
    "top_of_lobby": "👑",
    "hard_carry": "🚂",
    "wasted": "🥀",
    "ace": "🃏",
    "clutch_king": "🧊",
    "entry_king": "🚀",
    "utility_master": "🧪",
    "headhunter": "🎯",
    "untouchable": "🛡️",
    "mvp_machine": "⭐",
}

# Thresholds for the moment awards. Kept in one place so they are easy to tune.
CLUTCH_DONOR_MIN = 2  # lost every clutch, at least this many attempts
ENTRY_FODDER_MIN = 4  # at least this many opening duels ...
ENTRY_FODDER_MAX_RATE = 0.25  # ... and won at most this share of them
NADE_HOARDER_MIN_ROUNDS = 16  # a full-length game ...
NADE_HOARDER_MAX_NADES = 3  # ... with this many grenades (incl. flashes) thrown at most
BLANK_NADES_MIN = 6  # this many grenades thrown for zero utility damage
FLASH_ARTIST_MIN = 5  # this many flashbangs that blinded no enemy

# Fame awards
CLUTCH_KING_MIN = 2  # clutches won
ENTRY_KING_MIN = 4  # opening duels ...
ENTRY_KING_RATE = 0.7  # ... won at this rate
UTILITY_MASTER_DMG = 150  # utility damage in a game
HEADHUNTER_HS = 60  # HS% with a real kill count
UNTOUCHABLE_KD = 2.0
MVP_MACHINE_MIN = 5


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
    liability_enabled: bool = True
    liability_blame_share: int = 35  # % of the team's shortfall (blame.py) that makes a liability
    liability_close_win_margin: int = 3  # a win by at most this many rounds still counts as 'dragged'
    liability_max_kd: float = 0.9  # a near-even K/D is never a liability, whatever the teammates did (long OT games)
    # Grey zone: kills in [kill_threshold, kill_threshold + grey_zone) still land on the wall when the
    # underlying stats say the kills were scraped together (kills are the one number a late rush inflates).
    grey_zone: int = 3
    grey_kd: float = 0.65  # signal: K/D below this
    grey_adr: float = 55.0  # signal: ADR below this
    grey_blame_share: int = 35  # signal: worst on the team with this blame share, on a loss / close win
    grey_min_signals: int = 2  # how many of the three signals it takes ...
    grey_kd_hard: float = 0.5  # ... unless one of them is extreme: K/D under this ...
    grey_adr_hard: float = 45.0  # ... or ADR under this
    grey_since: int | None = None  # unix ts; grey-zone shames only for matches finished after this (0/None = always)
    # Wall of Fame carry rule (besides glory_kills / an ace): top-fragger of the lobby, best on the team,
    # and either a K/D or an ADR that says it wasn't just volume.
    fame_carry_kills: int = 20
    fame_carry_kd: float = 1.5
    fame_carry_adr: float = 90.0
    fame_carry_share: int = 28  # % of the team's impact (even split is 20)


@dataclass
class Detection:
    kind: PostKind
    match: MatchRecord
    player_ids: list[str] = field(default_factory=list)


def grey_zone_reasons(record: MatchRecord, pid: str, thresholds: Thresholds) -> list[str]:
    """Why a player with >= kill_threshold kills still belongs on the wall — empty when he doesn't.

    Only kills in [kill_threshold, kill_threshold + grey_zone) are examined. Deaths, ADR and the
    blame report are what a last-minute kill hunt damages rather than improves, so they decide:
    it takes ``grey_min_signals`` of the three, or a single extreme one (``grey_kd_hard`` /
    ``grey_adr_hard``). One bad number in an otherwise fine game is not a wall post.
    """
    r = record.players[pid]
    if thresholds.grey_zone <= 0 or not r.enriched:
        return []
    if not thresholds.kill_threshold <= r.kills < thresholds.kill_threshold + thresholds.grey_zone:
        return []
    if thresholds.grey_since and (record.finished_at or 0) < thresholds.grey_since:
        return []
    reasons: list[str] = []
    extreme = False
    if r.kd is not None and r.kd < thresholds.grey_kd:
        reasons.append(f"K/D {r.kd:.2f}")
        extreme |= r.kd < thresholds.grey_kd_hard
    if r.adr is not None and r.adr < thresholds.grey_adr:
        reasons.append(f"ADR {r.adr:.0f}")
        extreme |= r.adr < thresholds.grey_adr_hard
    b = compute_blame(record, pid)
    mattered = r.result == 0 or (
        r.team_score is not None and r.enemy_score is not None
        and r.team_score - r.enemy_score <= thresholds.liability_close_win_margin
    )
    if b is not None and mattered and b.rank == b.team_size and b.share >= thresholds.grey_blame_share:
        reasons.append(f"{b.share}% blame, worst on the team")
    if len(reasons) >= thresholds.grey_min_signals or extreme:
        return reasons
    return []


def is_liability(record: MatchRecord, pid: str, thresholds: Thresholds) -> BlameReport | None:
    """Blame report when ``pid`` was the team's worst player *and* it mattered — else None.

    Requires a K/D below ``liability_max_kd`` so a 23-kill overtime game never qualifies.
    Two shapes: the team lost and this row carries >= ``liability_blame_share`` of the gap to the
    team's best player; or the team won by <= ``liability_close_win_margin`` rounds (or in OT) with
    the same share. A team that got rolled evenly produces nobody: shares stay near 20%.
    """
    r = record.players[pid]
    if not thresholds.liability_enabled or r.kills < thresholds.kill_threshold or r.result is None:
        return None
    if r.kd is not None and r.kd >= thresholds.liability_max_kd:
        return None
    if grey_zone_reasons(record, pid, thresholds):
        return None  # that's a wall post, not a liability
    b = compute_blame(record, pid)
    if b is None or b.rank != b.team_size or b.share < thresholds.liability_blame_share:
        return None
    if r.result == 0:
        return b
    if r.team_score is not None and r.enemy_score is not None:
        margin = r.team_score - r.enemy_score
        if margin <= thresholds.liability_close_win_margin or r.enemy_score >= 13:  # 13+ conceded = overtime
            return b
    return None


def is_fame_carry(record: MatchRecord, pid: str, thresholds: Thresholds) -> CarryReport | None:
    """Carry report when the game was a genuine carry (not just a big kill count) — else None."""
    r = record.players[pid]
    if r.kills < thresholds.fame_carry_kills or not r.enriched:
        return None
    c = compute_carry(record, pid)
    if c is None or not c.top_of_lobby or c.rank != 1 or c.share < thresholds.fame_carry_share:
        return None
    if (r.kd or 0) >= thresholds.fame_carry_kd or (r.adr or 0) >= thresholds.fame_carry_adr:
        return c
    return None


def fame_awards(record: MatchRecord, pid: str) -> list[str]:
    """Badges for a glory-post player: what made the game exceptional."""
    r = record.players[pid]
    awards: list[str] = []
    c = compute_carry(record, pid)
    if (r.penta or 0) >= 1:
        awards.append("ace")
    if c is not None and c.top_of_lobby:
        awards.append("top_of_lobby")
    if c is not None and c.rank == 1 and c.heavy:
        awards.append("hard_carry" if c.won else "wasted")
    if r.kd is not None and r.kd >= UNTOUCHABLE_KD:
        awards.append("untouchable")
    if (r.mvps or 0) >= MVP_MACHINE_MIN:
        awards.append("mvp_machine")
    if r.hs_pct is not None and r.hs_pct >= HEADHUNTER_HS and r.kills >= 15:
        awards.append("headhunter")
    if r.extended:
        if (r.w1v1 or 0) + (r.w1v2 or 0) >= CLUTCH_KING_MIN:
            awards.append("clutch_king")
        entries = r.entry_count or 0
        if entries >= ENTRY_KING_MIN and (r.entry_wins or 0) >= entries * ENTRY_KING_RATE:
            awards.append("entry_king")
        if (r.utility_damage or 0) >= UTILITY_MASTER_DMG:
            awards.append("utility_master")
    return awards


def compute_awards(
    record: MatchRecord,
    pid: str,
    *,
    streak_before: int,
    shamed_count: int,
    kill_threshold: int = 10,
) -> list[str]:
    """Badges for a shamed tracked player in ``record``. ``streak_before`` excludes this match."""
    r: TrackedResult = record.players[pid]
    awards: list[str] = []
    if r.kills >= kill_threshold:
        awards.append("bait_job")  # grey zone: the kills were there, the game was not
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
    # moment awards go before the generic ones so they survive the card's pill-row cut-off
    awards.extend(moment_awards(r, rounds=record.rounds))
    if r.mvps == 0:
        awards.append("zero_mvp")
    if r.hs_pct is not None and r.hs_pct < 20 and r.kills >= 5:
        awards.append("headless")
    return awards


def moment_awards(r: TrackedResult, *, rounds: int | None) -> list[str]:
    """Badges derived from FaceIT's extended per-match stats (clutches, entries, utility).

    These are the API-only "what actually went wrong" markers: they don't know HP or weapons
    (that needs the demo), but they do know you lost every clutch or threw two nades all game.
    Empty for records that predate schema v3.
    """
    if not r.extended:
        return []
    out: list[str] = []
    clutches = (r.c1v1 or 0) + (r.c1v2 or 0)
    clutch_wins = (r.w1v1 or 0) + (r.w1v2 or 0)
    if clutches >= CLUTCH_DONOR_MIN and clutch_wins == 0:
        out.append("clutch_donor")
    entries = r.entry_count or 0
    if entries >= ENTRY_FODDER_MIN and (r.entry_wins or 0) <= entries * ENTRY_FODDER_MAX_RATE:
        out.append("entry_fodder")
    # FaceIT's "Utility Count" is damage utility (HE/molotov); flashes are counted separately
    damage_nades = r.utility_count or 0
    all_nades = damage_nades + (r.flash_count or 0)
    if rounds is not None and rounds >= NADE_HOARDER_MIN_ROUNDS and all_nades <= NADE_HOARDER_MAX_NADES:
        out.append("nade_hoarder")
    elif damage_nades >= BLANK_NADES_MIN and (r.utility_damage or 0) == 0:
        out.append("blank_nades")
    if (r.flash_count or 0) >= FLASH_ARTIST_MIN and (r.enemies_flashed or 0) == 0:
        out.append("flash_artist")
    return out


def detect(
    record: MatchRecord,
    *,
    streaks_before: dict[str, int],
    thresholds: Thresholds,
) -> list[Detection]:
    """Return the posts a match should trigger. Also fills ``awards`` on shamed players."""
    shamed = [
        pid for pid, r in record.players.items()
        if r.kills < thresholds.kill_threshold or grey_zone_reasons(record, pid, thresholds)
    ]
    redeemed: list[str] = []
    glorious: list[str] = []
    liable: list[str] = []
    for pid, r in record.players.items():
        if pid in shamed:
            continue
        streak = streaks_before.get(pid, 0)
        if streak >= 2 and r.kills >= thresholds.redemption_kills:
            redeemed.append(pid)
        elif thresholds.glory_enabled and (
            r.kills >= thresholds.glory_kills
            or ((r.penta or 0) >= 1 and r.kills >= thresholds.ace_min_kills)
            or is_fame_carry(record, pid, thresholds) is not None
        ):
            glorious.append(pid)
        elif is_liability(record, pid, thresholds) is not None:
            liable.append(pid)

    for pid, r in record.players.items():
        r.liability = pid in liable
        r.shamed = pid in shamed
        r.fame = pid in glorious
        if r.fame:
            r.awards = fame_awards(record, pid)
    for pid in shamed:
        record.players[pid].shamed = True
        record.players[pid].awards = compute_awards(
            record, pid, streak_before=streaks_before.get(pid, 0), shamed_count=len(shamed),
            kill_threshold=thresholds.kill_threshold,
        )

    out: list[Detection] = []
    if shamed:
        out.append(Detection(PostKind.SHAME, record, shamed))
    if redeemed:
        out.append(Detection(PostKind.REDEMPTION, record, redeemed))
    if glorious:
        out.append(Detection(PostKind.GLORY, record, glorious))
    if liable:
        out.append(Detection(PostKind.LIABILITY, record, liable))
    return out
