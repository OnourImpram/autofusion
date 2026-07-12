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
