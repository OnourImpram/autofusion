"""Deterministic packet compilation from one immutable snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from autofusion.dlp import DlpPolicy, preflight_packet
from autofusion.errors import PolicyError
from autofusion.models import ContextBudget, ModelProfile, Packet, ProviderRequest, SnapshotManifest
from autofusion.snapshot import assert_snapshot_fresh, assert_snapshot_intact
from autofusion.util import (
    JsonObject,
    canonical_json_bytes,
    deep_copy_json,
    sha256_bytes,
    sha256_json,
)


def _context_integer(value: object, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PolicyError(f"context fit requires {name} to be an integer >= {minimum}")
    return value


def _input_limit(budget: ContextBudget, handle: str) -> int:
    window = _context_integer(budget.window_tokens, f"{handle}.context_window_tokens")
    overhead = _context_integer(budget.prompt_overhead_tokens, f"{handle}.prompt_overhead_tokens")
    reserve = _context_integer(budget.reserved_output_tokens, f"{handle}.reserved_output_tokens")
    _context_integer(budget.mandatory_tokens, f"{handle}.mandatory_tokens", minimum=0)
    available = window - overhead - reserve
    if available <= 0:
        raise PolicyError(
            f"context fit for {handle}: overhead and output need {overhead + reserve} tokens; "
            f"window capacity is {window} tokens"
        )
    if budget.shared_input_limit is not None:
        shared = _context_integer(budget.shared_input_limit, f"{handle}.shared_input_limit")
        available = min(available, shared)
    return available


def profile_context_budget(
    profile: ModelProfile, *, mandatory_tokens: int = 0, shared_input_limit: int | None = None
) -> ContextBudget:
    """Require explicit profile capacity, overhead and reserved output capabilities."""

    values = profile.capabilities
    budget = ContextBudget(
        window_tokens=_context_integer(
            values.get("context_window_tokens"), f"{profile.handle}.context_window_tokens"
        ),
        prompt_overhead_tokens=_context_integer(
            values.get("prompt_overhead_tokens"), f"{profile.handle}.prompt_overhead_tokens"
        ),
        reserved_output_tokens=_context_integer(
            values.get("reserved_output_tokens"), f"{profile.handle}.reserved_output_tokens"
        ),
        mandatory_tokens=mandatory_tokens,
        shared_input_limit=shared_input_limit,
    )
    _input_limit(budget, profile.handle)
    return budget


def shared_context_limit(profiles: Sequence[ModelProfile]) -> int:
    """Use the smallest participant capacity after overhead and output reservations."""

    if not profiles:
        raise PolicyError("context fit cannot be verified without participant profiles")
    return min(
        _input_limit(profile_context_budget(profile), profile.handle) for profile in profiles
    )


def mandatory_context_tokens(packet: Packet) -> int:
    """Bound selected non-inline artifact tokens by UTF-8 bytes, without truncation.

    This conservative byte bound is not an exact provider tokenizer measurement.
    Inline text is already counted in the rendered request prompt.
    """

    paths = packet.payload.get("artifact_paths")
    inline = packet.payload.get("artifact_contents", {})
    if not isinstance(paths, list) or not isinstance(inline, dict):
        raise PolicyError("context fit requires a valid mandatory artifact manifest")
    entries = {str(entry["path"]): entry for entry in packet.snapshot.entries}
    required = 0
    for path in dict.fromkeys(str(item) for item in paths):
        normalized = PurePosixPath(path.replace("\\", "/"))
        relative = normalized.as_posix()
        if normalized.is_absolute() or ".." in normalized.parts or relative not in entries:
            raise PolicyError(f"context fit mandatory artifact is absent from snapshot: {path}")
        try:
            raw = (packet.snapshot.root / normalized).read_bytes()
        except OSError as exc:
            raise PolicyError(f"context fit cannot read mandatory artifact: {path}") from exc
        if sha256_bytes(raw) != entries[relative]["sha256"]:
            raise PolicyError(f"context fit mandatory artifact changed: {path}")
        if b"\x00" in raw:
            raise PolicyError(f"context fit cannot certify binary mandatory artifact: {path}")
        content = inline.get(path)
        if (
            isinstance(content, dict)
            and content.get("encoding") == "utf-8"
            and isinstance(content.get("content"), str)
        ):
            continue
        required += len(raw.decode("utf-8", errors="replace").encode("utf-8"))
    return required


def context_fit_error(requests: Sequence[ProviderRequest]) -> str | None:
    """Recompute actual prompt/schema byte bounds against the smallest usable window."""

    if not requests:
        return "context fit cannot be verified without participant requests"
    limits: list[int] = []
    for request in requests:
        if not isinstance(request.context_budget, ContextBudget):
            return f"context fit for {request.handle}: verified capacity is missing"
        try:
            limits.append(_input_limit(request.context_budget, request.handle))
        except PolicyError as exc:
            return str(exc)
    capacity = min(limits)
    for request in requests:
        budget = request.context_budget
        assert budget is not None
        needed = (
            len(request.prompt.encode("utf-8"))
            + len(canonical_json_bytes(request.response_schema))
            + budget.mandatory_tokens
        )
        if needed > capacity:
            return (
                f"context fit for {request.handle}: needs {needed} input tokens using the "
                f"conservative UTF-8 byte bound; shared input capacity is {capacity} tokens "
                "after prompt overhead and reserved output"
            )
    return None


def compile_packet(
    snapshot: SnapshotManifest,
    *,
    task: str,
    constraints: Mapping[str, object] | None = None,
    artifact_paths: Sequence[str] = (),
    verification_registry: Mapping[str, object] | None = None,
    focus_role: str | None = None,
    dlp_policy: DlpPolicy | None = None,
    include_artifact_contents: bool = False,
    artifact_content_max_bytes: int = 512 * 1024,
) -> Packet:
    """Compile a reviewer packet tied to exactly one immutable snapshot hash."""

    if not task.strip():
        raise ValueError("packet task must not be empty")
    assert_snapshot_fresh(snapshot)
    assert_snapshot_intact(snapshot)
    available_paths = {str(entry["path"]) for entry in snapshot.entries}
    requested_paths = tuple(artifact_paths)
    for artifact_path in requested_paths:
        normalized = PurePosixPath(artifact_path.replace("\\", "/"))
        if (
            not artifact_path
            or normalized.is_absolute()
            or ".." in normalized.parts
            or normalized.as_posix() not in available_paths
        ):
            raise ValueError(f"packet artifact path is absent from snapshot: {artifact_path}")
    if artifact_content_max_bytes < 0:
        raise ValueError("artifact content limit must be nonnegative")
    artifact_contents: JsonObject = {}
    content_bytes = 0
    if include_artifact_contents:
        entries = {str(entry["path"]): entry for entry in snapshot.entries}
        for artifact_path in requested_paths:
            raw = (snapshot.root / PurePosixPath(artifact_path)).read_bytes()
            content_bytes += len(raw)
            if content_bytes > artifact_content_max_bytes:
                raise ValueError("packet artifact contents exceed configured limit")
            entry = entries[artifact_path]
            if b"\x00" in raw:
                artifact_contents[artifact_path] = {
                    "encoding": "binary",
                    "sha256": entry["sha256"],
                    "size": entry["size"],
                }
            else:
                artifact_contents[artifact_path] = {
                    "encoding": "utf-8",
                    "content": raw.decode("utf-8", errors="replace"),
                    "sha256": entry["sha256"],
                    "size": entry["size"],
                }
    payload: JsonObject = {
        "version": 1,
        "task": task,
        "constraints": deep_copy_json(dict(constraints or {})),
        "artifact_paths": list(requested_paths),
        "artifact_contents": artifact_contents,
        "focus_role": focus_role,
        "snapshot": {
            "snapshot_hash": snapshot.snapshot_hash,
            "manifest_hash": snapshot.manifest_hash,
            "entries": deep_copy_json(list(snapshot.entries)),
        },
        "verification_registry": deep_copy_json(dict(verification_registry or {})),
    }
    preflight = preflight_packet(payload, dlp_policy)
    sanitized = dict(preflight.payload)
    rule_counts: dict[str, int] = {}
    for match in preflight.matches:
        rule_counts[match.rule] = rule_counts.get(match.rule, 0) + 1
    sanitized["dlp"] = {"action": preflight.action, "match_counts": rule_counts}
    return Packet(
        payload=sanitized,
        packet_hash=sha256_json(sanitized),
        snapshot=snapshot,
    )
