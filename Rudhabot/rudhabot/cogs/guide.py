"""/guide: the Rudhabot Guidebook in Discord. Private by default (it's personal reading); `public:True` posts a member
chapter in the channel. Staff chapters are always private, and each reader sees only the chapters for their rank."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.services.permissions import MEMBER, viewer_for
from rudhabot.views.guide import ChapterSelect, GuideButton, chapter, render, visible_chapters


class GuideCog(ServiceCog):
    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(ChapterSelect, GuideButton)

    async def _chapters(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        viewer = viewer_for(self.services, interaction.user, interaction.guild_id)
        text = current.casefold()
        return [app_commands.Choice(name=c.title, value=c.key) for c in visible_chapters(viewer)
                if text in c.title.casefold() or text in c.key][:25]

    @app_commands.command(name="guide", description="The Rudhabot Guidebook: how everything works, chapter by chapter")
    @app_commands.describe(chapter_key="Jump straight to a chapter",
                           public="Post it in the channel for everyone (member chapters only)")
    @app_commands.rename(chapter_key="chapter")
    @app_commands.autocomplete(chapter_key=_chapters)
    async def guide(self, interaction: discord.Interaction, chapter_key: Optional[str] = None, public: bool = False):
        viewer = viewer_for(self.services, interaction.user, interaction.guild_id)
        target = getattr(chapter_key, "value", chapter_key) or "home"
        found = chapter(target)
        staff_only = found is not None and found.requires is not None
        if public and not staff_only:
            # a public post shows what every member can read, whoever posts it; clicks give each person their own copy
            embed, view = render(target, viewer=MEMBER)
            return await interaction.response.send_message(embed=embed, view=view)
        embed, view = render(target, viewer=viewer)
        if public and staff_only and viewer.can(found.requires):
            embed.set_footer(text=(embed.footer.text or "") + " · staff chapters are only shown privately")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
