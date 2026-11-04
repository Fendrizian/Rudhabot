"""Keep secrets out of logs: Discord bot tokens, OpenAI keys and webhook URLs are replaced before a record is written,
whatever library logged them (main.py installs the filter on every root handler)."""
from __future__ import annotations

import logging
import re

PATTERNS = (
    (re.compile(r"[MNO][A-Za-z\d_-]{23,27}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,40}"), "[discord-token]"),
    (re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"), "[openai-key]"),
    (re.compile(r"(discord(?:app)?\.com/api/webhooks/\d+/)[A-Za-z0-9_-]{20,}"), r"\1[webhook-token]"),
    (re.compile(r"(?i)(authorization:\s*(?:bot|bearer)\s+)\S+"), r"\1[redacted]"),
)


def scrub(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretScrubber(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = scrub(message)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info and record.exc_text is None and record.exc_info[1] is not None:
            formatted = logging.Formatter().formatException(record.exc_info)
            if scrub(formatted) != formatted:
                record.exc_text = scrub(formatted)
        return True


def install(logger: logging.Logger | None = None) -> None:
    logger = logger or logging.getLogger()
    for handler in logger.handlers:
        if not any(isinstance(f, SecretScrubber) for f in handler.filters):
            handler.addFilter(SecretScrubber())
