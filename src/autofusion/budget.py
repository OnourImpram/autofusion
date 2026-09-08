"""Thread-safe hard accounting for orchestration resources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from time import monotonic

from autofusion.errors import BudgetExceeded
from autofusion.models import RunBudget


@dataclass(frozen=True, slots=True)
class CallReservation:
    sequence: int
    output_chars: int
    reserved_cost_usd: float


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    calls_used: int
    cost_used_usd: float
    reserved_cost_usd: float
    elapsed_s: float


class BudgetLedger:
    """Reserve attempts before dispatch, including every retry."""

    def __init__(
        self,
        budget: RunBudget,
        *,
        clock: Callable[[], float] = monotonic,
        started_at: float | None = None,
    ) -> None:
        if min(budget.max_calls, budget.max_wallclock_s, budget.max_output_chars_per_call) < 0:
            raise ValueError("budgets must be nonnegative")
        if budget.max_cost_usd is not None and budget.max_cost_usd < 0:
            raise ValueError("cost budget must be nonnegative")
        self._budget = budget
        self._clock = clock
        self._started_at = clock() if started_at is None else started_at
        self._deadline = self._started_at + budget.max_wallclock_s
        self._calls_used = 0
        self._cost_used_usd = 0.0
        self._reserved_cost_usd = 0.0
        self._settled: set[int] = set()
        self._lock = RLock()

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return BudgetSnapshot(
                self._calls_used,
                self._cost_used_usd,
                self._reserved_cost_usd,
                self._elapsed_s(),
            )

    def reserve_call(
        self, *, output_chars: int, estimated_cost_usd: float | None = None
    ) -> CallReservation:
        if output_chars < 0:
            raise ValueError("requested output must be nonnegative")
        if output_chars > self._budget.max_output_chars_per_call:
            raise BudgetExceeded("requested output exceeds per-call budget")
        reserved = 0.0 if estimated_cost_usd is None else estimated_cost_usd
        if reserved < 0:
            raise ValueError("estimated cost must be nonnegative")
        with self._lock:
            self._assert_wallclock()
            if self._calls_used >= self._budget.max_calls:
                raise BudgetExceeded("call budget exhausted")
            self._assert_cost(reserved)
            self._calls_used += 1
            self._reserved_cost_usd += reserved
            return CallReservation(self._calls_used, output_chars, reserved)

    def settle_call(self, reservation: CallReservation, *, actual_cost_usd: float | None) -> None:
        if actual_cost_usd is not None and actual_cost_usd < 0:
            raise ValueError("actual cost must be nonnegative")
        actual = 0.0 if actual_cost_usd is None else actual_cost_usd
        with self._lock:
            if reservation.sequence in self._settled:
                raise ValueError("call reservation is already settled")
            self._settled.add(reservation.sequence)
            self._reserved_cost_usd -= reservation.reserved_cost_usd
            if actual_cost_usd is None and self._budget.max_cost_usd is not None:
                raise BudgetExceeded("cost is unknown under a hard cost budget")
            self._cost_used_usd += actual
            self._assert_cost(0.0)

    def check_available(self) -> None:
        with self._lock:
            self._assert_wallclock()
            self._assert_cost(0.0)

    @property
    def deadline(self) -> float:
        return self._deadline

    def remaining_s(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def _elapsed_s(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def _assert_wallclock(self) -> None:
        if self.remaining_s() <= 0:
            raise BudgetExceeded("wall-clock budget exhausted")

    def _assert_cost(self, additional: float) -> None:
        maximum = self._budget.max_cost_usd
        used = self._cost_used_usd + self._reserved_cost_usd + additional
        if maximum is not None and used > maximum:
            raise BudgetExceeded("cost budget exhausted")
