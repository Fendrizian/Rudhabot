"""Parsing of the 'a number or all' amounts accepted by money commands."""
from __future__ import annotations


class AmountError(ValueError):
    """`code` is 'invalid' (not a number / 'all') or 'not_positive'."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_amount(text: str, all_value: int) -> int:
    """'all' (any case) means `all_value`; otherwise an integer. Must end up positive.

    Whether the result exceeds what the player owns is for the caller to decide, because each command words
    that refusal differently.
    """
    if isinstance(text, str) and text.lower() == "all":
        amount = all_value
    else:
        try:
            amount = int(text)
        except (TypeError, ValueError):
            raise AmountError("invalid") from None
    if amount <= 0:
        raise AmountError("not_positive")
    return amount
