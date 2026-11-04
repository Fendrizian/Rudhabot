"""Plain, immutable snapshots of a server's structure: roles, channels, permission overwrites and members.

The permission audit, snapshots, lockdown planning and the simulator all work on these, so none of them needs Discord.
`discord_port.guild_model()` builds one from a live `discord.Guild`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from rudhabot.security import perms as P


@dataclass(frozen=True)
class RoleModel:
    id: int
    name: str
    position: int
    permissions: int
    managed: bool = False          # owned by an integration (a bot's own role): can't be assigned or removed
    color: int = 0
    hoist: bool = False
    mentionable: bool = False
    member_count: int = 0
    bot_id: Optional[int] = None   # set on a bot's managed role

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "position": self.position, "permissions": self.permissions,
                "managed": self.managed, "color": self.color, "hoist": self.hoist, "mentionable": self.mentionable,
                "member_count": self.member_count, "bot_id": self.bot_id}

    @classmethod
    def from_dict(cls, d: dict) -> "RoleModel":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass(frozen=True)
class Overwrite:
    target_id: int
    target_type: str               # "role" | "member"
    allow: int = 0
    deny: int = 0

    def to_dict(self) -> dict:
        return {"target_id": self.target_id, "target_type": self.target_type, "allow": self.allow, "deny": self.deny}

    @classmethod
    def from_dict(cls, d: dict) -> "Overwrite":
        return cls(int(d["target_id"]), d["target_type"], int(d.get("allow", 0)), int(d.get("deny", 0)))


@dataclass(frozen=True)
class ChannelModel:
    id: int
    name: str
    type: str                      # text, news, forum, voice, stage, category
    position: int = 0
    category_id: Optional[int] = None
    topic: Optional[str] = None
    nsfw: bool = False
    slowmode: int = 0
    overwrites: tuple[Overwrite, ...] = ()

    @property
    def is_category(self) -> bool:
        return self.type == "category"

    @property
    def is_messageable(self) -> bool:
        return self.type in ("text", "news", "forum", "voice", "stage")

    def overwrite_for(self, target_id: int) -> Optional[Overwrite]:
        return next((o for o in self.overwrites if o.target_id == target_id), None)

    def with_overwrite(self, target_id: int, target_type: str, allow: int, deny: int) -> "ChannelModel":
        others = tuple(o for o in self.overwrites if o.target_id != target_id)
        if allow == 0 and deny == 0:
            return replace(self, overwrites=others)
        return replace(self, overwrites=others + (Overwrite(target_id, target_type, allow, deny),))

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "type": self.type, "position": self.position,
                "category_id": self.category_id, "topic": self.topic, "nsfw": self.nsfw, "slowmode": self.slowmode,
                "overwrites": [o.to_dict() for o in self.overwrites]}

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelModel":
        return cls(int(d["id"]), d["name"], d["type"], int(d.get("position", 0)), d.get("category_id"), d.get("topic"),
                   bool(d.get("nsfw")), int(d.get("slowmode", 0)),
                   tuple(Overwrite.from_dict(o) for o in d.get("overwrites", ())))


@dataclass(frozen=True)
class MemberModel:
    id: int
    roles: tuple[int, ...] = ()
    bot: bool = False
    joined_at: Optional[float] = None
    created_at: Optional[float] = None
    timed_out_until: Optional[float] = None


@dataclass(frozen=True)
class GuildModel:
    id: int
    name: str = "server"
    owner_id: int = 0
    member_count: int = 0
    mfa_level: int = 0                     # 1 = moderators need 2FA
    verification_level: int = 0            # 0 none .. 4 highest
    explicit_content_filter: int = 0       # 0 off, 1 members without roles, 2 everyone
    default_notifications: int = 0         # 0 all messages, 1 only mentions
    roles: tuple[RoleModel, ...] = ()
    channels: tuple[ChannelModel, ...] = ()
    members: tuple[MemberModel, ...] = ()  # usually just staff, bots and anyone holding a dangerous role
    bot_id: int = 0
    automod_rules: int = 0
    features: tuple[str, ...] = ()

    # ------------------------------------------------------------------ lookups
    @property
    def everyone(self) -> RoleModel:
        return next((r for r in self.roles if r.id == self.id), RoleModel(self.id, "@everyone", 0, 0))

    def role(self, role_id: int) -> Optional[RoleModel]:
        return next((r for r in self.roles if r.id == role_id), None)

    def channel(self, channel_id: int) -> Optional[ChannelModel]:
        return next((c for c in self.channels if c.id == channel_id), None)

    def member(self, user_id: int) -> Optional[MemberModel]:
        return next((m for m in self.members if m.id == user_id), None)

    def top_position(self, user_id: int) -> int:
        if user_id == self.owner_id:
            return 10 ** 9
        member = self.member(user_id)
        if member is None:
            return 0
        return max((r.position for r in self.roles if r.id in member.roles), default=0)

    @property
    def bot_top_position(self) -> int:
        return self.top_position(self.bot_id)

    def base_permissions(self, user_id: int) -> int:
        """Server-wide permissions (everyone + roles); the owner and administrators have everything."""
        if user_id == self.owner_id:
            return (1 << 53) - 1
        member = self.member(user_id)
        bits = self.everyone.permissions
        if member is not None:
            for role in self.roles:
                if role.id in member.roles:
                    bits |= role.permissions
        if bits & P.ADMINISTRATOR:
            return (1 << 53) - 1
        return bits

    def channels_in(self, category_id: int) -> list[ChannelModel]:
        return [c for c in self.channels if c.category_id == category_id]

    def replace_channel(self, channel: ChannelModel) -> "GuildModel":
        return replace(self, channels=tuple(channel if c.id == channel.id else c for c in self.channels))

    def replace_role(self, role: RoleModel) -> "GuildModel":
        return replace(self, roles=tuple(role if r.id == role.id else r for r in self.roles))
