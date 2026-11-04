"""The action adapter: the only path from a plan (or a moderator's command) to a change on the server.

For every action it:
  1. checks the kind is allowed for its authority (automatic actions are limited to the low-impact allowlist, with
     bounded parameters: timeouts <= 10 minutes, slowmode <= 2 minutes, probation <= 7 days);
  2. re-checks authorisation *now*: the requesting moderator's current clearance against the action and the target
     (never the owner, never Rudhabot, never anyone at or above Rudhabot's role; automatic actions never touch staff,
     except anti-nuke quarantine of an account that went over its budget);
  3. in shadow mode, records what it would have done and stops;
  4. records "attempted", calls Discord (paced: at most a few calls per second, one bulk job at a time), and records
     "confirmed" only when Discord accepted the change, or "failed" with the reason. Nothing is retried blindly.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Protocol

from rudhabot.security import perms as P
from rudhabot.security.clearance import Refused, Tier, check
from rudhabot.security.model import GuildModel, MemberModel
from rudhabot.security.responses import AUTO_KINDS, MAX_AUTO_TIMEOUT_S, PlannedAction

MAX_TIMEOUT_S = 28 * 86400          # Discord's own limit
MAX_AUTO_SLOWMODE_S = 120
MAX_SLOWMODE_S = 21600
MAX_AUTO_PROBATION_MIN = 7 * 1440


class ActionFailed(Exception):
    """Discord refused or the target is gone. The message is safe to show staff."""


class GuildPort(Protocol):
    """What the executor may do on a server. `discord_port.DiscordGuildPort` implements it for real; `fake.FakeGuild`
    implements it in memory for tests, drills and the simulator."""
    guild_id: int
    owner_id: int

    def model(self) -> GuildModel: ...
    def access(self, user_id: int) -> Optional[tuple[tuple[int, ...], int]]: ...   # (role ids, server permissions)
    def member(self, user_id: int) -> Optional[MemberModel]: ...
    async def delete_messages(self, channel_id: int, message_ids: list[int], reason: str) -> int: ...
    async def timeout(self, user_id: int, seconds: int, reason: str) -> None: ...
    async def remove_timeout(self, user_id: int, reason: str) -> None: ...
    async def kick(self, user_id: int, reason: str) -> None: ...
    async def ban(self, user_ids: list[int], reason: str, delete_seconds: int) -> tuple[list[int], list[int]]: ...
    async def unban(self, user_id: int, reason: str) -> None: ...
    async def add_role(self, user_id: int, role_id: int, reason: str) -> None: ...
    async def remove_roles(self, user_id: int, role_ids: list[int], reason: str) -> None: ...
    async def set_slowmode(self, channel_id: int, seconds: int, reason: str) -> int: ...
    async def set_overwrite(self, channel_id: int, target_id: int, target_type: str, allow: int, deny: int,
                            reason: str) -> None: ...
    async def edit_role_permissions(self, role_id: int, permissions: int, reason: str) -> None: ...
    async def move_role(self, role_id: int, position: int, reason: str) -> None: ...
    async def delete_webhook(self, webhook_id: int, reason: str) -> None: ...
    async def pause(self, invites: bool, dms: bool, minutes: int, reason: str) -> None: ...
    async def create_role(self, data: dict, reason: str) -> int: ...
    async def create_channel(self, data: dict, category_id: Optional[int], overwrites: list, reason: str) -> int: ...
    async def edit_setting(self, setting: str, value: int, reason: str) -> None: ...
    async def purge(self, channel_id: int, limit: int, user_id: Optional[int], reason: str) -> int: ...


@dataclass
class ActionResult:
    status: str                         # confirmed | partial | failed | shadow | refused | skipped
    detail: str = ""
    done: list = field(default_factory=list)
    failed: list = field(default_factory=list)       # (target, reason)
    action_id: Optional[int] = None
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("confirmed", "partial")

    def to_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail[:500], "done": self.done[:200],
                "failed": [[str(t), str(r)[:200]] for t, r in self.failed[:50]], "data": self.data}


class Pacer:
    """At most `rate` Discord calls per second on average, bursts up to `burst`, so a big clean-up never trips
    Discord's global rate limit or starves the bot's other commands."""

    def __init__(self, rate: float = 4.0, burst: int = 8, clock=time.monotonic, sleep=asyncio.sleep):
        self.rate, self.burst, self._clock, self._sleep = rate, burst, clock, sleep
        self._tokens, self._last = float(burst), clock()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens < 1:
                await self._sleep((1 - self._tokens) / self.rate)
                self._tokens = 1.0
                self._last = self._clock()
            self._tokens -= 1


class Executor:
    def __init__(self, port: GuildPort, *, tier_of, record=None, pacer: Optional[Pacer] = None):
        """`tier_of(user_id) -> Tier`: current clearance. `record(plan, status, result, actor)` -> action id
        (persists the outcome; may be None in the simulator)."""
        self.port = port
        self.tier_of = tier_of
        self.record = record
        self.pacer = pacer or Pacer()
        self.bulk_lock = asyncio.Lock()
        self.stats = defaultdict(int)

    # ------------------------------------------------------------------ guards
    def _protected(self, guild: GuildModel, user_id: int) -> Optional[str]:
        if user_id == guild.owner_id:
            return "the server owner"
        if user_id == guild.bot_id:
            return "Rudhabot itself"
        if guild.top_position(user_id) >= guild.bot_top_position and guild.member(user_id) is not None:
            return "someone whose role is at or above Rudhabot's"
        return None

    def validate(self, plan: PlannedAction, actor_id: Optional[int]) -> None:
        """Raise Refused if this plan may not run as requested."""
        automatic = actor_id is None
        if automatic:
            if plan.authority != "auto" or plan.kind not in AUTO_KINDS:
                raise Refused(f"{plan.kind} can't run without a moderator")
            if plan.kind == "timeout" and int(plan.params.get("seconds", 0)) > MAX_AUTO_TIMEOUT_S:
                raise Refused("automatic timeouts are limited to 10 minutes")
            if plan.kind == "slowmode" and int(plan.params.get("seconds", 0)) > MAX_AUTO_SLOWMODE_S:
                raise Refused("automatic slowmode is limited to 2 minutes")
            if plan.kind == "probation" and int(plan.params.get("minutes", 0)) > MAX_AUTO_PROBATION_MIN:
                raise Refused("automatic probation is limited to 7 days")
        else:
            action = {"timeout": "timeout_short" if int(plan.params.get("seconds", 0)) <= 600 else "timeout",
                      "delete_messages": "purge_small", "slowmode": "slowmode", "probation": "probation",
                      "quarantine": "quarantine", "delete_webhook": "remove_webhook", "kick_bot": "kick",
                      "revert_permissions": "restore", "kick": "kick", "ban": "bulk_ban" if len(plan.targets) > 1 else "ban",
                      "pause_invites": "pause_invites", "pause_dms": "pause_invites", "lockdown": "lockdown_approve",
                      "restore": "restore", "unban": "unban", "untimeout": "untimeout"}.get(plan.kind)
            if action is None:
                raise Refused(f"unknown action {plan.kind}")
            check(int(self.tier_of(actor_id)), action)
        if plan.kind == "timeout" and not 0 < int(plan.params.get("seconds", 0)) <= MAX_TIMEOUT_S:
            raise Refused("a timeout must be between 1 second and 28 days")
        if plan.kind == "slowmode" and not 0 <= int(plan.params.get("seconds", 0)) <= MAX_SLOWMODE_S:
            raise Refused("slowmode must be between 0 and 6 hours")

    def _target_check(self, plan: PlannedAction, user_id: int, actor_id: Optional[int], guild: GuildModel) -> Optional[str]:
        """Why this user can't be targeted, or None."""
        protected = self._protected(guild, user_id)
        if protected:
            return protected
        target_tier = int(self.tier_of(user_id))
        if actor_id is None:
            if plan.kind in ("timeout", "probation") and target_tier >= Tier.HELPER:
                return "staff (automatic actions never restrict staff)"
            if plan.kind == "quarantine" and target_tier >= Tier.OWNER:
                return "the server owner"
        else:
            if actor_id == user_id:
                return "yourself"
            actor_tier = int(self.tier_of(actor_id))
            if target_tier >= actor_tier and actor_tier < Tier.OWNER:
                return f"someone with {Tier(target_tier).label} clearance (needs higher rank)"
        return None

    # ------------------------------------------------------------------ run
    async def execute(self, plan: PlannedAction, *, actor_id: Optional[int] = None, shadow: bool = False,
                      context: Optional[dict] = None) -> ActionResult:
        context = context or {}
        try:
            self.validate(plan, actor_id)
        except Refused as refusal:
            result = ActionResult("refused", str(refusal))
            self._log(plan, result, actor_id)
            return result
        if shadow and actor_id is None:
            result = ActionResult("shadow", "shadow mode: recorded, not carried out")
            self._log(plan, result, actor_id)
            self.stats["shadow"] += 1
            return result
        attempt_id = self._log(plan, ActionResult("attempted"), actor_id)
        try:
            result = await self._run(plan, actor_id, context)
        except Exception as error:                       # report, never crash the pipeline
            result = ActionResult("failed", f"{type(error).__name__}: {error}"[:300])
        result.action_id = attempt_id
        self.stats[result.status] += 1
        if self.record is not None and attempt_id is not None:
            self.record(plan, result.status, result, actor_id, attempt_id)
        return result

    def _log(self, plan, result, actor_id) -> Optional[int]:
        if self.record is None:
            return None
        return self.record(plan, result.status, result, actor_id, None)

    async def _each(self, plan, users, actor_id, fn) -> ActionResult:
        guild = self.port.model()
        done, failed = [], []
        for user_id in users:
            why = self._target_check(plan, user_id, actor_id, guild)
            if why:
                failed.append((user_id, f"not allowed: {why}"))
                continue
            await self.pacer.wait()
            try:
                await fn(user_id)
                done.append(user_id)
            except ActionFailed as error:
                failed.append((user_id, str(error)))
        status = "confirmed" if done and not failed else "partial" if done else "failed"
        return ActionResult(status, f"{len(done)} done, {len(failed)} not done", done, failed)

    async def _run(self, plan: PlannedAction, actor_id: Optional[int], ctx: dict) -> ActionResult:
        reason = _reason(plan, actor_id)
        port = self.port
        kind = plan.kind
        if kind == "delete_messages":
            by_channel: dict[int, list[int]] = defaultdict(list)
            for channel_id, message_id in plan.params.get("messages", []):
                by_channel[int(channel_id)].append(int(message_id))
            deleted, failed = 0, []
            for channel_id, ids in by_channel.items():
                await self.pacer.wait()
                try:
                    deleted += await port.delete_messages(channel_id, ids, reason)
                except ActionFailed as error:
                    failed.append((channel_id, str(error)))
            status = "confirmed" if deleted and not failed else "partial" if deleted else "failed"
            return ActionResult(status, f"{deleted} message(s) deleted", [deleted], failed)
        if kind == "timeout":
            seconds = int(plan.params["seconds"])
            return await self._each(plan, plan.targets, actor_id, lambda u: port.timeout(u, seconds, reason))
        if kind == "untimeout":
            return await self._each(plan, plan.targets, actor_id, lambda u: port.remove_timeout(u, reason))
        if kind == "probation":
            role = int(ctx.get("probation_role") or 0)
            if not role:
                return ActionResult("failed", "no probation role is configured (/security setup)")
            return await self._each(plan, plan.targets, actor_id, lambda u: port.add_role(u, role, reason))
        if kind == "kick":
            return await self._each(plan, plan.targets, actor_id, lambda u: port.kick(u, reason))
        if kind == "kick_bot":
            async def kick_bot(u):
                member = port.member(u)
                if member is None or not member.bot:
                    raise ActionFailed("not a bot (or already gone)")
                await port.kick(u, reason)
            return await self._each(plan, plan.targets, actor_id, kick_bot)
        if kind == "ban":
            guild = port.model()
            allowed, failed = [], []
            for user_id in plan.targets:
                why = self._target_check(plan, user_id, actor_id, guild)
                (failed.append((user_id, f"not allowed: {why}")) if why else allowed.append(user_id))
            if not allowed:
                return ActionResult("failed", "nobody could be banned", [], failed)
            async with self.bulk_lock:
                done = []
                for i in range(0, len(allowed), 200):            # Discord's bulk-ban limit per request
                    await self.pacer.wait()
                    banned, refused = await port.ban(allowed[i:i + 200], reason, int(plan.params.get("delete_seconds", 0)))
                    done += banned
                    failed += [(u, "Discord refused") for u in refused]
            status = "confirmed" if done and not failed else "partial" if done else "failed"
            return ActionResult(status, f"{len(done)} banned", done, failed)
        if kind == "unban":
            done, failed = [], []
            for user_id in plan.targets:
                await self.pacer.wait()
                try:
                    await port.unban(user_id, reason)
                    done.append(user_id)
                except ActionFailed as error:
                    failed.append((user_id, str(error)))
            return ActionResult("confirmed" if done and not failed else "partial" if done else "failed",
                                f"{len(done)} unbanned", done, failed)
        if kind == "slowmode":
            seconds = int(plan.params.get("seconds", 0))
            done, failed, previous = [], [], {}
            for channel_id in plan.targets:
                await self.pacer.wait()
                try:
                    previous[str(channel_id)] = await port.set_slowmode(channel_id, seconds, reason)
                    done.append(channel_id)
                except ActionFailed as error:
                    failed.append((channel_id, str(error)))
            return ActionResult("confirmed" if done and not failed else "partial" if done else "failed",
                                f"slowmode {seconds}s on {len(done)} channel(s)", done, failed, data={"previous": previous})
        if kind == "quarantine":
            return await self._quarantine(plan, actor_id, reason, ctx)
        if kind == "delete_webhook":
            done, failed = [], []
            for hook in plan.targets:
                await self.pacer.wait()
                try:
                    await port.delete_webhook(hook, reason)
                    done.append(hook)
                except ActionFailed as error:
                    failed.append((hook, str(error)))
            return ActionResult("confirmed" if done and not failed else "partial" if done else "failed",
                                f"{len(done)} webhook(s) removed", done, failed)
        if kind == "revert_permissions":
            guild = port.model()
            done, failed, before = [], [], {}
            for role_id in plan.targets:
                role = guild.role(role_id)
                if role is None:
                    failed.append((role_id, "role no longer exists"))
                    continue
                if role.position >= guild.bot_top_position and role.id != guild.id:
                    failed.append((role_id, "role is above Rudhabot's"))
                    continue
                await self.pacer.wait()
                try:
                    before[str(role_id)] = role.permissions
                    await port.edit_role_permissions(role_id, role.permissions & ~int(plan.params.get("remove", 0)), reason)
                    done.append(role_id)
                except ActionFailed as error:
                    failed.append((role_id, str(error)))
            return ActionResult("confirmed" if done and not failed else "partial" if done else "failed",
                                f"permissions reverted on {len(done)} role(s)", done, failed, data={"before": before})
        if kind in ("pause_invites", "pause_dms"):
            minutes = max(1, min(1440, int(plan.params.get("minutes", 60))))
            await self.pacer.wait()
            await port.pause(kind == "pause_invites", kind == "pause_dms", minutes, reason)
            return ActionResult("confirmed", f"{'invites' if kind == 'pause_invites' else 'DMs'} paused for {minutes} min",
                                data={"minutes": minutes})
        return ActionResult("refused", f"{kind} is carried out by its own procedure")

    async def _quarantine(self, plan: PlannedAction, actor_id: Optional[int], reason: str, ctx: dict) -> ActionResult:
        guild = self.port.model()
        done, failed, removed_by_user = [], [], {}
        for user_id in plan.targets:
            why = self._target_check(plan, user_id, actor_id, guild)
            if why:
                failed.append((user_id, f"not allowed: {why}"))
                continue
            member = self.port.member(user_id)
            if member is None:
                failed.append((user_id, "not in the server"))
                continue
            try:
                if member.bot:
                    await self.pacer.wait()
                    await self.port.kick(user_id, reason)            # a bot's own role can't be removed: remove the bot
                    removed_by_user[str(user_id)] = {"kicked_bot": True, "roles": []}
                else:
                    strip = [r.id for r in guild.roles if r.id in member.roles and not r.managed
                             and r.permissions & P.DANGEROUS and r.position < guild.bot_top_position]
                    if strip:
                        await self.pacer.wait()
                        await self.port.remove_roles(user_id, strip, reason)
                    if ctx.get("quarantine_role"):
                        await self.pacer.wait()
                        await self.port.add_role(user_id, int(ctx["quarantine_role"]), reason)
                    await self.pacer.wait()
                    try:
                        await self.port.timeout(user_id, MAX_AUTO_TIMEOUT_S, reason)
                    except ActionFailed:
                        pass                                         # stripping the roles is what matters
                    removed_by_user[str(user_id)] = {"kicked_bot": False, "roles": strip}
                done.append(user_id)
            except ActionFailed as error:
                failed.append((user_id, str(error)))
        status = "confirmed" if done and not failed else "partial" if done else "failed"
        return ActionResult(status, f"{len(done)} account(s) quarantined", done, failed, data={"removed": removed_by_user})


def _reason(plan: PlannedAction, actor_id: Optional[int]) -> str:
    """The audit-log reason Discord shows: who and why, never message content."""
    who = f"by moderator {actor_id}" if actor_id else "automatic"
    incident = f" [incident {plan.incident_id}]" if plan.incident_id else ""
    return f"Rudhabot ({who}): {plan.reason}"[:480] + incident
