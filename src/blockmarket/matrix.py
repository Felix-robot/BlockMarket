"""Small paired-position payoff matrix for Gate 2 diagnostics."""

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

from .bots import BOTS
from .engine import MatchConfig, run_match
from .models import EnvironmentState
from .orderflow import advance
from .rules import RULESET, Rules, score


def safe_seeds(
    count: int, blocks: int, rules: Rules | None = None
) -> list[tuple[Decimal, Decimal, str]]:
    if count < 1:
        raise ValueError("seeds_must_be_positive")
    rules = replace(rules or Rules(), blocks=blocks)
    found = []
    candidate = 0
    while len(found) < count and candidate < 10000:
        x_prev = Decimal(((candidate * 37) % 101) - 50) / Decimal(100)
        x_cur = Decimal(((candidate * 61 + 17) % 101) - 50) / Decimal(100)
        orderflow_key = hashlib.sha256(
            f"blockmarket-development-seed-{candidate}".encode()
        ).hexdigest()
        state = EnvironmentState(x_prev, x_cur)
        try:
            for block_seq in range(1, blocks + 1):
                state = advance(state, rules)
        except RuntimeError:
            candidate += 1
            continue
        found.append((x_prev, x_cur, orderflow_key))
        candidate += 1
    if len(found) != count:
        raise RuntimeError("not_enough_safe_seeds")
    return found


def payoff_matrix(
    bot_names: Sequence[str],
    seeds: int = 3,
    blocks: int = 60,
    rules: Rules | None = None,
) -> dict[str, Any]:
    for name in bot_names:
        if name not in BOTS:
            raise ValueError("unknown_bot:" + name)
    match_rules = replace(rules or Rules(), blocks=blocks)
    seed_values = safe_seeds(seeds, blocks, match_rules)
    table = {row: {} for row in bot_names}
    pair_diagnostics = {row: {} for row in bot_names}
    position_deltas = []
    for name in bot_names:
        table[name][name] = score(Decimal(0))
        pair_diagnostics[name][name] = {
            "wins": 0,
            "losses": 0,
            "ties": seeds,
            "mean_score": score(Decimal(0)),
            "worst_seed_score": score(Decimal(0)),
            "best_seed_score": score(Decimal(0)),
        }
    for row_index, row in enumerate(bot_names):
        for column in bot_names[row_index + 1 :]:
            results = []
            paired_seed_results = []
            for index, (x_prev, x_cur, orderflow_key) in enumerate(seed_values):
                base = MatchConfig(
                    match_id=f"matrix-{row}-{column}-{index}-ab",
                    rules=match_rules,
                    x_prev=x_prev,
                    x_cur=x_cur,
                    tie_break_offset=index % 2,
                    orderflow_key=orderflow_key,
                )
                ab = run_match(BOTS[row], BOTS[column], base)
                ba_config = MatchConfig(
                    match_id=f"matrix-{row}-{column}-{index}-ba",
                    rules=base.rules,
                    x_prev=x_prev,
                    x_cur=x_cur,
                    tie_break_offset=index % 2,
                    orderflow_key=base.orderflow_key,
                )
                ba = run_match(BOTS[column], BOTS[row], ba_config)
                as_a = Decimal(ab["summary"]["scores"]["A"])
                as_b = Decimal(ba["summary"]["scores"]["B"])
                results.extend([as_a, as_b])
                paired_seed_results.append((as_a + as_b) / Decimal(2))
                position_deltas.append(as_a - as_b)
            paired_score = sum(results) / Decimal(len(results))
            table[row][column] = score(paired_score)
            table[column][row] = score(-paired_score)
            row_diagnostic = {
                "wins": sum(value > 0 for value in paired_seed_results),
                "losses": sum(value < 0 for value in paired_seed_results),
                "ties": sum(value == 0 for value in paired_seed_results),
                "mean_score": score(paired_score),
                "worst_seed_score": score(min(paired_seed_results)),
                "best_seed_score": score(max(paired_seed_results)),
            }
            pair_diagnostics[row][column] = row_diagnostic
            pair_diagnostics[column][row] = {
                "wins": row_diagnostic["losses"],
                "losses": row_diagnostic["wins"],
                "ties": row_diagnostic["ties"],
                "mean_score": score(-paired_score),
                "worst_seed_score": score(-max(paired_seed_results)),
                "best_seed_score": score(-min(paired_seed_results)),
            }
    absolute_position_deltas = sorted(abs(item) for item in position_deltas)
    max_position_delta = max(absolute_position_deltas, default=Decimal(0))
    mean_position_delta = (
        sum(absolute_position_deltas) / Decimal(len(absolute_position_deltas))
        if absolute_position_deltas
        else Decimal(0)
    )
    p95_index = max(0, (95 * len(absolute_position_deltas) + 99) // 100 - 1)
    p95_position_delta = (
        absolute_position_deltas[p95_index] if absolute_position_deltas else Decimal(0)
    )
    positive_edges = {
        (row, column)
        for row in bot_names
        for column in bot_names
        if row != column and Decimal(table[row][column]) > 0
    }
    cycles = []
    for first_index, first in enumerate(bot_names):
        for second_index in range(first_index + 1, len(bot_names)):
            second = bot_names[second_index]
            for third_index in range(second_index + 1, len(bot_names)):
                third = bot_names[third_index]
                if {
                    (first, second),
                    (second, third),
                    (third, first),
                } <= positive_edges:
                    cycles.append([first, second, third])
                elif {
                    (first, third),
                    (third, second),
                    (second, first),
                } <= positive_edges:
                    cycles.append([first, third, second])
    dominating_strategies = [
        row
        for row in bot_names
        if all(row == column or (row, column) in positive_edges for column in bot_names)
    ]
    return {
        "schema": "payoff-matrix.v1",
        "ruleset": RULESET,
        "blocks": blocks,
        "seed_count": seeds,
        "bots": list(bot_names),
        "scores": table,
        "pair_diagnostics": pair_diagnostics,
        "strict_positive_cycles": cycles,
        "strict_dominating_strategies": dominating_strategies,
        "max_unpaired_position_delta": score(max_position_delta),
        "mean_absolute_unpaired_position_delta": score(mean_position_delta),
        "p95_absolute_unpaired_position_delta": score(p95_position_delta),
        "note": "Each off-diagonal cell averages both A/B positions for every seed.",
    }
