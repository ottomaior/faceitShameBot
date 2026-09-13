"""Poller behaviour with a fake FaceIT client and a fake Discord channel: posting, ELO deltas, persistence."""
from __future__ import annotations

import copy
import json

import pytest

from shamebot.app import App
from shamebot.config import Settings
from shamebot.poller import Poller
from shamebot.rules import PostKind


class FakeFaceit:
    """Serves fixture payloads; ``elo`` can be changed between polls to simulate matches."""

    def __init__(self, fixtures, tracked):
        self.fixtures = fixtures
        self.tracked = tracked
        self.elo = {pid: 1300 for pid in tracked}
        self.history: dict[str, list[dict]] = {pid: [] for pid in tracked}
        self.calls: list[str] = []

    def add_match(self, suffix: str, finished_at: int) -> str:
        details = self.fixtures["details" + suffix]
        mid = details["match_id"]
        for pid in self.tracked:
            if any(p.get("player_id") == pid for t in self.fixtures["stats" + suffix]["rounds"][0]["teams"] for p in t["players"]):
                self.history[pid].insert(0, {"match_id": mid, "finished_at": finished_at})
        return mid

    async def get_player(self, pid):
        self.calls.append(f"player:{pid[:4]}")
        p = copy.deepcopy(self.fixtures["player"])
        p["player_id"] = pid
        p["nickname"] = self.tracked[pid]
        p["games"]["cs2"]["faceit_elo"] = self.elo[pid]
        return p

    async def get_history(self, pid, *, limit, offset=0):
        self.calls.append(f"history:{pid[:4]}")
        return self.history[pid][:limit]

    async def get_match(self, mid):
        for suffix in ("", "_shame", "_onekill"):
            if self.fixtures["details" + suffix]["match_id"] == mid:
                return self.fixtures["details" + suffix]
        return None

    async def get_match_stats(self, mid):
        for suffix in ("", "_shame", "_onekill"):
            if self.fixtures["details" + suffix]["match_id"] == mid:
                return self.fixtures["stats" + suffix]
        return None

    async def fetch_image(self, url, **kw):
        return None

    async def close(self):
        pass


class FakeMessage:
    _next_id = 1000

    def __init__(self):
        self.reactions = []
        self.edits = []
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def edit(self, *, view=None, attachments=None, **kw):
        self.edits.append((view, attachments))


class FakeChannel:
    _next_id = 1

    def __init__(self):
        self.posts = []
        self.messages: dict[int, FakeMessage] = {}
        FakeChannel._next_id += 1
        self.id = FakeChannel._next_id

    async def send(self, *args, view=None, files=None, allowed_mentions=None, **kw):
        self.posts.append((view, files, allowed_mentions))
        msg = FakeMessage()
        self.messages[msg.id] = msg
        return msg

    async def fetch_message(self, mid):
        return self.messages[mid]


class FakeClient:
    def __init__(self, channel):
        self.channel = channel
        self.guilds = []

    def get_channel(self, cid):
        return self.channel


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("STATS_BACKFILL_ON_STARTUP", "false")
    monkeypatch.setenv("POST_HISTORY_LIMIT", "10")
    monkeypatch.setenv("FACEIT_DISCORD_MENTIONS", "BRNWr:42")


def _app(fixtures, tracked, tmp_path) -> tuple[App, FakeFaceit]:
    s = Settings(state_file=str(tmp_path / "state.json"), stats_backfill_on_startup=False, post_history_limit=10, mentions={"BRNWr": 42}, postmortem_auto=False)
    app = App(s)
    fake = FakeFaceit(fixtures, tracked)
    app.faceit = fake  # type: ignore[assignment]
    app.tracked = dict(tracked)
    return app, fake


@pytest.mark.asyncio
async def test_poll_posts_and_attributes_elo(fixtures, tracked, tmp_path, env):
    app, fake = _app(fixtures, tracked, tmp_path)
    brnwr = next(p for p, n in tracked.items() if n == "BRNWr")
    mindtech = next(p for p, n in tracked.items() if n == "mindtech")
    channel = FakeChannel()
    poller = Poller(FakeClient(channel), app)
    poller.channel = channel

    # snapshots before any match
    await app.refresh_all_snapshots()
    assert app.state.snapshot(brnwr).elo == 1300

    # one new match (the screenshot game: BRNWr 9 K, mindtech 8 K) finishes later; both lose ELO
    later = app.state.snapshot(brnwr).elo_ts + 100
    mid = fake.add_match("_shame", later)
    fake.elo[brnwr] = 1275
    fake.elo[mindtech] = 1280
    posted = await poller.poll_once(allow_post=True)

    assert posted == 1
    view, files, _ = channel.posts[0]
    assert files and files[0].filename.startswith("shame-")
    rec = app.state.match_outcomes[mid]
    assert rec.players[brnwr].elo_delta == -25 and rec.players[brnwr].elo_after == 1275
    assert rec.players[mindtech].elo_delta == -20
    assert "double_feature" in rec.players[brnwr].awards
    assert mid in app.state.processed_matches
    text = json.dumps(view.to_components())
    assert "<@42>" in text  # BRNWr is mapped -> mentioned
    assert app.state.snapshot(brnwr).elo_history[-1][1] == 1275

    # second poll: nothing new, nothing posted, no re-processing
    assert await poller.poll_once(allow_post=True) == 0
    assert len(channel.posts) == 1


@pytest.mark.asyncio
async def test_two_matches_in_one_poll_get_no_delta(fixtures, tracked, tmp_path, env):
    app, fake = _app(fixtures, tracked, tmp_path)
    brnwr = next(p for p, n in tracked.items() if n == "BRNWr")
    channel = FakeChannel()
    poller = Poller(FakeClient(channel), app)
    poller.channel = channel
    await app.refresh_all_snapshots()
    t0 = app.state.snapshot(brnwr).elo_ts
    m1 = fake.add_match("_onekill", t0 + 10)
    m2 = fake.add_match("_shame", t0 + 20)
    fake.elo[brnwr] = 1250
    await poller.poll_once(allow_post=False)
    assert channel.posts == []
    assert app.state.match_outcomes[m1].players[brnwr].elo_delta is None
    assert app.state.match_outcomes[m2].players[brnwr].elo_delta is None
    assert app.state.match_outcomes[m2].players[brnwr].elo_after == 1250
    # streak going into m2 counts m1 (processed oldest first)
    assert "hat_trick" not in app.state.match_outcomes[m2].players[brnwr].awards
    assert app.state.match_outcomes[m2].players[brnwr].shamed


@pytest.mark.asyncio
async def test_old_matches_get_no_elo(fixtures, tracked, tmp_path, env):
    """Matches that finished before the first snapshot (startup scan) must not get ELO attributed."""
    app, fake = _app(fixtures, tracked, tmp_path)
    brnwr = next(p for p, n in tracked.items() if n == "BRNWr")
    poller = Poller(FakeClient(FakeChannel()), app)
    poller.channel = FakeChannel()
    await app.refresh_all_snapshots()
    old = app.state.snapshot(brnwr).elo_ts - 3600
    mid = fake.add_match("", old)
    await poller.poll_once(allow_post=False)
    r = app.state.match_outcomes[mid].players[brnwr]
    assert r.elo_after is None and r.elo_delta is None


@pytest.mark.asyncio
async def test_redemption_and_glory_posts(fixtures, tracked, tmp_path, env):
    app, fake = _app(fixtures, tracked, tmp_path)
    brnwr = next(p for p, n in tracked.items() if n == "BRNWr")
    channel = FakeChannel()
    poller = Poller(FakeClient(channel), app)
    poller.channel = channel
    await app.refresh_all_snapshots()
    t0 = app.state.snapshot(brnwr).elo_ts
    # two shames first, then a big game -> redemption
    fake.add_match("_onekill", t0 + 10)
    fake.add_match("_shame", t0 + 20)
    await poller.poll_once(allow_post=False)
    big = copy.deepcopy(fixtures["stats"])
    for t in big["rounds"][0]["teams"]:
        for p in t["players"]:
            if p["player_id"] == brnwr:
                p["player_stats"]["Kills"] = "27"
    fake.fixtures = dict(fixtures, stats=big)
    fake.add_match("", t0 + 30)
    # BRNWr's 27 kills make szucsu (12k, 0.71 K/D on a loss) the team's liability -> 2 posts
    assert await poller.poll_once(allow_post=True) == 2
    kinds = [files[0].filename.split("-")[0] for _, files, _ in channel.posts]
    assert kinds == [PostKind.REDEMPTION.value, PostKind.LIABILITY.value]
    view = channel.posts[0][0]
    assert "REDEMPTION" in json.dumps(view.to_components())


class _FakeLeetify:
    def __init__(self, ratings=None):
        self.ratings = ratings
        self.rate_limited = False
        self.calls = 0

    async def match_ratings(self, mid):
        self.calls += 1
        return self.ratings

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_routing_and_instant_postmortem_with_leetify_upgrade(fixtures, tracked, tmp_path, env):
    app, fake = _app(fixtures, tracked, tmp_path)
    app.settings.leetify_enabled = True
    app.settings.postmortem_auto = True
    app.settings.postmortem_leetify_wait_minutes = 360
    shame, fame, pm = FakeChannel(), FakeChannel(), FakeChannel()
    poller = Poller(FakeClient(shame), app)
    poller.channel = shame
    poller.channels = {"fame": fame, "postmortem": pm}
    poller.client.get_channel = lambda cid: {shame.id: shame, fame.id: fame, pm.id: pm}.get(cid)
    app.leetify = _FakeLeetify(None)  # Leetify has not processed anything yet
    await app.refresh_all_snapshots()
    brnwr = next(p for p, n in tracked.items() if n == "BRNWr")
    t0 = app.state.snapshot(brnwr).elo_ts

    # 1) double-feature shame match: shame post in #shame AND an immediate post-mortem in #postmortem
    mid = fake.add_match("_shame", t0 + 10)
    assert await poller.poll_once(allow_post=True) == 1
    assert [f[0].filename.split("-")[0] for _, f, _ in shame.posts] == ["shame"] and not fame.posts
    assert len(pm.posts) == 1 and pm.posts[0][1][0].filename == "postmortem.png"
    assert "hasn't processed" in json.dumps(pm.posts[0][0].to_components())
    pending = app.state.pending_postmortems[mid]
    assert pending["channel_id"] == pm.id and pending["message_id"] in pm.messages

    # Leetify still behind -> nothing edited, still pending
    assert await poller.upgrade_postmortems() == 0 and mid in app.state.pending_postmortems
    # Leetify catches up -> the same message is edited, no second post
    app.leetify = _FakeLeetify({row.steam_id: 0.01 * i for i, row in enumerate(app.state.match_outcomes[mid].teams[0].players)})
    assert await poller.upgrade_postmortems() == 1
    msg = pm.messages[pending["message_id"]]
    assert len(pm.posts) == 1 and len(msg.edits) == 1 and msg.edits[0][1][0].filename == "postmortem.png"
    assert "Leetify" in json.dumps(msg.edits[0][0].to_components()) and "hasn't processed" not in json.dumps(msg.edits[0][0].to_components())
    assert not app.state.pending_postmortems

    # 2) a glory game goes to the fame channel; single tracked player on the team -> no auto post-mortem
    big = copy.deepcopy(fixtures["stats"])
    for t in big["rounds"][0]["teams"]:
        for p in t["players"]:
            if p["player_id"] == brnwr:
                p["player_stats"]["Kills"] = "33"
    fake.fixtures = dict(fixtures, stats=big)
    app.settings.postmortem_min_tracked = 3
    fake.add_match("", t0 + 20)
    assert await poller.poll_once(allow_post=True) >= 1
    assert any(f[0].filename.startswith("glory") for _, f, _ in fame.posts)
    assert len(pm.posts) == 1


@pytest.mark.asyncio
async def test_postmortem_leetify_wait_expires(fixtures, tracked, tmp_path, env):
    app, fake = _app(fixtures, tracked, tmp_path)
    app.settings.leetify_enabled = True
    app.settings.postmortem_auto = True
    app.settings.postmortem_leetify_wait_minutes = 120
    pm = FakeChannel()
    poller = Poller(FakeClient(pm), app)
    poller.channel = pm
    app.leetify = _FakeLeetify(None)
    await app.refresh_all_snapshots()
    old = app.state.snapshot(next(iter(tracked))).elo_ts - 3 * 3600  # finished 3 h ago -> already past the wait
    mid = fake.add_match("_shame", old)
    await poller.poll_once(allow_post=True)
    assert sum(f[0].filename == "postmortem.png" for _, f, _ in pm.posts) == 1 and mid in app.state.pending_postmortems
    assert await poller.upgrade_postmortems() == 0
    assert not app.state.pending_postmortems  # dropped, message left as posted


@pytest.mark.asyncio
async def test_postmortem_with_leetify_at_post_time_is_not_queued(fixtures, tracked, tmp_path, env):
    app, fake = _app(fixtures, tracked, tmp_path)
    app.settings.leetify_enabled = True
    app.settings.postmortem_auto = True
    pm = FakeChannel()
    poller = Poller(FakeClient(pm), app)
    poller.channel = pm
    await app.refresh_all_snapshots()
    rec = _record_for(fixtures, tracked, "_shame")
    app.leetify = _FakeLeetify({row.steam_id: 0.02 for row in rec.teams[0].players})
    fake.add_match("_shame", app.state.snapshot(next(iter(tracked))).elo_ts + 5)
    await poller.poll_once(allow_post=True)
    assert sum(f[0].filename == "postmortem.png" for _, f, _ in pm.posts) == 1 and not app.state.pending_postmortems


def _record_for(fixtures, tracked, suffix):
    from shamebot.models import parse_match
    return parse_match("x", fixtures["stats" + suffix], fixtures["details" + suffix], tracked, kill_threshold=10)


@pytest.mark.asyncio
async def test_legacy_pending_postmortem_is_posted(fixtures, tracked, tmp_path, env):
    """Entries queued by the old wait-for-Leetify build (no message id) get posted immediately."""
    app, fake = _app(fixtures, tracked, tmp_path)
    app.settings.leetify_enabled = True
    app.settings.postmortem_auto = True
    pm = FakeChannel()
    poller = Poller(FakeClient(pm), app)
    poller.channel = pm
    app.leetify = _FakeLeetify(None)
    await app.refresh_all_snapshots()
    t0 = app.state.snapshot(next(iter(tracked))).elo_ts
    mid = fake.add_match("_shame", t0 + 10)
    await poller.poll_once(allow_post=False)  # cached + processed, nothing posted
    app.state.pending_postmortems[mid] = {"finished_at": t0 + 10}  # old format
    assert await poller.upgrade_postmortems() == 0
    assert [f[0].filename for _, f, _ in pm.posts] == ["postmortem.png"]
    assert app.state.pending_postmortems[mid]["message_id"]  # re-queued for the Leetify upgrade
