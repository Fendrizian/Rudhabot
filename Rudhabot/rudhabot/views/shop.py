"""The paginated National Online Shop."""
from __future__ import annotations

import math

import discord
from discord import ButtonStyle, Interaction

from rudhabot.services.shop import ShopRefused
from rudhabot.ui.theme import CURRENCY, Colors, SHOPICON
from rudhabot.views.base import ServiceButton, ServiceView


def shop_page(services, page: int, wallet: int):
    """Build one page of the shop: returns (embed, items on this page, total pages)."""
    catalog, size = services.catalog, services.settings.page_size
    items = list(catalog.shop_items.items())
    total_pages = math.ceil(len(items) / size)
    current = items[page * size:(page + 1) * size]
    embed = discord.Embed(
        title=f"{SHOPICON} NATIONAL ONLINE SHOP",
        description=f"Welcome to the National Online Shop! Commissioned by the monarchy. \n"
                    f"You have {CURRENCY}{wallet} in your wallet.",
        color=Colors.SHOP,
    )
    for item, price in current:
        embed.add_field(name=f"{catalog.emoji(item)} {item.title()}",
                        value=f"{catalog.description(item)}\n{CURRENCY}{price}", inline=False)
    embed.set_footer(text=f"Page {page + 1} / {total_pages}")
    return embed, current, total_pages


class ItemRow(ServiceView):
    def __init__(self, services, items, page, total_pages, user_id):
        super().__init__(services, timeout=300)
        self.page = page
        self.total_pages = total_pages
        self.user_id = user_id
        for item_name, price in items:
            self.add_item(BuyButton(item_name, price))
        self.add_item(PageNav("⬅ Previous", -1))
        self.add_item(PageNav("Next ⮕", 1))

    async def interaction_check(self, interaction: Interaction):
        if interaction.user.id != self.user_id:
            embed = discord.Embed(description="You can't use this menu!", color=Colors.UNAVAILABLE)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True


class BuyButton(ServiceButton):
    def __init__(self, item_name, price):
        super().__init__(label=item_name.title(), style=ButtonStyle.success, custom_id=f"buy_{item_name}")
        self.item_name = item_name
        self.price = price

    async def callback(self, interaction: Interaction):
        emoji = self.services.catalog.emoji(self.item_name)
        try:
            purchase = self.services.shop.buy(interaction.user.id, self.item_name, 1)
        except ShopRefused as refusal:
            if refusal.code != "insufficient_wallet":
                raise
            embed = discord.Embed(
                description=f"❌ You don't have enough money in your wallet to buy {emoji} **{self.item_name}**.",
                color=Colors.FAIL)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = discord.Embed(
            description=f"✅ You bought **1 {emoji} {self.item_name.title()}** for {CURRENCY}{purchase.total}",
            color=Colors.SUCCESS)
        await interaction.response.send_message(embed=embed)


class PageNav(ServiceButton):
    def __init__(self, label, direction):
        super().__init__(label=label, style=ButtonStyle.primary)
        self.direction = direction

    async def callback(self, interaction: Interaction):
        view: ItemRow = self.view
        new_page = (view.page + self.direction) % view.total_pages
        wallet = self.services.users.get(interaction.user.id).get("wallet", 0)
        embed, items, total_pages = shop_page(self.services, new_page, wallet)
        await interaction.response.edit_message(
            embed=embed, view=ItemRow(self.services, items, new_page, view.total_pages, view.user_id))
