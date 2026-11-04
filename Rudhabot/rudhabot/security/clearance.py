"""Clearance tiers: who may do what. Checked when an action *runs*, not only when a button is shown.

Tiers (plain names; the rank in brackets is only flavour):
    0 Member            (civilian)
    1 Helper            (corporal)       warn, short timeouts, slowmode, purge up to 50
    2 Moderator         (sergeant)       timeouts, kicks, purge, propose a lockdown, confirm low-impact proposals
    3 Senior Moderator  (lieutenant)     bans and unbans, approve lockdowns, set the threat level
    4 Admin             (commander)      configuration, shadow/live mode, restores, releasing quarantine
    5 Owner             (head of state)  everything; cannot be quarantined by the bot (Discord doesn't allow it)

A member's tier is the highest of: the role->tier map set with `/security clearance`, the configured admin role (4),
and, if `derive_tiers` is on, their Discord permissions (Administrator/Manage Server 4, Ban Members 3, Kick or Timeout
Members 2, Manage Messages 1). A quarantined account has tier 0 until it is released.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Iterable, Mapping, Optional

from rudhabot.security import perms as P


class Tier(IntEnum):
    MEMBER = 0
    HELPER = 1
    MODERATOR = 2
    SENIOR = 3
    ADMIN = 4
    OWNER = 5

    @property
    def label(self) -> str:
        return TIER_LABELS[self]

    @property
    def rank(self) -> str:
        return TIER_RANKS[self]


TIER_LABELS = {Tier.MEMBER: "Member", Tier.HELPER: "Helper", Tier.MODERATOR: "Moderator",
               Tier.SENIOR: "Senior Moderator", Tier.ADMIN: "Admin", Tier.OWNER: "Owner"}
TIER_RANKS = {Tier.MEMBER: "civilian", Tier.HELPER: "corporal", Tier.MODERATOR: "sergeant",
              Tier.SENIOR: "lieutenant", Tier.ADMIN: "commander", Tier.OWNER: "head of state"}

#: The tier each action needs. Anything not listed is refused (allowlist, not denylist).
REQUIRED: dict[str, Tier] = {
    "warn": Tier.HELPER,
    "timeout_short": Tier.HELPER,            # <= 10 minutes
    "timeout": Tier.MODERATOR,
    "untimeout": Tier.HELPER,
    "slowmode": Tier.HELPER,
    "purge_small": Tier.HELPER,              # <= 50 messages
    "purge": Tier.MODERATOR,
    "kick": Tier.MODERATOR,
    "ban": Tier.SENIOR,
    "unban": Tier.SENIOR,
    "bulk_ban": Tier.SENIOR,
    "probation": Tier.HELPER,
    "release_probation": Tier.HELPER,
    "case_edit": Tier.MODERATOR,
    "case_delete": Tier.ADMIN,
    "view_cases": Tier.HELPER,
    "view_incidents": Tier.HELPER,
    "incident_manage": Tier.MODERATOR,
    "confirm": Tier.MODERATOR,               # confirm a proposed low-impact response
    "lockdown_request": Tier.MODERATOR,
    "lockdown_approve": Tier.SENIOR,
    "lockdown_lift": Tier.MODERATOR,
    "pause_invites": Tier.SENIOR,
    "set_level": Tier.SENIOR,
    "quarantine": Tier.SENIOR,
    "release_quarantine": Tier.ADMIN,
    "remove_webhook": Tier.SENIOR,
    "snapshot": Tier.SENIOR,
    "restore": Tier.ADMIN,
    "allowlist": Tier.SENIOR,
    "config": Tier.ADMIN,
    "mode": Tier.ADMIN,
    "clearance": Tier.ADMIN,
    "posture": Tier.MODERATOR,
    "drill": Tier.MODERATOR,
    "ask": Tier.HELPER,
    "automod_sync": Tier.ADMIN,
    "verify_panel": Tier.ADMIN,
    "role_manage": Tier.ADMIN,               # the temporary /role tools (assign, remove, move)
}


def required(action: str) -> Tier:
    try:
        return REQUIRED[action]
    except KeyError:
        raise KeyError(f"unknown security action {action!r}") from None


def tier_from_permissions(bits: int) -> Tier:
    if bits & (P.ADMINISTRATOR | P.MANAGE_GUILD):
        return Tier.ADMIN
    if bits & P.BAN_MEMBERS:
        return Tier.SENIOR
    if bits & (P.KICK_MEMBERS | P.MODERATE_MEMBERS):
        return Tier.MODERATOR
    if bits & P.MANAGE_MESSAGES:
        return Tier.HELPER
    return Tier.MEMBER


def resolve_tier(user_id: int, role_ids: Iterable[int], permission_bits: int, *, owner_id: int,
                 role_tiers: Mapping[str, int], admin_role_id: int = 0, derive: bool = True,
                 quarantined: bool = False) -> Tier:
    if user_id == owner_id:
        return Tier.OWNER
    if quarantined:
        return Tier.MEMBER
    roles = set(role_ids)
    tier = Tier.MEMBER
    for role_id in roles:
        mapped = role_tiers.get(str(role_id))
        if mapped is not None:
            tier = max(tier, Tier(int(mapped)))
    if admin_role_id and admin_role_id in roles:
        tier = max(tier, Tier.ADMIN)
    if derive:
        tier = max(tier, tier_from_permissions(permission_bits))
    return tier


class Refused(Exception):
    """An action the requester may not take; the message is shown to them as-is."""


def check(actor_tier: int, action: str, *, target_tier: Optional[int] = None, actor_id: Optional[int] = None,
          target_id: Optional[int] = None) -> None:
    """Raise Refused unless `actor_tier` may perform `action` on a target of `target_tier`.

    Nobody may act on themselves, and nobody may act on someone at their own tier or above (a Moderator can't time
    out another Moderator; only the owner outranks an Admin)."""
    need = required(action)
    if actor_tier < need:
        raise Refused(f"This needs {need.label} clearance or higher.")
    if actor_id is not None and target_id is not None and actor_id == target_id:
        raise Refused("You can't use this on yourself.")
    if target_tier is not None and target_tier >= actor_tier and actor_tier < Tier.OWNER:
        raise Refused(f"They hold {Tier(target_tier).label} clearance; that needs someone of higher rank.")
