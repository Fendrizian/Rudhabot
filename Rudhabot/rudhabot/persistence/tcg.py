"""Cloudfall persistence: player profiles (`tcg.json`) and live matches (`tcg_matches.json`).

Both are new files, so existing data is untouched. A live match is stored as its replay recipe (seed, decks, seats,
action keys); the engine is deterministic, so replaying the keys rebuilds the exact state after a restart.
"""
from __future__ import annotations

from typing import Optional

from rudhabot.persistence.json_store import JsonStore, OwnedJson, json_copy

START_RATING = 1000


def blank_profile() -> dict:
    return {"collection": {}, "decks": {}, "active_deck": None, "rating": START_RATING, "wins": 0, "losses": 0,
            "draws": 0, "starter": None, "rewards": {"day": None, "count": 0, "opponents": {}}, "history": []}


class TcgRepository:
    """Profiles and live matches, kept in memory and written through to disk (see OwnedJson). Everything handed
    out is a copy, so callers change stored data only by saving it back."""

    def __init__(self, profiles: JsonStore, matches: JsonStore):
        self._profiles = OwnedJson(profiles)
        self._matches = OwnedJson(matches)

    # ---------------------------------------------------------------- profiles
    def profile(self, user_id: int | str) -> dict:
        profile = self._profiles.value().get(str(user_id))
        merged = blank_profile()
        if profile is not None:
            merged.update(json_copy(profile))
        return merged

    def save_profile(self, user_id: int | str, profile: dict) -> None:
        self._profiles.value()[str(user_id)] = json_copy(profile)
        self._profiles.persist()

    def all_profiles(self) -> dict[str, dict]:
        return json_copy(self._profiles.value())

    def delete_profile(self, user_id: int | str) -> bool:
        removed = self._profiles.value().pop(str(user_id), None) is not None
        if removed:
            self._profiles.persist()
        return removed

    # ---------------------------------------------------------------- matches
    def live_matches(self) -> dict[str, dict]:
        return json_copy(self._matches.value())

    def save_match(self, match_id: str, record: dict) -> None:
        self._matches.value()[match_id] = json_copy(record)
        self._matches.persist()

    def get_match(self, match_id: str) -> Optional[dict]:
        return json_copy(self._matches.value().get(match_id))

    def delete_match(self, match_id: str) -> None:
        if self._matches.value().pop(match_id, None) is not None:
            self._matches.persist()


def empty_matches() -> dict:
    return {}
