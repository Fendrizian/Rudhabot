"""`/setup`: the server profile (channels and roles Rudhabot uses) next to the security set-up, in one view."""
from __future__ import annotations

from typing import Optional

import discord

from rudhabot.ui.theme import Colors


def _mention(kind: str, value: int) -> str:
    if not value:
        return "not set"
    return f"<#{value}>" if kind == "channel" else f"<@&{value}>"


def profile_embed(profile, home: Optional[int], security=None, changes: Optional[dict] = None,
                  problems: Optional[list] = None) -> discord.Embed:
    embed = discord.Embed(title="⚙️ Server setup", color=Colors.SECURITY,
                          description=("Changed: " + ", ".join(f"`{k}`" for k in changes) + "\n" if changes else "")
                          + "Everything reads these live; no restart needed. `reset:` puts one back to its default.")
    entries = profile.entries()
    for kind, title in (("channel", "Channels"), ("role", "Roles")):
        lines = [f"**{e.key}** {_mention(kind, e.value)}{' ✏️' if e.overridden else ''}\n-# {e.purpose}"
                 for e in entries if e.kind == kind]
        embed.add_field(name=title, value="\n".join(lines)[:1024], inline=False)
    if security is not None:
        sec = [f"alerts {_mention('channel', int(security['alert_channel'] or 0))}",
               f"mod log {_mention('channel', int(security['modlog_channel'] or security['alert_channel'] or 0))}",
               f"probation {_mention('role', int(security['probation_role'] or 0))}",
               f"mode **{security['mode']}**"]
        embed.add_field(name="Security (change with /security setup)", value=" · ".join(sec), inline=False)
    embed.add_field(name="Home server", value=f"`{home}`" if home else "not known yet", inline=False)
    banker = next((e.value for e in entries if e.key == "banker"), 0)
    embed.add_field(name="Discord's command list", value=(
        "Staff commands are hidden from members there. Roles that should see them without the matching Discord "
        "permission need an override in Server Settings > Integrations > Rudhabot: "
        f"{_mention('role', banker)} for `/banker`, `/audit` and `/review`, and any `/security staff` role without "
        "Manage Messages, Kick Members or Ban Members."), inline=False)
    if problems:
        embed.add_field(name="Not changed", value="\n".join(problems)[:1024], inline=False)
    embed.set_footer(text="✏️ = set here with /setup (otherwise the default or the RUDHABOT_*_ID setting)")
    return embed
