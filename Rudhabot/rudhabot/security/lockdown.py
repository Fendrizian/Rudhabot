"""Scoped lockdowns: plan exactly which permission overwrites change, keep what they were, and restore them safely.

A lockdown denies ordinary members the right to speak (send messages, post in threads, create threads, react, polls)
in the channels in scope, by setting deny bits on @everyone's overwrite, and by neutralising any non-staff role
overwrite that explicitly *allows* speaking there (otherwise that role would bypass the lock). Staff roles (tier >= 1),
Rudhabot's own role and protected channels are left alone.

Restoring compares each overwrite with what the lockdown set: if it is unchanged, the saved original is put back; if
someone changed it meanwhile, it is left as they made it and listed in the "fix manually" report, so the bot never
silently overwrites a moderator's later decision. Everything that cannot be done is reported too: channels the bot
can't manage and member-specific overwrites that allow speaking. (Members with Administrator ignore overwrites; they
are staff by definition here.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from rudhabot.security import perms as P
from rudhabot.security.model import ChannelModel, GuildModel


@dataclass
class OverwriteChange:
    channel_id: int
    target_id: int
    target_type: str
    before: Optional[tuple[int, int]]        # (allow, deny), None = there was no overwrite
    after: tuple[int, int]

    def to_dict(self) -> dict:
        return {"channel_id": self.channel_id, "target_id": self.target_id, "target_type": self.target_type,
                "before": list(self.before) if self.before else None, "after": list(self.after)}

    @classmethod
    def from_dict(cls, d: dict) -> "OverwriteChange":
        return cls(int(d["channel_id"]), int(d["target_id"]), d["target_type"],
                   tuple(d["before"]) if d.get("before") else None, tuple(d["after"]))


@dataclass
class LockdownPlan:
    scope: str                               # server | channels | category
    channel_ids: list[int]
    changes: list[OverwriteChange] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def preview(self, guild: GuildModel, limit: int = 15) -> list[str]:
        lines = []
        for c in self.changes[:limit]:
            channel = guild.channel(c.channel_id)
            who = "@everyone" if c.target_id == guild.id else f"<@&{c.target_id}>"
            lines.append(f"#{channel.name if channel else c.channel_id}: {who} can no longer speak")
        if len(self.changes) > limit:
            lines.append(f"... and {len(self.changes) - limit} more change(s)")
        return lines


def _staff_roles(guild: GuildModel, role_tiers: dict, staff_role_ids: Iterable[int]) -> set[int]:
    staff = {int(r) for r, t in role_tiers.items() if int(t) >= 1} | set(staff_role_ids)
    for role in guild.roles:
        if role.permissions & (P.ADMINISTRATOR | P.MANAGE_MESSAGES | P.MODERATE_MEMBERS | P.KICK_MEMBERS
                               | P.BAN_MEMBERS | P.MANAGE_CHANNELS):
            staff.add(role.id)
        if role.managed and role.bot_id == guild.bot_id:
            staff.add(role.id)
    return staff


def scope_channels(guild: GuildModel, scope: str, channel_ids: Iterable[int] = (),
                   protected: Iterable[int] = ()) -> list[ChannelModel]:
    protected = set(protected)
    if scope == "server":
        chosen = [c for c in guild.channels if c.is_messageable]
    else:
        wanted = set(channel_ids)
        chosen = []
        for c in guild.channels:
            if c.id in wanted and c.is_category:
                chosen.extend(x for x in guild.channels_in(c.id) if x.is_messageable)
            elif c.id in wanted and c.is_messageable:
                chosen.append(c)
    seen, out = set(), []
    for c in chosen:
        if c.id not in seen and c.id not in protected:
            seen.add(c.id)
            out.append(c)
    return out


def plan_lockdown(guild: GuildModel, scope: str, channel_ids: Iterable[int] = (), *, role_tiers: dict = None,
                  staff_role_ids: Iterable[int] = (), protected: Iterable[int] = (),
                  locked_elsewhere: Iterable[int] = (), manageable: Optional[set[int]] = None) -> LockdownPlan:
    """`locked_elsewhere`: channels already in an active lockdown (left out: no overlapping changes).
    `manageable`: channels the bot can edit overwrites in (None = all)."""
    staff = _staff_roles(guild, role_tiers or {}, staff_role_ids)
    busy = set(locked_elsewhere)
    channels = scope_channels(guild, scope, channel_ids, protected)
    plan = LockdownPlan(scope, [c.id for c in channels])
    for channel in channels:
        if channel.id in busy:
            plan.skipped.append(f"#{channel.name}: already in an active lockdown")
            continue
        if manageable is not None and channel.id not in manageable:
            plan.manual.append(f"#{channel.name}: Rudhabot can't edit this channel's permissions; lock it by hand")
            continue
        everyone = channel.overwrite_for(guild.id)
        before = (everyone.allow, everyone.deny) if everyone else None
        allow, deny = before or (0, 0)
        after = (allow & ~P.SPEAK, deny | P.SPEAK)
        if after != (allow, deny):
            plan.changes.append(OverwriteChange(channel.id, guild.id, "role", before, after))
        for ow in channel.overwrites:
            if ow.target_id == guild.id:
                continue
            if ow.target_type == "member":
                if ow.allow & P.SEND_MESSAGES:
                    plan.manual.append(f"#{channel.name}: a member-specific overwrite still lets <@{ow.target_id}> speak")
                continue
            if ow.target_id in staff:
                continue
            if ow.allow & P.SPEAK:
                plan.changes.append(OverwriteChange(channel.id, ow.target_id, "role", (ow.allow, ow.deny),
                                                    (ow.allow & ~P.SPEAK, ow.deny)))
    return plan


def plan_restore(guild: GuildModel, changes: list[OverwriteChange]) -> tuple[list[OverwriteChange], list[str]]:
    """What to put back, and what to leave for staff. A change is restored only if the overwrite still is exactly
    what the lockdown set."""
    restore, manual = [], []
    for c in changes:
        channel = guild.channel(c.channel_id)
        if channel is None:
            manual.append(f"channel {c.channel_id} no longer exists; nothing to restore there")
            continue
        current = channel.overwrite_for(c.target_id)
        now = (current.allow, current.deny) if current else (0, 0)
        if now == (tuple(c.before) if c.before else (0, 0)):
            continue                       # never applied (e.g. the bot restarted part-way through): nothing to undo
        if now != tuple(c.after):
            who = "@everyone" if c.target_id == guild.id else f"role {c.target_id}"
            manual.append(f"#{channel.name}: {who}'s permissions were changed during the lockdown; left as they are")
            continue
        restore.append(c)
    return restore, manual


def apply_change(guild: GuildModel, change: OverwriteChange, restoring: bool = False) -> GuildModel:
    """Apply one change to a model (used by the simulator's fake guild and by tests)."""
    channel = guild.channel(change.channel_id)
    if channel is None:
        return guild
    allow, deny = (change.before or (0, 0)) if restoring else change.after
    return guild.replace_channel(channel.with_overwrite(change.target_id, change.target_type, allow, deny))
