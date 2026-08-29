"""Gate 4 round-robin tournament orchestration and audit-pack verification."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from .canonical import canonical_dumps, hash_payload, strict_loads
from .engine import MatchConfig, manifest_for
from .models import EnvironmentState
from .orderflow import advance
from .rules import RULESET, Rules, score
from .runner import RunnerLimits, run_subprocess_match
from .verifier import verify_replay

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_PARTICIPANTS = 16
MAX_SEEDS = 100
MAX_SUBMISSION_FILES = 1_000
MAX_SUBMISSION_BYTES = 20 * 1024 * 1024
MAX_TOURNAMENT_EVENTS = 1_000_000


class TournamentError(ValueError):
    pass


@dataclass(frozen=True)
class Participant:
    participant_id: str
    source: Path
    command: tuple[str, ...]
    entrypoint: str | None = None


@dataclass(frozen=True)
class TournamentConfig:
    tournament_id: str
    participants: tuple[Participant, ...]
    blocks: int = 300
    seed_count: int = 3
    limits: RunnerLimits = field(default_factory=RunnerLimits)


@dataclass(frozen=True)
class FrozenParticipant:
    participant_id: str
    root: Path
    entrypoint: str
    command_template: tuple[str, ...]
    command: tuple[str, ...]
    submission_hash: str

    def public_json(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "submission_hash": self.submission_hash,
            "entrypoint": self.entrypoint,
            "command": list(self.command_template),
        }


@dataclass(frozen=True)
class DerivedSeed:
    seed_index: int
    seed_id: str
    x_prev: Decimal
    x_cur: Decimal
    orderflow_key: str
    tie_break_offset: int


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    return strict_loads(path.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise TournamentError("unsafe_relative_path:" + value)
    return path


def _submission_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TournamentError("submission_symlink:" + path.as_posix())
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise TournamentError("submission_special_file:" + path.as_posix())
    if not files:
        raise TournamentError("submission_empty")
    if len(files) > MAX_SUBMISSION_FILES:
        raise TournamentError("submission_file_count_limit")
    if sum(path.stat().st_size for path in files) > MAX_SUBMISSION_BYTES:
        raise TournamentError("submission_size_limit")
    return files


def submission_hash(root: Path) -> str:
    files = _submission_files(root)
    body = {
        "schema": "submission-snapshot.v1",
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
            for path in files
        ],
    }
    return hash_payload(body)


def _validate_config(config: TournamentConfig) -> None:
    if not ID_RE.fullmatch(config.tournament_id):
        raise TournamentError("tournament_id_format")
    if not 2 <= len(config.participants) <= MAX_PARTICIPANTS:
        raise TournamentError("participant_count")
    if not 1 <= config.blocks <= 1_000_000:
        raise TournamentError("blocks_out_of_range")
    if not 1 <= config.seed_count <= MAX_SEEDS:
        raise TournamentError("seed_count_out_of_range")
    event_count = (
        len(config.participants)
        * (len(config.participants) - 1)
        * config.seed_count
        * config.blocks
    )
    if event_count > MAX_TOURNAMENT_EVENTS:
        raise TournamentError("tournament_event_limit")
    config.limits.validate()
    ids = [participant.participant_id for participant in config.participants]
    if len(set(ids)) != len(ids):
        raise TournamentError("duplicate_participant_id")
    for participant in config.participants:
        if not ID_RE.fullmatch(participant.participant_id):
            raise TournamentError("participant_id_format")
        if not participant.command or any(
            not isinstance(token, str) or not token for token in participant.command
        ):
            raise TournamentError("participant_command")
        if not any(
            "{entrypoint}" in token or "{submission}" in token
            for token in participant.command
        ):
            raise TournamentError("participant_command_missing_placeholder")


def _copy_submission(participant: Participant, destination: Path) -> FrozenParticipant:
    if participant.source.is_symlink():
        raise TournamentError("submission_root_symlink")
    source = participant.source.resolve(strict=True)
    destination.mkdir(parents=True)
    if source.is_file():
        target = destination / source.name
        shutil.copyfile(source, target)
        entrypoint = source.name
        if participant.entrypoint not in (None, entrypoint):
            raise TournamentError("file_entrypoint_mismatch")
    elif source.is_dir():
        if participant.entrypoint is None:
            raise TournamentError("directory_entrypoint_required")
        entrypoint_path = _safe_relative_path(participant.entrypoint)
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            target = destination / relative
            if path.is_symlink():
                raise TournamentError("submission_symlink:" + relative.as_posix())
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            else:
                raise TournamentError("submission_special_file:" + relative.as_posix())
        entrypoint = entrypoint_path.as_posix()
    else:
        raise TournamentError("submission_not_file_or_directory")
    files = _submission_files(destination)
    entrypoint_path = destination.joinpath(*PurePosixPath(entrypoint).parts)
    if not entrypoint_path.is_file():
        raise TournamentError("entrypoint_not_file")
    frozen_hash = submission_hash(destination)
    for path in files:
        path.chmod(0o444)
    command = tuple(
        token.replace("{entrypoint}", str(entrypoint_path)).replace(
            "{submission}", str(destination)
        )
        for token in participant.command
    )
    return FrozenParticipant(
        participant.participant_id,
        destination,
        entrypoint,
        participant.command,
        command,
        frozen_hash,
    )


def _derive_int(master_seed: str, label: str) -> int:
    return int.from_bytes(
        hmac.new(
            bytes.fromhex(master_seed), label.encode("utf-8"), hashlib.sha256
        ).digest(),
        "big",
    )


def derive_seeds(
    master_seed: str, seed_count: int, blocks: int, rules: Rules | None = None
) -> list[DerivedSeed]:
    if not isinstance(master_seed, str) or not HEX_64_RE.fullmatch(master_seed):
        raise TournamentError("master_seed_format")
    match_rules = replace(rules or Rules(), blocks=blocks)
    seeds = []
    for seed_index in range(seed_count):
        for attempt in range(10_000):
            prefix = f"blockmarket-tournament:{seed_index}:{attempt}"
            x_prev = Decimal(
                _derive_int(master_seed, prefix + ":x-prev") % 101 - 50
            ) / Decimal(100)
            x_cur = Decimal(
                _derive_int(master_seed, prefix + ":x-cur") % 101 - 50
            ) / Decimal(100)
            state = EnvironmentState(x_prev, x_cur)
            try:
                for _ in range(blocks):
                    state = advance(state, match_rules)
            except RuntimeError:
                continue
            orderflow_key = hmac.new(
                bytes.fromhex(master_seed),
                (prefix + ":orderflow-key").encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            tie_break_offset = _derive_int(master_seed, prefix + ":tie-break") % 2
            seed_id = hash_payload(
                {
                    "seed_index": seed_index,
                    "x_prev": format(x_prev, "f"),
                    "x_cur": format(x_cur, "f"),
                    "orderflow_key": orderflow_key,
                    "tie_break_offset": tie_break_offset,
                }
            )[:16]
            seeds.append(
                DerivedSeed(
                    seed_index,
                    seed_id,
                    x_prev,
                    x_cur,
                    orderflow_key,
                    tie_break_offset,
                )
            )
            break
        else:
            raise TournamentError("safe_seed_derivation_failed")
    return seeds


def _match_id(
    tournament_id: str,
    first_id: str,
    second_id: str,
    seed_index: int,
    leg: str,
) -> str:
    return f"{tournament_id}-{first_id}-{second_id}-s{seed_index:03d}-{leg.lower()}"


def _runner_limits_json(limits: RunnerLimits) -> dict[str, int]:
    return {
        "decision_timeout_ms": limits.decision_timeout_ms,
        "max_stdout_bytes": limits.max_stdout_bytes,
        "max_stderr_bytes": limits.max_stderr_bytes,
    }


def _sanitize_diagnostics(
    diagnostics: dict[str, Any],
    player_ids: dict[str, str],
    frozen: dict[str, FrozenParticipant],
) -> dict[str, Any]:
    sanitized = json.loads(canonical_dumps(diagnostics))
    for position, participant_id in player_ids.items():
        sanitized["players"][position]["command"] = list(
            frozen[participant_id].command_template
        )
        sanitized["players"][position]["participant_id"] = participant_id
    return sanitized


def _public_config(
    config: TournamentConfig, frozen: tuple[FrozenParticipant, ...]
) -> dict[str, Any]:
    return {
        "schema": "tournament-config.v1",
        "tournament_id": config.tournament_id,
        "ruleset": RULESET,
        "blocks": config.blocks,
        "seed_count": config.seed_count,
        "runner_limits": _runner_limits_json(config.limits),
        "participants": [participant.public_json() for participant in frozen],
    }


def _build_result(
    public_config: dict[str, Any],
    commitment: dict[str, Any],
    match_records: list[dict[str, Any]],
) -> dict[str, Any]:
    participant_ids = [
        participant["participant_id"] for participant in public_config["participants"]
    ]
    stats = {
        participant_id: {
            "total_score": Decimal(0),
            "paired_games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "invalid_actions": 0,
            "runner_faults": 0,
        }
        for participant_id in participant_ids
    }
    record_lookup = {
        (
            record["first_id"],
            record["second_id"],
            record["seed_index"],
            record["leg"],
        ): record
        for record in match_records
    }
    pair_results = []
    for first_index, first_id in enumerate(participant_ids):
        for second_id in participant_ids[first_index + 1 :]:
            seed_results = []
            for seed_index in range(public_config["seed_count"]):
                ab = record_lookup[(first_id, second_id, seed_index, "AB")]
                ba = record_lookup[(first_id, second_id, seed_index, "BA")]
                score_first = (
                    Decimal(ab["replay"]["summary"]["scores"]["A"])
                    + Decimal(ba["replay"]["summary"]["scores"]["B"])
                ) / Decimal(2)
                score_second = -score_first
                for participant_id, value in (
                    (first_id, score_first),
                    (second_id, score_second),
                ):
                    stats[participant_id]["total_score"] += value
                    stats[participant_id]["paired_games"] += 1
                    if value > 0:
                        stats[participant_id]["wins"] += 1
                    elif value < 0:
                        stats[participant_id]["losses"] += 1
                    else:
                        stats[participant_id]["draws"] += 1
                stats[first_id]["invalid_actions"] += (
                    ab["replay"]["summary"]["invalid_actions"]["A"]
                    + ba["replay"]["summary"]["invalid_actions"]["B"]
                )
                stats[second_id]["invalid_actions"] += (
                    ab["replay"]["summary"]["invalid_actions"]["B"]
                    + ba["replay"]["summary"]["invalid_actions"]["A"]
                )
                stats[first_id]["runner_faults"] += sum(
                    record["diagnostics"]["players"][position]["fault"] is not None
                    for record, position in ((ab, "A"), (ba, "B"))
                )
                stats[second_id]["runner_faults"] += sum(
                    record["diagnostics"]["players"][position]["fault"] is not None
                    for record, position in ((ab, "B"), (ba, "A"))
                )
                seed_results.append(
                    {
                        "seed_index": seed_index,
                        "seed_id": ab["seed_id"],
                        "score_first": score(score_first),
                        "score_second": score(score_second),
                        "ab_match_id": ab["match_id"],
                        "ba_match_id": ba["match_id"],
                    }
                )
            pair_mean = sum(
                Decimal(item["score_first"]) for item in seed_results
            ) / Decimal(len(seed_results))
            pair_results.append(
                {
                    "first_id": first_id,
                    "second_id": second_id,
                    "mean_score_first": score(pair_mean),
                    "mean_score_second": score(-pair_mean),
                    "seeds": seed_results,
                }
            )
    standings = []
    for participant in public_config["participants"]:
        participant_id = participant["participant_id"]
        item = stats[participant_id]
        mean_score = item["total_score"] / Decimal(item["paired_games"])
        standings.append(
            {
                "participant_id": participant_id,
                "submission_hash": participant["submission_hash"],
                "mean_score": score(mean_score),
                "total_score": score(item["total_score"]),
                "paired_games": item["paired_games"],
                "wins": item["wins"],
                "draws": item["draws"],
                "losses": item["losses"],
                "invalid_actions": item["invalid_actions"],
                "runner_faults": item["runner_faults"],
            }
        )
    standings.sort(
        key=lambda item: (
            -Decimal(item["mean_score"]),
            -item["wins"],
            item["participant_id"],
        )
    )
    previous_key = None
    previous_rank = 0
    for index, item in enumerate(standings, 1):
        rank_key = (item["mean_score"], item["wins"], item["draws"], item["losses"])
        if rank_key != previous_key:
            previous_rank = index
            previous_key = rank_key
        item["rank"] = previous_rank
    replay_index = [
        {
            "match_id": record["match_id"],
            "seed_index": record["seed_index"],
            "seed_id": record["seed_id"],
            "leg": record["leg"],
            "player_a": record["player_a"],
            "player_b": record["player_b"],
            "replay_path": record["replay_path"],
            "replay_sha256": record["replay_sha256"],
            "diagnostics_path": record["diagnostics_path"],
            "diagnostics_sha256": record["diagnostics_sha256"],
        }
        for record in match_records
    ]
    body = {
        "schema": "tournament-result.v1",
        "ruleset": RULESET,
        "tournament_id": public_config["tournament_id"],
        "seed_commitment": commitment["reveal_hash"],
        "blocks": public_config["blocks"],
        "seed_count": public_config["seed_count"],
        "paired_comparisons": len(pair_results) * public_config["seed_count"],
        "match_count": len(match_records),
        "standings": standings,
        "pair_results": pair_results,
        "replays": replay_index,
    }
    result = dict(body)
    result["result_hash"] = hash_payload(body)
    return result


def _build_audit_manifest(root: Path, tournament_id: str) -> dict[str, Any]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TournamentError("audit_symlink:" + path.as_posix())
        if path.is_file() and path != root / "audit-manifest.json":
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _file_sha256(path),
                }
            )
    body = {
        "schema": "audit-manifest.v1",
        "tournament_id": tournament_id,
        "files": files,
    }
    manifest = dict(body)
    manifest["audit_hash"] = hash_payload(body)
    return manifest


def run_tournament(
    config: TournamentConfig,
    output_dir: Path,
    master_seed: str | None = None,
) -> dict[str, Any]:
    _validate_config(config)
    master_seed = master_seed or secrets.token_hex(32)
    if not HEX_64_RE.fullmatch(master_seed):
        raise TournamentError("master_seed_format")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise TournamentError("output_directory_exists")
    for participant in config.participants:
        source = participant.source.resolve(strict=True)
        if source.is_dir() and output_dir.is_relative_to(source):
            raise TournamentError("output_inside_submission")
    output_dir.mkdir(parents=True)
    reveal = {
        "schema": "seed-reveal.v1",
        "tournament_id": config.tournament_id,
        "master_seed": master_seed,
        "seed_count": config.seed_count,
    }
    commitment = {
        "schema": "seed-commitment.v1",
        "tournament_id": config.tournament_id,
        "seed_count": config.seed_count,
        "reveal_hash": hash_payload(reveal),
    }
    with tempfile.TemporaryDirectory(prefix="blockmarket-frozen-") as staging_name:
        staging_root = Path(staging_name)
        frozen = tuple(
            _copy_submission(
                participant,
                staging_root / participant.participant_id,
            )
            for participant in config.participants
        )
        frozen_by_id = {item.participant_id: item for item in frozen}
        public_config = _public_config(config, frozen)
        commitment["tournament_config_hash"] = hash_payload(public_config)
        _write_json(output_dir / "tournament.json", public_config)
        _write_json(output_dir / "seed-commitment.json", commitment)
        seeds = derive_seeds(master_seed, config.seed_count, config.blocks)
        match_records = []
        for first_index, first in enumerate(frozen):
            for second in frozen[first_index + 1 :]:
                for derived in seeds:
                    for leg in ("AB", "BA"):
                        player_a = first if leg == "AB" else second
                        player_b = second if leg == "AB" else first
                        match_id = _match_id(
                            config.tournament_id,
                            first.participant_id,
                            second.participant_id,
                            derived.seed_index,
                            leg,
                        )
                        match_config = MatchConfig(
                            match_id=match_id,
                            rules=Rules(blocks=config.blocks),
                            x_prev=derived.x_prev,
                            x_cur=derived.x_cur,
                            tie_break_offset=derived.tie_break_offset,
                            orderflow_key=derived.orderflow_key,
                        )
                        replay, diagnostics = run_subprocess_match(
                            player_a.command,
                            player_b.command,
                            match_config,
                            config.limits,
                        )
                        for participant in frozen:
                            if (
                                submission_hash(participant.root)
                                != participant.submission_hash
                            ):
                                raise TournamentError(
                                    "submission_changed:" + participant.participant_id
                                )
                        if (
                            _read_json(output_dir / "tournament.json") != public_config
                            or _read_json(output_dir / "seed-commitment.json")
                            != commitment
                        ):
                            raise TournamentError("pre_match_commitment_changed")
                        sanitized = _sanitize_diagnostics(
                            diagnostics,
                            {
                                "A": player_a.participant_id,
                                "B": player_b.participant_id,
                            },
                            frozen_by_id,
                        )
                        match_records.append(
                            {
                                "match_id": match_id,
                                "first_id": first.participant_id,
                                "second_id": second.participant_id,
                                "seed_index": derived.seed_index,
                                "seed_id": derived.seed_id,
                                "leg": leg,
                                "player_a": player_a.participant_id,
                                "player_b": player_b.participant_id,
                                "replay": replay,
                                "diagnostics": sanitized,
                            }
                        )
        for participant in frozen:
            shutil.copytree(
                participant.root,
                output_dir / "submissions" / participant.participant_id,
                copy_function=shutil.copyfile,
            )
        for record in match_records:
            match_id = record["match_id"]
            replay_rel = f"replays/{match_id}.json"
            diagnostics_rel = f"diagnostics/{match_id}.json"
            replay_path = output_dir / replay_rel
            diagnostics_path = output_dir / diagnostics_rel
            _write_json(replay_path, record["replay"])
            _write_json(diagnostics_path, record["diagnostics"])
            record.update(
                {
                    "replay_path": replay_rel,
                    "replay_sha256": _file_sha256(replay_path),
                    "diagnostics_path": diagnostics_rel,
                    "diagnostics_sha256": _file_sha256(diagnostics_path),
                }
            )
    _write_json(output_dir / "seed-reveal.json", reveal)
    result = _build_result(public_config, commitment, match_records)
    _write_json(output_dir / "result.json", result)
    audit_manifest = _build_audit_manifest(output_dir, config.tournament_id)
    _write_json(output_dir / "audit-manifest.json", audit_manifest)
    verify_audit_pack(output_dir)
    return result


def _match_record_from_index(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    replay_path = _safe_relative_path(item["replay_path"])
    diagnostics_path = _safe_relative_path(item["diagnostics_path"])
    replay_file = root.joinpath(*replay_path.parts)
    diagnostics_file = root.joinpath(*diagnostics_path.parts)
    if _file_sha256(replay_file) != item["replay_sha256"]:
        raise TournamentError("replay_index_hash:" + item["match_id"])
    if _file_sha256(diagnostics_file) != item["diagnostics_sha256"]:
        raise TournamentError("diagnostics_index_hash:" + item["match_id"])
    replay = _read_json(replay_file)
    diagnostics = _read_json(diagnostics_file)
    verify_replay(replay)
    return {
        "match_id": item["match_id"],
        "seed_index": item["seed_index"],
        "seed_id": item["seed_id"],
        "leg": item["leg"],
        "player_a": item["player_a"],
        "player_b": item["player_b"],
        "replay_path": item["replay_path"],
        "replay_sha256": item["replay_sha256"],
        "diagnostics_path": item["diagnostics_path"],
        "diagnostics_sha256": item["diagnostics_sha256"],
        "replay": replay,
        "diagnostics": diagnostics,
    }


def verify_audit_pack(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise TournamentError("audit_pack_not_directory")
    manifest = _read_json(root / "audit-manifest.json")
    manifest_body = dict(manifest)
    claimed_audit_hash = manifest_body.pop("audit_hash", None)
    if claimed_audit_hash != hash_payload(manifest_body):
        raise TournamentError("audit_manifest_hash")
    actual_files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TournamentError("audit_symlink:" + path.as_posix())
        if path.is_file() and path != root / "audit-manifest.json":
            actual_files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _file_sha256(path),
                }
            )
    if manifest.get("files") != actual_files:
        raise TournamentError("audit_file_manifest")
    public_config = _read_json(root / "tournament.json")
    commitment = _read_json(root / "seed-commitment.json")
    reveal = _read_json(root / "seed-reveal.json")
    result = _read_json(root / "result.json")
    if commitment.get("reveal_hash") != hash_payload(reveal):
        raise TournamentError("seed_commitment")
    if commitment.get("tournament_config_hash") != hash_payload(public_config):
        raise TournamentError("tournament_config_commitment")
    if not HEX_64_RE.fullmatch(reveal.get("master_seed", "")):
        raise TournamentError("seed_reveal_format")
    if not (
        manifest.get("tournament_id")
        == public_config.get("tournament_id")
        == commitment.get("tournament_id")
        == reveal.get("tournament_id")
        == result.get("tournament_id")
    ):
        raise TournamentError("tournament_id_mismatch")
    if public_config.get("ruleset") != RULESET or result.get("ruleset") != RULESET:
        raise TournamentError("ruleset_mismatch")
    if reveal.get("seed_count") != public_config.get("seed_count"):
        raise TournamentError("seed_count_mismatch")
    participants = public_config.get("participants")
    if (
        not isinstance(participants, list)
        or not 2 <= len(participants) <= MAX_PARTICIPANTS
    ):
        raise TournamentError("public_participants")
    participant_ids = [item["participant_id"] for item in participants]
    if len(set(participant_ids)) != len(participant_ids):
        raise TournamentError("public_participant_ids")
    for participant in participants:
        participant_root = root / "submissions" / participant["participant_id"]
        if submission_hash(participant_root) != participant["submission_hash"]:
            raise TournamentError("submission_hash:" + participant["participant_id"])
        entrypoint = _safe_relative_path(participant["entrypoint"])
        if not participant_root.joinpath(*entrypoint.parts).is_file():
            raise TournamentError(
                "submission_entrypoint:" + participant["participant_id"]
            )
    seeds = derive_seeds(
        reveal["master_seed"],
        public_config["seed_count"],
        public_config["blocks"],
    )
    seed_by_index = {seed.seed_index: seed for seed in seeds}
    indexed_records = [
        _match_record_from_index(root, item) for item in result["replays"]
    ]
    expected_count = (
        len(participants)
        * (len(participants) - 1)
        // 2
        * public_config["seed_count"]
        * 2
    )
    if len(indexed_records) != expected_count:
        raise TournamentError("match_count")
    record_lookup = {}
    for record in indexed_records:
        key = (record["player_a"], record["player_b"], record["seed_index"])
        if key in record_lookup:
            raise TournamentError("duplicate_match_record")
        record_lookup[key] = record
        derived = seed_by_index.get(record["seed_index"])
        if derived is None or record["seed_id"] != derived.seed_id:
            raise TournamentError("derived_seed_id")
        expected_config = MatchConfig(
            match_id=record["match_id"],
            rules=Rules(blocks=public_config["blocks"]),
            x_prev=derived.x_prev,
            x_cur=derived.x_cur,
            tie_break_offset=derived.tie_break_offset,
            orderflow_key=derived.orderflow_key,
        )
        if record["replay"]["manifest"] != manifest_for(expected_config):
            raise TournamentError("replay_seed_or_manifest:" + record["match_id"])
    normalized_records = []
    for first_index, first_id in enumerate(participant_ids):
        for second_id in participant_ids[first_index + 1 :]:
            for seed in seeds:
                for leg, player_a, player_b in (
                    ("AB", first_id, second_id),
                    ("BA", second_id, first_id),
                ):
                    record = record_lookup.get((player_a, player_b, seed.seed_index))
                    expected_match_id = _match_id(
                        public_config["tournament_id"],
                        first_id,
                        second_id,
                        seed.seed_index,
                        leg,
                    )
                    if (
                        record is None
                        or record["match_id"] != expected_match_id
                        or record["leg"] != leg
                    ):
                        raise TournamentError("schedule_mismatch:" + expected_match_id)
                    normalized_records.append(
                        {
                            **record,
                            "first_id": first_id,
                            "second_id": second_id,
                        }
                    )
    expected_result = _build_result(public_config, commitment, normalized_records)
    if result != expected_result:
        raise TournamentError("tournament_result")
    return {
        "valid": True,
        "tournament_id": result["tournament_id"],
        "participants_verified": len(participants),
        "matches_verified": len(indexed_records),
        "audit_hash": manifest["audit_hash"],
        "result_hash": result["result_hash"],
    }


def config_from_request(value: Any, base_dir: Path) -> TournamentConfig:
    if not isinstance(value, dict) or set(value) != {
        "tournament_id",
        "blocks",
        "seed_count",
        "runner_limits",
        "participants",
    }:
        raise TournamentError("request_fields")
    raw_limits = value["runner_limits"]
    if not isinstance(raw_limits, dict) or set(raw_limits) != {
        "decision_timeout_ms",
        "max_stdout_bytes",
        "max_stderr_bytes",
    }:
        raise TournamentError("request_runner_limits")
    raw_participants = value["participants"]
    if not isinstance(raw_participants, list):
        raise TournamentError("request_participants")
    participants = []
    for raw in raw_participants:
        if (
            not isinstance(raw, dict)
            or not set(raw)
            <= {
                "participant_id",
                "source",
                "entrypoint",
                "command",
            }
            or not {"participant_id", "source", "command"} <= set(raw)
        ):
            raise TournamentError("request_participant_fields")
        source = Path(raw["source"])
        if not source.is_absolute():
            source = base_dir / source
        command = raw["command"]
        if not isinstance(command, list):
            raise TournamentError("request_participant_command")
        participants.append(
            Participant(
                participant_id=raw["participant_id"],
                source=source,
                command=tuple(command),
                entrypoint=raw.get("entrypoint"),
            )
        )
    return TournamentConfig(
        tournament_id=value["tournament_id"],
        participants=tuple(participants),
        blocks=value["blocks"],
        seed_count=value["seed_count"],
        limits=RunnerLimits(**raw_limits),
    )
