"""Joot Cafe: menu views, the item select and the quantity modal."""
from __future__ import annotations

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.services.shop import ShopRefused
from rudhabot.ui.replies import author_embed, error_embed
from rudhabot.ui.theme import CAFEICON, CURRENCY, Colors
from rudhabot.views.base import ServiceButton, ServiceModal, ServiceSelect, ServiceView
from rudhabot.views.payment import PayToggle, paid_suffix

WELCOME = "Welcome to Joot Cafe! The oldest running business in Rudharta that is full of history.\n"


def special_fields(embed: discord.Embed, services, specials) -> None:
    cap = services.menu.stock_cap
    for item, price, stock in specials:
        emoji = services.catalog.emoji(item)
        if stock <= 0:
            embed.add_field(name=f"{emoji} {item.title()} - SOLD OUT!", value=f"{CURRENCY}{price}", inline=True)
        else:
            embed.add_field(name=f"{emoji} {item.title()}", value=f"{CURRENCY}{price}\nStock: {stock}/{cap}", inline=True)


class CafeBuyView(ServiceView):
    def __init__(self, services, user_id, menu_type="specials", pay_with: str = "wallet"):
        super().__init__(services, timeout=300)
        self.user_id = user_id
        self.pay_with = pay_with
        self.menu_type = menu_type
        catalog = services.catalog
        if menu_type == "specials":
            available = [(item, price) for item, price, stock in services.menu.specials_with_stock() if stock > 0]
            if available:                                  # only offer items that are in stock
                self.add_item(CafeItemSelect(available))
        elif menu_type == "food":
            self.add_item(CafeItemSelect(list(catalog.cafe_food_items.items())))
        else:
            self.add_item(CafeItemSelect(list(catalog.cafe_beverage_items.items())))
        self.add_item(MenuSwitchButton("⭐ Specials", "specials"))
        self.add_item(MenuSwitchButton("🍽️ Food", "food"))
        self.add_item(MenuSwitchButton("🥤 Beverages", "beverages"))
        self.add_item(PayToggle(pay_with))

    async def interaction_check(self, interaction: Interaction):
        return interaction.user.id == self.user_id


class MenuSwitchButton(ServiceButton):
    def __init__(self, label, menu_type):
        super().__init__(label=label, style=ButtonStyle.success if menu_type == "specials" else ButtonStyle.primary)
        self.menu_type = menu_type

    async def callback(self, interaction: Interaction):
        view: CafeBuyView = self.view
        services = self.services
        catalog = services.catalog
        wallet = services.users.get(interaction.user.id).get("wallet", 0)
        if self.menu_type == "specials":
            description = f"{WELCOME}Here are the special items for {services.menu.month_name}!"
        elif self.menu_type == "food":
            description = f"{WELCOME}Here are some delicious food items from Joot Cafe!"
        else:
            description = f"{WELCOME}Here are some refreshing beverages from Joot Cafe!"
        embed = discord.Embed(title=f"{CAFEICON} JOOT CAFE", description=description, color=Colors.CAFE)
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Wallet: {wallet}ra")
        if self.menu_type == "specials":
            special_fields(embed, services, services.menu.specials_with_stock())
        else:
            items = catalog.cafe_food_items if self.menu_type == "food" else catalog.cafe_beverage_items
            for item, price in items.items():
                embed.add_field(name=f"{catalog.emoji(item)} {item.title()}", value=f"{CURRENCY}{price}", inline=True)
        await interaction.response.edit_message(embed=embed, view=CafeBuyView(services, view.user_id, self.menu_type, view.pay_with))


class CafeItemSelect(ServiceSelect):
    def __init__(self, items):
        options = [discord.SelectOption(label=f"{name.title()}", value=name) for name, _ in items]
        super().__init__(placeholder="Choose an item to buy...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: Interaction):
        await interaction.response.send_modal(CafeQuantityModal(self.services, self.values[0], getattr(self.view, "pay_with", "wallet")))


class CafeQuantityModal(ServiceModal):
    def __init__(self, services, item, pay_with: str = "wallet"):
        super().__init__(services, title="Cafe Purchase")
        self.item = item
        self.pay_with = pay_with
        self.quantity = ui.TextInput(label="Quantity", placeholder="Enter quantity", required=True)
        self.add_item(self.quantity)

    async def on_submit(self, interaction: Interaction):
        user = interaction.user
        try:
            quantity = int(self.quantity.value)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("❌ Invalid quantity format.", user), ephemeral=True)
            return
        if quantity < 1:
            await interaction.response.send_message(embed=error_embed("❌ Quantity must be at least 1.", user), ephemeral=True)
            return
        title, emoji = self.item.title(), self.services.catalog.emoji(self.item)
        try:
            purchase = self.services.shop.buy(user.id, self.item, quantity, self.pay_with)
        except ShopRefused as refusal:
            message = {
                "already_bought_special": f"❌ You can only buy 1 {emoji}**{title}** per month!",
                "special_quantity": f"❌ You can only buy 1 {emoji}**{title}** per month!",
                "sold_out": f"❌ {emoji}**{title}** is sold out!",
                "insufficient_wallet": f"❌ You don't have enough money in your wallet to buy {quantity} of **{emoji}{title}**.",
                "card_declined": refusal.data.get("message", "❌ Card declined."),
            }.get(refusal.code)
            if message is None:
                raise
            await interaction.response.send_message(embed=error_embed(message, user), ephemeral=True)
            return
        embed = author_embed(user, f"✅ You bought **{quantity} {emoji}{title}** for {CURRENCY}{purchase.total}{paid_suffix(self.pay_with)}", Colors.SUCCESS)
        await interaction.response.send_message(embed=embed)
