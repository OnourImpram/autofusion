"""Deterministic policy and preset resolution."""

from __future__ import annotations

from pathlib import PurePosixPath

from autofusion.config import FusionConfig, panel_assignments, validate_participant
from autofusion.errors import ConfigurationError, PolicyError
from autofusion.models import RouteDecision, RunBudget
from autofusion.util import JsonObject


def panel_participants(panel: JsonObject) -> tuple[str, ...]:
    return tuple(dict.fromkeys(handle for handle, _ in panel_assignments(panel)))


def _matches_pattern(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    candidate = PurePosixPath(normalized)
    return candidate.match(pattern) or candidate.match(pattern.removeprefix("**/"))


def _preset_rank(name: str) -> int:
    ranks = {"budget": 0, "fast": 1, "balanced": 2, "high": 3, "parallel": 3}
    if name not in ranks:
        raise ConfigurationError(f"preset has no defined minimum strength: {name}")
    return ranks[name]


def panel_rank(config: FusionConfig, panel: JsonObject) -> int:
    """Measure the executed review structure independently of panel or preset names."""
    topology = panel.get("topology")
    role = "proposer" if topology == "panel-rank" else "reviewer"
    identities = {
        config.model(handle).canonical_model
        for handle, assigned_role in panel_assignments(panel) if assigned_role == role
    }
    if topology == "panel-rank":
        return 3 if len(identities) >= 2 and panel.get("judge") else 0
    if topology == "adversarial-review" and len(identities) >= 2:
        return 3
    if topology == "dual-review" and len(identities) >= 2:
        return 2
    return 1 if identities and topology in {"review", "adversarial-review"} else 0


def _minimum_preset_for_paths(
    config: FusionConfig, paths: tuple[str, ...]
) -> tuple[str, list[str]]:
    routing = config.section("routing")
    hard_gates = routing.get("hard_gates")
    if not isinstance(hard_gates, dict):
        return "fast", []
    minimum = str(hard_gates.get("minimum_preset", "fast"))
    patterns = [item for item in hard_gates.get("patterns", []) if isinstance(item, str)]
    matched = sorted(
        {path for path in paths for pattern in patterns if _matches_pattern(path, pattern)}
    )
    return (minimum, matched) if matched else ("fast", [])


def resolve_route(
    config: FusionConfig,
    *,
    requested_preset: str,
    artifact_paths: tuple[str, ...],
    external_only: bool,
    explicit_panel: str | None = None,
) -> RouteDecision:
    """Resolve one reproducible route and enforce hard escalation gates."""

    selected = requested_preset
    reasons: list[str] = []
    hard_gate_reasons: list[str] = []
    minimum, matched_paths = _minimum_preset_for_paths(config, artifact_paths)
    minimum, _ = config.preset(minimum)
    if matched_paths:
        hard_gate_reasons.append(
            f"sensitive paths require at least {minimum}: {', '.join(matched_paths)}"
        )
    if requested_preset == "adaptive":
        selected = minimum if matched_paths else "fast"
        reasons.append(f"adaptive selected {selected} from deterministic risk rules")
    resolved_name, preset = config.preset(selected)
    if _preset_rank(resolved_name) < _preset_rank(minimum):
        resolved_name, preset = config.preset(minimum)
    panel_name = explicit_panel or str(preset.get("panel", ""))
    if not panel_name:
        raise ConfigurationError(f"preset {resolved_name} does not resolve to a panel")
    panel = config.panel(panel_name)
    if panel.get("enabled", True) is not True:
        raise PolicyError(f"selected panel is disabled: {panel_name}")
    for handle, role in panel_assignments(panel):
        validate_participant(config, handle, role)
    strength = panel_rank(config, panel)
    if matched_paths and strength < _preset_rank(minimum):
        raise PolicyError(f"panel {panel_name} does not satisfy routing minimum {minimum}")
    receipt_preset = f"panel:{panel_name}" if explicit_panel is not None else resolved_name
    participants = panel_participants(panel)
    topology = str(panel.get("topology", ""))
    if external_only and "self" in participants:
        raise PolicyError(f"external mode cannot run panel {panel_name} because it contains self")
    if not external_only and "self" not in participants:
        reasons.append("selected panel is fully external")
    guardrails = config.section("guardrails")
    max_panel_size = int(guardrails.get("max_panel_size", 1))
    if len(participants) > max_panel_size:
        raise PolicyError(
            f"panel {panel_name} has {len(participants)} participants, limit is {max_panel_size}"
        )
    max_calls = int(guardrails.get("max_calls_per_run", 1))
    max_wallclock = int(guardrails.get("max_wallclock_s", 1))
    max_output = int(guardrails.get("max_output_chars_per_call", 1))
    max_cost_raw = guardrails.get("max_cost_usd")
    max_cost = float(max_cost_raw) if isinstance(max_cost_raw, (int, float)) else None
    if not reasons:
        reasons.append(f"explicit preset {requested_preset} resolved to {resolved_name}")
    return RouteDecision(
        requested_preset=requested_preset,
        selected_preset=receipt_preset,
        panel=panel_name,
        topology=topology,
        participants=participants,
        reasons=tuple(reasons),
        hard_gates=tuple(hard_gate_reasons),
        panel_rank=strength,
        budget=RunBudget(
            max_calls=max_calls,
            max_wallclock_s=max_wallclock,
            max_cost_usd=max_cost,
            max_output_chars_per_call=max_output,
        ),
    )


def assert_callable_participants(config: FusionConfig, participants: tuple[str, ...]) -> None:
    for handle in participants:
        if handle == "self":
            continue
        profile = config.model(handle)
        if not profile.enabled:
            raise PolicyError(f"required participant is disabled: {handle}")
        if not profile.callable:
            raise PolicyError(f"required participant is not callable: {handle}")
