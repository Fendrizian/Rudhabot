"""Lottery visuals: ticket results, the /lottery info card, and odds formatting."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import discord

from rudhabot.domain.catalog import ItemCatalog
from rudhabot.domain.lottery import TicketSpec
from rudhabot.services.lottery import LotteryResult
from rudhabot.ui.replies import author_embed
from rudhabot.ui.theme import CURRENCY, Colors

MAX_PRIZE_LINES = 12


def odds(spec: TicketSpec, tier_index: int) -> str:
    table = spec.table
    return f"1 in {round(table.denominator / table.tiers[tier_index].weight):,}"


def percent(value) -> str:
    return f"{float(value) * 100:.2f}%"


def ticket_result(user, result: LotteryResult, catalog: ItemCatalog) -> discord.Embed:
    batch, spec = result.batch, result.batch.spec
    emoji = catalog.emoji(spec.item)
    if batch.tickets == 1:
        embed = _single(user, result, emoji)
    else:
        embed = _batch(user, result, emoji)
    return embed


def _single(user, result: LotteryResult, emoji: str) -> discord.Embed:
    batch, spec = result.batch, result.batch.spec
    amount = batch.total_won
    if batch.jackpots:
        embed = author_embed(user, f"🎰 **JACKPOT!**\n\nYou won **{CURRENCY}{amount}**!!!\n"
                                   f"The odds were {odds(spec, len(spec.table.tiers) - 1)}.", Colors.INCOME)
    elif amount:
        embed = author_embed(user, f"🎉 **You won {CURRENCY}{amount}!**", Colors.SUCCESS)
    else:
        embed = author_embed(user, "😶 **Zonk!** This ticket won nothing.", Colors.GAMBLING)
    embed.title = f"{emoji} {spec.label} Lottery Ticket"
    embed.set_footer(text=f"{result.tickets_left} {spec.label.lower()} ticket(s) left")
    embed.add_field(name="Balance", value=f"{CURRENCY}{result.wallet_after}", inline=True)
    return embed


def _batch(user, result: LotteryResult, emoji: str) -> discord.Embed:
    batch, spec = result.batch, result.batch.spec
    lines = []
    if batch.jackpots:
        lines.append(f"🎰 **JACKPOT** × {batch.jackpots}  —  {CURRENCY}{spec.jackpot * batch.jackpots}")
    prizes = sorted((a, c) for a, c in batch.wins.items() if a != spec.jackpot or not batch.jackpots)
    for amount, count in reversed(prizes[-MAX_PRIZE_LINES:]):
        lines.append(f"{CURRENCY}{amount} × {count}")
    if len(prizes) > MAX_PRIZE_LINES:
        lines.append(f"…and {len(prizes) - MAX_PRIZE_LINES} smaller prize tiers")
    if batch.zonks:
        lines.append(f"😶 Zonk × {batch.zonks}")

    color = Colors.INCOME if batch.jackpots else Colors.SUCCESS if batch.total_won else Colors.GAMBLING
    embed = author_embed(user, "\n".join(lines), color)
    embed.title = f"{emoji} {spec.label} Lottery — {batch.tickets} tickets"
    embed.add_field(name="Won", value=f"{CURRENCY}{batch.total_won}", inline=True)
    embed.add_field(name="Ticket value", value=f"{CURRENCY}{batch.tickets * spec.price}", inline=True)
    net = batch.net
    embed.add_field(name="Net", value=f"{'+' if net >= 0 else '-'}{CURRENCY}{abs(net)}", inline=True)
    embed.set_footer(text=f"{result.tickets_left} {spec.label.lower()} ticket(s) left")
    return embed


def lottery_info(specs, catalog: ItemCatalog, inventory: dict, recent: list[dict],
                 channel_id: Optional[int] = None) -> discord.Embed:
    embed = discord.Embed(
        title="🎟️ Lottery",
        description="Buy tickets from the shop (`/buy`) and draw them with `/use`. Prizes are paid instantly."
                    + (f"\nJackpot winners are announced in <#{channel_id}>." if channel_id else ""),
        color=Colors.INCOME,
    )
    for spec in specs:
        table = spec.table
        prizes = "\n".join(
            f"{'🎰 ' if t.amount == spec.jackpot else ''}{CURRENCY}{t.amount} — {odds(spec, i)}"
            for i, t in reversed(list(enumerate(table.tiers)))
        )
        header = (f"Win chance **{percent(table.win_probability)}** • Zonk **{percent(table.zonk_probability)}**\n"
                  f"Returns about **{float(spec.return_to_player) * 100:.0f}%** of the ticket price on average\n")
        embed.add_field(name=f"{catalog.emoji(spec.item)} {spec.label} Ticket — {CURRENCY}{spec.price}",
                        value=header + prizes, inline=False)
    owned = [f"{catalog.emoji(s.item)} {s.label}: **{inventory.get(s.item, 0)}**" for s in specs]
    embed.add_field(name="Your tickets", value="\n".join(owned), inline=False)
    if recent:
        lines = []
        for win in recent:
            stamp = int(datetime.fromisoformat(win["at"]).timestamp())
            label = next((s.label for s in specs if s.item == win["item"]), "Lottery")
            lines.append(f"<@{win['user_id']}> won {CURRENCY}{win['amount']} ({label}) <t:{stamp}:R>")
        embed.add_field(name="🏆 Recent jackpots", value="\n".join(lines), inline=False)
    return embed
