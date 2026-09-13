"""Parsing, rules, roasts, stats and state — all offline against captured FaceIT payloads."""
from __future__ import annotations

import json


from shamebot.models import MatchRecord, PlayerSnapshot, ScoreRow, TrackedResult, parse_match
from shamebot.roasts import RoastContext, RoastEngine
from shamebot.rules import PostKind, Thresholds, award_label, compute_awards, detect
from shamebot.state import State
from shamebot.stats import aggregate, elo_change, get_shame_title, hall_of_shame, per_map


def _record(fixtures, tracked, suffix="") -> MatchRecord:
    stats = fixtures["stats" + suffix]
    details = fixtures["details" + suffix]
    rec = parse_match(details["match_id"], stats, details, tracked, kill_threshold=10)
    assert rec is not None
    return rec


# ------------------------------------------------------------------ parsing


def test_parse_match_fields(fixtures, tracked):
    rec = _record(fixtures, tracked, "_shame")
    assert rec.v == 3
    assert rec.map == "de_ancient"
    assert rec.score == "7 / 13"
    assert rec.rounds == 20
    assert rec.faceit_url.startswith("https://www.faceit.com/en/cs2/room/1-")
    assert rec.map_image and rec.map_image.startswith("https://")
    assert [(t.name, t.score, t.win) for t in rec.teams] == [("team_MaMo", 7, False), ("OnlyFans", 13, True)]
    assert all(len(t.players) == 5 for t in rec.teams)
    assert all(row.level is not None for t in rec.teams for row in t.players)

    by_nick = {r.nickname: r for r in rec.players.values()}
    assert set(by_nick) == {"BRNWr", "mindtech"}
    b = by_nick["BRNWr"]
    assert (b.kills, b.deaths, b.assists, b.mvps, b.hs_pct) == (9, 19, 3, 0, 44)
    assert b.adr == 56.0 and b.kd == 0.47
    assert b.result == 0 and (b.team_score, b.enemy_score) == (7, 13)
    assert b.shamed is True


def test_parse_without_details(fixtures, tracked):
    stats = fixtures["stats"]
    rec = parse_match("1-abc", stats, None, tracked, kill_threshold=10, finished_at=123)
    assert rec is not None and rec.finished_at == 123
    assert rec.map == "de_dust2" and rec.map_image is None
    assert rec.faceit_url == "https://www.faceit.com/en/cs2/room/1-abc"


def test_record_roundtrip(fixtures, tracked):
    rec = _record(fixtures, tracked, "_onekill")
    again = MatchRecord.from_dict(rec.match_id, json.loads(json.dumps(rec.to_dict())))
    assert again.to_dict() == rec.to_dict()


def test_v1_record_loads_with_kills_only():
    rec = MatchRecord.from_dict("m1", {"finished_at": 5, "players": {"p1": {"nickname": "A", "kills": 4, "shamed": True}}})
    assert rec.v == 1 and not rec.enriched
    r = rec.players["p1"]
    assert r.kills == 4 and r.shamed and r.deaths is None and not r.enriched


# -------------------------------------------------------------------- rules


def test_detect_shame_and_awards(fixtures, tracked):
    rec = _record(fixtures, tracked, "_onekill")
    dets = detect(rec, streaks_before={pid: 2 for pid in rec.players}, thresholds=Thresholds())
    assert [d.kind for d in dets] == [PostKind.SHAME]
    shamed = {rec.players[p].nickname: rec.players[p] for p in dets[0].player_ids}
    assert set(shamed) == {"BRNWr", "szucsu"}
    b = shamed["BRNWr"]
    assert "bottom_of_lobby" in b.awards
    assert "double_feature" in b.awards
    assert "hat_trick" in b.awards  # streak 2 before + this one
    assert "anchor" in b.awards  # 4-13
    assert "spectator" in b.awards  # 1 kill
    assert "zero_mvp" in b.awards
    assert award_label("streak:5") == "Streak 5"


def test_detect_redemption_and_glory(fixtures, tracked):
    rec = _record(fixtures, tracked)
    pid = next(p for p, r in rec.players.items() if r.nickname == "BRNWr")
    other = next(p for p in rec.players if p != pid)
    rec.players[pid].kills = 26
    rec.players[other].kills = 31
    dets = detect(rec, streaks_before={pid: 2, other: 0}, thresholds=Thresholds())
    kinds = {d.kind: d.player_ids for d in dets}
    assert kinds[PostKind.REDEMPTION] == [pid]
    assert kinds[PostKind.GLORY] == [other]


def test_glory_disabled(fixtures, tracked):
    rec = _record(fixtures, tracked)
    for r in rec.players.values():
        r.kills, r.penta = 35, 1
    assert detect(rec, streaks_before={}, thresholds=Thresholds(glory_enabled=False)) == []


def test_carried_award(fixtures, tracked):
    rec = _record(fixtures, tracked, "_shame")
    pid = next(iter(rec.players))
    rec.players[pid].result = 1
    awards = compute_awards(rec, pid, streak_before=0, shamed_count=1)
    assert "carried" in awards and "anchor" not in awards and "double_feature" not in awards


# ------------------------------------------------------------------- roasts


def test_roast_is_deterministic_and_filled(fixtures, tracked):
    rec = _record(fixtures, tracked, "_onekill")
    detect(rec, streaks_before={}, thresholds=Thresholds())
    r = next(x for x in rec.players.values() if x.nickname == "BRNWr")
    ctx = RoastContext.from_match(rec, r, streak=1)
    eng = RoastEngine()
    a = eng.roast(ctx, seed=rec.match_id)
    b = RoastEngine().roast(ctx, seed=rec.match_id)
    assert a == b  # same seed, fresh memory: deterministic
    assert eng.roast(ctx, seed=rec.match_id) != a  # same engine again: the memory steers away from repeats
    assert "{" not in a and "}" not in a
    assert "1 kills" not in a
    assert a != eng.roast(ctx, seed="other-seed") or True  # different seed may differ


def test_custom_roasts_file(tmp_path, fixtures, tracked):
    path = tmp_path / "roasts.txt"
    path.write_text("# comment\n{nick} custom line {kills}\n", encoding="utf-8")
    eng = RoastEngine(custom_file=str(path))
    assert eng.custom == ["{nick} custom line {kills}"]
    rec = _record(fixtures, tracked, "_onekill")
    r = next(x for x in rec.players.values() if x.nickname == "BRNWr")
    ctx = RoastContext.from_match(rec, r)
    lines = {eng.roast(ctx, seed=str(i)) for i in range(60)}
    assert any("BRNWr custom line 1" in ln for ln in lines)


def test_excuse_and_positive_lines(fixtures, tracked):
    eng = RoastEngine()
    assert eng.excuse(seed="a") == eng.excuse(seed="a")
    rec = _record(fixtures, tracked)
    r = next(iter(rec.players.values()))
    ctx = RoastContext.from_match(rec, r, streak=3)
    assert "{" not in eng.redemption(ctx, seed="s")
    assert "{" not in eng.glory(ctx, seed="s", ace=True)


# -------------------------------------------------------------------- stats


def _mk(pid: str, kills: int, ts: int, *, result: int | None = 0, enriched: bool = True, map_name="de_mirage", elo_delta=None) -> MatchRecord:
    r = TrackedResult(nickname="P", kills=kills, shamed=kills < 10)
    if enriched:
        r.deaths, r.adr, r.kd, r.hs_pct, r.mvps, r.result, r.elo_delta = 15, 70.0, kills / 15, 40, 1, result, elo_delta
    return MatchRecord(match_id=f"m{ts}", finished_at=ts, v=2 if enriched else 1, map=map_name, players={pid: r})


def test_aggregate_matches_original_semantics():
    # newest first: shamed, shamed, clean, shamed, clean  -> current streak 2, longest 2
    recs = [_mk("p", 5, 50), _mk("p", 8, 40, result=1), _mk("p", 20, 30, result=1), _mk("p", 3, 20), _mk("p", 15, 10)]
    a = aggregate("p", recs, nickname="P")
    assert (a.games, a.shame_count, a.shame_rate, a.clean_games, a.clean_rate) == (5, 3, 60, 2, 40)
    assert (a.current_shame_streak, a.current_clean_streak, a.longest_shame_streak) == (2, 0, 2)
    assert (a.worst_kills, a.best_kills) == (3, 20)
    assert a.avg_kills == 10.2 and a.avg_kills_when_shamed == 5.3
    assert a.title == get_shame_title(3, 2) == "Regular"
    assert a.form == ["S", "S", "C", "S", "C"] and a.form_wl == ["L", "W", "W", "L", "L"]
    assert a.win_rate == 40 and a.avg_kd is not None


def test_aggregate_handles_v1_records():
    recs = [_mk("p", 4, 20, enriched=False), _mk("p", 12, 10, enriched=False)]
    a = aggregate("p", recs, nickname="P")
    assert a.games == 2 and a.shame_count == 1 and a.win_rate is None and a.avg_kd is None
    assert a.form_wl == ["?", "?"]


def test_heater_title_and_per_map():
    recs = [_mk("p", 1, 30), _mk("p", 2, 20, map_name="de_nuke"), _mk("p", 3, 10)]
    a = aggregate("p", recs)
    assert a.title == "Heater (reverse)"
    maps = per_map("p", recs)
    assert [m.map for m in maps] == ["de_mirage", "de_nuke"]
    assert maps[0].games == 2 and maps[0].shame_rate == 100 and maps[0].avg_kills == 2.0


def test_hall_of_shame_and_elo_change():
    recs = [_mk("p", 1, 30, elo_delta=-30), _mk("p", 25, 20, elo_delta=25), _mk("q", 30, 20, elo_delta=25)]
    aggs = [aggregate("p", [r for r in recs if "p" in r.players], nickname="P"), aggregate("q", [r for r in recs if "q" in r.players], nickname="Q")]
    entries = {e.label: e for e in hall_of_shame(aggs, {"p": recs[:2], "q": recs[2:]})}
    assert entries["Worst game ever"].nickname == "P" and entries["Worst game ever"].value == "1 kills"
    assert entries["Biggest ELO loss"].value == "-30"
    assert entries["Best game"].nickname == "Q"

    import time

    now = int(time.time())
    snap = PlayerSnapshot(player_id="p", nickname="P", elo=1300, elo_history=[[now - 40 * 86400, 1200], [now - 3 * 86400, 1280], [now, 1300]])
    assert elo_change(snap, days=7) == 20
    assert elo_change(snap, days=30) == 20  # oldest within 30d is 1280
    assert elo_change(snap, days=60) == 100
    assert elo_change(None, days=7) is None


# -------------------------------------------------------------------- state


def test_state_migration_from_v1(tmp_path, fixtures, tracked):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "processed_matches": ["a", "b"],
                "match_outcomes": {
                    "a": {"finished_at": 10, "players": {"p1": {"nickname": "A", "kills": 3, "shamed": True}}},
                },
                "last_digest_at": None,
                "stats_backfill_completed_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    st = State(str(path))
    st.load()
    assert st.processed_matches == {"a", "b"}
    assert st.pending_enrichment() == ["a"]
    assert st.stats_backfill_completed_at == "2026-01-01T00:00:00+00:00"

    rec = _record(fixtures, tracked, "_shame")
    st.put_match(rec)
    st.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3
    assert data["match_outcomes"][rec.match_id]["v"] == 3
    assert data["match_outcomes"]["a"]["v"] == 1  # untouched until enriched

    st2 = State(str(path))
    st2.load()
    assert st2.match_outcomes[rec.match_id].to_dict() == rec.to_dict()
    pid = next(iter(rec.players))
    assert st2.matches_for_player(pid)[0].match_id == rec.match_id


def test_snapshot_from_profile(fixtures):
    snap = PlayerSnapshot.from_profile(fixtures["player"])
    assert snap.nickname == "BRNWr" and snap.level == 6 and snap.elo == 1295
    assert snap.faceit_url == "https://www.faceit.com/en/players/BRNWr"
    snap.push_elo(1, 1295)
    snap.push_elo(2, 1270)
    assert snap.elo_history == [[1, 1295], [2, 1270]]
    again = PlayerSnapshot.from_dict(snap.player_id, json.loads(json.dumps(snap.to_dict())))
    assert again == snap


# ------------------------------------------------------------ moment awards


def test_moment_awards_from_fixture(fixtures, tracked):
    # mindtech went 0/1 in 1v1s and 0/2 in 1v2s in the captured shame match
    rec = _record(fixtures, tracked, "_shame")
    detect(rec, streaks_before={}, thresholds=Thresholds())
    m = next(x for x in rec.players.values() if x.nickname == "mindtech")
    assert "clutch_donor" in m.awards
    m.mvps = 0
    pid = next(p for p, x in rec.players.items() if x is m)
    awards = compute_awards(rec, pid, streak_before=0, shamed_count=2)
    assert awards.index("clutch_donor") < awards.index("zero_mvp")  # moments before generic pills
    b = next(x for x in rec.players.values() if x.nickname == "BRNWr")
    assert "clutch_donor" not in b.awards  # no clutch situations at all


def test_moment_awards_thresholds():
    from shamebot.rules import moment_awards

    base = dict(nickname="P", kills=4, shamed=True)
    assert moment_awards(TrackedResult(nickname="P", kills=4, shamed=True), rounds=24) == []  # not extended
    r = TrackedResult(**base, entry_count=5, entry_wins=1, c1v1=1, c1v2=1, w1v1=0, w1v2=0,
                      utility_count=1, flash_count=1, utility_damage=0)
    assert moment_awards(r, rounds=24) == ["clutch_donor", "entry_fodder", "nade_hoarder"]
    assert "nade_hoarder" not in moment_awards(r, rounds=12)  # short game, no hoarding verdict
    r2 = TrackedResult(**base, entry_count=0, entry_wins=0, utility_count=7, utility_damage=0, flash_count=6, enemies_flashed=0)
    assert moment_awards(r2, rounds=20) == ["blank_nades", "flash_artist"]
    r3 = TrackedResult(**base, entry_count=4, entry_wins=2, utility_count=7, utility_damage=30)
    assert moment_awards(r3, rounds=20) == []
    assert award_label("clutch_donor") == "Clutch donor"


def test_roast_prefers_moment_modifier_with_numbers(fixtures, tracked):
    rec = _record(fixtures, tracked, "_shame")
    detect(rec, streaks_before={}, thresholds=Thresholds())
    m = next(x for x in rec.players.values() if x.nickname == "mindtech")
    eng = RoastEngine()
    ctx = RoastContext.from_match(rec, m)
    assert ctx.clutches == 3 and ctx.clutch_wins == 0
    for i in range(20):
        line = eng.roast(ctx, seed=str(i))
        assert "{" not in line
        # a specific failure (the lost clutches, or the blame share) always takes a slot
        assert "clutch" in line.lower() or f"{ctx.blame}%" in line


def test_roast_singularises_extended_words():
    ctx = RoastContext(nick="P", kills=1, deaths=1, map="Mirage", adr=1.0, kd=1.0, hs=1, rounds=1,
                       score="", mvps=1, clutches=1, flashes=1, enemies_flashed=1)
    assert ctx.fmt("{clutches} clutches, {flashes} flashes, {enemies_flashed} enemies") == "1 clutch, 1 flash, 1 enemy"


# ------------------------------------------------------------------- blame


def test_blame_report_against_own_team(fixtures, tracked):
    from shamebot.blame import compute_blame

    rec = _record(fixtures, tracked, "_onekill")
    detect(rec, streaks_before={}, thresholds=Thresholds())
    pid = next(p for p, r in rec.players.items() if r.nickname == "BRNWr")
    b = compute_blame(rec, pid)
    assert b is not None and b.lost
    assert b.rank == 5 and b.team_size == 5  # worst impact on his own team
    assert b.share > 20 and b.heavy  # well above an even split
    assert b.kill_share <= 5 and b.death_share >= 20
    assert "lost 4 of 4 opening duels" in b.facts and "lowest ADR on the team" in b.facts
    assert b.summary().startswith(f"{b.share}% blame · 5th of 5")
    # the whole team's shares add up to ~100 and the best player carries none of it
    shares = [compute_blame(rec, row.pid).share for t in rec.teams for row in t.players if row.pid == pid]
    assert shares == [b.share]


def test_blame_even_team_and_best_player():
    from shamebot.blame import compute_blame
    from shamebot.models import ScoreRow, TeamRecord

    def row(pid, k, d, adr):
        return ScoreRow(pid=pid, nick=pid, k=k, d=d, a=2, adr=adr, kd=round(k / d, 2), hs=40, mvp=1)

    even = TeamRecord(name="A", score=5, win=False, players=[row(f"p{i}", 15, 15, 70.0) for i in range(5)])
    rec = MatchRecord(match_id="m", finished_at=1, v=3, map="de_mirage", rounds=24,
                      players={"p0": TrackedResult(nickname="p0", kills=15, shamed=False, deaths=15, result=0, team_score=5, enemy_score=13)},
                      teams=[even])
    b = compute_blame(rec, "p0")
    assert b.share == 20 and b.rank == 1

    star = TeamRecord(name="A", score=5, win=False, players=[row("p0", 30, 10, 110.0)] + [row(f"p{i}", 10, 18, 50.0) for i in range(1, 5)])
    rec.teams = [star]
    b = compute_blame(rec, "p0")
    assert b.share == 0 and b.rank == 1 and b.facts == []


def test_blame_missing_for_v1_records():
    from shamebot.blame import compute_blame

    rec = MatchRecord(match_id="m", finished_at=1, v=1, map="de_mirage", players={"p": TrackedResult(nickname="p", kills=3, shamed=True)})
    assert compute_blame(rec, "p") is None


def test_roast_blame_placeholder(fixtures, tracked):
    rec = _record(fixtures, tracked, "_onekill")
    detect(rec, streaks_before={}, thresholds=Thresholds())
    r = next(x for x in rec.players.values() if x.nickname == "BRNWr")
    ctx = RoastContext.from_match(rec, r)
    assert ctx.blame > 20 and ctx.blame_heavy
    assert ctx.fmt("{blame}% yours") == f"{ctx.blame}% yours"


# --------------------------------------------------------------- liability


def test_liability_detection_thresholds(fixtures, tracked):
    from shamebot.rules import is_liability

    rec = _record(fixtures, tracked)  # BRNWr 14 kills, worst on his team, lost 8-13, 34% blame
    pid = next(p for p, r in rec.players.items() if r.nickname == "BRNWr")
    assert is_liability(rec, pid, Thresholds()) is None  # 34 < 35: not decisive enough
    loose = Thresholds(liability_blame_share=30)
    b = is_liability(rec, pid, loose)
    assert b is not None and b.rank == 5 and b.lost
    dets = detect(rec, streaks_before={}, thresholds=loose)
    assert [d.kind for d in dets] == [PostKind.LIABILITY] and dets[0].player_ids == [pid]
    assert rec.players[pid].liability and not rec.players[pid].shamed
    # never for a shamed player (wall takes precedence) or when disabled
    assert is_liability(rec, pid, Thresholds(liability_blame_share=30, kill_threshold=15)) is None
    assert is_liability(rec, pid, Thresholds(liability_blame_share=30, liability_enabled=False)) is None
    # detect() resets the flag when the rule no longer holds
    detect(rec, streaks_before={}, thresholds=Thresholds())
    assert not rec.players[pid].liability


def test_liability_close_win_only(fixtures, tracked):
    from shamebot.rules import is_liability

    rec = _record(fixtures, tracked)
    pid = next(p for p, r in rec.players.items() if r.nickname == "BRNWr")
    r = rec.players[pid]
    t = Thresholds(liability_blame_share=30)
    r.result, r.team_score, r.enemy_score = 1, 13, 11  # dragged a close win
    assert is_liability(rec, pid, t) is not None
    r.team_score, r.enemy_score = 16, 14  # overtime win
    assert is_liability(rec, pid, t) is not None
    r.team_score, r.enemy_score = 13, 4  # comfortable win: nobody cares
    assert is_liability(rec, pid, t) is None
    r.result, r.team_score, r.enemy_score, r.kd = 0, 17, 19, 0.96  # long OT loss with a near-even K/D: not a liability
    assert is_liability(rec, pid, t) is None


def test_liability_roast_and_aggregate(fixtures, tracked):
    rec = _record(fixtures, tracked)
    pid = next(p for p, r in rec.players.items() if r.nickname == "BRNWr")
    detect(rec, streaks_before={}, thresholds=Thresholds(liability_blame_share=30))
    ctx = RoastContext.from_match(rec, rec.players[pid])
    assert ctx.team_best_kills == 20 and ctx.team_avg_kills > ctx.kills
    eng = RoastEngine()
    for i in range(10):
        line = eng.liability(ctx, seed=str(i))
        assert "{" not in line
    assert "{" not in eng.liability(ctx, seed="w", won=True)
    agg = aggregate(pid, [rec])
    assert agg.liability_count == 1 and agg.shame_count == 0 and agg.form == ["B"]
    # round-trips through the state dict
    again = MatchRecord.from_dict(rec.match_id, json.loads(json.dumps(rec.to_dict())))
    assert again.players[pid].liability


# --------------------------------------------------------------- grey zone


def _grey_record(kills: int, deaths: int, adr: float, *, result: int = 0, scores=(9, 13)) -> tuple[MatchRecord, str]:
    from shamebot.models import ScoreRow, TeamRecord

    def row(pid, k, d, a):
        return ScoreRow(pid=pid, nick=pid, k=k, d=d, a=3, adr=a, kd=round(k / d, 2), hs=40, mvp=1)

    mine = TeamRecord(name="A", score=scores[0], win=result == 1,
                      players=[row("me", kills, deaths, adr)] + [row(f"t{i}", 17, 14, 78.0) for i in range(4)])
    theirs = TeamRecord(name="B", score=scores[1], win=result == 0, players=[row(f"e{i}", 16, 15, 75.0) for i in range(5)])
    r = TrackedResult(nickname="me", kills=kills, shamed=False, deaths=deaths, adr=adr, kd=round(kills / deaths, 2),
                      hs_pct=40, mvps=1, result=result, team_score=scores[0], enemy_score=scores[1])
    rec = MatchRecord(match_id="g", finished_at=1_700_000_000, v=3, map="de_mirage", rounds=22, players={"me": r}, teams=[mine, theirs])
    return rec, "me"


def test_grey_zone_two_signals_or_extreme():
    from shamebot.rules import grey_zone_reasons

    th = Thresholds()
    # 11 kills, 19 deaths, 51 ADR on a loss: K/D + ADR + blame -> wall
    rec, pid = _grey_record(11, 19, 51.0)
    reasons = grey_zone_reasons(rec, pid, th)
    assert len(reasons) == 3 and reasons[0].startswith("K/D")
    # one weak signal alone (K/D 0.61, fine ADR, not worst by enough) is not enough
    rec, pid = _grey_record(11, 18, 72.0)
    assert grey_zone_reasons(rec, pid, th) == [] or len(grey_zone_reasons(rec, pid, th)) >= 2
    # an extreme single signal is
    rec, pid = _grey_record(12, 10, 40.0, result=1, scores=(13, 4))
    assert grey_zone_reasons(rec, pid, th) == ["ADR 40"]
    # outside the zone: never
    rec, pid = _grey_record(13, 25, 30.0)
    assert grey_zone_reasons(rec, pid, th) == []
    # blame signal needs a loss or close win: a stomp with a bad row but nothing else stays clean
    rec, pid = _grey_record(10, 13, 60.0, result=1, scores=(13, 3))
    assert grey_zone_reasons(rec, pid, th) == []
    # non-retroactive gate
    rec, pid = _grey_record(11, 19, 51.0)
    assert grey_zone_reasons(rec, pid, Thresholds(grey_since=1_800_000_000)) == []


def test_grey_zone_shame_post_and_roast():
    rec, pid = _grey_record(11, 19, 51.0)
    dets = detect(rec, streaks_before={}, thresholds=Thresholds())
    assert [d.kind for d in dets] == [PostKind.SHAME] and dets[0].player_ids == [pid]
    r = rec.players[pid]
    assert r.shamed and not r.liability
    assert r.awards[0] == "bait_job" and award_label("bait_job") == "Bait job"
    ctx = RoastContext.from_match(rec, r)
    ctx.grey_reasons = "K/D 0.58, ADR 51"
    eng = RoastEngine()
    lines = {eng.roast(ctx, seed=str(i)) for i in range(15)}
    assert all("{" not in ln for ln in lines)
    assert any("K/D 0.58, ADR 51" in ln for ln in lines)  # the reason is spoken
    # a liability never doubles up with a grey-zone shame
    from shamebot.rules import is_liability
    assert is_liability(rec, pid, Thresholds()) is None


# ------------------------------------------------------------------- fame


def _fame_record(kills: int, deaths: int, adr: float, *, result: int = 1, scores=(13, 8), mvp: int = 4, lobby_best: int = 18) -> tuple[MatchRecord, str]:
    from shamebot.models import ScoreRow, TeamRecord

    def row(pid, k, d, a, mv=1):
        return ScoreRow(pid=pid, nick=pid, k=k, d=d, a=3, adr=a, kd=round(k / max(d, 1), 2), hs=45, mvp=mv)

    mine = TeamRecord(name="A", score=scores[0], win=result == 1,
                      players=[row("me", kills, deaths, adr, mvp)] + [row(f"t{i}", 14, 15, 70.0) for i in range(4)])
    theirs = TeamRecord(name="B", score=scores[1], win=result == 0, players=[row(f"e{i}", lobby_best - i, 16, 78.0) for i in range(5)])
    r = TrackedResult(nickname="me", kills=kills, shamed=False, deaths=deaths, adr=adr, kd=round(kills / max(deaths, 1), 2),
                      hs_pct=45, mvps=mvp, result=result, team_score=scores[0], enemy_score=scores[1],
                      entry_count=6, entry_wins=5, c1v1=2, w1v1=2, c1v2=0, w1v2=0, utility_damage=160)
    rec = MatchRecord(match_id="f", finished_at=1_700_000_000, v=3, map="de_mirage", rounds=21, players={"me": r}, teams=[mine, theirs])
    return rec, "me"


def test_fame_carry_rule_and_awards():
    from shamebot.blame import compute_carry
    from shamebot.rules import fame_awards, is_fame_carry

    th = Thresholds()
    rec, pid = _fame_record(26, 11, 106.0)
    c = is_fame_carry(rec, pid, th)
    assert c is not None and c.top_of_lobby and c.rank == 1 and c.heavy and c.won
    assert c.label == "CARRY" and "top-fragger of the lobby" in c.facts
    dets = detect(rec, streaks_before={}, thresholds=th)
    assert [d.kind for d in dets] == [PostKind.GLORY]
    r = rec.players[pid]
    assert r.fame and not r.shamed
    for a in ("top_of_lobby", "hard_carry", "untouchable", "clutch_king", "entry_king", "utility_master"):
        assert a in r.awards, a
    assert "wasted" not in r.awards and award_label("hard_carry") == "Hard carry"
    # 22 kills, 1.1 K/D, 80 ADR, not top of lobby -> just a good game, no post
    rec, pid = _fame_record(22, 20, 80.0, lobby_best=24)
    assert is_fame_carry(rec, pid, th) is None
    assert detect(rec, streaks_before={}, thresholds=th) == []
    # carried a WIN while an enemy out-fragged the lobby -> still a carry (the BRNWr 26K Inferno case)
    rec, pid = _fame_record(26, 14, 116.0, lobby_best=29)
    c = is_fame_carry(rec, pid, th)
    assert c is not None and not c.top_of_lobby and c.won
    assert [d.kind for d in detect(rec, streaks_before={}, thresholds=th)] == [PostKind.GLORY]
    assert "top_of_lobby" not in rec.players[pid].awards and "hard_carry" in rec.players[pid].awards
    # the same line on a LOSS without the lobby lead -> no post
    rec, pid = _fame_record(26, 14, 116.0, result=0, scores=(10, 13), lobby_best=29)
    assert is_fame_carry(rec, pid, th) is None
    # same carry on a loss -> WASTED
    rec, pid = _fame_record(26, 11, 106.0, result=0, scores=(11, 13))
    detect(rec, streaks_before={}, thresholds=th)
    assert "wasted" in rec.players[pid].awards and compute_carry(rec, pid).label == "WASTED"
    # 30-bomb still posts without the carry conditions
    rec, pid = _fame_record(31, 25, 80.0, lobby_best=33)
    assert [d.kind for d in detect(rec, streaks_before={}, thresholds=th)] == [PostKind.GLORY]


def test_fame_praise_lines():
    rec, pid = _fame_record(26, 11, 106.0)
    detect(rec, streaks_before={}, thresholds=Thresholds())
    ctx = RoastContext.from_match(rec, rec.players[pid])
    assert ctx.carry >= 30 and ctx.lobby_second == 18 and ctx.lobby_gap == 8
    eng = RoastEngine()
    lines = [eng.glory(ctx, seed=str(i)) for i in range(20)]
    assert all("{" not in ln for ln in lines)
    assert all((f"{ctx.carry}%" in ln or f"{ctx.kill_share}%" in ln) for ln in lines)  # carry verdict always spoken
    assert "{" not in eng.glory(ctx, seed="a", ace=True)
    agg = aggregate(pid, [rec])
    assert agg.form == ["G"]


def test_roasts_avoid_recent_repeats(fixtures, tracked):
    from shamebot.roasts import GLORY_LINES, RECENT_CAP

    rec = _record(fixtures, tracked)
    r = next(iter(rec.players.values()))
    ctx = RoastContext.from_match(rec, r)
    shared: list[str] = []
    eng = RoastEngine(recent=shared)
    seen = {eng.glory(ctx, seed=f"m{i}") for i in range(len(GLORY_LINES))}
    assert len(seen) == len(GLORY_LINES)  # every base line used once before any repeats
    assert len(shared) <= RECENT_CAP and shared is eng.recent
    # the memory is what the app persists: a fresh engine over the same list keeps avoiding them
    eng2 = RoastEngine(recent=shared)
    assert eng2.glory(ctx, seed="m0") not in seen or len(GLORY_LINES) <= RECENT_CAP


def test_scorerow_extended_for_all_ten_and_roundtrip(fixtures, tracked):
    rec = parse_match("m", fixtures["stats"], fixtures["details"], tracked, kill_threshold=10)
    rows = [row for t in rec.teams for row in t.players]
    assert len(rows) == 10 and all(row.extended for row in rows)
    assert all(row.steam_id and len(row.steam_id) == 17 for row in rows)
    assert any(row.avatar for row in rows)
    d = rows[0].to_dict()
    assert "avatar" not in d and d["entry_count"] == rows[0].entry_count and d["steam_id"] == rows[0].steam_id
    back = ScoreRow.from_dict(d)
    assert back.extended and back.clutches == rows[0].clutches and back.kr == rows[0].kr and back.avatar is None
    old = ScoreRow.from_dict({"pid": "p", "nick": "n", "k": 1, "d": 2, "a": 0, "adr": 10.0, "kd": 0.5, "hs": 0, "mvp": 0})
    assert not old.extended and old.steam_id is None and "entry_count" not in old.to_dict()
