"""Who counts as an administrator."""
from __future__ import annotations


class AdminPolicy:
    """Administrators are members with the Administrator permission or the configured admin role."""

    def __init__(self, admin_role_id: int):
        self._admin_role_id = admin_role_id

    def is_admin(self, user) -> bool:
        permissions = getattr(user, "guild_permissions", None)   # absent on plain Users (DMs)
        if permissions is None:
            return False
        if permissions.administrator:
            return True
        return any(role.id == self._admin_role_id for role in getattr(user, "roles", ()))
