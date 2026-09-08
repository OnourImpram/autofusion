"""Deterministic adaptive routing helpers with non-bypassable hard gates."""

from __future__ import annotations

from dataclasses import dataclass

from autofusion.config import FusionConfig
from autofusion.models import ModelProfile, RouteDecision
from autofusion.policy import resolve_route

_RANK = {"budget": 0, "fast": 1, "balanced": 2, "high": 3, "quality": 3, "parallel": 3}


@dataclass(frozen=True, slots=True)
class AdaptiveSignals:
    artifact_paths: tuple[str, ...] = ()
    cross_module_scope: bool = False
    low_verification_strength: bool = False
    irreversible: bool = False
    external_only: bool = False


def adaptive_preset(signals: AdaptiveSignals) -> str:
    if signals.irreversible or signals.cross_module_scope:
        return "quality"
    if signals.low_verification_strength:
        return "balanced"
    return "fast"


def resolve_adaptive_route(
    config: FusionConfig,
    *,
    signals: AdaptiveSignals,
    requested_preset: str = "adaptive",
    explicit_panel: str | None = None,
) -> RouteDecision:
    """Resolve a route without allowing an adaptive choice to lower a hard gate."""

    baseline = adaptive_preset(signals) if requested_preset == "adaptive" else requested_preset
    route = resolve_route(
        config,
        requested_preset=baseline,
        artifact_paths=signals.artifact_paths,
        external_only=signals.external_only,
        explicit_panel=explicit_panel,
    )
    reasons = route.reasons
    if requested_preset == "adaptive":
        reasons = (f"adaptive baseline selected {baseline}", *reasons)
    return RouteDecision(
        requested_preset=requested_preset,
        selected_preset=route.selected_preset,
        panel=route.panel,
        topology=route.topology,
        participants=route.participants,
        reasons=reasons,
        hard_gates=route.hard_gates,
        budget=route.budget,
        panel_rank=route.panel_rank,
    )


def effective_participant_count(profiles: tuple[ModelProfile, ...]) -> int:
    """Count each declared provider once, never opaque hidden workers."""

    return len({profile.handle for profile in profiles})


def is_route_at_least(decision: RouteDecision, minimum_preset: str) -> bool:
    return decision.panel_rank >= _RANK.get(minimum_preset, 0)
