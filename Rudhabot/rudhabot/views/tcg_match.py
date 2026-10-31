"""Discord components for Cloudfall matches.

The board's buttons and the move menu are `DynamicItem`s whose custom ids carry the match id (and, for the menu, the
state version), so they never time out and keep working after a restart. Picking from an out-of-date menu is refused by
the service rather than applied twice.
"""
from __future__ import annotations

import re

import discord
from discord import ui

PAGE = 23          # select options per page (Discord allows 25; we keep room for clarity)


class MatchButton(ui.DynamicItem[ui.Button], template=r"tcg:(?P<op>act|hand|hint|log):(?P<mid>[0-9a-f]{10})"):
    LABELS = {"act": ("Act", "⚡", discord.ButtonStyle.primary), "hand": ("Hand", "🃏", discord.ButtonStyle.secondary),
              "hint": ("Oracle hint", "🔮", discord.ButtonStyle.secondary), "log": ("Log", "📜", discord.ButtonStyle.secondary)}

    def __init__(self, op: str, match_id: str):
        label, emoji, style = self.LABELS[op]
        super().__init__(ui.Button(label=label, emoji=emoji, style=style, custom_id=f"tcg:{op}:{match_id}"))
        self.op, self.match_id = op, match_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["op"], match["mid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("TcgCog")
        await cog.on_board_button(interaction, self.op, self.match_id)


def board_view(match_id: str, coach: bool, finished: bool = False) -> ui.View:
    view = ui.View(timeout=None)
    if finished:
        return view
    for op in ("act", "hand") + (("hint",) if coach else ()) + ("log",):
        view.add_item(MatchButton(op, match_id))
    return view


class PickSelect(ui.DynamicItem[ui.Select], template=r"tcg:pick:(?P<mid>[0-9a-f]{10}):(?P<ver>\d+):(?P<page>\d+)"):
    """The move menu. Its id carries the match and state version, so it never expires and survives restarts; the
    options are rebuilt from the engine on every click, and a menu from an older state version is refused."""

    def __init__(self, match_id: str, version: int, page: int, acts: list):
        pages = max(1, (len(acts) + PAGE - 1) // PAGE)
        chunk = acts[page * PAGE:(page + 1) * PAGE] or acts[:PAGE]
        start = page * PAGE if acts[page * PAGE:(page + 1) * PAGE] else 0
        placeholder = f"Choose a move ({len(acts)} options" + (f", page {page + 1}/{pages})" if pages > 1 else ")")
        super().__init__(ui.Select(custom_id=f"tcg:pick:{match_id}:{version}:{page}", placeholder=placeholder,
                                   options=[discord.SelectOption(label=a.label[:100], value=str(start + i))
                                            for i, a in enumerate(chunk)] or [discord.SelectOption(label="—", value="0")]))
        self.match_id, self.version, self.page = match_id, version, page

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Select, match: re.Match[str], /):
        self = cls.__new__(cls)
        ui.DynamicItem.__init__(self, item)
        self.match_id, self.version, self.page = match["mid"], int(match["ver"]), int(match["page"])
        return self

    async def callback(self, interaction: discord.Interaction) -> None:
        index = int(interaction.data["values"][0])
        await interaction.client.get_cog("TcgCog").on_pick(interaction, self.match_id, self.version, index)


class PageButton(ui.DynamicItem[ui.Button], template=r"tcg:page:(?P<mid>[0-9a-f]{10}):(?P<ver>\d+):(?P<page>\d+):(?P<dir>[pn])"):
    def __init__(self, match_id: str, version: int, page: int, forward: bool, disabled: bool = False):
        super().__init__(ui.Button(label="More ▶" if forward else "◀ More", style=discord.ButtonStyle.secondary,
                                   disabled=disabled, custom_id=f"tcg:page:{match_id}:{version}:{page}:{'n' if forward else 'p'}"))
        self.match_id, self.version, self.page = match_id, version, page

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["mid"], int(match["ver"]), int(match["page"]), match["dir"] == "n")

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.client.get_cog("TcgCog").on_page(interaction, self.match_id, self.version, self.page)


class MovePicker(ui.View):
    """Ephemeral, per-player menu of legal moves for one state version. Never times out (see PickSelect)."""

    def __init__(self, cog, match_id: str, version: int, acts: list, page: int = 0):
        super().__init__(timeout=None)
        self.cog, self.match_id, self.version, self.acts, self.page = cog, match_id, version, acts, page
        pages = max(1, (len(acts) + PAGE - 1) // PAGE)
        page = max(0, min(page, pages - 1))
        self.add_item(PickSelect(match_id, version, page, acts))
        if pages > 1:
            self.add_item(PageButton(match_id, version, max(0, page - 1), False, disabled=page == 0))
            self.add_item(PageButton(match_id, version, min(pages - 1, page + 1), True, disabled=page >= pages - 1))


class ChallengeView(ui.View):
    def __init__(self, cog, challenger, opponent, coach: bool):
        super().__init__(timeout=900)
        self.cog, self.challenger, self.opponent, self.coach = cog, challenger, opponent, coach
        self.answered = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("Only the challenged player can answer.", ephemeral=True)
            return False
        return not self.answered

    @ui.button(label="Accept", emoji="⚔️", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.answered = True
        self.stop()
        await self.cog.start_challenge(interaction, self.challenger, self.opponent, self.coach)

    @ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: ui.Button) -> None:
        self.answered = True
        self.stop()
        await interaction.response.edit_message(content=f"{self.opponent.display_name} declined the duel.", view=None)


class DeckModal(ui.Modal, title="Build a Cloudfall deck"):
    deck_name = ui.TextInput(label="Deck name", max_length=40)
    cards = ui.TextInput(label="Cards (name or id, with xN)", style=discord.TextStyle.paragraph, max_length=3000,
                         placeholder="Dren Voskarl x3, Rally Cry x2, C11 x2, ...")

    def __init__(self, cog, name: str = "", text: str = ""):
        super().__init__()
        self.cog = cog
        self.deck_name.default = name
        self.cards.default = text

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.save_deck_from_modal(interaction, str(self.deck_name.value), str(self.cards.value))
