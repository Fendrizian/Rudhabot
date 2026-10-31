"""Slot machine visuals.

Presentation only. The rules (odds, payouts, RNG draws, 2-second reel pacing, one wallet write, the player lock)
are unchanged and live in `rudhabot.domain.slots` / the cog. Compared with the original screen this adds:

  * a framed reel row with a spinning marker on the reel that is currently turning (not three black squares);
  * a "two matching... one reel left" suspense frame while the last reel turns;
  * outcome-specific headlines (JACKPOT / THREE DIAMONDS / A PAIR OF LEMONS / NO MATCH) and colours;
  * bet, net result and new balance at a glance, and the payout table while you wait.

It also needs one API call fewer than before: the redundant "third reel revealed" edit, which was replaced a
fraction of a second later by the result, is gone.
"""
from __future__ import annotations

from typing import Sequence

import discord

from rudhabot.domain.slots import DEFAULT_SYMBOLS, PAIR_MULTIPLIER, SlotKind, SlotOutcome, SlotSymbol
from rudhabot.ui.replies import author_embed
from rudhabot.ui.theme import CURRENCY, SLOTSICON, Colors

EMPTY = "⬛"
SPINNING = "🌀"
SUSPENSE_COLOR = Colors.GAMBLING
JACKPOT_THRESHOLD = 15            # triples paying at least this much get the gold treatment


def _row(cells: Sequence[str]) -> str:
    return "🎰 ┃ " + " ┃ ".join(cells) + " ┃ 🎰"


def paytable(symbols: Sequence[SlotSymbol] = DEFAULT_SYMBOLS, pair_multiplier: int = PAIR_MULTIPLIER) -> str:
    triples = "  ".join(f"{s.emoji}×{s.triple_multiplier}" for s in symbols)
    return f"Any pair **×{pair_multiplier}**\nThree of a kind: {triples}"


def spinning_frame(user, stopped: Sequence[SlotSymbol], bet: int) -> discord.Embed:
    """The screen while reel number `len(stopped)` is turning. `stopped` holds the reels already revealed."""
    cells = [s.emoji for s in stopped]
    if len(cells) < 3:
        cells.append(SPINNING)
    cells += [EMPTY] * (3 - len(cells))

    if len(stopped) == 2 and stopped[0] == stopped[1]:
        status, color = "😮 **Two matching!** One reel left...", SUSPENSE_COLOR
    else:
        status, color = f"Spinning reel {len(stopped) + 1} of 3...", Colors.GAMBLING

    embed = author_embed(user, f"{_row(cells)}\n\n{status}", color, title=f"{SLOTSICON} SLOTS")
    embed.add_field(name="Bet", value=f"{CURRENCY}{bet}", inline=True)
    if not stopped:                                   # show the payouts while the first reel spins up
        embed.add_field(name="Payouts", value=paytable(), inline=False)
    return embed


def headline(outcome: SlotOutcome) -> str:
    if outcome.kind is SlotKind.TRIPLE:
        if outcome.symbol.key == "seven":
            return "🌟 JACKPOT — TRIPLE SEVENS! 🌟"
        return f"✨ THREE {outcome.symbol.name.upper()}! ✨"
    if outcome.kind is SlotKind.PAIR:
        return f"A PAIR OF {outcome.symbol.name.upper()}"
    return "NO MATCH"


def result_frame(user, reels: Sequence[SlotSymbol], outcome: SlotOutcome, bet: int, wallet_after: int) -> discord.Embed:
    winnings = outcome.payout(bet)
    if outcome.won:
        verdict = f"🎉 You win! {CURRENCY}{winnings} (x{outcome.multiplier})"
        big = outcome.kind is SlotKind.TRIPLE and outcome.multiplier >= JACKPOT_THRESHOLD
        color = Colors.INCOME if big else Colors.SUCCESS
        change = f"+{CURRENCY}{outcome.net(bet)}"
    else:
        verdict = f"💸 You lose! {CURRENCY}{bet}"
        color = Colors.LOSS
        change = f"-{CURRENCY}{bet}"

    embed = author_embed(user, f"**{headline(outcome)}**\n{_row([s.emoji for s in reels])}\n\n{verdict}", color, title=f"{SLOTSICON} SLOTS")
    embed.add_field(name="Bet", value=f"{CURRENCY}{bet}", inline=True)
    embed.add_field(name="Net", value=change, inline=True)
    embed.add_field(name="Balance", value=f"{CURRENCY}{wallet_after}", inline=True)
    return embed
