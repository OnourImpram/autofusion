from __future__ import annotations

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
    ROOT / "plugins" / "autofusion" / ".claude-plugin" / "plugin.json",
    ROOT / "plugins" / "autofusion" / "skills" / "fusion" / "SKILL.md",
    ROOT / "plugins" / "autofusion" / "skills" / "fusion-parallel" / "SKILL.md",
    ROOT / "schemas" / "fusion-analysis.schema.json",
    ROOT / "schemas" / "fusion-receipt.schema.json",
    ROOT / "docs" / "competitive-research.md",
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
    "decision_impact",
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
        raise ValidationError(f"invalid JSON at {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"expected JSON object at {path.relative_to(ROOT)}")
    if not all(isinstance(key, str) for key in value):
        raise ValidationError(f"expected string keys at {path.relative_to(ROOT)}")
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


def as_string_list(value: Any, message: str) -> list[str]:
    items = as_list(value, message)
    if not all(isinstance(item, str) for item in items):
        raise ValidationError(message)
    return cast(list[str], items)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_paths() -> None:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_PATHS if not path.is_file()]
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


def validate_config() -> None:
    config = load_json(ROOT / ".fusion.example.json")
    models = as_object(config.get("models"), "models must be an object")
    presets = as_object(config.get("presets"), "presets must be an object")
    as_object(config.get("panels"), "panels must be an object")
    require(REQUIRED_MODELS <= set(models), "required built-in model handles are missing")
    require(REQUIRED_PRESETS <= set(presets), "required presets are missing")

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
        gpt_sol.get("model") == "gpt-5.6-sol"
        and gpt_sol.get("effort") == "xhigh",
        "gpt-sol must map to gpt-5.6-sol xhigh",
    )
    require(
        gpt_ultra.get("model") == "gpt-5.6-sol"
        and gpt_ultra.get("effort") == "ultra",
        "gpt-sol-ultra must map to gpt-5.6-sol ultra",
    )
    require(
        claude_opus.get("canonical_model") == "claude-opus-4-8",
        "claude-opus canonical model mismatch",
    )
    require(
        claude_fable.get("canonical_model") == "claude-fable-5",
        "claude-fable canonical model mismatch",
    )
    require("gpt-5.5" not in models, "obsolete gpt-5.5 handle must not return")
    require("opus-4.7" not in models, "obsolete opus-4.7 handle must not return")

    routing = as_object(config.get("routing"), "routing must be an object")
    compound = as_object(
        routing.get("compound"),
        "routing.compound must be an object",
    )
    require(compound.get("max_depth") == 1, "compound max_depth must remain one")
    require(
        compound.get("allow_as_panel_member") is False,
        "opaque compound providers must be excluded from default panels",
    )
    require(
        compound.get("count_hidden_workers_as_independent") is False,
        "hidden compound workers must not count as independent",
    )

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


def validate_schemas() -> None:
    for name in ("fusion-analysis.schema.json", "fusion-receipt.schema.json"):
        schema = load_json(ROOT / "schemas" / name)
        require(
            schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
            f"{name} must use JSON Schema draft 2020-12",
        )
        require(
            schema.get("additionalProperties") is False,
            f"{name} root must reject unknown properties",
        )


    receipt = load_json(ROOT / "schemas" / "fusion-receipt.schema.json")
    receipt_required = as_string_list(
        receipt.get("required"),
        "receipt schema required must be a string array",
    )
    require("privacy" in receipt_required, "receipt schema must require privacy mode")
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
    require(
        isinstance(receipt.get("allOf"), list),
        "receipt schema must enforce state-dependent fusion invariants",
    )


def validate_skills() -> None:
    expected = {
        "fusion": ROOT
        / "plugins"
        / "autofusion"
        / "skills"
        / "fusion"
        / "SKILL.md",
        "fusion-parallel": ROOT
        / "plugins"
        / "autofusion"
        / "skills"
        / "fusion-parallel"
        / "SKILL.md",
    }
    for name, path in expected.items():
        text = path.read_text(encoding="utf-8")
        require(text.startswith("---\n"), f"{path.relative_to(ROOT)} lacks frontmatter")
        require(f"name: {name}\n" in text, f"{path.relative_to(ROOT)} name mismatch")


def validate_claim_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    require("pre-engine alpha" in readme, "README must state the implementation boundary")
    require(
        "does not claim those components exist" in readme,
        "README must preserve the truthful capability boundary",
    )
    research = (ROOT / "docs" / "competitive-research.md").read_text(
        encoding="utf-8"
    )
    for source in (
        "https://sakana.ai/fugu-release/",
        "https://arxiv.org/abs/2606.21228",
        "https://openrouter.ai/docs/guides/features/plugins/fusion",
    ):
        require(source in research, f"competitive research is missing source {source}")


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
        require(not SECRET_PATTERN.search(text), f"possible credential in {path.relative_to(ROOT)}")
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