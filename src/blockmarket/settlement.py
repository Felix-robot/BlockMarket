"""Pure best-price selection, pro-rata allocation, and ledger updates."""

from collections.abc import Mapping
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from .models import Account, CustomerOrder, Quote
from .rules import MONEY_TICK, PLAYERS, Rules, money, price
from .validation import quote_from_action


class LedgerInvariantError(RuntimeError):
    pass


def executable_capacity(
    account: Account, quote: Quote, bot_side: str, rules: Rules
) -> int:
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


def _allocate(
    target: int, capacities: Mapping[str, int], block_seq: int, tie_break_offset: int
) -> dict[str, int]:
    allocations = {player: 0 for player in capacities}
    total = sum(capacities.values())
    if target <= 0 or total <= 0:
        return allocations
    target = min(target, total)
    remainders = {}
    for player, capacity in capacities.items():
        numerator = target * capacity
        allocations[player] = numerator // total
        remainders[player] = numerator % total
    remaining = target - sum(allocations.values())
    players = sorted(capacities)
    start = (tie_break_offset + block_seq - 1) % len(players)
    rotated = players[start:] + players[:start]
    rotation_rank = {player: rank for rank, player in enumerate(rotated)}
    priority = sorted(
        players, key=lambda player: (-remainders[player], rotation_rank[player])
    )
    for player in priority:
        if remaining <= 0:
            break
        if allocations[player] < capacities[player]:
            allocations[player] += 1
            remaining -= 1
    if remaining:
        raise LedgerInvariantError("allocation_remainder_unassigned")
    return allocations


def settle_block(
    accounts: Mapping[str, Account],
    effective_actions: Mapping[str, dict[str, Any]],
    order: CustomerOrder,
    block_seq: int,
    tie_break_offset: int,
    rules: Rules,
) -> tuple[dict[str, Account], list[dict[str, str]]]:
    bot_side = "ask" if order.side == "buy" else "bid"
    candidates = {}
    for player in PLAYERS:
        quote = quote_from_action(effective_actions[player], bot_side)
        if quote is None:
            continue
        eligible = (
            quote.price <= order.reservation_price
            if bot_side == "ask"
            else quote.price >= order.reservation_price
        )
        capacity = executable_capacity(accounts[player], quote, bot_side, rules)
        if eligible and capacity > 0:
            candidates[player] = (quote, capacity)

    if not candidates:
        return dict(accounts), []

    best_price = (
        min(quote.price for quote, _ in candidates.values())
        if bot_side == "ask"
        else max(quote.price for quote, _ in candidates.values())
    )
    best = {
        player: (quote, cap)
        for player, (quote, cap) in candidates.items()
        if quote.price == best_price
    }
    allocations = _allocate(
        order.quantity,
        {player: cap for player, (_, cap) in best.items()},
        block_seq,
        tie_break_offset,
    )

    updated = dict(accounts)
    fills = []
    for player in PLAYERS:
        quantity = allocations.get(player, 0)
        if quantity <= 0:
            continue
        quote = best[player][0]
        notional = (quote.price * Decimal(quantity)).quantize(MONEY_TICK)
        fee = (notional * rules.fee_rate).quantize(MONEY_TICK)
        account = updated[player]
        if bot_side == "bid":
            next_account = Account(
                account.cash - notional,
                account.inventory + quantity,
                account.fees + fee,
            )
        else:
            next_account = Account(
                account.cash + notional,
                account.inventory - quantity,
                account.fees + fee,
            )
        _assert_account(next_account, rules)
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


def _assert_account(account: Account, rules: Rules) -> None:
    if (
        account.inventory < rules.min_inventory
        or account.inventory > rules.max_inventory
    ):
        raise LedgerInvariantError("inventory_out_of_range")
    if account.net_cash < 0:
        raise LedgerInvariantError("negative_net_cash")
