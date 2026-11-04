"""Security records in the SQLite database: configuration versions, moderation cases, incidents, actions, approvals,
lockdowns, snapshots, probation, quarantine, timers, allowlist entries, staff activity and ban fingerprints.

Every table is scoped by guild id. Message text is never stored here except flagged-message snippets (at most 200
characters, deleted after `retention.snippet_days`); closed incidents are deleted after `retention.incident_days`.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable, Optional

from rudhabot.persistence.database import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_config (guild_id INTEGER, version INTEGER, value TEXT NOT NULL, created REAL,
    created_by INTEGER, note TEXT, PRIMARY KEY (guild_id, version));
CREATE TABLE IF NOT EXISTS sec_cases (guild_id INTEGER, case_no INTEGER, kind TEXT, target_id INTEGER,
    moderator_id INTEGER, reason TEXT, duration_s INTEGER, created REAL, expires REAL, status TEXT, incident_id TEXT,
    extra TEXT, PRIMARY KEY (guild_id, case_no));
CREATE INDEX IF NOT EXISTS sec_cases_target ON sec_cases (guild_id, target_id, case_no);
CREATE TABLE IF NOT EXISTS sec_incidents (guild_id INTEGER, id TEXT, opened REAL, last REAL, closed REAL,
    status TEXT, family TEXT, severity REAL, data TEXT, PRIMARY KEY (guild_id, id));
CREATE INDEX IF NOT EXISTS sec_incidents_last ON sec_incidents (guild_id, last);
CREATE TABLE IF NOT EXISTS sec_snippets (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, incident_id TEXT,
    channel_id INTEGER, message_id INTEGER, author_id INTEGER, text TEXT, created REAL,
    UNIQUE (guild_id, message_id));
CREATE TABLE IF NOT EXISTS sec_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, incident_id TEXT,
    kind TEXT, authority TEXT, status TEXT, targets TEXT, params TEXT, reason TEXT, codes TEXT, requested_by INTEGER,
    approved_by TEXT, created REAL, updated REAL, result TEXT, shadow INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS sec_actions_incident ON sec_actions (guild_id, incident_id);
CREATE TABLE IF NOT EXISTS sec_approvals (token TEXT PRIMARY KEY, guild_id INTEGER, action_id INTEGER,
    requested_by INTEGER, required_tier INTEGER, two_person INTEGER, approvers TEXT, status TEXT, created REAL,
    expires REAL, message TEXT);
CREATE TABLE IF NOT EXISTS sec_lockdowns (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, scope TEXT,
    channels TEXT, status TEXT, created REAL, expires REAL, requested_by INTEGER, approved_by INTEGER, changes TEXT,
    manual TEXT, incident_id TEXT, lifted_at REAL, lifted_by INTEGER, report TEXT);
CREATE TABLE IF NOT EXISTS sec_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, created REAL,
    reason TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS sec_probation (guild_id INTEGER, user_id INTEGER, role_id INTEGER, until REAL, reason TEXT,
    incident_id TEXT, PRIMARY KEY (guild_id, user_id));
CREATE TABLE IF NOT EXISTS sec_quarantine (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, user_id INTEGER,
    roles TEXT, created REAL, incident_id TEXT, status TEXT, released_at REAL, released_by INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS sec_timers (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, kind TEXT,
    target INTEGER, due REAL, data TEXT, UNIQUE (guild_id, kind, target));
CREATE TABLE IF NOT EXISTS sec_allow (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, kind TEXT, value TEXT,
    detectors TEXT, expires REAL, created_by INTEGER, created REAL, note TEXT);
CREATE TABLE IF NOT EXISTS sec_state (guild_id INTEGER, key TEXT, value TEXT, updated REAL,
    PRIMARY KEY (guild_id, key));
CREATE TABLE IF NOT EXISTS sec_seen (guild_id INTEGER, user_id INTEGER, last_seen REAL, PRIMARY KEY (guild_id, user_id));
CREATE TABLE IF NOT EXISTS sec_verdicts (guild_id INTEGER, incident_id TEXT, verdict TEXT, by INTEGER, created REAL,
    family TEXT, detectors TEXT, model INTEGER, PRIMARY KEY (guild_id, incident_id));
CREATE TABLE IF NOT EXISTS sec_fingerprints (guild_id INTEGER, user_id INTEGER, avatar_key TEXT, name_stem TEXT,
    created REAL, PRIMARY KEY (guild_id, user_id));
"""


def _j(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _l(text: Optional[str], default: Any = None) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


class SecurityStore:
    def __init__(self, db: Database, clock=None):
        self.db = db
        self._now = (lambda: clock.now().timestamp()) if clock is not None else time.time
        with db.transaction() as cur:
            for statement in _SCHEMA.strip().split(";"):
                if statement.strip():
                    cur.execute(statement)

    def now(self) -> float:
        return self._now()

    # ================================================================ configuration (versioned)
    def config(self, guild_id: int) -> tuple[dict, int]:
        row = self.db.query("SELECT value, version FROM sec_config WHERE guild_id=? ORDER BY version DESC LIMIT 1",
                            (guild_id,))
        return (_l(row[0][0], {}), row[0][1]) if row else ({}, 0)

    def save_config(self, guild_id: int, values: dict, by: int, note: str = "") -> int:
        with self.db.transaction() as cur:
            cur.execute("SELECT COALESCE(MAX(version), 0) FROM sec_config WHERE guild_id=?", (guild_id,))
            version = cur.fetchone()[0] + 1
            cur.execute("INSERT INTO sec_config VALUES (?, ?, ?, ?, ?, ?)",
                        (guild_id, version, _j(values), self.now(), by, note[:200]))
        return version

    def config_history(self, guild_id: int, limit: int = 10) -> list[dict]:
        rows = self.db.query("SELECT version, created, created_by, note FROM sec_config WHERE guild_id=? "
                             "ORDER BY version DESC LIMIT ?", (guild_id, limit))
        return [{"version": v, "created": c, "by": b, "note": n} for v, c, b, n in rows]

    def config_version(self, guild_id: int, version: int) -> Optional[dict]:
        row = self.db.query("SELECT value FROM sec_config WHERE guild_id=? AND version=?", (guild_id, version))
        return _l(row[0][0], {}) if row else None

    # ================================================================ cases (the moderation log)
    def add_case(self, guild_id: int, kind: str, target_id: int, moderator_id: int, reason: str,
                 duration_s: Optional[int] = None, incident_id: Optional[str] = None, extra: Optional[dict] = None,
                 status: str = "active") -> dict:
        now = self.now()
        expires = now + duration_s if duration_s else None
        with self.db.transaction() as cur:
            cur.execute("SELECT COALESCE(MAX(case_no), 0) FROM sec_cases WHERE guild_id=?", (guild_id,))
            number = cur.fetchone()[0] + 1
            cur.execute("INSERT INTO sec_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (guild_id, number, kind, target_id, moderator_id, (reason or "")[:500], duration_s, now,
                         expires, status, incident_id, _j(extra or {})))
        return self.case(guild_id, number)

    def case(self, guild_id: int, number: int) -> Optional[dict]:
        rows = self.db.query("SELECT * FROM sec_cases WHERE guild_id=? AND case_no=?", (guild_id, number))
        return self._case(rows[0]) if rows else None

    @staticmethod
    def _case(row) -> dict:
        keys = ("guild_id", "case_no", "kind", "target_id", "moderator_id", "reason", "duration_s", "created",
                "expires", "status", "incident_id", "extra")
        out = dict(zip(keys, row))
        out["extra"] = _l(out["extra"], {})
        return out

    def cases_for(self, guild_id: int, target_id: int, limit: int = 20) -> list[dict]:
        rows = self.db.query("SELECT * FROM sec_cases WHERE guild_id=? AND target_id=? ORDER BY case_no DESC LIMIT ?",
                             (guild_id, target_id, limit))
        return [self._case(r) for r in rows]

    def recent_cases(self, guild_id: int, limit: int = 20) -> list[dict]:
        rows = self.db.query("SELECT * FROM sec_cases WHERE guild_id=? ORDER BY case_no DESC LIMIT ?", (guild_id, limit))
        return [self._case(r) for r in rows]

    def update_case(self, guild_id: int, number: int, **fields) -> Optional[dict]:
        allowed = {"reason", "status", "extra"}
        sets = {k: (_j(v) if k == "extra" else v) for k, v in fields.items() if k in allowed}
        if not sets:
            return self.case(guild_id, number)
        self.db.execute(f"UPDATE sec_cases SET {', '.join(f'{k}=?' for k in sets)} WHERE guild_id=? AND case_no=?",
                        (*sets.values(), guild_id, number))
        return self.case(guild_id, number)

    def delete_case(self, guild_id: int, number: int) -> bool:
        before = self.case(guild_id, number)
        self.db.execute("DELETE FROM sec_cases WHERE guild_id=? AND case_no=?", (guild_id, number))
        return before is not None

    # ================================================================ incidents
    def save_incident(self, guild_id: int, incident: dict) -> None:
        data = dict(incident)
        snippets = data.pop("snippets", []) or []
        with self.db.transaction() as cur:
            cur.execute("INSERT INTO sec_incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id, id) DO UPDATE "
                        "SET last=excluded.last, closed=excluded.closed, status=excluded.status, "
                        "severity=excluded.severity, data=excluded.data",
                        (guild_id, data["id"], data["opened"], data["last"], data.get("closed_at"), data["status"],
                         data["family"], data.get("severity", 0.0), _j(data)))
            for s in snippets:
                cur.execute("INSERT OR IGNORE INTO sec_snippets (guild_id, incident_id, channel_id, message_id, "
                            "author_id, text, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (guild_id, data["id"], s.get("channel"), s.get("message"), s.get("author"),
                             (s.get("text") or "")[:200], s.get("ts") or self.now()))

    def incident(self, guild_id: int, incident_id: str) -> Optional[dict]:
        rows = self.db.query("SELECT data FROM sec_incidents WHERE guild_id=? AND id=?", (guild_id, incident_id))
        if not rows:
            return None
        data = _l(rows[0][0], {})
        data["snippets"] = self.snippets(guild_id, incident_id)
        return data

    def snippets(self, guild_id: int, incident_id: str) -> list[dict]:
        rows = self.db.query("SELECT channel_id, message_id, author_id, text, created FROM sec_snippets "
                             "WHERE guild_id=? AND incident_id=? ORDER BY id", (guild_id, incident_id))
        return [{"channel": c, "message": m, "author": a, "text": t, "ts": ts} for c, m, a, t, ts in rows]

    def incidents(self, guild_id: int, status: Optional[str] = None, limit: int = 20) -> list[dict]:
        if status:
            rows = self.db.query("SELECT data FROM sec_incidents WHERE guild_id=? AND status=? ORDER BY last DESC "
                                 "LIMIT ?", (guild_id, status, limit))
        else:
            rows = self.db.query("SELECT data FROM sec_incidents WHERE guild_id=? ORDER BY last DESC LIMIT ?",
                                 (guild_id, limit))
        return [_l(r[0], {}) for r in rows]

    def delete_incident(self, guild_id: int, incident_id: str) -> None:
        with self.db.transaction() as cur:
            cur.execute("DELETE FROM sec_incidents WHERE guild_id=? AND id=?", (guild_id, incident_id))
            cur.execute("DELETE FROM sec_snippets WHERE guild_id=? AND incident_id=?", (guild_id, incident_id))

    def redact_snippets(self, guild_id: int, incident_id: str) -> int:
        with self.db.transaction() as cur:
            cur.execute("DELETE FROM sec_snippets WHERE guild_id=? AND incident_id=?", (guild_id, incident_id))
            return cur.rowcount

    # ================================================================ actions
    def add_action(self, guild_id: int, plan: dict, status: str, requested_by: Optional[int] = None,
                   shadow: bool = False) -> int:
        now = self.now()
        return self.db.execute(
            "INSERT INTO sec_actions (guild_id, incident_id, kind, authority, status, targets, params, reason, codes, "
            "requested_by, approved_by, created, updated, result, shadow) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, plan.get("incident_id"), plan["kind"], plan["authority"], status, _j(plan.get("targets", [])),
             _j(plan.get("params", {})), plan.get("reason", "")[:500], _j(plan.get("codes", [])), requested_by,
             _j([]), now, now, None, int(shadow)))

    def update_action(self, action_id: int, status: str, result: Optional[dict] = None,
                      approved_by: Optional[list] = None) -> None:
        sets, params = ["status=?", "updated=?"], [status, self.now()]
        if result is not None:
            sets.append("result=?")
            params.append(_j(result))
        if approved_by is not None:
            sets.append("approved_by=?")
            params.append(_j(approved_by))
        self.db.execute(f"UPDATE sec_actions SET {', '.join(sets)} WHERE id=?", (*params, action_id))

    def action(self, action_id: int) -> Optional[dict]:
        rows = self.db.query("SELECT id, guild_id, incident_id, kind, authority, status, targets, params, reason, codes, "
                             "requested_by, approved_by, created, updated, result, shadow FROM sec_actions WHERE id=?",
                             (action_id,))
        return self._action(rows[0]) if rows else None

    @staticmethod
    def _action(r) -> dict:
        return {"id": r[0], "guild_id": r[1], "incident_id": r[2], "kind": r[3], "authority": r[4], "status": r[5],
                "targets": _l(r[6], []), "params": _l(r[7], {}), "reason": r[8], "codes": _l(r[9], []),
                "requested_by": r[10], "approved_by": _l(r[11], []), "created": r[12], "updated": r[13],
                "result": _l(r[14], None), "shadow": bool(r[15])}

    def actions_for_incident(self, guild_id: int, incident_id: str) -> list[dict]:
        rows = self.db.query("SELECT id, guild_id, incident_id, kind, authority, status, targets, params, reason, codes, "
                             "requested_by, approved_by, created, updated, result, shadow FROM sec_actions "
                             "WHERE guild_id=? AND incident_id=? ORDER BY id", (guild_id, incident_id))
        return [self._action(r) for r in rows]

    def action_counts(self, guild_id: int, since: float) -> dict:
        rows = self.db.query("SELECT status, COUNT(*) FROM sec_actions WHERE guild_id=? AND created>=? GROUP BY status",
                             (guild_id, since))
        return dict(rows)

    # ================================================================ approvals (one-use tokens)
    def add_approval(self, token: str, guild_id: int, action_id: int, requested_by: Optional[int], required_tier: int,
                     two_person: bool, expires: float) -> None:
        self.db.execute("INSERT INTO sec_approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (token, guild_id, action_id, requested_by, required_tier, int(two_person), _j([]), "pending",
                         self.now(), expires, None))

    def approval(self, token: str) -> Optional[dict]:
        rows = self.db.query("SELECT token, guild_id, action_id, requested_by, required_tier, two_person, approvers, "
                             "status, created, expires, message FROM sec_approvals WHERE token=?", (token,))
        if not rows:
            return None
        r = rows[0]
        return {"token": r[0], "guild_id": r[1], "action_id": r[2], "requested_by": r[3], "required_tier": r[4],
                "two_person": bool(r[5]), "approvers": _l(r[6], []), "status": r[7], "created": r[8], "expires": r[9],
                "message": _l(r[10], None)}

    def claim_approval(self, token: str, expected_status: str, new_status: str, approvers: list) -> bool:
        """Compare-and-set, so two moderators clicking at once can't both carry out an action."""
        with self.db.transaction() as cur:
            cur.execute("UPDATE sec_approvals SET status=?, approvers=? WHERE token=? AND status=?",
                        (new_status, _j(approvers), token, expected_status))
            return cur.rowcount == 1

    def set_approval_message(self, token: str, channel_id: int, message_id: int) -> None:
        self.db.execute("UPDATE sec_approvals SET message=? WHERE token=?", (_j([channel_id, message_id]), token))

    def pending_approvals(self, guild_id: int) -> list[dict]:
        rows = self.db.query("SELECT token FROM sec_approvals WHERE guild_id=? AND status IN ('pending', 'half') "
                             "ORDER BY created", (guild_id,))
        return [self.approval(t) for (t,) in rows]

    def expire_approvals(self, now: float) -> list[dict]:
        rows = self.db.query("SELECT token FROM sec_approvals WHERE status IN ('pending', 'half') AND expires<?", (now,))
        out = []
        for (token,) in rows:
            a = self.approval(token)
            if a and self.claim_approval(token, a["status"], "expired", a["approvers"]):
                out.append(a)
        return out

    # ================================================================ lockdowns
    def add_lockdown(self, guild_id: int, scope: str, channels: list[int], expires: float, requested_by: int,
                     approved_by: Optional[int], changes: list[dict], manual: list[str],
                     incident_id: Optional[str]) -> int:
        return self.db.execute(
            "INSERT INTO sec_lockdowns (guild_id, scope, channels, status, created, expires, requested_by, approved_by, "
            "changes, manual, incident_id) VALUES (?, ?, ?, 'applying', ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, scope, _j(channels), self.now(), expires, requested_by, approved_by, _j(changes), _j(manual),
             incident_id))

    def update_lockdown(self, lockdown_id: int, **fields) -> None:
        allowed = {"status", "changes", "manual", "expires", "lifted_at", "lifted_by", "report"}
        sets = {k: (_j(v) if k in ("changes", "manual", "report") else v) for k, v in fields.items() if k in allowed}
        self.db.execute(f"UPDATE sec_lockdowns SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                        (*sets.values(), lockdown_id))

    def lockdown(self, lockdown_id: int) -> Optional[dict]:
        rows = self.db.query("SELECT id, guild_id, scope, channels, status, created, expires, requested_by, approved_by, "
                             "changes, manual, incident_id, lifted_at, lifted_by, report FROM sec_lockdowns WHERE id=?",
                             (lockdown_id,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r[0], "guild_id": r[1], "scope": r[2], "channels": _l(r[3], []), "status": r[4], "created": r[5],
                "expires": r[6], "requested_by": r[7], "approved_by": r[8], "changes": _l(r[9], []),
                "manual": _l(r[10], []), "incident_id": r[11], "lifted_at": r[12], "lifted_by": r[13],
                "report": _l(r[14], None)}

    def active_lockdowns(self, guild_id: Optional[int] = None) -> list[dict]:
        if guild_id is None:
            rows = self.db.query("SELECT id FROM sec_lockdowns WHERE status IN ('applying', 'active', 'lifting')")
        else:
            rows = self.db.query("SELECT id FROM sec_lockdowns WHERE guild_id=? AND status IN ('applying', 'active', "
                                 "'lifting')", (guild_id,))
        return [self.lockdown(i) for (i,) in rows]

    # ================================================================ snapshots
    def add_snapshot(self, guild_id: int, data: dict, reason: str, keep: int = 30) -> int:
        snap_id = self.db.execute("INSERT INTO sec_snapshots (guild_id, created, reason, data) VALUES (?, ?, ?, ?)",
                                  (guild_id, self.now(), reason[:100], _j(data)))
        self.db.execute("DELETE FROM sec_snapshots WHERE guild_id=? AND id NOT IN (SELECT id FROM sec_snapshots "
                        "WHERE guild_id=? ORDER BY id DESC LIMIT ?)", (guild_id, guild_id, keep))
        return snap_id

    def snapshot(self, guild_id: int, snap_id: Optional[int] = None) -> Optional[dict]:
        if snap_id is None:
            rows = self.db.query("SELECT id, created, reason, data FROM sec_snapshots WHERE guild_id=? ORDER BY id DESC "
                                 "LIMIT 1", (guild_id,))
        else:
            rows = self.db.query("SELECT id, created, reason, data FROM sec_snapshots WHERE guild_id=? AND id=?",
                                 (guild_id, snap_id))
        if not rows:
            return None
        i, created, reason, data = rows[0]
        return {"id": i, "created": created, "reason": reason, "data": _l(data, {})}

    def snapshots(self, guild_id: int, limit: int = 10) -> list[dict]:
        rows = self.db.query("SELECT id, created, reason FROM sec_snapshots WHERE guild_id=? ORDER BY id DESC LIMIT ?",
                             (guild_id, limit))
        return [{"id": i, "created": c, "reason": r} for i, c, r in rows]

    # ================================================================ probation, quarantine, timers
    def set_probation(self, guild_id: int, user_id: int, role_id: int, until: float, reason: str,
                      incident_id: Optional[str]) -> None:
        self.db.execute("INSERT INTO sec_probation VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE "
                        "SET until=MAX(until, excluded.until), reason=excluded.reason",
                        (guild_id, user_id, role_id, until, reason[:200], incident_id))

    def probation(self, guild_id: int) -> list[dict]:
        rows = self.db.query("SELECT user_id, role_id, until, reason, incident_id FROM sec_probation WHERE guild_id=?",
                             (guild_id,))
        return [{"user_id": u, "role_id": r, "until": t, "reason": why, "incident_id": i} for u, r, t, why, i in rows]

    def due_probation(self, now: float) -> list[dict]:
        rows = self.db.query("SELECT guild_id, user_id, role_id FROM sec_probation WHERE until<=?", (now,))
        return [{"guild_id": g, "user_id": u, "role_id": r} for g, u, r in rows]

    def end_probation(self, guild_id: int, user_id: int) -> None:
        self.db.execute("DELETE FROM sec_probation WHERE guild_id=? AND user_id=?", (guild_id, user_id))

    def add_quarantine(self, guild_id: int, user_id: int, roles: list[int], incident_id: Optional[str],
                       reason: str) -> int:
        return self.db.execute("INSERT INTO sec_quarantine (guild_id, user_id, roles, created, incident_id, status, "
                               "reason) VALUES (?, ?, ?, ?, ?, 'active', ?)",
                               (guild_id, user_id, _j(roles), self.now(), incident_id, reason[:300]))

    def quarantined(self, guild_id: int, user_id: Optional[int] = None) -> list[dict]:
        sql = "SELECT id, user_id, roles, created, incident_id, reason FROM sec_quarantine WHERE guild_id=? AND status='active'"
        params: tuple = (guild_id,)
        if user_id is not None:
            sql += " AND user_id=?"
            params += (user_id,)
        rows = self.db.query(sql, params)
        return [{"id": i, "user_id": u, "roles": _l(r, []), "created": c, "incident_id": inc, "reason": why}
                for i, u, r, c, inc, why in rows]

    def release_quarantine(self, quarantine_id: int, by: int) -> None:
        self.db.execute("UPDATE sec_quarantine SET status='released', released_at=?, released_by=? WHERE id=?",
                        (self.now(), by, quarantine_id))

    def set_timer(self, guild_id: int, kind: str, target: int, due: float, data: Optional[dict] = None) -> None:
        self.db.execute("INSERT INTO sec_timers (guild_id, kind, target, due, data) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(guild_id, kind, target) DO UPDATE SET due=excluded.due, data=excluded.data",
                        (guild_id, kind, target, due, _j(data or {})))

    def due_timers(self, now: float) -> list[dict]:
        rows = self.db.query("SELECT id, guild_id, kind, target, due, data FROM sec_timers WHERE due<=? ORDER BY due",
                             (now,))
        return [{"id": i, "guild_id": g, "kind": k, "target": t, "due": d, "data": _l(x, {})} for i, g, k, t, d, x in rows]

    def timers(self, guild_id: int) -> list[dict]:
        rows = self.db.query("SELECT id, kind, target, due, data FROM sec_timers WHERE guild_id=? ORDER BY due",
                             (guild_id,))
        return [{"id": i, "kind": k, "target": t, "due": d, "data": _l(x, {})} for i, k, t, d, x in rows]

    def clear_timer(self, timer_id: int) -> None:
        self.db.execute("DELETE FROM sec_timers WHERE id=?", (timer_id,))

    # ================================================================ allowlist (with expiry)
    def add_allow(self, guild_id: int, kind: str, value: str, detectors: Iterable[str], expires: float, by: int,
                  note: str = "") -> int:
        return self.db.execute("INSERT INTO sec_allow (guild_id, kind, value, detectors, expires, created_by, created, "
                               "note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (guild_id, kind, str(value).lower(), _j(sorted(set(detectors))), expires, by, self.now(),
                                note[:200]))

    def allows(self, guild_id: int, now: Optional[float] = None) -> list[dict]:
        now = self.now() if now is None else now
        rows = self.db.query("SELECT id, kind, value, detectors, expires, created_by, created, note FROM sec_allow "
                             "WHERE guild_id=? AND expires>? ORDER BY expires", (guild_id, now))
        return [{"id": i, "kind": k, "value": v, "detectors": _l(d, []), "expires": e, "by": b, "created": c, "note": n}
                for i, k, v, d, e, b, c, n in rows]

    def remove_allow(self, guild_id: int, allow_id: int) -> bool:
        with self.db.transaction() as cur:
            cur.execute("DELETE FROM sec_allow WHERE guild_id=? AND id=?", (guild_id, allow_id))
            return cur.rowcount == 1

    # ================================================================ engine state, staff activity, fingerprints
    def state(self, guild_id: int, key: str) -> Any:
        rows = self.db.query("SELECT value FROM sec_state WHERE guild_id=? AND key=?", (guild_id, key))
        return _l(rows[0][0]) if rows else None

    def set_state(self, guild_id: int, key: str, value: Any) -> None:
        self.db.execute("INSERT INTO sec_state VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, key) DO UPDATE SET "
                        "value=excluded.value, updated=excluded.updated", (guild_id, key, _j(value), self.now()))

    def touch_seen(self, guild_id: int, seen: dict[int, float]) -> None:
        if not seen:
            return
        with self.db.transaction() as cur:
            cur.executemany("INSERT INTO sec_seen VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET "
                            "last_seen=MAX(last_seen, excluded.last_seen)",
                            [(guild_id, u, ts) for u, ts in seen.items()])

    def last_seen(self, guild_id: int, user_ids: Iterable[int]) -> dict[int, float]:
        ids = list(user_ids)
        if not ids:
            return {}
        out = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = self.db.query(f"SELECT user_id, last_seen FROM sec_seen WHERE guild_id=? AND user_id IN "
                                 f"({','.join('?' * len(chunk))})", (guild_id, *chunk))
            out.update(dict(rows))
        return out

    def add_fingerprint(self, guild_id: int, user_id: int, avatar_key: Optional[str], name_stem: str) -> None:
        self.db.execute("INSERT INTO sec_fingerprints VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE "
                        "SET avatar_key=excluded.avatar_key, name_stem=excluded.name_stem",
                        (guild_id, user_id, avatar_key, name_stem, self.now()))

    def fingerprints(self, guild_id: int) -> list[tuple[int, Optional[str], str]]:
        return self.db.query("SELECT user_id, avatar_key, name_stem FROM sec_fingerprints WHERE guild_id=?", (guild_id,))

    def remove_fingerprint(self, guild_id: int, user_id: int) -> None:
        self.db.execute("DELETE FROM sec_fingerprints WHERE guild_id=? AND user_id=?", (guild_id, user_id))

    # ================================================================ staff verdicts (Clairvoyance learns from them)
    def set_verdict(self, guild_id: int, incident_id: str, verdict: str, by: int, family: str,
                    detectors: list[str], model: bool) -> None:
        self.db.execute("INSERT INTO sec_verdicts VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id, incident_id) "
                        "DO UPDATE SET verdict=excluded.verdict, by=excluded.by, created=excluded.created",
                        (guild_id, incident_id, verdict, by, self.now(), family, _j(sorted(detectors)), int(model)))

    def verdicts(self, guild_id: int, limit: int = 200) -> list[dict]:
        rows = self.db.query("SELECT incident_id, verdict, by, created, family, detectors, model FROM sec_verdicts "
                             "WHERE guild_id=? ORDER BY created DESC LIMIT ?", (guild_id, limit))
        return [{"incident_id": i, "verdict": v, "by": b, "created": c, "family": f, "detectors": _l(d, []),
                 "model": bool(m)} for i, v, b, c, f, d, m in rows]

    # ================================================================ retention
    def sweep(self, guild_id: int, snippet_days: int, incident_days: int, fingerprint_days: int = 365) -> dict:
        now = self.now()
        with self.db.transaction() as cur:
            cur.execute("DELETE FROM sec_snippets WHERE guild_id=? AND created<?", (guild_id, now - snippet_days * 86400))
            snippets = cur.rowcount
            cur.execute("SELECT id FROM sec_incidents WHERE guild_id=? AND status='closed' AND COALESCE(closed, last)<?",
                        (guild_id, now - incident_days * 86400))
            old = [r[0] for r in cur.fetchall()]
            for incident_id in old:
                cur.execute("DELETE FROM sec_incidents WHERE guild_id=? AND id=?", (guild_id, incident_id))
                cur.execute("DELETE FROM sec_snippets WHERE guild_id=? AND incident_id=?", (guild_id, incident_id))
                cur.execute("DELETE FROM sec_actions WHERE guild_id=? AND incident_id=?", (guild_id, incident_id))
            cur.execute("DELETE FROM sec_approvals WHERE guild_id=? AND status NOT IN ('pending', 'half') AND created<?",
                        (guild_id, now - 30 * 86400))
            cur.execute("DELETE FROM sec_allow WHERE guild_id=? AND expires<?", (guild_id, now - 30 * 86400))
            cur.execute("DELETE FROM sec_fingerprints WHERE guild_id=? AND created<?",
                        (guild_id, now - fingerprint_days * 86400))
        return {"snippets": snippets, "incidents": len(old)}
