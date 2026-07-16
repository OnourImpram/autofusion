"""Provider registration from validated configuration without ambient dispatch."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass

from autofusion.config import FusionConfig
from autofusion.errors import ConfigurationError, ProviderError
from autofusion.models import ProviderRequest, ProviderResult
from autofusion.providers import (
    AnthropicHttpProvider,
    ClaudeCliAdapter,
    CliTransportProvider,
    CodexCapabilityProbe,
    CodexExecAdapter,
    CommandRunner,
    HttpClient,
    OpenAICompatibleHttpProvider,
    Provider,
    SelfProvider,
    UrllibHttpClient,
)
from autofusion.util import JsonObject


@dataclass(frozen=True, slots=True)
class ProviderRegistry:
    """A complete immutable handle-to-provider map for one loaded configuration."""

    providers: Mapping[str, Provider]

    def get(self, handle: str) -> Provider:
        try:
            provider = self.providers[handle]
        except KeyError as exc:
            raise ProviderError(f"unknown provider handle: {handle}") from exc
        if not provider.profile.enabled:
            raise ProviderError(f"provider handle is disabled by config: {handle}")
        return provider

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        return self.get(request.handle).invoke(request)

    @classmethod
    def from_config(
        cls,
        config: FusionConfig,
        *,
        command_runner: CommandRunner | None = None,
        http_client: HttpClient | None = None,
        codex_capability_probe: CodexCapabilityProbe | None = None,
    ) -> ProviderRegistry:
        runner = command_runner or CommandRunner()
        client = http_client or UrllibHttpClient()
        models = config.section("models")
        providers: dict[str, Provider] = {}
        for handle, raw_value in models.items():
            raw = _raw_model(raw_value, handle)
            profile = config.model(handle)
            if profile.transport == "self":
                providers[handle] = SelfProvider(profile)
            elif profile.transport == "codex-exec":
                providers[handle] = CliTransportProvider(
                    profile,
                    CodexExecAdapter(
                        executable=str(
                            profile.params.get("executable", _default_cli_executable("codex"))
                        ),
                        capability_probe=codex_capability_probe,
                        allow_live_probe=profile.params.get("probe_on_call") is True,
                    ),
                    runner,
                )
            elif profile.transport == "claude-exec":
                providers[handle] = CliTransportProvider(
                    profile,
                    ClaudeCliAdapter(
                        executable=str(
                            profile.params.get("executable", _default_cli_executable("claude"))
                        )
                    ),
                    runner,
                )
            elif profile.transport == "openai-compatible":
                providers[handle] = OpenAICompatibleHttpProvider(
                    profile=profile,
                    base_url=_required_string(raw, "base_url", handle),
                    key_env=_required_string(raw, "key_env", handle),
                    http_client=client,
                )
            elif profile.transport in {"anthropic", "anthropic-http"}:
                providers[handle] = AnthropicHttpProvider(
                    profile=profile,
                    base_url=str(raw.get("base_url", "https://api.anthropic.com")),
                    key_env=_required_string(raw, "key_env", handle),
                    http_client=client,
                )
            else:
                message = f"unsupported provider transport for {handle}: {profile.transport}"
                raise ConfigurationError(message)
        return cls(providers=providers)


def _raw_model(value: object, handle: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"models.{handle} must be a JSON object")
    return dict(value)


def _required_string(raw: JsonObject, key: str, handle: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"models.{handle}.{key} must be a non-empty string")
    return value


def _default_cli_executable(name: str) -> str:
    if os.name == "nt" and name == "claude" and shutil.which("claude.exe") is not None:
        return "claude.exe"
    if os.name == "nt":
        return f"{name}.cmd"
    return name
