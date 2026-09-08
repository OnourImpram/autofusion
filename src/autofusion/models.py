"""Typed runtime records shared by providers and orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from autofusion.util import JsonObject, sha256_text


class CallStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    POLICY_BLOCKED = "policy_blocked"


class RunState(StrEnum):
    PREPARED = "prepared"
    ROUTED = "routed"
    FROZEN = "frozen"
    DISPATCHED = "dispatched"
    ANALYZED = "analyzed"
    GROUNDED = "grounded"
    RECONCILED = "reconciled"
    SIGNED_OFF = "signed_off"
    ESCALATED = "escalated"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Verdict(StrEnum):
    SHIP = "ship"
    REVISE = "revise"
    BLOCKED = "blocked"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NOT_RUN = "not-run"


class Severity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    handle: str
    transport: str
    callable: bool
    model: str
    canonical_model: str
    vendor: str
    family: str
    effort: str
    context: str
    compound: bool = False
    worker_visibility: str = "not-applicable"
    enabled: bool = True
    params: JsonObject = field(default_factory=dict)
    capabilities: JsonObject = field(default_factory=dict)
    quota_group: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    run_id: str
    call_id: str
    handle: str
    prompt: str
    response_schema: JsonObject
    working_directory: Path
    timeout_s: float
    max_output_chars: int
    environment_allowlist: tuple[str, ...] = ()
    metadata: JsonObject = field(default_factory=dict)

    @property
    def prompt_hash(self) -> str:
        return sha256_text(self.prompt)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    call_id: str
    handle: str
    requested_model: str
    effective_model: str | None
    vendor: str
    family: str
    mode: str
    compound: bool
    worker_visibility: str
    status: CallStatus
    duration_ms: int
    output_text: str
    structured_output: JsonObject | None
    output_hash: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_verified: bool = False
    cost_source: str = "unknown"
    provider_usage_hash: str | None = None
    routing_attestation_hash: str | None = None
    error: str | None = None
    stderr_tail: str = ""
    truncated: bool = False
    configured_model: str | None = None
    observed_model: str | None = None
    identity_evidence: str = "legacy-unspecified"
    quota_group: str | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    source_ids: tuple[str, ...]
    severity: Severity
    category: str
    claim: str
    evidence_summary: str
    evidence_strength: str
    suggested_fix: str
    checkable: bool
    verification_id: str | None
    status: str = "open"
    stance_key: str | None = None
    stance: str | None = None

    def to_schema(self) -> JsonObject:
        return {
            "id": self.id,
            "source_ids": list(self.source_ids),
            "severity": self.severity.value,
            "category": self.category,
            "claim": self.claim,
            "evidence": {
                "summary": self.evidence_summary,
                "strength": self.evidence_strength,
                "sources": list(self.source_ids),
            },
            "suggested_fix": self.suggested_fix,
            "checkable": self.checkable,
            "verification_id": self.verification_id,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_calls: int
    max_wallclock_s: int
    max_cost_usd: float | None
    max_output_chars_per_call: int


@dataclass(frozen=True, slots=True)
class RouteDecision:
    requested_preset: str
    selected_preset: str
    panel: str
    topology: str
    participants: tuple[str, ...]
    reasons: tuple[str, ...]
    hard_gates: tuple[str, ...]
    budget: RunBudget


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    root: Path
    snapshot_hash: str
    manifest_hash: str
    entries: tuple[JsonObject, ...]
    source_root: Path


@dataclass(frozen=True, slots=True)
class Packet:
    payload: JsonObject
    packet_hash: str
    snapshot: SnapshotManifest


@dataclass(frozen=True, slots=True)
class RunArtifacts:
    run_id: str
    analysis_path: Path
    receipt_path: Path
    analysis: JsonObject
    receipt: JsonObject
