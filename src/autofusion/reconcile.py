"""Grounding attachment and evidence-aware finding reconciliation."""

from __future__ import annotations

from dataclasses import dataclass

from autofusion.analysis import unsettled_review_recommendations
from autofusion.errors import PolicyError
from autofusion.grounding import GroundingResult
from autofusion.proof import ProofCapsule, ProofVerdict, assert_reviewed_revision
from autofusion.util import JsonObject, deep_copy_json

_DISPOSITIONS = {"accepted", "rejected", "deadlock", "resolved", "waived"}


@dataclass(frozen=True, slots=True)
class GroundingLink:
    finding_id: str
    result: GroundingResult
    runner_attestation_hash: str
    runner_trust: str = "trusted-runner"


@dataclass(frozen=True, slots=True)
class FindingDisposition:
    finding_id: str
    disposition: str
    rationale: str

    def __post_init__(self) -> None:
        if self.disposition not in _DISPOSITIONS:
            raise ValueError(f"unsupported disposition: {self.disposition}")
        if not self.finding_id or not self.rationale.strip():
            raise ValueError("finding disposition requires an ID and rationale")


def attach_grounding(analysis: JsonObject, links: tuple[GroundingLink, ...]) -> JsonObject:
    updated = deep_copy_json(analysis)
    assert isinstance(updated, dict)
    findings = updated.get("findings")
    if not isinstance(findings, list):
        raise ValueError("analysis findings are unavailable")
    by_id = {
        str(finding.get("id")): finding for finding in findings if isinstance(finding, dict)
    }
    results: list[JsonObject] = []
    for link in links:
        finding = by_id.get(link.finding_id)
        if finding is None:
            raise ValueError(f"grounding references unknown finding: {link.finding_id}")
        result = link.result
        verdict = (
            result.verdict
            if result.verdict in {"confirmed", "not-reproduced"}
            else "inconclusive"
        )
        status = {
            "completed": "completed",
            "timeout": "timeout",
            "runner-error": "runner-error",
            "policy-blocked": "policy-blocked",
        }.get(result.execution_status, "environment-error")
        failure_class = result.failure_class
        if failure_class not in {
            "assertion",
            "static-diagnostic",
            "environment",
            "timeout",
            "policy",
            "runner",
            None,
        }:
            failure_class = "runner"
        results.append(
            {
                "finding_id": link.finding_id,
                "verification_id": result.verification_id,
                "verdict": verdict,
                "exit_code": result.exit_code,
                "output_hash": result.output_hash,
                "execution_status": status,
                "failure_class": failure_class,
                "matched_expected_failure": result.matched_expected_failure,
                "invocation_hash": result.invocation_hash,
                "runner_attestation_hash": link.runner_attestation_hash,
                "runner_trust": link.runner_trust,
            }
        )
        if verdict == "confirmed":
            finding["status"] = "grounded"
            finding["grounded_by"] = (
                f"{result.verification_id}:{link.runner_attestation_hash}"
            )
            evidence = finding.get("evidence")
            if isinstance(evidence, dict):
                evidence["strength"] = "grounded"
    updated["grounding_results"] = results
    return updated


def attach_proof_capsules(
    analysis: JsonObject, capsules: tuple[ProofCapsule, ...], *, reviewed_tree_hash: str,
) -> JsonObject:
    """Bind capsules using the independently frozen review tree's hash_proof_tree hash.

    Callers must supply review-side evidence, never a hash copied from the capsule intent.
    """

    updated = deep_copy_json(analysis)
    assert isinstance(updated, dict)
    findings = updated.get("findings")
    if not isinstance(findings, list):
        raise ValueError("analysis findings are unavailable")
    run_id = str(updated.get("run_id", ""))
    by_id = {
        str(finding.get("id")): finding for finding in findings if isinstance(finding, dict)
    }
    if len({capsule.intent.proof_id for capsule in capsules}) != len(capsules):
        raise ValueError("proof capsule IDs must be unique")
    results: list[JsonObject] = []
    for capsule in capsules:
        if capsule.intent.fusion_run_id != run_id:
            raise ValueError("proof capsule belongs to a different fusion run")
        assert_reviewed_revision(capsule, reviewed_tree_hash)
        finding = by_id.get(capsule.intent.finding_id)
        if finding is None:
            raise ValueError(
                f"proof capsule references unknown finding: {capsule.intent.finding_id}"
            )
        results.append(
            {
                "proof_id": capsule.intent.proof_id,
                "finding_id": capsule.intent.finding_id,
                "capsule_hash": capsule.capsule_hash,
                "intent_hash": capsule.body()["intent_hash"],
                "relation": capsule.intent.relation.value,
                "verification_id": capsule.intent.verification_id,
                "verdict": capsule.verdict.value,
                "mutation_required": capsule.intent.mutation_required,
                "mutation_gate_passed": capsule.mutation_gate_passed,
                "test_author": capsule.intent.test_author,
                "patch_author": capsule.intent.patch_author,
                "runner_attestation_hashes": sorted(
                    {
                        observation.runner_attestation_hash
                        for observation in capsule.observations
                    }
                ),
            }
        )
        if capsule.verdict is ProofVerdict.CONFIRMED:
            finding["status"] = "grounded"
            finding["grounded_by"] = f"proof:{capsule.capsule_hash}"
            evidence = finding.get("evidence")
            if isinstance(evidence, dict):
                evidence["strength"] = "grounded"
    updated["proof_results"] = results
    return updated


def reconcile_analysis(
    analysis: JsonObject,
    dispositions: tuple[FindingDisposition, ...],
    *,
    require_all: bool = True,
) -> JsonObject:
    updated = deep_copy_json(analysis)
    assert isinstance(updated, dict)
    findings = updated.get("findings")
    if not isinstance(findings, list):
        raise ValueError("analysis findings are unavailable")
    by_id = {
        str(finding.get("id")): finding for finding in findings if isinstance(finding, dict)
    }
    supplied = {item.finding_id for item in dispositions}
    if len(supplied) != len(dispositions):
        raise ValueError("finding dispositions must have unique IDs")
    if require_all and supplied != set(by_id):
        raise ValueError("every finding requires an explicit disposition")
    for disposition in dispositions:
        finding = by_id.get(disposition.finding_id)
        if finding is None:
            raise ValueError(f"disposition references unknown finding: {disposition.finding_id}")
        evidence = finding.get("evidence")
        grounded = isinstance(evidence, dict) and evidence.get("strength") == "grounded"
        if grounded and disposition.disposition in {"rejected", "waived"}:
            raise PolicyError("execution-confirmed findings cannot be rejected or waived")
        finding["status"] = disposition.disposition
        finding["disposition_rationale"] = disposition.rationale
    accepted = [
        finding
        for finding in findings
        if isinstance(finding, dict)
        and finding.get("status") in {"accepted", "resolved", "grounded"}
    ]
    deadlocked = [
        finding
        for finding in findings
        if isinstance(finding, dict) and finding.get("status") == "deadlock"
    ]
    blocking_deadlock = any(
        finding.get("severity") in {"blocker", "major"} for finding in deadlocked
    )
    blocker = any(finding.get("severity") == "blocker" for finding in accepted)
    if updated.get("context_complete") is False:
        effect = "human-required"
        rationale = "required participant or frozen context quorum was incomplete"
    elif unsettled_review_recommendations(updated.get("participants", []), findings):
        effect = "human-required"
        rationale = "a reviewer recommendation still requires explicit reconciliation"
    elif blocking_deadlock:
        effect = "human-required"
        rationale = "unresolved blocker or major finding requires operator judgment"
    elif blocker:
        effect = "blocked"
        rationale = "an accepted blocker prevents shipping"
    elif accepted:
        effect = "revised"
        rationale = "one or more findings were accepted or execution-confirmed"
    else:
        effect = "clarified" if findings else "none"
        rationale = "review findings did not change the artifact"
    updated["decision_impact"] = {
        "changed": bool(accepted),
        "effect": effect,
        "rationale": rationale,
    }
    return updated


def automatic_external_dispositions(analysis: JsonObject) -> tuple[FindingDisposition, ...]:
    findings = analysis.get("findings")
    if not isinstance(findings, list):
        raise ValueError("analysis findings are unavailable")
    dispositions: list[FindingDisposition] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("id", ""))
        evidence = finding.get("evidence")
        grounded = isinstance(evidence, dict) and evidence.get("strength") == "grounded"
        severity = finding.get("severity")
        if grounded:
            status = "accepted"
            rationale = "trusted execution confirmed the finding"
        elif severity in {"blocker", "major"}:
            status = "deadlock"
            rationale = "external models cannot settle an ungrounded blocking claim"
        else:
            status = "waived"
            rationale = "ungrounded minor claim did not meet the evidence burden"
        dispositions.append(FindingDisposition(finding_id, status, rationale))
    return tuple(dispositions)
