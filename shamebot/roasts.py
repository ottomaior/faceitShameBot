"""Roast / excuse / praise lines. Brutal but strictly about the game and the numbers."""
from __future__ import annotations

import logging
import os
import random
import re
from dataclasses import dataclass

from .blame import compute_blame, compute_carry
from .models import MatchRecord, TrackedResult

log = logging.getLogger(__name__)

# {nick} {kills} {deaths} {map} {adr} {kd} {hs} {rounds} {score} {mvps} are available in every line, plus the
# extended stats {clutches} {clutch_wins} {entries} {entry_wins} {nades} {damage_nades} {util_dmg} {flashes}
# {enemies_flashed} (0 for matches cached before schema v3) and {blame} (% of the team's shortfall, see blame.py).

TIER_S = [  # 0-2 kills
    "{kills} kills. The bomb had more impact on this match than you did.",
    "{kills} kills in {rounds} rounds. The spectators had a better K/D.",
    "You were a fifth player the way a cardboard cutout is a bodyguard.",
    "{kills} kills. At this point the enemy team owes you a thank-you card.",
    "Congratulations on the {kills}-kill masterclass. The bots in Deathmatch are taking notes.",
    "{kills} kills — you didn't play {map}, you toured it.",
    "The scoreboard checked twice to make sure you were actually connected.",
    "{kills} kills and {deaths} deaths. That's not a stat line, that's a police report.",
    "Every round you were alive was a round your team played a 4v5 by choice.",
    "{kills} kills. The defuse kit did more work and it doesn't even have hands.",
    "You got {kills} kills. Your ping had more digits than your frag count.",
    "{kills} kills: a performance so quiet the server thought you'd gone AFK.",
    "The enemy team's only complaint about you was that you kept ending the round early — for them.",
    "{kills} kills. You were the loot drop, not the player.",
    "You were less of a teammate and more of a respawn timer with a mic.",
]

TIER_A = [  # 3-5 kills
    "{kills} kills in {rounds} rounds. The server browser had more action.",
    "{kills} kills. You were furniture with a headset.",
    "{kills} kills and {deaths} deaths on {map}. The enemy sniper sent flowers.",
    "{kills} kills. You didn't hold the site, you haunted it.",
    "Five players on your team, and you brought {kills} kills and a lot of opinions.",
    "{kills} kills. Somewhere a bot in casual mode is feeling superior.",
    "{kills} kills: the kind of number you'd expect from someone who thinks 'entry frag' is a menu item.",
    "You ended with {kills} kills, which is one kill for every time you asked for a drop.",
    "{kills} kills. The killfeed forgot your name halfway through.",
    "{kills} kills on {map}. You didn't lose gunfights, you attended them.",
    "The enemy didn't have to play around you. They played through you.",
    "{kills} kills and {mvps} MVPs. Your most impactful round was the warmup.",
    "{kills} kills. You weren't baiting — baiting implies a plan.",
    "{kills} kills: the exact number your teammates will bring up for the next six weeks.",
    "You had {kills} kills and {deaths} deaths. That's a {kd} K/D. Your calculator is embarrassed too.",
]

TIER_B = [  # 6-9 kills
    "{kills} kills. So close to double digits, so far from relevance.",
    "{kills} kills. Almost a real score. Almost a real player.",
    "{kills} kills — the enemy called you 'free' and honestly, they weren't wrong.",
    "{kills} kills on {map}. The map has more callouts than you had frags.",
    "{kills} kills and {deaths} deaths: you traded like a crypto bro in 2022.",
    "{kills} kills. Your ADR of {adr} says you also didn't hurt them on the way out.",
    "{kills} kills. You weren't the problem, but you were definitely a symptom.",
    "{kills} kills. The gap between you and 10 was small. The gap between you and useful was not.",
    "{kills} kills with {hs}% headshots. Center mass is a strategy, technically.",
    "{kills} kills. Every one of them was a gift from the enemy economy.",
    "{kills} kills. You put up numbers — just not the kind anyone wanted.",
    "{kills} kills, {deaths} deaths. Your team lost rounds; you lost duels. Teamwork.",
    "{kills} kills. The wall of shame accepts single digits. Welcome back.",
    "{kills} kills on {map}. Not a bottom-frag, just a bottom-tier one.",
    "{kills} kills. If only the enemy stood still like you do on rotations.",
]

TIER_GREY = [  # made the kill threshold, stats didn't (grey zone / bait job)
    "{kills} kills. {deaths} deaths. The last few were bought at full price.",
    "{kills} kills and a {kd} K/D. Rushing for the double digits doesn't count as playing.",
    "{kills} kills, {adr} ADR. The scoreboard says you were there; the damage says you weren't.",
    "Congratulations on {kills} kills. The wall counts games, not the last four rounds.",
    "{kills} kills — hunted, not earned. {deaths} deaths say what it cost.",
    "You hit {kills} kills and still made the wall. The rule reads the whole scoreboard now.",
    "{kills} kills with {adr} ADR on {map}. Baiting for stats and still fumbling the stats.",
    "Double digits, {kd} K/D, zero relief. The wall has a grey zone and you live in it.",
    "{kills} kills and {deaths} deaths. You didn't reach double digits, you crawled into them.",
    "{kills} kills. The last two were bought with three deaths and a lost site. Great trade.",
    "A {kd} K/D at {kills} kills. The wall doesn't do rounding.",
    "{kills} kills, {adr} ADR. That's a kill count with nothing behind it.",
    "You hit {kills} and immediately checked the scoreboard. So did we.",
    "{kills} kills on {map}. Numbers went up, standards went down, the wall stayed exactly where it was.",
    "Double digits and still on the wall. The rush for ten cost more than it earned.",
]

MODIFIERS: dict[str, list[str]] = {
    "fed": [
        "Also {deaths} deaths — the enemy's ELO thanks you personally.",
        "{deaths} deaths. You didn't die for the team, you died for content.",
        "{deaths} deaths. The enemy's economy was built on your body.",
    ],
    "low_adr": [
        "ADR {adr}: a flashbang dealt more damage than you did.",
        "{adr} ADR. Your bullets were more of a suggestion.",
        "{adr} ADR. You were shooting warning shots.",
        "ADR {adr}. The enemy healed between rounds; you never gave them a reason not to.",
        "{adr} average damage. Your gun was set to 'notify'.",
    ],
    "zero_mvp": [
        "Zero MVPs, obviously.",
        "MVP count: {mvps}. The server didn't even consider it.",
        "Zero MVPs in {rounds} rounds. The star was in the room, and it wasn't you.",
        "MVPs: 0. Your team won rounds the way weather happens — without you.",
        "No MVP round. Not even the pistol round. Not even by accident.",
    ],
    "headless": [
        "{hs}% headshots — you were aiming at the map, not the model.",
        "{hs}% HS. Crosshair placement: floor.",
        "{hs}% headshots. You aimed at feet and hoped for gravity.",
        "{hs}% HS. Body shots for a body count you never got.",
        "{hs}% headshots — every duel was a lower-body workout.",
    ],
    "carried": [
        "Your team won {score}. Carried like a backpack.",
        "Somehow a win ({score}). Five names on the scoreboard, four players.",
        "A {score} win with you on the team. Your teammates deserve hazard pay.",
        "Won {score}. Four players carried a backpack with a mic in it.",
        "{score}, and your contribution was being in the lobby so the match could start.",
    ],
    "anchor": [
        "Lost {score}. You weren't in the boat, you were the anchor.",
        "{score}. The kind of loss that gets a person's mic checked.",
        "{score}. Your team didn't lose the game, they lost it with you.",
        "Lost {score} and dragged the whole boat down. Anchor, meet sea floor.",
        "{score}. When they review this demo, you're the part they skip.",
    ],
    "bottom_of_lobby": [
        "Lowest kills of all ten players. Unanimous.",
        "Bottom of the whole lobby. Both teams agree on something.",
        "Ten players, one bottom. Congratulations on the consistency.",
        "Last on the entire scoreboard. The enemy's worst player is sending you a fruit basket.",
        "Rock bottom of the lobby. Even the other team's bottom-fragger looked up at you — from above.",
    ],
    "streak": [
        "That's {streak} in a row. This isn't a slump, it's a lifestyle.",
        "Shame streak: {streak}. The wall is starting to feel like home.",
        "{streak} straight wall appearances. At this point you have a parking spot.",
        "Streak {streak}. The wall stopped printing new cards and just laminated yours.",
        "That's {streak} in a row. Consistency is a virtue, just not this kind.",
    ],
    # --- moment modifiers: driven by the extended stats, so they name the actual failure ---
    "clutch_donor": [
        "{clutches} clutch situations, {clutch_wins} won. 'Last alive' was a warning, not a role.",
        "Lost all {clutches} clutches. The enemy's post-plant was a formality.",
        "{clutch_wins}/{clutches} in clutches. Your teammates died so you could disappoint them personally.",
    ],
    "entry_fodder": [
        "{entry_wins}/{entries} opening duels. You weren't the entry, you were the doorbell.",
        "{entries} first peeks, {entry_wins} won. Swinging first so the team could learn where they were.",
        "Entry stats: {entry_wins} of {entries}. A scout, but for the enemy's crosshair.",
    ],
    "nade_hoarder": [
        "{nades} grenades thrown in {rounds} rounds. Saving them for the next match?",
        "{nades} nades all game. The buy menu was purely decorative.",
        "Utility thrown: {nades}. Your smokes are still in the inventory, unopened.",
    ],
    "blank_nades": [
        "{damage_nades} damage nades, {util_dmg} utility damage. Fireworks for the enemy.",
        "Threw {damage_nades} HE and molotovs and hurt nobody. That takes real precision.",
        "{damage_nades} nades, {util_dmg} damage. You were gifting the enemy a light show.",
        "Zero utility damage from {damage_nades} grenades. The molotov was decorative.",
        "{damage_nades} HEs and mollies, nobody hurt. You didn't throw utility, you littered.",
    ],
    "flash_artist": [
        "{flashes} flashbangs, {enemies_flashed} enemies blinded. Your own team saw every one of them.",
        "Popped {flashes} flashes and blinded zero enemies. The math checks out on your side.",
        "{flashes} flashbangs, {enemies_flashed} enemies affected. Your teammates have seen more sun than the enemy did.",
        "Popped {flashes} flashes and blinded only people who trusted you.",
        "{flashes} flashes, {enemies_flashed} enemies blinded. The enemy thanks you for the lighting.",
    ],
    "bait_job": [
        "Grey zone: {grey_reasons}. Kills were never the whole story.",
        "On the wall at {kills} kills because of {grey_reasons}.",
        "Grey zone. {kills} kills, {grey_reasons} — the scoreboard reads the whole row now.",
        "Kill count says {kills}. {grey_reasons} says wall. The wall wins ties.",
        "You rushed for the tenth kill and brought {grey_reasons} with it. Bait job.",
    ],
    "blame_heavy": [
        "{blame}% of that loss is yours. The other four can split the rest.",
        "Blame share: {blame}%. Statistically, you were the reason.",
        "The team lost {score}, and {blame}% of the gap to your best teammate is on your row.",
    ],
}

PRAISE: dict[str, list[str]] = {
    "top_of_lobby": [
        "Top-fragger of all ten. The enemy's best player had {lobby_second} — you had {kills}.",
        "Highest kills in the lobby. Both teams checked the scoreboard for the same reason.",
        "King of the server: {kills} kills, nobody within {lobby_gap} of you.",
        "Top of the lobby by {lobby_gap}. The enemy's best player would like a word, from a distance.",
        "Most kills of anyone in the server. Ten players, one problem.",
        "{kills} kills, first among ten. The enemy's top-fragger had {lobby_second} and a headache.",
    ],
    "hard_carry": [
        "{carry}% of the team's output. The other four were on the same bus, just not driving.",
        "Hard carry: {kill_share}% of the team's kills, {damage_share}% of its damage.",
        "Carried {score} at {carry}% of the team's output. Your teammates should split the ELO and send you the rest.",
        "{carry}% of the team's output. Four passengers, one engine, {score} on the board.",
        "Carried at {kill_share}% of the team's kills. The other four were there for the ELO.",
        "{carry}% of everything the team did. The rest was moral support.",
        "A {score} win that was {carry}% you. Your teammates' ELO is a gift and they know it.",
    ],
    "wasted": [
        "Lost {score} anyway. {carry}% of the team's output, wasted on four passengers.",
        "{kills} kills in a loss. The scoreboard knows who to blame, and it isn't you.",
        "A {kills}-kill loss. Somewhere a teammate is writing an excuse for this card.",
        "{carry}% of the team's output and a loss. That's not a game, that's a hostage situation.",
        "{kills} kills for nothing. Four teammates owe you a match, a drink and an apology.",
        "Lost {score} with {carry}% of the team's output on your row. You were the plan; nobody else read it.",
        "A loss at {kill_share}% of the team's kills. You didn't get beaten, you got outnumbered by your own team.",
    ],
    "untouchable": [
        "K/D {kd}. The enemy had to kill you twice as often just to break even.",
        "{kd} K/D. Dying was optional and you mostly declined.",
        "{kd} K/D. The enemy needed a plan for you and brought a wish.",
        "K/D {kd}. You died {deaths} times, mostly out of politeness.",
        "{kd} K/D on {map}. Their crosshair placement was a rumour.",
    ],
    "mvp_machine": [
        "{mvps} MVPs. The round-win star was basically your profile picture.",
        "{mvps} MVP rounds. The other four got to hear the sound effect, at least.",
        "{mvps} MVP rounds. The star icon should be a loyalty card by now.",
        "{mvps} MVPs. The team won rounds; you won the game.",
    ],
    "headhunter": [
        "{hs}% headshots. Body armour was a waste of the enemy's money.",
        "{hs}% HS. Their helmets were a formality.",
        "{hs}% headshots. The enemy's helmets are filing for unemployment.",
        "{hs}% HS at {kills} kills. Every duel was one click long.",
    ],
    "clutch_king": [
        "{clutch_wins} clutches won. 'Last alive' was the enemy's cue to save.",
        "Won {clutch_wins} of {clutches} clutches. Your team's plan B was just you.",
        "{clutch_wins} clutches. The enemy's post-plant was just a countdown to you.",
        "{clutch_wins} clutch rounds won. 'Last alive' was your best position.",
    ],
    "entry_king": [
        "Won {entry_wins} of {entries} opening duels. The round started when you said so.",
        "{entry_wins}/{entries} entries. Every site was open by the time your team arrived.",
        "{entry_wins} of {entries} opening duels. The round began with a funeral and you sent the invite.",
        "Entry stats: {entry_wins}/{entries}. Every site was pre-cleared by the time your team peeked.",
    ],
    "utility_master": [
        "{util_dmg} utility damage. Your grenades outfragged some players in this lobby.",
        "{util_dmg} damage from nades alone. The molotov had its own K/D.",
        "{util_dmg} utility damage. Your nades had a better ADR than some players.",
        "{util_dmg} damage from grenades. The molotov deserves its own card.",
    ],
}

PRAISE_ORDER = ("hard_carry", "wasted", "top_of_lobby", "untouchable", "clutch_king", "entry_king", "mvp_machine", "headhunter", "utility_master")

# Modifiers that describe a specific failure get picked before the generic ones.
MOMENT_MODIFIERS = ("bait_job", "blame_heavy", "clutch_donor", "entry_fodder", "nade_hoarder", "blank_nades", "flash_artist")

EXCUSES = [
    "My mouse double-clicked.",
    "Ping was 200 and it only spiked when I peeked.",
    "I was on an eco every single round.",
    "Teammates baited me. All four of them. Every round.",
    "New sensitivity, still adjusting.",
    "Playing on a laptop trackpad, actually.",
    "Sun in my eyes.",
    "Monitor was on 60 Hz, I just noticed.",
    "I was cooking dinner between rounds.",
    "Headset died, played by vibes.",
    "The map was updated and nobody told me.",
    "Cat walked across the keyboard on the pistol round.",
    "I was warming up for the next one.",
    "Windows update started mid-match.",
    "I'm testing a support role. It's very supportive.",
    "The enemy was clearly 5-stack cheating (Level 4s).",
    "My chair is broken.",
    "I had the wrong crosshair on.",
    "I was drunk, and honestly that's the good version.",
    "Discord overlay was covering the radar.",
    "I got matched against smurfs. Level 10 smurfs. On level 6 accounts.",
    "My hands were cold.",
    "Someone was watching over my shoulder.",
    "I was throwing to lower the ELO for the stack. Strategic.",
    "I only lost duels I wasn't supposed to win.",
    "Motion blur was on. Someone enabled it. Not me.",
    "The FaceIT anti-cheat was using all my CPU.",
    "I ran out of energy drink in the second half.",
    "The server was in Sweden, I was in a different mood.",
    "New keyboard, the W key is further left than my old one.",
    "I was on the phone with my mom.",
    "The other team was communicating. Unfair.",
    "My internet provider does maintenance whenever I'm on CT.",
    "I don't play this map. I've never played this map. Which map was it?",
    "I'm saving my aim for LAN.",
    "It was 3 AM and my ADR is nocturnal.",
    "I got flashed nine times by my own team. I counted.",
    "This isn't my main.",
    "I was checking the scoreboard to see how badly we were losing. Every round.",
    "Bad round. Twenty times.",
]

REDEMPTION_LINES = [
    "{kills} kills after {streak} straight wall appearances. The prophecy was real.",
    "From {streak} shames to {kills} kills. Someone found the aim setting.",
    "{kills} kills on {map}. The redemption arc nobody asked for and everybody needed.",
    "{kills} kills. The wall of shame is losing a regular. Temporarily.",
    "After {streak} shames in a row, {kills} kills — the mouse must have been plugged in this time.",
    "{kills} kills and {mvps} MVPs. The comeback is louder than the excuses ever were.",
]

GLORY_LINES = [
    "{kills} kills on {map}. The enemy team is filing a complaint.",
    "{kills} kills. That's not a stat line, that's a demo the enemy will review.",
    "{kills} kills and {adr} ADR. The server briefly considered giving you a second name slot.",
    "{kills} kills. Everyone on the wall of shame is now legally your fan.",
    "{kills} kills, {deaths} deaths. Certified problem.",
    "{kills} kills. Somebody buy this person a crosshair sponsorship.",
    "{kills} kills with a {kd} K/D. The enemy's excuses are already in the group chat.",
    "{kills} kills on {map}. Five enemies queued up, one of them left a review.",
    "{kills} kills and {mvps} MVPs. The team played a 5v5; you played a highlight reel.",
    "{kills} kills. Your teammates' job today was to hold your drink.",
    "{kills} kills, {hs}% headshots. Every peek was an appointment.",
    "{kills} kills. The other nine players were extras in your demo.",
    "{score} with {kills} kills. The enemy team is checking your account for a ban.",
    "{kills} kills at {adr} ADR. The killfeed had your name on speed dial.",
    "{kills} kills. This one goes on the fridge.",
    "{kills} kills. The enemy IGL called a timeout to discuss you specifically.",
    "{kills} kills on {map}. Their whole strat became 'where is {nick}'.",
    "{kills} kills. The enemy team is going to watch this demo with the lights off.",
    "{kills} kills, {deaths} deaths. You didn't win duels, you collected them.",
    "{kills} kills. The other team's spawn was just your queue.",
    "{kills} kills and a {kd} K/D. The enemy learned your name the hard way, {kills} times.",
    "{kills} kills. Half the lobby is reporting you, the other half is adding you.",
    "{kills} kills on {map}. The map was 5v5 on paper and 1v5 in practice.",
    "{kills} kills. The enemy's economy was your personal ATM.",
    "{kills} kills. There's a smurf accusation with your name on it and honestly, fair.",
    "{kills} frags. The enemy team is now a support group with a shared trauma.",
    "{kills} kills and {mvps} MVPs. Somebody check this account for a second keyboard.",
]

LIABILITY_LINES = [
    "{kills} kills. Your four teammates averaged {team_avg_kills}. They noticed.",
    "{kills} kills, {blame}% of the blame. Double digits don't buy innocence.",
    "Not on the wall, but not off the hook: {blame}% of that {score} is on your row.",
    "{kills} kills sounds fine until you see the best player on your team had {team_best_kills}.",
    "Last on your own team with {kills} kills. The wall has standards; this is the waiting room.",
    "{score}. {blame}% yours. The other four are drafting the group chat message.",
    "You avoided the wall by {kills_over} kill{kills_over_s}. You did not avoid the scoreboard.",
]

LIABILITY_WIN_LINES = [
    "Won {score} — with {kills} kills and {blame}% of the team's shortfall. Carried, technically.",
    "A win, somehow. {kills} kills, last on the team, {blame}% of the gap. Thank your teammates.",
    "{score} and you were the passenger: {kills} kills while the best on your team had {team_best_kills}.",
    "{kills} kills in a {score} win. The four people who actually won it have questions.",
    "Won {score}. {blame}% of the team's shortfall was your seat on the bus.",
    "A W with {kills} kills and the lowest impact on the team. ELO went up; standards did not.",
    "{score}. Your teammates won a 4v5 and you got the same ELO for watching.",
]

ACE_LINES = [
    "An ace on {map}. Five people, one clip, zero respect.",
    "ACE. The enemy team just experienced a group activity.",
    "One round, five kills, {kills} total. The highlight reel writes itself.",
    "Five kills in one round and {kills} in the game. The enemy's round-loss bonus was earned honestly.",
    "An ace and {kills} kills. The other team is now a support group.",
    "ACE on {map}. Five funerals, one invoice.",
]


_SINGULAR = {"clutches": "clutch", "flashes": "flash", "enemies": "enemy"}


@dataclass
class RoastContext:
    nick: str
    kills: int
    deaths: int
    map: str
    adr: float
    kd: float
    hs: int
    rounds: int
    score: str
    mvps: int
    streak: int = 0
    awards: tuple[str, ...] = ()
    # extended stats (0 when the record predates schema v3)
    clutches: int = 0
    clutch_wins: int = 0
    entries: int = 0
    entry_wins: int = 0
    nades: int = 0
    damage_nades: int = 0
    util_dmg: int = 0
    flashes: int = 0
    enemies_flashed: int = 0
    blame: int = 0  # % of the team's shortfall (blame.py); 0 when unknown
    blame_heavy: bool = False
    grey_reasons: str = ""  # 'K/D 0.52, ADR 48' — why a grey-zone game counted (filled by app)
    carry: int = 0  # % of the team's impact (blame.compute_carry)
    kill_share: int = 0
    damage_share: int = 0
    lobby_second: int = 0  # best kill count among the other nine players
    lobby_gap: int = 0
    team_avg_kills: int = 0  # the other teammates' average kills
    team_best_kills: int = 0
    kills_over: int = 0  # kills above the wall threshold (liability lines)
    kills_over_s: str = "s"

    @classmethod
    def from_match(cls, record: MatchRecord, r: TrackedResult, *, streak: int = 0) -> "RoastContext":
        score = ""
        if r.team_score is not None and r.enemy_score is not None:
            score = f"{r.team_score}–{r.enemy_score}"
        pid = next((p for p, x in record.players.items() if x is r), None)
        blame = compute_blame(record, pid) if pid else None
        team = next((t for t in record.teams if any(row.pid == pid for row in t.players)), None)
        others = [row.k for row in team.players if row.pid != pid] if team else []
        carry = compute_carry(record, pid) if pid else None
        lobby = [row.k for t in record.teams for row in t.players if row.pid != pid]
        return cls(
            nick=r.nickname,
            kills=r.kills,
            deaths=r.deaths or 0,
            map=record.map_label,
            adr=r.adr or 0.0,
            kd=r.kd if r.kd is not None else 0.0,
            hs=r.hs_pct or 0,
            rounds=record.rounds or 0,
            score=score,
            mvps=r.mvps or 0,
            streak=streak,
            awards=tuple(r.awards),
            clutches=(r.c1v1 or 0) + (r.c1v2 or 0),
            clutch_wins=(r.w1v1 or 0) + (r.w1v2 or 0),
            entries=r.entry_count or 0,
            entry_wins=r.entry_wins or 0,
            nades=(r.utility_count or 0) + (r.flash_count or 0),
            damage_nades=r.utility_count or 0,
            util_dmg=r.utility_damage or 0,
            flashes=r.flash_count or 0,
            enemies_flashed=r.enemies_flashed or 0,
            blame=blame.share if blame else 0,
            blame_heavy=bool(blame and blame.lost and blame.heavy),
            team_avg_kills=round(sum(others) / len(others)) if others else 0,
            team_best_kills=max(others) if others else 0,
            carry=carry.share if carry else 0,
            kill_share=carry.kill_share if carry else 0,
            damage_share=carry.damage_share if carry else 0,
            lobby_second=max(lobby) if lobby else 0,
            lobby_gap=max(0, r.kills - max(lobby)) if lobby else 0,
        )

    def fmt(self, line: str) -> str:
        try:
            out = line.format(**self.__dict__)
        except (KeyError, IndexError, ValueError):
            return line
        # "1 kills" -> "1 kill", but leave "21 kills" alone
        return re.sub(
            r"(?<!\d)1 (kills|deaths|MVPs|clutches|grenades|nades|flashes|flashbangs|enemies)\b",
            lambda m: "1 " + _SINGULAR.get(m.group(1), m.group(1)[:-1]),
            out,
        )


RECENT_CAP = 40  # lines remembered so consecutive posts don't repeat themselves


class RoastEngine:
    def __init__(self, *, level: str = "brutal", custom_file: str | None = None, recent: list[str] | None = None) -> None:
        self.level = level
        self.custom: list[str] = []
        # Templates used in the latest posts (persisted by the app). A pick avoids these while the
        # pool has anything else to offer; the same list object is shared with State so it survives restarts.
        self.recent: list[str] = recent if recent is not None else []
        if custom_file:
            self.load_custom(custom_file)

    def load_custom(self, path: str) -> None:
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                self.custom = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            log.info("Loaded %d custom roast line(s) from %s", len(self.custom), path)
        except OSError as exc:
            log.warning("Could not read %s: %s", path, exc)

    @staticmethod
    def _rng(*seed_parts: str) -> random.Random:
        return random.Random("|".join(seed_parts))

    def _pick(self, rng: random.Random, pool: list[str]) -> str:
        """Choose a template, skipping ones used recently, and remember it."""
        fresh = [ln for ln in pool if ln not in self.recent] or list(pool)
        line = rng.choice(fresh)
        self.recent.append(line)
        del self.recent[:-RECENT_CAP]
        return line

    def _tier(self, kills: int, awards: tuple[str, ...] = ()) -> list[str]:
        if "bait_job" in awards:
            return TIER_GREY
        if kills <= 2:
            return TIER_S
        if kills <= 5:
            return TIER_A
        return TIER_B

    def roast(self, ctx: RoastContext, *, seed: str) -> str:
        rng = self._rng("roast", seed, ctx.nick)
        pool = list(self._tier(ctx.kills, ctx.awards))
        if self.custom:
            pool.extend(self.custom)  # inside jokes get the same odds as the built-ins
        line = ctx.fmt(self._pick(rng, pool))

        mods: list[str] = []
        keyed = {
            "fed": "fed" in ctx.awards,
            "low_adr": ctx.adr and ctx.adr < 40,
            "zero_mvp": "zero_mvp" in ctx.awards,
            "headless": "headless" in ctx.awards,
            "carried": "carried" in ctx.awards,
            "anchor": "anchor" in ctx.awards,
            "bottom_of_lobby": "bottom_of_lobby" in ctx.awards,
            "streak": ctx.streak >= 3,
        }
        for key in MOMENT_MODIFIERS:
            keyed[key] = key in ctx.awards
        keyed["blame_heavy"] = ctx.blame_heavy
        keyed["bait_job"] = "bait_job" in ctx.awards and bool(ctx.grey_reasons)
        if keyed["bait_job"]:
            keyed["blame_heavy"] = False  # the grey-zone reasons already spell out the blame share
        # Specific failures (lost every clutch, threw no nades…) beat the generic jabs,
        # but at most one of each kind so the roast doesn't turn into a stat sheet.
        moments = [k for k in MOMENT_MODIFIERS if keyed[k]]
        generic = [k for k, on in keyed.items() if on and k not in MOMENT_MODIFIERS]
        rng.shuffle(moments)
        rng.shuffle(generic)
        candidates = (moments[:1] + generic + moments[1:])[:2]
        for key in candidates:
            mods.append(ctx.fmt(self._pick(rng, MODIFIERS[key])))
        return " ".join([line, *mods])

    def excuse(self, *, seed: str | None = None) -> str:
        rng = self._rng("excuse", seed) if seed else random.Random()
        return rng.choice(EXCUSES)

    def redemption(self, ctx: RoastContext, *, seed: str) -> str:
        return ctx.fmt(self._pick(self._rng("redemption", seed, ctx.nick), REDEMPTION_LINES))

    def liability(self, ctx: RoastContext, *, seed: str, won: bool = False, kill_threshold: int = 10) -> str:
        ctx.kills_over = max(0, ctx.kills - kill_threshold + 1)
        ctx.kills_over_s = "" if ctx.kills_over == 1 else "s"
        pool = LIABILITY_WIN_LINES if won else LIABILITY_LINES
        # kill-comparison lines only land when the gap is visible; otherwise the blame share carries it
        if ctx.team_avg_kills - ctx.kills < 4:
            pool = [ln for ln in pool if "{team_avg_kills}" not in ln] or pool
        if ctx.team_best_kills - ctx.kills < 6:
            pool = [ln for ln in pool if "{team_best_kills}" not in ln] or pool
        return ctx.fmt(self._pick(self._rng("liability", seed, ctx.nick), pool))

    def glory(self, ctx: RoastContext, *, seed: str, ace: bool = False) -> str:
        rng = self._rng("glory", seed, ctx.nick)
        pool = ACE_LINES if ace else GLORY_LINES
        line = ctx.fmt(self._pick(rng, pool))
        keys = [k for k in PRAISE_ORDER if k in ctx.awards]
        if "hard_carry" in keys or "wasted" in keys:  # the carry line says it; top-of-lobby would repeat it
            keys = [k for k in keys if k != "top_of_lobby"]
        rng.shuffle(keys)
        # the carry/wasted verdict always speaks first when present
        keys.sort(key=lambda k: 0 if k in ("hard_carry", "wasted") else 1)
        mods = [ctx.fmt(self._pick(rng, PRAISE[k])) for k in keys[:2]]
        return " ".join([line, *mods])
