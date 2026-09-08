"""Optional ACP profile admission and diagnostics without live provider calls."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from autofusion import doctor
from autofusion.config import FusionConfig, load_config
from autofusion.errors import ProviderError
from autofusion.registry import ProviderRegistry


def test_grok_portable_profile_stays_disabled(tmp_path: Path) -> None:
    config = load_config(repo_root=tmp_path, global_path=tmp_path / "no-global.json")
    profile = config.model("grok")
    assert profile.model == profile.canonical_model == "grok-4.6"
    assert profile.transport == "acp" and not profile.enabled
    assert profile.capabilities["read_only"] is True
    assert profile.capabilities["repo_access"] is False
    assert profile.capabilities["identity_source"] == "peer-session-model"
    registry = ProviderRegistry.from_config(config)
    with pytest.raises(ProviderError, match="disabled"):
        registry.get("grok")
    assert not any(
        p.transport == "acp" and p.vendor == "google"
        for p in (config.model(handle) for handle in config.section("models"))
    )


def test_grok_registry_honors_executable_override() -> None:
    from autofusion.providers.acp import AcpProvider

    config = FusionConfig(
        data={
            "models": {
                "grok": {
                    "transport": "acp",
                    "model": "grok-4.6",
                    "params": {"executable": "controlled-grok"},
                }
            }
        },
        source_paths=(),
    )
    provider = ProviderRegistry.from_config(config).get("grok")
    assert isinstance(provider, AcpProvider)
    assert provider.build_argv()[0] == "controlled-grok"
    assert provider.build_argv()[1:4] == ("--deny", "*", "agent")
    assert provider.build_argv()[-2:] == ("--no-leader", "stdio")


@pytest.mark.parametrize("enabled", [True, False])
def test_grok_doctor_probe_respects_disabled_profile(
    enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions: list[str] = []

    def version(path: str) -> str:
        versions.append(path)
        return "fake version"

    monkeypatch.setattr(doctor, "_version", version)
    config = FusionConfig(
        data={
            "models": {
                "grok": {
                    "enabled": enabled,
                    "transport": "acp",
                    "model": "grok-4.6",
                    "params": {"executable": sys.executable},
                }
            }
        },
        source_paths=(),
    )
    checks = doctor.inspect_environment(config, grounding_runner=None)["checks"]
    assert isinstance(checks, list)
    assert checks[0]["status"] == ("available" if enabled else "disabled")
    assert len(versions) == int(enabled)


def test_grok_doctor_missing_executable_is_blocking(tmp_path: Path) -> None:
    config = FusionConfig(
        data={
            "models": {
                "grok": {
                    "transport": "acp",
                    "model": "grok-4.6",
                    "params": {"executable": str(tmp_path / "absent-grok")},
                }
            }
        },
        source_paths=(),
    )
    result = doctor.inspect_environment(config, grounding_runner=None)
    assert result["blocking"] == ["model:grok"]
    checks = result["checks"]
    assert isinstance(checks, list)
    assert checks[0]["status"] == "missing"
