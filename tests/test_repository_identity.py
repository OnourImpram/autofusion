from __future__ import annotations

import copy
import importlib
from typing import Any

import pytest

repository = importlib.import_module("scripts.validate_repository")


def test_repository_current_identity_contract() -> None:
    repository.validate_config()


@pytest.mark.parametrize("field", ["model", "canonical_model", "family", "aliases"])
def test_repository_rejects_fable_under_unrelated_handle(field: str) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    profile: dict[str, Any] = copy.deepcopy(candidate["models"]["claude-opus"])
    profile[field] = ["claude-fable-5-1"] if field == "aliases" else "claude-fable-5-1"
    profile["enabled"] = False
    candidate["models"]["legacy-reviewer"] = profile

    with pytest.raises(repository.ValidationError, match=r"migration required.*claude-opus"):
        repository.validate_config_candidate(candidate)


@pytest.mark.parametrize(
    ("legacy", "replacement"),
    [("dual-fable", "dual-opus"), ("external-council-fable", "external-council")],
)
def test_repository_rejects_legacy_fable_panel(legacy: str, replacement: str) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    candidate["panels"][legacy] = copy.deepcopy(candidate["panels"][replacement])

    with pytest.raises(repository.ValidationError, match=rf"migration required.*{replacement}"):
        repository.validate_config_candidate(candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transport", "openai-compatible"),
        ("model", "gpt-5.6-sol"),
        ("canonical_model", "gpt-5.6-sol"),
        ("vendor", "other"),
        ("family", "gpt-5.6"),
        ("effort", "xhigh"),
        ("compound", False),
        ("worker_visibility", "transparent"),
        ("callable", False),
    ],
)
def test_repository_rejects_corrupt_astra_profile(field: str, value: object) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    candidate["models"]["astra-ultra"][field] = value

    with pytest.raises(repository.ValidationError, match="astra-ultra"):
        repository.validate_config_candidate(candidate)


@pytest.mark.parametrize("roles", [["reviewer"], ["judge"], ["reviewer", "judge", "proposer"]])
def test_repository_requires_explicit_astra_reviewer_and_judge_roles(roles: list[str]) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    candidate["routing"]["compound"]["allowed_roles"]["astra-ultra"] = roles

    with pytest.raises(repository.ValidationError, match="astra-ultra"):
        repository.validate_config_candidate(candidate)


@pytest.mark.parametrize("allowlist", ["compound", "models"])
def test_repository_requires_astra_allowlists(allowlist: str) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    if allowlist == "compound":
        candidate["routing"]["compound"]["allowed_handles"].remove("astra-ultra")
        del candidate["routing"]["compound"]["allowed_roles"]["astra-ultra"]
    else:
        candidate["guardrails"]["model_allowlist"].remove("astra-ultra")

    with pytest.raises(repository.ValidationError, match="astra-ultra"):
        repository.validate_config_candidate(candidate)


@pytest.mark.parametrize("handle", ["gpt-sol", "gpt-sol-ultra", "astra-ultra"])
def test_repository_requires_shared_profile_quota_group(handle: str) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    candidate["models"][handle]["quota_group"] = "independent-budget"

    with pytest.raises(repository.ValidationError, match="quota"):
        repository.validate_config_candidate(candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("models", ["gpt-5.6-sol", "gpt-6-astra"]),
        ("models", ["gpt-5.6-sol", "gpt-6-astra", "gpt-5.6-terra", "unknown"]),
        ("shared_exhaustion", False),
    ],
)
def test_repository_requires_shared_quota_contract(field: str, value: object) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    candidate["quota_groups"]["openai-chatgpt"][field] = value

    with pytest.raises(repository.ValidationError, match="quota"):
        repository.validate_config_candidate(candidate)
