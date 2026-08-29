"""Frozen constants for blockmarket-v1-prototype.2."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from typing import Any

getcontext().prec = 50
getcontext().rounding = ROUND_HALF_EVEN

RULESET = "blockmarket-v1-prototype.2"
SCHEMA_VERSION = "v1"
PLAYERS = ("A", "B")
ZERO = Decimal(0)
ONE = Decimal(1)
PRICE_TICK = Decimal("0.01")
MONEY_TICK = Decimal("0.00000001")
SCORE_TICK = Decimal("0.000000000001")


@dataclass(frozen=True)
class Rules:
    blocks: int = 300
    initial_cash: Decimal = Decimal("10000.00")
    initial_inventory: int = 50
    min_inventory: int = 0
    max_inventory: int = 100
    min_price: Decimal = Decimal("50.00")
    max_price: Decimal = Decimal("150.00")
    max_quote_quantity: int = 10
    fee_bps: Decimal = Decimal(5)
    reference_scale: Decimal = Decimal(8)
    informed_probability_bps: int = 1000
    signal_accuracy_bps: int = 6500
    min_customer_urgency: Decimal = Decimal("0.50")
    max_customer_urgency: Decimal = Decimal("3.50")
    hidden_reference_noise: Decimal = Decimal("5.00")
    henon_a: Decimal = Decimal("1.4")
    henon_b: Decimal = Decimal("0.3")
    max_abs_state: Decimal = Decimal(4)

    @property
    def fee_rate(self) -> Decimal:
        return self.fee_bps / Decimal(10000)

    def to_json(self) -> dict[str, Any]:
        return {
            "blocks": self.blocks,
            "initial_cash": money(self.initial_cash),
            "initial_inventory": str(self.initial_inventory),
            "inventory_range": [str(self.min_inventory), str(self.max_inventory)],
            "price_range": [price(self.min_price), price(self.max_price)],
            "price_tick": price(PRICE_TICK),
            "max_quote_quantity": str(self.max_quote_quantity),
            "fee_bps": decimal_text(self.fee_bps),
            "reference_scale": decimal_text(self.reference_scale),
            "informed_probability_bps": self.informed_probability_bps,
            "signal_accuracy_bps": self.signal_accuracy_bps,
            "customer_urgency_range": [
                price(self.min_customer_urgency),
                price(self.max_customer_urgency),
            ],
            "hidden_reference_noise": decimal_text(self.hidden_reference_noise),
            "decimal_precision": 50,
            "rounding": "ROUND_HALF_EVEN",
        }


def decimal_text(value: Decimal) -> str:
    """Render a Decimal without exponent notation or insignificant zeros."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def price(value: Decimal) -> str:
    return format(value.quantize(PRICE_TICK), ".2f")


def money(value: Decimal) -> str:
    return format(value.quantize(MONEY_TICK), ".8f")


def score(value: Decimal) -> str:
    return format(value.quantize(SCORE_TICK), ".12f")


def clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(max(value, lower), upper)
