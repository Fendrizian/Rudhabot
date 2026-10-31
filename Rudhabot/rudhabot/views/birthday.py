"""The birthday cake form. Scheduling itself lives in BirthdayService, which persists it so a restart can't lose it."""
from __future__ import annotations

import discord
from discord import Interaction, ui

from rudhabot.domain.birthday import BirthdayError
from rudhabot.ui.replies import error_embed
from rudhabot.views.base import ServiceModal


class BirthdayCakeModal(ServiceModal):
    def __init__(self, services, user):
        super().__init__(services, title="Birthday Cake Setup")
        self.user = user
        self.date = ui.TextInput(label="Date (DD/MM/YYYY)", placeholder="e.g. 21/09/2025", required=True)
        self.to = ui.TextInput(label="To (mention user, optional)", placeholder="e.g. @username", required=False)
        self.time = ui.TextInput(label="Time (HH:MM, GMT+8, optional)", placeholder="e.g. 14:30", required=False)
        self.message = ui.TextInput(label="Message (optional)", placeholder="Your birthday message...", required=False)
        for item in (self.date, self.to, self.time, self.message):
            self.add_item(item)

    async def _recipient(self):
        mention = self.to.value.strip()
        if mention.startswith("<@") and mention.endswith(">"):
            try:
                user = await self.services.messenger.find_user(int(mention.replace("<@", "").replace(">", "").replace("!", "")))
                if user:
                    return user
            except ValueError:
                pass
        return self.user

    async def on_submit(self, interaction: Interaction):
        recipient = await self._recipient()
        try:
            scheduled = self.services.birthdays.schedule_cake(
                owner_id=self.user.id, recipient_id=recipient.id, date_text=self.date.value,
                time_text=self.time.value, message_text=self.message.value)
        except BirthdayError as error:
            await interaction.response.send_message(embed=error_embed(str(error), interaction.user), ephemeral=True)
            return
        cake = self.services.catalog.emoji("special birthday cake", "🎂")
        message = self.message.value.strip() or "Congratulations!"
        embed = discord.Embed(
            description=f"{cake} Birthday celebration scheduled for {recipient.mention} on {self.date.value} "
                        f"at {scheduled.local_time} Rudhanese Time (GMT+8)!\nMessage: {message}",
            color=0x57F287,
        )
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)
