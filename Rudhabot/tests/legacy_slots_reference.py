"""The ORIGINAL slots algorithm, copied verbatim from the pre-refactor command (sleeps/embeds removed).
It exists only so tests can prove the new SlotMachine still has identical technicalities."""
import random

SYMBOLS = ["<:slot_cherries:1398958979513585695>", "<:slot_lemon:1398953879894233190>", "<:slot_orange:1398958425420988519>", "<:slot_grapes:1398958399194009621>", "<:slot_diamond:1398954988142592060>", "<:slot_hibiscus:1399010286144585760>", "<:slot_seven:1398952681371861022>"]
WEIGHTS = [30, 25, 20, 15, 5, 3, 2]


def legacy_spin(rng=random):
    spinning = ["⬛", "⬛", "⬛"]
    for i in range(3):
        spinning[i] = rng.choices(SYMBOLS, weights=WEIGHTS)[0]
    result = spinning.copy()
    if result[0] == result[1] == result[2]:
        if result[0] == "<:slot_seven:1398952681371861022>":
            multiplier = 50
        elif result[0] == "<:slot_hibiscus:1399010286144585760>":
            multiplier = 20
        elif result[0] == "<:slot_diamond:1398954988142592060>":
            multiplier = 15
        elif result[0] == "<:slot_grapes:1398958399194009621>":
            multiplier = 10
        elif result[0] == "<:slot_orange:1398958425420988519>":
            multiplier = 8
        elif result[0] == "<:slot_lemon:1398953879894233190>":
            multiplier = 5
        elif result[0] == "<:slot_cherries:1398958979513585695>":
            multiplier = 3
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        multiplier = 2
    else:
        multiplier = 0
    return result, multiplier
