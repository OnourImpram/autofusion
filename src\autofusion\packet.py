"""Deterministic packet compilation from one immutable snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from autofusion.dlp import DlpPolicy, preflight_packet
from autofusion.models import Packet, SnapshotManifest
from autofusion.snapshot import assert_snapshot_fresh, assert_snapshot_intact
from autofusion.util import JsonObject, deep_copy_json, sha256_json


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
