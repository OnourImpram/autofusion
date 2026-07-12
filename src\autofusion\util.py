"""Small deterministic helpers shared across trust boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def isoformat_z(value: datetime) -> str:
    """Serialize UTC time using the repository's canonical Z suffix."""

    normalized = value.astimezone(UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: object) -> bytes:
    """Encode JSON deterministically for hashing and signatures."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def read_json_object(path: Path) -> JsonObject:
    """Load one JSON object and reject arrays or scalar roots."""

    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError(f"{path} must contain a JSON object")
    return dict(parsed)


def atomic_write_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Atomically replace a file without exposing a partial artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: object, *, mode: int = 0o600) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value) + b"\n", mode=mode)


def bounded_text(value: str, limit: int) -> tuple[str, bool]:
    """Bound untrusted text while retaining the tail where diagnostics live."""

    if limit < 0:
        raise ValueError("limit must be nonnegative")
    if len(value) <= limit:
        return value, False
    if limit == 0:
        return "", True
    marker = "\n...[truncated]...\n"
    if limit <= len(marker):
        return value[-limit:], True
    head = (limit - len(marker)) // 3
    tail = limit - len(marker) - head
    return value[:head] + marker + value[-tail:], True


def deep_copy_json(value: object) -> object:
    """Copy JSON-compatible data without preserving mutable aliases."""

    return json.loads(canonical_json_bytes(value))


def stable_mapping(value: Mapping[str, object]) -> JsonObject:
    return {key: value[key] for key in sorted(value)}

