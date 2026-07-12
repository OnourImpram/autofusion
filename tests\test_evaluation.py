from __future__ import annotations

from dataclasses import replace

import pytest

from autofusion.evaluation import (
    REQUIRED_ARMS,
    EvaluationBudget,
    EvaluationCase,
    EvaluationHarness,
    EvaluationObservation,
    ReviewArm,
    aggregate_metrics,
)


def _observation(
    case: EvaluationCase,
    arm: ReviewArm,
    trial: int,
    *,
    passed: bool,
    finding_id: str = "shared",
) -> EvaluationObservation:
    return EvaluationObservation(
        task_id=case.task_id,
        arm=arm,
        input_hash=case.input_hash,
        budget_hash=case.budget_hash,
        passed=passed,
        verified_resolved=passed,
        reported_findings=2,
        confirmed_findings=1,
        expected_critical=1,
        confirmed_critical=1 if passed else 0,
        regressions=trial % 2,
        latency_ms=100 + trial,
        cost_usd=0.25,
        used_calls=1,
        confirmed_finding_ids=(finding_id,),
    )


def test_metric_math_includes_pass_at_k_pass_to_k_and_unique_gain() -> None:
    case = EvaluationCase(
        task_id="task-1",
        payload={"task": "review"},
        budget=EvaluationBudget(max_calls=3, max_wallclock_s=30, max_cost_usd=2.0),
    )
    observations = tuple(
        _observation(
            case,
            ReviewArm.CROSS_MODEL_REVIEW,
            trial,
            passed=trial < 2,
            finding_id=f"f-{trial}",
        )
        for trial in range(4)
    )
    metrics = aggregate_metrics(observations, k=2, baseline_confirmed_ids=frozenset({"task-1:f-0"}))
    assert metrics.verified_resolution == 0.5
    assert metrics.precision == 0.5
    assert metrics.critical_misses == 2
    assert metrics.regressions == 2
    assert metrics.latency_ms == 101.5
    assert metrics.cost_usd == 1.0
    assert metrics.pass_at_k == pytest.approx(5 / 6)
    assert metrics.pass_to_k == pytest.approx(1 / 6)
    assert metrics.unique_confirmed_gain == 3


def test_harness_holds_task_inputs_and_budgets_constant_across_all_arms() -> None:
    case = EvaluationCase(
        task_id="task-1",
        payload={"task": "review"},
        budget=EvaluationBudget(max_calls=3, max_wallclock_s=30, max_cost_usd=2.0),
    )
    harness = EvaluationHarness(cases=(case,))

    def execute(active_case: EvaluationCase, arm: ReviewArm, trial: int) -> EvaluationObservation:
        return _observation(active_case, arm, trial, passed=arm is not ReviewArm.SOLO)

    metrics = harness.run(execute, trials=2, k=1)
    assert set(metrics) == set(REQUIRED_ARMS)
    assert metrics[ReviewArm.SOLO].pass_at_k == 0.0
    assert metrics[ReviewArm.CROSS_MODEL_REVIEW].pass_at_k == 1.0

    def tampering_executor(
        active_case: EvaluationCase, arm: ReviewArm, trial: int
    ) -> EvaluationObservation:
        result = _observation(active_case, arm, trial, passed=True)
        return replace(result, budget_hash="tampered")

    with pytest.raises(ValueError, match="controlled task inputs or budgets"):
        harness.run(tampering_executor, trials=1, k=1)

    def over_budget_executor(
        active_case: EvaluationCase, arm: ReviewArm, trial: int
    ) -> EvaluationObservation:
        result = _observation(active_case, arm, trial, passed=True)
        return replace(result, used_calls=4)

    with pytest.raises(ValueError, match="exceeded"):
        harness.run(over_budget_executor, trials=1, k=1)
