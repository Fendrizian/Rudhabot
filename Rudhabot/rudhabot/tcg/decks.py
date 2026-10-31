"""Deckbuilding rules (Codex Part II, "brighter clouds are rarer") and the six archetype starter decks (Part IV)."""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from rudhabot.tcg.cards import CARD_BY_ID, CHARACTER

DECK_SIZE = 40
MIN_CHARACTERS = 12
MAX_GRADE4 = 4
# The Codex's deckbuilding table, rarity census and archetype notes all say one Grade 5 per deck; one continuity note
# says two. We follow the rulebook table (see docs/TCG.md, "Interpretations").
MAX_GRADE5 = 1


def copy_limit(card_id: str) -> int:
    card = CARD_BY_ID[card_id]
    if card.unique:
        return 1
    if card.kind == CHARACTER:
        return {1: 3, 2: 3, 3: 2, 4: 1, 5: 1}[card.grade]
    return 2


def validate_deck(card_ids: Iterable[str]) -> list[str]:
    """Return every rule the deck breaks (an empty list means the deck is legal)."""
    cards = list(card_ids)
    problems: list[str] = []
    unknown = sorted({c for c in cards if c not in CARD_BY_ID})
    if unknown:
        return [f"Unknown card id(s): {', '.join(unknown)}"]
    if len(cards) != DECK_SIZE:
        problems.append(f"A deck must have exactly {DECK_SIZE} cards (this one has {len(cards)}).")
    counts = Counter(cards)
    characters = [c for c in cards if CARD_BY_ID[c].kind == CHARACTER]
    if len(characters) < MIN_CHARACTERS:
        problems.append(f"A deck needs at least {MIN_CHARACTERS} Characters (this one has {len(characters)}).")
    for cid, n in sorted(counts.items()):
        limit = copy_limit(cid)
        if n > limit:
            problems.append(f"{CARD_BY_ID[cid].name}: at most {limit} cop{'y' if limit == 1 else 'ies'} (found {n}).")
    g4 = sum(1 for c in characters if CARD_BY_ID[c].grade == 4)
    g5 = sum(1 for c in characters if CARD_BY_ID[c].grade == 5)
    if g4 > MAX_GRADE4:
        problems.append(f"At most {MAX_GRADE4} Grade 4 characters (found {g4}).")
    if g5 > MAX_GRADE5:
        problems.append(f"At most {MAX_GRADE5} Grade 5 character (found {g5}).")
    return problems


def _expand(spec: dict[str, int]) -> tuple[str, ...]:
    return tuple(cid for cid, n in spec.items() for _ in range(n))


# Each archetype is the Codex's ~30-card core plus our fill (marked) to reach a legal 40.
ARCHETYPES: dict[str, dict] = {
    "vanguard": {
        "name": "Jahnen Vanguard", "plan": "Swarm aggro with Command: win by round 6-7, before Devastation.",
        "core": {"C12": 3, "C14": 3, "C10": 3, "C11": 2, "C13": 2, "C08": 1, "A19": 2, "A20": 2, "A01": 2, "A02": 1,
                 "A13": 1, "E01": 2, "E02": 1, "E14": 1, "E12": 1, "B02": 2, "B09": 1, "M03": 2, "M12": 2},
        "fill": {"A13": 1, "A02": 1, "A03": 2, "A22": 2},
    },
    "storm": {
        "name": "Stormcallers", "plan": "Soak, Freeze, Strike: burst turns from Precipitation and Lightning chains.",
        "core": {"C22": 1, "C25": 1, "C08": 1, "C27": 2, "C26": 3, "C23": 2, "C05": 2, "C30": 2, "A04": 2, "A05": 2,
                 "A07": 2, "A08": 1, "A09": 1, "A13": 2, "A22": 2, "E04": 1, "E05": 2, "B03": 1, "D06": 1, "S3": 2, "M04": 2},
        "fill": {"A06": 2, "A15": 2, "A08": 1},
    },
    "citadel": {
        "name": "Citadel Doctrine", "plan": "Control the Threat Level, hold Reactions open, finish with the Cloudpiercer.",
        "core": {"C01": 1, "C04": 2, "C05": 3, "C03": 3, "C06": 2, "C20": 2, "C33": 1, "A22": 2, "A21": 2, "A17": 2,
                 "A18": 1, "A13": 2, "A14": 2, "A02": 1, "A12": 1, "E11": 2, "E10": 2, "E15": 1, "M01": 1, "M06": 1, "M10": 1},
        "fill": {"A01": 2, "A02": 1, "A18": 1, "E15": 1},
    },
    "helix": {
        "name": "Helix Engineers", "plan": "Equipment and Tech value engine; Disrupt the opponent's best cloud.",
        "core": {"C15": 1, "C16": 2, "C17": 3, "C18": 2, "C10": 2, "C23": 2, "C13": 2, "E09": 2, "E13": 2, "E16": 1,
                 "E17": 2, "E12": 2, "E10": 2, "A23": 2, "A22": 2, "A01": 2, "A02": 2, "D04": 2, "D05": 1, "B04": 2,
                 "M02": 1, "M05": 1},
        "fill": {},
    },
    "blackcloud": {
        "name": "Blackcloud", "plan": "Embrace the Corruption: Fallen swarm, Armageddon or the Prophecy.",
        "core": {"C09": 1, "C07": 2, "C21": 3, "C24": 3, "C18": 2, "C10": 2, "C17": 2, "A24": 2, "D01": 2, "E08": 2,
                 "B04": 2, "D08": 1, "A19": 2, "A20": 2, "A02": 1, "S4": 2, "M09": 1},
        "fill": {"A01": 2, "A03": 2, "A13": 2, "D02": 1, "A02": 1},
    },
    "verdant": {
        "name": "Verdant Sanctuary", "plan": "Ramp Vapor, gain Resolve, wall up behind Guards, then land a bomb.",
        "core": {"C36": 3, "C29": 3, "C30": 2, "C26": 2, "C34": 2, "C35": 2, "C28": 2, "C33": 1, "C02": 1, "B08": 2,
                 "B05": 2, "E18": 2, "E11": 2, "A17": 2, "A18": 2, "M11": 2, "M07": 2, "M06": 1, "A12": 1, "A10": 1},
        "fill": {"A13": 2, "A01": 1},
    },
}
for _key, _deck in ARCHETYPES.items():
    _counts = Counter(_deck["core"]) + Counter(_deck["fill"])
    _deck["cards"] = _expand(dict(sorted(_counts.items())))
    _problems = validate_deck(_deck["cards"])
    assert not _problems, (_key, _problems)
