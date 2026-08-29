import shutil
import sys
import time
import unittest
from pathlib import Path

from blockmarket.engine import MatchConfig
from blockmarket.runner import RunnerLimits, public_manifest, run_subprocess_match
from blockmarket.verifier import verify_replay

GOOD_BOT = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("type") == "decision":
        seq = message["observation"]["block_seq"]
        print(json.dumps({"decision_seq": seq, "bid": None, "ask": None}), flush=True)
    elif message.get("type") == "end":
        break
"""

CRASH_BOT = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("type") == "decision":
        raise SystemExit(7)
"""

HANG_BOT = r"""
import json, sys, time
for line in sys.stdin:
    message = json.loads(line)
    if message.get("type") == "decision":
        time.sleep(10)
"""

GARBAGE_BOT = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("type") == "decision":
        print("not-json", flush=True)
"""

FLOOD_BOT = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("type") == "decision":
        print("x" * 10000, flush=True)
"""

STDERR_FLOOD_BOT = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("type") == "decision":
        print("x" * 1000000, file=sys.stderr, flush=True)
"""


def python_bot(source):
    return [sys.executable, "-u", "-c", source]


class RunnerTests(unittest.TestCase):
    def test_public_manifest_hides_orderflow_secrets(self):
        public = public_manifest(MatchConfig())
        self.assertNotIn("x_prev", public["environment"])
        self.assertNotIn("x_cur", public["environment"])
        self.assertNotIn("orderflow_key", public["environment"])
        self.assertIn("environment_commitment", public)

    def test_compliant_bots_produce_verifiable_replay(self):
        replay, diagnostics = run_subprocess_match(
            python_bot(GOOD_BOT),
            python_bot(GOOD_BOT),
            MatchConfig(match_id="runner-good").with_blocks(5),
        )
        self.assertTrue(verify_replay(replay)["valid"])
        self.assertEqual(replay["summary"]["invalid_actions"], {"A": 0, "B": 0})
        self.assertIsNone(diagnostics["players"]["A"]["fault"])
        self.assertIsNone(diagnostics["players"]["B"]["fault"])

    def test_crash_is_local_to_one_bot(self):
        replay, diagnostics = run_subprocess_match(
            python_bot(CRASH_BOT),
            python_bot(GOOD_BOT),
            MatchConfig(match_id="runner-crash").with_blocks(4),
        )
        self.assertEqual(replay["summary"]["invalid_actions"], {"A": 4, "B": 0})
        self.assertEqual(diagnostics["players"]["A"]["fault"], "process_exit_7")
        verify_replay(replay)

    def test_timeout_is_hard_and_local(self):
        started = time.monotonic()
        replay, diagnostics = run_subprocess_match(
            python_bot(HANG_BOT),
            python_bot(GOOD_BOT),
            MatchConfig(match_id="runner-timeout").with_blocks(3),
            RunnerLimits(decision_timeout_ms=50),
        )
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(replay["summary"]["invalid_actions"], {"A": 3, "B": 0})
        self.assertEqual(diagnostics["players"]["A"]["fault"], "timeout")
        verify_replay(replay)

    def test_bad_stdout_is_fail_closed(self):
        for source, expected in (
            (GARBAGE_BOT, "invalid_json"),
            (FLOOD_BOT, "stdout_limit"),
        ):
            with self.subTest(expected=expected):
                replay, diagnostics = run_subprocess_match(
                    python_bot(source),
                    python_bot(GOOD_BOT),
                    MatchConfig(match_id="runner-" + expected).with_blocks(2),
                    RunnerLimits(max_stdout_bytes=1024),
                )
                self.assertEqual(replay["summary"]["invalid_actions"], {"A": 2, "B": 0})
                self.assertEqual(diagnostics["players"]["A"]["fault"], expected)
                verify_replay(replay)

    def test_stderr_flood_is_fail_closed(self):
        replay, diagnostics = run_subprocess_match(
            python_bot(STDERR_FLOOD_BOT),
            python_bot(GOOD_BOT),
            MatchConfig(match_id="runner-stderr-limit").with_blocks(2),
            RunnerLimits(max_stderr_bytes=1024),
        )
        self.assertEqual(replay["summary"]["invalid_actions"], {"A": 2, "B": 0})
        self.assertEqual(diagnostics["players"]["A"]["fault"], "stderr_limit")
        self.assertEqual(len(diagnostics["players"]["A"]["stderr"]), 1024)
        verify_replay(replay)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_python_and_node_starter_kits(self):
        root = Path(__file__).resolve().parents[1]
        replay, diagnostics = run_subprocess_match(
            [sys.executable, "-u", str(root / "starter_kits/python/bot.py")],
            ["node", str(root / "starter_kits/typescript/bot.mjs")],
            MatchConfig(match_id="runner-cross-language").with_blocks(20),
        )
        self.assertEqual(replay["summary"]["invalid_actions"], {"A": 0, "B": 0})
        self.assertIsNone(diagnostics["players"]["A"]["fault"])
        self.assertIsNone(diagnostics["players"]["B"]["fault"])
        verify_replay(replay)


if __name__ == "__main__":
    unittest.main()
