"""Shop, cafe and inventory commands, plus /use (mystery boxes, lottery tickets, cakes, food and drink)."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import not_in_game
from rudhabot.domain.fortunes import crack
from rudhabot.services.lottery import TicketRefused
from rudhabot.services.shop import ShopRefused
from rudhabot.ui import lottery_embeds
from rudhabot.ui.replies import author_embed, error_embed, respond
from rudhabot.ui.theme import BOXICON, CAFEICON, CURRENCY
from rudhabot.views.birthday import BirthdayCakeModal
from rudhabot.views.cafe import CafeBuyView, special_fields
from rudhabot.views.inventory import InventoryView, create_inventory_embed, inventory_pages
from rudhabot.views.shop import ItemRow, shop_page


FORTUNES_SHOWN = 10          # fortunes listed when several cookies are eaten at once

class ShopCog(ServiceCog):
    @app_commands.command(name="cafe", description="View the Joot Cafe menu")
    @not_in_game()
    async def cafe(self, interaction: discord.Interaction):
        s = self.services
        account = self.users.get(interaction.user.id)
        embed = discord.Embed(
            title=f"{CAFEICON} JOOT CAFE",
            description=f"Welcome to Joot Cafe! The oldest running business in Rudharta that is full of history.\n"
                        f"Here are the special items for {s.menu.month_name}!",
            color=0xE67E22,
        )
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Wallet: {account.get('wallet', 0)}ra • Special items: 1 per person per month")
        special_fields(embed, s, s.menu.specials_with_stock())
        view = CafeBuyView(s, interaction.user.id, "specials")
        await respond(interaction, embed)
        await interaction.followup.send(view=view)

    @app_commands.command(name="shop", description="View the shop items")
    @not_in_game()
    async def shop(self, interaction: discord.Interaction):
        account = self.users.get(interaction.user.id)
        embed, items, total_pages = shop_page(self.services, 0, account.get("wallet", 0))
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        view = ItemRow(self.services, items, 0, total_pages, interaction.user.id)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="buy", description="Buy an item from the shop or cafe")
    @app_commands.describe(item="Name of the item to buy", quantity="Quantity of the item to buy",
                           pay_with="Pay from your wallet (default) or with your credit card")
    @app_commands.choices(pay_with=[app_commands.Choice(name="Wallet", value="wallet"),
                                    app_commands.Choice(name="Credit card", value="card")])
    @not_in_game()
    async def buy(self, interaction: discord.Interaction, item: str, quantity: int = 1,
                  pay_with: Optional[app_commands.Choice[str]] = None):
        user, catalog = interaction.user, self.services.catalog
        canonical = catalog.resolve(item)
        if not canonical:
            await respond(interaction, error_embed("❌ Item not found.", user), ephemeral=True)
            return
        emoji, title = catalog.emoji(canonical), canonical.title()
        try:
            purchase = self.services.shop.buy(user.id, canonical, quantity, pay_with.value if pay_with else "wallet")
        except ShopRefused as refusal:
            ephemeral = True
            message = {
                "card_declined": refusal.data.get("message", "❌ Card declined."),
                "not_positive": "❌ Quantity must be at least 1",
                "unknown_item": "❌ Item not found.",
                "already_bought_special": f"❌ You can only buy one {emoji}**{title}** per month!",
                "special_quantity": f"❌ You can only buy 1 {emoji}**{title}** per month!",
                "sold_out": f"❌ {emoji}**{title}** is sold out!",
                "insufficient_wallet": f"❌ You don't have enough money in your wallet to buy {quantity} of **{title}**.",
            }[refusal.code]
            if refusal.code == "insufficient_wallet":
                ephemeral = False
            await respond(interaction, error_embed(message, user), ephemeral=ephemeral)
            return
        paid = " on your credit card 💳" if pay_with and pay_with.value == "card" else ""
        await respond(interaction, author_embed(user, f"✅ You bought **{quantity} {emoji}{title}** for {CURRENCY}{purchase.total}{paid}", 0x57F287))

    @app_commands.command(name="sell", description="Sell shop items for half their price.")
    @app_commands.describe(item="Name of the item to sell", quantity="Quantity to sell (default 1)")
    @not_in_game()
    async def sell(self, interaction: discord.Interaction, item: str, quantity: int = 1):
        user, catalog = interaction.user, self.services.catalog
        canonical = catalog.resolve(item)
        if not canonical:
            await respond(interaction, error_embed("❌ Item not found.", user), ephemeral=True)
            return
        emoji, title = catalog.emoji(canonical), canonical.title()
        try:
            sale = self.services.shop.sell(user.id, canonical, quantity)
        except ShopRefused as refusal:
            message = {"cannot_sell": f"❌ You can't sell **{emoji}{title}**",
                       "not_enough_items": f"❌ You don't have enough **{emoji}{title}** to sell."}[refusal.code]
            await respond(interaction, error_embed(message, user), ephemeral=True)
            return
        await respond(interaction, author_embed(user, f"✅ You sold **{quantity} {emoji}{title}** for {CURRENCY}{sale.total}.", 0x57F287))

    @app_commands.command(name="inventory", description="Show all items you own and their quantity.")
    @not_in_game()
    async def inventory(self, interaction: discord.Interaction):
        items = list(self.users.get(interaction.user.id).get("inventory", {}).items())
        embed = create_inventory_embed(self.services.catalog, items, interaction.user, 0, inventory_pages(items))
        await interaction.response.send_message(embed=embed, view=InventoryView(self.services, items, interaction.user, 0))

    # ------------------------------------------------------------------- /use
    @app_commands.command(name="use", description="Use an item from your inventory")
    @app_commands.describe(item="Name of the item to use", quantity="Quantity to use (default 1)")
    @not_in_game()
    async def use(self, interaction: discord.Interaction, item: str, quantity: int = 1):
        s, user = self.services, interaction.user
        canonical = s.catalog.resolve(item)
        if not canonical:
            await respond(interaction, error_embed("❌ Item not found.", user), ephemeral=True)
            return
        item = canonical
        inventory = self.users.get(user.id).get("inventory", {})
        emoji = s.catalog.emoji(item)
        if item not in inventory or inventory[item] < quantity or quantity < 1:
            await respond(interaction, error_embed(f"❌ You don't have enough **{item.title()}** to use.", user), ephemeral=True)
            return

        if item == "mystery box":
            await self._open_mystery_boxes(interaction, inventory, quantity)
        elif s.lottery.is_ticket(item):
            await self._draw_tickets(interaction, item, quantity)
        elif item == "special birthday cake":
            await interaction.response.send_modal(BirthdayCakeModal(s, user))
        elif item in s.catalog.all_cafe_item_names():
            inventory[item] -= quantity
            if inventory[item] <= 0:
                del inventory[item]
            self.users.update(user.id, inventory=inventory)
            if item == "fortune cookie":
                await self._read_fortunes(interaction, emoji, quantity)
                return
            verb = "drank" if s.catalog.is_beverage(item) else "ate"
            await respond(interaction, author_embed(user, f"✅ You {verb} **{quantity} {emoji}{item.title()}**!", 0x57F287))
        elif item in s.catalog.shop_items:
            await respond(interaction, error_embed(f"❌ You can't use **{emoji}{item.title()}**. This item is not usable.", user))
        else:
            await respond(interaction, error_embed("❌ This item can't be used.", user))

    async def _read_fortunes(self, interaction, emoji: str, quantity: int):
        """Each cookie draws one fortune uniformly at random (see domain/fortunes.py). Up to 10 are listed."""
        fortunes = [crack(self.services.rng) for _ in range(quantity)]
        shown = fortunes[:FORTUNES_SHOWN]
        lines = [f"🥠 *{f}*" for f in shown]
        if len(fortunes) > len(shown):
            lines.append(f"…and {len(fortunes) - len(shown)} more crumbs of wisdom.")
        title = f"{emoji} Your fortune" + ("s" if quantity > 1 else "")
        await respond(interaction, author_embed(interaction.user, "\n".join(lines), 0xE67E22, title=title))

    async def _open_mystery_boxes(self, interaction, inventory: dict, quantity: int):
        s, user, catalog = self.services, interaction.user, self.services.catalog
        inventory["mystery box"] -= quantity
        if inventory["mystery box"] <= 0:
            del inventory["mystery box"]
        self.users.update(user.id, inventory=inventory)
        results, item_rewards, money_total, zonk_count = [], {}, 0, 0
        for _ in range(quantity):
            kind, value = s.mystery_box.draw()
            if kind == "item":
                item_rewards[value] = item_rewards.get(value, 0) + 1
                results.append(f"{catalog.emoji(value)} {value.title()}")
            elif kind == "money":
                money_total += value
                results.append(f"{CURRENCY} {value}")
            else:
                zonk_count += 1
                results.append("💤 Zonk! Nothing.")
        for reward, qty in item_rewards.items():
            inventory[reward] = inventory.get(reward, 0) + qty
        wallet = self.users.get(user.id)["wallet"] + money_total
        self.users.update(user.id, inventory=inventory, wallet=wallet)
        embed = author_embed(user, "\n".join(results), 0x7289DA, title=f"{BOXICON} Mystery Box Results")
        summary = []
        if item_rewards:
            summary.append("**Items received:** " + ", ".join(f"{catalog.emoji(k)} {k.title()} x{v}" for k, v in item_rewards.items()))
        if money_total > 0:
            summary.append(f"**Total Money:** {CURRENCY}{money_total}")
        if zonk_count > 0:
            summary.append(f"**Zonk:** {zonk_count} time(s)")
        if summary:
            embed.add_field(name="Summary", value="\n".join(summary), inline=False)
        await respond(interaction, embed)

    async def _draw_tickets(self, interaction, item: str, quantity: int):
        s, user = self.services, interaction.user
        try:
            result = s.lottery.draw(user.id, item, quantity)
        except TicketRefused:                      # the checks above make this unreachable in practice
            await respond(interaction, error_embed(f"❌ You don't have enough **{item.title()}** to use.", user), ephemeral=True)
            return
        await respond(interaction, lottery_embeds.ticket_result(user, result, s.catalog))
        if result.jackpot_hit:
            await s.announcer.jackpot(user.id, result.batch.spec, result.batch.jackpots)
