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
5. **Invite the bot** to that server (OAuth2 URL Generator → `bot` scope). If the bot is not in the server, channel lookup fails.

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
| `FACEIT_PLAYER_IDS` | Comma-separated FaceIT player UUIDs |
| `KILL_THRESHOLD` | Post when tracked player has fewer kills (default `10`) |
| `POLL_INTERVAL_SECONDS` | How often to check for new matches (default `300`) |
| `HISTORY_LIMIT` | How many recent CS2 games to check per player (default `30`) |
| `POST_HISTORY_ON_STARTUP` | Post shame for qualifying games in recent history on startup (default `true`) |
| `STATE_FILE` | Path to persisted processed match IDs (default `state.json`) |
| `BOT_STATS_TITLE` | Title on message and scoreboard image (default `BRWNr Bot Stats:`) |

### Optional (rolling stats)

| Variable | Description |
|----------|-------------|
| `STATS_COMMAND_ANY_CHANNEL` | Allow `/shamestats` outside the shame channel (default `false`) |
| `WEEKLY_DIGEST_ENABLED` | Post a weekly leaderboard digest (default `false`) |
| `WEEKLY_DIGEST_CRON_DAY` | UTC weekday for digest: 0=Monday … 6=Sunday (default `0`) |
| `WEEKLY_DIGEST_HOUR_UTC` | UTC hour to post digest (default `18`) |

No new API keys are required. The same `FACEIT_API_KEY` and `DISCORD_TOKEN` are used.

## Rolling shame statistics

For each tracked player, the bot caches match outcomes in `state.json` and computes stats over the last `HISTORY_LIMIT` games (default 30):

- **Shame count** — games under `KILL_THRESHOLD` kills
- **Shame rate** — percentage of cached window games
- **Current streak** — consecutive recent shame games
- **Worst kills** — lowest kill count in the window
- **Title** — fun label (e.g. Saint, Slump, Permanent resident)

These appear on shame posts (Discord text + scoreboard image footer). Use **`/shamestats`** in the shame channel for a leaderboard anytime (slash command; no Message Content Intent).

On first run after an upgrade, the bot may backfill match stats for the history window (one FaceIT request per uncached match, 1s apart). Existing `processed_matches` in `state.json` are preserved.

## Manual test checklist

1. Fill `.env` with real token, channel ID, API key, and at least one real `FACEIT_PLAYER_IDS` entry.
2. Start the bot; logs should show resolved nicknames and a retroactive scan. With `POST_HISTORY_ON_STARTUP=true`, qualifying games in the last `HISTORY_LIMIT` matches may post once.
3. Delete `state.json` only if you want to re-run the retroactive scan (may duplicate posts).
4. After a tracked teammate finishes a new match with &lt;10 kills, wait up to one poll interval; verify a post with title, kill line, and PNG scoreboard.
5. Restart the bot; previously posted matches must **not** be posted again.
6. If several tracked players share one match, only **one** shame post should appear.
7. Run `/shamestats` in the shame channel; verify leaderboard lines per tracked player.
8. After backfill completes, shame posts should show `Wall record (last 30): X/Y games` for shamed players.

## Behavior

- On startup with `POST_HISTORY_ON_STARTUP=true` (default), the bot scans the last `HISTORY_LIMIT` games and posts shame for any tracked player with fewer than `KILL_THRESHOLD` kills that was not processed before.
- Processed match IDs are stored in `state.json` so restarts do not repost the same games.
- Each poll checks recent history for new matches not yet in `state.json`.
- Only **tracked** teammates trigger a post; the scoreboard image still shows both teams.
- Shamed players are highlighted in red on the scoreboard image.
- Rolling stats (shame count, rate, streak, title) are shown on shame posts when outcome data is cached.
- `/shamestats` slash command posts a sorted leaderboard (requires bot invite with `applications.commands` scope).
- With `WEEKLY_DIGEST_ENABLED=true`, a summary posts once per week at the configured UTC day/hour.
