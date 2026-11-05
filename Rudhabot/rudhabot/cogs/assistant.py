"""/ask and @Rudhabot: understand what someone wants, then help.

Routing (fast and free first, then the language model):
  1. Clairvoyance's intent router reads the message (offline, ~1 ms).
  2. Confident it's a bot task -> offer that command as a button.        Unsure between tasks -> ask which one, with
     buttons for the top options.                                         General conversation -> the OpenAI assistant
     (or Rudhabot's local docs when OpenAI isn't configured).
  3. A button runs the *real* slash command: its own checks (permissions, "not in a game", ...) and its own logic, for
     the person who asked only. Commands that need details (amounts, bets) are never run with guessed details: the
     card shows the command to type, pre-filled with whatever the message said (`ml/intent_args.py`).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.ml import intent_args
from rudhabot.ml.intent import IntentRouter
from rudhabot.ml.intent_data import INTENTS
from rudhabot.services.audit import set_context
from rudhabot.ui.branding import brand
from rudhabot.ui.theme import Colors

log = logging.getLogger("rudhabot.assistant")
ASK_COLOR = Colors.CLAIRVOYANCE
EXTRA_ARGS = {"games": {"section": app_commands.Choice(name="Gambling games", value="games")}}
MENTION = re.compile(r"<@!?\d+>")


class RunIntent(ui.DynamicItem[ui.Button], template=r"run:(?P<uid>\d+):(?P<intent>[a-z_]+)"):
    """Runs (or explains) the command behind an intent, for the person it was offered to."""

    def __init__(self, user_id: int, intent: str, primary: bool = True):
        description, command, runnable = INTENTS[intent]
        label = (f"Run /{command}" if runnable else f"How to /{command}")[:80]
        super().__init__(ui.Button(label=label, style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary,
                                   custom_id=f"run:{user_id}:{intent}"))
        self.user_id, self.intent = user_id, intent

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /):
        return cls(int(match["uid"]), match["intent"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(content="This suggestion was for someone else. Try `/ask` yourself!",
                                                           ephemeral=True)
        router = getattr(getattr(interaction.client, "services", None), "intents", None)
        if router is not None:
            router.record("run", self.intent)
        await run_intent(interaction, self.intent)


def find_command(tree: app_commands.CommandTree, path: str):
    parts = path.split()
    command = tree.get_command(parts[0])
    for part in parts[1:]:
        command = command.get_command(part) if command is not None else None
    return command


async def run_intent(interaction: discord.Interaction, intent: str) -> None:
    description, path, runnable = INTENTS[intent]
    command = find_command(interaction.client.tree, path)
    if not runnable or command is None or isinstance(command, app_commands.Group):
        params = " ".join(f"<{p.name}>" if p.required else f"[{p.name}]" for p in getattr(command, "parameters", []))
        usage = f"/{path} {params}".strip()
        return await interaction.response.send_message(content=f"To {description}, use `{usage}`.", ephemeral=True)
    interaction.command = command
    set_context(f"/{command.qualified_name} (via assistant)", interaction.user.id, "command")
    try:
        await command._check_can_run(interaction)             # the command's own checks, exactly as if typed
        await command.callback(command.binding, interaction, **EXTRA_ARGS.get(intent, {}))
    except app_commands.AppCommandError as error:
        await interaction.client.tree.on_error(interaction, error)
    except Exception as error:
        await interaction.client.tree.on_error(interaction, app_commands.CommandInvokeError(command, error))


def prefilled(tree: Optional[app_commands.CommandTree], path: str, text: str, resolve_item=None) -> Optional[str]:
    """The command to type, with the arguments read from the message (`intent_args`), or None if it takes none or
    nothing could be read. Shown as text only: nothing runs with arguments the person didn't type into the command."""
    command = find_command(tree, path) if tree is not None else None
    params = list(getattr(command, "parameters", []) or [])
    if command is None or isinstance(command, app_commands.Group) or not params:
        return None
    spec = [(p.name, [(c.name, str(c.value)) for c in (p.choices or [])]) for p in params]
    values = intent_args.extract(text, spec, resolve_item)
    return intent_args.usage(path, [(p.name, p.required) for p in params], values) if values else None


def suggestion(router: IntentRouter, user_id: int, text: str, tree: Optional[app_commands.CommandTree] = None,
               resolve_item=None):
    """(embed, view) offering the command(s) the message most likely means, or None for general conversation.
    With the command tree, a command that needs arguments also shows them pre-filled from the message."""
    route = router.route(text)
    if route is None or route.intent == "chat":
        return None
    if route.confidence >= router.confident:
        description, path, runnable = INTENTS[route.intent]
        typed = None if runnable else prefilled(tree, path, text, resolve_item)
        embed = discord.Embed(description=f"It looks like you want to **{description}**."
                                          + (f"\nType `{typed}` to do it." if typed else ""), color=ASK_COLOR)
        view = ui.View(timeout=None)
        view.add_item(RunIntent(user_id, route.intent))
        router.record("offered", route.intent)
    elif route.confidence >= IntentRouter.CLARIFY:
        options = [route.intent] + [i for i, p in route.alternatives if i != "chat" and p >= 0.08][:2]
        embed = discord.Embed(description="I'm not quite sure what you mean. Did you want to:\n"
                                          + "\n".join(f"• {INTENTS[i][0]} (`/{INTENTS[i][1]}`)" for i in options),
                              color=ASK_COLOR)
        view = ui.View(timeout=None)
        for n, intent in enumerate(options):
            view.add_item(RunIntent(user_id, intent, primary=n == 0))
        router.record("clarified", route.intent)
    else:
        return None
    embed.set_footer(text=f"understood locally, {route.confidence:.0%} sure · or just ask me in more words")
    return brand(embed), view


def answer_embed(answer, question: str) -> discord.Embed:
    embed = discord.Embed(description=answer.text, color=ASK_COLOR)
    embed.set_author(name=f"Rudhabot · {question[:200]}")
    if answer.commands:
        embed.add_field(name="Try", value="  ".join(f"`{c}`" for c in answer.commands), inline=False)
    if answer.notice:
        embed.add_field(name="Note", value=answer.notice, inline=False)
    if answer.source == "openai":
        embed.set_footer(text=f"AI-generated with OpenAI ({answer.model}) from Rudhabot's docs. It can be wrong; "
                              "game rules and balances are decided by Rudhabot's code. Nothing is stored.")
    else:
        embed.set_footer(text="Answered from Rudhabot's built-in docs (no AI).")
    return embed


class AssistantCog(ServiceCog):
    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(RunIntent)

    def _resolve_item(self, name: str):
        catalog = getattr(self.services, "catalog", None)
        return catalog.resolve(name) if catalog is not None else None

    def _hidden(self, user, guild_id) -> frozenset:
        """Help entries this person can't use and guidebook chapters they can't read: /ask never cites them."""
        from rudhabot.services.permissions import viewer_for
        from rudhabot.views.guide import hidden_chapter_ids
        from rudhabot.views.help import hidden_commands
        viewer = viewer_for(self.services, user, guild_id)
        return frozenset(hidden_commands(viewer) | hidden_chapter_ids(viewer))

    @app_commands.command(name="ask", description="Ask Rudhabot anything, or say what you want to do")
    @app_commands.describe(question="e.g. 'show my inventory' or 'how does Corruption work?'", private="Only you see the answer")
    async def ask(self, interaction: discord.Interaction, question: app_commands.Range[str, 3, 500], private: bool = True):
        routed = suggestion(self.services.intents, interaction.user.id, question, self.bot.tree, self._resolve_item)
        if routed is not None:
            embed, view = routed
            return await interaction.response.send_message(embed=embed, view=view, ephemeral=private)
        await interaction.response.defer(ephemeral=private, thinking=True)
        answer = await self.services.assistant.ask(interaction.user.id, interaction.guild_id, question,
                                                   hidden=self._hidden(interaction.user, interaction.guild_id))
        await interaction.followup.send(embed=answer_embed(answer, question), ephemeral=private,
                                        allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """@Rudhabot <anything>: the same routing, answered in the channel."""
        if message.author.bot or self.bot.user is None or self.bot.user not in getattr(message, "mentions", []):
            return
        text = MENTION.sub("", message.content).strip()
        if not text:
            text = "help"
        try:
            routed = suggestion(self.services.intents, message.author.id, text, self.bot.tree, self._resolve_item)
            if routed is not None:
                embed, view = routed
                await message.reply(embed=embed, view=view, mention_author=False)
                return
            async with message.channel.typing():
                guild_id = getattr(message.guild, "id", None)
                answer = await self.services.assistant.ask(message.author.id, guild_id, text,
                                                           hidden=self._hidden(message.author, guild_id))
            await message.reply(embed=answer_embed(answer, text), mention_author=False,
                                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as error:
            log.info("could not answer a mention (%s %s)", error.status, error.text)
