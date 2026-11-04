"""The State of the Realm: daily economy snapshots and a weekly report that reads every system at once.

Daily (after the day ends, in the ledger's own clock): money supply (wallets + banks), credit owed, loans owed,
accounts holding money, the day's transactions and active accounts, and where money came from and went (net change
per reason, from the audit ledger). Kept for 400 days in SQLite (`economy_daily`), or JSON with RUDHABOT_STORAGE=json.

Weekly (Clairvoyance-branded, posted to the reports channel, or on demand with `/clairvoyance report`): the economy
trend from the snapshots, security (incidents, staff verdicts, what shadow mode would have done), community activity
(daily message counts only), Cloudfall results, Clairvoyance's health and what it has learned, birthdays ahead.
Read-only: the report changes nothing.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import date, timedelta, timezone
from typing import Optional

log = logging.getLogger("rudhabot.realm")
KEEP_DAYS = 400
TOP_REASONS = 5


class RealmService:
    def __init__(self, store, clock, services=None):
        """`services`: the container, bound once it exists (the report looks at almost everything)."""
        self._store, self._clock, self._s = store, clock, services

    def bind(self, services) -> None:
        self._s = services

    # ------------------------------------------------------------------ storage
    def _data(self) -> dict:
        data = self._store.load() or {}
        data.setdefault("days", {})
        return data

    def snapshots(self, days: int = 14) -> list[dict]:
        stored = self._data()["days"]
        return [stored[d] for d in sorted(stored)[-days:]]

    def last_report_week(self) -> Optional[str]:
        return self._data().get("last_report")

    def mark_reported(self, week: str) -> None:
        data = self._data()
        data["last_report"] = week
        self._store.save(data)

    # ------------------------------------------------------------------ daily snapshot
    def yesterday(self) -> date:
        return self._clock.now().date() - timedelta(days=1)

    def snapshot_due(self) -> Optional[date]:
        day = self.yesterday()
        return None if day.isoformat() in self._data()["days"] else day

    def take_snapshot(self, day: Optional[date] = None) -> dict:
        """Measure `day` (default: yesterday) and keep it. Balances are as of now (taken just after the day ends)."""
        day = day or self.yesterday()
        s = self._s
        accounts = s.users.all()
        supply = sum(int(a.get("wallet", 0)) + int(a.get("bank", 0)) for a in accounts.values())
        holders = sum(1 for a in accounts.values() if int(a.get("wallet", 0)) + int(a.get("bank", 0)) > 0)
        loans = 0
        for uid in accounts:
            loan = s.loans.active_loan(uid)
            if loan is not None:
                loans += loan.total_due()
        start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
        net, entries, users = Counter(), 0, set()
        ledger = s.audit.ledger
        source = ledger.since(start, end) if hasattr(ledger, "since") else \
            (e for e in ledger.read() if start <= e.get("ts", "") < end)
        for entry in source:
            if entry.get("account") not in ("wallet", "bank"):
                continue
            entries += 1
            users.add(entry.get("user"))
            net[_reason_label(entry.get("reason"))] += entry.get("delta", 0)
        snap = {"day": start, "supply": supply, "holders": holders, "credit_owed": s.bank.credit_owed(),
                "loans_owed": loans, "transactions": entries, "active_accounts": len(users),
                "tcg_results": sum(p.get("wins", 0) + p.get("losses", 0) + p.get("draws", 0)
                                   for p in s.tcg.repo.all_profiles().values()),
                "net_by_reason": {k: round(v) for k, v in net.most_common() if round(v)}}
        data = self._data()
        data["days"][start] = snap
        for old in sorted(data["days"])[:-KEEP_DAYS]:
            del data["days"][old]
        self._store.save(data)
        return snap

    # ------------------------------------------------------------------ weekly report
    def week_label(self) -> str:
        year, week, _ = self._clock.now().date().isocalendar()
        return f"{year}-W{week:02d}"

    def report(self, guild_id: Optional[int]) -> dict:
        s = self._s
        snaps = self.snapshots(8)
        out: dict = {"week": self.week_label(), "days": len(snaps)}
        if snaps:
            first, last = snaps[0], snaps[-1]
            week = snaps[-7:]
            flows = Counter()
            for snap in week:
                flows.update(snap.get("net_by_reason", {}))
            out["economy"] = {
                "supply": last["supply"], "supply_change": last["supply"] - first["supply"] if len(snaps) > 1 else None,
                "credit_owed": last["credit_owed"], "loans_owed": last["loans_owed"], "holders": last["holders"],
                "transactions": sum(x["transactions"] for x in week),
                "busiest": max(week, key=lambda x: x["transactions"])["day"],
                "sources": [(k, v) for k, v in flows.most_common() if v > 0][:TOP_REASONS],
                "sinks": [(k, v) for k, v in sorted(flows.items(), key=lambda kv: kv[1]) if v < 0][:TOP_REASONS],
                "tcg_results": (last.get("tcg_results", 0) - week[0].get("tcg_results", 0)) if len(week) > 1 else None,
            }
        since = self._clock.now(timezone.utc).timestamp() - 7 * 86400
        if guild_id:
            sec = s.security
            store = sec.store
            opened = store.db.query("SELECT family, COUNT(*) FROM sec_incidents WHERE guild_id=? AND opened>=? "
                                    "GROUP BY family", (guild_id, since))
            actions = store.db.query("SELECT kind, COUNT(*) FROM sec_actions WHERE guild_id=? AND created>=? AND "
                                     "status='shadow' GROUP BY kind", (guild_id, since))
            done = store.db.query("SELECT kind, COUNT(*) FROM sec_actions WHERE guild_id=? AND created>=? AND "
                                  "status IN ('confirmed', 'partial') GROUP BY kind", (guild_id, since))
            cal = sec.calibration(guild_id)
            rt = sec.runtime(guild_id)
            out["security"] = {
                "incidents": dict(opened), "shadow": dict(actions), "done": dict(done),
                "verdicts": cal.get("verdicts", 0), "precision": cal.get("precision"),
                "message_bar": cal.get("message_bar"), "level": int(rt.engine.level) if rt else 1,
                "mode": sec.config(guild_id)["mode"], "spared_trusted": sec.health.get("spared_trusted", 0),
            }
            since_day = time.strftime("%Y-%m-%d", time.gmtime(since))
            days = store.activity_totals(guild_id, since_day)
            posters = store.db.query("SELECT COUNT(DISTINCT user_id), COALESCE(SUM(messages), 0) FROM member_activity "
                                     "WHERE guild_id=? AND day>=?", (guild_id, since_day))[0]
            out["community"] = {"members": posters[0], "messages": posters[1],
                                "busiest": max(days, key=lambda d: d["messages"])["day"] if days else None}
        router = s.intents
        reviews = s.reviews
        out["clairvoyance"] = {
            "cloudfall": s.clairvoyance.status().get("ready", False),
            "intent": dict(getattr(router, "outcomes", {}) or {}),
            "anomaly_bar": reviews.detector.threshold,
            "signals_open": len(reviews.signals("open", 1000)),
        }
        out["birthdays"] = [(u.user_id, u.on.isoformat(), u.days_until) for u in s.birthdays.upcoming(10)
                            if u.days_until <= 7]
        return out


def _reason_label(reason: Optional[str]) -> str:
    """'/work' and '/crypto buy (via assistant)' -> the command; 'services/bank.py:BankService.apply_daily_interest'
    -> 'BankService.apply_daily_interest'; buttons keep their label."""
    text = (reason or "other").strip()
    if text.startswith("/"):
        return text.split(" (")[0]
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return text[:48] or "other"
