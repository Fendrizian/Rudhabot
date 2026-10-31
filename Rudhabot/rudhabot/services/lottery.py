"""Lottery service: turns tickets from an inventory into prizes in a wallet, and keeps the books."""
from __future__ import annotations

from dataclasses import dataclass

from rudhabot.clock import Clock
from rudhabot.domain.lottery import DrawBatch, Lottery, TicketSpec
from rudhabot.persistence.lottery import LotteryLedger
from rudhabot.persistence.users import UserRepository


class TicketRefused(Exception):
    def __init__(self, code: str, **data):
        super().__init__(code)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class LotteryResult:
    batch: DrawBatch
    wallet_after: int
    tickets_left: int

    @property
    def jackpot_hit(self) -> bool:
        return self.batch.jackpots > 0


class LotteryService:
    def __init__(self, users: UserRepository, ledger: LotteryLedger, lottery: Lottery, clock: Clock):
        self._users = users
        self._ledger = ledger
        self._lottery = lottery
        self._clock = clock

    def is_ticket(self, item: str) -> bool:
        return self._lottery.is_ticket(item)

    @property
    def specs(self) -> tuple[TicketSpec, ...]:
        return self._lottery.specs

    def spec_for(self, item: str) -> TicketSpec:
        return self._lottery.spec_for(item)

    def draw(self, user_id: int | str, item: str, quantity: int = 1) -> LotteryResult:
        """Use `quantity` tickets: consume them, draw every prize, pay the winnings, record the outcome."""
        if not self._lottery.is_ticket(item):
            raise TicketRefused("not_a_ticket", item=item)
        if quantity < 1:
            raise TicketRefused("not_positive")
        account = self._users.get(user_id)
        inventory = account.get("inventory", {})
        owned = inventory.get(item, 0)
        if owned < quantity:
            raise TicketRefused("not_enough_tickets", owned=owned)

        batch = self._lottery.draw_many(item, quantity)

        inventory[item] = owned - quantity
        if inventory[item] <= 0:
            del inventory[item]
        wallet_after = int(account["wallet"]) + batch.total_won
        self._users.update(user_id, inventory=inventory, wallet=wallet_after)   # one write: no half-applied state
        self._ledger.record(user_id, batch, self._clock.now())
        return LotteryResult(batch, wallet_after, owned - quantity)

    def recent_jackpots(self, limit: int = 5, item: str | None = None) -> list[dict]:
        return self._ledger.recent_jackpots(limit, item)
