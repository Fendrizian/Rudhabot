"""/tcg: Asgrelia: Cloudfall, the trading card game. Commands are thin: they call TcgService / OracleService and render.

Matches run in their own thread when the channel allows it. The public board shows both sides (never a hand) and the
Oracle's spectator-safe win estimate; players act through an ephemeral menu of the engine's legal moves.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rudhabot.cogs.base import ServiceCog
from rudhabot.services.tcg import TcgError, deck_text, parse_deck_text
from rudhabot.tcg import engine as E
from rudhabot.tcg.cards import CARD_BY_ID, CARDS, find_card
from rudhabot.tcg.decks import ARCHETYPES
from rudhabot.tcg.rules_text import RULES
from rudhabot.ui.tcg_embeds import (CLOUD, board_embed, card_embed, card_line, collection_embed, deck_embed,
                                    doctor_embed, hand_embed)
from rudhabot.views.tcg_match import ChallengeView, DeckModal, MatchButton, MovePicker, board_view

log = logging.getLogger("rudhabot.tcg")
ARCHETYPE_CHOICES = [app_commands.Choice(name=f"{a['name']}: {a['plan']}"[:100], value=k) for k, a in ARCHETYPES.items()]
DIFFICULTY_CHOICES = [app_commands.Choice(name=n.title(), value=n) for n in ("easy", "normal", "hard")]
RULE_CHOICES = [app_commands.Choice(name=t, value=t) for t in RULES]
NO_MENTIONS = discord.AllowedMentions.none()


def _note(text: str, color: int = CLOUD) -> discord.Embed:
    return discord.Embed(description=text, color=color)


async def _reply(interaction: discord.Interaction, embed: discord.Embed, ephemeral: bool = True, **kw) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral, allowed_mentions=NO_MENTIONS, **kw)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral, allowed_mentions=NO_MENTIONS, **kw)


class TcgCog(ServiceCog, commands.GroupCog, group_name="tcg", group_description="Asgrelia: Cloudfall trading card game"):
    deck = app_commands.Group(name="deck", description="Build, view and test your Cloudfall decks")

    def __init__(self, bot):
        super().__init__(bot)
        self.tcg = self.services.tcg
        self.oracle = self.services.oracle
        self.assistant = self.services.assistant

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(MatchButton)
        restored = self.tcg.restore()
        if restored:
            log.info("restored %d live Cloudfall match(es)", restored)
        self.sweeper.start()

    async def cog_unload(self) -> None:
        self.sweeper.cancel()

    # ================================================================== collection
    @app_commands.command(name="starter", description="Claim your free starter deck (one of the six Codex archetypes)")
    @app_commands.choices(deck=ARCHETYPE_CHOICES)
    async def starter(self, interaction: discord.Interaction, deck: app_commands.Choice[str]):
        try:
            profile = self.tcg.claim_starter(interaction.user.id, deck.value)
        except TcgError as error:
            return await _reply(interaction, _note(str(error), 0xE74C3C))
        arche = ARCHETYPES[deck.value]
        embed = deck_embed(arche["name"], list(arche["cards"]))
        embed.description = (f"Welcome to Asgrelia! **{arche['name']}** is now your active deck.\n*{arche['plan']}*\n"
                             "Try `/tcg practice` against the Oracle, or `/tcg challenge` a friend.")
        await _reply(interaction, embed, ephemeral=False)

    @app_commands.command(name="collection", description="Show your Cloudfall cards, decks and record")
    async def collection(self, interaction: discord.Interaction):
        await _reply(interaction, collection_embed(interaction.user, self.tcg.profile(interaction.user.id)))

    @app_commands.command(name="pack", description="Open a 5-card Cloudfall pack (paid from your wallet)")
    async def pack(self, interaction: discord.Interaction):
        try:
            pulled = self.tcg.open_pack(interaction.user.id)
        except TcgError as error:
            return await _reply(interaction, _note(str(error), 0xE74C3C))
        embed = discord.Embed(title="🎴 Cloudfall pack", color=CLOUD,
                              description="\n".join(card_line(CARD_BY_ID[c]) for c in pulled))
        embed.set_footer(text=f"Price {self.tcg.terms.pack_price} · pulls are random, drawn by Rudhabot")
        await _reply(interaction, embed, ephemeral=False)

    @app_commands.command(name="card", description="Look up a Cloudfall card")
    async def card(self, interaction: discord.Interaction, name: str):
        card = find_card(name)
        if card is None:
            return await _reply(interaction, _note("No card by that name."))
        await _reply(interaction, card_embed(card, self.oracle.similar_cards(card.id)), ephemeral=False)

    @card.autocomplete("name")
    async def _card_autocomplete(self, interaction: discord.Interaction, current: str):
        q = current.lower()
        hits = [c for c in CARDS if q in c.name.lower() or q == c.id.lower()][:25]
        return [app_commands.Choice(name=f"{c.name} ({c.id})", value=c.id) for c in hits]

    @app_commands.command(name="rules", description="Read the Cloudfall rules")
    @app_commands.choices(topic=RULE_CHOICES)
    async def rules(self, interaction: discord.Interaction, topic: Optional[app_commands.Choice[str]] = None):
        if topic is None:
            embed = discord.Embed(title="📖 Asgrelia: Cloudfall, quick rules", color=CLOUD,
                                  description=RULES["Overview"] + "\n\nPick a topic with `/tcg rules topic:` or ask `/ask`.")
            embed.add_field(name="Topics", value=", ".join(RULES), inline=False)
        else:
            embed = discord.Embed(title=f"📖 {topic.value}", description=RULES[topic.value], color=CLOUD)
        await _reply(interaction, embed)

    @app_commands.command(name="ladder", description="The Cloudfall rated ladder")
    async def ladder(self, interaction: discord.Interaction):
        rows = self.tcg.ladder()
        lines = [f"**{i + 1}.** <@{uid}> · **{p['rating']}** · {p['wins']}W {p['losses']}L" for i, (uid, p) in enumerate(rows)]
        await _reply(interaction, discord.Embed(title="🏆 Cloudfall ladder", description="\n".join(lines) or "No rated games yet.",
                                                color=CLOUD), ephemeral=False)

    @app_commands.command(name="forget-me", description="Delete your Cloudfall profile, collection and rating")
    async def forget_me(self, interaction: discord.Interaction):
        try:
            removed = self.tcg.forget_me(interaction.user.id)
        except TcgError as error:
            return await _reply(interaction, _note(str(error), 0xE74C3C))
        await _reply(interaction, _note("Your Cloudfall data was deleted." if removed else "You had no Cloudfall data."))

    # ================================================================== decks
    @deck.command(name="build", description="Create or edit a deck from your collection")
    async def deck_build(self, interaction: discord.Interaction, name: Optional[str] = None):
        profile = self.tcg.profile(interaction.user.id)
        base = name or profile["active_deck"] or ""
        text = deck_text(profile["decks"].get(base, [])) if base in profile["decks"] else ""
        await interaction.response.send_modal(DeckModal(self, base, text))

    async def save_deck_from_modal(self, interaction: discord.Interaction, name: str, text: str) -> None:
        cards, unknown = parse_deck_text(text)
        problems = self.tcg.save_deck(interaction.user.id, name, cards)
        if unknown:
            problems = [f"Unrecognised: {', '.join(unknown[:5])}"] + problems
        embed = deck_embed(name, cards, problems)
        if not problems:
            embed.description = "✅ Saved and set as your active deck."
        await _reply(interaction, embed)

    @deck.command(name="view", description="Show one of your decks")
    async def deck_view(self, interaction: discord.Interaction, name: Optional[str] = None):
        profile = self.tcg.profile(interaction.user.id)
        name = name or profile["active_deck"]
        if not name or name not in profile["decks"]:
            return await _reply(interaction, _note("No such deck. See `/tcg collection`."))
        await _reply(interaction, deck_embed(name, profile["decks"][name]))

    @deck.command(name="use", description="Set your active deck")
    async def deck_use(self, interaction: discord.Interaction, name: str):
        try:
            self.tcg.use_deck(interaction.user.id, name)
        except TcgError as error:
            return await _reply(interaction, _note(str(error), 0xE74C3C))
        await _reply(interaction, _note(f"**{name}** is now your active deck."))

    @deck_view.autocomplete("name")
    @deck_use.autocomplete("name")
    async def _deck_autocomplete(self, interaction: discord.Interaction, current: str):
        decks = self.tcg.profile(interaction.user.id)["decks"]
        return [app_commands.Choice(name=d, value=d) for d in decks if current.lower() in d.lower()][:25]

    @deck.command(name="analyze", description="Deck Doctor: the Oracle tests your deck against every archetype")
    async def deck_analyze(self, interaction: discord.Interaction):
        try:
            cards = self.tcg.active_deck(interaction.user.id)
        except TcgError as error:
            return await _reply(interaction, _note(str(error), 0xE74C3C))
        await interaction.response.defer(ephemeral=True, thinking=True)
        profile = self.tcg.profile(interaction.user.id)
        report = await self.oracle.gauntlet(cards)
        swaps = self.oracle.suggest_swaps(cards, profile["collection"])
        await interaction.followup.send(embed=doctor_embed(profile["active_deck"], report, swaps), ephemeral=True)

    # ================================================================== matches
    @app_commands.command(name="practice", description="Play a practice match against the Oracle AI (unrated, coaching on)")
    @app_commands.choices(difficulty=DIFFICULTY_CHOICES, opponent=ARCHETYPE_CHOICES)
    async def practice(self, interaction: discord.Interaction, difficulty: Optional[app_commands.Choice[str]] = None,
                       opponent: Optional[app_commands.Choice[str]] = None):
        try:
            match = self.tcg.create_practice(interaction.user.id, interaction.user.display_name,
                                             difficulty.value if difficulty else "normal",
                                             opponent.value if opponent else None)
        except TcgError as error:
            return await _reply(interaction, _note(str(error), 0xE74C3C))
        await interaction.response.defer(thinking=True)
        try:
            await self.tcg.run_ai(match)
            posted = await self._post_board(interaction, match, f"🔮 {interaction.user.display_name} vs the Oracle")
        except Exception:
            self.tcg.cancel(match.id)                  # never leave the player stuck in a match they can't see
            raise
        if not posted:
            await interaction.followup.send(embed=_note("I couldn't show a match board here, so the match was cancelled. "
                                                        "Try another channel.", 0xE74C3C), ephemeral=True)

    @app_commands.command(name="challenge", description="Challenge another player to a Cloudfall duel")
    async def challenge(self, interaction: discord.Interaction, opponent: discord.Member, coaching: bool = False):
        if opponent.bot or opponent.id == interaction.user.id:
            return await _reply(interaction, _note("Pick another (human) player. For a bot opponent use `/tcg practice`."))
        for user in (interaction.user, opponent):
            try:
                self.tcg.active_deck(user.id)
            except TcgError:
                return await _reply(interaction, _note(f"{user.display_name} needs a legal deck first (`/tcg starter`)."))
        view = ChallengeView(self, interaction.user, opponent, coaching)
        text = (f"⚔️ {opponent.mention}, **{interaction.user.display_name}** challenges you to Asgrelia: Cloudfall"
                + (" (coaching on, unrated)." if coaching else " (rated)."))
        await interaction.response.send_message(text, view=view,
                                                allowed_mentions=discord.AllowedMentions(users=[opponent]))

    async def start_challenge(self, interaction: discord.Interaction, challenger, opponent, coach: bool) -> None:
        try:
            match = self.tcg.create_pvp(challenger.id, challenger.display_name, opponent.id, opponent.display_name,
                                        coach=coach)
        except TcgError as error:
            return await interaction.response.edit_message(content=f"❌ {error}", view=None)
        await interaction.response.edit_message(content=f"⚔️ {challenger.display_name} vs {opponent.display_name}: "
                                                        "the duel begins!", view=None)
        try:
            posted = await self._post_board(interaction, match, f"⚔️ {challenger.display_name} vs {opponent.display_name}")
        except Exception:
            self.tcg.cancel(match.id)
            raise
        if not posted:
            await interaction.followup.send(content="I couldn't show the match board here, so the duel was cancelled.")

    @app_commands.command(name="match", description="Re-post the board of your current match")
    async def match_cmd(self, interaction: discord.Interaction):
        match = self.tcg.match_for(interaction.user.id)
        if match is None:
            return await _reply(interaction, _note("You're not in a match."))
        await interaction.response.defer()
        await self._post_board(interaction, match, None, in_place=True)

    @app_commands.command(name="forfeit", description="Concede your current Cloudfall match")
    async def forfeit(self, interaction: discord.Interaction):
        match = self.tcg.match_for(interaction.user.id)
        if match is None:
            return await _reply(interaction, _note("You're not in a match."))
        summary = await self.tcg.forfeit(match.id, interaction.user.id)
        await _reply(interaction, _note("You conceded."))
        await self._announce_result(match, summary, interaction)

    # ------------------------------------------------------------------ board plumbing
    async def _post_board(self, interaction: discord.Interaction, match, thread_title: Optional[str],
                          in_place: bool = False) -> bool:
        """Post the public board, preferring (1) a new thread, (2) the channel, (3) the interaction itself.

        (3) needs no channel permissions at all, so a match can always start. If even that fails, the match is
        cancelled so the player isn't left stuck in a game nobody can see. Returns whether the board was posted.
        """
        channel = interaction.channel
        perms = getattr(interaction, "app_permissions", None)
        can = lambda *names: perms is None or all(getattr(perms, n, False) for n in names)
        embed = await self._board(match)
        view = board_view(match.id, match.record.get("coach"))
        message, target, mode = None, None, None
        if thread_title and isinstance(channel, discord.TextChannel) and can(
                "create_public_threads", "send_messages_in_threads", "embed_links"):
            thread = None
            try:
                thread = await channel.create_thread(name=thread_title[:90], type=discord.ChannelType.public_thread,
                                                     auto_archive_duration=1440)
                message, target, mode = await thread.send(embed=embed, view=view, allowed_mentions=NO_MENTIONS), thread, "thread"
            except discord.HTTPException as error:
                log.warning("match %s: no thread (%s %s); trying the channel", match.id, error.status, error.text)
                if thread is not None:                 # don't leave an empty thread behind
                    try:
                        await thread.delete()
                    except discord.HTTPException:
                        pass
        if message is None and channel is not None and hasattr(channel, "send") and can("send_messages", "embed_links"):
            try:
                message, target, mode = await channel.send(embed=embed, view=view, allowed_mentions=NO_MENTIONS), channel, "channel"
            except discord.HTTPException as error:
                log.warning("match %s: can't post in channel (%s %s); replying instead", match.id, error.status, error.text)
        if message is None:
            try:
                message = await interaction.followup.send(embed=embed, view=view, wait=True, allowed_mentions=NO_MENTIONS)
                target, mode = channel, "reply"
            except discord.HTTPException as error:
                log.error("match %s: could not show the board at all (%s %s)", match.id, error.status, error.text)
                if not in_place:                       # a brand-new match nobody can see: cancel it
                    self.tcg.cancel(match.id)
                return False
        match.record["channel_id"] = getattr(target, "id", None) or getattr(message, "channel_id", None)
        match.record["message_id"] = getattr(message, "id", None)
        match.record["board_mode"] = mode
        self.tcg._persist(match)
        if mode == "thread":
            note = f"Your match board is ready in {target.mention}."
        elif mode == "channel":
            note = "Your match board is below." + (" (I need *Create Public Threads* to give each match its own thread.)"
                                                  if thread_title and not can("create_public_threads") else "")
        else:
            note = ("I can't post in this channel, so the board is attached to this reply. Press **Act** to play; it "
                    "refreshes the board. (Give me *Send Messages* and *Embed Links* here for live updates.)")
        await interaction.followup.send(content=note, ephemeral=True)
        return True

    async def _board(self, match) -> discord.Embed:
        win = await self.oracle.public_win_probability(match.game, 0)
        return board_embed(match.game, match.record, win, self.oracle.version)

    async def _refresh(self, match, finished: bool = False) -> bool:
        """Edit the public board in place. Returns False when that isn't possible (missing access, deleted message)."""
        channel_id, message_id = match.record.get("channel_id"), match.record.get("message_id")
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if channel is None or not message_id:
            return False
        try:
            await channel.get_partial_message(message_id).edit(
                embed=await self._board(match), view=board_view(match.id, match.record.get("coach"), finished))
            return True
        except discord.HTTPException as error:
            log.info("match %s: board not refreshed (%s %s)", match.id, error.status, error.text)
            return False

    async def _announce_result(self, match, summary: Optional[dict], interaction: Optional[discord.Interaction] = None) -> None:
        if summary is None:
            return
        await self._refresh(match, finished=True)
        g = match.game
        lines = [f"🏁 **{g.end_reason}.** " + ("Draw!" if g.winner == -1 else f"**{summary['names'][g.winner]}** wins!")]
        for uid, delta in summary.get("rating", {}).items():
            lines.append(f"<@{uid}> rating {delta:+d}")
        for uid, amount in summary.get("rewards", {}).items():
            lines.append(f"<@{uid}> earned {amount} for the win")
        if self.services.settings.ai.chronicle_enabled:
            record = summary["record"]
            try:
                tps = await self.oracle._run("turning_points", lambda: self.oracle.turning_points_sync(
                    g, record["decks"], record["names"], record["seed"], record["keys"], record.get("level", 3),
                    record.get("first")), lambda: [])
            except Exception:
                tps = []
            facts = {"names": summary["names"], "winner": g.winner, "reason": g.end_reason, "rounds": g.round,
                     "final_resolve": [p.resolve for p in g.players],
                     "turning_points": [{k: tp[k] for k in ("round", "player", "label")} for tp in tps]}
            lines.append("📜 " + await self.assistant.chronicle(facts))
        text = "\n".join(lines)[:2000]
        channel = self.bot.get_channel(match.record.get("channel_id") or 0)
        if channel is not None:
            try:
                await channel.send(text, allowed_mentions=NO_MENTIONS)
                return
            except discord.HTTPException as error:
                log.info("match %s: result not posted in channel (%s)", match.id, error.status)
        if interaction is not None:
            try:
                await interaction.followup.send(content=text, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                log.warning("match %s: result could not be announced", match.id)

    # ------------------------------------------------------------------ button & picker callbacks
    async def on_board_button(self, interaction: discord.Interaction, op: str, match_id: str) -> None:
        match = self.tcg.get(match_id)
        if match is None:
            return await _reply(interaction, _note("This match has ended."))
        seat = match.seat_of(interaction.user.id)
        if op == "log":
            return await _reply(interaction, discord.Embed(title="📜 Match log", color=CLOUD,
                                                           description="\n".join(match.game.log[-25:])[-4000:] or "—"))
        if seat is None:
            return await _reply(interaction, _note("You're watching this match. Enjoy the show!"))
        if op == "hand":
            return await _reply(interaction, hand_embed(match.game, seat))
        if E.decider(match.game) != seat:
            return await _reply(interaction, _note("Waiting for your opponent."))
        if op == "hint":
            if not match.record.get("coach"):
                return await _reply(interaction, _note("Coaching is off in this match (rated games are unassisted)."))
            await interaction.response.defer(ephemeral=True, thinking=True)
            hints = await self.oracle.hints(match.game)
            text = "\n".join(f"**{h.label}** · win {round(h.win * 100)}% ({h.delta * 100:+.0f})" for h in hints) or "No hint."
            embed = discord.Embed(title="🔮 Oracle's top moves", description=text, color=CLOUD)
            embed.set_footer(text=f"{self.oracle.version} · estimates, not guarantees · only you can see this")
            return await interaction.followup.send(embed=embed, ephemeral=True)
        acts = E.legal_actions(match.game)
        embed = _note(match.game.pending[0]["text"] if match.game.pending else "Choose your move.")
        picker = MovePicker(self, match_id, match.version, acts)
        board_message = getattr(interaction, "message", None)
        if board_message is not None and board_message.id == match.record.get("message_id"):
            # answering the click by editing the board keeps it current even without channel permissions
            await interaction.response.edit_message(embed=await self._board(match),
                                                    view=board_view(match.id, match.record.get("coach")))
            await interaction.followup.send(embed=embed, view=picker, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=picker, ephemeral=True)

    async def on_move(self, interaction: discord.Interaction, match_id: str, key: str, label: str, version: int) -> None:
        try:
            result = await self.tcg.act(match_id, interaction.user.id, key, version)
        except TcgError as error:
            return await interaction.response.edit_message(embed=_note(str(error), 0xE74C3C), view=None)
        match = result.match
        if result.finished is None and match.decider_is_ai:
            await interaction.response.edit_message(embed=_note(f"✅ {label}\n🔮 The Oracle is thinking…"), view=None)
            result = await self.tcg.run_ai(match)
        else:
            await interaction.response.edit_message(embed=_note(f"✅ {label}"), view=None)
        if result.finished is not None:
            await self._announce_result(match, result.finished, interaction)
            return
        refreshed = await self._refresh(match)
        seat = match.seat_of(interaction.user.id)
        embeds = [] if refreshed else [await self._board(match)]     # can't update the public board: show it privately
        if E.decider(match.game) == seat:              # still your decision: offer the next menu right away
            acts = E.legal_actions(match.game)
            prompt = match.game.pending[0]["text"] if match.game.pending else "Your move."
            await interaction.followup.send(embeds=embeds + [_note(prompt)],
                                            view=MovePicker(self, match.id, match.version, acts), ephemeral=True)
        elif embeds:
            await interaction.followup.send(embeds=embeds, ephemeral=True)

    # ------------------------------------------------------------------ idle players
    @tasks.loop(seconds=30)
    async def sweeper(self) -> None:
        try:
            for result in await self.tcg.sweep_timeouts():
                if result.finished is not None:
                    await self._announce_result(result.match, result.finished)
                    continue
                if result.match.decider_is_ai:
                    ai = await self.tcg.run_ai(result.match)
                    if ai.finished is not None:
                        await self._announce_result(result.match, ai.finished)
                        continue
                await self._refresh(result.match)
        except Exception:
            log.exception("Cloudfall timeout sweep failed")

    @sweeper.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()
