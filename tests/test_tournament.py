import hashlib
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from blockmarket.canonical import hash_payload
from blockmarket.runner import RunnerLimits
from blockmarket.tournament import (
    Participant,
    TournamentConfig,
    TournamentError,
    config_from_request,
    derive_seeds,
    run_tournament,
    verify_audit_pack,
)

MASTER_SEED = "11" * 32

NO_QUOTE_BOT = """\
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if message.get("type") == "decision":
        seq = message["observation"]["block_seq"]
        print(json.dumps({"decision_seq": seq, "bid": None, "ask": None}), flush=True)
    elif message.get("type") == "end":
        break
"""


def refresh_audit_manifest(root):
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root / "audit-manifest.json":
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    body = {
        "schema": "audit-manifest.v1",
        "tournament_id": "test-alpha",
        "files": files,
    }
    manifest = {**body, "audit_hash": hash_payload(body)}
    (root / "audit-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class TournamentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.no_quote = self.root / "no_quote.py"
        self.no_quote.write_text(NO_QUOTE_BOT, encoding="utf-8")
        project_root = Path(__file__).resolve().parents[1]
        self.signal_bot = project_root / "starter_kits/python/bot.py"
        self.config = TournamentConfig(
            tournament_id="test-alpha",
            participants=(
                Participant(
                    "signal",
                    self.signal_bot,
                    (sys.executable, "-u", "{entrypoint}"),
                ),
                Participant(
                    "noquote",
                    self.no_quote,
                    (sys.executable, "-u", "{entrypoint}"),
                ),
            ),
            blocks=8,
            seed_count=2,
            limits=RunnerLimits(decision_timeout_ms=250),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_round_robin_exports_self_verifying_audit_pack(self):
        output = self.root / "audit"
        result = run_tournament(self.config, output, MASTER_SEED)
        verification = verify_audit_pack(output)
        self.assertTrue(verification["valid"])
        self.assertEqual(verification["participants_verified"], 2)
        self.assertEqual(verification["matches_verified"], 4)
        self.assertEqual(result["match_count"], 4)
        self.assertEqual(result["paired_comparisons"], 2)
        self.assertEqual(len(result["standings"]), 2)
        self.assertEqual(
            Decimal(result["standings"][0]["total_score"])
            + Decimal(result["standings"][1]["total_score"]),
            0,
        )
        self.assertTrue((output / "seed-commitment.json").is_file())
        self.assertTrue((output / "seed-reveal.json").is_file())
        self.assertTrue((output / "audit-manifest.json").is_file())

    def test_same_seed_and_submissions_are_byte_deterministic(self):
        first = self.root / "audit-first"
        second = self.root / "audit-second"
        first_result = run_tournament(self.config, first, MASTER_SEED)
        second_result = run_tournament(self.config, second, MASTER_SEED)
        self.assertEqual(first_result, second_result)
        self.assertEqual(
            json.loads((first / "audit-manifest.json").read_text())["audit_hash"],
            json.loads((second / "audit-manifest.json").read_text())["audit_hash"],
        )

    def test_three_participant_round_robin_schedule(self):
        output = self.root / "audit-three"
        config = TournamentConfig(
            tournament_id="three-way",
            participants=(
                *self.config.participants,
                Participant(
                    "third",
                    self.no_quote,
                    (sys.executable, "-u", "{entrypoint}"),
                ),
            ),
            blocks=3,
            seed_count=2,
        )
        result = run_tournament(config, output, MASTER_SEED)
        self.assertEqual(result["match_count"], 12)
        self.assertEqual(result["paired_comparisons"], 6)
        self.assertEqual(
            [standing["paired_games"] for standing in result["standings"]],
            [4, 4, 4],
        )
        self.assertEqual(verify_audit_pack(output)["matches_verified"], 12)

    def test_any_file_tamper_breaks_audit_pack(self):
        output = self.root / "audit-tampered"
        run_tournament(self.config, output, MASTER_SEED)
        result_path = output / "result.json"
        value = json.loads(result_path.read_text())
        value["standings"][0]["rank"] = 99
        result_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(TournamentError, "audit_file_manifest"):
            verify_audit_pack(output)

    def test_rehashed_seed_tamper_is_caught_against_replays(self):
        output = self.root / "audit-seed-tampered"
        run_tournament(self.config, output, MASTER_SEED)
        reveal_path = output / "seed-reveal.json"
        commitment_path = output / "seed-commitment.json"
        reveal = json.loads(reveal_path.read_text())
        reveal["master_seed"] = "22" * 32
        reveal_path.write_text(
            json.dumps(reveal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        commitment = json.loads(commitment_path.read_text())
        commitment["reveal_hash"] = hash_payload(reveal)
        commitment_path.write_text(
            json.dumps(commitment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        refresh_audit_manifest(output)
        with self.assertRaisesRegex(
            TournamentError, "derived_seed_id|replay_seed_or_manifest"
        ):
            verify_audit_pack(output)

    def test_seed_derivation_is_deterministic_and_distinct(self):
        first = derive_seeds(MASTER_SEED, 3, 20)
        second = derive_seeds(MASTER_SEED, 3, 20)
        self.assertEqual(first, second)
        self.assertEqual(len({seed.seed_id for seed in first}), 3)
        self.assertEqual(len({seed.orderflow_key for seed in first}), 3)

    def test_request_resolves_relative_sources(self):
        request = {
            "tournament_id": "request-test",
            "blocks": 5,
            "seed_count": 1,
            "runner_limits": {
                "decision_timeout_ms": 100,
                "max_stdout_bytes": 1024,
                "max_stderr_bytes": 1024,
            },
            "participants": [
                {
                    "participant_id": "one",
                    "source": "no_quote.py",
                    "command": [sys.executable, "{entrypoint}"],
                },
                {
                    "participant_id": "two",
                    "source": "no_quote.py",
                    "command": [sys.executable, "{entrypoint}"],
                },
            ],
        }
        parsed = config_from_request(request, self.root)
        self.assertEqual(parsed.participants[0].source, self.no_quote)

    def test_existing_output_is_never_overwritten(self):
        output = self.root / "existing"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(TournamentError, "output_directory_exists"):
            run_tournament(self.config, output, MASTER_SEED)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_secret_artifacts_are_not_written_until_matches_finish(self):
        output = self.root / "audit-secret-lifecycle"
        probe = self.root / "probe.py"
        probe.write_text(
            NO_QUOTE_BOT.replace(
                'if message.get("type") == "decision":',
                'if message.get("type") == "decision":\n'
                f'        audit = __import__("pathlib").Path({str(output)!r})\n'
                '        secret_paths = [audit / "seed-reveal.json", '
                'audit / "replays", audit / "submissions"]\n'
                "        if any(path.exists() for path in secret_paths):\n"
                '            print("secret-exposed", file=sys.stderr, flush=True)',
            ),
            encoding="utf-8",
        )
        config = TournamentConfig(
            tournament_id="secret-lifecycle",
            participants=(
                Participant("one", probe, (sys.executable, "-u", "{entrypoint}")),
                Participant("two", probe, (sys.executable, "-u", "{entrypoint}")),
            ),
            blocks=3,
            seed_count=1,
        )
        run_tournament(config, output, MASTER_SEED)
        for path in (output / "diagnostics").glob("*.json"):
            diagnostics = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(diagnostics["players"]["A"]["stderr"], "")
            self.assertEqual(diagnostics["players"]["B"]["stderr"], "")

    def test_submission_self_modification_aborts_before_seed_reveal(self):
        mutating = self.root / "mutating.py"
        mutating.write_text(
            NO_QUOTE_BOT.replace(
                'if message.get("type") == "decision":',
                'if message.get("type") == "decision":\n'
                '        __import__("os").chmod(__file__, 0o644)\n'
                '        with open(__file__, "a", encoding="utf-8") as stream:\n'
                '            stream.write("\\n# mutated")',
            ),
            encoding="utf-8",
        )
        output = self.root / "audit-mutated-submission"
        config = TournamentConfig(
            tournament_id="mutated-submission",
            participants=(
                Participant(
                    "mutator", mutating, (sys.executable, "-u", "{entrypoint}")
                ),
                Participant(
                    "stable", self.no_quote, (sys.executable, "-u", "{entrypoint}")
                ),
            ),
            blocks=2,
            seed_count=1,
        )
        with self.assertRaisesRegex(TournamentError, "submission_changed:mutator"):
            run_tournament(config, output, MASTER_SEED)
        self.assertTrue((output / "seed-commitment.json").is_file())
        self.assertFalse((output / "seed-reveal.json").exists())

    def test_event_volume_limit_is_fail_closed(self):
        config = TournamentConfig(
            tournament_id="too-large",
            participants=self.config.participants,
            blocks=1_000_000,
            seed_count=1,
        )
        with self.assertRaisesRegex(TournamentError, "tournament_event_limit"):
            run_tournament(config, self.root / "too-large", MASTER_SEED)


if __name__ == "__main__":
    unittest.main()
