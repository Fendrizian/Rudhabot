"""In-Discord rules reference for Asgrelia: Cloudfall, condensed from the Codex rulebook (Part II).

Used by `/tcg rules` and as retrieval context for the assistant. Rudhabot's interpretations of points the Codex leaves
open are listed under "Rudhabot table rules" so players (and the assistant) can tell them apart from the Codex.
"""

RULES: dict[str, str] = {
    "Overview": (
        "Two players, about 25 minutes. Win by reducing your opponent's Resolve from 30 to 0, or by fulfilling the Prophecy "
        "of the Great Corruption. A shared Threat Level (0-8) rises every round from Round 3; each level switches on a "
        "Protocol that changes the rules for both players. If you must draw from an empty deck, you lose. If both players "
        "reach 0 Resolve at the same moment, the game is a draw."
    ),
    "Deckbuilding": (
        "Exactly 40 cards with at least 12 Characters. Grade 1-2 characters: up to 3 copies each. Grade 3: up to 2. Grade 4: "
        "1 each and at most 4 in the deck. Grade 5 (pure white): 1 in the whole deck. Actions, Equipment, Boons, "
        "Afflictions, Landmarks, Events and Skies: up to 2 each. Unique cards (◆): 1."
    ),
    "Setup": (
        "Shuffle, set Resolve to 30, Threat Level to 0, Vapor Capacity to 1. A coin flip decides who goes first. Each player "
        "draws 5 and may mulligan once. The first player skips their first draw; the second player draws two at their first "
        "Dawn."
    ),
    "Vapor": (
        "Every card costs Vapor. Capacity starts at 1 and rises by 1 at each of your Dawns, up to 8 (effects can push it to "
        "10), and your Vapor refills to your Capacity at Dawn, so your first turn has 2 Vapor. Unspent Vapor stays available "
        "for Reactions on your opponent's turn and is lost at your next Dawn."
    ),
    "Turn": (
        "Dawn: Capacity +1, refill Vapor, remove Frozen from your characters, Ready your Spent characters, draw 1, resolve "
        "'at your Dawn' effects. Main: play Characters (into an empty slot or replacing one of yours), Actions, Equipment, "
        "Boons, Afflictions, Landmarks, Events and Skies, and use abilities. Characters can't attack the turn they arrive "
        "unless they have Swift. Clash: declare attackers, opponent assigns interceptors, Reactions, damage. Dusk: 'at your "
        "Dusk' effects (including Corruption's spread), remove damage from every non-Corrupted character, 'until Dusk' "
        "effects end, discard down to 8. After the second player's Dusk the round ends."
    ),
    "Clash": (
        "Declare attackers: each Ready character that can attack may; attackers become Spent; a Corrupted character must "
        "attack if it can. Your opponent's Ready, non-Frozen characters may each intercept one attacker, one interceptor per "
        "attacker. Then a Reaction window (defender first). Intercepting pairs deal their Power to each other at the same "
        "time; First Strike deals damage first and its opponent strikes back only if it survives. Unintercepted attackers hit "
        "Resolve. Piercing sends excess damage beyond lethal on an interceptor to Resolve. Damage equal to or above Stamina "
        "KOs a character; it goes to the Rainfall with its attachments."
    ),
    "Threat Level and Protocols": (
        "The Threat Level rises by 1 at the end of every round from Round 3 onward, by 1 whenever a character becomes "
        "Corrupted or a Fallen character enters play, and falls by 1 when a Corrupted character is KO'd or Purified. The "
        "Protocol whose Severity equals the Threat Level is in effect for both players: 1 Hazard (non-Capital characters "
        "+0/+1), 2 Grave (no end-of-round rise), 3 Fright (+1 Power per other ally sharing an Affiliation, max +2), 4 Elegy "
        "(gain 2 Resolve at your Dusk), 5 Devastation (1 damage to every non-Capital/Coalition character at the start of each "
        "Clash), 6 Calamity (Capital and Coalition +2/+2; no new Landmarks or Skies), 7 Befalling (all +2/+0, every "
        "Affiliation, Cleanse 1 at each Dawn), 8 Armageddon (all +3/+3, Grade requirements ignored, each round every "
        "character gains 1 Taint and each player loses 2 Resolve)."
    ),
    "Cloud Grades": (
        "Every character has a Cloud Grade from 1 (dark gray) to 5 (pure white). Cards with a cloud Power print the Grade they "
        "need; you may play them only while you control a character with at least that Grade (checked when you play the "
        "card). Passive and Compressed need none, Precipitation 3, Lightning 4, Weather 4 for Skies and 5 for the greatest "
        "feats. Disrupt X, Dampened Cloud and Overcast Veil lower Grades; Zenith Alignment raises them; Reactor Core Fragment "
        "and Helix Tower let a Grade count as 5."
    ),
    "Markers and Chains": (
        "Soaked: Lightning damage dealt to it is increased by 2; removed at the end of the round. Frozen: can't attack or "
        "intercept; removed at its controller's Dawn. Chain (Power): a bonus if you already played an Action of that Power "
        "this turn. Storm sequence: Torrent (Soaked), then Thunderclap (+2 on Soaked, Chain splash), then Cold Front (spends "
        "Soaked for 3 more)."
    ),
    "Corruption": (
        "At 3 Taint a character's Taint is removed and it becomes Corrupted (Threat Level +1): +2/+2 and it reads its "
        "Corrupted Form, but it is Mute (no Command abilities, can't be targeted by Command cards), Unhealing (damage stays "
        "at Dusk), Compelled (must attack if it can) and Spreading (at its controller's Dusk it places 1 Taint on each "
        "adjacent character its controller controls). Fallen characters enter play Corrupted (+1 Threat Level). Cleanse X "
        "removes X Taint. Purify removes all Taint and ends Corruption: the character becomes Spent, the Threat Level falls "
        "by 1, and it can't gain Taint until the end of the round. A Corrupted character ignores further Taint."
    ),
    "Skies and Landmarks": (
        "One shared Sky; a new Sky replaces the old one. Zenith Alignment (all Grades +1, no darkening), Overcast Veil (all "
        "Grades -1, no Taint), Stormfront (Precipitation and Lightning cost 1 less and deal +1; 1 damage to each Soaked "
        "character at each Dusk), Black Tide (Corrupted +1/+1; each round Taint spreads and the Threat Level rises). Each "
        "player has one Landmark slot; a new Landmark replaces the old. Equipment: one Weapon and one Gear per character. "
        "Boons go on your characters, Afflictions on enemies; at most 2 of each per character."
    ),
    "Keywords": (
        "Guard: while Ready, enemy Actions that target a character must target a Guard character if able. Swift: can attack "
        "the turn it enters. First Strike: deals combat damage first. Skyborne: can't be intercepted by a character with a "
        "lower Grade. Piercing: excess damage on an interceptor goes to Resolve. Reaction: may be played on your opponent's "
        "turn with unspent Vapor. Command: needs a non-Corrupted, non-Mute character you control. Tech: needs no Grade and "
        "can't be reflected by Mirror Cloud or Dark Mirror. Bond (Name): a bonus while you control the named character."
    ),
    "Order of effects": (
        "If both players have effects at the same time, the active player resolves theirs first. If a card contradicts a "
        "rule, the card wins. 'Can't' always beats 'can'."
    ),
    "Rudhabot table rules": (
        "Rudhabot's interpretations where the Codex is open: one Grade 5 character per deck (the deckbuilding table; one "
        "continuity note says two). The coin-flip winner goes first. Mulligan choices are keep / redraw all / redraw the "
        "non-Characters / redraw one card. Reactions are offered in two windows: when an opponent's Action is played (before "
        "it resolves) and in the Clash after interceptors. Dark Mirror and Ava's Selfless are offered in the first window. "
        "Multi-target Actions (Beam Barrage, Blizzard) choose among enemy characters; Voltage Spike offers all-to-one target, "
        "all-to-Resolve, or a sweep that KOs what it can. Cathedral of Honor's Tolling Bells is always used on the first rise "
        "each round. An intercepted attacker whose interceptor left play deals no damage. A match still running after 40 "
        "rounds is decided on Resolve. In rated and casual duels an idle player is auto-moved with the safest option after 10 minutes and forfeits after 3 timeouts in a row; practice games against the Clairvoyance have no turn timer."
    ),
}
