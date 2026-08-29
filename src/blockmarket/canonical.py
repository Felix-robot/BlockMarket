"""Strict JSON and canonical SHA-256 helpers."""

import hashlib
import json
from collections.abc import Iterable
from typing import Any


class StrictJSONError(ValueError):
    pass


def _pairs_no_duplicates(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError("duplicate_key:" + key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise StrictJSONError("non_finite_number:" + value)


def strict_loads(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise StrictJSONError(str(exc)) from exc


def canonical_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_payload(value: Any) -> str:
    return sha256_text(canonical_dumps(value))


def chain_event(body: dict[str, Any], previous_hash: str) -> dict[str, Any]:
    event = dict(body)
    event["previous_hash"] = previous_hash
    event["event_hash"] = hash_payload(event)
    return event


def verify_event_hash(event: dict[str, Any]) -> bool:
    body = dict(event)
    claimed = body.pop("event_hash", None)
    return isinstance(claimed, str) and claimed == hash_payload(body)
