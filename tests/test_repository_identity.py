from __future__ import annotations

import copy
import importlib
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest

repository = importlib.import_module("scripts.validate_repository")


def test_repository_current_identity_contract() -> None:
    repository.validate_config()


VERSIONED_FILES = (
    "pyproject.toml",
    "src/autofusion/__init__.py",
    ".claude-plugin/marketplace.json",
    "plugins/autofusion/.claude-plugin/plugin.json",
)


@pytest.mark.parametrize("drifted", VERSIONED_FILES)
def test_repository_versions_must_agree_in_every_manifest(
    drifted: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in VERSIONED_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository.ROOT / relative, target)
    monkeypatch.setattr(repository, "ROOT", tmp_path)
    repository.validate_versions()  # positive arm: the copies agree

    manifest = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    current = manifest["project"]["version"]
    path = tmp_path / drifted
    text = path.read_text(encoding="utf-8")
    assert f'"{current}"' in text
    path.write_text(text.replace(f'"{current}"', f'"{current}.post1"'), encoding="utf-8")
    with pytest.raises(repository.ValidationError, match="versions must match"):
        repository.validate_versions()


@pytest.mark.parametrize("field", ["model", "canonical_model", "family", "aliases"])
def test_repository_rejects_fable_under_unrelated_handle(field: str) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    profile: dict[str, Any] = copy.deepcopy(candidate["models"]["claude-opus"])
    profile[field] = ["claude-fable-5-1"] if field == "aliases" else "claude-fable-5-1"
    profile["enabled"] = False
    candidate["models"]["legacy-reviewer"] = profile

    with pytest.raises(repository.ValidationError, match=r"migration required.*claude-opus"):
        repository.validate_config_candidate(candidate)


@pytest.mark.parametrize("field", ["model", "canonical_model", "family", "aliases"])
def test_repository_rejects_mythos_under_unrelated_handle(field: str) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    profile: dict[str, Any] = copy.deepcopy(candidate["models"]["claude-opus"])
    profile[field] = ["claude-mythos-5-2"] if field == "aliases" else "claude-mythos-5-2"
    profile["enabled"] = False
    candidate["models"]["legacy-reviewer"] = profile

    expected = r"migration required.*Mythos.*claude-opus"
    with pytest.raises(repository.ValidationError, match=expected):
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
        ("model", "gpt-6.1-sol"),
        ("canonical_model", "gpt-6.1-sol"),
        ("vendor", "other"),
        ("family", "gpt-6.1"),
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
        ("models", ["gpt-6.1-sol", "gpt-6-astra"]),
        ("models", ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"]),
        ("models", ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "gpt-6-astra", "unknown"]),
        ("shared_exhaustion", False),
    ],
)
def test_repository_requires_shared_quota_contract(field: str, value: object) -> None:
    candidate = repository.load_json(repository.ROOT / ".fusion.example.json")
    candidate["quota_groups"]["openai-chatgpt"][field] = value

    with pytest.raises(repository.ValidationError, match="quota"):
        repository.validate_config_candidate(candidate)
