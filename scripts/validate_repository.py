from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

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
    return value


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
    marketplace_plugins = marketplace.get("plugins")
    require(
        isinstance(marketplace_plugins, list) and len(marketplace_plugins) == 1,
        "marketplace must contain exactly one plugin",
    )
    entry = marketplace_plugins[0]
    require(isinstance(entry, dict), "marketplace plugin entry must be an object")
    versions = {
        marketplace.get("version"),
        entry.get("version"),
        plugin.get("version"),
    }
    require(len(versions) == 1, "marketplace and plugin versions must match")


def validate_config() -> None:
    config = load_json(ROOT / ".fusion.example.json")
    models = config.get("models")
    presets = config.get("presets")
    panels = config.get("panels")
    require(isinstance(models, dict), "models must be an object")
    require(isinstance(presets, dict), "presets must be an object")
    require(isinstance(panels, dict), "panels must be an object")
    require(REQUIRED_MODELS <= set(models), "required built-in model handles are missing")
    require(REQUIRED_PRESETS <= set(presets), "required presets are missing")
    require(models["self"].get("callable") is False, "self must be non-callable")
    require(
        models["gpt-sol"].get("model") == "gpt-5.6-sol"
        and models["gpt-sol"].get("effort") == "xhigh",
        "gpt-sol must map to gpt-5.6-sol xhigh",
    )
    require(
        models["gpt-sol-ultra"].get("model") == "gpt-5.6-sol"
        and models["gpt-sol-ultra"].get("effort") == "ultra",
        "gpt-sol-ultra must map to gpt-5.6-sol ultra",
    )
    require(
        models["claude-opus"].get("canonical_model") == "claude-opus-4-8",
        "claude-opus canonical model mismatch",
    )
    require(
        models["claude-fable"].get("canonical_model") == "claude-fable-5",
        "claude-fable canonical model mismatch",
    )
    require("gpt-5.5" not in models, "obsolete gpt-5.5 handle must not return")
    require("opus-4.7" not in models, "obsolete opus-4.7 handle must not return")

    routing = config.get("routing")
    require(isinstance(routing, dict), "routing must be an object")
    compound = routing.get("compound")
    require(isinstance(compound, dict), "routing.compound must be an object")
    require(compound.get("max_depth") == 1, "compound max_depth must remain one")
    require(
        compound.get("allow_as_panel_member") is False,
        "opaque compound providers must be excluded from default panels",
    )
    require(
        compound.get("count_hidden_workers_as_independent") is False,
        "hidden compound workers must not count as independent",
    )

    analysis = config.get("analysis")
    require(isinstance(analysis, dict), "analysis must be an object")
    required_sections = analysis.get("required_sections")
    require(isinstance(required_sections, list), "analysis sections must be an array")
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