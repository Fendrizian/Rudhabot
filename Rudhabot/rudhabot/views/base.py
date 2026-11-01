"""Base classes that give every interactive component access to the injected `Services`.

Dependencies enter at the root view's constructor and flow down: buttons and selects read them from their
parent view, modals are handed them by whichever component opens them.
"""
from __future__ import annotations

from discord import Interaction, ui

from rudhabot.services.audit import set_context


class ServiceView(ui.View):
    def __init__(self, services, *, timeout: float | None = 180):
        super().__init__(timeout=timeout)
        self.services = services

    def _label_interaction(self, interaction: Interaction) -> None:
        # runs in the same task as the button/select callback: its money changes are labelled e.g. "Blackjack (button)"
        name = type(self).__name__.removesuffix("View") or "component"
        set_context(f"{name} (button)", interaction.user.id, "component")

    async def interaction_check(self, interaction: Interaction) -> bool:
        self._label_interaction(interaction)
        return True

    async def on_error(self, interaction: Interaction, error: Exception, item) -> None:
        """A button or select failed: log it, count it for the review monitor (as the command tree does) and tell
        the player, instead of Discord's bare "This interaction failed"."""
        import logging
        import discord
        logging.getLogger("rudhabot.views").error("%s failed in %s", type(item).__name__, type(self).__name__,
                                                  exc_info=error)
        reviews = getattr(self.services, "reviews", None)
        if reviews is not None:
            reviews.on_failure(interaction.user.id, f"{type(self).__name__}: {type(error).__name__}")
        message = {"content": "❌ Something went wrong. Please try again.", "ephemeral": True}
        try:
            if interaction.response.is_done():
                await interaction.followup.send(**message)
            else:
                await interaction.response.send_message(**message)
        except discord.HTTPException:
            pass

    def __init_subclass__(cls, **kwargs):
        """Views that override interaction_check still get the audit label, without having to call super()."""
        super().__init_subclass__(**kwargs)
        own = cls.__dict__.get("interaction_check")
        if own is not None and not getattr(own, "_audit_labelled", False):
            async def interaction_check(self, interaction: Interaction, _own=own) -> bool:
                self._label_interaction(interaction)
                return await _own(self, interaction)
            interaction_check._audit_labelled = True
            cls.interaction_check = interaction_check


class ServiceButton(ui.Button):
    @property
    def services(self):
        return self.view.services


class ServiceSelect(ui.Select):
    @property
    def services(self):
        return self.view.services


class ServiceModal(ui.Modal):
    def __init__(self, services, *, title: str, **kwargs):
        super().__init__(title=title, **kwargs)
        self.services = services
