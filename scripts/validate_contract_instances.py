from __future__ import annotations

import copy
import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("autofusion.contracts")


class ContractError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(
            f"invalid JSON at {path.relative_to(ROOT)}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ContractError(
            f"expected JSON object at {path.relative_to(ROOT)}"
        )
    if not all(isinstance(key, str) for key in value):
        raise ContractError(
            f"expected string keys at {path.relative_to(ROOT)}"
        )
    return cast(dict[str, Any], value)


def as_object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ContractError(message)
    return cast(dict[str, Any], value)


def as_list(value: Any, message: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(message)
    return value


def as_string(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(message)
    return value


def as_string_list(value: Any, message: str) -> list[str]:
    items = as_list(value, message)
    if not all(isinstance(item, str) and item for item in items):
        raise ContractError(message)
    return cast(list[str], items)


def as_nonnegative_int(value: Any, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(message)
    return cast(int, value)


def as_positive_int(value: Any, message: str) -> int:
    result = as_nonnegative_int(value, message)
    if result < 1:
        raise ContractError(message)
    return result


def as_nonnegative_number(value: Any, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(message)
    if value < 0:
        raise ContractError(message)
    return float(value)


def as_nonnegative_decimal(value: Any, message: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(message)
    if value < 0:
        raise ContractError(message)
    return Decimal(str(value))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load_trusted_attestations() -> dict[str, Any]:
    return load_object(ROOT / "tests" / "fixtures" / "trusted-attestations.json")


def trusted_section(name: str) -> dict[str, Any]:
    attestations = load_trusted_attestations()
    return as_object(attestations.get(name), f"trusted {name} attestations must exist")


def validate_schema_instance(
    receipt: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(receipt),
        key=lambda error: error.json_path,
    )
    if errors:
        first = errors[0]
        raise ContractError(
            f"receipt schema violation at {first.json_path}: {first.message}"
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


def canonicalize_handle_identity(
    identity: str,
    model: dict[str, Any],
) -> str:
    alias = model.get("model")
    canonical = model.get("canonical_model")
    if identity == alias and isinstance(canonical, str) and canonical:
        return canonical
    return identity


def parse_datetime(value: Any, message: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ContractError(message)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractError(message) from exc


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
        require(current in presets, f"unknown runtime preset {current}")
        preset = as_object(presets[current], f"preset {current} must be an object")
        alias = preset.get("alias_for")
        if isinstance(alias, str) and alias:
            current = alias
            continue
        require(
            "panel" in preset,
            f"runtime preset {preset_name} must resolve to a concrete panel",
        )
        panel_name = as_string(
            preset.get("panel"),
            f"preset {current}.panel must be a string",
        )
        require(panel_name in panels, f"preset {current} references unknown panel")
        return panel_name, as_object(
            panels[panel_name],
            f"panel {panel_name} must be an object",
        )


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


def validate_runtime_policy_binding(
    receipt: dict[str, Any],
    config: dict[str, Any],
    models: dict[str, Any],
    participants: set[str],
) -> None:
    preset_name = as_string(receipt.get("preset"), "preset must be a string")
    panel_name, panel = resolve_preset_panel(config, preset_name)
    declared_panel = as_string(receipt.get("panel"), "panel must be a string")
    require(
        declared_panel == panel_name,
        "receipt panel must match the resolved preset panel",
    )
    require(
        receipt.get("topology") == panel.get("topology"),
        "receipt topology must match the resolved panel topology",
    )
    require(
        participants == expected_panel_participants(panel),
        "requested participants must exactly match the resolved panel",
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


def validate_timestamps(receipt: dict[str, Any]) -> None:
    finished_raw = receipt.get("finished_at")
    if finished_raw is None:
        return
    started = parse_datetime(receipt.get("started_at"), "started_at must be a date-time")
    finished = parse_datetime(finished_raw, "finished_at must be a date-time")
    require(finished >= started, "finished_at must not precede started_at")
    budgets = as_object(receipt.get("budgets"), "budgets must be an object")
    wallclock = as_nonnegative_number(
        budgets.get("wallclock_s"),
        "wallclock_s must be a nonnegative number",
    )
    elapsed = (finished - started).total_seconds()
    require(
        abs(elapsed - wallclock) <= 0.001,
        "wallclock_s must match started_at and finished_at",
    )


def validate_call_registry(
    call: dict[str, Any],
    models: dict[str, Any],
    participants: set[str],
) -> tuple[str, str | None]:
    handle = as_string(call.get("handle"), "call handle must be a string")
    require(handle != "self", "self cannot appear as an external call")
    require(handle in participants, f"call handle {handle} was not requested")
    require(handle in models, f"call handle {handle} is absent from the registry")
    model = as_object(models[handle], f"model {handle} must be an object")
    require(
        model.get("callable") is True,
        f"call handle {handle} is not callable",
    )

    requested_model = as_string(
        call.get("requested_model"),
        f"call {handle} requested_model must be a string",
    )
    require(
        requested_model == model.get("model"),
        f"call {handle} requested_model differs from the registry",
    )
    require(
        call.get("vendor") == model.get("vendor"),
        f"call {handle} vendor differs from the registry",
    )
    require(
        call.get("family") == model.get("family"),
        f"call {handle} family differs from the registry",
    )

    expected_effort = model.get("effort")
    if isinstance(expected_effort, str):
        require(
            call.get("mode") == expected_effort,
            f"call {handle} mode differs from the registry",
        )

    expected_compound = model.get("compound") is True
    require(
        call.get("compound") is expected_compound,
        f"call {handle} compound flag differs from the registry",
    )
    expected_visibility = model.get("worker_visibility")
    if not isinstance(expected_visibility, str):
        expected_visibility = "not-applicable"
    require(
        call.get("worker_visibility") == expected_visibility,
        f"call {handle} worker visibility differs from the registry",
    )

    raw_effective_model = call.get("effective_model")
    if raw_effective_model is None:
        return handle, None

    effective_model = as_string(
        raw_effective_model,
        f"call {handle} effective_model must be a string or null",
    )
    allowed_identities = allowed_model_identities(model)
    require(
        effective_model in allowed_identities,
        f"call {handle} effective_model is not allowed by the registry",
    )
    return handle, canonicalize_handle_identity(effective_model, model)

def validate_budget_semantics(
    receipt: dict[str, Any],
    config: dict[str, Any],
    calls: list[Any],
) -> None:
    budgets = as_object(receipt.get("budgets"), "budgets must be an object")
    max_calls = as_positive_int(
        budgets.get("max_calls"),
        "max_calls must be a positive integer",
    )
    used_calls = as_nonnegative_int(
        budgets.get("used_calls"),
        "used_calls must be a nonnegative integer",
    )
    max_wallclock = as_nonnegative_number(
        budgets.get("max_wallclock_s"),
        "max_wallclock_s must be a nonnegative number",
    )
    wallclock = as_nonnegative_number(
        budgets.get("wallclock_s"),
        "wallclock_s must be a nonnegative number",
    )

    require(
        used_calls == len(calls),
        "used_calls must equal the call record count",
    )
    require(used_calls <= max_calls, "used_calls exceeds max_calls")
    require(wallclock <= max_wallclock, "wallclock_s exceeds max_wallclock_s")

    call_costs: list[Decimal] = []
    has_unknown_cost = not calls
    all_known_costs_verified = bool(calls)
    for raw_call in calls:
        call = as_object(raw_call, "each call must be an object")
        raw_call_cost = call.get("cost_usd")
        call_cost_verified = call.get("cost_verified")
        require(
            isinstance(call_cost_verified, bool),
            "call cost_verified must be a boolean",
        )
        cost_source = as_string(
            call.get("cost_source"),
            "call cost_source must be a string",
        )
        provider_usage_hash = call.get("provider_usage_hash")
        if raw_call_cost is None:
            require(
                call_cost_verified is False,
                "unknown call cost cannot be marked verified",
            )
            require(
                cost_source == "unknown",
                "unknown call cost must use unknown cost_source",
            )
            require(
                provider_usage_hash is None,
                "unknown call cost cannot carry provider usage attestation",
            )
            has_unknown_cost = True
            continue
        if call_cost_verified is True:
            require(
                cost_source in {"provider-usage", "subscription-entitlement"},
                "verified cost requires a trusted cost source",
            )
            cost_hash = as_string(
                provider_usage_hash,
                "verified cost requires provider_usage_hash",
            )
            cost_attestation = as_object(
                trusted_section("cost").get(cost_hash),
                "provider usage hash must resolve to trusted cost evidence",
            )
            require(
                cost_attestation.get("source") == cost_source
                and cost_attestation.get("handle") == call.get("handle")
                and Decimal(str(cost_attestation.get("cost_usd"))) == Decimal(str(raw_call_cost)),
                "cost attestation must match the call cost",
            )
        else:
            all_known_costs_verified = False
            require(
                cost_source == "unknown",
                "unverified known cost must use unknown cost_source",
            )
            require(
                provider_usage_hash is None,
                "unverified known cost cannot carry provider usage attestation",
            )
        call_costs.append(
            as_nonnegative_decimal(
                raw_call_cost,
                "call cost_usd must be null or nonnegative",
            )
        )

    max_cost_raw = budgets.get("max_cost_usd")
    aggregate_cost_raw = budgets.get("cost_usd")
    cost_verified = budgets.get("cost_verified")
    require(
        isinstance(cost_verified, bool),
        "cost_verified must be a boolean",
    )

    if has_unknown_cost:
        require(
            aggregate_cost_raw is None,
            "aggregate cost must be null when any call cost is unknown",
        )
        require(
            cost_verified is False,
            "cost_verified must be false when any call cost is unknown",
        )
        require(
            max_cost_raw is None,
            "a hard monetary budget cannot be verified with unknown call cost",
        )
    else:
        aggregate_cost = as_nonnegative_decimal(
            aggregate_cost_raw,
            "aggregate cost_usd must be known when call costs are known",
        )
        expected_cost = sum(call_costs, start=Decimal("0"))
        require(
            aggregate_cost == expected_cost,
            "aggregate cost_usd must equal the sum of call costs",
        )
        require(
            cost_verified is all_known_costs_verified,
            "aggregate cost_verified must match per-call cost verification",
        )
        if max_cost_raw is not None:
            max_cost = as_nonnegative_decimal(
                max_cost_raw,
                "max_cost_usd must be null or nonnegative",
            )
            require(
                aggregate_cost <= max_cost,
                "cost_usd exceeds max_cost_usd",
            )

    guardrails = as_object(
        config.get("guardrails"),
        "guardrails must be an object",
    )
    configured_max_calls = as_positive_int(
        guardrails.get("max_calls_per_run"),
        "configured max_calls_per_run must be a positive integer",
    )
    configured_max_wallclock = as_positive_int(
        guardrails.get("max_wallclock_s"),
        "configured max_wallclock_s must be a positive integer",
    )
    require(
        max_calls <= configured_max_calls,
        "receipt max_calls exceeds the configured global guardrail",
    )
    require(
        max_wallclock <= configured_max_wallclock,
        "receipt max_wallclock_s exceeds the configured global guardrail",
    )

def validate_receipt_semantics(
    receipt: dict[str, Any],
    config: dict[str, Any],
) -> None:
    models = as_object(config.get("models"), "models must be an object")
    participant_list = as_string_list(
        receipt.get("requested_participants"),
        "requested_participants must be a string array",
    )
    participants = set(participant_list)
    require(
        len(participants) == len(participant_list),
        "requested participants must be unique",
    )

    guardrails = as_object(
        config.get("guardrails"),
        "guardrails must be an object",
    )
    max_panel_size = as_positive_int(
        guardrails.get("max_panel_size"),
        "configured max_panel_size must be a positive integer",
    )
    require(
        len(participants) <= max_panel_size,
        "requested participants exceed max_panel_size",
    )
    validate_runtime_policy_binding(receipt, config, models, participants)

    resolved_models: set[str] = set()
    if "self" in participants:
        self_registry = as_object(
            models.get("self"),
            "self model registry entry must be an object",
        )
        self_model = as_string(
            receipt.get("self_model"),
            "self_model must identify the active session model",
        )
        allowed_self_models = set(
            as_string_list(
                self_registry.get("allowed_models"),
                "self.allowed_models must be a string array",
            )
        )
        require(
            self_model in allowed_self_models,
            "self_model is not allowed by the trusted registry",
        )
        require(
            receipt.get("self_identity_source")
            == self_registry.get("identity_source")
            == "runtime-attested",
            "self identity must be runtime-attested",
        )
        self_identity_hash = as_string(
            receipt.get("self_identity_hash"),
            "self identity attestation hash must be present",
        )
        self_attestations = trusted_section("self_identity")
        self_attestation = as_object(
            self_attestations.get(self_identity_hash),
            "self identity hash must resolve to trusted runtime evidence",
        )
        require(
            self_attestation.get("model") == self_model
            and self_attestation.get("source") == "runtime-attested",
            "self identity attestation must match the active model",
        )
        resolved_models.add(self_model)
    else:
        require(
            receipt.get("self_model") is None,
            "self_model must be null when self is not a participant",
        )
        require(
            receipt.get("self_identity_source") is None,
            "self_identity_source must be null when self is absent",
        )
        require(
            receipt.get("self_identity_hash") is None,
            "self_identity_hash must be null when self is absent",
        )

    contains_compound = False
    for handle in participant_list:
        require(handle in models, f"unknown requested participant {handle}")
        model = as_object(models[handle], f"model {handle} must be an object")
        as_string(
            model.get("family"),
            f"model {handle} family must be a string",
        )
        contains_compound = contains_compound or model.get("compound") is True

    require(
        receipt.get("compound") is contains_compound,
        "compound does not match requested participant metadata",
    )

    calls = as_list(receipt.get("calls"), "calls must be an array")
    call_ids: list[str] = []
    completed_handles: set[str] = set()
    for raw_call in calls:
        call = as_object(raw_call, "each call must be an object")
        call_ids.append(
            as_string(call.get("call_id"), "call_id must be a string")
        )
        handle, effective_model = validate_call_registry(
            call,
            models,
            participants,
        )
        routing_hash = as_string(
            call.get("routing_attestation_hash"),
            f"call {handle} routing_attestation_hash must be present",
        )
        routing_attestation = as_object(
            trusted_section("routing").get(routing_hash),
            f"call {handle} routing attestation must be trusted",
        )
        require(
            routing_attestation.get("handle") == handle
            and routing_attestation.get("vendor") == call.get("vendor")
            and routing_attestation.get("effective_model") == call.get("effective_model")
            and routing_attestation.get("fallback_scope") == "same-model-endpoint"
            and routing_attestation.get("zdr") == "policy-required"
            and routing_attestation.get("region") == "policy-required"
            and routing_attestation.get("effective_identity_recheck") is True
            and routing_attestation.get("quorum_recheck_after_fallback") is True,
            f"call {handle} routing attestation does not match policy",
        )
        if call.get("status") == "completed":
            completed_handles.add(handle)
            if effective_model is None:
                raise ContractError(
                    f"completed call {handle} lacks effective model identity"
                )
            resolved_models.add(effective_model)

    require(
        len(call_ids) == len(set(call_ids)),
        "call_id values must be unique",
    )

    if receipt.get("fused") is True:
        required_external = participants - {"self"}
        require(
            completed_handles == required_external,
            "fused receipt requires a completed call for every "
            "external participant",
        )

    expected_cross_model = len(resolved_models) >= 2
    require(
        receipt.get("cross_model") is expected_cross_model,
        "cross_model does not match resolved model identities",
    )

    validate_budget_semantics(receipt, config, calls)
    validate_timestamps(receipt)

def validate_receipt(
    receipt: dict[str, Any],
    schema: dict[str, Any],
    config: dict[str, Any],
) -> None:
    validate_schema_instance(receipt, schema)
    validate_receipt_semantics(receipt, config)


def clone(receipt: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(receipt)


def first_call(receipt: dict[str, Any]) -> dict[str, Any]:
    calls = as_list(receipt.get("calls"), "calls must be an array")
    require(bool(calls), "fixture must contain at least one call")
    return as_object(calls[0], "fixture call must be an object")


def receipt_budgets(receipt: dict[str, Any]) -> dict[str, Any]:
    return as_object(receipt.get("budgets"), "budgets must be an object")


def assert_rejected(
    label: str,
    candidate: dict[str, Any],
    schema: dict[str, Any],
    config: dict[str, Any],
) -> None:
    try:
        validate_receipt(candidate, schema, config)
    except ContractError:
        LOGGER.info("negative contract rejected: %s", label)
        return
    raise ContractError(f"negative contract was accepted: {label}")


def exercise_negative_cases(
    valid: dict[str, Any],
    schema: dict[str, Any],
    config: dict[str, Any],
) -> None:
    candidate = clone(valid)
    candidate["context_complete"] = False
    assert_rejected("fused receipt with incomplete context", candidate, schema, config)

    candidate = clone(valid)
    candidate["topology"] = "advisor"
    assert_rejected("advisor reported as fusion", candidate, schema, config)

    candidate = clone(valid)
    candidate["state"] = "degraded"
    assert_rejected("degraded state reported as fusion", candidate, schema, config)

    candidate = clone(valid)
    first_call(candidate)["effective_model"] = None
    assert_rejected("completed call without effective model", candidate, schema, config)

    candidate = clone(valid)
    first_call(candidate)["effective_model"] = "claude-fable-5"
    assert_rejected("external model identity mismatch", candidate, schema, config)

    candidate = clone(valid)
    candidate["self_model"] = "fabricated-model"
    assert_rejected("fabricated self model identity", candidate, schema, config)

    candidate = clone(valid)
    candidate["self_identity_hash"] = None
    assert_rejected("missing self runtime attestation", candidate, schema, config)

    candidate = clone(valid)
    budgets = receipt_budgets(candidate)
    budgets["used_calls"] = 2
    assert_rejected("call budget exceeded", candidate, schema, config)

    candidate = clone(valid)
    budgets = receipt_budgets(candidate)
    budgets["wallclock_s"] = 901
    assert_rejected("wallclock budget exceeded", candidate, schema, config)

    candidate = clone(valid)
    candidate["finished_at"] = "2026-07-10T23:59:59Z"
    assert_rejected("finished before started", candidate, schema, config)

    candidate = clone(valid)
    budgets = receipt_budgets(candidate)
    budgets["wallclock_s"] = 11
    assert_rejected("wallclock timestamp mismatch", candidate, schema, config)

    candidate = clone(valid)
    first_call(candidate)["cost_usd"] = 100.0
    budgets = receipt_budgets(candidate)
    budgets["max_cost_usd"] = 1.0
    budgets["cost_usd"] = 0.0
    budgets["cost_verified"] = True
    assert_rejected("aggregate call cost underreported", candidate, schema, config)

    candidate = clone(valid)
    first_call(candidate)["cost_usd"] = 2.0
    budgets = receipt_budgets(candidate)
    budgets["max_cost_usd"] = 1.0
    budgets["cost_usd"] = 2.0
    budgets["cost_verified"] = True
    assert_rejected("cost budget exceeded", candidate, schema, config)

    candidate = clone(valid)
    budgets = receipt_budgets(candidate)
    budgets["max_cost_usd"] = 1.0
    assert_rejected("unknown cost with hard budget", candidate, schema, config)

    candidate = clone(valid)
    first_call(candidate)["cost_usd"] = 1.0
    first_call(candidate)["cost_verified"] = False
    budgets = receipt_budgets(candidate)
    budgets["cost_usd"] = 1.0
    budgets["cost_verified"] = True
    assert_rejected("unverified call cost marked as aggregate verified", candidate, schema, config)

    candidate = clone(valid)
    first_call(candidate)["cost_usd"] = 0.0
    first_call(candidate)["cost_verified"] = True
    first_call(candidate)["cost_source"] = "unknown"
    first_call(candidate)["provider_usage_hash"] = None
    budgets = receipt_budgets(candidate)
    budgets["cost_usd"] = 0.0
    budgets["cost_verified"] = True
    assert_rejected("verified zero cost without provider attestation", candidate, schema, config)

    candidate = clone(valid)
    first_call(candidate)["cost_usd"] = 0.0
    first_call(candidate)["cost_verified"] = True
    first_call(candidate)["cost_source"] = "subscription-entitlement"
    first_call(candidate)["provider_usage_hash"] = "9" * 64
    budgets = receipt_budgets(candidate)
    budgets["cost_usd"] = 0.0
    budgets["cost_verified"] = True
    assert_rejected("forged provider usage attestation", candidate, schema, config)

    candidate = clone(valid)
    first_call(candidate)["routing_attestation_hash"] = "9" * 64
    assert_rejected("forged routing attestation", candidate, schema, config)

    candidate = clone(valid)
    candidate["panel"] = "quality"
    assert_rejected("preset panel mismatch", candidate, schema, config)

    candidate = clone(valid)
    candidate["requested_participants"] = ["self", "gpt-sol", "claude-opus"]
    assert_rejected("participants outside resolved panel", candidate, schema, config)

    candidate = clone(valid)
    denied_config = clone(config)
    guardrails = as_object(denied_config.get("guardrails"), "guardrails must be an object")
    guardrails["provider_denylist"] = ["openai"]
    assert_rejected("denied provider participant", candidate, schema, denied_config)

    candidate = clone(valid)
    candidate["requested_participants"] = [
        "self",
        "gpt-sol",
        "claude-opus",
    ]
    assert_rejected(
        "missing external participant call",
        candidate,
        schema,
        config,
    )

    candidate = clone(valid)
    candidate["requested_participants"] = ["self", "claude-opus"]
    candidate["self_model"] = "claude-opus-4-8"
    call = first_call(candidate)
    call["handle"] = "claude-opus"
    call["requested_model"] = "opus"
    call["effective_model"] = "claude-opus-4-8"
    call["vendor"] = "anthropic"
    call["family"] = "claude-opus"
    call["mode"] = "xhigh"
    assert_rejected(
        "same model mislabeled as cross-model",
        candidate,
        schema,
        config,
    )

    candidate = clone(valid)
    calls = as_list(candidate.get("calls"), "calls must be an array")
    calls.append(copy.deepcopy(calls[0]))
    budgets = receipt_budgets(candidate)
    budgets["max_calls"] = 2
    budgets["used_calls"] = 2
    assert_rejected("duplicate call identifiers", candidate, schema, config)

    candidate = clone(valid)
    candidate["requested_participants"] = ["self", "self"]
    assert_rejected("duplicate requested participants", candidate, schema, config)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    schema = load_object(ROOT / "schemas" / "fusion-receipt.schema.json")
    config = load_object(ROOT / ".fusion.example.json")
    valid = load_object(ROOT / "tests" / "fixtures" / "receipt-valid.json")
    try:
        Draft202012Validator.check_schema(schema)
        validate_receipt(valid, schema, config)
        exercise_negative_cases(valid, schema, config)
    except (ContractError, SchemaError) as exc:
        LOGGER.error("%s", exc)
        return 1
    LOGGER.info("receipt contract instances passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())