"""Identity policy shared by configuration and executable provider boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from autofusion.errors import ConfigurationError, ProviderError
from autofusion.models import ModelProfile

FABLE_MIGRATION = (
    "Fable is self-only; replace callable Fable with claude-opus, "
    "dual-fable with dual-opus, and external-council-fable with external-council"
)


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    vendor: str
    family: str


def names_fable(value: object) -> bool:
    if isinstance(value, str):
        return "fable" in value.casefold()
    if isinstance(value, dict):
        return any(names_fable(key) or names_fable(item) for key, item in value.items())
    if isinstance(value, list):
        return any(names_fable(item) for item in value)
    return False


def reject_fable_routes(data: dict[str, object]) -> None:
    """Reject legacy routes even when a later merge would drop their allowlist entry."""

    models = data.get("models")
    if isinstance(models, dict):
        for handle, raw in models.items():
            if handle != "self" and (names_fable(handle) or names_fable(raw)):
                raise ConfigurationError(FABLE_MIGRATION)
    for section in ("panels", "presets", "routing", "guardrails", "advisors"):
        if names_fable(data.get(section)):
            raise ConfigurationError(FABLE_MIGRATION)


def assert_executable_identity(profile: ModelProfile) -> None:
    """Apply the self-only policy even to providers injected without config loading."""

    if names_fable(
        [profile.handle, profile.model, profile.canonical_model, profile.family, profile.params]
    ):
        raise ProviderError(FABLE_MIGRATION)
    if profile.transport == "self" or not profile.callable:
        raise ProviderError(f"provider {profile.handle} is non-callable")
