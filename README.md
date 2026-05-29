# FaceIT Wall of Shame Bot

Discord bot that polls your teammates' latest CS2 FaceIT matches and posts a **BRWNr Bot Stats:** scoreboard when a tracked player finishes with fewer than 10 kills (configurable).

## Setup

### 1. FaceIT API key

1. Go to [FaceIT Developer Portal](https://developers.faceit.com/).
2. Create an app and copy the **API Key** (Server-side / Data API).

### 2. FaceIT player IDs

`FACEIT_PLAYER_IDS` accepts any of these per teammate (comma-separated):

| Format | Example |
|--------|---------|
| FaceIT UUID | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
| Steam ID (17 digits) | `76561198899254198` — bot resolves via FaceIT |
| FaceIT nickname | `TheirExactNick` |

**Do not** use Discord IDs or the Discord application Public Key.

For UUID lookup manually:

1. Open their FaceIT profile in the browser.
2. Copy the player UUID from the profile URL or page source.
3. Add to `FACEIT_PLAYER_IDS` in `.env`.

Or look up via API:

```bash
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://open.faceit.com/data/v4/players?nickname=TheirNick"
```

### 3. Discord bot

1. Create an application at [Discord Developer Portal](https://discord.com/developers/applications).
2. Add a **Bot** user and copy the token → `DISCORD_TOKEN`.
3. Enable **Message Content Intent** if needed; invite the bot with `Send Messages` and `Attach Files`.
4. Enable **Developer Mode** in Discord, right-click your shame channel → **Copy Channel ID** → `SHAME_CHANNEL_ID`.
5. **Invite the bot** with `bot` and `applications.commands` scopes.

**Channel not found?** The bot must be in the same server as the channel. Copy the channel ID again with Developer Mode; do not use the bot token or Public Key as the channel ID.

### 4. Install and run

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env   # then edit .env with real values
python bot.py
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Discord bot token |
| `FACEIT_API_KEY` | FaceIT Data API bearer token |
| `SHAME_CHANNEL_ID` | Discord channel ID for shame posts |
| `FACEIT_PLAYER_IDS` | Comma-separated FaceIT player UUIDs, Steam IDs, or nicknames |
| `KILL_THRESHOLD` | Post when tracked player has fewer kills (default `10`) |
| `POLL_INTERVAL_SECONDS` | How often to check for new matches (default `300`) |
| `POST_HISTORY_LIMIT` | Recent games for **posts** and `/shamestats_recent` (default `30`). `HISTORY_LIMIT` is an alias. |
| `POST_HISTORY_ON_STARTUP` | Post shame for qualifying games in post window on startup (default `true`) |
| `STATS_BACKFILL_ON_STARTUP` | Cache all fetchable CS2 history for stats commands (default `true`) |
| `STATS_BACKFILL_SLEEP_SECONDS` | Delay between FaceIT match stats requests during backfill (default `1`) |
| `STATE_FILE` | Path to persisted state (default `state.json`) |
| `BOT_STATS_TITLE` | Title on message and scoreboard image (default `BRWNr Bot Stats:`) |

### Optional

| Variable | Description |
|----------|-------------|
| `FACEIT_DISCORD_MENTIONS` | `ExactFaceITNick:discord_user_id,...` — @mention mapped players on shame posts |
| `MENTION_SHAMED_ON_POST` | Enable mentions when map is set (default `true`) |
| `STATS_COMMAND_ANY_CHANNEL` | Allow stats slash commands outside shame channel (default `false`) |
| `WEEKLY_DIGEST_ENABLED` | Weekly all-time leaderboard digest (default `false`) |
| `WEEKLY_DIGEST_CRON_DAY` | UTC weekday: 0=Monday … 6=Sunday (default `0`) |
| `WEEKLY_DIGEST_HOUR_UTC` | UTC hour (default `18`) |

## Slash commands

| Command | Scope |
|---------|--------|
| `/shamestats_recent` | Last `POST_HISTORY_LIMIT` games (default 30) |
| `/shamestats_all` | All cached CS2 matches (after stats backfill) |
| `/glorystats` | All cached matches — best/avg kills, clean rate |

All return **aggregate** leaderboards (no per-match list).

**FaceIT history cap:** the Data API returns at most ~1100 matches per player via pagination. Stats commands reflect fetchable history only.

## Behavior

### Shame posts (recent only)

- Polls and startup posts use the **post window** (`POST_HISTORY_LIMIT`) so the channel is not flooded with old games.
- On startup, a **full history backfill** caches match stats silently; matches outside the post window are marked processed **without** posting.
- Shame posts show rolling **last N** stats on the message and scoreboard image.
- Optional **Discord @mentions** for mapped FaceIT nicknames (`FACEIT_DISCORD_MENTIONS`).

### Stats cache

- `match_outcomes` in `state.json` stores kills and shame flags per match (not pruned to 30 games).
- First startup after upgrade may take several minutes per player (one API call per match, 1s apart).
- `/shamestats_all` and `/glorystats` are unavailable until backfill finishes (ephemeral message if still running).

## Manual test checklist

1. Fill `.env` with real token, channel ID, API key, and at least one `FACEIT_PLAYER_IDS` entry.
2. Start the bot; logs show stats backfill progress, then retro scan for post window only.
3. `/shamestats_recent` — totals for last 30 games; `/shamestats_all` — full cached history.
4. New sub-threshold game: one shame post within one poll interval; mapped nick gets @mention.
5. Restart: no duplicate shame posts; stats not re-fetched for cached matches.
6. `/glorystats` — positive leaderboard from same cache.
