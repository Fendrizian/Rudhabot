"""Base classes that give every interactive component access to the injected `Services`.

Dependencies enter at the root view's constructor and flow down: buttons and selects read them from their
parent view, modals are handed them by whichever component opens them.
"""
from __future__ import annotations

from discord import ui


class ServiceView(ui.View):
    def __init__(self, services, *, timeout: float | None = 180):
        super().__init__(timeout=timeout)
        self.services = services


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
