"""Post-mortem engine: team-relative ranking, callouts, prose, view/render, and the app orchestration."""
from __future__ import annotations

import io
import json

import discord
import pytest
from PIL import Image

from shamebot.models import MatchRecord, ScoreRow, TeamRecord, parse_match
from shamebot.postmortem import (
    MAX_CALLOUTS,
    PlayerLine,
    PostmortemResult,
    TeamMetric,
    analyze,
    build_categories,
    choose_team,
    find_callouts,
    score_team,
    write_prose,
)
from shamebot.render.postmortem_card import render_postmortem
from shamebot.rules import Thresholds, detect
from shamebot.views import postmortem_view
from tests.test_poller import FakeChannel, FakeClient, _app, env  # noqa: F401


def _line(nick: str, *, tracked: bool = False, k: int = 15, d: int = 15, a: int = 3, adr: float = 70.0, kd: float | None = None,
          hs: int = 40, mvp: int = 1, leetify: float | None = None, extended: bool = True, **kw) -> PlayerLine:
    ext = dict(kr=0.6, entry_count=4, entry_wins=2, first_kills=2, clutches=2, clutch_wins=1, clutch_kills=1, util_dmg=60, enemies_flashed=5, util_count=10) if extended else {}
    ext.update(kw)
    ln = PlayerLine(pid=nick.lower(), nick=nick, tracked=tracked, level=7, steam_id="7656119" + nick.lower().ljust(10, "0"), avatar=None,
                    k=k, d=d, a=a, adr=adr, kd=kd if kd is not None else round(k / max(d, 1), 2), hs=hs, mvp=mvp, leetify=leetify, **ext)
    ln.impact = k + 0.5 * a + adr / 10 + mvp - 0.5 * d
    return ln


def _result(lines: list[PlayerLine], *, won: bool = False, rounds: int = 24) -> PostmortemResult:
    team_k = sum(ln.k for ln in lines) or 1
    team_d = sum(ln.d for ln in lines) or 1
    for ln in lines:
        ln.kill_share = round(100 * ln.k / team_k)
        ln.death_share = round(100 * ln.d / team_d)
    cats = build_categories(lines)
    rows = score_team(lines, cats)
    return PostmortemResult("m", "Mirage", "13 – 9" if won else "9 – 13", won, rounds, "team_x", 1_700_000_000, rows, cats,
                            [r.pid for r in rows if r.line.tracked], leetify_used=any(ln.leetify is not None for ln in lines))


def _record(fixtures, tracked, suffix):
    rec = parse_match(fixtures["details" + suffix]["match_id"], fixtures["stats" + suffix], fixtures["details" + suffix], tracked, kill_threshold=10)
    detect(rec, streaks_before={}, thresholds=Thresholds())
    return rec


# ------------------------------------------------------------------ scoring


def test_ranking_order_and_tiebreak():
    same = [_line(n) for n in ("Cid", "Ann", "Bob")]
    res = _result(same)
    assert [r.score for r in res.rows] == [0.5, 0.5, 0.5]
    assert [r.nick for r in res.rows] == ["Ann", "Bob", "Cid"]  # identical → alphabetical
    res = _result([_line("Low", k=5, d=20, adr=40), _line("High", k=25, d=10, adr=100)])
    assert [r.nick for r in res.rows] == ["High", "Low"] and res.rows[0].rank == 1 and res.rows[1].rank == 2
    assert res.rows[0].score > 0.5 > res.rows[1].score  # identical extended stats sit at 0.5, the rest separates them


def test_min_max_norm_and_lower_better():
    m = TeamMetric("Deaths", {"a": 10.0, "b": 20.0, "c": 15.0}, higher_better=False)
    assert m.norm("a") == 1.0 and m.norm("b") == 0.0 and m.norm("c") == 0.5
    assert m.rank("a") == 1 and m.rank("b") == 3
    flat = TeamMetric("HS", {"a": 40.0, "b": 40.0})
    assert flat.norm("a") == 0.5 and flat.rank("a") == 1
    sparse = TeamMetric("Leet", {"a": 1.0, "b": None})
    assert not sparse.usable and sparse.norm("a") is None


def test_missing_leetify_drops_metric_not_category():
    res = _result([_line("A", k=20), _line("B", k=10)])
    impact = next(c for c in res.categories if c.name == "Impact")
    assert impact.usable and not impact.metrics[0].usable
    assert not res.leetify_used
    assert sum(c.weight for c in res.usable_categories) == 11
    assert all(r.leetify_rank is None for r in res.rows)


def test_missing_extended_drops_categories():
    res = _result([_line("A", k=20, extended=False), _line("B", k=10, extended=False)])
    names = [c.name for c in res.usable_categories]
    assert names == ["Impact", "Fragging", "Discipline"]
    assert sum(c.weight for c in res.usable_categories) == 7
    assert res.rows[0].nick == "A"


def test_best_and_worst_at():
    res = _result([_line("Util", util_dmg=200, enemies_flashed=12, k=12), _line("Frag", k=25, adr=100), _line("Mid")])
    assert res.row("util").best_at == "Utility"
    assert res.row("frag").best_at == "Fragging"
    assert res.row("util").worst_at != "Utility"


def test_worst_means_last_on_team_not_own_lowest():
    # Star is top of the team in every category except HS%; his lowest own category is still 2nd on the team.
    star = _line("Star", k=25, d=10, adr=110, mvp=4, hs=20, util_dmg=150, enemies_flashed=9, clutch_wins=2, first_kills=5, entry_wins=4)
    res = _result([star, _line("A", k=15, d=16, hs=50), _line("B", k=12, d=18, hs=45), _line("C", k=10, d=20, hs=40)])
    row = res.row("star")
    assert row.rank == 1 and row.worst_at is None and row.low_at == "Discipline"
    assert res.row("c").worst_at in ("Fragging", "Impact", "Discipline") and res.row("c").best_at is None


def test_score_is_from_ranked_teams_perspective():
    rec = _two_team_record(["p1"], ["p3"])
    assert analyze(rec, team_index=0, tracked={"p1", "p3"}, leetify=None).score == "13 – 9"
    assert analyze(rec, team_index=1, tracked={"p1", "p3"}, leetify=None).score == "9 – 13"


# ----------------------------------------------------------------- callouts


def test_feed_illusion_callout_tracked_first_and_capped():
    lines = [
        _line("Baiter", tracked=True, k=24, d=24, adr=55, hs=20, mvp=0, entry_count=6, entry_wins=1, util_dmg=0, enemies_flashed=0),
        _line("Carry", k=22, d=10, adr=95, mvp=4),
        _line("Solid", k=18, d=14, adr=80, mvp=2),
        _line("Rand", k=14, d=16, adr=70),
        _line("Rand2", k=12, d=18, adr=60, entry_count=5, entry_wins=1, util_dmg=5, enemies_flashed=1),
    ]
    res = _result(lines)
    res.callouts = find_callouts(res)
    baiter = res.row("baiter")
    assert baiter.kills_rank == 1 and baiter.rank >= 3
    keys = [c.key for c in res.callouts]
    assert keys[0] == "feed_illusion" and res.callouts[0].pid == "baiter"
    assert len(res.callouts) <= MAX_CALLOUTS
    assert all(c.pid == "baiter" for c in res.callouts[:3])  # tracked hits come before random hits


def test_leetify_disagrees_and_quiet_carry():
    lines = [_line("Quiet", tracked=True, k=10, d=8, adr=90, mvp=5, leetify=0.08, clutch_wins=3, clutches=3),
             _line("Loud", k=25, d=22, adr=70, mvp=0, leetify=-0.05), _line("X", k=20, d=15, leetify=0.0)]
    res = _result(lines)
    res.callouts = find_callouts(res)
    keys = {c.key for c in res.callouts}
    assert res.rows[0].nick == "Quiet" and "quiet_carry" in keys and "leetify_disagrees" in keys
    assert res.leetify_used and res.rows[0].leetify_rank == 1


# -------------------------------------------------------------------- prose


def test_prose_deterministic_and_filled():
    lines = [_line("Top", tracked=True, k=25, d=10, adr=100), _line("Mid", tracked=True), _line("R1", k=14), _line("R2", k=13), _line("Low", tracked=True, k=4, d=22, adr=30)]
    a = write_prose(_result(lines), seed="s1")
    b = write_prose(_result([_line(ln.nick, tracked=ln.tracked, k=ln.k, d=ln.d, adr=ln.adr) for ln in lines]), seed="s1")
    assert a.headline == b.headline and a.lines == b.lines
    assert set(a.lines) == {"top", "mid", "low"}
    assert "{" not in a.headline and all("{" not in ln for ln in a.lines.values())
    assert "**Low**" in a.headline  # lost, tracked anchor
    assert a.lines["top"].startswith("**Top**") and a.lines["low"].startswith("**Low**")
    c = write_prose(_result(lines, won=True), seed="s2")
    assert "**Top**" in c.headline


def test_prose_rotates_with_recent():
    recent: list[str] = []
    lines = [_line("A", tracked=True, k=20), _line("B", k=10)]
    write_prose(_result(lines), seed="x", recent=recent)
    assert len(recent) == 2  # headline + one line
    first = list(recent)
    write_prose(_result(lines), seed="x", recent=recent)
    assert recent[2:] != first  # same seed, but the recent list forces different templates


# --------------------------------------------------------------- team choice


def _two_team_record(tracked_a: list[str], tracked_b: list[str]) -> MatchRecord:
    def rows(names, prefix):
        return [ScoreRow(pid=n, nick=n, k=10 + i, d=12, a=2, adr=60 + i, kd=1.0, hs=40, mvp=1, level=6, steam_id=f"{prefix}{i}") for i, n in enumerate(names)]
    ta = TeamRecord("team_a", 13, True, rows(tracked_a + [f"ra{i}" for i in range(5 - len(tracked_a))], "a"))
    tb = TeamRecord("team_b", 9, False, rows(tracked_b + [f"rb{i}" for i in range(5 - len(tracked_b))], "b"))
    return MatchRecord(match_id="m2", finished_at=1, v=3, map="de_nuke", score="13 / 9", rounds=22, competition=None, faceit_url="u", map_image=None, teams=[ta, tb], players={})


def test_choose_team_majority_focus_and_other_team():
    rec = _two_team_record(["p1", "p2"], ["p3"])
    tracked = {"p1", "p2", "p3"}
    assert choose_team(rec, tracked) == 0
    assert choose_team(rec, tracked, focus_pid="p3") == 0  # majority wins over focus
    tie = _two_team_record(["p1"], ["p3"])
    assert choose_team(tie, tracked, focus_pid="p3") == 1 and choose_team(tie, tracked, focus_pid="p1") == 0
    res = analyze(rec, team_index=0, tracked=tracked, leetify=None)
    assert res.tracked_pids == ["p2", "p1"] or set(res.tracked_pids) == {"p1", "p2"}
    assert res.other_team and res.other_team[0][0] == "p3"
    assert res.score == "13 – 9" and res.won
    assert any("Extended" in n for n in res.notes)


def test_single_tracked_player_gets_one_line():
    rec = _two_team_record(["p1"], [])
    res = write_prose(analyze(rec, team_index=0, tracked={"p1"}, leetify=None), seed="z")
    assert list(res.lines) == ["p1"] and res.headline


# ------------------------------------------------------------------ fixture


def test_analyze_fixture_with_leetify(fixtures, tracked):
    rec = _record(fixtures, tracked, "_shame")
    ti = choose_team(rec, set(tracked))
    steam = {row.nick: row.steam_id for row in rec.teams[ti].players}
    assert all(sid and len(sid) == 17 for sid in steam.values())
    leet = {sid: 0.05 - 0.02 * i for i, sid in enumerate(steam.values())}
    res = write_prose(analyze(rec, team_index=ti, tracked=set(tracked), leetify=leet), seed=rec.match_id)
    assert len(res.rows) == 5 and res.leetify_used and not res.notes
    nicks = {r.nick for r in res.rows if r.line.tracked}
    assert nicks == {"BRNWr", "mindtech"}
    assert res.rows[-1].nick in nicks  # the two shamed players are at the bottom
    assert all(r.leetify_rank for r in res.rows)
    assert len(res.usable_categories) == 6


def test_analyze_fixture_without_leetify_notes(fixtures, tracked):
    rec = _record(fixtures, tracked, "")
    res = analyze(rec, team_index=choose_team(rec, set(tracked)), tracked=set(tracked), leetify={})
    assert not res.leetify_used and any("Leetify" in n for n in res.notes)


# ------------------------------------------------------------- render + view


def test_render_postmortem(fixtures, tracked):
    rec = _record(fixtures, tracked, "_shame")
    res = write_prose(analyze(rec, team_index=0, tracked=set(tracked), leetify=None), seed="r")
    res.callouts = res.callouts or []
    png = render_postmortem(res, avatars={r.pid: b"garbage" for r in res.rows}, map_bytes=b"garbage", footer="Bot")
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size[0] == 1200 and img.size[1] > 700


def test_postmortem_view_serialises():
    lines = [_line("Top", tracked=True, k=25, d=10, adr=100, leetify=0.1), _line("R", k=10, leetify=-0.1)]
    res = write_prose(_result(lines), seed="v")
    res.notes.append("note")
    view, files = postmortem_view(res, b"png", links=[("Open on FaceIT", "https://faceit.com/m"), ("Top on Leetify", "https://leetify.com/app/profile/1")])
    payload = view.to_components()
    assert files[0].filename == "postmortem.png"
    text = json.dumps(payload)
    assert "Data provided by Leetify" in text and "note" in text and "Post-mortem" in text and "**Top**" in text
    row = next(c for c in payload[0]["components"] if c["type"] == discord.ComponentType.action_row.value)
    assert [b["url"] for b in row["components"]] == ["https://faceit.com/m", "https://leetify.com/app/profile/1"]


# --------------------------------------------------------------- app wiring


class _FakeLeetify:
    def __init__(self, ratings):
        self.ratings = ratings
        self.rate_limited = False

    async def match_ratings(self, mid):
        return self.ratings

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_app_pick_and_postmortem(fixtures, tracked, tmp_path, env):
    app, fake = _app(fixtures, tracked, tmp_path)
    app.settings.leetify_enabled = True
    for suffix, ts in (("_shame", 100), ("", 200), ("_onekill", 300)):
        rec = _record(fixtures, tracked, suffix)
        rec.finished_at = ts
        app.state.put_match(rec)
    # newest match only has one tracked player → skipped in favour of the newest 2+ tracked one
    one = app.state.match_outcomes[fixtures["details_onekill"]["match_id"]]
    one.players = {next(iter(one.players)): next(iter(one.players.values()))}
    picked = app.pick_postmortem_match()
    assert picked is not None and picked.finished_at == 200
    pid = next(p for p, n in tracked.items() if n == "BRNWr")
    assert app.pick_postmortem_match(pid).finished_at == 300

    rec = _record(fixtures, tracked, "_shame")
    ti = choose_team(rec, set(tracked))
    app.leetify = _FakeLeetify({row.steam_id: 0.01 * i for i, row in enumerate(rec.teams[ti].players)})
    res, png = await app.postmortem(rec)
    assert res.leetify_used and len(res.rows) == 5 and res.lines
    assert Image.open(io.BytesIO(png)).size[0] == 1200
