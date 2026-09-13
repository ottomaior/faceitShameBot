"""Post-mortem of one match: rank the five players of a team against each other.

Pure functions over a MatchRecord (all ten scoreboard rows carry extended stats since schema v3
rows were widened) plus optional per-match Leetify ratings. No network, so it is unit-testable; the
orchestration (fetching the match, Leetify, avatars, rendering) lives in ``App.postmortem``.

Scoring is *team-relative*: every metric is min-max normalised inside the team (0 = worst on the
team, 1 = best, 0.5 when everyone is equal), categories average their metrics, and the overall
score is the weighted mean of the usable categories. It answers "who played best in this game",
not "who is the better player" — that is ``/compare``.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Callable

from .blame import impact
from .leetify import fmt_rating
from .models import MatchRecord, ScoreRow

Fmt = Callable[[float], str]


def _pct(v: float) -> str:
    return f"{v:.0f}%"


def _f2(v: float) -> str:
    return f"{v:.2f}"


def _f1(v: float) -> str:
    return f"{v:.1f}"


def _int(v: float) -> str:
    return f"{v:.0f}"


def _leet(v: float) -> str:
    return fmt_rating(v, fraction=True)


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _share(part: float, whole: float) -> int:
    return round(100 * part / whole) if whole else 0


# ------------------------------------------------------------------- inputs


@dataclass
class PlayerLine:
    """One teammate's metric vector for the match."""

    pid: str
    nick: str
    tracked: bool
    level: int | None
    steam_id: str | None
    avatar: str | None
    k: int
    d: int
    a: int
    adr: float
    kd: float
    hs: int
    mvp: int
    kr: float | None = None
    entry_count: int | None = None
    entry_wins: int | None = None
    first_kills: int | None = None
    clutches: int | None = None
    clutch_wins: int | None = None
    clutch_kills: int | None = None
    util_dmg: int | None = None
    enemies_flashed: int | None = None
    util_count: int | None = None
    impact: float = 0.0
    leetify: float | None = None  # per-match Leetify rating as a fraction (×100 for display)
    kill_share: int = 0
    death_share: int = 0

    @property
    def extended(self) -> bool:
        return self.entry_count is not None

    @property
    def entry_pct(self) -> float | None:
        if not self.entry_count:
            return None
        return 100.0 * (self.entry_wins or 0) / self.entry_count

    @classmethod
    def from_row(cls, row: ScoreRow, *, tracked: bool, leetify: float | None) -> "PlayerLine":
        return cls(
            pid=row.pid, nick=row.nick, tracked=tracked, level=row.level, steam_id=row.steam_id, avatar=row.avatar,
            k=row.k, d=row.d, a=row.a, adr=row.adr, kd=row.kd, hs=row.hs, mvp=row.mvp,
            kr=row.kr,
            entry_count=row.entry_count, entry_wins=row.entry_wins, first_kills=row.first_kills,
            clutches=row.clutches if row.extended else None,
            clutch_wins=row.clutch_wins if row.extended else None,
            clutch_kills=row.clutch_kills,
            util_dmg=row.utility_damage, enemies_flashed=row.enemies_flashed, util_count=row.utility_count,
            impact=impact(row),
            leetify=leetify,
        )


# ---------------------------------------------------------------- categories


@dataclass
class TeamMetric:
    label: str
    values: dict[str, float | None]  # pid -> value
    fmt: Fmt = _f2
    higher_better: bool = True
    source: str = "faceit"  # "faceit" | "leetify"

    @property
    def present(self) -> dict[str, float]:
        return {p: v for p, v in self.values.items() if v is not None}

    @property
    def usable(self) -> bool:
        return len(self.present) >= 2

    def norm(self, pid: str) -> float | None:
        """Min-max position inside the team, 1 = best. 0.5 when the metric is flat."""
        v = self.values.get(pid)
        if v is None or not self.usable:
            return None
        vals = self.present.values()
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            return 0.5
        n = (v - lo) / (hi - lo)
        return n if self.higher_better else 1.0 - n

    def rank(self, pid: str) -> int | None:
        """Competition rank (1 = best); None when the value is missing."""
        v = self.values.get(pid)
        if v is None or not self.usable:
            return None
        better = sum(1 for o in self.present.values() if (o > v if self.higher_better else o < v))
        return 1 + better

    def text(self, pid: str) -> str:
        v = self.values.get(pid)
        return "—" if v is None else self.fmt(v)


@dataclass
class TeamCategory:
    name: str
    weight: int
    metrics: list[TeamMetric]
    emoji: str = ""

    @property
    def usable(self) -> bool:
        return any(m.usable for m in self.metrics)

    def score(self, pid: str) -> float | None:
        norms = [n for m in self.metrics if (n := m.norm(pid)) is not None]
        return sum(norms) / len(norms) if norms else None


def build_categories(lines: list[PlayerLine]) -> list[TeamCategory]:
    def col(attr: str) -> dict[str, float | None]:
        return {ln.pid: (None if getattr(ln, attr) is None else float(getattr(ln, attr))) for ln in lines}

    return [
        TeamCategory("Impact", 3, [
            TeamMetric("Leetify rating", col("leetify"), _leet, source="leetify"),
            TeamMetric("Impact", col("impact"), _f1),
            TeamMetric("MVPs", col("mvp"), _int),
        ], "💥"),
        TeamCategory("Fragging", 3, [
            TeamMetric("Kills", col("k"), _int),
            TeamMetric("K/D", col("kd"), _f2),
            TeamMetric("ADR", col("adr"), _int),
            TeamMetric("K/R", col("kr"), _f2),
        ], "🔫"),
        TeamCategory("Opening", 2, [
            TeamMetric("First kills", col("first_kills"), _int),
            TeamMetric("Entry success", {ln.pid: ln.entry_pct for ln in lines}, _pct),
            TeamMetric("Entry wins", col("entry_wins"), _int),
        ], "🚪"),
        TeamCategory("Clutch", 1, [
            TeamMetric("Clutch wins", col("clutch_wins"), _int),
            TeamMetric("Clutch kills", col("clutch_kills"), _int),
        ], "🧊"),
        TeamCategory("Utility", 1, [
            TeamMetric("Utility damage", col("util_dmg"), _int),
            TeamMetric("Enemies flashed", col("enemies_flashed"), _int),
        ], "💣"),
        TeamCategory("Discipline", 1, [
            TeamMetric("Deaths", col("d"), _int, higher_better=False),
            TeamMetric("Death share", col("death_share"), _pct, higher_better=False),
            TeamMetric("HS%", col("hs"), _pct),
        ], "🛡️"),
    ]


# ------------------------------------------------------------------- scoring


@dataclass
class RankedRow:
    line: PlayerLine
    rank: int  # 1 = best on the team
    score: float  # 0..1
    cat_scores: dict[str, float | None]
    best_at: str | None  # category this player leads on the team (largest margin)
    worst_at: str | None  # category this player is last on the team in (largest gap)
    low_at: str | None  # the player's own lowest category, for prose ("invisible at ...")
    kills_rank: int
    leetify_rank: int | None

    @property
    def pid(self) -> str:
        return self.line.pid

    @property
    def nick(self) -> str:
        return self.line.nick


def score_team(lines: list[PlayerLine], categories: list[TeamCategory]) -> list[RankedRow]:
    usable = [c for c in categories if c.usable]
    total_w = sum(c.weight for c in usable) or 1
    scores: dict[str, float] = {}
    cat_scores: dict[str, dict[str, float | None]] = {}
    for ln in lines:
        cs = {c.name: c.score(ln.pid) for c in categories}
        cat_scores[ln.pid] = cs
        scores[ln.pid] = sum(c.weight * (cs[c.name] or 0.0) for c in usable) / total_w

    order = sorted(lines, key=lambda ln: (-scores[ln.pid], -ln.impact, -ln.k, ln.d, ln.nick.lower()))
    kills = TeamMetric("Kills", {ln.pid: float(ln.k) for ln in lines})
    leet = TeamMetric("Leetify", {ln.pid: ln.leetify for ln in lines})

    rows: list[RankedRow] = []
    for idx, ln in enumerate(order, 1):
        best_at = worst_at = low_at = None
        best_margin = worst_margin = -1.0
        low_score = 2.0
        for c in usable:
            mine = cat_scores[ln.pid][c.name]
            if mine is None:
                continue
            others = [s for p, s in ((o.pid, cat_scores[o.pid][c.name]) for o in lines) if p != ln.pid and s is not None]
            if not others or max(others) - min(others) < 1e-9 and abs(mine - others[0]) < 1e-9:
                continue  # flat category: nobody leads, nobody trails
            # BEST: at least tied for the top; WORST: at least tied for the bottom. Largest margin wins.
            if mine >= max(others) - 1e-9 and mine - max(others) > best_margin:
                best_margin, best_at = mine - max(others), c.name
            if mine <= min(others) + 1e-9 and min(others) - mine > worst_margin:
                worst_margin, worst_at = min(others) - mine, c.name
            if mine < low_score:
                low_score, low_at = mine, c.name
        if worst_at == best_at:
            worst_at = None
        rows.append(RankedRow(
            line=ln, rank=idx, score=scores[ln.pid], cat_scores=cat_scores[ln.pid],
            best_at=best_at, worst_at=worst_at, low_at=low_at,
            kills_rank=kills.rank(ln.pid) or idx, leetify_rank=leet.rank(ln.pid),
        ))
    return rows


# -------------------------------------------------------------------- result


@dataclass
class Callout:
    key: str
    pid: str
    text: str


@dataclass
class PostmortemResult:
    match_id: str
    map_label: str
    score: str
    won: bool
    rounds: int
    team_name: str
    finished_at: int
    rows: list[RankedRow]
    categories: list[TeamCategory]
    tracked_pids: list[str]  # tracked friends on the ranked team, rank order
    other_team: list[tuple[str, int]] = field(default_factory=list)  # (nick, rank on their team)
    callouts: list[Callout] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    leetify_used: bool = False
    headline: str = ""
    lines: dict[str, str] = field(default_factory=dict)  # pid -> roast line (tracked only)

    @property
    def mvp(self) -> RankedRow:
        return self.rows[0]

    @property
    def anchor(self) -> RankedRow:
        return self.rows[-1]

    def row(self, pid: str) -> RankedRow:
        return next(r for r in self.rows if r.pid == pid)

    @property
    def usable_categories(self) -> list[TeamCategory]:
        return [c for c in self.categories if c.usable]


def choose_team(record: MatchRecord, tracked: set[str], *, focus_pid: str | None = None) -> int:
    """Index of the team to rank: most tracked players, tie broken by ``focus_pid``, else team 0."""
    if not record.teams:
        return 0
    counts = [sum(1 for row in t.players if row.pid in tracked) for t in record.teams]
    best = max(counts)
    candidates = [i for i, c in enumerate(counts) if c == best]
    if len(candidates) > 1 and focus_pid:
        for i in candidates:
            if any(row.pid == focus_pid for row in record.teams[i].players):
                return i
    return candidates[0]


def build_lines(record: MatchRecord, team_index: int, tracked: set[str], leetify: dict[str, float] | None) -> list[PlayerLine]:
    rows = record.teams[team_index].players
    lines = [
        PlayerLine.from_row(row, tracked=row.pid in tracked, leetify=(leetify or {}).get(row.steam_id or ""))
        for row in rows
    ]
    team_k = sum(ln.k for ln in lines)
    team_d = sum(ln.d for ln in lines)
    for ln in lines:
        ln.kill_share = _share(ln.k, team_k)
        ln.death_share = _share(ln.d, team_d)
    return lines


# ------------------------------------------------------------------ callouts

CALLOUT_TEXT = {
    "feed_illusion": "{nick}: {kills} kills, {rank_ord} in the post-mortem. The kill feed lied.",
    "quiet_carry": "{nick} was the real MVP with only {kills} kills — {best} did the work nobody screenshots.",
    "paper_kills": "{nick}: {kills} kills at {adr} ADR. Exit frags and a wide crosshair.",
    "entry_fodder": "{nick} took {entries} opening duels and won {entry_wins}. That's not entrying, that's scouting by dying.",
    "death_sponge": "{nick} ate {death_share}% of the team's deaths ({deaths}).",
    "no_util": "{nick}: {util_dmg} utility damage and {enemies_flashed} enemies flashed. Grenades are the round things in the buy menu.",
    "clutch_god": "{nick} won {clutch_wins} of {clutches} clutches. Someone tell the kill feed.",
    "leetify_disagrees": "Leetify has {nick} {leetify_rank_ord} on the team; the scoreboard says {kills_rank_ord}.",
}
MAX_CALLOUTS = 4


def _ctx(res: PostmortemResult, row: RankedRow) -> dict:
    ln = row.line
    kills_leader = max(res.rows, key=lambda r: (r.line.k, -r.rank))
    return {
        "nick": ln.nick, "rank": row.rank, "rank_ord": ordinal(row.rank), "n": len(res.rows),
        "kills": ln.k, "deaths": ln.d, "assists": ln.a, "adr": f"{ln.adr:.0f}", "kd": f"{ln.kd:.2f}",
        "mvps": ln.mvp, "hs": ln.hs,
        "best": (row.best_at or "nothing").lower(), "worst": (row.worst_at or row.low_at or "nothing").lower(),
        "score": res.score, "map": res.map_label,
        "mvp": res.mvp.nick, "anchor": res.anchor.nick, "kills_leader": kills_leader.nick,
        "entries": ln.entry_count or 0, "entry_wins": ln.entry_wins or 0,
        "clutches": ln.clutches or 0, "clutch_wins": ln.clutch_wins or 0,
        "util_dmg": ln.util_dmg or 0, "enemies_flashed": ln.enemies_flashed or 0,
        "death_share": ln.death_share, "kill_share": ln.kill_share,
        "leetify": fmt_rating(ln.leetify, fraction=True),
        "kills_rank_ord": ordinal(row.kills_rank),
        "leetify_rank_ord": ordinal(row.leetify_rank) if row.leetify_rank else "?",
    }


_SINGULAR = {"kills": "kill", "deaths": "death", "clutches": "clutch", "enemies": "enemy", "duels": "duel"}


def _fmt(line: str, ctx: dict) -> str:
    try:
        out = line.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return line
    return re.sub(
        r"(?<!\d)1 (kills|deaths|clutches|enemies|duels|MVPs)\b",
        lambda m: "1 " + _SINGULAR.get(m.group(1), m.group(1)[:-1]),
        out,
    )


def find_callouts(res: PostmortemResult) -> list[Callout]:
    n = len(res.rows)
    kills_median = median(r.line.k for r in res.rows) if res.rows else 0
    adr_rank = TeamMetric("ADR", {r.pid: r.line.adr for r in res.rows})
    found: list[tuple[int, Callout]] = []  # (priority, callout)
    for row in res.rows:
        ln = row.line
        hits: list[str] = []
        if row.kills_rank <= 2 and row.rank >= 3 and n >= 3:
            hits.append("feed_illusion")
        if row.rank == 1 and row.kills_rank >= 3:
            hits.append("quiet_carry")
        if ln.k >= kills_median and (adr_rank.rank(ln.pid) or 0) >= max(4, n - 1) and n >= 4:
            hits.append("paper_kills")
        if (ln.entry_count or 0) >= 4 and (ln.entry_pct or 0) <= 34:
            hits.append("entry_fodder")
        if ln.death_share >= 28:
            hits.append("death_sponge")
        if res.rounds >= 16 and ln.extended and (ln.util_dmg or 0) <= 20 and (ln.enemies_flashed or 0) <= 2:
            hits.append("no_util")
        if (ln.clutch_wins or 0) >= 2:
            hits.append("clutch_god")
        if row.leetify_rank is not None and abs(row.leetify_rank - row.kills_rank) >= 2:
            hits.append("leetify_disagrees")
        ctx = _ctx(res, row)
        for i, key in enumerate(hits):
            prio = (0 if ln.tracked else 1) * 100 + list(CALLOUT_TEXT).index(key) + i
            found.append((prio, Callout(key, ln.pid, _fmt(CALLOUT_TEXT[key], ctx))))
    found.sort(key=lambda t: t[0])
    return [c for _, c in found[:MAX_CALLOUTS]]


# --------------------------------------------------------------------- prose

HEADLINE_WON_TRACKED_MVP = [
    "**{mvp}** carried this {score} win on {map}. Kill feed says {kills_leader}, the numbers say {mvp}.",
    "{score} on {map}, and it was **{mvp}**'s game — {best_mvp} won it, whatever the scoreboard says.",
    "Won {score} on {map}. **{mvp}** was the reason; everyone else was the passenger list.",
    "{map}, {score}. **{mvp}** top of the post-mortem. The rest of you may now stop arguing.",
    "A {score} win on {map} that belongs to **{mvp}**. {anchor} was also present.",
    "**{mvp}** wins the post-mortem on {map} ({score}). The kill feed was a rumour; this is the audit.",
    "{score} on {map}. **{mvp}** did the work, **{anchor}** did the watching.",
    "Won {score} on {map}. Say thank you to **{mvp}** — best at {best_mvp}, best overall, best behaved.",
]
HEADLINE_WON_RANDOM_MVP = [
    "You won {score} on {map}. Say thank you to **{mvp}**, a stranger who out-played all of you.",
    "{score} on {map}. Best player on the team: **{mvp}**. Not one of you. Let that sink in.",
    "Won {score} on {map}, carried by a random called **{mvp}**. The post-mortem is humbling.",
    "{map}, {score}, and the MVP is **{mvp}** — someone you'll never queue with again. Tragic.",
    "A {score} win on {map} that a random (**{mvp}**) won for you. Add him, quickly.",
    "Won {score} on {map}. **{mvp}** was the best on the team and he isn't in this Discord.",
]
HEADLINE_LOST_TRACKED_ANCHOR = [
    "Lost {score} on {map}. The post-mortem names **{anchor}** as the anchor and it isn't close.",
    "{score} on {map}. **{mvp}** did what he could; **{anchor}** did what he does.",
    "Lost {score} on {map}. **{anchor}** finished last on the team — the numbers, not the kill feed, say so.",
    "{map}, {score}, gone. **{anchor}** was the weight; **{mvp}** was the one dragging it.",
    "Lost {score} on {map}. If you're looking for the reason, **{anchor}** is {anchor_ord} of {n}.",
    "A {score} loss on {map}. **{anchor}** last in the post-mortem. The argument ends here.",
    "{score} on {map}. **{mvp}** top, **{anchor}** bottom, and the kill feed was lying about both.",
    "Lost {score} on {map}. **{anchor}** was the anchor. Not a metaphor — {worst_anchor} was that bad.",
]
HEADLINE_LOST_RANDOM_ANCHOR = [
    "Lost {score} on {map}. For once the random (**{anchor}**) was the problem — enjoy it, it won't happen again.",
    "{score} on {map}. Worst on the team: **{anchor}**, a random. Your friends are merely mediocre.",
    "Lost {score} on {map}. **{anchor}** anchored it and he isn't one of you. Small mercies.",
    "{map}, {score}. The post-mortem blames **{anchor}** — not in this Discord, so blame freely.",
    "A {score} loss on {map} with a random (**{anchor}**) at the bottom. **{mvp}** was still the best of you.",
    "Lost {score} on {map}. **{anchor}** last, and for once that's a stranger's name.",
]
LINE_TOP = [
    "**{nick}** — 1st of {n}. {kills} kills, {adr} ADR, best at {best}. The argument is over, you can stop typing.",
    "**{nick}** top of the team. Worst category {worst}, which means even your bad was someone's good.",
    "**{nick}** — 1st of {n}. {kills}/{deaths}, {mvps} MVPs. Screenshot this, it won't happen next map.",
    "**{nick}** best on the team, carried by {best}. Enjoy the one evening nobody can argue with.",
    "**{nick}** — 1st of {n}. {kd} K/D and {adr} ADR. The kill feed agreed for once.",
    "**{nick}** took the post-mortem. {kills} kills, {clutch_wins} clutches, {mvps} MVPs. Unbearable in voice for a week.",
    "**{nick}** — 1st. Even Leetify ({leetify}) couldn't find a reason to disagree.",
    "**{nick}** top of {n}. Best at {best}. The friends' group chat may now proceed to the excuses.",
]
LINE_MID = [
    "**{nick}** — {rank_ord} of {n}. Solid at {best}, invisible at {worst}. Neither the hero nor the excuse.",
    "**{nick}** finished {rank_ord}. {kills}/{deaths} and {mvps} MVPs. The most forgettable game you'll argue about all week.",
    "**{nick}** — {rank_ord} of {n}. {adr} ADR, {kd} K/D. Present, contributing, unremarkable.",
    "**{nick}** {rank_ord}. Good at {best}, then took the rest of the game off around {worst}.",
    "**{nick}** — {rank_ord} of {n}. {kills} kills, {death_share}% of the team's deaths. The middle, where arguments go to die.",
    "**{nick}** {rank_ord} on the team. Not the problem, not the solution. A spectator with a K/D.",
    "**{nick}** — {rank_ord}. {entries} entries, {entry_wins} won. Somewhere between a teammate and a decoy.",
    "**{nick}** finished {rank_ord} of {n}. Worst at {worst}, and honestly that's the only thing worth mentioning.",
]
LINE_LAST = [
    "**{nick}** — {rank_ord} of {n}. {kills} kills, {deaths} deaths, {adr} ADR. The kill feed was the only place you existed.",
    "**{nick}** last on the team. {worst} was the worst of a bad set. Four players and a spectator with a mic.",
    "**{nick}** — {rank_ord} of {n}. {death_share}% of the team's deaths. You didn't lose the game, you subscribed to it.",
    "**{nick}** bottom of the post-mortem. {kd} K/D, {mvps} MVPs. The numbers are in and they're unkind.",
    "**{nick}** — {rank_ord} of {n}. {kills} kills at {adr} ADR. Statistically, a spawn point.",
    "**{nick}** last. {entries} opening duels, {entry_wins} won. You weren't entrying, you were announcing.",
    "**{nick}** — {rank_ord} of {n}. Even {best} was only 'best' relative to the rest of your game.",
    "**{nick}** last on the team. Leetify says {leetify}. Leetify is being polite.",
]
OTHER_TEAM = "{names} played against you: {placings} on their team."


def _pick(rng: random.Random, pool: list[str], recent: list[str] | None) -> str:
    if recent is None:
        return rng.choice(pool)
    fresh = [ln for ln in pool if ln not in recent] or list(pool)
    line = rng.choice(fresh)
    recent.append(line)
    del recent[:-40]
    return line


def write_prose(res: PostmortemResult, *, seed: str, recent: list[str] | None = None) -> PostmortemResult:
    """Fill ``headline`` and one roast line per tracked friend. Deterministic per seed."""
    rng = random.Random(f"postmortem|{seed}")
    if not res.rows:
        return res
    mvp, anchor = res.mvp, res.anchor
    base = _ctx(res, mvp)
    base.update({
        "best_mvp": (mvp.best_at or "everything").lower(),
        "anchor_ord": ordinal(anchor.rank),
        "worst_anchor": (anchor.worst_at or anchor.low_at or "everything").lower(),
    })
    if res.won:
        pool = HEADLINE_WON_TRACKED_MVP if mvp.line.tracked else HEADLINE_WON_RANDOM_MVP
    else:
        pool = HEADLINE_LOST_TRACKED_ANCHOR if anchor.line.tracked else HEADLINE_LOST_RANDOM_ANCHOR
    res.headline = _fmt(_pick(rng, pool, recent), base)

    n = len(res.rows)
    for pid in res.tracked_pids:
        row = res.row(pid)
        if row.rank == 1:
            pool = LINE_TOP
        elif row.rank == n and n > 1:
            pool = LINE_LAST
        else:
            pool = LINE_MID
        res.lines[pid] = _fmt(_pick(rng, pool, recent), _ctx(res, row))
    return res


# ---------------------------------------------------------------------- glue


def analyze(
    record: MatchRecord,
    *,
    team_index: int,
    tracked: set[str],
    leetify: dict[str, float] | None,
    notes: list[str] | None = None,
) -> PostmortemResult:
    team = record.teams[team_index]
    lines = build_lines(record, team_index, tracked, leetify)
    cats = build_categories(lines)
    rows = score_team(lines, cats)
    res = PostmortemResult(
        match_id=record.match_id,
        map_label=record.map_label,
        score=f"{team.score} – {max((t.score for i, t in enumerate(record.teams) if i != team_index), default=0)}",
        won=team.win,
        rounds=record.rounds or sum(t.score for t in record.teams),
        team_name=team.name,
        finished_at=record.finished_at,
        rows=rows,
        categories=cats,
        tracked_pids=[r.pid for r in rows if r.line.tracked],
        notes=list(notes or []),
        leetify_used=any(ln.leetify is not None for ln in lines),
    )
    for i, other in enumerate(record.teams):
        if i == team_index:
            continue
        others = [row for row in other.players if row.pid in tracked]
        if others:
            o_lines = build_lines(record, i, tracked, leetify)
            o_rows = score_team(o_lines, build_categories(o_lines))
            res.other_team = [(r.nick, r.rank) for r in o_rows if r.line.tracked]
    if leetify is not None and not res.leetify_used and any(ln.steam_id for ln in lines):
        res.notes.append("Leetify hasn't processed this match yet — Impact uses FaceIT impact + MVPs only.")
    if not any(ln.extended for ln in lines):
        res.notes.append("Extended FaceIT stats missing for this match — Opening, Clutch and Utility skipped.")
    res.callouts = find_callouts(res)
    return res
