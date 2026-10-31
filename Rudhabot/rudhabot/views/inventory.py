"""Paginated inventory."""
from __future__ import annotations

import math

import discord
from discord import ButtonStyle, Interaction

from rudhabot.ui.theme import INVICON
from rudhabot.views.base import ServiceButton, ServiceView

PAGE_SIZE = 7


def inventory_pages(inventory_items) -> int:
    return max(1, math.ceil(len(inventory_items) / PAGE_SIZE))


def create_inventory_embed(catalog, inventory_items, user, page, total_pages):
    embed = discord.Embed(title=f"{INVICON} {user.name}'s Inventory", color=0xA84300)
    if not inventory_items:
        embed.description = "Your inventory is empty."
    else:
        for item, qty in inventory_items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            embed.add_field(name=f"{catalog.emoji(item)} {item.title()} ({qty})",
                            value=f"{catalog.description(item)}", inline=False)
        embed.set_footer(text=f"Page {page + 1}/{total_pages}")
    return embed


class InventoryView(ServiceView):
    def __init__(self, services, inventory_items, user, page=0):
        super().__init__(services, timeout=300)
        self.inventory_items = inventory_items
        self.user = user
        self.page = page
        self.total_pages = inventory_pages(inventory_items)
        self.add_item(InventoryPageButton("⬅ Previous", -1))
        self.add_item(InventoryPageButton("Next ⮕", 1))

    async def interaction_check(self, interaction: Interaction):
        return interaction.user.id == self.user.id


class InventoryPageButton(ServiceButton):
    def __init__(self, label, direction):
        super().__init__(label=label, style=ButtonStyle.primary)
        self.direction = direction

    async def callback(self, interaction: Interaction):
        view: InventoryView = self.view
        new_page = (view.page + self.direction) % view.total_pages
        embed = create_inventory_embed(self.services.catalog, view.inventory_items, view.user, new_page, view.total_pages)
        view.page = new_page
        await interaction.response.edit_message(embed=embed, view=view)
