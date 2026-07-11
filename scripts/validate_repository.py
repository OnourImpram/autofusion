from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("autofusion.validate")

REQUIRED_PATHS = (
    ROOT / ".claude-plugin" / "marketplace.json",
    ROOT / ".fusion.example.json",
    ROOT / ".github" / "workflows" / "validate.yml",
    ROOT / "plugins" / "autofusion" / ".claude-plugin" / "plugin.json",
    ROOT / "plugins" / "autofusion" / "skills" / "fusion" / "SKILL.md",
    ROOT / "plugins" / "autofusion" / "skills" / "fusion-parallel" / "SKILL.md",
    ROOT / "requirements-validation.txt",
    ROOT / "schemas" / "fusion-analysis.schema.json",
    ROOT / "schemas" / "fusion-receipt.schema.json",
    ROOT / "scripts" / "validate_contract_instances.py",
    ROOT / "scripts" / "validate_analysis_instances.py",
    ROOT / "scripts" / "validate_linked_run.py",
    ROOT / "tests" / "fixtures" / "receipt-valid.json",
    ROOT / "tests" / "fixtures" / "analysis-valid.json",
    ROOT / "docs" / "competitive-research.md",
    ROOT / "docs" / "product-differentiators.md",
    ROOT / "docs" / "fusion-topologies.md",
)

REQUIRED_MODELS = {
    "self",
    "gpt-sol",
    "gpt-sol-ultra",
    "claude-opus",
    "claude-fable",
}

REQUIRED_PRESETS = {
    "fast",
    "balanced",
    "high",
    "quality",
    "budget",
    "adaptive",
}

REQUIRED_ANALYSIS_SECTIONS = {
    "consensus",
    "contradictions",
    "partial_coverage",
    "unique_insights",
    "blind_spots",
    "grounding_candidates",
    "grounding_results",
    "decision_impact",
}

SUPPORTED_TOPOLOGIES = {
    "review",
    "adversarial-review",
    "dual-review",
    "panel-rank",
    "advisor",
}

PANEL_ROLE_FIELDS = {
    "drafter": "drafter",
    "reviewers": "reviewer",
    "proposers": "proposer",
    "judge": "judge",
}

SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})"
)


class ValidationError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"invalid JSON at {path.relative_to(ROOT)}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ValidationError(
            f"expected JSON object at {path.relative_to(ROOT)}"
        )
    if not all(isinstance(key, str) for key in value):
        raise ValidationError(
            f"expected string keys at {path.relative_to(ROOT)}"
        )
    return cast(dict[str, Any], value)


def as_object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValidationError(message)
    return cast(dict[str, Any], value)


def as_list(value: Any, message: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(message)
    return value


def as_string(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(message)
    return value


def as_string_list(value: Any, message: str) -> list[str]:
    items = as_list(value, message)
    if not all(isinstance(item, str) and item for item in items):
        raise ValidationError(message)
    return cast(list[str], items)


def as_positive_int(value: Any, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(message)
    return cast(int, value)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_paths() -> None:
    missing = [
        str(path.relative_to(ROOT))
        for path in REQUIRED_PATHS
        if not path.is_file()
    ]
    require(not missing, f"missing required paths: {', '.join(missing)}")


def validate_versions() -> None:
    marketplace = load_json(ROOT / ".claude-plugin" / "marketplace.json")
    plugin = load_json(
        ROOT / "plugins" / "autofusion" / ".claude-plugin" / "plugin.json"
    )
    marketplace_plugins = as_list(
        marketplace.get("plugins"),
        "marketplace plugins must be an array",
    )
    require(
        len(marketplace_plugins) == 1,
        "marketplace must contain exactly one plugin",
    )
    entry = as_object(
        marketplace_plugins[0],
        "marketplace plugin entry must be an object",
    )
    versions = {
        marketplace.get("version"),
        entry.get("version"),
        plugin.get("version"),
    }
    require(len(versions) == 1, "marketplace and plugin versions must match")

    descriptions = (
        marketplace.get("description"),
        entry.get("description"),
        plugin.get("description"),
    )
    require(
        all(
            isinstance(description, str)
            and "skill-only" in description.lower()
            for description in descriptions
        ),
        "marketplace and plugin descriptions must state the skill-only boundary",
    )


def panel_assignments(
    panel_name: str,
    panel: dict[str, Any],
) -> list[tuple[str, str]]:
    assignments: list[tuple[str, str]] = []
    for field, role in PANEL_ROLE_FIELDS.items():
        if field not in panel:
            continue
        value = panel[field]
        if field in {"reviewers", "proposers"}:
            handles = as_string_list(
                value,
                f"panel {panel_name}.{field} must be a string array",
            )
            assignments.extend((handle, role) for handle in handles)
        else:
            assignments.append(
                (
                    as_string(
                        value,
                        f"panel {panel_name}.{field} must be a model handle",
                    ),
                    role,
                )
            )
    return assignments


def validate_panel_shape(
    panel_name: str,
    panel: dict[str, Any],
) -> str:
    topology = as_string(
        panel.get("topology"),
        f"panel {panel_name} must define topology",
    )
    require(
        topology in SUPPORTED_TOPOLOGIES,
        f"panel {panel_name} uses unsupported topology {topology}",
    )
    if topology in {"review", "adversarial-review", "dual-review"}:
        require(
            "drafter" in panel and "reviewers" in panel,
            f"panel {panel_name} must define drafter and reviewers",
        )
    if topology == "panel-rank":
        require(
            "proposers" in panel and "judge" in panel,
            f"panel {panel_name} must define proposers and judge",
        )
    return topology


def validate_config_graph(
    config: dict[str, Any],
    models: dict[str, Any],
    presets: dict[str, Any],
    panels: dict[str, Any],
) -> None:
    for preset_name, raw_preset in presets.items():
        preset = as_object(
            raw_preset,
            f"preset {preset_name} must be an object",
        )
        if "panel" in preset:
            panel_name = as_string(
                preset["panel"],
                f"preset {preset_name}.panel must be a string",
            )
            require(
                panel_name in panels,
                f"preset {preset_name} references unknown panel {panel_name}",
            )
        if "allowed_presets" in preset:
            allowed_presets = as_string_list(
                preset["allowed_presets"],
                f"preset {preset_name}.allowed_presets must be a string array",
            )
            require(
                preset_name not in allowed_presets,
                f"preset {preset_name} cannot route to itself",
            )
            unknown = set(allowed_presets) - set(presets)
            require(
                not unknown,
                f"preset {preset_name} references unknown presets: "
                f"{', '.join(sorted(unknown))}",
            )

    routing = as_object(config.get("routing"), "routing must be an object")
    compound = as_object(
        routing.get("compound"),
        "routing.compound must be an object",
    )
    require(
        compound.get("max_depth") == 1,
        "compound max_depth must remain one",
    )
    require(
        compound.get("default_allow_as_panel_member") is False,
        "compound panel membership must default to deny",
    )
    require(
        compound.get("count_hidden_workers_as_independent") is False,
        "hidden compound workers must not count as independent",
    )

    allowed_handles = as_string_list(
        compound.get("allowed_handles"),
        "routing.compound.allowed_handles must be a string array",
    )
    allowed_roles_raw = as_object(
        compound.get("allowed_roles"),
        "routing.compound.allowed_roles must be an object",
    )
    require(
        set(allowed_roles_raw) <= set(allowed_handles),
        "compound role policy references a handle outside allowed_handles",
    )
    allowed_roles: dict[str, set[str]] = {}
    for handle, raw_roles in allowed_roles_raw.items():
        roles = set(
            as_string_list(
                raw_roles,
                f"compound roles for {handle} must be a string array",
            )
        )
        require(
            roles <= set(PANEL_ROLE_FIELDS.values()),
            f"compound handle {handle} has unsupported roles",
        )
        allowed_roles[handle] = roles

    for handle in allowed_handles:
        require(handle in models, f"compound allowlist has unknown handle {handle}")
        model = as_object(
            models[handle],
            f"model {handle} must be an object",
        )
        require(
            model.get("compound") is True,
            f"compound allowlist handle {handle} is not marked compound",
        )
        require(
            handle in allowed_roles and bool(allowed_roles[handle]),
            f"compound allowlist handle {handle} has no permitted roles",
        )

    guardrails = as_object(
        config.get("guardrails"),
        "guardrails must be an object",
    )
    model_allowlist = set(
        as_string_list(
            guardrails.get("model_allowlist"),
            "guardrails.model_allowlist must be a string array",
        )
    )
    max_panel_size = as_positive_int(
        guardrails.get("max_panel_size"),
        "guardrails.max_panel_size must be a positive integer",
    )

    for panel_name, raw_panel in panels.items():
        panel = as_object(
            raw_panel,
            f"panel {panel_name} must be an object",
        )
        topology = validate_panel_shape(panel_name, panel)
        assignments = panel_assignments(panel_name, panel)
        handles = [handle for handle, _role in assignments]
        require(
            bool(handles),
            f"panel {panel_name} must contain at least one participant",
        )
        require(
            len(handles) == len(set(handles)),
            f"panel {panel_name} repeats a participant across roles",
        )
        require(
            len(handles) <= max_panel_size,
            f"panel {panel_name} exceeds max_panel_size",
        )

        for handle, role in assignments:
            require(
                handle in models,
                f"panel {panel_name} references unknown model {handle}",
            )
            model = as_object(
                models[handle],
                f"model {handle} must be an object",
            )
            if handle == "self":
                require(
                    role == "drafter"
                    and topology
                    in {"review", "adversarial-review", "dual-review"},
                    f"panel {panel_name} uses self in external role {role}",
                )
            else:
                require(
                    model.get("callable") is True,
                    f"panel {panel_name} dispatches noncallable model {handle}",
                )
                require(
                    handle in model_allowlist,
                    f"panel {panel_name} uses non-allowlisted model {handle}",
                )
            if model.get("compound") is True:
                require(
                    handle in allowed_handles,
                    f"panel {panel_name} uses compound model {handle} "
                    "without an explicit handle allowlist",
                )
                require(
                    role in allowed_roles.get(handle, set()),
                    f"panel {panel_name} uses compound model {handle} "
                    f"in disallowed role {role}",
                )

    hard_gates = as_object(
        routing.get("hard_gates"),
        "routing.hard_gates must be an object",
    )
    minimum_preset = as_string(
        hard_gates.get("minimum_preset"),
        "routing.hard_gates.minimum_preset must be a string",
    )
    require(
        minimum_preset in presets and minimum_preset != "adaptive",
        "hard gate minimum preset must resolve to a concrete preset",
    )


def validate_extended_orchestration(config: dict[str, Any]) -> None:
    orchestration = as_object(
        config.get("orchestration"),
        "orchestration must be an object",
    )
    require(
        orchestration.get("max_workflow_steps") == 5,
        "max_workflow_steps must remain bounded at five",
    )
    require(
        orchestration.get("per_step_routing") is True,
        "per-step routing contract must remain enabled",
    )
    require(
        orchestration.get("communication_graph") == "explicit-access-list",
        "agent communication must use explicit access lists",
    )
    require(
        orchestration.get("intra_workflow_tool_trace_isolation") is True,
        "agent tool traces must remain isolated within a workflow",
    )
    require(
        orchestration.get("inter_workflow_memory")
        == "hash-addressed-approved-only",
        "shared memory must be hash-addressed and approved",
    )
    require(
        orchestration.get("function_call_owner_required") is True,
        "every function call must retain its agent owner",
    )
    require(
        orchestration.get("marginal_value_stop") == "shadow",
        "marginal-value stopping must remain shadow-only in alpha",
    )

    provider = as_object(
        config.get("provider_routing"),
        "provider_routing must be an object",
    )
    require(
        provider.get("allow_fallbacks") is True
        and provider.get("fallback_scope") == "same-model-endpoint",
        "fallbacks must remain limited to same-model endpoints by default",
    )
    require(
        provider.get("model_fallback_requires_explicit_policy") is True,
        "model fallback must require explicit policy",
    )
    require(
        set(
            as_string_list(
                provider.get("fallback_on"),
                "provider fallback_on must be a string array",
            )
        )
        == {"rate-limit", "provider-unavailable", "timeout"},
        "provider fallback triggers changed unexpectedly",
    )
    require(
        set(
            as_string_list(
                provider.get("never_fallback_on"),
                "provider never_fallback_on must be a string array",
            )
        )
        == {"policy-blocked", "moderation-blocked", "identity-unverified"},
        "provider fallback deny reasons changed unexpectedly",
    )
    require(
        provider.get("require_parameters") is True,
        "provider routing must require requested parameters",
    )
    require(
        provider.get("effective_identity_recheck") is True
        and provider.get("quorum_recheck_after_fallback") is True,
        "fallback must recheck effective identity and quorum",
    )
    require(
        provider.get("data_collection") == "deny"
        and provider.get("zdr") == "policy-required"
        and provider.get("region") == "policy-required",
        "provider routing must preserve data and region policy",
    )
    thresholds = as_object(
        provider.get("performance_thresholds"),
        "provider performance_thresholds must be an object",
    )
    require(
        thresholds.get("latency_percentile") == "p90"
        and thresholds.get("throughput_percentile") == "p90",
        "provider performance thresholds must use p90 evidence",
    )
    stickiness = as_object(
        provider.get("session_stickiness"),
        "provider session_stickiness must be an object",
    )
    require(
        stickiness.get("within_run") is True
        and stickiness.get("across_runs") is False,
        "session stickiness must not create cross-run correlation",
    )

    repair = as_object(
        config.get("output_repair"),
        "output_repair must be an object",
    )
    require(
        repair.get("max_attempts") == 1
        and repair.get("syntax_only") is True,
        "output repair must be bounded and syntax-only",
    )
    require(
        repair.get("record_original_hash") is True
        and repair.get("record_repaired_hash") is True
        and repair.get("fail_on_semantic_delta") is True,
        "output repair must preserve hashes and fail on semantic delta",
    )
    require(
        set(
            as_string_list(
                repair.get("protected_fields"),
                "output_repair.protected_fields must be a string array",
            )
        )
        == {
            "agreement_strength",
            "decision_impact",
            "finding_status",
            "severity",
            "source_ids",
            "verification_id",
        },
        "output repair protected fields changed unexpectedly",
    )

def validate_config() -> None:
    config = load_json(ROOT / ".fusion.example.json")
    models = as_object(config.get("models"), "models must be an object")
    presets = as_object(config.get("presets"), "presets must be an object")
    panels = as_object(config.get("panels"), "panels must be an object")
    require(
        REQUIRED_MODELS <= set(models),
        "required built-in model handles are missing",
    )
    require(
        REQUIRED_PRESETS <= set(presets),
        "required presets are missing",
    )

    self_model = as_object(models.get("self"), "self model must be an object")
    gpt_sol = as_object(models.get("gpt-sol"), "gpt-sol must be an object")
    gpt_ultra = as_object(
        models.get("gpt-sol-ultra"),
        "gpt-sol-ultra must be an object",
    )
    claude_opus = as_object(
        models.get("claude-opus"),
        "claude-opus must be an object",
    )
    claude_fable = as_object(
        models.get("claude-fable"),
        "claude-fable must be an object",
    )

    require(self_model.get("callable") is False, "self must be non-callable")
    require(
        self_model.get("identity_source") == "runtime-attested",
        "self identity must come from runtime attestation",
    )
    require(
        set(
            as_string_list(
                self_model.get("allowed_models"),
                "self.allowed_models must be a string array",
            )
        )
        == {"claude-opus-4-8", "claude-fable-5"},
        "self allowed model identities must remain explicit",
    )
    require(
        gpt_sol.get("model") == "gpt-5.6-sol"
        and gpt_sol.get("effort") == "xhigh"
        and gpt_sol.get("compound") is False
        and gpt_sol.get("callable") is True,
        "gpt-sol must map to non-compound gpt-5.6-sol xhigh",
    )
    require(
        gpt_ultra.get("model") == "gpt-5.6-sol"
        and gpt_ultra.get("effort") == "ultra"
        and gpt_ultra.get("compound") is True
        and gpt_ultra.get("worker_visibility") == "opaque"
        and gpt_ultra.get("callable") is True,
        "gpt-sol-ultra must map to opaque compound gpt-5.6-sol ultra",
    )
    require(
        claude_opus.get("model") == "opus"
        and claude_opus.get("canonical_model") == "claude-opus-4-8"
        and claude_opus.get("callable") is True,
        "claude-opus mapping mismatch",
    )
    require(
        claude_fable.get("model") == "fable"
        and claude_fable.get("canonical_model") == "claude-fable-5"
        and claude_fable.get("callable") is True,
        "claude-fable mapping mismatch",
    )
    require("gpt-5.5" not in models, "obsolete gpt-5.5 handle must not return")
    require(
        "opus-4.7" not in models,
        "obsolete opus-4.7 handle must not return",
    )

    validate_config_graph(config, models, presets, panels)
    validate_extended_orchestration(config)

    analysis = as_object(config.get("analysis"), "analysis must be an object")
    required_sections = as_string_list(
        analysis.get("required_sections"),
        "analysis sections must be a string array",
    )
    require(
        REQUIRED_ANALYSIS_SECTIONS == set(required_sections),
        "analysis section contract changed unexpectedly",
    )
    require(
        analysis.get("agreement_is_confidence") is False,
        "agreement must not be treated as confidence",
    )


def validate_config_candidate(config: dict[str, Any]) -> None:
    models = as_object(config.get("models"), "models must be an object")
    presets = as_object(config.get("presets"), "presets must be an object")
    panels = as_object(config.get("panels"), "panels must be an object")
    validate_config_graph(config, models, presets, panels)
    validate_extended_orchestration(config)


def require_config_rejected(
    label: str,
    candidate: dict[str, Any],
) -> None:
    try:
        validate_config_candidate(candidate)
    except ValidationError:
        LOGGER.info("negative config rejected: %s", label)
        return
    raise ValidationError(f"negative config was accepted: {label}")


def validate_negative_config_guards() -> None:
    valid = load_json(ROOT / ".fusion.example.json")

    candidate = copy.deepcopy(valid)
    panels = as_object(candidate.get("panels"), "panels must be an object")
    council = as_object(
        panels.get("external-council"),
        "external-council panel must be an object",
    )
    proposers = as_list(
        council.get("proposers"),
        "external-council proposers must be an array",
    )
    proposers[0] = "self"
    require_config_rejected("self used as external proposer", candidate)

    candidate = copy.deepcopy(valid)
    panels = as_object(candidate.get("panels"), "panels must be an object")
    council = as_object(
        panels.get("external-council"),
        "external-council panel must be an object",
    )
    proposers = as_list(
        council.get("proposers"),
        "external-council proposers must be an array",
    )
    proposers[0] = "gpt-sol-ultra"
    council["judge"] = "gpt-sol"
    require_config_rejected("compound model used as proposer", candidate)

    candidate = copy.deepcopy(valid)
    provider = as_object(
        candidate.get("provider_routing"),
        "provider_routing must be an object",
    )
    provider["fallback_scope"] = "any-model"
    require_config_rejected("unbounded model fallback", candidate)

    candidate = copy.deepcopy(valid)
    repair = as_object(
        candidate.get("output_repair"),
        "output_repair must be an object",
    )
    protected = as_list(
        repair.get("protected_fields"),
        "output repair protected_fields must be an array",
    )
    protected.remove("severity")
    require_config_rejected("unprotected severity repair", candidate)

def validate_schemas() -> None:
    for name in ("fusion-analysis.schema.json", "fusion-receipt.schema.json"):
        schema = load_json(ROOT / "schemas" / name)
        require(
            schema.get("$schema")
            == "https://json-schema.org/draft/2020-12/schema",
            f"{name} must use JSON Schema draft 2020-12",
        )
        require(
            schema.get("additionalProperties") is False,
            f"{name} root must reject unknown properties",
        )

    receipt = load_json(ROOT / "schemas" / "fusion-receipt.schema.json")
    receipt_required = set(
        as_string_list(
            receipt.get("required"),
            "receipt schema required must be a string array",
        )
    )
    required_receipt_fields = {
        "finished_at",
        "consulted",
        "self_model",
        "self_identity_source",
        "self_identity_hash",
        "packet_hash",
        "analysis_hash",
        "panel",
        "findings",
        "privacy",
    }
    require(
        required_receipt_fields <= receipt_required,
        "receipt schema is missing required evidence fields",
    )
    receipt_defs = as_object(
        receipt.get("$defs"),
        "receipt schema must define reusable types",
    )
    call_schema = as_object(
        receipt_defs.get("call"),
        "receipt schema must define call",
    )
    call_properties = as_object(
        call_schema.get("properties"),
        "call schema properties are missing",
    )
    effective_model = as_object(
        call_properties.get("effective_model"),
        "effective_model schema is missing",
    )
    require(
        "anyOf" in effective_model,
        "effective_model must permit honest unresolved calls",
    )
    call_required = set(
        as_string_list(
            call_schema.get("required"),
            "call schema required must be a string array",
        )
    )
    require(
        "cost_verified" in call_required,
        "call schema must require per-call cost verification",
    )
    require(
        isinstance(receipt.get("allOf"), list),
        "receipt schema must enforce state-dependent fusion invariants",
    )


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    require(
        len(lines) >= 3 and lines[0] == "---",
        f"{path.relative_to(ROOT)} lacks frontmatter",
    )
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValidationError(
            f"{path.relative_to(ROOT)} has unterminated frontmatter"
        ) from exc

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        require(
            ":" in line,
            f"{path.relative_to(ROOT)} has malformed frontmatter",
        )
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        require(
            bool(key) and bool(value) and key not in metadata,
            f"{path.relative_to(ROOT)} has invalid frontmatter fields",
        )
        metadata[key] = value
    return metadata


def validate_skills() -> None:
    expected = {
        "fusion": (
            ROOT
            / "plugins"
            / "autofusion"
            / "skills"
            / "fusion"
            / "SKILL.md"
        ),
        "fusion-parallel": (
            ROOT
            / "plugins"
            / "autofusion"
            / "skills"
            / "fusion-parallel"
            / "SKILL.md"
        ),
    }
    for name, path in expected.items():
        metadata = parse_frontmatter(path)
        require(
            metadata.get("name") == name,
            f"{path.relative_to(ROOT)} name mismatch",
        )
        require(
            len(metadata.get("description", "")) >= 20,
            f"{path.relative_to(ROOT)} requires a substantive description",
        )


def validate_claim_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_lower = readme.lower()
    require(
        "pre-engine alpha" in readme_lower,
        "README must state the implementation boundary",
    )
    require(
        "does not claim those components exist" in readme_lower,
        "README must preserve the truthful capability boundary",
    )
    require(
        "override a preset only inside immutable global policy" in readme_lower,
        "README must keep explicit options inside immutable policy",
    )
    require(
        "always override a preset" not in readme_lower,
        "README must not let explicit settings bypass global policy",
    )

    research = (
        ROOT / "docs" / "competitive-research.md"
    ).read_text(encoding="utf-8")
    for source in (
        "https://sakana.ai/fugu-release/",
        "https://arxiv.org/abs/2606.21228",
        "https://openrouter.ai/docs/guides/features/plugins/fusion",
    ):
        require(
            source in research,
            f"competitive research is missing source {source}",
        )


def validate_repository_hygiene() -> None:
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.name == "LICENSE":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        require(
            not SECRET_PATTERN.search(text),
            f"possible credential in {path.relative_to(ROOT)}",
        )
        for marker in ("TO" + "DO", "FIX" + "ME"):
            require(
                marker not in text,
                f"{marker} marker in {path.relative_to(ROOT)}",
            )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    checks = (
        validate_paths,
        validate_versions,
        validate_config,
        validate_negative_config_guards,
        validate_schemas,
        validate_skills,
        validate_claim_boundaries,
        validate_repository_hygiene,
    )
    try:
        for check in checks:
            check()
    except ValidationError as exc:
        LOGGER.error("%s", exc)
        return 1
    LOGGER.info("repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())