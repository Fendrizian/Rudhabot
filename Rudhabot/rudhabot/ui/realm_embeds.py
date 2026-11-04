"""The weekly State of the Realm, as one Clairvoyance-branded embed."""
from __future__ import annotations

import discord

from rudhabot.ui.branding import brand
from rudhabot.ui.theme import CLAIRVOYANCEICON, CURRENCY, Colors


def _money(value) -> str:
    return f"{CURRENCY}{int(value):,}"


def _signed(value) -> str:
    return ("+" if value >= 0 else "−") + f"{CURRENCY}{abs(int(value)):,}"


def report_embed(r: dict) -> discord.Embed:
    embed = discord.Embed(title=f"{CLAIRVOYANCEICON} State of the Realm · {r['week']}", color=Colors.CLAIRVOYANCE,
                          description="The week across every system, read-only. Counts only: no message content.")
    eco = r.get("economy")
    if eco:
        lines = [f"Money supply {_money(eco['supply'])}"
                 + (f" ({_signed(eco['supply_change'])} this week)" if eco.get("supply_change") is not None else ""),
                 f"{eco['holders']:,} accounts hold money · credit owed {_money(eco['credit_owed'])} · "
                 f"loans owed {_money(eco['loans_owed'])}",
                 f"{eco['transactions']:,} wallet/bank movements · busiest day {eco['busiest']}"]
        if eco.get("sources"):
            lines.append("Came from: " + ", ".join(f"{k} {_signed(v)}" for k, v in eco["sources"][:4]))
        if eco.get("sinks"):
            lines.append("Went to: " + ", ".join(f"{k} {_signed(v)}" for k, v in eco["sinks"][:4]))
        embed.add_field(name="💰 Economy", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="💰 Economy", value="No daily snapshots yet: the first is taken after midnight.",
                        inline=False)
    sec = r.get("security")
    if sec:
        incidents = sum(sec["incidents"].values())
        lines = [f"Threat level {sec['level']} · mode **{sec['mode']}** · {incidents} incident(s)"
                 + (" (" + ", ".join(f"{n} {k}" for k, n in sorted(sec["incidents"].items())) + ")" if incidents else "")]
        if sec["done"]:
            lines.append("Carried out: " + ", ".join(f"{n}× {k.replace('_', ' ')}" for k, n in sec["done"].items()))
        if sec["shadow"]:
            lines.append("Shadow mode would have: " + ", ".join(f"{n}× {k.replace('_', ' ')}"
                                                                 for k, n in sec["shadow"].items()))
        if sec["verdicts"]:
            lines.append(f"Staff verdicts: {sec['verdicts']} · alerts {sec['precision']:.0%} real · model bar "
                         f"{sec['message_bar']:.2f}")
        if sec.get("spared_trusted"):
            lines.append(f"{sec['spared_trusted']} trusted member(s) left to staff instead of automatic action")
        embed.add_field(name="🛡️ Security", value="\n".join(lines)[:1024], inline=False)
    com = r.get("community")
    if com:
        embed.add_field(name="💬 Community", value=f"{com['members']:,} members posted {com['messages']:,} messages"
                        + (f" · busiest day {com['busiest']}" if com.get("busiest") else ""), inline=False)
    if eco and eco.get("tcg_results") is not None:
        embed.add_field(name="🃏 Cloudfall", value=f"{eco['tcg_results']:,} match results recorded", inline=False)
    cv = r.get("clairvoyance", {})
    intent = cv.get("intent") or {}
    embed.add_field(name=f"{CLAIRVOYANCEICON} Clairvoyance",
                    value=f"Cloudfall model {'ready' if cv.get('cloudfall') else 'off (heuristic)'} · anomaly bar "
                          f"{cv.get('anomaly_bar', 0):.2f} · {cv.get('signals_open', 0)} open review signal(s)\n"
                          f"Intent router: {intent.get('offered', 0)} suggestions, {intent.get('run', 0)} run",
                    inline=False)
    if r.get("birthdays"):
        embed.add_field(name="🎂 Birthdays this week", value=" · ".join(
            f"<@{uid}> {on[5:]}" for uid, on, _ in r["birthdays"][:10]), inline=False)
    return brand(embed, "State of the Realm")
