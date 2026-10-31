"""/ask: Rudhabot's assistant. Answers from Rudhabot's own docs; uses OpenAI when configured, local retrieval otherwise."""
from __future__ import annotations

import discord
from discord import app_commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.ui.theme import BOTICON

ASK_COLOR = 0x5865F2


class AssistantCog(ServiceCog):
    @app_commands.command(name="ask", description="Ask Rudhabot how something works (commands, Cloudfall rules, cards)")
    @app_commands.describe(question="Your question, e.g. 'how does Corruption work?'", private="Only you see the answer")
    async def ask(self, interaction: discord.Interaction, question: app_commands.Range[str, 3, 500], private: bool = True):
        assistant = self.services.assistant
        await interaction.response.defer(ephemeral=private, thinking=True)
        answer = await assistant.ask(interaction.user.id, interaction.guild_id, question)
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
        await interaction.followup.send(embed=embed, ephemeral=private, allowed_mentions=discord.AllowedMentions.none())
