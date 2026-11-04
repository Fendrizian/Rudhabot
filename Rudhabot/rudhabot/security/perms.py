"""Discord permission bits (the API's integer flags), so security logic can reason about permissions without discord.py.

`tests/test_security_core.py` checks every value against `discord.Permissions`.
"""
from __future__ import annotations

CREATE_INSTANT_INVITE = 1 << 0
KICK_MEMBERS = 1 << 1
BAN_MEMBERS = 1 << 2
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
MANAGE_GUILD = 1 << 5
ADD_REACTIONS = 1 << 6
VIEW_AUDIT_LOG = 1 << 7
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
MANAGE_MESSAGES = 1 << 13
EMBED_LINKS = 1 << 14
ATTACH_FILES = 1 << 15
MENTION_EVERYONE = 1 << 17
MANAGE_NICKNAMES = 1 << 27
MANAGE_ROLES = 1 << 28
MANAGE_WEBHOOKS = 1 << 29
MANAGE_EXPRESSIONS = 1 << 30
USE_APPLICATION_COMMANDS = 1 << 31
MANAGE_EVENTS = 1 << 33
MANAGE_THREADS = 1 << 34
CREATE_PUBLIC_THREADS = 1 << 35
CREATE_PRIVATE_THREADS = 1 << 36
SEND_MESSAGES_IN_THREADS = 1 << 38
MODERATE_MEMBERS = 1 << 40
SEND_POLLS = 1 << 49
USE_EXTERNAL_APPS = 1 << 50

#: Permissions that let one account damage the server or its members at scale.
DANGEROUS = (ADMINISTRATOR | MANAGE_GUILD | MANAGE_ROLES | MANAGE_CHANNELS | MANAGE_WEBHOOKS | BAN_MEMBERS
             | KICK_MEMBERS | MODERATE_MEMBERS | MENTION_EVERYONE | MANAGE_MESSAGES | MANAGE_EXPRESSIONS
             | MANAGE_THREADS | MANAGE_EVENTS | MANAGE_NICKNAMES | VIEW_AUDIT_LOG)

#: The subset that alone is enough to wreck the server (delete channels/roles, mass-ban, hand out admin).
CRITICAL = ADMINISTRATOR | MANAGE_GUILD | MANAGE_ROLES | MANAGE_CHANNELS | MANAGE_WEBHOOKS | BAN_MEMBERS

#: What a lockdown denies to ordinary members in a channel.
SPEAK = SEND_MESSAGES | SEND_MESSAGES_IN_THREADS | CREATE_PUBLIC_THREADS | CREATE_PRIVATE_THREADS | ADD_REACTIONS | SEND_POLLS

NAMES = {
    ADMINISTRATOR: "Administrator", MANAGE_GUILD: "Manage Server", MANAGE_ROLES: "Manage Roles",
    MANAGE_CHANNELS: "Manage Channels", MANAGE_WEBHOOKS: "Manage Webhooks", BAN_MEMBERS: "Ban Members",
    KICK_MEMBERS: "Kick Members", MODERATE_MEMBERS: "Timeout Members", MENTION_EVERYONE: "Mention @everyone",
    MANAGE_MESSAGES: "Manage Messages", MANAGE_EXPRESSIONS: "Manage Expressions", MANAGE_THREADS: "Manage Threads",
    MANAGE_EVENTS: "Manage Events", MANAGE_NICKNAMES: "Manage Nicknames", VIEW_AUDIT_LOG: "View Audit Log",
    SEND_MESSAGES: "Send Messages", VIEW_CHANNEL: "View Channel", CREATE_INSTANT_INVITE: "Create Invite",
    EMBED_LINKS: "Embed Links", ATTACH_FILES: "Attach Files", ADD_REACTIONS: "Add Reactions",
    SEND_MESSAGES_IN_THREADS: "Send Messages in Threads", CREATE_PUBLIC_THREADS: "Create Public Threads",
    CREATE_PRIVATE_THREADS: "Create Private Threads", SEND_POLLS: "Create Polls", USE_EXTERNAL_APPS: "Use External Apps",
    USE_APPLICATION_COMMANDS: "Use Application Commands",
}


def names(bits: int) -> list[str]:
    """Readable names of the set bits, most dangerous first."""
    order = [ADMINISTRATOR, MANAGE_GUILD, MANAGE_ROLES, MANAGE_CHANNELS, MANAGE_WEBHOOKS, BAN_MEMBERS, KICK_MEMBERS,
             MODERATE_MEMBERS, MENTION_EVERYONE, MANAGE_MESSAGES]
    out = [NAMES[b] for b in order if bits & b]
    rest = bits & ~sum(order)
    bit = 1
    while rest:
        if rest & 1:
            out.append(NAMES.get(bit, f"bit {bit.bit_length() - 1}"))
        rest >>= 1
        bit <<= 1
    return out


def dangerous(bits: int) -> int:
    return bits & DANGEROUS
