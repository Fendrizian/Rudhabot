"""Cloudfall persistence: player profiles (`tcg.json`) and live matches (`tcg_matches.json`).

Both are new files, so existing data is untouched. A live match is stored as its replay recipe (seed, decks, seats,
action keys); the engine is deterministic, so replaying the keys rebuilds the exact state after a restart.
"""
from __future__ import annotations

from typing import Optional

from rudhabot.persistence.json_store import JsonStore

START_RATING = 1000


def blank_profile() -> dict:
    return {"collection": {}, "decks": {}, "active_deck": None, "rating": START_RATING, "wins": 0, "losses": 0,
            "draws": 0, "starter": None, "rewards": {"day": None, "count": 0, "opponents": {}}, "history": []}


class TcgRepository:
    def __init__(self, profiles: JsonStore, matches: JsonStore):
        self._profiles = profiles
        self._matches = matches

    # ---------------------------------------------------------------- profiles
    def profile(self, user_id: int | str) -> dict:
        data = self._profiles.load()
        profile = data.get(str(user_id))
        if profile is None:
            return blank_profile()
        merged = blank_profile()
        merged.update(profile)
        return merged

    def save_profile(self, user_id: int | str, profile: dict) -> None:
        data = self._profiles.load()
        data[str(user_id)] = profile
        self._profiles.save(data)

    def all_profiles(self) -> dict[str, dict]:
        return self._profiles.load()

    def delete_profile(self, user_id: int | str) -> bool:
        data = self._profiles.load()
        removed = data.pop(str(user_id), None) is not None
        if removed:
            self._profiles.save(data)
        return removed

    # ---------------------------------------------------------------- matches
    def live_matches(self) -> dict[str, dict]:
        return self._matches.load()

    def save_match(self, match_id: str, record: dict) -> None:
        data = self._matches.load()
        data[match_id] = record
        self._matches.save(data)

    def get_match(self, match_id: str) -> Optional[dict]:
        return self._matches.load().get(match_id)

    def delete_match(self, match_id: str) -> None:
        data = self._matches.load()
        if data.pop(match_id, None) is not None:
            self._matches.save(data)


def empty_matches() -> dict:
    return {}
