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
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import timezone
from typing import Awaitable, Callable, Optional

from rudhabot.persistence.security import SecurityStore
from rudhabot.services.security_recovery import RecoveryMixin
from rudhabot.security import snapshots as snap
from rudhabot.security.actions import ActionFailed, ActionResult, Executor, GuildPort, Pacer
from rudhabot.security.briefing import Briefer, after_action
from rudhabot.security.clearance import Refused, Tier, check, resolve_tier
from rudhabot.security.detectors import name_stem
from rudhabot.security.engine import RULES_VERSION, GuildEngine, Incident, Outcome
from rudhabot.security.events import AuditEntry, Message, Tick
from rudhabot.security.levels import Level
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
REPORTS_PER_WINDOW = 5
REPORT_WINDOW_S = 600
MASSBAN_UNCHECKED = 10           # more established accounts than this in one /massban need a second approval
ANTINUKE_EXEMPT_MAX_DAYS = 7
MODEL_BAR, MODEL_BAR_MAX = 0.80, 0.95  # the confidence a model reading needs; staff verdicts move it within these


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
    reports: dict = field(default_factory=dict)         # reporter -> recent report times
    trusted: set = field(default_factory=set)           # bots ranked above Rudhabot (trusted, never contained)
    reported: dict = field(default_factory=dict)        # (channel, message) -> incident id
    activity: Counter = field(default_factory=Counter)  # (member, UTC day) -> messages, flushed with the state


class SecurityService(RecoveryMixin):
    def __init__(self, store: SecurityStore, clock, *, admin_role_id=0, models=None,
                 briefer: Optional[Briefer] = None, port_factory: Optional[Callable[[int], GuildPort]] = None,
                 bot_id: Callable[[], int] = lambda: 0, pacer_factory=Pacer):
        self.store, self.clock = store, clock
        self._admin_role_id = admin_role_id         # an id, or a live read from the server profile
        self.models = models
        self.briefer = briefer or Briefer()
        self.port_factory = port_factory
        self.bot_id = bot_id
        self.pacer_factory = pacer_factory
        self.runtimes: dict[int, Runtime] = {}
        self.notify: Optional[Notify] = None
        # (guild, member) -> why automatic restrictions should be left to staff (member standing sets this)
        self.protect: Optional[Callable[[int, int], Optional[str]]] = None
        self.health = Counter()
        self._model_sem = asyncio.Semaphore(2)
        self._tasks: set = set()                 # strong references: the loop only holds tasks weakly

    # ================================================================ plumbing
    @property
    def admin_role_id(self) -> int:
        role = self._admin_role_id
        return int(role() if callable(role) else role)

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
        self._apply_calibration(rt)
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
        # bots the owner placed above Rudhabot (e.g. Wick) are trusted like the owner: Rudhabot couldn't contain them
        # anyway, and their own raid clean-up must not look like a nuke
        top = model.bot_top_position
        rt.trusted = {m.id for m in model.members if m.bot and m.id != model.bot_id and model.top_position(m.id) > top}
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
        if kind == "user" and ident in rt.trusted:
            return True
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
        if isinstance(event, Message):
            if event.author_tier >= 1:
                rt.seen[event.author_id] = event.ts
            if not event.author_bot and event.webhook_id is None:
                rt.activity[(event.author_id, time.strftime("%Y-%m-%d", time.gmtime(event.ts)))] += 1
        if isinstance(event, AuditEntry) and event.action in ("role_create", "role_update", "member_role_update"):
            unknown = any(r not in rt.engine.antinuke.role_perms for r in event.roles_added)
            if unknown or self.now() - rt.refreshed_at > 5:
                self._refresh(rt)                # role permissions must be current to judge a grant
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
        stem = name_stem(name)
        self.store.add_fingerprint(guild_id, user_id, avatar_key, stem)
        if rt is not None:
            rt.engine.joins.note_ban(user_id, name, avatar_key)

    async def report(self, guild_id: int, reporter_id: int, event: Message,
                     reporter_new: bool = False) -> Optional[str]:
        """A member reported a message: an incident signal (never an automatic action on its own). Limited to
        REPORTS_PER_WINDOW per member; a message already reported adds to its incident instead of alerting again; a
        brand-new account's report is recorded without forcing an alert. Raises Refused when over the limit."""
        rt = self.runtime(guild_id)
        if rt is None:
            return None
        now = self.now()
        recent = [t for t in rt.reports.get(reporter_id, []) if now - t < REPORT_WINDOW_S]
        if len(recent) >= REPORTS_PER_WINDOW:
            raise Refused("You've sent a lot of reports in the last few minutes; the moderators have them. "
                          "Please try again later.")
        rt.reports[reporter_id] = recent + [now]
        key = (event.channel_id, event.message_id)
        known = rt.reported.get(key)
        if known is not None:
            inc = rt.engine.incidents.get(known)
            if inc is not None:
                inc.counts["member_report"] = inc.counts.get("member_report", 0) + 1
                rt.dirty.add(inc.id)
            return known
        sig = Signal("member_report", "spam", 0.45, 0.7, event.ts, [Reason.of("MSG_REPORTED", count=1)],
                     users=[event.author_id], channels=[event.channel_id],
                     messages=[(event.channel_id, event.message_id)], evidence={"reporter": reporter_id},
                     snippet=event.content[:200] if event.content else None)
        out = rt.engine._absorb([sig], event.ts)
        await self._outcome(rt, out, force_alert=not reporter_new)
        incident = (out.opened or out.updated or [None])[0]
        if incident is not None:
            rt.reported[key] = incident.id
            if len(rt.reported) > 5000:
                rt.reported.pop(next(iter(rt.reported)))
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

    def _spare_trusted(self, rt: Runtime, plan: PlannedAction) -> tuple[PlannedAction, list[int]]:
        """Automatic timeouts and probation skip members in good standing; staff are asked instead."""
        if self.protect is None or plan.authority != "auto" or plan.kind not in ("timeout", "probation"):
            return plan, []
        spared = [u for u in plan.targets if self.protect(rt.guild_id, u)]
        if not spared:
            return plan, []
        return replace(plan, targets=[u for u in plan.targets if u not in spared]), spared

    async def _dispatch(self, rt: Runtime, plan: PlannedAction) -> None:
        inc = self._incident(rt, plan.incident_id)
        plan, spared = self._spare_trusted(rt, plan)
        if spared:
            self.health["spared_trusted"] += len(spared)
            if inc is not None:
                inc.note(self.now(), "standing", f"{len(spared)} trusted member(s) spared an automatic "
                         f"{plan.kind}: left to staff")
                rt.dirty.add(inc.id)
            if plan.kind == "timeout":
                await self._dispatch(rt, replace(plan, authority="confirm", targets=spared,
                                                 reason=plan.reason + " (a trusted member: staff decide)"))
            if not plan.targets:
                return
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
                    self.store.set_timer(rt.guild_id, "slowmode", channel_id, now + revert,
                                         {"previous": previous, "set": int(plan.params.get("seconds", 0))})
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

    # ================================================================ staff verdicts: Clairvoyance learns this server
    async def verdict(self, guild_id: int, incident_id: str, user_id: int, real: bool) -> str:
        """A staff member's judgement of an alert. It feeds per-detector precision (shown in status, reports and the
        health check) and the per-server confidence bar the message and incident models must clear."""
        self.require(guild_id, user_id, "view_incidents")
        rt = self.runtime(guild_id)
        inc = self._incident(rt, incident_id)
        if inc is None:
            raise Refused("No such incident.")
        detectors = sorted(set(inc.counts))
        model = bool(inc.model_bonus) or any(s.get("source") == "model" for s in inc.signals)
        self.store.set_verdict(guild_id, incident_id, "real" if real else "false", user_id, inc.family, detectors, model)
        inc.note(self.now(), "verdict", f"<@{user_id}> marked it {'a real problem' if real else 'a false alarm'}")
        if inc.id in rt.engine.incidents:
            rt.dirty.add(inc.id)
        else:
            self.store.save_incident(guild_id, inc.to_dict())
        cal = self._apply_calibration(rt)
        return (f"Thanks: recorded as {'real' if real else 'a false alarm'}. Clairvoyance has {cal['verdicts']} "
                f"verdict(s) here; its model bar is now {cal['message_bar']:.2f}.")

    def calibration(self, guild_id: int) -> dict:
        """Precision of alerts by detector, and the confidence bars the models must clear on this server: they rise
        (up to 0.95) when alerts involving a model are judged false alarms, and fall back to 0.80 as they're judged
        real. Needs 5 model-involved verdicts before moving at all."""
        verdicts = self.store.verdicts(guild_id, 200)
        real = [v for v in verdicts if v["verdict"] == "real"]
        per = {}
        for v in verdicts:
            for d in v["detectors"]:
                p = per.setdefault(d, [0, 0])
                p[0] += v["verdict"] == "real"
                p[1] += 1
        model = [v for v in verdicts if v["model"]][:60]
        bar = MODEL_BAR
        if len(model) >= 5:
            false_share = sum(1 for v in model if v["verdict"] == "false") / len(model)
            bar = min(MODEL_BAR_MAX, MODEL_BAR + (MODEL_BAR_MAX - MODEL_BAR) * max(0.0, false_share - 0.3) / 0.7)
        return {"verdicts": len(verdicts), "precision": len(real) / len(verdicts) if verdicts else 0.0,
                "detectors": {d: {"real": r, "total": t, "precision": r / t} for d, (r, t) in per.items()},
                "model_verdicts": len(model), "message_bar": round(bar, 3), "incident_bar": round(bar, 3)}

    def _apply_calibration(self, rt: Runtime) -> dict:
        cal = self.calibration(rt.guild_id)
        rt.engine.message_bar, rt.engine.incident_bar = cal["message_bar"], cal["incident_bar"]
        return cal

    # ================================================================ economy holds
    def economy_hold(self, guild_id: int, user_id: int, joined_at: Optional[float] = None) -> Optional[str]:
        """Why money can't move to or from this member right now, or None. Quarantine and probation freeze transfers
        (no laundering through a contained account); during a raid (threat level 3+) so do accounts that joined in
        the last 24 hours."""
        rt = self.runtimes.get(guild_id) or self.runtime(guild_id)
        if rt is None:
            return None
        if user_id in rt.quarantined:
            return "quarantined by security"
        if self.store.on_probation(guild_id, user_id):
            return "on security probation"
        if int(rt.engine.level) >= 3 and joined_at is not None and self.now() - joined_at < 86400:
            return f"joined in the last 24 hours during a raid alert (threat level {int(rt.engine.level)})"
        return None

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

    async def _count_as(self, rt: Runtime, actor_id: int, action: str, target_id: Optional[int]) -> None:
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
        """Save a new version and tell staff what changed (and whether it weakened the defences): a hijacked admin
        account can't quietly switch security off."""
        rt = self.runtime(guild_id)
        old = rt.config if rt is not None else self.config(guild_id)
        version = self.store.save_config(guild_id, {k: v for k, v in config.to_dict().items()}, by, note)
        fresh = SecurityConfig(config.to_dict(), version)
        if rt is not None:
            rt.config = fresh
            rt.engine.cfg = fresh
        changes = old.diff(fresh)
        if changes:
            weakening = weakening_changes(changes) + (["rolled back to an older version"]
                                                      if note.startswith("rollback") else [])
            self._spawn(self._notify("settings", guild_id, {"by": by, "version": version, "note": note,
                                                            "changes": {k: [str(a), str(b)] for k, (a, b) in
                                                                        list(changes.items())[:12]},
                                                            "weakening": weakening}))
        return fresh

    def profile_changed(self, guild_id: int, by: int, changes: dict) -> None:
        """A /setup change to a role that grants authority is posted to the alert channel like a settings change."""
        weakening = [f"the {key} role changed: members of the new role get its authority" for key in changes
                     if key in ("admin", "banker")]
        self._spawn(self._notify("settings", guild_id, {"by": by, "version": "profile", "note": "server profile (/setup)",
                                                        "changes": {k: [str(a), str(b)] for k, (a, b) in changes.items()},
                                                        "weakening": weakening}))

    def _spawn(self, coro) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:                      # no loop (scripts, some tests): nothing to post to anyway
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def set_staff_tier(self, guild_id: int, actor_id: int, role_id: int, tier: int) -> SecurityConfig:
        """Map a role to a clearance tier. Never @everyone or an integration's role; the Admin tier, and Moderator or
        above for a role held by 25+ members, only by the server owner."""
        actor_tier = self.require(guild_id, actor_id, "clearance")
        rt = self.runtime(guild_id)
        model = rt.port.model()
        role = model.role(role_id)
        if role is None:
            raise Refused("That role doesn't exist any more.")
        if role.id == model.id:
            raise Refused("@everyone can't be given a staff tier: every member would get it.")
        if role.managed:
            raise Refused("A role managed by an integration (a bot, boosts) can't be given a staff tier.")
        if tier >= Tier.ADMIN and actor_tier < Tier.OWNER:
            raise Refused("Only the server owner can give a role the Admin tier.")
        if tier >= Tier.MODERATOR and role.member_count >= 25 and actor_tier < Tier.OWNER:
            raise Refused(f"'{role.name}' is held by {role.member_count} members; only the server owner can make such "
                          "a broad role staff.")
        return self.save_config(guild_id, self.config(guild_id).with_tier(role_id, tier or None), actor_id,
                                f"staff {role_id}={tier}")

    async def add_exemption(self, guild_id: int, actor_id: int, kind: str, value: str, checks: str,
                            days: int) -> str:
        """A time-limited exemption. Anti-nuke exemptions for members and roles need an Admin, can't cover the person
        asking, last at most ANTINUKE_EXEMPT_MAX_DAYS, and every exemption is announced."""
        tier = self.require(guild_id, actor_id, "allowlist")
        rt = self.runtime(guild_id)
        antinuke = checks in ("antinuke", "all") and kind in ("user", "role")
        if antinuke:
            if tier < Tier.ADMIN:
                raise Refused("Exempting staff or roles from anti-nuke needs Admin clearance.")
            access = rt.port.access(actor_id)
            if (kind == "user" and int(value) == actor_id) or (kind == "role" and access and int(value) in access[0]):
                raise Refused("You can't exempt yourself (or a role you hold) from anti-nuke.")
            days = min(days, ANTINUKE_EXEMPT_MAX_DAYS)
        expires = self.now() + days * 86400
        self.store.add_allow(guild_id, kind, value, [checks], expires, actor_id, "")
        rt.allows_at = 0
        await self._notify("settings", guild_id, {"by": actor_id, "version": rt.config.version,
                                                  "note": f"exemption: {kind} {value} skips {checks} checks",
                                                  "changes": {"exemption": ["", f"{kind} {value} ({checks}, {days} d)"]},
                                                  "weakening": [f"{kind} {value} exempt from {checks} checks"]
                                                  if checks in ("antinuke", "all") else []})
        return f"{kind} `{value}` skips {checks} checks until <t:{int(expires)}:R>" + (
            f" (anti-nuke exemptions last at most {ANTINUKE_EXEMPT_MAX_DAYS} days)" if antinuke else "")

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
            self._flush_activity(rt)
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
            channel = rt.port.model().channel(timer["target"])
            ours = timer["data"].get("set")
            if channel is not None and ours is not None and channel.slowmode != int(ours):
                log.info("slowmode in %s was changed by staff since; leaving it", timer["target"])
                return
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
        score, findings = posture_audit(model, configured={"alert_channel": cfg["alert_channel"],
                                                           "probation_role": cfg["probation_role"], "mode": cfg["mode"]},
                                        stale_staff=stale, staff_ids=staff)
        from rudhabot.security.posture import Finding
        for detector, p in sorted(self.calibration(guild_id)["detectors"].items()):
            if p["total"] >= 5 and p["precision"] < 0.5:
                findings.append(Finding("info", f"'{detector.replace('_', ' ')}' alerts are mostly false alarms here",
                                        f"Staff judged {p['real']} of {p['total']} real.",
                                        DETECTOR_HINTS.get(detector, "Review its thresholds in /security settings.")))
        return score, findings

    async def incident_report(self, guild_id: int, incident_id: str, use_llm: bool = True,
                              user_id: int = 0) -> tuple[str, str]:
        rt = self.runtime(guild_id)
        inc = self._incident(rt, incident_id)
        if inc is None:
            raise Refused("No such incident.")
        actions = self.store.actions_for_incident(guild_id, incident_id)
        if use_llm and rt.config["llm.summaries"] and self.briefer.enabled:
            return await self.briefer.summary(inc.to_dict(), actions, guild_id, user_id)
        return after_action(inc.to_dict(), actions), "template"

    async def ask(self, guild_id: int, question: str, user_id: int = 0) -> tuple[str, str]:
        status = self.status(guild_id)
        incidents = self.store.incidents(guild_id, limit=8)
        return await self.briefer.answer(question, {k: v for k, v in status.items() if k != "health"}, incidents,
                                         guild_id, user_id)

    async def shutdown(self) -> None:
        for rt in self.runtimes.values():
            try:
                for incident_id in list(rt.dirty):
                    inc = rt.engine.incidents.get(incident_id)
                    if inc is not None:
                        self._flush_incident(rt, inc)
                self.store.set_state(rt.guild_id, "engine", rt.engine.state())
                self.store.touch_seen(rt.guild_id, rt.seen)
                self._flush_activity(rt)
            except Exception:
                log.exception("saving security state on shutdown failed")

    def _flush_activity(self, rt: Runtime) -> None:
        counts, rt.activity = rt.activity, Counter()
        self.store.add_activity(rt.guild_id, counts)


#: settings whose higher value is a looser defence, and booleans whose switching off is
LOOSER_WHEN_HIGHER = ("joins.min_joins", "joins.surge_ratio", "joins.cohort_min", "messages.flood_count",
                      "messages.dup_count", "messages.coord_accounts", "messages.drip_accounts", "messages.mention_limit",
                      "messages.mention_window_limit", "messages.relaxed_factor", "links.delete_score",
                      "files.delete_score", "antinuke.window_s", "antinuke.budget_mod", "antinuke.budget_senior",
                      "antinuke.budget_admin", "levels.decay_minutes")
LOOSER_WHEN_LOWER = ("levels.auto_max", "approvals.expire_minutes")
LOOSER_LISTS_GROW = ("links.allow", "channels.ignored", "channels.relaxed")


def weakening_changes(changes: dict) -> list[str]:
    """Plain-language list of the changes in `changes` (key -> (old, new)) that weaken the defences."""
    out = []
    for key, (old, new) in changes.items():
        if key == "mode" and new == "shadow":
            out.append("automatic responses switched off (shadow mode)")
        elif isinstance(old, bool) and old and not new and key.startswith(("antinuke.", "auto.", "approvals.")):
            out.append(f"{key} switched off")
        elif key in LOOSER_WHEN_HIGHER and isinstance(new, (int, float)) and new > old:
            out.append(f"{key} loosened ({old} -> {new})")
        elif key in LOOSER_WHEN_LOWER and isinstance(new, (int, float)) and new < old:
            out.append(f"{key} loosened ({old} -> {new})")
        elif key in LOOSER_LISTS_GROW and len(new or []) > len(old or []):
            out.append(f"{key}: entries added")
        elif key == "links.deny" and len(new or []) < len(old or []):
            out.append("links.deny: entries removed")
        elif key == "tiers":
            raised = [r for r, t in (new or {}).items() if t > (old or {}).get(r, 0)]
            if raised:
                out.append(f"staff tier raised for {len(raised)} role(s)")
    return out


#: what to adjust when staff verdicts say a detector is noisy on this server
DETECTOR_HINTS = {
    "flood": "Raise messages.flood_count, or mark busy game channels relaxed (channels.relaxed).",
    "duplicate": "Raise messages.dup_count, or mark channels with repeated commands relaxed.",
    "coordinated": "Raise messages.coord_accounts.",
    "slow_drip": "Raise messages.drip_accounts, or allow the site (links.allow) if new members share it legitimately.",
    "channel_surge": "Mark event/announcement channels relaxed or protected.",
    "join_surge": "Raise joins.min_joins or joins.surge_ratio (common around events and partner posts).",
    "join_cohort": "Raise joins.cohort_min.",
    "invite": "Set messages.invites to allow if invites are normal here.",
    "link": "Add trusted sites to links.allow.",
    "model_scam": "Clairvoyance is already raising its bar from your verdicts; you can also turn models.message off.",
    "model_spam": "Clairvoyance is already raising its bar from your verdicts; you can also turn models.message off.",
    "incident_model": "Clairvoyance is already raising its bar from your verdicts; you can also turn models.incident off.",
}
