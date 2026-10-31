"""Common base for cogs: they receive the bot and read their dependencies from `bot.services`."""
from __future__ import annotations

from discord.ext import commands


class ServiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.services = bot.services
        self.users = bot.services.users
        self.clock = bot.services.clock
