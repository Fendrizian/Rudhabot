"""Typed, immutable configuration. Defaults reproduce the values the bot has always used."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

from rudhabot.errors import MissingTokenError

TOKEN_ENV_VAR = "DISCORD_TOKEN"
OPENAI_KEY_ENV_VAR = "OPENAI_API_KEY"
DEFAULT_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


@dataclass(frozen=True)
class Paths:
    """Data files. Relative names resolve against the working directory, exactly as before."""
    data: str = "data.json"
    crypto: str = "crypto.json"
    specials: str = "specials.json"
    birthday_log: str = "birthdays.json"
    birthday_registry: str = "birthday_registry.json"
    lottery: str = "lottery.json"
    tcg: str = "tcg.json"
    tcg_matches: str = "tcg_matches.json"

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


def _flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name)
    return default if value is None else value.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class AISettings:
    """The intelligence layer. Every value can be overridden by an environment variable (see .env.example).

    The OpenAI key itself is never stored here; it is read from OPENAI_API_KEY only when the client is built.
    """
    oracle_enabled: bool = True
    oracle_version: Optional[str] = None          # None = the manifest's `current` version
    models_dir: str = DEFAULT_MODELS_DIR
    llm_enabled: bool = True                      # only takes effect when OPENAI_API_KEY is set
    llm_model: str = "gpt-5.4-mini"
    llm_reasoning_effort: Optional[str] = None    # e.g. "low" for reasoning models; None = provider default
    llm_timeout_s: float = 20.0
    llm_max_output_tokens: int = 600
    llm_per_user_per_hour: int = 12
    llm_per_guild_per_hour: int = 120
    llm_daily_budget: int = 800
    chronicle_enabled: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "AISettings":
        d = cls()
        num = lambda name, default, kind: kind(environ[name]) if environ.get(name) else default
        return cls(
            oracle_enabled=_flag(environ, "ORACLE_ENABLED", d.oracle_enabled),
            oracle_version=environ.get("ORACLE_VERSION") or None,
            models_dir=environ.get("RUDHABOT_MODELS_DIR") or d.models_dir,
            llm_enabled=_flag(environ, "RUDHABOT_LLM_ENABLED", d.llm_enabled),
            llm_model=environ.get("RUDHABOT_LLM_MODEL") or d.llm_model,
            llm_reasoning_effort=environ.get("RUDHABOT_LLM_REASONING_EFFORT") or None,
            llm_timeout_s=num("RUDHABOT_LLM_TIMEOUT_S", d.llm_timeout_s, float),
            llm_max_output_tokens=num("RUDHABOT_LLM_MAX_OUTPUT_TOKENS", d.llm_max_output_tokens, int),
            llm_per_user_per_hour=num("RUDHABOT_LLM_PER_USER_PER_HOUR", d.llm_per_user_per_hour, int),
            llm_per_guild_per_hour=num("RUDHABOT_LLM_PER_GUILD_PER_HOUR", d.llm_per_guild_per_hour, int),
            llm_daily_budget=num("RUDHABOT_LLM_DAILY_BUDGET", d.llm_daily_budget, int),
            chronicle_enabled=_flag(environ, "RUDHABOT_CHRONICLE_ENABLED", d.chronicle_enabled),
        )


@dataclass(frozen=True)
class Settings:
    paths: Paths = field(default_factory=Paths)
    channels: Channels = field(default_factory=Channels)
    loans: LoanTerms = field(default_factory=LoanTerms)
    birthdays: BirthdayTerms = field(default_factory=BirthdayTerms)
    ai: AISettings = field(default_factory=AISettings.from_env)
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
