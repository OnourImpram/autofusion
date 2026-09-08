"""Bounded topology primitives with injected provider dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Protocol

from autofusion.budget import BudgetLedger
from autofusion.errors import BudgetExceeded, PolicyError, ProviderError
from autofusion.models import CallStatus, ProviderRequest, ProviderResult
from autofusion.packet import context_fit_error


class ProviderDispatcher(Protocol):
    """The provider boundary used by topology orchestration."""

    async def dispatch(self, request: ProviderRequest) -> ProviderResult: ...


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    topology: str
    results: tuple[ProviderResult, ...]
    required_handles: tuple[str, ...]
    context_complete: bool
    fused: bool
    degraded: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PairwiseOutcome:
    left_id: str
    right_id: str
    winner: str | None
    order_sensitive: bool


@dataclass(frozen=True, slots=True)
class PanelRankResult:
    proposals: tuple[ProviderResult, ...]
    judge_results: tuple[ProviderResult, ...]
    comparisons: tuple[PairwiseOutcome, ...]
    winner_id: str | None
    abstained: bool
    degraded: bool


def context_quorum(
    requests: Sequence[ProviderRequest], *, required_handles: Sequence[str]
) -> bool:
    """Require one frozen packet and verified fit for the required first passes."""

    if context_fit_error(requests) is not None:
        return False
    if len({request.handle for request in requests}) != len(requests):
        return False
    by_handle = {request.handle: request for request in requests}
    if any(handle not in by_handle for handle in required_handles):
        return False
    hashes = {str(by_handle[handle].metadata.get("packet_hash", "")) for handle in required_handles}
    return len(hashes) == 1 and "" not in hashes


async def dispatch_blind_first_passes(
    dispatcher: ProviderDispatcher,
    requests: Sequence[ProviderRequest],
    *,
    budget: BudgetLedger,
    max_concurrency: int,
) -> tuple[ProviderResult, ...]:
    """Dispatch isolated first passes with bounded concurrency."""

    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least one")
    fit_error = context_fit_error(requests)
    if fit_error is not None:
        return tuple(
            replace(
                _failed_result(request, PolicyError(fit_error)),
                status=CallStatus.POLICY_BLOCKED,
            )
            for request in requests
        )
    semaphore = asyncio.Semaphore(max_concurrency)

    async def dispatch_admitted(request: ProviderRequest) -> ProviderResult:
        async with semaphore:
            try:
                reservation = budget.reserve_call(output_chars=request.max_output_chars)
            except BudgetExceeded as exc:
                if budget.remaining_s() <= 0:
                    return _failed_result(request, TimeoutError("execution deadline exhausted"))
                return _failed_result(request, exc)
            remaining = budget.remaining_s()
            admitted_at = budget.deadline - remaining
            deadline = min(budget.deadline, admitted_at + request.timeout_s)
            if request.deadline_monotonic is not None:
                deadline = min(deadline, request.deadline_monotonic)
            request = replace(
                request,
                timeout_s=max(0.0, deadline - admitted_at),
                deadline_monotonic=deadline,
            )
            try:
                result = await asyncio.wait_for(
                    dispatcher.dispatch(request), timeout=request.timeout_s
                )
                if budget.remaining_s() <= 0:
                    raise TimeoutError("execution deadline exhausted")
            except asyncio.CancelledError:
                with suppress(BudgetExceeded):
                    budget.settle_call(reservation, actual_cost_usd=None)
                raise
            except Exception as exc:
                with suppress(BudgetExceeded):
                    budget.settle_call(reservation, actual_cost_usd=None)
                return _failed_result(request, exc)
            if result.handle != request.handle or result.call_id != request.call_id:
                with suppress(BudgetExceeded):
                    budget.settle_call(reservation, actual_cost_usd=result.cost_usd)
                return _failed_result(request, ProviderError("provider result identity mismatch"))
            try:
                budget.settle_call(reservation, actual_cost_usd=result.cost_usd)
            except BudgetExceeded:
                return replace(
                    result,
                    status=CallStatus.FAILED,
                    structured_output=None,
                    error="cost budget exhausted after provider response",
                )
            if len(result.output_text) > request.max_output_chars:
                return replace(
                    result,
                    status=CallStatus.FAILED,
                    structured_output=None,
                    error="provider output exceeded requested bound",
                )
            return result

    async def dispatch_one(request: ProviderRequest) -> ProviderResult:
        try:
            # This bounds queue waits as well as dispatch; transports enforce the same deadline.
            # https://docs.python.org/3.11/library/asyncio-task.html#asyncio.wait_for
            return await asyncio.wait_for(dispatch_admitted(request), timeout=budget.remaining_s())
        except TimeoutError:
            return _failed_result(request, TimeoutError("execution deadline exhausted"))

    return tuple(await asyncio.gather(*(dispatch_one(request) for request in requests)))


async def run_review(
    dispatcher: ProviderDispatcher, request: ProviderRequest, *, budget: BudgetLedger
) -> OrchestrationResult:
    quorum = context_quorum((request,), required_handles=(request.handle,))
    if not quorum:
        return OrchestrationResult(
            "review", (), (request.handle,), False, False, True, "context quorum failed"
        )
    results = await dispatch_blind_first_passes(
        dispatcher, (request,), budget=budget, max_concurrency=1
    )
    return _result("review", results, (request.handle,), context_complete=quorum)


async def run_adversarial_review(
    dispatcher: ProviderDispatcher, request: ProviderRequest, *, budget: BudgetLedger
) -> OrchestrationResult:
    quorum = context_quorum((request,), required_handles=(request.handle,))
    if not quorum:
        return OrchestrationResult(
            "adversarial-review",
            (),
            (request.handle,),
            False,
            False,
            True,
            "context quorum failed",
        )
    results = await dispatch_blind_first_passes(
        dispatcher, (request,), budget=budget, max_concurrency=1
    )
    return _result("adversarial-review", results, (request.handle,), context_complete=quorum)


async def run_adversarial_council(
    dispatcher: ProviderDispatcher,
    requests: Sequence[ProviderRequest],
    *,
    budget: BudgetLedger,
    max_concurrency: int = 2,
) -> OrchestrationResult:
    """Run multiple blind adversaries while preserving one bounded topology."""

    if len(requests) < 2:
        raise ValueError("adversarial council requires at least two reviewer requests")
    required = tuple(request.handle for request in requests)
    if not context_quorum(requests, required_handles=required):
        return OrchestrationResult(
            "adversarial-review",
            (),
            required,
            context_complete=False,
            fused=False,
            degraded=True,
            reason="context quorum failed before adversarial dispatch",
        )
    results = await dispatch_blind_first_passes(
        dispatcher,
        requests,
        budget=budget,
        max_concurrency=max_concurrency,
    )
    return _result("adversarial-review", results, required, context_complete=True)


async def run_dual_review(
    dispatcher: ProviderDispatcher,
    requests: Sequence[ProviderRequest],
    *,
    budget: BudgetLedger,
    max_concurrency: int = 2,
) -> OrchestrationResult:
    if len(requests) < 2:
        raise ValueError("dual review requires at least two reviewer requests")
    required = tuple(request.handle for request in requests)
    if not context_quorum(requests, required_handles=required):
        return OrchestrationResult(
            "dual-review",
            (),
            required,
            context_complete=False,
            fused=False,
            degraded=True,
            reason="context quorum failed before dispatch",
        )
    results = await dispatch_blind_first_passes(
        dispatcher, requests, budget=budget, max_concurrency=max_concurrency
    )
    return _result("dual-review", results, required, context_complete=True)


async def run_advisor(
    dispatcher: ProviderDispatcher, request: ProviderRequest, *, budget: BudgetLedger
) -> OrchestrationResult:
    quorum = context_quorum((request,), required_handles=(request.handle,))
    if not quorum:
        return OrchestrationResult(
            "advisor", (), (request.handle,), False, False, True, "context quorum failed"
        )
    results = await dispatch_blind_first_passes(
        dispatcher, (request,), budget=budget, max_concurrency=1
    )
    complete = results[0].status is CallStatus.COMPLETED
    return OrchestrationResult(
        "advisor",
        results,
        (request.handle,),
        context_complete=quorum,
        fused=False,
        degraded=not complete,
        reason=None if complete else "advisor did not complete",
    )


PairwiseRequestFactory = Callable[[str, ProviderResult, str, ProviderResult], ProviderRequest]


async def run_panel_rank(
    dispatcher: ProviderDispatcher,
    proposer_requests: Sequence[ProviderRequest],
    *,
    pairwise_request: PairwiseRequestFactory,
    budget: BudgetLedger,
    max_concurrency: int = 3,
) -> PanelRankResult:
    """Rank anonymous proposals with each pair judged in both answer orders."""

    if len(proposer_requests) < 2:
        raise ValueError("panel rank requires at least two proposals")
    proposer_handles = tuple(request.handle for request in proposer_requests)
    if not context_quorum(proposer_requests, required_handles=proposer_handles):
        return PanelRankResult((), (), (), None, abstained=True, degraded=True)
    proposals = await dispatch_blind_first_passes(
        dispatcher, proposer_requests, budget=budget, max_concurrency=max_concurrency
    )
    if any(result.status is not CallStatus.COMPLETED for result in proposals):
        return PanelRankResult(proposals, (), (), None, abstained=True, degraded=True)
    outcomes: list[PairwiseOutcome] = []
    judge_results: list[ProviderResult] = []
    wins = {f"p{index:02d}": 0 for index in range(1, len(proposals) + 1)}
    for left_index, left in enumerate(proposals):
        for right_index in range(left_index + 1, len(proposals)):
            right = proposals[right_index]
            left_id = f"p{left_index + 1:02d}"
            right_id = f"p{right_index + 1:02d}"
            forward = pairwise_request(left_id, left, right_id, right)
            reverse = pairwise_request(right_id, right, left_id, left)
            judgments = await dispatch_blind_first_passes(
                dispatcher, (forward, reverse), budget=budget, max_concurrency=2
            )
            judge_results.extend(judgments)
            first_winner = _winner(judgments[0], left_id, right_id)
            second_winner = _winner(judgments[1], right_id, left_id)
            sensitive = first_winner is None or first_winner != second_winner
            winner = None if sensitive else first_winner
            outcomes.append(PairwiseOutcome(left_id, right_id, winner, sensitive))
            if sensitive:
                return PanelRankResult(
                    proposals,
                    tuple(judge_results),
                    tuple(outcomes),
                    None,
                    abstained=True,
                    degraded=True,
                )
            if winner is not None:
                wins[winner] += 1
    ranked = sorted(wins.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return PanelRankResult(
            proposals,
            tuple(judge_results),
            tuple(outcomes),
            None,
            abstained=True,
            degraded=True,
        )
    return PanelRankResult(
        proposals,
        tuple(judge_results),
        tuple(outcomes),
        ranked[0][0],
        abstained=False,
        degraded=False,
    )


def _result(
    topology: str,
    results: tuple[ProviderResult, ...],
    required: tuple[str, ...],
    *,
    context_complete: bool,
) -> OrchestrationResult:
    completed = {result.handle for result in results if result.status is CallStatus.COMPLETED}
    missing = [handle for handle in required if handle not in completed]
    fused = context_complete and not missing
    return OrchestrationResult(
        topology,
        results,
        required,
        context_complete,
        fused,
        degraded=not fused,
        reason=None if fused else f"required reviewers did not complete: {', '.join(missing)}",
    )


def _winner(result: ProviderResult, left_id: str, right_id: str) -> str | None:
    if result.status is not CallStatus.COMPLETED or not isinstance(result.structured_output, dict):
        return None
    raw = result.structured_output.get("winner")
    if not isinstance(raw, str):
        return None
    candidate = {"left": left_id, "a": left_id, "right": right_id, "b": right_id}.get(
        raw.casefold(), raw
    )
    return candidate if candidate in {left_id, right_id} else None


def _failed_result(request: ProviderRequest, error: Exception) -> ProviderResult:
    metadata = request.metadata
    return ProviderResult(
        call_id=request.call_id,
        handle=request.handle,
        requested_model=str(metadata.get("requested_model", request.handle)),
        effective_model=None,
        configured_model=metadata.get("configured_model"),
        identity_evidence=(
            "unavailable" if metadata.get("configured_model") is not None else "legacy-unspecified"
        ),
        quota_group=metadata.get("quota_group"),
        vendor=str(metadata.get("vendor", "unknown")),
        family=str(metadata.get("family", "unknown")),
        mode=str(metadata.get("mode", "unknown")),
        compound=bool(metadata.get("compound", False)),
        worker_visibility=str(metadata.get("worker_visibility", "not-applicable")),
        status=CallStatus.TIMEOUT if isinstance(error, TimeoutError) else CallStatus.FAILED,
        duration_ms=0,
        output_text="",
        structured_output=None,
        output_hash=None,
        error=str(error) or "execution deadline exhausted",
    )
