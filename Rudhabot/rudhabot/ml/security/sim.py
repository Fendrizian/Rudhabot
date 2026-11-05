"""The wargame simulator: synthetic server days with realistic normal activity and injected attacks.

A simulated server has members (account ages, avatars, names), staff with clearance tiers, channels (one of them a
relaxed roleplay-battle channel) and a daily rhythm with quiet hours. Normal life includes the hard cases that fool
naive security bots: announcement hype bursts, roleplay battles full of rapid commands and pings, onboarding waves of
legitimate joins from a partner invite, birthday floods of near-identical wishes, staff clean-up bans and role
restructuring.

Attack families (each randomised in size, speed and style):
    join_raid       look-alike young accounts join in a burst, many then spam
    spam_raid       recently joined accounts flood channels with near-duplicates and pings
    phishing        a few compromised *established* members post scam links (look-alike or unknown domains)
    mention_bomb    messages with tens of member pings
    staff_nuke      a compromised staff account deletes channels/roles and mass-bans
    malicious_bot   an admin adds a bot that then destroys or spams
    webhook_spam    a webhook floods @everyone messages
    slow_drip       raid accounts trickle in over hours and post scam links later (built to slip past rate rules)

`simulate_day()` returns the event stream and the ground-truth attack windows. `run()` replays it through the real
GuildEngine and scores what the rules and plans did. Nothing here uses real user data.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from rudhabot.security.clearance import Tier
from rudhabot.security.events import AuditEntry, MemberJoin, Message

FAMILIES = ("join_raid", "spam_raid", "phishing", "mention_bomb", "staff_nuke", "malicious_bot", "webhook_spam",
            "slow_drip")
HELD_OUT = ("slow_drip", "malicious_bot")          # never seen when training the incident model
NORMAL_EVENTS = ("announcement", "battle", "onboarding_wave", "celebration", "staff_cleanup", "restructure")

RELAXED = (102,)
GUILD = 1
BOT = 99

_WORDS = ("senate", "legion", "governor", "province", "border", "treaty", "caravan", "tax", "harbor", "fleet",
          "capital", "council", "election", "festival", "market", "ration", "garrison", "frontier", "decree",
          "embassy", "mine", "railway", "citizens", "parade", "anthem", "flag", "colony", "alliance", "census")
_CHAT = (
    "anyone up for the {w} meeting later?", "lol the {w} thing yesterday was wild", "gm everyone",
    "did the {w} vote pass?", "i think the {w} should lower the {w2}", "brb dinner", "who's leading the {w} now",
    "that {w} map is so good", "wait what happened to the {w}", "can someone explain the {w} rules",
    "we need more people in the {w} roleplay", "the {w} update is out!!", "nice one", "agreed",
    "i'll bring supplies to the {w}", "is the {w} event tonight or tomorrow", "omg yes", "thanks for the help!",
    "haha true", "my {w} build is almost done", "who wants to trade for {w} goods", "selling iron, buying grain",
    "the {w} anthem slaps", "happy to join the {w} council", "ok see you at the {w}", "same tbh",
    "honestly the {w} lore is the best part", "can we get a {w} channel", "first time here, hi all",
    "how do i get the citizen role?", "the {w} treaty is unfair to the north", "long live the {w}!",
)
_BATTLE = ("!attack {w}", "!defend the {w}", "!move troops to {w}", "!fortify {w}", "!retreat", "!charge",
           "i hold the {w} line", "flanking from the {w}", "reinforcements to {w}!", "!scout {w}")
_LEGIT_LINKS = ("https://www.youtube.com/watch?v={id}", "https://tenor.com/view/{w}-gif-{id}",
                "https://github.com/{w}/{w2}", "https://en.wikipedia.org/wiki/{W}", "https://imgur.com/{id}",
                "https://open.spotify.com/track/{id}", "https://x.com/{w}/status/{id}",
                "https://discord.com/channels/1/101/{id}", "https://docs.google.com/document/d/{id}")
_SCAM = (
    "free discord nitro for everyone, claim before it runs out {url}",
    "@everyone steam is giving away 50$ gift cards {url} first 100 people",
    "hey bro i accidentally reported your account, please verify here {url} or it will be banned",
    "Airdrop is live! connect your wallet to claim {url}",
    "who wants free nitro? {url} just login with discord",
    "i'm leaving discord, giving away my csgo skins {url}",
    "18+ leaked pics of the server girls {url}",
    "Get 3 months of nitro free {url} limited time offer",
    "check this out, made a new game, need testers, download {url}",
    "crypto signals group, 10x guaranteed {url}",
)
_SPAM = ("JOIN MY SERVER {url}", "buy cheap followers and boosts at {url}", "selling accounts dm me",
         "FREE ROBUX FREE ROBUX FREE ROBUX", "spam spam spam lol get raided", "this server is dead join {url}",
         "{pings} wake up", "nuked by the raid squad lol", "subscribe to my channel {url} {url}")
_BRANDS = ("discord", "steamcommunity", "nitro", "steampowered", "discordapp")


def _lookalike(rng: random.Random) -> str:
    brand = rng.choice(_BRANDS)
    style = rng.random()
    if style < 0.3:                                    # one-letter swap / homoglyph-ish
        i = rng.randrange(1, len(brand) - 1)
        swap = {"i": "l", "o": "0", "c": "k", "m": "rn", "s": "z", "e": "3", "a": "4"}.get(brand[i], "x")
        label = brand[:i] + swap + brand[i + 1:]
    elif style < 0.6:
        label = f"{brand}-{rng.choice(('gift', 'nitro', 'promo', 'free', 'claim', 'verify', 'event'))}"
    elif style < 0.8:
        label = f"{rng.choice(('free', 'get', 'claim', 'gift'))}{brand}"
    else:                                              # an unknown domain: no brand at all, only the lure gives it away
        label = "".join(rng.choice("bcdfghklmnprstvz") + rng.choice("aeiou") for _ in range(rng.randint(3, 5)))
    tld = rng.choice(("com", "gift", "ru", "xyz", "click", "site", "online", "store", "shop", "app", "gg"))
    path = rng.choice(("", "/claim", "/gift", f"/{rng.randrange(10**6)}", "/login", "/airdrop"))
    return f"https://{label}.{tld}{path}"


@dataclass
class Member:
    id: int
    created_at: float
    joined_at: float
    has_avatar: bool
    name: str
    tier: int = 0
    activity: float = 1.0


@dataclass
class Day:
    events: list
    attacks: list = field(default_factory=list)        # dicts: family, start, end, accounts, messages, actor
    normal: list = field(default_factory=list)         # (event name, start, end)
    start: float = 0.0
    members: dict = field(default_factory=dict)


class Server:
    """A persistent simulated community; `simulate_day()` advances it one day at a time."""

    def __init__(self, seed: int, members: int = 1200, start: float = 1_780_000_000.0):
        self.rng = random.Random(seed)
        self.t0 = start - start % 86400
        self.day_index = 0
        self.next_id = 10_000
        self.members: dict[int, Member] = {}
        self.staff: list[Member] = []
        self.msg_ids = iter(range(10**9, 10**12))
        self.tz_shift = self.rng.uniform(0, 24)          # when this community is awake
        self.base_rate = self.rng.uniform(2.0, 5.0) * members / 1000       # messages per minute at peak
        self.join_rate = self.rng.uniform(8, 30) * members / 1000          # joins per day
        for _ in range(members):
            self._add_member(start - self.rng.uniform(1, 900) * 86400)
        for tier, count in ((Tier.HELPER, 4), (Tier.MODERATOR, 4), (Tier.SENIOR, 2), (Tier.ADMIN, 2)):
            for _ in range(count):
                m = self.rng.choice(list(self.members.values()))
                m.tier = int(tier)
                self.staff.append(m)

    # ------------------------------------------------------------------ population
    def _name(self, raid_stem: Optional[str] = None) -> str:
        rng = self.rng
        if raid_stem:
            return f"{raid_stem}{rng.randrange(100, 99999)}"
        first = rng.choice(("ari", "Bima", "citra", "Dewa", "eka", "Fajar", "gita", "Hana", "Indra", "joko", "Kirana",
                            "Lukas", "mira", "Nadia", "oscar", "Putri", "Rizky", "sari", "Tomas", "Wulan", "yusuf",
                            "Zara", "Leo", "max", "Nova", "kai", "ash", "Rhea"))
        style = rng.random()
        if style < 0.3:
            return first
        if style < 0.55:
            return f"{first}{rng.randrange(1, 9999)}"
        if style < 0.75:
            return f"{first}_{rng.choice(_WORDS)}"
        return f"{rng.choice(('the', 'not', 'just', 'xx', 'its'))}{first.title()}{rng.choice(('', '_', '.'))}{rng.randrange(100)}"

    def _add_member(self, joined_at: float, created_at: Optional[float] = None, avatar: Optional[bool] = None,
                    name: Optional[str] = None) -> Member:
        uid = self.next_id
        self.next_id += 1
        created = created_at if created_at is not None else joined_at - self.rng.lognormvariate(math.log(500), 1.0) * 86400
        m = Member(uid, created, joined_at, self.rng.random() < 0.85 if avatar is None else avatar,
                   name or self._name(), activity=self.rng.paretovariate(1.6))
        self.members[uid] = m
        return m

    # ------------------------------------------------------------------ helpers
    def _activity(self, ts: float) -> float:
        hour = ((ts % 86400) / 3600 + self.tz_shift) % 24
        return 0.12 + 0.88 * max(0.0, math.sin(math.pi * (hour - 7) / 16)) ** 1.5 if 7 <= hour <= 23 else 0.12

    def _text(self, channel: int, link_p: float = 0.04) -> tuple[str, int]:
        rng = self.rng
        w, w2 = rng.choice(_WORDS), rng.choice(_WORDS)
        if channel in RELAXED:
            text = rng.choice(_BATTLE).format(w=w)
        else:
            text = rng.choice(_CHAT).format(w=w, w2=w2)
        mentions = 0
        if rng.random() < link_p:
            text += " " + rng.choice(_LEGIT_LINKS).format(w=w, w2=w2, W=w.title(), id=rng.randrange(10**8))
        if rng.random() < 0.08:
            mentions = rng.choice((1, 1, 1, 2))
        return text, mentions

    def _msg(self, ts: float, author: Member, channel: int, text: str, mentions: int = 0, **kw) -> Message:
        return Message(GUILD, channel, next(self.msg_ids), author.id, ts, content=text, author_tier=author.tier,
                       joined_at=author.joined_at, created_at=author.created_at, user_mentions=mentions, **kw)

    def _authors(self, k: int) -> list[Member]:
        pool = list(self.members.values())
        weights = [m.activity for m in pool]
        return self.rng.choices(pool, weights=weights, k=k)

    # ------------------------------------------------------------------ one day
    def simulate_day(self, attacks: tuple[str, ...] = (), normal_events: Optional[tuple[str, ...]] = None) -> Day:
        rng = self.rng
        start = self.t0 + self.day_index * 86400
        self.day_index += 1
        day = Day([], start=start)
        events = day.events
        channel_weights = [(101, 5), (102, 2), (103, 1.5), (105, 2), (106, 1)]
        # ---------------------------------------------------------------- normal chatter
        for minute in range(1440):
            ts = start + minute * 60
            lam = self.base_rate * self._activity(ts)
            for _ in range(_poisson(rng, lam)):
                author = self._authors(1)[0]
                channel = rng.choices([c for c, _ in channel_weights], [w for _, w in channel_weights])[0]
                text, mentions = self._text(channel)
                events.append(self._msg(ts + rng.uniform(0, 60), author, channel, text, mentions,
                                        is_reply=rng.random() < 0.2))
        # ---------------------------------------------------------------- normal joins
        for _ in range(_poisson(rng, self.join_rate)):
            ts = start + rng.uniform(0, 86400)
            young = rng.random() < 0.2
            m = self._add_member(ts, created_at=ts - (rng.uniform(0.02, 6) if young else rng.lognormvariate(math.log(400), 1)) * 86400)
            events.append(MemberJoin(GUILD, m.id, ts, m.created_at, has_avatar=m.has_avatar, name=m.name,
                                     invite_code=rng.choice(("public", "public", "partner1", None))))
            if rng.random() < 0.5:
                events.append(self._msg(ts + rng.uniform(30, 900), m, 101, rng.choice(("hi!", "hello everyone",
                                                                                         "hey :)", "glad to be here"))))
        # ---------------------------------------------------------------- staff housekeeping
        for _ in range(_poisson(rng, 2.0)):
            ts = start + rng.uniform(0, 86400)
            actor = rng.choice([s for s in self.staff if s.tier >= Tier.MODERATOR])
            action = rng.choice(("ban", "kick", "channel_update", "member_role_update", "role_update", "message_bulk_delete"))
            events.append(AuditEntry(GUILD, action, actor.id, ts, target_id=rng.randrange(10**6), actor_tier=actor.tier))
        if normal_events is None:
            normal_events = tuple(e for e in NORMAL_EVENTS if rng.random() < {"announcement": 0.35, "battle": 0.4,
                                  "onboarding_wave": 0.12, "celebration": 0.2, "staff_cleanup": 0.15,
                                  "restructure": 0.08}[e])
        for name in normal_events:
            getattr(self, f"_normal_{name}")(day, start + rng.uniform(3600, 82800))
        for family in attacks:
            getattr(self, f"_attack_{family}")(day, start + rng.uniform(1800, 84600))
        events.sort(key=lambda e: e.ts)
        day.members = self.members
        return day

    # ------------------------------------------------------------------ normal hard cases
    def _normal_announcement(self, day: Day, t: float) -> None:
        rng = self.rng
        admin = rng.choice([s for s in self.staff if s.tier >= Tier.SENIOR])
        day.events.append(self._msg(t, admin, 104, "@everyone the summer festival starts now! join us in general",
                                    everyone_attempt=True))
        n = rng.randint(40, 160)
        for author in self._authors(n):
            ts = t + rng.expovariate(1 / 240)
            day.events.append(self._msg(ts, author, 101, rng.choice(("lets goooo", "yay!!", "hype", "finally!!",
                                                                    "see you there", "🎉🎉", "woo"))))
        for _ in range(rng.randint(3, 15)):
            ts = t + rng.uniform(0, 3600)
            m = self._add_member(ts)
            day.events.append(MemberJoin(GUILD, m.id, ts, m.created_at, has_avatar=m.has_avatar, name=m.name,
                                         invite_code="festival"))
        day.normal.append(("announcement", t, t + 1800))

    def _normal_battle(self, day: Day, t: float) -> None:
        rng = self.rng
        fighters = self._authors(rng.randint(8, 30))
        duration = rng.uniform(1200, 3600)
        n = int(duration / rng.uniform(1.5, 5))
        for _ in range(n):
            author = rng.choice(fighters)
            text = rng.choice(_BATTLE).format(w=rng.choice(_WORDS))
            day.events.append(self._msg(t + rng.uniform(0, duration), author, 102, text,
                                        mentions=rng.choice((0, 0, 1, 2, 3))))
        day.normal.append(("battle", t, t + duration))

    def _normal_onboarding_wave(self, day: Day, t: float) -> None:
        rng = self.rng
        n = rng.randint(25, 90)
        span = rng.uniform(1800, 7200)
        for _ in range(n):
            ts = t + rng.uniform(0, span)
            young = rng.random() < 0.25
            m = self._add_member(ts, created_at=ts - (rng.uniform(0.5, 10) if young else rng.uniform(60, 2000)) * 86400)
            day.events.append(MemberJoin(GUILD, m.id, ts, m.created_at, has_avatar=m.has_avatar, name=m.name,
                                         invite_code="partner2"))
            if rng.random() < 0.6:
                day.events.append(self._msg(ts + rng.uniform(20, 600), m, 101, rng.choice(
                    ("hi from the partner server!", "hello!", "hey all", "came from the alliance post", "greetings"))))
        day.normal.append(("onboarding_wave", t, t + span))

    def _normal_celebration(self, day: Day, t: float) -> None:
        rng = self.rng
        who = rng.choice(("Raditya", "the governor", "Citra", "our founder", "Bima"))
        for author in self._authors(rng.randint(15, 60)):
            text = rng.choice((f"happy birthday {who}!!", f"Happy birthday {who}! have a great day",
                               f"hbd {who} 🎂", f"happy birthday {who} 🎉🎉"))
            day.events.append(self._msg(t + rng.expovariate(1 / 300), author, 101, text, mentions=rng.choice((0, 1))))
        day.normal.append(("celebration", t, t + 1800))

    def _normal_staff_cleanup(self, day: Day, t: float) -> None:
        rng = self.rng
        senior = rng.choice([s for s in self.staff if s.tier >= Tier.SENIOR])
        for k in range(rng.randint(3, 8)):
            day.events.append(AuditEntry(GUILD, "ban", senior.id, t + k * rng.uniform(30, 120),
                                         target_id=rng.randrange(10**6), actor_tier=senior.tier, reason="spam account"))
        day.normal.append(("staff_cleanup", t, t + 900))

    def _normal_restructure(self, day: Day, t: float) -> None:
        rng = self.rng
        admin = rng.choice([s for s in self.staff if s.tier >= Tier.ADMIN])
        for k in range(rng.randint(6, 14)):
            action = rng.choice(("role_update", "channel_update", "overwrite_update", "role_create", "channel_create"))
            day.events.append(AuditEntry(GUILD, action, admin.id, t + k * rng.uniform(60, 240),
                                         target_id=rng.randrange(10**6), actor_tier=admin.tier,
                                         perms_before=0, perms_after=0))
        day.normal.append(("restructure", t, t + 2400))

    # ------------------------------------------------------------------ attacks
    def _record(self, day: Day, family: str, start: float, end: float, accounts=(), messages=(), actor=None,
                actions: int = 0) -> None:
        day.attacks.append({"family": family, "start": start, "end": end, "accounts": set(accounts),
                            "messages": set(messages), "actor": actor, "actions": actions})

    def _raiders(self, t: float, n: int, span: float, stem: Optional[str] = None) -> list[Member]:
        rng = self.rng
        stem = stem or rng.choice(("freebie", "raidr", "xNuke", "giftbot", "user", "spm", "nitroo"))
        same_age = t - rng.uniform(0.05, 3) * 86400
        out = []
        for _ in range(n):
            ts = t + rng.uniform(0, span)
            m = self._add_member(ts, created_at=same_age + rng.uniform(-6, 6) * 3600, avatar=rng.random() < 0.3,
                                 name=self._name(stem) if rng.random() < 0.8 else None)
            out.append(m)
        return out

    def _attack_join_raid(self, day: Day, t: float) -> None:
        rng = self.rng
        n, span = rng.randint(25, 250), rng.uniform(60, 600)
        raiders = self._raiders(t, n, span)
        url = _lookalike(rng) if rng.random() < 0.6 else f"discord.gg/{rng.choice(('raid', 'free', 'nsfw'))}{rng.randrange(999)}"
        msgs, end = [], t
        spam_share = rng.uniform(0.3, 0.8)
        for m in raiders:
            day.events.append(MemberJoin(GUILD, m.id, m.joined_at, m.created_at, has_avatar=m.has_avatar, name=m.name,
                                         invite_code="public"))
            end = max(end, m.joined_at)
            if rng.random() < spam_share:
                for _ in range(rng.randint(1, 4)):
                    ts = m.joined_at + rng.uniform(5, 240)
                    text = rng.choice(_SPAM).format(url=url, pings=" ".join("<@1>" for _ in range(3)))
                    msg = self._msg(ts, m, rng.choice((101, 103, 105)), text, mentions=rng.choice((0, 0, 3, 8)))
                    day.events.append(msg)
                    msgs.append(msg.message_id)
                    end = max(end, ts)
        self._record(day, "join_raid", t, end, [m.id for m in raiders], msgs)

    def _attack_spam_raid(self, day: Day, t: float) -> None:
        rng = self.rng
        n = rng.randint(6, 40)
        accounts = self._raiders(t - rng.uniform(600, 40000), n, 600)
        base = rng.choice(_SPAM)
        url = _lookalike(rng) if rng.random() < 0.5 else ""
        msgs, end = [], t
        for m in accounts:
            for _ in range(rng.randint(3, 12)):
                ts = t + rng.uniform(0, 300)
                text = base.format(url=url, pings=" ".join("<@2>" for _ in range(rng.randint(2, 6))))
                if rng.random() < 0.5:
                    text += " " + rng.choice(("!!", "lol", "xd", str(rng.randrange(99))))
                msg = self._msg(ts, m, rng.choice((101, 102, 103, 105, 106)), text, mentions=rng.choice((0, 2, 5)))
                day.events.append(msg)
                msgs.append(msg.message_id)
                end = max(end, ts)
        self._record(day, "spam_raid", t, end, [m.id for m in accounts], msgs)

    def _attack_phishing(self, day: Day, t: float) -> None:
        rng = self.rng
        victims = [m for m in self._authors(rng.randint(1, 6)) if m.tier == 0] or self._authors(1)
        template, url = rng.choice(_SCAM), _lookalike(rng)
        msgs, end = [], t
        for m in victims:
            for _ in range(rng.randint(1, 5)):
                ts = t + rng.uniform(0, 1800)
                msg = self._msg(ts, m, rng.choice((101, 103, 105, 106)), template.format(url=url),
                                everyone_attempt="@everyone" in template)
                day.events.append(msg)
                msgs.append(msg.message_id)
                end = max(end, ts)
        self._record(day, "phishing", t, end, [m.id for m in victims], msgs)

    def _attack_mention_bomb(self, day: Day, t: float) -> None:
        rng = self.rng
        accounts = self._raiders(t - 300, rng.randint(1, 5), 200)
        msgs, end = [], t
        for m in accounts:
            for _ in range(rng.randint(1, 4)):
                ts = t + rng.uniform(0, 120)
                k = rng.randint(10, 50)
                msg = self._msg(ts, m, rng.choice((101, 105)), " ".join(f"<@{i}>" for i in range(k)), mentions=k)
                day.events.append(msg)
                msgs.append(msg.message_id)
                end = max(end, ts)
        self._record(day, "mention_bomb", t, end, [m.id for m in accounts], msgs)

    def _attack_staff_nuke(self, day: Day, t: float) -> None:
        rng = self.rng
        actor = rng.choice([s for s in self.staff if s.tier >= Tier.MODERATOR])
        n, span = rng.randint(5, 30), rng.uniform(60, 600)
        times = sorted(t + rng.uniform(0, span) for _ in range(n))
        for ts in times:
            action = rng.choice(("channel_delete", "channel_delete", "role_delete", "ban", "ban", "webhook_create",
                                 "kick"))
            day.events.append(AuditEntry(GUILD, action, actor.id, ts, target_id=rng.randrange(10**6),
                                         actor_tier=actor.tier))
        self._record(day, "staff_nuke", t, times[-1], [actor.id], actor=actor.id, actions=n)

    def _attack_malicious_bot(self, day: Day, t: float) -> None:
        rng = self.rng
        admin = rng.choice([s for s in self.staff if s.tier >= Tier.ADMIN])
        bot_id = 5_000_000 + rng.randrange(10**5)
        day.events.append(AuditEntry(GUILD, "bot_add", admin.id, t, target_id=bot_id, actor_tier=admin.tier))
        n = rng.randint(5, 25)
        times = sorted(t + rng.uniform(20, 400) for _ in range(n))
        for ts in times:
            action = rng.choice(("channel_delete", "role_delete", "ban", "ban", "webhook_create", "channel_create"))
            day.events.append(AuditEntry(GUILD, action, bot_id, ts, target_id=rng.randrange(10**6), actor_bot=True,
                                         actor_tier=0))
        self._record(day, "malicious_bot", t, times[-1], [bot_id], actor=bot_id, actions=n)

    def _attack_webhook_spam(self, day: Day, t: float) -> None:
        rng = self.rng
        hook = 7_000_000 + rng.randrange(10**5)
        actor = rng.choice(self.staff)
        day.events.append(AuditEntry(GUILD, "webhook_create", actor.id, t - 30, target_id=hook, actor_tier=actor.tier))
        msgs, end = [], t
        for _ in range(rng.randint(10, 80)):
            ts = t + rng.uniform(0, 180)
            msg = Message(GUILD, rng.choice((101, 104)), next(self.msg_ids), hook, ts,
                          content="@everyone " + rng.choice(_SCAM).format(url=_lookalike(rng)), author_bot=True,
                          webhook_id=hook, everyone_attempt=True)
            day.events.append(msg)
            msgs.append(msg.message_id)
            end = max(end, ts)
        self._record(day, "webhook_spam", t, end, [hook], msgs)

    def _attack_slow_drip(self, day: Day, t: float) -> None:
        rng = self.rng
        n = rng.randint(6, 20)
        span = rng.uniform(3 * 3600, 10 * 3600)
        raiders = self._raiders(t, n, span)
        template = rng.choice(_SCAM)
        msgs, end = [], t
        for m in raiders:
            day.events.append(MemberJoin(GUILD, m.id, m.joined_at, m.created_at, has_avatar=m.has_avatar, name=m.name,
                                         invite_code="public"))
            for _ in range(rng.randint(1, 2)):
                ts = m.joined_at + rng.uniform(600, 5400)
                msg = self._msg(ts, m, rng.choice((101, 103, 105)), template.format(url=_lookalike(rng)))
                day.events.append(msg)
                msgs.append(msg.message_id)
                end = max(end, ts)
        self._record(day, "slow_drip", t, end, [m.id for m in raiders], msgs)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(rng.gauss(lam, math.sqrt(lam)) + 0.5))
    limit, k, p = math.exp(-lam), 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def label_at(day: Day, ts: float, pad: float = 120.0) -> Optional[str]:
    """The attack family active at `ts` (from its first event until `pad` seconds after its last), or None."""
    for a in day.attacks:
        if a["start"] <= ts <= a["end"] + pad:
            return a["family"]
    return None


def schedule(rng: random.Random, days: int, attack_rate: float = 0.6, families=FAMILIES) -> list[tuple[str, ...]]:
    """Which attacks happen on each day: some quiet days, some with one or two attacks."""
    out = []
    for _ in range(days):
        if rng.random() < attack_rate:
            k = 1 if rng.random() < 0.8 else 2
            out.append(tuple(rng.sample(families, k)))
        else:
            out.append(())
    return out
