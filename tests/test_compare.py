"""Verdict engine, Leetify payload mapping and the compare view — all offline with synthetic data."""
from __future__ import annotations

import json

import discord

from shamebot.compare import Category, Metric, build_categories, margin_label, score, write_verdict
from shamebot.leetify import LeetifyProfile, fmt_rating, parse_match_ratings, parse_profile, profile_url
from shamebot.stats import PlayerAggregate
from shamebot.views import compare_view


def _agg(name: str, **kw) -> PlayerAggregate:
    base = dict(games=30, shame_count=6, shame_rate=20, win_rate=50, avg_kd=1.0, avg_adr=75.0, avg_hs=45.0,
                longest_shame_streak=2, kills_std=5.0, extended_games=30, kpr=0.7, entry_success_pct=50,
                first_kills_pr=0.1, clutch_pct=35, util_dmg_pr=6.0, flashed_pr=0.3)
    base.update(kw)
    return PlayerAggregate(name.lower(), name, **base)


def _leet(name: str, **kw) -> LeetifyProfile:
    base = dict(rating=0.5, aim=60.0, positioning=55.0, utility=50.0, clutch=0.05, opening=0.01, preaim=12.0,
                reaction_time_ms=600.0, spray_accuracy=40.0, opening_duel_ct_pct=50.0, opening_duel_t_pct=48.0,
                trade_kill_pct=45.0, traded_death_pct=60.0)
    base.update(kw)
    return LeetifyProfile("7656119800000000" + ("1" if name == "A" else "2"), name, **base)


# ------------------------------------------------------------------ metrics


def test_metric_relative_difference_and_direction():
    assert Metric("x", 10, 5).rel == 0.5 and Metric("x", 10, 5).better == -1
    assert Metric("x", 5, 10).better == 1
    assert Metric("x", 10, 5, higher_better=False).better == 1  # lower is better
    assert Metric("x", None, 5).rel is None and Metric("x", None, 5).better == 0
    assert Metric("x", 100, 101).better == 0  # within 2% -> tie
    assert Metric("x", -3, 3).rel == -1.0  # clamped


def test_category_tie_threshold_and_missing_metrics():
    c = Category("C", 2, [Metric("a", 1, 1.03), Metric("b", None, 5)])
    assert c.score is not None and abs(c.score) < 0.05 and c.winner == 0
    assert Category("C", 1, [Metric("a", None, None)]).score is None


# ------------------------------------------------------------------- scoring


def test_dominant_winner_and_reasons():
    a = _agg("A", win_rate=60, avg_kd=1.3, avg_adr=90.0, avg_hs=55.0, kpr=0.9, entry_success_pct=60, clutch_pct=50, util_dmg_pr=9.0, shame_rate=5, kills_std=3.0, longest_shame_streak=1)
    b = _agg("B", win_rate=35, avg_kd=0.7, avg_adr=55.0, avg_hs=30.0, kpr=0.5, entry_success_pct=35, clutch_pct=20, util_dmg_pr=3.0, shame_rate=40, kills_std=8.0, longest_shame_streak=5)
    cats = build_categories(a, b, elo_a=1500, elo_b=1100, leet_a=_leet("A", rating=2.0, aim=80), leet_b=_leet("B", rating=-2.0, aim=30, trade_kill_pct=30.0, traded_death_pct=40.0, positioning=40.0))
    res = score("A", "B", cats)
    assert res.winner == -1 and res.winner_name == "A" and res.loser_name == "B"
    assert res.points_a == 13 and res.points_b == 0
    assert res.margin == 1.0 and res.margin_label == "dominant"
    assert len(res.reasons) == 3 and res.reasons[0].startswith("Impact:")
    res = write_verdict(res, seed="s", shame_rate_loser=40)
    assert "**A**" in res.verdict and "13" in res.verdict and "B" in res.verdict
    assert res.jab and "{" not in res.jab
    assert write_verdict(score("A", "B", cats), seed="s").verdict == res.verdict  # deterministic


def test_mirror_gives_b_and_symmetric_points():
    a = _agg("A", avg_kd=0.7, win_rate=35)
    b = _agg("B", avg_kd=1.3, win_rate=60)
    res = score("A", "B", build_categories(a, b, elo_a=1000, elo_b=1400, leet_a=None, leet_b=None))
    assert res.winner == 1 and res.winner_name == "B"
    flipped = score("B", "A", build_categories(b, a, elo_a=1400, elo_b=1000, leet_a=None, leet_b=None))
    assert flipped.winner == -1 and (flipped.points_a, flipped.points_b) == (res.points_b, res.points_a)


def test_identical_players_are_a_coin_flip():
    a = _agg("A")
    res = write_verdict(score("A", "B", build_categories(a, _agg("B"), elo_a=1300, elo_b=1300, leet_a=None, leet_b=None)), seed="x")
    assert res.winner == 0 and res.points_a == res.points_b == 0
    assert res.margin_label == "coin-flip" and res.jab == "" and "0–0" in res.verdict


def test_missing_leetify_side_skips_only_leetify_metrics():
    a, b = _agg("A"), _agg("B")
    cats = build_categories(a, b, elo_a=1300, elo_b=1300, leet_a=_leet("A"), leet_b=None)
    aim = next(c for c in cats if c.name == "Aim")
    assert [m.label for m in aim.metrics if m.usable] == ["HS%"]
    teamplay = next(c for c in cats if c.name == "Teamplay")
    assert teamplay.score is None and teamplay.winner == 0
    res = score("A", "B", cats, notes=["No Leetify profile for B"])
    assert res.notes == ["No Leetify profile for B"]


def test_window_leetify_rating_preferred_over_profile():
    a, b = _agg("A"), _agg("B")
    cats = build_categories(a, b, elo_a=1, elo_b=1, leet_a=_leet("A", rating=5.0), leet_b=_leet("B", rating=-5.0),
                            window_leet_a=[0.01, 0.02, 0.03], window_leet_b=[-0.01, -0.02, -0.03])
    m = cats[0].metrics[0]
    assert m.label == "Leetify rating (window)" and m.a == 2.0 and m.b == -2.0 and m.text("a") == "+2.00"
    cats = build_categories(a, b, elo_a=1, elo_b=1, leet_a=_leet("A", rating=5.0), leet_b=_leet("B", rating=-5.0), window_leet_a=[0.01], window_leet_b=None)
    assert cats[0].metrics[0].label == "Leetify rating" and cats[0].metrics[0].a == 5.0


def test_margin_labels():
    assert margin_label(0.61) == "dominant" and margin_label(0.3) == "clear" and margin_label(0.15) == "narrow" and margin_label(0.05) == "coin-flip"


# ------------------------------------------------------------------- leetify

SYNTHETIC_PROFILE = {
    "privacy_mode": "public", "winrate": 0.43, "total_matches": 100, "name": "A", "steam64_id": "76561198000000001",
    "ranks": {"leetify": 0.06, "faceit": 6, "faceit_elo": 1500},
    "rating": {"aim": 67.4, "positioning": 60.3, "utility": 42.1, "clutch": 0.089, "opening": 0.019, "ct_leetify": 0.01, "t_leetify": -0.01},
    "stats": {"preaim": 11.3, "reaction_time_ms": 648.2, "spray_accuracy": 40.5, "counter_strafing_good_shots_ratio": 85.1,
              "ct_opening_duel_success_percentage": 53.7, "t_opening_duel_success_percentage": 44.0,
              "trade_kills_success_percentage": 44.2, "traded_deaths_success_percentage": 61.5, "flashbang_leading_to_kill": 3.6, "he_foes_damage_avg": 14.4},
    "recent_matches": [{"leetify_rating": 0.0016, "data_source": "faceit"}, {"leetify_rating": -0.02, "data_source": "matchmaking"}],
}
SYNTHETIC_MATCH = {"id": "x", "data_source": "faceit", "stats": [
    {"steam64_id": "76561198000000001", "name": "A", "leetify_rating": 0.0016},
    {"steam64_id": "76561198000000002", "name": "B", "leetify_rating": -0.033},
    {"steam64_id": "76561198000000003", "name": "C"},
]}


def test_parse_leetify_profile_and_match():
    p = parse_profile(SYNTHETIC_PROFILE)
    assert p.steam_id == "76561198000000001" and p.rating == 0.06 and p.aim == 67.4
    assert p.opening_duel_pct == 48.9 and p.reaction_time_ms == 648.2 and p.recent_ratings == [0.0016, -0.02]
    assert p.url == profile_url("76561198000000001") == "https://leetify.com/app/profile/76561198000000001"
    assert parse_match_ratings(SYNTHETIC_MATCH) == {"76561198000000001": 0.0016, "76561198000000002": -0.033}
    assert fmt_rating(0.06) == "+0.06" and fmt_rating(-1.9) == "-1.90" and fmt_rating(0.0016, fraction=True) == "+0.16" and fmt_rating(None) == "—"


def test_compare_view_serialises():
    a, b = _agg("A"), _agg("B", avg_kd=0.5)
    res = write_verdict(score("A", "B", build_categories(a, b, elo_a=1400, elo_b=1000, leet_a=_leet("A"), leet_b=None), notes=["note"]), seed="v")
    view, files = compare_view(res, b"png", scope_label="Last 30", links=[("A on FaceIT", "https://faceit.com/a"), ("A on Leetify", "https://leetify.com/app/profile/1")])
    payload = view.to_components()
    assert files[0].filename == "compare.png"
    text = json.dumps(payload)
    assert "Data provided by Leetify" in text and "note" in text and res.verdict.split("**")[1] in text
    row = next(c for c in payload[0]["components"] if c["type"] == discord.ComponentType.action_row.value)
    assert [b["url"] for b in row["components"]] == ["https://faceit.com/a", "https://leetify.com/app/profile/1"]
