"""Housekeeping: a daily backup of the SQLite database (kept for 14 days in `<data dir>/backups`)."""
from __future__ import annotations

import asyncio
import logging

from discord.ext import tasks

from rudhabot.cogs.base import ServiceCog

log = logging.getLogger("rudhabot.maintenance")
KEEP_BACKUPS = 14


class MaintenanceCog(ServiceCog):
    def __init__(self, bot):
        super().__init__(bot)
        self.storage = self.services.storage
        self._last_backup_day = None

    async def cog_load(self) -> None:
        if self.storage.imports:
            log.info("imported into SQLite: %s", "; ".join(self.storage.imports))
        self.backups.start()

    async def cog_unload(self) -> None:
        self.backups.cancel()

    @tasks.loop(hours=1)
    async def backups(self) -> None:
        await self.backup_if_due()

    @backups.before_loop
    async def _ready(self) -> None:
        await self.bot.wait_until_ready()

    async def backup_if_due(self) -> None:
        day = self.clock.now().strftime("%Y%m%d")
        if self.storage.db is None or day == self._last_backup_day:
            return
        try:
            # SQLite's online backup runs on its own connection, so it can run off the event loop
            path = await asyncio.to_thread(self.storage.backup, self.services.settings.paths.backups, KEEP_BACKUPS, day)
            self._last_backup_day = day
            log.info("database backed up to %s", path)
        except Exception:
            log.exception("database backup failed")
