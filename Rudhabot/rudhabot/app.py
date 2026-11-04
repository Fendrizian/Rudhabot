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
        services = getattr(interaction.client, "services", None)
        home = services.settings.home_guild_id if services is not None else 0
        if home and interaction.guild_id and interaction.guild_id != home:
            await self._tell(interaction, "Rudhabot serves one server only, so its commands don't work here.",
                             Colors.UNAVAILABLE)
            return False
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
            await self._tell(interaction, MSG_ONGAME, Colors.FAIL)
        elif isinstance(cause, NotAuthorized):
            await self._tell(interaction, MSG_NOPERMISSION, Colors.UNAVAILABLE)
        elif isinstance(error, app_commands.CheckFailure):
            log.info("check failed for /%s: %s", getattr(interaction.command, "name", "?"), error)
        else:
            log.error("unhandled error in /%s", getattr(interaction.command, "name", "?"), exc_info=error)
            await self._tell(interaction, "❌ Something went wrong. Please try again.", Colors.FAIL)

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
        intents.message_content = True      # assistant mentions, spam and link detection
        intents.members = True              # join defence, probation, clearance (privileged: enable in the portal)
        intents.moderation = True           # audit-log entries (anti-nuke) and bans
        super().__init__(command_prefix=">", intents=intents, tree_cls=RudhaTree)
        self.services = services or Services.build(settings or Settings(), self, clock=clock, rng=rng)
        self.guild_id: Optional[int] = None
        self._commands_synced = False

    @property
    def primary_guild(self) -> Optional[discord.Guild]:
        """The home server: where the background jobs act and the cross-system views look."""
        return self.get_guild(self.guild_id) if self.guild_id else None

    def _find_home(self) -> Optional[int]:
        """RUDHABOT_GUILD_ID; else the server that holds the configured audit/notification channels; else the only
        (or first) server the bot is in."""
        home = self.services.settings.home_guild_id
        if home:
            return home
        profile = self.services.profile
        for key in ("audit", "notifications", "birthdays", "lottery"):
            channel = self.get_channel(profile.channel(key))
            if channel is not None and getattr(channel, "guild", None) is not None:
                return channel.guild.id
        return self.guilds[0].id if self.guilds else None

    async def setup_hook(self) -> None:
        from rudhabot.cogs.registry import ALL_COGS      # imported late: cogs import this package's services
        for cog in ALL_COGS:
            await self.add_cog(cog(self))

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            self.services.storage.close()

    async def on_ready(self) -> None:
        self.guild_id = self._find_home()
        if self.guild_id:
            log.info("home server %s", self.guild_id)
        log.info("logged in as %s (ID: %s)", self.user, self.user.id)
        home = self.services.settings.home_guild_id
        others = [g for g in self.guilds if home and g.id != home]
        if others:
            log.warning("Rudhabot is in %d other server(s) %s; its commands are refused there",
                        len(others), [g.id for g in others])
        if self._commands_synced:                         # on_ready also fires after every reconnect
            return
        try:
            if home:
                # one server: register the commands there (they appear at once) and none globally (nothing twice)
                guild = discord.Object(id=home)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                await self.http.bulk_upsert_global_commands(self.application_id, payload=[])
            else:
                await self.tree.sync()
                if self.guild_id:
                    await self.tree.sync(guild=discord.Object(id=self.guild_id))   # clears stale guild-scoped copies
            self._commands_synced = True
            log.info("slash commands synced%s", f" to server {home}" if home else "")
        except Exception:
            log.exception("syncing slash commands failed")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        home = self.services.settings.home_guild_id
        if home and guild.id != home:
            log.warning("added to server %s (%s), which isn't the home server: commands are refused there",
                        guild.id, guild.name)
