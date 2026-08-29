"""BlockMarket deterministic market-game core."""

from .engine import MatchConfig, run_match
from .runner import RunnerLimits, run_subprocess_match
from .tournament import (
    Participant,
    TournamentConfig,
    TournamentError,
    run_tournament,
    verify_audit_pack,
)
from .verifier import VerificationError, verify_replay

__all__ = [
    "MatchConfig",
    "Participant",
    "RunnerLimits",
    "TournamentConfig",
    "TournamentError",
    "VerificationError",
    "run_match",
    "run_subprocess_match",
    "run_tournament",
    "verify_audit_pack",
    "verify_replay",
]
__version__ = "0.3.0"
