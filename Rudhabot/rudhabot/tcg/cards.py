"""The Asgrelia: Cloudfall base set, transcribed from the TCG Codex (Part III). Immutable data only.

Protocols are rules (see `PROTOCOLS`), not cards, so the playable card pool is the 112 cards below.
Card text is kept verbatim so `/tcg card` and the assistant can quote it; the engine implements it in `engine.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CHARACTER, ACTION, EQUIPMENT, BOON, AFFLICTION, LANDMARK, EVENT, SKY = (
    "Character", "Action", "Equipment", "Boon", "Affliction", "Landmark", "Event", "Sky")

COMPRESSED, PRECIPITATION, LIGHTNING, WEATHER, PASSIVE = "Compressed", "Precipitation", "Lightning", "Weather", "Passive"
COMMAND, TECH, CORRUPTION, COALITION_TAG = "Command", "Tech", "Corruption", "Coalition"
CLOUD_POWERS = (COMPRESSED, PRECIPITATION, LIGHTNING, WEATHER, PASSIVE)

AFFILIATIONS = ("Capital", "Jahnen", "Nevux", "Aptevium", "Brenesia", "Huttava", "Mellines", "Ontasaro",
                "Sanctimonia", "Terra Eden", "Coalition")


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    kind: str
    cost: int
    text: str
    grade: int = 0                       # a character's own Cloud Grade, or the Grade a powered card requires
    power: Optional[str] = None          # Compressed / Precipitation / Lightning / Weather / Passive / Command / Tech / Corruption
    affiliations: tuple = ()
    attack: int = 0                      # printed Power (named `attack` to avoid clashing with the cloud `power` tag)
    stamina: int = 0
    keywords: frozenset = field(default_factory=frozenset)
    fallen: bool = False
    unique: bool = False
    reaction: bool = False
    slot: Optional[str] = None           # Equipment: "Weapon" or "Gear"
    palvanium: bool = False
    tech: bool = False
    damage: int = 0                      # printed damage of an Action (used by Mirror Cloud's "half its damage")

    @property
    def is_character(self) -> bool:
        return self.kind == CHARACTER

    @property
    def is_command(self) -> bool:
        return self.power == COMMAND


def _ch(id, name, cost, grade, affs, p, s, text, kw=(), fallen=False):
    return Card(id, name, CHARACTER, cost, text, grade=grade, affiliations=tuple(affs), attack=p, stamina=s,
                keywords=frozenset(kw), fallen=fallen)


def _ac(id, name, cost, power, text, grade=0, reaction=False, damage=0, tech=False):
    return Card(id, name, ACTION, cost, text, grade=grade, power=power, reaction=reaction, damage=damage,
                tech=tech or power == TECH)


def _eq(id, name, cost, slot, text, power=None, grade=0, palvanium=False, tech=False):
    return Card(id, name, EQUIPMENT, cost, text, grade=grade, power=power, slot=slot, palvanium=palvanium, tech=tech)


CARDS: tuple[Card, ...] = (
    # ---------------------------------------------------------------- Capital Province and Coalition
    _ch("C01", "Harp Vanthe", 6, 5, ("Capital", "Coalition"), 4, 6,
        "Prodigy Strategist: At your Dawn, look at the top 3 cards, take 1, bottom the rest. Command: Once per turn, give a "
        "Ready ally +2 Power until Dusk. Bond (Lana): both get +1/+1. Harp ignores every Bond except Lana's. Corrupted: Loses "
        "both abilities. When it attacks, reveal your top card and deal damage equal to its cost to the opponent's Resolve."),
    _ch("C02", "Vortan Isyphiel", 7, 5, ("Capital",), 5, 7,
        "Mandate: At your Dawn, switch the Protocol in effect to the one just above or below the Threat Level, until your next "
        "Dawn. Hidden Scheme: When the Protocol in effect changes, draw 1 (once per turn). Corrupted: The Threat Level can't fall."),
    _ch("C03", "Ava Evensong", 2, 2, ("Capital", "Coalition"), 1, 3,
        "At your Dusk, Cleanse 1 from a character you control. Selfless: When an Action would damage another ally, Ava may take "
        "the damage instead. Bond (Harp): Ava gets +2/+2 (Harp gets nothing). Corrupted: Selfless is forced, and whenever Ava is "
        "damaged she deals that much damage to a target enemy character."),
    _ch("C04", "Orren Valtreis", 4, 3, ("Capital",), 3, 5,
        "Assessment: Once per round, at your Dawn, raise or lower the Threat Level by 1. Corrupted: Can only raise it, and draws "
        "2 cards each time."),
    _ch("C05", "Dalia Venn", 2, 2, ("Capital",), 1, 3,
        "Archive Keeper: At your Dawn, look at the top 2 cards and bottom either or both. When you play a Landmark, draw 1. "
        "Corrupted: Looks at the opponent's top 2 cards instead."),
    _ch("C06", "Brannic Ostrel", 4, 3, ("Capital",), 4, 4,
        "Sortie Captain: Cloudpiercer Sortie costs 3 less and needs only Threat Level 4. Gets +1/+1 while any Protocol is in "
        "effect. Corrupted: Sortie can be played at any Threat Level, and you take 3 damage when you do."),
    _ch("C07", "Ilyra Voss", 3, 3, ("Capital",), 1, 2,
        "Fallen. Patient Zero: At your Dusk, place 1 Taint on target non-Corrupted enemy character of Grade 3 or lower.",
        fallen=True),
    # ---------------------------------------------------------------- Jahnen
    _ch("C08", "Lana Aurelia", 7, 5, ("Jahnen", "Coalition"), 6, 6,
        "Swift. Reckless: Must attack every Clash. Unprecedented: Your Lightning Actions cost 1 less (min 1) and deal +1 damage. "
        "When Lana attacks, deal 1 damage to each enemy character (3 to Soaked ones). Bond (Harp): +1/+1. Corrupted: Her attack "
        "deals 3 damage to every other character, yours included. Threat Level +1 each time she attacks.", kw=("Swift",)),
    _ch("C09", "May Selunara", 6, 5, ("Jahnen",), 4, 4,
        "Fallen. Dark Mirror: When an enemy Compressed, Precipitation or Lightning Action would damage May or you, you may reflect "
        "it: it deals +2 damage to a target of your choice among the enemy characters and Resolve instead. Combat damage can't "
        "be reflected.", fallen=True),
    _ch("C10", "Yurie Valea", 2, 1, ("Jahnen", "Coalition"), 1, 3,
        "Comms Network: The first time each turn you play a Command card, draw 1. Overwork: At your Dusk, you may Spend Yurie to "
        "draw 1. Corrupted: Your Command cards can target your Corrupted characters, who are not Mute for them."),
    _ch("C11", "Mossie Caldreth", 3, 3, ("Jahnen", "Coalition"), 3, 4,
        "Cold Commander: Afflictions can't attach to her. Elite Corps: Your other Jahnen characters get +1 Power. Bond (Lana): "
        "+1 Power. Corrupted: Elite Corps gives +2 and applies to all your characters."),
    _ch("C12", "Dren Voskarl", 1, 1, ("Jahnen",), 2, 1,
        "Swift. When you play a Command card, Dren gets +1 Power until Dusk. Corrupted: Gains Piercing.", kw=("Swift",)),
    _ch("C13", "Kessa Brandt", 3, 2, ("Jahnen",), 2, 3,
        "Curved Shot: Once per turn, pay 1 Vapor to deal 2 damage to target character (3 if you played a Compressed Action this "
        "turn). Corrupted: Her shots ignore Guard and place 1 Taint."),
    _ch("C14", "Rhea Corvane", 2, 1, ("Jahnen",), 2, 2,
        "Standard of the Revolution: When Rhea enters, your other Jahnen characters get +1 Power until Dusk. Command: Spend Rhea "
        "to give target Jahnen character Swift this turn. Corrupted: When Rhea attacks, other attackers get +2 Power."),
    # ---------------------------------------------------------------- Nevux
    _ch("C15", "Claude Elaris", 5, 4, ("Nevux",), 3, 5,
        "Inventor: Your Equipment costs 1 less (min 1). Energy Disruptor: Pay 2 Vapor to Disrupt 2 on target character. "
        "Insensitive: Command cards can't target him. Corrupted: Disrupt costs 0 and also places 1 Taint on the target."),
    _ch("C16", "Ysolde Marek", 5, 3, ("Nevux",), 3, 6,
        "Meritocracy: Your characters with Equipment get +1/+1. Funding: Once per turn, when you attach Equipment, draw 1. "
        "Corrupted: Your Corrupted characters with Equipment get +2/+2 instead."),
    _ch("C17", "Bexley Torr", 2, 1, ("Nevux",), 2, 2,
        "Rigger: When Bexley enters, search your deck for an Equipment costing 2 or less and add it to your hand. Corrupted: "
        "Spend: discard target Equipment attached to an enemy character."),
    _ch("C18", "Fenn Duskin", 3, 2, ("Nevux",), 2, 3,
        "Test Subject: Gets +1 Power for each Taint on Fenn. Overclock costs 0 on Fenn. Corrupted: Meltdown: when KO'd, deal 3 "
        "damage to every character."),
    # ---------------------------------------------------------------- Aptevium
    _ch("C19", "Neil Blythe", 4, 3, ("Aptevium",), 4, 4,
        "Philanthropic Leader: At your Dawn, gain 1 Resolve for each other Aptevium character you control (max 3). Grudge "
        "(Jahnen): +2 Power against Jahnen characters. Fearful: Can't intercept Lana. Corrupted: Grudge applies to every enemy "
        "character."),
    _ch("C20", "Garrick Holm", 4, 3, ("Aptevium",), 3, 5,
        "Guard. Bastion: Your other characters get +0/+1 while Garrick is Ready. Corrupted: Black Phalanx: +1 Power for each "
        "other Corrupted character you control.", kw=("Guard",)),
    _ch("C21", "Orlan Thessaly", 2, 2, ("Aptevium",), 1, 1,
        "Fallen. Hungering Grudge: Whenever Orlan damages a character, place 1 Taint on it. +2 Power against Jahnen characters.",
        fallen=True),
    # ---------------------------------------------------------------- Brenesia
    _ch("C22", "Wahen Nirvana", 5, 4, ("Brenesia",), 4, 6,
        "Valleyflow: When you play a Precipitation Action, place Soaked on target enemy character. Palvanium Heritage: Palvanium "
        "Equipment costs 1 less. Bond (Serena): Both get +1/+1, and your first Precipitation Action each turn costs 1 less. "
        "Corrupted: Flood Without End: your Precipitation Actions deal +2 damage."),
    _ch("C23", "Hollis Dray", 2, 1, ("Brenesia",), 1, 4,
        "Guard. Palvanium Hands: Palvanium Equipment costs 1 less (min 1). Corrupted: Gets +1/+1 for each Equipment attached to "
        "him.", kw=("Guard",)),
    _ch("C24", "Tiber Vaskel", 2, 2, ("Brenesia",), 1, 1,
        "Fallen. Cave-In: At your Dusk, discard target Equipment attached to an enemy character.", fallen=True),
    # ---------------------------------------------------------------- Huttava
    _ch("C25", "Serena Nirvana", 5, 4, ("Huttava",), 4, 6,
        "Peakfrost: When you play a Precipitation Action, place Frozen on target Soaked enemy character. Highborn: Your "
        "characters can't be Frozen. Bond (Wahen): Both get +1/+1, and your first Precipitation Action each turn costs 1 less. "
        "Corrupted: Eternal Winter: Frozen doesn't wear off enemy characters while she is in play, and at your Dusk she deals 1 "
        "damage to each Frozen character."),
    _ch("C26", "Pelle Andrane", 2, 1, ("Huttava",), 2, 2,
        "Skyborne. Mountain Guide: When Pelle enters, search your deck for a Landmark and add it to your hand. Corrupted: "
        "Avalanche: when Pelle attacks, discard the opponent's Landmark.", kw=("Skyborne",)),
    _ch("C27", "Odalys Frey", 3, 3, ("Huttava",), 2, 4,
        "Frostwarden: Once per turn, pay 1 Vapor to place Frozen on target Soaked character; if it is already Frozen, deal 2 "
        "damage to it instead. Corrupted: Shatterfrost: damage dealt to Frozen characters is doubled."),
    # ---------------------------------------------------------------- Mellines
    _ch("C28", "Roaelis Zyscie", 4, 3, ("Mellines",), 3, 5,
        "Parliament: At your Dawn, draw 1 if you control characters of 3 or more different Affiliations. Corrupted: Dictator: "
        "your characters get +1/+1 for each different Affiliation you control."),
    _ch("C29", "Cassia Dovanelle", 2, 2, ("Mellines",), 1, 3,
        "Festive Spirit: Whenever you play a Landmark or Event, gain 2 Resolve. Corrupted: Tainted Feast: whenever you gain "
        "Resolve, place 1 Taint on target enemy character."),
    _ch("C30", "Tomas Ricelle", 3, 2, ("Mellines",), 2, 4,
        "Guard. Wonder Guardian: Gets +1/+1 while you control a Landmark. Your opponent's effects can't discard your Landmark. "
        "Corrupted: Desecrator: when Tomas attacks, discard any Landmark.", kw=("Guard",)),
    # ---------------------------------------------------------------- Ontasaro
    _ch("C31", "Jerral Alaharnes", 5, 4, ("Ontasaro",), 5, 5,
        "First Strike. Underdog: +2 Power while attacking if you control fewer characters than your opponent. Corrupted: "
        "Ravenous Chieftain: when Jerral KOs a character, he gets a permanent +2/+2.", kw=("First Strike",)),
    _ch("C32", "Kesh Tavaro", 3, 2, ("Ontasaro",), 3, 3,
        "First Strike. Marksman: +2 Power when intercepting a character with a higher cost. Corrupted: Gains Skyborne.",
        kw=("First Strike",)),
    # ---------------------------------------------------------------- Sanctimonia
    _ch("C33", "Caelestis Keziah", 5, 4, ("Sanctimonia",), 3, 6,
        "Sanctuary: Your opponent's cards can't place Taint on your characters. Absolution: Once per round, at your Dawn, Purify "
        "target Corrupted character. Corrupted: Heretic's Rite: Absolution places 2 Taint on target character instead."),
    _ch("C34", "Ambrelle Ruthe", 3, 3, ("Sanctimonia",), 2, 4,
        "When Ambrelle enters, gain 2 Resolve. Litany: Once per turn, pay 1 Vapor to Cleanse 2 from target character. Corrupted: "
        "Dark Litany: whenever she Cleanses, deal 2 damage to target enemy character."),
    _ch("C35", "Gaius Valenmoor", 4, 3, ("Sanctimonia",), 4, 4,
        "Guard. Honor: Gets +3 Power when attacking or intercepting Corrupted characters, and can't attack unless an enemy "
        "character is Spent. Corrupted: Oathbreaker: loses Guard and Honor, and gets +3 Power against non-Corrupted characters.",
        kw=("Guard",)),
    # ---------------------------------------------------------------- Terra Eden
    _ch("C36", "Fern Alderwyn", 2, 1, ("Terra Eden",), 1, 3,
        "Green Thumb: At your Dawn, gain 1 extra Vapor this turn (2 if you control a Landmark). Corrupted: Blighted Bloom: at "
        "your Dawn, gain 2 extra Vapor and place 1 Taint on target character."),

    # ---------------------------------------------------------------- Actions (24)
    _ac("A01", "Cloud Beam", 2, COMPRESSED, "Deal 3 damage to target character. Chain (Compressed): +1 damage.", damage=3),
    _ac("A02", "Curved Beam", 3, COMPRESSED, "Deal 4 damage to target character. Ignores Guard.", damage=4),
    _ac("A03", "Beam Barrage", 4, COMPRESSED,
        "Deal 2 damage to each of up to 3 target characters. Chain (Compressed): 3 damage each.", damage=2),
    _ac("A04", "Torrent", 2, PRECIPITATION, "Deal 2 damage to target character and place Soaked on it.", grade=3, damage=2),
    _ac("A05", "Cold Front", 2, PRECIPITATION,
        "Place Frozen on target character. If it was Soaked, remove Soaked and deal 3 damage to it.", grade=3, damage=3),
    _ac("A06", "Blizzard", 4, PRECIPITATION,
        "Place Frozen on up to 2 target enemy characters. Deal 2 damage to each of them that is Soaked.", grade=3, damage=2),
    _ac("A07", "Thunderclap", 3, LIGHTNING,
        "Deal 4 damage to target character. Chain (Precipitation): also deal 2 damage to each character adjacent to it.",
        grade=4, damage=4),
    _ac("A08", "Chain Bolt", 4, LIGHTNING, "Deal 2 damage to each enemy character.", grade=4, damage=2),
    _ac("A09", "Voltage Spike", 5, LIGHTNING,
        "Draining: as you play this, Spend a Ready character you control. Deal 7 damage divided as you choose among enemy "
        "characters and/or the opponent's Resolve.", grade=4, damage=7),
    _ac("A10", "Tsunami", 7, PRECIPITATION,
        "Deal 5 damage to each enemy character and place Soaked on each. Discard the opposing Landmark.", grade=5, damage=5),
    _ac("A11", "Tempest Call", 6, WEATHER,
        "Deal 6 damage to target character and 3 damage to each other enemy character. If the Sky is Stormfront, deal +2 to "
        "each.", grade=5, damage=6),
    _ac("A12", "Clear the Skies", 4, WEATHER,
        "Discard the Sky. Remove all Taint from every character. Lower the Threat Level by 2. Draw 1.", grade=5),
    _ac("A13", "Cloud Shield", 1, COMPRESSED, "Reaction. Prevent up to 3 damage to target character or to you.", reaction=True),
    _ac("A14", "Mirror Cloud", 2, COMPRESSED,
        "Reaction. When an enemy Compressed, Precipitation or Lightning Action targets your character or you, negate it and "
        "deal half its damage (rounded up) to the opponent's Resolve.", reaction=True),
    _ac("A15", "Fog Veil", 2, PRECIPITATION,
        "Reaction. Target character you control can't be targeted by enemy Actions this turn. Draw 1.", grade=3, reaction=True),
    _ac("A16", "Aerial Retreat", 1, PASSIVE,
        "Reaction. Return target character you control to your hand. Its Equipment and attachments are discarded.", reaction=True),
    _ac("A17", "Mend", 2, PASSIVE, "Remove all damage from target non-Corrupted character and gain 3 Resolve."),
    _ac("A18", "Emergency Purge", 2, PASSIVE, "Reaction. Purify target character.", reaction=True),
    _ac("A19", "Rally Cry", 1, COMMAND, "Up to 2 target Ready characters you control get +2 Power until Dusk."),
    _ac("A20", "Tactical Order", 2, COMMAND, "Target character you control gains Swift and +1 Power until Dusk. Draw 1."),
    _ac("A21", "Executive Order", 3, COMMAND, "Raise or lower the Threat Level by up to 2. Draw 1."),
    _ac("A22", "Archive Query", 1, TECH,
        "Look at the top 4 cards of your deck. Put a non-Character card into your hand and the rest on the bottom in any order."),
    _ac("A23", "Hovercraft Delivery", 3, TECH,
        "Search your deck for an Equipment costing 3 or less and attach it to a character you control for free. Shuffle."),
    _ac("A24", "Reach Deeper", 0, CORRUPTION,
        "Gain 3 Vapor this turn. Place 1 Taint on target character you control. Once per turn."),

    # ---------------------------------------------------------------- Equipment and Weapons (18)
    _eq("E01", "Cloudforged Saber", 2, "Weapon", "+2 Power."),
    _eq("E02", "Palvanium Blade", 3, "Weapon", "+3 Power. Opponent's effects can't discard it.", palvanium=True),
    _eq("E03", "Cloud Lance", 3, "Weapon",
        "+1 Power and Piercing. When the equipped character attacks, deal 2 damage to target enemy character.", power=COMPRESSED),
    _eq("E04", "Stormcaller's Rod", 4, "Weapon", "+1 Power. Your Lightning Actions cost 1 less (min 1).", power=LIGHTNING, grade=4),
    _eq("E05", "Frostbinder Staff", 3, "Weapon",
        "+1 Power. Once per turn, when you play a Precipitation Action, place Soaked or Frozen on target enemy character.",
        power=PRECIPITATION, grade=3),
    _eq("E06", "Rainforest Longbow", 2, "Weapon", "+1 Power. At the start of your Clash, deal 1 damage to target enemy character."),
    _eq("E07", "Crosier of Honor", 3, "Weapon",
        "+1/+1. At your Dusk, Cleanse 1 from target character. +3 Power when attacking or intercepting Corrupted characters."),
    _eq("E08", "Obsidian Cloudshard", 2, "Weapon", "+4 Power. At your Dawn, place 1 Taint on the equipped character.",
        power=CORRUPTION),
    _eq("E09", "Energy Disruptor", 4, "Gear",
        "Once per turn, move 1 Taint from any character to another character. When the equipped character damages a character, "
        "Disrupt 1 on it.", tech=True),
    _eq("E10", "Palvanium Plating", 3, "Gear", "+0/+3. The first damage from Actions it takes each turn is reduced by 2.",
        palvanium=True),
    _eq("E11", "Condensed Aegis", 2, "Gear", "+0/+2 and Guard.", power=COMPRESSED),
    _eq("E12", "Anti-Gravity Harness", 2, "Gear", "+0/+1 and Swift.", tech=True),
    _eq("E13", "Temporal Stabilizer", 3, "Gear",
        "When the equipped character would be KO'd, return it to your hand instead and discard this.", tech=True),
    _eq("E14", "Relay Headset", 1, "Gear", "Your first Command card each turn costs 1 less (min 0).", tech=True),
    _eq("E15", "Archive Lens", 2, "Gear", "+0/+1. At your Dawn, look at the top 2 cards of your deck and bottom either or both."),
    _eq("E16", "Reactor Core Fragment", 4, "Gear",
        "+1 Power. The equipped character counts as Grade 5. At your Dawn, pay 1 Vapor or discard this.", tech=True),
    _eq("E17", "Hovercraft Transport", 3, "Gear",
        "Swift. When the equipped character attacks, your other characters that entered this turn may also attack.", tech=True),
    _eq("E18", "Haven Satchel", 2, "Gear", "+0/+1. At your Dawn, gain 1 extra Vapor this turn and 1 Resolve."),

    # ---------------------------------------------------------------- Boons (9)
    Card("B01", "Cloud Aegis", BOON, 1, "Prevent the first 2 damage dealt to this character each turn.", power=COMPRESSED),
    Card("B02", "Rallied Spirit", BOON, 1, "+2 Power while it is not Mute, and +1 more if you played a Command card this turn.",
         power=COMMAND),
    Card("B03", "Static Charge", BOON, 1, "+2 Power. Your Lightning Actions deal +1 damage while this character is Ready.",
         power=LIGHTNING, grade=4),
    Card("B04", "Overclock", BOON, 2, "+3 Power. At your Dusk, place 1 Taint on it.", power=TECH, tech=True),
    Card("B05", "Blessing of Honor", BOON, 2, "+1/+2. Taint can't be placed on it. If it is Corrupted when this attaches, Purify it.",
         power=PASSIVE),
    Card("B06", "Skyborne", BOON, 1, "This character has Skyborne.", power=PASSIVE),
    Card("B07", "Focused Mind", BOON, 1,
         "When it attacks, look at your top card and you may bottom it. Once per turn, pay 1 Vapor to draw 1.", power=PASSIVE),
    Card("B08", "Verdant Boon", BOON, 2, "Your Vapor Capacity is +1 while this is attached. Only one Verdant Boon counts.",
         power=PASSIVE),
    Card("B09", "Sworn Bond", BOON, 1,
         "Choose another character you control. While both are in play, each gets +1/+1 and they count as Bonded to each other. "
         "If either leaves, discard this.", power=COALITION_TAG),

    # ---------------------------------------------------------------- Afflictions (9)
    Card("D01", "Seed of Ruin", AFFLICTION, 2, "Attach to a character of Grade 4 or lower. At the end of each round, place 1 Taint on it.",
         power=CORRUPTION),
    Card("D02", "Hushed", AFFLICTION, 1, "This character is Mute."),
    Card("D03", "Marked Target", AFFLICTION, 1, "The first time each turn this character is dealt damage, it takes 2 extra damage."),
    Card("D04", "Dampened Cloud", AFFLICTION, 2, "Its Cloud Grade is 1 lower (min 1).", power=TECH, tech=True),
    Card("D05", "Burnout", AFFLICTION, 2, "It doesn't Ready at its controller's Dawn unless they pay 2 Vapor.", power=TECH, tech=True),
    Card("D06", "Hoarfrost", AFFLICTION, 2, "−2 Power. If it was Frozen when this attached, deal 2 damage to it.",
         power=PRECIPITATION, grade=3),
    Card("D07", "Static Leash", AFFLICTION, 2, "Whenever it attacks or intercepts, it takes 2 damage.", power=LIGHTNING, grade=4),
    Card("D08", "Quarantine", AFFLICTION, 2,
         "It can't attack, and can't place Taint on other characters. Taint can't be placed on it. May attach to any character, "
         "including your own."),
    Card("D09", "Estranged", AFFLICTION, 1, "Bond effects on it or naming it are ignored, and it can't be chosen for Sworn Bond."),

    # ---------------------------------------------------------------- Landmarks and Events (12)
    Card("M01", "Citadel of the Clouds", LANDMARK, 5,
         "Seat of Power: Your Vapor Capacity +1. While any Protocol is in effect, your Capital and Coalition characters get +1/+1. "
         "Central Cloud Archive: At your Dawn, look at the top card of your deck and you may bottom it.",
         affiliations=("Capital",), unique=True),
    Card("M02", "Helix Tower", LANDMARK, 4,
         "Rotating Floors: At your Dawn, rearrange your characters in any order. Your Equipment costs 1 less (min 1). Your Grade 4 "
         "characters count as Grade 5 for Weather cards.", affiliations=("Nevux",), unique=True),
    Card("M03", "Jahnen Military Academy", LANDMARK, 3,
         "Alumni: Characters you control with an active Bond get +1/+1. Whenever you play a Command card, your Jahnen characters "
         "get +1 Power until Dusk.", affiliations=("Coalition",)),
    Card("M04", "Huttava Highlands", LANDMARK, 3,
         "High Altitude: Your Precipitation Actions cost 1 less (min 1). Your characters of Grade 2 or lower have Skyborne.",
         affiliations=("Huttava",)),
    Card("M05", "Brenesian Palvanium Mines", LANDMARK, 3,
         "Palvanium Veins: At your Dawn, gain 1 extra Vapor this turn for every 2 Equipment you control (rounded down). Your "
         "Palvanium Equipment costs 1 less.", affiliations=("Brenesia",)),
    Card("M06", "Cathedral of Honor", LANDMARK, 4,
         "Hallowed Ground: At your Dusk, Cleanse 1 from each of your characters. Tolling Bells: Once per round, when the Threat "
         "Level would rise, you may prevent that rise.", affiliations=("Sanctimonia",)),
    Card("M07", "Terra Eden Haven", LANDMARK, 3,
         "Green Vision: Your Vapor Capacity +1. At your Dawn, if you control no Corrupted characters, gain 2 Resolve.",
         affiliations=("Terra Eden",)),
    Card("M08", "Ontasaro Canopy", LANDMARK, 2,
         "Dense Canopy: Enemy characters can't attack the turn they enter, even with Swift. Your characters of Grade 3 or lower "
         "have First Strike when intercepting.", affiliations=("Ontasaro",)),
    Card("M09", "The Prophecy of the Great Corruption", LANDMARK, 3,
         "At your Dusk, if the Threat Level is 8 and you control 3 or more Corrupted characters, you win the game. Whenever a "
         "character becomes Corrupted, the Threat Level rises by 1 extra.", unique=True),
    Card("M10", "Cloudpiercer Sortie", EVENT, 7,
         "Play only if the Threat Level is 6 or higher. Deal 5 damage to every enemy character and 5 damage to the opponent's "
         "Resolve. Then deal 5 damage to your own Resolve. The Cloudpiercer is almost never used.", unique=True),
    Card("M11", "Mellinean Festival", EVENT, 2, "Each player gains 3 Resolve. You draw 2 cards."),
    Card("M12", "Coalition Summit", EVENT, 3,
         "Choose up to 3 characters you control with different Affiliations. Each gets +2/+2 and Swift until Dusk. Draw 1."),

    # ---------------------------------------------------------------- Skies (4)
    Card("S1", "Zenith Alignment", SKY, 3,
         "The sky blazes. All Cloud Grades are +1 (max 5). The sky stops darkening: the Threat Level does not rise at the end of "
         "the round.", power=WEATHER, grade=4),
    Card("S2", "Overcast Veil", SKY, 2, "The clouds dim. All Cloud Grades are −1 (min 1). Taint can't be placed on characters.",
         power=WEATHER, grade=4),
    Card("S3", "Stormfront", SKY, 3,
         "Precipitation and Lightning Actions cost 1 less (min 1) and deal +1 damage. At each Dusk, deal 1 damage to each Soaked "
         "character.", power=WEATHER, grade=4),
    Card("S4", "Black Tide", SKY, 3,
         "The Corruption surges. Corrupted characters get +1/+1. At the end of each round, each character that has Taint gains 1 "
         "more, and the Threat Level rises by 1."),
)

CARD_BY_ID: dict[str, Card] = {card.id: card for card in CARDS}
CARD_IDS: tuple[str, ...] = tuple(card.id for card in CARDS)
CARD_INDEX: dict[str, int] = {cid: i for i, cid in enumerate(CARD_IDS)}


@dataclass(frozen=True)
class Protocol:
    severity: int
    name: str
    posture: str
    effect: str


PROTOCOLS: tuple[Protocol, ...] = (
    Protocol(1, "Hazard", "Partial mobilization; the Capital stays out", "Partial Mobilization: Characters that are not Capital get +0/+1."),
    Protocol(2, "Grave", "Contain the threat; no escalation",
             "Containment: The sky stops darkening. The Threat Level does not rise at the end of the round. Corruption can still raise it."),
    Protocol(3, "Fright", "Coordinated mobilization across provinces",
             "Coordinated Mobilization: Each character gets +1 Power for each other character its controller has that shares an "
             "Affiliation (max +2)."),
    Protocol(4, "Elegy", "Recovery is the priority", "Recovery Efforts: At each player's Dusk, that player gains 2 Resolve."),
    Protocol(5, "Devastation", "The Capital oversees countermeasures",
             "Widespread Destruction: At the start of each Clash, deal 1 damage to every character. Capital and Coalition characters "
             "are not affected."),
    Protocol(6, "Calamity", "The Capital takes over provincial authority",
             "Takeover: Capital and Coalition characters get +2/+2. No new Landmarks or Skies can be played."),
    Protocol(7, "Befalling", "Total mobilization, no extrajudicial powers",
             "Unified Mobilization: All characters get +2/+0 and count as every Affiliation. At each Dawn, each player Cleanses 1 "
             "from each of their characters."),
    Protocol(8, "Armageddon", "Mobilization to 110%; extrajudicial powers",
             "Total Mobilization: All characters get +3/+3, and Cloud Grade requirements are ignored. At the end of each round, "
             "every character gains 1 Taint and each player loses 2 Resolve."),
)
PROTOCOL_BY_LEVEL = {p.severity: p for p in PROTOCOLS}


def find_card(query: str) -> Optional[Card]:
    """Look a card up by id or (case-insensitive, prefix-tolerant) name."""
    q = query.strip().lower()
    if not q:
        return None
    if q.upper() in CARD_BY_ID:
        return CARD_BY_ID[q.upper()]
    exact = [c for c in CARDS if c.name.lower() == q]
    if exact:
        return exact[0]
    partial = [c for c in CARDS if q in c.name.lower()]
    return partial[0] if partial else None
