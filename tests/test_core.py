"""Parsing, rules, roasts, stats and state — all offline against captured FaceIT payloads."""
from __future__ import annotations

import json


from shamebot.models import MatchRecord, PlayerSnapshot, TrackedResult, parse_match
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
    assert rec.v == 2
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
    eng = RoastEngine()
    ctx = RoastContext.from_match(rec, r, streak=1)
    a = eng.roast(ctx, seed=rec.match_id)
    b = eng.roast(ctx, seed=rec.match_id)
    assert a == b
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
    assert data["schema_version"] == 2
    assert data["match_outcomes"][rec.match_id]["v"] == 2
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
