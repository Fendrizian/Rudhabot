"""Embeds for Asgrelia: Cloudfall. The public board never shows a hand or a deck order; hands are sent ephemerally."""
from __future__ import annotations

from collections import Counter
from typing import Optional

import discord

from rudhabot.tcg import engine as E
from rudhabot.tcg.cards import CARD_BY_ID, CHARACTER, PROTOCOL_BY_LEVEL, Card
from rudhabot.tcg.decks import ARCHETYPES
from rudhabot.tcg.state import Game
from rudhabot.ui.theme import Colors

CLOUD = Colors.TCG_BLUE        # the open sky: boards, cards, collection
CORRUPT = Colors.TCG_DEEP      # Fallen cards and a board at Threat Level 6+
GRADE_SHADES = {1: "▪️", 2: "◽", 3: "⬜", 4: "🤍", 5: "✨"}


def bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return "▰" * filled + "▱" * (width - filled)


def card_line(card: Card) -> str:
    if card.kind == CHARACTER:
        tag = f"G{card.grade} {'/'.join(card.affiliations)} {card.attack}/{card.stamina}" + (" · Fallen" if card.fallen else "")
    else:
        tag = card.kind + (f" · {card.power}" if card.power else "") + (f" G{card.grade}" if card.grade else "")
    return f"**{card.name}** `{card.id}` · {card.cost}💧 · {tag}" + (" ◆" if card.unique else "")


def card_embed(card: Card, similar: list[tuple[str, float]] = ()) -> discord.Embed:
    embed = discord.Embed(title=f"{card.name}  ({card.id})", description=card.text, color=CORRUPT if card.fallen else CLOUD)
    embed.add_field(name="Type", value=card.kind + (" · Reaction" if card.reaction else ""), inline=True)
    embed.add_field(name="Cost", value=f"{card.cost} Vapor", inline=True)
    if card.kind == CHARACTER:
        embed.add_field(name="Cloud Grade", value=f"{GRADE_SHADES[card.grade]} {card.grade}", inline=True)
        embed.add_field(name="Power / Stamina", value=f"{card.attack} / {card.stamina}", inline=True)
        embed.add_field(name="Affiliation", value=", ".join(card.affiliations), inline=True)
    elif card.power or card.grade:
        embed.add_field(name="Power / Grade", value=f"{card.power or 'None'}" + (f", Grade {card.grade}" if card.grade else ""),
                        inline=True)
    if similar:
        embed.add_field(name="🔮 Clairvoyance: plays a similar role to",
                        value=", ".join(f"{CARD_BY_ID[c].name}" for c, _ in similar), inline=False)
    embed.set_footer(text="Asgrelia: Cloudfall · base set")
    return embed


def _unit_line(g: Game, slot: int, u) -> str:
    if u is None:
        return f"`{slot + 1}` ·  *empty*"
    bits = [f"`{slot + 1}` **{E.uname(u)}** {E.power(g, u)}/{E.remaining(g, u)}"
            + (f" (of {E.stamina(g, u)})" if u.damage else "") + f" · G{E.grade(g, u)}"]
    status = []
    if u.corrupted:
        status.append("☠️")
    elif u.taint:
        status.append("🌑" * u.taint)
    status.append("🟢" if u.ready else "💤")
    if u.frozen:
        status.append("❄️")
    if u.soaked:
        status.append("💧")
    if E.has_kw(g, u, "Guard"):
        status.append("🛡️")
    if u.uid in g.attackers:
        status.append("⚔️")
    bits.append("".join(status))
    attached = [CARD_BY_ID[c].name for c in u.equipment() + u.boons + u.afflictions]
    if attached:
        bits.append("· " + ", ".join(attached))
    return " ".join(bits)


def waiting_text(g: Game) -> str:
    if g.over:
        return "🏁 " + g.end_reason
    seat = E.decider(g)
    name = g.players[seat].name
    if g.pending:
        return f"**{name}** to decide: {g.pending[0]['text']}"
    return {"main": f"**{name}**'s Main phase", "declare": f"**{name}** is declaring attackers",
            "intercept": f"**{name}** chooses interceptors"}.get(g.phase, f"**{name}** to act")


def board_embed(g: Game, record: dict, win_p0: Optional[float] = None, model: str = "") -> discord.Embed:
    level = E.protocol(g)
    proto = PROTOCOL_BY_LEVEL[level].name if level else "none"
    lines = [f"**Round {g.round}** · Threat {bar(g.threat / 8, 8)} **{g.threat}** · Protocol **{proto}**"
             + (" *(Mandate)*" if g.mandate else ""),
             f"Sky: **{CARD_BY_ID[g.sky].name if g.sky else 'clear'}**"]
    if win_p0 is not None:
        lines.append(f"🔮 {g.players[0].name} {bar(win_p0)} {round(win_p0 * 100)}% · {round((1 - win_p0) * 100)}% {g.players[1].name}")
    embed = discord.Embed(title=f"⛅ Cloudfall · {g.players[0].name} vs {g.players[1].name}", description="\n".join(lines),
                          color=CORRUPT if g.threat >= 6 else CLOUD)
    for p in g.players:
        head = (f"❤️ {p.resolve} · 💧 {p.vapor}/{E.capacity(g, p.seat)} · ✋ {len(p.hand)} · 🂠 {len(p.deck)}"
                + (f" · 🏛️ {CARD_BY_ID[p.landmark].name}" if p.landmark else ""))
        field = "\n".join([head] + [_unit_line(g, i, u) for i, u in enumerate(p.field)])
        marker = "▶ " if g.active == p.seat and not g.over else ""
        embed.add_field(name=f"{marker}{p.name}", value=field[:1024], inline=False)
    embed.add_field(name="Now", value=waiting_text(g)[:1024], inline=False)
    recent = [line for line in g.log[-6:]]
    if recent:
        embed.add_field(name="Recent", value="\n".join(recent)[-1024:], inline=False)
    footer = f"match {record['id']}" + (f" · win% by Clairvoyance {model}, both hands hidden" if win_p0 is not None else "")
    if record.get("coach"):
        footer += " · coaching on (unrated)"
    embed.set_footer(text=footer)
    return embed


def hand_embed(g: Game, seat: int) -> discord.Embed:
    p = g.players[seat]
    playable = {a.data["cid"] for a in E.legal_actions(g) if a.key.startswith("c:")} if E.decider(g) == seat else set()
    lines = [("▶ " if cid in playable else "· ") + card_line(CARD_BY_ID[cid]) for cid in p.hand]
    embed = discord.Embed(title=f"Your hand ({len(p.hand)})", description="\n".join(lines) or "*empty*", color=Colors.TCG_SKY)
    embed.set_footer(text="▶ = playable right now · only you can see this")
    return embed


def collection_embed(user, profile: dict) -> discord.Embed:
    counts = Counter(profile["collection"])
    total = sum(counts.values())
    embed = discord.Embed(title=f"{user.display_name}'s Cloudfall collection",
                          description=f"**{total}** cards · **{len(counts)}**/112 different · rating **{profile['rating']}** · "
                                      f"{profile['wins']}W {profile['losses']}L {profile['draws']}D", color=CLOUD)
    by_kind: dict[str, list[str]] = {}
    for cid, n in sorted(counts.items()):
        card = CARD_BY_ID[cid]
        by_kind.setdefault(card.kind, []).append(f"{card.name} ×{n}")
    for kind, items in by_kind.items():
        embed.add_field(name=kind, value=", ".join(items)[:1024], inline=False)
    decks = ", ".join(("**" + d + "**" if d == profile["active_deck"] else d) for d in profile["decks"]) or "none"
    embed.add_field(name="Decks", value=decks[:1024], inline=False)
    return embed


def deck_embed(name: str, cards: list[str], problems: list[str] = ()) -> discord.Embed:
    counts = Counter(cards)
    sections: dict[str, list[str]] = {}
    for cid, n in sorted(counts.items(), key=lambda kv: (CARD_BY_ID[kv[0]].kind, CARD_BY_ID[kv[0]].cost)):
        card = CARD_BY_ID[cid]
        sections.setdefault(card.kind, []).append(f"{n}× {card.name} ({card.cost})")
    embed = discord.Embed(title=f"Deck: {name} ({len(cards)} cards)", color=Colors.FAIL if problems else CLOUD)
    for kind, items in sections.items():
        embed.add_field(name=f"{kind} ({sum(int(i.split('×')[0]) for i in items)})", value="\n".join(items)[:1024], inline=True)
    if problems:
        embed.add_field(name="❌ Not legal yet", value="\n".join(problems)[:1024], inline=False)
    return embed


def doctor_embed(name: str, report: dict, swaps: list) -> discord.Embed:
    embed = discord.Embed(title=f"🔮 Deck Doctor: {name}", color=Colors.CLAIRVOYANCE,
                          description="The Clairvoyance piloted your deck against each archetype (seat-swapped). Small samples, so "
                                      "treat these as a rough guide, not a verdict.")
    rows = []
    for key, res in sorted(report["matchups"].items(), key=lambda kv: -kv[1]["score"]):
        rows.append(f"{bar(res['score'], 8)} **{round(res['score'] * 100)}%** vs {ARCHETYPES[key]['name']} ({res['games']} games)")
    embed.add_field(name="Matchups", value="\n".join(rows) or "No games finished in the time budget.", inline=False)
    if swaps:
        embed.add_field(name="Suggested swaps (learned card value, from your collection)",
                        value="\n".join(f"− {CARD_BY_ID[cut].name} → + {CARD_BY_ID[add].name}" for cut, add, _ in swaps),
                        inline=False)
    embed.set_footer(text=f"model {report.get('model')} · {report.get('seconds')} s · advisory only")
    return embed
