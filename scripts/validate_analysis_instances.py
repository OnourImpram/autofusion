from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("autofusion.analysis-contracts")


class AnalysisError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(
            f"invalid JSON at {path.relative_to(ROOT)}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise AnalysisError(
            f"expected JSON object at {path.relative_to(ROOT)}"
        )
    if not all(isinstance(key, str) for key in value):
        raise AnalysisError(
            f"expected string keys at {path.relative_to(ROOT)}"
        )
    return cast(dict[str, Any], value)


def as_object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise AnalysisError(message)
    return cast(dict[str, Any], value)


def as_list(value: Any, message: str) -> list[Any]:
    if not isinstance(value, list):
        raise AnalysisError(message)
    return value


def as_string(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise AnalysisError(message)
    return value


def as_string_list(value: Any, message: str) -> list[str]:
    items = as_list(value, message)
    if not all(isinstance(item, str) and item for item in items):
        raise AnalysisError(message)
    return cast(list[str], items)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AnalysisError(message)


def load_trusted_attestations() -> dict[str, Any]:
    return load_object(ROOT / "tests" / "fixtures" / "trusted-attestations.json")


def trusted_section(name: str) -> dict[str, Any]:
    attestations = load_trusted_attestations()
    return as_object(attestations.get(name), f"trusted {name} attestations must exist")


def validate_schema_instance(
    analysis: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(analysis),
        key=lambda error: error.json_path,
    )
    if errors:
        first = errors[0]
        raise AnalysisError(
            f"analysis schema violation at {first.json_path}: {first.message}"
        )


def allowed_model_identities(model: dict[str, Any]) -> set[str]:
    identities: set[str] = set()
    for key in ("model", "canonical_model"):
        value = model.get(key)
        if isinstance(value, str) and value:
            identities.add(value)
    aliases_raw = model.get("effective_model_aliases")
    if aliases_raw is not None:
        identities.update(
            as_string_list(
                aliases_raw,
                "effective_model_aliases must be a string array",
            )
        )
    return identities


def resolve_preset_panel(
    config: dict[str, Any],
    preset_name: str,
) -> tuple[str, dict[str, Any]]:
    presets = as_object(config.get("presets"), "presets must be an object")
    panels = as_object(config.get("panels"), "panels must be an object")
    seen: set[str] = set()
    current = preset_name
    while True:
        require(current not in seen, "preset aliases must not cycle")
        seen.add(current)
        require(current in presets, f"unknown preset {current}")
        preset = as_object(presets[current], f"preset {current} must be an object")
        alias = preset.get("alias_for")
        if isinstance(alias, str) and alias:
            current = alias
            continue
        require("panel" in preset, f"preset {preset_name} must resolve to a panel")
        panel_name = as_string(preset.get("panel"), f"preset {current}.panel must be a string")
        require(panel_name in panels, f"preset {current} references unknown panel")
        return panel_name, as_object(panels[panel_name], f"panel {panel_name} must be an object")


def expected_panel_participants(panel: dict[str, Any]) -> set[str]:
    participants: set[str] = set()
    for field in ("drafter", "judge"):
        value = panel.get(field)
        if isinstance(value, str) and value:
            participants.add(value)
    for field in ("reviewers", "proposers"):
        value = panel.get(field)
        if value is None:
            continue
        participants.update(as_string_list(value, f"panel {field} must be a string array"))
    return participants


def validate_analysis_policy_binding(
    analysis: dict[str, Any],
    config: dict[str, Any],
    models: dict[str, Any],
    participants: set[str],
) -> None:
    panel_name = as_string(analysis.get("panel"), "analysis panel must be a string")
    panels = as_object(config.get("panels"), "panels must be an object")
    require(panel_name in panels, "analysis panel must exist in the registry")
    panel = as_object(panels[panel_name], f"panel {panel_name} must be an object")
    require(
        analysis.get("topology") == panel.get("topology"),
        "analysis topology must match the declared panel",
    )
    require(
        participants == expected_panel_participants(panel),
        "analysis participants must exactly match the declared panel",
    )
    guardrails = as_object(config.get("guardrails"), "guardrails must be an object")
    allowlist = set(
        as_string_list(
            guardrails.get("model_allowlist"),
            "guardrails.model_allowlist must be a string array",
        )
    )
    denylist = set(
        as_string_list(
            guardrails.get("provider_denylist"),
            "guardrails.provider_denylist must be a string array",
        )
    )
    for handle in participants - {"self"}:
        require(handle in allowlist, f"participant {handle} is not model-allowlisted")
        model = as_object(models.get(handle), f"model {handle} must be an object")
        vendor = as_string(model.get("vendor"), f"model {handle} vendor must be a string")
        require(vendor not in denylist, f"participant {handle} vendor is denied")


def trusted_verification_ids(config: dict[str, Any]) -> set[str]:
    verification = as_object(
        config.get("verification"),
        "verification must be an object",
    )
    profiles = as_object(
        verification.get("profiles"),
        "verification.profiles must be an object",
    )
    identifiers: set[str] = set()
    for profile_name, raw_profile in profiles.items():
        profile = as_object(
            raw_profile,
            f"verification profile {profile_name} must be an object",
        )
        commands = as_object(
            profile.get("commands"),
            f"verification profile {profile_name} commands must be an object",
        )
        for command_name, raw_command in commands.items():
            command = as_object(
                raw_command,
                f"verification command {profile_name}.{command_name} "
                "must be an object",
            )
            as_string_list(
                command.get("argv"),
                f"verification command {profile_name}.{command_name} "
                "must use argv",
            )
            identifiers.add(f"{profile_name}.{command_name}")
    require(bool(identifiers), "verification registry must not be empty")
    return identifiers


def resolve_self_profile(
    models: dict[str, Any],
    effective_model: str,
) -> dict[str, Any]:
    self_registry = as_object(
        models.get("self"),
        "self registry entry must be an object",
    )
    allowed = set(
        as_string_list(
            self_registry.get("allowed_models"),
            "self.allowed_models must be a string array",
        )
    )
    require(
        effective_model in allowed,
        "self participant identity is not allowlisted",
    )
    for handle, raw_model in models.items():
        if handle == "self":
            continue
        model = as_object(raw_model, f"model {handle} must be an object")
        if effective_model in allowed_model_identities(model):
            return model
    raise AnalysisError(
        "self participant identity has no trusted model profile"
    )


def validate_participants(
    analysis: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, str], set[str]]:
    models = as_object(config.get("models"), "models must be an object")
    participants = as_list(
        analysis.get("participants"),
        "participants must be an array",
    )
    families: dict[str, str] = {}
    eligible: set[str] = set()
    statuses: list[str] = []
    participant_ids: set[str] = set()

    for raw_participant in participants:
        participant = as_object(
            raw_participant,
            "each participant must be an object",
        )
        source_id = as_string(
            participant.get("source_id"),
            "participant source_id must be a string",
        )
        require(
            source_id not in families,
            f"duplicate participant source_id {source_id}",
        )
        participant_ids.add(source_id)
        effective_model = as_string(
            participant.get("effective_model"),
            f"participant {source_id} effective_model must be a string",
        )

        call_id = participant.get("call_id")
        output_hash = participant.get("output_hash")
        as_string(
            participant.get("identity_hash"),
            f"participant {source_id} identity_hash must be present",
        )
        if source_id == "self":
            require(call_id is None, "self participant must not have a call_id")
            require(output_hash is not None, "self participant must hash its output artifact")
            as_string(output_hash, "self participant output_hash must be a hash")
            model = resolve_self_profile(models, effective_model)
        else:
            as_string(call_id, f"participant {source_id} call_id must be a string")
            as_string(output_hash, f"participant {source_id} output_hash must be a hash")
            require(
                source_id in models,
                f"participant {source_id} is absent from the model registry",
            )
            model = as_object(
                models[source_id],
                f"model {source_id} must be an object",
            )
            require(
                model.get("callable") is True,
                f"participant {source_id} is not callable",
            )
            require(
                effective_model in allowed_model_identities(model),
                f"participant {source_id} effective_model is not allowlisted",
            )

        family = as_string(
            participant.get("family"),
            f"participant {source_id} family must be a string",
        )
        require(
            family == model.get("family"),
            f"participant {source_id} family differs from the registry",
        )
        require(
            participant.get("compound") is (model.get("compound") is True),
            f"participant {source_id} compound flag differs from the registry",
        )
        expected_visibility = model.get("worker_visibility")
        if not isinstance(expected_visibility, str):
            expected_visibility = "not-applicable"
        require(
            participant.get("worker_visibility") == expected_visibility,
            f"participant {source_id} worker visibility differs "
            "from the registry",
        )

        status = as_string(
            participant.get("status"),
            f"participant {source_id} status must be a string",
        )
        statuses.append(status)
        families[source_id] = family
        if status == "completed":
            eligible.add(source_id)

    validate_analysis_policy_binding(analysis, config, models, participant_ids)

    expected_context_complete = all(
        status == "completed" for status in statuses
    )
    require(
        analysis.get("context_complete") is expected_context_complete,
        "context_complete does not match participant completion",
    )
    if not expected_context_complete:
        decision = as_object(
            analysis.get("decision_impact"),
            "decision_impact must be an object",
        )
        require(
            decision.get("effect") in {"blocked", "human-required"},
            "incomplete participant context must block or require a human",
        )
    return families, eligible


def validate_evidence(
    raw_evidence: Any,
    eligible: set[str],
    label: str,
    *,
    allow_grounded: bool,
) -> tuple[str, set[str]]:
    evidence = as_object(raw_evidence, f"{label} evidence must be an object")
    sources = set(
        as_string_list(
            evidence.get("sources"),
            f"{label} evidence sources must be a string array",
        )
    )
    require(
        sources <= eligible,
        f"{label} evidence references an ineligible or unknown source",
    )
    strength = as_string(
        evidence.get("strength"),
        f"{label} evidence strength must be a string",
    )
    if strength == "grounded":
        require(
            allow_grounded,
            f"{label} cannot claim grounding without a finding result",
        )
    return strength, sources


def validate_consensus(
    analysis: dict[str, Any],
    families: dict[str, str],
    eligible: set[str],
) -> None:
    consensus = as_list(
        analysis.get("consensus"),
        "consensus must be an array",
    )
    identifiers: set[str] = set()
    for raw_item in consensus:
        item = as_object(raw_item, "each consensus item must be an object")
        item_id = as_string(item.get("id"), "consensus id must be a string")
        require(item_id not in identifiers, f"duplicate consensus id {item_id}")
        identifiers.add(item_id)

        supporters = set(
            as_string_list(
                item.get("supporters"),
                f"consensus {item_id} supporters must be a string array",
            )
        )
        require(
            supporters <= eligible,
            f"consensus {item_id} has an ineligible or unknown supporter",
        )
        _strength, evidence_sources = validate_evidence(
            item.get("evidence"),
            eligible,
            f"consensus {item_id}",
            allow_grounded=False,
        )
        require(
            evidence_sources <= supporters,
            f"consensus {item_id} evidence exceeds declared supporters",
        )

        supporter_families = {families[source] for source in supporters}
        require(
            len(supporters) >= 2,
            f"consensus {item_id} requires at least two supporters",
        )
        expected_agreement = (
            "same-family" if len(supporter_families) == 1 else "cross-family"
        )
        require(
            item.get("agreement_strength") == expected_agreement,
            f"consensus {item_id} agreement_strength is inconsistent",
        )


def validate_contradictions(
    analysis: dict[str, Any],
    eligible: set[str],
) -> None:
    contradictions = as_list(
        analysis.get("contradictions"),
        "contradictions must be an array",
    )
    identifiers: set[str] = set()
    for raw_item in contradictions:
        item = as_object(
            raw_item,
            "each contradiction must be an object",
        )
        item_id = as_string(
            item.get("id"),
            "contradiction id must be a string",
        )
        require(
            item_id not in identifiers,
            f"duplicate contradiction id {item_id}",
        )
        identifiers.add(item_id)

        positions = as_list(
            item.get("positions"),
            f"contradiction {item_id} positions must be an array",
        )
        position_sources: set[str] = set()
        for raw_position in positions:
            position = as_object(
                raw_position,
                f"contradiction {item_id} position must be an object",
            )
            source_id = as_string(
                position.get("source_id"),
                f"contradiction {item_id} source_id must be a string",
            )
            require(
                source_id in eligible,
                f"contradiction {item_id} uses an ineligible source",
            )
            require(
                source_id not in position_sources,
                f"contradiction {item_id} repeats a source position",
            )
            position_sources.add(source_id)
            _strength, evidence_sources = validate_evidence(
                position.get("evidence"),
                eligible,
                f"contradiction {item_id}",
                allow_grounded=False,
            )
            require(
                source_id in evidence_sources,
                f"contradiction {item_id} position lacks source evidence",
            )


def validate_coverage_and_unique_insights(
    analysis: dict[str, Any],
    eligible: set[str],
) -> None:
    coverage = as_list(
        analysis.get("partial_coverage"),
        "partial_coverage must be an array",
    )
    for raw_item in coverage:
        item = as_object(raw_item, "coverage item must be an object")
        covered_by = set(
            as_string_list(
                item.get("covered_by"),
                "coverage covered_by must be a string array",
            )
        )
        require(
            covered_by <= eligible,
            "coverage references an ineligible or unknown source",
        )

    insights = as_list(
        analysis.get("unique_insights"),
        "unique_insights must be an array",
    )
    for raw_item in insights:
        item = as_object(raw_item, "unique insight must be an object")
        source_id = as_string(
            item.get("source_id"),
            "unique insight source_id must be a string",
        )
        require(
            source_id in eligible,
            "unique insight source is ineligible or unknown",
        )
        _strength, evidence_sources = validate_evidence(
            item.get("evidence"),
            eligible,
            "unique insight",
            allow_grounded=False,
        )
        require(
            source_id in evidence_sources,
            "unique insight lacks its source evidence",
        )


def validate_grounding(
    analysis: dict[str, Any],
    config: dict[str, Any],
    eligible: set[str],
) -> None:
    trusted_verifications = trusted_verification_ids(config)
    findings_raw = as_list(
        analysis.get("findings"),
        "findings must be an array",
    )
    findings: dict[str, dict[str, Any]] = {}
    for raw_finding in findings_raw:
        finding = as_object(raw_finding, "each finding must be an object")
        finding_id = as_string(
            finding.get("id"),
            "finding id must be a string",
        )
        require(
            finding_id not in findings,
            f"duplicate finding id {finding_id}",
        )
        findings[finding_id] = finding

    candidates_raw = as_list(
        analysis.get("grounding_candidates"),
        "grounding_candidates must be an array",
    )
    candidates: set[tuple[str, str]] = set()
    for raw_candidate in candidates_raw:
        candidate = as_object(
            raw_candidate,
            "each grounding candidate must be an object",
        )
        finding_id = as_string(
            candidate.get("finding_id"),
            "grounding candidate finding_id must be a string",
        )
        verification_id = as_string(
            candidate.get("verification_id"),
            "grounding candidate verification_id must be a string",
        )
        require(
            finding_id in findings,
            f"grounding candidate references unknown finding {finding_id}",
        )
        require(
            verification_id in trusted_verifications,
            f"grounding candidate uses unknown verification {verification_id}",
        )
        pair = (finding_id, verification_id)
        require(pair not in candidates, "duplicate grounding candidate")
        candidates.add(pair)

    results_raw = as_list(
        analysis.get("grounding_results"),
        "grounding_results must be an array",
    )
    results: dict[tuple[str, str], str] = {}
    for raw_result in results_raw:
        result = as_object(
            raw_result,
            "each grounding result must be an object",
        )
        finding_id = as_string(
            result.get("finding_id"),
            "grounding result finding_id must be a string",
        )
        verification_id = as_string(
            result.get("verification_id"),
            "grounding result verification_id must be a string",
        )
        pair = (finding_id, verification_id)
        require(pair in candidates, "grounding result lacks a trusted candidate")
        require(pair not in results, "duplicate grounding result")
        verdict = as_string(
            result.get("verdict"),
            "grounding result verdict must be a string",
        )
        as_string(
            result.get("output_hash"),
            "executed grounding result must include output_hash",
        )
        exit_code = result.get("exit_code")
        require(
            isinstance(exit_code, int) and not isinstance(exit_code, bool),
            "executed grounding result must include integer exit_code",
        )
        execution_status = as_string(
            result.get("execution_status"),
            "grounding result execution_status must be a string",
        )
        failure_class = result.get("failure_class")
        matched_expected_failure = result.get("matched_expected_failure")
        require(
            isinstance(matched_expected_failure, bool),
            "grounding result matched_expected_failure must be a boolean",
        )
        confirming_failure_classes = {"assertion", "static-diagnostic"}
        runner_trust = as_string(
            result.get("runner_trust"),
            "grounding result runner_trust must be a string",
        )
        as_string(
            result.get("invocation_hash"),
            "grounding result invocation_hash must be present",
        )
        as_string(
            result.get("runner_attestation_hash"),
            "grounding result runner_attestation_hash must be present",
        )
        if verdict == "confirmed":
            runner_attestation_hash = as_string(
                result.get("runner_attestation_hash"),
                "runner attestation hash must be a string",
            )
            runner_attestation = as_object(
                trusted_section("runner").get(runner_attestation_hash),
                "confirmed grounding requires trusted runner evidence",
            )
            require(
                runner_attestation.get("runner_trust") == runner_trust
                and runner_attestation.get("verification_id") == verification_id
                and runner_attestation.get("finding_id") == finding_id
                and runner_attestation.get("invocation_hash")
                == result.get("invocation_hash")
                and runner_attestation.get("output_hash") == result.get("output_hash")
                and runner_attestation.get("execution_status") == execution_status
                and runner_attestation.get("failure_class") == failure_class
                and runner_attestation.get("matched_expected_failure")
                == matched_expected_failure
                and runner_attestation.get("exit_code") == exit_code,
                "runner attestation must match the grounding result",
            )
            require(
                runner_trust == "trusted-runner",
                "confirmed grounding result requires trusted runner attestation",
            )
            require(
                execution_status == "completed",
                "confirmed grounding result must complete execution",
            )
            require(
                failure_class in confirming_failure_classes,
                "confirmed grounding result requires an assertion or static diagnostic",
            )
            require(
                matched_expected_failure is True,
                "confirmed grounding result must match the expected failure",
            )
            require(
                exit_code != 0,
                "confirmed grounding result must have nonzero exit_code",
            )
        if verdict == "not-reproduced":
            require(
                execution_status == "completed",
                "not-reproduced grounding result must complete execution",
            )
            require(
                exit_code == 0,
                "not-reproduced grounding result must have zero exit_code",
            )
            require(
                failure_class is None,
                "not-reproduced grounding result must not carry a failure class",
            )
            require(
                matched_expected_failure is False,
                "not-reproduced grounding result cannot match the expected failure",
            )
        if verdict == "inconclusive":
            require(
                not (
                    execution_status == "completed"
                    and failure_class in confirming_failure_classes
                    and matched_expected_failure is True
                ),
                "inconclusive grounding result cannot carry confirming evidence",
            )
        results[pair] = verdict

    for finding_id, finding in findings.items():
        source_ids = set(
            as_string_list(
                finding.get("source_ids"),
                f"finding {finding_id} source_ids must be a string array",
            )
        )
        require(
            source_ids <= eligible,
            f"finding {finding_id} uses an ineligible or unknown source",
        )
        strength, evidence_sources = validate_evidence(
            finding.get("evidence"),
            eligible,
            f"finding {finding_id}",
            allow_grounded=True,
        )
        require(
            evidence_sources <= source_ids,
            f"finding {finding_id} evidence exceeds declared sources",
        )

        checkable = finding.get("checkable")
        if not isinstance(checkable, bool):
            raise AnalysisError(
                f"finding {finding_id} checkable must be a boolean"
            )
        raw_verification_id = finding.get("verification_id")
        finding_pair: tuple[str, str] | None
        if checkable:
            verification_id = as_string(
                raw_verification_id,
                f"finding {finding_id} requires verification_id",
            )
            require(
                verification_id in trusted_verifications,
                f"finding {finding_id} uses unknown verification",
            )
            finding_pair = (finding_id, verification_id)
            require(
                finding_pair in candidates,
                f"finding {finding_id} lacks a grounding candidate",
            )
        else:
            require(
                raw_verification_id is None,
                f"noncheckable finding {finding_id} cannot name verification",
            )
            finding_pair = None

        status = as_string(
            finding.get("status"),
            f"finding {finding_id} status must be a string",
        )
        if status == "grounded" or strength == "grounded":
            if not checkable or finding_pair is None:
                raise AnalysisError(
                    f"grounded finding {finding_id} must be checkable"
                )
            require(
                results.get(finding_pair) == "confirmed",
                f"grounded finding {finding_id} lacks confirmed execution",
            )


def validate_analysis_semantics(
    analysis: dict[str, Any],
    config: dict[str, Any],
) -> None:
    families, eligible = validate_participants(analysis, config)
    validate_consensus(analysis, families, eligible)
    validate_contradictions(analysis, eligible)
    validate_coverage_and_unique_insights(analysis, eligible)
    validate_grounding(analysis, config, eligible)


def validate_analysis(
    analysis: dict[str, Any],
    schema: dict[str, Any],
    config: dict[str, Any],
) -> None:
    validate_schema_instance(analysis, schema)
    validate_analysis_semantics(analysis, config)


def clone(analysis: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(analysis)


def first_participant(analysis: dict[str, Any]) -> dict[str, Any]:
    participants = as_list(
        analysis.get("participants"),
        "participants must be an array",
    )
    require(bool(participants), "fixture must contain participants")
    return as_object(participants[0], "participant must be an object")


def second_participant(analysis: dict[str, Any]) -> dict[str, Any]:
    participants = as_list(
        analysis.get("participants"),
        "participants must be an array",
    )
    require(len(participants) >= 2, "fixture must contain two participants")
    return as_object(participants[1], "participant must be an object")


def first_consensus(analysis: dict[str, Any]) -> dict[str, Any]:
    consensus = as_list(analysis.get("consensus"), "consensus must be an array")
    require(bool(consensus), "fixture must contain consensus")
    return as_object(consensus[0], "consensus item must be an object")


def first_finding(analysis: dict[str, Any]) -> dict[str, Any]:
    findings = as_list(analysis.get("findings"), "findings must be an array")
    require(bool(findings), "fixture must contain findings")
    return as_object(findings[0], "finding must be an object")


def first_candidate(analysis: dict[str, Any]) -> dict[str, Any]:
    candidates = as_list(
        analysis.get("grounding_candidates"),
        "grounding_candidates must be an array",
    )
    require(bool(candidates), "fixture must contain a grounding candidate")
    return as_object(candidates[0], "grounding candidate must be an object")


def first_grounding_result(analysis: dict[str, Any]) -> dict[str, Any]:
    results = as_list(
        analysis.get("grounding_results"),
        "grounding_results must be an array",
    )
    require(bool(results), "fixture must contain a grounding result")
    return as_object(results[0], "grounding result must be an object")


def assert_rejected(
    label: str,
    candidate: dict[str, Any],
    schema: dict[str, Any],
    config: dict[str, Any],
) -> None:
    try:
        validate_analysis(candidate, schema, config)
    except AnalysisError:
        LOGGER.info("negative analysis rejected: %s", label)
        return
    raise AnalysisError(f"negative analysis was accepted: {label}")


def exercise_negative_cases(
    valid: dict[str, Any],
    schema: dict[str, Any],
    config: dict[str, Any],
) -> None:
    candidate = clone(valid)
    consensus = first_consensus(candidate)
    consensus["supporters"] = ["self", "ghost"]
    assert_rejected("nonexistent consensus supporter", candidate, schema, config)

    candidate = clone(valid)
    second_participant(candidate)["status"] = "failed"
    candidate["context_complete"] = False
    decision = as_object(
        candidate.get("decision_impact"),
        "decision_impact must be an object",
    )
    decision["effect"] = "blocked"
    assert_rejected("failed participant used as evidence", candidate, schema, config)

    candidate = clone(valid)
    first_consensus(candidate)["agreement_strength"] = "same-family"
    assert_rejected("fabricated agreement strength", candidate, schema, config)

    candidate = clone(valid)
    consensus = first_consensus(candidate)
    consensus["supporters"] = ["self"]
    evidence = as_object(consensus.get("evidence"), "consensus evidence must be an object")
    evidence["sources"] = ["self"]
    assert_rejected("single-source consensus item", candidate, schema, config)

    candidate = clone(valid)
    second_participant(candidate)["effective_model"] = "claude-fable-5"
    assert_rejected(
        "deliberately disallowed legacy Fable participant identity", candidate, schema, config
    )

    candidate = clone(valid)
    first_finding(candidate)["verification_id"] = "python.untrusted"
    assert_rejected("untrusted finding verification", candidate, schema, config)

    candidate = clone(valid)
    result = first_grounding_result(candidate)
    result["execution_status"] = "runner-error"
    result["failure_class"] = "environment"
    result["exit_code"] = 127
    assert_rejected("runner error cannot confirm grounding", candidate, schema, config)

    candidate = clone(valid)
    result = first_grounding_result(candidate)
    result["runner_trust"] = "unknown"
    assert_rejected("untrusted runner cannot confirm grounding", candidate, schema, config)

    candidate = clone(valid)
    result = first_grounding_result(candidate)
    result["runner_attestation_hash"] = "9" * 64
    assert_rejected("forged runner attestation", candidate, schema, config)

    candidate = clone(valid)
    candidate["grounding_results"] = []
    assert_rejected("grounded finding without execution", candidate, schema, config)

    candidate = clone(valid)
    first_candidate(candidate)["finding_id"] = "f99"
    assert_rejected("candidate for nonexistent finding", candidate, schema, config)

    candidate = clone(valid)
    first_participant(candidate)["status"] = "failed"
    assert_rejected("complete context with failed participant", candidate, schema, config)

    candidate = clone(valid)
    candidate["topology"] = "panel-rank"
    candidate["panel"] = "external-council"
    assert_rejected("self cannot appear in panel-rank analysis", candidate, schema, config)

    candidate = clone(valid)
    denied_config = clone(config)
    guardrails = as_object(denied_config.get("guardrails"), "guardrails must be an object")
    guardrails["provider_denylist"] = ["openai"]
    assert_rejected("denied provider in analysis", candidate, schema, denied_config)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    schema = load_object(ROOT / "schemas" / "fusion-analysis.schema.json")
    config = load_object(ROOT / ".fusion.example.json")
    valid = load_object(ROOT / "tests" / "fixtures" / "analysis-valid.json")
    try:
        Draft202012Validator.check_schema(schema)
        validate_analysis(valid, schema, config)
        exercise_negative_cases(valid, schema, config)
    except (AnalysisError, SchemaError) as exc:
        LOGGER.error("%s", exc)
        return 1
    LOGGER.info("analysis contract instances passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
