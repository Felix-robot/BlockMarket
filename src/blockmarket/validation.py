"""Fail-closed validation for Bot decisions."""

import re
from decimal import Decimal
from typing import Any

from .models import Quote
from .rules import Rules

PRICE_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")
QUANTITY_RE = re.compile(r"^[1-9][0-9]*$")
ACTION_KEYS = {"decision_seq", "bid", "ask"}
QUOTE_KEYS = {"price", "quantity"}


def _validate_quote(
    value: Any, rules: Rules, side: str
) -> tuple[Quote | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        return None, side + "_not_object"
    if set(value) != QUOTE_KEYS:
        return None, side + "_fields"
    raw_price = value.get("price")
    raw_quantity = value.get("quantity")
    if not isinstance(raw_price, str) or not PRICE_RE.fullmatch(raw_price):
        return None, side + "_price_format"
    if not isinstance(raw_quantity, str) or not QUANTITY_RE.fullmatch(raw_quantity):
        return None, side + "_quantity_format"
    parsed_price = Decimal(raw_price)
    quantity = int(raw_quantity)
    if parsed_price < rules.min_price or parsed_price > rules.max_price:
        return None, side + "_price_range"
    if quantity > rules.max_quote_quantity:
        return None, side + "_quantity_range"
    return Quote(parsed_price, quantity), None


def validate_action(
    value: Any, expected_seq: int, rules: Rules
) -> tuple[bool, str | None]:
    if not isinstance(value, dict):
        return False, "action_not_object"
    if set(value) != ACTION_KEYS:
        return False, "action_fields"
    seq = value.get("decision_seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        return False, "decision_seq_type"
    if seq != expected_seq:
        return False, "decision_seq_mismatch"
    bid, error = _validate_quote(value.get("bid"), rules, "bid")
    if error:
        return False, error
    ask, error = _validate_quote(value.get("ask"), rules, "ask")
    if error:
        return False, error
    if bid is not None and ask is not None and bid.price >= ask.price:
        return False, "crossed_quote"
    return True, None


def hold_action(decision_seq: int) -> dict[str, Any]:
    return {"decision_seq": decision_seq, "bid": None, "ask": None}


def normalize_action(value: Any, expected_seq: int, rules: Rules) -> dict[str, Any]:
    valid, error = validate_action(value, expected_seq, rules)
    return {
        "submitted": value,
        "valid": valid,
        "error": error,
        "effective": value if valid else hold_action(expected_seq),
    }


def quote_from_action(action: dict[str, Any], side: str) -> Quote | None:
    value = action[side]
    if value is None:
        return None
    return Quote(Decimal(value["price"]), int(value["quantity"]))
