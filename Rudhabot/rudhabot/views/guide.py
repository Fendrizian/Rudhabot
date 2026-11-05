"""/guide: the Rudhabot Guidebook (rudhabot/assets/guidebook.md), chapter by chapter, for one reader at a time.

Members see Part 1; staff also see the Part 2 chapters for their rank (`{staff}`, `{banker}`, `{helper}`, `{admin}`
on a chapter heading, the same requirements as /help). Long chapters are split into pages that fit an embed.

Navigation uses DynamicItems with fixed custom ids ("guide:..."), so the menus never time out and keep working after a
restart. Previous / Next step through pages and on into the next chapter the reader can see; someone else clicking
gets their own private copy, rendered for them.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import discord
from discord import ButtonStyle, Interaction, ui

from rudhabot.services.permissions import MEMBER, REQUIREMENTS, Viewer, viewer_for
from rudhabot.ui.theme import Colors
from rudhabot.views.navigation import show_for_clicker

GUIDE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "guidebook.md")
PAGE_CHARS = 3800                     # an embed description holds 4096
COLOR = Colors.HELP
ICON = "📖"
TITLE = "Rudhabot Guidebook"
_HEADING = re.compile(r"^### (?P<title>.+?)(?:\s+\{(?P<requires>[a-z]+)\})?\s*$")


@dataclass(frozen=True)
class Chapter:
    key: str
    part: str
    title: str
    requires: Optional[str]
    text: str
    pages: tuple[str, ...] = field(default=())

    @property
    def summary(self) -> str:
        """Its opening sentence without markdown, short enough for a menu line."""
        first = self.text.split("\n\n", 1)[0].replace("\n", " ")
        first = re.sub(r"[*_`]", "", re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0])
        return first if len(first) <= 100 else first[:99].rstrip() + "…"


def slug(title: str) -> str:
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


def paginate(text: str, limit: int = PAGE_CHARS) -> tuple[str, ...]:
    """Split at blank lines (between paragraphs and lists) so each page fits; a single huge block is cut by lines."""
    pages, current = [], ""
    for block in text.split("\n\n"):
        pieces = [block] if len(block) <= limit else _by_lines(block, limit)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                pages.append(current)
                current = piece
    if current:
        pages.append(current)
    return tuple(pages) or ("",)


def _by_lines(block: str, limit: int) -> list[str]:
    out, current = [], ""
    for line in block.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            out.append(current)
            current = line[:limit]
    return out + ([current] if current else [])


def parse(markdown: str) -> tuple[str, list[Chapter]]:
    """(the lead paragraph, the chapters in order). HTML comments are notes for editors, never shown."""
    markdown = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    lead, part, chapters = "", "", []
    title = requires = None
    body: list[str] = []

    def close():
        if title is not None:
            text = "\n".join(body).strip()
            chapters.append(Chapter(slug(title), part, title, requires, text, paginate(text)))

    for line in markdown.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            close()
            title, body = None, []
            part = line[3:].strip()
            continue
        m = _HEADING.match(line)
        if m:
            close()
            title, requires, body = m["title"].strip(), m["requires"], []
            if requires is not None and requires not in REQUIREMENTS:
                raise ValueError(f"unknown requirement {{{requires}}} on chapter {title!r}")
            continue
        if title is None:
            if not part and line.strip():
                lead = f"{lead} {line.strip()}".strip()
            continue
        body.append(line)
    close()
    return lead, chapters


@lru_cache(maxsize=1)
def _load() -> tuple[str, tuple[Chapter, ...]]:
    try:
        with open(GUIDE_FILE, encoding="utf-8") as handle:
            lead, chapters = parse(handle.read())
    except OSError:
        return "The guidebook is missing from this installation.", ()
    return lead, tuple(chapters)


def lead() -> str:
    return _load()[0]


def chapters() -> tuple[Chapter, ...]:
    return _load()[1]


def visible_chapters(viewer: Viewer = MEMBER) -> list[Chapter]:
    return [c for c in chapters() if viewer.can(c.requires)]


def chapter(key: str) -> Optional[Chapter]:
    return next((c for c in chapters() if c.key == key), None)


def hidden_chapter_ids(viewer: Viewer) -> set[str]:
    """Knowledge-base ids of the chapters this reader can't see (so /ask never quotes them)."""
    return {f"guide:{c.key}" for c in chapters() if not viewer.can(c.requires)}


# ------------------------------------------------------------------ embeds
def contents_embed(viewer: Viewer = MEMBER) -> discord.Embed:
    embed = discord.Embed(title=f"{ICON} {TITLE}", color=COLOR,
                          description=lead() + "\n\nPick a chapter below, or press **Next** to read from the start.")
    shown = visible_chapters(viewer)
    parts: dict[str, list[Chapter]] = {}
    for c in shown:
        parts.setdefault(c.part, []).append(c)
    number = 0
    for part, items in parts.items():
        lines = []
        for c in items:
            number += 1
            lines.append(f"`{number}.` **{c.title}**")
        embed.add_field(name=part, value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text=f"{len(shown)} chapters for you")
    return embed


def chapter_embed(key: str, page: int, viewer: Viewer = MEMBER) -> discord.Embed:
    shown = visible_chapters(viewer)
    c = next(x for x in shown if x.key == key)
    page = max(0, min(page, len(c.pages) - 1))
    embed = discord.Embed(title=f"{ICON} {c.title}" + (f" ({page + 1}/{len(c.pages)})" if len(c.pages) > 1 else ""),
                          description=c.pages[page], color=COLOR)
    embed.set_footer(text=f"{TITLE} · {c.part} · chapter {shown.index(c) + 1} of {len(shown)}")
    return embed


def _sequence(viewer: Viewer) -> list[tuple[str, int]]:
    """Every page the reader can see, in reading order: (chapter key, page)."""
    return [(c.key, p) for c in visible_chapters(viewer) for p in range(len(c.pages))]


# ------------------------------------------------------------------ navigation (never expires)
def viewer_of(interaction: Interaction) -> Viewer:
    services = getattr(interaction.client, "services", None)
    if services is None:
        return MEMBER
    return viewer_for(services, interaction.user, getattr(interaction, "guild_id", None))


class ChapterSelect(ui.DynamicItem[ui.Select], template=r"guide:menu"):
    def __init__(self, current: Optional[str] = None, viewer: Viewer = MEMBER):
        options = [discord.SelectOption(label="Contents", value="home", emoji="📖", default=current == "home")]
        options += [discord.SelectOption(label=c.title[:100], value=c.key, description=c.summary,
                                         default=c.key == current) for c in visible_chapters(viewer)][:24]
        super().__init__(ui.Select(custom_id="guide:menu", placeholder="Jump to a chapter…", options=options))

    @classmethod
    async def from_custom_id(cls, interaction: Interaction, item: ui.Select, match: re.Match[str], /):
        return cls()

    async def callback(self, interaction: Interaction) -> None:
        embed, view = render(interaction.data["values"][0], 0, viewer_of(interaction))
        await show_for_clicker(interaction, embed, view)


class GuideButton(ui.DynamicItem[ui.Button], template=r"guide:(?P<target>[a-z0-9_]+):(?P<page>\d+)"):
    def __init__(self, target: str, page: int = 0, label: str = "", disabled: bool = False, emoji: Optional[str] = None):
        super().__init__(ui.Button(label=label, emoji=emoji, disabled=disabled, style=ButtonStyle.secondary,
                                   custom_id=f"guide:{target}:{page}"))
        self.target, self.page = target, page

    @classmethod
    async def from_custom_id(cls, interaction: Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(match["target"], int(match["page"]))

    async def callback(self, interaction: Interaction) -> None:
        embed, view = render(self.target, self.page, viewer_of(interaction))
        await show_for_clicker(interaction, embed, view)


def guide_view(current: str = "home", page: int = 0, viewer: Viewer = MEMBER) -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(ChapterSelect(current, viewer))
    order = _sequence(viewer)
    here = order.index((current, page)) if (current, page) in order else -1
    # custom ids must be unique within a message: the disabled ends use the contents target with spare page numbers
    if here > 0:
        view.add_item(GuideButton(*order[here - 1], label="◀ Previous"))
    else:
        view.add_item(GuideButton("home", 1, label="◀ Previous", disabled=True))
    if here + 1 < len(order):
        view.add_item(GuideButton(*order[here + 1], label="Next ▶"))
    else:
        view.add_item(GuideButton("home", 2, label="Next ▶", disabled=True))
    if current != "home":
        view.add_item(GuideButton("home", 0, label="Contents", emoji="📖"))
    return view


def render(target: str, page: int = 0, viewer: Viewer = MEMBER) -> tuple[discord.Embed, ui.View]:
    """One page for one reader. A chapter they can't see (or an unknown one) shows the contents."""
    c = chapter(target)
    if c is None or not viewer.can(c.requires):
        return contents_embed(viewer), guide_view("home", 0, viewer)
    page = max(0, min(page, len(c.pages) - 1))
    return chapter_embed(c.key, page, viewer), guide_view(c.key, page, viewer)
