"""Birthdays as values: validation, countdowns and leap-day handling."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Optional


class BirthdayError(ValueError):
    """A user-facing validation problem; the message is safe to show."""


@dataclass(frozen=True, order=True)
class BirthdayDate:
    month: int
    day: int
    year: Optional[int] = None      # optional: users may keep their age private

    def __post_init__(self):
        if not 1 <= self.month <= 12:
            raise BirthdayError("❌ The month must be between 1 and 12.")
        # 2000 is a leap year, so 29 February is accepted without a year
        if not 1 <= self.day <= calendar.monthrange(self.year or 2000, self.month)[1]:
            raise BirthdayError("❌ That day does not exist in that month.")

    # ------------------------------------------------------------------ dates
    def occurrence_in(self, year: int) -> date:
        """The calendar date this birthday falls on in `year` (29 Feb is celebrated on 28 Feb in common years)."""
        day = self.day
        if self.month == 2 and day == 29 and not calendar.isleap(year):
            day = 28
        return date(year, self.month, day)

    def is_on(self, today: date) -> bool:
        return self.occurrence_in(today.year) == today

    def next_occurrence(self, today: date) -> date:
        this_year = self.occurrence_in(today.year)
        return this_year if this_year >= today else self.occurrence_in(today.year + 1)

    def days_until(self, today: date) -> int:
        return (self.next_occurrence(today) - today).days

    def turning(self, today: date) -> Optional[int]:
        """The age reached on the next occurrence (None if no birth year was given)."""
        return None if self.year is None else self.next_occurrence(today).year - self.year

    # ---------------------------------------------------------- serialisation
    def to_dict(self) -> dict:
        data = {"month": self.month, "day": self.day}
        if self.year is not None:
            data["year"] = self.year
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BirthdayDate":
        return cls(int(data["month"]), int(data["day"]), data.get("year"))

    def label(self) -> str:
        return f"{self.day} {calendar.month_name[self.month]}" + (f" {self.year}" if self.year else "")
