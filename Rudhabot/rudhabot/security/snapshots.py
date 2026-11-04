"""Server snapshots: roles, channels, categories and their permission overwrites, and a diff-preview restore.

A snapshot is taken every 6 hours, before every lockdown and on demand. Restoring compares the snapshot with the
server now and plans the smallest set of steps: recreate deleted roles (permissions, colour, hoist, mentionable;
position best-effort, never above Rudhabot's own role), recreate deleted categories and channels (with their topic,
slowmode, NSFW flag and overwrites, re-pointed at recreated roles), and put back changed role permissions and changed
overwrites. It never deletes anything that was added since. What can't come back is stated plainly: message history,
pins, webhooks, invites and integrations inside deleted channels, and who held a deleted role (members are listed in
the report only if the snapshot recorded them).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rudhabot.security.model import ChannelModel, GuildModel, Overwrite, RoleModel

SNAPSHOT_VERSION = 1


def capture(guild: GuildModel) -> dict:
    return {"version": SNAPSHOT_VERSION, "guild_id": guild.id, "name": guild.name,
            "roles": [r.to_dict() for r in guild.roles if not r.managed],
            "channels": [c.to_dict() for c in guild.channels],
            "settings": {"verification_level": guild.verification_level, "mfa_level": guild.mfa_level,
                         "explicit_content_filter": guild.explicit_content_filter,
                         "default_notifications": guild.default_notifications}}


@dataclass
class Step:
    kind: str                     # create_role | create_channel | edit_role | set_overwrite | edit_setting
    description: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "description": self.description, "data": self.data}


@dataclass
class RestorePlan:
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.steps


def diff(snapshot: dict, guild: GuildModel, bot_top_position: Optional[int] = None) -> RestorePlan:
    plan = RestorePlan()
    top = guild.bot_top_position if bot_top_position is None else bot_top_position
    old_roles = {int(r["id"]): RoleModel.from_dict(r) for r in snapshot.get("roles", ())}
    old_channels = {int(c["id"]): ChannelModel.from_dict(c) for c in snapshot.get("channels", ())}
    roles_now = {r.id: r for r in guild.roles}
    channels_now = {c.id: c for c in guild.channels}

    # roles: recreate deleted, revert permission changes (never touching roles at or above Rudhabot's)
    for role_id, old in sorted(old_roles.items(), key=lambda kv: kv[1].position):
        if role_id == guild.id:
            now = roles_now.get(role_id)
            if now and now.permissions != old.permissions:
                plan.steps.append(Step("edit_role", f"@everyone permissions back to the snapshot",
                                       {"role_id": role_id, "permissions": old.permissions}))
            continue
        now = roles_now.get(role_id)
        if now is None:
            plan.steps.append(Step("create_role", f"recreate role '{old.name}'",
                                   {"old_id": role_id, **old.to_dict(), "position": min(old.position, max(1, top - 1))}))
        elif now.permissions != old.permissions:
            if now.position >= top:
                plan.notes.append(f"role '{now.name}' changed but sits above Rudhabot's role; fix its permissions by hand")
            else:
                plan.steps.append(Step("edit_role", f"role '{now.name}' permissions back to the snapshot",
                                       {"role_id": role_id, "permissions": old.permissions}))
    # channels: categories first so recreated channels can go back inside them
    for old in sorted(old_channels.values(), key=lambda c: (not c.is_category, c.position)):
        now = channels_now.get(old.id)
        if now is None:
            plan.steps.append(Step("create_channel", f"recreate {'category' if old.is_category else 'channel'} "
                                   f"'{old.name}' (empty: its messages can't be restored)",
                                   {"old_id": old.id, **old.to_dict()}))
            continue
        current = {o.target_id: o for o in now.overwrites}
        for ow in old.overwrites:
            mine = current.get(ow.target_id)
            if mine is None or (mine.allow, mine.deny) != (ow.allow, ow.deny):
                plan.steps.append(Step("set_overwrite", f"#{now.name}: permissions for "
                                       f"{'@everyone' if ow.target_id == guild.id else ow.target_type + ' ' + str(ow.target_id)} back to the snapshot",
                                       {"channel_id": now.id, **ow.to_dict()}))
        for target_id, mine in current.items():
            if target_id not in {o.target_id for o in old.overwrites}:
                plan.notes.append(f"#{now.name}: an overwrite was added since the snapshot (left in place)")
    s = snapshot.get("settings", {})
    for key, label in (("verification_level", "verification level"), ("mfa_level", "2FA for moderation"),
                       ("explicit_content_filter", "explicit media filter")):
        old_value = s.get(key)
        if old_value is not None and getattr(guild, key) < old_value:
            plan.steps.append(Step("edit_setting", f"{label} back to {old_value}", {"setting": key, "value": old_value}))
    if any(st.kind == "create_channel" for st in plan.steps):
        plan.notes.append("Recreated channels start empty: message history, pins, webhooks and invites can't be restored.")
    if any(st.kind == "create_role" for st in plan.steps):
        plan.notes.append("Recreated roles start with no members: Discord doesn't let a bot see who held a deleted role.")
    return plan


def remap_overwrites(overwrites: list[dict], role_map: dict[int, int]) -> list[Overwrite]:
    """Point overwrites of recreated channels at the recreated roles' new ids."""
    out = []
    for o in overwrites:
        ow = Overwrite.from_dict(o)
        new_id = role_map.get(ow.target_id, ow.target_id) if ow.target_type == "role" else ow.target_id
        out.append(Overwrite(new_id, ow.target_type, ow.allow, ow.deny))
    return out
