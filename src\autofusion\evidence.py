"""Append-only hash-chained runtime evidence records."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock

from autofusion.errors import ReceiptError
from autofusion.util import JsonObject, canonical_json_bytes, isoformat_z, sha256_json, utc_now

_LOCK = Lock()
_GENESIS_HASH = "0" * 64


@contextmanager
def _file_lock(path: Path, timeout_s: float = 10.0) -> Iterator[None]:
    deadline = time.monotonic() + timeout_s
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ReceiptError("timed out waiting for evidence ledger lock") from None
            time.sleep(0.05)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    sequence: int
    timestamp: str
    kind: str
    payload: JsonObject
    previous_hash: str
    record_hash: str

    def as_json(self) -> JsonObject:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "kind": self.kind,
            "payload": self.payload,
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
        }


def _decode_records(path: Path) -> list[EvidenceRecord]:
    if not path.exists():
        return []
    records: list[EvidenceRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReceiptError(f"unable to read evidence ledger: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReceiptError(f"invalid evidence JSON at line {line_number}") from exc
        if not isinstance(raw, dict):
            raise ReceiptError(f"evidence line {line_number} is not an object")
        try:
            record = EvidenceRecord(
                sequence=int(raw["sequence"]),
                timestamp=str(raw["timestamp"]),
                kind=str(raw["kind"]),
                payload=dict(raw["payload"]),
                previous_hash=str(raw["previous_hash"]),
                record_hash=str(raw["record_hash"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReceiptError(f"malformed evidence record at line {line_number}") from exc
        records.append(record)
    return records


def _record_hash(record: EvidenceRecord) -> str:
    body = record.as_json()
    body.pop("record_hash")
    return sha256_json(body)


class EvidenceLedger:
    """A local append-only chain that rejects tampering before each append."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def verify(self) -> tuple[EvidenceRecord, ...]:
        previous_hash = _GENESIS_HASH
        records = _decode_records(self.path)
        for expected_sequence, record in enumerate(records, start=1):
            if record.sequence != expected_sequence:
                raise ReceiptError("evidence sequence is not contiguous")
            if record.previous_hash != previous_hash or record.record_hash != _record_hash(record):
                raise ReceiptError("evidence chain verification failed")
            previous_hash = record.record_hash
        return tuple(records)

    def append(self, kind: str, payload: JsonObject) -> EvidenceRecord:
        if not kind or not isinstance(payload, dict):
            raise ValueError("evidence kind and payload are required")
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock(self.path.with_suffix(f"{self.path.suffix}.lock")):
                existing = self.verify()
                previous_hash = existing[-1].record_hash if existing else _GENESIS_HASH
                draft = EvidenceRecord(
                    sequence=len(existing) + 1,
                    timestamp=isoformat_z(utc_now()),
                    kind=kind,
                    payload=dict(payload),
                    previous_hash=previous_hash,
                    record_hash="",
                )
                record = replace(draft, record_hash=_record_hash(draft))
                flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
                descriptor = os.open(self.path, flags, 0o600)
                try:
                    os.write(descriptor, canonical_json_bytes(record.as_json()) + b"\n")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return record
