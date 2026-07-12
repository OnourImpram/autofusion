"""Capability-brokered environment variable names without secret inspection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from autofusion.config import FusionConfig
from autofusion.errors import PolicyError

_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class CredentialBroker:
    """Release only explicitly configured credential variable names to one provider."""

    config: FusionConfig
    globally_allowed: frozenset[str] = frozenset()

    def for_handle(self, handle: str) -> tuple[str, ...]:
        models = self.config.section("models")
        raw = models.get(handle)
        if not isinstance(raw, dict):
            raise PolicyError(f"credential broker cannot resolve model handle: {handle}")
        key_env = raw.get("key_env")
        if key_env is None:
            return ()
        if not isinstance(key_env, str) or not _ENV_NAME.fullmatch(key_env):
            raise PolicyError(f"provider {handle} has an invalid credential variable name")
        if self.globally_allowed and key_env not in self.globally_allowed:
            raise PolicyError(f"provider credential is not globally approved: {key_env}")
        return (key_env,)

