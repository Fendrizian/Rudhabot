"""Moderation commands carried out through the security service: a mixin of SecurityService.

/warn, /timeout, /kick, /ban, /massban, /purge, /slowmode and releasing probation or quarantine. Each re-checks the
moderator's clearance, refuses targets of equal or higher rank, records a numbered case, counts high-impact actions
against the moderator's own anti-nuke budget, and reports exactly what Discord accepted.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from rudhabot.security.actions import ActionFailed, ActionResult
from rudhabot.security.clearance import Refused, Tier, check
from rudhabot.security.events import AuditEntry
from rudhabot.security.responses import PlannedAction

if TYPE_CHECKING:                          # annotations only: the service module imports this one
    from rudhabot.services.security import Runtime


log = logging.getLogger("rudhabot.security")
MASSBAN_UNCHECKED = 10           # more established accounts than this in one /massban need a second approval


class ModerationMixin:
    # ================================================================ moderation commands
    async def moderate(self, guild_id: int, actor_id: int, kind: str, targets: list[int], *, reason: str,
                       seconds: int = 0, delete_seconds: int = 0,
                       count_budget: bool = True) -> tuple[ActionResult, list[dict]]:
        rt = self.runtime(guild_id)
        if rt is None:
            raise Refused("Security isn't available in this server.")
        plan = PlannedAction(kind, "confirm", Tier.HELPER, reason or "no reason given", targets=list(targets),
                             params={"seconds": seconds, "delete_seconds": delete_seconds})
        if kind == "ban" and len(targets) > 1:
            self.require(guild_id, actor_id, "bulk_ban")
        result = await rt.executor.execute(plan, actor_id=actor_id, context=self._context(rt))
        if result.status == "refused":
            raise Refused(result.detail)
        cases = []
        for user_id in result.done if kind != "delete_messages" else []:
            case = self.store.add_case(guild_id, kind, user_id, actor_id, plan.reason, duration_s=seconds or None)
            cases.append(case)
            await self._notify("case", guild_id, {"case": case})
            if kind in ("ban", "kick") and count_budget:
                await self._count_as(rt, actor_id, kind, user_id)
        return result, cases

    async def _count_as(self, rt: "Runtime", actor_id: int, action: str, target_id: Optional[int]) -> None:
        """Rudhabot's own audit-log entries are exempt from anti-nuke, so a moderator's ban or kick through Rudhabot
        is counted as *their* action: a hijacked account can't use the bot to dodge its budget."""
        try:
            await self.handle(rt.guild_id, AuditEntry(rt.guild_id, action, actor_id, self.now(), target_id=target_id,
                                                      actor_tier=int(self.tier_of(rt.guild_id, actor_id))))
        except Exception:
            log.exception("could not count a moderation action against its moderator")

    def raid_accounts(self, guild_id: int) -> set[int]:
        """Accounts involved in an open raid/spam/phishing incident."""
        rt = self.runtime(guild_id)
        return {u for i in rt.engine.incidents.values() if i.family in ("raid", "spam", "phishing") for u in i.users}

    async def massban(self, guild_id: int, actor_id: int, user_ids: list[int], reason: str,
                      delete_seconds: int) -> dict:
        """Raid clean-up: raid accounts (in an open incident, or joined in the last day) are banned at once and don't
        count against anyone. Up to MASSBAN_UNCHECKED other accounts are banned too, counted against the moderator's
        anti-nuke budget; more than that need a second Senior Moderator to approve."""
        rt = self.runtime(guild_id)
        if rt is None:
            raise Refused("Security isn't available in this server.")
        self.require(guild_id, actor_id, "bulk_ban")
        raid = self.raid_accounts(guild_id)
        now = self.now()
        for uid in user_ids:
            member = rt.port.member(uid)
            if member is not None and member.joined_at and now - member.joined_at < 86400:
                raid.add(uid)
        fast = [u for u in user_ids if u in raid]
        others = [u for u in user_ids if u not in raid]
        out = {"banned": [], "failed": [], "cases": [], "awaiting": 0, "token": None}
        if fast:
            result, cases = await self.moderate(guild_id, actor_id, "ban", fast, reason=reason,
                                                delete_seconds=delete_seconds, count_budget=False)
            out["banned"] += result.done
            out["failed"] += result.failed
            out["cases"] += cases
        if others and len(others) <= MASSBAN_UNCHECKED:
            result, cases = await self.moderate(guild_id, actor_id, "ban", others, reason=reason,
                                                delete_seconds=delete_seconds)
            out["banned"] += result.done
            out["failed"] += result.failed
            out["cases"] += cases
        elif others:
            plan = PlannedAction("ban", "approve2", Tier.SENIOR,
                                 f"mass ban of {len(others)} established account(s): {reason}"[:300], targets=others,
                                 params={"delete_seconds": delete_seconds})
            out["token"] = await self.request_approval(rt, plan, requested_by=actor_id)
            out["awaiting"] = len(others)
        return out

    async def warn(self, guild_id: int, actor_id: int, target_id: int, reason: str) -> dict:
        tier = self.require(guild_id, actor_id, "warn")
        check(int(tier), "warn", target_tier=int(self.tier_of(guild_id, target_id)), actor_id=actor_id,
              target_id=target_id)
        case = self.store.add_case(guild_id, "warn", target_id, actor_id, reason)
        await self._notify("case", guild_id, {"case": case})
        return case

    async def purge(self, guild_id: int, actor_id: int, channel_id: int, count: int, user_id: Optional[int],
                    reason: str) -> int:
        rt = self.runtime(guild_id)
        self.require(guild_id, actor_id, "purge_small" if count <= 50 else "purge")
        count = max(1, min(500, int(count)))
        await rt.executor.pacer.wait()
        removed = await rt.port.purge(channel_id, count, user_id, f"Rudhabot purge by {actor_id}: {reason}"[:480])
        case = self.store.add_case(guild_id, "purge", user_id or 0, actor_id, reason,
                                   extra={"channel": channel_id, "removed": removed})
        await self._notify("case", guild_id, {"case": case})
        return removed

    async def slowmode(self, guild_id: int, actor_id: int, channel_id: int, seconds: int,
                       revert_minutes: int = 0) -> ActionResult:
        rt = self.runtime(guild_id)
        plan = PlannedAction("slowmode", "confirm", Tier.HELPER, "set by moderator", targets=[channel_id],
                             params={"seconds": seconds, "revert_after": revert_minutes * 60})
        result = await rt.executor.execute(plan, actor_id=actor_id)
        if result.status == "refused":
            raise Refused(result.detail)
        if result.ok and revert_minutes:
            previous = result.data.get("previous", {}).get(str(channel_id), 0)
            self.store.set_timer(guild_id, "slowmode", channel_id, self.now() + revert_minutes * 60,
                                 {"previous": previous, "set": int(seconds)})
        return result

    async def release_quarantine(self, guild_id: int, actor_id: int, user_id: int) -> str:
        rt = self.runtime(guild_id)
        self.require(guild_id, actor_id, "release_quarantine")
        entries = self.store.quarantined(guild_id, user_id)
        if not entries:
            raise Refused("That account isn't quarantined.")
        restored, failed = 0, []
        for q in entries:
            if q["roles"]:
                try:
                    for role_id in q["roles"]:
                        await rt.executor.pacer.wait()
                        await rt.port.add_role(user_id, role_id, f"Rudhabot: quarantine released by {actor_id}")
                        restored += 1
                except ActionFailed as error:
                    failed.append(str(error))
            if rt.config["quarantine_role"]:
                try:
                    await rt.port.remove_roles(user_id, [rt.config["quarantine_role"]], "quarantine released")
                except ActionFailed:
                    pass
            try:
                await rt.port.remove_timeout(user_id, "quarantine released")
            except ActionFailed:
                pass
            self.store.release_quarantine(q["id"], actor_id)
        rt.quarantined.discard(user_id)
        case = self.store.add_case(guild_id, "release", user_id, actor_id, "quarantine released",
                                   extra={"roles_restored": restored})
        await self._notify("case", guild_id, {"case": case})
        return f"Released: {restored} role(s) given back" + (f"; failed: {'; '.join(failed)}" if failed else ".")

    async def release_probation(self, guild_id: int, actor_id: int, user_id: int) -> str:
        rt = self.runtime(guild_id)
        self.require(guild_id, actor_id, "release_probation")
        role = rt.config["probation_role"]
        if role:
            try:
                await rt.port.remove_roles(user_id, [role], f"Rudhabot: probation released by {actor_id}")
            except ActionFailed as error:
                raise Refused(f"Couldn't remove the probation role: {error}") from None
        self.store.end_probation(guild_id, user_id)
        return "Probation lifted."

    async def verify(self, guild_id: int, user_id: int) -> str:
        rt = self.runtime(guild_id)
        role = rt.config["verified_role"] if rt else 0
        if not role:
            return "Verification isn't set up on this server."
        access = rt.port.access(user_id)
        if access and role in access[0]:
            return "You're already verified."
        try:
            await rt.executor.pacer.wait()
            await rt.port.add_role(user_id, role, "Rudhabot: passed the verification check")
        except ActionFailed:
            return "Verification couldn't be completed right now; please tell a moderator."
        return "Verified. Welcome!"
