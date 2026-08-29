#!/usr/bin/env python3
"""BlockMarket JSONL starter Bot: one-sided quoting from the public signal."""

import json
import sys
from decimal import Decimal


def decide(observation):
    reference = Decimal(observation["reference_price"])
    lower, upper = map(Decimal, observation["limits"]["price_range"])
    bid_price = max(lower, min(reference - Decimal("0.45"), upper))
    ask_price = max(lower, min(reference + Decimal("0.45"), upper))
    bid = {
        "price": format(bid_price, ".2f"),
        "quantity": "7",
    }
    ask = {
        "price": format(ask_price, ".2f"),
        "quantity": "7",
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


def main():
    initialized = False
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("type") == "init":
            if message.get("protocol") != "blockmarket-jsonl-v1":
                raise ValueError("unsupported_protocol")
            initialized = True
        elif message.get("type") == "decision":
            if not initialized:
                raise ValueError("missing_init")
            print(
                json.dumps(decide(message["observation"]), separators=(",", ":")),
                flush=True,
            )
        elif message.get("type") == "end":
            return


if __name__ == "__main__":
    main()
