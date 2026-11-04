"""Normalised server events: what the detectors see, whether they come from Discord or from the wargame simulator.

Times are POSIX seconds (floats). Message text lives only in memory for the moment it is analysed; nothing here is
persisted as-is (incidents keep fingerprints, counts and, for flagged messages only, a snippet of at most 200 chars).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Attachment:
    filename: str
    size: int = 0
    content_type: Optional[str] = None


@dataclass(frozen=True)
class MemberJoin:
    guild_id: int
    user_id: int
    ts: float
    created_at: float                       # account creation (from the snowflake)
    bot: bool = False
    has_avatar: bool = True
    name: str = ""                          # used in memory for the name-shape feature only
    avatar_key: Optional[str] = None        # Discord's avatar hash: equal hashes = the same image
    flagged_spammer: bool = False           # Discord's own "likely spammer" account flag
    invite_code: Optional[str] = None


@dataclass(frozen=True)
class MemberLeave:
    guild_id: int
    user_id: int
    ts: float


@dataclass(frozen=True)
class Message:
    guild_id: int
    channel_id: int
    message_id: int
    author_id: int
    ts: float
    content: str = ""
    author_tier: int = 0                    # clearance tier at the time (0 = ordinary member)
    author_roles: tuple[int, ...] = ()
    author_bot: bool = False
    webhook_id: Optional[int] = None
    joined_at: Optional[float] = None       # when the author joined this server
    created_at: Optional[float] = None      # when the author's account was created
    user_mentions: int = 0                  # distinct members mentioned
    role_mentions: int = 0
    everyone_attempt: bool = False          # wrote @everyone/@here (whether or not it pinged)
    attachments: tuple[Attachment, ...] = ()
    embeds: int = 0
    stickers: int = 0
    is_reply: bool = False

    @property
    def account_age_days(self) -> Optional[float]:
        return None if self.created_at is None else max(0.0, (self.ts - self.created_at) / 86400)

    @property
    def member_age_days(self) -> Optional[float]:
        return None if self.joined_at is None else max(0.0, (self.ts - self.joined_at) / 86400)


@dataclass(frozen=True)
class AuditEntry:
    """One audit-log entry. `action` is discord.py's AuditLogAction name (channel_delete, ban, role_update, ...)."""
    guild_id: int
    action: str
    actor_id: int
    ts: float
    target_id: Optional[int] = None
    target_type: Optional[str] = None       # role, channel, member, webhook, guild, ...
    reason: Optional[str] = None
    actor_bot: bool = False
    actor_tier: int = 0
    actor_roles: tuple[int, ...] = ()
    # role_update / overwrite changes: permission bits before/after; member_role_update: roles added
    perms_before: Optional[int] = None
    perms_after: Optional[int] = None
    roles_added: tuple[int, ...] = ()
    roles_removed: tuple[int, ...] = ()
    target_is_everyone: bool = False
    before: dict = field(default_factory=dict)   # small scalar diffs (verification_level, mfa_level, ...)
    after: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Tick:
    """Periodic clock event: decays threat levels, expires probation/slowmode/lockdowns, closes quiet incidents."""
    guild_id: int
    ts: float


Event = MemberJoin | MemberLeave | Message | AuditEntry | Tick
