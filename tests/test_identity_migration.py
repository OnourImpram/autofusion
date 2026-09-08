from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_engine import _registry, _repo, _review_output

from autofusion.config import FusionConfig, load_config, validate_config
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import ConfigurationError, ProviderError
from autofusion.models import ProviderRequest
from autofusion.providers.cli import ClaudeCliAdapter, CliTransportProvider
from autofusion.providers.http import AnthropicHttpProvider, OpenAICompatibleHttpProvider
from autofusion.registry import ProviderRegistry


def test_current_self_and_reviewer_identities() -> None:
    config = load_config()
    assert config.model("claude-opus").canonical_model == "claude-opus-5"
    assert set(config.data["models"]["self"]["allowed_models"]) == {
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
        "claude-fable-5-1",
    }
    assert "claude-fable" not in config.section("models")


@pytest.mark.parametrize("identity", ["claude-fable-5", "claude-fable-5-1", "fable", "FABLE"])
@pytest.mark.parametrize("field", ["model", "canonical_model", "family", "alias_for"])
def test_callable_fable_alias_overlay_is_rejected(identity: str, field: str) -> None:
    with pytest.raises(ConfigurationError, match=r"Fable.*self-only.*claude-opus"):
        load_config(
            overrides={
                "models": {
                    "renamed-reviewer": {
                        "transport": "claude-exec",
                        "callable": True,
                        field: identity,
                    }
                }
            }
        )


@pytest.mark.parametrize(
    "panel,replacement",
    [("dual-fable", "dual-opus"), ("external-council-fable", "external-council")],
)
def test_legacy_fable_panel_overlay_names_replacement(panel: str, replacement: str) -> None:
    with pytest.raises(ConfigurationError, match=replacement):
        load_config(overrides={"panels": {panel: {"enabled": False}}})


def test_disabled_or_unreferenced_fable_profile_is_still_rejected() -> None:
    data = load_config().data
    data["models"]["renamed"] = {
        "transport": "claude-exec",
        "model": "claude-fable-5-1",
        "enabled": False,
    }
    with pytest.raises(ConfigurationError, match="self-only"):
        validate_config(FusionConfig(data, ()))


@pytest.mark.parametrize(
    "model,family",
    [
        ("claude-opus-5", "claude-opus"),
        ("claude-fable-5-1", "claude-fable"),
        ("claude-sonnet-5", "claude-sonnet"),
        ("claude-haiku-4-5-20251001", "claude-haiku"),
    ],
)
def test_self_uses_validated_session_family(tmp_path: Path, model: str, family: str) -> None:
    engine = FusionEngine(load_config(), _registry(_review_output()), work_root=tmp_path / "work")
    pending = engine.run(
        FusionRunRequest(
            task="Review",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="fast",
            self_model=model,
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    artifacts = engine.finalize(pending.run_id, dispositions=())
    assert artifacts.analysis["participants"][0]["family"] == family


def test_routed_self_requires_explicit_vendor_family_metadata(tmp_path: Path) -> None:
    config = load_config(
        overrides={
            "models": {
                "self": {
                    "allowed_models": ["gpt-6-astra"],
                    "identities": {"gpt-6-astra": {"vendor": "openai", "family": "gpt-6"}},
                }
            }
        }
    )
    engine = FusionEngine(config, _registry(_review_output()), work_root=tmp_path / "work")
    pending = engine.run(
        FusionRunRequest(
            task="Review",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="gpt-6-astra",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    artifacts = engine.finalize(pending.run_id, dispositions=())
    assert artifacts.analysis["participants"][0]["family"] == "gpt-6"
    records = [
        json.loads(line)
        for path in (tmp_path / "work").rglob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    identities = [record["payload"] for record in records if record.get("kind") == "self-identity"]
    assert len(identities) == 1
    assert identities[0]["vendor"] == "openai"
    assert identities[0]["family"] == "gpt-6"


def test_unknown_self_without_identity_metadata_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="identity metadata"):
        load_config(overrides={"models": {"self": {"allowed_models": ["unknown-session"]}}})


def test_runtime_registry_rejects_injected_callable_fable() -> None:
    profile = replace(load_config().model("claude-opus"), model="claude-fable-5-1")
    registry = ProviderRegistry({"claude-opus": type("Forbidden", (), {"profile": profile})()})
    with pytest.raises(ProviderError, match="self-only"):
        registry.get("claude-opus")


def test_direct_cli_adapter_rejects_fable_before_execution(tmp_path: Path) -> None:
    profile = replace(load_config().model("claude-opus"), canonical_model="claude-fable-5")
    request = ProviderRequest("r", "c", "claude-opus", "review", {}, tmp_path, 1, 100)
    provider = CliTransportProvider(profile, ClaudeCliAdapter(), None)  # type: ignore[arg-type]
    with pytest.raises(ProviderError, match="self-only"):
        provider.invoke(request)


@pytest.mark.parametrize("provider_type", [AnthropicHttpProvider, OpenAICompatibleHttpProvider])
@pytest.mark.parametrize("model", ["claude-fable-5", "claude-fable-5-1"])
def test_fable_self_profile_cannot_be_executed_by_http(
    tmp_path: Path,
    model: str,
    provider_type: type[AnthropicHttpProvider] | type[OpenAICompatibleHttpProvider],
) -> None:
    profile = replace(load_config().model("self"), model=model, canonical_model=model)
    provider = provider_type(profile, "https://unused.invalid", "UNUSED_TEST_KEY", None)  # type: ignore[arg-type]
    request = ProviderRequest("r", "c", "self", "review", {}, tmp_path, 1, 100)
    with pytest.raises(ProviderError, match="self-only"):
        provider.invoke(request)
