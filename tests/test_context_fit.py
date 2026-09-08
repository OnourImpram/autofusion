from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autofusion.budget import BudgetLedger
from autofusion.errors import PolicyError
from autofusion.models import (
    CallStatus,
    ContextBudget,
    ModelProfile,
    ProviderRequest,
    ProviderResult,
    RunBudget,
)
from autofusion.packet import (
    compile_packet,
    context_fit_error,
    mandatory_context_tokens,
    profile_context_budget,
    shared_context_limit,
)
from autofusion.snapshot import build_snapshot
from autofusion.topologies import (
    context_quorum,
    dispatch_blind_first_passes,
    run_dual_review,
    run_panel_rank,
)
from autofusion.util import canonical_json_bytes


def _budget(**values: Any) -> ContextBudget:
    return ContextBudget(**{
        "window_tokens": 1000,
        "prompt_overhead_tokens": 10,
        "reserved_output_tokens": 10,
        **values,
    })


def _request(handle: str = "reviewer", **values: Any) -> ProviderRequest:
    return ProviderRequest(**{
        "run_id": "run-context",
        "call_id": f"call-{handle}",
        "handle": handle,
        "prompt": "review",
        "response_schema": {},
        "working_directory": Path.cwd(),
        "timeout_s": 10,
        "max_output_chars": 1000,
        "metadata": {"packet_hash": "a" * 64},
        **values,
    })


def _profile(handle: str = "reviewer", **capabilities: Any) -> ModelProfile:
    return ModelProfile(
        handle=handle,
        transport="test",
        callable=True,
        model=handle,
        canonical_model=handle,
        vendor="test",
        family=handle,
        effort="test",
        context="repo-snapshot",
        capabilities={
            "context_window_tokens": 1000,
            "prompt_overhead_tokens": 10,
            "reserved_output_tokens": 10,
            **capabilities,
        },
    )


class Dispatcher:
    def __init__(self, *, proposal_text: str = "ok") -> None:
        self.calls: list[ProviderRequest] = []
        self.proposal_text = proposal_text

    async def dispatch(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        output = {"answer": self.proposal_text}
        return ProviderResult(
            request.call_id, request.handle, request.handle, request.handle,
            "test", request.handle, "test", False, "not-applicable",
            CallStatus.COMPLETED, 0, canonical_json_bytes(output).decode(), output,
            "b" * 64,
        )


def test_equal_packet_hash_without_capacity_is_not_context_quorum() -> None:
    assert not context_quorum((_request(),), required_handles=("reviewer",))


def test_equal_packet_hashes_do_not_allow_context_overflow() -> None:
    requests = (
        _request("first", context_budget=_budget()),
        _request("second", prompt="x" * 1000, context_budget=_budget()),
    )
    dispatcher = Dispatcher()
    outcome = asyncio.run(run_dual_review(
        dispatcher, requests, budget=BudgetLedger(RunBudget(2, 10, None, 1000))
    ))
    assert not outcome.context_complete
    assert outcome.degraded and not outcome.fused
    assert dispatcher.calls == []


@pytest.mark.parametrize("reverse", [False, True])
def test_context_fit_uses_smallest_participant(reverse: bool) -> None:
    requests = [
        _request("large", prompt="x" * 100, context_budget=_budget()),
        _request("small", prompt="x", context_budget=_budget(window_tokens=100)),
    ]
    if reverse:
        requests.reverse()
    error = context_fit_error(requests)
    assert error is not None and "large" in error and "102" in error and "80" in error


@pytest.mark.parametrize("field", ["prompt_overhead_tokens", "reserved_output_tokens"])
def test_context_fit_counts_overhead_and_output_reservation(field: str) -> None:
    request = _request(prompt="x" * 70, context_budget=_budget(window_tokens=100))
    assert context_fit_error((request,)) is None
    request = replace(request, context_budget=_budget(window_tokens=100, **{field: 20}))
    assert context_fit_error((request,)) is not None


def test_context_fit_recomputes_actual_prompt_and_schema() -> None:
    request = _request(prompt="x" * 78, context_budget=_budget(window_tokens=100))
    assert context_fit_error((request,)) is None
    assert context_fit_error((replace(request, prompt="x" * 79),)) is not None
    assert context_fit_error((replace(request, response_schema={"x": "y"}),))


def test_utf8_bytes_are_used_as_conservative_input_bound() -> None:
    request = _request(prompt="\U0001f680" * 20, context_budget=_budget(window_tokens=100))
    assert context_fit_error((request,)) is not None


@pytest.mark.parametrize("invalid", [None, True, False, 0, -1, 1.5, "100", float("inf")])
@pytest.mark.parametrize("key", [
    "context_window_tokens", "prompt_overhead_tokens", "reserved_output_tokens",
])
def test_profile_context_capabilities_fail_closed(key: str, invalid: Any) -> None:
    with pytest.raises(PolicyError, match=key):
        profile_context_budget(_profile(**{key: invalid}))


def test_missing_profile_context_capability_fails_closed() -> None:
    profile = replace(_profile(), capabilities={})
    with pytest.raises(PolicyError, match="context_window_tokens"):
        profile_context_budget(profile)


def test_shared_context_limit_uses_every_profile_and_rejects_empty() -> None:
    assert shared_context_limit((
        _profile("large"), _profile("small", context_window_tokens=100)
    )) == 80
    with pytest.raises(PolicyError):
        shared_context_limit(())


@pytest.mark.parametrize("invalid", [None, True, 0, -1, 10.5, "100"])
def test_request_window_is_validated_at_dispatch(invalid: Any) -> None:
    request = _request(context_budget=_budget(window_tokens=invalid))
    dispatcher = Dispatcher()
    ledger = BudgetLedger(RunBudget(1, 10, None, 1000))
    results = asyncio.run(dispatch_blind_first_passes(
        dispatcher, (request,), budget=ledger, max_concurrency=1,
    ))
    assert results[0].status is CallStatus.POLICY_BLOCKED
    assert dispatcher.calls == []
    assert ledger.snapshot().calls_used == 0


def test_batch_overflow_blocks_all_calls_before_budget_reservation() -> None:
    requests = (
        _request("fits", context_budget=_budget()),
        _request("overflow", prompt="x" * 1000, context_budget=_budget()),
    )
    dispatcher = Dispatcher()
    ledger = BudgetLedger(RunBudget(2, 10, None, 1000))
    results = asyncio.run(dispatch_blind_first_passes(
        dispatcher, requests, budget=ledger, max_concurrency=2,
    ))
    assert all(result.status is CallStatus.POLICY_BLOCKED for result in results)
    assert all("overflow" in (result.error or "") for result in results)
    assert dispatcher.calls == []
    assert ledger.snapshot().calls_used == 0


def test_shared_input_limit_survives_single_judge_request() -> None:
    request = _request(prompt="x" * 100, context_budget=_budget(shared_input_limit=80))
    assert context_fit_error((request,)) is not None


def test_mandatory_artifact_overflow_is_counted_without_inline_contents(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("x" * 1000, encoding="utf-8")
    snapshot = build_snapshot(source, tmp_path / "snapshots")
    packet = compile_packet(snapshot, task="review", artifact_paths=("app.py",))
    mandatory = mandatory_context_tokens(packet)
    assert mandatory == 1000
    request = _request(context_budget=_budget(mandatory_tokens=mandatory))
    assert context_fit_error((request,)) is not None
    assert packet.payload["artifact_paths"] == ["app.py"]


def test_inline_artifacts_are_not_counted_twice(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("x" * 100, encoding="utf-8")
    snapshot = build_snapshot(source, tmp_path / "snapshots")
    packet = compile_packet(
        snapshot, task="review", artifact_paths=("app.py",), include_artifact_contents=True,
    )
    assert mandatory_context_tokens(packet) == 0


@pytest.mark.parametrize("inline", [False, True])
def test_binary_mandatory_artifact_fails_closed(tmp_path: Path, inline: bool) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "artifact.bin").write_bytes(b"x\x00y")
    snapshot = build_snapshot(source, tmp_path / "snapshots")
    packet = compile_packet(
        snapshot, task="review", artifact_paths=("artifact.bin",),
        include_artifact_contents=inline,
    )
    with pytest.raises(PolicyError, match="binary"):
        mandatory_context_tokens(packet)


def test_panel_rank_preserves_proposals_when_growing_judge_prompt_overflows() -> None:
    proposals = tuple(_request(handle, context_budget=_budget(window_tokens=400))
                      for handle in ("first", "second"))
    dispatcher = Dispatcher(proposal_text="x" * 200)

    def pairwise(
        left_id: str, left: ProviderResult, right_id: str, right: ProviderResult,
    ) -> ProviderRequest:
        return _request(
            "judge", call_id=f"judge-{left_id}-{right_id}",
            prompt=left.output_text + right.output_text,
            context_budget=_budget(window_tokens=400),
        )

    ledger = BudgetLedger(RunBudget(4, 10, None, 1000))
    outcome = asyncio.run(run_panel_rank(
        dispatcher, proposals, pairwise_request=pairwise, budget=ledger,
    ))
    assert len(outcome.proposals) == 2
    assert all(result.status is CallStatus.COMPLETED for result in outcome.proposals)
    assert len(outcome.judge_results) == 2
    assert all(result.status is CallStatus.POLICY_BLOCKED for result in outcome.judge_results)
    assert [request.handle for request in dispatcher.calls] == ["first", "second"]
    assert ledger.snapshot().calls_used == 2
    assert outcome.degraded and outcome.abstained and outcome.winner_id is None
