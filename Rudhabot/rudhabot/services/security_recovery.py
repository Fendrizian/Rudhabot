"""Lockdowns and snapshot restores: the multi-step recovery procedures of the security service.

A mixin of SecurityService (it uses the service's store, runtimes, approvals and notifications), kept apart because
these procedures are long and change rarely: plan, record the plan before touching anything, apply with pacing,
report what has to be fixed by hand, and undo safely.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from rudhabot.security import snapshots as snap
from rudhabot.security.actions import ActionFailed, ActionResult, outcome_status
from rudhabot.security.clearance import Refused, Tier
from rudhabot.security.lockdown import OverwriteChange, plan_lockdown, plan_restore
from rudhabot.security.responses import PlannedAction

if TYPE_CHECKING:                          # annotations only: the service module imports this one
    from rudhabot.services.security import Runtime

log = logging.getLogger("rudhabot.security")


class RecoveryMixin:
    # ================================================================ lockdowns
    def _lockdown_plan(self, rt: "Runtime", plan: PlannedAction):
        locked = {c for l in self.store.active_lockdowns(rt.guild_id) for c in l["channels"]}
        protected = set(rt.config["channels.protected"]) | {rt.config["alert_channel"], rt.config["modlog_channel"]}
        return plan_lockdown(rt.port.model(), plan.params.get("scope", "channels"), plan.targets,
                             role_tiers=rt.config["tiers"], protected=protected, locked_elsewhere=locked)

    async def request_lockdown(self, guild_id: int, requester: int, scope: str, channels: list[int], minutes: int,
                               reason: str) -> tuple[str, list[str]]:
        rt = self.runtime(guild_id)
        self.require(guild_id, requester, "lockdown_request")
        minutes = max(5, min(int(minutes or rt.config["lockdown.default_minutes"]), int(rt.config["lockdown.max_minutes"])))
        if scope != "server" and not channels:
            raise Refused("Choose the channels (or a category) to lock, or the whole server.")
        plan = PlannedAction("lockdown", "approve2" if rt.config["approvals.two_person"] else "confirm", Tier.SENIOR,
                             reason or "manual lockdown", targets=list(channels), params={"scope": scope, "minutes": minutes})
        lp = self._lockdown_plan(rt, plan)
        if not lp.changes:
            raise Refused("Nothing to lock: those channels are already locked, protected or not lockable.")
        token = await self.request_approval(rt, plan, requested_by=requester)
        return token, lp.preview(rt.port.model())

    async def apply_lockdown(self, rt: "Runtime", plan: PlannedAction, actor_id: Optional[int],
                             approvers: list) -> ActionResult:
        lp = self._lockdown_plan(rt, plan)
        if not lp.changes:
            return ActionResult("skipped", "nothing to lock")
        try:
            self.take_snapshot(rt.guild_id, "before lockdown")
        except Exception:
            log.exception("snapshot before lockdown failed")
        minutes = int(plan.params.get("minutes", rt.config["lockdown.default_minutes"]))
        expires = self.now() + minutes * 60
        lock_id = self.store.add_lockdown(rt.guild_id, lp.scope, lp.channel_ids, expires,
                                          approvers[0] if approvers else (actor_id or 0),
                                          approvers[-1] if len(approvers) > 1 else actor_id,
                                          [c.to_dict() for c in lp.changes], lp.manual, plan.incident_id)
        self.store.set_timer(rt.guild_id, "lockdown", lock_id, expires)
        done, failed = 0, []
        reason = f"Rudhabot lockdown #{lock_id}: {plan.reason}"[:480]
        for change in lp.changes:
            await rt.executor.pacer.wait()
            try:
                await rt.port.set_overwrite(change.channel_id, change.target_id, change.target_type, *change.after, reason)
                done += 1
            except ActionFailed as error:
                failed.append(f"channel {change.channel_id}: {error}")
        manual = lp.manual + failed
        self.store.update_lockdown(lock_id, status="active", manual=manual)
        report = {"id": lock_id, "changed": done, "failed": len(failed), "manual": manual, "expires": expires,
                  "channels": len(lp.channel_ids), "scope": lp.scope}
        await self._notify("lockdown", rt.guild_id, {"event": "applied", **report})
        status = outcome_status(done, failed)
        return ActionResult(status, f"lockdown #{lock_id}: {done} change(s), {len(failed)} failed", data=report)

    async def lift_lockdown(self, guild_id: int, lockdown_id: int, by: Optional[int], reason: str = "lifted") -> dict:
        rt = self.runtime(guild_id)
        lock = self.store.lockdown(lockdown_id)
        if rt is None or lock is None or lock["guild_id"] != guild_id:
            raise Refused("No such lockdown.")
        if lock["status"] not in ("applying", "active", "lifting"):
            raise Refused(f"Lockdown #{lockdown_id} is already {lock['status']}.")
        if by is not None:
            self.require(guild_id, by, "lockdown_lift")
        self.store.update_lockdown(lockdown_id, status="lifting")
        changes = [OverwriteChange.from_dict(c) for c in lock["changes"]]
        restore, manual = plan_restore(rt.port.model(), changes)
        done = 0
        for change in restore:
            await rt.executor.pacer.wait()
            allow, deny = change.before or (0, 0)
            try:
                await rt.port.set_overwrite(change.channel_id, change.target_id, change.target_type, allow, deny,
                                            f"Rudhabot: lifting lockdown #{lockdown_id} ({reason})"[:480])
                done += 1
            except ActionFailed as error:
                manual.append(f"channel {change.channel_id}: couldn't restore ({error}); set it back by hand")
        report = {"id": lockdown_id, "restored": done, "manual": manual, "reason": reason}
        self.store.update_lockdown(lockdown_id, status="lifted" if not manual else "lifted_partial",
                                   lifted_at=self.now(), lifted_by=by, report=report)
        for timer in self.store.timers(guild_id):
            if timer["kind"] == "lockdown" and timer["target"] == lockdown_id:
                self.store.clear_timer(timer["id"])
        await self._notify("lockdown", guild_id, {"event": "lifted", **report})
        return report

    # ================================================================ snapshots and restore
    def take_snapshot(self, guild_id: int, reason: str) -> int:
        rt = self.runtime(guild_id)
        snap_id = self.store.add_snapshot(guild_id, snap.capture(rt.port.model()), reason)
        rt.last["snapshot"] = self.now()
        return snap_id

    async def request_restore(self, guild_id: int, requester: int, snapshot_id: Optional[int]) -> tuple[str, list[str]]:
        rt = self.runtime(guild_id)
        self.require(guild_id, requester, "snapshot")
        data = self.store.snapshot(guild_id, snapshot_id)
        if data is None:
            raise Refused("There is no snapshot yet. Use /security tools action:take snapshot.")
        plan = PlannedAction("restore", "approve2" if rt.config["approvals.two_person"] else "confirm", Tier.ADMIN,
                             f"restore from snapshot #{data['id']}", params={"snapshot_id": data["id"]})
        preview = self.preview(rt, plan)
        token = await self.request_approval(rt, plan, requested_by=requester)
        return token, preview

    async def apply_restore(self, rt: "Runtime", plan: PlannedAction, actor_id: Optional[int]) -> ActionResult:
        data = self.store.snapshot(rt.guild_id, plan.params.get("snapshot_id"))
        if data is None:
            return ActionResult("failed", "snapshot not found")
        model = rt.port.model()
        rp = snap.diff(data["data"], model)
        if rp.empty:
            return ActionResult("skipped", "the server already matches the snapshot")
        reason = f"Rudhabot restore from snapshot #{data['id']}"
        role_map: dict[int, int] = {}
        channel_map: dict[int, int] = {}
        done, failed = [], []
        for step in rp.steps:
            await rt.executor.pacer.wait()
            try:
                d = step.data
                if step.kind == "create_role":
                    role_map[int(d["old_id"])] = await rt.port.create_role(d, reason)
                elif step.kind == "edit_role":
                    await rt.port.edit_role_permissions(int(d["role_id"]), int(d["permissions"]), reason)
                elif step.kind == "create_channel":
                    category = d.get("category_id")
                    category = channel_map.get(category, category) if category else None
                    overwrites = snap.remap_overwrites(d.get("overwrites", []), role_map)
                    channel_map[int(d["old_id"])] = await rt.port.create_channel(d, category, overwrites, reason)
                elif step.kind == "set_overwrite":
                    target = role_map.get(int(d["target_id"]), int(d["target_id"]))
                    await rt.port.set_overwrite(int(d["channel_id"]), target, d["target_type"], int(d["allow"]),
                                                int(d["deny"]), reason)
                elif step.kind == "edit_setting":
                    await rt.port.edit_setting(d["setting"], int(d["value"]), reason)
                done.append(step.description)
            except ActionFailed as error:
                failed.append((step.description, str(error)))
        result = ActionResult(outcome_status(done, failed),
                              f"{len(done)} step(s) done, {len(failed)} failed", done, failed,
                              data={"notes": rp.notes, "roles": {str(k): v for k, v in role_map.items()},
                                    "channels": {str(k): v for k, v in channel_map.items()}})
        await self._notify("restore", rt.guild_id, {"result": result.to_dict(), "snapshot": data["id"]})
        return result
