"""The composition root: the bot subclass that owns the services, loads the cogs and handles command errors."""
from __future__ import annotations

import logging
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from rudhabot.container import Services
from rudhabot.errors import AlreadyInGame, NotAuthorized
from rudhabot.services.audit import set_context
from rudhabot.settings import Settings
from rudhabot.ui.messages import MSG_NOPERMISSION, MSG_ONGAME
from rudhabot.ui.theme import Colors

log = logging.getLogger("rudhabot")


class RudhaTree(app_commands.CommandTree):
    """Turns check failures into the standard messages and keeps unexpected errors from going unanswered."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # runs in the same task as the command, so every money change it makes is labelled "/command"
        command = interaction.command
        set_context(f"/{command.qualified_name}" if command else "slash command", interaction.user.id, "command")
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        cause = getattr(error, "original", error)
        reviews = getattr(getattr(interaction.client, "services", None), "reviews", None)
        if reviews is not None:                     # repeated failures are one of Clairvoyance's review signals
            name = getattr(interaction.command, "qualified_name", "?")
            reviews.on_failure(interaction.user.id, f"/{name}: {type(cause).__name__}")
        if isinstance(cause, AlreadyInGame):
            await self._tell(interaction, MSG_ONGAME, Colors.ERROR)
        elif isinstance(cause, NotAuthorized):
            await self._tell(interaction, MSG_NOPERMISSION, Colors.NOTICE)
        elif isinstance(error, app_commands.CheckFailure):
            log.info("check failed for /%s: %s", getattr(interaction.command, "name", "?"), error)
        else:
            log.error("unhandled error in /%s", getattr(interaction.command, "name", "?"), exc_info=error)
            await self._tell(interaction, "❌ Something went wrong. Please try again.", Colors.ERROR)

    @staticmethod
    async def _tell(interaction: discord.Interaction, message: str, color: int) -> None:
        embed = discord.Embed(description=message, color=color)
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.warning("could not report an error to %s", interaction.user)


class RudhaBot(commands.Bot):
    def __init__(self, settings: Optional[Settings] = None, *, clock=None, rng=random, services: Optional[Services] = None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=">", intents=intents, tree_cls=RudhaTree)
        self.services = services or Services.build(settings or Settings(), self, clock=clock, rng=rng)
        self.guild_id: Optional[int] = None
        self._commands_synced = False

    @property
    def primary_guild(self) -> Optional[discord.Guild]:
        """The server the background jobs act in (the first one the bot joined)."""
        return self.get_guild(self.guild_id) if self.guild_id else None

    async def setup_hook(self) -> None:
        from rudhabot.cogs.registry import ALL_COGS      # imported late: cogs import this package's services
        for cog in ALL_COGS:
            await self.add_cog(cog(self))

    async def on_ready(self) -> None:
        if self.guilds:
            self.guild_id = self.guilds[0].id
            log.info("using guild %s", self.guild_id)
        log.info("logged in as %s (ID: %s)", self.user, self.user.id)
        if self._commands_synced:                         # on_ready also fires after every reconnect
            return
        try:
            await self.tree.sync()
            if self.guild_id:
                await self.tree.sync(guild=discord.Object(id=self.guild_id))   # clears stale guild-scoped copies
            self._commands_synced = True
            log.info("slash commands synced")
        except Exception:
            log.exception("syncing slash commands failed")
