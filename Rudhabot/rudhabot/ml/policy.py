"""Move selection for the Clairvoyance: afterstate search over the engine's legal actions.

The engine enumerates legal moves; an evaluator scores the state each move leads to; the policy picks one. The AI therefore
can only ever *choose among moves the rules already allow*. Before searching, hidden information is re-sampled
("determinization"): the searcher's own deck order and the opponent's hand+deck are shuffled from what that player could
know, so the AI never benefits from seeing cards a human in its seat could not see.

Two depths:
- 1-ply (`score_actions`): score the position right after each legal move.
- Turn lookahead (`plan_actions`): a Cloudfall turn is a run of decisions by the same player (play cards, use
  abilities, go to the Clash, declare attackers). For the most promising first moves, the search plays out the rest of
  that run greedily and scores where the *whole line* leaves the game, so a move is judged by the turn it starts,
  not only by the position one step later. Each line stops as soon as the opponent has to decide (or the turn or game
  ends), so the search never assumes what the opponent will do. Optionally it averages several determinizations.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

import numpy as np

from rudhabot.ml.features import _preview, encoder_for
from rudhabot.ml.nn import _Net
from rudhabot.tcg import engine as E
from rudhabot.tcg.state import Game


class Evaluator(Protocol):
    name: str

    def values(self, games: Sequence[Game], me: int) -> np.ndarray: ...


class HeuristicEvaluator:
    """The non-ML baseline (and the production fallback): a hand-tuned material + Resolve + tempo score."""
    name = "heuristic-v1"

    def values(self, games: Sequence[Game], me: int) -> np.ndarray:
        return np.array([self._value(g, me) for g in games], dtype=np.float32)

    @staticmethod
    def _side(g: Game, seat: int) -> float:
        p = g.players[seat]
        board = 0.0
        for u in p.units():
            pw, st = E.base_stats(g, u)
            board += pw * 0.8 + (st - u.damage) * 0.5 - (1.5 if u.corrupted else 0) - 0.4 * u.taint
        return p.resolve * 0.35 + board + 0.4 * len(p.hand) + 0.25 * E.capacity(g, p.seat) + 0.6 * (p.landmark is not None)

    def _value(self, g: Game, me: int) -> float:
        if g.over:
            return terminal_value(g, me)
        to_resolve, swing, _, _ = _preview(g, me)
        score = self._side(g, me) - self._side(g, g.other(me)) + 30 * 0.35 * to_resolve + 3.0 * swing
        return 1.0 / (1.0 + math.exp(-score / 6.0))


class NetEvaluator:
    def __init__(self, net: _Net, name: Optional[str] = None):
        self.net = net
        self.name = name or net.meta.get("version", "clairvoyance")
        self._encode_many = encoder_for(net.ARCH)[1]

    def values(self, games: Sequence[Game], me: int) -> np.ndarray:
        out = np.empty(len(games), dtype=np.float32)
        live = [i for i, g in enumerate(games) if not g.over]
        for i, g in enumerate(games):
            if g.over:
                out[i] = terminal_value(g, me)
        if live:
            out[live] = self.net.predict(*self._encode_many([games[i] for i in live], me))
        return out


class BlendEvaluator:
    """logit(P) = logit(P_net) + w * logit(P_heuristic): the learned model plus the hand-written prior."""

    def __init__(self, net: _Net, weight: float = 0.5, name: Optional[str] = None):
        self.net_eval, self.heuristic, self.weight = NetEvaluator(net), HeuristicEvaluator(), weight
        self.name = name or f"{net.meta.get('version', 'clairvoyance')}+h{weight:g}"

    def values(self, games: Sequence[Game], me: int) -> np.ndarray:
        p_net = np.clip(self.net_eval.values(games, me), 1e-4, 1 - 1e-4)
        p_h = np.clip(self.heuristic.values(games, me), 1e-4, 1 - 1e-4)
        z = np.log(p_net / (1 - p_net)) + self.weight * np.log(p_h / (1 - p_h))
        return (1 / (1 + np.exp(-z))).astype(np.float32)


def terminal_value(g: Game, me: int) -> float:
    return 0.5 if g.winner == -1 else float(g.winner == me)


def determinize(g: Game, me: int, rng: random.Random) -> Game:
    """A copy of `g` where everything `me` cannot see is re-sampled consistently with what `me` does see."""
    c = g.clone()
    c.rng = random.Random(rng.random())
    mine = c.players[me]
    rng.shuffle(mine.deck)
    opp = c.players[c.other(me)]
    hidden = opp.hand + opp.deck
    rng.shuffle(hidden)
    k = len(opp.hand)
    opp.hand, opp.deck = hidden[:k], hidden[k:]
    return c


def _children(base: Game, acts) -> list[Game]:
    out = []
    for act in acts:
        child = base.clone()
        E.apply_trusted(child, act)          # acts were listed on this very position
        out.append(child)
    return out


def score_actions(g: Game, evaluator: Evaluator, rng: random.Random, use_determinization: bool = True):
    """[(Act, value-for-the-decider)] for every legal action, best first (1-ply)."""
    me = E.decider(g)
    acts = E.legal_actions(g)
    if len(acts) <= 1:
        return [(a, 0.5) for a in acts]
    base = determinize(g, me, rng) if use_determinization else g.clone()
    values = evaluator.values(_children(base, acts), me)
    ranked = sorted(zip(acts, values.tolist()), key=lambda pair: -pair[1])
    return ranked


@dataclass(frozen=True)
class Lookahead:
    """Turn-lookahead settings: roll out the `top` best 1-ply moves for up to `max_steps` further decisions, averaged
    over `samples` determinizations, evaluating at most `budget` positions per sample (then lines stop where they are)."""
    top: int = 4
    max_steps: int = 8
    samples: int = 1
    budget: int = 800


@dataclass(frozen=True)
class Plan:
    act: E.Act
    value: float                       # estimated value for the decider where the line leaves the game
    line: tuple[str, ...] = ()         # the follow-up moves the search expects to make in this run of decisions,
                                       # up to the first one that depends on cards it can't see
    searched: bool = False             # False: 1-ply estimate only (not among the top candidates)


# Prompts whose options are cards from a hidden zone (the deck): in a determinized copy those are sampled cards.
_HIDDEN_PROMPTS = ("p:look_take:", "p:look_bottom:", "p:take_card:", "p:delivery:", "p:mulligan:")


def _shown(parent: Game, act: E.Act, me: int, deck_size: int) -> bool:
    """Whether a follow-up move may be shown in a hint: not once the line has drawn from the decider's deck or meets a
    prompt listing deck cards, because in the determinized copy those cards are a guess, not the real deck."""
    return len(parent.players[me].deck) >= deck_size and not act.key.startswith(_HIDDEN_PROMPTS)


def _same_run(g: Game, me: int, active: int, rnd: int) -> bool:
    """Still `me` deciding, in the same turn: the line may go on."""
    return not g.over and g.active == active and g.round == rnd and E.decider(g) == me


def plan_actions(g: Game, evaluator: Evaluator, rng: random.Random, look: Lookahead = Lookahead()) -> list[Plan]:
    """Every legal action as a Plan, best first: the `look.top` most promising first moves are judged by the line they
    start (see the module docstring); the rest keep their 1-ply estimate and rank after them."""
    me = E.decider(g)
    acts = E.legal_actions(g)
    if len(acts) <= 1:
        return [Plan(a, 0.5) for a in acts]
    n, active, rnd, deck_size = len(acts), g.active, g.round, len(g.players[me].deck)
    totals = np.zeros(n)
    roll: list[int] = []
    lines: dict[int, list[str]] = {}
    hidden_from: set[int] = set()                         # lines whose later moves depend on sampled cards
    for sample in range(max(1, look.samples)):
        children = _children(determinize(g, me, rng), acts)
        values = evaluator.values(children, me).astype(np.float64)
        if sample == 0:                                   # the candidates are fixed by the first sample
            roll = [int(i) for i in np.argsort(-values, kind="stable")[:look.top]]
            lines = {i: [] for i in roll}
        frontier = [i for i in roll if _same_run(children[i], me, active, rnd)]
        evaluated = n
        for _ in range(look.max_steps):
            if not frontier or evaluated >= look.budget:
                break
            batch, owner, moves = [], [], []
            for i in frontier:
                options = E.legal_actions(children[i])
                batch.extend(_children(children[i], options))
                owner.extend([i] * len(options))
                moves.extend(options)
            scores = evaluator.values(batch, me)
            evaluated += len(batch)
            best: dict[int, int] = {}
            for j, i in enumerate(owner):
                if i not in best or scores[j] > scores[best[i]]:
                    best[i] = j
            for i, j in best.items():
                if sample == 0 and i not in hidden_from:
                    if _shown(children[i], moves[j], me, deck_size):
                        lines[i].append(moves[j].label)
                    else:
                        hidden_from.add(i)
                children[i], values[i] = batch[j], float(scores[j])
            frontier = [i for i in frontier if _same_run(children[i], me, active, rnd)]
        totals += values
    means = totals / max(1, look.samples)
    searched = set(roll)
    order = sorted(range(n), key=lambda i: (i not in searched, -means[i]))
    return [Plan(acts[i], float(means[i]), tuple(lines.get(i, ())), i in searched) for i in order]


def _rank(g: Game, evaluator: Evaluator, rng: random.Random, look: Optional[Lookahead]):
    if look is None:
        return score_actions(g, evaluator, rng)
    return [(p.act, p.value) for p in plan_actions(g, evaluator, rng, look)]


def choose_scored(g: Game, evaluator: Evaluator, rng: random.Random, temperature: float = 0.0, epsilon: float = 0.0,
                  look: Optional[Lookahead] = None):
    """(action, estimated value for the decider, or None if not evaluated). `look` turns on the turn lookahead."""
    acts = E.legal_actions(g)
    if len(acts) == 1:
        return acts[0], None
    if epsilon and rng.random() < epsilon:
        return rng.choice(acts), None
    ranked = _rank(g, evaluator, rng, look)
    if temperature <= 0:
        return ranked[0]
    logits = np.array([v for _, v in ranked]) / temperature
    probs = np.exp(logits - logits.max())
    probs /= probs.sum()
    return ranked[int(rng.choices(range(len(ranked)), weights=probs.tolist())[0])]


def choose(g: Game, evaluator: Evaluator, rng: random.Random, temperature: float = 0.0, epsilon: float = 0.0,
           look: Optional[Lookahead] = None):
    return choose_scored(g, evaluator, rng, temperature, epsilon, look)[0]


class RandomPolicy:
    name = "random"
    last_value = None

    def pick(self, g: Game, rng: random.Random):
        return rng.choice(E.legal_actions(g))


class SearchPolicy:
    def __init__(self, evaluator: Evaluator, temperature: float = 0.0, epsilon: float = 0.0,
                 look: Optional[Lookahead] = None):
        self.evaluator, self.temperature, self.epsilon, self.look = evaluator, temperature, epsilon, look
        self.name = evaluator.name + (f"+look{look.top}" if look else "")
        self.last_value: Optional[float] = None       # value of the chosen move's afterstate or line (TD targets)

    def pick(self, g: Game, rng: random.Random):
        act, self.last_value = choose_scored(g, self.evaluator, rng, self.temperature, self.epsilon, self.look)
        return act
