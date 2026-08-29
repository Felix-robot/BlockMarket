import json
import unittest
from decimal import Decimal
from pathlib import Path

from blockmarket.matrix import payoff_matrix


class MatrixAndSchemaTests(unittest.TestCase):
    def test_all_schema_files_are_valid_json(self):
        schema_dir = Path(__file__).resolve().parents[1] / "schemas"
        schemas = list(schema_dir.glob("*.json"))
        self.assertGreaterEqual(len(schemas), 5)
        for path in schemas:
            with self.subTest(schema=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("$schema", value)
                self.assertIn("$id", value)

    def test_paired_matrix_is_antisymmetric(self):
        bots = ["NoQuote", "TightSpread", "InventoryAware"]
        matrix = payoff_matrix(bots, seeds=2, blocks=25)
        for row in bots:
            self.assertEqual(Decimal(matrix["scores"][row][row]), 0)
            for column in bots:
                self.assertEqual(
                    Decimal(matrix["scores"][row][column]),
                    -Decimal(matrix["scores"][column][row]),
                )

    def test_matrix_reports_cycles_and_dominators(self):
        matrix = payoff_matrix(
            ["SignalFollower", "TightSpread", "OpponentAdaptive"],
            seeds=8,
            blocks=100,
        )
        self.assertEqual(
            matrix["strict_positive_cycles"],
            [["SignalFollower", "TightSpread", "OpponentAdaptive"]],
        )
        self.assertEqual(matrix["strict_dominating_strategies"], [])


if __name__ == "__main__":
    unittest.main()
