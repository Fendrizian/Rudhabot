# Asgrelia: Cloudfall: implementation notes and traceability

Source of truth: *Asgrelia: Cloudfall — Trading Card Game Codex* (Oct 23, 2026). The Codex has 120 entries: 112 playable
cards, 8 Protocols, and the cloud system, which is rules only. All 120 are implemented.

| Code | Role |
|---|---|
| `rudhabot/tcg/cards.py` | Immutable card data, with Codex text kept verbatim, plus the 8 Protocols |
| `rudhabot/tcg/decks.py` | Deckbuilding rules and the six archetype starter decks |
| `rudhabot/tcg/state.py` | Mutable match state with a fast `clone()` |
| `rudhabot/tcg/engine.py` | The rules: turn flow, combat, Corruption, Threat Level, card effects, legal-move generation |
| `rudhabot/tcg/rules_text.py` | The in-Discord rules reference |
| `services/tcg.py`, `cogs/tcg.py`, `views/tcg_match.py`, `ui/tcg_embeds.py` | Lifecycle and Discord |

## Rule → code → tests

| Codex rule | Engine | Tests (`tests/test_tcg_engine.py` unless noted) |
|---|---|---|
| 40 cards, ≥12 Characters, copy limits by Grade, ≤4 Grade 4, 1 Grade 5, Unique 1 | `decks.validate_deck` | `DeckRuleTests` |
| Setup: Resolve 30, TL 0, Capacity 1, coin flip, 5 cards, one mulligan, first skips draw, second draws 2 | `new_game`, `_start_turn` | `test_setup_*` |
| Vapor: Capacity +1 per Dawn up to 8 (10 by effects), refill at Dawn, unspent kept for Reactions | `_start_turn`, `capacity` | `test_capacity_*` |
| Dawn / Main / Clash / Dusk; round ends after the second player's Dusk | `_start_turn`, `_to_clash`, `_dusk`, `_step_dusk2`, `_round_end` | integration + fuzz |
| Characters enter Ready; can't attack the turn they enter unless Swift | `can_attack` | `test_new_character_cannot_attack_unless_swift` |
| Attackers become Spent; Corrupted (and Lana) must attack | `_declare`, `_compelled`, `legal_actions` | combat tests |
| Interceptors: Ready, not Frozen, one each; Skyborne vs lower Grade | `_intercept_options` | `test_skyborne_*`, `test_frozen_*` |
| Simultaneous damage, First Strike, Piercing, KO at damage ≥ Stamina | `_step_clash_damage`, `check_kos` | `test_first_strike_*`, `test_simultaneous_trade`, `test_piercing_*` |
| Damage removed at Dusk except on the Corrupted | `_step_dusk2` | `test_damage_heals_*` |
| 3 Taint corrupts: +2/+2, Mute, Unhealing, Compelled, Spreading; ignores more Taint | `place_taint`, `corrupt`, `_dusk` | `CorruptionAndThreatTests` |
| Fallen enter Corrupted (TL +1); Cleanse; Purify (Spent, TL −1, no Taint until round end) | `_place_character`, `cleanse`, `purify` | `test_fallen_*`, `test_purify_*` |
| TL +1 each round end from Round 3, ±1 for corruption and KO/Purify, clamped 0–8 | `change_threat`, `_round_end` | `test_threat_*`, `test_ko_of_a_corrupted_*` |
| Protocol in effect = the Severity equal to TL; all 8 Protocols | `protocol`, `_stat_bonus`, `_to_clash`, `_dusk`, `_round_end`, `meets_grade`, `_play_options` | `ProtocolSkyTests` |
| Cloud Grade requirement checked on play; Armageddon ignores it; Helix Tower and Reactor Core | `meets_grade`, `grade`, `max_grade` | `test_grade_requirement_*`, `test_armageddon_*` |
| Soaked (+2 Lightning damage, cleared at round end), Frozen (cleared at controller's Dawn), Chains | `deal`, `_round_end`, `_start_turn`, `_chain` | `test_soaked_*`, `test_cold_front_*`, `test_compressed_chain_*` |
| Command needs a non-Corrupted, non-Mute character; Command can't target Mute or Claude | `can_command`, `command_target_ok` | `test_command_cards_*` |
| Guard redirects enemy targeting (Curved Beam ignores it) | `_single_targets` | `test_guard_*` |
| Reactions (Cloud Shield, Mirror Cloud, Fog Veil, Aerial Retreat, Emergency Purge), Dark Mirror, Selfless | `_reaction_opts`, `_apply_response`, `deal` | `test_mirror_cloud_*`, `test_cloud_shield_*` |
| One Weapon and one Gear; ≤2 Boons and ≤2 Afflictions; attachments leave with the character | `_play_options`, `_detach_all` | `test_equipment_slots_*` |
| Bonds; Harp ignores every Bond except Lana's; Estranged | `_bond_partner`, `_sworn_active`, `_stat_bonus` | `test_bond_harp_and_lana` |
| One Landmark per player, one shared Sky; Calamity blocks new ones | `_play_card`, `_play_options` | `test_calamity_*` |
| Win: Resolve ≤ 0, the Prophecy, decking out; simultaneous 0 is a draw | `_check_resolve`, `_step_dusk2`, `draw` | `WinConditionTests` |
| Beginner levels 1–3 | `_filter_level`, `protocol`, `place_taint`, `_reaction_opts` | engine option |
| Determinism, replay and restart recovery | `replay`; `TcgService.restore` | `test_replay_*`; `test_tcg_service.py` |
| Server authority: duplicate or stale clicks, wrong player, illegal keys | `apply` raises `IllegalAction`; `TcgService.act` checks seat and version | `test_illegal_action_*`; `MatchTests` |

**Card effects:** every one of the 36 Characters (normal and Corrupted Forms), 24 Actions, 18 Equipment, 9 Boons,
9 Afflictions, 9 Landmarks, 3 Events and 4 Skies has an implementation in `engine.py`. They are dispatched by card id in
`_stat_bonus` / `power` / `has_kw` (static abilities), `_dawn_triggers` / `_dusk` / `_declare` / `_place_character` /
`_attach_equipment` (triggers), `ACTIONS` (Action resolvers), `_ability_opts` / `_use_ability` (activated abilities) and
`_resolve_event`.

**Fuzzing:** the suite plays 40 seeded random-legal games across every archetype pairing and checks the invariants. During
development, 300+ further games and more than 16,000 self-play training and evaluation games ran with no engine errors.

## Interpretations

Some matters are open or ambiguous in the Codex. Each interpretation below is shown to players under `/tcg rules` →
*Rudhabot table rules*, and marked `# interp:` in the code.

1. **Grade 5 limit = 1 per deck.** The deckbuilding table, the rarity census and the archetype notes say 1; one continuity
   note says 2. Rudhabot follows the rulebook table.
2. **Coin flip:** the winner goes first (the Codex says the winner *chooses*).
3. **Mulligan options:** keep / redraw all / redraw the non-Characters / redraw one card. The Codex allows any subset, but
   the full list of 32 subsets is impractical in Discord menus.
4. **Reaction timing:** the Codex allows Reactions "any time you could pay". Rudhabot offers them in two windows:
   - when the opponent plays an Action, before it resolves;
   - in the Clash, after interceptors (defender first).

   Your own Reactions are also playable during your own Main phase.
5. **Dark Mirror and Selfless:**
   - Both are offered in the Action-response window.
   - Dark Mirror redirects each hit on May, or on her controller, to the chosen target with +2.
   - Ava's corrupted, forced Selfless redirects every Action hit on her other allies.
6. **Mirror Cloud** returns half the negated card's *printed* damage, rounded up.
7. **Multi-target Actions** (Beam Barrage, Blizzard) choose among enemy characters. **Voltage Spike** offers three ways to
   divide its 7 damage: all to one character, all to Resolve, or a sweep that KOs what it can and sends the rest to Resolve.
8. **Guard** restricts single-target enemy Actions and Kessa's shots, as her Corrupted text implies. Multi-target Actions
   are not restricted.
9. **Tolling Bells** (Cathedral of Honor) is always used on the first Threat Level rise each round.
10. **Threat Level:**
    - Only a KO or a Purify of a Corrupted character lowers it.
    - Replacing a Corrupted character, or returning one to hand, does not.
    - A Fallen character *entering* play doesn't trigger the Prophecy's "becomes Corrupted" extra rise.
11. **Interceptor leaves play:** an intercepted attacker whose interceptor left play deals no damage.
12. **Hovercraft Delivery** attaches equipment without checking its Grade requirement, because the card is attached, not played.
13. **Precipitation triggers** (Valleyflow, Peakfrost, Frostbinder) happen after the Precipitation Action resolves, even
    if it was negated: the card was still *played*.
14. **Simultaneous triggers** at Dawn and Dusk resolve in a fixed field order, not "in any order". Cards a "look at the top"
    effect puts on the bottom go there in a fixed order.
15. **Helix Tower:** Rotating Floors offers one swap of two slots per Dawn (or none).
16. **Card-text readings:**
    - Claude's Corrupted, free Disrupt is limited to once per turn, to prevent zero-cost loops.
    - Corrupted Ysolde gives Corrupted characters +2/+2 and others still +1/+1.
    - Corrupted Mossie's Elite Corps includes herself.
    - The Wahen + Serena discount applies once per turn in total.
17. **Safety valves:**
    - At most 80 actions per turn.
    - A match still running after 40 rounds is decided on Resolve.
    - In duels, an idle player gets a safe default move after 10 minutes (`TCG_TURN_TIMEOUT_S`) and forfeits after 3 timeouts in a row (`TCG_MAX_TIMEOUTS`). Practice games have no turn timer; one abandoned for 24 h (`TCG_PRACTICE_IDLE_S`) is cancelled. Move menus never expire, but a menu from an earlier state is refused.

## Discord experience

- **Getting started:**
  - `/tcg starter` gives one free archetype deck.
  - `/tcg pack` costs 250 from your wallet and gives 5 cards. The weighting is by rarity: Unique and Grade 5 are rarest.
  - `/tcg deck build` opens a text modal (`Dren Voskarl x3, A19 x2, …`) that checks both legality and ownership.
- **Matches:**
  - `/tcg challenge @user [coaching]` runs a PvP match in its own thread. `/tcg practice [difficulty] [opponent]` plays
    against the Clairvoyance AI.
  - The board shows both fields, never a hand, plus a spectator-safe win bar.
  - **Act** opens your ephemeral menu of legal moves. **Hand** shows your hand ephemerally. **🔮 hint** works only with
    coaching on. **Log** shows the recent events.
- **Ratings and rewards:**
  - PvP without coaching is rated: Elo with K = 24, on `/tcg ladder`.
  - The winner earns 150 per rated win, at most 3 rewarded wins a day and at most 2 against the same opponent.
  - Practice and coached games are unrated and unrewarded, so the AI can't be farmed.
- **Data:** `/tcg forget-me` deletes your profile. Matches persist in `tcg_matches.json` as replay recipes; profiles live in
  `tcg.json`. Both are new files, so existing data is untouched.

## Not implemented or remaining gaps

- Spectator-only threads and tournaments are not built.
- Replays are stored only for live matches; finished matches keep a 20-entry summary history per player.
- Reaction windows beyond the two above, and full "any order" trigger sequencing, are not built.
- Balance has not been playtested. The Codex's watchlist levers are not applied, since the Codex marks them as conditional.

## Skyfall Wishes (gacha)

`/tcg wish banner | pull | shards | daily | craft | convergence`. Rules live in `domain/gacha.py` and the service in
`services/gacha.py`. Every roll is made server-side with the bot's RNG.

**Rarity:**

| Rarity | Cards | Count |
|---|---|---|
| ★5 Pure White | Grade 5 characters and Unique cards | 8 |
| ★4 White | Grade 4 characters, and other cards costing 5+ | 8 |
| ★3 Light Gray | Grade 3 characters, and other cards costing 3–4 | 38 |
| ★2 Gray | everything else | 58 |

**Rates (published in `/tcg wish banner`):**
- Base: ★5 1.6% · ★4 9% · ★3 28% · ★2 61.4%.
- Measured over 300,000 simulated wishes including pity: ★5 2.6%, about 39 wishes per ★5 on average.

**Pity and the featured banner:**
- From wish 56 the ★5 chance rises by 5 points per wish. Wish 75 is a guaranteed ★5.
- Every 10th wish without a ★4 or better is a guaranteed ★4.
- Each ISO week features one ★5 and three ★4, rotating through every ★5.
- A ★5 is the featured one 50% of the time. If you lose the 50/50, your next ★5 is the featured one (shown as
  "next ★5 is featured"), so in practice the featured share is 2/3.
- The standard banner has no rate-up and keeps its own pity.

**Currency:**
- One wish costs 160 Cloud Shards.
- You can get shards from:
  - `/tcg wish daily`: 80 a day;
  - `/tcg wish shards`: 10 ra each (one wish = 1,600 ra), paid from your wallet or credit card, up to 16,000 per purchase;
  - rated PvP wins: +60, under the same daily cap as the ra reward;
  - practice wins against Clairvoyance: +20, up to 3 a day.

**Extras:**
- **Cloudshine:** 4% of cards come as cosmetic ✨ foils, tracked separately.
- **Vapor Dust and crafting:** copies beyond a card's deck limit become Dust (★2 5 · ★3 20 · ★4 80 · ★5 300).
  `/tcg wish craft` makes any card (★2 30 · ★3 100 · ★4 400 · ★5 1,600).
- **Convergence:** every wish anyone makes fills a server-wide weekly meter. At 250 / 750 / 1,500 wishes, everyone who
  wished that week can claim 160 / 320 / 640 shards with `/tcg wish convergence`.

**Auditing:** shard and dust balances appear in the audit trail, and ra spent on shards is audited like any other wallet
change.
