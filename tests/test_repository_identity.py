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
