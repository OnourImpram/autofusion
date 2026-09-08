"""Cross-branch checks for context admission and configured route evidence."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_engine import _repo, _review_output
from test_engine_context import CountingProvider, _capabilities
from test_providers import RecordingRunner, UltraProbe, _outcome

from autofusion.budget import BudgetLedger, CallReservation
from autofusion.config import FusionConfig, load_config, validate_participant
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import ConfigurationError, PolicyError
from autofusion.evidence import EvidenceLedger
from autofusion.models import ContextBudget, ProviderRequest
from autofusion.packet import context_fit_error
from autofusion.providers.cli import CliTransportProvider, CodexExecAdapter
from autofusion.registry import ProviderRegistry
from autofusion.util import canonical_json_bytes


@pytest.mark.parametrize("location", ["prompt", "schema"])
def test_dlp_growth_cannot_exceed_admitted_context(tmp_path: Path, location: str) -> None:
    config = load_config()
    provider = CountingProvider(config.model("gpt-sol"))
    prompt = "a@b.co" if location == "prompt" else "Review"
    schema = {"description": "a@b.co"} if location == "schema" else {}
    needed = len(prompt.encode("utf-8")) + len(canonical_json_bytes(schema))
    request = ProviderRequest(
        "dlp-context", "dlp-context", "gpt-sol", prompt, schema, tmp_path, 1, 1000,
        context_budget=ContextBudget(needed + 2, 1, 1),
    )
    assert context_fit_error((request,)) is None
    with pytest.raises(PolicyError, match="context fit"):
        ProviderRegistry({"gpt-sol": provider}, config=config).invoke(request)
    assert not provider.requests


def test_context_preflight_precedes_rejected_provider_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(overrides={
        "models": {"gpt-sol": {"capabilities": _capabilities(100)}},
    })
    provider = CountingProvider(replace(config.model("gpt-sol"), vendor="wrong-vendor"))
    engine = FusionEngine(
        config, ProviderRegistry({"gpt-sol": provider}), work_root=tmp_path / "work",
    )
    reservations: list[int] = []
    reserve_call = BudgetLedger.reserve_call

    def track_reservation(self: BudgetLedger, *, output_chars: int) -> CallReservation:
        reservations.append(output_chars)
        return reserve_call(self, output_chars=output_chars)

    monkeypatch.setattr(BudgetLedger, "reserve_call", track_reservation)
    request = FusionRunRequest(
        task="Review", repo_root=_repo(tmp_path), artifact_kind="plan",
        artifact_paths=("app.py",), preset="fast", self_model="claude-opus-5",
        run_grounding=False,
    )
    with pytest.raises(PolicyError, match="context fit"):
        engine.run(request)
    assert not provider.requests
    assert not reservations

    # A fitting request reaches the independent admission rejection, without invocation.
    config.data["models"]["gpt-sol"]["capabilities"] = _capabilities(1_000_000)
    provider.profile = replace(config.model("gpt-sol"), vendor="wrong-vendor")
    result = engine.run(request)
    assert not isinstance(result, PendingRun)
    assert result.receipt["calls"][0]["status"] == "failed"
    with pytest.raises(PolicyError, match="does not match admitted configuration"):
        replace(engine.registry, config=config).invoke(ProviderRequest(
            "control", "control", "gpt-sol", "Review", {}, request.repo_root, 1, 100,
        ))
    assert len(reservations) == 1
    assert not provider.requests


def test_fable_callable_is_rejected_by_participant_admission() -> None:
    data = load_config().data
    data["models"]["claude-opus"]["model"] = "claude-fable-5-1"
    with pytest.raises(ConfigurationError, match="Fable is self-only"):
        validate_participant(FusionConfig(data, ()), "claude-opus", "reviewer")


def test_astra_configured_route_survives_engine_result_handling(tmp_path: Path) -> None:
    config = load_config(overrides={
        "panels": {"astra-review": {
            "topology": "review", "drafter": "self", "reviewers": ["astra-ultra"],
        }},
    })
    runner = RecordingRunner(
        _outcome('{"type":"turn.completed","usage":{}}'),
        last_message=json.dumps(_review_output()),
    )
    provider = CliTransportProvider(
        config.model("astra-ultra"),
        CodexExecAdapter(capability_probe=UltraProbe(True)), runner,
    )
    engine = FusionEngine(
        config, ProviderRegistry({"astra-ultra": provider}), work_root=tmp_path / "work",
    )
    pending = engine.run(FusionRunRequest(
        task="Review", repo_root=_repo(tmp_path), artifact_kind="plan",
        artifact_paths=("app.py",), preset="fast", panel="astra-review",
        self_model="claude-opus-5", run_grounding=False,
    ))
    assert isinstance(pending, PendingRun)
    artifacts = engine.finalize(pending.run_id, dispositions=())
    call = artifacts.receipt["calls"][0]
    assert call["status"] == "completed"
    assert call["configured_model"] == call["effective_model"] == "gpt-6-astra"
    assert call["observed_model"] is None
    assert call["identity_evidence"] == "configured-route"
    assert call["quota_group"] == "openai-chatgpt"
    routing = [
        record.payload for record in EvidenceLedger(pending.evidence_path).verify()
        if record.kind == "provider-routing"
    ]
    assert len(routing) == 1
    assert routing[0]["identity_evidence"] == "configured-route"
    assert routing[0]["observed_model"] is None
    assert routing[0]["configured_model"] == "gpt-6-astra"
    assert routing[0]["quota_group"] == "openai-chatgpt"
