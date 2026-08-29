"""Deterministic in-process reference strategies for Gate 2 analysis."""

import hashlib
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from .rules import PRICE_TICK, clamp

Bot = Callable[[dict[str, Any], str], dict[str, Any]]


def _action(
    seq: int, bid: Decimal, ask: Decimal, quantity: int, observation: dict[str, Any]
) -> dict[str, Any]:
    lower = Decimal(observation["limits"]["price_range"][0])
    upper = Decimal(observation["limits"]["price_range"][1])
    bid = clamp(bid.quantize(PRICE_TICK), lower, upper - PRICE_TICK)
    ask = clamp(ask.quantize(PRICE_TICK), lower + PRICE_TICK, upper)
    if bid >= ask:
        midpoint = clamp((bid + ask) / 2, lower + PRICE_TICK, upper - PRICE_TICK)
        bid = (midpoint - PRICE_TICK).quantize(PRICE_TICK)
        ask = (midpoint + PRICE_TICK).quantize(PRICE_TICK)
    return {
        "decision_seq": seq,
        "bid": {"price": format(bid, ".2f"), "quantity": str(quantity)},
        "ask": {"price": format(ask, ".2f"), "quantity": str(quantity)},
    }


def no_quote(observation: dict[str, Any], player: str) -> dict[str, Any]:
    return {"decision_seq": observation["block_seq"], "bid": None, "ask": None}


def wide_spread(observation: dict[str, Any], player: str) -> dict[str, Any]:
    ref = Decimal(observation["reference_price"])
    return _action(
        observation["block_seq"],
        ref - Decimal("2.50"),
        ref + Decimal("2.50"),
        5,
        observation,
    )


def tight_spread(observation: dict[str, Any], player: str) -> dict[str, Any]:
    ref = Decimal(observation["reference_price"])
    return _action(
        observation["block_seq"],
        ref - Decimal("0.20"),
        ref + Decimal("0.20"),
        10,
        observation,
    )


def inventory_aware(observation: dict[str, Any], player: str) -> dict[str, Any]:
    ref = Decimal(observation["reference_price"])
    inventory = Decimal(observation["account"]["inventory"])
    skew = (inventory - Decimal(50)) * Decimal("0.05")
    center = ref - skew
    return _action(
        observation["block_seq"],
        center - Decimal("0.45"),
        center + Decimal("0.45"),
        8,
        observation,
    )


def signal_follower(observation: dict[str, Any], player: str) -> dict[str, Any]:
    ref = Decimal(observation["reference_price"])
    decision_seq = observation["block_seq"]
    bid = {
        "price": format((ref - Decimal("0.45")).quantize(PRICE_TICK), ".2f"),
        "quantity": "7",
    }
    ask = {
        "price": format((ref + Decimal("0.45")).quantize(PRICE_TICK), ".2f"),
        "quantity": "7",
    }
    if observation["market_signal"] == "UP":
        ask = None
    else:
        bid = None
    return {"decision_seq": decision_seq, "bid": bid, "ask": ask}


def opponent_adaptive(observation: dict[str, Any], player: str) -> dict[str, Any]:
    opponent = "B" if player == "A" else "A"
    own_volume = int(observation["cumulative_volume"][player])
    opponent_volume = int(observation["cumulative_volume"][opponent])
    ref = Decimal(observation["reference_price"])
    if opponent_volume <= own_volume:
        bid = {
            "price": format((ref - Decimal("0.40")).quantize(PRICE_TICK), ".2f"),
            "quantity": "9",
        }
        ask = {
            "price": format((ref + Decimal("0.40")).quantize(PRICE_TICK), ".2f"),
            "quantity": "9",
        }
        if observation["market_signal"] == "UP":
            ask = None
        else:
            bid = None
        return {
            "decision_seq": observation["block_seq"],
            "bid": bid,
            "ask": ask,
        }
    previous_ref = (
        Decimal(observation["reference_history"][-2])
        if len(observation["reference_history"]) > 1
        else ref
    )
    chase_center = ref + Decimal("0.02") * (ref - previous_ref)
    return _action(
        observation["block_seq"],
        chase_center - Decimal("0.08"),
        chase_center + Decimal("0.08"),
        10,
        observation,
    )


def random_valid(observation: dict[str, Any], player: str) -> dict[str, Any]:
    material = "{}|{}".format(
        "|".join(observation["reference_history"][-4:]),
        observation["block_seq"],
    ).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    ref = Decimal(observation["reference_price"])
    center_shift = Decimal(int.from_bytes(digest[:2], "big") % 401 - 200) / Decimal(100)
    half_spread = Decimal(int.from_bytes(digest[2:4], "big") % 200 + 10) / Decimal(100)
    quantity = 1 + digest[4] % 10
    center = ref + center_shift
    return _action(
        observation["block_seq"],
        center - half_spread,
        center + half_spread,
        quantity,
        observation,
    )


BOTS: dict[str, Bot] = {
    "NoQuote": no_quote,
    "WideSpread": wide_spread,
    "TightSpread": tight_spread,
    "InventoryAware": inventory_aware,
    "SignalFollower": signal_follower,
    "OpponentAdaptive": opponent_adaptive,
    "RandomValid": random_valid,
}


def get_bot(name: str) -> Bot:
    try:
        return BOTS[name]
    except KeyError as exc:
        raise ValueError("unknown_bot:" + name) from exc
