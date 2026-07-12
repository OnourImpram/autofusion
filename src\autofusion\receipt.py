"""Atomic linked artifact persistence and tamper-evident receipt chaining."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from autofusion.config import FusionConfig
from autofusion.contracts import canonical_analysis_bytes, validate_linked
from autofusion.errors import ReceiptError
from autofusion.models import RunArtifacts
from autofusion.util import (
    JsonObject,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    read_json_object,
    sha256_bytes,
)


def _safe_run_id(run_id: str) -> str:
    if not run_id or len(run_id) > 128:
        raise ReceiptError("run_id length is invalid")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(character not in allowed for character in run_id) or run_id in {".", ".."}:
        raise ReceiptError("run_id contains unsafe characters")
    return run_id


def _receipt_digest(receipt: JsonObject) -> str:
    unsigned = dict(receipt)
    unsigned.pop("receipt_hash", None)
    return sha256_bytes(canonical_json_bytes(unsigned))


@contextmanager
def _exclusive_lock(path: Path, timeout_s: float = 10.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ReceiptError(f"timed out waiting for receipt lock: {path}") from None
            time.sleep(0.05)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


class ReceiptStore:
    """Persist one analysis and receipt pair as an append-only run directory."""

    def __init__(self, root: Path, config: FusionConfig) -> None:
        self.root = root.resolve()
        self.config = config

    @property
    def _head_path(self) -> Path:
        return self.root / "chain-head.json"

    def persist(self, analysis: JsonObject, receipt: JsonObject) -> RunArtifacts:
        run_id = _safe_run_id(str(analysis.get("run_id", "")))
        if receipt.get("run_id") != run_id:
            raise ReceiptError("receipt and analysis run_id differ")
        run_directory = self.root / run_id
        analysis_path = run_directory / "analysis.json"
        receipt_path = run_directory / "receipt.json"
        if analysis_path.exists() or receipt_path.exists():
            raise ReceiptError(f"run already exists: {run_id}")
        analysis_bytes = canonical_analysis_bytes(analysis)
        prepared = dict(receipt)
        prepared["analysis_hash"] = sha256_bytes(analysis_bytes)
        with _exclusive_lock(self.root / ".chain.lock"):
            previous_hash: str | None = None
            if self._head_path.is_file():
                head = read_json_object(self._head_path)
                head_hash = head.get("receipt_hash")
                if isinstance(head_hash, str):
                    previous_hash = head_hash
            prepared["previous_receipt_hash"] = previous_hash
            prepared["receipt_hash"] = _receipt_digest(prepared)
            validate_linked(prepared, analysis, analysis_bytes, self.config)
            run_directory.mkdir(parents=True, exist_ok=False)
            try:
                atomic_write_bytes(analysis_path, analysis_bytes)
                atomic_write_json(receipt_path, prepared)
                atomic_write_json(
                    self._head_path,
                    {"run_id": run_id, "receipt_hash": prepared["receipt_hash"]},
                )
            except BaseException:
                analysis_path.unlink(missing_ok=True)
                receipt_path.unlink(missing_ok=True)
                with suppress(OSError):
                    run_directory.rmdir()
                raise
        return RunArtifacts(
            run_id=run_id,
            analysis_path=analysis_path,
            receipt_path=receipt_path,
            analysis=analysis,
            receipt=prepared,
        )

    def verify_chain(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        receipts: dict[str, JsonObject] = {}
        for path in sorted(self.root.glob("*/receipt.json")):
            receipt = read_json_object(path)
            recorded_hash = receipt.get("receipt_hash")
            if not isinstance(recorded_hash, str) or _receipt_digest(receipt) != recorded_hash:
                raise ReceiptError(f"receipt digest mismatch: {path}")
            if recorded_hash in receipts:
                raise ReceiptError(f"duplicate receipt digest: {recorded_hash}")
            receipts[recorded_hash] = receipt
        if not receipts:
            return ()
        heads = set(receipts)
        for receipt in receipts.values():
            previous = receipt.get("previous_receipt_hash")
            if previous is not None:
                if not isinstance(previous, str) or previous not in receipts:
                    raise ReceiptError("receipt chain references a missing predecessor")
                heads.discard(previous)
        if len(heads) != 1:
            raise ReceiptError("receipt chain must have exactly one head")
        current = next(iter(heads))
        ordered: list[str] = []
        seen: set[str] = set()
        while current:
            if current in seen:
                raise ReceiptError("receipt chain contains a cycle")
            seen.add(current)
            receipt = receipts[current]
            ordered.append(str(receipt["run_id"]))
            previous = receipt.get("previous_receipt_hash")
            current = previous if isinstance(previous, str) else ""
        if len(seen) != len(receipts):
            raise ReceiptError("receipt chain contains an orphan branch")
        return tuple(reversed(ordered))
