import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class IdempotencyScope:
    workspace_id: str
    purpose: str
    key: str

    def __post_init__(self):
        if not all((self.workspace_id, self.purpose, self.key)):
            raise ValueError("workspace, purpose and key are required")


def canonicalize(value: Any) -> Any:
    """Build a typed canonical representation, failing closed on ambiguity."""
    if isinstance(value, Enum):
        return {"$enum": canonicalize(value.value)}
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise TypeError("floats are forbidden in semantic payloads")
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("semantic datetimes must be timezone-aware")
        return {
            "$datetime": value.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        }
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        return canonicalize(asdict(value))
    if isinstance(value, (tuple, list)):
        return [canonicalize(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("semantic mapping keys must be strings")
        return {key: canonicalize(value[key]) for key in sorted(value)}
    raise TypeError(f"unsupported semantic type: {type(value).__name__}")


def semantic_hash(payload: Any) -> str:
    encoded = json.dumps(
        canonicalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode()).hexdigest()
