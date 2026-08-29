import copy
import unittest
from decimal import Decimal

from blockmarket.bots import BOTS, inventory_aware, no_quote, tight_spread
from blockmarket.engine import MatchConfig, run_match
from blockmarket.verifier import VerificationError, verify_replay


class EngineAndVerifierTests(unittest.TestCase):
    def test_match_verifies_and_is_exactly_zero_sum(self):
        replay = run_match(inventory_aware, tight_spread, MatchConfig().with_blocks(80))
        result = verify_replay(replay)
        self.assertTrue(result["valid"])
        scores = replay["summary"]["scores"]
        self.assertEqual(Decimal(scores["A"]) + Decimal(scores["B"]), 0)

    def test_repeated_match_is_byte_structurally_identical(self):
        config = MatchConfig(match_id="determinism").with_blocks(50)
        first = run_match(inventory_aware, tight_spread, config)
        second = run_match(inventory_aware, tight_spread, config)
        self.assertEqual(first, second)

    def test_tampered_replay_fails(self):
        replay = run_match(inventory_aware, tight_spread, MatchConfig().with_blocks(10))
        tampered = copy.deepcopy(replay)
        tampered["events"][0]["reference_price"] = "50.00"
        with self.assertRaises(VerificationError):
            verify_replay(tampered)

    def test_strategy_exception_is_local_invalid_action(self):
        def broken(observation, player):
            raise RuntimeError("boom")

        replay = run_match(broken, no_quote, MatchConfig().with_blocks(5))
        self.assertEqual(replay["summary"]["invalid_actions"]["A"], 5)
        self.assertEqual(replay["summary"]["invalid_actions"]["B"], 0)
        verify_replay(replay)

    def test_unserializable_strategy_output_is_local_invalid_action(self):
        def decimal_output(observation, player):
            return {"decision_seq": observation["block_seq"], "bid": Decimal(1)}

        replay = run_match(decimal_output, no_quote, MatchConfig().with_blocks(3))
        self.assertEqual(replay["summary"]["invalid_actions"]["A"], 3)
        verify_replay(replay)

    def test_invalid_config_fails_before_match(self):
        with self.assertRaisesRegex(ValueError, "match_id_length"):
            run_match(no_quote, no_quote, MatchConfig(match_id="").with_blocks(2))

    def test_all_reference_bots_emit_legal_actions(self):
        for name, bot in BOTS.items():
            with self.subTest(bot=name):
                replay = run_match(
                    bot, no_quote, MatchConfig(match_id="bot-" + name).with_blocks(25)
                )
                self.assertEqual(replay["summary"]["invalid_actions"]["A"], 0)
                verify_replay(replay)


if __name__ == "__main__":
    unittest.main()
