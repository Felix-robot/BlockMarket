import unittest
from decimal import Decimal

from blockmarket.models import Account, CustomerOrder
from blockmarket.rules import Rules
from blockmarket.settlement import settle_block


def action(seq, bid=None, ask=None):
    def quote(value):
        if value is None:
            return None
        price, quantity = value
        return {"price": price, "quantity": str(quantity)}

    return {"decision_seq": seq, "bid": quote(bid), "ask": quote(ask)}


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules()
        self.accounts = {
            "A": Account(Decimal(10000), 50),
            "B": Account(Decimal(10000), 50),
        }

    def settle(self, order, a, b, seq=1, offset=0, accounts=None):
        return settle_block(
            accounts or self.accounts, {"A": a, "B": b}, order, seq, offset, self.rules
        )

    def test_hold_has_no_fill(self):
        updated, fills = self.settle(
            CustomerOrder("noise", "buy", 5, Decimal(101)), action(1), action(1)
        )
        self.assertEqual(fills, [])
        self.assertEqual(updated, self.accounts)

    def test_best_price_only_does_not_sweep_next_level(self):
        updated, fills = self.settle(
            CustomerOrder("noise", "buy", 5, Decimal(101)),
            action(1, ask=("100.00", 5)),
            action(1, ask=("99.00", 3)),
        )
        self.assertEqual(
            [(fill["player_id"], fill["quantity"]) for fill in fills], [("B", "3")]
        )
        self.assertEqual(updated["B"].inventory, 47)

    def test_sell_order_chooses_highest_bid(self):
        _, fills = self.settle(
            CustomerOrder("noise", "sell", 4, Decimal(99)),
            action(1, bid=("100.00", 4)),
            action(1, bid=("99.50", 4)),
        )
        self.assertEqual(
            [(fill["player_id"], fill["quantity"]) for fill in fills], [("A", "4")]
        )

    def test_equal_capacity_tie_splits_evenly(self):
        _, fills = self.settle(
            CustomerOrder("noise", "buy", 6, Decimal(101)),
            action(1, ask=("100.00", 6)),
            action(1, ask=("100.00", 6)),
        )
        self.assertEqual(
            {fill["player_id"]: fill["quantity"] for fill in fills},
            {"A": "3", "B": "3"},
        )

    def test_unequal_capacity_tie_is_prorata(self):
        _, fills = self.settle(
            CustomerOrder("noise", "buy", 5, Decimal(101)),
            action(1, ask=("100.00", 2)),
            action(1, ask=("100.00", 8)),
        )
        self.assertEqual(
            {fill["player_id"]: fill["quantity"] for fill in fills},
            {"A": "1", "B": "4"},
        )

    def test_equal_remainder_rotates_by_block(self):
        order = CustomerOrder("noise", "buy", 1, Decimal(101))
        quotes = action(1, ask=("100.00", 1))
        _, first = self.settle(order, quotes, quotes, seq=1, offset=0)
        quotes2 = action(2, ask=("100.00", 1))
        _, second = self.settle(order, quotes2, quotes2, seq=2, offset=0)
        self.assertEqual(first[0]["player_id"], "A")
        self.assertEqual(second[0]["player_id"], "B")

    def test_inventory_caps_ask(self):
        accounts = {"A": Account(Decimal(10000), 2), "B": self.accounts["B"]}
        updated, fills = self.settle(
            CustomerOrder("noise", "buy", 10, Decimal(101)),
            action(1, ask=("100.00", 10)),
            action(1),
            accounts=accounts,
        )
        self.assertEqual(fills[0]["quantity"], "2")
        self.assertEqual(updated["A"].inventory, 0)

    def test_max_inventory_caps_bid(self):
        accounts = {"A": Account(Decimal(10000), 99), "B": self.accounts["B"]}
        updated, fills = self.settle(
            CustomerOrder("noise", "sell", 10, Decimal(99)),
            action(1, bid=("100.00", 10)),
            action(1),
            accounts=accounts,
        )
        self.assertEqual(fills[0]["quantity"], "1")
        self.assertEqual(updated["A"].inventory, 100)

    def test_cash_and_fee_cap_bid(self):
        accounts = {"A": Account(Decimal("200.20"), 50), "B": self.accounts["B"]}
        updated, fills = self.settle(
            CustomerOrder("noise", "sell", 10, Decimal(99)),
            action(1, bid=("100.00", 10)),
            action(1),
            accounts=accounts,
        )
        self.assertEqual(fills[0]["quantity"], "2")
        self.assertGreaterEqual(updated["A"].net_cash, 0)

    def test_fee_is_recorded_separately(self):
        updated, fills = self.settle(
            CustomerOrder("noise", "buy", 2, Decimal(100)),
            action(1, ask=("100.00", 2)),
            action(1),
        )
        self.assertEqual(updated["A"].cash, Decimal("10200.00000000"))
        self.assertEqual(updated["A"].fees, Decimal("0.10000000"))
        self.assertEqual(fills[0]["fee"], "0.10000000")


if __name__ == "__main__":
    unittest.main()
