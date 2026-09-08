from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_engine import _registry, _repo, _review_output
from test_providers import RecordingRunner, UltraProbe, _outcome, _request

from autofusion.config import load_config
from autofusion.doctor import inspect_environment
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import ConfigurationError, ProviderError
from autofusion.models import ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers.cli import CliTransportProvider, CodexExecAdapter
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.registry import ProviderRegistry


def test_astra_ultra_profile_and_shared_quota() -> None:
    config = load_config()
    profile = config.model("astra-ultra")
    assert (profile.transport, profile.model, profile.effort) == (
        "codex-exec",
        "gpt-6-astra",
        "ultra",
    )
    assert profile.compound and profile.worker_visibility == "opaque"
    assert profile.quota_group == config.model("gpt-sol").quota_group == "openai-chatgpt"
    assert config.model("gpt-sol-ultra").quota_group == profile.quota_group
    assert set(config.section("quota_groups")[profile.quota_group]["models"]) == {
        "gpt-5.6-sol",
        "gpt-6-astra",
        "gpt-5.6-terra",
    }
    assert "astra-ultra" in config.section("guardrails")["model_allowlist"]
    assert config.section("routing")["compound"]["allowed_roles"]["astra-ultra"] == [
        "reviewer",
        "judge",
    ]


def test_astra_ultra_argv_uses_configured_effort(tmp_path: Path) -> None:
    profile = load_config().model("astra-ultra")
    runner = RecordingRunner(_outcome('{"model":"gpt-6-astra"}'))
    provider = CliTransportProvider(
        profile, CodexExecAdapter(capability_probe=UltraProbe(True)), runner
    )
    result = provider.invoke(replace(_request(tmp_path), handle="astra-ultra"))
    assert runner.argv is not None
    assert runner.argv[runner.argv.index("--model") + 1] == "gpt-6-astra"
    assert runner.argv[runner.argv.index("--config") + 1] == "model_reasoning_effort=ultra"
    assert result.compound and result.worker_visibility == "opaque"


def test_astra_ultra_rejects_unproven_capability(tmp_path: Path) -> None:
    profile = load_config().model("astra-ultra")
    with pytest.raises(ProviderError, match="unproven"):
        CodexExecAdapter(capability_probe=UltraProbe(False)).build_argv(
            profile,
            _request(tmp_path),
            output_schema_path=tmp_path / "schema",
            output_last_message_path=tmp_path / "output",
        )


def test_astra_compound_cannot_be_a_proposer() -> None:
    with pytest.raises(ConfigurationError, match=r"compound.*proposer"):
        load_config(overrides={"panels": {"external-council": {"proposers": ["astra-ultra"]}}})


def test_astra_receipt_keeps_one_compound_participant(tmp_path: Path) -> None:
    config = load_config(
        overrides={
            "panels": {
                "astra-review": {
                    "topology": "review",
                    "drafter": "self",
                    "reviewers": ["astra-ultra"],
                }
            }
        }
    )
    providers = dict(_registry(_review_output()).providers)
    providers["astra-ultra"] = DeterministicFakeProvider(
        config.model("astra-ultra"), _review_output()
    )
    engine = FusionEngine(config, ProviderRegistry(providers), work_root=tmp_path / "work")
    pending = engine.run(
        FusionRunRequest(
            task="Review",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="fast",
            panel="astra-review",
            self_model="claude-opus-5",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    artifacts = engine.finalize(pending.run_id, dispositions=())
    assert artifacts.receipt["requested_participants"] == ["self", "astra-ultra"]
    assert len(artifacts.receipt["calls"]) == 1
    call = artifacts.receipt["calls"][0]
    assert call["configured_model"] == call["observed_model"] == "gpt-6-astra"
    assert call["identity_evidence"] == "provider-response"
    assert call["quota_group"] == "openai-chatgpt"


def test_doctor_distinguishes_configuration_from_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autofusion.doctor._resolve_executable", lambda _: None)
    result = inspect_environment(load_config(), grounding_runner=None)
    profile = result["configured_profiles"]["astra-ultra"]
    assert profile["configured_model"] == "gpt-6-astra"
    assert profile["observed_model"] is None
    assert profile["quota_group"] == "openai-chatgpt"


@pytest.mark.parametrize("failure", ["policy", "exception"])
def test_new_astra_failure_preserves_configuration(tmp_path: Path, failure: str) -> None:
    config = load_config(
        overrides={
            "panels": {
                "astra-review": {
                    "topology": "review",
                    "drafter": "self",
                    "reviewers": ["astra-ultra"],
                }
            }
        }
    )

    class UnprovenProvider:
        profile: ModelProfile = config.model("astra-ultra")

        def invoke(self, request: ProviderRequest) -> ProviderResult:
            raise ProviderError("Codex ultra capability unproven")

    engine = FusionEngine(
        config, ProviderRegistry({"astra-ultra": UnprovenProvider()}), work_root=tmp_path / "work"
    )
    request = FusionRunRequest(
        task="Review",
        repo_root=_repo(tmp_path),
        artifact_kind="plan",
        artifact_paths=("app.py",),
        preset="fast",
        panel="astra-review",
        self_model="claude-opus-5",
        run_grounding=False,
    )
    if failure == "policy":
        route, _ = engine._resolve_route(request)
        call = engine._result_json(engine._policy_blocked_results(route, "policy blocked")[0])
    else:
        artifacts = engine.run(request)
        assert not isinstance(artifacts, PendingRun)
        call = artifacts.receipt["calls"][0]
    assert call["configured_model"] == "gpt-6-astra"
    assert call["observed_model"] is None
    assert call["identity_evidence"] == "unavailable"
    assert call["quota_group"] == "openai-chatgpt"
