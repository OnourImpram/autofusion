"""Conservative, provenance-preserving analysis of participant outputs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from autofusion.models import CallStatus, ProviderResult, Severity
from autofusion.util import JsonObject, sha256_json


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    result: ProviderResult
    context_complete: bool = True
    identity_hash: str | None = None

    @property
    def source_id(self) -> str:
        return self.result.handle


def build_analysis(
    *,
    run_id: str,
    packet_hash: str,
    panel: str,
    topology: str,
    inputs: Sequence[AnalysisInput],
    required_handles: Iterable[str] = (),
) -> JsonObject:
    """Build analysis without upgrading a single participant claim to consensus."""

    required = set(required_handles)
    handles = [item.source_id for item in inputs]
    if len(handles) != len(set(handles)):
        raise ValueError("analysis participants must have unique model handles")
    completed = {
        item.result.handle
        for item in inputs
        if item.context_complete and item.result.status is CallStatus.COMPLETED
    }
    context_complete = required.issubset(completed) and all(
        item.context_complete for item in inputs if item.result.handle in required
    )
    findings = _extract_findings(inputs)
    consensus, contradictions, unique_insights = _compare(findings)
    blind_spots: list[JsonObject] = []
    if not context_complete:
        blind_spots.append(
            {
                "area": "mandatory context",
                "reason": "a required participant lacked complete frozen context or output",
                "next_action": "rerun the missing participant against the frozen packet",
            }
        )
    candidates = [
        {
            "finding_id": finding["id"],
            "verification_id": finding["verification_id"],
            "relevance": "the source marked the finding checkable",
        }
        for finding in findings
        if finding["checkable"] and isinstance(finding["verification_id"], str)
    ]
    blockers = any(finding["severity"] == Severity.BLOCKER.value for finding in findings)
    if not context_complete:
        impact: JsonObject = {
            "changed": False,
            "effect": "human-required",
            "rationale": "context quorum failed",
        }
    elif blockers:
        impact = {
            "changed": True,
            "effect": "blocked",
            "rationale": "at least one blocker was reported",
        }
    else:
        impact = {"changed": False, "effect": "none", "rationale": "no blocker was reported"}
    return {
        "schema_version": "0.2",
        "run_id": run_id,
        "packet_hash": packet_hash,
        "panel": panel,
        "topology": topology,
        "context_complete": context_complete,
        "participants": [_participant(item) for item in inputs],
        "consensus": consensus,
        "contradictions": contradictions,
        "partial_coverage": [],
        "unique_insights": unique_insights,
        "blind_spots": blind_spots,
        "grounding_candidates": candidates,
        "grounding_results": [],
        "proof_results": [],
        "decision_impact": impact,
        "findings": findings,
    }


def _participant(item: AnalysisInput) -> JsonObject:
    result = item.result
    status = {
        CallStatus.COMPLETED: "completed",
        CallStatus.FAILED: "failed",
        CallStatus.TIMEOUT: "timed-out",
        CallStatus.CANCELLED: "failed",
        CallStatus.POLICY_BLOCKED: "policy-blocked",
    }[result.status]
    identity = {
        "effective_model": result.effective_model,
        "family": result.family,
        "vendor": result.vendor,
        "compound": result.compound,
        "worker_visibility": result.worker_visibility,
    }
    return {
        "source_id": item.source_id,
        "effective_model": result.effective_model,
        "family": result.family,
        "compound": result.compound,
        "worker_visibility": result.worker_visibility,
        "status": status,
        "call_id": None if item.source_id == "self" else result.call_id,
        "output_hash": result.output_hash,
        "identity_hash": item.identity_hash or sha256_json(identity),
    }


def _extract_findings(inputs: Sequence[AnalysisInput]) -> list[JsonObject]:
    findings: list[JsonObject] = []
    for source_number, item in enumerate(inputs, start=1):
        payload = item.result.structured_output
        raw_findings = payload.get("findings") if isinstance(payload, dict) else None
        if not isinstance(raw_findings, list):
            continue
        for finding_number, raw in enumerate(raw_findings, start=1):
            if isinstance(raw, dict):
                findings.append(
                    _normalize_finding(
                        raw,
                        source_id=item.source_id,
                        family=item.result.family,
                        finding_id=f"f{source_number:02d}{finding_number:02d}",
                    )
                )
    return findings


def _normalize_finding(
    raw: JsonObject, *, source_id: str, family: str, finding_id: str
) -> JsonObject:
    evidence_raw = raw.get("evidence")
    evidence = evidence_raw if isinstance(evidence_raw, dict) else {}
    claim = str(raw.get("claim", "unstructured participant finding")).strip()
    if not claim:
        claim = "unstructured participant finding"
    severity = str(raw.get("severity", Severity.MINOR.value))
    allowed_severities = {level.value for level in Severity}
    if severity not in allowed_severities:
        severity = Severity.MINOR.value
    strength = str(evidence.get("strength", "unverified"))
    if strength not in {
        "grounded",
        "artifact-cited",
        "corroborated",
        "unverified",
        "contradicted",
    }:
        strength = "unverified"
    status = str(raw.get("status", "proposed"))
    if status not in {
        "proposed",
        "grounded",
        "accepted",
        "rejected",
        "deadlock",
        "resolved",
        "waived",
    }:
        status = "proposed"
    # Provider output is untrusted. Only the dispatch record establishes provenance.
    provenance = [source_id]
    verification = raw.get("verification_id")
    return {
        "id": finding_id,
        "source_ids": provenance,
        "severity": severity,
        "category": str(raw.get("category", "review")),
        "claim": claim,
        "evidence": {
            "summary": str(evidence.get("summary", "participant report")),
            "strength": strength,
            "sources": provenance,
        },
        "suggested_fix": str(raw.get("suggested_fix", "investigate before changing the artifact")),
        "checkable": bool(raw.get("checkable", False)),
        "verification_id": verification if isinstance(verification, str) else None,
        "status": status,
        "_stance_key": str(raw.get("stance_key", claim.casefold())),
        "_stance": str(raw.get("stance", claim.casefold())),
        "_family": family,
    }


def _compare(
    findings: Sequence[JsonObject],
) -> tuple[list[JsonObject], list[JsonObject], list[JsonObject]]:
    groups: dict[str, list[JsonObject]] = defaultdict(list)
    for finding in findings:
        groups[str(finding["_stance_key"])].append(finding)
    consensus: list[JsonObject] = []
    contradictions: list[JsonObject] = []
    unique: list[JsonObject] = []
    for index, group in enumerate(groups.values(), start=1):
        sources = list(
            dict.fromkeys(
                source
                for finding in group
                for source in finding["source_ids"]
                if isinstance(source, str)
            )
        )
        stances = {str(finding["_stance"]) for finding in group}
        if len(stances) > 1 and len(group) >= 2:
            contradictions.append(
                {
                    "id": f"x{index:02d}",
                    "topic": str(group[0]["_stance_key"]),
                    "positions": [
                        {
                            "source_id": finding["source_ids"][0],
                            "claim": finding["claim"],
                            "evidence": finding["evidence"],
                        }
                        for finding in group
                    ],
                    "status": "human-required",
                }
            )
        elif len(sources) >= 2:
            families = {str(finding["_family"]) for finding in group}
            consensus.append(
                {
                    "id": f"c{index:02d}",
                    "claim": group[0]["claim"],
                    "supporters": sources,
                    "evidence": {
                        "summary": group[0]["evidence"]["summary"],
                        "strength": "corroborated",
                        "sources": sources,
                    },
                    "agreement_strength": "cross-family" if len(families) > 1 else "same-family",
                }
            )
        else:
            unique.append(
                {
                    "source_id": group[0]["source_ids"][0],
                    "insight": group[0]["claim"],
                    "evidence": group[0]["evidence"],
                }
            )
    for finding in findings:
        finding.pop("_stance_key", None)
        finding.pop("_stance", None)
        finding.pop("_family", None)
    return consensus, contradictions, unique
