"""Strict provider transports and their testable integration seams."""

from autofusion.providers.base import Provider, SelfProvider
from autofusion.providers.cli import (
    ClaudeCliAdapter,
    CliTransportProvider,
    CodexCapabilityProbe,
    CodexExecAdapter,
)
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.providers.http import (
    AnthropicHttpProvider,
    HttpClient,
    OpenAICompatibleHttpProvider,
    UrllibHttpClient,
)
from autofusion.providers.process import CommandOutcome, CommandRunner

__all__ = [
    "AnthropicHttpProvider",
    "ClaudeCliAdapter",
    "CliTransportProvider",
    "CodexCapabilityProbe",
    "CodexExecAdapter",
    "CommandOutcome",
    "CommandRunner",
    "DeterministicFakeProvider",
    "HttpClient",
    "OpenAICompatibleHttpProvider",
    "Provider",
    "SelfProvider",
    "UrllibHttpClient",
]
