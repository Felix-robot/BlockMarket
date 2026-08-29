"""Deterministic pure match orchestration."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from .canonical import canonical_dumps, chain_event, hash_payload
from .models import Account, EnvironmentState
from .orderflow import reference_price, step
from .rules import PLAYERS, RULESET, Rules, money, price, score
from .settlement import settle_block
from .validation import normalize_action

Strategy = Callable[[dict[str, Any], str], dict[str, Any]]
ActionSource = Callable[[dict[str, dict[str, Any]]], Mapping[str, Any]]


@dataclass(frozen=True)
class MatchConfig:
    match_id: str = "demo-match"
    rules: Rules = field(default_factory=Rules)
    x_prev: Decimal = Decimal("0.1")
    x_cur: Decimal = Decimal("0.3")
    tie_break_offset: int = 0
    orderflow_key: str = (
        "2a97516c354b68848cdbd8f54a226a0a9e32811d8112f0331a7e2a3c3e98f6e7"
    )

    def with_blocks(self, blocks: int) -> "MatchConfig":
        return replace(self, rules=replace(self.rules, blocks=blocks))


def manifest_for(config: MatchConfig) -> dict[str, Any]:
    environment = {
        "kind": "henon-v1",
        "a": format(config.rules.henon_a, "f"),
        "b": format(config.rules.henon_b, "f"),
        "x_prev": format(config.x_prev, "f"),
        "x_cur": format(config.x_cur, "f"),
        "max_abs_state": format(config.rules.max_abs_state, "f"),
        "orderflow_key": config.orderflow_key,
    }
    commitment = hash_payload(
        {
            "x_prev": environment["x_prev"],
            "x_cur": environment["x_cur"],
            "orderflow_key": environment["orderflow_key"],
        }
    )
    return {
        "schema": "manifest.v1",
        "ruleset": RULESET,
        "match_id": config.match_id,
        "players": list(PLAYERS),
        "rules": config.rules.to_json(),
        "environment": environment,
        "environment_commitment": commitment,
        "tie_break_offset": config.tie_break_offset,
    }


def _observation(
    manifest: dict[str, Any],
    player: str,
    block_seq: int,
    account: Account,
    ref_history: list[str],
    signal_history: list[str],
    last_fills: list[dict[str, str]],
    cumulative_volume: Mapping[str, int],
) -> dict[str, Any]:
    current_ref = Decimal(ref_history[-1])
    return {
        "schema": "observation.v1",
        "ruleset": manifest["ruleset"],
        "match_id": manifest["match_id"],
        "player_id": player,
        "block_seq": block_seq,
        "remaining_blocks": manifest["rules"]["blocks"] - block_seq + 1,
        "reference_price": ref_history[-1],
        "reference_history": list(ref_history),
        "market_signal": signal_history[-1],
        "signal_history": list(signal_history),
        "last_fills": list(last_fills),
        "cumulative_volume": {key: str(cumulative_volume[key]) for key in PLAYERS},
        "account": account.to_json(current_ref),
        "limits": {
            "price_range": manifest["rules"]["price_range"],
            "price_tick": manifest["rules"]["price_tick"],
            "inventory_range": manifest["rules"]["inventory_range"],
            "max_quote_quantity": manifest["rules"]["max_quote_quantity"],
        },
    }


def _call_strategy(strategy: Strategy, observation: dict[str, Any], player: str) -> Any:
    try:
        submitted = strategy(observation, player)
    except Exception as exc:  # noqa: BLE001 - untrusted strategy failures must stay local.
        return {"strategy_error": type(exc).__name__}
    try:
        canonical_dumps(submitted)
    except (TypeError, ValueError, OverflowError):
        return {"unserializable_submission": type(submitted).__name__}
    return submitted


def _validate_config(config: MatchConfig) -> None:
    rules = config.rules
    if not isinstance(config.match_id, str) or not 1 <= len(config.match_id) <= 128:
        raise ValueError("match_id_length")
    if rules.blocks < 1 or rules.blocks > 1_000_000:
        raise ValueError("blocks_out_of_range")
    if config.tie_break_offset not in (0, 1):
        raise ValueError("tie_break_offset_must_be_0_or_1")
    if not isinstance(config.orderflow_key, str) or not re.fullmatch(
        r"[0-9a-f]{64}", config.orderflow_key
    ):
        raise ValueError("orderflow_key_format")
    decimal_values = (
        config.x_prev,
        config.x_cur,
        rules.initial_cash,
        rules.min_price,
        rules.max_price,
        rules.fee_bps,
        rules.reference_scale,
        rules.min_customer_urgency,
        rules.max_customer_urgency,
        rules.hidden_reference_noise,
        rules.henon_a,
        rules.henon_b,
        rules.max_abs_state,
    )
    if any(not value.is_finite() for value in decimal_values):
        raise ValueError("non_finite_config_decimal")
    if (
        abs(config.x_prev) > rules.max_abs_state
        or abs(config.x_cur) > rules.max_abs_state
    ):
        raise ValueError("initial_environment_state_out_of_range")
    if rules.initial_cash <= 0 or rules.fee_bps < 0:
        raise ValueError("invalid_cash_or_fee")
    if rules.min_price <= 0 or rules.min_price >= rules.max_price:
        raise ValueError("invalid_price_range")
    if (
        rules.min_inventory > rules.initial_inventory
        or rules.initial_inventory > rules.max_inventory
    ):
        raise ValueError("invalid_initial_inventory")
    if rules.max_quote_quantity < 1:
        raise ValueError("invalid_max_quote_quantity")
    if not 0 <= rules.informed_probability_bps <= 10000:
        raise ValueError("invalid_informed_probability")
    if not 0 <= rules.signal_accuracy_bps <= 10000:
        raise ValueError("invalid_signal_accuracy")
    if (
        rules.min_customer_urgency < 0
        or rules.min_customer_urgency > rules.max_customer_urgency
    ):
        raise ValueError("invalid_customer_urgency")
    if rules.hidden_reference_noise < 0:
        raise ValueError("invalid_hidden_reference_noise")


def run_match_with_action_source(
    action_source: ActionSource, config: MatchConfig | None = None
) -> dict[str, Any]:
    config = config or MatchConfig()
    _validate_config(config)

    manifest = manifest_for(config)
    state = EnvironmentState(config.x_prev, config.x_cur)
    opening_ref = reference_price(state, config.rules, config.orderflow_key, 1)
    accounts = {
        player: Account(config.rules.initial_cash, config.rules.initial_inventory)
        for player in PLAYERS
    }
    initial_accounts = {
        player: account.to_json(opening_ref) for player, account in accounts.items()
    }
    cumulative_volume = {"A": 0, "B": 0}
    ref_history = [price(opening_ref)]
    signal_history: list[str] = []
    last_fills: list[dict[str, str]] = []
    events = []
    previous_hash = hash_payload(manifest)

    for block_seq in range(1, config.rules.blocks + 1):
        current_ref, market_signal, order, next_state = step(
            state, config.rules, config.orderflow_key, block_seq
        )
        signal_history.append(market_signal)
        accounts_before = {
            player: accounts[player].to_json(current_ref) for player in PLAYERS
        }
        observations = {}
        for player in PLAYERS:
            observations[player] = _observation(
                manifest,
                player,
                block_seq,
                accounts[player],
                ref_history,
                signal_history,
                last_fills,
                cumulative_volume,
            )
        try:
            submissions = action_source(observations)
        except Exception as exc:  # noqa: BLE001 - action sources are untrusted adapters.
            submissions = {
                player: {"action_source_error": type(exc).__name__}
                for player in PLAYERS
            }
        if not isinstance(submissions, Mapping):
            submissions = {
                player: {"action_source_error": "invalid_result"} for player in PLAYERS
            }
        action_records = {}
        effective_actions = {}
        for player in PLAYERS:
            submitted = submissions.get(
                player, {"action_source_error": "missing_player"}
            )
            action_records[player] = normalize_action(
                submitted, block_seq, config.rules
            )
            effective_actions[player] = action_records[player]["effective"]

        accounts, fills = settle_block(
            accounts,
            effective_actions,
            order,
            block_seq,
            config.tie_break_offset,
            config.rules,
        )
        for fill in fills:
            cumulative_volume[fill["player_id"]] += int(fill["quantity"])
        next_ref = reference_price(
            next_state, config.rules, config.orderflow_key, block_seq + 1
        )
        body = {
            "schema": "event.v1",
            "match_id": config.match_id,
            "block_seq": block_seq,
            "environment_state_before": state.to_json(),
            "reference_price": price(current_ref),
            "market_signal": market_signal,
            "customer_order": order.to_json(),
            "accounts_before": accounts_before,
            "actions": action_records,
            "fills": fills,
            "accounts_after": {
                player: accounts[player].to_json(next_ref) for player in PLAYERS
            },
            "next_reference_price": price(next_ref),
            "cumulative_volume": {
                player: str(cumulative_volume[player]) for player in PLAYERS
            },
        }
        event = chain_event(body, previous_hash)
        events.append(event)
        previous_hash = event["event_hash"]
        state = next_state
        ref_history.append(price(next_ref))
        last_fills = fills

    terminal_price = reference_price(
        state, config.rules, config.orderflow_key, config.rules.blocks + 1
    )
    wealth = {player: accounts[player].equity(terminal_price) for player in PLAYERS}
    initial_wealth = (
        config.rules.initial_cash
        + Decimal(config.rules.initial_inventory) * opening_ref
    )
    score_a = (wealth["A"] - wealth["B"]) / initial_wealth
    score_b = -score_a
    summary_body = {
        "schema": "summary.v1",
        "match_id": config.match_id,
        "ruleset": RULESET,
        "blocks_completed": config.rules.blocks,
        "terminal_price": price(terminal_price),
        "initial_wealth": money(initial_wealth),
        "final_accounts": {
            player: accounts[player].to_json(terminal_price) for player in PLAYERS
        },
        "wealth": {player: money(wealth[player]) for player in PLAYERS},
        "scores": {"A": score(score_a), "B": score(score_b)},
        "invalid_actions": {
            player: sum(1 for event in events if not event["actions"][player]["valid"])
            for player in PLAYERS
        },
        "final_event_hash": previous_hash,
        "manifest_hash": hash_payload(manifest),
    }
    summary = dict(summary_body)
    summary["summary_hash"] = hash_payload(summary_body)
    return {
        "schema": "replay.v1",
        "manifest": manifest,
        "initial_accounts": initial_accounts,
        "events": events,
        "summary": summary,
    }


def run_match(
    strategy_a: Strategy, strategy_b: Strategy, config: MatchConfig | None = None
) -> dict[str, Any]:
    strategies = {"A": strategy_a, "B": strategy_b}

    def action_source(observations: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            player: _call_strategy(strategies[player], observations[player], player)
            for player in PLAYERS
        }

    return run_match_with_action_source(action_source, config)
