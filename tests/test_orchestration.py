from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from autofusion.analysis import AnalysisInput, build_analysis
from autofusion.budget import BudgetLedger
from autofusion.config import load_config
from autofusion.errors import BudgetExceeded
from autofusion.models import CallStatus, ProviderRequest, ProviderResult, RunBudget, RunState
from autofusion.router import AdaptiveSignals, effective_participant_count, resolve_adaptive_route
from autofusion.state import RunStateMachine, StateTransitionError
from autofusion.topologies import run_dual_review, run_panel_rank


def _request(handle: str, call_id: str, *, packet_hash: str = "a" * 64) -> ProviderRequest:
    return ProviderRequest(
        run_id="run-1",
        call_id=call_id,
        handle=handle,
        prompt="independent review",
        response_schema={},
        working_directory=Path.cwd(),
        timeout_s=30,
        max_output_chars=100,
        metadata={"packet_hash": packet_hash},
    )


def _result(
    request: ProviderRequest,
    *,
    status: CallStatus = CallStatus.COMPLETED,
    structured_output: dict[str, object] | None = None,
) -> ProviderResult:
    return ProviderResult(
        call_id=request.call_id,
        handle=request.handle,
        requested_model=request.handle,
        effective_model=request.handle,
        vendor="test",
        family=request.handle,
        mode="test",
        compound=False,
        worker_visibility="transparent",
        status=status,
        duration_ms=1,
        output_text="ok",
        structured_output=structured_output or {},
        output_hash="b" * 64,
    )


class FakeDispatcher:
    def __init__(self, response: Callable[[ProviderRequest], ProviderResult]) -> None:
        self._response = response
        self.calls: list[ProviderRequest] = []

    async def dispatch(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        return self._response(request)


def test_terminal_state_cannot_be_resurrected() -> None:
    machine = RunStateMachine()
    machine.transition(RunState.ROUTED, reason="route selected")
    machine.terminate(RunState.FAILED, reason="provider unavailable")

    with pytest.raises(StateTransitionError, match="terminal"):
        machine.transition(RunState.FROZEN, reason="retry")


def test_budget_boundary_and_retry_attempts_are_consumed() -> None:
    ledger = BudgetLedger(RunBudget(2, 10, 1.0, 10))
    first = ledger.reserve_call(output_chars=10, estimated_cost_usd=0.5)
    ledger.settle_call(first, actual_cost_usd=0.5)
    retry = ledger.reserve_call(output_chars=10, estimated_cost_usd=0.5)
    ledger.settle_call(retry, actual_cost_usd=0.5)

    with pytest.raises(BudgetExceeded, match="call budget"):
        ledger.reserve_call(output_chars=1)


def test_unknown_cost_fails_closed_under_hard_cost_budget() -> None:
    ledger = BudgetLedger(RunBudget(1, 10, 1.0, 10))
    reservation = ledger.reserve_call(output_chars=1)
    with pytest.raises(BudgetExceeded, match="unknown"):
        ledger.settle_call(reservation, actual_cost_usd=None)


def test_hard_gate_cannot_be_downgraded() -> None:
    decision = resolve_adaptive_route(
        load_config(overrides={}),
        signals=AdaptiveSignals(artifact_paths=("src/auth/session.py",)),
        requested_preset="fast",
    )

    assert decision.selected_preset == "balanced"
    assert decision.hard_gates


def test_single_source_claim_is_unique_not_consensus() -> None:
    request = _request("reviewer", "call-1")
    result = _result(
        request,
        structured_output={
            "findings": [
                {
                    "claim": "race",
                    "severity": "major",
                    "source_ids": ["forged-source-a", "forged-source-b"],
                }
            ]
        },
    )
    analysis = build_analysis(
        run_id="run-1",
        packet_hash="a" * 64,
        panel="default",
        topology="review",
        inputs=(AnalysisInput(result),),
        required_handles=("reviewer",),
    )

    assert analysis["consensus"] == []
    assert analysis["unique_insights"][0]["source_id"] == "reviewer"


def test_compound_profile_does_not_inflate_participant_count() -> None:
    compound = load_config(overrides={}).model("gpt-sol-ultra")

    assert compound.compound
    assert effective_participant_count((compound,)) == 1


def test_failed_required_reviewer_degrades_dual_review() -> None:
    first = _request("first", "call-1")
    second = _request("second", "call-2")

    def response(request: ProviderRequest) -> ProviderResult:
        status = CallStatus.FAILED if request.handle == "second" else CallStatus.COMPLETED
        return _result(request, status=status)

    dispatcher = FakeDispatcher(response)
    budget = BudgetLedger(RunBudget(2, 10, None, 100))
    outcome = asyncio.run(run_dual_review(dispatcher, (first, second), budget=budget))

    assert not outcome.fused
    assert outcome.degraded
    assert "second" in (outcome.reason or "")


def test_context_quorum_failure_stops_before_dispatch() -> None:
    first = _request("first", "call-1", packet_hash="a" * 64)
    second = _request("second", "call-2", packet_hash="b" * 64)
    dispatcher = FakeDispatcher(_result)
    budget = BudgetLedger(RunBudget(2, 10, None, 100))
    outcome = asyncio.run(run_dual_review(dispatcher, (first, second), budget=budget))

    assert outcome.degraded
    assert not outcome.context_complete
    assert dispatcher.calls == []


def test_panel_rank_abstains_when_answer_order_changes_winner() -> None:
    first = _request("first", "proposal-1")
    second = _request("second", "proposal-2")

    def response(request: ProviderRequest) -> ProviderResult:
        output: dict[str, object] | None = (
            {"winner": "left"} if request.handle == "judge" else None
        )
        return _result(request, structured_output=output)

    dispatcher = FakeDispatcher(response)

    def pairwise(
        left_id: str,
        left: ProviderResult,
        right_id: str,
        right: ProviderResult,
    ) -> ProviderRequest:
        return _request("judge", f"judge-{left_id}-{right_id}")

    outcome = asyncio.run(
        run_panel_rank(
            dispatcher,
            (first, second),
            pairwise_request=pairwise,
            budget=BudgetLedger(RunBudget(4, 10, None, 100)),
        )
    )

    assert outcome.abstained
    assert outcome.comparisons[0].order_sensitive
