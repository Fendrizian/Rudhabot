"""Paginated leaderboard."""
from __future__ import annotations

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.ui.theme import CURRENCY

PAGE_SIZE = 10


def create_leaderboard_embed(entries, page, total_pages, leaderboard_type):
    embed = discord.Embed(title=f"🏆 Server Leaderboard - {leaderboard_type.title()}", color=0xE91E63)
    start = page * PAGE_SIZE
    for idx, (user_id, value) in enumerate(entries[start:start + PAGE_SIZE], start=start + 1):
        embed.add_field(name=f"#{idx}", value=f"<@{user_id}>: {CURRENCY}{value}", inline=False)
    embed.set_footer(text=f"Page {page + 1} / {total_pages}")
    return embed


class LeaderboardView(ui.View):
    def __init__(self, entries, page, total_pages, leaderboard_type):
        super().__init__(timeout=300)
        self.entries = entries
        self.page = page
        self.total_pages = total_pages
        self.leaderboard_type = leaderboard_type
        self.add_item(LeaderboardNav("⬅ Previous", -1))
        self.add_item(LeaderboardNav("Next ⮕", 1))

    async def interaction_check(self, interaction: Interaction):
        return True


class LeaderboardNav(ui.Button):
    def __init__(self, label, direction):
        super().__init__(label=label, style=ButtonStyle.primary)
        self.direction = direction

    async def callback(self, interaction: Interaction):
        view: LeaderboardView = self.view
        new_page = (view.page + self.direction) % view.total_pages
        embed = create_leaderboard_embed(view.entries, new_page, view.total_pages, view.leaderboard_type)
        await interaction.response.edit_message(
            embed=embed, view=LeaderboardView(view.entries, new_page, view.total_pages, view.leaderboard_type))
