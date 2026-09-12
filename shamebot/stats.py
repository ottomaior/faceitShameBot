"""Aggregate statistics computed from cached MatchRecords (no API calls)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .models import MatchRecord, PlayerSnapshot


# Titles ported unchanged from the original bot.
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


@dataclass
class PlayerAggregate:
    player_id: str
    nickname: str
    games: int = 0
    shame_count: int = 0
    shame_rate: int = 0
    clean_games: int = 0
    clean_rate: int = 0
    current_shame_streak: int = 0
    current_clean_streak: int = 0
    longest_shame_streak: int = 0
    avg_kills: float | None = None
    avg_kills_when_shamed: float | None = None
    worst_kills: int | None = None
    best_kills: int | None = None
    worst_match_id: str | None = None
    best_match_id: str | None = None
    title: str = "Saint"
    glory_title: str = "Work in progress"
    # enriched-only (None when no v2 records exist)
    wins: int = 0
    losses: int = 0
    win_rate: int | None = None
    avg_deaths: float | None = None
    avg_adr: float | None = None
    avg_kd: float | None = None
    avg_hs: float | None = None
    avg_mvps: float | None = None
    aces: int = 0
    bottom_of_lobby: int = 0
    elo_sum: int = 0  # sum of known elo deltas in the window
    kills_std: float | None = None
    # extended-only (schema v3 records; None when the window has none)
    extended_games: int = 0
    rounds: int = 0
    kpr: float | None = None
    entry_success_pct: float | None = None
    entry_rate: float | None = None
    first_kills_pr: float | None = None
    clutch_pct: float | None = None
    clutch_attempts: int = 0
    util_dmg_pr: float | None = None
    flashed_pr: float | None = None
    flash_success_pct: float | None = None
    sniper_share_pct: float | None = None
    form: list[str] = field(default_factory=list)  # newest first: "S" shamed, "G" glory, "C" clean, "W"/"L" via form_wl
    form_wl: list[str] = field(default_factory=list)  # newest first: "W", "L", "?"

    @property
    def games_in_window(self) -> int:  # backwards-compatible name
        return self.games


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def aggregate(
    player_id: str,
    records: list[MatchRecord],
    *,
    nickname: str | None = None,
    glory_kills: int = 30,
) -> PlayerAggregate:
    """Aggregate a player's cached matches. ``records`` must be newest first."""
    agg = PlayerAggregate(player_id=player_id, nickname=nickname or player_id)
    results = [(m, m.players[player_id]) for m in records if player_id in m.players]
    if not results:
        return agg
    if nickname is None:
        agg.nickname = results[0][1].nickname

    kills_all: list[float] = []
    kills_shamed: list[float] = []
    deaths: list[float] = []
    adrs: list[float] = []
    kds: list[float] = []
    hss: list[float] = []
    mvps: list[float] = []
    enriched = 0
    ext = {"kills": 0, "rounds": 0, "entry_c": 0, "entry_w": 0, "fk": 0, "clutch_c": 0, "clutch_w": 0,
           "util": 0, "flashed": 0, "flash_c": 0, "flash_w": 0, "sniper": 0}

    for m, r in results:
        agg.games += 1
        kills_all.append(r.kills)
        if agg.best_kills is None or r.kills > agg.best_kills:
            agg.best_kills, agg.best_match_id = r.kills, m.match_id
        if r.shamed:
            agg.shame_count += 1
            kills_shamed.append(r.kills)
            if agg.worst_kills is None or r.kills < agg.worst_kills:
                agg.worst_kills, agg.worst_match_id = r.kills, m.match_id
        if r.enriched:
            enriched += 1
            deaths.append(r.deaths or 0)
            adrs.append(r.adr or 0.0)
            kds.append(r.kd or 0.0)
            hss.append(r.hs_pct or 0)
            mvps.append(r.mvps or 0)
            if r.result == 1:
                agg.wins += 1
            elif r.result == 0:
                agg.losses += 1
            if (r.penta or 0) > 0:
                agg.aces += r.penta or 0
            if "bottom_of_lobby" in r.awards:
                agg.bottom_of_lobby += 1
            if r.elo_delta is not None:
                agg.elo_sum += r.elo_delta
            if r.extended and (m.rounds or 0) > 0:
                agg.extended_games += 1
                ext["kills"] += r.kills
                ext["rounds"] += m.rounds or 0
                ext["entry_c"] += r.entry_count or 0
                ext["entry_w"] += r.entry_wins or 0
                ext["fk"] += r.first_kills or 0
                ext["clutch_c"] += (r.c1v1 or 0) + (r.c1v2 or 0)
                ext["clutch_w"] += (r.w1v1 or 0) + (r.w1v2 or 0)
                ext["util"] += r.utility_damage or 0
                ext["flashed"] += r.enemies_flashed or 0
                ext["flash_c"] += r.flash_count or 0
                ext["flash_w"] += r.flash_successes or 0
                ext["sniper"] += r.sniper_kills or 0
        if len(agg.form) < 10:
            agg.form.append("S" if r.shamed else ("G" if r.kills >= glory_kills else "C"))
            agg.form_wl.append("?" if r.result is None else ("W" if r.result == 1 else "L"))

    # streaks (records are newest first)
    for _, r in results:
        if r.shamed:
            agg.current_shame_streak += 1
        else:
            break
    for _, r in results:
        if not r.shamed:
            agg.current_clean_streak += 1
        else:
            break
    run = 0
    for _, r in reversed(results):
        if r.shamed:
            run += 1
            agg.longest_shame_streak = max(agg.longest_shame_streak, run)
        else:
            run = 0

    agg.shame_rate = round(100 * agg.shame_count / agg.games) if agg.games else 0
    agg.clean_games = agg.games - agg.shame_count
    agg.clean_rate = round(100 * agg.clean_games / agg.games) if agg.games else 0
    agg.avg_kills = _mean(kills_all)
    agg.avg_kills_when_shamed = _mean(kills_shamed)
    agg.title = get_shame_title(agg.shame_count, agg.current_shame_streak)
    agg.glory_title = get_glory_title(agg.clean_rate, agg.best_kills)

    if len(kills_all) >= 2:
        mean = sum(kills_all) / len(kills_all)
        agg.kills_std = round((sum((k - mean) ** 2 for k in kills_all) / len(kills_all)) ** 0.5, 1)
    if agg.extended_games:
        rounds = ext["rounds"]
        agg.rounds = rounds
        agg.kpr = round(ext["kills"] / rounds, 2)
        agg.entry_rate = round(ext["entry_c"] / rounds, 2)
        agg.entry_success_pct = round(100 * ext["entry_w"] / ext["entry_c"]) if ext["entry_c"] else None
        agg.first_kills_pr = round(ext["fk"] / rounds, 2)
        agg.clutch_attempts = ext["clutch_c"]
        agg.clutch_pct = round(100 * ext["clutch_w"] / ext["clutch_c"]) if ext["clutch_c"] else None
        agg.util_dmg_pr = round(ext["util"] / rounds, 1)
        agg.flashed_pr = round(ext["flashed"] / rounds, 2)
        agg.flash_success_pct = round(100 * ext["flash_w"] / ext["flash_c"]) if ext["flash_c"] else None
        agg.sniper_share_pct = round(100 * ext["sniper"] / ext["kills"]) if ext["kills"] else None
    if enriched:
        decided = agg.wins + agg.losses
        agg.win_rate = round(100 * agg.wins / decided) if decided else None
        agg.avg_deaths = _mean(deaths)
        agg.avg_adr = _mean(adrs)
        agg.avg_kd = round(sum(kds) / len(kds), 2)
        agg.avg_hs = _mean(hss)
        agg.avg_mvps = _mean(mvps)
    return agg


# --------------------------------------------------------------------- maps


@dataclass
class MapStat:
    map: str
    games: int = 0
    wins: int = 0
    losses: int = 0
    shames: int = 0
    kills: list[int] = field(default_factory=list)

    @property
    def win_rate(self) -> int | None:
        decided = self.wins + self.losses
        return round(100 * self.wins / decided) if decided else None

    @property
    def avg_kills(self) -> float | None:
        return _mean([float(k) for k in self.kills])

    @property
    def shame_rate(self) -> int:
        return round(100 * self.shames / self.games) if self.games else 0


def per_map(player_id: str, records: list[MatchRecord]) -> list[MapStat]:
    by_map: dict[str, MapStat] = {}
    for m in records:
        r = m.players.get(player_id)
        if not r or not m.map:
            continue
        stat = by_map.setdefault(m.map, MapStat(map=m.map))
        stat.games += 1
        stat.kills.append(r.kills)
        if r.shamed:
            stat.shames += 1
        if r.result == 1:
            stat.wins += 1
        elif r.result == 0:
            stat.losses += 1
    return sorted(by_map.values(), key=lambda s: s.games, reverse=True)


# ---------------------------------------------------------------------- ELO


def elo_change(snapshot: PlayerSnapshot | None, *, days: int) -> int | None:
    """ELO now minus the oldest snapshot within ``days`` (None if unknown)."""
    if not snapshot or snapshot.elo is None or not snapshot.elo_history:
        return None
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    window = [e for t, e in snapshot.elo_history if t >= cutoff]
    if not window:
        return None
    return snapshot.elo - window[0]


# -------------------------------------------------------------- hall of shame


@dataclass
class HallEntry:
    label: str
    nickname: str
    value: str
    match_id: str | None = None


def hall_of_shame(
    aggregates: list[PlayerAggregate],
    records_by_player: dict[str, list[MatchRecord]],
) -> list[HallEntry]:
    entries: list[HallEntry] = []
    with_games = [a for a in aggregates if a.games]
    if not with_games:
        return entries

    worst = min(with_games, key=lambda a: (a.worst_kills if a.worst_kills is not None else 999))
    if worst.worst_kills is not None:
        entries.append(HallEntry("Worst game ever", worst.nickname, f"{worst.worst_kills} kills", worst.worst_match_id))

    longest = max(with_games, key=lambda a: a.longest_shame_streak)
    if longest.longest_shame_streak:
        entries.append(HallEntry("Longest shame streak", longest.nickname, f"{longest.longest_shame_streak} games"))

    most = max(with_games, key=lambda a: (a.shame_count, a.shame_rate))
    entries.append(HallEntry("Most shames", most.nickname, f"{most.shame_count} ({most.shame_rate}%)"))

    bottom = max(with_games, key=lambda a: a.bottom_of_lobby)
    if bottom.bottom_of_lobby:
        entries.append(HallEntry("Bottom of the lobby (times)", bottom.nickname, str(bottom.bottom_of_lobby)))

    # biggest single ELO loss / gain
    biggest_loss: tuple[int, str, str] | None = None
    biggest_gain: tuple[int, str, str] | None = None
    for pid, records in records_by_player.items():
        for m in records:
            r = m.players.get(pid)
            if not r or r.elo_delta is None:
                continue
            if biggest_loss is None or r.elo_delta < biggest_loss[0]:
                biggest_loss = (r.elo_delta, r.nickname, m.match_id)
            if biggest_gain is None or r.elo_delta > biggest_gain[0]:
                biggest_gain = (r.elo_delta, r.nickname, m.match_id)
    if biggest_loss and biggest_loss[0] < 0:
        entries.append(HallEntry("Biggest ELO loss", biggest_loss[1], f"{biggest_loss[0]:+d}", biggest_loss[2]))
    if biggest_gain and biggest_gain[0] > 0:
        entries.append(HallEntry("Biggest ELO gain", biggest_gain[1], f"{biggest_gain[0]:+d}", biggest_gain[2]))

    saint = min(with_games, key=lambda a: (a.shame_rate, a.shame_count))
    entries.append(HallEntry("Cleanest record", saint.nickname, f"{saint.clean_rate}% clean"))

    best = max(with_games, key=lambda a: a.best_kills or 0)
    if best.best_kills:
        entries.append(HallEntry("Best game", best.nickname, f"{best.best_kills} kills", best.best_match_id))
    return entries


def group_by_player(records: list[MatchRecord]) -> dict[str, list[MatchRecord]]:
    out: dict[str, list[MatchRecord]] = defaultdict(list)
    for m in records:
        for pid in m.players:
            out[pid].append(m)
    for rows in out.values():
        rows.sort(key=lambda m: m.finished_at, reverse=True)
    return out
