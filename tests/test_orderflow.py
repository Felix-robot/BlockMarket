import unittest
from decimal import Decimal

from blockmarket.models import EnvironmentState
from blockmarket.orderflow import EnvironmentEscape, advance, step
from blockmarket.rules import Rules


class OrderflowTests(unittest.TestCase):
    key = "2a97516c354b68848cdbd8f54a226a0a9e32811d8112f0331a7e2a3c3e98f6e7"

    def test_default_path_is_deterministic_and_bounded(self):
        rules = Rules()
        first = EnvironmentState(Decimal("0.1"), Decimal("0.3"))
        traces = []
        for _ in range(2):
            state = first
            trace = []
            for block_seq in range(1, 301):
                reference, signal, order, state = step(
                    state, rules, self.key, block_seq
                )
                trace.append((reference, signal, order.to_json(), state.to_json()))
            traces.append(trace)
        self.assertEqual(traces[0], traces[1])
        self.assertEqual(len({item[1] for item in traces[0]}), 2)
        self.assertEqual(len({item[2]["side"] for item in traces[0]}), 2)
        self.assertEqual(len({item[2]["kind"] for item in traces[0]}), 2)

    def test_escape_is_fail_closed(self):
        with self.assertRaises(EnvironmentEscape):
            advance(EnvironmentState(Decimal(0), Decimal(4)), Rules())


if __name__ == "__main__":
    unittest.main()
