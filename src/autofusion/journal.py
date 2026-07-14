"""Hash chained, idempotent lifecycle journal for resumable fusion runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from autofusion.errors import JournalError
from autofusion.evidence import EvidenceLedger, EvidenceRecord
from autofusion.util import JsonObject, deep_copy_json, sha256_json

JournalStatus = Literal[
    "started",
    "completed",
    "waiting",
    "failed",
    "cancelled",
]

_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_STEP_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_STATUSES = frozenset({"started", "completed", "waiting", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class JournalEvent:
    sequence: int
    timestamp: str
    run_id: str
    step: str
    status: JournalStatus
    idempotency_key: str
    payload_hash: str
    payload: JsonObject
    attestation_hash: str

    @classmethod
    def from_record(cls, record: EvidenceRecord) -> JournalEvent:
        payload = record.payload
        try:
            status = str(payload["status"])
            if status not in _STATUSES:
                raise ValueError("unknown journal status")
            details = payload["payload"]
            if not isinstance(details, dict):
                raise TypeError("journal payload is not an object")
            return cls(
                sequence=record.sequence,
                timestamp=record.timestamp,
                run_id=str(payload["run_id"]),
                step=str(payload["step"]),
                status=cast(JournalStatus, status),
                idempotency_key=str(payload["idempotency_key"]),
                payload_hash=str(payload["payload_hash"]),
                payload=dict(details),
                attestation_hash=record.record_hash,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalError("journal event payload is malformed") from exc


@dataclass(frozen=True, slots=True)
class JournalSummary:
    run_id: str
    event_count: int
    latest_step: str | None
    latest_status: JournalStatus | None
    waiting: bool
    terminal: bool
    journal_head_hash: str | None

    def as_json(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "event_count": self.event_count,
            "latest_step": self.latest_step,
            "latest_status": self.latest_status,
            "waiting": self.waiting,
            "terminal": self.terminal,
            "journal_head_hash": self.journal_head_hash,
        }


class RunJournal:
    """Record lifecycle events once and reject conflicting retries."""

    def __init__(self, path: Path, run_id: str) -> None:
        if not _KEY_RE.fullmatch(run_id):
            raise JournalError("run_id is unsafe for a durable journal")
        self.path = path
        self.run_id = run_id
        self._ledger = EvidenceLedger(path)

    def events(self) -> tuple[JournalEvent, ...]:
        events = tuple(JournalEvent.from_record(record) for record in self._ledger.verify())
        if any(event.run_id != self.run_id for event in events):
            raise JournalError("journal contains an event for a different run")
        for event in events:
            if event.payload_hash != sha256_json(event.payload):
                raise JournalError("journal payload hash verification failed")
            if not _STEP_RE.fullmatch(event.step) or not _KEY_RE.fullmatch(
                event.idempotency_key
            ):
                raise JournalError("journal contains an unsafe step or idempotency key")
        return events

    def record(
        self,
        step: str,
        status: JournalStatus,
        *,
        idempotency_key: str,
        payload: JsonObject | None = None,
    ) -> JournalEvent:
        if not _STEP_RE.fullmatch(step):
            raise JournalError("journal step is invalid")
        if status not in _STATUSES:
            raise JournalError("journal status is invalid")
        if not _KEY_RE.fullmatch(idempotency_key):
            raise JournalError("journal idempotency key is invalid")
        copied = deep_copy_json(payload or {})
        if not isinstance(copied, dict):
            raise JournalError("journal payload must be a JSON object")
        details = dict(copied)
        payload_hash = sha256_json(details)
        existing = self.events()
        for event in existing:
            if event.idempotency_key != idempotency_key:
                continue
            if (
                event.step == step
                and event.status == status
                and event.payload_hash == payload_hash
            ):
                return event
            raise JournalError("idempotency key was reused with conflicting journal data")
        record = self._ledger.append(
            "run-journal",
            {
                "run_id": self.run_id,
                "step": step,
                "status": status,
                "idempotency_key": idempotency_key,
                "payload_hash": payload_hash,
                "payload": details,
            },
        )
        return JournalEvent.from_record(record)

    def summary(self) -> JournalSummary:
        events = self.events()
        latest = events[-1] if events else None
        terminal = bool(
            latest
            and latest.step == "terminal"
            and latest.status in {"completed", "failed", "cancelled"}
        )
        return JournalSummary(
            run_id=self.run_id,
            event_count=len(events),
            latest_step=latest.step if latest else None,
            latest_status=latest.status if latest else None,
            waiting=bool(latest and latest.status == "waiting"),
            terminal=terminal,
            journal_head_hash=latest.attestation_hash if latest else None,
        )
