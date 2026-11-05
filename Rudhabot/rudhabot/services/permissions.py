"""Who counts as an administrator, and who counts as a banker."""
from __future__ import annotations

from typing import Callable, Union

RoleId = Union[int, Callable[[], int]]      # a fixed id, or a live read from the server profile (/setup)


def _resolve(role: RoleId) -> int:
    return int(role() if callable(role) else role)


class AdminPolicy:
    """Administrators: the Administrator permission or the admin role.

    Bankers: the banker role, plus every administrator. Bankers get read access to the economy (audit log, review
    signals, anyone's bank account) and account-level tools (card holds, lifting loan bans), but they can never
    create or remove money: /add-money and /remove-money stay admin-only."""

    def __init__(self, admin_role_id: RoleId, banker_role_id: RoleId = 0):
        self._admin_role_id = admin_role_id
        self._banker_role_id = banker_role_id

    @property
    def admin_role_id(self) -> int:
        return _resolve(self._admin_role_id)

    @property
    def banker_role_id(self) -> int:
        return _resolve(self._banker_role_id)

    @staticmethod
    def _has_role(user, role_id: int) -> bool:
        return bool(role_id) and any(role.id == role_id for role in getattr(user, "roles", ()))

    def is_admin(self, user) -> bool:
        permissions = getattr(user, "guild_permissions", None)   # absent on plain Users (DMs)
        if permissions is None:
            return False
        if permissions.administrator:
            return True
        return self._has_role(user, self.admin_role_id)

    def is_banker(self, user) -> bool:
        if getattr(user, "guild_permissions", None) is None:     # DMs: no roles to check
            return False
        return self.is_admin(user) or self._has_role(user, self.banker_role_id)


# ---------------------------------------------------------------------------------------------- who is looking
#: what a help entry or command needs: an economy role, or a security clearance tier (by name)
REQUIREMENTS = ("admin", "banker", "helper", "moderator", "senior")


class Viewer:
    """What one person may use: shown in /help, offered by /ask. Visibility only; every command still checks."""

    __slots__ = ("admin", "banker", "tier")

    def __init__(self, admin: bool = False, banker: bool = False, tier: int = 0):
        self.admin, self.banker, self.tier = admin, banker or admin, int(tier)

    def can(self, requirement) -> bool:
        if requirement is None:
            return True
        if requirement == "admin":
            return self.admin
        if requirement == "banker":
            return self.banker
        from rudhabot.security.clearance import Tier
        need = {"helper": Tier.HELPER, "moderator": Tier.MODERATOR, "senior": Tier.SENIOR}[requirement]
        return self.tier >= need

    @property
    def staff(self) -> bool:
        return self.admin or self.banker or self.tier >= 1

    def __repr__(self) -> str:
        return f"Viewer(admin={self.admin}, banker={self.banker}, tier={self.tier})"


MEMBER = Viewer()
EVERYTHING = Viewer(True, True, 5)


def viewer_for(services, user, guild_id) -> Viewer:
    """The person's economy roles (admin, banker: the /setup roles, read live) and security clearance tier."""
    admin = services.admin.is_admin(user)
    banker = services.admin.is_banker(user)
    tier = 0
    if guild_id:
        try:
            tier = int(services.security.tier_of(guild_id, user.id))
        except Exception:                               # no security runtime (yet): no clearance
            tier = 0
    return Viewer(admin, banker, tier)
