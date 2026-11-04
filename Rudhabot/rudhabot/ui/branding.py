"""Clairvoyance branding: every message a Clairvoyance model contributed to carries its name and logo.

Embeds get a footer with the Clairvoyance logo as its icon (the custom emoji's image, which renders even where custom
emoji text can't, e.g. in footers). Titles and fields keep using CLAIRVOYANCEICON inline.
"""
from __future__ import annotations

import re

import discord

from rudhabot.ui.theme import CLAIRVOYANCEICON

_EMOJI_ID = re.search(r":(\d+)>", CLAIRVOYANCEICON).group(1)
CLAIRVOYANCE_ICON_URL = f"https://cdn.discordapp.com/emojis/{_EMOJI_ID}.png"
NAME = "Clairvoyance"


def brand(embed: discord.Embed, note: str = "") -> discord.Embed:
    """Put the Clairvoyance logo in the footer, keeping any footer text already there."""
    existing = embed.footer.text if embed.footer and embed.footer.text else ""
    parts = [] if NAME in existing else [NAME]
    parts += [p for p in (note, existing) if p]
    embed.set_footer(text=" · ".join(parts)[:2048], icon_url=CLAIRVOYANCE_ICON_URL)
    return embed


def is_branded(embed: discord.Embed) -> bool:
    return bool(embed.footer and embed.footer.icon_url == CLAIRVOYANCE_ICON_URL)
