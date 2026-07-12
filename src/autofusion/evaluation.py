"""Controlled evaluation of solo and multi-review execution arms."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import comb
from typing import cast

from autofusion.util import JsonObject, deep_copy_json, sha256_json


class ReviewArm(StrEnum):
    SOLO = "solo"
    SAME_MODEL_REVIEW = "same-model-review"
    CROSS_MODEL_REVIEW = "cross-model-review"
    ADVERSARIAL_REVIEW = "adversarial-review"
    COMPOUND_REFERENCE = "compound-reference"


REQUIRED_ARMS: tuple[ReviewArm, ...] = tuple(ReviewArm)


@dataclass(frozen=True, slots=True)
class EvaluationBudget:
    max_calls: int
    max_wallclock_s: int
    max_cost_usd: float

    def __post_init__(self) -> None:
        if self.max_calls < 1 or self.max_wallclock_s < 1 or self.max_cost_usd < 0:
            raise ValueError(
                "evaluation budget values must be non-negative and non-zero where required"
            )

    def as_json(self) -> JsonObject:
        return {
            "max_calls": self.max_calls,
            "max_wallclock_s": self.max_wallclock_s,
            "max_cost_usd": self.max_cost_usd,
        }


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    task_id: str
    payload: JsonObject
    budget: EvaluationBudget

    @property
    def input_hash(self) -> str:
        return sha256_json(self.payload)

    @property
    def budget_hash(self) -> str:
        return sha256_json(self.budget.as_json())


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    task_id: str
    arm: ReviewArm
    input_hash: str
    budget_hash: str
    passed: bool
    verified_resolved: bool
    reported_findings: int
    confirmed_findings: int
    expected_critical: int
    confirmed_critical: int
    regressions: int
    latency_ms: int
    cost_usd: float
    used_calls: int = 0
    confirmed_finding_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        counts = (
            self.reported_findings,
            self.confirmed_findings,
            self.expected_critical,
            self.confirmed_critical,
            self.regressions,
            self.latency_ms,
            self.used_calls,
        )
        if any(value < 0 for value in counts) or self.cost_usd < 0:
            raise ValueError("evaluation observations cannot contain negative metrics")
        if self.confirmed_findings > self.reported_findings:
            raise ValueError("confirmed findings cannot exceed reported findings")
        if self.confirmed_critical > self.expected_critical:
            raise ValueError("confirmed critical findings cannot exceed expected critical findings")


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    verified_resolution: float
    precision: float
    critical_misses: int
    regressions: int
    latency_ms: float
    cost_usd: float
    pass_at_k: float
    pass_to_k: float
    unique_confirmed_gain: int
    samples: int


def _pass_at_k(successes: int, trials: int, k: int) -> float:
    return 1.0 - (comb(trials - successes, k) / comb(trials, k))


def _pass_to_k(successes: int, trials: int, k: int) -> float:
    if successes < k:
        return 0.0
    return comb(successes, k) / comb(trials, k)


def aggregate_metrics(
    observations: Sequence[EvaluationObservation],
    *,
    k: int,
    baseline_confirmed_ids: frozenset[str] = frozenset(),
) -> EvaluationMetrics:
    """Compute controlled arm metrics, including standard pass@k and pass^k estimators."""

    if not observations:
        raise ValueError("evaluation metrics require observations")
    if k < 1:
        raise ValueError("k must be positive")
    by_task: dict[str, list[EvaluationObservation]] = defaultdict(list)
    for observation in observations:
        by_task[observation.task_id].append(observation)
    pass_at_values: list[float] = []
    pass_to_values: list[float] = []
    for task_observations in by_task.values():
        trials = len(task_observations)
        if trials < k:
            raise ValueError("each task requires at least k trials")
        successes = sum(observation.passed for observation in task_observations)
        pass_at_values.append(_pass_at_k(successes, trials, k))
        pass_to_values.append(_pass_to_k(successes, trials, k))
    count = len(observations)
    reported = sum(item.reported_findings for item in observations)
    confirmed = sum(item.confirmed_findings for item in observations)
    gained = {
        f"{item.task_id}:{finding_id}"
        for item in observations
        for finding_id in item.confirmed_finding_ids
    }
    return EvaluationMetrics(
        verified_resolution=sum(item.verified_resolved for item in observations) / count,
        precision=confirmed / reported if reported else 0.0,
        critical_misses=sum(
            item.expected_critical - item.confirmed_critical for item in observations
        ),
        regressions=sum(item.regressions for item in observations),
        latency_ms=sum(item.latency_ms for item in observations) / count,
        cost_usd=sum(item.cost_usd for item in observations),
        pass_at_k=sum(pass_at_values) / len(pass_at_values),
        pass_to_k=sum(pass_to_values) / len(pass_to_values),
        unique_confirmed_gain=len(gained - baseline_confirmed_ids),
        samples=count,
    )


@dataclass(frozen=True, slots=True)
class EvaluationHarness:
    cases: tuple[EvaluationCase, ...]

    def __post_init__(self) -> None:
        identifiers = [case.task_id for case in self.cases]
        if not self.cases or any(not identifier for identifier in identifiers):
            raise ValueError("evaluation harness requires named cases")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("evaluation case IDs must be unique")

    def run(
        self,
        executor: Callable[[EvaluationCase, ReviewArm, int], EvaluationObservation],
        *,
        trials: int,
        k: int,
        arms: tuple[ReviewArm, ...] = REQUIRED_ARMS,
    ) -> dict[ReviewArm, EvaluationMetrics]:
        """Run every arm against the same immutable input and budget hashes."""

        if trials < k or k < 1:
            raise ValueError("trials must be at least positive k")
        if set(arms) != set(REQUIRED_ARMS) or len(arms) != len(REQUIRED_ARMS):
            raise ValueError("controlled evaluation requires every reference arm exactly once")
        collected: dict[ReviewArm, list[EvaluationObservation]] = {arm: [] for arm in arms}
        for case in self.cases:
            frozen_case = EvaluationCase(
                task_id=case.task_id,
                payload=cast(JsonObject, deep_copy_json(case.payload)),
                budget=case.budget,
            )
            for arm in arms:
                for trial in range(trials):
                    observation = executor(frozen_case, arm, trial)
                    if (
                        observation.task_id != frozen_case.task_id
                        or observation.arm != arm
                        or observation.input_hash != frozen_case.input_hash
                        or observation.budget_hash != frozen_case.budget_hash
                    ):
                        raise ValueError(
                            "evaluation executor changed controlled task inputs or budgets"
                        )
                    if (
                        observation.used_calls > frozen_case.budget.max_calls
                        or observation.latency_ms
                        > frozen_case.budget.max_wallclock_s * 1000
                        or observation.cost_usd > frozen_case.budget.max_cost_usd
                    ):
                        raise ValueError("evaluation observation exceeded its controlled budget")
                    collected[arm].append(observation)
        baseline_ids = frozenset(
            f"{item.task_id}:{finding_id}"
            for item in collected[ReviewArm.SOLO]
            for finding_id in item.confirmed_finding_ids
        )
        return {
            arm: aggregate_metrics(
                observations, k=k, baseline_confirmed_ids=baseline_ids
            )
            for arm, observations in collected.items()
        }
