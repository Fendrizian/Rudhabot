"""Base class for command-level tests: a real RudhaBot (real cogs, services, repositories) over a temp directory,
driven through FakeInteractions with a fixed clock and scripted randomness."""
import tempfile
import unittest
from datetime import datetime
from unittest.mock import AsyncMock

import discord

from rudhabot.app import RudhaBot
from rudhabot.clock import FixedClock
from rudhabot.settings import Paths, Settings
from tests.fakes import FakeInteraction, FakeUser, ScriptedRng, fast_sleep_patch

NOW = datetime(2026, 3, 15, 12, 0, 0)       # 20:00 on 15 March in GMT+8


class CogTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.script = ScriptedRng()       # queue exact rolls/reels in a test: self.script.rolls / self.script.reels
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FixedClock(NOW)
        self.bot = RudhaBot(Settings(paths=Paths.in_directory(self._tmp.name)), clock=self.clock,
                            rng=self.script)
        await self.bot._async_setup_hook()      # what login() does before setup_hook (initialises the ready-event)
        await self.bot.setup_hook()
        self.s = self.bot.services
        # nothing may touch the network: capture every DM / channel post instead
        self.s.messenger.post = AsyncMock(return_value=True)
        self.s.messenger.dm = AsyncMock(return_value=True)
        self.s.messenger.find_user = AsyncMock(return_value=None)
        self._sleep = fast_sleep_patch()
        self.alice, self.bob, self.admin = FakeUser(1001, "alice"), FakeUser(1002, "bob"), FakeUser(9999, "admin", admin=True)

    async def asyncTearDown(self):
        import asyncio as aio
        aio.sleep = self._sleep
        for cog in list(self.bot.cogs.values()):
            await cog.cog_unload()
        for task in aio.all_tasks():
            if task is not aio.current_task():
                task.cancel()
        await self._sleep(0)

    def cog(self, name):
        return self.bot.cogs[name]

    async def run_command(self, name, actor, guild=None, group=None, **kwargs):
        """Invoke a slash command the way discord.py does: checks first, then the callback, errors to the tree."""
        command = self.bot.tree.get_command(name)
        if group:
            command = next(c for c in command.commands if c.name == group)
        it = FakeInteraction(self.bot, actor, guild)
        it.command = command
        try:
            await command._check_can_run(it)
            await command.callback(command.binding, it, **kwargs)
        except discord.app_commands.AppCommandError as error:
            await self.bot.tree.on_error(it, error)
        except Exception as error:
            await self.bot.tree.on_error(it, discord.app_commands.CommandInvokeError(command, error))
        return it

    def fund(self, user, wallet=0, **fields):
        self.s.users.update(user.id, wallet=wallet, **fields)

    def account(self, user):
        return self.s.users.get(user.id)
