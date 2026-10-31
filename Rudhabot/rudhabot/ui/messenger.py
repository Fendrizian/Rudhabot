"""Delivery of DMs and channel posts. One place for the "find the user / try, log and carry on" plumbing."""
from __future__ import annotations

import logging
from typing import Optional

import discord

log = logging.getLogger("rudhabot.messenger")


class Messenger:
    def __init__(self, client: discord.Client):
        self._client = client

    async def find_user(self, user_id: int) -> Optional[discord.User]:
        """Cache first, then the API."""
        user = self._client.get_user(user_id)
        if user:
            return user
        try:
            return await self._client.fetch_user(user_id)
        except discord.NotFound:
            log.info("user %s not found on Discord", user_id)
        except Exception as error:
            log.warning("could not fetch user %s: %s", user_id, error)
        return None

    async def dm(self, user: discord.abc.User, **message) -> bool:
        try:
            await user.send(**message)
            return True
        except discord.Forbidden:
            log.info("DM to %s forbidden", getattr(user, "id", user))
        except Exception as error:
            log.warning("DM to %s failed: %s", getattr(user, "id", user), error)
        return False

    async def resolve_channel(self, channel_id: int):
        channel = self._client.get_channel(channel_id)
        if channel:
            return channel
        try:
            return await self._client.fetch_channel(channel_id)
        except Exception as error:
            log.warning("channel %s unavailable: %s", channel_id, error)
            return None

    async def post(self, channel, **message) -> bool:
        """`channel` may be a channel object or an id. Never raises."""
        if isinstance(channel, int):
            channel = await self.resolve_channel(channel)
        if channel is None:
            return False
        try:
            await channel.send(**message)
            return True
        except Exception as error:
            log.warning("posting to channel %s failed: %s", getattr(channel, "id", channel), error)
            return False
