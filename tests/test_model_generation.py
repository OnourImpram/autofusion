"""The built-in profiles name the current model generation, and handles name families only."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from autofusion.config import FusionConfig, validate_config
from autofusion.model_naming import carries_version, family_name, naming_violations

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "src" / "autofusion" / "default_config.json"
EXAMPLE_CONFIG = ROOT / ".fusion.example.json"

NOT_RUN = "NOT_RUN: portable defaults require main-session smoke verification"
SHARED_QUOTA_SOURCE = (
    "operator-stated shared ChatGPT quota; four ids answered on one account, 2026-11-15"
)
OPENAI_QUOTA_MODELS = ("gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "gpt-6-astra")


class Row(NamedTuple):
    model: str
    canonical: str
    family: str
    transport: str
    effort: str
    compound: bool
    enabled: bool
    name: str


TABLE: dict[str, Row] = {
    "gpt-sol": Row(
        "gpt-6.1-sol", "gpt-6.1-sol", "gpt-6.1", "codex-exec", "xhigh", False, True, "sol"
    ),
    "gpt-sol-ultra": Row(
        "gpt-6.1-sol", "gpt-6.1-sol", "gpt-6.1", "codex-exec", "ultra", True, True, "sol"
    ),
    "gpt-terra": Row(
        "gpt-6.1-terra", "gpt-6.1-terra", "gpt-6.1", "codex-exec", "xhigh", False, False, "terra"
    ),
    "gpt-luna": Row(
        "gpt-6-luna", "gpt-6-luna", "gpt-6", "codex-exec", "xhigh", False, False, "luna"
    ),
    "astra-ultra": Row(
        "gpt-6-astra", "gpt-6-astra", "gpt-6", "codex-exec", "ultra", True, True, "astra"
    ),
    "claude-opus": Row(
        "opus", "claude-opus-5-5", "claude-opus", "claude-exec", "xhigh", False, True, "opus"
    ),
    "gemini-flash": Row(
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-high",
        "gemini",
        "agy-headless",
        "high",
        False,
        False,
        "flash",
    ),
    "gemini-argon": Row(
        "gemini-4-argon-high",
        "gemini-4-argon-high",
        "gemini",
        "agy-headless",
        "high",
        False,
        False,
        "argon",
    ),
    "gemini-carbon": Row(
        "gemini-4-carbon-high",
        "gemini-4-carbon-high",
        "gemini",
        "agy-headless",
        "high",
        False,
        False,
        "carbon",
    ),
    "gemini-hydrogen": Row(
        "gemini-4-hydrogen-xhigh",
        "gemini-4-hydrogen-xhigh",
        "gemini",
        "agy-headless",
        "xhigh",
        False,
        False,
        "hydrogen",
    ),
    "grok": Row("grok-4.7", "grok-4.7", "grok", "acp", "xhigh", False, False, "grok"),
}
NEW_HANDLES = ("gpt-terra", "gpt-luna", "gemini-argon", "gemini-carbon", "gemini-hydrogen")
DISABLED = tuple(handle for handle, row in TABLE.items() if not row.enabled)
CARRIED_WINDOW = {
    "gpt-sol": ("gpt-5.6-sol", 1050000),
    "gpt-sol-ultra": ("gpt-5.6-sol", 1050000),
    "gpt-terra": ("gpt-5.6-sol", 1050000),
    "gpt-luna": ("gpt-5.6-sol", 1050000),
    "claude-opus": ("claude-opus-5", 1000000),
}


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _models(path: Path) -> dict[str, Any]:
    models = _load(path)["models"]
    assert isinstance(models, dict)
    return models


@pytest.fixture(scope="module")
def default_config() -> FusionConfig:
    return FusionConfig(data=_load(DEFAULT_CONFIG), source_paths=())


@pytest.mark.parametrize("path", [DEFAULT_CONFIG, EXAMPLE_CONFIG], ids=["default", "example"])
def test_shipped_handles_carry_no_version(path: Path) -> None:
    carrying = [handle for handle in _models(path) if carries_version(handle)]
    assert carrying == []
    assert naming_violations(_models(path)) == []


@pytest.mark.parametrize("handle", sorted(TABLE))
def test_profile_resolves_to_the_current_generation(
    default_config: FusionConfig, handle: str
) -> None:
    row = TABLE[handle]
    assert handle in default_config.section("models"), f"{handle} is not shipped"
    profile = default_config.model(handle)
    assert (profile.model, profile.canonical_model) == (row.model, row.canonical)
    assert (profile.family, profile.transport, profile.effort) == (
        row.family,
        row.transport,
        row.effort,
    )
    assert (profile.compound, profile.enabled) == (row.compound, row.enabled)
    assert family_name(_models(DEFAULT_CONFIG)[handle]) == row.name


def test_every_table_handle_is_shipped_and_the_config_is_valid(
    default_config: FusionConfig,
) -> None:
    assert set(TABLE) <= set(default_config.section("models"))
    validate_config(default_config)
    validate_config(FusionConfig(data=_load(EXAMPLE_CONFIG), source_paths=()))


@pytest.mark.parametrize("handle", DISABLED)
def test_disabled_profiles_name_an_activation_gate(handle: str) -> None:
    raw = _models(DEFAULT_CONFIG)[handle]
    assert raw["enabled"] is False
    gate = raw.get("activation_gate")
    assert isinstance(gate, str)
    assert gate.startswith("main-session-verified-")


@pytest.mark.parametrize("handle", ["gemini-argon", "gemini-carbon", "gemini-hydrogen"])
def test_new_gemini_profiles_have_the_shape_and_gate_of_gemini_flash(handle: str) -> None:
    models = _models(DEFAULT_CONFIG)
    flash, profile = models["gemini-flash"], models[handle]
    assert profile["activation_gate"] == flash["activation_gate"]
    assert profile["capabilities"] == flash["capabilities"]
    for key in ("transport", "callable", "vendor", "family", "context", "compound"):
        assert profile[key] == flash[key]


@pytest.mark.parametrize("handle", ["gpt-terra", "gpt-luna"])
def test_new_openai_profiles_have_the_shape_of_gpt_sol(handle: str) -> None:
    models = _models(DEFAULT_CONFIG)
    sol, profile = models["gpt-sol"], models[handle]
    for key in ("transport", "callable", "vendor", "effort", "context", "compound", "params"):
        assert profile[key] == sol[key]
    assert profile["quota_group"] == sol["quota_group"] == "openai-chatgpt"
    assert set(profile["capabilities"]) == set(sol["capabilities"]) | {"verification"}


@pytest.mark.parametrize("handle", NEW_HANDLES)
def test_new_profiles_state_that_no_smoke_has_run(handle: str) -> None:
    assert _models(DEFAULT_CONFIG)[handle]["capabilities"]["verification"] == NOT_RUN


@pytest.mark.parametrize("handle", sorted(CARRIED_WINDOW))
def test_carried_context_windows_say_so_and_are_not_verified(handle: str) -> None:
    origin, tokens = CARRIED_WINDOW[handle]
    raw = _models(DEFAULT_CONFIG)[handle]
    canonical = TABLE[handle].canonical
    capabilities = raw["capabilities"]
    assert capabilities["context_window_tokens"] == tokens
    source = capabilities["context_window_source"]
    assert source == f"carried from {origin} on 2026-11-15; NOT_VERIFIED for {canonical}"
    assert "http" not in source


@pytest.mark.parametrize("path", [DEFAULT_CONFIG, EXAMPLE_CONFIG], ids=["default", "example"])
def test_one_quota_group_holds_the_four_openai_ids(path: Path) -> None:
    data = _load(path)
    group = data["quota_groups"]["openai-chatgpt"]
    assert tuple(group["models"]) == OPENAI_QUOTA_MODELS
    assert group["source"] == SHARED_QUOTA_SOURCE
    assert group["shared_exhaustion"] is True
    config = FusionConfig(data=data, source_paths=())
    for handle in ("gpt-sol", "gpt-sol-ultra", "gpt-terra", "gpt-luna", "astra-ultra"):
        if handle in data["models"]:
            assert config.model(handle).quota_group == "openai-chatgpt"


def test_example_agrees_with_the_default_for_every_shared_handle() -> None:
    default, example = _models(DEFAULT_CONFIG), _models(EXAMPLE_CONFIG)
    shared = [handle for handle in example if handle in default]
    assert {"gpt-sol", "gpt-sol-ultra", "claude-opus", "gemini-flash", "grok"} <= set(shared)
    for handle in shared:
        assert example[handle] == default[handle], handle


def test_self_admits_the_current_session_identities() -> None:
    expected = {
        "claude-opus-5-5": "claude-opus",
        "claude-sonnet-5-5": "claude-sonnet",
        "claude-haiku-5-5": "claude-haiku",
        "claude-fable-5-2": "claude-fable",
        "claude-mythos-5-2": "claude-mythos",
    }
    for path in (DEFAULT_CONFIG, EXAMPLE_CONFIG):
        raw = _models(path)["self"]
        allowed = set(raw["allowed_models"])
        assert set(expected) <= allowed
        assert {"claude-opus-5", "claude-sonnet-5", "claude-fable-5-1"} <= allowed
        for model, family in expected.items():
            assert raw["identities"][model] == {"vendor": "anthropic", "family": family}


# Negative arms: fixture configurations built here, never the shipped file.


def _sol(**changes: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "transport": "codex-exec",
        "model": "gpt-6.1-sol",
        "vendor": "openai",
        "family": "gpt-6.1",
        "effort": "xhigh",
        "compound": False,
    }
    profile.update(changes)
    return profile


@pytest.mark.parametrize("handle", ["gpt-sol-61", "gpt-6-sol", "sol2", "gpt-6.1-sol"])
def test_negative_a_version_in_a_handle_is_reported(handle: str) -> None:
    violations = naming_violations({handle: _sol()})
    assert len(violations) == 1
    assert handle in violations[0] and "version" in violations[0]


def test_negative_a_second_handle_of_the_same_family_effort_and_compound_is_reported() -> None:
    violations = naming_violations({"gpt-sol": _sol(), "gpt-sol-again": _sol()})
    assert len(violations) == 1
    assert "gpt-sol" in violations[0] and "gpt-sol-again" in violations[0]
    assert "sol" in violations[0]


def test_negative_the_duplicate_is_found_through_the_canonical_identity() -> None:
    alias = {"transport": "claude-exec", "model": "opus", "canonical_model": "claude-opus-5-5"}
    other = {"transport": "claude-exec", "model": "claude-opus-5-5"}
    violations = naming_violations({"claude-opus": alias, "claude-opus-twin": other})
    assert len(violations) == 1 and "opus" in violations[0]


def test_positive_control_different_effort_or_compound_is_a_different_handle() -> None:
    models = {
        "gpt-sol": _sol(),
        "gpt-sol-ultra": _sol(effort="ultra", compound=True),
        "gpt-sol-high": _sol(effort="high"),
        "gpt-sol-agent": _sol(compound=True),
        "gpt-terra": _sol(model="gpt-6.1-terra"),
        "self": {"transport": "self", "callable": False, "family": "active-claude-session"},
    }
    assert naming_violations(models) == []


def test_negative_arms_fire_independently_on_one_configuration() -> None:
    models = {"gpt-sol-61": _sol(), "gpt-sol": _sol(), "gpt-sol-again": copy.deepcopy(_sol())}
    violations = naming_violations(models)
    assert len(violations) == 3
    assert sum("version" in message for message in violations) == 1
    assert sum("both name family" in message for message in violations) == 2


@pytest.mark.parametrize(
    ("profile", "name"),
    [
        ({"model": "gpt-6.1-sol"}, "sol"),
        ({"model": "gemini-4-argon-high", "effort": "high"}, "argon"),
        ({"model": "gemini-4-hydrogen-xhigh", "effort": "xhigh"}, "hydrogen"),
        ({"model": "opus", "canonical_model": "claude-opus-5-5"}, "opus"),
        ({"model": "claude-haiku-4-5-20251001"}, "haiku"),
        ({"model": "chatgpt-web-high", "effort": "high"}, "web"),
        ({"model": "openrouter/fusion"}, "fusion"),
        ({"model": "gemini-4-high", "effort": "high"}, "gemini"),
        ({}, None),
        ({"model": "6.1"}, None),
    ],
)
def test_family_name_drops_versions_and_the_effort_suffix(
    profile: dict[str, Any], name: str | None
) -> None:
    assert family_name(profile) == name
