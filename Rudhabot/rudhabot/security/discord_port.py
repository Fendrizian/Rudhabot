"""The one module that talks to discord.py: converts Discord objects into the security model and events, and
implements GuildPort with real API calls (every Discord error becomes an ActionFailed with a short, safe message).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Callable, Optional

import discord

from rudhabot.security import perms as P
from rudhabot.security.actions import ActionFailed
from rudhabot.security.events import Attachment, AuditEntry, MemberJoin, Message
from rudhabot.security.model import ChannelModel, GuildModel, MemberModel, Overwrite, RoleModel

log = logging.getLogger("rudhabot.security")

CHANNEL_TYPES = {discord.ChannelType.text: "text", discord.ChannelType.news: "news",
                 discord.ChannelType.forum: "forum", discord.ChannelType.voice: "voice",
                 discord.ChannelType.stage_voice: "stage", discord.ChannelType.category: "category",
                 discord.ChannelType.media: "forum"}


def _ts(dt) -> Optional[float]:
    return dt.timestamp() if dt is not None else None


# ====================================================================== model
def guild_model(guild: discord.Guild, automod_rules: int = 0) -> GuildModel:
    roles = tuple(RoleModel(r.id, r.name, r.position, r.permissions.value, r.managed, r.colour.value, r.hoist,
                            r.mentionable, len(r.members), getattr(r.tags, "bot_id", None) if r.tags else None)
                  for r in guild.roles)
    channels = []
    for c in guild.channels:
        kind = CHANNEL_TYPES.get(c.type)
        if kind is None:
            continue
        overwrites = []
        for target, ow in c.overwrites.items():
            allow, deny = ow.pair()
            overwrites.append(Overwrite(target.id, "member" if isinstance(target, (discord.Member, discord.User))
                                        else "role", allow.value, deny.value))
        channels.append(ChannelModel(c.id, c.name, kind, c.position, c.category_id, getattr(c, "topic", None),
                                     bool(getattr(c, "nsfw", False)), int(getattr(c, "slowmode_delay", 0) or 0),
                                     tuple(overwrites)))
    interesting = {guild.owner_id, guild.me.id if guild.me else 0}
    dangerous_roles = {r.id for r in guild.roles if r.permissions.value & P.DANGEROUS}
    members = []
    for m in guild.members:
        if m.id in interesting or m.bot or any(r.id in dangerous_roles for r in m.roles):
            members.append(member_model(m))
    return GuildModel(guild.id, guild.name, guild.owner_id or 0, guild.member_count or len(guild.members),
                      int(guild.mfa_level.value if hasattr(guild.mfa_level, "value") else guild.mfa_level),
                      guild.verification_level.value, guild.explicit_content_filter.value,
                      guild.default_notifications.value, roles, tuple(channels), tuple(members),
                      guild.me.id if guild.me else 0, automod_rules, tuple(guild.features))


def member_model(m: discord.Member) -> MemberModel:
    return MemberModel(m.id, tuple(r.id for r in m.roles), m.bot, _ts(m.joined_at), _ts(m.created_at),
                       _ts(m.timed_out_until))


# ====================================================================== events
def join_event(member: discord.Member) -> MemberJoin:
    flags = getattr(member, "public_flags", None)
    return MemberJoin(member.guild.id, member.id, _ts(member.joined_at) or discord.utils.utcnow().timestamp(),
                      member.created_at.timestamp(), member.bot, member.avatar is not None, member.name,
                      member.avatar.key if member.avatar else None, bool(flags and flags.spammer))


def message_event(message: discord.Message, tier_of: Callable[[discord.Member], int]) -> Message:
    author = message.author
    member = author if isinstance(author, discord.Member) else None
    content = message.content or ""
    mentions = {u.id for u in message.mentions if not u.bot and u.id != author.id}
    return Message(
        guild_id=message.guild.id, channel_id=message.channel.id, message_id=message.id, author_id=author.id,
        ts=message.created_at.timestamp(), content=content[:4000],
        author_tier=int(tier_of(member)) if member else 0,
        author_roles=tuple(r.id for r in member.roles) if member else (),
        author_bot=author.bot, webhook_id=message.webhook_id,
        joined_at=_ts(member.joined_at) if member else None, created_at=author.created_at.timestamp(),
        user_mentions=len(mentions), role_mentions=len(message.role_mentions),
        everyone_attempt=("@everyone" in content or "@here" in content),
        attachments=tuple(Attachment(a.filename, a.size, a.content_type) for a in message.attachments[:10]),
        embeds=len(message.embeds), stickers=len(message.stickers), is_reply=message.reference is not None)


def _perm(diff, name):
    value = getattr(diff, name, None)
    return value.value if isinstance(value, discord.Permissions) else None


def audit_event(entry: discord.AuditLogEntry, tier_of: Callable[[Optional[discord.Member]], int]) -> AuditEntry:
    guild = entry.guild
    actor = guild.get_member(entry.user_id) if entry.user_id else None
    target = entry.target
    target_id = getattr(target, "id", None)
    before, after = entry.before, entry.after
    action = entry.action.name
    perms_before = perms_after = None
    is_everyone = False
    roles_added = roles_removed = ()
    small_before, small_after = {}, {}
    if action == "role_update":
        perms_before, perms_after = _perm(before, "permissions"), _perm(after, "permissions")
        is_everyone = target_id == guild.id
    elif action in ("overwrite_create", "overwrite_update"):
        perms_before, perms_after = _perm(before, "allow") or 0, _perm(after, "allow")
        extra = entry.extra
        is_everyone = getattr(extra, "id", None) == guild.id
    elif action == "member_role_update":
        roles_added = tuple(r.id for r in (getattr(after, "roles", None) or []))
        roles_removed = tuple(r.id for r in (getattr(before, "roles", None) or []))
    elif action == "guild_update":
        for key in ("verification_level", "mfa_level", "explicit_content_filter"):
            b, a = getattr(before, key, None), getattr(after, key, None)
            if b is not None and a is not None:
                small_before[key] = int(getattr(b, "value", b))
                small_after[key] = int(getattr(a, "value", a))
        if getattr(after, "vanity_url_code", None) is not None:
            small_after["vanity_url_code"] = True
    elif action == "automod_rule_update":
        enabled = getattr(after, "enabled", None)
        if enabled is not None:
            small_after["enabled"] = bool(enabled)
    return AuditEntry(guild.id, action, entry.user_id or 0, entry.created_at.timestamp(), target_id,
                      type(target).__name__.lower() if target is not None else None, (entry.reason or "")[:200],
                      actor_bot=bool(actor and actor.bot), actor_tier=int(tier_of(actor)) if actor else 0,
                      actor_roles=tuple(r.id for r in actor.roles) if actor else (),
                      perms_before=perms_before, perms_after=perms_after, roles_added=roles_added,
                      roles_removed=roles_removed, target_is_everyone=is_everyone, before=small_before,
                      after=small_after)


# ====================================================================== the port
class DiscordGuildPort:
    def __init__(self, client: discord.Client, guild_id: int, automod_rules: Callable[[], int] = lambda: 0):
        self.client, self.guild_id = client, guild_id
        self._automod_rules = automod_rules

    @property
    def guild(self) -> discord.Guild:
        guild = self.client.get_guild(self.guild_id)
        if guild is None:
            raise ActionFailed("Rudhabot can't see this server right now")
        return guild

    def model(self) -> GuildModel:
        return guild_model(self.guild, self._automod_rules())

    def member(self, user_id: int) -> Optional[MemberModel]:
        m = self.guild.get_member(user_id)
        return member_model(m) if m else None

    @property
    def owner_id(self) -> int:
        return self.guild.owner_id or 0

    def access(self, user_id: int):
        m = self.guild.get_member(user_id)
        if m is None:
            return None
        return tuple(r.id for r in m.roles), m.guild_permissions.value

    async def _member(self, user_id: int) -> discord.Member:
        m = self.guild.get_member(user_id)
        if m is None:
            try:
                m = await self.guild.fetch_member(user_id)
            except discord.HTTPException:
                raise ActionFailed("not in the server") from None
        return m

    def _channel(self, channel_id: int):
        channel = self.guild.get_channel(channel_id)
        if channel is None:
            raise ActionFailed("channel not found")
        return channel

    @staticmethod
    def _fail(error: Exception) -> ActionFailed:
        if isinstance(error, discord.Forbidden):
            return ActionFailed("Missing Permissions (check Rudhabot's role and channel permissions)")
        if isinstance(error, discord.NotFound):
            return ActionFailed("not found (already gone?)")
        if isinstance(error, discord.HTTPException):
            return ActionFailed(f"Discord error {error.status}")
        return ActionFailed(type(error).__name__)

    async def delete_messages(self, channel_id, message_ids, reason):
        channel = self._channel(channel_id)
        ids = list(dict.fromkeys(message_ids))
        deleted = 0
        try:
            for i in range(0, len(ids), 100):
                chunk = ids[i:i + 100]
                if len(chunk) == 1:
                    try:
                        await channel.get_partial_message(chunk[0]).delete()
                    except discord.NotFound:
                        pass
                else:
                    await channel.delete_messages([discord.Object(x) for x in chunk], reason=reason)
                deleted += len(chunk)
        except discord.HTTPException as error:
            if deleted == 0 and not isinstance(error, discord.Forbidden):
                for message_id in ids:            # older than 14 days or mixed: one by one
                    try:
                        await channel.get_partial_message(message_id).delete()
                        deleted += 1
                    except discord.NotFound:
                        deleted += 1
                    except discord.HTTPException as e:
                        raise self._fail(e) from None
            else:
                raise self._fail(error) from None
        return deleted

    async def timeout(self, user_id, seconds, reason):
        member = await self._member(user_id)
        try:
            await member.timeout(timedelta(seconds=seconds), reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def remove_timeout(self, user_id, reason):
        member = await self._member(user_id)
        try:
            await member.timeout(None, reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def kick(self, user_id, reason):
        try:
            await self.guild.kick(discord.Object(user_id), reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def ban(self, user_ids, reason, delete_seconds):
        delete_seconds = max(0, min(604800, int(delete_seconds)))
        try:
            if len(user_ids) == 1:
                await self.guild.ban(discord.Object(user_ids[0]), reason=reason, delete_message_seconds=delete_seconds)
                return list(user_ids), []
            result = await self.guild.bulk_ban([discord.Object(u) for u in user_ids], reason=reason,
                                               delete_message_seconds=delete_seconds)
            return [o.id for o in result.banned], [o.id for o in result.failed]
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def unban(self, user_id, reason):
        try:
            await self.guild.unban(discord.Object(user_id), reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def add_role(self, user_id, role_id, reason):
        member = await self._member(user_id)
        try:
            await member.add_roles(discord.Object(role_id), reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def remove_roles(self, user_id, role_ids, reason):
        member = await self._member(user_id)
        try:
            await member.remove_roles(*[discord.Object(r) for r in role_ids], reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def set_slowmode(self, channel_id, seconds, reason):
        channel = self._channel(channel_id)
        previous = int(getattr(channel, "slowmode_delay", 0) or 0)
        try:
            await channel.edit(slowmode_delay=int(seconds), reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None
        return previous

    async def set_overwrite(self, channel_id, target_id, target_type, allow, deny, reason):
        channel = self._channel(channel_id)
        target = self.guild.get_role(target_id) if target_type == "role" else self.guild.get_member(target_id)
        if target is None:
            raise ActionFailed("role or member not found")
        overwrite = None if not allow and not deny else discord.PermissionOverwrite.from_pair(
            discord.Permissions(allow), discord.Permissions(deny))
        try:
            await channel.set_permissions(target, overwrite=overwrite, reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def edit_role_permissions(self, role_id, permissions, reason):
        role = self.guild.get_role(role_id)
        if role is None:
            raise ActionFailed("role not found")
        try:
            await role.edit(permissions=discord.Permissions(permissions), reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def delete_webhook(self, webhook_id, reason):
        try:
            hook = await self.client.fetch_webhook(webhook_id)
            await hook.delete(reason=reason)
        except discord.NotFound:
            return
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def pause(self, invites, dms, minutes, reason):
        until = discord.utils.utcnow() + timedelta(minutes=min(1440, minutes))
        kwargs = {}
        if invites:
            kwargs["invites_disabled_until"] = until
        if dms:
            kwargs["dms_disabled_until"] = until
        try:
            await self.guild.edit(reason=reason, **kwargs)
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def create_role(self, data, reason):
        try:
            role = await self.guild.create_role(name=data["name"], permissions=discord.Permissions(int(data["permissions"])),
                                                colour=int(data.get("color", 0)), hoist=bool(data.get("hoist")),
                                                mentionable=bool(data.get("mentionable")), reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None
        try:
            if int(data.get("position", 1)) > 1:
                await role.edit(position=int(data["position"]), reason=reason)
        except discord.HTTPException:
            log.warning("recreated role %s but could not move it to position %s", role.id, data.get("position"))
        return role.id

    async def create_channel(self, data, category_id, overwrites, reason):
        guild = self.guild
        mapped = {}
        for ow in overwrites:
            target = guild.get_role(ow.target_id) if ow.target_type == "role" else guild.get_member(ow.target_id)
            if target is not None:
                mapped[target] = discord.PermissionOverwrite.from_pair(discord.Permissions(ow.allow),
                                                                       discord.Permissions(ow.deny))
        category = guild.get_channel(category_id) if category_id else None
        kind = data["type"]
        try:
            if kind == "category":
                channel = await guild.create_category(data["name"], overwrites=mapped, reason=reason)
            elif kind in ("voice", "stage"):
                maker = guild.create_voice_channel if kind == "voice" else guild.create_stage_channel
                channel = await maker(data["name"], category=category, overwrites=mapped, reason=reason)
            elif kind == "forum":
                channel = await guild.create_forum(data["name"], category=category, overwrites=mapped, reason=reason,
                                                   topic=data.get("topic") or "")
            else:
                channel = await guild.create_text_channel(data["name"], category=category, overwrites=mapped,
                                                          reason=reason, news=kind == "news",
                                                          topic=data.get("topic") or "",
                                                          slowmode_delay=int(data.get("slowmode", 0)),
                                                          nsfw=bool(data.get("nsfw")))
        except discord.HTTPException as error:
            raise self._fail(error) from None
        return channel.id

    async def edit_setting(self, setting, value, reason):
        kwargs = {"verification_level": lambda v: {"verification_level": discord.VerificationLevel(v)},
                  "explicit_content_filter": lambda v: {"explicit_content_filter": discord.ContentFilter(v)},
                  "mfa_level": lambda v: {"mfa_level": discord.MFALevel(v)}}.get(setting)
        if kwargs is None:
            raise ActionFailed(f"unknown setting {setting}")
        try:
            await self.guild.edit(reason=reason, **kwargs(value))
        except discord.HTTPException as error:
            raise self._fail(error) from None

    async def purge(self, channel_id, limit, user_id, reason):
        channel = self._channel(channel_id)
        check = (lambda m: m.author.id == user_id) if user_id else (lambda m: True)
        try:
            gone = await channel.purge(limit=limit, check=check, reason=reason)
        except discord.HTTPException as error:
            raise self._fail(error) from None
        return len(gone)
