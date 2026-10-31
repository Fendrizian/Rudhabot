"""Birthday commands (/birthday set|view|remove|upcoming) and the jobs behind them:
yearly greetings with a gift, and delivery of scheduled cake celebrations."""
from __future__ import annotations

import calendar
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rudhabot.cogs.base import ServiceCog
from rudhabot.domain.birthday import BirthdayError
from rudhabot.ui import birthday_embeds
from rudhabot.ui.replies import author_embed, error_embed, respond
from rudhabot.ui.theme import Colors

log = logging.getLogger("rudhabot.birthdays")

MONTHS = [app_commands.Choice(name=calendar.month_name[i], value=i) for i in range(1, 13)]


class BirthdayCog(ServiceCog, commands.GroupCog, group_name="birthday", group_description="Birthdays and celebrations"):
    async def cog_load(self):
        self.greet.start()
        self.deliver_cakes.start()

    async def cog_unload(self):
        self.greet.cancel()
        self.deliver_cakes.cancel()

    # ----------------------------------------------------------------- commands
    @app_commands.command(name="set", description="Register your birthday")
    @app_commands.describe(day="Day of the month", month="Month", year="Birth year (optional, shows your age)")
    @app_commands.choices(month=MONTHS)
    async def set_birthday(self, interaction: discord.Interaction, day: app_commands.Range[int, 1, 31],
                           month: app_commands.Choice[int], year: Optional[int] = None):
        try:
            birthday = self.services.birthdays.set_birthday(interaction.user.id, month.value, day, year)
        except BirthdayError as error:
            await respond(interaction, error_embed(str(error), interaction.user), ephemeral=True)
            return
        await respond(interaction, birthday_embeds.birthday_saved(interaction.user, birthday))

    @app_commands.command(name="view", description="See your birthday, or someone else's")
    @app_commands.describe(user="Whose birthday to show (default: yours)")
    async def view_birthday(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        target = user or interaction.user
        birthday = self.services.birthdays.get_birthday(target.id)
        if birthday is None:
            message = ("❌ You haven't set your birthday yet. Use `/birthday set`." if target.id == interaction.user.id
                       else f"❌ {target.name} hasn't set a birthday.")
            await respond(interaction, error_embed(message, interaction.user), ephemeral=True)
            return
        await respond(interaction, birthday_embeds.birthday_card(target, birthday, self.services.birthdays.today(),
                                                                 own=target.id == interaction.user.id))

    @app_commands.command(name="remove", description="Remove your registered birthday")
    async def remove_birthday(self, interaction: discord.Interaction):
        if self.services.birthdays.remove_birthday(interaction.user.id):
            await respond(interaction, author_embed(interaction.user, "✅ Your birthday has been removed.", Colors.SUCCESS))
        else:
            await respond(interaction, error_embed("❌ You haven't set a birthday.", interaction.user), ephemeral=True)

    @app_commands.command(name="upcoming", description="List the next birthdays")
    async def upcoming(self, interaction: discord.Interaction):
        await respond(interaction, birthday_embeds.upcoming_list(self.services.birthdays.upcoming(10)))

    # --------------------------------------------------------------- background
    @tasks.loop(minutes=10)
    async def greet(self):
        """Gift and announce today's birthdays. Safe to run often: each person is greeted once per year."""
        try:
            for greeting in self.services.birthdays.collect_due_greetings():
                await self.services.announcer.birthday(greeting)
        except Exception:
            log.exception("birthday greeting pass failed")

    @tasks.loop(minutes=1)
    async def deliver_cakes(self):
        """Deliver cake celebrations whose time has come (they are stored, so they survive restarts)."""
        try:
            birthdays = self.services.birthdays
            for entry in birthdays.due_cake_celebrations():
                sender = await self.services.messenger.find_user(int(entry["scheduled_by"]))
                delivered = await self.services.announcer.cake(entry, sender.name if sender else "a friend")
                status = birthdays.record_cake_result(entry, delivered)
                if status == "failed":
                    log.warning("cake celebration %s could not be delivered", entry["id"])
        except Exception:
            log.exception("cake delivery pass failed")

    @greet.before_loop
    @deliver_cakes.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()
