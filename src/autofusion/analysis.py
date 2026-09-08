"""Conservative, provenance-preserving analysis of participant outputs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from autofusion.models import CallStatus, ProviderResult, Severity
from autofusion.util import JsonObject, deep_copy_json, sha256_json


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    result: ProviderResult
    context_complete: bool = True
    identity_hash: str | None = None

    @property
    def source_id(self) -> str:
        return self.result.handle


def participant_context_complete(participant: JsonObject) -> bool:
    """Keep transport completion distinct from a complete reviewer contribution.

    Reviewer metadata is optional for legacy artifacts and other participant roles.
    The reviewer contract cannot distinguish optional from mandatory blind spots,
    so a reported gap requires reconciliation with a human.
    """

    coverage = participant.get("coverage")
    return (
        participant.get("status") == "completed"
        and participant.get("context_complete", True) is True
        and participant.get("recommendation") != "abstain"
        and not participant.get("blind_spots")
        and (
            coverage is None
            or (
                isinstance(coverage, list)
                and any(isinstance(area, str) and area.strip() for area in coverage)
            )
        )
    )


def unsettled_review_recommendations(
    participants: Sequence[JsonObject], findings: Sequence[JsonObject]
) -> bool:
    """Require human judgment until an adverse review has settled findings.

    All of a source's findings need explicit dispositions. A different source's
    findings cannot explain a recommendation, nor can an undisposed finding.
    """

    for participant in participants:
        if participant.get("status") != "completed" or participant.get("recommendation") not in {
            "block", "revise"
        }:
            continue
        reported = [
            finding for finding in findings
            if participant.get("source_id") in finding.get("source_ids", [])
        ]
        if not reported or any(
            finding.get("status") not in {"accepted", "rejected", "resolved", "waived"}
            for finding in reported
        ):
            return True
    return False


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
    participants = [_participant(item) for item in inputs]
    completed = {
        str(item["source_id"]) for item in participants if participant_context_complete(item)
    }
    context_complete = required.issubset(completed) and len(completed) == len(participants)
    findings = _extract_findings(inputs)
    consensus, contradictions, unique_insights = _compare(findings)
    partial_coverage, blind_spots = _review_coverage(participants)
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
    elif unsettled_review_recommendations(participants, findings):
        impact = {
            "changed": False,
            "effect": "human-required",
            "rationale": "a reviewer recommendation still requires explicit reconciliation",
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
        "participants": participants,
        "consensus": consensus,
        "contradictions": contradictions,
        "partial_coverage": partial_coverage,
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
    participant: JsonObject = {
        "source_id": item.source_id,
        "effective_model": result.effective_model,
        "family": result.family,
        "compound": result.compound,
        "worker_visibility": result.worker_visibility,
        "status": status,
        "call_id": None if item.source_id == "self" else result.call_id,
        "output_hash": result.output_hash,
        "identity_hash": item.identity_hash or sha256_json(identity),
        "context_complete": item.context_complete,
    }
    output = result.structured_output
    if isinstance(output, dict):
        for name in ("coverage", "blind_spots", "recommendation"):
            if name in output:
                participant[name] = deep_copy_json(output[name])
    return participant


def _review_coverage(
    participants: Sequence[JsonObject],
) -> tuple[list[JsonObject], list[JsonObject]]:
    reviewers = [
        item for item in participants if item.get("status") == "completed" and "coverage" in item
    ]
    covered_by: dict[str, list[str]] = defaultdict(list)
    blind_spots: list[JsonObject] = []
    for participant in reviewers:
        source = str(participant["source_id"])
        for area in participant["coverage"]:
            if isinstance(area, str) and area.strip() and source not in covered_by[area]:
                covered_by[area].append(source)
        for gap in participant.get("blind_spots", []):
            blind_spots.append(
                {
                    "area": f"review by {source}",
                    "reason": f"{source} reported: {gap}",
                    "next_action": "provide missing context and rerun the reviewer",
                }
            )
    coverage: list[JsonObject] = []
    for area, sources in covered_by.items():
        missing = [str(item["source_id"]) for item in reviewers if item["source_id"] not in sources]
        coverage.append(
            {
                "area": area,
                "covered_by": sources,
                "missing": f"not covered by: {', '.join(missing)}" if missing else "",
            }
        )
    return coverage, blind_spots


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
