# FaceIT Wall of Shame Bot

Discord bot that watches your teammates' CS2 FaceIT matches and posts a **Wall of Shame** card
whenever one of them finishes with fewer than 10 kills (configurable) — with a rendered scoreboard
image, a stats-based roast, award badges, ELO change, and buttons for excuses.

It also posts **Redemption Arc** cards (a shame-streaker finally drops 25+), **Highlight** cards
(30-bombs, aces, hard carries) and **Liability** cards (dodged the wall on kills, still the reason the team lost),
tracks ELO, and answers slash commands with player cards and leaderboards.

## What gets posted

| Post | Trigger | Look |
|---|---|---|
| 🧱 **Wall of Shame** | tracked player `< KILL_THRESHOLD` kills — or in the **grey zone** (`KILL_THRESHOLD` … `+SHAME_GREY_ZONE-1` kills) when two of three signals say the game was still bad: K/D `< SHAME_GREY_KD`, ADR `< SHAME_GREY_ADR`, worst on the team with blame ≥ `SHAME_GREY_BLAME_SHARE` on a loss/close win (one extreme signal — K/D `< SHAME_GREY_KD_HARD` or ADR `< SHAME_GREY_ADR_HARD` — is enough on its own). Kills a late rush inflates; deaths and ADR it doesn't, so rushing for the tenth kill no longer helps. Grey-zone posts get a 🎣 *Bait job* badge and a *GREY ZONE* headline | crimson card: avatar, level, giant kill count, "CERTIFIED BOT" stamp, K/D · ADR · HS% · MVPs · Deaths tiles, awards, roast, last-10 form, full scoreboard. Two teammates in one game → *Double Feature*. |
| 🌅 **Redemption Arc** | shame streak ≥ 2 going in, then `≥ REDEMPTION_KILLS` | green card |
| 🏆 **Wall of Fame** | `≥ GLORY_KILLS` kills, an ace (15+ kills), or a **carry**: best impact on his team with ≥ `FAME_CARRY_SHARE`% of its output, `≥ FAME_CARRY_KILLS` kills and K/D ≥ `FAME_CARRY_KD` or ADR ≥ `FAME_CARRY_ADR` — on a loss he must also be the top-fragger of the lobby. Two tracked players who are the team's top two on a win, both clear the kill and K/D/ADR gates and together own ≥ 2×`FAME_CARRY_SHARE` of the output post together as a 🤝 **Duo carry** (`GLORY_POSTS_ENABLED`) | gold card: fame awards (👑 Top of the lobby · 🚂 Hard carry / 🥀 Wasted · 🃏 Ace · 🛡️ Untouchable · ⭐ MVP machine · 🎯 Headhunter · 🧊 Clutch king · 🚀 Entry king · 🧪 Utility master), praise line with award-driven follow-ups, and a **carry report** strip (share of the team's output, kill/damage/MVP share, rank, facts) |
| 🪨 **Liability** | `≥ KILL_THRESHOLD` kills but still the team's worst by a wide margin — blame share ≥ `LIABILITY_BLAME_SHARE` on a loss, or on a win by ≤ `LIABILITY_CLOSE_WIN_MARGIN` rounds / in OT; K/D must be < `LIABILITY_MAX_KD` | slate card, `LIABILITY` stamp, blame strip front and centre |
| 📅 **Weekly digest** | optional, `WEEKLY_DIGEST_*` | podium leaderboard + bot of the week, biggest ELO loss… |

Every post is a Discord **Components V2** message (accent bar, avatar thumbnail, media gallery,
buttons): **Open on FaceIT** · **🎤 Excuse me** (posts a random excuse on the player's behalf) ·
**📊 Wall** (ephemeral leaderboard). Shame posts get a 🤡 reaction (`SHAME_REACTIONS`).

Awards on shame cards: Bottom of the lobby · Double feature / Triple threat · Hat-trick / Streak N ·
Carried · Anchor · Spectator · Fed · Zero MVP club · Headless — plus **moment awards** from FaceIT's
extended stats that name what actually went wrong and feed the roast with the numbers: 🎁 Clutch donor
(lost every clutch) · 🚪 Entry fodder (opening duels mostly lost) · 🧳 Nade hoarder (≤3 grenades in a full
game) · 💨 Blank nades (6+ damage nades, zero utility damage) · 🔦 Flash artist (5+ flashes, no enemy
blinded) · 🎣 Bait job (grey-zone shame). Thresholds live at the top of `shamebot/rules.py`.

Shame cards also carry a **blame report** — how much of the result is on this player, measured against
his *own four teammates in that game* (all ten scoreboard rows are cached): a `BLAME 41%` pill, a share
bar, his impact rank on the team, and the facts behind it (share of the team's deaths/kills/damage,
lost opening duels, lost clutches, lowest ADR, MVP-less wins). Impact = kills + ½ assists + ADR/10 +
MVPs − ½ deaths; blame share = his part of the team's total shortfall below its best player, so an
even team splits 20/20/20/20/20 and the best player always gets 0. The share is also in the message
line and available to roasts as `{blame}` (`shamebot/blame.py`).

## Slash commands

| Command | What it does |
|---|---|
| `/wall [scope]` | Shame leaderboard image (podium + rows). `recent` = last `POST_HISTORY_LIMIT` games, `all` = every cached match |
| `/glory` | Glory board: best game, clean rate, averages |
| `/profile [player]` | Player card: level, ELO + 7d/30d change + trend, averages, last-10 form, per-map summary |
| `/last [player]` | The player's most recent match as a card |
| `/compare a b [scope]` | Head-to-head **verdict**: 8 weighted categories (Impact, Fragging, Opening, Aim, Clutch, Utility, Teamplay, Consistency) over FaceIT extended stats + Leetify analytics → winner, score, margin, reasons, and a jab |
| `/postmortem [player] [match_id]` | **Who actually played best in the last game.** Ranks all five players of the friends' team against each other (randoms included) — six categories (Impact incl. Leetify's per-match rating, Fragging, Opening, Clutch, Utility, Discipline), a card with score bars and best/worst chips, a headline verdict, one roast per tracked friend, and *kill-feed illusions* (most kills but mid-table, quiet carries, entry fodder…). Also posted automatically after every game with 2+ of you (`POSTMORTEM_AUTO`, see below). Default: the newest match with 2+ tracked players; `player` → their last match; `match_id` → any match |
| `/maps [player]` | Per-map games / win % / avg kills / wall % |
| `/elo` | Tracked players ranked by ELO with 7d/30d deltas |
| `/awards` | Hall of shame: worst game ever, longest streak, biggest ELO loss… |
| `/shametest match_id [kind]` | *(admins)* render the full post for any match (`shame`/`redemption`/`glory`/`liability`/`last`), ignoring the threshold |

`player` autocompletes tracked nicknames and defaults to the caller when mapped via
`FACEIT_DISCORD_MENTIONS`. Commands are limited to the shame channel unless
`STATS_COMMAND_ANY_CHANNEL=true`.

## Setup

### 1. FaceIT API key
Create an app at the [FaceIT Developer Portal](https://developers.faceit.com/) and copy the
**Server-side / Data API** key → `FACEIT_API_KEY`.

### 2. Players
`FACEIT_PLAYER_IDS` accepts FaceIT UUIDs, 17-digit Steam IDs, or exact FaceIT nicknames
(comma-separated). Not Discord IDs.

### 3. Discord bot
1. Create an application at the [Discord Developer Portal](https://discord.com/developers/applications), add a **Bot**, copy the token → `DISCORD_TOKEN`.
2. Invite it with the `bot` + `applications.commands` scopes and **Send Messages**, **Attach Files**,
   **Add Reactions** permissions.
3. Developer Mode → right-click the shame channel → **Copy Channel ID** → `SHAME_CHANNEL_ID`.
4. Optional: your Discord user ID → `ADMIN_DISCORD_IDS` (for `/shametest`) and
   `FACEIT_DISCORD_MENTIONS=Nick:discord_id,...` for pings.

### 4. Run locally
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -r requirements.txt
copy .env.example .env          # then edit .env
python bot.py
```

### 5. Railway (or any host)
The repo has a `Procfile` (`worker: python bot.py`). **Mount a Volume** (e.g. at `/data`) and set
`STATE_FILE=/data/state.json` — Railway's disk is wiped on every deploy, and without a volume the
bot re-runs the full history backfill each time and loses ELO history.

## Environment variables
See [`.env.example`](.env.example) — every variable is documented there. Highlights:

| Variable | Default | Description |
|---|---|---|
| `KILL_THRESHOLD` | `10` | Shame when kills are below this |
| `SHAME_GREY_ZONE` / `SHAME_GREY_KD` / `SHAME_GREY_ADR` / `SHAME_GREY_BLAME_SHARE` / `SHAME_GREY_MIN_SIGNALS` / `SHAME_GREY_KD_HARD` / `SHAME_GREY_ADR_HARD` | `3` / `0.65` / `55` / `35` / `2` / `0.5` / `45` | Grey-zone shame rule (see above); `SHAME_GREY_ZONE=0` disables it. Applies only to matches finished after the feature is first seen (`grey_zone_since` in state) unless `SHAME_GREY_RETROACTIVE=true` |
| `REDEMPTION_KILLS` / `GLORY_KILLS` | `25` / `30` | Redemption / Wall of Fame kill thresholds |
| `FAME_CARRY_KILLS` / `FAME_CARRY_KD` / `FAME_CARRY_ADR` / `FAME_CARRY_SHARE` | `20` / `1.5` / `90` / `28` | Wall of Fame carry rule (see above) |
| `GLORY_POSTS_ENABLED` | `true` | Set `false` for a shame-only channel |
| `LIABILITY_POSTS_ENABLED` / `LIABILITY_BLAME_SHARE` / `LIABILITY_CLOSE_WIN_MARGIN` / `LIABILITY_MAX_KD` | `true` / `35` / `3` / `0.9` | Liability posts (see above). Only for matches finished after the feature is first seen (`liability_since` in state); `LIABILITY_RETROACTIVE=true` lifts that for dry runs. Tune with `python tools/liability_audit.py state.json --share 35` |
| `POLL_INTERVAL_SECONDS` | `300` | `60` recommended: posts and post-mortems land within ~2 min of the match, and per-match ELO deltas stay clean |
| `POST_HISTORY_LIMIT` | `30` | "Recent" window for posts and stats |
| `SHAME_REACTIONS` | `🤡` | Reactions added to shame posts |
| `CUSTOM_ROASTS_FILE` | `roasts_custom.txt` | Your own roast lines (see `roasts_custom.txt.example`) |
| `ADMIN_DISCORD_IDS` | – | Who may run `/shametest` |
| `FAME_CHANNEL_ID` / `POSTMORTEM_CHANNEL_ID` | – | Optional channels for Wall of Fame + Redemption posts and for automatic post-mortems; unset → `SHAME_CHANNEL_ID`. Slash commands work in any configured channel |
| `POSTMORTEM_AUTO` / `POSTMORTEM_MIN_TRACKED` | `true` / `2` | Post a post-mortem automatically, right after every match with that many tracked players on one team |
| `POSTMORTEM_LEETIFY_WAIT_MINUTES` / `POSTMORTEM_LEETIFY_CHECK_SECONDS` | `360` / `180` | If Leetify had not processed the match at post time, keep checking at that interval and edit the post in place with the ratings once it has, for up to that many minutes |
| `LEETIFY_API_KEY` | – | Optional Leetify API key (leetify.com/app/developer) for `/compare` and `/profile`; `LEETIFY_ENABLED=false` turns Leetify off |
| `STATE_FILE` | `state.json` | Persisted cache (use a volume path in production) |

## How it works
- `shamebot/faceit.py` — async FaceIT Data API v4 client (aiohttp).
- `shamebot/poller.py` — every poll: refresh ELO snapshots, fetch each player's recent history,
  process unseen matches oldest-first, attribute ELO deltas, detect posts, mark processed.
- `shamebot/state.py` — `state.json` (schema v2): full 10-player scoreboard + ADR/K-D/HS/MVP/W-L per
  match, ELO history per player. Older kills-only entries are upgraded in the background.
- `shamebot/rules.py` / `roasts.py` / `blame.py` — detection, awards, deterministic roast selection, blame report.
- `shamebot/postmortem.py` — `/postmortem` engine: team-relative min-max scoring per category, callouts, seeded prose.
- `shamebot/leetify.py` / `compare.py` — Leetify public API client (live only, never persisted, "Data
  provided by Leetify" attribution) and the head-to-head verdict engine.
- `shamebot/render/` — Pillow cards (bundled Inter + Bebas Neue fonts, SIL OFL).
- `shamebot/views.py` / `commands.py` — Components V2 messages, persistent buttons, slash commands.

## Development
```bash
pip install -r requirements-dev.txt
pytest                       # offline tests against captured FaceIT payloads in tests/fixtures
python tools/preview.py      # renders sample cards to out/preview/*.png
python tools/liability_audit.py state.json --share 35   # which cached games would be Liability posts
python tools/capture_fixture.py [match_id] [suffix]   # refresh fixtures from the live API
```

**FaceIT history cap:** the Data API returns at most ~1100 matches per player; all-time stats
reflect fetchable history only.
