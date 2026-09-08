"""Runtime validation for analysis and receipt artifacts."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator

from autofusion.analysis import participant_context_complete, unsettled_review_recommendations
from autofusion.config import FusionConfig
from autofusion.errors import ReceiptError
from autofusion.models import ModelProfile
from autofusion.policy import panel_participants
from autofusion.util import JsonObject, canonical_json_bytes, read_json_object, sha256_bytes


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptError(message)


def _object(value: object, message: str) -> JsonObject:
    _require(isinstance(value, dict), message)
    assert isinstance(value, dict)
    _require(all(isinstance(key, str) for key in value), message)
    return dict(value)


def _objects(value: object, message: str) -> list[JsonObject]:
    _require(isinstance(value, list), message)
    assert isinstance(value, list)
    return [_object(item, message) for item in value]


def _strings(value: object, message: str) -> list[str]:
    _require(isinstance(value, list), message)
    assert isinstance(value, list)
    _require(all(isinstance(item, str) for item in value), message)
    return [item for item in value if isinstance(item, str)]


def _schema(name: str) -> JsonObject:
    resource = files("autofusion").joinpath("schemas", name)
    parsed: object = __import__("json").loads(resource.read_text(encoding="utf-8"))
    return _object(parsed, f"invalid packaged schema: {name}")


def _validate_schema(instance: JsonObject, schema_name: str) -> None:
    validator = Draft202012Validator(_schema(schema_name))
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ReceiptError(f"{schema_name} validation failed at {location}: {first.message}")


def validate_analysis(analysis: JsonObject) -> None:
    """Validate schema plus the strongest runtime semantic invariants."""

    _validate_schema(analysis, "fusion-analysis.schema.json")
    participants = _objects(analysis.get("participants"), "participants must be objects")
    source_ids = [str(item["source_id"]) for item in participants]
    _require(len(source_ids) == len(set(source_ids)), "participant source_id values must be unique")
    completed = {
        str(item["source_id"]) for item in participants if item.get("status") == "completed"
    }
    _require(
        bool(analysis.get("context_complete"))
        == all(participant_context_complete(item) for item in participants),
        "context_complete must match participant coverage and completion",
    )
    for item in _objects(analysis.get("consensus"), "consensus must contain objects"):
        supporters = _strings(item.get("supporters"), "consensus supporters must be strings")
        _require(len(set(supporters)) >= 2, "consensus requires at least two supporters")
        _require(set(supporters) <= completed, "consensus supporters must be completed")
        evidence = _object(item.get("evidence"), "consensus evidence must be an object")
        sources = set(_strings(evidence.get("sources"), "evidence sources must be strings"))
        _require(sources <= set(supporters), "consensus evidence must come from supporters")
    finding_ids: set[str] = set()
    findings = _objects(analysis.get("findings"), "findings must contain objects")
    for finding in findings:
        finding_id = str(finding["id"])
        _require(finding_id not in finding_ids, f"duplicate finding id: {finding_id}")
        finding_ids.add(finding_id)
        sources = set(_strings(finding.get("source_ids"), "finding sources must be strings"))
        _require(bool(sources), f"finding {finding_id} requires at least one source")
        _require(sources <= completed, f"finding {finding_id} cites an incomplete source")
    candidates = {
        (str(item["finding_id"]), str(item["verification_id"]))
        for item in _objects(
            analysis.get("grounding_candidates"), "grounding candidates must be objects"
        )
    }
    confirmed: set[tuple[str, str]] = set()
    for result in _objects(
        analysis.get("grounding_results"), "grounding results must be objects"
    ):
        key = (str(result["finding_id"]), str(result["verification_id"]))
        _require(key in candidates, "grounding result requires a declared candidate")
        if result.get("verdict") == "confirmed":
            _require(result.get("execution_status") == "completed", "confirmed run must complete")
            _require(
                result.get("failure_class") in {"assertion", "static-diagnostic"},
                "confirmed run requires assertion or static diagnostic failure",
            )
            _require(result.get("matched_expected_failure") is True, "failure must match claim")
            exit_code = result.get("exit_code")
            _require(isinstance(exit_code, int) and exit_code != 0, "confirmed run must fail")
            _require(result.get("runner_trust") == "trusted-runner", "runner must be trusted")
            confirmed.add(key)
    proof_confirmed: set[str] = set()
    for result in _objects(analysis.get("proof_results"), "proof results must be objects"):
        finding_id = str(result["finding_id"])
        _require(finding_id in finding_ids, "proof result references an unknown finding")
        if result.get("verdict") == "confirmed":
            _require(
                result.get("mutation_gate_passed") is True,
                "confirmed proof requires a passing mutation gate",
            )
            _require(
                result.get("test_author") != result.get("patch_author"),
                "proof test and patch authors must be independent",
            )
            proof_confirmed.add(finding_id)
    for finding in findings:
        if finding.get("status") == "grounded":
            key = (str(finding["id"]), str(finding.get("verification_id")))
            _require(
                key in confirmed or str(finding["id"]) in proof_confirmed,
                f"grounded finding {finding['id']} lacks confirmation",
            )
    decision = _object(analysis.get("decision_impact"), "decision_impact must be an object")
    if not bool(analysis.get("context_complete")):
        _require(
            decision.get("effect") in {"blocked", "human-required"},
            "incomplete context must block or require a human",
        )
    if unsettled_review_recommendations(participants, findings):
        _require(
            decision.get("effect") in {"blocked", "human-required"},
            "unsettled reviewer recommendation requires human reconciliation",
        )


def validate_proof_intent(intent: JsonObject) -> None:
    _validate_schema(intent, "proof-intent.schema.json")
    _require(
        intent.get("test_author") != intent.get("patch_author"),
        "proof test author must be independent from the patch author",
    )


def validate_proof_capsule(capsule: JsonObject) -> None:
    _validate_schema(capsule, "proof-capsule.schema.json")
    _require(
        capsule.get("test_author") != capsule.get("patch_author"),
        "proof capsule authors must remain independent",
    )
    if capsule.get("verdict") == "confirmed":
        _require(
            capsule.get("mutation_gate_passed") is True,
            "confirmed proof capsule requires a passing mutation gate",
        )


def _validate_costs(receipt: JsonObject) -> None:
    calls = _objects(receipt.get("calls"), "calls must contain objects")
    budgets = _object(receipt.get("budgets"), "budgets must be an object")
    verified_calls = [call for call in calls if call.get("cost_verified") is True]
    unknown_calls = [call for call in calls if call.get("cost_verified") is not True]
    if unknown_calls:
        _require(budgets.get("cost_verified") is False, "unknown call cost forbids verified total")
    if budgets.get("cost_verified") is True:
        _require(len(verified_calls) == len(calls), "verified total requires every call cost")
        total = sum(Decimal(str(call.get("cost_usd", 0))) for call in calls)
        _require(
            Decimal(str(budgets.get("cost_usd"))) == total,
            "aggregate cost must equal exact call cost sum",
        )


def _validate_execution_identity(call: JsonObject, profile: ModelProfile) -> None:
    fields = {"configured_model", "observed_model", "identity_evidence", "quota_group"}
    if not fields.intersection(call):
        return  # Historical receipts did not separate configuration from observation.
    _require(fields <= call.keys(), "execution identity fields must appear together")
    evidence = call.get("identity_evidence")
    configured = call.get("configured_model")
    observed = call.get("observed_model")
    quota_group = call.get("quota_group")
    if evidence == "legacy-unspecified":
        _require(
            configured is None and observed is None,
            "legacy identity evidence cannot claim configuration or observation",
        )
        _require(
            quota_group is None or quota_group == profile.quota_group,
            "call quota group mismatch",
        )
        return
    _require(configured == profile.canonical_model, "configured model mismatch")
    _require(quota_group == profile.quota_group, "call quota group mismatch")
    if evidence in {"provider-response", "session-asserted"}:
        _require(
            call.get("status") == "completed" and observed == call.get("effective_model")
            and observed == configured,
            "observed model must match completed effective identity",
        )
    elif evidence == "configured-route":
        _require(
            profile.transport == "codex-exec" and observed is None
            and call.get("status") == "completed" and call.get("effective_model") == configured,
            "configured route requires completed Codex execution without observed identity",
        )
    else:
        _require(
            evidence == "unavailable" and call.get("status") != "completed"
            and observed is None and call.get("effective_model") is None,
            "unavailable identity requires failed execution without observation",
        )


def validate_receipt(receipt: JsonObject, config: FusionConfig) -> None:
    """Validate receipt schema, policy binding, quorum, and verdict semantics."""

    _validate_schema(receipt, "fusion-receipt.schema.json")
    preset_name = str(receipt["preset"])
    if preset_name.startswith("panel:"):
        expected_panel = preset_name.removeprefix("panel:")
        _require(bool(expected_panel), "explicit panel preset is empty")
    else:
        _, preset = config.preset(preset_name)
        expected_panel = str(preset.get("panel", ""))
    _require(receipt.get("panel") == expected_panel, "receipt panel does not match preset")
    panel = config.panel(expected_panel)
    _require(receipt.get("topology") == panel.get("topology"), "receipt topology mismatch")
    expected = set(panel_participants(panel))
    requested = set(
        _strings(receipt.get("requested_participants"), "requested participants must be strings")
    )
    _require(requested == expected, "receipt participants do not match resolved panel")
    calls = _objects(receipt.get("calls"), "calls must contain objects")
    call_ids = [str(call["call_id"]) for call in calls]
    _require(len(call_ids) == len(set(call_ids)), "call_id values must be unique")
    for call in calls:
        profile = config.model(str(call["handle"]))
        _validate_execution_identity(call, profile)
        _require(call.get("requested_model") == profile.model, "requested model mismatch")
        if call.get("status") == "completed":
            _require(
                call.get("effective_model") == profile.canonical_model,
                "effective model mismatch",
            )
        else:
            _require(call.get("effective_model") is None, "failed call cannot claim model identity")
        _require(call.get("vendor") == profile.vendor, "call vendor mismatch")
        _require(call.get("family") == profile.family, "call family mismatch")
        _require(call.get("mode") == profile.effort, "call mode mismatch")
        _require(call.get("compound") is profile.compound, "call compound status mismatch")
    completed = {str(call["handle"]) for call in calls if call.get("status") == "completed"}
    external = requested - {"self"}
    if receipt.get("fused") is True:
        _require(external <= completed, "fused run requires every external participant")
        _require(receipt.get("context_complete") is True, "fused run requires context quorum")
        _require(not receipt.get("degradation_reasons"), "fused run cannot be degraded")
        _require(receipt.get("state") in {"signed_off", "escalated"}, "invalid fused state")
    if receipt.get("verdict") in {"ship", "revise"}:
        _require(receipt.get("state") == "signed_off", "ship or revise requires signed_off")
    budgets = _object(receipt.get("budgets"), "budgets must be an object")
    _require(budgets.get("used_calls") == len(calls), "used_calls must equal calls length")
    _require(
        int(budgets["used_calls"]) <= int(budgets["max_calls"]), "call budget was exceeded"
    )
    _validate_costs(receipt)


def _finding_counts(analysis: JsonObject) -> JsonObject:
    findings = _objects(analysis.get("findings"), "findings must contain objects")
    severity = Counter(str(finding["severity"]) for finding in findings)
    grounding = _objects(
        analysis.get("grounding_results"), "grounding results must contain objects"
    )
    confirmed = {
        str(result["finding_id"])
        for result in grounding
        if result.get("verdict") == "confirmed"
    }
    proof = _objects(analysis.get("proof_results"), "proof results must contain objects")
    proof_confirmed = {
        str(result["finding_id"])
        for result in proof
        if result.get("verdict") == "confirmed"
        and result.get("mutation_gate_passed") is True
    }
    deadlocks = sum(1 for finding in findings if finding.get("status") == "deadlock")
    return {
        "blocker": severity["blocker"],
        "major": severity["major"],
        "minor": severity["minor"],
        "confirmed_by_exec": len(confirmed),
        "confirmed_by_proof": len(proof_confirmed),
        "deadlocks": deadlocks,
    }


def validate_linked(
    receipt: JsonObject,
    analysis: JsonObject,
    analysis_bytes: bytes,
    config: FusionConfig,
) -> None:
    validate_analysis(analysis)
    validate_receipt(receipt, config)
    for key in ("run_id", "packet_hash", "panel", "topology", "context_complete"):
        _require(receipt.get(key) == analysis.get(key), f"linked {key} mismatch")
    _require(
        receipt.get("analysis_hash") == sha256_bytes(analysis_bytes),
        "analysis artifact hash mismatch",
    )
    requested = set(
        _strings(receipt.get("requested_participants"), "requested participants must be strings")
    )
    participants = _objects(analysis.get("participants"), "participants must contain objects")
    _require(
        {str(item["source_id"]) for item in participants} == requested,
        "analysis participants do not match receipt",
    )
    calls = {
        str(call["call_id"]): call
        for call in _objects(receipt.get("calls"), "calls must contain objects")
    }
    for participant in participants:
        handle = str(participant["source_id"])
        if handle == "self":
            _require(participant.get("call_id") is None, "self cannot have a provider call")
            _require(
                participant.get("effective_model") == receipt.get("self_model"),
                "self model mismatch",
            )
            _require(
                participant.get("identity_hash") == receipt.get("self_identity_hash"),
                "self identity mismatch",
            )
            continue
        participant_call_id = participant.get("call_id")
        call = calls.get(str(participant_call_id))
        _require(
            call is not None and call.get("handle") == handle,
            f"missing linked call for participant {handle}",
        )
        assert call is not None
        for participant_key, call_key in (
            ("call_id", "call_id"),
            ("effective_model", "effective_model"),
            ("family", "family"),
            ("compound", "compound"),
            ("worker_visibility", "worker_visibility"),
            ("output_hash", "output_hash"),
            ("status", "status"),
        ):
            _require(
                participant.get(participant_key) == call.get(call_key),
                f"participant {handle} {participant_key} mismatch",
            )
    counts = _object(receipt.get("findings"), "receipt findings must be an object")
    _require(counts == _finding_counts(analysis), "receipt finding summary mismatch")
    if receipt.get("verdict") == "ship":
        _require(counts["blocker"] == 0 and counts["major"] == 0, "ship has blocking findings")
        decision = _object(analysis.get("decision_impact"), "decision impact must be an object")
        _require(
            decision.get("effect") not in {"blocked", "human-required"},
            "ship conflicts with decision impact",
        )


def validate_linked_paths(receipt_path: Path, analysis_path: Path, config: FusionConfig) -> None:
    analysis_bytes = analysis_path.read_bytes()
    analysis = read_json_object(analysis_path)
    receipt = read_json_object(receipt_path)
    validate_linked(receipt, analysis, analysis_bytes, config)


def canonical_analysis_bytes(analysis: JsonObject) -> bytes:
    validate_analysis(analysis)
    return canonical_json_bytes(analysis) + b"\n"
