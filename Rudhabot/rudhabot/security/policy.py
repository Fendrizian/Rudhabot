"""Per-server security configuration: typed, validated, versioned settings with safe defaults.

Settings are flat dotted keys (`joins.min_joins`). Each has a type, bounds and a description; `/security settings`
lists and changes them (clearance: Admin), every change is a new version kept in history (so it can be rolled back), and
the active version is recorded on every incident. Lists (deny/allow domains, relaxed channels, ...) and the role->tier
map are edited through their own commands.

The defaults reflect the agreed policy: automation starts in *shadow* mode (everything is detected and reported, no
automatic action is taken) until an admin switches it to live; automatic actions are limited to deleting spam,
slowmode, probation and timeouts of at most 10 minutes, plus anti-nuke quarantine of an account over its budget.
Everything else is proposed to moderators.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    help: str
    kind: type = int
    low: Optional[float] = None
    high: Optional[float] = None
    choices: tuple = ()

    def parse(self, raw: Any) -> Any:
        if self.kind is bool:
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in ("1", "true", "yes", "on", "enable", "enabled"):
                return True
            if text in ("0", "false", "no", "off", "disable", "disabled"):
                return False
            raise ValueError(f"{self.key} must be on or off")
        if self.kind is str:
            value = str(raw).strip().lower()
            if self.choices and value not in self.choices:
                raise ValueError(f"{self.key} must be one of: {', '.join(self.choices)}")
            return value
        try:
            value = self.kind(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{self.key} must be a number") from None
        if self.low is not None and value < self.low or self.high is not None and value > self.high:
            raise ValueError(f"{self.key} must be between {self.low:g} and {self.high:g}")
        return value


S = Setting
SETTINGS: tuple[Setting, ...] = (
    # ---------------------------------------------------------------- general
    S("mode", "shadow", "shadow: detect and report only (the drill). live: carry out the standing orders.", str,
      choices=("shadow", "live")),
    S("alert_channel", 0, "Channel for security alerts and approval requests (the operations room).", int),
    S("modlog_channel", 0, "Channel for the moderation log (every case).", int),
    S("probation_role", 0, "Role that restricts new or suspicious members (recruits in quarantine barracks).", int),
    S("probation_minutes", 60, "How long probation lasts before it lifts on its own.", int, 5, 10080),
    S("verified_role", 0, "Role granted by the verification button (0 = verification off).", int),
    S("quarantine_role", 0, "Optional role given to quarantined accounts (e.g. one that can only see a review channel).", int),
    S("derive_tiers", True, "Give clearance from Discord permissions too (Ban Members = Senior Moderator, ...).", bool),
    # ---------------------------------------------------------------- joins
    S("joins.min_joins", 8, "Fewest joins in a minute that can count as a join surge.", int, 3, 500),
    S("joins.surge_ratio", 4.0, "A surge is at least this many times the usual join rate.", float, 1.5, 100),
    S("joins.cohort_min", 5, "Look-alike accounts joining together that count as a cohort.", int, 3, 200),
    S("joins.cohort_window_s", 180, "Window for spotting a join cohort, in seconds.", int, 30, 3600),
    S("joins.new_account_days", 7, "Accounts younger than this are 'new' (context only, never proof).", int, 1, 365),
    # ---------------------------------------------------------------- messages
    S("messages.flood_count", 7, "Messages from one member within the flood window that count as a flood.", int, 3, 100),
    S("messages.flood_window_s", 6, "Flood window, in seconds.", int, 2, 120),
    S("messages.dup_count", 4, "Identical messages from one member within the window that count as spam.", int, 2, 50),
    S("messages.dup_window_s", 45, "Duplicate window, in seconds.", int, 5, 3600),
    S("messages.coord_accounts", 3, "Accounts posting near-identical text that count as coordinated.", int, 2, 50),
    S("messages.coord_window_s", 180, "Coordination window, in seconds.", int, 30, 3600),
    S("messages.mention_limit", 8, "Distinct members mentioned in one message that count as a mention bomb.", int, 3, 100),
    S("messages.mention_window_limit", 15, "Distinct mentions by one member within a minute.", int, 5, 200),
    S("messages.invites", "flag", "Invites to other servers: allow, flag (report) or delete.", str,
      choices=("allow", "flag", "delete")),
    S("messages.relaxed_factor", 3.0, "Flood and duplicate limits are multiplied by this in relaxed channels "
      "(roleplay battles, games).", float, 1.0, 20),
    # ---------------------------------------------------------------- links and files
    S("links.delete_score", 0.85, "Link risk at or above which a link message is deleted (deny list, look-alikes).",
      float, 0.5, 1.0),
    S("links.shorteners", "flag", "URL shorteners: allow, flag or delete.", str, choices=("allow", "flag", "delete")),
    S("files.delete_score", 0.85, "Attachment risk at or above which the message is deleted (disguised programs).",
      float, 0.5, 1.0),
    # ---------------------------------------------------------------- anti-nuke
    S("antinuke.enabled", True, "Watch the audit log for destructive bursts by any account.", bool),
    S("antinuke.window_s", 600, "Anti-nuke budget window, in seconds.", int, 60, 7200),
    S("antinuke.budget_mod", 8, "High-impact change points a Moderator (or below) may spend per window.", int, 1, 200),
    S("antinuke.budget_senior", 12, "Points a Senior Moderator may spend per window.", int, 1, 200),
    S("antinuke.budget_admin", 20, "Points an Admin may spend per window.", int, 1, 400),
    S("antinuke.quarantine", True, "Quarantine an account that goes over budget (strip its dangerous roles).", bool),
    S("antinuke.contain", True, "Also remove webhooks and bots that account added, and undo dangerous grants to "
      "@everyone, during the same window.", bool),
    # ---------------------------------------------------------------- threat levels
    S("levels.auto_max", 4, "Highest threat level detectors may raise on their own (5 = Critical is staff-only by "
      "default).", int, 1, 5),
    S("levels.decay_minutes", 30, "Quiet minutes before an automatically raised level steps down by one.", int, 5, 1440),
    # ---------------------------------------------------------------- automation (live mode only)
    S("auto.delete_spam", True, "Delete flood, duplicate, coordinated and malicious-link messages.", bool),
    S("auto.slowmode", True, "Apply slowmode to channels under a message surge (lifted automatically).", bool),
    S("auto.probation", True, "Put risky joins on probation (lifted automatically).", bool),
    S("auto.timeouts", True, "Time out repeat spammers for a short while (at most 10 minutes).", bool),
    S("auto.timeout_minutes", 5, "Length of an automatic timeout.", int, 1, 10),
    S("auto.model_corroborates", True, "Let a very confident message-model score count as the second piece of "
      "evidence for deleting a risky link (never on its own).", bool),
    # ---------------------------------------------------------------- approvals, emergencies, retention
    S("approvals.two_person", True, "Lockdowns and server restores need a second staff member's approval.", bool),
    S("approvals.expire_minutes", 20, "Unanswered approval requests expire after this long.", int, 2, 1440),
    S("lockdown.default_minutes", 30, "Default lockdown length.", int, 5, 1440),
    S("lockdown.max_minutes", 720, "Longest allowed lockdown.", int, 10, 1440),
    S("retention.snippet_days", 14, "Days a flagged message snippet (<= 200 chars) is kept.", int, 1, 60),
    S("retention.incident_days", 90, "Days a closed incident is kept.", int, 7, 730),
    # ---------------------------------------------------------------- Clairvoyance models and the LLM
    S("models.message", True, "Use Clairvoyance's message-risk model (advisory).", bool),
    S("models.incident", True, "Use Clairvoyance's incident model (advisory).", bool),
    S("models.ranker", True, "Order the low-severity digest with Clairvoyance's alert ranker.", bool),
    S("models.staff_baseline", True, "Compare staff activity with each account's own usual pattern.", bool),
    S("llm.summaries", True, "Draft incident summaries with the LLM when it is configured (reason codes and counts "
      "only are sent; never message text).", bool),
)
BY_KEY = {s.key: s for s in SETTINGS}
LIST_KEYS = ("links.deny", "links.allow", "channels.relaxed", "channels.protected", "channels.ignored")


def defaults() -> dict:
    out = {s.key: s.default for s in SETTINGS}
    out.update({k: [] for k in LIST_KEYS})
    out["tiers"] = {}                     # role id (str) -> tier (int)
    return out


class SecurityConfig:
    """An immutable view of one server's settings at one version."""

    def __init__(self, values: Optional[dict] = None, version: int = 0):
        merged = defaults()
        for key, value in (values or {}).items():
            if key in BY_KEY:
                try:
                    merged[key] = BY_KEY[key].parse(value)
                except ValueError:
                    pass                  # a bad stored value falls back to the default instead of breaking security
            elif key in LIST_KEYS and isinstance(value, list):
                merged[key] = [str(v).lower() if key.startswith("links.") else int(v) for v in value]
            elif key == "tiers" and isinstance(value, dict):
                merged[key] = {str(k): max(0, min(4, int(v))) for k, v in value.items()}
        self._values = merged
        self.version = version

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    @property
    def live(self) -> bool:
        return self._values["mode"] == "live"

    def to_dict(self) -> dict:
        import copy
        return copy.deepcopy(self._values)

    def with_value(self, key: str, raw: Any) -> "SecurityConfig":
        """A new config with one setting changed (validated). Raises ValueError with a user-facing message."""
        if key not in BY_KEY:
            raise ValueError(f"unknown setting {key!r}")
        values = self.to_dict()
        values[key] = BY_KEY[key].parse(raw)
        return SecurityConfig(values, self.version)

    def with_list(self, key: str, item: Any, add: bool) -> "SecurityConfig":
        if key not in LIST_KEYS:
            raise ValueError(f"unknown list {key!r}")
        values = self.to_dict()
        item = str(item).strip().lower() if key.startswith("links.") else int(item)
        current = [x for x in values[key] if x != item]
        values[key] = current + [item] if add else current
        if len(values[key]) > 500:
            raise ValueError("lists hold at most 500 entries")
        return SecurityConfig(values, self.version)

    def with_tier(self, role_id: int, tier: Optional[int]) -> "SecurityConfig":
        values = self.to_dict()
        if tier is None:
            values["tiers"].pop(str(role_id), None)
        else:
            if not 0 <= tier <= 4:
                raise ValueError("a role's tier must be 0-4 (5 is the server owner)")
            values["tiers"][str(role_id)] = tier
        return SecurityConfig(values, self.version)

    def relaxed(self, channel_id: int) -> bool:
        return channel_id in self._values["channels.relaxed"]

    def protected(self, channel_id: int) -> bool:
        return channel_id in self._values["channels.protected"]

    def ignored(self, channel_id: int) -> bool:
        return channel_id in self._values["channels.ignored"]

    def diff(self, other: "SecurityConfig") -> dict:
        a, b = self.to_dict(), other.to_dict()
        return {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
