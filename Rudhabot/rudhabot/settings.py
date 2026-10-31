"""Typed, immutable configuration. Defaults reproduce the values the bot has always used."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from rudhabot.errors import MissingTokenError

TOKEN_ENV_VAR = "DISCORD_TOKEN"


@dataclass(frozen=True)
class Paths:
    """Data files. Relative names resolve against the working directory, exactly as before."""
    data: str = "data.json"
    crypto: str = "crypto.json"
    specials: str = "specials.json"
    birthday_log: str = "birthdays.json"
    birthday_registry: str = "birthday_registry.json"
    lottery: str = "lottery.json"

    @classmethod
    def in_directory(cls, directory: str) -> "Paths":
        join = lambda name: os.path.join(directory, name)
        defaults = cls()
        return cls(**{name: join(getattr(defaults, name)) for name in defaults.__dataclass_fields__})


@dataclass(frozen=True)
class Channels:
    notifications: int = 1215241240539431033
    lottery: int = 1419303864326295608
    # The original code posted birthday messages to the placeholder id 123456789012345678, which can never
    # resolve. Until you point this at a real channel, birthdays are announced in the notifications channel.
    birthdays: int = 1215241240539431033


@dataclass(frozen=True)
class LoanTerms:
    interest_rate: float = 0.02
    extension_fee_rate: float = 0.10
    ban_divisor: int = 200
    max_amount: int = 10000
    min_amount: int = 100
    max_days: int = 30
    min_days: int = 1
    bankrupt_role_id: int = 1395412495254159371


@dataclass(frozen=True)
class BirthdayTerms:
    utc_offset_hours: int = 8          # "Rudhanese Time (GMT+8)", as used by the birthday cake
    gift_amount: int = 500             # wallet gift on your registered birthday
    min_year: int = 1900


@dataclass(frozen=True)
class Settings:
    paths: Paths = field(default_factory=Paths)
    channels: Channels = field(default_factory=Channels)
    loans: LoanTerms = field(default_factory=LoanTerms)
    birthdays: BirthdayTerms = field(default_factory=BirthdayTerms)
    page_size: int = 5
    admin_role_id: int = 1142722971606200460


def read_token(environ: Mapping[str, str] = os.environ) -> str:
    """Return the bot token from the environment. The token is never stored in source control."""
    token = (environ.get(TOKEN_ENV_VAR) or "").strip()
    if not token:
        raise MissingTokenError(
            f"The {TOKEN_ENV_VAR} environment variable is not set. "
            f"Export your bot token first, e.g.  export {TOKEN_ENV_VAR}='...'  (and regenerate any token "
            f"that was ever committed to source control)."
        )
    return token
