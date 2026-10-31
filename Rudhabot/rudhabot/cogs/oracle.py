"""/oracle: health and model management for the in-house Oracle model (administrators only)."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from rudhabot.cogs.base import ServiceCog
from rudhabot.cogs.checks import admin_only


class OracleCog(ServiceCog, commands.GroupCog, group_name="oracle", group_description="Rudhabot's in-house ML model"):
    @app_commands.command(name="status", description="Model version, health, measured quality and live latency")
    @admin_only()
    async def status(self, interaction: discord.Interaction):
        oracle = self.services.oracle
        st = oracle.status()
        embed = discord.Embed(title="🔮 Oracle status", color=0x57F287 if st["ready"] else 0xE67E22)
        embed.add_field(name="Model", value=f"`{st['model']}`" + ("" if st["ready"] else f"\n⚠️ {st['load_error']}"), inline=False)
        if st.get("params"):
            embed.add_field(name="Size", value=f"{st['params']:,} parameters", inline=True)
        m = st.get("metrics")
        if m:
            embed.add_field(name="Held-out quality", value=f"log-loss {m['log_loss']:.3f} · Brier {m['brier']:.3f} · "
                                                         f"ECE {m['ece']:.3f} · AUC {m['auc']:.3f}", inline=False)
        s = st.get("strength")
        if s:
            embed.add_field(name="Strength", value=f"vs heuristic {s['vs_heuristic']['score']:.0%} · vs random "
                                                   f"{s['vs_random']['score']:.0%}", inline=False)
        calls = ", ".join(f"{k} {v}" for k, v in sorted(st["calls"].items())) or "none yet"
        embed.add_field(name="Calls since start", value=calls[:1024], inline=False)
        embed.add_field(name="Latency", value=f"p50 {st['latency_ms_p50']:.1f} ms · p95 {st['latency_ms_p95']:.1f} ms", inline=False)
        a = self.services.assistant
        state = f"on · `{a.model.model}`" if a.enabled else f"off: {a.disabled_reason}"
        if a.last_error:
            state += f"\nlast error: `{a.last_error[:200]}`"
        embed.add_field(name="OpenAI assistant", value=(state + f"\ncalls: {a.stats}")[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="reload", description="Reload the Oracle model (optionally a specific version, for rollback)")
    @admin_only()
    async def reload(self, interaction: discord.Interaction, version: str = ""):
        ok = self.services.oracle.reload(version or None)
        text = f"✅ Loaded `{self.services.oracle.version}`." if ok else f"⚠️ {self.services.oracle.load_error}; using the heuristic."
        await interaction.response.send_message(text, ephemeral=True)
