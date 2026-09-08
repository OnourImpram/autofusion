from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autofusion.config import FusionConfig, load_config, merge_layers, validate_config
from autofusion.errors import ConfigurationError, PolicyError
from autofusion.policy import resolve_route


def test_builtin_profiles_and_quality_alias() -> None:
    config = load_config()
    assert config.model("self").callable is False
    assert config.model("gpt-sol").canonical_model == "gpt-5.6-sol"
    assert config.model("gpt-sol-ultra").compound is True
    assert config.model("claude-opus").canonical_model == "claude-opus-4-8"
    assert config.model("claude-fable").canonical_model == "claude-fable-5"
    assert config.model("claude-fable").enabled is False
    assert config.preset("quality")[0] == "high"


def test_overlay_cannot_expand_allowlist_or_execution_limits() -> None:
    base = load_config().data
    overlay: dict[str, Any] = {
        "guardrails": {
            "model_allowlist": [
                "gpt-sol",
                "gpt-sol-ultra",
                "claude-opus",
                "claude-fable",
                "untrusted",
            ],
            "provider_denylist": ["openrouter"],
            "max_calls_per_run": 999,
            "max_wallclock_s": 99999,
            "fail_closed": False,
        },
        "routing": {"compound": {"max_depth": 8}},
    }
    merged = merge_layers(base, overlay)
    guardrails = merged["guardrails"]
    assert "untrusted" not in guardrails["model_allowlist"]
    assert "openrouter" in guardrails["provider_denylist"]
    assert guardrails["max_calls_per_run"] == base["guardrails"]["max_calls_per_run"]
    assert guardrails["max_wallclock_s"] == base["guardrails"]["max_wallclock_s"]
    assert guardrails["fail_closed"] is True
    assert merged["routing"]["compound"]["max_depth"] == 1


def test_repo_config_requires_explicit_trust(tmp_path: Path) -> None:
    (tmp_path / ".fusion.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="trust-repo-config"):
        load_config(tmp_path)
    assert load_config(tmp_path, trust_repo_config=True).source_paths == (
        (tmp_path / ".fusion.json").resolve(),
    )


def test_invalid_self_profile_is_rejected() -> None:
    data = json.loads(json.dumps(load_config().data))
    data["models"]["self"]["callable"] = True
    with pytest.raises(ConfigurationError, match="non-callable"):
        validate_config(FusionConfig(data=data, source_paths=()))


def test_adaptive_route_escalates_sensitive_paths() -> None:
    route = resolve_route(
        load_config(),
        requested_preset="adaptive",
        artifact_paths=("src/auth/session.py",),
        external_only=False,
    )
    assert route.selected_preset == "balanced"
    assert route.panel == "dual-opus"
    assert route.hard_gates


def test_explicit_fast_cannot_bypass_hard_gate() -> None:
    route = resolve_route(
        load_config(),
        requested_preset="fast",
        artifact_paths=("migrations/001.sql",),
        external_only=False,
    )
    assert route.selected_preset == "balanced"


def test_external_mode_rejects_self_panel() -> None:
    with pytest.raises(PolicyError, match="contains self"):
        resolve_route(
            load_config(),
            requested_preset="fast",
            artifact_paths=(),
            external_only=True,
        )


def test_parallel_preset_remains_external_under_hard_gate_ranking() -> None:
    route = resolve_route(
        load_config(),
        requested_preset="parallel",
        artifact_paths=("app.py",),
        external_only=True,
    )
    assert route.selected_preset == "parallel"
    assert route.panel == "external-council"
    assert "self" not in route.participants


def test_fable_panel_is_disabled_until_identity_gate_passes() -> None:
    with pytest.raises(PolicyError, match="panel is disabled"):
        resolve_route(
            load_config(),
            requested_preset="parallel",
            artifact_paths=(),
            external_only=True,
            explicit_panel="external-council-fable",
        )


def test_enabled_panel_cannot_reference_disabled_model() -> None:
    data = json.loads(json.dumps(load_config().data))
    data["panels"]["dual-fable"]["enabled"] = True
    with pytest.raises(ConfigurationError, match="uses disabled model"):
        validate_config(FusionConfig(data=data, source_paths=()))


def test_explicit_weak_panel_cannot_bypass_path_minimum() -> None:
    with pytest.raises(PolicyError, match="minimum"):
        resolve_route(load_config(), requested_preset="fast",
                      artifact_paths=("migrations/001.sql",), external_only=False,
                      explicit_panel="default")


def test_relabelled_preset_cannot_bypass_path_minimum() -> None:
    config = load_config(overrides={"presets": {"balanced": {"panel": "default"}}})
    with pytest.raises(PolicyError, match="minimum"):
        resolve_route(config, requested_preset="balanced",
                      artifact_paths=("migrations/001.sql",), external_only=False)


@pytest.mark.parametrize("overlay", [10, None, 2])
def test_cost_cap_cannot_be_relaxed(overlay: object) -> None:
    base = merge_layers(load_config().data, {"guardrails": {"max_cost_usd": 5}})
    merged = merge_layers(base, {"guardrails": {"max_cost_usd": overlay}})
    assert merged["guardrails"]["max_cost_usd"] == (2 if overlay == 2 else 5)


@pytest.mark.parametrize(
    "invalid", ["unlimited", True, -1, float("nan"), float("inf"), -float("inf")]
)
def test_invalid_cost_is_rejected(invalid: object) -> None:
    data = load_config().data
    with pytest.raises(ConfigurationError, match="cost"):
        merge_layers(data, {"guardrails": {"max_cost_usd": invalid}})
    data["guardrails"]["max_cost_usd"] = invalid
    with pytest.raises(ConfigurationError, match="cost"):
        validate_config(FusionConfig(data, ()))
    with pytest.raises(ConfigurationError, match="cost"):
        resolve_route(FusionConfig(data, ()), requested_preset="fast", artifact_paths=(),
                      external_only=False)


@pytest.mark.parametrize("base_action", ["flag", "redact", "block"])
@pytest.mark.parametrize("overlay_action", ["flag", "redact", "block"])
def test_dlp_merge_only_tightens(base_action: str, overlay_action: str) -> None:
    ranks = ["flag", "redact", "block"]
    merged = merge_layers({"guardrails": {"sensitive_data_action": base_action}},
                          {"guardrails": {"sensitive_data_action": overlay_action}})
    assert merged["guardrails"]["sensitive_data_action"] == ranks[
        max(ranks.index(base_action), ranks.index(overlay_action))
    ]


@pytest.mark.parametrize("weakening", ["role", "marker"])
def test_overlay_cannot_weaken_compound_admission(weakening: str) -> None:
    overlay: dict[str, Any] = {
        "panels": {"external-council": {"proposers": ["gpt-sol-ultra", "claude-opus"]}}
    }
    if weakening == "role":
        overlay["routing"] = {"compound": {"allowed_roles": {
            "gpt-sol-ultra": ["reviewer", "judge", "proposer"]
        }}}
    else:
        overlay["models"] = {"gpt-sol-ultra": {"compound": False}}
    with pytest.raises(ConfigurationError, match="compound"):
        load_config(overrides=overlay)


@pytest.mark.parametrize("weakening", ["minimum", "patterns"])
def test_overlay_cannot_weaken_hard_path_gates(weakening: str) -> None:
    gate: dict[str, Any] = (
        {"minimum_preset": "fast"} if weakening == "minimum" else {"patterns": []}
    )
    config = load_config(overrides={"routing": {"hard_gates": gate}})
    with pytest.raises(PolicyError, match="minimum"):
        resolve_route(config, requested_preset="fast", artifact_paths=("migrations/001.sql",),
                      external_only=False, explicit_panel="default")
