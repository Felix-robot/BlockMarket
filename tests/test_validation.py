import json
import unittest
from pathlib import Path

from blockmarket.canonical import StrictJSONError, canonical_dumps, strict_loads
from blockmarket.rules import Rules
from blockmarket.validation import normalize_action, validate_action


class ValidationTests(unittest.TestCase):
    def test_all_documented_action_cases(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "action_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 20)
        for case in cases:
            with self.subTest(case=case["case_id"]):
                valid, error = validate_action(
                    case["action"], case["expected_seq"], Rules()
                )
                self.assertEqual(valid, case["valid"])
                self.assertEqual(error, case["error"])

    def test_invalid_action_becomes_hold(self):
        record = normalize_action({"decision_seq": 1, "bid": None}, 1, Rules())
        self.assertFalse(record["valid"])
        self.assertEqual(
            record["effective"], {"decision_seq": 1, "bid": None, "ask": None}
        )

    def test_strict_json_rejects_duplicate_keys(self):
        with self.assertRaises(StrictJSONError):
            strict_loads('{"a":1,"a":2}')

    def test_strict_json_rejects_non_finite_numbers(self):
        with self.assertRaises(StrictJSONError):
            strict_loads('{"a":NaN}')

    def test_canonical_json_is_stable(self):
        self.assertEqual(canonical_dumps({"b": 2, "a": 1}), '{"a":1,"b":2}')


if __name__ == "__main__":
    unittest.main()
