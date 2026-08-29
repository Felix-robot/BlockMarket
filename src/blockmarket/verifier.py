"""Independent replay verifier.

This module intentionally does not import or call engine.py, orderflow.py, or
settlement.py. It duplicates the compact state transition and ledger rules so a
shared implementation bug is less likely to validate itself.
"""

import hashlib
from collections.abc import Mapping
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from .canonical import hash_payload, verify_event_hash
from .models import Account, CustomerOrder, EnvironmentState, Quote
from .rules import (
    MONEY_TICK,
    PLAYERS,
    PRICE_TICK,
    RULESET,
    Rules,
    clamp,
    money,
    price,
    score,
)
from .validation import normalize_action, quote_from_action


class VerificationError(ValueError):
    pass


def _fail(code: str) -> None:
    raise VerificationError(code)


def _rules_from_manifest(manifest: dict[str, Any]) -> Rules:
    raw = manifest["rules"]
    inventory_range = raw["inventory_range"]
    price_range = raw["price_range"]
    environment = manifest["environment"]
    rules = Rules(
        blocks=int(raw["blocks"]),
        initial_cash=Decimal(raw["initial_cash"]),
        initial_inventory=int(raw["initial_inventory"]),
        min_inventory=int(inventory_range[0]),
        max_inventory=int(inventory_range[1]),
        min_price=Decimal(price_range[0]),
        max_price=Decimal(price_range[1]),
        max_quote_quantity=int(raw["max_quote_quantity"]),
        fee_bps=Decimal(raw["fee_bps"]),
        reference_scale=Decimal(raw["reference_scale"]),
        informed_probability_bps=int(raw["informed_probability_bps"]),
        signal_accuracy_bps=int(raw["signal_accuracy_bps"]),
        min_customer_urgency=Decimal(raw["customer_urgency_range"][0]),
        max_customer_urgency=Decimal(raw["customer_urgency_range"][1]),
        hidden_reference_noise=Decimal(raw["hidden_reference_noise"]),
        henon_a=Decimal(environment["a"]),
        henon_b=Decimal(environment["b"]),
        max_abs_state=Decimal(environment["max_abs_state"]),
    )
    if raw != rules.to_json():
        _fail("manifest_rules_noncanonical")
    return rules


def _ref(
    state: EnvironmentState, rules: Rules, orderflow_key: str, block_seq: int
) -> Decimal:
    noise_unit = Decimal(
        _prf(orderflow_key, block_seq, "reference-noise") % 2_000_001 - 1_000_000
    ) / Decimal(1_000_000)
    return clamp(
        Decimal(100)
        + rules.reference_scale * state.x_cur
        + rules.hidden_reference_noise * noise_unit,
        rules.min_price,
        rules.max_price,
    ).quantize(PRICE_TICK)


def _prf(orderflow_key: str, block_seq: int, lane: str) -> int:
    material = (
        bytes.fromhex(orderflow_key) + block_seq.to_bytes(8, "big") + lane.encode()
    )
    return int.from_bytes(hashlib.sha256(material).digest(), "big")


def _order(
    state: EnvironmentState,
    next_state: EnvironmentState,
    rules: Rules,
    orderflow_key: str,
    block_seq: int,
) -> CustomerOrder:
    ref = _ref(state, rules, orderflow_key, block_seq)
    next_ref = _ref(next_state, rules, orderflow_key, block_seq + 1)
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
    reservation = ref + urgency if side == "buy" else ref - urgency
    reservation = clamp(reservation, rules.min_price, rules.max_price).quantize(
        PRICE_TICK
    )
    return CustomerOrder(kind, side, quantity, reservation)


def _signal(
    state: EnvironmentState,
    next_state: EnvironmentState,
    rules: Rules,
    orderflow_key: str,
    block_seq: int,
) -> str:
    current_ref = _ref(state, rules, orderflow_key, block_seq)
    next_ref = _ref(next_state, rules, orderflow_key, block_seq + 1)
    actual = "UP" if next_ref >= current_ref else "DOWN"
    truthful = (
        _prf(orderflow_key, block_seq, "public-signal") % 10000
        < rules.signal_accuracy_bps
    )
    if truthful:
        return actual
    return "DOWN" if actual == "UP" else "UP"


def _advance(state: EnvironmentState, rules: Rules) -> EnvironmentState:
    next_state = EnvironmentState(
        state.x_cur,
        Decimal(1)
        - rules.henon_a * state.x_cur * state.x_cur
        + rules.henon_b * state.x_prev,
    )
    if (
        abs(next_state.x_prev) > rules.max_abs_state
        or abs(next_state.x_cur) > rules.max_abs_state
    ):
        _fail("environment_state_escape")
    return next_state


def _capacity(account: Account, quote: Quote, bot_side: str, rules: Rules) -> int:
    if bot_side == "ask":
        risk_cap = account.inventory - rules.min_inventory
    else:
        inventory_cap = rules.max_inventory - account.inventory
        unit_cost = quote.price * (Decimal(1) + rules.fee_rate)
        cash_cap = int(
            (account.net_cash / unit_cost).to_integral_value(rounding=ROUND_FLOOR)
        )
        risk_cap = min(inventory_cap, max(cash_cap, 0))
    return max(0, min(quote.quantity, risk_cap))


def _allocation(
    target: int, capacities: Mapping[str, int], block_seq: int, offset: int
) -> dict[str, int]:
    total = sum(capacities.values())
    result = {player: 0 for player in capacities}
    if total == 0:
        return result
    target = min(target, total)
    remainders = {}
    for player, capacity in capacities.items():
        numerator = target * capacity
        result[player] = numerator // total
        remainders[player] = numerator % total
    remaining = target - sum(result.values())
    players = sorted(capacities)
    start = (offset + block_seq - 1) % len(players)
    rotated = players[start:] + players[:start]
    rank = {player: index for index, player in enumerate(rotated)}
    for player in sorted(players, key=lambda item: (-remainders[item], rank[item])):
        if remaining and result[player] < capacities[player]:
            result[player] += 1
            remaining -= 1
    if remaining:
        _fail("allocation_remainder_unassigned")
    return result


def _settle(
    accounts: Mapping[str, Account],
    actions: Mapping[str, dict[str, Any]],
    order: CustomerOrder,
    block_seq: int,
    offset: int,
    rules: Rules,
) -> tuple[dict[str, Account], list[dict[str, str]]]:
    bot_side = "ask" if order.side == "buy" else "bid"
    candidates = {}
    for player in PLAYERS:
        quote = quote_from_action(actions[player], bot_side)
        if quote is None:
            continue
        eligible = (
            quote.price <= order.reservation_price
            if bot_side == "ask"
            else quote.price >= order.reservation_price
        )
        capacity = _capacity(accounts[player], quote, bot_side, rules)
        if eligible and capacity:
            candidates[player] = (quote, capacity)
    if not candidates:
        return dict(accounts), []
    best_price = (
        min(item[0].price for item in candidates.values())
        if bot_side == "ask"
        else max(item[0].price for item in candidates.values())
    )
    best = {
        player: item
        for player, item in candidates.items()
        if item[0].price == best_price
    }
    allocation = _allocation(
        order.quantity,
        {player: item[1] for player, item in best.items()},
        block_seq,
        offset,
    )
    updated = dict(accounts)
    fills = []
    for player in PLAYERS:
        quantity = allocation.get(player, 0)
        if not quantity:
            continue
        quote = best[player][0]
        notional = (quote.price * Decimal(quantity)).quantize(MONEY_TICK)
        fee = (notional * rules.fee_rate).quantize(MONEY_TICK)
        current = updated[player]
        if bot_side == "bid":
            next_account = Account(
                current.cash - notional,
                current.inventory + quantity,
                current.fees + fee,
            )
        else:
            next_account = Account(
                current.cash + notional,
                current.inventory - quantity,
                current.fees + fee,
            )
        if (
            next_account.net_cash < 0
            or not rules.min_inventory <= next_account.inventory <= rules.max_inventory
        ):
            _fail("ledger_invariant")
        updated[player] = next_account
        fills.append(
            {
                "player_id": player,
                "bot_side": bot_side,
                "price": price(quote.price),
                "quantity": str(quantity),
                "notional": money(notional),
                "fee": money(fee),
            }
        )
    return updated, fills


def verify_replay(replay: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(replay, dict) or set(replay) != {
        "schema",
        "manifest",
        "initial_accounts",
        "events",
        "summary",
    }:
        _fail("replay_fields")
    if replay["schema"] != "replay.v1":
        _fail("replay_schema")
    manifest = replay["manifest"]
    if manifest.get("schema") != "manifest.v1" or manifest.get("ruleset") != RULESET:
        _fail("manifest_version")
    if manifest.get("players") != list(PLAYERS):
        _fail("manifest_players")
    rules = _rules_from_manifest(manifest)
    environment = manifest["environment"]
    commitment = hash_payload(
        {
            "x_prev": environment["x_prev"],
            "x_cur": environment["x_cur"],
            "orderflow_key": environment["orderflow_key"],
        }
    )
    if manifest.get("environment_commitment") != commitment:
        _fail("environment_commitment")
    offset = manifest.get("tie_break_offset")
    if offset not in (0, 1):
        _fail("tie_break_offset")

    state = EnvironmentState(
        Decimal(environment["x_prev"]), Decimal(environment["x_cur"])
    )
    opening_ref = _ref(state, rules, environment["orderflow_key"], 1)
    accounts = {
        player: Account(rules.initial_cash, rules.initial_inventory)
        for player in PLAYERS
    }
    expected_initial = {
        player: accounts[player].to_json(opening_ref) for player in PLAYERS
    }
    if replay["initial_accounts"] != expected_initial:
        _fail("initial_accounts")
    previous_hash = hash_payload(manifest)
    cumulative = {"A": 0, "B": 0}
    invalid = {"A": 0, "B": 0}

    events = replay["events"]
    if not isinstance(events, list) or len(events) != rules.blocks:
        _fail("event_count")
    for block_seq, event in enumerate(events, 1):
        if event.get("block_seq") != block_seq:
            _fail("block_sequence")
        if event.get("previous_hash") != previous_hash or not verify_event_hash(event):
            _fail("event_hash_chain")
        current_ref = _ref(state, rules, environment["orderflow_key"], block_seq)
        next_state = _advance(state, rules)
        order = _order(
            state,
            next_state,
            rules,
            environment["orderflow_key"],
            block_seq,
        )
        next_ref = _ref(next_state, rules, environment["orderflow_key"], block_seq + 1)
        if event.get("environment_state_before") != state.to_json():
            _fail("environment_state")
        if event.get("reference_price") != price(current_ref):
            _fail("reference_price")
        if event.get("market_signal") != _signal(
            state,
            next_state,
            rules,
            environment["orderflow_key"],
            block_seq,
        ):
            _fail("market_signal")
        if event.get("customer_order") != order.to_json():
            _fail("customer_order")
        if event.get("accounts_before") != {
            player: accounts[player].to_json(current_ref) for player in PLAYERS
        }:
            _fail("accounts_before")

        effective = {}
        for player in PLAYERS:
            record = event["actions"].get(player)
            if not isinstance(record, dict) or "submitted" not in record:
                _fail("action_record")
            expected_record = normalize_action(record["submitted"], block_seq, rules)
            if record != expected_record:
                _fail("action_normalization")
            if not record["valid"]:
                invalid[player] += 1
            effective[player] = record["effective"]
        accounts, fills = _settle(accounts, effective, order, block_seq, offset, rules)
        if event.get("fills") != fills:
            _fail("fills")
        for fill in fills:
            cumulative[fill["player_id"]] += int(fill["quantity"])
        if event.get("accounts_after") != {
            player: accounts[player].to_json(next_ref) for player in PLAYERS
        }:
            _fail("accounts_after")
        if event.get("next_reference_price") != price(next_ref):
            _fail("next_reference_price")
        if event.get("cumulative_volume") != {
            player: str(cumulative[player]) for player in PLAYERS
        }:
            _fail("cumulative_volume")
        previous_hash = event["event_hash"]
        state = next_state

    terminal = _ref(state, rules, environment["orderflow_key"], rules.blocks + 1)
    wealth = {player: accounts[player].equity(terminal) for player in PLAYERS}
    initial_wealth = rules.initial_cash + Decimal(rules.initial_inventory) * opening_ref
    score_a = (wealth["A"] - wealth["B"]) / initial_wealth
    expected_summary = {
        "schema": "summary.v1",
        "match_id": manifest["match_id"],
        "ruleset": RULESET,
        "blocks_completed": rules.blocks,
        "terminal_price": price(terminal),
        "initial_wealth": money(initial_wealth),
        "final_accounts": {
            player: accounts[player].to_json(terminal) for player in PLAYERS
        },
        "wealth": {player: money(wealth[player]) for player in PLAYERS},
        "scores": {"A": score(score_a), "B": score(-score_a)},
        "invalid_actions": invalid,
        "final_event_hash": previous_hash,
        "manifest_hash": hash_payload(manifest),
    }
    expected_summary["summary_hash"] = hash_payload(expected_summary)
    if replay["summary"] != expected_summary:
        _fail("summary")
    if (
        Decimal(expected_summary["scores"]["A"])
        + Decimal(expected_summary["scores"]["B"])
        != 0
    ):
        _fail("non_zero_sum")
    return {
        "valid": True,
        "match_id": manifest["match_id"],
        "ruleset": RULESET,
        "events_verified": len(events),
        "summary_hash": expected_summary["summary_hash"],
    }
