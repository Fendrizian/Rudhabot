"""The economy audit trail: every change to anyone's money is recorded, wherever it comes from.

How completeness is guaranteed
  Money lives in a few repositories (wallet/bank in `UserRepository`; credit and shares in the bank and stock
  services). Each of them reports every balance change to `AuditService.record()` at the moment it is written, so no
  command, button, background job or future feature can move money without leaving an entry.

How each entry is labelled (most specific wins)
  1. an explicit `reason=` given by the code that moved the money (e.g. "credit card statement interest");
  2. the interaction that caused it: `audit_context()` is entered automatically for every slash command (the command
     tree) and every button/select (ServiceView), so a /work payout is labelled "/work" and a blackjack payout
     "Blackjack (button)";
  3. otherwise the code location that made the change (e.g. "services/loans.py:LoanService.auto_collect"),
     which covers background jobs.

Entries are appended to the local ledger (`ledger.jsonl`) and queued for the audit channel, where they are posted in
batched embeds to stay well inside Discord's rate limits. Listeners (the Clairvoyance anomaly monitor) receive every
entry as it is recorded.
"""
from __future__ import annotations

import contextvars
import itertools
import logging
import os
import sys
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from rudhabot.clock import Clock
from rudhabot.persistence.ledger import Ledger

log = logging.getLogger("rudhabot.audit")


@dataclass(frozen=True)
class AuditContext:
    label: str                 # "/work", "Blackjack (button)", "loan auto-payment", ...
    actor: Optional[int]       # the Discord user who caused it (None for background jobs)
    source: str                # "command" | "component" | "task"


_context: contextvars.ContextVar[Optional[AuditContext]] = contextvars.ContextVar("rudhabot_audit", default=None)


@contextmanager
def audit_context(label: str, actor: Optional[int] = None, source: str = "task") -> Iterator[None]:
    token = _context.set(AuditContext(label, actor, source))
    try:
        yield
    finally:
        _context.reset(token)


def set_context(label: str, actor: Optional[int], source: str) -> None:
    """Label everything that happens in the current task (used at the start of an interaction)."""
    _context.set(AuditContext(label, actor, source))


def current_context() -> Optional[AuditContext]:
    return _context.get()


_PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKIP = (os.path.join("persistence", ""), os.path.join("services", "audit.py"), os.path.join("services", "bank.py"),
         os.path.join("services", "stocks.py"))


def _caller_location() -> str:
    """The first frame outside the money plumbing: preferably Rudhabot's own code, else whoever called it."""
    frame = sys._getframe(2)
    fallback = None
    while frame is not None:
        path = frame.f_code.co_filename
        plumbing = path.startswith(_PACKAGE_DIR) and any(part in path for part in _SKIP)
        if not plumbing and "contextlib" not in path:
            owner = frame.f_locals.get("self")
            name = f"{type(owner).__name__}.{frame.f_code.co_name}" if owner is not None else frame.f_code.co_name
            if path.startswith(_PACKAGE_DIR):
                return f"{os.path.relpath(path, _PACKAGE_DIR)}:{name}"
            fallback = fallback or f"{os.path.basename(path)}:{name}"
        frame = frame.f_back
    return fallback or "unknown"


RECENT_PER_USER = 400          # what /bank history and the finance insights look at
RECENT_GLOBAL = 1000           # what /audit looks at


class AuditService:
    def __init__(self, ledger: Ledger, clock: Clock, channel_id: Optional[int] = None, post_to_channel: bool = True):
        self.ledger, self.clock = ledger, clock
        self.channel_id = channel_id if post_to_channel else None
        self.pending: deque = deque(maxlen=5000)          # waiting to be posted to the audit channel
        self.listeners: list[Callable[[dict], None]] = []
        self._ids = itertools.count(1)
        self.dropped = 0
        # recent entries in memory, so /balance and /bank history don't re-read the whole ledger file (which grows to
        # 50 MB before it rotates). Filled from the ledger once, on first use; kept current by record().
        self._recent_all: deque = deque(maxlen=RECENT_GLOBAL)
        self._recent_by_user: dict[str, deque] = defaultdict(lambda: deque(maxlen=RECENT_PER_USER))
        self._indexed = False

    def record(self, user_id, account: str, delta: int | float, balance_after: int | float,
               reason: Optional[str] = None, meta: Optional[dict] = None) -> Optional[dict]:
        """Record one balance change (`account` = wallet, bank, savings, credit, shares:NAS, ...). Zero deltas are
        ignored. Never raises: auditing must not break the economy, so failures are logged loudly instead."""
        if not delta:
            return None
        ctx = current_context()
        entry = {
            "id": f"{int(self.clock.now().timestamp())}-{next(self._ids)}",
            "ts": self.clock.now().isoformat(timespec="seconds"),
            "user": str(user_id), "account": account,
            "delta": round(delta, 4) if isinstance(delta, float) else int(delta),
            "after": round(balance_after, 4) if isinstance(balance_after, float) else int(balance_after),
            "reason": reason or (ctx.label if ctx else _caller_location()),
            "source": ctx.source if ctx else "code",
            "actor": str(ctx.actor) if ctx and ctx.actor is not None else None,
        }
        if meta:
            entry["meta"] = meta
        try:
            self.ledger.append(entry)
        except OSError:
            log.exception("AUDIT LEDGER WRITE FAILED for %s", entry)
        if self._indexed:                                 # otherwise the first recent() reads it from the file
            self._index(entry)
        if self.channel_id:
            if len(self.pending) == self.pending.maxlen:
                self.dropped += 1
            self.pending.append(entry)
        for listener in list(self.listeners):
            try:
                listener(entry)
            except Exception:
                log.exception("audit listener failed")
        return entry

    def drain(self, limit: int = 20) -> list[dict]:
        out = []
        while self.pending and len(out) < limit:
            out.append(self.pending.popleft())
        return out

    def _index(self, entry: dict) -> None:
        self._recent_all.append(entry)
        self._recent_by_user[entry.get("user")].append(entry)

    def recent(self, n: int = 20, user_id=None) -> list[dict]:
        """The last `n` entries (oldest first), for one member or for everyone; at most RECENT_PER_USER /
        RECENT_GLOBAL are kept in memory, the full history stays in the ledger file."""
        if not self._indexed:
            for entry in self.ledger.read():
                self._index(entry)
            self._indexed = True
        source = self._recent_all if user_id is None else self._recent_by_user.get(str(user_id), ())
        return list(source)[-n:] if n > 0 else []
