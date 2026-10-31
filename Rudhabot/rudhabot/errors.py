"""Exceptions shared across layers. The two CheckFailure subclasses are rendered by RudhaTree.on_error."""
from discord import app_commands


class MissingTokenError(RuntimeError):
    """The DISCORD_TOKEN environment variable is missing or empty."""


class AlreadyInGame(app_commands.CheckFailure):
    """The user tried to run a command while a gambling game is in progress."""


class NotAuthorized(app_commands.CheckFailure):
    """The user lacks the administrator permission / admin role required for a command."""
