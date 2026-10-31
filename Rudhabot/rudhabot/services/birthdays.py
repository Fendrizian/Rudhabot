"""Birthday service: the birthday registry, yearly greetings with a gift, and scheduled cake celebrations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Container, Optional

from rudhabot.clock import Clock, utc_offset_zone
from rudhabot.domain.birthday import BirthdayDate, BirthdayError
from rudhabot.persistence.birthdays import BirthdayRegistry, CelebrationLog
from rudhabot.persistence.users import UserRepository
from rudhabot.settings import BirthdayTerms

CAKE_ITEM = "special birthday cake"


@dataclass(frozen=True)
class Greeting:
    """Someone whose registered birthday is today and who has just been given their gift."""
    user_id: int
    age: Optional[int]
    gift: int


@dataclass(frozen=True)
class Upcoming:
    user_id: int
    birthday: BirthdayDate
    on: date
    days_until: int
    turning: Optional[int]


@dataclass(frozen=True)
class ScheduledCake:
    entry: dict
    send_at: datetime            # timezone-aware (UTC)
    local_time: str              # "HH:MM", the time as the user typed it (GMT+8)


class BirthdayService:
    def __init__(self, registry: BirthdayRegistry, celebrations: CelebrationLog, users: UserRepository,
                 terms: BirthdayTerms, clock: Clock):
        self._registry = registry
        self._celebrations = celebrations
        self._users = users
        self._terms = terms
        self._clock = clock
        self._zone = utc_offset_zone(terms.utc_offset_hours)

    # ------------------------------------------------------------------ registry
    def today(self) -> date:
        return self._clock.now(self._zone).date()

    def set_birthday(self, user_id: int, month: int, day: int, year: Optional[int] = None) -> BirthdayDate:
        if year is not None and not (self._terms.min_year <= year <= self.today().year):
            raise BirthdayError(f"❌ The year must be between {self._terms.min_year} and {self.today().year}.")
        birthday = BirthdayDate(month, day, year)
        if year is not None and birthday.occurrence_in(year) > self.today():
            raise BirthdayError("❌ That birthday is in the future.")
        self._registry.set(user_id, birthday)
        return birthday

    def get_birthday(self, user_id: int) -> Optional[BirthdayDate]:
        return self._registry.get(user_id)

    def remove_birthday(self, user_id: int) -> bool:
        return self._registry.remove(user_id)

    def upcoming(self, limit: int = 10, only: Optional[Container[int]] = None) -> list[Upcoming]:
        today = self.today()
        rows = [
            Upcoming(uid, bd, bd.next_occurrence(today), bd.days_until(today), bd.turning(today))
            for uid, bd in self._registry.all().items()
            if only is None or uid in only
        ]
        rows.sort(key=lambda r: (r.days_until, r.user_id))
        return rows[:limit]

    # ---------------------------------------------------------- yearly greetings
    def collect_due_greetings(self) -> list[Greeting]:
        """Give today's birthday gift to everyone who hasn't had one this year. Safe to call as often as you like:
        each user is greeted at most once per year (marked *before* paying, so a crash can't double-pay)."""
        today = self.today()
        greetings = []
        for user_id, birthday in self._registry.all().items():
            if not birthday.is_on(today) or self._registry.last_celebrated_year(user_id) == today.year:
                continue
            self._registry.mark_celebrated(user_id, today.year)
            self._users.credit_wallet(user_id, self._terms.gift_amount)
            age = today.year - birthday.year if birthday.year else None
            greetings.append(Greeting(user_id, age, self._terms.gift_amount))
        return greetings

    # ----------------------------------------------------------- cake celebrations
    def schedule_cake(self, *, owner_id: int, recipient_id: int, date_text: str, time_text: str,
                      message_text: str) -> ScheduledCake:
        """Validate the cake form, take one cake from the owner, and persist the celebration so it survives restarts."""
        try:
            day, month, year = map(int, date_text.strip().split("/"))
            when = datetime(year, month, day)
        except Exception:
            raise BirthdayError("❌ Invalid date format. Use DD/MM/YYYY.") from None
        try:
            hour, minute = map(int, time_text.strip().split(":")) if time_text.strip() else (0, 0)
            send_local = when.replace(hour=hour, minute=minute, tzinfo=self._zone)
        except Exception:
            raise BirthdayError("❌ Invalid time format. Use HH:MM.") from None
        send_at = send_local.astimezone(timezone.utc)
        if send_at < self._clock.now(timezone.utc):
            raise BirthdayError("❌ The specified time is in the past.")
        if not self._users.consume_item(owner_id, CAKE_ITEM, 1):
            raise BirthdayError("❌ You don't have a special birthday cake to use.")
        entry = self._celebrations.schedule(
            {
                "scheduled_by": owner_id,
                "scheduled_for": recipient_id,
                "date": date_text.strip(),
                "time": time_text.strip() or "00:00",
                "message": message_text.strip(),
                "scheduled_at": self._clock.now(timezone.utc).isoformat(),
            },
            send_at,
        )
        return ScheduledCake(entry, send_at, f"{hour:02d}:{minute:02d}")

    def due_cake_celebrations(self) -> list[dict]:
        return self._celebrations.due(self._clock.now(timezone.utc))

    MAX_DELIVERY_ATTEMPTS = 3

    def mark_cake_celebration(self, entry_id: str, status: str, **extra) -> None:
        self._celebrations.mark(entry_id, status, **extra)

    def record_cake_result(self, entry: dict, delivered: bool) -> str:
        """Mark a delivery attempt. A failed delivery stays pending (and is retried on the next pass) until it has
        failed MAX_DELIVERY_ATTEMPTS times, so a brief Discord outage doesn't swallow someone's cake."""
        attempts = entry.get("attempts", 0) + 1
        status = "sent" if delivered else ("failed" if attempts >= self.MAX_DELIVERY_ATTEMPTS else "pending")
        self._celebrations.mark(entry["id"], status, attempts=attempts)
        return status
