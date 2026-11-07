"""A simulated payment provider. No real money moves, and no card number is ever stored.

Any number passing the Luhn check is approved, except the decline test card.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

APPROVED_TEST_CARD = "4242424242424242"
DECLINED_TEST_CARD = "4000000000000002"


class PaymentDeclined(Exception):
    """The charge was refused. The message is safe to show to the user."""


@dataclass(frozen=True)
class Charge:
    reference: str
    amount_cents: int
    last4: str


def _luhn_valid(digits: str) -> bool:
    total = 0
    for position, digit in enumerate(reversed(digits)):
        value = int(digit)
        if position % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def charge(card_number: str, amount_cents: int) -> Charge:
    """Charge a card, or raise PaymentDeclined."""
    digits = re.sub(r"[\s-]", "", card_number or "")
    if not re.fullmatch(r"\d{12,19}", digits) or not _luhn_valid(digits):
        raise PaymentDeclined("That card number is not valid.")
    if digits == DECLINED_TEST_CARD:
        raise PaymentDeclined("The card was declined.")
    return Charge(f"sim_{secrets.token_hex(8)}", amount_cents, digits[-4:])
