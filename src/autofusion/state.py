"""Explicit, terminal-safe lifecycle management for one fusion run."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from autofusion.models import RunState
from autofusion.util import isoformat_z, utc_now


class StateTransitionError(RuntimeError):
    """A requested run-state transition is not permitted."""


_TERMINAL = frozenset(
    {
        RunState.SIGNED_OFF,
        RunState.ESCALATED,
        RunState.DEGRADED,
        RunState.FAILED,
        RunState.CANCELLED,
    }
)
_NEXT: dict[RunState, frozenset[RunState]] = {
    RunState.PREPARED: frozenset({RunState.ROUTED, RunState.FAILED, RunState.CANCELLED}),
    RunState.ROUTED: frozenset({RunState.FROZEN, RunState.FAILED, RunState.CANCELLED}),
    RunState.FROZEN: frozenset({RunState.DISPATCHED, RunState.FAILED, RunState.CANCELLED}),
    RunState.DISPATCHED: frozenset(
        {RunState.ANALYZED, RunState.DEGRADED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.ANALYZED: frozenset(
        {
            RunState.GROUNDED,
            RunState.RECONCILED,
            RunState.ESCALATED,
            RunState.DEGRADED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.GROUNDED: frozenset(
        {
            RunState.RECONCILED,
            RunState.ESCALATED,
            RunState.DEGRADED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.RECONCILED: frozenset(
        {
            RunState.SIGNED_OFF,
            RunState.ESCALATED,
            RunState.DEGRADED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class StateTransition:
    before: RunState
    after: RunState
    reason: str
    occurred_at: str


class RunStateMachine:
    """Serialize transitions and permanently seal terminal runs."""

    def __init__(self, initial: RunState = RunState.PREPARED) -> None:
        self._state = initial
        self._history: list[StateTransition] = []
        self._lock = RLock()

    @property
    def state(self) -> RunState:
        with self._lock:
            return self._state

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return self._state in _TERMINAL

    @property
    def history(self) -> tuple[StateTransition, ...]:
        with self._lock:
            return tuple(self._history)

    def transition(self, target: RunState, *, reason: str) -> RunState:
        if not reason.strip():
            raise ValueError("transition reason must be non-empty")
        with self._lock:
            if self._state in _TERMINAL:
                raise StateTransitionError(f"terminal run cannot leave {self._state.value}")
            if target not in _NEXT.get(self._state, frozenset()):
                message = f"invalid transition: {self._state.value} -> {target.value}"
                raise StateTransitionError(message)
            prior = self._state
            self._state = target
            self._history.append(StateTransition(prior, target, reason, isoformat_z(utc_now())))
            return target

    def terminate(self, target: RunState, *, reason: str) -> RunState:
        if target not in _TERMINAL:
            raise ValueError(f"{target.value} is not terminal")
        return self.transition(target, reason=reason)
