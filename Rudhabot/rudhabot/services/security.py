"""Server security orchestration: events in, engine, actions out, records kept.

This service holds one `Runtime` per server (its configuration, detection engine, action executor and buffers) and
does everything that spans several steps: carrying out or proposing what the engine plans, approvals (one-use tokens,
compare-and-set, clearance re-checked when clicked), lockdowns and their restore, snapshots and restores, probation and
slowmode expiry, quarantine, moderation cases, model scoring off the event loop, retention, and drills.

It never imports discord.py: the cogs convert Discord objects into events and give it a port factory.
`notify(kind, guild_id, payload)` is how it tells the UI layer what to post.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections import Counter
from dataclasses import dataclass, field
from datetime import timezone
from typing import Awaitable, Callable, Optional

from rudhabot.persistence.security import SecurityStore
from rudhabot.security import snapshots as snap
from rudhabot.security.actions import ActionFailed, ActionResult, Executor, GuildPort, Pacer
from rudhabot.security.briefing import Briefer, after_action
from rudhabot.security.clearance import Refused, Tier, check, resolve_tier
from rudhabot.security.detectors import _stem
from rudhabot.security.engine import RULES_VERSION, GuildEngine, Incident, Outcome
from rudhabot.security.events import AuditEntry, Message, Tick
from rudhabot.security.levels import Level
from rudhabot.security.lockdown import OverwriteChange, plan_lockdown, plan_restore
from rudhabot.security.policy import SecurityConfig
from rudhabot.security.posture import audit as posture_audit
from rudhabot.security.responses import PlannedAction
from rudhabot.security.signals import Reason, Signal

log = logging.getLogger("rudhabot.security")

Notify = Callable[[str, int, dict], Awaitable[None]]
ALERT_SEVERITY = 0.5            # incidents at or above this are alerted at once; weaker ones go into the digest
DIGEST_EVERY_S = 600
SNAPSHOT_EVERY_S = 6 * 3600
STATE_EVERY_S = 300
MODEL_EVERY_S = 60
MAX_MODEL_QUEUE = 200
STALE_STAFF_DAYS = 60


@dataclass
class Runtime:
    guild_id: int
    config: SecurityConfig
    engine: GuildEngine
    port: GuildPort
    executor: Executor
    dirty: set = field(default_factory=set)
    seen: dict = field(default_factory=dict)
    quarantined: set = field(default_factory=set)
    allows: list = field(default_factory=list)
    allows_at: float = 0.0
    digest: list = field(default_factory=list)
    last: dict = field(default_factory=dict)            # task name -> last run
    model_pending: int = 0
    refreshed_at: float = 0.0


class SecurityService:
    def __init__(self, store: SecurityStore, clock, *, admin_role_id: int = 0, models=None,
                 briefer: Optional[Briefer] = None, port_factory: Optional[Callable[[int], GuildPort]] = None,
                 bot_id: Callable[[], int] = lambda: 0, pacer_factory=Pacer):
        self.store, self.clock = store, clock
        self.admin_role_id = admin_role_id
        self.models = models
        self.briefer = briefer or Briefer()
        self.port_factory = port_factory
        self.bot_id = bot_id
        self.pacer_factory = pacer_factory
        self.runtimes: dict[int, Runtime] = {}
        self.notify: Optional[Notify] = None
        self.health = Counter()
        self._model_sem = asyncio.Semaphore(2)
        self._tasks: set = set()                 # strong references: the loop only holds tasks weakly

    # ================================================================ plumbing
    def now(self) -> float:
        return self.clock.now(timezone.utc).timestamp()

    async def _notify(self, kind: str, guild_id: int, payload: dict) -> None:
        if self.notify is None:
            return
        try:
            await self.notify(kind, guild_id, payload)
        except Exception:
            self.health["notify_errors"] += 1
            log.exception("security notification %s failed", kind)

    def runtime(self, guild_id: int) -> Optional[Runtime]:
        rt = self.runtimes.get(guild_id)
        if rt is not None:
            return rt
        if self.port_factory is None:
            return None
        values, version = self.store.config(guild_id)
        config = SecurityConfig(values, version)
        state = self.store.state(guild_id, "engine") or {}
        port = self.port_factory(guild_id)
        engine = GuildEngine(guild_id, config, bot_id=self.bot_id(), state=state,
                             model_versions=self.models.versions() if self.models else {})
        rt = Runtime(guild_id, config, engine, port, None)       # executor needs rt for tier lookups
        rt.executor = Executor(port, tier_of=lambda uid: self.tier_of(guild_id, uid), record=self._recorder(guild_id),
                               pacer=self.pacer_factory())
        rt.quarantined = {q["user_id"] for q in self.store.quarantined(guild_id)}
        engine.exempt = lambda kind, ident, detector: self._exempt(rt, kind, ident, detector)
        for fp_user, avatar, stem in self.store.fingerprints(guild_id):
            if avatar:
                engine.joins.banned[f"a:{avatar}"] = fp_user
            if stem and len(stem) >= 5:
                engine.joins.banned[f"n:{stem}"] = fp_user
        self.runtimes[guild_id] = rt
        self._refresh(rt)
        return rt

    def attach(self, guild_id: int, port: GuildPort) -> Runtime:
        """Use a specific port for a server (tests, drills)."""
        factory = self.port_factory
        self.port_factory = lambda gid: port if gid == guild_id else factory(gid)
        self.runtimes.pop(guild_id, None)
        return self.runtime(guild_id)

    def _refresh(self, rt: Runtime) -> None:
        """Keep the anti-nuke detector's view of roles current (member counts, permissions)."""
        try:
            model = rt.port.model()
        except Exception:
            return
        an = rt.engine.antinuke
        an.role_members = {r.id: r.member_count for r in model.roles}
        an.role_perms = {r.id: r.permissions for r in model.roles}
        an.everyone_id = model.id
        an.bot_id = model.bot_id or an.bot_id
        rt.engine.bot_id = model.bot_id or rt.engine.bot_id
        rt.refreshed_at = self.now()

    def _recorder(self, guild_id: int):
        def record(plan: PlannedAction, status: str, result: ActionResult, actor_id, action_id):
            if action_id is None:
                return self.store.add_action(guild_id, plan.to_dict(), status, actor_id, shadow=status == "shadow")
            self.store.update_action(action_id, status, result.to_dict())
            return action_id
        return record

    def _exempt(self, rt: Runtime, kind: str, ident: int, detector: str) -> bool:
        now = self.now()
        if now - rt.allows_at > 60:
            rt.allows = self.store.allows(rt.guild_id, now)
            rt.allows_at = now
            rt.engine.extra_allow = {a["value"] for a in rt.allows if a["kind"] == "domain"}
        value = str(ident)
        for a in rt.allows:
            if a["kind"] == kind and a["value"] == value and (not a["detectors"] or detector in a["detectors"]
                                                               or "all" in a["detectors"]):
                return True
        return False

    # ================================================================ clearance
    def tier_of(self, guild_id: int, user_id: Optional[int]) -> Tier:
        if user_id is None:
            return Tier.MEMBER
        rt = self.runtimes.get(guild_id) or self.runtime(guild_id)
        if rt is None:
            return Tier.MEMBER
        if user_id == rt.port.owner_id:
            return Tier.OWNER
        access = rt.port.access(user_id)
        if access is None:
            return Tier.MEMBER
        roles, bits = access
        return resolve_tier(user_id, roles, bits, owner_id=rt.port.owner_id, role_tiers=rt.config["tiers"],
                            admin_role_id=self.admin_role_id, derive=rt.config["derive_tiers"],
                            quarantined=user_id in rt.quarantined)

    def require(self, guild_id: int, user_id: int, action: str) -> Tier:
        tier = self.tier_of(guild_id, user_id)
        check(int(tier), action)
        return tier

    # ================================================================ events
    async def handle(self, guild_id: int, event) -> Optional[Outcome]:
        rt = self.runtime(guild_id)
        if rt is None:
            return None
        if isinstance(event, Message) and event.author_tier >= 1:
            rt.seen[event.author_id] = event.ts
        if isinstance(event, AuditEntry) and event.action in ("role_create", "role_update", "member_role_update") \
                and self.now() - rt.refreshed_at > 5:
            self._refresh(rt)                    # role permissions must be current to judge a grant
        try:
            out = rt.engine.observe(event)
        except Exception:
            self.health["engine_errors"] += 1
            log.exception("security engine failed on %s", type(event).__name__)
            return None
        await self._outcome(rt, out)
        if isinstance(event, Message) and out.model_candidate and self.models is not None \
                and rt.config["models.message"] and self.models.message_ready:
            if rt.model_pending < MAX_MODEL_QUEUE:
                rt.model_pending += 1
                task = asyncio.get_running_loop().create_task(self._score_message(rt, event))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            else:
                self.health["model_dropped"] += 1
        return out

    async def _score_message(self, rt: Runtime, event: Message) -> None:
        try:
            async with self._model_sem:
                scored = await self.models.score_message(event.content)
            if scored is None:
                self.health["model_fallback"] += 1
                return
            out = rt.engine.observe_model(event, scored["scam"], scored["spam"], scored["uncertain"], scored["version"])
            await self._outcome(rt, out)
        except Exception:
            self.health["model_errors"] += 1
            log.exception("message model scoring failed")
        finally:
            rt.model_pending -= 1

    async def note_ban(self, guild_id: int, user_id: int, name: str, avatar_key: Optional[str]) -> None:
        """Remember a banned account's profile fingerprint (avatar hash, name stem) for ban-evasion hints."""
        rt = self.runtime(guild_id)
        stem = _stem(name)
        self.store.add_fingerprint(guild_id, user_id, avatar_key, stem)
        if rt is not None:
            rt.engine.joins.note_ban(user_id, name, avatar_key)

    async def report(self, guild_id: int, reporter_id: int, event: Message) -> Optional[str]:
        """A member reported a message: an incident signal (never an automatic action on its own)."""
        rt = self.runtime(guild_id)
        if rt is None:
            return None
        sig = Signal("member_report", "spam", 0.45, 0.7, event.ts, [Reason.of("MSG_REPORTED", count=1)],
                     users=[event.author_id], channels=[event.channel_id],
                     messages=[(event.channel_id, event.message_id)], evidence={"reporter": reporter_id},
                     snippet=event.content[:200] if event.content else None)
        out = rt.engine._absorb([sig], event.ts)
        await self._outcome(rt, out, force_alert=True)
        incident = (out.opened or out.updated or [None])[0]
        return incident.id if incident else None

    # ================================================================ outcomes
    async def _outcome(self, rt: Runtime, out: Outcome, force_alert: bool = False) -> None:
        for inc in out.opened + out.updated:
            rt.dirty.add(inc.id)
        for inc in out.closed:
            self.store.save_incident(rt.guild_id, inc.to_dict())
            rt.dirty.discard(inc.id)
            if inc.alerted:
                await self._notify("incident_closed", rt.guild_id, {"incident": inc.to_dict()})
        if out.level_change:
            old, new, reason = out.level_change
            self.store.set_state(rt.guild_id, "engine", rt.engine.state())
            await self._notify("level", rt.guild_id, {"old": old, "new": new, "reason": reason,
                                                      "orders": rt.engine.threat.orders.__dict__,
                                                      "live": rt.config.live})
        for plan in out.plans:
            await self._dispatch(rt, plan)
        for inc in out.opened + out.updated:
            if not inc.alerted and (force_alert or inc.severity >= ALERT_SEVERITY or inc.family == "nuke"):
                inc.alerted = True
                self._flush_incident(rt, inc)
                await self._notify("incident", rt.guild_id, {"incident": inc.to_dict(), "new": True,
                                                             "level": int(rt.engine.level), "live": rt.config.live})
            elif not inc.alerted and inc in out.opened:
                rt.digest.append(inc.id)

    def _incident(self, rt: Runtime, incident_id: Optional[str]) -> Optional[Incident]:
        if not incident_id:
            return None
        inc = rt.engine.incidents.get(incident_id)
        if inc is None:
            data = self.store.incident(rt.guild_id, incident_id)
            inc = Incident.from_dict(data) if data else None
        return inc

    def _flush_incident(self, rt: Runtime, inc: Incident) -> None:
        self.store.save_incident(rt.guild_id, inc.to_dict())
        inc.snippets = []                      # persisted to the snippet table (with its own retention)
        rt.dirty.discard(inc.id)

    async def _dispatch(self, rt: Runtime, plan: PlannedAction) -> None:
        inc = self._incident(rt, plan.incident_id)
        if plan.authority == "auto":
            result = await self.run_plan(rt, plan, actor_id=None)
            if inc is not None:
                prefix = "[shadow] would " if result.status == "shadow" else ""
                inc.note(self.now(), "action", f"{prefix}{plan.kind.replace('_', ' ')}: {result.status}"
                         + (f" ({result.detail})" if result.detail and result.status != "shadow" else ""))
                inc.actions.append({"id": result.action_id, "kind": plan.kind, "authority": "auto",
                                    "status": result.status})
                rt.dirty.add(inc.id)
            if result.status in ("confirmed", "partial") and plan.kind in ("quarantine", "kick_bot", "delete_webhook",
                                                                          "revert_permissions"):
                await self._notify("containment", rt.guild_id, {"plan": plan.to_dict(), "result": result.to_dict(),
                                                                "incident": inc.to_dict() if inc else None})
        elif plan.authority in ("confirm", "approve2"):
            token = await self.request_approval(rt, plan, requested_by=None)
            if inc is not None:
                inc.note(self.now(), "proposal", f"proposed {plan.kind.replace('_', ' ')} ({plan.authority})")
                inc.actions.append({"id": None, "kind": plan.kind, "authority": plan.authority, "status": "awaiting",
                                    "token": token})
                rt.dirty.add(inc.id)

    # ================================================================ carrying out a plan
    def _context(self, rt: Runtime) -> dict:
        return {"probation_role": rt.config["probation_role"], "quarantine_role": rt.config["quarantine_role"]}

    async def run_plan(self, rt: Runtime, plan: PlannedAction, actor_id: Optional[int],
                       approvers: Optional[list] = None) -> ActionResult:
        shadow = not rt.config.live
        if plan.kind == "lockdown":
            if shadow and actor_id is None:
                return ActionResult("shadow", "shadow mode")
            return await self.apply_lockdown(rt, plan, actor_id, approvers or [])
        if plan.kind == "restore":
            if shadow and actor_id is None:
                return ActionResult("shadow", "shadow mode")
            return await self.apply_restore(rt, plan, actor_id)
        result = await rt.executor.execute(plan, actor_id=actor_id, shadow=shadow, context=self._context(rt))
        if not result.ok:
            return result
        now = self.now()
        if plan.kind == "probation":
            minutes = int(plan.params.get("minutes", rt.config["probation_minutes"]))
            for user_id in result.done:
                self.store.set_probation(rt.guild_id, user_id, int(rt.config["probation_role"]), now + minutes * 60,
                                         plan.reason, plan.incident_id)
        elif plan.kind == "slowmode":
            revert = int(plan.params.get("revert_after", 0))
            for channel_id in result.done:
                if revert:
                    previous = result.data.get("previous", {}).get(str(channel_id), 0)
                    self.store.set_timer(rt.guild_id, "slowmode", channel_id, now + revert, {"previous": previous})
        elif plan.kind == "quarantine":
            for user_id in result.done:
                removed = result.data.get("removed", {}).get(str(user_id), {})
                self.store.add_quarantine(rt.guild_id, user_id, removed.get("roles", []), plan.incident_id, plan.reason)
                rt.quarantined.add(user_id)
                case = self.store.add_case(rt.guild_id, "quarantine", user_id, actor_id or 0, plan.reason,
                                           incident_id=plan.incident_id, extra={"roles": removed.get("roles", []),
                                                                                "kicked_bot": removed.get("kicked_bot")})
                await self._notify("case", rt.guild_id, {"case": case})
        elif plan.kind in ("timeout", "kick", "ban", "kick_bot"):
            kind = {"kick_bot": "kick"}.get(plan.kind, plan.kind)
            for user_id in result.done:
                case = self.store.add_case(rt.guild_id, kind, user_id, actor_id or 0, plan.reason,
                                           duration_s=int(plan.params.get("seconds", 0)) or None,
                                           incident_id=plan.incident_id, extra={"automatic": actor_id is None})
                await self._notify("case", rt.guild_id, {"case": case})
        return result

    # ================================================================ approvals
    async def request_approval(self, rt: Runtime, plan: PlannedAction, requested_by: Optional[int]) -> str:
        action_id = self.store.add_action(rt.guild_id, plan.to_dict(), "awaiting", requested_by)
        token = secrets.token_urlsafe(9)
        expires = self.now() + int(rt.config["approvals.expire_minutes"]) * 60
        two = plan.authority == "approve2"
        self.store.add_approval(token, rt.guild_id, action_id, requested_by, int(plan.tier), two, expires)
        preview = self.preview(rt, plan)
        await self._notify("approval", rt.guild_id, {"token": token, "plan": plan.to_dict(), "preview": preview,
                                                     "two_person": two, "requested_by": requested_by,
                                                     "expires": expires, "action_id": action_id,
                                                     "live": rt.config.live})
        return token

    def preview(self, rt: Runtime, plan: PlannedAction) -> list[str]:
        try:
            if plan.kind == "lockdown":
                lp = self._lockdown_plan(rt, plan)
                lines = lp.preview(rt.port.model()) or ["(nothing to change: already locked or protected)"]
                return lines + [f"⚠️ {m}" for m in lp.manual[:6]] + [f"skip: {s}" for s in lp.skipped[:4]]
            if plan.kind == "restore":
                data = self.store.snapshot(rt.guild_id, plan.params.get("snapshot_id"))
                if data is None:
                    return ["no snapshot available"]
                rp = snap.diff(data["data"], rt.port.model())
                return [s.description for s in rp.steps[:15]] + [f"note: {n}" for n in rp.notes[:4]] \
                    or ["the server already matches the snapshot"]
            if plan.kind in ("ban", "kick", "timeout"):
                return [f"{len(plan.targets)} account(s): " + ", ".join(f"<@{u}>" for u in plan.targets[:20])
                        + (" ..." if len(plan.targets) > 20 else "")]
            if plan.kind == "revert_permissions":
                from rudhabot.security import perms as P
                return [f"remove {', '.join(P.names(int(plan.params.get('remove', 0))))} from role(s) "
                        + ", ".join(str(t) for t in plan.targets)]
            if plan.kind in ("pause_invites", "pause_dms"):
                return [f"pause {'invites' if plan.kind == 'pause_invites' else 'DMs'} for "
                        f"{plan.params.get('minutes', 60)} minutes (Discord lifts it on its own)"]
        except Exception:
            log.exception("preview failed")
        return [plan.reason]

    async def approve(self, guild_id: int, token: str, user_id: int) -> tuple[str, Optional[ActionResult]]:
        """Approve (or second) a proposal. Returns (message, result-if-carried-out). Raises Refused."""
        rt = self.runtime(guild_id)
        a = self.store.approval(token)
        if rt is None or a is None or a["guild_id"] != guild_id:
            raise Refused("This request doesn't exist (or belongs to another server).")
        if a["status"] not in ("pending", "half"):
            raise Refused(f"This request is already {a['status']}.")
        if self.now() > a["expires"]:
            self.store.claim_approval(token, a["status"], "expired", a["approvers"])
            raise Refused("This request has expired.")
        tier = self.tier_of(guild_id, user_id)
        if tier < a["required_tier"]:
            raise Refused(f"This needs {Tier(a['required_tier']).label} clearance or higher.")
        approvers = list(a["approvers"])
        if user_id in approvers:
            raise Refused("You've already approved this; it needs someone else.")
        if a["two_person"]:
            if a["requested_by"] == user_id and tier < Tier.OWNER:
                raise Refused("You requested this, so a second staff member has to approve it.")
            needed = 1 if a["requested_by"] is not None else 2
            approvers.append(user_id)
            if len(approvers) < needed:
                if not self.store.claim_approval(token, a["status"], "half", approvers):
                    raise Refused("Someone else just answered this request.")
                await self._notify("approval_update", guild_id, {"token": token, "status": "half",
                                                                 "approvers": approvers})
                return "First approval recorded: one more staff member must approve.", None
        else:
            approvers.append(user_id)
        if not self.store.claim_approval(token, a["status"], "approved", approvers):
            raise Refused("Someone else just answered this request.")
        action = self.store.action(a["action_id"])
        plan = PlannedAction(action["kind"], action["authority"], Tier(a["required_tier"]), action["reason"],
                             action["targets"], action["params"], action["codes"], action["incident_id"])
        result = await self.run_plan(rt, plan, actor_id=user_id, approvers=approvers)
        self.store.update_action(a["action_id"], result.status, result.to_dict(), approved_by=approvers)
        inc = self._incident(rt, plan.incident_id)
        if inc is not None:
            inc.note(self.now(), "decision", f"{plan.kind.replace('_', ' ')} approved by "
                     + ", ".join(f"<@{u}>" for u in approvers) + f": {result.status}")
            for entry in inc.actions:
                if entry.get("token") == token:
                    entry["status"] = result.status
            rt.dirty.add(inc.id)
        await self._notify("approval_update", guild_id, {"token": token, "status": "approved",
                                                         "approvers": approvers, "result": result.to_dict()})
        return f"Approved: {result.status}. {result.detail}", result

    async def deny(self, guild_id: int, token: str, user_id: int) -> str:
        a = self.store.approval(token)
        if a is None or a["guild_id"] != guild_id:
            raise Refused("This request doesn't exist.")
        if a["status"] not in ("pending", "half"):
            raise Refused(f"This request is already {a['status']}.")
        if self.tier_of(guild_id, user_id) < a["required_tier"] and user_id != a["requested_by"]:
            raise Refused(f"This needs {Tier(a['required_tier']).label} clearance or higher.")
        if not self.store.claim_approval(token, a["status"], "denied", a["approvers"] + [user_id]):
            raise Refused("Someone else just answered this request.")
        self.store.update_action(a["action_id"], "denied")
        await self._notify("approval_update", guild_id, {"token": token, "status": "denied", "by": user_id})
        return "Declined. Nothing was changed."

    # ================================================================ lockdowns
    def _lockdown_plan(self, rt: Runtime, plan: PlannedAction):
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

    async def apply_lockdown(self, rt: Runtime, plan: PlannedAction, actor_id: Optional[int],
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
        status = "confirmed" if done and not failed else "partial" if done else "failed"
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

    async def apply_restore(self, rt: Runtime, plan: PlannedAction, actor_id: Optional[int]) -> ActionResult:
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
        result = ActionResult("confirmed" if done and not failed else "partial" if done else "failed",
                              f"{len(done)} step(s) done, {len(failed)} failed", done, failed,
                              data={"notes": rp.notes, "roles": {str(k): v for k, v in role_map.items()},
                                    "channels": {str(k): v for k, v in channel_map.items()}})
        await self._notify("restore", rt.guild_id, {"result": result.to_dict(), "snapshot": data["id"]})
        return result

    # ================================================================ moderation commands
    async def moderate(self, guild_id: int, actor_id: int, kind: str, targets: list[int], *, reason: str,
                       seconds: int = 0, delete_seconds: int = 0) -> tuple[ActionResult, list[dict]]:
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
        return result, cases

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
                                 {"previous": previous})
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
                raise Refused(f"Couldn't remove the probation role: {error}")
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

    # ================================================================ configuration and levels
    def save_config(self, guild_id: int, config: SecurityConfig, by: int, note: str) -> SecurityConfig:
        rt = self.runtime(guild_id)
        version = self.store.save_config(guild_id, {k: v for k, v in config.to_dict().items()}, by, note)
        fresh = SecurityConfig(config.to_dict(), version)
        if rt is not None:
            rt.config = fresh
            rt.engine.cfg = fresh
        return fresh

    def config(self, guild_id: int) -> SecurityConfig:
        rt = self.runtime(guild_id)
        if rt is not None:
            return rt.config
        values, version = self.store.config(guild_id)
        return SecurityConfig(values, version)

    async def set_level(self, guild_id: int, by: int, level: int, reason: str, hold_minutes: int) -> Outcome:
        rt = self.runtime(guild_id)
        self.require(guild_id, by, "set_level")
        out = rt.engine.set_level(Level(level), self.now(), by, reason or "set by staff", hold_minutes * 60)
        await self._outcome(rt, out)
        self.store.set_state(guild_id, "engine", rt.engine.state())
        return out

    # ================================================================ periodic work
    async def tick(self) -> None:
        now = self.now()
        for rt in list(self.runtimes.values()):
            try:
                await self._tick(rt, now)
            except Exception:
                self.health["tick_errors"] += 1
                log.exception("security tick failed for %s", rt.guild_id)
        for timer in self.store.due_timers(now):
            try:
                await self._timer(timer)
            except Exception:
                log.exception("security timer %s failed", timer)
            finally:
                self.store.clear_timer(timer["id"])
        for item in self.store.due_probation(now):
            rt = self.runtime(item["guild_id"])
            try:
                if rt is not None and rt.port.access(item["user_id"]):
                    await rt.port.remove_roles(item["user_id"], [item["role_id"]], "Rudhabot: probation period over")
            except ActionFailed:
                pass
            self.store.end_probation(item["guild_id"], item["user_id"])
        for a in self.store.expire_approvals(now):
            self.store.update_action(a["action_id"], "expired")
            await self._notify("approval_update", a["guild_id"], {"token": a["token"], "status": "expired"})

    def _due(self, rt: Runtime, name: str, every: float, now: float) -> bool:
        if now - rt.last.get(name, 0.0) >= every:
            rt.last[name] = now
            return True
        return False

    async def _tick(self, rt: Runtime, now: float) -> None:
        out = rt.engine.observe(Tick(rt.guild_id, now))
        await self._outcome(rt, out)
        for incident_id in list(rt.dirty):
            inc = rt.engine.incidents.get(incident_id)
            if inc is not None:
                self._flush_incident(rt, inc)
                if inc.alerted:
                    await self._notify("incident", rt.guild_id, {"incident": inc.to_dict(), "new": False,
                                                                 "level": int(rt.engine.level), "live": rt.config.live})
            rt.dirty.discard(incident_id)
        if self._due(rt, "model", MODEL_EVERY_S, now) and self.models is not None and rt.config["models.incident"] \
                and self.models.incident_ready:
            vector = rt.engine.incident_features(now)
            scored = await self.models.score_incident(vector)
            if scored is not None:
                out = rt.engine.model_incident_signal(now, scored["p"], scored["family"], scored["confident"])
                await self._outcome(rt, out)
        if self._due(rt, "state", STATE_EVERY_S, now):
            self.store.set_state(rt.guild_id, "engine", rt.engine.state())
            self.store.touch_seen(rt.guild_id, rt.seen)
            rt.seen = {}
            self._refresh(rt)
        if self._due(rt, "digest", DIGEST_EVERY_S, now) and rt.digest:
            items = []
            for incident_id in rt.digest[:25]:
                inc = self._incident(rt, incident_id)
                if inc is not None and not inc.alerted:
                    items.append(inc.to_dict())
            rt.digest = []
            if items:
                await self._notify("digest", rt.guild_id, {"incidents": items})
        if now - rt.last.get("snapshot", 0.0) >= SNAPSHOT_EVERY_S:
            try:
                self.take_snapshot(rt.guild_id, "scheduled")
            except Exception:
                log.exception("scheduled snapshot failed")
                rt.last["snapshot"] = now
        if self._due(rt, "sweep", 3600, now):
            self.store.sweep(rt.guild_id, rt.config["retention.snippet_days"], rt.config["retention.incident_days"])

    async def _timer(self, timer: dict) -> None:
        rt = self.runtime(timer["guild_id"])
        if rt is None:
            return
        if timer["kind"] == "slowmode":
            try:
                await rt.port.set_slowmode(timer["target"], int(timer["data"].get("previous", 0)),
                                           "Rudhabot: temporary slowmode over")
            except ActionFailed as error:
                log.warning("could not lift slowmode in %s: %s", timer["target"], error)
        elif timer["kind"] == "lockdown":
            lock = self.store.lockdown(timer["target"])
            if lock and lock["status"] in ("applying", "active", "lifting"):
                await self.lift_lockdown(timer["guild_id"], timer["target"], None, "expired")

    # ================================================================ reporting
    def status(self, guild_id: int) -> dict:
        rt = self.runtime(guild_id)
        if rt is None:
            return {}
        threat = rt.engine.threat
        now = self.now()
        open_incidents = sorted(rt.engine.incidents.values(), key=lambda i: i.severity, reverse=True)
        snaps = self.store.snapshots(guild_id, 1)
        return {
            "level": int(threat.level), "level_label": threat.level.label, "level_theme": threat.level.theme,
            "level_source": threat.source, "level_since": threat.since, "hold_until": threat.hold_until,
            "mode": rt.config["mode"], "config_version": rt.config.version, "rules": RULES_VERSION,
            "open_incidents": len(open_incidents),
            "incidents": [{"id": i.id, "family": i.family, "severity": i.severity, "users": len(i.users),
                           "last": i.last} for i in open_incidents[:5]],
            "lockdowns": [{"id": l["id"], "scope": l["scope"], "channels": len(l["channels"]), "expires": l["expires"]}
                          for l in self.store.active_lockdowns(guild_id)],
            "probation": len(self.store.probation(guild_id)),
            "quarantined": [q["user_id"] for q in self.store.quarantined(guild_id)],
            "pending_approvals": len(self.store.pending_approvals(guild_id)),
            "last_snapshot": snaps[0]["created"] if snaps else None,
            "actions_24h": self.store.action_counts(guild_id, now - 86400),
            "models": self.models.status(rt.config) if self.models is not None else {"available": False},
            "llm": self.briefer.enabled and rt.config["llm.summaries"],
            "events": rt.engine.counters["events"], "health": dict(self.health),
            "alert_channel": rt.config["alert_channel"], "probation_role": rt.config["probation_role"],
        }

    def posture(self, guild_id: int) -> tuple[int, list]:
        rt = self.runtime(guild_id)
        model = rt.port.model()
        staff = [m.id for m in model.members if not m.bot and self.tier_of(guild_id, m.id) >= Tier.MODERATOR
                 and m.id != model.owner_id]
        seen = self.store.last_seen(guild_id, staff)
        cutoff = self.now() - STALE_STAFF_DAYS * 86400
        installed = self.store.state(guild_id, "installed_at")
        if installed is None:
            self.store.set_state(guild_id, "installed_at", self.now())
            installed = self.now()
        # only judge staleness once Rudhabot has been watching long enough to know
        stale = [u for u in staff if seen.get(u, 0) < cutoff] if installed < cutoff else []
        cfg = rt.config
        return posture_audit(model, configured={"alert_channel": cfg["alert_channel"],
                                                "probation_role": cfg["probation_role"], "mode": cfg["mode"]},
                             stale_staff=stale, staff_ids=staff)

    async def incident_report(self, guild_id: int, incident_id: str, use_llm: bool = True) -> tuple[str, str]:
        rt = self.runtime(guild_id)
        inc = self._incident(rt, incident_id)
        if inc is None:
            raise Refused("No such incident.")
        actions = self.store.actions_for_incident(guild_id, incident_id)
        if use_llm and rt.config["llm.summaries"] and self.briefer.enabled:
            return await self.briefer.summary(inc.to_dict(), actions, guild_id)
        return after_action(inc.to_dict(), actions), "template"

    async def ask(self, guild_id: int, question: str) -> tuple[str, str]:
        status = self.status(guild_id)
        incidents = self.store.incidents(guild_id, limit=8)
        return await self.briefer.answer(question, {k: v for k, v in status.items() if k != "health"}, incidents,
                                         guild_id)

    async def shutdown(self) -> None:
        for rt in self.runtimes.values():
            try:
                for incident_id in list(rt.dirty):
                    inc = rt.engine.incidents.get(incident_id)
                    if inc is not None:
                        self._flush_incident(rt, inc)
                self.store.set_state(rt.guild_id, "engine", rt.engine.state())
                self.store.touch_seen(rt.guild_id, rt.seen)
            except Exception:
                log.exception("saving security state on shutdown failed")
