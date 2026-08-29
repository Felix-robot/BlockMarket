"""Fault-isolated JSONL subprocess runner for untrusted local Bot programs.

This is a process boundary and protocol implementation, not an OS security
sandbox. Network and filesystem isolation belong to the later OCI gate.
"""

from __future__ import annotations

import os
import select
import subprocess
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .canonical import StrictJSONError, canonical_dumps, strict_loads
from .engine import MatchConfig, manifest_for, run_match_with_action_source
from .rules import PLAYERS


@dataclass(frozen=True)
class RunnerLimits:
    decision_timeout_ms: int = 250
    max_stdout_bytes: int = 65_536
    max_stderr_bytes: int = 65_536

    def validate(self) -> None:
        if self.decision_timeout_ms < 1:
            raise ValueError("decision_timeout_must_be_positive")
        if self.max_stdout_bytes < 128:
            raise ValueError("max_stdout_bytes_too_small")
        if self.max_stderr_bytes < 0:
            raise ValueError("max_stderr_bytes_negative")


def public_manifest(config: MatchConfig) -> dict[str, Any]:
    """Return public match metadata without hidden order-flow state or key."""
    manifest = manifest_for(config)
    environment = manifest["environment"]
    return {
        "schema": manifest["schema"],
        "ruleset": manifest["ruleset"],
        "match_id": manifest["match_id"],
        "players": manifest["players"],
        "rules": manifest["rules"],
        "environment": {
            "kind": environment["kind"],
            "a": environment["a"],
            "b": environment["b"],
            "max_abs_state": environment["max_abs_state"],
        },
        "environment_commitment": manifest["environment_commitment"],
        "tie_break_offset": manifest["tie_break_offset"],
    }


class BotProcess:
    def __init__(
        self,
        command: Sequence[str],
        player: str,
        manifest: dict[str, Any],
        limits: RunnerLimits,
    ) -> None:
        self.command = list(command)
        self.player = player
        self.limits = limits
        self.process: subprocess.Popen[bytes] | None = None
        self.fault: str | None = None
        self._stdout_buffer = bytearray()
        self._stderr = bytearray()
        self._lock = threading.RLock()
        self._stderr_thread: threading.Thread | None = None
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                close_fds=True,
            )
        except (OSError, ValueError) as exc:
            self.fault = "spawn_" + type(exc).__name__
            return
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name=f"blockmarket-stderr-{player}",
            daemon=True,
        )
        self._stderr_thread.start()
        self._write_message(
            {
                "type": "init",
                "protocol": "blockmarket-jsonl-v1",
                "player_id": player,
                "manifest": manifest,
            }
        )

    def _set_fault(self, code: str) -> None:
        with self._lock:
            if self.fault is None:
                self.fault = code
            process = self.process
            if process is not None and process.poll() is None:
                process.terminate()

    def _drain_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        while True:
            try:
                chunk = os.read(process.stderr.fileno(), 4096)
            except OSError:
                return
            if not chunk:
                return
            with self._lock:
                remaining = self.limits.max_stderr_bytes - len(self._stderr)
                if remaining > 0:
                    self._stderr.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self._set_fault("stderr_limit")
                    return

    def _write_message(self, message: dict[str, Any]) -> bool:
        if self.fault is not None:
            return False
        process = self.process
        if process is None or process.stdin is None:
            self._set_fault("stdin_unavailable")
            return False
        try:
            process.stdin.write((canonical_dumps(message) + "\n").encode("utf-8"))
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            self._set_fault("broken_pipe")
            return False
        return True

    def _read_line(self) -> bytes | None:
        process = self.process
        if process is None or process.stdout is None:
            self._set_fault("stdout_unavailable")
            return None
        deadline = time.monotonic() + self.limits.decision_timeout_ms / 1000
        while True:
            newline = self._stdout_buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._stdout_buffer[:newline])
                trailing = bytes(self._stdout_buffer[newline + 1 :])
                self._stdout_buffer.clear()
                if trailing:
                    self._set_fault("unsolicited_stdout")
                    return None
                return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._set_fault("timeout")
                return None
            readable, _, _ = select.select([process.stdout.fileno()], [], [], remaining)
            if not readable:
                self._set_fault("timeout")
                return None
            try:
                chunk = os.read(process.stdout.fileno(), 4096)
            except OSError:
                self._set_fault("stdout_read_error")
                return None
            if not chunk:
                code = process.poll()
                if code is None:
                    try:
                        code = process.wait(timeout=min(remaining, 0.05))
                    except subprocess.TimeoutExpired:
                        code = None
                self._set_fault(
                    "process_exit" if code is None else f"process_exit_{code}"
                )
                return None
            self._stdout_buffer.extend(chunk)
            if len(self._stdout_buffer) > self.limits.max_stdout_bytes:
                self._set_fault("stdout_limit")
                return None

    def request(self, observation: dict[str, Any]) -> Any:
        if self.fault is not None:
            return {"runner_error": self.fault}
        if not self._write_message({"type": "decision", "observation": observation}):
            return {"runner_error": self.fault or "write_failed"}
        line = self._read_line()
        if line is None:
            return {"runner_error": self.fault or "read_failed"}
        if len(line) > self.limits.max_stdout_bytes:
            self._set_fault("stdout_limit")
            return {"runner_error": self.fault}
        try:
            text = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self._set_fault("stdout_utf8")
            return {"runner_error": self.fault}
        try:
            return strict_loads(text)
        except StrictJSONError:
            self._set_fault("invalid_json")
            return {"runner_error": self.fault}

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None and self.fault is None:
            self._write_message({"type": "end"})
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.2)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=0.2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def diagnostics(self) -> dict[str, Any]:
        process = self.process
        return {
            "command": list(self.command),
            "fault": self.fault,
            "exit_code": process.poll() if process is not None else None,
            "stderr": self._stderr.decode("utf-8", errors="replace"),
        }


def run_subprocess_match(
    command_a: Sequence[str],
    command_b: Sequence[str],
    config: MatchConfig | None = None,
    limits: RunnerLimits | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = config or MatchConfig()
    limits = limits or RunnerLimits()
    limits.validate()
    manifest = public_manifest(config)
    processes = {
        "A": BotProcess(command_a, "A", manifest, limits),
        "B": BotProcess(command_b, "B", manifest, limits),
    }
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="blockmarket-bot")

    def action_source(observations: dict[str, dict[str, Any]]) -> dict[str, Any]:
        futures = {
            player: executor.submit(processes[player].request, observations[player])
            for player in PLAYERS
        }
        submissions = {}
        for player in PLAYERS:
            try:
                submissions[player] = futures[player].result()
            except Exception as exc:  # noqa: BLE001 - preserve the other Bot's turn.
                code = "runner_internal_" + type(exc).__name__
                processes[player]._set_fault(code)
                submissions[player] = {"runner_error": code}
        return submissions

    try:
        replay = run_match_with_action_source(action_source, config)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        for process in processes.values():
            process.close()
    diagnostics = {
        "protocol": "blockmarket-jsonl-v1",
        "limits": {
            "decision_timeout_ms": limits.decision_timeout_ms,
            "max_stdout_bytes": limits.max_stdout_bytes,
            "max_stderr_bytes": limits.max_stderr_bytes,
        },
        "players": {player: processes[player].diagnostics() for player in PLAYERS},
    }
    return replay, diagnostics
