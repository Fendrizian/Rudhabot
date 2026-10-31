"""Local retrieval over Rudhabot's own documentation: command help, the Cloudfall rules and every card's text.

This is the *only* source of facts the assistant may cite, so it can't invent bot features or card text. Plain BM25,
no external service. It also powers the non-AI fallback answer when OpenAI is disabled or unavailable.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from rudhabot.tcg.cards import CARDS, PROTOCOLS
from rudhabot.tcg.rules_text import RULES

_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset("a an the of to and or in on is it for you your i do does how what can with at by be this that my "
                  "me are when why who which tell about please".split())


def _stem(t: str) -> str:
    """Tiny plural folding so 'loans' finds 'loan' and 'abilities' finds 'ability'."""
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokens(text: str) -> list[str]:
    words = (t[:-2] if t.endswith("'s") else t for t in _WORD.findall(text.lower()))
    return [_stem(t) for t in words if t and t not in _STOP]


_SOFT_STOP = frozenset({"work", "use", "get", "make", "play", "mean"})


def _content(terms: list[str]) -> list[str]:
    """Drop helper verbs ('how do loans *work*') when the question has other words to match on."""
    kept = [t for t in terms if t not in _SOFT_STOP]
    return kept or terms


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    text: str
    kind: str          # "command" | "rule" | "card" | "protocol"


def build_documents() -> list[Doc]:
    from rudhabot.views.help import get_commands_data
    docs = [Doc(f"cmd:{c['name']}", f"/{c['name']} {c['args']}".strip(), c["description"], "command")
            for c in get_commands_data()]
    docs += [Doc(f"rule:{title}", title, text, "rule") for title, text in RULES.items()]
    for card in CARDS:
        meta = [card.kind, f"cost {card.cost}"]
        if card.kind == "Character":
            meta += [f"Grade {card.grade}", "/".join(card.affiliations), f"{card.attack}/{card.stamina}"]
        elif card.power:
            meta.append(card.power + (f" Grade {card.grade}" if card.grade else ""))
        docs.append(Doc(f"card:{card.id}", f"{card.name} ({card.id})", f"{', '.join(meta)}. {card.text}", "card"))
    docs += [Doc(f"protocol:{p.severity}", f"Protocol {p.name} (Severity {p.severity})", p.effect, "protocol")
             for p in PROTOCOLS]
    return docs


class KnowledgeBase:
    def __init__(self, docs: list[Doc] | None = None, k1: float = 1.4, b: float = 0.7):
        self.docs = docs or build_documents()
        self.k1, self.b = k1, b
        self._tf = [Counter(tokens(d.title + " " + d.title + " " + d.text)) for d in self.docs]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = sum(self._len) / max(1, len(self._len))
        df = Counter(t for tf in self._tf for t in tf)
        n = len(self.docs)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 5, kinds: tuple[str, ...] = ()) -> list[tuple[Doc, float]]:
        q = _content(tokens(query))
        scored = []
        for i, doc in enumerate(self.docs):
            if kinds and doc.kind not in kinds:
                continue
            tf, length = self._tf[i], self._len[i]
            score = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    score += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self._avg))
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda pair: -pair[1])
        return scored[:k]

    def covers(self, query: str, doc: Doc) -> bool:
        """Whether `doc` actually answers `query`: at least half of the query's terms appear in it. Keeps the offline
        answer from returning an unrelated document for a general question ('what's the mass of the sun')."""
        q = set(_content(tokens(query)))
        if not q:
            return False
        have = set(tokens(doc.title + " " + doc.text))
        return len(q & have) / len(q) >= 0.5

    def get(self, doc_id: str) -> Doc | None:
        return next((d for d in self.docs if d.id == doc_id), None)

    def command_names(self) -> set[str]:
        return {d.id[4:] for d in self.docs if d.kind == "command"}
