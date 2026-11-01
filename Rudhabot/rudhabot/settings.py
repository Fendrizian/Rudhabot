"""Typed, immutable configuration. Defaults reproduce the values the bot has always used."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

from rudhabot.errors import MissingTokenError

TOKEN_ENV_VAR = "DISCORD_TOKEN"
OPENAI_KEY_ENV_VAR = "OPENAI_API_KEY"
BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the folder holding main.py
DEFAULT_MODELS_DIR = os.path.join(BOT_ROOT, "models")


def _id(name: str, default: int, environ: Optional[Mapping[str, str]] = None) -> int:
    """A Discord id from the environment (read when Settings is built, i.e. after .env is loaded), else the default."""
    environ = os.environ if environ is None else environ
    value = (environ.get(name) or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be a numeric Discord id, got {value!r}") from None


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
    ledger: str = "ledger.jsonl"            # the economy audit trail (append-only)
    bank: str = "bank.json"                 # card tiers, interest bookkeeping, credit cards
    stocks: str = "stocks.json"             # the Rudharta Exchange
    gacha: str = "gacha.json"               # the weekly Skyfall Convergence meter
    reviews: str = "reviews.json"           # Clairvoyance review signals
    failures: str = "failures.jsonl"        # failed interactions (input to anomaly detection)

    @classmethod
    def in_directory(cls, directory: str) -> "Paths":
        join = lambda name: os.path.join(directory, name)
        defaults = cls()
        return cls(**{name: join(getattr(defaults, name)) for name in defaults.__dataclass_fields__})

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Paths":
        """RUDHABOT_DATA_DIR keeps every data file (economy, audit ledger, TCG, ...) in one folder, created if missing.
        A relative path is resolved against the bot's folder (where main.py is), so the working directory the bot is
        started from doesn't matter. Unset = the working directory, as before."""
        directory = ((os.environ if environ is None else environ).get("RUDHABOT_DATA_DIR") or "").strip()
        if not directory:
            return cls()
        directory = os.path.join(BOT_ROOT, os.path.expanduser(directory))     # an absolute path is kept as-is
        os.makedirs(directory, exist_ok=True)
        return cls.in_directory(directory)


@dataclass(frozen=True)
class Channels:
    notifications: int = 1215241240539431033      # loan notices
    lottery: int = 1419303864326295608            # jackpot announcements
    birthdays: int = 1566283840350191646          # greetings and cake deliveries
    audit: int = 1215240269377830943              # the economy audit log (every money change)
    reviews: int = 1215240269377830943            # Clairvoyance review signals

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Channels":
        d = cls()
        audit = _id("RUDHABOT_AUDIT_CHANNEL_ID", d.audit, environ)
        return cls(notifications=_id("RUDHABOT_NOTIFICATIONS_CHANNEL_ID", d.notifications, environ),
                   lottery=_id("RUDHABOT_LOTTERY_CHANNEL_ID", d.lottery, environ),
                   birthdays=_id("RUDHABOT_BIRTHDAYS_CHANNEL_ID", d.birthdays, environ),
                   audit=audit,
                   reviews=_id("RUDHABOT_REVIEW_CHANNEL_ID", audit, environ))      # follows the audit channel if unset


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

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "LoanTerms":
        return cls(bankrupt_role_id=_id("RUDHABOT_BANKRUPT_ROLE_ID", cls.bankrupt_role_id, environ))


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
    clairvoyance_enabled: bool = True
    clairvoyance_version: Optional[str] = None          # None = the manifest's `current` version
    models_dir: str = DEFAULT_MODELS_DIR
    llm_enabled: bool = True                      # only takes effect when OPENAI_API_KEY is set
    llm_model: str = "gpt-5.4-mini"
    llm_reasoning_effort: Optional[str] = None    # None = "low" for reasoning models (gpt-5*, o*), omitted otherwise
    llm_timeout_s: float = 30.0
    llm_max_output_tokens: int = 2500             # reasoning models spend part of this on thinking
    llm_per_user_per_hour: int = 12
    llm_per_guild_per_hour: int = 120
    llm_daily_budget: int = 800
    chronicle_enabled: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "AISettings":
        d = cls()
        num = lambda name, default, kind: kind(environ[name]) if environ.get(name) else default
        return cls(
            # ORACLE_* are the pre-rename names, still honoured so existing .env files keep working
            clairvoyance_enabled=_flag(environ, "CLAIRVOYANCE_ENABLED", _flag(environ, "ORACLE_ENABLED", d.clairvoyance_enabled)),
            clairvoyance_version=environ.get("CLAIRVOYANCE_VERSION") or environ.get("ORACLE_VERSION") or None,
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
    paths: Paths = field(default_factory=Paths.from_env)
    channels: Channels = field(default_factory=Channels.from_env)
    loans: LoanTerms = field(default_factory=LoanTerms.from_env)
    birthdays: BirthdayTerms = field(default_factory=BirthdayTerms)
    ai: AISettings = field(default_factory=AISettings.from_env)
    page_size: int = 5
    admin_role_id: int = field(default_factory=lambda: _id("RUDHABOT_ADMIN_ROLE_ID", 1142722971606200460))
    banker_role_id: int = field(default_factory=lambda: _id("RUDHABOT_BANKER_ROLE_ID", 1566271529245999255))  # cogs/banker.py


def load_env_file(path: str = ".env", environ=os.environ) -> list[str]:
    """Load KEY=VALUE lines from a .env file into the environment (without overriding variables that are already set).

    Returns the names that were loaded (never the values). Supports comments, blank lines, `export KEY=...` and quoted
    values. A missing file is fine.
    """
    if not os.path.exists(path):
        return []
    loaded = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, value = (part.strip() for part in line.split("=", 1))
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            if key and value and not environ.get(key):
                environ[key] = value
                loaded.append(key)
    return loaded


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
