from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from test_engine import _repo, _review_output

from autofusion.config import load_config
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import ConfigurationError, PolicyError
from autofusion.models import ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.registry import ProviderRegistry


@dataclass
class CountingProvider:
    profile: ModelProfile
    requests: list[ProviderRequest] = field(default_factory=list)

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        return DeterministicFakeProvider(self.profile, _review_output()).invoke(request)


def _capabilities(window: int) -> dict[str, object]:
    return {
        "context_window_tokens": window,
        "prompt_overhead_tokens": 10,
        "reserved_output_tokens": 10,
    }


@pytest.mark.parametrize("smallest", ["self", "gpt-sol"])
def test_engine_blocks_mandatory_context_overflow(tmp_path: Path, smallest: str) -> None:
    config = load_config(
        overrides={
            "models": {
                "self": {"capabilities": _capabilities(1_000_000)},
                "gpt-sol": {"capabilities": _capabilities(1_000_000)},
                smallest: {"capabilities": _capabilities(15_000)},
            }
        }
    )
    provider = CountingProvider(config.model("gpt-sol"))
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("# mandatory context\n" * 1500, encoding="utf-8")
    engine = FusionEngine(
        config, ProviderRegistry({"gpt-sol": provider}), work_root=tmp_path / "work"
    )
    with pytest.raises(PolicyError, match="context"):
        engine.run(
            FusionRunRequest(
                task="Review",
                repo_root=repo,
                artifact_kind="plan",
                artifact_paths=("app.py",),
                preset="fast",
                self_model="claude-opus-5",
                run_grounding=False,
            )
        )
    assert not provider.requests


def test_engine_small_judge_blocks_proposers_before_dispatch(tmp_path: Path) -> None:
    config = load_config(
        overrides={
            "models": {
                "gpt-sol": {"capabilities": _capabilities(1_000_000)},
                "claude-opus": {"capabilities": _capabilities(1_000_000)},
                "gpt-sol-ultra": {"capabilities": _capabilities(100)},
            }
        }
    )
    providers = {
        handle: CountingProvider(config.model(handle))
        for handle in ("gpt-sol", "claude-opus", "gpt-sol-ultra")
    }
    engine = FusionEngine(config, ProviderRegistry(providers), work_root=tmp_path / "work")
    with pytest.raises(PolicyError, match="context"):
        engine.run(
            FusionRunRequest(
                task="Review",
                repo_root=_repo(tmp_path),
                artifact_kind="plan",
                artifact_paths=("app.py",),
                preset="parallel",
                external_only=True,
                run_grounding=False,
            )
        )
    assert not any(provider.requests for provider in providers.values())


def test_engine_advisor_capacity_is_checked(tmp_path: Path) -> None:
    config = load_config(
        overrides={
            "models": {"gpt-sol": {"capabilities": _capabilities(100)}},
            "panels": {"advisor-limit": {"topology": "advisor", "advisor": "gpt-sol"}},
        }
    )
    provider = CountingProvider(config.model("gpt-sol"))
    engine = FusionEngine(
        config, ProviderRegistry({"gpt-sol": provider}), work_root=tmp_path / "work"
    )
    # Inspect dispatch directly: advisor participant admission is repaired in the sibling run.
    from autofusion.budget import BudgetLedger
    from autofusion.packet import compile_packet
    from autofusion.policy import resolve_route
    from autofusion.snapshot import build_snapshot

    route = resolve_route(
        config,
        requested_preset="fast",
        artifact_paths=("app.py",),
        external_only=True,
        explicit_panel="advisor-limit",
    )
    packet = compile_packet(
        build_snapshot(_repo(tmp_path), tmp_path / "snapshots"),
        task="Review",
        artifact_paths=("app.py",),
    )
    with pytest.raises(PolicyError, match="context"):
        engine._dispatch(route, packet, "context-advisor", BudgetLedger(route.budget))
    assert not provider.requests


@pytest.mark.parametrize("value", [None, True, -1, 0, "1000", 1.5])
def test_config_rejects_invalid_context_capability(value: object) -> None:
    with pytest.raises(ConfigurationError, match="context_window_tokens"):
        load_config(
            overrides={"models": {"gpt-sol": {"capabilities": {"context_window_tokens": value}}}}
        )


def test_engine_rejects_unknown_window(tmp_path: Path) -> None:
    config = load_config()
    config.data["models"]["gpt-sol"]["capabilities"].pop("context_window_tokens", None)
    provider = CountingProvider(config.model("gpt-sol"))
    engine = FusionEngine(
        config, ProviderRegistry({"gpt-sol": provider}), work_root=tmp_path / "work"
    )
    with pytest.raises(PolicyError, match="context_window_tokens"):
        engine.run(
            FusionRunRequest(
                task="Review",
                repo_root=_repo(tmp_path),
                artifact_kind="plan",
                artifact_paths=("app.py",),
                preset="fast",
                self_model="claude-opus-5",
                run_grounding=False,
            )
        )
    assert not provider.requests


@pytest.mark.parametrize("proposal_size", [8, 15_000])
def test_engine_judge_retains_packet_and_rechecks_grown_context(
    tmp_path: Path, proposal_size: int
) -> None:
    config = load_config(
        overrides={
            "models": {
                handle: {"capabilities": _capabilities(20_000)}
                for handle in ("gpt-sol", "claude-opus", "gpt-sol-ultra")
            }
        }
    )
    proposal = {
        "summary": "proposal",
        "proposal": "a" * proposal_size,
        "assumptions": [],
        "risks": [],
        "verification": [],
    }
    judges: list[ProviderRequest] = []

    class Judge:
        profile = config.model("gpt-sol-ultra")

        def invoke(self, request: ProviderRequest) -> ProviderResult:
            judges.append(request)
            output = {
                "winner": "left" if len(judges) == 1 else "right",
                "rationale": "consistent choice",
                "confidence": "high",
            }
            return DeterministicFakeProvider(self.profile, output).invoke(request)

    registry = ProviderRegistry(
        {
            "gpt-sol": DeterministicFakeProvider(config.model("gpt-sol"), proposal),
            "claude-opus": DeterministicFakeProvider(config.model("claude-opus"), proposal),
            "gpt-sol-ultra": Judge(),
        }
    )
    engine = FusionEngine(config, registry, work_root=tmp_path / "work")
    result = engine.run(
        FusionRunRequest(
            task="Required frozen task for the judge",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="parallel",
            external_only=True,
            run_grounding=False,
        )
    )
    assert not isinstance(result, PendingRun)
    calls = result.receipt["calls"]
    assert sum(call["status"] == "completed" for call in calls[:2]) == 2
    if proposal_size == 8:
        assert len(judges) == 2
        assert all("Required frozen task for the judge" in request.prompt for request in judges)
        assert result.receipt["fused"] is True
    else:
        assert not judges
        assert result.receipt["fused"] is False
        assert len(calls) == 4
        assert all(call["status"] == "policy-blocked" for call in calls[2:])


def test_engine_checks_fixed_judge_context_before_proposers(tmp_path: Path) -> None:
    from autofusion.budget import BudgetLedger
    from autofusion.packet import compile_packet, mandatory_context_tokens
    from autofusion.policy import resolve_route
    from autofusion.prompts import load_response_schema, proposal_prompt
    from autofusion.snapshot import build_snapshot
    from autofusion.util import canonical_json_bytes

    packet = compile_packet(
        build_snapshot(_repo(tmp_path), tmp_path / "snapshots"),
        task="Review",
        artifact_paths=("app.py",),
    )
    proposer_need = (
        len(proposal_prompt(packet).encode("utf-8"))
        + len(canonical_json_bytes(load_response_schema("proposal-output.schema.json")))
        + mandatory_context_tokens(packet)
    )
    config = load_config(
        overrides={
            "models": {
                handle: {"capabilities": _capabilities(proposer_need + 20)}
                for handle in ("gpt-sol", "claude-opus", "gpt-sol-ultra")
            }
        }
    )
    providers = {
        handle: CountingProvider(config.model(handle))
        for handle in ("gpt-sol", "claude-opus", "gpt-sol-ultra")
    }
    engine = FusionEngine(config, ProviderRegistry(providers), work_root=tmp_path / "work")
    route = resolve_route(
        config, requested_preset="parallel", artifact_paths=("app.py",), external_only=True
    )
    with pytest.raises(PolicyError, match="context fit"):
        engine._dispatch(route, packet, "fixed-judge-context", BudgetLedger(route.budget))
    assert not any(provider.requests for provider in providers.values())
