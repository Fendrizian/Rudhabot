"""Who counts as an administrator, and who counts as a banker."""
from __future__ import annotations


class AdminPolicy:
    """Administrators: the Administrator permission or the admin role.

    Bankers: the banker role, plus every administrator. Bankers get read access to the economy (audit log, review
    signals, anyone's bank account) and account-level tools (card holds, lifting loan bans), but they can never
    create or remove money: /add-money and /remove-money stay admin-only."""

    def __init__(self, admin_role_id: int, banker_role_id: int = 0):
        self._admin_role_id = admin_role_id
        self._banker_role_id = banker_role_id

    @staticmethod
    def _has_role(user, role_id: int) -> bool:
        return bool(role_id) and any(role.id == role_id for role in getattr(user, "roles", ()))

    def is_admin(self, user) -> bool:
        permissions = getattr(user, "guild_permissions", None)   # absent on plain Users (DMs)
        if permissions is None:
            return False
        if permissions.administrator:
            return True
        return self._has_role(user, self._admin_role_id)

    def is_banker(self, user) -> bool:
        if getattr(user, "guild_permissions", None) is None:     # DMs: no roles to check
            return False
        return self.is_admin(user) or self._has_role(user, self._banker_role_id)
