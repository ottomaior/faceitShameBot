"""Render sample cards from tests/fixtures into out/preview/*.png (no Discord, no API key needed).

    python tools/preview.py

Map/avatar images are downloaded once into tests/fixtures/images/ and reused offline afterwards.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shamebot.models import MatchRecord, parse_match  # noqa: E402
from shamebot.render import theme as T  # noqa: E402
from shamebot.render.shame_card import HeroPlayer, render_match_card  # noqa: E402
from shamebot.roasts import RoastContext, RoastEngine  # noqa: E402
from shamebot.rules import Thresholds, detect  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
IMG = FIX / "images"
OUT = ROOT / "out" / "preview"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def image_bytes(url: str | None) -> bytes | None:
    """Fetch once, then serve from tests/fixtures/images."""
    if not url:
        return None
    IMG.mkdir(parents=True, exist_ok=True)
    path = IMG / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".img")
    if path.exists():
        return path.read_bytes()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (faceit-shame-bot preview)"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
    except Exception as exc:  # noqa: BLE001
        print("  (image fetch failed:", exc, ")")
        return None
    path.write_bytes(data)
    return data


def roster_avatars(details: dict) -> dict[str, str]:
    out = {}
    for fac in details.get("teams", {}).values():
        for m in fac.get("roster", []):
            if m.get("avatar"):
                out[m["player_id"]] = m["avatar"]
    return out


def build_record(suffix: str, tracked: dict[str, str]) -> tuple[MatchRecord, dict]:
    stats = load(f"match_stats{suffix}.json")
    details = load(f"match_details{suffix}.json")
    rec = parse_match(details["match_id"], stats, details, tracked, kill_threshold=10)
    assert rec is not None
    return rec, details


def hero_for(rec: MatchRecord, pid: str, details: dict, elos: dict[str, int], *, line: str, elo_delta: int | None, form: list[str], record_line: str) -> HeroPlayer:
    r = rec.players[pid]
    level = next((row.level for t in rec.teams for row in t.players if row.pid == pid), None)
    return HeroPlayer(
        pid=pid,
        result=r,
        avatar=image_bytes(roster_avatars(details).get(pid)),
        level=level,
        elo=elos.get(pid),
        elo_delta=elo_delta,
        line=line,
        form=form,
        record_line=record_line,
        avg_kd=0.92,
        avg_adr=68.0,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tracked_list = load("tracked_players.json")
    tracked = {p["player_id"]: p["nickname"] for p in tracked_list}
    elos = {p["player_id"]: p["faceit_elo"] for p in tracked_list}
    engine = RoastEngine(custom_file=str(ROOT / "roasts_custom.txt"))
    footer = "BRWNr Bot Stats"

    # 1) the screenshot match: two teammates under 10 -> double feature
    rec, details = build_record("_shame", tracked)
    dets = detect(rec, streaks_before={pid: 1 for pid in rec.players}, thresholds=Thresholds())
    shamed = [d for d in dets if d.kind.value == "shame"][0].player_ids
    heroes = []
    for pid in shamed:
        ctx = RoastContext.from_match(rec, rec.players[pid], streak=2)
        heroes.append(
            hero_for(rec, pid, details, elos, line=engine.roast(ctx, seed=rec.match_id), elo_delta=-24,
                     form=["S", "S", "C", "C", "S", "C", "G", "C", "S", "C"],
                     record_line="8/30 on the wall (27%) · streak 2 · Regular")
        )
    png = render_match_card(rec, heroes, T.SHAME, map_bytes=image_bytes(rec.map_image), tracked=set(tracked), headline="WALL OF SHAME — DOUBLE FEATURE", footer=footer)
    (OUT / "1_shame_double.png").write_bytes(png)

    # 2) single shame: the 1-kill game (only BRNWr as hero)
    rec, details = build_record("_onekill", tracked)
    detect(rec, streaks_before={pid: 2 for pid in rec.players}, thresholds=Thresholds())
    pid = next(p for p, r in rec.players.items() if r.nickname == "BRNWr")
    ctx = RoastContext.from_match(rec, rec.players[pid], streak=3)
    hero = hero_for(rec, pid, details, elos, line=engine.roast(ctx, seed=rec.match_id), elo_delta=-27,
                    form=["S", "S", "S", "C", "S", "C", "C", "C", "S", "C"],
                    record_line="11/30 on the wall (37%) · streak 3 · Heater (reverse)")
    png = render_match_card(rec, [hero], T.SHAME, map_bytes=image_bytes(rec.map_image), tracked=set(tracked), footer=footer)
    (OUT / "2_shame_single.png").write_bytes(png)

    # 3) redemption + 4) glory + 5) neutral, from the 14-kill game with tweaked numbers
    rec, details = build_record("", tracked)
    pid = next(p for p, r in rec.players.items() if r.nickname == "BRNWr")
    r = rec.players[pid]
    r.kills, r.deaths, r.kd, r.adr, r.mvps, r.hs_pct = 27, 14, 1.93, 96.4, 4, 52
    for t in rec.teams:
        for row in t.players:
            if row.pid == pid:
                row.k, row.d, row.kd, row.adr, row.mvp, row.hs = 27, 14, 1.93, 96.4, 4, 52
    ctx = RoastContext.from_match(rec, r, streak=3)
    hero = hero_for(rec, pid, details, elos, line=engine.redemption(ctx, seed=rec.match_id), elo_delta=+21,
                    form=["G", "S", "S", "S", "C", "C", "S", "C", "C", "C"],
                    record_line="Streak broken after 3 · 9/30 on the wall (30%)")
    png = render_match_card(rec, [hero], T.REDEMPTION, map_bytes=image_bytes(rec.map_image), tracked=set(tracked), footer=footer)
    (OUT / "3_redemption.png").write_bytes(png)

    r.kills, r.penta = 33, 1
    ctx = RoastContext.from_match(rec, r)
    hero = hero_for(rec, pid, details, elos, line=engine.glory(ctx, seed=rec.match_id, ace=True), elo_delta=+25,
                    form=["G", "C", "C", "C", "S", "C", "C", "C", "C", "C"],
                    record_line="Best game: 33 K · 2/30 on the wall (7%) · Saint")
    png = render_match_card(rec, [hero], T.GLORY, map_bytes=image_bytes(rec.map_image), tracked=set(tracked), headline="HIGHLIGHT — ACE", footer=footer)
    (OUT / "4_glory.png").write_bytes(png)

    r.kills, r.penta, r.deaths, r.kd, r.adr, r.mvps = 14, 0, 18, 0.78, 69.0, 0
    hero = hero_for(rec, pid, details, elos, line="", elo_delta=-19,
                    form=["C", "S", "C", "C", "S", "C", "C", "C", "S", "C"],
                    record_line="Last match · 9/30 on the wall (30%)")
    png = render_match_card(rec, [hero], T.NEUTRAL, map_bytes=image_bytes(rec.map_image), tracked=set(tracked), footer=footer)
    (OUT / "5_last_neutral.png").write_bytes(png)

    for p in sorted(OUT.glob("*.png")):
        print(p.relative_to(ROOT), f"{p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()


def preview_boards() -> None:
    """Leaderboard + profile previews with synthetic aggregates (layout check only)."""
    from shamebot.render.leaderboard_card import LeaderRow, render_leaderboard
    from shamebot.render.profile_card import ProfileData, render_profile
    from shamebot.stats import MapStat, PlayerAggregate

    tracked_list = load("tracked_players.json")
    fake = [(8, 30, "Regular", 1, 3), (11, 30, "Heater (reverse)", 3, 1), (4, 30, "Regular", 0, 6), (2, 30, "Slump", 0, 7), (0, 30, "Saint", 0, 12)]
    rows = []
    for p, (sc, gw, title, streak, worst) in zip(tracked_list, fake):
        details = [f"worst {worst} K"] + ([f"streak {streak}"] if streak else [])
        rows.append(LeaderRow(p["nickname"], image_bytes(p["avatar"]), p["skill_level"], f"{sc}/{gw}", "on the wall", sc / gw, f"{round(100*sc/gw)}%", title, details))
    rows.sort(key=lambda r: r.ratio, reverse=True)
    png = render_leaderboard(rows, T.SHAME, headline="WALL OF SHAME — LEADERBOARD", subtitle="Last 30 games per player", footer="BRWNr Bot Stats",
                             extra_lines=["Bot of the week: mindtech (4 shames)", "Biggest ELO loss: BRNWr (−71)"])
    (OUT / "6_leaderboard.png").write_bytes(png)

    p = tracked_list[0]
    recent = PlayerAggregate(p["player_id"], p["nickname"], games=30, shame_count=8, shame_rate=27, clean_games=22, clean_rate=73, current_shame_streak=2, longest_shame_streak=3,
                             avg_kills=14.2, worst_kills=1, best_kills=29, title="Regular", glory_title="Solid", wins=13, losses=17, win_rate=43, avg_deaths=16.1, avg_adr=68.4, avg_kd=0.92, avg_hs=46.0,
                             form=["S", "S", "C", "C", "S", "C", "G", "C", "S", "C"], form_wl=["L", "L", "W", "W", "L", "W", "W", "L", "L", "W"])
    alltime = PlayerAggregate(p["player_id"], p["nickname"], games=612, shame_count=140, shame_rate=23, clean_rate=77, longest_shame_streak=5, best_kills=34, worst_kills=1, glory_title="Fragger")
    maps = [MapStat("de_mirage", 140, 70, 70, 30, [15]*140), MapStat("de_dust2", 120, 50, 70, 35, [13]*120), MapStat("de_ancient", 90, 48, 42, 20, [16]*90), MapStat("de_nuke", 60, 25, 35, 18, [12]*60)]
    hist = [1180, 1195, 1210, 1188, 1230, 1245, 1260, 1240, 1275, 1290, 1310, 1295, 1280, 1300, 1295]
    data = ProfileData(p["nickname"], image_bytes(p["avatar"]), p["skill_level"], p["faceit_elo"], p["country"], -18, +42, hist, recent, alltime, maps)
    (OUT / "7_profile.png").write_bytes(render_profile(data, footer="BRWNr Bot Stats"))


def preview_compare() -> None:
    from shamebot.compare import build_categories, score, write_verdict
    from shamebot.leetify import LeetifyProfile
    from shamebot.render.compare_card import CompareSide, render_compare
    from shamebot.stats import PlayerAggregate

    tracked_list = load("tracked_players.json")
    pa, pb = tracked_list[0], tracked_list[1]
    a = PlayerAggregate(pa["player_id"], pa["nickname"], games=30, shame_count=6, shame_rate=20, win_rate=47, avg_kd=0.96, avg_adr=74.1, avg_hs=48.0, longest_shame_streak=2, kills_std=5.1,
                        extended_games=30, kpr=0.68, entry_success_pct=52, first_kills_pr=0.12, clutch_pct=33, util_dmg_pr=5.2, flashed_pr=0.31, title="Slump")
    b = PlayerAggregate(pb["player_id"], pb["nickname"], games=30, shame_count=9, shame_rate=30, win_rate=40, avg_kd=0.79, avg_adr=66.0, avg_hs=41.0, longest_shame_streak=4, kills_std=6.4,
                        extended_games=30, kpr=0.58, entry_success_pct=44, first_kills_pr=0.09, clutch_pct=40, util_dmg_pr=7.9, flashed_pr=0.44, title="Regular")
    la = LeetifyProfile(pa["player_id"], pa["nickname"], rating=0.06, aim=67.4, positioning=60.3, utility=42.1, clutch=0.09, opening=0.02, preaim=11.3, reaction_time_ms=648, spray_accuracy=40.5, opening_duel_ct_pct=53.7, opening_duel_t_pct=44.0, trade_kill_pct=44.2, traded_death_pct=61.5)
    lb = LeetifyProfile(pb["player_id"], pb["nickname"], rating=-1.9, aim=27.8, positioning=48.0, utility=62.5, clutch=0.05, opening=-0.03, preaim=14.9, reaction_time_ms=702, spray_accuracy=31.2, opening_duel_ct_pct=45.1, opening_duel_t_pct=40.3, trade_kill_pct=47.0, traded_death_pct=58.2)
    cats = build_categories(a, b, elo_a=pa["faceit_elo"], elo_b=pb["faceit_elo"], leet_a=la, leet_b=lb, window_leet_a=[0.01, 0.02, -0.005, 0.03], window_leet_b=[-0.02, -0.01, -0.03, 0.0])
    res = write_verdict(score(a.nickname, b.nickname, cats), seed="preview", shame_rate_loser=30)
    print("verdict:", res.verdict, "|", res.jab)
    sides = [CompareSide(a.nickname, image_bytes(pa["avatar"]), pa["skill_level"], pa["faceit_elo"], a.title, True), CompareSide(b.nickname, image_bytes(pb["avatar"]), pb["skill_level"], pb["faceit_elo"], b.title, True)]
    (OUT / "8_compare.png").write_bytes(render_compare(sides[0], sides[1], res, subtitle="Last 30 games per player", footer="BRWNr Bot Stats"))


if __name__ == "__main__":
    preview_boards()
    preview_compare()
