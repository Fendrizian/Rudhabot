"""Housekeeping, hourly: a daily backup of the SQLite database (kept for 14 days in `<data dir>/backups`), the daily
economy snapshot, and the weekly State of the Realm report (Sundays from 20:00 Rudhanese Time, to the reports
channel; see services/realm.py)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta, timezone

import discord
from discord.ext import tasks

from rudhabot.cogs.base import ServiceCog

log = logging.getLogger("rudhabot.maintenance")
KEEP_BACKUPS = 14
REPORT_WEEKDAY, REPORT_HOUR = 6, 20             # Sunday, 20:00 in the server's time zone (GMT+8 by default)


class MaintenanceCog(ServiceCog):
    def __init__(self, bot):
        super().__init__(bot)
        self.storage = self.services.storage
        self._last_backup_day = None

    async def cog_load(self) -> None:
        for line in self.storage.imports:
            (log.error if "NOT imported" in line else log.info)("SQLite import: %s", line)
        self.backups.start()

    async def cog_unload(self) -> None:
        self.backups.cancel()

    @tasks.loop(hours=1)
    async def backups(self) -> None:
        await self.backup_if_due()
        await self.snapshot_if_due()
        await self.report_if_due()

    async def snapshot_if_due(self) -> None:
        realm = self.services.realm
        try:
            day = realm.snapshot_due()
            if day is not None:
                snap = realm.take_snapshot(day)
                log.info("economy snapshot for %s: supply %s, %s movements", day, snap["supply"], snap["transactions"])
        except Exception:
            log.exception("economy snapshot failed")

    async def report_if_due(self, force: bool = False) -> bool:
        """Post the weekly report once per ISO week, on the report day from the report hour (or now, if forced)."""
        realm = self.services.realm
        offset = timezone(timedelta(hours=self.services.settings.birthdays.utc_offset_hours))
        local = self.clock.now(timezone.utc).astimezone(offset)
        week = realm.week_label()
        if not force and (realm.last_report_week() == week or local.weekday() != REPORT_WEEKDAY
                          or local.hour < REPORT_HOUR):
            return False
        try:
            from rudhabot.ui.realm_embeds import report_embed
            embed = report_embed(realm.report(self.bot.guild_id))
            posted = await self.services.messenger.post(self.services.profile.channel("reports"), embed=embed,
                                                        allowed_mentions=discord.AllowedMentions.none())
            if posted:
                realm.mark_reported(week)
            return bool(posted)
        except Exception:
            log.exception("weekly report failed")
            return False

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
