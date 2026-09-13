"""Blame report: how much of a lost game is on one player, measured against his own teammates.

Everything here comes from the per-match scoreboard already cached in state.json (all ten rows) plus
the tracked player's extended stats. No demo, no per-round data — so it is a *contribution* estimate,
not a round-by-round verdict, and the card says so by listing the facts it is built on.

Impact per teammate (basic stats every row has):
    impact = kills + 0.5 * assists + ADR / 10 + MVPs - 0.5 * deaths
Blame share = this player's part of the team's total shortfall below its best player. Five equal
players split it 20/20/20/20/20; the best player on the team always gets 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import MatchRecord, ScoreRow, TrackedResult

# Only list a fact when it is actually damning.
DEATH_SHARE_FLAG = 25  # % of team deaths (even split is 20)
KILL_SHARE_FLAG = 15  # % of team kills
DAMAGE_SHARE_FLAG = 15  # % of team damage
BLAME_HEAVY = 35  # % of the shortfall → "blame_heavy" roast modifier
MAX_FACTS = 4


def impact(row: ScoreRow) -> float:
    return row.k + 0.5 * row.a + row.adr / 10 + row.mvp - 0.5 * row.d


@dataclass
class BlameReport:
    share: int  # % of the team's shortfall that is this player's
    rank: int  # 1 = best impact on the team … team_size = worst
    team_size: int
    kill_share: int  # % of team kills
    death_share: int  # % of team deaths
    damage_share: int  # % of team damage (ADR × rounds, so shares are exact)
    lost: bool
    facts: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return "BLAME" if self.lost else "DEAD WEIGHT"

    @property
    def heavy(self) -> bool:
        return self.share >= BLAME_HEAVY

    def summary(self) -> str:
        """One line for the Discord message: '41% blame · 5th of 5 on the team'."""
        word = "blame" if self.lost else "dead weight"
        return f"{self.share}% {word} · {_ordinal(self.rank)} of {self.team_size} on the team"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _pct(part: float, whole: float) -> int:
    return round(100 * part / whole) if whole else 0


def compute_blame(record: MatchRecord, pid: str) -> BlameReport | None:
    """Blame report for a tracked player, or None when the cached record has no team rows (v1)."""
    r: TrackedResult | None = record.players.get(pid)
    if r is None:
        return None
    team = next((t for t in record.teams if any(row.pid == pid for row in t.players)), None)
    if team is None or len(team.players) < 2:
        return None
    rows = team.players
    me = next(row for row in rows if row.pid == pid)

    scores = {row.pid: impact(row) for row in rows}
    best = max(scores.values())
    shortfalls = {p: best - s for p, s in scores.items()}
    total = sum(shortfalls.values())
    share = _pct(shortfalls[pid], total) if total > 1e-9 else round(100 / len(rows))
    rank = 1 + sum(1 for p, s in scores.items() if s > scores[pid])

    team_k = sum(row.k for row in rows)
    team_d = sum(row.d for row in rows)
    team_dmg = sum(row.adr for row in rows)  # every row is ADR over the same round count
    rep = BlameReport(
        share=share,
        rank=rank,
        team_size=len(rows),
        kill_share=_pct(me.k, team_k),
        death_share=_pct(me.d, team_d),
        damage_share=_pct(me.adr, team_dmg),
        lost=r.result == 0,
    )
    rep.facts = _facts(rep, me, rows, r)
    return rep


def _facts(rep: BlameReport, me: ScoreRow, rows: list[ScoreRow], r: TrackedResult) -> list[str]:
    """Short, checkable statements — the specific ones (duels, clutches) come first."""
    facts: list[str] = []
    if rep.death_share >= DEATH_SHARE_FLAG:
        facts.append(f"{rep.death_share}% of the team's deaths")
    if r.extended:
        lost_entries = (r.entry_count or 0) - (r.entry_wins or 0)
        if lost_entries >= 2 and lost_entries > (r.entry_wins or 0):
            facts.append(f"lost {lost_entries} of {r.entry_count} opening duels")
        clutches = (r.c1v1 or 0) + (r.c1v2 or 0)
        wins = (r.w1v1 or 0) + (r.w1v2 or 0)
        if clutches == 1 and wins == 0:
            facts.append("lost the only clutch")
        elif clutches >= 2 and wins == 0:
            facts.append(f"lost all {clutches} clutches")
        elif clutches >= 2 and wins < clutches:
            facts.append(f"{wins}/{clutches} clutches")
    if rep.kill_share <= KILL_SHARE_FLAG:
        facts.append(f"{rep.kill_share}% of the team's kills")
    if rep.damage_share <= DAMAGE_SHARE_FLAG:
        facts.append(f"{rep.damage_share}% of the team's damage")
    if me.adr <= min(row.adr for row in rows):
        facts.append("lowest ADR on the team")
    elif me.kd <= min(row.kd for row in rows):
        facts.append("lowest K/D on the team")
    if me.mvp == 0 and (r.team_score or 0) >= 5:
        facts.append(f"0 MVPs in {r.team_score} rounds won")
    return facts[:MAX_FACTS]


# ---------------------------------------------------------------------- carry (the mirror image)

CARRY_HEAVY = 30  # % of the team's impact (even split is 20)
KILL_SHARE_HIGH = 30
DAMAGE_SHARE_HIGH = 28
MVP_SHARE_HIGH = 45


@dataclass
class CarryReport:
    """How much of the team's output was this player's. Same shape as BlameReport so the card can
    draw either; ``positive`` tells the renderer to keep the accent colour instead of going red."""

    share: int  # % of the team's total impact
    rank: int
    team_size: int
    kill_share: int
    death_share: int
    damage_share: int
    mvp_share: int
    won: bool
    top_of_lobby: bool  # highest kills of all ten
    facts: list[str] = field(default_factory=list)
    positive: bool = True

    @property
    def label(self) -> str:
        return "CARRY" if self.won else "WASTED"

    @property
    def heavy(self) -> bool:
        return self.share >= CARRY_HEAVY

    def summary(self) -> str:
        word = "carry" if self.won else "of the team's output, wasted"
        return f"{self.share}% {word} · {_ordinal(self.rank)} of {self.team_size} on the team"


def compute_carry(record: MatchRecord, pid: str) -> CarryReport | None:
    r: TrackedResult | None = record.players.get(pid)
    if r is None:
        return None
    team = next((t for t in record.teams if any(row.pid == pid for row in t.players)), None)
    if team is None or len(team.players) < 2:
        return None
    rows = team.players
    me = next(row for row in rows if row.pid == pid)
    scores = {row.pid: max(0.0, impact(row)) for row in rows}
    total = sum(scores.values())
    rank = 1 + sum(1 for p, s in scores.items() if s > scores[pid])
    team_k = sum(row.k for row in rows)
    team_d = sum(row.d for row in rows)
    team_dmg = sum(row.adr for row in rows)
    team_mvp = sum(row.mvp for row in rows)
    all_rows = [row for t in record.teams for row in t.players]
    rep = CarryReport(
        share=_pct(scores[pid], total) if total else round(100 / len(rows)),
        rank=rank,
        team_size=len(rows),
        kill_share=_pct(me.k, team_k),
        death_share=_pct(me.d, team_d),
        damage_share=_pct(me.adr, team_dmg),
        mvp_share=_pct(me.mvp, team_mvp),
        won=r.result == 1,
        top_of_lobby=me.k >= max(row.k for row in all_rows),
    )
    rep.facts = _carry_facts(rep, me, rows, r)
    return rep


def _carry_facts(rep: CarryReport, me: ScoreRow, rows: list[ScoreRow], r: TrackedResult) -> list[str]:
    facts: list[str] = []
    if rep.top_of_lobby:
        facts.append("top-fragger of the lobby")
    if rep.kill_share >= KILL_SHARE_HIGH:
        facts.append(f"{rep.kill_share}% of the team's kills")
    if rep.damage_share >= DAMAGE_SHARE_HIGH:
        facts.append(f"{rep.damage_share}% of the team's damage")
    if rep.mvp_share >= MVP_SHARE_HIGH and me.mvp >= 3:
        facts.append(f"{me.mvp} of the team's {sum(row.mvp for row in rows)} MVPs")
    if r.extended:
        entries, wins = r.entry_count or 0, r.entry_wins or 0
        if entries >= 4 and wins >= entries * 0.7:
            facts.append(f"won {wins} of {entries} opening duels")
        clutches = (r.c1v1 or 0) + (r.c1v2 or 0)
        cwins = (r.w1v1 or 0) + (r.w1v2 or 0)
        if cwins >= 2:
            facts.append(f"won {cwins} of {clutches} clutches")
        elif cwins == 1 and (r.w1v2 or 0) == 1:
            facts.append("won a 1v2")
    if me.d <= min(row.d for row in rows) and me.d < 12:
        facts.append(f"fewest deaths on the team ({me.d})")
    return facts[:MAX_FACTS]
