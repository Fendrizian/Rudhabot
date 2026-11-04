"""An in-memory server implementing GuildPort: used by the tests, by `/security drill` and by the wargame simulator.

Nothing here touches Discord. It applies changes to a GuildModel, keeps members/messages/bans, records every call,
and can be told to refuse specific operations (to test partial failures and lost permissions).
"""
from __future__ import annotations

import itertools
from dataclasses import replace
from typing import Optional

from rudhabot.security import perms as P
from rudhabot.security.actions import ActionFailed
from rudhabot.security.model import ChannelModel, GuildModel, MemberModel, Overwrite, RoleModel


class FakeGuild:
    _ids = itertools.count(9_000_000)

    def __init__(self, model: GuildModel):
        self.guild_id = model.id
        self._model = model
        self.members: dict[int, MemberModel] = {m.id: m for m in model.members}
        self.messages: dict[int, set[int]] = {}           # channel -> live message ids
        self.banned: set[int] = set()
        self.webhooks: set[int] = set()
        self.timeouts: dict[int, int] = {}
        self.paused: dict[str, int] = {}
        self.calls: list[tuple] = []
        self.refuse: set[str] = set()                     # operation names that fail with "Missing Permissions"
        self.refuse_targets: set[int] = set()

    # ------------------------------------------------------------------ helpers
    def model(self) -> GuildModel:
        return replace(self._model, members=tuple(self.members.values()))

    def member(self, user_id: int) -> Optional[MemberModel]:
        return self.members.get(user_id)

    @property
    def owner_id(self) -> int:
        return self._model.owner_id

    def access(self, user_id: int):
        m = self.members.get(user_id)
        if m is None:
            return None
        return m.roles, self.model().base_permissions(user_id)

    def _guard(self, op: str, target: Optional[int] = None) -> None:
        self.calls.append((op, target))
        if op in self.refuse or (target is not None and target in self.refuse_targets):
            raise ActionFailed("Missing Permissions")

    def add_member(self, member: MemberModel) -> None:
        self.members[member.id] = member

    def post(self, channel_id: int, message_id: int) -> None:
        self.messages.setdefault(channel_id, set()).add(message_id)

    # ------------------------------------------------------------------ GuildPort
    async def delete_messages(self, channel_id, message_ids, reason):
        self._guard("delete_messages", channel_id)
        live = self.messages.setdefault(channel_id, set())
        gone = [m for m in message_ids if m in live]
        live.difference_update(gone)
        return len(gone)

    async def timeout(self, user_id, seconds, reason):
        self._guard("timeout", user_id)
        if user_id not in self.members:
            raise ActionFailed("Unknown Member")
        self.timeouts[user_id] = seconds

    async def remove_timeout(self, user_id, reason):
        self._guard("remove_timeout", user_id)
        self.timeouts.pop(user_id, None)

    async def kick(self, user_id, reason):
        self._guard("kick", user_id)
        if self.members.pop(user_id, None) is None:
            raise ActionFailed("Unknown Member")

    async def ban(self, user_ids, reason, delete_seconds):
        self._guard("ban")
        done, refused = [], []
        for u in user_ids:
            if u in self.refuse_targets:
                refused.append(u)
            else:
                self.banned.add(u)
                self.members.pop(u, None)
                done.append(u)
        return done, refused

    async def unban(self, user_id, reason):
        self._guard("unban", user_id)
        if user_id not in self.banned:
            raise ActionFailed("Unknown Ban")
        self.banned.discard(user_id)

    async def add_role(self, user_id, role_id, reason):
        self._guard("add_role", user_id)
        m = self.members.get(user_id)
        if m is None:
            raise ActionFailed("Unknown Member")
        if role_id not in m.roles:
            self.members[user_id] = replace(m, roles=m.roles + (role_id,))

    async def remove_roles(self, user_id, role_ids, reason):
        self._guard("remove_roles", user_id)
        m = self.members.get(user_id)
        if m is None:
            raise ActionFailed("Unknown Member")
        self.members[user_id] = replace(m, roles=tuple(r for r in m.roles if r not in set(role_ids)))

    async def move_role(self, role_id, position, reason):
        """Discord's semantics: the role takes `position` and the roles it passes shift by one to make room."""
        self._guard("move_role", role_id)
        role = self._model.role(role_id)
        if role is None:
            raise ActionFailed("Unknown Role")
        old, new = role.position, int(position)
        roles = []
        for r in self._model.roles:
            if r.id == role_id:
                roles.append(replace(r, position=new))
            elif old < new and old < r.position <= new:
                roles.append(replace(r, position=r.position - 1))
            elif new < old and new <= r.position < old:
                roles.append(replace(r, position=r.position + 1))
            else:
                roles.append(r)
        self._model = replace(self._model, roles=tuple(roles))

    async def set_slowmode(self, channel_id, seconds, reason):
        self._guard("set_slowmode", channel_id)
        ch = self._model.channel(channel_id)
        if ch is None:
            raise ActionFailed("Unknown Channel")
        self._model = self._model.replace_channel(replace(ch, slowmode=seconds))
        return ch.slowmode

    async def set_overwrite(self, channel_id, target_id, target_type, allow, deny, reason):
        self._guard("set_overwrite", channel_id)
        ch = self._model.channel(channel_id)
        if ch is None:
            raise ActionFailed("Unknown Channel")
        self._model = self._model.replace_channel(ch.with_overwrite(target_id, target_type, allow, deny))

    async def edit_role_permissions(self, role_id, permissions, reason):
        self._guard("edit_role_permissions", role_id)
        role = self._model.role(role_id)
        if role is None:
            raise ActionFailed("Unknown Role")
        self._model = self._model.replace_role(replace(role, permissions=permissions))

    async def delete_webhook(self, webhook_id, reason):
        self._guard("delete_webhook", webhook_id)
        self.webhooks.discard(webhook_id)

    async def pause(self, invites, dms, minutes, reason):
        self._guard("pause")
        if invites:
            self.paused["invites"] = minutes
        if dms:
            self.paused["dms"] = minutes

    async def create_role(self, data, reason):
        self._guard("create_role")
        new_id = next(self._ids)
        role = RoleModel(new_id, data["name"], int(data.get("position", 1)), int(data.get("permissions", 0)),
                         color=int(data.get("color", 0)), hoist=bool(data.get("hoist")),
                         mentionable=bool(data.get("mentionable")))
        self._model = replace(self._model, roles=self._model.roles + (role,))
        return new_id

    async def create_channel(self, data, category_id, overwrites, reason):
        self._guard("create_channel")
        new_id = next(self._ids)
        channel = ChannelModel(new_id, data["name"], data["type"], int(data.get("position", 0)), category_id,
                               data.get("topic"), bool(data.get("nsfw")), int(data.get("slowmode", 0)),
                               tuple(o if isinstance(o, Overwrite) else Overwrite.from_dict(o) for o in overwrites))
        self._model = replace(self._model, channels=self._model.channels + (channel,))
        return new_id

    async def edit_setting(self, setting, value, reason):
        self._guard("edit_setting")
        self._model = replace(self._model, **{setting: value})

    async def purge(self, channel_id, limit, user_id, reason):
        self._guard("purge", channel_id)
        live = self.messages.setdefault(channel_id, set())
        gone = sorted(live)[-limit:]
        live.difference_update(gone)
        return len(gone)

    # ------------------------------------------------------------------ simulated damage (for drills/tests)
    def delete_channel(self, channel_id: int) -> None:
        self._model = replace(self._model, channels=tuple(c for c in self._model.channels if c.id != channel_id))

    def delete_role(self, role_id: int) -> None:
        self._model = replace(self._model, roles=tuple(r for r in self._model.roles if r.id != role_id))


def demo_guild(guild_id: int = 1, *, owner: int = 10, bot: int = 99, members: int = 1200) -> FakeGuild:
    """A small but realistic server for drills and tests: staff tiers, a bot role on top, public and staff channels."""
    roles = (
        RoleModel(guild_id, "@everyone", 0, P.VIEW_CHANNEL | P.SEND_MESSAGES | P.ADD_REACTIONS | P.CREATE_INSTANT_INVITE
                  | P.EMBED_LINKS | P.ATTACH_FILES | P.SEND_MESSAGES_IN_THREADS | P.CREATE_PUBLIC_THREADS,
                  member_count=members),
        RoleModel(2, "Citizen", 1, 0, member_count=members - 50),
        RoleModel(3, "Helper", 3, P.MANAGE_MESSAGES, member_count=6),
        RoleModel(4, "Moderator", 4, P.MANAGE_MESSAGES | P.KICK_MEMBERS | P.MODERATE_MEMBERS, member_count=5),
        RoleModel(5, "Senior Moderator", 5, P.MANAGE_MESSAGES | P.KICK_MEMBERS | P.MODERATE_MEMBERS | P.BAN_MEMBERS,
                  member_count=3),
        RoleModel(6, "Admin", 6, P.ADMINISTRATOR, member_count=2),
        RoleModel(7, "Probation", 2, 0),
        RoleModel(8, "Rudhabot", 10, P.ADMINISTRATOR, managed=True, bot_id=bot, member_count=1),
        RoleModel(9, "OtherBot", 7, P.MANAGE_MESSAGES | P.MANAGE_CHANNELS, managed=True, bot_id=98, member_count=1),
    )
    staff_room = (Overwrite(guild_id, "role", 0, P.VIEW_CHANNEL), Overwrite(3, "role", P.VIEW_CHANNEL, 0))
    channels = (
        ChannelModel(100, "Public", "category", 0),
        ChannelModel(101, "general", "text", 1, 100),
        ChannelModel(102, "roleplay-battles", "text", 2, 100),
        ChannelModel(103, "trading", "text", 3, 100, overwrites=(Overwrite(2, "role", P.SEND_MESSAGES, 0),)),
        ChannelModel(104, "announcements", "news", 4, 100, overwrites=(Overwrite(guild_id, "role", 0, P.SEND_MESSAGES),)),
        ChannelModel(200, "Staff", "category", 5, overwrites=staff_room),
        ChannelModel(201, "security-alerts", "text", 6, 200, overwrites=staff_room),
        ChannelModel(202, "mod-log", "text", 7, 200, overwrites=staff_room),
    )
    staff = (
        MemberModel(owner, (2,)), MemberModel(bot, (8,), bot=True), MemberModel(98, (9,), bot=True),
        MemberModel(21, (2, 3)), MemberModel(22, (2, 4)), MemberModel(23, (2, 5)), MemberModel(24, (2, 6)),
        MemberModel(25, (2, 4)),
    )
    model = GuildModel(guild_id, "Demo Micronation", owner, members, mfa_level=1, verification_level=2,
                       explicit_content_filter=2, default_notifications=1, roles=roles, channels=channels,
                       members=staff, bot_id=bot, automod_rules=2)
    return FakeGuild(model)
