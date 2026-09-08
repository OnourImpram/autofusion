from pathlib import Path

import pytest

from autofusion.config import load_config
from autofusion.engine import FusionEngine, FusionRunRequest
from autofusion.errors import PolicyError
from autofusion.policy import resolve_route
from autofusion.registry import ProviderRegistry


def test_pack_minimum_alias_retains_required_strength(tmp_path: Path) -> None:
    config = load_config(overrides={
        "presets": {"strict-custom": {"alias_for": "high"}},
        "packs": {"security": {"minimum_preset": "strict-custom"}},
    })
    engine = FusionEngine(config, ProviderRegistry({}), work_root=tmp_path)
    with pytest.raises(PolicyError, match="minimum"):
        engine._resolve_route(FusionRunRequest(
            task="Review", repo_root=tmp_path, artifact_kind="plan", preset="fast",
            panel="dual-opus", pack="security",
        ))
    route, _ = engine._resolve_route(FusionRunRequest(
        task="Review", repo_root=tmp_path, artifact_kind="plan", preset="fast",
        panel="quality", pack="security",
    ))
    assert route.panel == "quality"


def test_hard_minimum_alias_retains_required_strength() -> None:
    config = load_config(overrides={
        "presets": {"strict-custom": {"alias_for": "high"}},
        "routing": {"hard_gates": {"minimum_preset": "strict-custom"}},
    })
    with pytest.raises(PolicyError, match="minimum"):
        resolve_route(config, requested_preset="fast", artifact_paths=("migrations/001.sql",),
                      external_only=False, explicit_panel="dual-opus")
