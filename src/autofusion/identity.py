"""Identity policy shared by configuration and executable provider boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from autofusion.errors import ConfigurationError, ProviderError
from autofusion.models import ModelProfile

SELF_ONLY_FAMILIES = ("fable", "mythos")

SELF_ONLY_MIGRATION = (
    "Fable is self-only, and so is Mythos; replace callable Fable or Mythos with claude-opus, "
    "dual-fable with dual-opus, and external-council-fable with external-council"
)


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    vendor: str
    family: str


def names_self_only(value: object) -> bool:
    """True when any string inside ``value`` names a self-only family (Fable or Mythos)."""

    if isinstance(value, str):
        text = value.casefold()
        return any(family in text for family in SELF_ONLY_FAMILIES)
    if isinstance(value, dict):
        return any(names_self_only(key) or names_self_only(item) for key, item in value.items())
    if isinstance(value, list):
        return any(names_self_only(item) for item in value)
    return False


def reject_self_only_routes(data: dict[str, object]) -> None:
    """Reject legacy routes even when a later merge would drop their allowlist entry."""

    models = data.get("models")
    if isinstance(models, dict):
        for handle, raw in models.items():
            if handle != "self" and (names_self_only(handle) or names_self_only(raw)):
                raise ConfigurationError(SELF_ONLY_MIGRATION)
    for section in ("panels", "presets", "routing", "guardrails", "advisors"):
        if names_self_only(data.get(section)):
            raise ConfigurationError(SELF_ONLY_MIGRATION)


def assert_executable_identity(profile: ModelProfile) -> None:
    """Apply the self-only policy even to providers injected without config loading."""

    if names_self_only(
        [profile.handle, profile.model, profile.canonical_model, profile.family, profile.params]
    ):
        raise ProviderError(SELF_ONLY_MIGRATION)
    if profile.transport == "self" or not profile.callable:
        raise ProviderError(f"provider {profile.handle} is non-callable")
