"""Birthday visuals."""
from __future__ import annotations

from datetime import date

import discord

from rudhabot.domain.birthday import BirthdayDate
from rudhabot.services.birthdays import Greeting, Upcoming
from rudhabot.ui.replies import author_embed
from rudhabot.ui.theme import CURRENCY, Colors


def countdown(days: int) -> str:
    return "**today!** 🎉" if days == 0 else "tomorrow" if days == 1 else f"in **{days}** days"


def birthday_saved(user, birthday: BirthdayDate) -> discord.Embed:
    return author_embed(user, f"🎂 Your birthday is set to **{birthday.label()}**.\n"
                              f"You'll get a gift and a shout-out when the day comes!", Colors.SUCCESS)


def birthday_card(user, birthday: BirthdayDate, today: date, own: bool) -> discord.Embed:
    days = birthday.days_until(today)
    lines = [f"🎂 **{birthday.label()}**", f"Next birthday {countdown(days)}"]
    turning = birthday.turning(today)
    if turning is not None:
        lines.append(f"Turning **{turning}**")
    embed = discord.Embed(title=f"{user.name}'s Birthday", description="\n".join(lines), color=Colors.PARTY)
    embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def upcoming_list(rows: list[Upcoming]) -> discord.Embed:
    embed = discord.Embed(title="🎂 Upcoming birthdays", color=Colors.PARTY)
    if not rows:
        embed.description = "No birthdays registered yet. Use `/birthday set` to add yours!"
        return embed
    embed.description = "\n".join(
        f"<@{r.user_id}> — **{r.birthday.day} {r.on.strftime('%B')}** ({countdown(r.days_until)})"
        + (f", turning {r.turning}" if r.turning is not None else "")
        for r in rows
    )
    return embed


def greeting(event: Greeting) -> discord.Embed:
    age = f" 🎈 Turning **{event.age}** today!" if event.age is not None else ""
    return discord.Embed(
        title="🎂 Happy Birthday!",
        description=f"Everyone wish <@{event.user_id}> a happy birthday!{age}\n"
                    f"As a gift, **{CURRENCY}{event.gift}** has been added to their wallet.",
        color=Colors.PARTY,
    )


def cake_celebration(cake_emoji: str, message: str, sender_name: str) -> discord.Embed:
    embed = discord.Embed(title="🎊 Happy Birthday!", description=f"{cake_emoji} {message}\n\n- From {sender_name}",
                          color=Colors.BLURPLE)
    embed.set_footer(text="Sent by Rudhabot • Enjoy your special day!")
    return embed
