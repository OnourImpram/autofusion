"""Metadata-only precedent and downstream outcome records."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from autofusion.errors import PrecedentError
from autofusion.evidence import EvidenceLedger
from autofusion.util import JsonObject, isoformat_z, sha256_json, sha256_text, utc_now

_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_DISPOSITIONS = frozenset({"accepted", "rejected", "deadlock", "resolved", "waived"})


class OutcomeVerdict(StrEnum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class PrecedentMatch:
    record_id: str
    repository_scope_hash: str
    finding_fingerprint: str
    category: str
    disposition: str
    source_receipt_hash: str
    policy_hash: str
    expires_at: str
    outcome: OutcomeVerdict | None
    outcome_evidence_hash: str | None
    attestation_hash: str

    def as_json(self) -> JsonObject:
        return {
            "record_id": self.record_id,
            "repository_scope_hash": self.repository_scope_hash,
            "finding_fingerprint": self.finding_fingerprint,
            "category": self.category,
            "disposition": self.disposition,
            "source_receipt_hash": self.source_receipt_hash,
            "policy_hash": self.policy_hash,
            "expires_at": self.expires_at,
            "outcome": self.outcome.value if self.outcome else None,
            "outcome_evidence_hash": self.outcome_evidence_hash,
            "attestation_hash": self.attestation_hash,
        }


def _require_hash(name: str, value: str) -> str:
    if not _HASH_RE.fullmatch(value):
        raise PrecedentError(f"{name} must be a lowercase SHA-256 hash")
    return value


def finding_fingerprint(*, category: str, claim: str, path_hashes: tuple[str, ...]) -> str:
    """Create a stable fingerprint without persisting raw finding text."""

    if not category.strip() or not claim.strip():
        raise PrecedentError("finding fingerprint requires category and claim")
    for value in path_hashes:
        _require_hash("path_hash", value)
    return sha256_json(
        {
            "version": 1,
            "category": category.strip().casefold(),
            "claim_hash": sha256_text(" ".join(claim.split()).casefold()),
            "path_hashes": sorted(set(path_hashes)),
        }
    )


def repository_scope_hash(repository_identifier: str) -> str:
    if not repository_identifier.strip():
        raise PrecedentError("repository identifier is required")
    return sha256_text(repository_identifier.strip().casefold())


class PrecedentLedger:
    """Store hash-chained metadata and expose it only after blind review."""

    def __init__(self, path: Path, repository_hash: str) -> None:
        self.path = path
        self.repository_hash = _require_hash("repository_scope_hash", repository_hash)
        self._ledger = EvidenceLedger(path)

    def record_decision(
        self,
        *,
        fingerprint: str,
        category: str,
        disposition: str,
        source_receipt_hash: str,
        policy_hash: str,
        ttl_days: int,
    ) -> PrecedentMatch:
        _require_hash("finding_fingerprint", fingerprint)
        _require_hash("source_receipt_hash", source_receipt_hash)
        _require_hash("policy_hash", policy_hash)
        if disposition not in _DISPOSITIONS:
            raise PrecedentError("precedent disposition is invalid")
        if not category.strip() or len(category) > 128:
            raise PrecedentError("precedent category is invalid")
        if not 1 <= ttl_days <= 3650:
            raise PrecedentError("precedent TTL must be between 1 and 3650 days")
        identity: JsonObject = {
            "repository_scope_hash": self.repository_hash,
            "finding_fingerprint": fingerprint,
            "category": category.strip(),
            "disposition": disposition,
            "source_receipt_hash": source_receipt_hash,
            "policy_hash": policy_hash,
            "ttl_days": ttl_days,
            "retrieval_phase": "post-blind-review",
            "privacy": "metadata-only",
        }
        record_id = f"precedent-{sha256_json(identity)[:24]}"
        for match in self._matches(include_expired=True):
            if match.record_id == record_id:
                return match
        expires_at = isoformat_z(utc_now() + timedelta(days=ttl_days))
        body: JsonObject = {**identity, "expires_at": expires_at}
        record = self._ledger.append(
            "precedent-decision", {"record_id": record_id, **body}
        )
        return PrecedentMatch(
            record_id=record_id,
            repository_scope_hash=self.repository_hash,
            finding_fingerprint=fingerprint,
            category=category.strip(),
            disposition=disposition,
            source_receipt_hash=source_receipt_hash,
            policy_hash=policy_hash,
            expires_at=expires_at,
            outcome=None,
            outcome_evidence_hash=None,
            attestation_hash=record.record_hash,
        )

    def record_outcome(
        self,
        record_id: str,
        *,
        outcome: OutcomeVerdict,
        evidence_hash: str,
    ) -> PrecedentMatch:
        _require_hash("outcome_evidence_hash", evidence_hash)
        matches = {item.record_id: item for item in self._matches(include_expired=True)}
        decision = matches.get(record_id)
        if decision is None:
            raise PrecedentError("outcome references an unknown precedent record")
        payload: JsonObject = {
            "record_id": record_id,
            "repository_scope_hash": self.repository_hash,
            "outcome": outcome.value,
            "outcome_evidence_hash": evidence_hash,
            "source_receipt_hash": decision.source_receipt_hash,
            "privacy": "metadata-only",
        }
        for record in self._ledger.verify():
            if record.kind != "precedent-outcome":
                continue
            if record.payload == payload:
                return replace(
                    decision,
                    outcome=outcome,
                    outcome_evidence_hash=evidence_hash,
                    attestation_hash=record.record_hash,
                )
        record = self._ledger.append("precedent-outcome", payload)
        return PrecedentMatch(
            record_id=decision.record_id,
            repository_scope_hash=decision.repository_scope_hash,
            finding_fingerprint=decision.finding_fingerprint,
            category=decision.category,
            disposition=decision.disposition,
            source_receipt_hash=decision.source_receipt_hash,
            policy_hash=decision.policy_hash,
            expires_at=decision.expires_at,
            outcome=outcome,
            outcome_evidence_hash=evidence_hash,
            attestation_hash=record.record_hash,
        )

    def query(
        self,
        fingerprint: str,
        *,
        phase: str,
        now: datetime | None = None,
    ) -> tuple[PrecedentMatch, ...]:
        _require_hash("finding_fingerprint", fingerprint)
        if phase != "post-blind-review":
            raise PrecedentError("precedent retrieval is forbidden before blind review")
        current = now or datetime.now(UTC)
        return tuple(
            match
            for match in self._matches(include_expired=False, now=current)
            if match.finding_fingerprint == fingerprint
        )

    def _matches(
        self,
        *,
        include_expired: bool,
        now: datetime | None = None,
    ) -> tuple[PrecedentMatch, ...]:
        current = now or datetime.now(UTC)
        decisions: dict[str, PrecedentMatch] = {}
        outcomes: dict[str, tuple[OutcomeVerdict, str, str]] = {}
        for record in self._ledger.verify():
            payload = record.payload
            if payload.get("repository_scope_hash") != self.repository_hash:
                raise PrecedentError("precedent ledger crossed repository scope")
            if record.kind == "precedent-decision":
                try:
                    match = PrecedentMatch(
                        record_id=str(payload["record_id"]),
                        repository_scope_hash=self.repository_hash,
                        finding_fingerprint=_require_hash(
                            "finding_fingerprint", str(payload["finding_fingerprint"])
                        ),
                        category=str(payload["category"]),
                        disposition=str(payload["disposition"]),
                        source_receipt_hash=_require_hash(
                            "source_receipt_hash", str(payload["source_receipt_hash"])
                        ),
                        policy_hash=_require_hash(
                            "policy_hash", str(payload["policy_hash"])
                        ),
                        expires_at=str(payload["expires_at"]),
                        outcome=None,
                        outcome_evidence_hash=None,
                        attestation_hash=record.record_hash,
                    )
                except (KeyError, ValueError) as exc:
                    raise PrecedentError("precedent decision record is malformed") from exc
                if match.disposition not in _DISPOSITIONS:
                    raise PrecedentError("precedent decision contains an invalid disposition")
                decisions[match.record_id] = match
            elif record.kind == "precedent-outcome":
                try:
                    outcomes[str(payload["record_id"])] = (
                        OutcomeVerdict(str(payload["outcome"])),
                        _require_hash(
                            "outcome_evidence_hash", str(payload["outcome_evidence_hash"])
                        ),
                        record.record_hash,
                    )
                except (KeyError, ValueError) as exc:
                    raise PrecedentError("precedent outcome record is malformed") from exc
            else:
                raise PrecedentError("precedent ledger contains an unknown record kind")
        merged: list[PrecedentMatch] = []
        for record_id, decision in decisions.items():
            expiry = datetime.fromisoformat(decision.expires_at.replace("Z", "+00:00"))
            if not include_expired and expiry <= current:
                continue
            outcome = outcomes.get(record_id)
            if outcome is None:
                merged.append(decision)
                continue
            merged.append(
                PrecedentMatch(
                    record_id=decision.record_id,
                    repository_scope_hash=decision.repository_scope_hash,
                    finding_fingerprint=decision.finding_fingerprint,
                    category=decision.category,
                    disposition=decision.disposition,
                    source_receipt_hash=decision.source_receipt_hash,
                    policy_hash=decision.policy_hash,
                    expires_at=decision.expires_at,
                    outcome=outcome[0],
                    outcome_evidence_hash=outcome[1],
                    attestation_hash=outcome[2],
                )
            )
        return tuple(sorted(merged, key=lambda item: item.record_id))
