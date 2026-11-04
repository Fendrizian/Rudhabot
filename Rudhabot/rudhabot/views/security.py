"""Security buttons. All are DynamicItems with fixed custom ids, so they keep working after a restart and never time
out; every press is authorised again by the service (clearance, two-person rule, expiry, one-use token), so an old or
replayed button can't do anything its presser couldn't do by command.

    sec:ok:<token> / sec:no:<token>     approve / decline a proposal
    sec:verify                         the verification panel's button
    sec:report:<incident id>           show the after-action report (staff only, private)
    sec:verdict:<real|false>:<id>      staff judge an alert; Clairvoyance learns this server's bar from it
"""
from __future__ import annotations

import re

import discord
from discord import ButtonStyle, ui


class ApprovalButton(ui.DynamicItem[ui.Button], template=r"sec:(?P<op>ok|no):(?P<token>[A-Za-z0-9_-]{8,24})"):
    def __init__(self, op: str, token: str):
        label, emoji, style = (("Approve", "✅", ButtonStyle.success) if op == "ok" else
                               ("Decline", "✖️", ButtonStyle.secondary))
        super().__init__(ui.Button(label=label, emoji=emoji, style=style, custom_id=f"sec:{op}:{token}"))
        self.op, self.token = op, token

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["op"], match["token"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.get_cog("SecurityCog").on_approval_button(interaction, self.op, self.token)


class VerifyButton(ui.DynamicItem[ui.Button], template=r"sec:verify"):
    def __init__(self):
        super().__init__(ui.Button(label="Verify", emoji="✅", style=ButtonStyle.success, custom_id="sec:verify"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.get_cog("SecurityCog").on_verify_button(interaction)


class ReportButton(ui.DynamicItem[ui.Button], template=r"sec:report:(?P<incident>[0-9]{4,12})"):
    def __init__(self, incident_id: str):
        super().__init__(ui.Button(label="After-action report", emoji="📋", style=ButtonStyle.secondary,
                                   custom_id=f"sec:report:{incident_id}"))
        self.incident_id = incident_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["incident"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.get_cog("SecurityCog").on_report_button(interaction, self.incident_id)


class VerdictButton(ui.DynamicItem[ui.Button], template=r"sec:verdict:(?P<v>real|false):(?P<incident>[0-9]{4,12})"):
    """Staff tell Clairvoyance whether an alert was right; it learns this server's bar from the answers."""

    def __init__(self, verdict: str, incident_id: str):
        label, emoji, style = (("Real", "✅", ButtonStyle.secondary) if verdict == "real" else
                               ("False alarm", "🙅", ButtonStyle.secondary))
        super().__init__(ui.Button(label=label, emoji=emoji, style=style,
                                   custom_id=f"sec:verdict:{verdict}:{incident_id}"))
        self.verdict, self.incident_id = verdict, incident_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["v"], match["incident"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.get_cog("SecurityCog").on_verdict_button(interaction, self.incident_id,
                                                                          self.verdict == "real")


def approval_view(token: str, open_: bool = True) -> ui.View:
    view = ui.View(timeout=None)
    if open_:
        view.add_item(ApprovalButton("ok", token))
        view.add_item(ApprovalButton("no", token))
    return view


def incident_view(incident_id: str) -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(ReportButton(incident_id))
    view.add_item(VerdictButton("real", incident_id))
    view.add_item(VerdictButton("false", incident_id))
    return view


def verify_view() -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(VerifyButton())
    return view


DYNAMIC_ITEMS = (ApprovalButton, VerifyButton, ReportButton, VerdictButton)
