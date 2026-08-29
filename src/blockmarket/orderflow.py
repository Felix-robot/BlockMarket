"""Deterministic second-order synthetic customer order flow."""

import hashlib
from decimal import ROUND_FLOOR, Decimal

from .models import CustomerOrder, EnvironmentState
from .rules import PRICE_TICK, Rules, clamp


class EnvironmentEscape(RuntimeError):
    pass


def _prf(orderflow_key: str, block_seq: int, lane: str) -> int:
    material = (
        bytes.fromhex(orderflow_key) + block_seq.to_bytes(8, "big") + lane.encode()
    )
    return int.from_bytes(hashlib.sha256(material).digest(), "big")


def reference_price(
    state: EnvironmentState, rules: Rules, orderflow_key: str, block_seq: int
) -> Decimal:
    noise_unit = Decimal(
        _prf(orderflow_key, block_seq, "reference-noise") % 2_000_001 - 1_000_000
    ) / Decimal(1_000_000)
    raw = (
        Decimal(100)
        + rules.reference_scale * state.x_cur
        + rules.hidden_reference_noise * noise_unit
    )
    return clamp(raw, rules.min_price, rules.max_price).quantize(PRICE_TICK)


def public_signal(
    state: EnvironmentState,
    next_state: EnvironmentState,
    rules: Rules,
    orderflow_key: str,
    block_seq: int,
) -> str:
    current_ref = reference_price(state, rules, orderflow_key, block_seq)
    next_ref = reference_price(next_state, rules, orderflow_key, block_seq + 1)
    actual = "UP" if next_ref >= current_ref else "DOWN"
    truthful = (
        _prf(orderflow_key, block_seq, "public-signal") % 10000
        < rules.signal_accuracy_bps
    )
    if truthful:
        return actual
    return "DOWN" if actual == "UP" else "UP"


def customer_order(
    state: EnvironmentState,
    next_state: EnvironmentState,
    rules: Rules,
    orderflow_key: str,
    block_seq: int,
) -> CustomerOrder:
    ref = reference_price(state, rules, orderflow_key, block_seq)
    next_ref = reference_price(next_state, rules, orderflow_key, block_seq + 1)
    informed = (
        _prf(orderflow_key, block_seq, "kind") % 10000 < rules.informed_probability_bps
    )
    kind = "informed" if informed else "noise"
    if informed and next_ref != ref:
        side = "buy" if next_ref > ref else "sell"
    else:
        side = "buy" if _prf(orderflow_key, block_seq, "side") % 2 == 0 else "sell"
    quantity = 1 + _prf(orderflow_key, block_seq, "quantity") % rules.max_quote_quantity
    urgency_steps = int(
        (
            (rules.max_customer_urgency - rules.min_customer_urgency) / PRICE_TICK
        ).to_integral_value(rounding=ROUND_FLOOR)
    )
    urgency = rules.min_customer_urgency + PRICE_TICK * Decimal(
        _prf(orderflow_key, block_seq, "urgency") % (urgency_steps + 1)
    )
    if informed:
        urgency = min(
            rules.max_customer_urgency,
            urgency + min(abs(next_ref - ref) / Decimal(4), Decimal("1.50")),
        )
    if side == "buy":
        reservation = ref + urgency
    else:
        reservation = ref - urgency
    reservation = clamp(reservation, rules.min_price, rules.max_price).quantize(
        PRICE_TICK
    )
    return CustomerOrder(kind, side, quantity, reservation)


def advance(state: EnvironmentState, rules: Rules) -> EnvironmentState:
    x_next = (
        Decimal(1)
        - rules.henon_a * state.x_cur * state.x_cur
        + rules.henon_b * state.x_prev
    )
    next_state = EnvironmentState(state.x_cur, x_next)
    if (
        abs(next_state.x_prev) > rules.max_abs_state
        or abs(next_state.x_cur) > rules.max_abs_state
    ):
        raise EnvironmentEscape("environment_state_escape")
    return next_state


def step(
    state: EnvironmentState, rules: Rules, orderflow_key: str, block_seq: int
) -> tuple[Decimal, str, CustomerOrder, EnvironmentState]:
    if (
        abs(state.x_prev) > rules.max_abs_state
        or abs(state.x_cur) > rules.max_abs_state
    ):
        raise EnvironmentEscape("environment_state_escape")
    next_state = advance(state, rules)
    return (
        reference_price(state, rules, orderflow_key, block_seq),
        public_signal(state, next_state, rules, orderflow_key, block_seq),
        customer_order(state, next_state, rules, orderflow_key, block_seq),
        next_state,
    )
