"""Economy audit log and Clairvoyance review signals: posting to their channels, and the admin commands.

/audit recent [user]       the latest ledger entries (admins)
/review list [status]      Clairvoyance's review signals (admins)
/review resolve <id> ...   mark a signal dismissed or confirmed, with a note (admins). Nothing else happens:
                           signals never change balances or permissions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import admin_only
from rudhabot.ui.theme import BANKICON, CLAIRVOYANCEICON, TCG, Colors

log = logging.getLogger("rudhabot.audit")
ACCOUNT_ICONS = {"wallet": "👛", "bank": BANKICON, "savings": "🐷", "credit": "💳", "shards": TCG.SHARD, "dust": TCG.DUST}


def entry_line(e: dict) -> str:
    icon = ACCOUNT_ICONS.get(e["account"].split(":")[0], "📈" if e["account"].startswith("shares") else "🪙")
    actor = f" by <@{e['actor']}>" if e.get("actor") and e["actor"] != e["user"] else ""
    return (f"{icon} <@{e['user']}> **{e['delta']:+,}** {e['account']} → {e['after']:,} · {e['reason']}{actor} "
            f"`{e['ts'][11:19]}`")


class AuditCog(ServiceCog):
    def __init__(self, bot):
        super().__init__(bot)
        self.audit = self.services.audit
        self.reviews = self.services.reviews

    async def cog_load(self) -> None:
        self.poster.start()
        self.refitter.start()

    async def cog_unload(self) -> None:
        self.poster.cancel()
        self.refitter.cancel()

    # ------------------------------------------------------------------ channel posting
    @tasks.loop(seconds=15)
    async def poster(self) -> None:
        try:
            await self.flush()
        except Exception:
            log.exception("audit posting failed")

    @poster.before_loop
    async def _ready(self) -> None:
        await self.bot.wait_until_ready()

    async def flush(self) -> None:
        """Post queued audit entries (up to 20 per embed, 3 embeds per run) and any new review signals."""
        channels = self.services.settings.channels
        for _ in range(3):
            batch = self.audit.drain(20)
            if not batch:
                break
            embed = discord.Embed(title="🧾 Economy audit log", description="\n".join(entry_line(e) for e in batch)[:4096],
                                  color=Colors.NOTIFICATIONS)
            embed.set_footer(text=f"{len(batch)} change(s) · full history in ledger.jsonl")
            await self.services.messenger.post(channels.audit, embed=embed, allowed_mentions=discord.AllowedMentions.none())
        while self.reviews.pending:
            signal = self.reviews.pending.popleft()
            await self.services.messenger.post(channels.reviews, embed=review_embed(signal),
                                               allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(hours=6)
    async def refitter(self) -> None:
        try:
            await asyncio.to_thread(self.reviews.refit)
        except Exception:
            log.exception("anomaly model refit failed")

    @refitter.before_loop
    async def _ready2(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ admin commands
    @app_commands.command(name="audit", description="Admin: the latest economy audit entries")
    @app_commands.describe(user="Only this user's entries")
    @admin_only()
    async def audit_cmd(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        entries = self.audit.recent(20, user.id if user else None)
        embed = discord.Embed(title="🧾 Audit log" + (f" · {user.display_name}" if user else ""), color=Colors.NOTIFICATIONS,
                              description="\n".join(entry_line(e) for e in reversed(entries))[:4096] or "No entries.")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    review = app_commands.Group(name="review", description="Admin: Clairvoyance review signals")

    @review.command(name="list", description="Open (or all) review signals")
    @app_commands.choices(status=[app_commands.Choice(name=s, value=s) for s in ("open", "dismissed", "confirmed", "all")])
    @admin_only()
    async def review_list(self, interaction: discord.Interaction, status: Optional[app_commands.Choice[str]] = None):
        value = status.value if status else "open"
        items = self.reviews.signals(None if value == "all" else value)
        embed = discord.Embed(title=f"{CLAIRVOYANCEICON} Review signals ({value})", color=Colors.CLAIRVOYANCE,
                              description="\n\n".join(_signal_text(s) for s in items)[:4096] or "Nothing to review.")
        embed.set_footer(text="Signals are for human review only: nothing is changed automatically.")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @review.command(name="resolve", description="Mark a review signal dismissed or confirmed")
    @app_commands.choices(outcome=[app_commands.Choice(name="Dismiss (nothing wrong)", value="dismissed"),
                                   app_commands.Choice(name="Confirm (needs action by a moderator)", value="confirmed")])
    @admin_only()
    async def review_resolve(self, interaction: discord.Interaction, signal_id: str, outcome: app_commands.Choice[str],
                             note: str = ""):
        try:
            signal = self.reviews.resolve(signal_id.strip(), interaction.user.id, outcome.value, note)
        except KeyError:
            return await interaction.response.send_message("No signal with that id.", ephemeral=True)
        await interaction.response.send_message(f"Signal `{signal['id']}` marked **{signal['status']}**.", ephemeral=True)


def _signal_text(s: dict) -> str:
    return (f"`{s['id']}` <@{s['user']}> · {s['kind']} score {s['score']:.2f} · {s['hour'].replace('T', ' ')}h · "
            f"**{s['status']}**\n" + "\n".join(f"• {r}" for r in s["reasons"]))


def review_embed(signal: dict) -> discord.Embed:
    embed = discord.Embed(title=f"{CLAIRVOYANCEICON} Clairvoyance review signal", description=_signal_text(signal), color=Colors.CLAIRVOYANCE)
    embed.set_footer(text="Unusual activity worth a look. Nothing was changed. Resolve with /review resolve.")
    return embed
