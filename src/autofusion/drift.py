"""Deterministic snapshots and structural drift reports for JSON-compatible state."""

from __future__ import annotations

from dataclasses import dataclass

from autofusion.util import canonical_json_bytes, deep_copy_json, sha256_bytes, sha256_json


@dataclass(frozen=True, slots=True)
class DriftSnapshot:
    subject: str
    content: object
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class DriftChange:
    path: str
    kind: str
    before_hash: str | None
    after_hash: str | None


@dataclass(frozen=True, slots=True)
class DriftReport:
    subject: str
    before_hash: str
    after_hash: str
    changes: tuple[DriftChange, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def capture_drift_snapshot(subject: str, content: object) -> DriftSnapshot:
    """Freeze JSON-compatible state into a deterministic content-addressed snapshot."""

    if not subject.strip():
        raise ValueError("drift snapshot subject is required")
    copied = deep_copy_json(content)
    return DriftSnapshot(
        subject=subject,
        content=copied,
        snapshot_hash=sha256_bytes(canonical_json_bytes(copied)),
    )


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _leaf_hashes(value: object, path: str = "") -> dict[str, str]:
    if isinstance(value, dict):
        if not value:
            return {path or "/": sha256_json(value)}
        leaves: dict[str, str] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise ValueError("drift snapshots require string object keys")
            leaves.update(_leaf_hashes(value[key], f"{path}/{_pointer_token(key)}"))
        return leaves
    if isinstance(value, list):
        if not value:
            return {path or "/": sha256_json(value)}
        indexed_leaves: dict[str, str] = {}
        for index, item in enumerate(value):
            indexed_leaves.update(_leaf_hashes(item, f"{path}/{index}"))
        return indexed_leaves
    return {path or "/": sha256_json(value)}


def compare_drift_snapshots(before: DriftSnapshot, after: DriftSnapshot) -> DriftReport:
    """Compare snapshots by stable JSON pointer, independent of mapping insertion order."""

    if before.subject != after.subject:
        raise ValueError("drift snapshots must describe the same subject")
    before_leaves = _leaf_hashes(before.content)
    after_leaves = _leaf_hashes(after.content)
    changes: list[DriftChange] = []
    for path in sorted(set(before_leaves) | set(after_leaves)):
        old = before_leaves.get(path)
        new = after_leaves.get(path)
        if old == new:
            continue
        kind = "added" if old is None else "removed" if new is None else "changed"
        changes.append(DriftChange(path=path, kind=kind, before_hash=old, after_hash=new))
    return DriftReport(
        subject=before.subject,
        before_hash=before.snapshot_hash,
        after_hash=after.snapshot_hash,
        changes=tuple(changes),
    )
