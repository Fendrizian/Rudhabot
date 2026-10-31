"""Public announcements: lottery jackpots, birthday greetings and delivered cake messages."""
from __future__ import annotations

import discord

from rudhabot.domain.catalog import ItemCatalog
from rudhabot.domain.lottery import TicketSpec
from rudhabot.services.birthdays import Greeting
from rudhabot.settings import Channels
from rudhabot.ui import birthday_embeds
from rudhabot.ui.messenger import Messenger
from rudhabot.ui.theme import CURRENCY, Colors


class Announcer:
    def __init__(self, messenger: Messenger, channels: Channels, catalog: ItemCatalog):
        self._messenger = messenger
        self._channels = channels
        self._catalog = catalog

    async def jackpot(self, user_id: int, spec: TicketSpec, count: int = 1) -> bool:
        embed = discord.Embed(
            title="🎰 LOTTERY JACKPOT! 🎰",
            description=f"<@{user_id}> just hit the **{spec.label} lottery** jackpot"
                        f"{f' {count} times' if count > 1 else ''}!\n\n"
                        f"**{CURRENCY}{spec.jackpot * count}**",
            color=Colors.INCOME,
        )
        thumbnail = _emoji_url(self._catalog.emoji(spec.item))
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        return await self._messenger.post(self._channels.lottery, content=f"🎉 <@{user_id}> 🎉", embed=embed)

    async def birthday(self, greeting: Greeting) -> bool:
        return await self._messenger.post(self._channels.birthdays, content=f"<@{greeting.user_id}>",
                                          embed=birthday_embeds.greeting(greeting))

    async def cake(self, entry: dict, sender_name: str) -> bool:
        """Deliver a scheduled cake message to the recipient (DM) and the birthday channel. True if either worked."""
        embed = birthday_embeds.cake_celebration(entry.get("message") or "Congratulations!", sender_name)
        recipient_id = int(entry["scheduled_for"])
        delivered = False
        user = await self._messenger.find_user(recipient_id)
        if user:
            delivered |= await self._messenger.dm(user, embed=embed)
        delivered |= await self._messenger.post(self._channels.birthdays, content=f"- <@{recipient_id}> -", embed=embed)
        return delivered


def _emoji_url(emoji: str) -> str | None:
    """CDN url of a custom emoji given as `<:name:id>` (None if it isn't one)."""
    try:
        return f"https://cdn.discordapp.com/emojis/{int(emoji.strip('<>').split(':')[-1])}.png"
    except (ValueError, IndexError):
        return None
