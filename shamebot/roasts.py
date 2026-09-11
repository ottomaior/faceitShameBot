"""Roast / excuse / praise lines. Brutal but strictly about the game and the numbers."""
from __future__ import annotations

import logging
import os
import random
import re
from dataclasses import dataclass

from .models import MatchRecord, TrackedResult

log = logging.getLogger(__name__)

# {nick} {kills} {deaths} {map} {adr} {kd} {hs} {rounds} {score} {mvps} are available in every line.

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

MODIFIERS: dict[str, list[str]] = {
    "fed": [
        "Also {deaths} deaths — the enemy's ELO thanks you personally.",
        "{deaths} deaths. You didn't die for the team, you died for content.",
        "{deaths} deaths. The enemy's economy was built on your body.",
    ],
    "low_adr": [
        "ADR {adr}: a flashbang dealt more damage than you did.",
        "{adr} ADR. Your bullets were more of a suggestion.",
    ],
    "zero_mvp": [
        "Zero MVPs, obviously.",
        "MVP count: {mvps}. The server didn't even consider it.",
    ],
    "headless": [
        "{hs}% headshots — you were aiming at the map, not the model.",
        "{hs}% HS. Crosshair placement: floor.",
    ],
    "carried": [
        "Your team won {score}. Carried like a backpack.",
        "Somehow a win ({score}). Five names on the scoreboard, four players.",
    ],
    "anchor": [
        "Lost {score}. You weren't in the boat, you were the anchor.",
        "{score}. The kind of loss that gets a person's mic checked.",
    ],
    "bottom_of_lobby": [
        "Lowest kills of all ten players. Unanimous.",
        "Bottom of the whole lobby. Both teams agree on something.",
    ],
    "streak": [
        "That's {streak} in a row. This isn't a slump, it's a lifestyle.",
        "Shame streak: {streak}. The wall is starting to feel like home.",
    ],
}

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
]

ACE_LINES = [
    "An ace on {map}. Five people, one clip, zero respect.",
    "ACE. The enemy team just experienced a group activity.",
    "One round, five kills, {kills} total. The highlight reel writes itself.",
]


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

    @classmethod
    def from_match(cls, record: MatchRecord, r: TrackedResult, *, streak: int = 0) -> "RoastContext":
        score = ""
        if r.team_score is not None and r.enemy_score is not None:
            score = f"{r.team_score}–{r.enemy_score}"
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
        )

    def fmt(self, line: str) -> str:
        try:
            out = line.format(**self.__dict__)
        except (KeyError, IndexError, ValueError):
            return line
        # "1 kills" -> "1 kill", but leave "21 kills" alone
        return re.sub(r"(?<!\d)1 (kills|deaths|MVPs)\b", lambda m: "1 " + m.group(1)[:-1], out)


class RoastEngine:
    def __init__(self, *, level: str = "brutal", custom_file: str | None = None) -> None:
        self.level = level
        self.custom: list[str] = []
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

    def _tier(self, kills: int) -> list[str]:
        if kills <= 2:
            return TIER_S
        if kills <= 5:
            return TIER_A
        return TIER_B

    def roast(self, ctx: RoastContext, *, seed: str) -> str:
        rng = self._rng("roast", seed, ctx.nick)
        pool = list(self._tier(ctx.kills))
        if self.custom:
            pool.extend(self.custom)  # inside jokes get the same odds as the built-ins
        line = ctx.fmt(rng.choice(pool))

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
        candidates = [k for k, on in keyed.items() if on]
        rng.shuffle(candidates)
        for key in candidates[:2]:
            mods.append(ctx.fmt(rng.choice(MODIFIERS[key])))
        return " ".join([line, *mods])

    def excuse(self, *, seed: str | None = None) -> str:
        rng = self._rng("excuse", seed) if seed else random.Random()
        return rng.choice(EXCUSES)

    def redemption(self, ctx: RoastContext, *, seed: str) -> str:
        return ctx.fmt(self._rng("redemption", seed, ctx.nick).choice(REDEMPTION_LINES))

    def glory(self, ctx: RoastContext, *, seed: str, ace: bool = False) -> str:
        pool = ACE_LINES if ace else GLORY_LINES
        return ctx.fmt(self._rng("glory", seed, ctx.nick).choice(pool))
