"""A fake Discord for command-level tests: users, interactions, channels, guilds and scripted randomness."""
import asyncio
import random
from types import SimpleNamespace


class FakeUser:
    def __init__(self, id, name="user", admin=False, roles=()):
        self.id, self.name, self.display_name, self.global_name = id, name, name, name
        self.mention, self.bot = f"<@{id}>", False
        self.display_avatar = SimpleNamespace(url=f"https://cdn.example/{id}.png")
        self.guild_permissions = SimpleNamespace(administrator=admin)
        self.roles = list(roles)
        self.dms = []

    async def send(self, **message):
        self.dms.append(message)


class FakeMessage:
    _ids = iter(range(5000, 10**9))

    def __init__(self, channel=None, **content):
        self.id, self.channel, self.content = next(FakeMessage._ids), channel, content
        self.edits = []

    async def edit(self, **kw):
        self.edits.append(kw)


class FakeChannel:
    """A text channel that records what is sent to it."""
    def __init__(self, id=777):
        self.id, self.mention = id, f"<#{id}>"
        self.sent: list[FakeMessage] = []

    async def send(self, content=None, **kw):
        message = FakeMessage(self, content=content, **kw)
        self.sent.append(message)
        return message

    def get_partial_message(self, message_id):
        return next(m for m in self.sent if m.id == message_id)


class FakeResponse:
    def __init__(self, log, stop_first=False):
        self.log, self.done, self.stop_first = log, False, stop_first

    def is_done(self): return self.done

    async def send_message(self, *a, **k):
        self.log.append(("send", k)); self.done = True

    async def defer(self, *a, **k): self.log.append(("defer", k)); self.done = True
    async def edit_message(self, **k): self.log.append(("edit_message", k)); self.done = True
    async def send_modal(self, modal): self.log.append(("modal", {"modal": modal})); self.done = True


class FakeFollowup:
    def __init__(self, log): self.log = log
    async def send(self, **k): self.log.append(("followup", k)); return FakeMessage()


class FakeGuild:
    default_role = "everyone"

    def __init__(self, id=1, channels=None, members=None, roles=None):
        self.id = id
        self._channels, self._members, self._roles = channels or {}, members or {}, roles or {}

    def get_channel(self, cid): return self._channels.get(cid)
    def get_member(self, uid): return self._members.get(uid)
    def get_role(self, rid): return self._roles.get(rid)


class FakeInteraction:
    """Records everything the bot says. `log` holds (kind, kwargs) tuples in order."""
    def __init__(self, bot, user, guild=None):
        self.client, self.user, self.guild = bot, user, guild
        self.guild_id = getattr(guild, "id", None)
        self.channel = None
        self.data = {}
        self.log = []
        self.response, self.followup = FakeResponse(self.log), FakeFollowup(self.log)
        self.command = None

    async def edit_original_response(self, **k): self.log.append(("edit", k)); return FakeMessage()

    # ------------------------------------------------------------ convenience
    @property
    def embeds(self):
        return [k["embed"] for _, k in self.log if k.get("embed") is not None]

    @property
    def last_embed(self):
        return self.embeds[-1]

    def text(self):
        """All embed descriptions/titles/fields joined, for substring assertions."""
        parts = []
        for e in self.embeds:
            parts += [e.title or "", e.description or ""] + [f"{f.name} {f.value}" for f in e.fields]
        return "\n".join(parts)

    def ephemeral(self):
        return [k.get("ephemeral") for _, k in self.log if "embed" in k]


class ScriptedRng(random.Random):
    """Deterministic stand-in for the bot's RNG: queue the exact rolls / reels you want."""
    def __init__(self, rolls=(), reels=(), seed=0):
        super().__init__(seed)
        self.rolls, self.reels = list(rolls), list(reels)

    def randrange(self, *args, **kwargs):
        return self.rolls.pop(0) if self.rolls else super().randrange(*args, **kwargs)

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        if self.reels:
            return [population[self.reels.pop(0)]]
        return super().choices(population, weights, cum_weights=cum_weights, k=k)


def fast_sleep_patch():
    """Replace asyncio.sleep so tests don't wait; returns the original to restore."""
    original = asyncio.sleep

    async def fast(delay=0, result=None, *a, **k):
        await original(0)
        return result
    asyncio.sleep = fast
    return original
