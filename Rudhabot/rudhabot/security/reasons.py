"""Reason codes: every alert and action says *what was observed*, in plain language, never just a score.

`explain(code, **facts)` renders one; unknown facts are left out rather than failing.
"""
from __future__ import annotations

import string

REASONS: dict[str, str] = {
    # joins
    "JOIN_SURGE": "{count} joins in {window}, about {ratio}x this server's usual rate for this time",
    "JOIN_COHORT": "{count} accounts joined within {window} that look alike ({traits})",
    "JOIN_NEW_ACCOUNT": "account created {age} before joining",
    "JOIN_SPAMMER_FLAG": "Discord has flagged this account as a likely spammer",
    "JOIN_BAN_EVASION": "profile matches a banned account ({traits}); a possible match, not proof",
    # messages
    "MSG_FLOOD": "{count} messages in {window}",
    "MSG_DUPLICATE": "the same message {count} times in {window}",
    "MSG_COORDINATED": "{accounts} accounts posted near-identical messages within {window} (similarity {similarity})",
    "MSG_MENTION_BOMB": "mentioned {count} members in one message",
    "MSG_MENTION_FLOOD": "mentioned {count} members in {window}",
    "MSG_EVERYONE_ATTEMPT": "tried to mention @everyone/@here without permission",
    "MSG_INVITE": "posted an invite to another server",
    "MSG_CHANNEL_SURGE": "{count} messages in {window} in this channel, about {ratio}x its usual rate",
    "MSG_NEW_ACCOUNT_LINK": "posted a link {age} after joining",
    "MSG_REPEAT_OFFENDER": "{count} messages removed in the last {window}",
    "MSG_MODEL_SCAM": "the message model rates this as likely scam or phishing ({confidence})",
    "MSG_MODEL_SPAM": "the message model rates this as likely spam ({confidence})",
    "MSG_MODEL_UNSURE": "the message model is unsure about this message ({confidence}); left for review",
    "MSG_REPORTED": "reported by {count} member(s)",
    # links
    "LINK_DENYLIST": "links to a domain on this server's deny list ({domain})",
    "LINK_IP_LOGGER": "links to a known IP-logging site ({domain})",
    "LINK_HOMOGLYPH": "link uses look-alike letters to imitate {brand} ({domain})",
    "LINK_LOOKALIKE": "link domain is a misspelling of {brand} ({domain})",
    "LINK_BRAND_IN_DOMAIN": "link uses the {brand} name on a domain {brand} doesn't own ({domain})",
    "LINK_BRAND_SUBDOMAIN": "link hides a {brand} address inside another domain ({domain})",
    "LINK_BRAND_OTHER_TLD": "link uses the {brand} name on an unofficial domain ending ({domain})",
    "LINK_USERINFO": "link uses the 'name@host' trick so it looks like a different site",
    "LINK_PUNYCODE": "link domain uses encoded international characters ({domain})",
    "LINK_IP_HOST": "link points at a raw IP address",
    "LINK_SHORTENER": "link goes through a URL shortener, hiding where it leads ({domain})",
    "LINK_WEAK_TLD": "link uses a domain ending often abused for throwaway sites ({domain})",
    "LINK_ODD_PORT": "link uses an unusual port",
    "LINK_LONG_HOST": "link has an unusually long or deep host name",
    "LINK_MASKED_MISMATCH": "the link text shows one site but the link goes to another",
    "LURE_MONEY": "pairs a link with a free-gift / money lure",
    "LURE_ACCOUNT": "pairs a link with an account-threat or 'verify now' lure",
    "LURE_ADULT": "pairs a link with an adult-content lure",
    # files
    "FILE_EXECUTABLE": "attached a program or script file ({filename})",
    "FILE_DOUBLE_EXTENSION": "file name disguises a program as a document or image ({filename})",
    "FILE_BIDI_TRICK": "file name uses a right-to-left character to disguise its type",
    "FILE_TYPE_MISMATCH": "file's declared type doesn't match its name ({filename})",
    "FILE_ARCHIVE": "attached an archive, whose contents can't be checked",
    # webhooks and bots
    "WEBHOOK_FLOOD": "a webhook posted {count} messages in {window}",
    "WEBHOOK_CREATED": "a webhook was created by {actor}",
    "BOT_ADDED": "a bot was added by {actor}",
    "BOT_DANGEROUS": "a newly added bot holds dangerous permissions ({permissions})",
    # anti-nuke
    "NUKE_BUDGET": "{actor} made {count} high-impact changes in {window} ({actions}), over their limit of {budget}",
    "NUKE_WARNING": "{actor} has used {used} of their {budget}-point limit for high-impact changes in {window}",
    "PERM_ESCALATION_BROAD": "{actor} gave {target} dangerous permissions ({permissions})",
    "ROLE_CREATED_DANGEROUS": "{actor} created a role with dangerous permissions ({permissions})",
    "PERM_ESCALATION_GRANT": "{actor} gave a member a role with dangerous permissions ({permissions})",
    "PERM_OVERWRITE_DANGEROUS": "{actor} gave {target} dangerous permissions in a channel ({permissions})",
    "CONFIG_WEAKENED": "{actor} weakened a server safety setting ({setting}: {before} -> {after})",
    "AUTOMOD_REMOVED": "{actor} removed or disabled a native AutoMod rule",
    "STAFF_UNUSUAL": "{actor} is acting far outside their own usual pattern ({detail})",
    # incident model / ranking
    "MODEL_INCIDENT": "the incident model sees a likely {family} pattern ({confidence})",
    "MODEL_UNSURE": "the incident model is unsure ({confidence}); deterministic rules decide",
    # manual
    "STAFF_REPORT": "raised by {actor}",
}


class _Facts(dict):
    def __missing__(self, key):
        return "?"


def explain(code: str, **facts) -> str:
    template = REASONS.get(code)
    if template is None:
        return code.replace("_", " ").lower()
    return string.Formatter().vformat(template, (), _Facts({k: _fmt(v) for k, v in facts.items()}))


def _fmt(value):
    if isinstance(value, float):
        return f"{value:.0%}" if 0 <= value <= 1 else f"{value:.1f}"
    return value


def duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    if seconds < 86400 * 2:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} days"
