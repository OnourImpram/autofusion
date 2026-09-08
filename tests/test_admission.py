from __future__ import annotations

import json
from pathlib import Path

import pytest

from autofusion import cli
from autofusion.config import FusionConfig, load_config, validate_config
from autofusion.engine import FusionEngine, FusionRunRequest
from autofusion.errors import ConfigurationError, PolicyError
from autofusion.models import ModelProfile, ProviderRequest, ProviderResult
from autofusion.registry import ProviderRegistry


class NeverProvider:
    def __init__(self, profile: ModelProfile) -> None:
        self.profile = profile
        self.calls = 0

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        self.calls += 1
        raise AssertionError("rejected admission invoked a provider")


@pytest.mark.parametrize("restriction", ["allowlist", "disabled", "callable", "vendor"])
def test_advisor_admission_rejects_before_dispatch(tmp_path: Path, restriction: str) -> None:
    data = load_config().data
    data["models"]["advisor-test"] = dict(data["models"]["gpt-sol"])
    data["guardrails"]["model_allowlist"].append("advisor-test")
    data["panels"]["advice"] = {"topology": "advisor", "advisor": "advisor-test"}
    if restriction == "allowlist":
        data["guardrails"]["model_allowlist"].remove("advisor-test")
    elif restriction == "vendor":
        data["guardrails"]["provider_denylist"] = ["openai"]
    else:
        data["models"]["advisor-test"]["enabled" if restriction == "disabled" else "callable"] = (
            False
        )
    config = FusionConfig(data, ())
    provider = NeverProvider(config.model("advisor-test"))
    engine = FusionEngine(
        config, ProviderRegistry({"advisor-test": provider}), work_root=tmp_path / "work"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises((ConfigurationError, PolicyError)):
        engine.run(
            FusionRunRequest(
                task="Review",
                repo_root=repo,
                artifact_kind="plan",
                panel="advice",
                external_only=True,
                run_grounding=False,
            )
        )
    assert provider.calls == 0


@pytest.mark.parametrize("restriction", ["proposer", "allowlist"])
def test_compound_roles_are_checked_on_load(restriction: str) -> None:
    data = load_config().data
    if restriction == "proposer":
        data["panels"]["external-council"]["proposers"] = ["gpt-sol-ultra", "claude-opus"]
    else:
        data["routing"]["compound"]["allowed_handles"] = []
    with pytest.raises(ConfigurationError, match="compound"):
        validate_config(FusionConfig(data, ()))


def test_scalar_judge_role_rejects_list_before_dispatch() -> None:
    with pytest.raises(ConfigurationError, match="judge"):
        load_config(overrides={"panels": {"external-council": {"judge": ["gpt-sol-ultra"]}}})


@pytest.mark.parametrize("mismatch", ["fable", "vendor", "callable"])
def test_registry_rejects_a_different_executable_profile(tmp_path: Path, mismatch: str) -> None:
    from dataclasses import replace

    config = load_config()
    profile = config.model("claude-opus")
    if mismatch == "fable":
        profile = replace(profile, model="claude-fable-5-1", canonical_model="claude-fable-5-1")
    elif mismatch == "vendor":
        profile = replace(profile, vendor="denied-vendor")
    else:
        profile = replace(profile, callable=False)
    provider = NeverProvider(profile)
    registry = ProviderRegistry({"claude-opus": provider}, config=config)
    with pytest.raises((ConfigurationError, PolicyError)):
        registry.invoke(ProviderRequest("run", "call", "claude-opus", "Review", {},
                                        tmp_path, 1, 100))
    assert provider.calls == 0


def test_packet_declaration_does_not_remove_cli_repository_access(tmp_path: Path) -> None:
    config = load_config(overrides={"models": {"gpt-sol": {"context": "packet"}}})
    (tmp_path / "app.txt").write_text("token=" + "syntheticcredential12345", encoding="utf-8")
    provider = NeverProvider(config.model("gpt-sol"))
    registry = ProviderRegistry({"gpt-sol": provider}, config=config)
    with pytest.raises(PolicyError, match="DLP"):
        registry.invoke(ProviderRequest("run", "call", "gpt-sol", "Review", {},
                                        tmp_path, 1, 100))
    assert provider.calls == 0


@pytest.mark.parametrize("location", ["prompt", "snapshot", "schema"])
def test_direct_call_blocks_credentials_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    config = load_config(overrides={"guardrails": {"sensitive_data_action": "block"}})
    provider = NeverProvider(config.model("gpt-sol"))
    monkeypatch.setattr(cli, "_config", lambda args: config)
    monkeypatch.setattr(
        ProviderRegistry, "from_config", lambda config: ProviderRegistry({"gpt-sol": provider})
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = "token=" + "syntheticcredential12345"
    (repo / "app.txt").write_text(secret if location == "snapshot" else "safe", encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({
        "type": "object", "description": secret if location == "schema" else "safe"
    }), encoding="utf-8")
    status = cli.main(
        [
            "call",
            "gpt-sol",
            "--repo",
            str(repo),
            "--schema",
            str(schema),
            "--prompt",
            secret if location == "prompt" else "Review",
            "--work-root",
            str(tmp_path / "work"),
        ]
    )
    assert status == 2
    assert provider.calls == 0
