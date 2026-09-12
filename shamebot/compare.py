"""Head-to-head verdict engine: weighted categories over FaceIT + Leetify metrics.

Pure functions over plain inputs — no network, so it is fully unit-testable. The orchestration
(fetching aggregates, Leetify profiles, per-match Leetify ratings) lives in ``App.compare``.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from .leetify import LeetifyProfile, fmt_rating
from .stats import PlayerAggregate

Fmt = Callable[[float], str]


def _pct(v: float) -> str:
    return f"{v:.0f}%"


def _f2(v: float) -> str:
    return f"{v:.2f}"


def _f1(v: float) -> str:
    return f"{v:.1f}"


def _int(v: float) -> str:
    return f"{v:.0f}"


def _ms(v: float) -> str:
    return f"{v:.0f} ms"


def _leet(v: float) -> str:
    return fmt_rating(v)


@dataclass
class Metric:
    label: str
    a: float | None
    b: float | None
    fmt: Fmt = _f2
    higher_better: bool = True
    source: str = "faceit"  # "faceit" | "leetify"

    @property
    def usable(self) -> bool:
        return self.a is not None and self.b is not None

    @property
    def rel(self) -> float | None:
        """Relative difference in favour of A, clamped to [-1, 1]. None when a side is missing."""
        if not self.usable:
            return None
        base = max(abs(self.a), abs(self.b), 1e-9)  # type: ignore[arg-type]
        d = (self.a - self.b) / base  # type: ignore[operator]
        if not self.higher_better:
            d = -d
        return max(-1.0, min(1.0, d))

    @property
    def better(self) -> int:
        """-1 if A is better, 1 if B, 0 tie/unknown."""
        r = self.rel
        if r is None or abs(r) < 0.02:
            return 0
        return -1 if r > 0 else 1

    def text(self, side: str) -> str:
        v = self.a if side == "a" else self.b
        return "—" if v is None else self.fmt(v)


@dataclass
class Category:
    name: str
    weight: int
    metrics: list[Metric]
    emoji: str = ""

    @property
    def score(self) -> float | None:
        rels = [m.rel for m in self.metrics if m.rel is not None]
        return sum(rels) / len(rels) if rels else None

    @property
    def winner(self) -> int:
        s = self.score
        if s is None or abs(s) < 0.05:
            return 0
        return -1 if s > 0 else 1

    @property
    def decided(self) -> bool:
        return self.winner != 0


@dataclass
class CompareResult:
    a_name: str
    b_name: str
    categories: list[Category]
    points_a: int = 0
    points_b: int = 0
    winner: int = 0  # -1 A, 1 B, 0 draw
    margin: float = 0.0
    margin_label: str = "coin-flip"
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    verdict: str = ""
    jab: str = ""

    @property
    def winner_name(self) -> str:
        return self.a_name if self.winner == -1 else self.b_name

    @property
    def loser_name(self) -> str:
        return self.b_name if self.winner == -1 else self.a_name

    @property
    def total_weight(self) -> int:
        return sum(c.weight for c in self.categories)


# ------------------------------------------------------------------ categories


def _window_leetify(ratings: list[float] | None) -> float | None:
    """Average of per-match Leetify ratings (fractions) in displayed units, needs >= 3 games."""
    if not ratings or len(ratings) < 3:
        return None
    return round(100 * sum(ratings) / len(ratings), 2)


def build_categories(
    a: PlayerAggregate,
    b: PlayerAggregate,
    *,
    elo_a: int | None,
    elo_b: int | None,
    leet_a: LeetifyProfile | None,
    leet_b: LeetifyProfile | None,
    window_leet_a: list[float] | None = None,
    window_leet_b: list[float] | None = None,
) -> list[Category]:
    la = leet_a or LeetifyProfile("", "")
    lb = leet_b or LeetifyProfile("", "")
    wa, wb = _window_leetify(window_leet_a), _window_leetify(window_leet_b)
    impact_leet = Metric("Leetify rating (window)", wa, wb, _leet, source="leetify")
    if not impact_leet.usable:
        impact_leet = Metric("Leetify rating", la.rating, lb.rating, _leet, source="leetify")

    return [
        Category("Impact", 3, [
            impact_leet,
            Metric("FaceIT ELO", elo_a, elo_b, _int),
            Metric("Win rate", a.win_rate, b.win_rate, _pct),
        ], "💥"),
        Category("Fragging", 2, [
            Metric("K/D", a.avg_kd, b.avg_kd, _f2),
            Metric("Kills / round", a.kpr, b.kpr, _f2),
            Metric("ADR", a.avg_adr, b.avg_adr, _int),
        ], "🔫"),
        Category("Opening", 2, [
            Metric("Entry success", a.entry_success_pct, b.entry_success_pct, _pct),
            Metric("First kills / round", a.first_kills_pr, b.first_kills_pr, _f2),
            Metric("Opening duels (Leetify)", la.opening_duel_pct, lb.opening_duel_pct, _pct, source="leetify"),
        ], "🚪"),
        Category("Aim", 2, [
            Metric("HS%", a.avg_hs, b.avg_hs, _pct),
            Metric("Aim (Leetify)", la.aim, lb.aim, _int, source="leetify"),
            Metric("Spray accuracy (Leetify)", la.spray_accuracy, lb.spray_accuracy, _pct, source="leetify"),
            Metric("Reaction time (Leetify)", la.reaction_time_ms, lb.reaction_time_ms, _ms, higher_better=False, source="leetify"),
            Metric("Preaim (Leetify)", la.preaim, lb.preaim, _f1, higher_better=False, source="leetify"),
        ], "🎯"),
        Category("Clutch", 1, [
            Metric("1v1 + 1v2 won", a.clutch_pct, b.clutch_pct, _pct),
            Metric("Clutch (Leetify)", la.clutch, lb.clutch, _f2, source="leetify"),
        ], "🧊"),
        Category("Utility", 1, [
            Metric("Utility dmg / round", a.util_dmg_pr, b.util_dmg_pr, _f1),
            Metric("Enemies flashed / round", a.flashed_pr, b.flashed_pr, _f2),
            Metric("Utility (Leetify)", la.utility, lb.utility, _int, source="leetify"),
        ], "💣"),
        Category("Teamplay", 1, [
            Metric("Trade kills (Leetify)", la.trade_kill_pct, lb.trade_kill_pct, _pct, source="leetify"),
            Metric("Deaths traded (Leetify)", la.traded_death_pct, lb.traded_death_pct, _pct, source="leetify"),
            Metric("Positioning (Leetify)", la.positioning, lb.positioning, _int, source="leetify"),
        ], "🤝"),
        Category("Consistency", 1, [
            Metric("Shame rate", float(a.shame_rate), float(b.shame_rate), _pct, higher_better=False),
            Metric("Kills std-dev", a.kills_std, b.kills_std, _f1, higher_better=False),
            Metric("Longest shame streak", float(a.longest_shame_streak), float(b.longest_shame_streak), _int, higher_better=False),
        ], "📈"),
    ]


# --------------------------------------------------------------------- scoring


def margin_label(margin: float) -> str:
    if margin >= 0.6:
        return "dominant"
    if margin >= 0.3:
        return "clear"
    if margin >= 0.1:
        return "narrow"
    return "coin-flip"


def score(a_name: str, b_name: str, categories: list[Category], *, notes: list[str] | None = None) -> CompareResult:
    res = CompareResult(a_name, b_name, categories, notes=list(notes or []))
    total = 0
    for c in categories:
        if c.winner == -1:
            res.points_a += c.weight
            total -= c.weight
        elif c.winner == 1:
            res.points_b += c.weight
            total += c.weight
    res.winner = 0 if total == 0 else (-1 if total < 0 else 1)
    res.margin = abs(total) / max(res.total_weight, 1)
    res.margin_label = margin_label(res.margin) if res.winner else "coin-flip"

    won = [c for c in categories if c.winner == res.winner and res.winner]
    won.sort(key=lambda c: c.weight * abs(c.score or 0), reverse=True)
    for c in won[:3]:
        best = max((m for m in c.metrics if m.rel is not None and m.better == res.winner), key=lambda m: abs(m.rel or 0), default=None)
        if best:
            wa, wb = (best.text("a"), best.text("b")) if res.winner == -1 else (best.text("b"), best.text("a"))
            res.reasons.append(f"{c.name}: {best.label} {wa} vs {wb}")
        else:
            res.reasons.append(c.name)
    return res


# ----------------------------------------------------------------------- text

VERDICT = {
    "dominant": [
        "**{w}** is the better player, and it isn't close — {pa}–{pb}. {l} was invited to make it look like a comparison.",
        "**{w}** wins {pa}–{pb}. Calling this a rivalry would be generous to {l}.",
        "**{w}** dominates {pa}–{pb}. {l} takes the categories nobody screenshots.",
    ],
    "clear": [
        "**{w}** is the better player right now — clear win, {pa}–{pb}. {l} keeps {lc} as a consolation prize.",
        "**{w}** takes it {pa}–{pb}. {l} has {lc}, which is a nice way of saying 'not the important ones'.",
        "**{w}** by a clear margin ({pa}–{pb}). {l} wins {lc}; everything with kills in it goes the other way.",
    ],
    "narrow": [
        "**{w}** edges it {pa}–{pb}. Close enough that {l} will demand a recount.",
        "**{w}** wins on points, {pa}–{pb}. {l} loses by exactly one good night.",
        "**{w}** narrowly — {pa}–{pb}. {l} takes {lc}, {w} takes {wc}. One bad Dust2 flips this.",
    ],
    "coin-flip": [
        "Dead even — {pa}–{pb}. Neither of you is the problem, which means both of you are.",
        "Coin-flip, {pa}–{pb}. Two players, one skill level, zero bragging rights.",
        "Tie at {pa}–{pb}. Settle it the traditional way: blame the fifth.",
    ],
}

JABS = [
    "Wall record does not lie: {l} has been on it {ls}% of the time.",
    "{l}'s best argument is 'the sample size'. It isn't.",
    "{l} would like it noted that {lc} is very important, actually.",
    "For {l}, ELO is a number and numbers can change. Just not lately.",
    "{l}: still better than the excuses button suggests.",
]


def _join(names: list[str]) -> str:
    if not names:
        return "nothing"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def write_verdict(res: CompareResult, *, seed: str, shame_rate_loser: int | None = None) -> CompareResult:
    rng = random.Random(f"verdict|{seed}")
    w, l = res.winner_name, res.loser_name
    wc = _join([c.name for c in res.categories if c.winner == res.winner and res.winner])
    lc = _join([c.name for c in res.categories if c.winner == -res.winner and res.winner])
    pa, pb = (res.points_a, res.points_b) if res.winner != 1 else (res.points_b, res.points_a)
    if res.winner == 0:
        pa, pb = res.points_a, res.points_b
        w, l = res.a_name, res.b_name
    line = rng.choice(VERDICT[res.margin_label])
    res.verdict = line.format(w=w, l=l, pa=pa, pb=pb, wc=wc, lc=lc)
    if res.winner:
        jab = rng.choice(JABS)
        res.jab = jab.format(l=l, lc=lc, ls=shame_rate_loser if shame_rate_loser is not None else "?")
    return res
