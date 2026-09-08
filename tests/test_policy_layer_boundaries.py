from __future__ import annotations

import pytest

from autofusion.config import FusionConfig, load_config, merge_layers, validate_participant
from autofusion.errors import ConfigurationError, PolicyError
from autofusion.policy import resolve_route


@pytest.mark.parametrize("preset", ["balanced", "high", "quality"])
def test_builtin_minimum_cannot_be_redefined_as_weaker_alias(preset: str) -> None:
    with pytest.raises(ConfigurationError, match=r"minimum|weaken"):
        load_config(overrides={"presets": {preset: {"alias_for": "fast"}}})


def test_unknown_pack_minimum_strength_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="minimum"):
        load_config(
            overrides={
                "presets": {"custom-strength": {"panel": "quality"}},
                "packs": {"release": {"minimum_preset": "custom-strength"}},
            }
        )


@pytest.mark.parametrize("key", ["model_allowlist", "provider_denylist"])
@pytest.mark.parametrize("value", ["gpt-sol gpt-sol-ultra claude-opus untrusted", {}])
def test_malformed_guardrail_lists_are_rejected(key: str, value: object) -> None:
    with pytest.raises(ConfigurationError, match="list"):
        load_config(overrides={"guardrails": {key: value}})


def test_direct_config_cannot_use_string_allowlist_membership() -> None:
    data = load_config().data
    data["guardrails"]["model_allowlist"] = "gpt-sol claude-opus"
    with pytest.raises(ConfigurationError, match="list"):
        validate_participant(FusionConfig(data, ()), "gpt-sol", "reviewer")


def test_later_alias_change_cannot_reinterpret_an_inherited_hard_minimum() -> None:
    global_layer = merge_layers(
        load_config().data,
        {
            "presets": {"global-strict": {"alias_for": "high"}},
            "routing": {"hard_gates": {"minimum_preset": "global-strict"}},
        },
    )
    local_layer = merge_layers(
        global_layer,
        {
            "presets": {"global-strict": {"alias_for": "fast"}},
        },
    )
    with pytest.raises(PolicyError, match="minimum"):
        resolve_route(
            FusionConfig(local_layer, ()),
            requested_preset="fast",
            artifact_paths=("migrations/001.sql",),
            external_only=False,
            explicit_panel="dual-opus",
        )
