"""Content-addressed, materialized repository snapshots."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from autofusion.errors import SnapshotError
from autofusion.models import SnapshotManifest
from autofusion.util import JsonObject, canonical_json_bytes, sha256_bytes

_REPARSE_POINT = 0x400


@dataclass(frozen=True, slots=True)
class SnapshotLimits:
    """Hard limits applied before source material reaches a reviewer."""

    max_files: int = 10_000
    max_file_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_path_bytes: int = 4_096
    excluded_patterns: tuple[str, ...] = (
        ".git/**",
        "**/.git/**",
        ".fusion/**",
        ".mypy_cache/**",
        ".pytest_cache/**",
        ".ruff_cache/**",
        ".venv/**",
        "__pycache__/**",
        "node_modules/**",
        "*.pyc",
    )


def _is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & _REPARSE_POINT)


def _relative_text(root: Path, path: Path, limits: SnapshotLimits) -> str:
    relative = path.relative_to(root)
    normalized = relative.as_posix()
    if not normalized or normalized == "." or normalized.startswith("../"):
        raise SnapshotError(f"invalid snapshot path: {path}")
    if len(normalized.encode("utf-8")) > limits.max_path_bytes:
        raise SnapshotError(f"snapshot path exceeds limit: {normalized}")
    return normalized


def _stable_file_bytes(path: Path, limits: SnapshotLimits) -> bytes:
    before = path.lstat()
    if _is_link_or_reparse(path) or not stat.S_ISREG(before.st_mode):
        raise SnapshotError(f"snapshot refuses link or non-regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SnapshotError(f"unable to open snapshot file: {path}") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(limits.max_file_bytes + 1)
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = path.lstat()
    identity_changed = (
        before.st_dev != opened.st_dev
        or before.st_ino != opened.st_ino
        or before.st_size != opened.st_size
        or before.st_mtime_ns != opened.st_mtime_ns
        or after.st_dev != before.st_dev
        or after.st_ino != before.st_ino
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    )
    if identity_changed:
        raise SnapshotError(f"source changed while being frozen: {path}")
    if len(data) > limits.max_file_bytes:
        raise SnapshotError(f"snapshot file exceeds limit: {path}")
    return data


def _excluded(relative: str, patterns: tuple[str, ...]) -> bool:
    candidate = PurePosixPath(relative)
    for pattern in patterns:
        if candidate.match(pattern):
            return True
        if pattern.endswith("/**"):
            prefix = pattern.removesuffix("/**")
            if relative == prefix or relative.startswith(f"{prefix}/"):
                return True
    return False


def _collect_files(root: Path, limits: SnapshotLimits) -> list[tuple[str, Path]]:
    if not root.is_dir() or _is_link_or_reparse(root):
        raise SnapshotError(f"snapshot root must be a real directory: {root}")
    discovered: list[tuple[str, Path]] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise SnapshotError(f"unable to enumerate snapshot directory: {directory}") from exc
        for entry in entries:
            if _is_link_or_reparse(entry):
                raise SnapshotError(f"snapshot refuses link or reparse point: {entry}")
            relative = _relative_text(root, entry, limits)
            if _excluded(relative, limits.excluded_patterns):
                continue
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                discovered.append((relative, entry))
            else:
                raise SnapshotError(f"snapshot refuses unsupported entry: {entry}")
            if len(discovered) > limits.max_files:
                raise SnapshotError("snapshot file count exceeds limit")
    return sorted(discovered)


def _manifest_entries(root: Path, limits: SnapshotLimits) -> tuple[JsonObject, ...]:
    entries: list[JsonObject] = []
    total_size = 0
    for relative_path, source_path in _collect_files(root, limits):
        payload = _stable_file_bytes(source_path, limits)
        total_size += len(payload)
        if total_size > limits.max_total_bytes:
            raise SnapshotError("snapshot total size exceeds limit")
        entries.append(
            {"path": relative_path, "sha256": sha256_bytes(payload), "size": len(payload)}
        )
    return tuple(entries)


def assert_snapshot_fresh(manifest: SnapshotManifest, limits: SnapshotLimits | None = None) -> None:
    """Reject a run when its live source tree no longer matches the frozen manifest."""

    active_limits = limits or SnapshotLimits()
    current_entries = _manifest_entries(manifest.source_root, active_limits)
    if current_entries != manifest.entries:
        raise SnapshotError("source tree changed after snapshot freeze")


def assert_snapshot_intact(
    manifest: SnapshotManifest, limits: SnapshotLimits | None = None
) -> None:
    """Verify that the materialized review input still matches its manifest."""

    active_limits = limits or SnapshotLimits()
    materialized_entries = _manifest_entries(manifest.root, active_limits)
    if materialized_entries != manifest.entries:
        raise SnapshotError("materialized snapshot no longer matches its manifest")


def build_snapshot(
    source_root: Path,
    destination_root: Path,
    *,
    limits: SnapshotLimits | None = None,
) -> SnapshotManifest:
    """Copy one stable source tree to a hash-addressed immutable snapshot directory."""

    active_limits = limits or SnapshotLimits()
    source = source_root.absolute()
    destination = destination_root.absolute()
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise SnapshotError("snapshot destination must not be inside the source tree")
    initial_entries = _manifest_entries(source, active_limits)
    manifest_hash = sha256_bytes(canonical_json_bytes(list(initial_entries)))
    snapshot_hash = sha256_bytes(
        canonical_json_bytes({"manifest_hash": manifest_hash, "version": 1})
    )
    target = destination / snapshot_hash
    if target.exists():
        existing = SnapshotManifest(
            root=target,
            snapshot_hash=snapshot_hash,
            manifest_hash=manifest_hash,
            entries=initial_entries,
            source_root=source,
        )
        assert_snapshot_intact(existing, active_limits)
        assert_snapshot_fresh(existing, active_limits)
        return existing
    temporary = destination / f".{snapshot_hash}.partial"
    shutil.rmtree(temporary, ignore_errors=True)
    try:
        for entry in initial_entries:
            relative = str(entry["path"])
            source_path = source / Path(relative)
            payload = _stable_file_bytes(source_path, active_limits)
            if sha256_bytes(payload) != entry["sha256"]:
                raise SnapshotError(f"source changed while copying: {relative}")
            output = temporary / Path(relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
            os.chmod(output, 0o444)
        assert_snapshot_fresh(
            SnapshotManifest(
                root=temporary,
                snapshot_hash=snapshot_hash,
                manifest_hash=manifest_hash,
                entries=initial_entries,
                source_root=source,
            ),
            active_limits,
        )
        temporary.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, target)
        for directory in sorted(
            (path for path in target.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            os.chmod(directory, 0o555)
        os.chmod(target, 0o555)
        manifest = SnapshotManifest(
            root=target,
            snapshot_hash=snapshot_hash,
            manifest_hash=manifest_hash,
            entries=initial_entries,
            source_root=source,
        )
        assert_snapshot_intact(manifest, active_limits)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
