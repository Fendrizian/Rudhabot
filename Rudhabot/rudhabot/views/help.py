"""/help: a sectioned overview, one page per section, and the full paginated list.

Everything is rendered for one viewer: members see only the sections and commands they can use (REQUIRES below), so
the Banker, Admin, Moderation and Security sections only appear to people with that role or clearance.

Navigation uses DynamicItems with fixed custom ids ("help:..."), so the menus never time out and keep working after a
restart. When someone other than the person who ran /help clicks, they get their own private copy (rendered for them)
instead of changing the shared message.
"""
from __future__ import annotations

import math
import os
import re
from typing import Optional

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.services.permissions import MEMBER, Viewer, viewer_for
from rudhabot.ui.theme import BOTICON, COMPONENT_FALLBACK, Colors
from rudhabot.views.help_data import REQUIRES, SECTION_BY_KEY, SECTIONS, get_commands_data  # noqa: F401 (re-exported)
from rudhabot.views.navigation import show_for_clicker

PER_PAGE = 8                 # commands per page, in sections and in the full list
COLOR = Colors.HELP
ABOUT = ("**Rudhabot** is a bot specialized for the Rudharta Discord server, with features tuned for the community's "
         "needs. **Rudhabot** is developed by the Digital Research Institute, and as such is the property of the Kingdom "
         "of Rudharta created by ``fendrizian``. Copyright 2026.")
THUMBNAIL = "https://micronations.wiki/images/en/images/images/6/6a/Flag_of_Rudharta.png"
# The Digital Research Institute banner, shown full-size on the main help page (the overview) only. It is uploaded with
# the message and referenced as attachment://dri.png. Discord keeps attachments across edits and would show an
# unreferenced one as a loose image, so every page change sets the attachments explicitly (see _show).
HELP_IMAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "dri.png")
HELP_IMAGE_NAME = "dri.png"


def help_file() -> Optional[discord.File]:
    """A fresh upload of the emblem for a new help message (None if the asset is missing)."""
    return discord.File(HELP_IMAGE, filename=HELP_IMAGE_NAME) if os.path.exists(HELP_IMAGE) else None


def _with_image(embed: discord.Embed) -> discord.Embed:
    if os.path.exists(HELP_IMAGE):
        embed.set_image(url=f"attachment://{HELP_IMAGE_NAME}")
    return embed


def shows_image(embed: discord.Embed) -> bool:
    return embed.image.url == f"attachment://{HELP_IMAGE_NAME}"


def image_upload(embed: discord.Embed) -> dict:
    """Keyword arguments for sending `embed`: the banner file when the page shows it, nothing otherwise."""
    image = help_file() if shows_image(embed) else None
    return {"file": image} if image else {}

def visible(name: str, viewer: Viewer) -> bool:
    return viewer.can(REQUIRES.get(name))


def _by_name(viewer: Viewer = MEMBER) -> dict[str, dict]:
    return {c["name"]: c for c in get_commands_data() if visible(c["name"], viewer)}


def hidden_commands(viewer: Viewer) -> set[str]:
    """Help entries this viewer can't use (for /ask, which must not suggest them either)."""
    return {c["name"] for c in get_commands_data() if not visible(c["name"], viewer)}


def visible_sections(viewer: Viewer = MEMBER) -> list[tuple]:
    commands = _by_name(viewer)
    return [s for s in SECTIONS if any(n in commands for n in s[4])]


def alphabetical(names) -> list[str]:
    """House rule: /help always lists commands alphabetically (case-insensitive), wherever they appear."""
    return sorted(names, key=str.casefold)


def _field(embed: discord.Embed, command: dict) -> None:
    slash = "" if command["name"][:1].isupper() else "/"          # context-menu entries ("Report to moderators")
    embed.add_field(name=f"{slash}{command['name']} {command['args']}".strip(), value=command["description"], inline=False)


# ------------------------------------------------------------------ embeds
def overview_embed(viewer: Viewer = MEMBER) -> discord.Embed:
    embed = discord.Embed(title=f"{BOTICON} HELP", color=COLOR,
                          description=ABOUT + "\n\nPick a section below, or press **Full list** for every command. "
                                              "Tip: `/ask` answers questions in plain words.")
    embed.set_thumbnail(url=THUMBNAIL)
    commands = _by_name(viewer)
    sections = visible_sections(viewer)
    for _key, title, emoji, summary, names in sections:
        count = sum(1 for n in names if n in commands)
        embed.add_field(name=f"{emoji} {title}", value=f"{summary} · {count}", inline=True)
    embed.set_footer(text=f"{len(commands)} commands you can use, in {len(sections)} sections")
    return embed


def _section_commands(key: str, viewer: Viewer = MEMBER) -> list[dict]:
    commands = _by_name(viewer)
    return [commands[n] for n in alphabetical(SECTION_BY_KEY[key][3]) if n in commands]


def section_pages(key: str, viewer: Viewer = MEMBER) -> int:
    return max(1, math.ceil(len(_section_commands(key, viewer)) / PER_PAGE))


def section_embed(key: str, page: int = 0, viewer: Viewer = MEMBER) -> discord.Embed:
    title, emoji, summary, _ = SECTION_BY_KEY[key]
    pages = section_pages(key, viewer)
    page = max(0, min(page, pages - 1))
    embed = discord.Embed(title=f"{emoji} {title}" + (f" ({page + 1}/{pages})" if pages > 1 else ""),
                          description=summary, color=COLOR)
    for command in _section_commands(key, viewer)[page * PER_PAGE:(page + 1) * PER_PAGE]:
        _field(embed, command)
    keys = [k for k, *_ in visible_sections(viewer)]
    embed.set_footer(text=f"Section {keys.index(key) + 1}/{len(keys)}" + (f" · page {page + 1}/{pages}" if pages > 1 else "")
                          + " · use the menu to switch sections")
    return embed


def ordered_commands(viewer: Viewer = MEMBER) -> list[dict]:
    """Every command the viewer can use, for the full list, alphabetically."""
    commands = _by_name(viewer)
    return [commands[n] for n in alphabetical(commands)]


def full_pages(viewer: Viewer = MEMBER) -> int:
    return max(1, math.ceil(len(_by_name(viewer)) / PER_PAGE))


def create_help_embed(commands_data, page, total_pages):
    """The full list, one page at a time (kept for the "Full list" view)."""
    embed = discord.Embed(title=f"{BOTICON} HELP · all commands", description=ABOUT, color=COLOR)
    embed.set_thumbnail(url=THUMBNAIL)
    for command in commands_data[page * PER_PAGE:(page + 1) * PER_PAGE]:
        _field(embed, command)
    embed.set_footer(text=f"Page {page + 1}/{total_pages}")
    return embed


# ------------------------------------------------------------------ navigation (never expires)
def viewer_of(interaction: Interaction) -> Viewer:
    """Whoever clicked: each person is shown only their own commands."""
    services = getattr(interaction.client, "services", None)
    if services is None:
        return MEMBER
    return viewer_for(services, interaction.user, getattr(interaction, "guild_id", None))


async def _show(interaction: Interaction, embed: discord.Embed, view: ui.View) -> None:
    """Edit the help message for the person who opened it; give anyone else their own private copy. The banner
    belongs to the overview only: it is re-attached there and dropped everywhere else."""
    await show_for_clicker(interaction, embed, view, upload=help_file if shows_image(embed) else None)


class SectionSelect(ui.DynamicItem[ui.Select], template=r"help:menu"):
    def __init__(self, current: Optional[str] = None, viewer: Viewer = MEMBER):
        options = [discord.SelectOption(label="Overview", value="home", emoji="🏠", default=current == "home")]
        options += [discord.SelectOption(label=title, value=key, emoji=COMPONENT_FALLBACK.get(emoji, emoji), description=summary[:100], default=key == current)
                    for key, title, emoji, summary, _ in visible_sections(viewer)]
        options.append(discord.SelectOption(label="Full list (all commands)", value="full", emoji="📖", default=current == "full"))
        super().__init__(ui.Select(custom_id="help:menu", placeholder="Jump to a section…", options=options))

    @classmethod
    async def from_custom_id(cls, interaction: Interaction, item: ui.Select, match: re.Match[str], /):
        return cls()

    async def callback(self, interaction: Interaction) -> None:
        choice = interaction.data["values"][0]
        embed, view = render(choice, viewer=viewer_of(interaction))
        await _show(interaction, embed, view)


class NavButton(ui.DynamicItem[ui.Button], template=r"help:(?P<target>[a-z_]+):(?P<page>\d+)"):
    def __init__(self, target: str, page: int = 0, label: str = "", disabled: bool = False, emoji: Optional[str] = None):
        super().__init__(ui.Button(label=label, emoji=emoji, disabled=disabled, style=ButtonStyle.primary
                                   if target == "full" else ButtonStyle.secondary, custom_id=f"help:{target}:{page}"))
        self.target, self.page = target, page

    @classmethod
    async def from_custom_id(cls, interaction: Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["target"], int(match["page"]))

    async def callback(self, interaction: Interaction) -> None:
        embed, view = render(self.target, self.page, viewer=viewer_of(interaction))
        await _show(interaction, embed, view)


def _pager(view: ui.View, target: str, page: int, pages: int) -> None:
    # custom ids must be unique within a message, so the prev/next ids differ even at the ends
    view.add_item(NavButton(target, max(0, page - 1) if page > 0 else pages + 1, "◀ Previous", disabled=page == 0))
    view.add_item(NavButton(target, page + 1 if page < pages - 1 else pages + 2, "Next ▶", disabled=page >= pages - 1))


def help_view(current: str = "home", page: int = 0, viewer: Viewer = MEMBER) -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(SectionSelect(current, viewer))
    if current == "full":
        _pager(view, "full", page, full_pages(viewer))
        view.add_item(NavButton("home", 0, "Overview", emoji="🏠"))
        return view
    if current in SECTION_BY_KEY and section_pages(current, viewer) > 1:
        _pager(view, current, page, section_pages(current, viewer))
    view.add_item(NavButton("full", 0, "Full list", emoji="📖"))
    if current != "home":
        view.add_item(NavButton("home", 0, "Overview", emoji="🏠"))
    return view


def render(target: str, page: int = 0, viewer: Viewer = MEMBER) -> tuple[discord.Embed, ui.View]:
    """One help page for one viewer. A section they can't see (or an unknown one) shows the overview."""
    if target == "full":
        pages = full_pages(viewer)
        page = max(0, min(page, pages - 1))
        embed, view = create_help_embed(ordered_commands(viewer), page, pages), help_view("full", page, viewer)
    elif target in SECTION_BY_KEY and target in {k for k, *_ in visible_sections(viewer)}:
        page = max(0, min(page, section_pages(target, viewer) - 1))
        embed, view = section_embed(target, page, viewer), help_view(target, page, viewer)
    else:
        return _with_image(overview_embed(viewer)), help_view("home", viewer=viewer)   # banner: main page only
    return embed, view
