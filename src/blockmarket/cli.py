"""Command line entry points for matches, tournaments, and verification."""

import argparse
import json
from pathlib import Path

from .bots import BOTS, get_bot
from .canonical import canonical_dumps, strict_loads
from .engine import MatchConfig, run_match
from .matrix import payoff_matrix
from .runner import RunnerLimits, run_subprocess_match
from .tournament import config_from_request, run_tournament, verify_audit_pack
from .verifier import verify_replay


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blockmarket")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="run two in-process reference strategies")
    demo.add_argument("--bot-a", default="InventoryAware", choices=sorted(BOTS))
    demo.add_argument("--bot-b", default="TightSpread", choices=sorted(BOTS))
    demo.add_argument("--blocks", type=int, default=50)
    demo.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="independently verify a replay")
    verify.add_argument("replay", type=Path)

    matrix = subparsers.add_parser("matrix", help="run a paired-position payoff matrix")
    matrix.add_argument("--blocks", type=int, default=60)
    matrix.add_argument("--seeds", type=int, default=3)
    matrix.add_argument("--bots", nargs="+", choices=sorted(BOTS), default=sorted(BOTS))
    matrix.add_argument("--output", type=Path)

    run = subparsers.add_parser("run", help="run two JSONL subprocess Bots")
    run.add_argument("--bot-a", nargs="+", required=True, metavar="COMMAND")
    run.add_argument("--bot-b", nargs="+", required=True, metavar="COMMAND")
    run.add_argument("--blocks", type=int, default=300)
    run.add_argument("--timeout-ms", type=int, default=250)
    run.add_argument("--max-stdout-bytes", type=int, default=65_536)
    run.add_argument("--max-stderr-bytes", type=int, default=65_536)
    run.add_argument("--output", type=Path, required=True)

    tournament = subparsers.add_parser(
        "tournament", help="run a paired round-robin and export an audit pack"
    )
    tournament.add_argument("request", type=Path)
    tournament.add_argument("--output", type=Path, required=True)
    tournament.add_argument("--master-seed-file", type=Path)

    verify_tournament = subparsers.add_parser(
        "verify-tournament", help="verify a complete tournament audit pack"
    )
    verify_tournament.add_argument("audit_pack", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        replay = run_match(
            get_bot(args.bot_a),
            get_bot(args.bot_b),
            MatchConfig(match_id=f"demo-{args.bot_a}-{args.bot_b}").with_blocks(
                args.blocks
            ),
        )
        _write_json(args.output, replay)
        print(canonical_dumps(replay["summary"]))
        return
    if args.command == "verify":
        replay = strict_loads(args.replay.read_text(encoding="utf-8"))
        print(canonical_dumps(verify_replay(replay)))
        return
    if args.command == "matrix":
        result = payoff_matrix(args.bots, seeds=args.seeds, blocks=args.blocks)
        if args.output:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.command == "run":
        replay, diagnostics = run_subprocess_match(
            args.bot_a,
            args.bot_b,
            MatchConfig(match_id="subprocess-match").with_blocks(args.blocks),
            RunnerLimits(
                decision_timeout_ms=args.timeout_ms,
                max_stdout_bytes=args.max_stdout_bytes,
                max_stderr_bytes=args.max_stderr_bytes,
            ),
        )
        _write_json(args.output, replay)
        print(canonical_dumps({"summary": replay["summary"], "runner": diagnostics}))
        return
    if args.command == "tournament":
        request = strict_loads(args.request.read_text(encoding="utf-8"))
        config = config_from_request(request, args.request.resolve().parent)
        master_seed = (
            args.master_seed_file.read_text(encoding="utf-8").strip()
            if args.master_seed_file
            else None
        )
        result = run_tournament(config, args.output, master_seed)
        print(
            canonical_dumps(
                {
                    "tournament_id": result["tournament_id"],
                    "result_hash": result["result_hash"],
                    "standings": result["standings"],
                }
            )
        )
        return
    if args.command == "verify-tournament":
        print(canonical_dumps(verify_audit_pack(args.audit_pack)))
        return
    raise AssertionError("unreachable")
