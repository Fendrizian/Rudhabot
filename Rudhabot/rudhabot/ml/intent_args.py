"""Reading a command's arguments out of what someone typed, after the intent router has picked the command.

"flip a coin for 100 on heads" -> the router says `coinflip`; this module reads `bet:100 choice:heads`, so /ask can
show the exact command to type instead of a bare template. It is plain rules, not a model, and it never runs
anything: the result is only shown as text, and the person still types (and the command still checks) the real thing.

Each parameter is read by its name:
- bet / amount: "all" / "everything" / "max", or the first money amount (100, 1,000, 2.5k, 1m);
- choice / on / play: one of the command's own choices, matched by value or name ("don't pass", "tails");
- guess: a number from 2 to 12 after "guess", "on" or "number" (the dice game), or the only small number left;
- target: a Discord mention in the text;
- item: the longest run of words that the shop catalogue recognises (when a resolver is given).
Anything not found is left out, and the usage shows `<name>` for it.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional, Sequence

_AMOUNT = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([km])?(?![\w.])", re.I)
_ALL = re.compile(r"\b(all|everything|max|all in|all-in)\b", re.I)
_MENTION = re.compile(r"<@!?(\d+)>")
_GUESS = re.compile(r"\b(?:guess|on|number|pick)\s+(\d{1,2})\b", re.I)
_WORD = re.compile(r"[a-z0-9']+")


def _amounts(text: str) -> list[tuple[int, int, int]]:
    """[(value, start, end)] for every money-looking number in the text."""
    out = []
    for m in _AMOUNT.finditer(text):
        number = float(m.group(1).replace(",", ""))
        scale = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
        value = int(round(number * scale))
        if value > 0:
            out.append((value, m.start(), m.end()))
    return out


def _choice(text: str, choices: Sequence[tuple[str, str]]) -> Optional[str]:
    """The one choice (value) whose value or display name appears in the text. A match inside a longer match does
    not count ("pass" inside "don't pass"); two different choices ("rock paper scissors") mean none was picked."""
    plain = lambda s: " ".join(_WORD.findall(s.lower().replace("’", "").replace("'", "")))   # "don't" = "dont"
    flat = f" {plain(text)} "
    matches = []
    for name, value in choices:
        for form in {value, re.sub(r"\s*\(.*\)", "", name)}:
            words = plain(form)
            if words and f" {words} " in flat:
                matches.append((words, value))
    kept = {value for words, value in matches
            if not any(words != other and f" {words} " in f" {other} " for other, _ in matches)}
    return kept.pop() if len(kept) == 1 else None


def _item(text: str, resolve: Callable[[str], Optional[str]]) -> Optional[str]:
    words = _WORD.findall(text.lower())
    for size in range(min(4, len(words)), 0, -1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if phrase in ("a", "an", "the", "my", "item", "items"):
                continue
            found = resolve(phrase)
            if found:
                return found
    return None


def extract(text: str, params: Iterable[tuple[str, Sequence[tuple[str, str]]]],
            resolve_item: Optional[Callable[[str], Optional[str]]] = None) -> dict[str, str]:
    """{parameter name: value as it would be typed} for the parameters found in `text`.

    `params` is [(name, [(choice name, choice value), ...])] in the command's order (choices may be empty)."""
    params = list(params)
    names = [n for n, _ in params]
    found: dict[str, str] = {}
    used: set[tuple[int, int]] = set()
    if "guess" in names:
        m = _GUESS.search(text)
        if m and 2 <= int(m.group(1)) <= 12:
            found["guess"] = m.group(1)
            used.add(m.span(1))
    for name, choices in params:
        if name in found:
            continue
        if name in ("bet", "amount"):
            if _ALL.search(text):
                found[name] = "all"
                continue
            free = [a for a in _amounts(text) if not any(s <= a[1] < e for s, e in used)]
            if "guess" in names and "guess" not in found and len(free) >= 2:
                small = next((a for a in free if 2 <= a[0] <= 12), None)
                if small is not None:
                    found["guess"] = str(small[0])
                    used.add((small[1], small[2]))
                    free = [a for a in free if a is not small]
            if free:
                found[name] = str(free[0][0])
                used.add((free[0][1], free[0][2]))
        elif choices:
            value = _choice(text, choices)
            if value is not None:
                found[name] = value
        elif name == "target":
            m = _MENTION.search(text)
            if m:
                found[name] = f"<@{m.group(1)}>"
        elif name == "item" and resolve_item is not None:
            item = _item(text, resolve_item)
            if item:
                found[name] = item
    if "guess" in names and "guess" not in found:
        left = [a for a in _amounts(text) if not any(s <= a[1] < e for s, e in used) and 2 <= a[0] <= 12]
        if len(left) == 1:
            found["guess"] = str(left[0][0])
    return {n: found[n] for n in names if n in found}


def usage(path: str, params: Iterable[tuple[str, bool]], values: dict[str, str]) -> str:
    """`/path name:value ...` with what was found, `<name>` for missing required ones, optional ones only if found."""
    parts = [f"/{path}"]
    for name, required in params:
        if name in values:
            parts.append(f"{name}:{values[name]}")
        elif required:
            parts.append(f"<{name}>")
    return " ".join(parts)
