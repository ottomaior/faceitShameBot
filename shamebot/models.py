"""Data classes for the cached state (schema v2) and parsers for FaceIT payloads."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 3  # v3 = extended per-match stats (entry/clutch/utility/flash)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _opt_int(v: Any) -> int | None:
    return None if v is None else _int(v)


@dataclass
class PlayerSnapshot:
    player_id: str
    nickname: str
    avatar: str | None = None
    country: str | None = None
    steam_id: str | None = None  # steam64, needed for Leetify lookups
    level: int | None = None
    elo: int | None = None
    elo_updated_at: str | None = None
    elo_ts: int | None = None  # unix time of the last ELO refresh (for delta attribution)
    faceit_url: str | None = None
    elo_history: list[list[int]] = field(default_factory=list)  # [[unix_ts, elo], ...]

    ELO_HISTORY_CAP = 300

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("ELO_HISTORY_CAP", None)
        return d

    @classmethod
    def from_dict(cls, player_id: str, d: dict) -> "PlayerSnapshot":
        return cls(
            player_id=player_id,
            nickname=d.get("nickname") or player_id,
            avatar=d.get("avatar"),
            country=d.get("country"),
            steam_id=d.get("steam_id"),
            level=_opt_int(d.get("level")),
            elo=_opt_int(d.get("elo")),
            elo_updated_at=d.get("elo_updated_at"),
            elo_ts=_opt_int(d.get("elo_ts")),
            faceit_url=d.get("faceit_url"),
            elo_history=[[_int(t), _int(e)] for t, e in (d.get("elo_history") or [])],
        )

    @classmethod
    def from_profile(cls, profile: dict, previous: "PlayerSnapshot | None" = None) -> "PlayerSnapshot":
        cs2 = (profile.get("games") or {}).get("cs2") or {}
        url = profile.get("faceit_url") or ""
        snap = cls(
            player_id=profile["player_id"],
            nickname=profile.get("nickname") or profile["player_id"],
            avatar=profile.get("avatar") or None,
            country=profile.get("country"),
            steam_id=str(cs2.get("game_player_id") or profile.get("steam_id_64") or "") or None,
            level=_opt_int(cs2.get("skill_level")) or None,
            elo=_opt_int(cs2.get("faceit_elo")),
            faceit_url=url.replace("{lang}", "en") if url else None,
        )
        if previous:
            snap.elo_history = list(previous.elo_history)
            snap.elo_updated_at = previous.elo_updated_at
            snap.elo_ts = previous.elo_ts
        return snap

    def push_elo(self, ts: int, elo: int) -> None:
        if self.elo_history and self.elo_history[-1][1] == elo and self.elo_history[-1][0] >= ts:
            return
        self.elo_history.append([ts, elo])
        if len(self.elo_history) > self.ELO_HISTORY_CAP:
            del self.elo_history[: -self.ELO_HISTORY_CAP]


@dataclass
class ScoreRow:
    """One line of the 10-player scoreboard (kept compact on purpose)."""

    pid: str
    nick: str
    k: int
    d: int
    a: int
    adr: float
    kd: float
    hs: int
    mvp: int
    level: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ScoreRow":
        return cls(
            pid=d.get("pid", ""),
            nick=d.get("nick", "?"),
            k=_int(d.get("k")),
            d=_int(d.get("d")),
            a=_int(d.get("a")),
            adr=_float(d.get("adr")),
            kd=_float(d.get("kd")),
            hs=_int(d.get("hs")),
            mvp=_int(d.get("mvp")),
            level=_opt_int(d.get("level")),
        )


@dataclass
class TeamRecord:
    name: str
    score: int
    win: bool
    players: list[ScoreRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": self.score,
            "win": self.win,
            "players": [p.to_dict() for p in self.players],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TeamRecord":
        return cls(
            name=d.get("name", "Team"),
            score=_int(d.get("score")),
            win=bool(d.get("win")),
            players=[ScoreRow.from_dict(p) for p in d.get("players") or []],
        )


@dataclass
class TrackedResult:
    """A tracked player's stats in one match. Fields are None for v1 (kills-only) records."""

    nickname: str
    kills: int
    shamed: bool
    deaths: int | None = None
    assists: int | None = None
    adr: float | None = None
    kd: float | None = None
    kr: float | None = None
    hs_pct: int | None = None
    mvps: int | None = None
    result: int | None = None  # 1 win / 0 loss
    team_score: int | None = None
    enemy_score: int | None = None
    triple: int | None = None
    quadro: int | None = None
    penta: int | None = None
    elo_after: int | None = None
    elo_delta: int | None = None
    awards: list[str] = field(default_factory=list)
    # --- extended (schema v3) ---
    entry_count: int | None = None
    entry_wins: int | None = None
    first_kills: int | None = None
    c1v1: int | None = None
    w1v1: int | None = None
    c1v2: int | None = None
    w1v2: int | None = None
    clutch_kills: int | None = None
    damage: int | None = None
    utility_damage: int | None = None
    enemies_flashed: int | None = None
    flash_count: int | None = None
    flash_successes: int | None = None
    utility_count: int | None = None
    sniper_kills: int | None = None
    double_kills: int | None = None

    EXTENDED_FIELDS = (
        "entry_count", "entry_wins", "first_kills", "c1v1", "w1v1", "c1v2", "w1v2", "clutch_kills",
        "damage", "utility_damage", "enemies_flashed", "flash_count", "flash_successes",
        "utility_count", "sniper_kills", "double_kills",
    )

    @property
    def enriched(self) -> bool:
        return self.deaths is not None

    @property
    def extended(self) -> bool:
        return self.entry_count is not None

    @property
    def won(self) -> bool | None:
        return None if self.result is None else self.result == 1

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("EXTENDED_FIELDS", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TrackedResult":
        return cls(
            **{k: _opt_int(d.get(k)) for k in cls.EXTENDED_FIELDS},
            nickname=d.get("nickname") or "?",
            kills=_int(d.get("kills")),
            shamed=bool(d.get("shamed")),
            deaths=_opt_int(d.get("deaths")),
            assists=_opt_int(d.get("assists")),
            adr=None if d.get("adr") is None else _float(d.get("adr")),
            kd=None if d.get("kd") is None else _float(d.get("kd")),
            kr=None if d.get("kr") is None else _float(d.get("kr")),
            hs_pct=_opt_int(d.get("hs_pct")),
            mvps=_opt_int(d.get("mvps")),
            result=_opt_int(d.get("result")),
            team_score=_opt_int(d.get("team_score")),
            enemy_score=_opt_int(d.get("enemy_score")),
            triple=_opt_int(d.get("triple")),
            quadro=_opt_int(d.get("quadro")),
            penta=_opt_int(d.get("penta")),
            elo_after=_opt_int(d.get("elo_after")),
            elo_delta=_opt_int(d.get("elo_delta")),
            awards=list(d.get("awards") or []),
        )


@dataclass
class MatchRecord:
    match_id: str
    finished_at: int = 0
    v: int = 1
    map: str | None = None
    score: str | None = None  # "7 / 13" (team order as FaceIT reports it)
    rounds: int | None = None
    competition: str | None = None
    faceit_url: str | None = None
    map_image: str | None = None
    teams: list[TeamRecord] = field(default_factory=list)
    players: dict[str, TrackedResult] = field(default_factory=dict)

    @property
    def enriched(self) -> bool:
        return self.v >= 2 and bool(self.teams)

    @property
    def map_label(self) -> str:
        return (self.map or "unknown map").replace("de_", "").capitalize()

    def team_of(self, pid: str) -> TeamRecord | None:
        for team in self.teams:
            if any(row.pid == pid for row in team.players):
                return team
        return None

    def lowest_kills(self) -> int | None:
        rows = [row.k for team in self.teams for row in team.players]
        return min(rows) if rows else None

    def to_dict(self) -> dict:
        d: dict = {
            "v": self.v,
            "finished_at": self.finished_at,
            "players": {pid: r.to_dict() for pid, r in self.players.items()},
        }
        if self.v >= 2:
            d.update(
                {
                    "map": self.map,
                    "score": self.score,
                    "rounds": self.rounds,
                    "competition": self.competition,
                    "faceit_url": self.faceit_url,
                    "map_image": self.map_image,
                    "teams": [t.to_dict() for t in self.teams],
                }
            )
        return d

    @classmethod
    def from_dict(cls, match_id: str, d: dict) -> "MatchRecord":
        return cls(
            match_id=match_id,
            finished_at=_int(d.get("finished_at")),
            v=_int(d.get("v"), 1),
            map=d.get("map"),
            score=d.get("score"),
            rounds=_opt_int(d.get("rounds")),
            competition=d.get("competition"),
            faceit_url=d.get("faceit_url"),
            map_image=d.get("map_image"),
            teams=[TeamRecord.from_dict(t) for t in d.get("teams") or []],
            players={pid: TrackedResult.from_dict(p) for pid, p in (d.get("players") or {}).items()},
        )


# --------------------------------------------------------------------------- parsers


def _stat(ps: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        val = ps.get(key)
        if val not in (None, ""):
            return val
    return default


def parse_match(
    match_id: str,
    stats: dict,
    details: dict | None,
    tracked: dict[str, str],
    *,
    kill_threshold: int,
    finished_at: int | None = None,
) -> MatchRecord | None:
    """Build a v2 MatchRecord from ``/matches/{id}/stats`` (+ optional ``/matches/{id}``).

    ``tracked`` maps player_id -> nickname for the players we care about.
    Returns None when the payload has no rounds.
    """
    rounds = stats.get("rounds") or []
    if not rounds:
        return None
    rd = rounds[0]
    round_stats = rd.get("round_stats") or {}

    details = details or {}
    levels: dict[str, int] = {}
    for faction in (details.get("teams") or {}).values():
        for member in faction.get("roster") or []:
            if member.get("player_id") and member.get("game_skill_level"):
                levels[member["player_id"]] = _int(member["game_skill_level"])  # 0 = unranked -> unknown

    map_name = round_stats.get("Map") or (details.get("voting", {}).get("map", {}).get("pick") or [None])[0]
    map_image = None
    for entity in details.get("voting", {}).get("map", {}).get("entities") or []:
        if entity.get("game_map_id") == map_name:
            map_image = entity.get("image_lg") or entity.get("image_sm")
            break

    faceit_url = (details.get("faceit_url") or f"https://www.faceit.com/{{lang}}/cs2/room/{match_id}").replace(
        "{lang}", "en"
    )

    teams: list[TeamRecord] = []
    tracked_results: dict[str, TrackedResult] = {}
    for team in rd.get("teams") or []:
        ts = team.get("team_stats") or {}
        team_rec = TeamRecord(
            name=ts.get("Team", "Team"),
            score=_int(ts.get("Final Score")),
            win=_int(ts.get("Team Win")) == 1,
        )
        for player in team.get("players") or []:
            ps = player.get("player_stats") or {}
            pid = str(player.get("player_id") or "")
            nick = player.get("nickname") or "?"
            row = ScoreRow(
                pid=pid,
                nick=nick,
                k=_int(_stat(ps, "Kills")),
                d=_int(_stat(ps, "Deaths")),
                a=_int(_stat(ps, "Assists")),
                adr=round(_float(_stat(ps, "ADR", "Average Damage per Round")), 1),
                kd=round(_float(_stat(ps, "K/D Ratio")), 2),
                hs=_int(_stat(ps, "Headshots %")),
                mvp=_int(_stat(ps, "MVPs")),
                level=levels.get(pid),
            )
            team_rec.players.append(row)
            if pid in tracked or nick in tracked.values():
                key = pid if pid in tracked else next(p for p, n in tracked.items() if n == nick)
                tracked_results[key] = TrackedResult(
                    nickname=nick,
                    kills=row.k,
                    shamed=row.k < kill_threshold,
                    deaths=row.d,
                    assists=row.a,
                    adr=row.adr,
                    kd=row.kd,
                    kr=round(_float(_stat(ps, "K/R Ratio")), 2),
                    hs_pct=row.hs,
                    mvps=row.mvp,
                    result=_int(_stat(ps, "Result"), default=1 if team_rec.win else 0),
                    team_score=team_rec.score,
                    triple=_int(_stat(ps, "Triple Kills")),
                    quadro=_int(_stat(ps, "Quadro Kills")),
                    penta=_int(_stat(ps, "Penta Kills")),
                    entry_count=_int(_stat(ps, "Entry Count")),
                    entry_wins=_int(_stat(ps, "Entry Wins")),
                    first_kills=_int(_stat(ps, "First Kills")),
                    c1v1=_int(_stat(ps, "1v1Count")),
                    w1v1=_int(_stat(ps, "1v1Wins")),
                    c1v2=_int(_stat(ps, "1v2Count")),
                    w1v2=_int(_stat(ps, "1v2Wins")),
                    clutch_kills=_int(_stat(ps, "Clutch Kills")),
                    damage=_int(_stat(ps, "Damage")),
                    utility_damage=_int(_stat(ps, "Utility Damage")),
                    enemies_flashed=_int(_stat(ps, "Enemies Flashed")),
                    flash_count=_int(_stat(ps, "Flash Count")),
                    flash_successes=_int(_stat(ps, "Flash Successes")),
                    utility_count=_int(_stat(ps, "Utility Count")),
                    sniper_kills=_int(_stat(ps, "Sniper Kills")),
                    double_kills=_int(_stat(ps, "Double Kills")),
                )
        teams.append(team_rec)

    # enemy score: the other team's score
    for pid, res in tracked_results.items():
        for team in teams:
            if not any(r.pid == pid for r in team.players):
                res.enemy_score = team.score

    if finished_at is None:
        finished_at = _int(details.get("finished_at"))

    return MatchRecord(
        match_id=match_id,
        finished_at=finished_at,
        v=SCHEMA_VERSION,
        map=map_name,
        score=round_stats.get("Score"),
        rounds=_opt_int(round_stats.get("Rounds")),
        competition=details.get("competition_name"),
        faceit_url=faceit_url,
        map_image=map_image,
        teams=teams,
        players=tracked_results,
    )
