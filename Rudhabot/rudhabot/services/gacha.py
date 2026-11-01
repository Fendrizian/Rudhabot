"""Skyfall Wishes service: shards, wishes with pity, Cloudshine foils, Vapor Dust crafting and the weekly Convergence.

Per-player state lives in the Cloudfall profile (`tcg.json`); the server-wide Convergence meter in `gacha.json`.
Ra spent on shards goes through UserRepository (audited automatically); shard and dust balances are reported to the
audit trail here as `shards` / `dust` entries.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import asdict
from typing import Optional

from rudhabot.clock import Clock
from rudhabot.domain.gacha import (CONVERGENCE_MILESTONES, CRAFT_COST, DAILY_SHARDS, STANDARD, WISH_COST, Banner,
                                   PityState, Pull, dust_for_extra, iso_week, rarity, weekly_banner, wish)
from rudhabot.persistence.json_store import JsonStore
from rudhabot.persistence.tcg import TcgRepository
from rudhabot.persistence.users import UserRepository
from rudhabot.tcg.cards import CARD_BY_ID

SHARD_PRICE = 10                 # ra per Cloud Shard
MAX_SHARD_PURCHASE = 16_000      # per command, so a typo can't empty a wallet


class GachaRefused(Exception):
    """A user-facing refusal; the message is shown as-is."""


class GachaService:
    def __init__(self, profiles: TcgRepository, community: JsonStore, users: UserRepository, clock: Clock,
                 rng: random.Random, audit=None, bank=None):
        self._profiles, self._community, self._users = profiles, community, users
        self._clock, self._rng, self._audit, self._bank = clock, rng, audit, bank

    # ------------------------------------------------------------------ helpers
    def banner(self, key: str = "featured") -> Banner:
        return STANDARD if key == "standard" else weekly_banner(self._clock.now().date())

    def _record(self, uid: str, account: str, delta: int, after: int, reason: str) -> None:
        if self._audit is not None and delta:
            self._audit.record(uid, account, delta, after, reason=reason)

    @staticmethod
    def pity(profile: dict, banner_key: str) -> PityState:
        raw = profile.setdefault("pity", {}).get(banner_key if banner_key == "standard" else "featured", {})
        return PityState(**{k: raw.get(k, v) for k, v in asdict(PityState()).items()})

    def _week(self) -> dict:
        data = self._community.load()
        week = iso_week(self._clock.now().date())
        if data.get("week") != week:
            data = {"week": week, "wishes": 0, "participants": [], "claims": {}}
        return data

    # ------------------------------------------------------------------ shards
    def buy_shards(self, user_id, shards: int, pay_with: str = "wallet") -> int:
        uid = str(user_id)
        if shards < 1 or shards > MAX_SHARD_PURCHASE:
            raise GachaRefused(f"❌ Buy between 1 and {MAX_SHARD_PURCHASE:,} shards at a time.")
        cost = shards * SHARD_PRICE
        if pay_with == "card":
            if self._bank is None:
                raise GachaRefused("❌ Card payments are unavailable.")
            from rudhabot.services.bank import BankRefused
            try:
                self._bank.charge(uid, cost, f"{shards} Cloud Shards")
            except BankRefused as refusal:
                raise GachaRefused(str(refusal)) from None
        else:
            acc = self._users.get(uid)
            if acc["wallet"] < cost:
                raise GachaRefused(f"❌ {shards:,} shards cost {cost:,}; your wallet has {acc['wallet']:,}.")
            self._users.update(uid, wallet=acc["wallet"] - cost)
        profile = self._profiles.profile(uid)
        profile["shards"] = profile.get("shards", 0) + shards
        self._profiles.save_profile(uid, profile)
        self._record(uid, "shards", shards, profile["shards"], f"bought {shards} shards ({pay_with})")
        return profile["shards"]

    def grant_shards(self, user_id, shards: int, reason: str) -> int:
        uid = str(user_id)
        profile = self._profiles.profile(uid)
        profile["shards"] = profile.get("shards", 0) + shards
        self._profiles.save_profile(uid, profile)
        self._record(uid, "shards", shards, profile["shards"], reason)
        return profile["shards"]

    def claim_daily(self, user_id) -> int:
        uid = str(user_id)
        profile = self._profiles.profile(uid)
        today = self._clock.now().date().isoformat()
        if profile.get("daily_shards") == today:
            raise GachaRefused("⏳ You already claimed today's shards. Come back tomorrow!")
        profile["daily_shards"] = today
        profile["shards"] = profile.get("shards", 0) + DAILY_SHARDS
        self._profiles.save_profile(uid, profile)
        self._record(uid, "shards", DAILY_SHARDS, profile["shards"], "daily shards")
        return profile["shards"]

    # ------------------------------------------------------------------ wishes
    def wish(self, user_id, count: int, banner_key: str = "featured") -> tuple[list[Pull], list[bool], int, Banner]:
        """Make `count` (1 or 10) wishes. Returns (pulls, is_new flags, dust gained, banner)."""
        uid = str(user_id)
        if count not in (1, 10):
            raise GachaRefused("❌ Wish 1 or 10 times.")
        banner = self.banner(banner_key)
        profile = self._profiles.profile(uid)
        cost = WISH_COST * count
        if profile.get("shards", 0) < cost:
            raise GachaRefused(f"❌ {count} wish{'es' if count > 1 else ''} cost {cost:,} Cloud Shards; you have "
                               f"{profile.get('shards', 0):,}. Get more with `/tcg wish daily` or `/tcg wish shards`.")
        state = self.pity(profile, banner_key)
        collection = Counter(profile["collection"])
        foils = Counter(profile.get("foils", {}))
        pulls, new, dust = [], [], 0
        for _ in range(count):
            pull = wish(banner, state, self._rng)
            new.append(collection[pull.card] == 0)
            collection[pull.card] += 1
            if pull.foil:
                foils[pull.card] += 1
            dust += dust_for_extra(pull.card, collection[pull.card])
            pulls.append(pull)
        profile["shards"] -= cost
        profile["collection"] = dict(collection)
        profile["foils"] = dict(foils)
        profile["dust"] = profile.get("dust", 0) + dust
        profile.setdefault("pity", {})["standard" if banner_key == "standard" else "featured"] = asdict(state)
        profile["wishes_total"] = profile.get("wishes_total", 0) + count
        self._profiles.save_profile(uid, profile)
        self._record(uid, "shards", -cost, profile["shards"], f"{count} wish(es) on {banner.name}")
        self._record(uid, "dust", dust, profile["dust"], "duplicate cards became Vapor Dust")
        week = self._week()
        week["wishes"] += count
        if uid not in week["participants"]:
            week["participants"].append(uid)
        self._community.save(week)
        return pulls, new, dust, banner

    # ------------------------------------------------------------------ crafting and Convergence
    def craft(self, user_id, card_id: str) -> int:
        uid = str(user_id)
        card = CARD_BY_ID.get(card_id)
        if card is None:
            raise GachaRefused("❌ Unknown card.")
        cost = CRAFT_COST[rarity(card)]
        profile = self._profiles.profile(uid)
        if profile.get("dust", 0) < cost:
            raise GachaRefused(f"❌ Crafting {card.name} costs {cost:,} Vapor Dust; you have {profile.get('dust', 0):,}.")
        profile["dust"] -= cost
        profile["collection"][card_id] = profile["collection"].get(card_id, 0) + 1
        self._profiles.save_profile(uid, profile)
        self._record(uid, "dust", -cost, profile["dust"], f"crafted {card.name}")
        return profile["dust"]

    def convergence(self) -> dict:
        week = self._week()
        return {"week": week["week"], "wishes": week["wishes"], "participants": len(week["participants"]),
                "milestones": [{"at": at, "shards": reward, "reached": week["wishes"] >= at} for at, reward in CONVERGENCE_MILESTONES]}

    def claim_convergence(self, user_id) -> int:
        uid = str(user_id)
        week = self._week()
        if uid not in week["participants"]:
            raise GachaRefused("❌ Make at least one wish this week to share in the Convergence.")
        claimed = set(week["claims"].get(uid, []))
        reward = 0
        for at, shards in CONVERGENCE_MILESTONES:
            if week["wishes"] >= at and at not in claimed:
                reward += shards
                claimed.add(at)
        if not reward:
            raise GachaRefused("Nothing new to claim yet. The meter fills as everyone in the server wishes.")
        week["claims"][uid] = sorted(claimed)
        self._community.save(week)
        self.grant_shards(uid, reward, f"Convergence {week['week']}")
        return reward
