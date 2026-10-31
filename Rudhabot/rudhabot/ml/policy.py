"""Move selection for the Clairvoyance: 1-ply afterstate search over the engine's legal actions.

The engine enumerates legal moves; an evaluator scores the state each move leads to; the policy picks one. The AI therefore
can only ever *choose among moves the rules already allow*. Before searching, hidden information is re-sampled
("determinization"): the searcher's own deck order and the opponent's hand+deck are shuffled from what that player could
know, so the AI never benefits from seeing cards a human in its seat could not see.
"""
from __future__ import annotations

import math
import random
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
        board = sum(E.power(g, u) * 0.8 + E.remaining(g, u) * 0.5 - (1.5 if u.corrupted else 0) - 0.4 * u.taint
                    for u in p.units())
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


def score_actions(g: Game, evaluator: Evaluator, rng: random.Random, use_determinization: bool = True):
    """[(Act, value-for-the-decider)] for every legal action, best first."""
    me = E.decider(g)
    acts = E.legal_actions(g)
    if len(acts) <= 1:
        return [(a, 0.5) for a in acts]
    base = determinize(g, me, rng) if use_determinization else g.clone()
    children = []
    for act in acts:
        child = base.clone()
        E.apply(child, act.key)
        children.append(child)
    values = evaluator.values(children, me)
    ranked = sorted(zip(acts, values.tolist()), key=lambda pair: -pair[1])
    return ranked


def choose_scored(g: Game, evaluator: Evaluator, rng: random.Random, temperature: float = 0.0, epsilon: float = 0.0):
    """(action, estimated value of the state it leads to for the decider, or None if not evaluated)."""
    acts = E.legal_actions(g)
    if len(acts) == 1:
        return acts[0], None
    if epsilon and rng.random() < epsilon:
        return rng.choice(acts), None
    ranked = score_actions(g, evaluator, rng)
    if temperature <= 0:
        return ranked[0]
    logits = np.array([v for _, v in ranked]) / temperature
    probs = np.exp(logits - logits.max())
    probs /= probs.sum()
    return ranked[int(rng.choices(range(len(ranked)), weights=probs.tolist())[0])]


def choose(g: Game, evaluator: Evaluator, rng: random.Random, temperature: float = 0.0, epsilon: float = 0.0):
    return choose_scored(g, evaluator, rng, temperature, epsilon)[0]


class RandomPolicy:
    name = "random"
    last_value = None

    def pick(self, g: Game, rng: random.Random):
        return rng.choice(E.legal_actions(g))


class SearchPolicy:
    def __init__(self, evaluator: Evaluator, temperature: float = 0.0, epsilon: float = 0.0):
        self.evaluator, self.temperature, self.epsilon = evaluator, temperature, epsilon
        self.name = evaluator.name
        self.last_value: Optional[float] = None       # value of the chosen move's afterstate (for TD targets)

    def pick(self, g: Game, rng: random.Random):
        act, self.last_value = choose_scored(g, self.evaluator, rng, self.temperature, self.epsilon)
        return act
