"""FaceIT Wall of Shame bot — entrypoint. All logic lives in the ``shamebot`` package."""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from shamebot.app import App
from shamebot.commands import register
from shamebot.config import settings
from shamebot.poller import Poller
from shamebot.render import theme
from shamebot.views import DYNAMIC_ITEMS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("discord").setLevel(logging.WARNING)
log = logging.getLogger("shamebot")


class ShameBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.app = App(settings)
        self.tree = app_commands.CommandTree(self)
        self.poller = Poller(self, self.app)

    async def setup_hook(self) -> None:
        self.app.state.load()
        self.add_dynamic_items(*DYNAMIC_ITEMS)
        register(self.tree, self.app)
        if not theme.fonts_available():
            log.warning("Bundled fonts missing in assets/fonts — cards will use Pillow's default font.")

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        await self.app.resolve_players()
        try:
            synced = await self.tree.sync()
            log.info("Synced %d slash command(s).", len(synced))
        except discord.HTTPException as exc:
            log.warning(
                "Slash command sync failed (posts still work): %s. Re-invite the bot with the "
                "applications.commands scope if needed.",
                exc,
            )
        self.loop.create_task(self.poller.start())

    async def close(self) -> None:
        await self.app.close()
        await super().close()


if __name__ == "__main__":
    settings.validate_or_exit()
    ShameBot().run(settings.discord_token, log_handler=None)
