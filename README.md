# FaceIT Wall of Shame Bot

Discord bot that watches your teammates' CS2 FaceIT matches and posts a **Wall of Shame** card
whenever one of them finishes with fewer than 10 kills (configurable) — with a rendered scoreboard
image, a stats-based roast, award badges, ELO change, and buttons for excuses.

It also posts **Redemption Arc** cards (a shame-streaker finally drops 25+) and **Highlight** cards
(30-bombs / aces), tracks ELO, and answers slash commands with player cards and leaderboards.

## What gets posted

| Post | Trigger | Look |
|---|---|---|
| 🧱 **Wall of Shame** | tracked player `< KILL_THRESHOLD` kills | crimson card: avatar, level, giant kill count, "CERTIFIED BOT" stamp, K/D · ADR · HS% · MVPs · Deaths tiles, awards, roast, last-10 form, full scoreboard. Two teammates in one game → *Double Feature*. |
| 🌅 **Redemption Arc** | shame streak ≥ 2 going in, then `≥ REDEMPTION_KILLS` | green card |
| 🏆 **Highlight** | `≥ GLORY_KILLS` kills or an ace (`GLORY_POSTS_ENABLED`) | gold card |
| 📅 **Weekly digest** | optional, `WEEKLY_DIGEST_*` | podium leaderboard + bot of the week, biggest ELO loss… |

Every post is a Discord **Components V2** message (accent bar, avatar thumbnail, media gallery,
buttons): **Open on FaceIT** · **🎤 Excuse me** (posts a random excuse on the player's behalf) ·
**📊 Wall** (ephemeral leaderboard). Shame posts get a 🤡 reaction (`SHAME_REACTIONS`).

Awards on shame cards: Bottom of the lobby · Double feature / Triple threat · Hat-trick / Streak N ·
Carried · Anchor · Spectator · Fed · Zero MVP club · Headless.

## Slash commands

| Command | What it does |
|---|---|
| `/wall [scope]` | Shame leaderboard image (podium + rows). `recent` = last `POST_HISTORY_LIMIT` games, `all` = every cached match |
| `/glory` | Glory board: best game, clean rate, averages |
| `/profile [player]` | Player card: level, ELO + 7d/30d change + trend, averages, last-10 form, per-map summary |
| `/last [player]` | The player's most recent match as a card |
| `/compare a b [scope]` | Head-to-head card |
| `/maps [player]` | Per-map games / win % / avg kills / wall % |
| `/elo` | Tracked players ranked by ELO with 7d/30d deltas |
| `/awards` | Hall of shame: worst game ever, longest streak, biggest ELO loss… |
| `/shametest match_id [kind]` | *(admins)* render the full post for any match, ignoring the threshold |

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
| `REDEMPTION_KILLS` / `GLORY_KILLS` | `25` / `30` | Redemption / highlight thresholds |
| `GLORY_POSTS_ENABLED` | `true` | Set `false` for a shame-only channel |
| `POLL_INTERVAL_SECONDS` | `300` | `120` recommended for clean per-match ELO deltas |
| `POST_HISTORY_LIMIT` | `30` | "Recent" window for posts and stats |
| `SHAME_REACTIONS` | `🤡` | Reactions added to shame posts |
| `CUSTOM_ROASTS_FILE` | `roasts_custom.txt` | Your own roast lines (see `roasts_custom.txt.example`) |
| `ADMIN_DISCORD_IDS` | – | Who may run `/shametest` |
| `STATE_FILE` | `state.json` | Persisted cache (use a volume path in production) |

## How it works
- `shamebot/faceit.py` — async FaceIT Data API v4 client (aiohttp).
- `shamebot/poller.py` — every poll: refresh ELO snapshots, fetch each player's recent history,
  process unseen matches oldest-first, attribute ELO deltas, detect posts, mark processed.
- `shamebot/state.py` — `state.json` (schema v2): full 10-player scoreboard + ADR/K-D/HS/MVP/W-L per
  match, ELO history per player. Older kills-only entries are upgraded in the background.
- `shamebot/rules.py` / `roasts.py` — detection, awards, deterministic roast selection.
- `shamebot/render/` — Pillow cards (bundled Inter + Bebas Neue fonts, SIL OFL).
- `shamebot/views.py` / `commands.py` — Components V2 messages, persistent buttons, slash commands.

## Development
```bash
pip install -r requirements-dev.txt
pytest                       # offline tests against captured FaceIT payloads in tests/fixtures
python tools/preview.py      # renders sample cards to out/preview/*.png
python tools/capture_fixture.py [match_id] [suffix]   # refresh fixtures from the live API
```

**FaceIT history cap:** the Data API returns at most ~1100 matches per player; all-time stats
reflect fetchable history only.
