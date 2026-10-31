"""The Asgrelia: Cloudfall rules engine (Codex Part II + Part III card text). Deterministic and server-authoritative.

Public API
    new_game(decks, names, seed, level=3, first=None) -> Game
    legal_actions(game) -> list[Act]          every move the deciding player may make right now
    apply(game, key)                           apply one move by its key (raises IllegalAction otherwise)
    decider(game) -> seat | None               who must act
    replay(decks, names, seed, keys, ...)      rebuild a match exactly (restart recovery)

Every decision is a data-only `Act`. Discord, the Clairvoyance AI and the tests all drive the game through the same three calls,
so nothing outside this module can adjudicate an outcome. Interpretations of under-specified rules are listed in
docs/TCG.md and marked `# interp:` below.
"""
from __future__ import annotations

import math
from itertools import combinations
from typing import Callable, Iterable, Optional

from rudhabot.tcg.cards import (ACTION, AFFLICTION, BOON, CARD_BY_ID, CHARACTER, COMMAND, COMPRESSED, CORRUPTION,
                                EQUIPMENT, EVENT, LANDMARK, LIGHTNING, PRECIPITATION, PROTOCOL_BY_LEVEL, SKY, WEATHER)
from rudhabot.tcg.state import Game, Player, Unit

MAX_ROUNDS = 40              # interp: safety valve, a match still running after 40 rounds is decided on Resolve
MAX_ACTIONS_PER_TURN = 80    # interp: safety valve against zero-cost ability loops
HAND_LIMIT = 8
CLOUD_PROJECTILES = (COMPRESSED, PRECIPITATION, LIGHTNING)
SWEEPERS = {"A08", "A10", "A11"}   # Actions that damage enemy characters without targeting them


class IllegalAction(ValueError):
    pass


class Act:
    __slots__ = ("key", "label", "data")

    def __init__(self, key: str, label: str, data=None):
        self.key, self.label, self.data = key, label, data

    def __repr__(self):
        return f"Act({self.key!r})"


def name(cid: str) -> str:
    return CARD_BY_ID[cid].name


def uname(u: Unit) -> str:
    return CARD_BY_ID[u.cid].name.split()[0]


# =============================================================================================== static queries
def protocol(g: Game) -> int:
    """The Severity of the Protocol in effect (0 = none). Beginner level 1 treats the track as a plain clock."""
    if g.level == 1:
        return 0
    level = g.threat
    if g.mandate is not None:
        level = max(0, min(8, g.threat + g.mandate[1]))
    return level


def controls(g: Game, seat: int, cid: str, corrupted: Optional[bool] = None) -> Optional[Unit]:
    for u in g.players[seat].field:
        if u is not None and u.cid == cid and (corrupted is None or u.corrupted == corrupted):
            return u
    return None


def in_play(g: Game, cid: str, corrupted: Optional[bool] = None) -> list[Unit]:
    return [u for u in g.all_units() if u.cid == cid and (corrupted is None or u.corrupted == corrupted)]


def landmark(g: Game, seat: int, cid: str) -> bool:
    return g.players[seat].landmark == cid


def any_landmark(g: Game, cid: str) -> list[int]:
    return [p.seat for p in g.players if p.landmark == cid]


def affiliations(g: Game, u: Unit) -> frozenset:
    if protocol(g) == 7:
        return frozenset({"Capital", "Jahnen", "Nevux", "Aptevium", "Brenesia", "Huttava", "Mellines", "Ontasaro",
                          "Sanctimonia", "Terra Eden", "Coalition"})
    return frozenset(u.card.affiliations)


def grade(g: Game, u: Unit) -> int:
    value = 5 if u.gear == "E16" else u.card.grade
    value -= u.disrupt
    value -= u.afflictions.count("D04")
    if g.sky == "S1":
        value += 1
    elif g.sky == "S2":
        value -= 1
    return max(1, min(5, value))


def is_mute(g: Game, u: Unit, for_command: bool = False) -> bool:
    if "D02" in u.afflictions:
        return True
    if u.corrupted:
        return not (for_command and controls(g, u.owner, "C10", corrupted=True))
    return False


def estranged(u: Unit) -> bool:
    return "D09" in u.afflictions


def _bond_partner(g: Game, u: Unit, partner_cid: str) -> bool:
    """Bond (Name) is active: you control the named character and neither side is Estranged."""
    if estranged(u):
        return False
    other = controls(g, u.owner, partner_cid)
    return other is not None and not estranged(other)


def _sworn_active(g: Game, u: Unit) -> bool:
    if u.sworn is None or estranged(u) or u.cid == "C01":     # Harp ignores every Bond except Lana's
        return False
    other = g.unit(u.sworn)
    return other is not None and other.owner == u.owner and not estranged(other)


def has_active_bond(g: Game, u: Unit) -> bool:
    if u.cid == "C01":
        return _bond_partner(g, u, "C08")
    if u.cid == "C08":
        return _bond_partner(g, u, "C01")
    if u.cid == "C03":
        return _bond_partner(g, u, "C01")
    if u.cid == "C11":
        return _bond_partner(g, u, "C08")
    if u.cid == "C22":
        return _bond_partner(g, u, "C25")
    if u.cid == "C25":
        return _bond_partner(g, u, "C22")
    return _sworn_active(g, u)


def _distinct_affiliations(g: Game, seat: int) -> int:
    affs: set = set()
    for u in g.players[seat].units():
        affs |= affiliations(g, u)
    return len(affs)


def _stat_bonus(g: Game, u: Unit) -> tuple[int, int]:
    """Static +Power/+Stamina from every source except the combat-only ones."""
    p = s = 0
    seat = u.owner
    owner = g.players[seat]
    if u.corrupted:
        p += 2; s += 2
    p += u.perm_power + u.tmp_power
    s += u.perm_stamina + u.tmp_stamina
    # equipment
    weapon_power = {"E01": 2, "E02": 3, "E03": 1, "E04": 1, "E05": 1, "E06": 1, "E07": 1, "E08": 4}
    p += weapon_power.get(u.weapon, 0)
    if u.weapon == "E07":
        s += 1
    gear = {"E10": (0, 3), "E11": (0, 2), "E12": (0, 1), "E15": (0, 1), "E16": (1, 0), "E18": (0, 1)}.get(u.gear, (0, 0))
    p += gear[0]; s += gear[1]
    # boons and afflictions
    for b in u.boons:
        if b == "B02" and not is_mute(g, u):
            p += 2 + (1 if "command" in owner.turn_flags else 0)
        elif b == "B03":
            p += 2
        elif b == "B04":
            p += 3
        elif b == "B05":
            p += 1; s += 2
    if _sworn_active(g, u):
        p += 1; s += 1
    p -= 2 * u.afflictions.count("D06")
    # bonds
    cid = u.cid
    if cid in ("C01", "C08") and _bond_partner(g, u, "C08" if cid == "C01" else "C01"):
        p += 1; s += 1
    if cid == "C03" and _bond_partner(g, u, "C01"):
        p += 2; s += 2
    if cid == "C11" and _bond_partner(g, u, "C08"):
        p += 1
    if cid in ("C22", "C25") and _bond_partner(g, u, "C25" if cid == "C22" else "C22"):
        p += 1; s += 1
    # self abilities
    if cid == "C18":
        p += u.taint
    if cid == "C23" and u.corrupted:
        n = len(u.equipment()); p += n; s += n
    if cid == "C30" and owner.landmark:
        p += 1; s += 1
    if cid == "C06" and protocol(g) > 0:
        p += 1; s += 1
    if cid == "C20" and u.corrupted:
        p += sum(1 for o in owner.units() if o is not u and o.corrupted)
    affs = affiliations(g, u)
    # auras from allies
    for o in owner.units():
        if o.cid == "C11":
            if o.corrupted:
                p += 2
            elif o is not u and "Jahnen" in affs:
                p += 1
        elif o.cid == "C20" and o is not u and o.ready:
            s += 1
        elif o.cid == "C16" and u.equipment():
            if o.corrupted and u.corrupted:
                p += 2; s += 2
            else:
                p += 1; s += 1
        elif o.cid == "C28" and o.corrupted:
            n = _distinct_affiliations(g, seat); p += n; s += n
    # landmarks
    if owner.landmark == "M01" and protocol(g) > 0 and affs & {"Capital", "Coalition"}:
        p += 1; s += 1
    if owner.landmark == "M03" and has_active_bond(g, u):
        p += 1; s += 1
    # protocols
    level = protocol(g)
    if level == 1 and "Capital" not in affs:
        s += 1
    elif level == 3:
        p += min(2, sum(1 for o in owner.units() if o is not u and affiliations(g, o) & affs))
    elif level == 6 and affs & {"Capital", "Coalition"}:
        p += 2; s += 2
    elif level == 7:
        p += 2
    elif level == 8:
        p += 3; s += 3
    if g.sky == "S4" and u.corrupted:
        p += 1; s += 1
    return p, s


def stamina(g: Game, u: Unit) -> int:
    return max(0, u.card.stamina + _stat_bonus(g, u)[1])


def power(g: Game, u: Unit, vs: Optional[Unit] = None, role: Optional[str] = None) -> int:
    value = u.card.attack + _stat_bonus(g, u)[0]
    if vs is not None:
        vs_affs = affiliations(g, vs)
        if u.cid == "C19" and (u.corrupted or "Jahnen" in vs_affs):
            value += 2
        if u.cid == "C21" and "Jahnen" in vs_affs:
            value += 2
        if u.cid == "C35":
            if not u.corrupted and vs.corrupted:
                value += 3
            elif u.corrupted and not vs.corrupted:
                value += 3
        if u.weapon == "E07" and vs.corrupted:
            value += 3
        if u.cid == "C32" and role == "intercept" and vs.card.cost > u.card.cost:
            value += 2
    if role == "attack":
        owner, enemy = g.players[u.owner], g.players[g.other(u.owner)]
        if u.cid == "C31" and len(owner.units()) < len(enemy.units()):
            value += 2
        rhea = g.unit(next((a for a in g.attackers if (x := g.unit(a)) and x.cid == "C14" and x.corrupted), -1))
        if rhea is not None and rhea is not u and rhea.owner == u.owner:
            value += 2
    return max(0, value)


def has_kw(g: Game, u: Unit, kw: str, role: Optional[str] = None) -> bool:
    card = u.card
    if kw == "Guard":
        if u.cid == "C35" and u.corrupted:
            return u.gear == "E11"
        return "Guard" in card.keywords or u.gear == "E11"
    if kw == "Swift":
        return "Swift" in card.keywords or u.gear in ("E12", "E17") or u.tmp_swift
    if kw == "First Strike":
        if "First Strike" in card.keywords:
            return True
        return role == "intercept" and landmark(g, u.owner, "M08") and grade(g, u) <= 3
    if kw == "Skyborne":
        return ("Skyborne" in card.keywords or "B06" in u.boons or (u.cid == "C32" and u.corrupted)
                or (landmark(g, u.owner, "M04") and grade(g, u) <= 2))
    if kw == "Piercing":
        return (u.cid == "C12" and u.corrupted) or u.weapon == "E03"
    return False


def capacity(g: Game, seat: int) -> int:
    p = g.players[seat]
    bonus = 0
    if p.landmark in ("M01", "M07"):
        bonus += 1
    if any("B08" in u.boons for u in p.units()):
        bonus += 1                       # only one Verdant Boon counts
    return min(10, p.base_capacity + bonus)


def max_grade(g: Game, seat: int, weather: bool = False) -> int:
    best = 0
    helix = landmark(g, seat, "M02")
    for u in g.players[seat].units():
        gr = grade(g, u)
        if weather and helix and gr == 4:
            gr = 5
        best = max(best, gr)
    return best


def meets_grade(g: Game, seat: int, card) -> bool:
    if card.grade <= 0 or card.kind == CHARACTER or protocol(g) == 8:
        return True
    return max_grade(g, seat, weather=card.power == WEATHER) >= card.grade


def can_command(g: Game, seat: int) -> bool:
    return any(not is_mute(g, u, for_command=True) for u in g.players[seat].units())


def command_target_ok(g: Game, u: Unit, seat: int) -> bool:
    return u.owner == seat and u.cid != "C15" and not is_mute(g, u, for_command=True)


def remaining(g: Game, u: Unit) -> int:
    return stamina(g, u) - u.damage


# =============================================================================================== costs
def cost(g: Game, seat: int, cid: str, target: Optional[Unit] = None) -> int:
    card = CARD_BY_ID[cid]
    value = card.cost
    p = g.players[seat]

    def less(n: int, floor: int) -> None:
        nonlocal value
        value = max(floor, value - n) if value > floor else value

    if card.kind == EQUIPMENT:
        if controls(g, seat, "C15"):
            less(1, 1)
        if p.landmark == "M02":
            less(1, 1)
        if card.palvanium:
            if controls(g, seat, "C22"):
                less(1, 0)
            if controls(g, seat, "C23"):
                less(1, 1)
            if p.landmark == "M05":
                less(1, 0)
    if card.kind == ACTION and card.power == LIGHTNING:
        if controls(g, seat, "C08"):
            less(1, 1)
        if any(u.weapon == "E04" for u in p.units()):
            less(1, 1)
        if g.sky == "S3":
            less(1, 1)
    if card.kind == ACTION and card.power == PRECIPITATION:
        if p.landmark == "M04":
            less(1, 1)
        if g.sky == "S3":
            less(1, 1)
        twins = controls(g, seat, "C22") and controls(g, seat, "C25")
        if twins and "precip_discount" not in p.turn_flags and _bond_partner(g, controls(g, seat, "C22"), "C25"):
            less(1, 0)
    if card.is_command and "relay" not in p.turn_flags and any(u.gear == "E14" for u in p.units()):
        less(1, 0)
    if cid == "B04" and target is not None and target.cid == "C18":
        value = 0
    if cid == "M10" and controls(g, seat, "C06"):
        less(3, 0)
    return value


# =============================================================================================== taint, threat, damage
def change_threat(g: Game, delta: int, reason: str = "") -> None:
    if delta == 0:
        return
    if delta > 0:
        for seat in any_landmark(g, "M06"):
            p = g.players[seat]
            if "bells" not in p.round_flags:
                p.round_flags.add("bells")           # interp: Tolling Bells is always used on the first rise each round
                g.say(f"🔔 Cathedral of Honor's Tolling Bells prevent the Threat Level from rising ({reason}).")
                return
    if delta < 0 and in_play(g, "C02", corrupted=True):
        return
    old = g.threat
    g.threat = max(0, min(8, g.threat + delta))
    if g.threat != old:
        g.say(f"Threat Level {old} → {g.threat}" + (f" ({reason})" if reason else ""))
        _protocol_check(g)


def _protocol_check(g: Game) -> None:
    level = protocol(g)
    if level == g.protocol_seen:
        return
    g.protocol_seen = level
    for u in in_play(g, "C02"):
        p = g.players[u.owner]
        if "scheme" not in p.turn_flags:
            p.turn_flags.add("scheme")
            draw(g, u.owner, 1, "Hidden Scheme")


def place_taint(g: Game, u: Unit, n: int, source_seat: int) -> None:
    if g.level < 3 or u.corrupted or n <= 0 or g.unit(u.uid) is None:
        return
    if g.sky == "S2" or "D08" in u.afflictions or "B05" in u.boons or u.no_taint_round:
        return
    if source_seat != u.owner and controls(g, u.owner, "C33"):
        return
    u.taint += n
    if u.taint >= 3:
        corrupt(g, u)


def corrupt(g: Game, u: Unit) -> None:
    u.taint = 0
    u.corrupted = True
    g.say(f"☠️ {uname(u)} becomes Corrupted.")
    change_threat(g, 1, "Corruption")
    for _ in any_landmark(g, "M09"):
        change_threat(g, 1, "the Prophecy")


def cleanse(g: Game, u: Unit, n: int) -> None:
    if u.taint <= 0:
        return
    u.taint = max(0, u.taint - n)


def purify(g: Game, u: Unit) -> None:
    was = u.corrupted
    u.taint = 0
    u.no_taint_round = True
    if was:
        u.corrupted = False
        u.ready = False
        g.say(f"✨ {uname(u)} is Purified.")
        change_threat(g, -1, "Purify")


def freeze(g: Game, u: Unit) -> None:
    if controls(g, u.owner, "C25"):            # Highborn
        return
    u.frozen = True


def disrupt(g: Game, u: Unit, n: int) -> None:
    u.disrupt += n


def _shielded(amount: int, holder) -> int:
    if holder.shield > 0:
        blocked = min(holder.shield, amount)
        holder.shield -= blocked
        amount -= blocked
    return amount


def deal(g: Game, ref: tuple, amount: int, src: dict) -> int:
    """Deal damage to ('u', uid) or ('p', seat). `src` = {kind: action|combat|effect, seat, power, unit, stack}."""
    if amount <= 0 or g.over:
        return 0
    stack = src.get("stack")
    if stack is not None and src.get("kind") == "action" and src.get("power") in CLOUD_PROJECTILES:
        may = stack.get("may")
        if stack.get("reflect") is not None and may is not None:
            may_unit = g.unit(may)
            if may_unit is not None and (ref == ("u", may) or ref == ("p", may_unit.owner)):
                g.say("🪞 Dark Mirror reflects the attack.")
                return deal(g, stack["reflect"], amount + 2, {"kind": "effect", "seat": may_unit.owner})
    if ref[0] == "p":
        player = g.players[ref[1]]
        amount = _shielded(amount, player)
        player.resolve -= amount
        return amount
    u = g.unit(ref[1])
    if u is None:
        return 0
    if stack is not None and stack.get("selfless") and src.get("kind") == "action":
        ally, ava_uid = stack["selfless"]
        ava = g.unit(ava_uid)
        if ava is not None and u.owner == ava.owner and u is not ava and (ally is None or ally == u.uid):
            u = ava
    if src.get("kind") == "action" and src.get("power") == LIGHTNING and u.soaked:
        amount += 2
    if u.frozen and in_play(g, "C27", corrupted=True):
        amount *= 2
    if "D03" in u.afflictions and not u.marked_used:
        u.marked_used = True
        amount += 2
    if "B01" in u.boons and not u.aegis_used:
        u.aegis_used = True
        amount = max(0, amount - 2)
    if u.gear == "E10" and src.get("kind") == "action" and not u.plating_used:
        u.plating_used = True
        amount = max(0, amount - 2)
    amount = _shielded(amount, u)
    if amount <= 0:
        return 0
    u.damage += amount
    source = g.unit(src["unit"]) if src.get("unit") else None
    if source is not None:
        if source.cid == "C21" and "D08" not in source.afflictions:
            place_taint(g, u, 1, source.owner)
        if source.gear == "E09":
            disrupt(g, u, 1)
        if source.cid == "C31" and source.corrupted and u.damage >= stamina(g, u):
            source.perm_power += 2; source.perm_stamina += 2
    if u.cid == "C03" and u.corrupted:
        _queue_target(g, u.owner, "dmgN", _enemy_units(g, u.owner), f"Ava strikes back for {amount}:", n=amount)
    return amount


# =============================================================================================== zones
def draw(g: Game, seat: int, n: int = 1, why: str = "") -> None:
    p = g.players[seat]
    for _ in range(n):
        if not p.deck:
            _finish(g, g.other(seat), f"{p.name} had to draw from an empty deck")
            return
        p.hand.append(p.deck.pop())


def gain_resolve(g: Game, seat: int, n: int) -> None:
    if n <= 0:
        return
    g.players[seat].resolve += n
    if controls(g, seat, "C29", corrupted=True):
        _queue_target(g, seat, "taint1", _enemy_units(g, seat), "Tainted Feast: place 1 Taint on")


def _detach_all(g: Game, u: Unit) -> list[str]:
    cards = u.equipment() + list(u.boons) + list(u.afflictions)
    if u.sworn is not None:
        partner = g.unit(u.sworn)
        if partner is not None:
            partner.sworn = None
            if "B09" in partner.boons:
                partner.boons.remove("B09")
                g.players[partner.owner].rainfall.append("B09")
    return cards


def leave_play(g: Game, u: Unit, to_hand: bool = False, ko: bool = False) -> None:
    """Remove a character with its attachments. Only a KO of a Corrupted character lowers the Threat Level."""
    owner = g.players[u.owner]
    if u not in owner.field:
        return
    owner.field[owner.field.index(u)] = None
    owner.rainfall.extend(_detach_all(g, u))
    (owner.hand if to_hand else owner.rainfall).append(u.cid)
    if u.corrupted and ko:
        change_threat(g, -1, f"{uname(u)} is KO'd")


def check_kos(g: Game) -> None:
    for _ in range(12):
        dead = [u for u in g.all_units() if u.damage >= stamina(g, u)]
        if not dead:
            break
        for u in dead:
            if g.unit(u.uid) is None:
                continue
            meltdown = u.cid == "C18" and u.corrupted
            if u.gear == "E13":
                u.gear = None
                g.players[u.owner].rainfall.append("E13")
                g.say(f"⏳ Temporal Stabilizer returns {uname(u)} to hand.")
                leave_play(g, u, to_hand=True)
            else:
                g.say(f"💥 {uname(u)} is KO'd.")
                leave_play(g, u, ko=True)
            if meltdown:
                g.say("☢️ Fenn's Meltdown deals 3 damage to every character.")
                for o in g.all_units():
                    deal(g, ("u", o.uid), 3, {"kind": "effect", "seat": u.owner})
    _check_resolve(g)


def _check_resolve(g: Game) -> None:
    if g.over:
        return
    dead = [p.resolve <= 0 for p in g.players]
    if all(dead):
        _finish(g, -1, "both players reached 0 Resolve at the same moment")
    elif dead[0]:
        _finish(g, 1, f"{g.players[0].name}'s Resolve fell to 0")
    elif dead[1]:
        _finish(g, 0, f"{g.players[1].name}'s Resolve fell to 0")


def _finish(g: Game, winner: int, reason: str) -> None:
    if g.over:
        return
    g.winner, g.end_reason, g.phase = winner, reason, "over"
    g.pending.clear()
    g.resume.clear()
    g.stack = None
    g.say(f"🏁 {reason}. " + ("Draw." if winner == -1 else f"{g.players[winner].name} wins!"))


# =============================================================================================== prompts
def _push(g: Game, seat: int, kind: str, opts: list[tuple], text: str, optional: bool = False, **data) -> None:
    """Queue a decision. opts: [(key, label, payload)]. A mandatory prompt with one option resolves at once."""
    if optional:
        opts = [("skip", "Skip", None)] + list(opts)
    if not opts:
        return
    if len(opts) == 1 and not optional:
        RESOLVERS[kind](g, {"kind": kind, "seat": seat, **data}, opts[0][2])
        return
    g.pending.append({"kind": kind, "seat": seat, "opts": opts, "text": text, **data})


def _unit_opts(units: Iterable[Unit], prefix: str = "") -> list[tuple]:
    return [(f"u{u.uid}", f"{prefix}{uname(u)}", u.uid) for u in units]


def _enemy_units(g: Game, seat: int) -> list[Unit]:
    return g.players[g.other(seat)].units()


def _queue_target(g: Game, seat: int, kind: str, units: list[Unit], text: str, optional: bool = False, **data) -> None:
    _push(g, seat, kind, _unit_opts(units), text, optional=optional, **data)


def then(g: Game, *tokens: str) -> None:
    g.resume.extend(reversed(tokens))


# ---------------------------------------------------------------------------------- prompt resolvers
def _r_taint1(g, pr, uid):
    u = g.unit(uid)
    if u is not None:
        place_taint(g, u, 1, pr["seat"])


def _r_taint2(g, pr, uid):
    u = g.unit(uid)
    if u is not None:
        place_taint(g, u, 2, pr["seat"])


def _r_dmg(n):
    def resolve(g, pr, uid):
        if uid is not None:
            deal(g, ("u", uid), pr.get("n", n), {"kind": "effect", "seat": pr["seat"]})
            check_kos(g)
    return resolve


def _r_cleanse1(g, pr, uid):
    u = g.unit(uid) if uid is not None else None
    if u is not None:
        cleanse(g, u, 1)


def _r_purify(g, pr, uid):
    u = g.unit(uid) if uid is not None else None
    if u is not None:
        purify(g, u)


def _r_soak(g, pr, uid):
    u = g.unit(uid) if uid is not None else None
    if u is not None:
        u.soaked = True


def _r_freeze(g, pr, uid):
    u = g.unit(uid) if uid is not None else None
    if u is not None:
        freeze(g, u)


def _r_frostbinder(g, pr, payload):
    if payload is None:
        return
    uid, what = payload
    u = g.unit(uid)
    if u is not None:
        if what == "soak":
            u.soaked = True
        else:
            freeze(g, u)


def _r_discard_equipment(g, pr, payload):
    if payload is None:
        return
    uid, slot = payload
    u = g.unit(uid)
    if u is None or getattr(u, slot) is None or getattr(u, slot) == "E02":
        return
    g.players[u.owner].rainfall.append(getattr(u, slot))
    g.say(f"🔧 {name(getattr(u, slot))} is discarded from {uname(u)}.")
    setattr(u, slot, None)
    check_kos(g)


def _r_discard_landmark(g, pr, seat):
    if seat is None:
        return
    p = g.players[seat]
    if p.landmark:
        g.say(f"🏚️ {name(p.landmark)} is discarded.")
        p.rainfall.append(p.landmark)
        p.landmark = None
        check_kos(g)


def _r_mulligan(g, pr, payload):
    p = g.players[pr["seat"]]
    if payload == "keep":
        return
    if payload == "all":
        back = list(p.hand)
    elif payload == "noncharacters":
        back = [c for c in p.hand if CARD_BY_ID[c].kind != CHARACTER]
    else:
        back = [p.hand[payload]]
    for c in back:
        p.hand.remove(c)
    p.deck.extend(back)
    g.rng.shuffle(p.deck)
    draw(g, p.seat, len(back))


def _r_look_take(g, pr, idx):
    p = g.players[pr["seat"]]
    top = [p.deck.pop() for _ in range(min(pr["n"], len(p.deck)))]
    if idx is not None and idx < len(top):
        p.hand.append(top.pop(idx))
    p.deck[0:0] = top                           # rest to the bottom


def _r_look_bottom(g, pr, mask):
    owner = g.players[pr["target"]]
    n = min(pr["n"], len(owner.deck))
    top = [owner.deck.pop() for _ in range(n)]          # top[0] is the top card
    keep = [c for i, c in enumerate(top) if not (mask >> i) & 1]
    bottom = [c for i, c in enumerate(top) if (mask >> i) & 1]
    owner.deck[0:0] = bottom
    owner.deck.extend(reversed(keep))


def _r_take_card(g, pr, cid):
    """Search the deck for `cid` (Bexley, Pelle, Archive Query) and put it into the hand."""
    p = g.players[pr["seat"]]
    if pr.get("from_top"):
        top = [p.deck.pop() for _ in range(min(len(pr["top"]), len(p.deck)))]
        if cid is not None and cid in top:
            top.remove(cid)
            p.hand.append(cid)
        p.deck[0:0] = top                          # the rest to the bottom
        return
    if cid is not None and cid in p.deck:
        p.deck.remove(cid)
        p.hand.append(cid)
    g.rng.shuffle(p.deck)


def _r_delivery(g, pr, payload):
    p = g.players[pr["seat"]]
    if payload is not None:
        cid, uid = payload
        u = g.unit(uid)
        if u is not None and cid in p.deck:
            p.deck.remove(cid)
            _attach_equipment(g, u, cid)
    g.rng.shuffle(p.deck)


def _r_threat(g, pr, delta):
    if delta:
        change_threat(g, delta, pr.get("why", ""))
        if pr.get("draw_on_raise") and delta > 0:
            draw(g, pr["seat"], 2, "Orren")


def _r_mandate(g, pr, offset):
    g.mandate = (pr["seat"], offset) if offset else None
    _protocol_check(g)


def _r_yes_draw(g, pr, yes):
    if yes:
        u = g.unit(pr["uid"])
        if u is not None:
            u.ready = False
            draw(g, pr["seat"], 1)


def _r_burnout(g, pr, yes):
    u = g.unit(pr["uid"])
    if yes and u is not None and g.players[pr["seat"]].vapor >= 2:
        g.players[pr["seat"]].vapor -= 2
        u.ready = True


def _r_reactor(g, pr, yes):
    u = g.unit(pr["uid"])
    if u is None or u.gear != "E16":
        return
    p = g.players[pr["seat"]]
    if yes and p.vapor >= 1:
        p.vapor -= 1
    else:
        u.gear = None
        p.rainfall.append("E16")


def _r_swap(g, pr, pair):
    if pair is None:
        return
    a, b = pair
    f = g.players[pr["seat"]].field
    f[a], f[b] = f[b], f[a]


def _r_discard_hand(g, pr, cid):
    p = g.players[pr["seat"]]
    if cid in p.hand:
        p.hand.remove(cid)
        p.rainfall.append(cid)
    if len(p.hand) > HAND_LIMIT:
        _discard_prompt(g, p.seat)


def _r_landmark_choice(g, pr, seat):
    _r_discard_landmark(g, pr, seat)


def _r_respond(g, pr, payload):
    _apply_response(g, pr["seat"], payload, window=pr.get("window", "stack"))


RESOLVERS: dict[str, Callable] = {
    "mulligan": _r_mulligan, "look_take": _r_look_take, "look_bottom": _r_look_bottom, "take_card": _r_take_card,
    "delivery": _r_delivery, "threat": _r_threat, "mandate": _r_mandate, "overwork": _r_yes_draw,
    "burnout": _r_burnout, "reactor": _r_reactor, "swap": _r_swap, "discard": _r_discard_hand,
    "taint1": _r_taint1, "taint2": _r_taint2, "dmg1": _r_dmg(1), "dmg2": _r_dmg(2), "dmgN": _r_dmg(0),
    "cleanse1": _r_cleanse1, "purify": _r_purify, "soak": _r_soak, "freeze": _r_freeze, "frostbinder": _r_frostbinder,
    "discard_equipment": _r_discard_equipment, "discard_landmark": _r_landmark_choice, "respond": _r_respond,
}


def _discard_prompt(g: Game, seat: int) -> None:
    p = g.players[seat]
    opts = [(f"d{cid}", f"Discard {name(cid)}", cid) for cid in sorted(set(p.hand))]
    _push(g, seat, "discard", opts, f"Hand limit {HAND_LIMIT}: choose a card to discard")


def _look_bottom_prompt(g: Game, seat: int, target: int, n: int, why: str) -> None:
    deck = g.players[target].deck
    n = min(n, len(deck))
    if n == 0:
        return
    top = [deck[-1 - i] for i in range(n)]
    shown = top if target == seat else top    # Dalia's corrupted form looks at the opponent's cards (shown to her controller)
    opts = [("m0", "Keep them on top", 0)]
    for i, c in enumerate(shown):
        opts.append((f"m{1 << i}", f"Bottom {name(c)}", 1 << i))
    if n == 2:
        opts.append(("m3", "Bottom both", 3))
    _push(g, seat, "look_bottom", opts, f"{why}: top of {'your' if target == seat else 'the opponent'} deck: "
          + ", ".join(name(c) for c in shown), target=target, n=n)


# =============================================================================================== setup and turn flow
def new_game(decks: list[list[str]], names: list[str], seed: int, level: int = 3, first: Optional[int] = None) -> Game:
    decks = [_filter_level(list(d), level) for d in decks]
    g = Game(decks, names, seed, beginner_level=level)
    for p in g.players:
        g.rng.shuffle(p.deck)
    coin = g.rng.randrange(2)                  # always drawn, so replays with an explicit `first` stay in sync
    g.first = coin if first is None else first # interp: the coin-flip winner goes first
    g.active = g.first
    for p in g.players:
        draw(g, p.seat, 5)
    g.say(f"🪙 {g.players[g.first].name} goes first.")
    for seat in (g.first, g.other(g.first)):
        p = g.players[seat]
        opts = [("keep", "Keep this hand", "keep"), ("all", "Mulligan: redraw all 5", "all")]
        if any(CARD_BY_ID[c].kind != CHARACTER for c in p.hand):
            opts.append(("noncharacters", "Mulligan: redraw the non-Characters", "noncharacters"))
        for i, c in enumerate(p.hand):
            opts.append((f"one{i}", f"Mulligan: redraw only {name(c)}", i))
        g.pending.append({"kind": "mulligan", "seat": seat, "opts": opts, "text": "Opening hand: keep or mulligan once"})
    then(g, "begin")
    return g


def _filter_level(deck: list[str], level: int) -> list[str]:
    if level >= 3:
        return deck
    if level == 2:
        return [c for c in deck if CARD_BY_ID[c].kind != SKY and CARD_BY_ID[c].power != CORRUPTION]
    allowed = (CHARACTER, ACTION, EQUIPMENT)
    return [c for c in deck if CARD_BY_ID[c].kind in allowed and not CARD_BY_ID[c].reaction
            and CARD_BY_ID[c].power != CORRUPTION]


def _step_begin(g: Game) -> None:
    _start_turn(g, g.first)


def _start_turn(g: Game, seat: int) -> None:
    g.turn += 1
    g.active = seat
    g.phase = "main"
    for p in g.players:
        p.turn_flags.clear()
        p.shield = 0
        p.actions_this_turn = 0
    for u in g.all_units():
        u.aegis_used = u.plating_used = u.marked_used = u.fog = u.attacked = False
        u.shield = 0
    if g.mandate is not None and g.mandate[0] == seat:
        g.mandate = None
        _protocol_check(g)
    p = g.players[seat]
    p.dawns += 1
    p.base_capacity = min(8, p.base_capacity + 1)
    p.vapor = capacity(g, seat)
    g.say(f"🌅 Round {g.round}, {p.name}'s Dawn: {p.vapor} Vapor.")
    serena_lock = controls(g, g.other(seat), "C25", corrupted=True)
    for u in p.units():
        if not serena_lock:
            u.frozen = False
        if not u.ready:
            if "D05" in u.afflictions:
                if p.vapor >= 2:
                    _push(g, seat, "burnout", [("yes", f"Pay 2 Vapor to Ready {uname(u)}", True),
                                               ("no", "Leave it Spent", False)], "Burnout", uid=u.uid)
            else:
                u.ready = True
    if not (seat == g.first and p.dawns == 1):
        draw(g, seat, 2 if (seat != g.first and p.dawns == 1) else 1)
    if g.over:
        return
    if protocol(g) == 7:
        for q in g.players:
            for u in q.units():
                cleanse(g, u, 1)
    _dawn_triggers(g, seat)
    then(g, "main")


def _dawn_triggers(g: Game, seat: int) -> None:
    p = g.players[seat]
    for u in list(p.units()):
        cid, corr = u.cid, u.corrupted
        if cid == "C01" and not corr and len(p.deck) > 0:
            n = min(3, len(p.deck))
            top = [p.deck[-1 - i] for i in range(n)]
            opts = [(f"t{i}", f"Take {name(c)}", i) for i, c in enumerate(top)]
            _push(g, seat, "look_take", opts, "Prodigy Strategist: take 1 of your top 3 (the rest go to the bottom)", n=n)
        elif cid == "C02":
            _push(g, seat, "mandate", [("m0", "Keep the Protocol at the Threat Level", 0),
                                       ("m-1", "Mandate: Protocol one below", -1), ("m+1", "Mandate: Protocol one above", 1)],
                  "Mandate")
        elif cid == "C04" and "orren" not in p.round_flags:
            p.round_flags.add("orren")
            opts = [("t0", "Leave the Threat Level", 0), ("t+1", "Raise the Threat Level by 1", 1)]
            if not corr:
                opts.append(("t-1", "Lower the Threat Level by 1", -1))
            _push(g, seat, "threat", opts, "Assessment", why="Orren's Assessment", draw_on_raise=corr)
        elif cid == "C05":
            _look_bottom_prompt(g, seat, g.other(seat) if corr else seat, 2, "Archive Keeper")
        elif cid == "C19":
            others = sum(1 for o in p.units() if o is not u and "Aptevium" in affiliations(g, o))
            gain_resolve(g, seat, min(3, others))
        if cid == "C28" and not corr and _distinct_affiliations(g, seat) >= 3:
            draw(g, seat, 1, "Parliament")
        if cid == "C33" and "absolution" not in p.round_flags:
            if corr:
                targets = g.all_units()
                if targets:
                    p.round_flags.add("absolution")
                    _queue_target(g, seat, "taint2", targets, "Heretic's Rite: place 2 Taint on")
            else:
                targets = [o for o in g.all_units() if o.corrupted]
                if targets:
                    p.round_flags.add("absolution")
                    _queue_target(g, seat, "purify", targets, "Absolution: Purify", optional=True)
        if cid == "C36":
            if corr:
                p.vapor += 2
                _queue_target(g, seat, "taint1", g.all_units(), "Blighted Bloom: place 1 Taint on")
            else:
                p.vapor += 2 if p.landmark else 1
        if u.weapon == "E08":
            place_taint(g, u, 1, seat)
        if u.gear == "E15":
            _look_bottom_prompt(g, seat, seat, 2, "Archive Lens")
        if u.gear == "E16":
            if p.vapor >= 1:
                _push(g, seat, "reactor", [("pay", "Pay 1 Vapor to keep Reactor Core Fragment", True),
                                           ("drop", "Discard it", False)], "Reactor Core Fragment", uid=u.uid)
            else:
                _r_reactor(g, {"seat": seat, "uid": u.uid}, False)
        if u.gear == "E18":
            p.vapor += 1
            gain_resolve(g, seat, 1)
    if p.landmark == "M01":
        _look_bottom_prompt(g, seat, seat, 1, "Central Cloud Archive")
    elif p.landmark == "M02":
        slots = [i for i in range(4)]
        opts = [("s-", "Keep the order", None)] + [(f"s{a}{b}", f"Swap slots {a + 1} and {b + 1}", (a, b))
                                                   for a, b in combinations(slots, 2)
                                                   if p.field[a] is not None or p.field[b] is not None]
        if len(opts) > 1:
            _push(g, seat, "swap", opts, "Rotating Floors")
    elif p.landmark == "M05":
        p.vapor += sum(len(u.equipment()) for u in p.units()) // 2
    elif p.landmark == "M07" and not any(u.corrupted for u in p.units()):
        gain_resolve(g, seat, 2)


def _step_main(g: Game) -> None:
    g.phase = "main"


def _to_clash(g: Game) -> None:
    seat = g.active
    for u in g.players[seat].units():
        if u.weapon == "E06":
            _queue_target(g, seat, "dmg1", _enemy_units(g, seat), "Rainforest Longbow: deal 1 damage to")
    if protocol(g) == 5:
        for u in g.all_units():
            if not affiliations(g, u) & {"Capital", "Coalition"}:
                deal(g, ("u", u.uid), 1, {"kind": "effect", "seat": seat})
        check_kos(g)
    then(g, "declare")


def _step_declare(g: Game) -> None:
    g.phase = "declare"
    g.attackers, g.blocks, g.block_queue = [], {}, []


def can_attack(g: Game, u: Unit) -> bool:
    if not u.ready or u.frozen or "D08" in u.afflictions or u.uid in g.attackers:
        return False
    new = u.entered_turn == g.turn
    if new:
        if landmark(g, g.other(u.owner), "M08"):
            return False
        transport = any((a := g.unit(x)) is not None and a.gear == "E17" for x in g.attackers)
        if not has_kw(g, u, "Swift") and not transport:
            return False
    if u.cid == "C35" and not u.corrupted and not any(not e.ready for e in _enemy_units(g, u.owner)):
        return False
    return True


def _compelled(g: Game, u: Unit) -> bool:
    return u.corrupted or u.cid == "C08"


def _declare(g: Game, u: Unit) -> None:
    seat = u.owner
    u.ready = False
    u.attacked = True
    g.attackers.append(u.uid)
    g.say(f"⚔️ {uname(u)} attacks.")
    if "D07" in u.afflictions:
        deal(g, ("u", u.uid), 2, {"kind": "effect", "seat": g.other(seat)})
    if u.cid == "C08":
        if u.corrupted:
            for o in g.all_units():
                if o is not u:
                    deal(g, ("u", o.uid), 3, {"kind": "effect", "seat": seat})
            change_threat(g, 1, "Lana's corrupted attack")
        else:
            for o in _enemy_units(g, seat):
                deal(g, ("u", o.uid), 3 if o.soaked else 1, {"kind": "effect", "seat": seat})
    if u.cid == "C01" and u.corrupted and g.players[seat].deck:
        top = g.players[seat].deck[-1]
        g.say(f"Harp reveals {name(top)}.")
        deal(g, ("p", g.other(seat)), CARD_BY_ID[top].cost, {"kind": "effect", "seat": seat})
    if u.cid == "C26" and u.corrupted and g.players[g.other(seat)].landmark and not controls(g, g.other(seat), "C30"):
        _r_discard_landmark(g, {}, g.other(seat))
    if u.cid == "C30" and u.corrupted:
        opts = [(f"l{p.seat}", f"Discard {name(p.landmark)}", p.seat) for p in g.players
                if p.landmark and not (p.seat != seat and controls(g, p.seat, "C30"))]
        _push(g, seat, "discard_landmark", opts, "Desecrator: discard a Landmark")
    if u.weapon == "E03":
        _queue_target(g, seat, "dmg2", _enemy_units(g, seat), "Cloud Lance: deal 2 damage to")
    if "B07" in u.boons and g.players[seat].deck:
        _look_bottom_prompt(g, seat, seat, 1, "Focused Mind")
    check_kos(g)


def _finish_declare(g: Game) -> None:
    alive = [a for a in g.attackers if g.unit(a) is not None]
    g.attackers = alive
    if not alive:
        _dusk(g)
        return
    g.block_queue = list(alive)
    g.phase = "intercept"
    _advance_intercepts(g)


def _advance_intercepts(g: Game) -> None:
    while g.block_queue and not _intercept_options(g):
        g.block_queue.pop(0)
    if not g.block_queue:
        _open_clash_reactions(g)


def _intercept_options(g: Game) -> list[Unit]:
    if not g.block_queue:
        return []
    attacker = g.unit(g.block_queue[0])
    if attacker is None:
        return []
    defender = g.players[g.other(g.active)]
    taken = set(g.blocks.values())
    out = []
    for u in defender.units():
        if not u.ready or u.frozen or u.uid in taken:
            continue
        if has_kw(g, attacker, "Skyborne") and grade(g, u) < grade(g, attacker):
            continue
        if u.cid == "C19" and attacker.cid == "C08":
            continue
        out.append(u)
    return out


def _intercept(g: Game, interceptor_uid: Optional[int]) -> None:
    attacker_uid = g.block_queue.pop(0)
    if interceptor_uid is not None:
        g.blocks[attacker_uid] = interceptor_uid
        i = g.unit(interceptor_uid)
        g.say(f"🛡️ {uname(i)} intercepts {uname(g.unit(attacker_uid))}.")
        if "D07" in i.afflictions:
            deal(g, ("u", i.uid), 2, {"kind": "effect", "seat": g.active})
            check_kos(g)
    _advance_intercepts(g)


def _open_clash_reactions(g: Game) -> None:
    then(g, "react_defender", "react_active", "clash_damage")
    g.phase = "clash"


def _step_react(seat_of: Callable[[Game], int]):
    def step(g: Game) -> None:
        seat = seat_of(g)
        opts = _reaction_opts(g, seat, window="clash")
        if len(opts) > 1:
            g.pending.append({"kind": "respond", "seat": seat, "opts": opts, "window": "clash",
                              "text": "Clash: play a Reaction or pass"})
    return step


def _step_clash_damage(g: Game) -> None:
    defender = g.other(g.active)
    for aid in list(g.attackers):
        a = g.unit(aid)
        if a is None or g.over:
            continue
        iid = g.blocks.get(aid)
        if iid is None:
            deal(g, ("p", defender), power(g, a, role="attack"), {"kind": "combat", "seat": g.active, "unit": aid})
            continue
        i = g.unit(iid)
        if i is None:
            continue                          # interp: an attacker whose interceptor left stays intercepted
        pa, pi = power(g, a, i, "attack"), power(g, i, a, "intercept")
        hp = remaining(g, i)
        fa, fi = has_kw(g, a, "First Strike", "attack"), has_kw(g, i, "First Strike", "intercept")
        src_a = {"kind": "combat", "seat": g.active, "unit": aid}
        src_i = {"kind": "combat", "seat": defender, "unit": iid}
        if fa and not fi:
            deal(g, ("u", iid), pa, src_a)
            if g.unit(iid) and i.damage < stamina(g, i):
                deal(g, ("u", aid), pi, src_i)
        elif fi and not fa:
            deal(g, ("u", aid), pi, src_i)
            if g.unit(aid) and a.damage < stamina(g, a):
                deal(g, ("u", iid), pa, src_a)
        else:
            deal(g, ("u", iid), pa, src_a)
            deal(g, ("u", aid), pi, src_i)
        if has_kw(g, a, "Piercing") and pa > hp > 0:
            deal(g, ("p", defender), pa - hp, {"kind": "combat", "seat": g.active})
        check_kos(g)
    g.attackers, g.blocks = [], {}
    check_kos(g)
    if not g.over:
        _dusk(g)


def _dusk(g: Game) -> None:
    seat = g.active
    p = g.players[seat]
    g.phase = "dusk"
    level = protocol(g)
    for i, u in enumerate(p.field):
        if u is not None and u.corrupted and "D08" not in u.afflictions:
            for j in (i - 1, i + 1):
                if 0 <= j < 4 and p.field[j] is not None:
                    place_taint(g, p.field[j], 1, seat)
    for u in p.units():
        if "B04" in u.boons:
            place_taint(g, u, 1, seat)
    if controls(g, seat, "C25", corrupted=True):
        for u in g.all_units():
            if u.frozen:
                deal(g, ("u", u.uid), 1, {"kind": "effect", "seat": seat})
    if g.sky == "S3":
        for u in g.all_units():
            if u.soaked:
                deal(g, ("u", u.uid), 1, {"kind": "effect", "seat": seat})
    if level == 4:
        gain_resolve(g, seat, 2)
    if p.landmark == "M06":
        for u in p.units():
            cleanse(g, u, 1)
    check_kos(g)
    for u in list(p.units()):
        if u.cid == "C03" and not u.corrupted:
            _queue_target(g, seat, "cleanse1", [o for o in p.units() if o.taint], "Ava: Cleanse 1 from")
        if u.cid == "C10" and not u.corrupted and u.ready:
            _push(g, seat, "overwork", [("yes", "Spend Yurie to draw 1", True), ("no", "No", False)], "Overwork",
                  uid=u.uid)
        if u.cid == "C07" and "D08" not in u.afflictions:
            targets = [o for o in _enemy_units(g, seat) if not o.corrupted and grade(g, o) <= 3]
            _queue_target(g, seat, "taint1", targets, "Patient Zero: place 1 Taint on")
        if u.cid == "C24":
            opts = [(f"e{o.uid}{slot[0]}", f"Discard {name(getattr(o, slot))} from {uname(o)}", (o.uid, slot))
                    for o in _enemy_units(g, seat) for slot in ("weapon", "gear")
                    if getattr(o, slot) and getattr(o, slot) != "E02"]
            _push(g, seat, "discard_equipment", opts, "Cave-In")
        if u.weapon == "E07":
            _queue_target(g, seat, "cleanse1", [o for o in g.all_units() if o.taint], "Crosier of Honor: Cleanse 1 from")
    then(g, "dusk2")


def _step_dusk2(g: Game) -> None:
    seat = g.active
    p = g.players[seat]
    if p.landmark == "M09" and g.threat == 8 and sum(1 for u in p.units() if u.corrupted) >= 3:
        _finish(g, seat, f"{p.name} fulfilled the Prophecy of the Great Corruption")
        return
    for u in g.all_units():
        if not u.corrupted:
            u.damage = 0
        u.tmp_power = u.tmp_stamina = 0
        u.tmp_swift = False
        u.disrupt = 0
    check_kos(g)
    if len(p.hand) > HAND_LIMIT:
        _discard_prompt(g, seat)
    then(g, "end_turn")


def _step_end_turn(g: Game) -> None:
    seat = g.active
    if seat != g.first:
        _round_end(g)
        if g.over:
            return
    _start_turn(g, g.other(seat))


def _round_end(g: Game) -> None:
    level = protocol(g)
    if g.round >= 3 and level != 2 and g.sky != "S1":
        change_threat(g, 1, "the sky darkens")
    for u in g.all_units():
        u.soaked = False
        if "D01" in u.afflictions:
            place_taint(g, u, u.afflictions.count("D01"), g.other(u.owner))
    if g.sky == "S4":
        for u in g.all_units():
            if u.taint > 0:
                place_taint(g, u, 1, g.sky_owner if g.sky_owner is not None else u.owner)
        change_threat(g, 1, "Black Tide")
    if level == 8:
        for u in g.all_units():
            place_taint(g, u, 1, u.owner)
        for p in g.players:
            p.resolve -= 2
        check_kos(g)
    for p in g.players:
        p.round_flags.clear()
    for u in g.all_units():
        u.no_taint_round = False
    g.round += 1
    if g.round > MAX_ROUNDS and not g.over:
        r0, r1 = g.players[0].resolve, g.players[1].resolve
        _finish(g, -1 if r0 == r1 else (0 if r0 > r1 else 1), "the match reached the round limit")


STEPS: dict[str, Callable[[Game], None]] = {
    "begin": _step_begin, "main": _step_main, "declare": _step_declare,
    "react_defender": _step_react(lambda g: g.other(g.active)), "react_active": _step_react(lambda g: g.active),
    "clash_damage": _step_clash_damage, "dusk2": _step_dusk2, "end_turn": _step_end_turn,
    "resolve_stack": lambda g: _resolve_stack(g),
}


def _run(g: Game) -> None:
    while not g.over and not g.pending and g.resume:
        STEPS[g.resume.pop()](g)


# =============================================================================================== card plays
def _single_targets(g: Game, seat: int, ignore_guard: bool = False, enemy_only: bool = False,
                    own_only: bool = False) -> list[Unit]:
    own = [] if enemy_only else g.players[seat].units()
    if own_only:
        return own
    enemies = [u for u in _enemy_units(g, seat) if not u.fog]
    if not ignore_guard:
        guards = [u for u in enemies if u.ready and has_kw(g, u, "Guard")]
        if guards:
            enemies = guards
    return own + enemies


def _combos(units: list[Unit], k: int) -> list[tuple]:
    out = []
    for r in range(1, k + 1):
        out.extend(combinations(units, r))
    return out


def _play_options(g: Game, seat: int, cid: str) -> list[tuple]:
    """[(key_suffix, label_suffix, payload)] for playing `cid`, or [] if it cannot be played now."""
    card = CARD_BY_ID[cid]
    p = g.players[seat]
    level = protocol(g)
    if not meets_grade(g, seat, card):
        return []
    if card.is_command and not can_command(g, seat):
        return []
    if card.kind in (LANDMARK, SKY) and level == 6:
        return []
    out: list[tuple] = []

    def add(suffix: str, label: str, targets: list = (), extra=None, target_unit: Optional[Unit] = None):
        if cost(g, seat, cid, target_unit) <= p.vapor:
            out.append((suffix, label, {"cid": cid, "targets": list(targets), "extra": extra}))

    k = card.kind
    if k == CHARACTER:
        for slot in range(4):
            occupant = p.field[slot]
            add(f"s{slot}", f"into slot {slot + 1}" + (f" (replacing {uname(occupant)})" if occupant else ""),
                extra=slot)
        return out
    if k == ACTION:
        if card.reaction and cid == "A14":
            return []
        if cid in ("A01", "A04", "A05", "A07"):
            for u in _single_targets(g, seat):
                add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
        elif cid == "A02":
            for u in _single_targets(g, seat, ignore_guard=True):
                add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
        elif cid in ("A03", "A06"):
            pool = [u for u in _enemy_units(g, seat) if not u.fog]
            for combo in _combos(pool, 3 if cid == "A03" else 2):
                add("u" + "-".join(str(u.uid) for u in combo), "on " + ", ".join(uname(u) for u in combo),
                    [("u", u.uid) for u in combo])
        elif cid == "A11":
            for u in _single_targets(g, seat):
                add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
        elif cid == "A21":
            for d in (-2, -1, 1, 2):
                add(f"t{d:+d}", f"Threat Level {d:+d}", extra=d)
        elif cid in ("A08", "A10", "A12", "A22", "A23"):
            add("", "")
        elif cid == "A09":
            spendable = [u for u in p.units() if u.ready]
            enemies = [u for u in _enemy_units(g, seat) if not u.fog]
            for s in spendable:
                add(f"x{s.uid}p", f"spending {uname(s)}: 7 to Resolve", [("p", g.other(seat))], extra=(s.uid, "resolve"))
                for e in enemies:
                    add(f"x{s.uid}u{e.uid}", f"spending {uname(s)}: 7 to {uname(e)}", [("u", e.uid)], extra=(s.uid, "unit"))
                if len(enemies) > 1:
                    add(f"x{s.uid}w", f"spending {uname(s)}: sweep (KO what it can, rest to Resolve)",
                        [("u", e.uid) for e in enemies] + [("p", g.other(seat))], extra=(s.uid, "sweep"))
        elif cid == "A13":
            for u in p.units():
                add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
            add("me", "on yourself", [("p", seat)])
        elif cid in ("A15", "A16"):
            for u in p.units():
                add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
        elif cid == "A17":
            for u in g.all_units():
                if not u.corrupted and (u.owner == seat or not u.fog):
                    add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
        elif cid == "A18":
            for u in g.all_units():
                if (u.corrupted or u.taint) and (u.owner == seat or not u.fog):
                    add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
        elif cid == "A19":
            pool = [u for u in p.units() if u.ready and command_target_ok(g, u, seat)]
            for combo in _combos(pool, 2):
                add("u" + "-".join(str(u.uid) for u in combo), "on " + ", ".join(uname(u) for u in combo),
                    [("u", u.uid) for u in combo])
        elif cid == "A20":
            for u in p.units():
                if command_target_ok(g, u, seat):
                    add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
        elif cid == "A24":
            if "reach" not in p.turn_flags:
                for u in p.units():
                    add(f"u{u.uid}", f"tainting {uname(u)}", [("u", u.uid)])
        return out
    if k == EQUIPMENT:
        slot = "weapon" if card.slot == "Weapon" else "gear"
        for u in p.units():
            if getattr(u, slot) is None:
                add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)], target_unit=u)
        return out
    if k == BOON:
        for u in p.units():
            if len(u.boons) >= 2:
                continue
            if cid == "B02" and not command_target_ok(g, u, seat):
                continue
            if cid == "B09":
                if estranged(u) or u.sworn is not None:
                    continue
                for o in p.units():
                    if o is not u and not estranged(o) and o.sworn is None and u.uid < o.uid:
                        add(f"u{u.uid}-{o.uid}", f"on {uname(u)} + {uname(o)}", [("u", u.uid), ("u", o.uid)])
                continue
            add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)], target_unit=u)
        return out
    if k == AFFLICTION:
        pool = g.all_units() if cid == "D08" else _enemy_units(g, seat)
        for u in pool:
            if len(u.afflictions) >= 2 or u.cid == "C11":
                continue
            if cid == "D01" and grade(g, u) > 4:
                continue
            add(f"u{u.uid}", f"on {uname(u)}", [("u", u.uid)])
        return out
    if k == LANDMARK:
        add("", "")
        return out
    if k == SKY:
        add("", "")
        return out
    if k == EVENT:
        if cid == "M10":
            brannic = controls(g, seat, "C06")
            need = 0 if (brannic and brannic.corrupted) else (4 if brannic else 6)
            if g.threat < need:
                return []
            add("", "")
        elif cid == "M11":
            add("", "")
        elif cid == "M12":
            pool = p.units()
            for combo in _combos(pool, 3):
                affs = [affiliations(g, u) for u in combo]
                if all(not (affs[i] & affs[j]) for i in range(len(affs)) for j in range(i + 1, len(affs))):
                    add("u" + "-".join(str(u.uid) for u in combo), "on " + ", ".join(uname(u) for u in combo),
                        [("u", u.uid) for u in combo])
        return out
    return out


def _pay_and_mark(g: Game, seat: int, card, target: Optional[Unit] = None) -> None:
    p = g.players[seat]
    p.vapor -= cost(g, seat, card.id, target)
    if card.kind == ACTION and card.power == PRECIPITATION and controls(g, seat, "C22") and controls(g, seat, "C25"):
        p.turn_flags.add("precip_discount")
    if card.is_command:
        if any(u.gear == "E14" for u in p.units()):
            p.turn_flags.add("relay")
        p.turn_flags.add("command")
        if controls(g, seat, "C10", corrupted=False) and "comms" not in p.turn_flags:
            p.turn_flags.add("comms")
            draw(g, seat, 1, "Comms Network")
        for u in p.units():
            if u.cid == "C12":
                u.tmp_power += 1
        if p.landmark == "M03":
            for u in p.units():
                if "Jahnen" in affiliations(g, u):
                    u.tmp_power += 1
    if card.kind == ACTION and card.power:
        p.turn_flags.add(f"power:{card.power}")


def _place_character(g: Game, seat: int, cid: str, slot: int) -> None:
    p = g.players[seat]
    if p.field[slot] is not None:
        leave_play(g, p.field[slot])
    u = Unit(g.next_uid, cid, seat, g.turn)
    g.next_uid += 1
    p.field[slot] = u
    g.say(f"🌥️ {p.name} plays {name(cid)}.")
    if u.corrupted:
        change_threat(g, 1, f"{uname(u)} is Fallen")
    if cid == "C14":
        for o in p.units():
            if o is not u and "Jahnen" in affiliations(g, o):
                o.tmp_power += 1
    elif cid == "C17":
        cards = sorted({c for c in p.deck if CARD_BY_ID[c].kind == EQUIPMENT and CARD_BY_ID[c].cost <= 2})
        _push(g, seat, "take_card", [(f"c{c}", f"Take {name(c)}", c) for c in cards], "Rigger: take an Equipment",
              optional=True)
    elif cid == "C26":
        cards = sorted({c for c in p.deck if CARD_BY_ID[c].kind == LANDMARK})
        _push(g, seat, "take_card", [(f"c{c}", f"Take {name(c)}", c) for c in cards], "Mountain Guide: take a Landmark",
              optional=True)
    elif cid == "C34":
        gain_resolve(g, seat, 2)


def _attach_equipment(g: Game, u: Unit, cid: str) -> None:
    card = CARD_BY_ID[cid]
    setattr(u, "weapon" if card.slot == "Weapon" else "gear", cid)
    p = g.players[u.owner]
    if controls(g, u.owner, "C16") and "funding" not in p.turn_flags:
        p.turn_flags.add("funding")
        draw(g, u.owner, 1, "Funding")


def _play_card(g: Game, seat: int, payload: dict) -> None:
    cid = payload["cid"]
    card = CARD_BY_ID[cid]
    p = g.players[seat]
    targets = payload["targets"]
    target_unit = g.unit(targets[0][1]) if targets and targets[0][0] == "u" else None
    _pay_and_mark(g, seat, card, target_unit)
    p.hand.remove(cid)
    p.actions_this_turn += 1
    k = card.kind
    if k == CHARACTER:
        _place_character(g, seat, cid, payload["extra"])
    elif k == EQUIPMENT:
        g.say(f"🔧 {p.name} equips {name(cid)} on {uname(target_unit)}.")
        _attach_equipment(g, target_unit, cid)
    elif k == BOON:
        target_unit.boons.append(cid)
        g.say(f"💠 {p.name} attaches {name(cid)} to {uname(target_unit)}.")
        if cid == "B05" and target_unit.corrupted:
            purify(g, target_unit)
        if cid == "B09":
            partner = g.unit(targets[1][1])
            target_unit.sworn, partner.sworn = partner.uid, target_unit.uid
    elif k == AFFLICTION:
        was_frozen = target_unit.frozen
        target_unit.afflictions.append(cid)
        g.say(f"🩸 {p.name} afflicts {uname(target_unit)} with {name(cid)}.")
        if cid == "D06" and was_frozen:
            deal(g, ("u", target_unit.uid), 2, {"kind": "effect", "seat": seat})
    elif k == LANDMARK:
        if p.landmark:
            p.rainfall.append(p.landmark)
        p.landmark = cid
        g.say(f"🏛️ {p.name} raises {name(cid)}.")
        if controls(g, seat, "C05", corrupted=False):
            draw(g, seat, 1, "Archive Keeper")
        if controls(g, seat, "C29", corrupted=False):
            gain_resolve(g, seat, 2)
    elif k == SKY:
        if g.sky and g.sky_owner is not None:
            g.players[g.sky_owner].rainfall.append(g.sky)
        g.sky, g.sky_owner = cid, seat
        g.say(f"🌦️ The Sky becomes {name(cid)}.")
    elif k == EVENT:
        g.say(f"📜 {p.name} plays {name(cid)}.")
        _resolve_event(g, seat, cid, targets)
        p.rainfall.append(cid)
        if controls(g, seat, "C29", corrupted=False):
            gain_resolve(g, seat, 2)
    elif k == ACTION:
        g.say(f"✨ {p.name} plays {name(cid)}" + (" " + payload.get("label", "") if payload.get("label") else "") + ".")
        stack = {"cid": cid, "seat": seat, "targets": list(targets), "extra": payload.get("extra"),
                 "power": card.power, "negated": False, "reflect": None, "may": None, "selfless": None}
        enemy = g.other(seat)
        ava = controls(g, enemy, "C03", corrupted=True)
        if ava is not None:
            stack["selfless"] = (None, ava.uid)                     # forced Selfless
        g.stack = stack
        if card.power not in (COMMAND, None) or targets:
            opts = _reaction_opts(g, enemy, window="stack")
            if len(opts) > 1:
                g.pending.append({"kind": "respond", "seat": enemy, "opts": opts, "window": "stack",
                                  "text": f"{p.name} plays {name(cid)}: respond or pass"})
                then(g, "resolve_stack")
                return
        _resolve_stack(g)
    check_kos(g)


def _resolve_event(g: Game, seat: int, cid: str, targets: list) -> None:
    enemy = g.other(seat)
    if cid == "M10":
        for u in _enemy_units(g, seat):
            deal(g, ("u", u.uid), 5, {"kind": "effect", "seat": seat})
        deal(g, ("p", enemy), 5, {"kind": "effect", "seat": seat})
        deal(g, ("p", seat), 5, {"kind": "effect", "seat": seat})
        if controls(g, seat, "C06", corrupted=True):
            deal(g, ("p", seat), 3, {"kind": "effect", "seat": seat})
    elif cid == "M11":
        gain_resolve(g, seat, 3)
        gain_resolve(g, enemy, 3)
        draw(g, seat, 2)
    elif cid == "M12":
        for _, uid in targets:
            u = g.unit(uid)
            if u is not None:
                u.tmp_power += 2; u.tmp_stamina += 2; u.tmp_swift = True
        draw(g, seat, 1)


# =============================================================================================== actions and reactions
def _action_bonus(g: Game, seat: int, power_tag: str) -> int:
    bonus = 0
    if power_tag in (PRECIPITATION, LIGHTNING) and g.sky == "S3":
        bonus += 1
    if power_tag == LIGHTNING:
        if controls(g, seat, "C08"):
            bonus += 1
        bonus += sum(1 for u in g.players[seat].units() if "B03" in u.boons and u.ready)
    if power_tag == PRECIPITATION and controls(g, seat, "C22", corrupted=True):
        bonus += 2
    return bonus


def _resolve_stack(g: Game) -> None:
    st = g.stack
    g.stack = None
    if st is None:
        return
    seat, cid = st["seat"], st["cid"]
    p = g.players[seat]
    card = CARD_BY_ID[cid]
    if not st["negated"]:
        live = []
        for ref in st["targets"]:
            if ref[0] == "u":
                u = g.unit(ref[1])
                if u is None or (u.fog and u.owner != seat):
                    continue
            live.append(ref)
        st["targets"] = live
        ACTIONS[cid](g, st, _action_bonus(g, seat, card.power))
    else:
        g.say(f"🪞 {name(cid)} is negated.")
    p.rainfall.append(cid)
    check_kos(g)
    if card.power == PRECIPITATION and not g.over:
        enemies = _enemy_units(g, seat)
        if controls(g, seat, "C22"):
            _queue_target(g, seat, "soak", enemies, "Valleyflow: place Soaked on")
        if controls(g, seat, "C25"):
            _queue_target(g, seat, "freeze", [u for u in enemies if u.soaked], "Peakfrost: place Frozen on")
        if any(u.weapon == "E05" for u in p.units()) and "frostbinder" not in p.turn_flags:
            p.turn_flags.add("frostbinder")
            opts = [(f"{w}{u.uid}", f"{'Soak' if w == 's' else 'Freeze'} {uname(u)}", (u.uid, "soak" if w == "s" else "freeze"))
                    for u in enemies for w in ("s", "f")]
            _push(g, seat, "frostbinder", opts, "Frostbinder Staff")


def _src(st: dict, power_tag=None) -> dict:
    return {"kind": "action", "seat": st["seat"], "power": power_tag or CARD_BY_ID[st["cid"]].power, "stack": st}


def _units_of(g: Game, st: dict) -> list[Unit]:
    return [u for ref in st["targets"] if ref[0] == "u" and (u := g.unit(ref[1])) is not None]


def _chain(g: Game, st: dict, tag: str) -> bool:
    """True if an Action of `tag` was played earlier this turn (the current card's own mark doesn't count)."""
    return f"chain:{tag}" in g.players[st["seat"]].turn_flags


def _a_damage(n: int, chain_tag: Optional[str] = None, chain_bonus: int = 0, soak: bool = False):
    def run(g: Game, st: dict, bonus: int) -> None:
        amount = n + bonus + (chain_bonus if chain_tag and _chain(g, st, chain_tag) else 0)
        for u in _units_of(g, st):
            deal(g, ("u", u.uid), amount, _src(st))
            if soak and g.unit(u.uid):
                u.soaked = True
    return run


def _a_beam_barrage(g, st, bonus):
    amount = (3 if _chain(g, st, COMPRESSED) else 2) + bonus
    for u in _units_of(g, st):
        deal(g, ("u", u.uid), amount, _src(st))


def _a_cold_front(g, st, bonus):
    for u in _units_of(g, st):
        was = u.soaked
        freeze(g, u)
        if was:
            u.soaked = False
            deal(g, ("u", u.uid), 3 + bonus, _src(st))


def _a_blizzard(g, st, bonus):
    for u in _units_of(g, st):
        freeze(g, u)
        if u.soaked:
            deal(g, ("u", u.uid), 2 + bonus, _src(st))


def _a_thunderclap(g, st, bonus):
    for u in _units_of(g, st):
        owner = g.players[u.owner]
        slot = owner.field.index(u)
        neighbours = [owner.field[j] for j in (slot - 1, slot + 1) if 0 <= j < 4 and owner.field[j] is not None]
        deal(g, ("u", u.uid), 4 + bonus, _src(st))
        if _chain(g, st, PRECIPITATION):
            for o in neighbours:
                deal(g, ("u", o.uid), 2 + bonus, _src(st))


def _a_chain_bolt(g, st, bonus):
    for u in _enemy_units(g, st["seat"]):
        deal(g, ("u", u.uid), 2 + bonus, _src(st))


def _a_voltage(g, st, bonus):
    spend_uid, mode = st["extra"]
    spender = g.unit(spend_uid)
    if spender is not None:
        spender.ready = False
    total = 7 + bonus
    enemy = g.other(st["seat"])
    if mode == "resolve":
        deal(g, ("p", enemy), total, _src(st))
    elif mode == "unit":
        for u in _units_of(g, st):
            deal(g, ("u", u.uid), total, _src(st))
    else:
        for u in sorted(_units_of(g, st), key=lambda x: remaining(g, x)):
            need = remaining(g, u) - (2 if u.soaked else 0)
            if 0 < need <= total:
                deal(g, ("u", u.uid), need, _src(st))
                total -= need
        if total > 0:
            deal(g, ("p", enemy), total, _src(st))


def _a_tsunami(g, st, bonus):
    seat = st["seat"]
    for u in _enemy_units(g, seat):
        deal(g, ("u", u.uid), 5 + bonus, _src(st))
        if g.unit(u.uid):
            u.soaked = True
    enemy = g.players[g.other(seat)]
    if enemy.landmark and not controls(g, enemy.seat, "C30"):
        _r_discard_landmark(g, {}, enemy.seat)


def _a_tempest(g, st, bonus):
    extra = 2 if g.sky == "S3" else 0
    targets = _units_of(g, st)
    chosen = targets[0] if targets else None
    if chosen is not None:
        deal(g, ("u", chosen.uid), 6 + extra + bonus, _src(st))
    for u in _enemy_units(g, st["seat"]):
        if chosen is None or u.uid != chosen.uid:
            deal(g, ("u", u.uid), 3 + extra + bonus, _src(st))


def _a_clear_skies(g, st, bonus):
    if g.sky and g.sky_owner is not None:
        g.players[g.sky_owner].rainfall.append(g.sky)
    g.sky = g.sky_owner = None
    for u in g.all_units():
        u.taint = 0
    change_threat(g, -2, "Clear the Skies")
    draw(g, st["seat"], 1)


def _a_cloud_shield(g, st, bonus):
    for ref in st["targets"]:
        holder = g.players[ref[1]] if ref[0] == "p" else g.unit(ref[1])
        if holder is not None:
            holder.shield += 3


def _a_fog(g, st, bonus):
    for u in _units_of(g, st):
        u.fog = True
    draw(g, st["seat"], 1)


def _a_retreat(g, st, bonus):
    for u in _units_of(g, st):
        leave_play(g, u, to_hand=True)


def _a_mend(g, st, bonus):
    for u in _units_of(g, st):
        if not u.corrupted:
            u.damage = 0
    gain_resolve(g, st["seat"], 3)


def _a_purge(g, st, bonus):
    for u in _units_of(g, st):
        purify(g, u)


def _a_rally(g, st, bonus):
    for u in _units_of(g, st):
        u.tmp_power += 2


def _a_tactical(g, st, bonus):
    for u in _units_of(g, st):
        u.tmp_swift = True
        u.tmp_power += 1
    draw(g, st["seat"], 1)


def _a_executive(g, st, bonus):
    change_threat(g, st["extra"], "Executive Order")
    draw(g, st["seat"], 1)


def _a_query(g, st, bonus):
    seat = st["seat"]
    deck = g.players[seat].deck
    top = [deck[-1 - i] for i in range(min(4, len(deck)))]
    opts = [(f"c{c}", f"Take {name(c)}", c) for c in dict.fromkeys(top) if CARD_BY_ID[c].kind != CHARACTER]
    if opts:
        _push(g, seat, "take_card", opts, "Archive Query: take a non-Character card", from_top=True, top=top)
    else:
        _r_take_card(g, {"seat": seat, "from_top": True, "top": top}, None)


def _a_delivery(g, st, bonus):
    seat = st["seat"]
    p = g.players[seat]
    opts = []
    for c in sorted({c for c in p.deck if CARD_BY_ID[c].kind == EQUIPMENT and CARD_BY_ID[c].cost <= 3}):
        slot = "weapon" if CARD_BY_ID[c].slot == "Weapon" else "gear"
        for u in p.units():
            if getattr(u, slot) is None:
                opts.append((f"c{c}u{u.uid}", f"Attach {name(c)} to {uname(u)}", (c, u.uid)))
    if opts:
        _push(g, seat, "delivery", opts, "Hovercraft Delivery")
    else:
        g.rng.shuffle(p.deck)


def _a_reach(g, st, bonus):
    p = g.players[st["seat"]]
    p.turn_flags.add("reach")
    p.vapor += 3
    for u in _units_of(g, st):
        place_taint(g, u, 1, st["seat"])


ACTIONS: dict[str, Callable] = {
    "A01": _a_damage(3, COMPRESSED, 1), "A02": _a_damage(4), "A03": _a_beam_barrage, "A04": _a_damage(2, soak=True),
    "A05": _a_cold_front, "A06": _a_blizzard, "A07": _a_thunderclap, "A08": _a_chain_bolt, "A09": _a_voltage,
    "A10": _a_tsunami, "A11": _a_tempest, "A12": _a_clear_skies, "A13": _a_cloud_shield, "A15": _a_fog,
    "A16": _a_retreat, "A17": _a_mend, "A18": _a_purge, "A19": _a_rally, "A20": _a_tactical, "A21": _a_executive,
    "A22": _a_query, "A23": _a_delivery, "A24": _a_reach,
}


def _reaction_opts(g: Game, seat: int, window: str) -> list[tuple]:
    """Responses available to `seat`: Reaction cards it can pay for, plus Dark Mirror / Selfless in a stack window."""
    opts: list[tuple] = [("pass", "Pass", None)]
    if g.level == 1:
        return opts
    p = g.players[seat]
    st = g.stack if window == "stack" else None
    hand = set(p.hand)
    targeted_units = {ref[1] for ref in (st["targets"] if st else []) if ref[0] == "u"}
    targeted_me = st is not None and (("p", seat) in st["targets"]
                                      or any((u := g.unit(t)) is not None and u.owner == seat for t in targeted_units))
    projectile = st is not None and st["power"] in CLOUD_PROJECTILES and not CARD_BY_ID[st["cid"]].tech

    def ok(cid):
        return cid in hand and cost(g, seat, cid) <= p.vapor and meets_grade(g, seat, CARD_BY_ID[cid])

    if ok("A13"):
        for u in p.units():
            opts.append((f"A13u{u.uid}", f"Cloud Shield on {uname(u)}", ("A13", ("u", u.uid))))
        opts.append(("A13me", "Cloud Shield on yourself", ("A13", ("p", seat))))
    if st is not None and ok("A14") and projectile and targeted_me and not st["negated"]:
        opts.append(("A14", f"Mirror Cloud: negate {name(st['cid'])}", ("A14", None)))
    if ok("A15"):
        for u in p.units():
            if st is None or u.uid in targeted_units:
                opts.append((f"A15u{u.uid}", f"Fog Veil on {uname(u)}", ("A15", ("u", u.uid))))
    if ok("A16"):
        for u in p.units():
            opts.append((f"A16u{u.uid}", f"Aerial Retreat: return {uname(u)}", ("A16", ("u", u.uid))))
    if ok("A18"):
        for u in g.all_units():
            if u.corrupted or u.taint:
                opts.append((f"A18u{u.uid}", f"Emergency Purge on {uname(u)}", ("A18", ("u", u.uid))))
    if st is not None and projectile and st["reflect"] is None:
        may = controls(g, seat, "C09", corrupted=True)
        if may is not None and (may.uid in targeted_units or ("p", seat) in st["targets"] or st["cid"] in SWEEPERS):
            caster = st["seat"]
            for u in g.players[caster].units():
                opts.append((f"dm{u.uid}", f"Dark Mirror: reflect onto {uname(u)}", ("DM", ("u", u.uid), may.uid)))
            opts.append(("dmp", "Dark Mirror: reflect onto their Resolve", ("DM", ("p", caster), may.uid)))
    if st is not None and st["selfless"] is None:
        ava = controls(g, seat, "C03", corrupted=False)
        if ava is not None and CARD_BY_ID[st["cid"]].damage:
            for uid in targeted_units:
                u = g.unit(uid)
                if u is not None and u.owner == seat and u is not ava:
                    opts.append((f"sf{uid}", f"Selfless: Ava takes the damage for {uname(u)}", ("SF", uid, ava.uid)))
    return opts


def _apply_response(g: Game, seat: int, payload, window: str) -> None:
    if payload is None:
        return                                 # pass: the window closes for this player
    p = g.players[seat]
    kind = payload[0]
    st = g.stack
    if kind == "DM":
        st["reflect"], st["may"] = payload[1], payload[2]
    elif kind == "SF":
        st["selfless"] = (payload[1], payload[2])
    else:
        cid, ref = payload
        card = CARD_BY_ID[cid]
        p.vapor -= cost(g, seat, cid)
        p.hand.remove(cid)
        p.turn_flags.add(f"power:{card.power}")
        p.turn_flags.add(f"chain:{card.power}")
        g.say(f"⚡ {p.name} responds with {name(cid)}.")
        if cid == "A14":
            st["negated"] = True
            dmg = math.ceil(CARD_BY_ID[st["cid"]].damage / 2)
            deal(g, ("p", st["seat"]), dmg, {"kind": "effect", "seat": seat})
        else:
            sub = {"cid": cid, "seat": seat, "targets": [ref], "extra": None, "power": card.power, "negated": False,
                   "reflect": None, "may": None, "selfless": None}
            ACTIONS[cid](g, sub, 0)
        p.rainfall.append(cid)
        check_kos(g)
    if g.over:
        return
    opts = _reaction_opts(g, seat, window)
    if len(opts) > 1:
        g.pending.insert(0, {"kind": "respond", "seat": seat, "opts": opts, "window": window,
                             "text": "Respond again or pass"})


# =============================================================================================== abilities
def _ability_opts(g: Game, seat: int) -> list[Act]:
    p = g.players[seat]
    out: list[Act] = []
    f = p.turn_flags
    for u in p.units():
        cid, uid = u.cid, u.uid
        if cid == "C01" and not u.corrupted and not is_mute(g, u) and f"harp{uid}" not in f:
            for o in p.units():
                if o is not u and o.ready:
                    out.append(Act(f"ab:harp:{uid}:{o.uid}", f"Harp's Command: {uname(o)} +2 Power", ("harp", uid, o.uid)))
        elif cid == "C14" and not u.corrupted and not is_mute(g, u) and u.ready:
            for o in p.units():
                if "Jahnen" in affiliations(g, o) and not has_kw(g, o, "Swift"):
                    out.append(Act(f"ab:rhea:{uid}:{o.uid}", f"Rhea's Command: Spend Rhea, {uname(o)} gains Swift",
                                   ("rhea", uid, o.uid)))
        elif cid == "C13" and f"kessa{uid}" not in f and p.vapor >= 1:
            for o in _single_targets(g, seat, ignore_guard=u.corrupted):
                out.append(Act(f"ab:kessa:{uid}:{o.uid}", f"Kessa's Curved Shot at {uname(o)}", ("kessa", uid, o.uid)))
        elif cid == "C15":
            price = 0 if u.corrupted else 2
            if p.vapor >= price and not (u.corrupted and f"claude{uid}" in f):
                for o in g.all_units():
                    out.append(Act(f"ab:claude:{uid}:{o.uid}", f"Energy Disruptor: Disrupt 2 on {uname(o)}",
                                   ("claude", uid, o.uid)))
        elif cid == "C17" and u.corrupted and u.ready:
            for o in _enemy_units(g, seat):
                for slot in ("weapon", "gear"):
                    if getattr(o, slot) and getattr(o, slot) != "E02":
                        out.append(Act(f"ab:bexley:{uid}:{o.uid}{slot[0]}",
                                       f"Bexley: discard {name(getattr(o, slot))} from {uname(o)}", ("bexley", uid, (o.uid, slot))))
        elif cid == "C27" and f"odalys{uid}" not in f and p.vapor >= 1:
            for o in g.all_units():
                if o.soaked:
                    out.append(Act(f"ab:odalys:{uid}:{o.uid}", f"Frostwarden on {uname(o)}", ("odalys", uid, o.uid)))
        elif cid == "C34" and f"litany{uid}" not in f and p.vapor >= 1:
            for o in g.all_units():
                if o.taint:
                    out.append(Act(f"ab:litany:{uid}:{o.uid}", f"Litany: Cleanse 2 from {uname(o)}", ("litany", uid, o.uid)))
        if u.gear == "E09" and f"disruptor{uid}" not in f:
            for a in g.all_units():
                if a.taint:
                    for b in g.all_units():
                        if b is not a:
                            out.append(Act(f"ab:move:{uid}:{a.uid}>{b.uid}", f"Energy Disruptor: move 1 Taint {uname(a)} → {uname(b)}",
                                           ("move", uid, (a.uid, b.uid))))
        if "B07" in u.boons and f"focus{uid}" not in f and p.vapor >= 1:
            out.append(Act(f"ab:focus:{uid}", f"Focused Mind ({uname(u)}): pay 1, draw 1", ("focus", uid, None)))
    return out


def _use_ability(g: Game, seat: int, data) -> None:
    kind, uid, target = data
    p = g.players[seat]
    u = g.unit(uid)
    p.actions_this_turn += 1
    if kind == "harp":
        p.turn_flags.add(f"harp{uid}")
        o = g.unit(target)
        o.tmp_power += 2
    elif kind == "rhea":
        u.ready = False
        g.unit(target).tmp_swift = True
    elif kind == "kessa":
        p.turn_flags.add(f"kessa{uid}")
        p.vapor -= 1
        o = g.unit(target)
        dmg = 3 if f"power:{COMPRESSED}" in p.turn_flags else 2
        deal(g, ("u", target), dmg, {"kind": "effect", "seat": seat, "unit": uid})
        if u.corrupted and g.unit(target):
            place_taint(g, o, 1, seat)
    elif kind == "claude":
        if u.corrupted:
            p.turn_flags.add(f"claude{uid}")
            place_taint(g, g.unit(target), 1, seat)
        else:
            p.vapor -= 2
        if g.unit(target):
            disrupt(g, g.unit(target), 2)
    elif kind == "bexley":
        u.ready = False
        _r_discard_equipment(g, {}, target)
    elif kind == "odalys":
        p.turn_flags.add(f"odalys{uid}")
        p.vapor -= 1
        o = g.unit(target)
        if o.frozen:
            deal(g, ("u", target), 2, {"kind": "effect", "seat": seat, "unit": uid})
        else:
            freeze(g, o)
    elif kind == "litany":
        p.turn_flags.add(f"litany{uid}")
        p.vapor -= 1
        cleanse(g, g.unit(target), 2)
        if u.corrupted:                                      # Dark Litany: whenever she Cleanses
            _queue_target(g, seat, "dmg2", _enemy_units(g, seat), "Dark Litany: deal 2 damage to")
    elif kind == "move":
        p.turn_flags.add(f"disruptor{uid}")
        a, b = g.unit(target[0]), g.unit(target[1])
        if a.taint:
            a.taint -= 1
            place_taint(g, b, 1, seat)
    elif kind == "focus":
        p.turn_flags.add(f"focus{uid}")
        p.vapor -= 1
        draw(g, seat, 1)
    check_kos(g)


# =============================================================================================== public API
def decider(g: Game) -> Optional[int]:
    if g.over:
        return None
    if g.pending:
        return g.pending[0]["seat"]
    if g.phase == "intercept":
        return g.other(g.active)
    return g.active


def legal_actions(g: Game) -> list[Act]:
    if g.over:
        return []
    if g.pending:
        pr = g.pending[0]
        return [Act(f"p:{pr['kind']}:{key}", label, payload) for key, label, payload in pr["opts"]]
    seat = g.active
    p = g.players[seat]
    if g.phase == "main":
        acts: list[Act] = []
        if p.actions_this_turn < MAX_ACTIONS_PER_TURN:
            for cid in sorted(set(p.hand)):
                for suffix, label, payload in _play_options(g, seat, cid):
                    acts.append(Act(f"c:{cid}:{suffix}", f"Play {name(cid)} {label}".strip(), payload))
            acts.extend(_ability_opts(g, seat))
        acts.append(Act("clash", "Go to the Clash", None))
        return acts
    if g.phase == "declare":
        able = [u for u in p.units() if can_attack(g, u)]
        acts = [Act(f"atk:{u.uid}", f"Attack with {uname(u)} ({power(g, u, role='attack')} Power)", u.uid) for u in able]
        if not any(_compelled(g, u) for u in able):
            acts.append(Act("done", "Finish declaring attackers" if g.attackers else "Don't attack", None))
        return acts
    if g.phase == "intercept":
        attacker = g.unit(g.block_queue[0])
        acts = [Act("noblock", f"Don't intercept {uname(attacker)}", None)]
        acts += [Act(f"blk:{u.uid}", f"Intercept {uname(attacker)} with {uname(u)}", u.uid) for u in _intercept_options(g)]
        return acts
    return []


def apply(g: Game, key: str) -> None:
    acts = {a.key: a for a in legal_actions(g)}
    act = acts.get(key)
    if act is None:
        raise IllegalAction(f"{key!r} is not a legal action now")
    g.history.append(key)
    if g.pending:
        pr = g.pending.pop(0)
        RESOLVERS[pr["kind"]](g, pr, act.data)
    elif g.phase == "main":
        if key == "clash":
            _to_clash(g)
        elif key.startswith("c:"):
            payload = dict(act.data)
            _play_card(g, g.active, payload)
        else:
            _use_ability(g, g.active, act.data)
    elif g.phase == "declare":
        if key == "done":
            _finish_declare(g)
        else:
            _declare(g, g.unit(act.data))
    elif g.phase == "intercept":
        _intercept(g, act.data)
    _mark_chains(g)
    _run(g)


def _mark_chains(g: Game) -> None:
    """Chain bonuses look at Actions played *before* the current one; promote this turn's power marks to chain marks
    once the Action that made them has finished resolving."""
    if g.stack is not None:
        return
    for p in g.players:
        for flag in [f for f in p.turn_flags if f.startswith("power:")]:
            p.turn_flags.add("chain:" + flag[6:])


def replay(decks, names, seed: int, keys: Iterable[str], level: int = 3, first: Optional[int] = None) -> Game:
    g = new_game(decks, names, seed, level=level, first=first)
    for key in keys:
        apply(g, key)
    return g


def public_summary(g: Game) -> dict:
    """A compact, hidden-information-safe snapshot (no hands, no deck order) used by the UI and the assistant."""
    def unit_view(u: Unit) -> dict:
        return {"name": u.card.name, "id": u.cid, "power": power(g, u), "stamina": stamina(g, u), "damage": u.damage,
                "grade": grade(g, u), "taint": u.taint, "corrupted": u.corrupted, "ready": u.ready, "frozen": u.frozen,
                "soaked": u.soaked, "attached": [name(c) for c in u.equipment() + u.boons + u.afflictions]}
    level = protocol(g)
    return {
        "round": g.round, "threat": g.threat, "protocol": PROTOCOL_BY_LEVEL[level].name if level else None,
        "sky": name(g.sky) if g.sky else None, "active": g.players[g.active].name, "phase": g.phase,
        "players": [{"name": p.name, "resolve": p.resolve, "vapor": p.vapor, "capacity": capacity(g, p.seat),
                     "hand": len(p.hand), "deck": len(p.deck), "landmark": name(p.landmark) if p.landmark else None,
                     "field": [unit_view(u) if u else None for u in p.field]} for p in g.players],
    }
