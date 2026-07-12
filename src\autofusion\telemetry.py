"""Explicitly opt-in, metadata-only telemetry records."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeAlias

from autofusion.errors import PolicyError
from autofusion.util import JsonObject

TelemetryScalar: TypeAlias = str | int | float | bool | None
_EVENT_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_ALLOWED_METADATA = frozenset(
    {
        "cost_usd",
        "duration_ms",
        "error_code",
        "event_version",
        "model",
        "packet_hash",
        "policy_hash",
        "provider",
        "run_id",
        "schema_hash",
        "status",
    }
)


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    event: str
    metadata: JsonObject


def build_telemetry_event(
    event: str, metadata: Mapping[str, TelemetryScalar], *, enabled: bool = False
) -> TelemetryEvent | None:
    """Create a record only when telemetry is explicitly enabled by the caller."""

    if not enabled:
        return None
    if not _EVENT_RE.fullmatch(event):
        raise PolicyError("telemetry event name is invalid")
    unknown = set(metadata) - _ALLOWED_METADATA
    if unknown:
        raise PolicyError("telemetry accepts only approved metadata fields")
    normalized: JsonObject = {}
    for key, value in metadata.items():
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise PolicyError("telemetry metadata must be scalar")
        if isinstance(value, str) and len(value) > 256:
            raise PolicyError("telemetry metadata value exceeds its limit")
        normalized[key] = value
    return TelemetryEvent(event=event, metadata=normalized)


def emit_telemetry(event: TelemetryEvent | None, sink: Callable[[TelemetryEvent], None]) -> None:
    """Send nothing for an opt-out event and leave transport ownership to the caller."""

    if event is not None:
        sink(event)
